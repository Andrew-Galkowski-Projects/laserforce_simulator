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
