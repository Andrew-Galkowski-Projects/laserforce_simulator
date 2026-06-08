"""LG-02-Part2c-2 — multi-round-robin Season DB-integration tests.

Seam contract ``.claude/worktrees/lg-02-part2c-2-seam-contract.md`` §7 + §8.
These pin the PUBLIC seam of the multi-RR slice against its locked names:

  * ``Match.season_phase`` FK (nullable, ``SET_NULL``, reverse accessor
    ``phase.matches``; deleting a ``SeasonPhase`` SET_NULLs its Matches).
  * Per-phase RR completion — ``_phase_complete(rr1)`` is True only once RR1's
    fixtures are played (scoped by ``match__season_phase=rr1``); ``current_phase()``
    returns RR2 only after RR1 is complete (the cursor finishes RR1 before RR2).
  * Find-or-create distinctness — the SAME pairing in RR1 vs RR2 creates TWO
    DISTINCT Matches (both ``season=<season>``, different ``season_phase``);
    re-running a ``(season, phase, pairing)`` is idempotent.
  * ``scheduled_fixtures_by_phase()`` — one ``(phase, fixtures)`` tuple per RR
    phase in ordinal order with a monotonic, non-overlapping global matchday
    calendar; ``scheduled_fixtures()`` (flat) == the concatenation.
  * Cumulative standings — ``_final_standings_for_phase`` aggregates BOTH RR
    phases (whole-season filter).
  * RR1 -> RR2 -> playoff end-to-end — the playoff auto-builds seeded by the
    cumulative standings (via ``activate_pending_tournament_phase``) and drains
    to a Season champion.

Tests assert SCHEMA-LEVEL outcomes (Match counts, ``season_phase_id`` attribution,
``state``, ``champion_team`` id, bracket-node winners) — NEVER exact simulated
point totals (tournament sims are non-deterministic). Small-N seeded sims (N=2/3).

These assertions WILL fail until the Code agent lands the by-phase play loop +
the find-or-create phase key + the per-phase completion wiring; that is the TDD
red state, not a defect in this file.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.db.models import ForeignKey
from django.test import TestCase

from matches.models import (
    BracketNode,
    GameRound,
    League,
    Match,
    Season,
    SeasonPhase,
)
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 25


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _two_rr_season(prefix: str, n: int = 2):
    """An active Season composed of ordinal-1 + ordinal-2 ``round_robin``
    phases (NO trailing tournament), ``n`` slotted teams enrolled, started.

    Returns ``(season, teams, rr1, rr2)``.
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
    rr1 = SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    rr2 = SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="round_robin"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr1, rr2


