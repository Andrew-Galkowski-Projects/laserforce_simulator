"""LG-01 / LG-06g — Pure-unit tests for ``matches/standings.py``.

No DB, no Django imports in the assertion path. The LG-01 seam contract is
locked at ``.claude/worktrees/lg-01-seam-contract.md`` (§2b, §6b); the LG-06g
form / side-detail extension at ``.claude/worktrees/lg-06g-seam-contract.md``.
Pure ``SimpleTestCase`` with hand-crafted dict-list inputs.

LG-06g changed the signature to
``compute_standings(completed_matches, enrolled_teams, season_rounds)`` — every
callsite below passes the 3rd arg (``[]`` where only Match-grain columns are
exercised).
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches.standings import (
    StandingsRow,
    compute_standings,
    match_points_by_team,
    match_score,
    rerank_round_robin,
)

# ---------------------------------------------------------------------------
# Helpers — build one match dict (9-key shape) and one round dict (6-key shape)
# ---------------------------------------------------------------------------


def _match(
    *,
    match_id: int = 1,
    team_red_id: int = 100,
    team_blue_id: int = 200,
    winner_team_id: int | None = None,
    red_rounds_won: int = 0,
    blue_rounds_won: int = 0,
    red_total_points: int = 0,
    blue_total_points: int = 0,
    date_played: int = 0,
) -> dict:
    """Build one ``completed_matches`` entry with every locked key.

    ``date_played`` defaults to ``0`` so order falls to the ``match_id``
    tiebreak; streak / L5 tests pass it explicitly to control ordering.
    """
    return {
        "match_id": match_id,
        "team_red_id": team_red_id,
        "team_blue_id": team_blue_id,
        "winner_team_id": winner_team_id,
        "red_rounds_won": red_rounds_won,
        "blue_rounds_won": blue_rounds_won,
        "red_total_points": red_total_points,
        "blue_total_points": blue_total_points,
        "date_played": date_played,
    }


def _round(
    *,
    round_id: int = 1,
    team_red_id: int = 100,
    team_blue_id: int = 200,
    red_points: int = 0,
    blue_points: int = 0,
    date_played: int = 0,
) -> dict:
    """Build one ``season_rounds`` entry (6-key shape, physical sides)."""
    return {
        "round_id": round_id,
        "team_red_id": team_red_id,
        "team_blue_id": team_blue_id,
        "red_points": red_points,
        "blue_points": blue_points,
        "date_played": date_played,
    }


# ---------------------------------------------------------------------------
# §6b — Empty input
# ---------------------------------------------------------------------------


class TestComputeStandingsEmptyInput(SimpleTestCase):
    """No matches / no enrolled teams edges."""

    def test_no_matches_all_enrolled_rows_zeroed(self) -> None:
        rows = compute_standings([], [(1, "A"), (2, "B")], [])
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r.matches_played, 0)
            self.assertEqual(r.wins, 0)
            self.assertEqual(r.losses, 0)
            self.assertEqual(r.ties, 0)
            self.assertEqual(r.league_points, 0)
            self.assertEqual(r.round_wins, 0)
            self.assertEqual(r.total_score, 0)
            # LG-06g — zeroed form / side fields on an empty Season.
            self.assertEqual(r.match_streak, ("", 0))
            self.assertEqual(r.match_l5, (0, 0, 0))
            self.assertEqual(r.round_streak, ("", 0))
            self.assertEqual(r.round_l5, (0, 0, 0))
            self.assertEqual(r.red_wlt, (0, 0, 0))
            self.assertEqual(r.blue_wlt, (0, 0, 0))
            self.assertEqual(r.red_points_for, 0)
            self.assertEqual(r.blue_points_for, 0)

    def test_no_enrolled_teams_and_no_matches_returns_empty_list(self) -> None:
        rows = compute_standings([], [], [])
        self.assertEqual(rows, [])

    def test_match_teams_not_in_enrolled_are_still_aggregated(self) -> None:
        rows = compute_standings(
            [_match(match_id=1, team_red_id=1, team_blue_id=2)],
            [],
            [],
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.team_id for r in rows}, {1, 2})


# ---------------------------------------------------------------------------
# §6b — Basic win/loss
# ---------------------------------------------------------------------------


class TestComputeStandingsBasicWinLoss(SimpleTestCase):
    """W/L attribution and league_points math."""

    def test_one_match_red_wins_red_w_blue_l(self) -> None:
        matches = [
            _match(match_id=1, team_red_id=1, team_blue_id=2, winner_team_id=1),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].wins, 1)
        self.assertEqual(by_id[1].losses, 0)
        self.assertEqual(by_id[1].ties, 0)
        self.assertEqual(by_id[2].wins, 0)
        self.assertEqual(by_id[2].losses, 1)
        self.assertEqual(by_id[2].ties, 0)

    def test_one_match_blue_wins_blue_w_red_l(self) -> None:
        matches = [
            _match(match_id=1, team_red_id=1, team_blue_id=2, winner_team_id=2),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[2].wins, 1)
        self.assertEqual(by_id[1].losses, 1)

    def test_league_points_3w_1t_0l(self) -> None:
        # team 1 record: 2W + 1T + 1L = 2*3 + 1 + 0 = 7 league_points.
        matches = [
            _match(match_id=1, team_red_id=1, team_blue_id=2, winner_team_id=1),
            _match(match_id=2, team_red_id=1, team_blue_id=3, winner_team_id=1),
            _match(match_id=3, team_red_id=1, team_blue_id=4, winner_team_id=None),
            _match(match_id=4, team_red_id=1, team_blue_id=5, winner_team_id=5),
        ]
        rows = compute_standings(
            matches,
            [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "E")],
            [],
        )
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].wins, 2)
        self.assertEqual(by_id[1].ties, 1)
        self.assertEqual(by_id[1].losses, 1)
        self.assertEqual(by_id[1].league_points, 7)


# ---------------------------------------------------------------------------
# §6b — Tie attribution
# ---------------------------------------------------------------------------


class TestComputeStandingsTie(SimpleTestCase):
    """Null + defensive-unknown winner ids count as tie for both sides."""

    def test_winner_team_id_none_counts_as_tie_both(self) -> None:
        matches = [
            _match(match_id=1, team_red_id=1, team_blue_id=2, winner_team_id=None),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].ties, 1)
        self.assertEqual(by_id[2].ties, 1)
        self.assertEqual(by_id[1].wins, 0)
        self.assertEqual(by_id[2].wins, 0)
        self.assertEqual(by_id[1].losses, 0)
        self.assertEqual(by_id[2].losses, 0)

    def test_unknown_winner_id_counts_as_tie_defensive(self) -> None:
        matches = [
            _match(match_id=1, team_red_id=1, team_blue_id=2, winner_team_id=999),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].ties, 1)
        self.assertEqual(by_id[2].ties, 1)


# ---------------------------------------------------------------------------
# §6b — Tiebreak ladder
# ---------------------------------------------------------------------------


class TestComputeStandingsTiebreakLadder(SimpleTestCase):
    """Sort ladder: league_points desc, round_wins desc, total_score desc,
    team_name asc.
    """

    def test_tied_league_points_resolved_by_round_wins(self) -> None:
        matches2 = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=10,
                winner_team_id=1,
                red_rounds_won=2,
                blue_rounds_won=0,
                red_total_points=100,
                blue_total_points=10,
            ),
            _match(
                match_id=2,
                team_red_id=2,
                team_blue_id=10,
                winner_team_id=2,
                red_rounds_won=1,
                blue_rounds_won=1,
                red_total_points=80,
                blue_total_points=20,
            ),
        ]
        rows2 = compute_standings(matches2, [(1, "A"), (2, "B"), (10, "Filler")], [])
        by_id = {r.team_id: r for r in rows2}
        self.assertEqual(by_id[1].league_points, 3)
        self.assertEqual(by_id[2].league_points, 3)
        self.assertGreater(by_id[1].round_wins, by_id[2].round_wins)
        self.assertLess(by_id[1].rank, by_id[2].rank)

    def test_tied_league_points_and_round_wins_resolved_by_total_score(
        self,
    ) -> None:
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=10,
                winner_team_id=1,
                red_rounds_won=2,
                blue_rounds_won=0,
                red_total_points=500,
                blue_total_points=10,
            ),
            _match(
                match_id=2,
                team_red_id=2,
                team_blue_id=10,
                winner_team_id=2,
                red_rounds_won=2,
                blue_rounds_won=0,
                red_total_points=200,
                blue_total_points=10,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B"), (10, "Filler")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].league_points, by_id[2].league_points)
        self.assertEqual(by_id[1].round_wins, by_id[2].round_wins)
        self.assertGreater(by_id[1].total_score, by_id[2].total_score)
        self.assertLess(by_id[1].rank, by_id[2].rank)

    def test_tied_on_all_three_resolved_by_team_name_alphabetical(self) -> None:
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=10,
                winner_team_id=1,
                red_rounds_won=2,
                blue_rounds_won=0,
                red_total_points=100,
                blue_total_points=10,
            ),
            _match(
                match_id=2,
                team_red_id=2,
                team_blue_id=11,
                winner_team_id=2,
                red_rounds_won=2,
                blue_rounds_won=0,
                red_total_points=100,
                blue_total_points=10,
            ),
        ]
        rows = compute_standings(
            matches,
            [(1, "Zebra"), (2, "Alpha"), (10, "Filler1"), (11, "Filler2")],
            [],
        )
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].league_points, by_id[2].league_points)
        self.assertEqual(by_id[1].round_wins, by_id[2].round_wins)
        self.assertEqual(by_id[1].total_score, by_id[2].total_score)
        self.assertLess(by_id[2].rank, by_id[1].rank)


# ---------------------------------------------------------------------------
# §6b — Rank populated 1-based and dense
# ---------------------------------------------------------------------------


class TestComputeStandingsRankPopulated(SimpleTestCase):
    """rank is 1-based, dense, in iteration order."""

    def test_rank_is_one_based_and_dense(self) -> None:
        rows = compute_standings([], [(1, "A"), (2, "B"), (3, "C")], [])
        ranks = [r.rank for r in rows]
        self.assertEqual(ranks, [1, 2, 3])


# ---------------------------------------------------------------------------
# §6b — Team-elim bonus flows through total_score
# ---------------------------------------------------------------------------


class TestComputeStandingsTeamElimBonusFlowsIn(SimpleTestCase):
    """The 10k team-elim bonus is already baked into red_total_points /
    blue_total_points by the view; the pure module just sums it.
    """

    def test_red_total_points_carries_team_elim_bonus_into_total_score(
        self,
    ) -> None:
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                red_total_points=15000,  # 5k regular + 10k team-elim bonus
                blue_total_points=2000,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].total_score, 15000)
        self.assertEqual(by_id[2].total_score, 2000)


# ===========================================================================
# LG-06g — Match-grain form (match_streak / match_l5)
# ===========================================================================


class TestComputeStandingsMatchForm(SimpleTestCase):
    """match_streak / match_l5 read the completed-Match corpus, ordered by
    (date_played, match_id) ascending; the streak runs from the most recent.
    """

    def test_winning_streak_runs_from_most_recent(self) -> None:
        # Team 1 wins matches 1,2,3 in chronological order ⇒ ("W", 3).
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                date_played=1,
            ),
            _match(
                match_id=2,
                team_red_id=1,
                team_blue_id=3,
                winner_team_id=1,
                date_played=2,
            ),
            _match(
                match_id=3,
                team_red_id=1,
                team_blue_id=4,
                winner_team_id=1,
                date_played=3,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B"), (3, "C"), (4, "D")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].match_streak, ("W", 3))
        # The three opponents each have a fresh single-loss streak.
        self.assertEqual(by_id[2].match_streak, ("L", 1))

    def test_streak_breaks_on_result_change(self) -> None:
        # Most recent result is a loss ⇒ ("L", 1) regardless of prior wins.
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                date_played=1,
            ),
            _match(
                match_id=2,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                date_played=2,
            ),
            _match(
                match_id=3,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=2,
                date_played=3,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].match_streak, ("L", 1))

    def test_tie_streak(self) -> None:
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=None,
                date_played=1,
            ),
            _match(
                match_id=2,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=None,
                date_played=2,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].match_streak, ("T", 2))

    def test_l5_window_counts_last_five_only(self) -> None:
        # 6 matches for team 1: oldest is a loss, the last 5 are W,W,W,W,L
        # (the very first loss falls outside the window).
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=2,
                date_played=1,
            ),
            _match(
                match_id=2,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                date_played=2,
            ),
            _match(
                match_id=3,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                date_played=3,
            ),
            _match(
                match_id=4,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                date_played=4,
            ),
            _match(
                match_id=5,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                date_played=5,
            ),
            _match(
                match_id=6,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=2,
                date_played=6,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        # Last 5 (matches 2..6) = W,W,W,W,L ⇒ (4,1,0). Streak = ("L",1).
        self.assertEqual(by_id[1].match_l5, (4, 1, 0))
        self.assertEqual(by_id[1].match_streak, ("L", 1))

    def test_ordering_tiebreak_by_match_id_when_dates_equal(self) -> None:
        # Same date_played ⇒ order by match_id ⇒ most-recent = match_id 2 (a
        # loss), so the streak is the loss not the win.
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
                date_played=5,
            ),
            _match(
                match_id=2,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=2,
                date_played=5,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")], [])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].match_streak, ("L", 1))


# ===========================================================================
# LG-06g — Round-grain form (round_streak / round_l5)
# ===========================================================================


class TestComputeStandingsRoundForm(SimpleTestCase):
    """round_streak / round_l5 read the season_rounds corpus (every persisted
    Round), independent of the Match corpus; team's own W/L/T regardless of
    physical side.
    """

    def test_round_form_independent_of_match_corpus(self) -> None:
        # No completed matches at all — round form still computes.
        rounds = [
            _round(
                round_id=1,
                team_red_id=1,
                team_blue_id=2,
                red_points=100,
                blue_points=50,
                date_played=1,
            ),
            _round(
                round_id=2,
                team_red_id=2,
                team_blue_id=1,
                red_points=20,
                blue_points=80,
                date_played=2,
            ),
        ]
        rows = compute_standings([], [(1, "A"), (2, "B")], rounds)
        by_id = {r.team_id: r for r in rows}
        # Team 1 won round 1 (red, 100>50) and round 2 (blue, 80>20) ⇒ W,W.
        self.assertEqual(by_id[1].round_streak, ("W", 2))
        self.assertEqual(by_id[1].round_l5, (2, 0, 0))
        # Match-grain stays zeroed (no completed matches passed in).
        self.assertEqual(by_id[1].match_streak, ("", 0))

    def test_round_result_when_team_plays_blue(self) -> None:
        rounds = [
            _round(
                round_id=1,
                team_red_id=2,
                team_blue_id=1,
                red_points=10,
                blue_points=99,
                date_played=1,
            ),
        ]
        rows = compute_standings([], [(1, "A"), (2, "B")], rounds)
        by_id = {r.team_id: r for r in rows}
        # Team 1 played blue and blue won (99>10) ⇒ W.
        self.assertEqual(by_id[1].round_streak, ("W", 1))
        self.assertEqual(by_id[2].round_streak, ("L", 1))

    def test_round_tie(self) -> None:
        rounds = [
            _round(
                round_id=1,
                team_red_id=1,
                team_blue_id=2,
                red_points=50,
                blue_points=50,
                date_played=1,
            ),
        ]
        rows = compute_standings([], [(1, "A"), (2, "B")], rounds)
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].round_streak, ("T", 1))
        self.assertEqual(by_id[1].round_l5, (0, 0, 1))

    def test_round_l5_window(self) -> None:
        # 6 rounds for team 1, all as red; results L,W,W,W,W,W (oldest first).
        rounds = [
            _round(
                round_id=1,
                team_red_id=1,
                team_blue_id=2,
                red_points=10,
                blue_points=99,
                date_played=1,
            ),
            _round(
                round_id=2,
                team_red_id=1,
                team_blue_id=2,
                red_points=99,
                blue_points=10,
                date_played=2,
            ),
            _round(
                round_id=3,
                team_red_id=1,
                team_blue_id=2,
                red_points=99,
                blue_points=10,
                date_played=3,
            ),
            _round(
                round_id=4,
                team_red_id=1,
                team_blue_id=2,
                red_points=99,
                blue_points=10,
                date_played=4,
            ),
            _round(
                round_id=5,
                team_red_id=1,
                team_blue_id=2,
                red_points=99,
                blue_points=10,
                date_played=5,
            ),
            _round(
                round_id=6,
                team_red_id=1,
                team_blue_id=2,
                red_points=99,
                blue_points=10,
                date_played=6,
            ),
        ]
        rows = compute_standings([], [(1, "A"), (2, "B")], rounds)
        by_id = {r.team_id: r for r in rows}
        # Last 5 (rounds 2..6) = all W ⇒ (5,0,0); the early loss falls outside.
        self.assertEqual(by_id[1].round_l5, (5, 0, 0))
        self.assertEqual(by_id[1].round_streak, ("W", 5))


# ===========================================================================
# LG-06g — Side split (red_wlt / blue_wlt / red_points_for / blue_points_for)
# ===========================================================================


class TestComputeStandingsSideSplit(SimpleTestCase):
    """Per-physical-side W-L-T + points; a team aggregates into both sides
    across the Season.
    """

    def test_red_side_record_and_points(self) -> None:
        rounds = [
            _round(
                round_id=1,
                team_red_id=1,
                team_blue_id=2,
                red_points=100,
                blue_points=40,
            ),
            _round(
                round_id=2, team_red_id=1, team_blue_id=2, red_points=30, blue_points=70
            ),
        ]
        rows = compute_standings([], [(1, "A"), (2, "B")], rounds)
        by_id = {r.team_id: r for r in rows}
        # Team 1 played red both rounds: 1 win (100>40), 1 loss (30<70).
        self.assertEqual(by_id[1].red_wlt, (1, 1, 0))
        self.assertEqual(by_id[1].red_points_for, 130)
        self.assertEqual(by_id[1].blue_wlt, (0, 0, 0))
        self.assertEqual(by_id[1].blue_points_for, 0)
        # Team 2 played blue both rounds: 1 loss, 1 win.
        self.assertEqual(by_id[2].blue_wlt, (1, 1, 0))
        self.assertEqual(by_id[2].blue_points_for, 110)

    def test_team_aggregates_into_both_sides(self) -> None:
        rounds = [
            _round(
                round_id=1, team_red_id=1, team_blue_id=2, red_points=80, blue_points=20
            ),
            _round(
                round_id=2, team_red_id=2, team_blue_id=1, red_points=10, blue_points=90
            ),
        ]
        rows = compute_standings([], [(1, "A"), (2, "B")], rounds)
        by_id = {r.team_id: r for r in rows}
        # Team 1: red in round 1 (win, 80 pts), blue in round 2 (win, 90 pts).
        self.assertEqual(by_id[1].red_wlt, (1, 0, 0))
        self.assertEqual(by_id[1].red_points_for, 80)
        self.assertEqual(by_id[1].blue_wlt, (1, 0, 0))
        self.assertEqual(by_id[1].blue_points_for, 90)

    def test_side_tie_increments_t_on_both_sides(self) -> None:
        rounds = [
            _round(
                round_id=1, team_red_id=1, team_blue_id=2, red_points=50, blue_points=50
            ),
        ]
        rows = compute_standings([], [(1, "A"), (2, "B")], rounds)
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].red_wlt, (0, 0, 1))
        self.assertEqual(by_id[2].blue_wlt, (0, 0, 1))
        self.assertEqual(by_id[1].red_points_for, 50)
        self.assertEqual(by_id[2].blue_points_for, 50)


# ---------------------------------------------------------------------------
# §6b — Defensive: no Django imports leaked into the pure module
# ---------------------------------------------------------------------------


class TestMatchScore(SimpleTestCase):
    """The 6-point Match score: +2 per Round won, +2 for winning the Match."""

    def test_sweep_by_red_is_six_zero(self) -> None:
        # Red wins both Rounds and the Match -> 2+2 (rounds) + 2 (match) = 6.
        self.assertEqual(match_score(2, 0, 100, 100, 200), (6, 0))

    def test_sweep_by_blue_is_zero_six(self) -> None:
        self.assertEqual(match_score(0, 2, 200, 100, 200), (0, 6))

    def test_split_rounds_match_winner_gets_bonus(self) -> None:
        # The spec example: Red wins R1, Blue wins R2 (1-1 on rounds), Blue wins
        # the Match on total points -> Red 2 (one round), Blue 2 (one round) + 2
        # (match) = 4. Score 2-4 in favour of Blue.
        self.assertEqual(match_score(1, 1, 200, 100, 200), (2, 4))

    def test_split_rounds_red_wins_match(self) -> None:
        self.assertEqual(match_score(1, 1, 100, 100, 200), (4, 2))

    def test_tied_match_no_match_bonus(self) -> None:
        # winner_team_id None (true tie) -> only the round points, no +2 bonus.
        self.assertEqual(match_score(1, 1, None, 100, 200), (2, 2))


class TestMatchPointsByTeam(SimpleTestCase):
    """``match_points_by_team`` sums each team's Match score across Matches
    (the ranking metric for both Swiss and round-robin standings)."""

    @staticmethod
    def _m(red_id, blue_id, rrw, brw, winner):
        return {
            "team_red_id": red_id,
            "team_blue_id": blue_id,
            "red_rounds_won": rrw,
            "blue_rounds_won": brw,
            "winner_team_id": winner,
        }

    def test_empty_input_is_empty_dict(self) -> None:
        self.assertEqual(match_points_by_team([]), {})

    def test_single_match_sweep(self) -> None:
        pts = match_points_by_team([self._m(1, 2, 2, 0, 1)])
        self.assertEqual(pts, {1: 6, 2: 0})

    def test_accumulates_across_matches(self) -> None:
        # Team 1: a 6-0 sweep then a scrappy 4-2 split win -> 10.
        # Team 2: lost the sweep (0). Team 3: lost the split (2).
        pts = match_points_by_team(
            [
                self._m(1, 2, 2, 0, 1),  # team1 sweeps team2
                self._m(1, 3, 1, 1, 1),  # team1 wins split over team3
            ]
        )
        self.assertEqual(pts[1], 10)
        self.assertEqual(pts[2], 0)
        self.assertEqual(pts[3], 2)

    def test_split_win_scores_below_a_sweep(self) -> None:
        # The whole point: a dominant sweep (6) outranks a scrappy split win (4),
        # which 3*wins (both = 3) could not distinguish.
        sweep = match_points_by_team([self._m(1, 2, 2, 0, 1)])
        split = match_points_by_team([self._m(3, 4, 1, 1, 3)])
        self.assertGreater(sweep[1], split[3])


class TestRerankRoundRobin(SimpleTestCase):
    """``rerank_round_robin`` re-ranks on (match wins desc, Match score desc,
    round_wins desc, total_score desc) — wins PRIMARY, Match score (in
    league_points) the TIEBREAKER — stably, renumbering rank 1-based dense."""

    @staticmethod
    def _row(team_id, wins, league_points, *, round_wins=0, total_score=0, rank=0):
        return StandingsRow(
            team_id=team_id,
            matches_played=0,
            wins=wins,
            losses=0,
            ties=0,
            league_points=league_points,
            round_wins=round_wins,
            total_score=total_score,
            rank=rank,
            match_streak=("", 0),
            match_l5=(0, 0, 0),
            round_streak=("", 0),
            round_l5=(0, 0, 0),
            red_wlt=(0, 0, 0),
            blue_wlt=(0, 0, 0),
            red_points_for=0,
            blue_points_for=0,
        )

    def test_empty_input(self) -> None:
        self.assertEqual(rerank_round_robin([]), [])

    def test_match_wins_are_primary(self) -> None:
        # More match wins ranks higher even with FEWER Match-score points: two
        # split wins (2 wins, 8 pts) beats one sweep (1 win, 6 pts).
        rows = [self._row(1, 1, 6), self._row(2, 2, 8)]
        ordered = rerank_round_robin(rows)
        self.assertEqual([r.team_id for r in ordered], [2, 1])
        self.assertEqual([r.rank for r in ordered], [1, 2])

    def test_match_score_breaks_ties_at_equal_wins(self) -> None:
        # Equal wins -> higher Match score first: two sweeps (12) over two splits
        # (8), both at 2 wins.
        rows = [self._row(1, 2, 8), self._row(2, 2, 12)]
        ordered = rerank_round_robin(rows)
        self.assertEqual([r.team_id for r in ordered], [2, 1])

    def test_round_wins_then_total_score_break_further_ties(self) -> None:
        rows = [
            self._row(1, 2, 8, round_wins=2, total_score=100),
            self._row(2, 2, 8, round_wins=3, total_score=50),
            self._row(3, 2, 8, round_wins=2, total_score=200),
        ]
        ordered = rerank_round_robin(rows)
        self.assertEqual([r.team_id for r in ordered], [2, 3, 1])

    def test_stable_tail_preserved_on_full_tie(self) -> None:
        # Fully tied rows keep their input order (the compute_standings
        # team_name-asc tail survives as the final tiebreak).
        rows = [self._row(5, 1, 3), self._row(2, 1, 3), self._row(9, 1, 3)]
        ordered = rerank_round_robin(rows)
        self.assertEqual([r.team_id for r in ordered], [5, 2, 9])


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.standings`` in a fresh subprocess must not pull in
    ``django.*`` — the allowlist is ``dataclasses`` + ``typing`` +
    ``collections``.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.standings  # noqa: F401

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


# Reference to silence unused-import warnings if StandingsRow is dropped.
_ = StandingsRow
