"""LG-07a — Django ``TestCase`` tests for the member-night setup / drain views,
the ``play_member_night_task`` Celery drain (under ``CELERY_TASK_ALWAYS_EAGER``),
and the drawn-team game shape.

The BINDING seam contract is
``.claude/worktrees/lg-07-member-night-seam-contract.md`` §5 / §9. These tests
assert SCHEMA-level outcomes — Match counts, ``season_phase`` stamping,
``is_draw_team`` Teams, roster validity, completion derivation, the GAME-count
``{completed, total}`` task return, the PLAY-01 cancel, and the 302 / 405 / 409 /
202 status codes — and NEVER a raw simulated point total (member nights run a
FRESH ``random.Random()``, non-deterministic by design).

The drain tests run under the project's ``LF_CELERY_EAGER=1`` conftest (so
``.delay(...)`` runs synchronously) with a small ``ROUND_TICKS`` patch, exactly
as ``test_league_play.py`` does. Per the project rule the task / simulator are
NOT ``mock.patch``-ed (so signature drift surfaces as a real failure) — only
``matches.tasks._play_cancel_requested`` is patched, to drive the PLAY-01 cancel.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.member_night import MAX_GAMES, MIN_GAMES
from matches.models import League, Match, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

_FAST_TICKS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member_night_active_season(prefix: str, *, n_teams: int = 2, site: str = "SiteA"):
    """An active Season whose cursor is on an ordinal-1 ``member_night`` phase
    with a single VIABLE Site (``n_teams`` × 6 = 12 players all share ``site``).

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


def _plain_rr_active_season(prefix: str):
    """A phase-less active RR Season — its cursor is the implicit ``round_robin``
    phase, NOT a member night (used for the dashboard-error guard)."""
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    for i in range(2):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return league, season


def _real_player_ids(teams) -> set[int]:
    return set(Player.objects.filter(team__in=teams).values_list("id", flat=True))


def _post_setup(client, season, sites=("SiteA",)):
    """POST the multi-valued ``sites`` toggle-box field to ``member_night_setup``."""
    return client.post(
        reverse("member_night_setup", args=[season.id]), {"sites": list(sites)}
    )


# ===========================================================================
# member_night_setup
# ===========================================================================


class TestMemberNightSetup(TestCase):
    """``member_night_setup`` POST — draws games, creates drawn Teams + unplayed
    Match shells stamped ``season`` + ``season_phase=mn``, 302 to the dashboard."""

    def test_get_returns_405(self) -> None:
        _league, season, _teams, _mn = _member_night_active_season("MnSetGet")
        r = self.client.get(reverse("member_night_setup", args=[season.id]))
        self.assertEqual(r.status_code, 405)

    def test_dashboard_error_when_cursor_not_member_night(self) -> None:
        _league, season = _plain_rr_active_season("MnSetWrongPhase")
        r = _post_setup(self.client, season)
        # _render_season_dashboard_error re-renders the dashboard with status 400.
        self.assertEqual(r.status_code, 400)
        # Nothing was created.
        self.assertEqual(Match.objects.filter(season=season).count(), 0)

    def test_post_creates_shells_and_redirects(self) -> None:
        _league, season, _teams, mn = _member_night_active_season("MnSetOk")
        r = _post_setup(self.client, season)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("season_dashboard", args=[season.id]))
        shells = Match.objects.filter(season=season, season_phase=mn)
        # The per-Site game count is drawn in [MIN_GAMES, MAX_GAMES].
        self.assertGreaterEqual(shells.count(), MIN_GAMES)
        self.assertLessEqual(shells.count(), MAX_GAMES)

    def test_all_sites_present_token_draws(self) -> None:
        _league, season, _teams, mn = _member_night_active_season("MnSetAll")
        r = _post_setup(self.client, season)
        self.assertEqual(r.status_code, 302)
        self.assertGreaterEqual(
            Match.objects.filter(season_phase=mn).count(), MIN_GAMES
        )

    def test_shells_are_unplayed_and_stamped(self) -> None:
        _league, season, _teams, mn = _member_night_active_season("MnSetStamp")
        _post_setup(self.client, season)
        for m in Match.objects.filter(season_phase=mn):
            self.assertEqual(m.season_id, season.id)
            self.assertEqual(m.season_phase_id, mn.id)
            self.assertFalse(m.is_completed)

    def test_both_teams_are_draw_teams(self) -> None:
        _league, season, _teams, mn = _member_night_active_season("MnSetDraw")
        _post_setup(self.client, season)
        for m in Match.objects.filter(season_phase=mn).select_related(
            "team_red", "team_blue"
        ):
            self.assertTrue(m.team_red.is_draw_team)
            self.assertTrue(m.team_blue.is_draw_team)