def _two_rr_playoff_season(prefix: str, n: int = 4):
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
    rr1 = SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    rr2 = SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="round_robin"
    )
    tournament_phase = SeasonPhase.objects.create(
        season=season, ordinal=3, phase_type="tournament"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr1, rr2, tournament_phase


def _play_phase(season, teams, phase) -> None:
    """Drive ``simulate_scheduled_round`` over every fixture of ONE phase,
    passing the owning phase so each Round's Match is attributed correctly.
    """
    by_id = {t.id: t for t in teams}
    fixtures = None
    for candidate, phase_fixtures in season.scheduled_fixtures_by_phase():
        if candidate.pk == phase.pk:
            fixtures = phase_fixtures
            break
    assert fixtures is not None, "phase has no fixtures in scheduled_fixtures_by_phase"
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for fixture in fixtures:
            team_a = by_id[fixture.team_a_id]
            team_b = by_id[fixture.team_b_id]
            sim.simulate_scheduled_round(
                season,
                team_a,
                team_b,
                fixture.round_number,
                season_phase=phase,
            )


def _drain_tournament(tournament) -> None:
    """Drain a built tournament bracket to a champion via the real engine."""
    from matches.tournament_engine import play_next_node

    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(200):
            if play_next_node(tournament) is None:
                break
    tournament.refresh_from_db()


# ---------------------------------------------------------------------------
# Match.season_phase FK schema
# ---------------------------------------------------------------------------


class TestMatchSeasonPhaseFK(TestCase):
    """``Match.season_phase`` is a nullable SET_NULL FK with reverse
    accessor ``phase.matches``."""

    def test_field_is_nullable_blank_set_null(self) -> None:
        field = Match._meta.get_field("season_phase")
        self.assertIsInstance(field, ForeignKey)
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        from django.db.models import SET_NULL

        self.assertEqual(field.remote_field.on_delete, SET_NULL)

    def test_reverse_accessor_is_phase_matches(self) -> None:
        field = Match._meta.get_field("season_phase")
        self.assertEqual(field.remote_field.related_name, "matches")
        # The reverse accessor resolves on a SeasonPhase instance.
        season, _teams, rr1, _rr2 = _two_rr_season("FkReverse")
        self.assertEqual(rr1.matches.count(), 0)

    def test_default_match_has_null_season_phase(self) -> None:
        red, _ = make_team_with_slots("FkDefRed")
        blue, _ = make_team_with_slots("FkDefBlue")
        match = Match.objects.create(team_red=red, team_blue=blue)
        match.refresh_from_db()
        self.assertIsNone(match.season_phase_id)

    def test_deleting_phase_set_nulls_its_matches_not_cascade(self) -> None:
        season, _teams, rr1, _rr2 = _two_rr_season("FkSetNull")
        red, _ = make_team_with_slots("FkSNRed")
        blue, _ = make_team_with_slots("FkSNBlue")
        match = Match.objects.create(
            season=season, season_phase=rr1, team_red=red, team_blue=blue
        )
        match_pk = match.pk
        rr1.delete()
        # The Match survives; its season_phase is nulled (NOT cascaded out).
        match = Match.objects.get(pk=match_pk)
        self.assertIsNone(match.season_phase_id)


# ---------------------------------------------------------------------------
# scheduled_fixtures_by_phase — ordinal order + monotonic global calendar
# ---------------------------------------------------------------------------


class TestScheduledFixturesByPhase(TestCase):
    """``scheduled_fixtures_by_phase()`` returns one tuple per RR phase in
    ordinal order with a monotonic, non-overlapping global matchday calendar.
    """

    def test_one_tuple_per_rr_phase_in_ordinal_order(self) -> None:
        season, _teams, rr1, rr2 = _two_rr_season("ByPhaseOrder", n=3)
        by_phase = season.scheduled_fixtures_by_phase()
        self.assertEqual(len(by_phase), 2)
        self.assertEqual([phase.pk for phase, _ in by_phase], [rr1.pk, rr2.pk])
        for _phase, fixtures in by_phase:
            self.assertGreater(len(fixtures), 0)

    def test_phase2_matchdays_offset_past_phase1_no_overlap(self) -> None:
        season, _teams, _rr1, _rr2 = _two_rr_season("ByPhaseOffset", n=3)
        by_phase = season.scheduled_fixtures_by_phase()
        (_p1, fixtures1), (_p2, fixtures2) = by_phase
        max_md_1 = max(f.matchday for f in fixtures1)
        min_md_2 = min(f.matchday for f in fixtures2)
        # RR2's first matchday is strictly after RR1's last matchday.
        self.assertGreater(min_md_2, max_md_1)

    def test_global_matchday_calendar_is_monotonic_1_to_n(self) -> None:
        season, _teams, _rr1, _rr2 = _two_rr_season("ByPhaseMono", n=3)
        flat = season.scheduled_fixtures()
        matchdays = sorted({f.matchday for f in flat})
        # Distinct matchdays form a contiguous 1..N run (no gaps, no overlap).
        self.assertEqual(matchdays, list(range(1, len(matchdays) + 1)))

    def test_scheduled_fixtures_flat_equals_concatenation(self) -> None:
        season, _teams, _rr1, _rr2 = _two_rr_season("ByPhaseConcat", n=3)
        by_phase = season.scheduled_fixtures_by_phase()
        concat = []
        for _phase, fixtures in by_phase:
            concat.extend(fixtures)
        self.assertEqual(season.scheduled_fixtures(), concat)

    def test_flat_fixture_count_is_double_single_rr(self) -> None:
        # A two-RR-phase Season schedules twice the fixtures of one RR phase.
        season, _teams, _rr1, _rr2 = _two_rr_season("ByPhaseDouble", n=3)
        by_phase = season.scheduled_fixtures_by_phase()
        (_p1, fixtures1), (_p2, fixtures2) = by_phase
        # Both RR phases over the same team set produce equal-length lists.
        self.assertEqual(len(fixtures1), len(fixtures2))
        self.assertEqual(len(season.scheduled_fixtures()), 2 * len(fixtures1))

    def test_less_than_two_teams_returns_empty(self) -> None:
        league = League.objects.create(name="ByPhaseSolo League")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        t, _ = make_team_with_slots("ByPhaseSolo")
        season.teams.add(t)
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        SeasonPhase.objects.create(season=season, ordinal=2, phase_type="round_robin")
        self.assertEqual(season.scheduled_fixtures_by_phase(), [])
        self.assertEqual(season.scheduled_fixtures(), [])


# ---------------------------------------------------------------------------
# Per-phase RR completion + cursor finishes RR1 before RR2
# ---------------------------------------------------------------------------


class TestPerPhaseCompletion(TestCase):
    """``_phase_complete(rr1)`` is True only once RR1 fixtures are played, and
    ``current_phase()`` returns RR2 only after RR1 is complete."""

    def test_rr1_incomplete_before_play(self) -> None:
        season, _teams, rr1, _rr2 = _two_rr_season("CompPre", n=2)
        self.assertFalse(season._phase_complete(rr1))

    def test_cursor_on_rr1_before_any_play(self) -> None:
        season, _teams, rr1, _rr2 = _two_rr_season("CursorPre", n=2)
        current = season.current_phase()
        self.assertIsNotNone(current)
        self.assertEqual(current.pk, rr1.pk)

    def test_rr1_complete_after_playing_rr1_only(self) -> None:
        season, teams, rr1, rr2 = _two_rr_season("CompRr1", n=2)
        _play_phase(season, teams, rr1)
        season.refresh_from_db()
        self.assertTrue(season._phase_complete(rr1))
        # RR2 is still incomplete (its fixtures unplayed).
        self.assertFalse(season._phase_complete(rr2))

    def test_cursor_advances_to_rr2_only_after_rr1_complete(self) -> None:
        season, teams, _rr1, rr2 = _two_rr_season("CursorAdv", n=2)
        _play_phase(season, teams, _rr1)
        season.refresh_from_db()
        current = season.current_phase()
        self.assertIsNotNone(current)
        self.assertEqual(current.pk, rr2.pk)

    def test_cursor_none_once_both_rr_phases_played(self) -> None:
        season, teams, rr1, rr2 = _two_rr_season("CursorDone", n=2)
        _play_phase(season, teams, rr1)
        _play_phase(season, teams, rr2)
        season.refresh_from_db()
        self.assertTrue(season._phase_complete(rr2))
        self.assertIsNone(season.current_phase())

    def test_season_completes_only_after_final_rr_phase(self) -> None:
        season, teams, rr1, rr2 = _two_rr_season("CompFinal", n=2)
        _play_phase(season, teams, rr1)
        season.refresh_from_db()
        # RR1 done but RR2 (the final phase) is not — Season stays active.
        self.assertEqual(season.state, "active")
        self.assertIsNone(season.champion_team_id)
        _play_phase(season, teams, rr2)
        season.refresh_from_db()
        # Final RR phase complete ⇒ Season completed + champion stamped.
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)


