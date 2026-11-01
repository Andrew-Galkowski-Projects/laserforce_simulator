"""LG-07a -- the ``play_member_night_task`` Celery drain (under
``CELERY_TASK_ALWAYS_EAGER``) and the drawn-team scoreboard.

ANG-08: the HTML member-night setup / play views and their tests went; the
API covers setup, play-all, single, live and the guards in
``test_api_member_night.py``. The member night is drawn here through the
service (``league_play_services.setup_member_night``).

These tests assert SCHEMA-level outcomes -- Match counts, ``season_phase``
stamping, completion derivation, the GAME-count ``{completed, total}`` task
return and the PLAY-01 cancel -- and NEVER a raw simulated point total (member
nights run a FRESH ``random.Random()``, non-deterministic by design).

The drain tests run under the project's ``LF_CELERY_EAGER=1`` conftest (so
``.delay(...)`` runs synchronously) with a small ``ROUND_TICKS`` patch. Per the
project rule the task / simulator are NOT ``mock.patch``-ed (so signature drift
surfaces as a real failure) -- only ``matches.tasks._play_cancel_requested`` is
patched, to drive the PLAY-01 cancel.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase

from matches import league_play_services
from matches.member_night import MIN_GAMES
from matches.models import League, Match, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Player

_FAST_TICKS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member_night_active_season(prefix: str, *, n_teams: int = 2, site: str = "SiteA"):
    """An active Season whose cursor is on an ordinal-1 ``member_night`` phase
    with a single VIABLE Site (``n_teams`` x 6 = 12 players all share ``site``).

    Returns ``(league, season, teams, mn_phase)``.
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    Player.objects.filter(team__in=teams).update(home_site=site)
    mn = SeasonPhase.objects.create(season=season, ordinal=1, phase_type="member_night")
    season.start_season()
    season.refresh_from_db()
    return league, season, teams, mn


def _real_player_ids(teams) -> set[int]:
    return set(Player.objects.filter(team__in=teams).values_list("id", flat=True))


def _service_setup(season, sites=("SiteA",)) -> int:
    """Draw the member night through the service the API's setup uses."""
    return league_play_services.setup_member_night(season, list(sites))


# ===========================================================================
# play_member_night_task -- the drain (CELERY_TASK_ALWAYS_EAGER conftest)
# ===========================================================================


class TestPlayMemberNightTaskDrain(TestCase):
    """``play_member_night_task`` drains every shell to ``is_completed=True``,
    returns GAME counts, advances the cursor, and clears ``active_play_job_id``."""

    def _setup_with_shells(self, prefix: str):
        _league, season, teams, mn = _member_night_active_season(prefix)
        _service_setup(season)
        return season, teams, mn

    def test_drains_all_shells_to_completed(self) -> None:
        from matches.tasks import play_member_night_task

        season, _teams, mn = self._setup_with_shells("MnDrainAll")
        n = Match.objects.filter(season_phase=mn).count()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.delay(season.id)
        # Every member-night Match is now complete.
        self.assertEqual(
            Match.objects.filter(season_phase=mn, is_completed=False).count(), 0
        )
        self.assertEqual(
            Match.objects.filter(season_phase=mn, is_completed=True).count(), n
        )

    def test_returns_game_count_completed_total(self) -> None:
        from matches.tasks import play_member_night_task

        season, _teams, mn = self._setup_with_shells("MnDrainCount")
        n = Match.objects.filter(season_phase=mn).count()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_member_night_task.delay(season.id)
        self.assertEqual(result.state, "SUCCESS")
        # GAME counts (NOT round counts): completed == total == number of games.
        self.assertEqual(result.result["completed"], n)
        self.assertEqual(result.result["total"], n)

    def test_phase_completes_and_cursor_advances(self) -> None:
        from matches.tasks import play_member_night_task

        season, _teams, mn = self._setup_with_shells("MnDrainAdvance")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.delay(season.id)
        season.refresh_from_db()
        # The member night is the only phase => once complete the cursor advances
        # past it (no phase left).
        self.assertTrue(season._member_night_phase_complete(mn))
        self.assertIsNone(season.current_phase())

    def test_finally_clears_active_play_job_id(self) -> None:
        from matches.tasks import play_member_night_task

        season, _teams, _mn = self._setup_with_shells("MnDrainClear")
        season.active_play_job_id = "some-job-id"
        season.save(update_fields=["active_play_job_id"])
        # ANG-01d-2 (ADR-0040): the finally clears only the task's OWN job id,
        # so the task must run under the id the enqueue recorded.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.apply_async(
                kwargs={"season_id": season.id}, task_id="some-job-id"
            )
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)

    def test_play_real_players_get_player_round_state(self) -> None:
        from matches.models import GameRound, PlayerRoundState
        from matches.tasks import play_member_night_task

        season, teams, mn = self._setup_with_shells("MnDrainPRS")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.delay(season.id)
        # PlayerRoundState rows for the member-night Rounds reference the REAL
        # enrolled Players (career stats stay unified -- borrowed, not cloned).
        real_ids = _real_player_ids(teams)
        prs_player_ids = set(
            PlayerRoundState.objects.filter(
                game_round__in=GameRound.objects.filter(match__season_phase=mn)
            ).values_list("player_id", flat=True)
        )
        self.assertTrue(prs_player_ids, "expected PlayerRoundState rows for the drain")
        self.assertTrue(prs_player_ids <= real_ids)


