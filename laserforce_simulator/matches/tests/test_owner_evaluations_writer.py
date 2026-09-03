"""CAR-02 — tests for the lazy writer ``matches.league_views._ensure_owner_evaluations``
(seam contract §3.1 / §6.3).

The writer ensures one ``OwnerEvaluation`` row per completed Season of a League
up to and including ``up_to_season``, written oldest→newest in Season order so
the per-factor caps + cumulatives + tenure derivation are correct. It is keyed
on ``get_or_create(league, season)`` (idempotent) and never backfills Seasons
before the first computable one.

Standings are hand-constructed from ``Match`` rows (the LG-01c / LG-06g
fixture-pattern) so the wins-delta SIGN is controllable; assertions are
schema-level — row presence / Season order / ``team_managed`` chain / per-factor
cap-chaining / tenure reset / idempotency — NEVER exact simulated point totals.

These FAIL until the Code agent lands ``OwnerEvaluation`` + the writer. Reuses
the LG-01 ``current_team`` FK + the snapshot-chain tenure rule.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from matches.league_views import (
    _classify_playoffs_for_team,
    _ensure_owner_evaluations,
)
from matches.models import (
    BracketNode,
    GameRound,
    League,
    Match,
    OwnerEvaluation,
    Season,
    SeasonPhase,
    Tournament,
    TournamentParticipant,
)
from matches.owner_mood import MOOD_FACTOR_CAP
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(prefix: str):
    team, _ = make_team_with_slots(prefix)
    return team


def _make_league(name: str, *, current_team=None) -> League:
    return League.objects.create(
        name=name, mode="league", state="active", current_team=current_team
    )


def _make_completed_season(
    league: League,
    *,
    name: str,
    start_date: date,
    team_ids: list[int],
) -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format="single_round_robin",
        state="completed",
        starting_team_ids_json=sorted(team_ids),
    )


def _add_match(
    season: Season,
    team_red,
    team_blue,
    *,
    red_pts: int,
    blue_pts: int,
) -> Match:
    """A completed Match: red plays red in R1 and blue in R2 (mirror).

    Pinning both rounds to the same physical winner gives a clean 2-0 Match
    record so the standings W/L is deterministic.
    """
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=season,
        red_round1_points=red_pts,
        blue_round1_points=blue_pts,
        red_round2_points=red_pts,
        blue_round2_points=blue_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=1,
        red_points=red_pts,
        blue_points=blue_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_blue,
        team_blue=team_red,
        round_number=2,
        red_points=blue_pts,
        blue_points=red_pts,
        is_completed=True,
    )
    return match


# ---------------------------------------------------------------------------
# TestEnsureWritesOneRowPerCompletedSeason
# ---------------------------------------------------------------------------


class TestEnsureWritesOneRowPerCompletedSeason(TestCase):
    """One row per completed Season up to and including ``up_to_season``,
    in ascending Season order."""

    def _two_season_league(self):
        team = _make_team("WriterT")
        opp = _make_team("WriterO")
        league = _make_league("WriterL", current_team=team)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[team.id, opp.id],
        )
        s2 = _make_completed_season(
            league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        # Give the manager team a win each Season so wins-delta is computable.
        _add_match(s1, team, opp, red_pts=100, blue_pts=10)
        _add_match(s2, team, opp, red_pts=100, blue_pts=10)
        return league, team, opp, s1, s2

    def test_one_row_per_completed_season(self) -> None:
        league, _team, _opp, _s1, s2 = self._two_season_league()
        _ensure_owner_evaluations(league, s2)
        self.assertEqual(league.owner_evaluations.count(), 2)

    def test_rows_written_in_ascending_season_order(self) -> None:
        league, _team, _opp, s1, s2 = self._two_season_league()
        _ensure_owner_evaluations(league, s2)
        season_ids = list(
            league.owner_evaluations.order_by("id").values_list("season_id", flat=True)
        )
        # Oldest→newest: Season 1 before Season 2 (by Season id ascending).
        self.assertEqual(season_ids, [s1.id, s2.id])

    def test_up_to_season_bounds_the_set(self) -> None:
        league, _team, _opp, s1, _s2 = self._two_season_league()
        # Only ensure up to Season 1 — Season 2 must NOT get a row.
        _ensure_owner_evaluations(league, s1)
        season_ids = set(league.owner_evaluations.values_list("season_id", flat=True))
        self.assertEqual(season_ids, {s1.id})

    def test_team_managed_set_to_current_team_on_first_row(self) -> None:
        league, team, _opp, _s1, s2 = self._two_season_league()
        _ensure_owner_evaluations(league, s2)
        first = league.owner_evaluations.order_by("season_id").first()
        self.assertEqual(first.team_managed_id, team.id)


# ---------------------------------------------------------------------------
# TestEnsureIdempotent
# ---------------------------------------------------------------------------


class TestEnsureIdempotent(TestCase):
    """A second call writes no new rows and leaves existing rows untouched."""

    def _setup(self):
        team = _make_team("IdemT")
        opp = _make_team("IdemO")
        league = _make_league("IdemL", current_team=team)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_match(s1, team, opp, red_pts=100, blue_pts=10)
        return league, s1

    def test_second_call_writes_no_new_rows(self) -> None:
        league, s1 = self._setup()
        _ensure_owner_evaluations(league, s1)
        count_after_first = OwnerEvaluation.objects.count()
        _ensure_owner_evaluations(league, s1)
        self.assertEqual(OwnerEvaluation.objects.count(), count_after_first)

    def test_existing_row_left_untouched(self) -> None:
        league, s1 = self._setup()
        _ensure_owner_evaluations(league, s1)
        row = OwnerEvaluation.objects.get(league=league, season=s1)
        before = (
            row.wins_delta,
            row.playoffs_delta,
            row.wins_total,
            row.playoffs_total,
            row.verdict,
            row.hot_seat_level,
            row.team_managed_id,
        )
        _ensure_owner_evaluations(league, s1)
        row.refresh_from_db()
        after = (
            row.wins_delta,
            row.playoffs_delta,
            row.wins_total,
            row.playoffs_total,
            row.verdict,
            row.hot_seat_level,
            row.team_managed_id,
        )
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# TestEnsureCapChaining
# ---------------------------------------------------------------------------


class TestEnsureCapChaining(TestCase):
    """Per-factor cumulative totals are cap-chained across Seasons; the wins
    cumulative never exceeds ``MOOD_FACTOR_CAP``."""

    def test_wins_total_never_exceeds_cap(self) -> None:
        team = _make_team("CapT")
        opp = _make_team("CapO")
        league = _make_league("CapL", current_team=team)
        seasons = []
        # Many dominant Seasons in a row — each contributes a positive wins
        # delta; the cumulative must clamp at +1.
        for i in range(8):
            s = _make_completed_season(
                league,
                name=f"Season {i + 1}",
                start_date=date(2020 + i, 1, 1),
                team_ids=[team.id, opp.id],
            )
            _add_match(s, team, opp, red_pts=100, blue_pts=1)
            seasons.append(s)
        _ensure_owner_evaluations(league, seasons[-1])
        for row in OwnerEvaluation.objects.filter(league=league):
            self.assertLessEqual(
                row.wins_total, MOOD_FACTOR_CAP + 1e-9, "wins_total exceeded cap"
            )

    def test_later_row_wins_total_accumulates_from_earlier(self) -> None:
        team = _make_team("AccT")
        opp = _make_team("AccO")
        league = _make_league("AccL", current_team=team)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[team.id, opp.id],
        )
        s2 = _make_completed_season(
            league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        # A win each Season ⇒ a positive wins delta each Season.
        _add_match(s1, team, opp, red_pts=100, blue_pts=10)
        _add_match(s2, team, opp, red_pts=100, blue_pts=10)
        _ensure_owner_evaluations(league, s2)
        row1 = OwnerEvaluation.objects.get(league=league, season=s1)
        row2 = OwnerEvaluation.objects.get(league=league, season=s2)
        # Same managed team across both Seasons (one tenure) ⇒ s2's cumulative
        # is the chained sum (>= s1's), capped at +1.
        self.assertGreaterEqual(row2.wins_total, row1.wins_total - 1e-9)
        self.assertLessEqual(row2.wins_total, MOOD_FACTOR_CAP + 1e-9)
        # And the per-Season delta is recorded distinctly from the cumulative.
        self.assertGreater(row1.wins_delta, 0.0)


# ---------------------------------------------------------------------------
# TestEnsureTenureResetFromSnapshotChain
# ---------------------------------------------------------------------------


class TestEnsureTenureResetFromSnapshotChain(TestCase):
    """A ``team_managed`` change between consecutive rows (a fired→reassigned
    chain) resets the cumulative + restarts the grace counter — derived from
    the snapshot chain, NOT a tenure_id field."""

    def test_team_managed_change_marks_new_tenure(self) -> None:
        team_a = _make_team("TenA")
        team_b = _make_team("TenB")
        opp = _make_team("TenOpp")
        league = _make_league("TenL", current_team=team_a)

        # Season 1: managed team_a. Hand-write a "fired" row to end the tenure.
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[team_a.id, opp.id],
        )
        _add_match(s1, opp, team_a, red_pts=100, blue_pts=1)  # team_a loses
        OwnerEvaluation.objects.create(
            league=league,
            season=s1,
            team_managed=team_a,
            wins_delta=-0.25,
            playoffs_delta=-0.2,
            wins_total=-0.25,
            playoffs_total=-0.2,
            verdict="fired",
            hot_seat_level=0,
        )
        # Reassignment: current_team flips to team_b (the post-fire team).
        league.current_team = team_b
        league.save(update_fields=["current_team"])

        # Season 2: managed team_b (the new tenure's first Season).
        s2 = _make_completed_season(
            league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            team_ids=[team_b.id, opp.id],
        )
        _add_match(s2, team_b, opp, red_pts=100, blue_pts=1)  # team_b wins

        _ensure_owner_evaluations(league, s2)

        row2 = OwnerEvaluation.objects.get(league=league, season=s2)
        # New tenure: team_managed is the post-fire team_b.
        self.assertEqual(row2.team_managed_id, team_b.id)
        # Cumulative reset — the s2 wins_total equals the s2 wins_delta (no
        # carry from the prior, negative, team_a tenure).
        self.assertAlmostEqual(row2.wins_total, row2.wins_delta, places=6)
        # The reset is positive (team_b won), NOT the -0.25 carried sum.
        self.assertGreater(row2.wins_total, 0.0)

    def test_pre_existing_fired_row_is_source_of_truth(self) -> None:
        # The writer must NOT recompute the prior "fired" row — a re-run leaves
        # the hand-written fired row's verdict intact.
        team_a = _make_team("SrcA")
        opp = _make_team("SrcOpp")
        league = _make_league("SrcL", current_team=team_a)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[team_a.id, opp.id],
        )
        _add_match(s1, team_a, opp, red_pts=100, blue_pts=1)  # team_a actually won
        # But the persisted row says "fired" (a contrived prior verdict).
        OwnerEvaluation.objects.create(
            league=league,
            season=s1,
            team_managed=team_a,
            wins_delta=-0.25,
            playoffs_delta=0.0,
            wins_total=-0.25,
            playoffs_total=0.0,
            verdict="fired",
            hot_seat_level=0,
        )
        _ensure_owner_evaluations(league, s1)
        row1 = OwnerEvaluation.objects.get(league=league, season=s1)
        # Idempotent: the persisted row (not a recompute) is the source of truth.
        self.assertEqual(row1.verdict, "fired")
        self.assertAlmostEqual(row1.wins_total, -0.25, places=6)


# ---------------------------------------------------------------------------
# TestEnsureNoBackfill
# ---------------------------------------------------------------------------


class TestEnsureNoBackfill(TestCase):
    """No backfill of Seasons before the first computable one; non-completed
    Seasons get no row."""

    def test_non_completed_seasons_get_no_row(self) -> None:
        team = _make_team("NbT")
        opp = _make_team("NbO")
        league = _make_league("NbL", current_team=team)
        completed = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_match(completed, team, opp, red_pts=100, blue_pts=10)
        # An active (non-completed) Season exists too.
        active = Season.objects.create(
            league=league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            state="active",
        )
        _ensure_owner_evaluations(league, completed)
        season_ids = set(league.owner_evaluations.values_list("season_id", flat=True))
        self.assertIn(completed.id, season_ids)
        self.assertNotIn(active.id, season_ids)

    def test_only_completed_up_to_bound_get_rows(self) -> None:
        team = _make_team("BoundT")
        opp = _make_team("BoundO")
        league = _make_league("BoundL", current_team=team)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2023, 1, 1),
            team_ids=[team.id, opp.id],
        )
        s2 = _make_completed_season(
            league,
            name="Season 2",
            start_date=date(2024, 1, 1),
            team_ids=[team.id, opp.id],
        )
        s3 = _make_completed_season(
            league,
            name="Season 3",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        for s in (s1, s2, s3):
            _add_match(s, team, opp, red_pts=100, blue_pts=10)
        # Bound the ensure at s2 — s3 must NOT get a row.
        _ensure_owner_evaluations(league, s2)
        season_ids = set(league.owner_evaluations.values_list("season_id", flat=True))
        self.assertEqual(season_ids, {s1.id, s2.id})
        self.assertNotIn(s3.id, season_ids)


# ---------------------------------------------------------------------------
# TestClassifyPlayoffsForTeam
# ---------------------------------------------------------------------------


class TestCar03MultiplayerIsolation(TestCase):
    """CAR-03 — the owner-mood lifecycle is inert for a non-career League.

    The writer ``_ensure_owner_evaluations`` early-returns for
    ``league.mode != "league"`` (writes 0 ``OwnerEvaluation`` rows), and the
    ``_is_career_league`` predicate is a tiny unit assertion on the same rule.
    """

    def _make_multiplayer_league(self, name: str, *, current_team=None) -> League:
        return League.objects.create(
            name=name,
            mode="multiplayer",
            state="active",
            current_team=current_team,
        )

    def _completed_season_for(self, league: League):
        team = _make_team("Car03MpT")
        opp = _make_team("Car03MpO")
        league.current_team = team
        league.save(update_fields=["current_team"])
        season = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_match(season, team, opp, red_pts=100, blue_pts=10)
        return season

    def test_multiplayer_league_writes_zero_rows(self) -> None:
        league = self._make_multiplayer_league("Car03MpL")
        season = self._completed_season_for(league)
        _ensure_owner_evaluations(league, season)
        self.assertEqual(OwnerEvaluation.objects.count(), 0)
        self.assertEqual(league.owner_evaluations.count(), 0)

    def test_is_career_league_predicate(self) -> None:
        from matches.league_views import _is_career_league

        career = League.objects.create(name="Car03Career", mode="league")
        multiplayer = League.objects.create(name="Car03Mp", mode="multiplayer")
        self.assertTrue(_is_career_league(career))
        self.assertFalse(_is_career_league(multiplayer))


class TestClassifyPlayoffsForTeam(TestCase):
    """``_classify_playoffs_for_team`` maps a built bracket to the flat
    ``(playoff_result, rounds_won, num_rounds)`` triple feeding
    ``owner_mood.compute_playoffs_delta`` (the ORM glue, distinct from the
    pure math in ``test_owner_mood``). Sim-free — Tournament / participant /
    BracketNode rows are constructed directly; assertions are schema-level.
    """

    def _season_with_tournament(self, prefix: str, teams: list):
        league = _make_league(f"{prefix}L", current_team=teams[0])
        season = _make_completed_season(
            league,
            name="S1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        tournament = Tournament.objects.create(name=f"{prefix} Playoffs")
        SeasonPhase.objects.create(
            season=season,
            ordinal=2,
            phase_type="tournament",
            tournament=tournament,
        )
        return season, tournament

    def test_none_when_no_tournament_phase(self) -> None:
        team = _make_team("CpNone")
        league = _make_league("CpNoneL", current_team=team)
        season = _make_completed_season(
            league, name="S1", start_date=date(2025, 1, 1), team_ids=[team.id]
        )
        self.assertEqual(_classify_playoffs_for_team(season, team.id), ("none", 0, 0))

    def test_champion(self) -> None:
        teams = [_make_team(f"CpCh{i}") for i in range(2)]
        season, tournament = self._season_with_tournament("CpCh", teams)
        for i, tm in enumerate(teams):
            TournamentParticipant.objects.create(
                tournament=tournament, team=tm, seed=i + 1
            )
        BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, winner=teams[0]
        )
        tournament.champion = teams[0]
        tournament.save(update_fields=["champion"])
        self.assertEqual(
            _classify_playoffs_for_team(season, teams[0].id), ("champion", 0, 1)
        )

    def test_seeded_counts_distinct_rounds_won(self) -> None:
        teams = [_make_team(f"CpSe{i}") for i in range(4)]
        season, tournament = self._season_with_tournament("CpSe", teams)
        for i, tm in enumerate(teams):
            TournamentParticipant.objects.create(
                tournament=tournament, team=tm, seed=i + 1
            )
        # 2-round bracket: teams[1] wins its round-1 node, teams[0] is champion.
        BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, winner=teams[1]
        )
        BracketNode.objects.create(
            tournament=tournament, bracket_round=2, position=0, winner=teams[0]
        )
        tournament.champion = teams[0]
        tournament.save(update_fields=["champion"])
        self.assertEqual(
            _classify_playoffs_for_team(season, teams[1].id), ("seeded", 1, 2)
        )

    def test_missed_when_not_a_participant(self) -> None:
        teams = [_make_team(f"CpMi{i}") for i in range(2)]
        season, tournament = self._season_with_tournament("CpMi", teams)
        TournamentParticipant.objects.create(
            tournament=tournament, team=teams[0], seed=1
        )
        BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, winner=teams[0]
        )
        tournament.champion = teams[0]
        tournament.save(update_fields=["champion"])
        outsider = _make_team("CpMiOut")
        self.assertEqual(
            _classify_playoffs_for_team(season, outsider.id), ("missed", 0, 1)
        )


# ---------------------------------------------------------------------------
# FIN-05 — luxury-tax challenge firing through the writer
# ---------------------------------------------------------------------------
#
# Seam contract `.claude/worktrees/fin-05-luxury-tax-firing-seam-contract.md`
# §3 / §7.3. The writer derives `luxury_tax_paid = tsf is not None and
# tsf.luxury_tax > 0`, passes it + `league.challenge_fired_luxury_tax` into
# `decide_verdict`, and stamps `fired_reason` on the row:
#   - challenge fire (finance ON, toggle ON, luxury_tax > 0, past grace)
#     ⇒ verdict="fired" + fired_reason="luxury_tax"
#   - mood fire (no luxury tax, mood <= -1 past grace)
#     ⇒ fired_reason="owner_mood"
#   - retained / hot_seat ⇒ fired_reason=""
#
# A challenge fire still records the wins/playoffs/money deltas + cap-chained
# totals (NOT zeroed). Finance OFF ⇒ no TeamSeasonFinance row ⇒ never a luxury
# fire (byte-identical to a toggle-OFF run). Non-career ⇒ writer early-returns.
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the model field + the writer's luxury wire — the
# TDD red state.


from datetime import date as _fin05_date  # noqa: E402

from matches import finance as _fin05_finance  # noqa: E402
from matches.league_views import (  # noqa: E402
    _ensure_owner_evaluations as _fin05_ensure,
    _ensure_team_finances as _fin05_ensure_finances,
)
from matches.models import TeamSeasonFinance as _fin05_TSF  # noqa: E402


def _fin05_make_league(
    name: str, *, current_team=None, finance_enabled=True, challenge=True
):
    return League.objects.create(
        name=name,
        mode="league",
        state="active",
        current_team=current_team,
        finance_enabled=finance_enabled,
        challenge_fired_luxury_tax=challenge,
    )


def _fin05_completed_season(league, *, name, start_date, team_ids):
    return Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format="single_round_robin",
        state="completed",
        starting_team_ids_json=sorted(team_ids),
    )


def _fin05_three_completed_seasons(league, team, opp, *, win=True):
    """Three completed Seasons so the managed team is strictly PAST the 2-Season
    grace period at the latest Season (`seasons_in_tenure == 3 > 2`). The
    `win` flag controls whether `team` wins (mood-safe) or loses (mood-fire)
    each Season."""
    seasons = []
    for i in range(3):
        s = _fin05_completed_season(
            league,
            name=f"Season {i + 1}",
            start_date=_fin05_date(2023 + i, 1, 1),
            team_ids=[team.id, opp.id],
        )
        if win:
            _add_match(s, team, opp, red_pts=100, blue_pts=1)  # team wins
        else:
            _add_match(s, opp, team, red_pts=100, blue_pts=1)  # team loses
        seasons.append(s)
    return seasons


def _fin05_stamp_luxury_tax(team, season, *, amount):
    """Hand-construct the managed team's TeamSeasonFinance row with a controlled
    luxury_tax (write the row directly per the assertion-discipline rule — do
    NOT run real simulations for the firing assertions)."""
    tsf, _ = _fin05_TSF.objects.get_or_create(
        team=team,
        season=season,
        defaults={"luxury_tax": amount},
    )
    tsf.luxury_tax = amount
    tsf.save(update_fields=["luxury_tax"])
    return tsf


class TestFin05ChallengeFireWritesLuxuryReason(TestCase):
    """A challenge fire ⇒ verdict="fired" + fired_reason="luxury_tax"."""

    def test_challenge_fire_row(self) -> None:
        team = _make_team("Fin05ChT")
        opp = _make_team("Fin05ChO")
        league = _fin05_make_league(
            "Fin05ChL", current_team=team, finance_enabled=True, challenge=True
        )
        # team WINS each Season (mood is safe — only the luxury rule can fire).
        seasons = _fin05_three_completed_seasons(league, team, opp, win=True)
        latest = seasons[-1]
        # Managed team paid the luxury tax in the latest Season.
        _fin05_stamp_luxury_tax(team, latest, amount=50_000.0)

        _fin05_ensure_finances(league, latest)
        # Re-stamp after the finance writer (it may overwrite luxury_tax).
        _fin05_stamp_luxury_tax(team, latest, amount=50_000.0)
        _fin05_ensure(league, latest)

        ev = OwnerEvaluation.objects.get(league=league, season=latest)
        self.assertEqual(ev.verdict, "fired")
        self.assertEqual(ev.fired_reason, "luxury_tax")

    def test_challenge_fire_records_deltas_and_totals_not_zeroed(self) -> None:
        # Mood-recorded-normally invariant (decision #6): a challenge fire still
        # carries the computed wins/playoffs/money deltas + cap-chained totals.
        team = _make_team("Fin05MoodT")
        opp = _make_team("Fin05MoodO")
        league = _fin05_make_league(
            "Fin05MoodL", current_team=team, finance_enabled=True, challenge=True
        )
        seasons = _fin05_three_completed_seasons(league, team, opp, win=True)
        latest = seasons[-1]
        _fin05_stamp_luxury_tax(team, latest, amount=50_000.0)
        _fin05_ensure_finances(league, latest)
        _fin05_stamp_luxury_tax(team, latest, amount=50_000.0)
        _fin05_ensure(league, latest)

        ev = OwnerEvaluation.objects.get(league=league, season=latest)
        self.assertEqual(ev.verdict, "fired")
        # team won every Season ⇒ a positive wins delta (NOT zeroed by the fire).
        self.assertGreater(ev.wins_delta, 0.0)
        # cap-chained cumulative across the in-tenure Seasons (not zeroed).
        self.assertGreater(ev.wins_total, 0.0)
        self.assertLessEqual(ev.wins_total, MOOD_FACTOR_CAP + 1e-9)


class TestFin05MoodFireWritesMoodReason(TestCase):
    """A mood fire (no luxury tax, mood <= -1 past grace) ⇒
    fired_reason="owner_mood"."""

    def test_mood_fire_row(self) -> None:
        team = _make_team("Fin05MdT")
        opp = _make_team("Fin05MdO")
        # Toggle ON but the team never pays the luxury tax ⇒ only mood can fire.
        league = _fin05_make_league(
            "Fin05MdL", current_team=team, finance_enabled=True, challenge=True
        )
        # team LOSES each Season ⇒ mood sinks below -1 past grace ⇒ mood fire.
        seasons = _fin05_three_completed_seasons(league, team, opp, win=False)
        latest = seasons[-1]
        _fin05_ensure_finances(league, latest)
        # Ensure no luxury tax for the managed team in any Season.
        _fin05_TSF.objects.filter(team=team).update(luxury_tax=0.0)
        _fin05_ensure(league, latest)

        ev = OwnerEvaluation.objects.get(league=league, season=latest)
        self.assertEqual(ev.verdict, "fired")
        self.assertEqual(ev.fired_reason, "owner_mood")


class TestFin05RetainedOrHotSeatWritesEmptyReason(TestCase):
    """A retained / hot-seat row ⇒ fired_reason=""."""

    def test_retained_row_empty_reason(self) -> None:
        team = _make_team("Fin05RtT")
        opp = _make_team("Fin05RtO")
        league = _fin05_make_league(
            "Fin05RtL", current_team=team, finance_enabled=True, challenge=True
        )
        # team WINS each Season ⇒ retained; no luxury tax stamped.
        seasons = _fin05_three_completed_seasons(league, team, opp, win=True)
        latest = seasons[-1]
        _fin05_ensure_finances(league, latest)
        _fin05_TSF.objects.filter(team=team).update(luxury_tax=0.0)
        _fin05_ensure(league, latest)

        ev = OwnerEvaluation.objects.get(league=league, season=latest)
        self.assertEqual(ev.verdict, "retained")
        self.assertEqual(ev.fired_reason, "")


class TestFin05FinanceOffInert(TestCase):
    """Toggle ON but `finance_enabled` OFF ⇒ no TeamSeasonFinance row ⇒ never a
    luxury fire; the row set is byte-identical to a no-FIN-05 (toggle-OFF) run
    with identical inputs."""

    def _run(self, *, finance_enabled, challenge):
        team = _make_team(f"Fin05Off{int(finance_enabled)}{int(challenge)}T")
        opp = _make_team(f"Fin05Off{int(finance_enabled)}{int(challenge)}O")
        league = _fin05_make_league(
            f"Fin05Off{int(finance_enabled)}{int(challenge)}L",
            current_team=team,
            finance_enabled=finance_enabled,
            challenge=challenge,
        )
        # team WINS each Season ⇒ mood-safe; only a luxury fire could occur.
        seasons = _fin05_three_completed_seasons(league, team, opp, win=True)
        latest = seasons[-1]
        # Stamp luxury tax ONLY makes sense with finance ON; with finance OFF the
        # writer never reads a finance row, so stamping is harmless.
        _fin05_ensure_finances(league, latest)
        _fin05_ensure(league, latest)
        return league, latest, team

    def test_finance_off_never_luxury_fires(self) -> None:
        league, latest, _team = self._run(finance_enabled=False, challenge=True)
        ev = OwnerEvaluation.objects.get(league=league, season=latest)
        # Mood-safe + no finance row ⇒ retained, empty reason, never luxury.
        self.assertEqual(ev.verdict, "retained")
        self.assertEqual(ev.fired_reason, "")

    def test_finance_off_byte_identical_to_toggle_off(self) -> None:
        # finance OFF + toggle ON vs finance OFF + toggle OFF must produce the
        # same row shape (verdict + fired_reason + deltas/totals).
        league_a, latest_a, _ta = self._run(finance_enabled=False, challenge=True)
        league_b, latest_b, _tb = self._run(finance_enabled=False, challenge=False)
        ev_a = OwnerEvaluation.objects.get(league=league_a, season=latest_a)
        ev_b = OwnerEvaluation.objects.get(league=league_b, season=latest_b)
        self.assertEqual(ev_a.verdict, ev_b.verdict)
        self.assertEqual(ev_a.fired_reason, ev_b.fired_reason)
        self.assertAlmostEqual(ev_a.wins_total, ev_b.wins_total, places=6)
        self.assertAlmostEqual(ev_a.playoffs_total, ev_b.playoffs_total, places=6)
        self.assertEqual(ev_a.money_delta, ev_b.money_delta)


class TestFin05NonCareerInert(TestCase):
    """`mode="multiplayer"` ⇒ writer early-returns, zero rows (CAR-03)."""

    def test_multiplayer_writes_zero_rows(self) -> None:
        team = _make_team("Fin05MpT")
        opp = _make_team("Fin05MpO")
        league = League.objects.create(
            name="Fin05MpL",
            mode="multiplayer",
            state="active",
            current_team=team,
            finance_enabled=True,
            challenge_fired_luxury_tax=True,
        )
        seasons = _fin05_three_completed_seasons(league, team, opp, win=True)
        latest = seasons[-1]
        _fin05_stamp_luxury_tax(team, latest, amount=50_000.0)
        _fin05_ensure(league, latest)
        self.assertEqual(OwnerEvaluation.objects.count(), 0)


# ---------------------------------------------------------------------------
# CONF-04 - the two-tier owner-mood playoff classification
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/conf-04-seam-contract.md`` §8 + §11.4;
# rationale in
# [ADR-0037](../../docs/adr/0037-worlds-is-a-derived-season-phase.md).
#
# ``matches/owner_mood.py`` is NOT TOUCHED - no new constant, no new branch, no
# new result string. Its ``"seeded"`` branch is already depth-proportional
# (``(PLAYOFF_ADVANCE_SCALE / num_rounds) * rounds_won``), so feeding it the
# longer two-bracket path yields the intended ladder for free.
#
# The whole change lives in ``_classify_playoffs_for_team``, which keeps its
# signature, its return triple and its four result strings. For a
# >= 2-Conference Season ``num_rounds`` becomes the Team's WHOLE possible path
# (its own Conference's Regional playoff + the Worlds bracket) and
# ``rounds_won`` counts the distinct rounds it won across BOTH.
#
# The accident this prevents: a >= 2-Conference career League previously scored
# ("none", 0, 0) forever, because a regional phase leaves ``tournament_id``
# NULL. The Worlds phase DOES set it, so without §8.1's branch the axis would
# have switched on and charged every non-qualifier the full ``"missed"``
# penalty regardless of how far it went in its own region.
#
# Every fixture below is hand-built - no simulation, no activation. Assertions
# are the returned triple and the row the writer produces; ``owner_mood``
# internals are NOT asserted (contract §11.3).
#
# Appended as a NEW class; no existing class above is modified.


from matches.models import Conference as _Conf04Conference  # noqa: E402


def _conf04_partitioned_season(prefix: str, *, sizes=(2, 2)):
    """A COMPLETED >= 2-Conference Season composed RR(1) -> regional(2) ->
    worlds(3), with every row written directly.

    The Conference snapshots are stamped explicitly because
    ``conference_by_team_id()`` reads ``starting_team_ids_json`` for a
    non-draft Season. Returns
    ``(league, season, conferences, groups, regional_phase, worlds_phase)``.
    """
    groups: list[list] = []
    for ci, size in enumerate(sizes, start=1):
        groups.append([_make_team(f"{prefix}{ci}x{ti}") for ti in range(size)])
    all_teams = [team for group in groups for team in group]

    league = _make_league(f"{prefix}L", current_team=all_teams[0])
    season = _make_completed_season(
        league,
        name="S1",
        start_date=date(2025, 1, 1),
        team_ids=[team.id for team in all_teams],
    )
    conferences = []
    for ci, group in enumerate(groups, start=1):
        conference = _Conf04Conference.objects.create(
            season=season,
            name=f"{prefix}Conf{ci}",
            ordinal=ci,
            starting_team_ids_json=sorted(team.id for team in group),
        )
        conference.teams.add(*group)
        conferences.append(conference)

    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    regional_phase = SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament", tournament_mode="standings"
    )
    worlds_phase = SeasonPhase.objects.create(
        season=season, ordinal=3, phase_type="tournament", tournament_mode="worlds"
    )
    return league, season, conferences, groups, regional_phase, worlds_phase


def _conf04_regional_bracket(
    prefix: str, phase, conference, teams, *, stage: str = "regional_playoff"
):
    """A Conference-scoped bracket on ``phase`` with ``teams`` seeded 1..n."""
    tournament = Tournament.objects.create(
        name=f"{prefix} {conference.name}",
        season_phase=phase,
        conference=conference,
        qualifier_stage=stage,
    )
    for seed, team in enumerate(teams, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=seed
        )
    return tournament


def _conf04_worlds_bracket(prefix: str, phase, teams):
    """The Season-wide Worlds bracket on the forward embed. Both CONF-02
    linkage columns are left NULL and ``qualifier_stage`` at ``""`` (§5.2)."""
    tournament = Tournament.objects.create(name=f"{prefix} Worlds")
    for seed, team in enumerate(teams, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=seed
        )
    phase.tournament = tournament
    phase.save(update_fields=["tournament"])
    return tournament


def _conf04_node(tournament, bracket_round: int, position: int, winner=None):
    return BracketNode.objects.create(
        tournament=tournament,
        bracket_round=bracket_round,
        position=position,
        winner=winner,
    )


class TestConf04ClassifyPlayoffsTwoTier(TestCase):
    """CONF-04 §8.4 - the five-row decision table, the two-bracket arithmetic,
    and the byte-identical 0/1-Conference path."""

    def setUp(self) -> None:
        (
            self.league,
            self.season,
            self.conferences,
            self.groups,
            self.regional_phase,
            self.worlds_phase,
        ) = _conf04_partitioned_season("Conf04Cls", sizes=(2, 2))
        # Conference 1's Regional playoff: a 2-round bracket. groups[0][0] wins
        # both rounds and takes the Conference crown.
        self.regional = _conf04_regional_bracket(
            "Conf04Cls", self.regional_phase, self.conferences[0], self.groups[0]
        )
        _conf04_node(self.regional, 1, 0, winner=self.groups[0][0])
        _conf04_node(self.regional, 2, 0, winner=self.groups[0][0])
        self.regional.champion = self.groups[0][0]
        self.regional.save(update_fields=["champion"])
        # Conference 2's Regional playoff, so its Teams have a bracket too.
        self.regional_two = _conf04_regional_bracket(
            "Conf04Cls2", self.regional_phase, self.conferences[1], self.groups[1]
        )
        _conf04_node(self.regional_two, 1, 0, winner=self.groups[1][0])
        _conf04_node(self.regional_two, 2, 0, winner=self.groups[1][0])
        self.regional_two.champion = self.groups[1][0]
        self.regional_two.save(update_fields=["champion"])
        # Worlds: a 2-round bracket between the two Conference champions.
        # groups[0][0] wins round 1; groups[1][0] wins the final and Worlds.
        self.worlds = _conf04_worlds_bracket(
            "Conf04Cls",
            self.worlds_phase,
            [self.groups[0][0], self.groups[1][0]],
        )
        _conf04_node(self.worlds, 1, 0, winner=self.groups[0][0])
        _conf04_node(self.worlds, 2, 0, winner=self.groups[1][0])
        self.worlds.champion = self.groups[1][0]
        self.worlds.save(update_fields=["champion"])

    # -- row 3: the Worlds champion ---------------------------------------

    def test_the_worlds_champion_is_classified_champion(self) -> None:
        result, rounds_won, num_rounds = _classify_playoffs_for_team(
            self.season, self.groups[1][0].id
        )
        self.assertEqual(result, "champion")
        self.assertEqual(rounds_won, 0, "the champion row carries rounds_won 0")

    def test_the_champion_num_rounds_is_the_whole_two_bracket_path(self) -> None:
        _result, _rounds_won, num_rounds = _classify_playoffs_for_team(
            self.season, self.groups[1][0].id
        )
        # 2 regional rounds + 2 Worlds rounds.
        self.assertEqual(num_rounds, 4)

    def test_only_the_worlds_bracket_can_crown_champion(self) -> None:
        # groups[0][0] IS a Conference champion and is NOT classified champion.
        result, _rounds_won, _num_rounds = _classify_playoffs_for_team(
            self.season, self.groups[0][0].id
        )
        self.assertNotEqual(result, "champion")

    # -- row 4: seeded, counted PER BRACKET then ADDED ---------------------

    def test_a_worlds_finalist_counts_rounds_from_both_brackets(self) -> None:
        # groups[0][0] won regional rounds 1 and 2, plus Worlds round 1.
        self.assertEqual(
            _classify_playoffs_for_team(self.season, self.groups[0][0].id),
            ("seeded", 3, 4),
        )

    def test_the_two_brackets_are_never_unioned_on_bracket_round(self) -> None:
        """The load-bearing arithmetic pin (§8.3). A Team that won round 1 of
        its region and round 1 of Worlds has won TWO rounds; a set union of the
        raw ``bracket_round`` integers would collapse them to ONE."""
        (
            _league,
            season,
            conferences,
            groups,
            regional_phase,
            worlds_phase,
        ) = _conf04_partitioned_season("Conf04Union", sizes=(2, 2))
        regional = _conf04_regional_bracket(
            "Conf04Union", regional_phase, conferences[0], groups[0]
        )
        # A ONE-round region: the Team wins bracket_round 1 here...
        _conf04_node(regional, 1, 0, winner=groups[0][0])
        regional.champion = groups[0][0]
        regional.save(update_fields=["champion"])
        worlds = _conf04_worlds_bracket(
            "Conf04Union", worlds_phase, [groups[0][0], groups[1][0]]
        )
        # ...and bracket_round 1 again here. Distinct rounds => 2, not 1.
        _conf04_node(worlds, 1, 0, winner=groups[0][0])
        _conf04_node(worlds, 2, 0, winner=groups[1][0])
        worlds.champion = groups[1][0]
        worlds.save(update_fields=["champion"])

        self.assertEqual(
            _classify_playoffs_for_team(season, groups[0][0].id),
            ("seeded", 2, 3),
        )

    def test_a_first_round_regional_exit_is_seeded_with_zero_rounds_won(self) -> None:
        self.assertEqual(
            _classify_playoffs_for_team(self.season, self.groups[0][1].id),
            ("seeded", 0, 4),
        )

    def test_being_a_participant_of_worlds_alone_is_enough_for_seeded(self) -> None:
        (
            _league,
            season,
            conferences,
            groups,
            regional_phase,
            worlds_phase,
        ) = _conf04_partitioned_season("Conf04WOnly", sizes=(2, 2))
        # Conference 1 is too small for a Regional playoff - it has none - but
        # its Standings leader still reached Worlds (the CONF-03 §2.3 fallback).
        worlds = _conf04_worlds_bracket(
            "Conf04WOnly", worlds_phase, [groups[0][0], groups[1][0]]
        )
        _conf04_node(worlds, 1, 0, winner=groups[1][0])
        worlds.champion = groups[1][0]
        worlds.save(update_fields=["champion"])
        self.assertEqual(
            _classify_playoffs_for_team(season, groups[0][0].id),
            ("seeded", 0, 1),
        )

    def test_the_denominator_is_the_same_for_every_team_of_a_conference(self) -> None:
        # ``num_rounds`` is the Conference's maximum path, not a per-Team value.
        _r1, _w1, first = _classify_playoffs_for_team(self.season, self.groups[0][0].id)
        _r2, _w2, second = _classify_playoffs_for_team(
            self.season, self.groups[0][1].id
        )
        self.assertEqual(first, second)

    # -- row 5: participant of neither -------------------------------------

    def test_a_team_in_neither_bracket_is_missed(self) -> None:
        (
            _league,
            season,
            conferences,
            groups,
            regional_phase,
            worlds_phase,
        ) = _conf04_partitioned_season("Conf04Miss", sizes=(3, 2))
        # Conference 1's Regional playoff seats only its top TWO Teams; the
        # third was cut.
        regional = _conf04_regional_bracket(
            "Conf04Miss", regional_phase, conferences[0], groups[0][:2]
        )
        _conf04_node(regional, 1, 0, winner=groups[0][0])
        regional.champion = groups[0][0]
        regional.save(update_fields=["champion"])
        worlds = _conf04_worlds_bracket(
            "Conf04Miss", worlds_phase, [groups[0][0], groups[1][0]]
        )
        _conf04_node(worlds, 1, 0, winner=groups[0][0])
        worlds.champion = groups[0][0]
        worlds.save(update_fields=["champion"])

        self.assertEqual(
            _classify_playoffs_for_team(season, groups[0][2].id),
            ("missed", 0, 2),
        )

    # -- row 1: a Team in no Conference at all -----------------------------

    def test_an_unpartitioned_team_is_none_not_missed(self) -> None:
        """A pinned defensive choice (§8.4 row 1): an unpartitioned Team in a
        partitioned Season is BROKEN DATA, and broken data must not fire a
        Manager. ``"none"`` is the neutral 0.0 delta; ``"missed"`` is -0.2."""
        stray = _make_team("Conf04Stray")
        self.season.teams.add(stray)
        self.assertEqual(
            _classify_playoffs_for_team(self.season, stray.id), ("none", 0, 0)
        )

    # -- row 2: no bracket at all ------------------------------------------

    def test_no_bracket_anywhere_is_none(self) -> None:
        (
            _league,
            season,
            _conferences,
            groups,
            _regional_phase,
            _worlds_phase,
        ) = _conf04_partitioned_season("Conf04NoBr", sizes=(2, 2))
        # Neither phase ever built: this is the pre-CONF-04 accident's shape,
        # and it must stay NEUTRAL rather than flipping the axis on.
        self.assertEqual(
            _classify_playoffs_for_team(season, groups[0][0].id), ("none", 0, 0)
        )

    def test_an_unbuilt_worlds_phase_alone_does_not_switch_the_axis_on(self) -> None:
        (
            _league,
            season,
            conferences,
            groups,
            regional_phase,
            _worlds_phase,
        ) = _conf04_partitioned_season("Conf04Unbuilt", sizes=(2, 2))
        # Only Conference 2 fielded a bracket; Conference 1's Team has neither a
        # region nor a built Worlds bracket.
        _conf04_regional_bracket(
            "Conf04Unbuilt", regional_phase, conferences[1], groups[1]
        )
        self.assertEqual(
            _classify_playoffs_for_team(season, groups[0][0].id), ("none", 0, 0)
        )

    # -- the Last-chance bracket is excluded from BOTH sides ---------------

    def test_the_last_chance_bracket_is_outside_the_denominator(self) -> None:
        last_chance = _conf04_regional_bracket(
            "Conf04Lc",
            self.regional_phase,
            self.conferences[0],
            self.groups[0],
            stage="last_chance",
        )
        _conf04_node(last_chance, 1, 0, winner=self.groups[0][1])
        _conf04_node(last_chance, 2, 0, winner=self.groups[0][1])
        _conf04_node(last_chance, 3, 0, winner=self.groups[0][1])
        # Still 2 regional rounds + 2 Worlds rounds - the 3-round Last-chance
        # bracket contributes nothing.
        _result, _rounds_won, num_rounds = _classify_playoffs_for_team(
            self.season, self.groups[0][0].id
        )
        self.assertEqual(num_rounds, 4)

    def test_the_last_chance_bracket_is_outside_the_numerator(self) -> None:
        last_chance = _conf04_regional_bracket(
            "Conf04LcNum",
            self.regional_phase,
            self.conferences[0],
            self.groups[0],
            stage="last_chance",
        )
        _conf04_node(last_chance, 1, 0, winner=self.groups[0][1])
        # groups[0][1] won a Last-chance node and STILL has 0 rounds won: a Team
        # cannot ride the Last-chance bracket past its Conference's max path.
        self.assertEqual(
            _classify_playoffs_for_team(self.season, self.groups[0][1].id),
            ("seeded", 0, 4),
        )

    # -- the signature and result vocabulary are unchanged -----------------

    def test_the_return_is_always_the_flat_triple(self) -> None:
        for team in [self.groups[0][0], self.groups[0][1], self.groups[1][0]]:
            triple = _classify_playoffs_for_team(self.season, team.id)
            self.assertIsInstance(triple, tuple)
            self.assertEqual(len(triple), 3)
            self.assertIn(triple[0], {"champion", "seeded", "missed", "none"})
            self.assertIsInstance(triple[1], int)
            self.assertIsInstance(triple[2], int)

    def test_no_new_playoff_result_string_is_emitted(self) -> None:
        results = {
            _classify_playoffs_for_team(self.season, team.id)[0]
            for group in self.groups
            for team in group
        }
        self.assertTrue(results <= {"champion", "seeded", "missed", "none"})


class TestConf04ClassifyFlatPathIsByteIdentical(TestCase):
    """CONF-04 §8.2 / §12 invariant 4 - the 0/1-Conference branch is verbatim.
    These reproduce ``TestClassifyPlayoffsForTeam``'s triples exactly."""

    def _season_with_tournament(self, prefix: str, teams: list):
        league = _make_league(f"{prefix}L", current_team=teams[0])
        season = _make_completed_season(
            league,
            name="S1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        tournament = Tournament.objects.create(name=f"{prefix} Playoffs")
        SeasonPhase.objects.create(
            season=season, ordinal=2, phase_type="tournament", tournament=tournament
        )
        return season, tournament

    def test_zero_conference_champion_triple_is_unchanged(self) -> None:
        teams = [_make_team(f"Conf04FlatCh{i}") for i in range(2)]
        season, tournament = self._season_with_tournament("Conf04FlatCh", teams)
        for i, team in enumerate(teams):
            TournamentParticipant.objects.create(
                tournament=tournament, team=team, seed=i + 1
            )
        BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, winner=teams[0]
        )
        tournament.champion = teams[0]
        tournament.save(update_fields=["champion"])
        self.assertEqual(
            _classify_playoffs_for_team(season, teams[0].id), ("champion", 0, 1)
        )

    def test_zero_conference_seeded_triple_is_unchanged(self) -> None:
        teams = [_make_team(f"Conf04FlatSe{i}") for i in range(4)]
        season, tournament = self._season_with_tournament("Conf04FlatSe", teams)
        for i, team in enumerate(teams):
            TournamentParticipant.objects.create(
                tournament=tournament, team=team, seed=i + 1
            )
        BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, winner=teams[1]
        )
        BracketNode.objects.create(
            tournament=tournament, bracket_round=2, position=0, winner=teams[0]
        )
        tournament.champion = teams[0]
        tournament.save(update_fields=["champion"])
        self.assertEqual(
            _classify_playoffs_for_team(season, teams[1].id), ("seeded", 1, 2)
        )

    def test_zero_conference_missed_triple_is_unchanged(self) -> None:
        teams = [_make_team(f"Conf04FlatMi{i}") for i in range(2)]
        season, tournament = self._season_with_tournament("Conf04FlatMi", teams)
        TournamentParticipant.objects.create(
            tournament=tournament, team=teams[0], seed=1
        )
        BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, winner=teams[0]
        )
        tournament.champion = teams[0]
        tournament.save(update_fields=["champion"])
        outsider = _make_team("Conf04FlatOut")
        self.assertEqual(
            _classify_playoffs_for_team(season, outsider.id), ("missed", 0, 1)
        )

    def test_zero_conference_none_triple_is_unchanged(self) -> None:
        team = _make_team("Conf04FlatNone")
        league = _make_league("Conf04FlatNoneL", current_team=team)
        season = _make_completed_season(
            league, name="S1", start_date=date(2025, 1, 1), team_ids=[team.id]
        )
        self.assertEqual(_classify_playoffs_for_team(season, team.id), ("none", 0, 0))

    def test_a_one_conference_season_still_takes_the_flat_branch(self) -> None:
        # ``len(ordered_conferences()) >= 2`` is the branch predicate, so ONE
        # Conference keeps the verbatim scan.
        teams = [_make_team(f"Conf04One{i}") for i in range(2)]
        season, tournament = self._season_with_tournament("Conf04One", teams)
        _Conf04Conference.objects.create(
            season=season,
            name="Conf04OneConf",
            ordinal=1,
            starting_team_ids_json=sorted(t.id for t in teams),
        )
        for i, team in enumerate(teams):
            TournamentParticipant.objects.create(
                tournament=tournament, team=team, seed=i + 1
            )
        BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, winner=teams[0]
        )
        tournament.champion = teams[0]
        tournament.save(update_fields=["champion"])
        self.assertEqual(
            _classify_playoffs_for_team(season, teams[0].id), ("champion", 0, 1)
        )


