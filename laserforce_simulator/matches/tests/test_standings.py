"""LG-01 — Pure-unit tests for ``matches/standings.py``.

No DB, no Django imports in the assertion path. The seam contract is
locked at ``.claude/worktrees/lg-01-seam-contract.md`` (§2b, §6b). Pure
``SimpleTestCase`` with hand-crafted dict-list inputs.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches.standings import StandingsRow, compute_standings

# ---------------------------------------------------------------------------
# Helpers — build one match dict (the 8-key shape, §2b)
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
) -> dict:
    """Build one ``completed_matches`` entry with every locked key."""
    return {
        "match_id": match_id,
        "team_red_id": team_red_id,
        "team_blue_id": team_blue_id,
        "winner_team_id": winner_team_id,
        "red_rounds_won": red_rounds_won,
        "blue_rounds_won": blue_rounds_won,
        "red_total_points": red_total_points,
        "blue_total_points": blue_total_points,
    }


# ---------------------------------------------------------------------------
# §6b — Empty input
# ---------------------------------------------------------------------------


class TestComputeStandingsEmptyInput(SimpleTestCase):
    """No matches / no enrolled teams edges."""

    def test_no_matches_all_enrolled_rows_zeroed(self) -> None:
        rows = compute_standings([], [(1, "A"), (2, "B")])
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r.matches_played, 0)
            self.assertEqual(r.wins, 0)
            self.assertEqual(r.losses, 0)
            self.assertEqual(r.ties, 0)
            self.assertEqual(r.league_points, 0)
            self.assertEqual(r.round_wins, 0)
            self.assertEqual(r.total_score, 0)

    def test_no_enrolled_teams_and_no_matches_returns_empty_list(self) -> None:
        rows = compute_standings([], [])
        self.assertEqual(rows, [])

    def test_match_teams_not_in_enrolled_are_still_aggregated(self) -> None:
        # Contract §2b: "A match entry whose team_red_id or team_blue_id is
        # NOT in enrolled_teams is still aggregated (its rows are added to
        # the table). The Code agent does not filter — the view passes only
        # the Season's matches." So empty enrolled_teams + a non-empty match
        # list produces rows for the matched teams (defensive fallback —
        # the team name is the empty string when not in enrolled_teams).
        rows = compute_standings(
            [_match(match_id=1, team_red_id=1, team_blue_id=2)],
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
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=1,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].wins, 1)
        self.assertEqual(by_id[1].losses, 0)
        self.assertEqual(by_id[1].ties, 0)
        self.assertEqual(by_id[2].wins, 0)
        self.assertEqual(by_id[2].losses, 1)
        self.assertEqual(by_id[2].ties, 0)

    def test_one_match_blue_wins_blue_w_red_l(self) -> None:
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=2,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")])
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
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=None,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].ties, 1)
        self.assertEqual(by_id[2].ties, 1)
        self.assertEqual(by_id[1].wins, 0)
        self.assertEqual(by_id[2].wins, 0)
        self.assertEqual(by_id[1].losses, 0)
        self.assertEqual(by_id[2].losses, 0)

    def test_unknown_winner_id_counts_as_tie_defensive(self) -> None:
        # winner_team_id 999 is neither red (1) nor blue (2) -- defensive tie.
        matches = [
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=2,
                winner_team_id=999,
            ),
        ]
        rows = compute_standings(matches, [(1, "A"), (2, "B")])
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
        # Each of A and B has 1W (3 league_points) but A has more round_wins.
        matches = [
            # Team 1 (A) won match vs team 3 with red_rounds_won=2.
            _match(
                match_id=1,
                team_red_id=1,
                team_blue_id=3,
                winner_team_id=1,
                red_rounds_won=2,
                blue_rounds_won=0,
                red_total_points=100,
                blue_total_points=50,
            ),
            # Team 2 (B) won match vs team 4 with red_rounds_won=2 too --
            # equal round_wins on the W side -- so make A's W with more
            # round_wins overall. Give A a second match (also a tie, no W)
            # but with a round_win count contributing.
            _match(
                match_id=2,
                team_red_id=2,
                team_blue_id=4,
                winner_team_id=2,
                red_rounds_won=2,
                blue_rounds_won=1,
                red_total_points=100,
                blue_total_points=80,
            ),
            # A picks up an extra round_win in a tie match.
            _match(
                match_id=3,
                team_red_id=1,
                team_blue_id=5,
                winner_team_id=None,
                red_rounds_won=1,
                blue_rounds_won=1,
                red_total_points=50,
                blue_total_points=50,
            ),
        ]
        rows = compute_standings(
            matches,
            [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "E")],
        )
        # A has 1W + 1T = 3 + 1 = 4 points; B has 1W = 3 points -- A ranks
        # above B on league_points alone (so this case doesn't test the
        # tiebreak). Rewire so both tie on league_points and A has more
        # round_wins. The exact scaffold above doesn't isolate. Simpler:
        # make both teams 1W exactly, with A scoring more round_wins.
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
        rows2 = compute_standings(
            matches2,
            [(1, "A"), (2, "B"), (10, "Filler")],
        )
        by_id = {r.team_id: r for r in rows2}
        # Both A and B have 3 league_points.
        self.assertEqual(by_id[1].league_points, 3)
        self.assertEqual(by_id[2].league_points, 3)
        # A has more round_wins (2 > 1).
        self.assertGreater(by_id[1].round_wins, by_id[2].round_wins)
        # A ranks higher (lower rank number).
        self.assertLess(by_id[1].rank, by_id[2].rank)

    def test_tied_league_points_and_round_wins_resolved_by_total_score(
        self,
    ) -> None:
        # Both A and B win one match each with equal round_wins; A scores
        # more total_score.
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
        rows = compute_standings(
            matches,
            [(1, "A"), (2, "B"), (10, "Filler")],
        )
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].league_points, by_id[2].league_points)
        self.assertEqual(by_id[1].round_wins, by_id[2].round_wins)
        self.assertGreater(by_id[1].total_score, by_id[2].total_score)
        self.assertLess(by_id[1].rank, by_id[2].rank)

    def test_tied_on_all_three_resolved_by_team_name_alphabetical(self) -> None:
        # Two teams with identical records -- name asc breaks tie.
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
        )
        by_id = {r.team_id: r for r in rows}
        # Both Zebra (team 1) and Alpha (team 2) have identical records.
        self.assertEqual(by_id[1].league_points, by_id[2].league_points)
        self.assertEqual(by_id[1].round_wins, by_id[2].round_wins)
        self.assertEqual(by_id[1].total_score, by_id[2].total_score)
        # Alpha sorts before Zebra -- so team 2 ranks higher.
        self.assertLess(by_id[2].rank, by_id[1].rank)


# ---------------------------------------------------------------------------
# §6b — Rank populated 1-based and dense
# ---------------------------------------------------------------------------


class TestComputeStandingsRankPopulated(SimpleTestCase):
    """rank is 1-based, dense, in iteration order."""

    def test_rank_is_one_based_and_dense(self) -> None:
        rows = compute_standings([], [(1, "A"), (2, "B"), (3, "C")])
        ranks = [r.rank for r in rows]
        self.assertEqual(ranks, [1, 2, 3])


# ---------------------------------------------------------------------------
# §6b — Team-elim bonus flows through total_score
# ---------------------------------------------------------------------------


class TestComputeStandingsTeamElimBonusFlowsIn(SimpleTestCase):
    """The 10k team-elim bonus is already baked into red_total_points /
    blue_total_points by the view (Match.red_total_points property). The
    pure module simply sums it into total_score.
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
        rows = compute_standings(matches, [(1, "A"), (2, "B")])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].total_score, 15000)
        self.assertEqual(by_id[2].total_score, 2000)


# ---------------------------------------------------------------------------
# §6b — Defensive: no Django imports leaked into the pure module
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Mirrors the HX-01 / HX-02 / HX-03 / HX-04 / RES-04 / RV-03 /
    LG-00 / LG-00b precedent.

    Importing ``matches.standings`` in a fresh subprocess must not pull in
    ``django.*`` -- the pure module's import allowlist is ``dataclasses``
    + ``typing`` + ``collections``.
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