# ---------------------------------------------------------------------------
# Find-or-create distinctness — same pairing in RR1 vs RR2 → two Matches
# ---------------------------------------------------------------------------


class TestFindOrCreateDistinctness(TestCase):
    """The SAME pairing in RR1 vs RR2 creates two DISTINCT Matches, both
    ``season=<season>``, different ``season_phase``; a re-run is idempotent."""

    def test_same_pairing_different_phase_creates_two_matches(self) -> None:
        season, teams, rr1, rr2 = _two_rr_season("DistTwo", n=2)
        team_a, team_b = teams
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            sim.simulate_scheduled_round(
                season, team_a, team_b, 1, season_phase=rr1
            )
            sim.simulate_scheduled_round(
                season, team_a, team_b, 1, season_phase=rr2
            )
        season_matches = Match.objects.filter(season=season)
        # Two distinct Match rows for the identical pairing across phases.
        self.assertEqual(season_matches.count(), 2)
        phase_ids = sorted(m.season_phase_id for m in season_matches)
        self.assertEqual(phase_ids, sorted([rr1.pk, rr2.pk]))
        # Both carry season=<season>.
        for m in season_matches:
            self.assertEqual(m.season_id, season.id)

    def test_rerun_same_phase_pairing_is_idempotent(self) -> None:
        season, teams, rr1, _rr2 = _two_rr_season("DistIdem", n=2)
        team_a, team_b = teams
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            sim.simulate_scheduled_round(
                season, team_a, team_b, 1, season_phase=rr1
            )
            # Re-run round 1 of the SAME (season, phase, pairing): finds the
            # existing Match, no second row.
            sim.simulate_scheduled_round(
                season, team_a, team_b, 1, season_phase=rr1
            )
        self.assertEqual(
            Match.objects.filter(season=season, season_phase=rr1).count(), 1
        )

    def test_rounds_attribute_to_their_owning_phase(self) -> None:
        season, teams, rr1, rr2 = _two_rr_season("DistAttr", n=2)
        team_a, team_b = teams
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            sim.simulate_scheduled_round(
                season, team_a, team_b, 1, season_phase=rr1
            )
            sim.simulate_scheduled_round(
                season, team_a, team_b, 1, season_phase=rr2
            )
        rr1_rounds = GameRound.objects.filter(match__season_phase=rr1)
        rr2_rounds = GameRound.objects.filter(match__season_phase=rr2)
        self.assertEqual(rr1_rounds.count(), 1)
        self.assertEqual(rr2_rounds.count(), 1)

    def test_legacy_season_phase_none_keeps_one_match_per_pairing(self) -> None:
        # season_phase=None (legacy / phase-less path) collapses to a single
        # Match per (season, NULL, pairing).
        season, teams, _rr1, _rr2 = _two_rr_season("DistLegacy", n=2)
        team_a, team_b = teams
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            sim.simulate_scheduled_round(season, team_a, team_b, 1)
            sim.simulate_scheduled_round(season, team_a, team_b, 1)
        self.assertEqual(
            Match.objects.filter(
                season=season, season_phase__isnull=True
            ).count(),
            1,
        )


