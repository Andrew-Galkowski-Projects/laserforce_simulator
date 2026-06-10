"""LG-03 — Pure-unit tests for ``matches/season_awards.py``.

No DB, no Django imports in the assertion path. The seam contract is
locked at ``.claude/worktrees/lg-03-seam-contract.md`` (§1 pure module,
§1.2 ``AwardWinner``, §1.3 floor + tiebreak, §1.4 ``compute_season_awards``,
§8.1 test boundary). Mirrors the HX-03 ``test_h2h_stats.py`` /
``matches/standings.py`` precedent — hand-crafted dict literals through a
``_round`` keyword helper, plus a ``TestNoDjangoImportsLeaked`` subprocess
fresh-import + ``sys.modules`` walk.

The pure function ``compute_season_awards`` consumes the
``_build_round_dicts`` output shape VERBATIM (the contract reuses it as the
per-PlayerRoundState seam) — the keys it actually reads are
``player_id / player_name / team_id / team_name / role / points_scored /
mvp / tags_made / times_tagged / accuracy / survival_seconds /
resupplies_given``. The nuke-elimination count is supplied separately as the
``nuke_elims_by_player`` map.

These tests assert on the PURE FUNCTION OUTPUT only — winner identity /
category / value / floor / tiebreak / dict shape — NEVER on simulated point
totals (the contract forbids it). The production module + view land in
parallel, so these FAIL until the Code agent lands ``matches/season_awards.py``.
"""

from __future__ import annotations

import unittest

from matches.season_awards import (
    AWARD_CATEGORIES,
    HEADLINE_FINALS_MVP,
    HEADLINE_SEASON_MVP,
    AwardWinner,
    compute_season_awards,
)

# ---------------------------------------------------------------------------
# Pure-unit fixture helper — one ``_build_round_dicts``-shaped row.
# ---------------------------------------------------------------------------


def _round(
    *,
    player_id: int,
    player_name: str = "P",
    team_id: int = 100,
    team_name: str = "Red",
    role: str = "scout",
    points_scored: float = 0.0,
    mvp: float = 0.0,
    tags_made: int = 0,
    times_tagged: int = 0,
    accuracy: float = 0.0,
    survival_seconds: float = 0.0,
    resupplies_given: int = 0,
) -> dict:
    """Build one per-PlayerRoundState seam dict with every read key populated.

    The default name/team are derived from ``player_id`` when not given so a
    multi-player fixture stays readable.
    """
    return {
        "player_id": player_id,
        "player_name": player_name if player_name != "P" else f"P{player_id}",
        "team_id": team_id,
        "team_name": team_name,
        "role": role,
        "points_scored": points_scored,
        "mvp": mvp,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "accuracy": accuracy,
        "survival_seconds": survival_seconds,
        "resupplies_given": resupplies_given,
    }


def _winner(result: dict, category: str):
    """Read the single ``AwardWinner`` (or ``None``) at ``category``."""
    return result[category]


# ===========================================================================
# Returned dict shape
# ===========================================================================


class TestReturnDictShape(unittest.TestCase):
    """The returned dict is keyed by category; tag_ratio is a list; the two
    headline keys are ``AwardWinner | None``."""

    def test_keys_present_for_every_category_and_headline(self) -> None:
        result = compute_season_awards([], {}, [], None)
        for key, _label in AWARD_CATEGORIES:
            self.assertIn(key, result)
        self.assertIn(HEADLINE_SEASON_MVP, result)
        self.assertIn(HEADLINE_FINALS_MVP, result)

    def test_headline_keys_are_the_locked_strings(self) -> None:
        self.assertEqual(HEADLINE_SEASON_MVP, "season_mvp")
        self.assertEqual(HEADLINE_FINALS_MVP, "finals_mvp")

    def test_award_categories_has_six_locked_entries(self) -> None:
        keys = [k for k, _ in AWARD_CATEGORIES]
        self.assertEqual(
            keys,
            [
                "most_points",
                "tag_ratio",
                "most_resupplies",
                "longest_survival",
                "most_efficient_nuke",
                "best_accuracy",
            ],
        )

    def test_tag_ratio_value_is_a_list(self) -> None:
        rows = [_round(player_id=1, role="scout", tags_made=10, times_tagged=2)]
        result = compute_season_awards(rows, {}, [], None)
        self.assertIsInstance(result["tag_ratio"], list)

    def test_empty_input_every_category_absent(self) -> None:
        result = compute_season_awards([], {}, [], None)
        self.assertIsNone(result["most_points"])
        self.assertEqual(result["tag_ratio"], [])
        self.assertIsNone(result["most_resupplies"])
        self.assertIsNone(result["longest_survival"])
        self.assertIsNone(result["most_efficient_nuke"])
        self.assertIsNone(result["best_accuracy"])
        self.assertIsNone(result["season_mvp"])
        self.assertIsNone(result["finals_mvp"])

    def test_winner_is_award_winner_instance(self) -> None:
        rows = [_round(player_id=1, points_scored=500.0)]
        result = compute_season_awards(rows, {}, [], None)
        self.assertIsInstance(result["most_points"], AwardWinner)


