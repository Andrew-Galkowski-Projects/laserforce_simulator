"""LG-01b — Django ``TestCase`` tests for the create-League flow at
``GET/POST /leagues/create/`` (URL name ``league_create``, view
``matches.views.league_create``, form ``matches.forms.CreateLeagueForm``).

The seam contract is locked at ``.claude/worktrees/lg-01b-seam-contract.md``.
The view is GET-renders-form / POST-creates-and-redirects, wraps all writes
in ``@transaction.atomic``, and on success creates one League (mode="league",
state="active") + one Season (state="draft", schedule_format=
"single_round_robin") + ``num_teams`` Teams (each with 6 Players via
``teams.views._generate_teams``), enrols them on the Season, and redirects
to ``reverse("season_standings", season_id=season.id)``.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import League, Season
from teams.models import Player, Team

# ---------------------------------------------------------------------------
# Reusable valid payload
# ---------------------------------------------------------------------------


def _valid_payload(**overrides) -> dict:
    payload = {
        "league_name": "Spring 2026",
        "season_name": "Season 1",
        "start_date": "2026-06-01",
        "num_teams": "4",
        "schedule_format": "single_round_robin",
        "mean": "50",
        "std_dev": "15",
        # LG-01j — ``map_mode`` is a required field on
        # ``CreateLeagueForm``; pre-LG-01j tests get the locked default
        # ``"none"`` with an empty ``map_pool`` so existing LG-01b /
        # LG-01g happy paths continue to pass.
        "map_mode": "none",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# TestLeagueCreateGet — GET surface
# ---------------------------------------------------------------------------


class TestLeagueCreateGet(TestCase):
    """LG-01b — GET ``/leagues/create/`` renders the create form."""

    def test_get_returns_200(self) -> None:
        response = self.client.get(reverse("league_create"))
        self.assertEqual(response.status_code, 200)

    def test_template_used(self) -> None:
        response = self.client.get(reverse("league_create"))
        self.assertTemplateUsed(response, "leagues/create.html")

    def test_all_locked_dom_ids_present(self) -> None:
        response = self.client.get(reverse("league_create"))
        body = response.content.decode()
        for dom_id in (
            "league-create-form",
            "league-create-league-name",
            "league-create-season-name",
            "league-create-start-date",
            "league-create-num-teams",
            "league-create-schedule-format",
            "league-create-mean",
            "league-create-std-dev",
            "league-create-submit",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing DOM id {dom_id!r}")

    def test_schedule_format_select_is_disabled(self) -> None:
        response = self.client.get(reverse("league_create"))
        body = response.content.decode()
        # Locate the schedule-format element and confirm the `disabled`
        # attribute is present on the SAME element (within a short
        # window — the attribute might be either before or after the id).
        idx = body.find('id="league-create-schedule-format"')
        self.assertNotEqual(idx, -1, "schedule-format id missing")
        # Find the surrounding element's `<` / `>` to bound the search.
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        self.assertNotEqual(start, -1)
        self.assertNotEqual(end, -1)
        element = body[start : end + 1]
        self.assertIn(
            "disabled",
            element,
            "schedule-format select missing `disabled` attribute",
        )

    def test_reverse_resolves_to_create_path(self) -> None:
        self.assertEqual(reverse("league_create"), "/leagues/create/")


# ---------------------------------------------------------------------------
# TestLeagueCreatePost — POST happy path
# ---------------------------------------------------------------------------


class TestLeagueCreatePost(TestCase):
    """LG-01b — POST ``/leagues/create/`` creates a League + Season + Teams."""

    def test_post_valid_returns_302(self) -> None:
        response = self.client.post(reverse("league_create"), _valid_payload())
        self.assertEqual(response.status_code, 302)

    def test_post_creates_league(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        leagues = League.objects.filter(name="Spring 2026")
        self.assertEqual(leagues.count(), 1)
        league = leagues.get()
        self.assertEqual(league.mode, "league")
        self.assertEqual(league.state, "active")

    def test_post_creates_season_in_draft_state(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        seasons = Season.objects.filter(name="Season 1")
        self.assertEqual(seasons.count(), 1)
        season = seasons.get()
        self.assertEqual(season.state, "draft")
        self.assertEqual(season.schedule_format, "single_round_robin")
        self.assertEqual(season.start_date, date(2026, 6, 1))
        self.assertIsNone(season.champion_team)
        self.assertIsNone(season.starting_team_ids_json)
        league = League.objects.get(name="Spring 2026")
        self.assertEqual(season.league_id, league.id)

    def test_post_creates_num_teams_teams_enrolled_in_season(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        self.assertEqual(season.teams.count(), 4)

    def test_post_each_team_has_six_players(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        for team in season.teams.all():
            # ``Player`` reverse accessor on Team is ``team.players`` (the
            # ``related_name="players"`` on Player.team FK in teams/models.py).
            self.assertEqual(
                team.players.count(),
                6,
                f"Team {team.name!r} has {team.players.count()} players, expected 6",
            )

    def test_post_redirects_to_season_standings(self) -> None:
        response = self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        self.assertEqual(
            response["Location"], reverse("season_standings", args=[season.id])
        )

    def test_redirect_target_renders_200(self) -> None:
        response = self.client.post(reverse("league_create"), _valid_payload())
        # Follow the redirect manually.
        follow = self.client.get(response["Location"])
        self.assertEqual(follow.status_code, 200)

    def test_post_num_teams_16_creates_16_teams_96_players(self) -> None:
        payload = _valid_payload(num_teams="16", league_name="Big League")
        self.client.post(reverse("league_create"), payload)
        season = Season.objects.get(name="Season 1")
        self.assertEqual(season.teams.count(), 16)
        total_players = sum(t.players.count() for t in season.teams.all())
        self.assertEqual(total_players, 96)


# ---------------------------------------------------------------------------
# TestLeagueCreateFreeAgents — free-agent pool seeded at creation
# ---------------------------------------------------------------------------


class TestLeagueCreateFreeAgents(TestCase):
    """Creating a League seeds its own 100–200 pool of free agents."""

    def test_post_creates_per_league_free_agent_pool(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        league = League.objects.get(name="Spring 2026")
        self.assertIsNotNone(league.free_agent_pool)
        count = league.free_agent_pool.players.count()
        self.assertGreaterEqual(count, 100)
        self.assertLessEqual(count, 200)

    def test_pool_not_enrolled_and_hidden_from_regular_teams(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        league = League.objects.get(name="Spring 2026")
        season = Season.objects.get(name="Season 1")
        pool = league.free_agent_pool
        # The pool Team is never enrolled in the Season.
        self.assertNotIn(pool.id, season.teams.values_list("id", flat=True))
        # And it never leaks into the competitive team list.
        self.assertNotIn(pool.id, Team.objects.regular().values_list("id", flat=True))

    def test_each_league_gets_its_own_separate_pool(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload(league_name="L1"))
        self.client.post(reverse("league_create"), _valid_payload(league_name="L2"))
        l1 = League.objects.get(name="L1")
        l2 = League.objects.get(name="L2")
        self.assertIsNotNone(l1.free_agent_pool)
        self.assertIsNotNone(l2.free_agent_pool)
        self.assertNotEqual(l1.free_agent_pool_id, l2.free_agent_pool_id)
        # Each pool holds only its own League's free agents.
        self.assertGreaterEqual(l1.free_agent_pool.players.count(), 100)
        self.assertGreaterEqual(l2.free_agent_pool.players.count(), 100)


# ---------------------------------------------------------------------------
# TestLeagueCreateFormValidation — per-field validation
# ---------------------------------------------------------------------------


class TestLeagueCreateFormValidation(TestCase):
    """LG-01b — invalid POSTs re-render the form and write nothing."""

    def _assert_rejects(self, payload: dict) -> None:
        """POST ``payload`` and assert nothing was created (200 re-render)."""
        before_leagues = League.objects.count()
        before_seasons = Season.objects.count()
        before_teams = Team.objects.count()
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before_leagues)
        self.assertEqual(Season.objects.count(), before_seasons)
        self.assertEqual(Team.objects.count(), before_teams)

    def test_missing_league_name_rerenders_form(self) -> None:
        payload = _valid_payload()
        del payload["league_name"]
        before = League.objects.count()
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before)
        # The form-field name should appear in the rendered error block.
        self.assertIn(b"league_name", response.content)

    def test_num_teams_5_invalid(self) -> None:
        self._assert_rejects(_valid_payload(num_teams="5"))

    def test_num_teams_0_invalid(self) -> None:
        self._assert_rejects(_valid_payload(num_teams="0"))

    def test_mean_negative_invalid(self) -> None:
        self._assert_rejects(_valid_payload(mean="-1"))

    def test_mean_over_100_invalid(self) -> None:
        self._assert_rejects(_valid_payload(mean="101"))

    def test_std_dev_zero_invalid(self) -> None:
        self._assert_rejects(_valid_payload(std_dev="0"))

    def test_std_dev_over_40_invalid(self) -> None:
        self._assert_rejects(_valid_payload(std_dev="41"))

    def test_missing_start_date_invalid(self) -> None:
        payload = _valid_payload()
        del payload["start_date"]
        self._assert_rejects(payload)

    def test_tampered_schedule_format_still_creates_single_round_robin(self) -> None:
        # The field is disabled=True — Django serves the initial value
        # regardless of what the client posts. The persisted Season should
        # have ``schedule_format="single_round_robin"``.
        payload = _valid_payload(schedule_format="double_round_robin")
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = Season.objects.get(name="Season 1")
        self.assertEqual(season.schedule_format, "single_round_robin")


# ---------------------------------------------------------------------------
# TestSeamWithGenerateTeams — end-to-end + transaction
# ---------------------------------------------------------------------------


class TestSeamWithGenerateTeams(TestCase):
    """LG-01b — exercise the real ``_generate_teams`` and ``@transaction.atomic``."""

    def test_real_generate_teams_creates_six_players_per_team(self) -> None:
        """All 6 slot FKs on each generated Team should be populated by
        ``_generate_teams`` (greedy preferred-role match plus leftover
        back-fill — see ``teams/views.py``)."""
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        for team in season.teams.all():
            slot_player_ids = {
                team.slot_commander_id,
                team.slot_heavy_id,
                team.slot_scout_1_id,
                team.slot_scout_2_id,
                team.slot_medic_id,
                team.slot_ammo_id,
            }
            # All 6 slots filled (no Nones).
            self.assertNotIn(
                None, slot_player_ids, f"Team {team.name} has empty slot(s)"
            )
            # 6 distinct Player ids.
            self.assertEqual(
                len(slot_player_ids),
                6,
                f"Team {team.name} has duplicate slot FKs: {slot_player_ids}",
            )

    def test_player_stats_in_valid_range(self) -> None:
        """Generated Player stats clamp to ``[0, 100]`` per the LG-00
        ``draw_stats`` contract (``random.gauss`` clamped at write time)."""
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        team = season.teams.first()
        player = team.players.first()
        # Three canonical stat fields from ``teams/models.py``.
        for attr in ("accuracy", "survival", "decision_making"):
            value = getattr(player, attr)
            self.assertGreaterEqual(value, 0, f"{attr}={value} on {player.name}")
            self.assertLessEqual(value, 100, f"{attr}={value} on {player.name}")

    def test_transaction_rollback_on_season_create_failure(self) -> None:
        """If ``Season.objects.create`` raises mid-way through the view,
        the surrounding ``@transaction.atomic`` decorator must roll back
        the preceding ``League`` write so the DB is unchanged.
        """
        before_leagues = League.objects.count()
        before_seasons = Season.objects.count()
        before_teams = Team.objects.count()

        # Patch the Season ``create`` classmethod through the view-side
        # import path. If that path doesn't intercept, fall back to the
        # model-side patch.
        try:
            with patch(
                "matches.league_views.Season.objects.create",
                side_effect=Exception("boom"),
            ):
                with self.assertRaises(Exception):
                    self.client.post(reverse("league_create"), _valid_payload())
        except AttributeError:
            # ``matches.views`` may not re-export ``Season`` — patch the
            # model itself instead.
            with patch(
                "matches.models.Season.objects.create",
                side_effect=Exception("boom"),
            ):
                with self.assertRaises(Exception):
                    self.client.post(reverse("league_create"), _valid_payload())

        # All 5 upstream writes must be rolled back.
        self.assertEqual(League.objects.count(), before_leagues)
        self.assertEqual(Season.objects.count(), before_seasons)
        self.assertEqual(Team.objects.count(), before_teams)


# ---------------------------------------------------------------------------
# LG-01g — current_team auto-set on league_create (§9c)
# ---------------------------------------------------------------------------


class TestLg01gCurrentTeamAutoSet(TestCase):
    """LG-01g — ``league_create`` populates ``League.current_team`` to
    the alphabetically-first generated Team.

    Locked at ``.claude/worktrees/lg-01g-seam-contract.md`` §8 + §9c.
    The auto-set lands INSIDE the existing ``@transaction.atomic`` body
    between the League create and the Season create, so a Season-create
    failure rolls the auto-set back atomically.
    """

    def test_league_create_populates_current_team_to_first_alphabetical_team(
        self,
    ) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        league = League.objects.get(name="Spring 2026")
        self.assertIsNotNone(league.current_team)
        # The alphabetically-first Team in the Season's M2M.
        season = league.seasons.first()
        first_alphabetical = sorted(t.name for t in season.teams.all())[0]
        self.assertEqual(league.current_team.name, first_alphabetical)

    def test_league_create_current_team_is_in_created_teams(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        league = League.objects.get(name="Spring 2026")
        season = league.seasons.first()
        enrolled_team_ids = set(season.teams.values_list("id", flat=True))
        self.assertIn(league.current_team_id, enrolled_team_ids)


# ---------------------------------------------------------------------------
# LG-01j — Per-Season map configuration on the create-League form
# ---------------------------------------------------------------------------
#
# Seam contract: ``.claude/worktrees/lg-01j-seam-contract.md`` §6
# ``CreateLeagueForm`` extension (2 new fields ``map_mode`` + ``map_pool``
# with 3 cross-field ``clean()`` rules) and the view-side persistence
# (Section 7 ``league_create`` view extension): ``map_mode=`` passed into
# ``Season.objects.create`` and ``season.map_pool.set(cleaned["map_pool"])``
# after ``season.teams.add(*created_teams)``.
#
# Tests use the existing LG-01b ``_valid_payload`` helper and exercise
# the form / view through the public POST endpoint (no mocking of
# ``_maps_with_confirmed_config`` — the helper already exists in
# ``matches/forms.py`` for ``MatchSetupForm`` / ``SingleRoundSetupForm``;
# LG-01j reuses it verbatim per the seam contract Section 6).


import io as _lg01j_io

from django.core.files.uploadedfile import (
    SimpleUploadedFile as _Lg01jSimpleUploadedFile,
)

from core.models import (
    ArenaMap as _Lg01jArenaMap,
    MapZoneConfig as _Lg01jMapZoneConfig,
)
from matches.forms import CreateLeagueForm as _Lg01jCreateLeagueForm


def _lg01j_png_bytes() -> bytes:
    from PIL import Image as _PILImage

    buf = _lg01j_io.BytesIO()
    _PILImage.new("RGB", (10, 10), color=(0, 128, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _lg01j_make_map_with_config(name: str = "Map") -> _Lg01jArenaMap:
    """Build an ArenaMap with at least one confirmed MapZoneConfig so it
    surfaces in ``_maps_with_confirmed_config()``."""
    arena_map = _Lg01jArenaMap.objects.create(
        name=name,
        image=_Lg01jSimpleUploadedFile(
            f"{name}.png", _lg01j_png_bytes(), content_type="image/png"
        ),
        img_width=10,
        img_height=10,
    )
    _Lg01jMapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=50,
        zone_data={"zones": [[1, 1], [1, 1]]},
        confirmed=True,
    )
    return arena_map


def _lg01j_make_map_without_config(name: str = "MapNoCfg") -> _Lg01jArenaMap:
    """ArenaMap with NO confirmed MapZoneConfig — should NOT surface in
    the picker queryset."""
    return _Lg01jArenaMap.objects.create(
        name=name,
        image=_Lg01jSimpleUploadedFile(
            f"{name}.png", _lg01j_png_bytes(), content_type="image/png"
        ),
        img_width=10,
        img_height=10,
    )


class TestLeagueCreateMapMode(TestCase):
    """LG-01j — ``CreateLeagueForm`` gains ``map_mode`` field; choices,
    initial, and per-mode-vs-pool ``clean()`` rules."""

    def test_form_has_map_mode_field(self) -> None:
        form = _Lg01jCreateLeagueForm()
        self.assertIn("map_mode", form.fields)

    def test_map_mode_choices_match_three_locked_tuples(self) -> None:
        form = _Lg01jCreateLeagueForm()
        choices = list(form.fields["map_mode"].choices)
        # Choices come from the model field; locked at §3 + §13.
        # Allow the Django default blank choice prefix to be omitted
        # since ``required=True`` + ``initial="none"`` is the locked
        # form definition (no blank choice).
        self.assertEqual(
            choices,
            [
                ("none", "3-zone fallback"),
                ("single", "Single map"),
                ("random_per_round", "Random per Round"),
            ],
        )

    def test_map_mode_initial_is_none_string(self) -> None:
        form = _Lg01jCreateLeagueForm()
        self.assertEqual(form.fields["map_mode"].initial, "none")

    def test_map_mode_required_true(self) -> None:
        form = _Lg01jCreateLeagueForm()
        self.assertTrue(form.fields["map_mode"].required)

    def test_clean_accepts_mode_none_with_empty_pool(self) -> None:
        payload = _valid_payload(map_mode="none")
        form = _Lg01jCreateLeagueForm(payload)
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_clean_accepts_mode_single_with_one_map(self) -> None:
        m = _lg01j_make_map_with_config("S1")
        payload = _valid_payload(map_mode="single")
        payload["map_pool"] = [str(m.id)]
        form = _Lg01jCreateLeagueForm(payload)
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_clean_accepts_mode_random_per_round_with_one_map(self) -> None:
        m = _lg01j_make_map_with_config("R1")
        payload = _valid_payload(map_mode="random_per_round")
        payload["map_pool"] = [str(m.id)]
        form = _Lg01jCreateLeagueForm(payload)
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_clean_accepts_mode_random_per_round_with_five_maps(self) -> None:
        ms = [_lg01j_make_map_with_config(f"R5_{i}") for i in range(5)]
        payload = _valid_payload(map_mode="random_per_round")
        payload["map_pool"] = [str(m.id) for m in ms]
        form = _Lg01jCreateLeagueForm(payload)
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_clean_rejects_bogus_mode(self) -> None:
        payload = _valid_payload(map_mode="bogus")
        form = _Lg01jCreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        # Django field-level enum error keyed on ``map_mode``.
        self.assertIn("map_mode", form.errors)


class TestLeagueCreateMapPool(TestCase):
    """LG-01j — ``CreateLeagueForm`` gains ``map_pool`` field; queryset
    is ``_maps_with_confirmed_config()``; the 3 mode-vs-pool rules
    enforced; happy-path view persistence."""

    def test_form_has_map_pool_field(self) -> None:
        form = _Lg01jCreateLeagueForm()
        self.assertIn("map_pool", form.fields)

    def test_map_pool_required_false(self) -> None:
        form = _Lg01jCreateLeagueForm()
        self.assertFalse(form.fields["map_pool"].required)

    def test_queryset_excludes_maps_without_confirmed_config(self) -> None:
        live = _lg01j_make_map_with_config("LiveMap")
        skipped = _lg01j_make_map_without_config("SkippedMap")
        form = _Lg01jCreateLeagueForm()
        qs_ids = set(form.fields["map_pool"].queryset.values_list("id", flat=True))
        self.assertIn(live.id, qs_ids)
        self.assertNotIn(skipped.id, qs_ids)

    # ---- 3 mode-vs-pool rules ----

    def test_clean_rejects_mode_none_with_non_empty_pool(self) -> None:
        m = _lg01j_make_map_with_config("NoneWithPool")
        payload = _valid_payload(map_mode="none")
        payload["map_pool"] = [str(m.id)]
        form = _Lg01jCreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        # Locked error message — byte-equal.
        self.assertIn(
            "Map pool must be empty when Map mode is '3-zone fallback'.",
            form.errors.get("map_pool", []),
        )

    def test_clean_rejects_mode_single_with_empty_pool(self) -> None:
        payload = _valid_payload(map_mode="single")
        # No map_pool key in payload ⇒ empty.
        form = _Lg01jCreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Map pool must contain exactly 1 map when Map mode is 'Single map'.",
            form.errors.get("map_pool", []),
        )

    def test_clean_rejects_mode_single_with_two_maps(self) -> None:
        m1 = _lg01j_make_map_with_config("S2A")
        m2 = _lg01j_make_map_with_config("S2B")
        payload = _valid_payload(map_mode="single")
        payload["map_pool"] = [str(m1.id), str(m2.id)]
        form = _Lg01jCreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Map pool must contain exactly 1 map when Map mode is 'Single map'.",
            form.errors.get("map_pool", []),
        )

    def test_clean_rejects_mode_random_per_round_with_empty_pool(self) -> None:
        payload = _valid_payload(map_mode="random_per_round")
        form = _Lg01jCreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Map pool must contain at least 1 map when Map mode is 'Random per Round'.",
            form.errors.get("map_pool", []),
        )

    # ---- Tamper POSTs through the view ----

    def test_view_post_mode_none_with_pool_rejected_no_league_created(self) -> None:
        m = _lg01j_make_map_with_config("TamperNone")
        payload = _valid_payload(map_mode="none")
        payload["map_pool"] = [str(m.id)]
        before = League.objects.count()
        response = self.client.post(reverse("league_create"), payload)
        # 200 re-render, NOT 302.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before)

    def test_view_post_mode_single_with_two_maps_rejected(self) -> None:
        m1 = _lg01j_make_map_with_config("TamperS2A")
        m2 = _lg01j_make_map_with_config("TamperS2B")
        payload = _valid_payload(map_mode="single")
        payload["map_pool"] = [str(m1.id), str(m2.id)]
        before = League.objects.count()
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before)

    def test_view_post_mode_random_empty_pool_rejected(self) -> None:
        payload = _valid_payload(map_mode="random_per_round")
        before = League.objects.count()
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before)

    # ---- Happy-path view persistence ----

    def test_view_post_mode_none_empty_pool_creates_season_with_none(self) -> None:
        payload = _valid_payload(map_mode="none", league_name="NoneOK")
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="NoneOK")
        season = league.seasons.first()
        self.assertEqual(season.map_mode, "none")
        self.assertEqual(season.map_pool.count(), 0)

    def test_view_post_mode_single_with_one_map_creates_season(self) -> None:
        m = _lg01j_make_map_with_config("SingleOK")
        payload = _valid_payload(map_mode="single", league_name="SingleL")
        payload["map_pool"] = [str(m.id)]
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="SingleL")
        season = league.seasons.first()
        self.assertEqual(season.map_mode, "single")
        self.assertEqual(list(season.map_pool.values_list("id", flat=True)), [m.id])

    def test_view_post_mode_random_with_pool_creates_season(self) -> None:
        ms = [_lg01j_make_map_with_config(f"RandOK{i}") for i in range(3)]
        payload = _valid_payload(map_mode="random_per_round", league_name="RandL")
        payload["map_pool"] = [str(m.id) for m in ms]
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="RandL")
        season = league.seasons.first()
        self.assertEqual(season.map_mode, "random_per_round")
        ids = sorted(season.map_pool.values_list("id", flat=True))
        self.assertEqual(ids, sorted([m.id for m in ms]))

    def test_view_post_invalid_rolls_back_no_league_or_team_rows(self) -> None:
        """Atomic rollback unchanged — an invalid form does NOT create
        the League or its Teams."""
        m = _lg01j_make_map_with_config("AtomTamper")
        payload = _valid_payload(map_mode="none", league_name="ShouldNotExist")
        payload["map_pool"] = [str(m.id)]
        before_leagues = League.objects.count()
        before_teams = Team.objects.count()
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before_leagues)
        self.assertEqual(Team.objects.count(), before_teams)
        self.assertFalse(League.objects.filter(name="ShouldNotExist").exists())


# ---------------------------------------------------------------------------
# LG-02-Part2a — one explicit round_robin SeasonPhase created per new Season
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2a-seam-contract.md`` §1.5 / §4:
# a successful ``league_create`` POST creates the Season AND exactly one
# ``SeasonPhase(ordinal=1, phase_type="round_robin")`` linked to it, inside the
# same ``@transaction.atomic`` block — so the existing rollback test (patched
# ``Season.objects.create``) leaves ZERO ``SeasonPhase`` rows.
#
# Appended as a NEW class; no existing class above is modified.


from matches.models import SeasonPhase as _Lg02SeasonPhase  # noqa: E402


class TestLg02Part2aSeasonPhaseOnCreate(TestCase):
    """LG-02-Part2a — ``league_create`` seeds one explicit RR SeasonPhase."""

    def test_post_creates_exactly_one_round_robin_phase(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        phases = list(season.phases.all())
        self.assertEqual(len(phases), 1)
        phase = phases[0]
        self.assertEqual(phase.ordinal, 1)
        self.assertEqual(phase.phase_type, "round_robin")

    def test_phase_linked_to_the_created_season(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        phase = _Lg02SeasonPhase.objects.get(season=season)
        self.assertEqual(phase.season_id, season.id)

    def test_only_one_phase_row_total_for_a_single_create(self) -> None:
        before = _Lg02SeasonPhase.objects.count()
        self.client.post(reverse("league_create"), _valid_payload())
        self.assertEqual(_Lg02SeasonPhase.objects.count(), before + 1)

    # -- LG-02-Part2b empty-equivalence: omitting / blanking ``phases`` keeps
    #    the Part2a single-RR shape (and the dormant columns populated). --

    def test_empty_phases_field_persists_single_round_robin(self) -> None:
        # ``phases`` absent from the POST ⇒ one round_robin phase (Part2a
        # equivalence). The RR row's schedule_format copies the Season format.
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        phases = list(season.phases.all())
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].phase_type, "round_robin")
        self.assertEqual(phases[0].schedule_format, "single_round_robin")
        self.assertIsNone(phases[0].tournament_id)

    def test_blank_phases_field_persists_single_round_robin(self) -> None:
        payload = _valid_payload()
        payload["phases"] = ""
        self.client.post(reverse("league_create"), payload)
        season = Season.objects.get(name="Season 1")
        phases = list(season.phases.all())
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].phase_type, "round_robin")
        self.assertEqual(phases[0].schedule_format, "single_round_robin")
        self.assertIsNone(phases[0].tournament_id)


# ---------------------------------------------------------------------------
# LG-02-Part2b — composer writes MULTIPLE ordered SeasonPhase rows
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2b-seam-contract.md`` §4 / §5a /
# §8: ``CreateLeagueForm`` gains a hidden ``phases`` field; ``clean()`` calls
# ``parse_phase_composition`` and stashes ``cleaned_data["phase_specs"]``; the
# ``league_create`` view loops over the specs creating one ``SeasonPhase`` per
# spec inside the existing ``@transaction.atomic`` block — RR rows copy
# ``schedule_format="single_round_robin"``, the tournament row's
# ``schedule_format`` is None, and EVERY row's ``tournament`` FK is None
# (always NULL in Part2b). A no-RR composition is rejected at the form layer
# (form invalid, 200 re-render) with ZERO rows created (atomic boundary holds).
#
# Appended as NEW classes; no existing class above is modified.


class TestLg02Part2bComposerMultiPhase(TestCase):
    """LG-02-Part2b — a multi-phase composer POST persists ordered rows."""

    def test_composer_persists_three_ordered_phase_rows(self) -> None:
        payload = _valid_payload()
        payload["phases"] = "round_robin,tournament,round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = Season.objects.get(name="Season 1")
        phases = list(season.phases.all())  # Meta.ordering=["ordinal"]
        self.assertEqual(len(phases), 3)
        self.assertEqual([p.ordinal for p in phases], [1, 2, 3])
        self.assertEqual(
            [p.phase_type for p in phases],
            ["round_robin", "tournament", "round_robin"],
        )

    def test_composer_rr_rows_copy_single_round_robin_format(self) -> None:
        payload = _valid_payload()
        payload["phases"] = "round_robin,tournament,round_robin"
        self.client.post(reverse("league_create"), payload)
        season = Season.objects.get(name="Season 1")
        phases = list(season.phases.all())
        # Ordinals 1 and 3 are round_robin rows; both copy the Season format.
        self.assertEqual(phases[0].schedule_format, "single_round_robin")
        self.assertEqual(phases[2].schedule_format, "single_round_robin")

    def test_composer_tournament_row_schedule_format_is_none(self) -> None:
        payload = _valid_payload()
        payload["phases"] = "round_robin,tournament,round_robin"
        self.client.post(reverse("league_create"), payload)
        season = Season.objects.get(name="Season 1")
        phases = list(season.phases.all())
        self.assertEqual(phases[1].phase_type, "tournament")
        self.assertIsNone(phases[1].schedule_format)

    def test_composer_every_row_tournament_fk_is_none(self) -> None:
        # Part2b: the tournament FK is ALWAYS NULL (the embed is Part2c).
        payload = _valid_payload()
        payload["phases"] = "round_robin,tournament,round_robin"
        self.client.post(reverse("league_create"), payload)
        season = Season.objects.get(name="Season 1")
        for phase in season.phases.all():
            self.assertIsNone(
                phase.tournament_id,
                f"phase ordinal={phase.ordinal} has a non-null tournament FK",
            )


class TestLg02Part2bComposerNoRoundRobinRejected(TestCase):
    """LG-02-Part2b — a no-RR composition is rejected at the form layer with
    ZERO League / Season / SeasonPhase rows created (atomic boundary holds)."""

    def test_no_rr_composition_rerenders_form_200(self) -> None:
        payload = _valid_payload(league_name="NoRrL")
        payload["phases"] = "tournament"
        response = self.client.post(reverse("league_create"), payload)
        # Form invalid ⇒ re-render, NOT redirect.
        self.assertEqual(response.status_code, 200)

    def test_no_rr_composition_creates_zero_rows(self) -> None:
        before_leagues = League.objects.count()
        before_seasons = Season.objects.count()
        before_phases = _Lg02SeasonPhase.objects.count()

        payload = _valid_payload(league_name="NoRrZero")
        payload["phases"] = "tournament"
        self.client.post(reverse("league_create"), payload)

        # The @transaction.atomic boundary + the form-layer rejection mean
        # nothing is written.
        self.assertEqual(League.objects.count(), before_leagues)
        self.assertEqual(Season.objects.count(), before_seasons)
        self.assertEqual(_Lg02SeasonPhase.objects.count(), before_phases)
        self.assertFalse(League.objects.filter(name="NoRrZero").exists())

    def test_rollback_on_season_create_failure_leaves_zero_phases(self) -> None:
        """The phase is created INSIDE the same ``@transaction.atomic`` block,
        so a mid-flow ``Season.objects.create`` failure rolls the phase back
        too — zero ``SeasonPhase`` rows persist.
        """
        before_phases = _Lg02SeasonPhase.objects.count()

        try:
            with patch(
                "matches.league_views.Season.objects.create",
                side_effect=Exception("boom"),
            ):
                with self.assertRaises(Exception):
                    self.client.post(reverse("league_create"), _valid_payload())
        except AttributeError:
            with patch(
                "matches.models.Season.objects.create",
                side_effect=Exception("boom"),
            ):
                with self.assertRaises(Exception):
                    self.client.post(reverse("league_create"), _valid_payload())

        self.assertEqual(_Lg02SeasonPhase.objects.count(), before_phases)


# ---------------------------------------------------------------------------
# LG-02-Part2c-3a — composer persists a double_round_robin RR phase
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3a-seam-contract.md`` §2.12 /
# §2.13 / §4. A composer POST whose wire value carries a
# ``round_robin:double_round_robin`` row persists a ``SeasonPhase`` with
# ``schedule_format == "double_round_robin"``; the default / bare composer still
# persists ``single_round_robin``; an unknown-format token is rejected at the
# form layer leaving ZERO League / Season / SeasonPhase rows (transaction
# atomicity).
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the per-token ``type:format`` parse in
# ``parse_phase_composition`` and the ``double_round_robin`` ``<select>`` option
# in the composer — the TDD red state, not a defect in this file.


class TestLg02Part2c3aComposerDoubleRoundRobin(TestCase):
    """LG-02-Part2c-3a — composer persists a ``double_round_robin`` RR phase."""

    def test_double_rr_token_persists_double_round_robin_phase(self) -> None:
        payload = _valid_payload(league_name="DoubleRrL")
        payload["phases"] = "round_robin:double_round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="DoubleRrL").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].phase_type, "round_robin")
        self.assertEqual(phases[0].schedule_format, "double_round_robin")
        self.assertIsNone(phases[0].tournament_id)

    def test_double_rr_then_tournament_persists_both_rows(self) -> None:
        payload = _valid_payload(league_name="DoubleRrTourneyL")
        payload["phases"] = "round_robin:double_round_robin,tournament"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="DoubleRrTourneyL").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(len(phases), 2)
        self.assertEqual(phases[0].phase_type, "round_robin")
        self.assertEqual(phases[0].schedule_format, "double_round_robin")
        self.assertEqual(phases[1].phase_type, "tournament")
        self.assertIsNone(phases[1].schedule_format)

    def test_bare_composer_still_persists_single_round_robin(self) -> None:
        # The default / bare composer (no explicit format) persists the Part2b
        # single_round_robin shape.
        payload = _valid_payload(league_name="BareRrL")
        payload["phases"] = "round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="BareRrL").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].schedule_format, "single_round_robin")

    def test_default_create_still_persists_single_round_robin(self) -> None:
        # Omitting the phases field entirely keeps the Part2a default.
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        phases = list(season.phases.all())
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].schedule_format, "single_round_robin")


class TestLg02Part2c3aComposerUnknownFormatRejected(TestCase):
    """LG-02-Part2c-3a — an unknown per-phase format is rejected at the form
    layer leaving ZERO rows (transaction atomicity)."""

    def test_unknown_format_rerenders_form_200(self) -> None:
        payload = _valid_payload(league_name="BadFmtL")
        payload["phases"] = "round_robin:triple_round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)

    def test_unknown_format_creates_zero_rows(self) -> None:
        before_leagues = League.objects.count()
        before_seasons = Season.objects.count()
        before_phases = _Lg02SeasonPhase.objects.count()

        payload = _valid_payload(league_name="BadFmtZero")
        payload["phases"] = "round_robin:triple_round_robin"
        self.client.post(reverse("league_create"), payload)

        self.assertEqual(League.objects.count(), before_leagues)
        self.assertEqual(Season.objects.count(), before_seasons)
        self.assertEqual(_Lg02SeasonPhase.objects.count(), before_phases)
        self.assertFalse(League.objects.filter(name="BadFmtZero").exists())


# ---------------------------------------------------------------------------
# LG-02-Part2c-3b — composer stamps tournament_mode="standings" on every phase
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3b-seam-contract.md``: the
# ``league_create`` spec loop stamps ``tournament_mode=spec.tournament_mode``.
# DORMANT this slice — the composer does not write a mode, so every phase
# (RR + tournament) persists the ``"standings"`` default.
#
# Appended as a NEW class; no existing class above is modified.


class TestLg02Part2c3bComposerTournamentMode(TestCase):
    """LG-02-Part2c-3b — every composed phase persists tournament_mode=standings."""

    def test_composed_tournament_phase_is_standings(self) -> None:
        payload = _valid_payload(league_name="ModeStdL")
        payload["phases"] = "round_robin,tournament"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="ModeStdL").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(phases[1].phase_type, "tournament")
        self.assertEqual(phases[1].tournament_mode, "standings")

    def test_every_composed_phase_is_standings(self) -> None:
        payload = _valid_payload(league_name="ModeAllStdL")
        payload["phases"] = "round_robin,tournament,round_robin"
        self.client.post(reverse("league_create"), payload)
        season = League.objects.get(name="ModeAllStdL").seasons.get()
        for phase in season.phases.all():
            self.assertEqual(phase.tournament_mode, "standings")

    def test_default_create_phase_is_standings(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload())
        season = Season.objects.get(name="Season 1")
        self.assertEqual(season.phases.get().tournament_mode, "standings")


# ---------------------------------------------------------------------------
# LG-02-Part2c-3c — composer persists per-phase tournament_mode (strength /
# unseeded), and a tampered random_draw mode is rejected at the form layer
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3c-seam-contract.md`` §2 / §9:
# the composer wire token gains a ``tournament:<mode>`` form; a
# ``tournament:strength`` / ``tournament:unseeded`` phase persists a
# ``SeasonPhase`` whose ``tournament_mode`` is stamped from the wire; a tampered
# ``tournament:random_draw`` POST (deferred mode) is rejected at the form layer
# (form invalid, 200 re-render) with ZERO League / Season / SeasonPhase rows
# created (the @transaction.atomic boundary holds).
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the Part2c-3c ``tournament:<mode>`` parse + the
# guard relaxation — the TDD red state, not a defect in this file.


class TestLg02Part2c3cComposerTournamentMode(TestCase):
    """LG-02-Part2c-3c — composer stamps the per-phase ``tournament_mode``."""

    def test_strength_tournament_persists_strength_mode(self) -> None:
        # A strength tournament may sit FIRST (no preceding RR required) — the
        # trailing round_robin satisfies the >=1-RR rule.
        payload = _valid_payload(league_name="StrengthL")
        payload["phases"] = "tournament:strength,round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="StrengthL").seasons.get()
        phases = list(season.phases.all())  # Meta.ordering=["ordinal"]
        self.assertEqual([p.phase_type for p in phases], ["tournament", "round_robin"])
        self.assertEqual(phases[0].tournament_mode, "strength")
        # The RR phase keeps the standings default.
        self.assertEqual(phases[1].tournament_mode, "standings")

    def test_unseeded_tournament_persists_unseeded_mode(self) -> None:
        payload = _valid_payload(league_name="UnseededL")
        payload["phases"] = "tournament:unseeded,round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="UnseededL").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual([p.phase_type for p in phases], ["tournament", "round_robin"])
        self.assertEqual(phases[0].tournament_mode, "unseeded")

    def test_mid_season_standings_persists_standings_mode(self) -> None:
        # RR -> standings tournament -> RR: a mid-season standings tournament
        # has a preceding RR, so the guard never fires.
        payload = _valid_payload(league_name="MidStandingsL")
        payload["phases"] = "round_robin,tournament:standings,round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="MidStandingsL").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(
            [p.phase_type for p in phases],
            ["round_robin", "tournament", "round_robin"],
        )
        self.assertEqual(phases[1].tournament_mode, "standings")

    def test_explicit_standings_tournament_after_rr(self) -> None:
        payload = _valid_payload(league_name="ExplicitStandingsL")
        payload["phases"] = "round_robin,tournament:standings"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="ExplicitStandingsL").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(phases[1].phase_type, "tournament")
        self.assertEqual(phases[1].tournament_mode, "standings")


class TestLg02Part2c3cComposerRandomDrawRejected(TestCase):
    """LG-02-Part2c-3c — a tampered ``tournament:random_draw`` mode is rejected
    at the form layer leaving ZERO rows (transaction atomicity)."""

    def test_random_draw_mode_rerenders_form_200(self) -> None:
        payload = _valid_payload(league_name="RandomDrawL")
        payload["phases"] = "round_robin,tournament:random_draw"
        response = self.client.post(reverse("league_create"), payload)
        # Form invalid ⇒ re-render, NOT redirect.
        self.assertEqual(response.status_code, 200)

    def test_random_draw_mode_creates_zero_rows(self) -> None:
        before_leagues = League.objects.count()
        before_seasons = Season.objects.count()
        before_phases = _Lg02SeasonPhase.objects.count()

        payload = _valid_payload(league_name="RandomDrawZero")
        payload["phases"] = "round_robin,tournament:random_draw"
        self.client.post(reverse("league_create"), payload)

        self.assertEqual(League.objects.count(), before_leagues)
        self.assertEqual(Season.objects.count(), before_seasons)
        self.assertEqual(_Lg02SeasonPhase.objects.count(), before_phases)
        self.assertFalse(League.objects.filter(name="RandomDrawZero").exists())

    def test_standings_before_rr_still_rejected(self) -> None:
        # The guard still fires for a standings tournament with no preceding RR.
        before_phases = _Lg02SeasonPhase.objects.count()
        payload = _valid_payload(league_name="StandingsFirstL")
        payload["phases"] = "tournament:standings,round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_Lg02SeasonPhase.objects.count(), before_phases)
        self.assertFalse(League.objects.filter(name="StandingsFirstL").exists())


# ---------------------------------------------------------------------------
# LG-02-Part2c-3d — composer persists the per-phase tournament_cut from the wire
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3d-seam-contract.md`` §4 / §5 /
# §9: the ``tournament[:mode[:cut]]`` wire token's cut threads through the
# ``league_create`` spec loop as ``tournament_cut=spec.tournament_cut``;
# ``tournament_format`` is NOT set from the form (no PhaseSpec.tournament_format)
# so it takes the column default ``"single_elimination"``.
#
# Appended as a NEW class; no existing class above is modified. These WILL fail
# until the Code agent lands the cut grammar + the spec-loop kwarg + the two new
# SeasonPhase columns — the TDD red state.


class TestLg02Part2c3dComposerTournamentCut(TestCase):
    """LG-02-Part2c-3d — a composed tournament phase persists ``tournament_cut``
    from the wire, and ``tournament_format`` defaults ``single_elimination``."""

    def test_standings_8_persists_cut_8(self) -> None:
        payload = _valid_payload(league_name="Cut8L")
        payload["phases"] = "round_robin,tournament:standings:8"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="Cut8L").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(phases[1].phase_type, "tournament")
        self.assertEqual(phases[1].tournament_mode, "standings")
        self.assertEqual(phases[1].tournament_cut, 8)

    def test_strength_4_persists_cut_4(self) -> None:
        payload = _valid_payload(league_name="Cut4L")
        payload["phases"] = "tournament:strength:4,round_robin"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="Cut4L").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(phases[0].phase_type, "tournament")
        self.assertEqual(phases[0].tournament_mode, "strength")
        self.assertEqual(phases[0].tournament_cut, 4)

    def test_bare_tournament_persists_cut_zero(self) -> None:
        # No cut on the wire ⇒ tournament_cut 0 (no cut).
        payload = _valid_payload(league_name="Cut0L")
        payload["phases"] = "round_robin,tournament"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="Cut0L").seasons.get()
        phases = list(season.phases.all())
        self.assertEqual(phases[1].tournament_cut, 0)

    def test_tournament_format_defaults_single_elimination(self) -> None:
        payload = _valid_payload(league_name="CutFmtL")
        payload["phases"] = "round_robin,tournament:standings:8"
        self.client.post(reverse("league_create"), payload)
        season = League.objects.get(name="CutFmtL").seasons.get()
        phases = list(season.phases.all())
        # tournament_format is not set from the form ⇒ column default.
        self.assertEqual(phases[1].tournament_format, "single_elimination")
        # The RR row also carries the default (inert there).
        self.assertEqual(phases[0].tournament_format, "single_elimination")

    def test_sub_floor_cut_rejected_zero_rows(self) -> None:
        # A cut < 4 (and != 0) is rejected at the form layer (the parser's floor
        # ValueError re-wraps as a forms.ValidationError) — ZERO rows created.
        before_leagues = League.objects.count()
        before_phases = _Lg02SeasonPhase.objects.count()
        payload = _valid_payload(league_name="CutFloorL")
        payload["phases"] = "round_robin,tournament:standings:2"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before_leagues)
        self.assertEqual(_Lg02SeasonPhase.objects.count(), before_phases)
        self.assertFalse(League.objects.filter(name="CutFloorL").exists())


# ---------------------------------------------------------------------------
# LG-02-Part2c-3e — composer persists the full per-phase tournament sub-config
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3e-seam-contract.md`` §7:
# ``league_create`` sets ALL 8 new fields from the parsed spec —
#   tournament_format, final_series_length, semifinal_series_length,
#   quarterfinal_series_length, earlier_series_length, wb_advancers, lb_advancers,
#   swiss_rounds — from the 11-field ``tournament:mode:cut:format:fsl:ssl:qsl:esl:
#   wb:lb:swiss`` wire token. Appended as a NEW class; no existing class above is
#   modified. These WILL fail until the Code agent lands the full grammar + the
#   spec-loop kwargs + the 7 new SeasonPhase columns — the TDD red state.


class TestLg02Part2c3eComposerFullSubConfig(TestCase):
    """LG-02-Part2c-3e — a composed tournament phase persists all 8 new fields."""

    def test_double_elimination_full_token_persists_all_fields(self) -> None:
        payload = _valid_payload(league_name="SubCfgDE")
        payload["phases"] = (
            "round_robin,tournament:standings:0:double_elimination:3:3:1:1:0:0:0"
        )
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="SubCfgDE").seasons.get()
        phases = list(season.phases.all())
        t = phases[1]
        self.assertEqual(t.phase_type, "tournament")
        self.assertEqual(t.tournament_format, "double_elimination")
        self.assertEqual(t.final_series_length, 3)
        self.assertEqual(t.semifinal_series_length, 3)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 1)
        self.assertEqual(t.wb_advancers, 0)
        self.assertEqual(t.lb_advancers, 0)
        self.assertEqual(t.swiss_rounds, 0)

    def test_round_robin_double_elim_combo_persists(self) -> None:
        payload = _valid_payload(league_name="SubCfgRRDE")
        payload["phases"] = (
            "round_robin,tournament:standings:0:round_robin_double_elim:1:1:1:1:8:4:0"
        )
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="SubCfgRRDE").seasons.get()
        t = list(season.phases.all())[1]
        self.assertEqual(t.tournament_format, "round_robin_double_elim")
        self.assertEqual(t.wb_advancers, 8)
        self.assertEqual(t.lb_advancers, 4)

    def test_swiss_rounds_persists(self) -> None:
        payload = _valid_payload(league_name="SubCfgSwiss")
        payload["phases"] = "round_robin,tournament:standings:0:swiss:1:1:1:1:0:0:6"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="SubCfgSwiss").seasons.get()
        t = list(season.phases.all())[1]
        self.assertEqual(t.tournament_format, "swiss")
        self.assertEqual(t.swiss_rounds, 6)

    def test_bare_tournament_defaults_all_sub_config(self) -> None:
        payload = _valid_payload(league_name="SubCfgBare")
        payload["phases"] = "round_robin,tournament"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        season = League.objects.get(name="SubCfgBare").seasons.get()
        t = list(season.phases.all())[1]
        self.assertEqual(t.tournament_format, "single_elimination")
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.semifinal_series_length, 1)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 1)
        self.assertEqual(t.wb_advancers, 0)
        self.assertEqual(t.lb_advancers, 0)
        self.assertEqual(t.swiss_rounds, 0)

    def test_invalid_combo_rejected_zero_rows(self) -> None:
        # An invalid RR→DE wb/lb combo is rejected at the form layer (parser's
        # combo ValueError re-wraps) — ZERO rows created.
        before_leagues = League.objects.count()
        before_phases = _Lg02SeasonPhase.objects.count()
        payload = _valid_payload(league_name="SubCfgBadCombo")
        payload["phases"] = (
            "round_robin,tournament:standings:0:round_robin_double_elim:1:1:1:1:8:2:0"
        )
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before_leagues)
        self.assertEqual(_Lg02SeasonPhase.objects.count(), before_phases)
        self.assertFalse(League.objects.filter(name="SubCfgBadCombo").exists())

    def test_bad_series_tier_rejected_zero_rows(self) -> None:
        before_leagues = League.objects.count()
        payload = _valid_payload(league_name="SubCfgBadTier")
        payload["phases"] = (
            "round_robin,tournament:standings:0:single_elimination:2:1:1:1:0:0:0"
        )
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(League.objects.count(), before_leagues)
        self.assertFalse(League.objects.filter(name="SubCfgBadTier").exists())


# ---------------------------------------------------------------------------
# LG-04 — baseline PlayerSeasonRating rows written at league_create
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-04-player-development-seam-contract.md``
# §4 / §7.3: a ``league_create`` POST writes exactly one baseline
# ``PlayerSeasonRating`` per FOUNDING Player — every competitive-Team Player
# (active slots + bench) AND every free-agent-pool Player — tagged to the
# founding draft Season, with ``potential is None``, ``age == player.age``, and
# the 19 stat fields equal to the AS-GENERATED live values (NO development is
# applied at baseline: row stats == live stats).
#
# Appended as a NEW class; no existing class above is modified. These WILL fail
# until the Code agent lands the ``PlayerSeasonRating`` model + the baseline
# writer in ``league_create`` — the TDD red state, not a defect in this file.


from matches.development import STAT_FIELDS as _Lg04StatFields  # noqa: E402
from matches.models import PlayerSeasonRating as _Lg04PlayerSeasonRating  # noqa: E402


class TestLg04BaselineRatings(TestCase):
    """LG-04 — ``league_create`` writes one baseline rating per founding Player."""

    def _create_and_get_league(self, league_name: str = "BaselineL") -> League:
        payload = _valid_payload(league_name=league_name)
        # Generate bench players too so the active-slots + bench set is exercised.
        payload["players_per_team"] = "8"
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        return League.objects.get(name=league_name)

    def _founding_players(self, league: League) -> set[int]:
        """Every founding Player id: competitive-Team players (active + bench)
        PLUS every free-agent-pool Player."""
        season = league.seasons.get()
        ids: set[int] = set()
        for team in season.teams.all():
            ids |= set(team.players.values_list("id", flat=True))
        if league.free_agent_pool is not None:
            ids |= set(league.free_agent_pool.players.values_list("id", flat=True))
        return ids

    def test_one_baseline_row_per_founding_player(self) -> None:
        league = self._create_and_get_league("BaselineCount")
        season = league.seasons.get()
        founding = self._founding_players(league)
        rows = _Lg04PlayerSeasonRating.objects.filter(season=season)
        # Exactly one row per founding Player.
        self.assertEqual(rows.count(), len(founding))
        rated_player_ids = set(rows.values_list("player_id", flat=True))
        self.assertEqual(rated_player_ids, founding)

    def test_competitive_team_players_get_a_baseline_row(self) -> None:
        league = self._create_and_get_league("BaselineComp")
        season = league.seasons.get()
        team = season.teams.first()
        # Active-slot AND bench players (players_per_team=8 ⇒ 2 bench).
        for player in team.players.all():
            self.assertTrue(
                _Lg04PlayerSeasonRating.objects.filter(
                    season=season, player=player
                ).exists(),
                f"no baseline row for competitive player {player.name!r}",
            )

    def test_free_agent_pool_players_get_a_baseline_row(self) -> None:
        league = self._create_and_get_league("BaselineFA")
        season = league.seasons.get()
        pool = league.free_agent_pool
        self.assertIsNotNone(pool)
        # Spot-check a handful of pool players (the pool is 100-200 large).
        for player in pool.players.all()[:5]:
            self.assertTrue(
                _Lg04PlayerSeasonRating.objects.filter(
                    season=season, player=player
                ).exists(),
                f"no baseline row for free-agent player {player.name!r}",
            )

    def test_baseline_rows_tagged_to_founding_draft_season(self) -> None:
        league = self._create_and_get_league("BaselineSeason")
        season = league.seasons.get()
        # Every baseline row tags to the founding draft Season's id.
        season_ids = set(
            _Lg04PlayerSeasonRating.objects.values_list("season_id", flat=True)
        )
        self.assertEqual(season_ids, {season.id})

    def test_baseline_potential_is_filled(self) -> None:
        # LG-05 supersedes the LG-04 "potential is None" contract: the baseline
        # row now carries a computed potential, floored at the row's overall.
        league = self._create_and_get_league("BaselinePot")
        season = league.seasons.get()
        rows = list(_Lg04PlayerSeasonRating.objects.filter(season=season))
        self.assertTrue(rows)
        for row in rows:
            self.assertIsNotNone(
                row.potential, "baseline potential must be filled (LG-05)"
            )
            self.assertGreaterEqual(row.potential, row.overall_rating)
            self.assertLessEqual(row.potential, 100.0)

    def test_baseline_age_equals_player_age(self) -> None:
        league = self._create_and_get_league("BaselineAge")
        season = league.seasons.get()
        for row in _Lg04PlayerSeasonRating.objects.filter(season=season).select_related(
            "player"
        ):
            self.assertEqual(
                row.age,
                row.player.age,
                f"baseline age {row.age} != live age {row.player.age} "
                f"for {row.player.name!r}",
            )

    def test_baseline_stats_equal_live_values_no_development(self) -> None:
        league = self._create_and_get_league("BaselineStats")
        season = league.seasons.get()
        for row in _Lg04PlayerSeasonRating.objects.filter(season=season).select_related(
            "player"
        ):
            for name in _Lg04StatFields:
                self.assertEqual(
                    getattr(row, name),
                    getattr(row.player, name),
                    f"baseline {name} drifted from live value for "
                    f"{row.player.name!r} — NO development at baseline",
                )

    def test_baseline_overall_rating_matches_player(self) -> None:
        league = self._create_and_get_league("BaselineOvr")
        season = league.seasons.get()
        row = (
            _Lg04PlayerSeasonRating.objects.filter(season=season)
            .select_related("player")
            .first()
        )
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row.overall_rating, row.player.overall_rating, places=4)


# ---------------------------------------------------------------------------
# LG-05 — baseline PlayerSeasonRating.potential + Player.potential filled at
# league_create
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-05-player-potential-seam-contract.md``
# §4 / §6: LG-05 changes ``_write_baseline_ratings`` to compute a potential per
# founding developing-set Player — ``Player.potential`` is filled non-None
# within ``[overall, 100]`` and the baseline ``PlayerSeasonRating.potential``
# column is filled non-None (matching ``player.potential``). The baseline
# applies the scouting-noise band exactly as a rollover.
#
# NOTE: the pre-LG-05 ``TestLg04BaselineRatings`` class above asserts
# ``potential is None`` at baseline — once the Code agent lands LG-05 that
# becomes stale (baseline now fills potential, per the contract). The agent /
# docs pass updates it; THIS file only ADDS the new live-potential class below.
# These WILL fail until ``Player.potential`` + the baseline potential write
# land — the TDD red state, not a defect in this file.


class TestLg05BaselineRatingsPotential(TestCase):
    """LG-05 — ``league_create`` fills ``Player.potential`` + the baseline
    ``PlayerSeasonRating.potential`` for every founding Player."""

    def _create_and_get_league(self, league_name: str = "Lg05BaselineL") -> League:
        payload = _valid_payload(league_name=league_name)
        payload["players_per_team"] = "8"  # exercise active-slots + bench
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        return League.objects.get(name=league_name)

    def _founding_players(self, league: League):
        season = league.seasons.get()
        ids: set[int] = set()
        for team in season.teams.all():
            ids |= set(team.players.values_list("id", flat=True))
        if league.free_agent_pool is not None:
            ids |= set(league.free_agent_pool.players.values_list("id", flat=True))
        return Player.objects.filter(id__in=ids)

    def test_founding_player_potential_is_non_none(self) -> None:
        league = self._create_and_get_league("Lg05BaseNonNone")
        for player in self._founding_players(league):
            self.assertIsNotNone(
                player.potential, f"founding player {player.name!r} potential is None"
            )

    def test_founding_player_potential_within_overall_and_100(self) -> None:
        league = self._create_and_get_league("Lg05BaseRange")
        for player in self._founding_players(league):
            self.assertIsNotNone(player.potential)
            self.assertGreaterEqual(
                player.potential,
                player.overall_rating - 1e-6,
                f"potential below current overall for {player.name!r}",
            )
            self.assertLessEqual(
                player.potential, 100.0, f"potential above 100 for {player.name!r}"
            )

    def test_baseline_rating_row_potential_non_none(self) -> None:
        league = self._create_and_get_league("Lg05BaseRowNonNone")
        season = league.seasons.get()
        rows = _Lg04PlayerSeasonRating.objects.filter(season=season)
        self.assertTrue(rows.exists())
        for row in rows:
            self.assertIsNotNone(
                row.potential, "baseline rating-row potential must be filled"
            )

    def test_baseline_rating_row_potential_matches_live_player(self) -> None:
        league = self._create_and_get_league("Lg05BaseRowMatch")
        season = league.seasons.get()
        for row in _Lg04PlayerSeasonRating.objects.filter(season=season).select_related(
            "player"
        ):
            self.assertAlmostEqual(
                row.potential,
                row.player.potential,
                places=4,
                msg=f"baseline row potential drifted from live for {row.player.name!r}",
            )

    def test_baseline_rating_row_potential_within_overall_and_100(self) -> None:
        league = self._create_and_get_league("Lg05BaseRowRange")
        season = league.seasons.get()
        for row in _Lg04PlayerSeasonRating.objects.filter(season=season):
            self.assertIsNotNone(row.potential)
            self.assertGreaterEqual(row.potential, row.overall_rating - 1e-6)
            self.assertLessEqual(row.potential, 100.0)


# ---------------------------------------------------------------------------
# CAR-01 — manager names their own team on the create-League form
# ---------------------------------------------------------------------------
#
# Seam contract: ``CreateLeagueForm`` gains an optional
# ``manager_team_name = forms.CharField(max_length=100, required=False,
# label="Your team name")`` (widget DOM id ``league-create-manager-team-name``),
# inserted after ``league_name`` / before ``season_name``.
#
# View (inside its ``@transaction.atomic``): when ``manager_team_name`` is
# non-blank after ``.strip()``, the alphabetical-first generated team is RENAMED
# to that value and becomes ``league.current_team``; the named team stays ONE OF
# THE N generated teams (league size == ``num_teams``). When blank/omitted, the
# LG-01g behaviour is unchanged — ``current_team`` is the alphabetically-first
# enrolled team (byte-identical to today).
#
# These exercise the REAL ``_generate_teams`` end-to-end (no ``mock.patch``) so
# signature drift surfaces as a failure, mirroring ``TestSeamWithGenerateTeams``.


class TestCar01ManagerTeamName(TestCase):
    """CAR-01 — the manager's named team becomes one of the N + current_team."""

    # ---- Named-team path (``manager_team_name`` non-blank) ----

    def test_post_named_team_returns_302(self) -> None:
        response = self.client.post(
            reverse("league_create"),
            _valid_payload(manager_team_name="Galkowski FC", num_teams="4"),
        )
        self.assertEqual(response.status_code, 302)

    def test_current_team_is_the_named_team(self) -> None:
        self.client.post(
            reverse("league_create"),
            _valid_payload(manager_team_name="Galkowski FC", num_teams="4"),
        )
        league = League.objects.get(name="Spring 2026")
        self.assertIsNotNone(league.current_team)
        self.assertEqual(league.current_team.name, "Galkowski FC")

    def test_named_team_is_enrolled_in_season(self) -> None:
        self.client.post(
            reverse("league_create"),
            _valid_payload(manager_team_name="Galkowski FC", num_teams="4"),
        )
        league = League.objects.get(name="Spring 2026")
        season = league.seasons.first()
        enrolled_ids = set(season.teams.values_list("id", flat=True))
        self.assertIn(league.current_team_id, enrolled_ids)

    def test_named_team_is_one_of_the_n_not_an_extra(self) -> None:
        # league size == num_teams: the manager team is one of the N generated
        # teams, NOT an extra (N+1).
        self.client.post(
            reverse("league_create"),
            _valid_payload(manager_team_name="Galkowski FC", num_teams="4"),
        )
        season = Season.objects.get(name="Season 1")
        self.assertEqual(season.teams.count(), 4)

    def test_named_team_name_is_stored(self) -> None:
        self.client.post(
            reverse("league_create"),
            _valid_payload(manager_team_name="Galkowski FC", num_teams="4"),
        )
        league = League.objects.get(name="Spring 2026")
        self.assertEqual(league.current_team.name, "Galkowski FC")

    def test_whitespace_name_is_stripped(self) -> None:
        self.client.post(
            reverse("league_create"),
            _valid_payload(manager_team_name="  X  ", num_teams="4"),
        )
        league = League.objects.get(name="Spring 2026")
        self.assertEqual(league.current_team.name, "X")

    # ---- Blank-name fallback path (LG-01g behaviour unchanged) ----

    def test_blank_name_falls_back_to_alphabetical_first(self) -> None:
        # ``manager_team_name`` omitted entirely ⇒ current_team is the
        # alphabetically-first enrolled team (LG-01g, byte-identical).
        self.client.post(reverse("league_create"), _valid_payload(num_teams="4"))
        league = League.objects.get(name="Spring 2026")
        season = league.seasons.first()
        first_alphabetical = sorted(t.name for t in season.teams.all())[0]
        self.assertEqual(league.current_team.name, first_alphabetical)

    def test_empty_string_name_falls_back_to_alphabetical_first(self) -> None:
        self.client.post(
            reverse("league_create"),
            _valid_payload(manager_team_name="", num_teams="4"),
        )
        league = League.objects.get(name="Spring 2026")
        season = league.seasons.first()
        first_alphabetical = sorted(t.name for t in season.teams.all())[0]
        self.assertEqual(league.current_team.name, first_alphabetical)

    def test_blank_name_creates_num_teams_teams(self) -> None:
        self.client.post(reverse("league_create"), _valid_payload(num_teams="4"))
        season = Season.objects.get(name="Season 1")
        self.assertEqual(season.teams.count(), 4)

    # ---- Form-level (light) ----

    def test_form_valid_with_blank_manager_team_name(self) -> None:
        form = _Lg01jCreateLeagueForm(_valid_payload())
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_form_round_trips_populated_manager_team_name(self) -> None:
        form = _Lg01jCreateLeagueForm(_valid_payload(manager_team_name="Galkowski FC"))
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())
        self.assertEqual(form.cleaned_data["manager_team_name"], "Galkowski FC")