# ===========================================================================
# Drawn-team game shape (§9 drawn-team shape)
# ===========================================================================


class TestMemberNightDrawnTeamShape(TestCase):
    """Each drawn Team is ``roster_errors``-valid (borrowed Players, no "does not
    belong" error) and its 6 slot FKs reference the REAL enrolled Players."""

    def setUp(self) -> None:
        _league, self.season, self.teams, self.mn = _member_night_active_season(
            "MnShape"
        )
        _post_setup(self.client, self.season)
        self.real_ids = _real_player_ids(self.teams)

    def test_drawn_teams_have_no_does_not_belong_error(self) -> None:
        for m in Match.objects.filter(season_phase=self.mn).select_related(
            "team_red", "team_blue"
        ):
            for team in (m.team_red, m.team_blue):
                belong_errors = [
                    e for e in team.roster_errors if "does not belong" in e
                ]
                self.assertEqual(
                    belong_errors, [], f"{team.name}: {team.roster_errors}"
                )

    def test_each_drawn_team_has_six_slots_filled_with_real_players(self) -> None:
        for m in Match.objects.filter(season_phase=self.mn).select_related(
            "team_red", "team_blue"
        ):
            for team in (m.team_red, m.team_blue):
                slot_ids = [
                    team.slot_commander_id,
                    team.slot_heavy_id,
                    team.slot_scout_1_id,
                    team.slot_scout_2_id,
                    team.slot_medic_id,
                    team.slot_ammo_id,
                ]
                # All 6 filled, distinct, and every borrowed Player is a REAL
                # enrolled Player (career stats stay unified — no clones).
                self.assertTrue(all(sid is not None for sid in slot_ids))
                self.assertEqual(len(set(slot_ids)), 6)
                for sid in slot_ids:
                    self.assertIn(sid, self.real_ids)


# ===========================================================================
# play_member_night — async enqueue view (202 / 409 / 405)
# ===========================================================================


class TestPlayMemberNight(TestCase):
    """``play_member_night`` POST → 202 ``{job_id, season_id}`` when an unplayed
    member-night Match exists; 409 otherwise; 405 on GET."""

    def test_get_returns_405(self) -> None:
        _league, season, _teams, _mn = _member_night_active_season("MnPlayGet")
        r = self.client.get(reverse("play_member_night", args=[season.id]))
        self.assertEqual(r.status_code, 405)

    def test_409_when_no_unplayed_member_night_match(self) -> None:
        # Cursor is on the member night (viable Site) but NO shells exist yet.
        _league, season, _teams, _mn = _member_night_active_season("MnPlay409")
        r = self.client.post(reverse("play_member_night", args=[season.id]))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json(), {"error": "No member night to play."})

    def test_202_with_job_id_and_season_id(self) -> None:
        _league, season, _teams, _mn = _member_night_active_season("MnPlay202")
        _post_setup(self.client, season)
        # Under EAGER the task drains during .delay; patch ROUND_TICKS so the
        # synchronous drain stays fast.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            r = self.client.post(reverse("play_member_night", args=[season.id]))
        self.assertEqual(r.status_code, 202)
        payload = r.json()
        self.assertIn("job_id", payload)
        self.assertEqual(payload["season_id"], season.id)


# ===========================================================================
# play_member_night_task — the drain (CELERY_TASK_ALWAYS_EAGER conftest)
# ===========================================================================