class TestPlayMemberNightTaskCancel(TestCase):
    """PLAY-01 -- a mid-run cancel halts the drain leaving played games committed,
    returns ``cancelled: True`` with partial counts, and clears the job marker."""

    def test_top_cancel_returns_immediately_without_playing(self) -> None:
        from matches import tasks as _tasks
        from matches.tasks import play_member_night_task

        _league, season, _teams, mn = _member_night_active_season("MnCancelTop")
        _service_setup(season)
        n = Match.objects.filter(season_phase=mn).count()

        with patch.object(_tasks, "_play_cancel_requested", lambda sid: True):
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                result = play_member_night_task.delay(season.id)
        self.assertEqual(result.result.get("cancelled"), True)
        self.assertEqual(result.result["completed"], 0)
        self.assertEqual(result.result["total"], n)
        # No game was played.
        self.assertEqual(
            Match.objects.filter(season_phase=mn, is_completed=True).count(), 0
        )

    def test_mid_run_cancel_commits_played_games_only(self) -> None:
        from matches import tasks as _tasks
        from matches.tasks import play_member_night_task

        _league, season, _teams, mn = _member_night_active_season("MnCancelMid")
        _service_setup(season)
        n = Match.objects.filter(season_phase=mn).count()
        self.assertGreaterEqual(n, MIN_GAMES)  # >= 5 shells => a mid-run cancel
        season.active_play_job_id = "job-mid"
        season.save(update_fields=["active_play_job_id"])

        # _play_cancel_requested is called: top (call 0), then before each shell
        # (call 1 = game 0, call 2 = game 1, ...). Cancel on the 3rd call so
        # exactly ONE game plays, then the drain stops.
        state = {"n": 0}

        def _cancel(season_id):
            i = state["n"]
            state["n"] += 1
            return i >= 2

        # ANG-01d-2 (ADR-0040): run under the recorded id -- the finally clears
        # only the task's OWN job id.
        with patch.object(_tasks, "_play_cancel_requested", _cancel):
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                result = play_member_night_task.apply_async(
                    kwargs={"season_id": season.id}, task_id="job-mid"
                )

        self.assertEqual(result.result.get("cancelled"), True)
        self.assertEqual(result.result["completed"], 1)
        self.assertEqual(result.result["total"], n)
        # Exactly one game committed; the rest stay unplayed (resumable).
        self.assertEqual(
            Match.objects.filter(season_phase=mn, is_completed=True).count(), 1
        )
        self.assertEqual(
            Match.objects.filter(season_phase=mn, is_completed=False).count(), n - 1
        )
        # The job marker is cleared in the finally even on a cancel return.
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)


class TestMemberNightScoreboard(TestCase):
    """LG-07 -- a member-night game's Round scoreboard shows the borrowed Players.

    Regression: the scoreboard once grouped by ``player__team``, but a drawn
    team BORROWS Players (their ``Player.team`` is their real team), so both
    sides showed 0 players. Grouping by ``team_color`` fixes it; read through
    ``GET /api/rounds/<id>/``."""

    def test_round_scoreboard_shows_borrowed_players_per_side(self) -> None:
        from matches.models import GameRound
        from matches.tasks import play_member_night_task

        _league, season, _teams, mn = _member_night_active_season("MnScoreboard")
        _service_setup(season)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.delay(season.id)
        game_round = GameRound.objects.filter(match__season_phase=mn).first()
        self.assertIsNotNone(game_round)
        r = self.client.get(f"/api/rounds/{game_round.id}/")
        self.assertEqual(r.status_code, 200, r.content)
        board = r.json()["data"]["scoreboard"]
        # Both drawn sides show their 6 borrowed Players (not 0).
        self.assertEqual(len(board["red"]["players"]), 6)
        self.assertEqual(len(board["blue"]["players"]), 6)