class TestConf04PlayoffsDeltaThroughTheWriter(TestCase):
    """CONF-04 §11.2 - the owner-evaluation rows the writer produces. The
    ladder falls out of ``compute_playoffs_delta``'s existing depth-proportional
    ``"seeded"`` branch; ``owner_mood.py`` gains nothing (§12 invariant 4)."""

    def _evaluation_for(self, league, season):
        _ensure_owner_evaluations(league, season)
        return league.owner_evaluations.get(season=season)

    def test_the_worlds_champion_earns_the_title_delta(self) -> None:
        (
            league,
            season,
            conferences,
            groups,
            regional_phase,
            worlds_phase,
        ) = _conf04_partitioned_season("Conf04WrCh", sizes=(2, 2))
        league.current_team = groups[0][0]
        league.save(update_fields=["current_team"])
        regional = _conf04_regional_bracket(
            "Conf04WrCh", regional_phase, conferences[0], groups[0]
        )
        _conf04_node(regional, 1, 0, winner=groups[0][0])
        regional.champion = groups[0][0]
        regional.save(update_fields=["champion"])
        worlds = _conf04_worlds_bracket(
            "Conf04WrCh", worlds_phase, [groups[0][0], groups[1][0]]
        )
        _conf04_node(worlds, 1, 0, winner=groups[0][0])
        worlds.champion = groups[0][0]
        worlds.save(update_fields=["champion"])

        row = self._evaluation_for(league, season)
        self.assertGreater(row.playoffs_delta, 0.0)

    def test_a_non_qualifier_is_not_charged_the_full_miss_penalty(self) -> None:
        """The pre-CONF-04 accident's regression guard. A Team that reached its
        own Regional playoff but not Worlds is ``"seeded"``, never
        ``"missed"``."""
        (
            league,
            season,
            conferences,
            groups,
            regional_phase,
            worlds_phase,
        ) = _conf04_partitioned_season("Conf04WrSe", sizes=(2, 2))
        league.current_team = groups[0][1]
        league.save(update_fields=["current_team"])
        regional = _conf04_regional_bracket(
            "Conf04WrSe", regional_phase, conferences[0], groups[0]
        )
        _conf04_node(regional, 1, 0, winner=groups[0][0])
        regional.champion = groups[0][0]
        regional.save(update_fields=["champion"])
        worlds = _conf04_worlds_bracket(
            "Conf04WrSe", worlds_phase, [groups[0][0], groups[1][0]]
        )
        _conf04_node(worlds, 1, 0, winner=groups[0][0])
        worlds.champion = groups[0][0]
        worlds.save(update_fields=["champion"])

        self.assertEqual(
            _classify_playoffs_for_team(season, groups[0][1].id)[0], "seeded"
        )
        row = self._evaluation_for(league, season)
        self.assertEqual(row.playoffs_delta, 0.0, "seeded with 0 rounds won gives 0.0")

    def test_the_verdict_column_is_still_written(self) -> None:
        (
            league,
            season,
            conferences,
            groups,
            regional_phase,
            worlds_phase,
        ) = _conf04_partitioned_season("Conf04WrV", sizes=(2, 2))
        league.current_team = groups[0][0]
        league.save(update_fields=["current_team"])
        _conf04_worlds_bracket("Conf04WrV", worlds_phase, [groups[0][0], groups[1][0]])
        row = self._evaluation_for(league, season)
        self.assertIn(row.verdict, {"retained", "hot_seat", "fired"})