# ===========================================================================
# Most Points — summed points_scored
# ===========================================================================


class TestMostPoints(unittest.TestCase):
    def test_winner_is_highest_summed_points(self) -> None:
        rows = [
            _round(player_id=1, points_scored=300.0),
            _round(player_id=1, points_scored=300.0),  # P1 sums to 600
            _round(player_id=2, points_scored=500.0),  # P2 sums to 500
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "most_points")
        self.assertEqual(win.player_id, 1)
        self.assertEqual(win.category, "most_points")

    def test_value_is_the_summed_total(self) -> None:
        rows = [
            _round(player_id=1, points_scored=300.0),
            _round(player_id=1, points_scored=250.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "most_points")
        self.assertEqual(win.value, 550.0)

    def test_no_role_floor_on_count_award(self) -> None:
        # P1 played 4 games but lower total; P2 played 1 game with a big total.
        # Most Points is a COUNT award — NO games floor — so the 1-game P2 wins.
        rows = [
            _round(player_id=1, points_scored=100.0),
            _round(player_id=1, points_scored=100.0),
            _round(player_id=1, points_scored=100.0),
            _round(player_id=1, points_scored=100.0),
            _round(player_id=2, points_scored=900.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "most_points")
        self.assertEqual(win.player_id, 2)

    def test_identity_fields_carried(self) -> None:
        rows = [
            _round(
                player_id=7,
                player_name="Ace",
                team_id=42,
                team_name="Blue",
                points_scored=800.0,
            )
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "most_points")
        self.assertEqual(win.player_id, 7)
        self.assertEqual(win.player_name, "Ace")
        self.assertEqual(win.team_id, 42)
        self.assertEqual(win.team_name, "Blue")
        self.assertEqual(win.label, "Most Points")


# ===========================================================================
# Tag Ratio — per Role, sum(tags)/max(sum(times_tagged), 1), 5-winner split
# ===========================================================================


class TestTagRatio(unittest.TestCase):
    def test_one_winner_per_present_role(self) -> None:
        rows = [
            _round(player_id=1, role="commander", tags_made=10, times_tagged=2),
            _round(player_id=2, role="heavy", tags_made=10, times_tagged=2),
            _round(player_id=3, role="scout", tags_made=10, times_tagged=2),
            _round(player_id=4, role="medic", tags_made=10, times_tagged=2),
            _round(player_id=5, role="ammo", tags_made=10, times_tagged=2),
        ]
        winners = compute_season_awards(rows, {}, [], None)["tag_ratio"]
        roles = [w.role for w in winners]
        self.assertEqual(roles, ["commander", "heavy", "scout", "medic", "ammo"])
        self.assertTrue(all(w.category == "tag_ratio" for w in winners))

    def test_absent_role_skipped(self) -> None:
        # Only commander + scout play — only those two role winners emit.
        rows = [
            _round(player_id=1, role="commander", tags_made=8, times_tagged=2),
            _round(player_id=2, role="scout", tags_made=12, times_tagged=3),
        ]
        winners = compute_season_awards(rows, {}, [], None)["tag_ratio"]
        roles = {w.role for w in winners}
        self.assertEqual(roles, {"commander", "scout"})

    def test_per_role_winner_is_best_ratio_in_that_bucket(self) -> None:
        rows = [
            # scout bucket: P1 ratio 4/2=2.0, P2 ratio 9/3=3.0 → P2 wins.
            _round(player_id=1, role="scout", tags_made=4, times_tagged=2),
            _round(player_id=2, role="scout", tags_made=9, times_tagged=3),
        ]
        winners = compute_season_awards(rows, {}, [], None)["tag_ratio"]
        scout = next(w for w in winners if w.role == "scout")
        self.assertEqual(scout.player_id, 2)
        self.assertAlmostEqual(scout.value, 3.0)

    def test_ratio_is_sum_over_sum_not_mean_of_per_round_ratios(self) -> None:
        # P1: round A 10/0, round B 0/10 → sum/sum = 10/10 = 1.0.
        # A mean-of-per-round-ratios would (mis)compute (inf + 0)/2.
        rows = [
            _round(player_id=1, role="scout", tags_made=10, times_tagged=0),
            _round(player_id=1, role="scout", tags_made=0, times_tagged=10),
        ]
        winners = compute_season_awards(rows, {}, [], None)["tag_ratio"]
        scout = next(w for w in winners if w.role == "scout")
        self.assertAlmostEqual(scout.value, 1.0)

    def test_max_denominator_clamp_avoids_div_by_zero(self) -> None:
        # times_tagged sums to 0 → clamp denominator to 1 → ratio == tags.
        rows = [
            _round(player_id=1, role="scout", tags_made=7, times_tagged=0),
        ]
        winners = compute_season_awards(rows, {}, [], None)["tag_ratio"]
        scout = next(w for w in winners if w.role == "scout")
        self.assertAlmostEqual(scout.value, 7.0)

    def test_rate_award_floor_within_role_bucket(self) -> None:
        # games floor = ceil(max_games / 2). max_games = 4 (P2). ceil(4/2)=2.
        # P1 played 1 game with a perfect ratio but is BELOW the floor →
        # excluded; P2 (4 games) is the scout winner despite a lower ratio.
        rows = [
            _round(player_id=1, role="scout", tags_made=50, times_tagged=1),
            _round(player_id=2, role="scout", tags_made=4, times_tagged=2),
            _round(player_id=2, role="scout", tags_made=4, times_tagged=2),
            _round(player_id=2, role="scout", tags_made=4, times_tagged=2),
            _round(player_id=2, role="scout", tags_made=4, times_tagged=2),
        ]
        winners = compute_season_awards(rows, {}, [], None)["tag_ratio"]
        scout = next(w for w in winners if w.role == "scout")
        self.assertEqual(scout.player_id, 2)


# ===========================================================================
# Most Resupplies — summed resupplies_given, Ammo CAN win
# ===========================================================================


class TestMostResupplies(unittest.TestCase):
    def test_winner_is_highest_summed_resupplies(self) -> None:
        rows = [
            _round(player_id=1, role="medic", resupplies_given=5),
            _round(player_id=1, role="medic", resupplies_given=5),  # 10
            _round(player_id=2, role="ammo", resupplies_given=8),  # 8
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "most_resupplies")
        self.assertEqual(win.player_id, 1)
        self.assertEqual(win.value, 10)

    def test_ammo_can_win(self) -> None:
        rows = [
            _round(player_id=1, role="medic", resupplies_given=3),
            _round(player_id=2, role="ammo", resupplies_given=20),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "most_resupplies")
        self.assertEqual(win.player_id, 2)
        self.assertEqual(win.role, None)  # not a tag_ratio per-role winner

    def test_no_floor_on_count_award(self) -> None:
        # 1-game high-resupply player beats a 4-game lower-total player.
        rows = [
            _round(player_id=1, role="medic", resupplies_given=2),
            _round(player_id=1, role="medic", resupplies_given=2),
            _round(player_id=1, role="medic", resupplies_given=2),
            _round(player_id=1, role="medic", resupplies_given=2),
            _round(player_id=2, role="ammo", resupplies_given=30),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "most_resupplies")
        self.assertEqual(win.player_id, 2)


# ===========================================================================
# Longest Survival — mean survival_seconds per Round
# ===========================================================================


class TestLongestSurvival(unittest.TestCase):
    def test_winner_is_highest_mean_survival(self) -> None:
        # P1 mean = (900 + 900)/2 = 900; P2 mean = 800/1 = 800.
        rows = [
            _round(player_id=1, survival_seconds=900.0),
            _round(player_id=1, survival_seconds=900.0),
            _round(player_id=2, survival_seconds=800.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "longest_survival")
        self.assertEqual(win.player_id, 1)
        self.assertAlmostEqual(win.value, 900.0)

    def test_mean_not_sum(self) -> None:
        # P1 mean = (100 + 900)/2 = 500. P2 mean = 600/1 = 600 (with floor met).
        # P1 has 2 games (max), floor ceil(2/2)=1 admits both. P2 wins on mean.
        rows = [
            _round(player_id=1, survival_seconds=100.0),
            _round(player_id=1, survival_seconds=900.0),
            _round(player_id=2, survival_seconds=600.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "longest_survival")
        self.assertEqual(win.player_id, 2)

    def test_rate_floor_excludes_below_threshold(self) -> None:
        # max_games = 4 → floor ceil(4/2)=2. P1 (1 game, perfect) excluded;
        # P2 (4 games) wins despite lower mean.
        rows = [
            _round(player_id=1, survival_seconds=900.0),
            _round(player_id=2, survival_seconds=400.0),
            _round(player_id=2, survival_seconds=400.0),
            _round(player_id=2, survival_seconds=400.0),
            _round(player_id=2, survival_seconds=400.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "longest_survival")
        self.assertEqual(win.player_id, 2)


# ===========================================================================
# Most Efficient Nuke — from nuke_elims_by_player, Commander-only, absent map
# ===========================================================================


class TestMostEfficientNuke(unittest.TestCase):
    def test_winner_is_highest_nuke_elim_count(self) -> None:
        rows = [
            _round(player_id=1, role="commander", player_name="Cmd1", team_id=10),
            _round(player_id=2, role="commander", player_name="Cmd2", team_id=20),
        ]
        nukes = {1: 5, 2: 9}
        win = _winner(
            compute_season_awards(rows, nukes, [], None), "most_efficient_nuke"
        )
        self.assertEqual(win.player_id, 2)
        self.assertEqual(win.value, 9)

    def test_empty_nuke_map_absent(self) -> None:
        rows = [_round(player_id=1, role="commander")]
        result = compute_season_awards(rows, {}, [], None)
        self.assertIsNone(result["most_efficient_nuke"])

    def test_value_from_map_count(self) -> None:
        rows = [_round(player_id=1, role="commander", player_name="C", team_id=3)]
        win = _winner(
            compute_season_awards(rows, {1: 4}, [], None), "most_efficient_nuke"
        )
        self.assertEqual(win.value, 4)
        self.assertEqual(win.category, "most_efficient_nuke")


# ===========================================================================
# Best Accuracy — mean accuracy per Round
# ===========================================================================


class TestBestAccuracy(unittest.TestCase):
    def test_winner_is_highest_mean_accuracy(self) -> None:
        rows = [
            _round(player_id=1, accuracy=80.0),
            _round(player_id=1, accuracy=80.0),
            _round(player_id=2, accuracy=70.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "best_accuracy")
        self.assertEqual(win.player_id, 1)
        self.assertAlmostEqual(win.value, 80.0)

    def test_mean_not_sum(self) -> None:
        # Both have 2 games (floor ceil(2/2)=1 admits both). P2 wins on mean.
        rows = [
            _round(player_id=1, accuracy=90.0),
            _round(player_id=1, accuracy=10.0),  # mean 50
            _round(player_id=2, accuracy=60.0),
            _round(player_id=2, accuracy=60.0),  # mean 60
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "best_accuracy")
        self.assertEqual(win.player_id, 2)

    def test_rate_floor_excludes_below_threshold(self) -> None:
        # max_games = 4 → floor 2. P1 (1 game, perfect) excluded; P2 wins.
        rows = [
            _round(player_id=1, accuracy=100.0),
            _round(player_id=2, accuracy=50.0),
            _round(player_id=2, accuracy=50.0),
            _round(player_id=2, accuracy=50.0),
            _round(player_id=2, accuracy=50.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "best_accuracy")
        self.assertEqual(win.player_id, 2)


# ===========================================================================
# Tiebreak — value desc → player_id asc
# ===========================================================================


class TestTiebreak(unittest.TestCase):
    def test_equal_value_lower_player_id_wins(self) -> None:
        # Two players with identical summed points → lower player_id wins.
        rows = [
            _round(player_id=5, points_scored=500.0),
            _round(player_id=2, points_scored=500.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "most_points")
        self.assertEqual(win.player_id, 2)

    def test_tiebreak_within_tag_ratio_role_bucket(self) -> None:
        # Two scouts, identical ratio → lower player_id wins the scout slot.
        rows = [
            _round(player_id=9, role="scout", tags_made=6, times_tagged=2),
            _round(player_id=3, role="scout", tags_made=6, times_tagged=2),
        ]
        winners = compute_season_awards(rows, {}, [], None)["tag_ratio"]
        scout = next(w for w in winners if w.role == "scout")
        self.assertEqual(scout.player_id, 3)

    def test_tiebreak_on_nuke_count(self) -> None:
        rows = [
            _round(player_id=8, role="commander"),
            _round(player_id=4, role="commander"),
        ]
        win = _winner(
            compute_season_awards(rows, {8: 3, 4: 3}, [], None),
            "most_efficient_nuke",
        )
        self.assertEqual(win.player_id, 4)


# ===========================================================================
# Season MVP — summed get_mvp (a many-games player beats a 1-game streak)
# ===========================================================================


class TestSeasonMvp(unittest.TestCase):
    def test_mvp_is_summed_not_mean(self) -> None:
        # P1: 3 games of mvp 5 each = summed 15.
        # P2: 1 game of mvp 12 = summed 12.
        # Summed → P1 wins (a mean would give P2 12 vs P1 5).
        rows = [
            _round(player_id=1, mvp=5.0),
            _round(player_id=1, mvp=5.0),
            _round(player_id=1, mvp=5.0),
            _round(player_id=2, mvp=12.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "season_mvp")
        self.assertEqual(win.player_id, 1)
        self.assertAlmostEqual(win.value, 15.0)
        self.assertEqual(win.category, "season_mvp")
        self.assertEqual(win.label, "Season MVP")

    def test_no_games_floor_on_headline(self) -> None:
        # P2 plays 1 game but its summed mvp is highest → wins (no floor).
        rows = [
            _round(player_id=1, mvp=2.0),
            _round(player_id=1, mvp=2.0),
            _round(player_id=1, mvp=2.0),
            _round(player_id=1, mvp=2.0),
            _round(player_id=2, mvp=100.0),
        ]
        win = _winner(compute_season_awards(rows, {}, [], None), "season_mvp")
        self.assertEqual(win.player_id, 2)

    def test_season_mvp_absent_on_empty(self) -> None:
        self.assertIsNone(compute_season_awards([], {}, [], None)["season_mvp"])


# ===========================================================================
# Finals MVP — summed get_mvp over finals_rounds, champion-team-filtered
# ===========================================================================


class TestFinalsMvp(unittest.TestCase):
    def test_finals_mvp_is_champion_team_summed_leader(self) -> None:
        finals = [
            _round(player_id=1, team_id=100, mvp=8.0),
            _round(player_id=1, team_id=100, mvp=8.0),  # champion team, summed 16
            _round(player_id=2, team_id=200, mvp=50.0),  # other team — excluded
        ]
        win = _winner(compute_season_awards([], {}, finals, 100), "finals_mvp")
        self.assertEqual(win.player_id, 1)
        self.assertAlmostEqual(win.value, 16.0)
        self.assertEqual(win.category, "finals_mvp")
        self.assertEqual(win.label, "Finals MVP")

    def test_finals_mvp_filters_to_champion_team(self) -> None:
        # A non-champion-team player with a higher summed mvp must NOT win.
        finals = [
            _round(player_id=1, team_id=100, mvp=5.0),
            _round(player_id=2, team_id=200, mvp=99.0),
        ]
        win = _winner(compute_season_awards([], {}, finals, 100), "finals_mvp")
        self.assertEqual(win.player_id, 1)

    def test_finals_mvp_absent_when_finals_rounds_empty(self) -> None:
        result = compute_season_awards([], {}, [], 100)
        self.assertIsNone(result["finals_mvp"])

    def test_finals_mvp_absent_when_champion_team_none(self) -> None:
        finals = [_round(player_id=1, team_id=100, mvp=8.0)]
        result = compute_season_awards([], {}, finals, None)
        self.assertIsNone(result["finals_mvp"])

    def test_finals_mvp_summed_over_finals_rounds(self) -> None:
        finals = [
            _round(player_id=1, team_id=100, mvp=3.0),
            _round(player_id=1, team_id=100, mvp=4.0),  # summed 7
            _round(player_id=2, team_id=100, mvp=6.0),  # summed 6
        ]
        win = _winner(compute_season_awards([], {}, finals, 100), "finals_mvp")
        self.assertEqual(win.player_id, 1)
        self.assertAlmostEqual(win.value, 7.0)


# ===========================================================================
# AwardWinner dataclass shape
# ===========================================================================


class TestAwardWinnerDataclass(unittest.TestCase):
    def test_role_default_is_none(self) -> None:
        win = AwardWinner(
            category="most_points",
            label="Most Points",
            player_id=1,
            player_name="P",
            team_id=1,
            team_name="T",
            value=10.0,
        )
        self.assertIsNone(win.role)

    def test_frozen(self) -> None:
        win = AwardWinner(
            category="most_points",
            label="Most Points",
            player_id=1,
            player_name="P",
            team_id=1,
            team_name="T",
            value=10.0,
        )
        with self.assertRaises(Exception):
            win.value = 99.0  # type: ignore[misc]


# ===========================================================================
# No Django imports leaked (purity)
# ===========================================================================


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """Mirrors the HX-03 / standings.py / stat_feats.py precedent.

    Importing ``matches.season_awards`` in a fresh subprocess must not pull
    in ``django.*`` or ``matches.models`` — the pure module's import
    allowlist is ``dataclasses`` / ``typing`` / ``math`` / ``collections``.
    """

    def test_clean_import_in_subprocess(self) -> None:
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
            import matches.season_awards  # noqa: F401

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


if __name__ == "__main__":
    unittest.main()
