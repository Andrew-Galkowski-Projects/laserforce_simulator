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


# ===========================================================================
# LG-02-Part2c-3d — per-tournament-block participant CUT
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3d-seam-contract.md`` §3 / §8 /
# §9: ``activate_pending_tournament_phase`` slices the mode-ordered seed vector
# to the TOP ``phase.tournament_cut`` BEFORE building the bracket. The LIVE rules:
#   - ``tournament_cut=N`` (N>0)  ⇒ exactly N TournamentParticipants, dense seeds
#     1..N taken from the TOP of the mode-ordered seed order;
#   - ``tournament_cut=0``        ⇒ the FULL participant set (byte-identical to
#     today — the slice is not applied);
#   - ``tournament_cut > enrolled`` ⇒ all teams (``order[:cut]`` is a Python
#     no-op past the end);
#   - the built Tournament's ``format`` stays ``"single_elimination"`` regardless
#     of ``tournament_format`` (dormant this slice — the build hardcodes it);
#   - the champion is still stamped after draining (cut changes WHO seeds in, not
#     the crown machinery).
# Standings AND strength modes are covered with deterministic seed order; the
# non-deterministic ``unseeded`` shuffle is asserted on COUNT only. NEVER assert
# point totals (tournament sims are non-deterministic).
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the ``order = order[:phase.tournament_cut]`` slice +
# the ``SeasonPhase.tournament_cut`` column — the TDD red state.


from matches.models import TournamentParticipant as _Lg3dParticipant  # noqa: E402

# The 19 stat fields summed by ``Player.overall_rating`` (the mean). Injecting a
# single constant value per team's players makes that team's mean == the value,
# so ``strength`` seeding (default_seed_order: mean DESC, team_id ASC) is
# deterministic.
_LG3D_STAT_FIELDS = (
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)


def _lg3d_set_team_rating(team, value: int) -> None:
    for player in team.active_players:
        for field in _LG3D_STAT_FIELDS:
            setattr(player, field, value)
        player.save()


