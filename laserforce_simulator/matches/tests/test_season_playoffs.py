"""LG-02-Part2c-1 — ``play_playoffs_task`` + the two playoff play views.

Seam contract ``.claude/worktrees/lg-02-part2c-1-seam-contract.md`` §3 + §4 + §8:

  - ``play_playoffs_task`` (under the existing ``CELERY_TASK_ALWAYS_EAGER``
    conftest) drains a built bracket to a champion, flips the Season to
    ``completed`` with ``champion_team == tournament.champion``, and returns
    ``{"completed", "total"}`` STAGE counts.
  - ``play_single_round`` (sync): POST → 302 + exactly ONE playoff Match
    advanced via one node; GET → 405; non-tournament-phase → dashboard
    error re-render (``play_error``), no advance.
  - ``play_playoffs`` (async): POST → 202 + ``{job_id, season_id}``;
    phase-mismatch → 409 JSON ``{"error"}``; GET → 405.

Tests assert SCHEMA-LEVEL outcomes (state, champion identity, node-resolution
deltas, status codes, JSON keys) — NEVER exact simulated point totals
(tournament sims are non-deterministic). N=4 small seeded sims. The RR is
played via the real ``simulate_scheduled_round`` (which auto-builds the
tournament phase) under a small ``ROUND_TICKS`` patch.

These assertions WILL fail until the Code agent lands
``play_playoffs_task`` + ``play_single_round`` + ``play_playoffs`` + the
``play-single-round/`` / ``play-playoffs/`` routes; that is the TDD red state.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import BracketNode, League, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 30


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _rr_tournament_season(prefix: str, n: int = 4):
    """An active Season: ordinal-1 ``round_robin`` + ordinal-2 ``tournament``
    SeasonPhase, ``n`` slotted teams enrolled, started.

    Returns ``(season, teams, tournament_phase)``.
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    tournament_phase = SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, tournament_phase


def _play_rr(season: Season, teams: list) -> None:
    """Play every RR fixture (auto-builds the tournament phase on completion)."""
    by_id = {t.id: t for t in teams}
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        # Mirror the production play loop: tag each Match with its owning RR
        # phase (LG-02-Part2c-2 per-phase completion scopes by season_phase).
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                team_a = by_id[fixture.team_a_id]
                team_b = by_id[fixture.team_b_id]
                sim.simulate_scheduled_round(
                    season,
                    team_a,
                    team_b,
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )


def _built_playoff_season(prefix: str, n: int = 4):
    """An active Season whose RR is complete and tournament phase is built+active."""
    season, teams, tournament_phase = _rr_tournament_season(prefix, n)
    _play_rr(season, teams)
    season.refresh_from_db()
    tournament_phase.refresh_from_db()
    return season, teams, tournament_phase


def _resolved_node_count(tournament) -> int:
    """Count bracket nodes that have a winner (resolved or bye)."""
    return BracketNode.objects.filter(
        tournament=tournament, winner__isnull=False
    ).count()


# ---------------------------------------------------------------------------
# play_playoffs_task — drains a built bracket to a champion (EAGER)
# ---------------------------------------------------------------------------


class TestPlayPlayoffsTask(TestCase):
    """``play_playoffs_task.apply(args=(season_id,))`` under EAGER."""

    def test_drains_to_champion_and_completes_season(self) -> None:
        from matches.tasks import play_playoffs_task

        season, _teams, tournament_phase = _built_playoff_season("TaskDrain")
        self.assertIsNotNone(tournament_phase.tournament_id)
        self.assertEqual(tournament_phase.tournament.state, "active")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(season.id,))

        season.refresh_from_db()
        tournament_phase.refresh_from_db()
        tournament = tournament_phase.tournament

        # Tournament drained to a champion.
        self.assertEqual(tournament.state, "completed")
        self.assertIsNotNone(tournament.champion_id)
        # Season crowned with the tournament champion.
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tournament.champion_id)

        # Return shape: {"completed", "total"} STAGE counts.
        payload = result.get()
        self.assertIn("completed", payload)
        self.assertIn("total", payload)
        self.assertIsInstance(payload["completed"], int)
        self.assertIsInstance(payload["total"], int)
        # A fully-drained single-elim bracket has all stages complete.
        self.assertGreater(payload["total"], 0)
        self.assertEqual(payload["completed"], payload["total"])

    def test_returns_zero_counts_when_no_built_playoff(self) -> None:
        from matches.tasks import play_playoffs_task

        # RR not played ⇒ current phase is the RR phase, not a built tournament.
        season, _teams, _tp = _rr_tournament_season("TaskNoop")
        result = play_playoffs_task.apply(args=(season.id,))
        payload = result.get()
        self.assertEqual(payload, {"completed": 0, "total": 0})
        season.refresh_from_db()
        self.assertEqual(season.state, "active")


# ---------------------------------------------------------------------------
# play_single_round — sync, one playoff Match per POST
# ---------------------------------------------------------------------------