class TestPlayMemberNightTaskDrain(TestCase):
    """``play_member_night_task`` drains every shell to ``is_completed=True``,
    returns GAME counts, advances the cursor, and clears ``active_play_job_id``."""

    def _setup_with_shells(self, prefix: str):
        _league, season, teams, mn = _member_night_active_season(prefix)
        _post_setup(self.client, season)
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
        # The member night is the only phase ⇒ once complete the cursor advances
        # past it (no phase left).
        self.assertTrue(season._member_night_phase_complete(mn))
        self.assertIsNone(season.current_phase())

    def test_finally_clears_active_play_job_id(self) -> None:
        from matches.tasks import play_member_night_task

        season, _teams, _mn = self._setup_with_shells("MnDrainClear")
        season.active_play_job_id = "some-job-id"
        season.save(update_fields=["active_play_job_id"])
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.delay(season.id)
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)

    def test_play_real_players_get_player_round_state(self) -> None:
        from matches.models import GameRound, PlayerRoundState
        from matches.tasks import play_member_night_task

        season, teams, mn = self._setup_with_shells("MnDrainPRS")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.delay(season.id)
        # PlayerRoundState rows for the member-night Rounds reference the REAL
        # enrolled Players (career stats stay unified — borrowed, not cloned).
        real_ids = _real_player_ids(teams)
        prs_player_ids = set(
            PlayerRoundState.objects.filter(
                game_round__in=GameRound.objects.filter(match__season_phase=mn)
            ).values_list("player_id", flat=True)
        )
        self.assertTrue(prs_player_ids, "expected PlayerRoundState rows for the drain")
        self.assertTrue(prs_player_ids <= real_ids)


class TestPlayMemberNightTaskCancel(TestCase):
    """PLAY-01 — a mid-run cancel halts the drain leaving played games committed,
    returns ``cancelled: True`` with partial counts, and clears the job marker."""

    def test_top_cancel_returns_immediately_without_playing(self) -> None:
        from matches import tasks as _tasks
        from matches.tasks import play_member_night_task

        _league, season, _teams, mn = _member_night_active_season("MnCancelTop")
        _post_setup(self.client, season)
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
        _post_setup(self.client, season)
        n = Match.objects.filter(season_phase=mn).count()
        self.assertGreaterEqual(n, MIN_GAMES)  # >= 5 shells ⇒ a mid-run cancel
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

        with patch.object(_tasks, "_play_cancel_requested", _cancel):
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                result = play_member_night_task.delay(season.id)

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


# ===========================================================================
# play_member_night_single — SYNC play of ONE member-night game (302)
# ===========================================================================


