"""CRE-01 — tests for the League template chooser, the relocated Advanced
create screen, and the transient Difficulty pick.

The seam contract is locked at ``.claude/worktrees/cre-01-seam-contract.md``.

``create/`` now serves the **chooser** (``league_create``); the pre-CRE-01 full
form moved verbatim to ``create/advanced/`` (``league_create_advanced``). Both
views funnel through the single writer ``_create_league_and_season``.

These tests deliberately exercise the REAL team generator (no ``_generate_teams``
mock) so signature drift on the seam surfaces here.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from matches.forms import CreateLeagueForm
from matches.league_templates import LEAGUE_TEMPLATES, LEAGUE_TEMPLATES_BY_KEY
from matches.league_views import _template_to_form_data
from matches.models import League, Season
from teams.models import Team

# ---------------------------------------------------------------------------
# Independent ranking helper
# ---------------------------------------------------------------------------
#
# Deliberately NOT ``_rank_teams_by_strength`` — recomputed here from the raw
# player ratings so the difficulty assertions are independent of the
# implementation they verify.


def _mean_overall(team: Team) -> float:
    actives = team.active_players
    if not actives:
        return 0.0
    return sum(p.overall_rating for p in actives) / len(actives)


def _ranked_strongest_first(teams) -> "list[Team]":
    """Mean active-roster ``overall_rating`` DESC, ``team_id`` ASC tiebreak."""
    return sorted(teams, key=lambda t: (-_mean_overall(t), t.id))


def _chooser_post(key: str, *, league_name: str, difficulty: str = "medium") -> dict:
    return {
        "template": key,
        "league_name": league_name,
        "difficulty": difficulty,
    }


def _advanced_payload(**overrides) -> dict:
    payload = {
        "league_name": "Adv League",
        "season_name": "Season 1",
        "start_date": "2026-06-01",
        "num_teams": "4",
        "schedule_format": "single_round_robin",
        "mean": "50",
        "std_dev": "15",
        "map_mode": "none",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Templates table -> valid form data
# ---------------------------------------------------------------------------


class TestCre01TemplateTableIsValid(TestCase):
    """Every ``LEAGUE_TEMPLATES`` row resolves to a valid ``CreateLeagueForm``."""

    def test_by_key_covers_every_row(self) -> None:
        self.assertEqual(len(LEAGUE_TEMPLATES_BY_KEY), len(LEAGUE_TEMPLATES))
        for template in LEAGUE_TEMPLATES:
            self.assertIs(LEAGUE_TEMPLATES_BY_KEY[template.key], template)

    def test_every_template_produces_a_valid_form(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                form = CreateLeagueForm(
                    data=_template_to_form_data(
                        template, league_name="X", difficulty="medium"
                    )
                )
                self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_every_template_phases_string_parses(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                form = CreateLeagueForm(
                    data=_template_to_form_data(
                        template, league_name="X", difficulty="medium"
                    )
                )
                self.assertTrue(form.is_valid(), msg=form.errors.as_json())
                specs = form.cleaned_data["phase_specs"]
                self.assertEqual(
                    [s.phase_type for s in specs],
                    [tok.split(":")[0] for tok in template.phases.split(",")],
                )

    def test_chooser_form_data_requests_no_conferences(self) -> None:
        # CONF-05 — ``number_of_conferences`` is required=False/empty_value=0,
        # so the chooser path never trips the conference redirect.
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                form = CreateLeagueForm(
                    data=_template_to_form_data(
                        template, league_name="X", difficulty="medium"
                    )
                )
                self.assertTrue(form.is_valid(), msg=form.errors.as_json())
                self.assertFalse(form.cleaned_data.get("number_of_conferences"))


# ---------------------------------------------------------------------------
# Chooser GET surface
# ---------------------------------------------------------------------------


class TestCre01ChooserGet(TestCase):
    def test_get_returns_200(self) -> None:
        self.assertEqual(self.client.get(reverse("league_create")).status_code, 200)

    def test_template_used(self) -> None:
        response = self.client.get(reverse("league_create"))
        self.assertTemplateUsed(response, "leagues/create.html")

    def test_reverse_resolves_to_create_path(self) -> None:
        self.assertEqual(reverse("league_create"), "/leagues/create/")

    def test_locked_chooser_dom_ids_present(self) -> None:
        body = self.client.get(reverse("league_create")).content.decode()
        for dom_id in (
            "league-create-template",
            "league-create-league-name",
            "league-create-difficulty",
            "league-create-submit",
            "league-create-advanced-link",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing DOM id {dom_id!r}")

    def test_every_template_is_offered_as_an_option(self) -> None:
        body = self.client.get(reverse("league_create")).content.decode()
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                self.assertIn(f'value="{template.key}"', body)
                self.assertIn(template.label, body)

    def test_advanced_link_points_at_the_advanced_route(self) -> None:
        body = self.client.get(reverse("league_create")).content.decode()
        self.assertIn(f'href="{reverse("league_create_advanced")}"', body)


# ---------------------------------------------------------------------------
# Chooser POST — each template end-to-end (real generator, no mocks)
# ---------------------------------------------------------------------------


class TestCre01ChooserPostCreatesEachTemplate(TestCase):
    def test_each_template_creates_league_and_draft_season(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                name = f"L-{template.key}"
                response = self.client.post(
                    reverse("league_create"),
                    _chooser_post(template.key, league_name=name),
                )
                self.assertEqual(response.status_code, 302)

                league = League.objects.get(name=name)
                season = league.seasons.get()
                self.assertEqual(season.state, "draft")
                self.assertEqual(season.teams.count(), template.num_teams)
                self.assertEqual(
                    response["Location"],
                    reverse("season_standings", args=[season.id]),
                )

    def test_each_template_creates_the_expected_phase_shape(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                name = f"P-{template.key}"
                self.client.post(
                    reverse("league_create"),
                    _chooser_post(template.key, league_name=name),
                )
                season = League.objects.get(name=name).seasons.get()
                expected = [tok.split(":")[0] for tok in template.phases.split(",")]
                actual = list(
                    season.phases.order_by("ordinal").values_list(
                        "phase_type", flat=True
                    )
                )
                self.assertEqual(actual, expected)

    def test_finance_enabled_is_honored_per_template(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                name = f"F-{template.key}"
                self.client.post(
                    reverse("league_create"),
                    _chooser_post(template.key, league_name=name),
                )
                league = League.objects.get(name=name)
                self.assertEqual(league.finance_enabled, template.finance_enabled)

    def test_only_the_career_template_enables_finance(self) -> None:
        enabled = {t.key for t in LEAGUE_TEMPLATES if t.finance_enabled}
        self.assertEqual(enabled, {"8_team_career"})

    def test_member_nights_template_yields_a_member_night_phase(self) -> None:
        self.client.post(
            reverse("league_create"),
            _chooser_post("8_team_member_nights", league_name="MN"),
        )
        season = League.objects.get(name="MN").seasons.get()
        self.assertTrue(season.phases.filter(phase_type="member_night").exists())

    def test_double_rr_template_persists_the_double_round_robin_format(self) -> None:
        self.client.post(
            reverse("league_create"),
            _chooser_post("8_team_double_rr", league_name="DRR"),
        )
        season = League.objects.get(name="DRR").seasons.get()
        phase = season.phases.filter(phase_type="round_robin").first()
        self.assertIsNotNone(phase)
        self.assertEqual(phase.schedule_format, "double_round_robin")


# ---------------------------------------------------------------------------
# Chooser POST — rejection paths
# ---------------------------------------------------------------------------


class TestCre01ChooserRejectsBadInput(TestCase):
    def test_unknown_template_key_re_renders_chooser(self) -> None:
        response = self.client.post(
            reverse("league_create"), _chooser_post("nope", league_name="X")
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "leagues/create.html")

    def test_unknown_template_key_creates_nothing(self) -> None:
        self.client.post(
            reverse("league_create"), _chooser_post("nope", league_name="X")
        )
        self.assertEqual(League.objects.count(), 0)
        self.assertEqual(Season.objects.count(), 0)

    def test_missing_template_key_creates_nothing(self) -> None:
        self.client.post(reverse("league_create"), {"league_name": "X"})
        self.assertEqual(League.objects.count(), 0)

    def test_blank_league_name_re_renders_and_creates_nothing(self) -> None:
        response = self.client.post(
            reverse("league_create"), _chooser_post("4_team_quick", league_name="")
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "leagues/create.html")
        self.assertEqual(League.objects.count(), 0)


# ---------------------------------------------------------------------------
# Difficulty pick
# ---------------------------------------------------------------------------


class TestCre01DifficultyPickChooserPath(TestCase):
    """easy -> strongest, medium -> N//2, hard -> weakest (chooser POST)."""

    def _create(self, difficulty: str, name: str) -> League:
        self.client.post(
            reverse("league_create"),
            _chooser_post("8_team_classic", league_name=name, difficulty=difficulty),
        )
        return League.objects.get(name=name)

    def test_easy_picks_the_strongest_team(self) -> None:
        league = self._create("easy", "Easy")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[0].id)

    def test_medium_picks_the_middle_team(self) -> None:
        league = self._create("medium", "Med")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[len(ranked) // 2].id)

    def test_hard_picks_the_weakest_team(self) -> None:
        league = self._create("hard", "Hard")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[-1].id)

    def test_easy_is_at_least_as_strong_as_hard(self) -> None:
        easy = self._create("easy", "E2")
        hard = self._create("hard", "H2")
        self.assertGreaterEqual(
            _mean_overall(easy.current_team), _mean_overall(hard.current_team)
        )

    def test_current_team_is_one_of_the_enrolled_teams(self) -> None:
        for difficulty in ("easy", "medium", "hard"):
            with self.subTest(difficulty=difficulty):
                league = self._create(difficulty, f"Enrolled-{difficulty}")
                season = league.seasons.get()
                self.assertIn(
                    league.current_team_id,
                    set(season.teams.values_list("id", flat=True)),
                )