# ---------------------------------------------------------------------------
# Cumulative standings — aggregate BOTH RR phases
# ---------------------------------------------------------------------------


class TestCumulativeStandings(TestCase):
    """``_final_standings_for_phase`` aggregates Matches across BOTH RR
    phases (whole-season filter)."""

    def test_standings_count_all_season_matches_across_phases(self) -> None:
        season, teams, rr1, rr2 = _two_rr_season("CumCount", n=3)
        _play_phase(season, teams, rr1)
        _play_phase(season, teams, rr2)
        season.refresh_from_db()

        rows = season._final_standings_for_phase(rr2)
        # One row per enrolled team.
        self.assertEqual(len(rows), len(teams))
        # matches_played sums BOTH RR phases — each pair meets twice per phase
        # ⇒ across two RR phases every team has played more than a single RR
        # phase alone would yield. Assert the aggregate exceeds the per-phase
        # match count (cumulative, not per-phase scoped).
        total_matches = Match.objects.filter(season=season, is_completed=True).count()
        summed_played = sum(r.matches_played for r in rows)
        # Each completed Match contributes to two teams' matches_played.
        self.assertEqual(summed_played, 2 * total_matches)

    def test_rr_final_phase_champion_is_cumulative_leader(self) -> None:
        season, teams, rr1, rr2 = _two_rr_season("CumChamp", n=3)
        _play_phase(season, teams, rr1)
        _play_phase(season, teams, rr2)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        rows = season._final_standings_for_phase(rr2)
        # Champion == the cumulative (whole-season) standings leader.
        self.assertEqual(season.champion_team_id, rows[0].team_id)