class TestPlayMemberNightSingle(TestCase):
    """``play_member_night_single`` POST drains exactly ONE unplayed shell, 302s
    to the dashboard, leaves the rest unplayed; 405 on GET; dashboard-error when
    nothing to play."""

    def test_get_returns_405(self) -> None:
        _league, season, _teams, _mn = _member_night_active_season("MnSingleGet")
        r = self.client.get(reverse("play_member_night_single", args=[season.id]))
        self.assertEqual(r.status_code, 405)

    def test_dashboard_error_when_no_unplayed(self) -> None:
        # Cursor on the member night but NO shells set up yet.
        _league, season, _teams, _mn = _member_night_active_season("MnSingleNone")
        r = self.client.post(reverse("play_member_night_single", args=[season.id]))
        self.assertEqual(r.status_code, 400)

    def test_plays_exactly_one_game_and_redirects(self) -> None:
        _league, season, _teams, mn = _member_night_active_season("MnSingleOne")
        _post_setup(self.client, season)
        total = Match.objects.filter(season_phase=mn).count()
        self.assertGreaterEqual(total, MIN_GAMES)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            r = self.client.post(reverse("play_member_night_single", args=[season.id]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("season_dashboard", args=[season.id]))
        # Exactly ONE shell is now complete; the rest stay unplayed (resumable).
        self.assertEqual(
            Match.objects.filter(season_phase=mn, is_completed=True).count(), 1
        )
        self.assertEqual(
            Match.objects.filter(season_phase=mn, is_completed=False).count(),
            total - 1,
        )


# ===========================================================================
# play_member_night_live — SYNC play of ONE game + the live-watch handoff
# ===========================================================================


class TestPlayMemberNightLive(TestCase):
    """``play_member_night_live`` drains ONE shell at ``fidelity="full"``, stashes
    the 2 Round ids in the ``live_watch`` session (``kind="member_night"``), and
    redirects to ``play_week_live_watch``; 405 on GET; error when nothing to play."""

    def test_get_returns_405(self) -> None:
        _league, season, _teams, _mn = _member_night_active_season("MnLiveGet")
        r = self.client.get(reverse("play_member_night_live", args=[season.id]))
        self.assertEqual(r.status_code, 405)

    def test_dashboard_error_when_no_unplayed(self) -> None:
        _league, season, _teams, _mn = _member_night_active_season("MnLiveNone")
        r = self.client.post(reverse("play_member_night_live", args=[season.id]))
        self.assertEqual(r.status_code, 400)

    def test_plays_one_game_and_redirects_to_watch_with_session(self) -> None:
        _league, season, _teams, mn = _member_night_active_season("MnLiveOne")
        _post_setup(self.client, season)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            r = self.client.post(reverse("play_member_night_live", args=[season.id]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("play_week_live_watch", args=[season.id]))
        # One game committed; the live-watch session handoff carries its 2 Rounds.
        self.assertEqual(
            Match.objects.filter(season_phase=mn, is_completed=True).count(), 1
        )
        watch = self.client.session.get("live_watch")
        self.assertIsInstance(watch, dict)
        self.assertEqual(watch["season_id"], season.id)
        self.assertEqual(watch["kind"], "member_night")
        self.assertEqual(len(watch["round_ids"]), 2)


# ===========================================================================
# Schedule surfaces — League schedule + Team schedule member-night sections
# ===========================================================================


class TestMemberNightScheduleSurfaces(TestCase):
    """LG-07 — member-night games surface on the League schedule (per
    member_night phase) and on the Team schedule (games where one of the Team's
    Players appeared). Read-only / derived; the drawn-team Matches are
    Standings-excluded but visible here."""

    def test_league_schedule_shows_member_night_section(self) -> None:
        _league, season, _teams, mn = _member_night_active_season("MnSchedLeague")
        _post_setup(self.client, season)  # unplayed shells exist
        r = self.client.get(reverse("season_schedule", args=[season.id]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('id="season-schedule-member-nights"', body)
        self.assertIn(f'id="season-schedule-member-night-{mn.ordinal}"', body)
        a_game = Match.objects.filter(season_phase=mn).first()
        self.assertIn(f'id="member-night-game-{a_game.id}"', body)

    def test_league_schedule_absent_with_no_member_night(self) -> None:
        _league, season = _plain_rr_active_season("MnSchedNone")
        body = self.client.get(
            reverse("season_schedule", args=[season.id])
        ).content.decode()
        self.assertNotIn('id="season-schedule-member-nights"', body)

    def test_team_schedule_shows_member_night_appearances(self) -> None:
        from matches.tasks import play_member_night_task

        _league, season, teams, _mn = _member_night_active_season("MnSchedTeam")
        _post_setup(self.client, season)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.delay(season.id)  # commit the games (PRS rows)
        team = teams[0]
        r = self.client.get(
            reverse(
                "team_schedule",
                kwargs={"league_id": season.league_id, "team_id": team.id},
            )
        )
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('id="team-schedule-member-nights"', body)
        # At least one of this Team's Players is listed as having played.
        player_names = list(
            Player.objects.filter(team=team).values_list("name", flat=True)
        )
        self.assertTrue(
            any(name in body for name in player_names),
            "expected one of the Team's players in a member-night appearance",
        )


class TestMemberNightScoreboard(TestCase):
    """LG-07 — a member-night game's Round scoreboard shows the borrowed Players.

    Regression: ``game_round_detail`` grouped by ``player__team``, but a drawn
    team BORROWS Players (their ``Player.team`` is their real team), so both
    sides showed 0 players. Grouping by ``team_color`` fixes it."""

    def test_round_detail_shows_borrowed_players_per_side(self) -> None:
        from matches.models import GameRound
        from matches.tasks import play_member_night_task

        _league, season, _teams, mn = _member_night_active_season("MnScoreboard")
        _post_setup(self.client, season)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_member_night_task.delay(season.id)
        game_round = GameRound.objects.filter(match__season_phase=mn).first()
        self.assertIsNotNone(game_round)
        r = self.client.get(reverse("game_round_detail", args=[game_round.id]))
        self.assertEqual(r.status_code, 200)
        # Both drawn sides show their 6 borrowed Players (not 0).
        self.assertEqual(len(r.context["red_players"]), 6)
        self.assertEqual(len(r.context["blue_players"]), 6)
