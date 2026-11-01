"""CRE-01 -- the League templates, the transient Difficulty pick and the
UXP-18 unused-name draw, through ``POST /api/leagues/``.

ANG-08: the HTML chooser and Advanced pages are gone; both API paths (the
``template`` path and the Advanced path) funnel through the single writer
``league_services.create_league_and_season``. The chooser / Advanced GET,
redirect and re-render cases went with the pages.

These tests deliberately exercise the REAL team generator (no
``_generate_teams`` mock) so signature drift on the seam surfaces here.
"""

from __future__ import annotations

from django.test import TestCase

from matches.forms import CreateLeagueForm
from matches.league_services import template_to_form_data
from matches.league_templates import LEAGUE_TEMPLATES, LEAGUE_TEMPLATES_BY_KEY
from matches.models import League
from matches.tests.league_api_fixtures import LeagueApiTestCase
from teams.models import Team

LIST = "/api/leagues/"

# ---------------------------------------------------------------------------
# Independent ranking helper
# ---------------------------------------------------------------------------
#
# Deliberately NOT ``_rank_teams_by_strength`` -- recomputed here from the raw
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


def _template_body(key: str, *, league_name: str, difficulty: str = "medium") -> dict:
    return {"template": key, "league_name": league_name, "difficulty": difficulty}


def _advanced_body(**overrides) -> dict:
    body = {
        "league_name": "Adv League",
        "season_name": "Season 1",
        "start_date": "2026-06-01",
        "num_teams": 4,
        "schedule_format": "single_round_robin",
        "mean": 50,
        "std_dev": 15,
        "map_mode": "none",
    }
    body.update(overrides)
    return body


