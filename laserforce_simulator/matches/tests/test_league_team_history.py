"""LG-01z-e -- pure-unit tests for ``matches.team_history_logic``.

No DB, no Django, plus a ``TestNoDjangoImportsLeaked`` subprocess purity
check. The HTML Team History screen's view tests went with the screen
(ANG-08); the API serves the same tabs (``GET
/api/leagues/<id>/team-history/``).
"""

from __future__ import annotations

from django.test import TestCase

from laserforce_simulator.testing import run_project_python
from matches.team_history_logic import (
    OverallRecord,
    PlayerRollup,
    SeasonRow,
    compute_overall_record,
    compute_player_rollups,
    compute_season_rows,
    round_outcome,
)


class TestRoundOutcome(TestCase):
    def test_win_loss_tie(self) -> None:
        self.assertEqual(round_outcome(10, 5), "W")
        self.assertEqual(round_outcome(5, 10), "L")
        self.assertEqual(round_outcome(7, 7), "T")


class TestComputeOverallRecord(TestCase):
    def test_empty_is_all_zero(self) -> None:
        rec = compute_overall_record([], championships=0)
        self.assertEqual(rec, OverallRecord(0, 0, 0, 0, 0))

    def test_counts_outcomes(self) -> None:
        rec = compute_overall_record(
            ["W", "W", "L", "T"], championships=2, playoff_appearances=0
        )
        self.assertEqual(rec.wins, 2)
        self.assertEqual(rec.losses, 1)
        self.assertEqual(rec.ties, 1)
        self.assertEqual(rec.championships, 2)
        self.assertEqual(rec.playoff_appearances, 0)

    def test_unknown_outcome_counts_as_tie(self) -> None:
        rec = compute_overall_record(["?", "X"], championships=0)
        self.assertEqual((rec.wins, rec.losses, rec.ties), (0, 0, 2))


class TestComputeSeasonRows(TestCase):
    def test_empty(self) -> None:
        self.assertEqual(compute_season_rows([]), [])

    def test_maps_keys_in_order(self) -> None:
        rows = compute_season_rows(
            [
                {
                    "season_id": 2,
                    "year": 2027,
                    "wins": 3,
                    "losses": 1,
                    "ties": 0,
                    "rank": 1,
                },
                {
                    "season_id": 1,
                    "year": 2026,
                    "wins": 1,
                    "losses": 3,
                    "ties": 0,
                    "rank": 4,
                },
            ]
        )
        self.assertEqual([r.season_id for r in rows], [2, 1])
        self.assertEqual(rows[0], SeasonRow(2, 2027, 3, 1, 0, 1))
        self.assertIsNone(compute_season_rows([{"season_id": 5}])[0].rank)


class TestComputePlayerRollups(TestCase):
    def test_empty(self) -> None:
        self.assertEqual(compute_player_rollups([]), [])

    def test_folds_rounds_per_player(self) -> None:
        dicts = [
            {
                "player_id": 1,
                "player_name": "Ada",
                "on_team": True,
                "season_year": 2026,
                "points_scored": 100,
                "tags_made": 5,
                "times_tagged": 2,
                "missiles_landed": 0,
                "resupplies_given": 0,
                "specials_used": 1,
            },
            {
                "player_id": 1,
                "player_name": "Ada",
                "on_team": True,
                "season_year": 2027,
                "points_scored": 50,
                "tags_made": 3,
                "times_tagged": 1,
                "missiles_landed": 0,
                "resupplies_given": 0,
                "specials_used": 0,
            },
        ]
        rollups = compute_player_rollups(dicts)
        self.assertEqual(len(rollups), 1)
        r = rollups[0]
        self.assertEqual(r.games_played, 2)
        self.assertEqual(r.stats["points_scored"], 150)
        self.assertEqual(r.stats["tags_made"], 8)
        self.assertEqual(r.last_season_year, 2027)
        self.assertTrue(r.on_team)
        self.assertEqual(r.colour_class, "team-history-player-green")

    def test_off_team_is_blue(self) -> None:
        r = compute_player_rollups(
            [{"player_id": 9, "player_name": "Bo", "on_team": False}]
        )[0]
        self.assertFalse(r.on_team)
        self.assertEqual(r.colour_class, "team-history-player-blue")

    def test_sorted_by_name_then_id(self) -> None:
        rollups = compute_player_rollups(
            [
                {"player_id": 3, "player_name": "Zed", "on_team": True},
                {"player_id": 1, "player_name": "Ada", "on_team": True},
                {"player_id": 2, "player_name": "Ada", "on_team": True},
            ]
        )
        self.assertEqual([r.player_id for r in rollups], [1, 2, 3])

    def test_last_season_year_none_when_all_none(self) -> None:
        r = compute_player_rollups(
            [{"player_id": 1, "player_name": "X", "on_team": True, "season_year": None}]
        )[0]
        self.assertIsNone(r.last_season_year)
        self.assertIsInstance(r, PlayerRollup)


class TestNoDjangoImportsLeaked(TestCase):
    """The pure module must import zero Django modules."""

    def test_no_django_in_sys_modules(self) -> None:
        code = (
            "import sys\n"
            "import matches.team_history_logic\n"
            "leaked = [m for m in sys.modules if m == 'django' "
            "or m.startswith('django.')]\n"
            "assert not leaked, leaked\n"
        )
        result = run_project_python(code)
        self.assertEqual(result.returncode, 0, result.stderr)