# ---------------------------------------------------------------------------
# FIN-03 — league_create seeds AI budgets by team strength
# ---------------------------------------------------------------------------
#
# Seam contract (FIN-03): finance-ON ``league_create`` calls
# ``_seed_team_budgets_by_strength(created_teams)`` BEFORE
# ``_write_baseline_ratings``, seeding EVERY team (incl. ``current_team``).
# Teams are ranked by mean active-roster ``overall_rating`` DESC (tie-break
# team_id ASC), then assigned a rank-linear band ``[SEED_BUDGET_MIN=20,
# SEED_BUDGET_MAX=90]`` (strongest -> 90, weakest -> 20; single team ->
# SEED_BUDGET_SINGLE=55; round int). The SAME level is set on all THREE
# ``budget_scouting`` / ``budget_coaching`` / ``budget_facilities`` fields via
# a ``bulk_update``. Finance-OFF: NO seeding — budgets stay at the field
# default 34.
#
# Assertion discipline: assert on the SEEDED LEVELS (differentiation by
# strength, all-3-equal-per-team, current_team seeded, single-team -> 55,
# OFF -> default 34) — NOT on exact baseline potential gauss floats. These WILL
# fail until the Code agent lands ``_seed_team_budgets_by_strength`` + the
# SEED_BUDGET_* consts + the league_create seeding call — the TDD red state.