class _CreateApiTestCase(LeagueApiTestCase):
    def create(self, body: dict) -> League:
        response = self.api.post(LIST, body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return League.objects.get(pk=response.json()["data"]["id"])


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
                    data=template_to_form_data(
                        template, league_name="X", difficulty="medium"
                    )
                )
                self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_every_template_phases_string_parses(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                form = CreateLeagueForm(
                    data=template_to_form_data(
                        template, league_name="X", difficulty="medium"
                    )
                )
                self.assertTrue(form.is_valid(), msg=form.errors.as_json())
                specs = form.cleaned_data["phase_specs"]
                self.assertEqual(
                    [s.phase_type for s in specs],
                    [tok.split(":")[0] for tok in template.phases.split(",")],
                )

    def test_template_form_data_requests_no_conferences(self) -> None:
        # CONF-05 -- ``number_of_conferences`` is required=False/empty_value=0,
        # so the template path never creates Conferences.
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                form = CreateLeagueForm(
                    data=template_to_form_data(
                        template, league_name="X", difficulty="medium"
                    )
                )
                self.assertTrue(form.is_valid(), msg=form.errors.as_json())
                self.assertFalse(form.cleaned_data.get("number_of_conferences"))

    def test_only_the_career_template_enables_finance(self) -> None:
        enabled = {t.key for t in LEAGUE_TEMPLATES if t.finance_enabled}
        self.assertEqual(enabled, {"8_team_career"})


# ---------------------------------------------------------------------------
# Template path -- each template end-to-end (real generator, no mocks)
# ---------------------------------------------------------------------------


class TestCre01TemplatePathCreatesEachTemplate(_CreateApiTestCase):
    def test_each_template_creates_league_and_draft_season(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                league = self.create(
                    _template_body(template.key, league_name=f"L-{template.key}")
                )
                season = league.seasons.get()
                self.assertEqual(season.state, "draft")
                self.assertEqual(season.teams.count(), template.num_teams)

    def test_each_template_creates_the_expected_phase_shape(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                season = self.create(
                    _template_body(template.key, league_name=f"P-{template.key}")
                ).seasons.get()
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
                league = self.create(
                    _template_body(template.key, league_name=f"F-{template.key}")
                )
                self.assertEqual(league.finance_enabled, template.finance_enabled)

    def test_member_nights_template_yields_a_member_night_phase(self) -> None:
        season = self.create(
            _template_body("8_team_member_nights", league_name="MN")
        ).seasons.get()
        self.assertTrue(season.phases.filter(phase_type="member_night").exists())

    def test_double_rr_template_persists_the_double_round_robin_format(self) -> None:
        season = self.create(
            _template_body("8_team_double_rr", league_name="DRR")
        ).seasons.get()
        phase = season.phases.filter(phase_type="round_robin").first()
        self.assertIsNotNone(phase)
        self.assertEqual(phase.schedule_format, "double_round_robin")


# ---------------------------------------------------------------------------
# Difficulty pick
# ---------------------------------------------------------------------------


class TestCre01DifficultyPickTemplatePath(_CreateApiTestCase):
    """easy -> strongest, medium -> N//2, hard -> weakest (template path)."""

    def _create(self, difficulty: str, name: str) -> League:
        return self.create(
            _template_body("8_team_classic", league_name=name, difficulty=difficulty)
        )

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


class TestCre01DifficultyPickAdvancedPath(_CreateApiTestCase):
    """The same pick applies on the Advanced path."""

    def _create(self, name: str, **overrides) -> League:
        return self.create(_advanced_body(league_name=name, num_teams=8, **overrides))

    def test_easy_picks_the_strongest_team(self) -> None:
        league = self._create("AdvEasy", difficulty="easy")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[0].id)

    def test_hard_picks_the_weakest_team(self) -> None:
        league = self._create("AdvHard", difficulty="hard")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[-1].id)

    def test_omitted_difficulty_defaults_to_medium(self) -> None:
        league = self._create("AdvOmit")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[len(ranked) // 2].id)

    def test_blank_difficulty_defaults_to_medium(self) -> None:
        league = self._create("AdvBlank", difficulty="")
        ranked = _ranked_strongest_first(league.seasons.get().teams.all())
        self.assertEqual(league.current_team_id, ranked[len(ranked) // 2].id)


class TestCre01DifficultyAndRenameCompose(_CreateApiTestCase):
    """Difficulty PICKS the team; ``manager_team_name`` RENAMES the picked team."""

    def test_rename_applies_to_the_difficulty_picked_team(self) -> None:
        league = self.create(
            _advanced_body(
                league_name="Compose",
                num_teams=8,
                difficulty="hard",
                manager_team_name="Galkowski FC",
            )
        )
        season = league.seasons.get()
        self.assertEqual(league.current_team.name, "Galkowski FC")
        # The renamed team is still the WEAKEST of the enrolled teams.
        ranked = _ranked_strongest_first(season.teams.all())
        self.assertEqual(league.current_team_id, ranked[-1].id)

    def test_rename_does_not_add_an_extra_team(self) -> None:
        season = self.create(
            _advanced_body(
                league_name="NoExtra",
                num_teams=8,
                difficulty="easy",
                manager_team_name="Mine",
            )
        ).seasons.get()
        self.assertEqual(season.teams.count(), 8)
        self.assertIn("Mine", [t.name for t in season.teams.all()])


# ---------------------------------------------------------------------------
# UXP-18 -- a second League draws unused team names, not "#2" copies
# ---------------------------------------------------------------------------


class TestUxp18SecondLeagueGetsUnusedTeamNames(_CreateApiTestCase):
    @staticmethod
    def _team_names(league: League) -> set[str]:
        return set(league.seasons.get().teams.values_list("name", flat=True))

    def test_two_leagues_from_one_template_get_disjoint_unsuffixed_names(
        self,
    ) -> None:
        key = LEAGUE_TEMPLATES[0].key
        first = self._team_names(self.create(_template_body(key, league_name="First")))
        second = self._team_names(
            self.create(_template_body(key, league_name="Second"))
        )
        self.assertEqual(len(second), LEAGUE_TEMPLATES[0].num_teams)
        self.assertEqual(first & second, set())
        self.assertFalse(
            any("#" in n for n in first | second), f"{first!r} / {second!r}"
        )

    def test_exhausted_pool_falls_back_to_the_suffix(self) -> None:
        from teams.constants import TEAM_NAMES

        for name in TEAM_NAMES:
            Team.objects.create(name=name)
        names = self._team_names(
            self.create(_advanced_body(league_name="Exhausted", num_teams=4))
        )
        self.assertEqual(len(names), 4)
        self.assertTrue(all(" #" in n for n in names), names)