class TestPlaySingleRound(TestCase):
    """POST ``/seasons/<id>/play-single-round/`` plays ONE playoff Match."""

    def test_post_advances_exactly_one_node_and_redirects(self) -> None:
        season, _teams, tournament_phase = _built_playoff_season("SingleAdv")
        tournament = tournament_phase.tournament
        before = _resolved_node_count(tournament)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_single_round", args=[season.id]))

        self.assertEqual(response.status_code, 302)
        tournament.refresh_from_db()
        after = _resolved_node_count(tournament)
        # Exactly one additional node resolved (one playoff Match via one node).
        self.assertEqual(after, before + 1)

    def test_post_redirects_to_season_dashboard(self) -> None:
        season, _teams, _tp = _built_playoff_season("SingleRedir")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_single_round", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("season_dashboard", args=[season.id])
        )

    def test_get_returns_405(self) -> None:
        season, _teams, _tp = _built_playoff_season("SingleGet")
        response = self.client.get(reverse("play_single_round", args=[season.id]))
        self.assertEqual(response.status_code, 405)

    def test_post_on_non_tournament_phase_renders_dashboard_error_no_advance(
        self,
    ) -> None:
        # RR not yet played ⇒ current phase is the RR phase (no built playoff).
        season, _teams, _tp = _rr_tournament_season("SingleNoPlayoff")
        response = self.client.post(reverse("play_single_round", args=[season.id]))
        # Dashboard re-render carrying a play_error (no redirect to dashboard).
        self.assertIsNotNone(response.context.get("play_error"))
        # No tournament was built / no node advanced.
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_post_missing_season_returns_404(self) -> None:
        response = self.client.post(reverse("play_single_round", args=[9_999_999]))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# play_playoffs — async, 202 / 409 / 405
# ---------------------------------------------------------------------------


class TestPlayPlayoffs(TestCase):
    """POST ``/seasons/<id>/play-playoffs/`` enqueues the drain task."""

    def test_post_returns_202_with_job_id_and_season_id(self) -> None:
        season, _teams, _tp = _built_playoff_season("PlayoffsOk")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_playoffs", args=[season.id]))
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertIn("job_id", payload)
        self.assertIn("season_id", payload)
        self.assertEqual(payload["season_id"], season.id)
        self.assertIsInstance(payload["job_id"], str)

    def test_post_on_non_tournament_phase_returns_409(self) -> None:
        season, _teams, _tp = _rr_tournament_season("PlayoffsMismatch")
        response = self.client.post(reverse("play_playoffs", args=[season.id]))
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertIn("error", payload)

    def test_get_returns_405(self) -> None:
        season, _teams, _tp = _built_playoff_season("PlayoffsGet")
        response = self.client.get(reverse("play_playoffs", args=[season.id]))
        self.assertEqual(response.status_code, 405)

    def test_post_missing_season_returns_404(self) -> None:
        response = self.client.post(reverse("play_playoffs", args=[9_999_999]))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# LG-02-Part2c-2 — multi-RR → playoff cumulative-seed regression
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-2-seam-contract.md`` §7:
# a TWO-RR-phase Season then a trailing tournament auto-builds the playoff
# seeded by the CUMULATIVE standings (both RR phases) once RR2 completes, then
# drains to the Season champion — alongside the existing single-RR playoff
# regression above. Appended as a NEW class; no existing class is modified.


def _multi_rr_tournament_season(prefix: str, n: int = 4):
    """An active Season: RR1 (ordinal 1) + RR2 (ordinal 2) + tournament
    (ordinal 3), ``n`` slotted teams enrolled, started.

    Returns ``(season, teams, rr1, rr2, tournament_phase)``.
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    rr1 = SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    rr2 = SeasonPhase.objects.create(season=season, ordinal=2, phase_type="round_robin")
    tournament_phase = SeasonPhase.objects.create(
        season=season, ordinal=3, phase_type="tournament"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr1, rr2, tournament_phase


def _play_one_phase(season, teams, phase) -> None:
    by_id = {t.id: t for t in teams}
    fixtures = None
    for candidate, phase_fixtures in season.scheduled_fixtures_by_phase():
        if candidate.pk == phase.pk:
            fixtures = phase_fixtures
            break
    assert fixtures is not None
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for fixture in fixtures:
            sim.simulate_scheduled_round(
                season,
                by_id[fixture.team_a_id],
                by_id[fixture.team_b_id],
                fixture.round_number,
                season_phase=phase,
            )


def _drain(tournament) -> None:
    from matches.tournament_engine import play_next_node

    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(200):
            if play_next_node(tournament) is None:
                break
    tournament.refresh_from_db()


class TestMultiRrPlayoffCumulativeSeedRegression(TestCase):
    """A two-RR-phase Season's trailing playoff seeds from CUMULATIVE standings
    and drains to the Season champion."""

    def test_playoff_not_built_until_rr2_done(self) -> None:
        season, teams, rr1, _rr2, tp = _multi_rr_tournament_season("MrrBuild", n=4)
        _play_one_phase(season, teams, rr1)
        tp.refresh_from_db()
        # RR1 complete but RR2 not — no playoff yet.
        self.assertIsNone(tp.tournament_id)

    def test_playoff_seeds_match_cumulative_standings(self) -> None:
        season, teams, rr1, rr2, tp = _multi_rr_tournament_season("MrrSeed", n=4)
        _play_one_phase(season, teams, rr1)
        _play_one_phase(season, teams, rr2)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)

        from matches.models import TournamentParticipant

        participants = list(
            TournamentParticipant.objects.filter(tournament=tp.tournament).order_by(
                "seed"
            )
        )
        self.assertEqual(len(participants), len(teams))
        rows = season._final_standings_for_phase(rr2)
        rank_to_team = {row.rank: row.team_id for row in rows}
        for p in participants:
            self.assertEqual(p.team_id, rank_to_team[p.seed])

    def test_draining_playoff_crowns_season_champion(self) -> None:
        season, teams, rr1, rr2, tp = _multi_rr_tournament_season("MrrCrown", n=4)
        _play_one_phase(season, teams, rr1)
        _play_one_phase(season, teams, rr2)
        tp.refresh_from_db()
        _drain(tp.tournament)
        # The raw engine drains the bracket; complete_if_finished stamps the
        # Season champion (mirrors the Part2c-1 single-RR playoff precedent).
        season.complete_if_finished()
        season.refresh_from_db()
        tp.refresh_from_db()
        self.assertEqual(tp.tournament.state, "completed")
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)