from matches.tests.conftest import make_team_with_slots as _fin03_team  # noqa: E402


def _fin03_payload(**overrides) -> dict:
    payload = _valid_payload(**overrides)
    payload.setdefault("finance_enabled", "on")
    return payload


class TestFin03SeedBudgetsByStrengthHelper(TestCase):
    """``_seed_team_budgets_by_strength(teams)`` ranks by mean active-roster
    overall DESC (team_id ASC tiebreak), assigns the rank-linear band
    ``[20, 90]`` to all 3 budget fields, and bulk-updates. Exercised directly so
    the strength ordering is deterministic."""

    def _pin_roster_overall(self, team, value: int) -> None:
        for p in team.active_players:
            for name in _Lg04StatFields:
                setattr(p, name, value)
            p.save()

    def test_consts_exist_with_locked_values(self) -> None:
        from matches.league_views import (
            SEED_BUDGET_MAX,
            SEED_BUDGET_MIN,
            SEED_BUDGET_SINGLE,
        )

        self.assertEqual(SEED_BUDGET_MIN, 20)
        self.assertEqual(SEED_BUDGET_MAX, 90)
        self.assertEqual(SEED_BUDGET_SINGLE, 55)

    def test_strongest_gets_max_weakest_gets_min(self) -> None:
        from matches.league_views import _seed_team_budgets_by_strength

        strong, _ = _fin03_team("Fin03Strong")
        weak, _ = _fin03_team("Fin03Weak")
        self._pin_roster_overall(strong, 90)
        self._pin_roster_overall(weak, 20)

        _seed_team_budgets_by_strength([strong, weak])

        strong.refresh_from_db()
        weak.refresh_from_db()
        # Strongest roster -> SEED_BUDGET_MAX (90), weakest -> SEED_BUDGET_MIN (20).
        self.assertEqual(strong.budget_scouting, 90)
        self.assertEqual(weak.budget_scouting, 20)

    def test_all_three_budget_fields_equal_per_team(self) -> None:
        from matches.league_views import _seed_team_budgets_by_strength

        a, _ = _fin03_team("Fin03TripA")
        b, _ = _fin03_team("Fin03TripB")
        c, _ = _fin03_team("Fin03TripC")
        self._pin_roster_overall(a, 80)
        self._pin_roster_overall(b, 50)
        self._pin_roster_overall(c, 30)

        _seed_team_budgets_by_strength([a, b, c])

        for team in (a, b, c):
            team.refresh_from_db()
            self.assertEqual(team.budget_scouting, team.budget_coaching)
            self.assertEqual(team.budget_scouting, team.budget_facilities)

    def test_single_team_gets_seed_budget_single(self) -> None:
        from matches.league_views import _seed_team_budgets_by_strength

        solo, _ = _fin03_team("Fin03Solo")
        self._pin_roster_overall(solo, 60)

        _seed_team_budgets_by_strength([solo])

        solo.refresh_from_db()
        self.assertEqual(solo.budget_scouting, 55)
        self.assertEqual(solo.budget_coaching, 55)
        self.assertEqual(solo.budget_facilities, 55)

    def test_rank_linear_spread_strongest_above_weakest(self) -> None:
        from matches.league_views import _seed_team_budgets_by_strength

        strong, _ = _fin03_team("Fin03SpreadStrong")
        mid, _ = _fin03_team("Fin03SpreadMid")
        weak, _ = _fin03_team("Fin03SpreadWeak")
        self._pin_roster_overall(strong, 95)
        self._pin_roster_overall(mid, 55)
        self._pin_roster_overall(weak, 15)

        _seed_team_budgets_by_strength([strong, mid, weak])

        strong.refresh_from_db()
        mid.refresh_from_db()
        weak.refresh_from_db()
        self.assertEqual(strong.budget_scouting, 90)
        self.assertEqual(weak.budget_scouting, 20)
        # The middle team lands strictly between the band ends (rank-linear).
        self.assertGreater(mid.budget_scouting, weak.budget_scouting)
        self.assertLess(mid.budget_scouting, strong.budget_scouting)