# ---------------------------------------------------------------------------
# RR1 -> RR2 -> playoff end-to-end (cumulative-seeded auto-build + drain)
# ---------------------------------------------------------------------------


class TestMultiRrPlayoffEndToEnd(TestCase):
    """A RR1 + RR2 + tournament Season auto-builds a cumulative-seeded playoff
    once RR2 completes, then drains to a Season champion."""

    def test_playoff_not_built_until_both_rr_phases_done(self) -> None:
        season, teams, rr1, _rr2, tournament_phase = _two_rr_playoff_season(
            "E2eBuild", n=4
        )
        _play_phase(season, teams, rr1)
        tournament_phase.refresh_from_db()
        # RR1 done, RR2 not — the playoff is NOT yet built.
        self.assertIsNone(tournament_phase.tournament_id)

    def test_playoff_auto_builds_after_rr2_seeded_by_cumulative_standings(
        self,
    ) -> None:
        season, teams, rr1, rr2, tournament_phase = _two_rr_playoff_season(
            "E2eSeed", n=4
        )
        _play_phase(season, teams, rr1)
        _play_phase(season, teams, rr2)
        tournament_phase.refresh_from_db()
        # The playoff is built + active (matchups visible) after RR2.
        self.assertIsNotNone(tournament_phase.tournament_id)
        tournament = tournament_phase.tournament
        self.assertEqual(tournament.state, "active")

        # Seeds == the cumulative (whole-season) standings ranks.
        from matches.models import TournamentParticipant

        participants = list(
            TournamentParticipant.objects.filter(tournament=tournament).order_by(
                "seed"
            )
        )
        self.assertEqual(len(participants), len(teams))
        self.assertEqual(
            [p.seed for p in participants], list(range(1, len(teams) + 1))
        )
        rows = season._final_standings_for_phase(rr2)
        rank_to_team = {row.rank: row.team_id for row in rows}
        for p in participants:
            self.assertEqual(p.team_id, rank_to_team[p.seed])

    def test_draining_playoff_crowns_season_champion(self) -> None:
        season, teams, rr1, rr2, tournament_phase = _two_rr_playoff_season(
            "E2eDrain", n=4
        )
        _play_phase(season, teams, rr1)
        _play_phase(season, teams, rr2)
        tournament_phase.refresh_from_db()
        self.assertIsNotNone(tournament_phase.tournament_id)

        _drain_tournament(tournament_phase.tournament)
        # The raw engine drains the bracket; the play loop / view stamps the
        # Season champion via complete_if_finished (mirrors the Part2c-1
        # single-RR playoff precedent).
        season.complete_if_finished()
        season.refresh_from_db()
        tournament_phase.refresh_from_db()
        tournament = tournament_phase.tournament

        # Tournament drained + Season crowned with the tournament champion.
        self.assertEqual(tournament.state, "completed")
        self.assertIsNotNone(tournament.champion_id)
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tournament.champion_id)

    def test_drained_bracket_has_winners_on_every_decisive_node(self) -> None:
        season, teams, rr1, rr2, tournament_phase = _two_rr_playoff_season(
            "E2eNodes", n=4
        )
        _play_phase(season, teams, rr1)
        _play_phase(season, teams, rr2)
        tournament_phase.refresh_from_db()
        _drain_tournament(tournament_phase.tournament)
        tournament = tournament_phase.tournament
        # Every non-bye bracket node has a winner once drained.
        decisive = BracketNode.objects.filter(tournament=tournament, is_bye=False)
        self.assertGreater(decisive.count(), 0)
        for node in decisive:
            self.assertIsNotNone(node.winner_id)
