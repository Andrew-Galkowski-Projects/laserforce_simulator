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