def _lg3d_first_phase_tournament_season(
    prefix: str,
    mode: str,
    *,
    cut: int,
    n: int = 8,
    ratings: list[int] | None = None,
    tournament_format: str = "single_elimination",
):
    """A draft Season whose FIRST phase is a ``mode`` tournament (ordinal 1) with
    the given ``tournament_cut`` (+ optional ``tournament_format``), followed by
    an ordinal-2 round_robin (so the >=1-RR rule holds). ``n`` slotted teams
    enrolled, NOT yet started.

    Returns ``(season, teams, tournament_phase)``.
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        if ratings is not None:
            _lg3d_set_team_rating(t, ratings[i])
        teams.append(t)
        season.teams.add(t)
    tournament_phase = SeasonPhase.objects.create(
        season=season,
        ordinal=1,
        phase_type="tournament",
        tournament_mode=mode,
        tournament_cut=cut,
        tournament_format=tournament_format,
    )
    SeasonPhase.objects.create(season=season, ordinal=2, phase_type="round_robin")
    return season, teams, tournament_phase


class TestStrengthCutBuild(TestCase):
    """A first-phase ``strength`` tournament with a cut builds exactly ``cut``
    participants with dense seeds 1..cut taken from the TOP of the
    strength-ordered seed vector (deterministic injected ratings)."""

    # 8 distinct ratings — the top 4 by mean are 90, 80, 70, 60 (teams 1,3,5,7).
    _RATINGS = [10, 90, 20, 80, 30, 70, 40, 60]

    def test_cut_4_creates_four_top_seeded_participants(self) -> None:
        season, teams, tp = _lg3d_first_phase_tournament_season(
            "StrCut4", "strength", cut=4, n=8, ratings=self._RATINGS
        )
        season.start_season()
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)

        participants = list(
            _Lg3dParticipant.objects.filter(tournament=tp.tournament).order_by("seed")
        )
        # Exactly cut participants with dense 1..cut seeds.
        self.assertEqual(len(participants), 4)
        self.assertEqual([p.seed for p in participants], [1, 2, 3, 4])

        # The cut keeps the TOP of the strength order — the 4 strongest teams
        # (ratings 90, 80, 70, 60) in mean-DESC order.
        from matches.bracket import default_seed_order as _seed
        from teams.models import Team

        team_ids = season.starting_team_ids_json or []
        team_ratings = [
            (
                tid,
                sum(p.overall_rating for p in Team.objects.get(pk=tid).active_players)
                / max(len(Team.objects.get(pk=tid).active_players), 1),
            )
            for tid in team_ids
        ]
        full_order = _seed(team_ratings)
        expected_top4 = full_order[:4]
        self.assertEqual([p.team_id for p in participants], expected_top4)

    def test_built_format_honors_tournament_format(
        self,
    ) -> None:
        # LG-02-Part2c-3e flipped tournament_format dormant→live: the build now
        # reads phase.tournament_format instead of hardcoding single_elimination
        # (cut=4 keeps an even participant count so the swiss build is valid).
        season, _teams, tp = _lg3d_first_phase_tournament_season(
            "StrFmt",
            "strength",
            cut=4,
            n=8,
            ratings=self._RATINGS,
            tournament_format="swiss",
        )
        season.start_season()
        tp.refresh_from_db()
        self.assertEqual(tp.tournament.format, "swiss")

    def test_cut_zero_keeps_full_participant_set(self) -> None:
        season, teams, tp = _lg3d_first_phase_tournament_season(
            "StrCut0", "strength", cut=0, n=8, ratings=self._RATINGS
        )
        season.start_season()
        tp.refresh_from_db()
        participants = list(
            _Lg3dParticipant.objects.filter(tournament=tp.tournament).order_by("seed")
        )
        # Full set, dense 1..N — byte-identical to a no-cut build.
        self.assertEqual(len(participants), len(teams))
        self.assertEqual([p.seed for p in participants], list(range(1, len(teams) + 1)))

    def test_cut_greater_than_enrolled_keeps_all_teams(self) -> None:
        # cut > enrolled ⇒ order[:cut] is a no-op slice ⇒ all teams.
        season, teams, tp = _lg3d_first_phase_tournament_season(
            "StrCutBig", "strength", cut=99, n=8, ratings=self._RATINGS
        )
        season.start_season()
        tp.refresh_from_db()
        participants = list(
            _Lg3dParticipant.objects.filter(tournament=tp.tournament).order_by("seed")
        )
        self.assertEqual(len(participants), len(teams))
        self.assertEqual([p.seed for p in participants], list(range(1, len(teams) + 1)))


class TestUnseededCutBuild(TestCase):
    """A first-phase ``unseeded`` tournament with a cut builds exactly ``cut``
    participants with dense seeds 1..cut. The shuffle is non-deterministic, so
    only the COUNT + dense seeds are asserted (NOT which teams)."""

    def test_cut_4_creates_four_dense_seeded_participants(self) -> None:
        season, teams, tp = _lg3d_first_phase_tournament_season(
            "UnsCut4", "unseeded", cut=4, n=8
        )
        season.start_season()
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        participants = list(
            _Lg3dParticipant.objects.filter(tournament=tp.tournament).order_by("seed")
        )
        self.assertEqual(len(participants), 4)
        self.assertEqual([p.seed for p in participants], [1, 2, 3, 4])
        # The 4 cut team ids are a subset of the enrolled set (a valid sample).
        cut_ids = {p.team_id for p in participants}
        self.assertEqual(len(cut_ids), 4)
        self.assertTrue(cut_ids.issubset({t.id for t in teams}))

    def test_cut_zero_keeps_full_set(self) -> None:
        season, teams, tp = _lg3d_first_phase_tournament_season(
            "UnsCut0", "unseeded", cut=0, n=8
        )
        season.start_season()
        tp.refresh_from_db()
        participants = list(_Lg3dParticipant.objects.filter(tournament=tp.tournament))
        self.assertEqual(len(participants), len(teams))


class TestStandingsCutBuild(TestCase):
    """A season-ending ``standings`` tournament with a cut: completing the RR
    auto-builds a playoff seeded from the TOP ``cut`` standings ranks with dense
    1..cut seeds; the champion is still stamped after draining."""

    def _rr_then_cut_standings_season(self, prefix: str, *, cut: int, n: int = 8):
        league = League.objects.create(name=f"{prefix} League")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        teams = []
        for i in range(n):
            t, _ = make_team_with_slots(f"{prefix}{i}")
            teams.append(t)
            season.teams.add(t)
        rr = SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="round_robin"
        )
        tp = SeasonPhase.objects.create(
            season=season,
            ordinal=2,
            phase_type="tournament",
            tournament_mode="standings",
            tournament_cut=cut,
        )
        season.start_season()
        season.refresh_from_db()
        return season, teams, rr, tp

    def test_cut_4_seeds_top_four_standings_ranks(self) -> None:
        season, teams, rr, tp = self._rr_then_cut_standings_season("StdCut4", cut=4)
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)

        participants = list(
            _Lg3dParticipant.objects.filter(tournament=tp.tournament).order_by("seed")
        )
        # Exactly cut participants, dense 1..cut.
        self.assertEqual(len(participants), 4)
        self.assertEqual([p.seed for p in participants], [1, 2, 3, 4])

        # Seed i team == standings rank i team (the TOP cut of the rank order).
        rows = season._final_standings_for_phase(rr)
        rank_to_team = {row.rank: row.team_id for row in rows}
        for p in participants:
            self.assertEqual(p.team_id, rank_to_team[p.seed])

        # Built format stays single_elimination.
        self.assertEqual(tp.tournament.format, "single_elimination")

    def test_cut_zero_seeds_full_standings_field(self) -> None:
        season, teams, _rr, tp = self._rr_then_cut_standings_season("StdCut0", cut=0)
        _play_rr(season, teams)
        tp.refresh_from_db()
        participants = list(_Lg3dParticipant.objects.filter(tournament=tp.tournament))
        self.assertEqual(len(participants), len(teams))

    def test_cut_champion_still_stamped_after_drain(self) -> None:
        season, teams, _rr, tp = self._rr_then_cut_standings_season(
            "StdCutCrown", cut=4
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)

        from matches.tournament_engine import play_next_node

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(200):
                if play_next_node(tp.tournament) is None:
                    break
        tp.refresh_from_db()
        season.complete_if_finished()
        season.refresh_from_db()

        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)


# ===========================================================================
# LG-02-Part2c-3e — non-single-elim Season tournament-phase build (5 formats)
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3e-seam-contract.md`` §4:
# ``Season.activate_pending_tournament_phase`` now passes
# ``format=phase.tournament_format`` + the 7 sub-config kwargs into
# ``Tournament.objects.create``, so a Season ``tournament`` phase builds via ANY
# of the 5 formats with full per-format sub-config parity. The cut slice + seed +
# name + ``lock_and_build()`` are unchanged. For each format we build a Season
# (round_robin phase then a tournament phase whose ``tournament_format=<fmt>`` +
# sub-config), play the RR to completion so ``activate_pending_tournament_phase``
# fires, then assert:
#   (a) phase.tournament.format == <fmt>
#   (b) the built Tournament's sub-config fields match the phase
#   (c) the bracket actually built for that format —
#         double_elim  ⇒ BracketNodes with bracket_type ∈ {winners,losers,grand_final}
#         round_robin  ⇒ bracket_type=="round_robin" nodes
#         swiss        ⇒ bracket_type=="swiss" nodes
#         single_elim  ⇒ winners-only tree
#         round_robin_double_elim ⇒ (seeding) round_robin nodes built at lock
# then drain via the play loop and assert champion stamped + season completed.
#
# N=8 with wb=4/lb=2 (a valid RR→DE combo) keeps every format valid. Tests assert
# SCHEMA-LEVEL outcomes (built format, sub-config fields, bracket_type sets,
# champion id, state) — NEVER raw simulated point totals (non-deterministic).
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the per-format build kwargs + the 7 SeasonPhase
# columns — the TDD red state, not a defect in this file.


from matches.models import BracketNode as _Lg3eBracketNode  # noqa: E402
from matches.models import (  # noqa: E402
    TournamentParticipant as _Lg3eParticipant,
)


def _drain_and_complete(season, tournament_phase) -> None:
    """Drain a built tournament-phase bracket to a champion via the real engine,
    then complete the Season. Refreshes both ``tournament_phase.tournament`` and
    ``season`` in place.

    Bounded loop (RR / double-elim / swiss drain over more nodes than single-elim,
    so the bound is generous). NEVER asserts point totals.
    """
    from matches.tournament_engine import play_next_node

    tournament_phase.refresh_from_db()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(2000):
            if play_next_node(tournament_phase.tournament) is None:
                break
    tournament_phase.refresh_from_db()
    season.complete_if_finished()
    season.refresh_from_db()


def _lg3e_rr_then_tournament_season(
    prefix: str,
    *,
    tournament_format: str,
    n: int = 8,
    final_series_length: int = 1,
    semifinal_series_length: int = 1,
    quarterfinal_series_length: int = 1,
    earlier_series_length: int = 1,
    wb_advancers: int = 0,
    lb_advancers: int = 0,
    swiss_rounds: int = 0,
    tournament_cut: int = 0,
):
    """An active Season: ordinal-1 ``round_robin`` then an ordinal-2 season-ending
    ``standings`` tournament whose ``tournament_format`` + 7 sub-config columns are
    set. ``n`` slotted teams, started. Returns ``(season, teams, rr, tp)``."""
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    rr = SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    tp = SeasonPhase.objects.create(
        season=season,
        ordinal=2,
        phase_type="tournament",
        tournament_mode="standings",
        tournament_cut=tournament_cut,
        tournament_format=tournament_format,
        final_series_length=final_series_length,
        semifinal_series_length=semifinal_series_length,
        quarterfinal_series_length=quarterfinal_series_length,
        earlier_series_length=earlier_series_length,
        wb_advancers=wb_advancers,
        lb_advancers=lb_advancers,
        swiss_rounds=swiss_rounds,
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr, tp


def _lg3e_bracket_types(tournament) -> set[str]:
    return set(
        _Lg3eBracketNode.objects.filter(tournament=tournament).values_list(
            "bracket_type", flat=True
        )
    )


class TestBuildSingleEliminationFormat(TestCase):
    """A ``single_elimination`` tournament phase builds a winners-only tree
    (byte-identical to today's auto-build)."""

    def test_built_format_and_winners_only_tree(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtSE", tournament_format="single_elimination", n=8
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        self.assertEqual(tp.tournament.format, "single_elimination")
        # Winners-only: every node is bracket_type "winners".
        self.assertEqual(_lg3e_bracket_types(tp.tournament), {"winners"})

    def test_drains_to_champion_and_completes_season(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtSEDrain", tournament_format="single_elimination", n=8
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        _drain_and_complete(season, tp)
        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)


class TestBuildDoubleEliminationFormat(TestCase):
    """A ``double_elimination`` tournament phase builds a Winners + Losers +
    Grand-final tree, carrying the sub-config series lengths."""

    def test_built_format_and_sub_config_and_bracket_types(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtDE",
            tournament_format="double_elimination",
            n=8,
            final_series_length=3,
            semifinal_series_length=3,
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        self.assertEqual(tp.tournament.format, "double_elimination")
        # Sub-config carried onto the built Tournament.
        self.assertEqual(tp.tournament.final_series_length, 3)
        self.assertEqual(tp.tournament.semifinal_series_length, 3)
        # The DE tree carries winners / losers / grand_final nodes.
        types = _lg3e_bracket_types(tp.tournament)
        self.assertTrue(types.issubset({"winners", "losers", "grand_final"}))
        self.assertIn("winners", types)
        self.assertIn("losers", types)
        self.assertIn("grand_final", types)

    def test_drains_to_champion_and_completes_season(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtDEDrain", tournament_format="double_elimination", n=8
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        _drain_and_complete(season, tp)
        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)


class TestBuildRoundRobinFormat(TestCase):
    """A ``round_robin`` tournament phase builds round_robin bracket nodes."""

    def test_built_format_and_round_robin_nodes(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtRR", tournament_format="round_robin", n=8
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        self.assertEqual(tp.tournament.format, "round_robin")
        self.assertEqual(_lg3e_bracket_types(tp.tournament), {"round_robin"})

    def test_drains_to_champion_and_completes_season(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtRRDrain", tournament_format="round_robin", n=8
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        _drain_and_complete(season, tp)
        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)


class TestBuildSwissFormat(TestCase):
    """A ``swiss`` tournament phase builds swiss bracket nodes, carrying
    ``swiss_rounds``. Swiss needs an EVEN participant count (N=8)."""

    def test_built_format_swiss_rounds_and_swiss_nodes(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtSW", tournament_format="swiss", n=8, swiss_rounds=3
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        self.assertEqual(tp.tournament.format, "swiss")
        self.assertEqual(tp.tournament.swiss_rounds, 3)
        self.assertEqual(_lg3e_bracket_types(tp.tournament), {"swiss"})

    def test_drains_to_champion_and_completes_season(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtSWDrain", tournament_format="swiss", n=8, swiss_rounds=3
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        _drain_and_complete(season, tp)
        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)


class TestBuildRoundRobinDoubleElimFormat(TestCase):
    """A ``round_robin_double_elim`` tournament phase builds with a valid wb/lb
    combo (8 teams, wb=4/lb=2). The seeding stage builds round_robin nodes; the
    sub-config wb/lb advancers are carried onto the Tournament."""

    def test_built_format_and_wb_lb_advancers(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtRRDE",
            tournament_format="round_robin_double_elim",
            n=8,
            wb_advancers=4,
            lb_advancers=2,
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        self.assertEqual(tp.tournament.format, "round_robin_double_elim")
        # wb/lb advancers carried onto the built Tournament.
        self.assertEqual(tp.tournament.wb_advancers, 4)
        self.assertEqual(tp.tournament.lb_advancers, 2)
        # The seeding stage builds round_robin nodes at lock (DE finals build
        # lazily once the RR seeding completes).
        self.assertIn("round_robin", _lg3e_bracket_types(tp.tournament))

    def test_drains_to_champion_and_completes_season(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtRRDEDrain",
            tournament_format="round_robin_double_elim",
            n=8,
            wb_advancers=4,
            lb_advancers=2,
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        _drain_and_complete(season, tp)
        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)


class TestBuildFormatWithCutInterplay(TestCase):
    """A ``tournament_cut`` combined with a non-single-elim format: the cut slices
    the seeded order to the top N before the (chosen-format) bracket builds."""

    def test_double_elim_with_cut_seeds_top_four(self) -> None:
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtCutDE",
            tournament_format="double_elimination",
            n=8,
            tournament_cut=4,
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        self.assertEqual(tp.tournament.format, "double_elimination")

        participants = list(
            _Lg3eParticipant.objects.filter(tournament=tp.tournament).order_by("seed")
        )
        # The cut keeps exactly 4 participants, dense seeds 1..4.
        self.assertEqual(len(participants), 4)
        self.assertEqual([p.seed for p in participants], [1, 2, 3, 4])

    def test_swiss_with_cut_seeds_top_four(self) -> None:
        # Swiss needs an even participant count — cut=4 keeps that.
        season, teams, _rr, tp = _lg3e_rr_then_tournament_season(
            "FmtCutSW",
            tournament_format="swiss",
            n=8,
            tournament_cut=4,
            swiss_rounds=2,
        )
        _play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        self.assertEqual(tp.tournament.format, "swiss")
        participants = list(
            _Lg3eParticipant.objects.filter(tournament=tp.tournament).order_by("seed")
        )
        self.assertEqual(len(participants), 4)
        self.assertEqual([p.seed for p in participants], [1, 2, 3, 4])