class TestFin03CreateSeedsBudgets(TestCase):
    """Finance-ON ``league_create`` seeds differentiated budgets across the
    generated teams, with ``current_team`` ALSO seeded; finance-OFF leaves every
    budget at the field default 34."""

    def test_finance_on_seeds_differentiated_budgets(self) -> None:
        payload = _fin03_payload(league_name="Fin03CreateDiff", num_teams="4")
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="Fin03CreateDiff")
        season = league.seasons.get()

        levels = sorted(t.budget_scouting for t in season.teams.all())
        # Seeding produced a rank-linear spread: strongest != weakest.
        self.assertEqual(levels[0], 20, "weakest team should seed to SEED_BUDGET_MIN")
        self.assertEqual(
            levels[-1], 90, "strongest team should seed to SEED_BUDGET_MAX"
        )
        self.assertGreater(
            levels[-1], levels[0], "finance-ON create should differentiate budgets"
        )

    def test_finance_on_all_three_fields_equal_per_team(self) -> None:
        payload = _fin03_payload(league_name="Fin03CreateEqual", num_teams="4")
        self.client.post(reverse("league_create"), payload)
        league = League.objects.get(name="Fin03CreateEqual")
        for team in league.seasons.get().teams.all():
            self.assertEqual(team.budget_scouting, team.budget_coaching)
            self.assertEqual(team.budget_scouting, team.budget_facilities)

    def test_finance_on_current_team_is_seeded_not_default(self) -> None:
        payload = _fin03_payload(league_name="Fin03CreateCurrent", num_teams="4")
        self.client.post(reverse("league_create"), payload)
        league = League.objects.get(name="Fin03CreateCurrent")
        league.refresh_from_db()
        current = league.current_team
        self.assertIsNotNone(current)
        current.refresh_from_db()
        # current_team is ALSO seeded — its budgets are NOT left at the 34 default.
        self.assertNotEqual(
            current.budget_scouting,
            34,
            "current_team must be seeded by strength, not left at the default",
        )
        self.assertEqual(current.budget_scouting, current.budget_coaching)
        self.assertEqual(current.budget_scouting, current.budget_facilities)

    def test_finance_off_leaves_budgets_at_default_34(self) -> None:
        payload = _valid_payload(league_name="Fin03CreateOff", num_teams="4")
        # No finance_enabled key => toggle OFF.
        response = self.client.post(reverse("league_create"), payload)
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="Fin03CreateOff")
        self.assertFalse(league.finance_enabled)
        for team in league.seasons.get().teams.all():
            self.assertEqual(team.budget_scouting, 34)
            self.assertEqual(team.budget_coaching, 34)
            self.assertEqual(team.budget_facilities, 34)