class TestCre01DifficultyPickAdvancedPath(TestCase):
    """The same pick applies on the Advanced POST."""

    def _create(self, name: str, **overrides) -> League:
        self.client.post(
            reverse("league_create_advanced"),
            _advanced_payload(league_name=name, num_teams="8", **overrides),
        )
        return League.objects.get(name=name)

    def test_easy_picks_the_strongest_team(self) -> None:
        league = self._create("AdvEasy", difficulty="easy")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[0].id)

    def test_hard_picks_the_weakest_team(self) -> None:
        league = self._create("AdvHard", difficulty="hard")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[-1].id)

    def test_omitted_difficulty_defaults_to_medium(self) -> None:
        # ``difficulty`` is required=False; an Advanced POST that omits it
        # stays valid and behaves as Medium.
        league = self._create("AdvOmit")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[len(ranked) // 2].id)

    def test_blank_difficulty_defaults_to_medium(self) -> None:
        league = self._create("AdvBlank", difficulty="")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[len(ranked) // 2].id)


class TestCre01DifficultyAndRenameCompose(TestCase):
    """Difficulty PICKS the team; ``manager_team_name`` RENAMES the picked team."""

    def test_rename_applies_to_the_difficulty_picked_team(self) -> None:
        self.client.post(
            reverse("league_create_advanced"),
            _advanced_payload(
                league_name="Compose",
                num_teams="8",
                difficulty="hard",
                manager_team_name="Galkowski FC",
            ),
        )
        league = League.objects.get(name="Compose")
        season = league.seasons.get()
        self.assertEqual(league.current_team.name, "Galkowski FC")
        # The renamed team is still the WEAKEST of the enrolled teams.
        ranked = _ranked_strongest_first(season.teams.all())
        self.assertEqual(league.current_team_id, ranked[-1].id)

    def test_rename_does_not_add_an_extra_team(self) -> None:
        self.client.post(
            reverse("league_create_advanced"),
            _advanced_payload(
                league_name="NoExtra",
                num_teams="8",
                difficulty="easy",
                manager_team_name="Mine",
            ),
        )
        season = League.objects.get(name="NoExtra").seasons.get()
        self.assertEqual(season.teams.count(), 8)
        self.assertIn("Mine", [t.name for t in season.teams.all()])


# ---------------------------------------------------------------------------
# Advanced relocation
# ---------------------------------------------------------------------------


class TestCre01AdvancedRelocation(TestCase):
    def test_get_returns_200(self) -> None:
        response = self.client.get(reverse("league_create_advanced"))
        self.assertEqual(response.status_code, 200)

    def test_template_used(self) -> None:
        response = self.client.get(reverse("league_create_advanced"))
        self.assertTemplateUsed(response, "leagues/create_advanced.html")

    def test_reverse_resolves_to_advanced_path(self) -> None:
        self.assertEqual(reverse("league_create_advanced"), "/leagues/create/advanced/")

    def test_full_form_dom_ids_preserved(self) -> None:
        body = self.client.get(reverse("league_create_advanced")).content.decode()
        for dom_id in (
            "league-create-form",
            "league-create-league-name",
            "league-create-manager-team-name",
            "league-create-difficulty",
            "league-create-season-name",
            "league-create-start-date",
            "league-create-num-teams",
            "league-create-number-of-conferences",
            "league-create-schedule-format",
            "league-create-mean",
            "league-create-std-dev",
            "league-create-map-mode",
            "league-create-submit",
            "league-create-use-template-link",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing DOM id {dom_id!r}")

    def test_back_link_points_at_the_chooser(self) -> None:
        body = self.client.get(reverse("league_create_advanced")).content.decode()
        self.assertIn(f'href="{reverse("league_create")}"', body)

    def test_form_action_posts_to_the_advanced_route(self) -> None:
        body = self.client.get(reverse("league_create_advanced")).content.decode()
        self.assertIn(f'action="{reverse("league_create_advanced")}"', body)

    def test_post_creates_league_season_and_teams(self) -> None:
        response = self.client.post(
            reverse("league_create_advanced"), _advanced_payload()
        )
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="Adv League")
        season = league.seasons.get()
        self.assertEqual(season.state, "draft")
        self.assertEqual(season.teams.count(), 4)
        self.assertEqual(season.schedule_format, "single_round_robin")

    def test_invalid_post_re_renders_advanced_and_creates_nothing(self) -> None:
        response = self.client.post(
            reverse("league_create_advanced"), _advanced_payload(league_name="")
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "leagues/create_advanced.html")
        self.assertEqual(League.objects.count(), 0)


# ---------------------------------------------------------------------------
# CONF-05 interop — the conference redirect still works from the Advanced form
# ---------------------------------------------------------------------------


class TestCre01ConferenceRedirectSurvives(TestCase):
    def test_conferences_requested_redirects_to_manage_conferences(self) -> None:
        response = self.client.post(
            reverse("league_create_advanced"),
            _advanced_payload(
                league_name="ConfLeague", num_teams="8", number_of_conferences="2"
            ),
        )
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="ConfLeague").seasons.get()
        self.assertEqual(
            response["Location"],
            reverse("manage_conferences", args=[season.id]),
        )
        self.assertEqual(season.conferences.count(), 2)

    def test_no_conferences_redirects_to_standings(self) -> None:
        response = self.client.post(
            reverse("league_create_advanced"), _advanced_payload(league_name="Flat")
        )
        season = League.objects.get(name="Flat").seasons.get()
        self.assertEqual(
            response["Location"],
            reverse("season_standings", args=[season.id]),
        )
