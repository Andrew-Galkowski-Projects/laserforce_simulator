"""LG-03 — pure-unit tests for ``matches.season_awards``.

Covers the two pure entry points of the season-awards module — built from
hand-crafted seam-dict lists, NO DB, NO mocks:

* ``compute_season_awards(player_rounds, *, min_games) -> AwardSet`` — the 7
  metrics, the per-role K/D (5 ``kd_by_role`` keys incl. ``None`` for unplayed
  roles), the rate-award qualifier (gates ONLY ``season_mvp`` /
  ``best_accuracy`` / ``most_efficient_nuke``), the tiebreak ladder, and the
  empty-input ⇒ all-``None`` edge.
* ``pick_finals_mvp(final_round_dicts) -> AwardWinner | None`` — best mean MVP
  + empty-input ``None``.

Plus ``TestNoDjangoImportsLeaked`` — the subprocess fresh-import + ``sys.modules``
walk that defends the frozen ``dataclasses`` / ``typing`` / ``collections``-only
import allowlist (mirrors the ``season_player_stats`` /
``league_leaders_logic`` precedent).

Assertion discipline (LG-03 §5.4): assert on award WINNER identity / values /
the 5-key ``kd_by_role`` shape — NEVER on simulated point totals (every fixture
here is a hand-built dict, so there are no sim totals at all).

Written test-first against the LG-03 seam contract
(``.claude/worktrees/lg-03-season-awards-seam-contract.md``); these FAIL until
the Code agent lands ``matches/season_awards.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

from matches.season_awards import (
    AwardSet,
    AwardWinner,
    compute_season_awards,
    pick_finals_mvp,
)

# The 5 role strings stored on ``PlayerRoundState.role`` (the locked
# ``kd_by_role`` key set).
ROLES = ("commander", "heavy", "scout", "medic", "ammo")


def _row(
    player_id: int,
    *,
    name: str | None = None,
    role: str = "scout",
    team_id: int = 1,
    team_name: str = "T",
    points_scored: float = 0,
    tags_made: float = 0,
    times_tagged: float = 0,
    accuracy: float = 0.0,
    mvp: float = 0.0,
    resupplies_given: float = 0,
    specials_used: float = 0,
    own_specials_cancelled: float = 0,
) -> dict:
    """Build one §1.2 seam dict (one per-round appearance) by hand."""
    return {
        "player_id": player_id,
        "player_name": name or f"P{player_id}",
        "role": role,
        "team_id": team_id,
        "team_name": team_name,
        "points_scored": points_scored,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "accuracy": accuracy,
        "mvp": mvp,
        "resupplies_given": resupplies_given,
        "specials_used": specials_used,
        "own_specials_cancelled": own_specials_cancelled,
    }


# ===========================================================================
# Dataclass shape
# ===========================================================================


class TestAwardDataclasses(unittest.TestCase):
    def test_award_winner_field_order_and_frozen(self) -> None:
        w = AwardWinner(
            player_id=7,
            player_name="Ace",
            role="scout",
            team_id=3,
            team_name="Reds",
            value=12.5,
        )
        self.assertEqual(w.player_id, 7)
        self.assertEqual(w.player_name, "Ace")
        self.assertEqual(w.role, "scout")
        self.assertEqual(w.team_id, 3)
        self.assertEqual(w.team_name, "Reds")
        self.assertAlmostEqual(w.value, 12.5)
        with self.assertRaises(Exception):
            w.value = 0.0  # frozen=True

    def test_award_set_has_seven_slots(self) -> None:
        out = compute_season_awards([], min_games=0)
        self.assertIsInstance(out, AwardSet)
        for slot in (
            "most_points",
            "best_accuracy",
            "kd_by_role",
            "best_medic",
            "most_efficient_nuke",
            "season_mvp",
            "finals_mvp",
        ):
            self.assertTrue(hasattr(out, slot), slot)


# ===========================================================================
# Empty input ⇒ every slot None (incl. all 5 kd_by_role entries)
# ===========================================================================


class TestComputeEmpty(unittest.TestCase):
    def test_empty_input_all_slots_none(self) -> None:
        out = compute_season_awards([], min_games=0)
        self.assertIsNone(out.most_points)
        self.assertIsNone(out.best_accuracy)
        self.assertIsNone(out.best_medic)
        self.assertIsNone(out.most_efficient_nuke)
        self.assertIsNone(out.season_mvp)
        self.assertIsNone(out.finals_mvp)

    def test_empty_input_kd_by_role_has_five_none_entries(self) -> None:
        out = compute_season_awards([], min_games=0)
        self.assertEqual(set(out.kd_by_role), set(ROLES))
        self.assertEqual(len(out.kd_by_role), 5)
        for role in ROLES:
            self.assertIsNone(out.kd_by_role[role], role)


# ===========================================================================
# Most Points — SUM(points_scored), any role, NOT gated
# ===========================================================================


class TestMostPoints(unittest.TestCase):
    def test_winner_is_sum_of_points(self) -> None:
        rows = [
            _row(1, points_scored=100),
            _row(1, points_scored=250),  # P1 total = 350
            _row(2, points_scored=300),  # P2 total = 300
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(out.most_points.player_id, 1)
        self.assertAlmostEqual(out.most_points.value, 350.0)

    def test_total_award_not_gated_low_games_player_can_win(self) -> None:
        # P1 plays once with a huge total; min_games=5 must NOT gate it
        # (most_points is a total award).
        rows = [_row(1, points_scored=9000)]
        rows += [_row(2, points_scored=10) for _ in range(5)]  # P2: 5 games, low
        out = compute_season_awards(rows, min_games=5)
        self.assertEqual(out.most_points.player_id, 1)


# ===========================================================================
# Best Accuracy — MEAN(accuracy), any role, gated (rate)
# ===========================================================================


class TestBestAccuracy(unittest.TestCase):
    def test_winner_is_mean_accuracy(self) -> None:
        rows = [
            _row(1, accuracy=80.0),
            _row(1, accuracy=60.0),  # mean 70
            _row(2, accuracy=90.0),  # mean 90
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(out.best_accuracy.player_id, 2)
        self.assertAlmostEqual(out.best_accuracy.value, 90.0)

    def test_rate_award_gated_low_games_player_excluded(self) -> None:
        # P1: 1 game, perfect accuracy. P2: 3 games, lower accuracy.
        # min_games=2 gates P1 out of best_accuracy (a rate award).
        rows = [_row(1, accuracy=100.0)]
        rows += [_row(2, accuracy=50.0) for _ in range(3)]
        out = compute_season_awards(rows, min_games=2)
        self.assertEqual(out.best_accuracy.player_id, 2)


# ===========================================================================
# K/D by role — SUM(tags)/max(SUM(tagged),1), one winner per role, 5 keys
# ===========================================================================


class TestKdByRole(unittest.TestCase):
    def test_one_winner_per_role_all_five_keys_present(self) -> None:
        rows = [
            _row(1, role="commander", tags_made=10, times_tagged=2),
            _row(2, role="commander", tags_made=4, times_tagged=2),
            _row(3, role="heavy", tags_made=8, times_tagged=4),
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(set(out.kd_by_role), set(ROLES))
        # commander winner is P1 (10/2 = 5.0 vs P2 4/2 = 2.0).
        self.assertEqual(out.kd_by_role["commander"].player_id, 1)
        self.assertAlmostEqual(out.kd_by_role["commander"].value, 5.0)
        # heavy winner is P3.
        self.assertEqual(out.kd_by_role["heavy"].player_id, 3)
        # roles nobody played ⇒ None.
        self.assertIsNone(out.kd_by_role["scout"])
        self.assertIsNone(out.kd_by_role["medic"])
        self.assertIsNone(out.kd_by_role["ammo"])

    def test_kd_uses_sum_over_sum_with_max_one_denominator(self) -> None:
        # P1: tags 3/tagged 0, then tags 0/tagged 0 ⇒ 3 / max(0,1) = 3.0.
        rows = [
            _row(1, role="scout", tags_made=3, times_tagged=0),
            _row(1, role="scout", tags_made=0, times_tagged=0),
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertAlmostEqual(out.kd_by_role["scout"].value, 3.0)

    def test_kd_is_sum_over_sum_not_mean_of_ratios(self) -> None:
        # tags 1/tagged 0 then tags 0/tagged 4 ⇒ sum/sum = 1 / max(4,1) = 0.25.
        rows = [
            _row(1, role="ammo", tags_made=1, times_tagged=0),
            _row(1, role="ammo", tags_made=0, times_tagged=4),
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertAlmostEqual(out.kd_by_role["ammo"].value, 0.25)

    def test_kd_not_gated_low_games_player_can_win(self) -> None:
        # P1: 1 game, dominant K/D. P2: 4 games, weak. min_games=3 must NOT
        # gate K/D (a total/count award).
        rows = [_row(1, role="medic", tags_made=20, times_tagged=1)]
        rows += [_row(2, role="medic", tags_made=1, times_tagged=10) for _ in range(4)]
        out = compute_season_awards(rows, min_games=3)
        self.assertEqual(out.kd_by_role["medic"].player_id, 1)


# ===========================================================================
# Best Medic — SUM(resupplies_given), medic-only, NOT gated
# ===========================================================================


class TestBestMedic(unittest.TestCase):
    def test_winner_is_medic_with_most_resupplies(self) -> None:
        rows = [
            _row(1, role="medic", resupplies_given=5),
            _row(1, role="medic", resupplies_given=7),  # total 12
            _row(2, role="medic", resupplies_given=10),  # total 10
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(out.best_medic.player_id, 1)
        self.assertAlmostEqual(out.best_medic.value, 12.0)

    def test_non_medic_resupplies_ignored(self) -> None:
        # A non-medic with huge resupplies must NOT win best_medic.
        rows = [
            _row(1, role="ammo", resupplies_given=99),
            _row(2, role="medic", resupplies_given=3),
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(out.best_medic.player_id, 2)

    def test_no_medic_means_none(self) -> None:
        rows = [_row(1, role="scout", resupplies_given=50)]
        out = compute_season_awards(rows, min_games=0)
        self.assertIsNone(out.best_medic)

    def test_best_medic_not_gated(self) -> None:
        # P1 medic plays once with a big total; min_games must not gate it.
        rows = [_row(1, role="medic", resupplies_given=40)]
        rows += [_row(2, role="medic", resupplies_given=1) for _ in range(5)]
        out = compute_season_awards(rows, min_games=5)
        self.assertEqual(out.best_medic.player_id, 1)


# ===========================================================================
# Most Efficient Nuke — (SUM(used) - SUM(cancelled)) / max(SUM(used), 1),
# commander-only, gated (rate)
# ===========================================================================


class TestMostEfficientNuke(unittest.TestCase):
    def test_winner_is_commander_with_best_efficiency(self) -> None:
        # P1: used 10, cancelled 2 ⇒ (10-2)/10 = 0.8.
        # P2: used 5, cancelled 0 ⇒ 5/5 = 1.0.
        rows = [
            _row(1, role="commander", specials_used=10, own_specials_cancelled=2),
            _row(2, role="commander", specials_used=5, own_specials_cancelled=0),
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(out.most_efficient_nuke.player_id, 2)
        self.assertAlmostEqual(out.most_efficient_nuke.value, 1.0)

    def test_sums_across_rounds_before_ratio(self) -> None:
        # P1 over 2 rounds: used 4+6=10, cancelled 1+1=2 ⇒ (10-2)/10 = 0.8.
        rows = [
            _row(1, role="commander", specials_used=4, own_specials_cancelled=1),
            _row(1, role="commander", specials_used=6, own_specials_cancelled=1),
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertAlmostEqual(out.most_efficient_nuke.value, 0.8)

    def test_zero_used_max_one_denominator_no_div_by_zero(self) -> None:
        rows = [
            _row(1, role="commander", specials_used=0, own_specials_cancelled=0),
        ]
        out = compute_season_awards(rows, min_games=0)
        # (0 - 0) / max(0, 1) == 0.0, no crash.
        self.assertAlmostEqual(out.most_efficient_nuke.value, 0.0)

    def test_non_commander_ignored(self) -> None:
        rows = [
            _row(1, role="heavy", specials_used=10, own_specials_cancelled=0),
            _row(2, role="commander", specials_used=2, own_specials_cancelled=0),
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(out.most_efficient_nuke.player_id, 2)

    def test_no_commander_means_none(self) -> None:
        rows = [_row(1, role="scout", specials_used=5)]
        out = compute_season_awards(rows, min_games=0)
        self.assertIsNone(out.most_efficient_nuke)

    def test_rate_award_gated(self) -> None:
        # P1 commander: 1 game, perfect efficiency. P2 commander: 3 games,
        # lower. min_games=2 gates P1 out (a rate award).
        rows = [_row(1, role="commander", specials_used=3, own_specials_cancelled=0)]
        rows += [
            _row(2, role="commander", specials_used=4, own_specials_cancelled=2)
            for _ in range(3)
        ]
        out = compute_season_awards(rows, min_games=2)
        self.assertEqual(out.most_efficient_nuke.player_id, 2)


# ===========================================================================
# Season MVP — MEAN(mvp), any role, gated (rate)
# ===========================================================================


class TestSeasonMvp(unittest.TestCase):
    def test_winner_is_mean_mvp(self) -> None:
        rows = [
            _row(1, mvp=10.0),
            _row(1, mvp=20.0),  # mean 15
            _row(2, mvp=18.0),  # mean 18
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(out.season_mvp.player_id, 2)
        self.assertAlmostEqual(out.season_mvp.value, 18.0)

    def test_rate_award_gated(self) -> None:
        rows = [_row(1, mvp=100.0)]  # 1 game, huge mean
        rows += [_row(2, mvp=30.0) for _ in range(3)]  # 3 games
        out = compute_season_awards(rows, min_games=2)
        self.assertEqual(out.season_mvp.player_id, 2)


# ===========================================================================
# Qualifier — gates ONLY the three rate awards
# ===========================================================================


class TestQualifierBoundary(unittest.TestCase):
    """A single low-games COMMANDER who would win every metric: with
    ``min_games`` above their game count, they still win the total awards
    (most_points / best_medic / kd_by_role) but NONE of the three rate awards.

    P1 is a single-role commander (consistent role across both rows) so the
    role-scoped awards bucket them as a commander; P3 is a separate single-row
    medic so ``best_medic`` (ungated) still has a low-games winner to assert.
    """

    def setUp(self) -> None:
        # P1: 2 commander games, dominant on every metric.
        self.rows = [
            _row(
                1,
                role="commander",
                points_scored=9999,
                tags_made=50,
                times_tagged=1,
                accuracy=100.0,
                mvp=100.0,
                specials_used=5,
                own_specials_cancelled=0,
            ),
            _row(
                1,
                role="commander",
                points_scored=1,
                tags_made=1,
                times_tagged=1,
                accuracy=100.0,
                mvp=100.0,
                specials_used=5,
                own_specials_cancelled=0,
            ),
        ]
        # P3: 1 medic game with a huge resupply total (low-games total award).
        self.rows.append(_row(3, role="medic", resupplies_given=99))
        self.rows.append(_row(4, role="medic", resupplies_given=2))
        # P2: grinder, many commander games, modest numbers — wins the gated
        # rate awards once P1 is excluded.
        for _ in range(6):
            self.rows.append(
                _row(
                    2,
                    role="commander",
                    points_scored=10,
                    tags_made=2,
                    times_tagged=5,
                    accuracy=40.0,
                    mvp=20.0,
                    specials_used=4,
                    own_specials_cancelled=1,
                )
            )

    def test_low_games_player_wins_total_awards(self) -> None:
        # P1 has 2 games; min_games=4 gates the rate awards only.
        out = compute_season_awards(self.rows, min_games=4)
        self.assertEqual(out.most_points.player_id, 1)
        # best_medic (ungated total) — P3 (1 game, 99 resupplies) wins.
        self.assertEqual(out.best_medic.player_id, 3)
        # K/D-by-role (commander) — ungated, P1 wins (50/1=50 over 2 games).
        self.assertEqual(out.kd_by_role["commander"].player_id, 1)

    def test_low_games_player_loses_rate_awards(self) -> None:
        out = compute_season_awards(self.rows, min_games=4)
        # P1 (2 games) is gated below min_games=4 ⇒ P2 wins each rate award.
        self.assertEqual(out.season_mvp.player_id, 2)
        self.assertEqual(out.best_accuracy.player_id, 2)
        self.assertEqual(out.most_efficient_nuke.player_id, 2)

    def test_zero_min_games_gates_nothing(self) -> None:
        out = compute_season_awards(self.rows, min_games=0)
        # With no gate P1 dominates every rate award too.
        self.assertEqual(out.season_mvp.player_id, 1)
        self.assertEqual(out.best_accuracy.player_id, 1)
        self.assertEqual(out.most_efficient_nuke.player_id, 1)


# ===========================================================================
# Tiebreak ladder — metric → games_played desc → player_id asc
# ===========================================================================


class TestTiebreakLadder(unittest.TestCase):
    def test_tie_breaks_games_desc_then_player_id_asc(self) -> None:
        # most_points: all three players have total 100, differing games + ids.
        rows = [
            _row(3, points_scored=100),  # P3: 1 game
            _row(2, points_scored=50),  # P2: 2 games
            _row(2, points_scored=50),
            _row(1, points_scored=100),  # P1: 1 game
        ]
        out = compute_season_awards(rows, min_games=0)
        # Value tie (100) → games desc (P2 has 2) → so P2 wins.
        self.assertEqual(out.most_points.player_id, 2)

    def test_tie_with_equal_games_breaks_on_lower_player_id(self) -> None:
        rows = [
            _row(5, points_scored=100),
            _row(2, points_scored=100),
        ]
        out = compute_season_awards(rows, min_games=0)
        # Equal value, equal games (1) → lower player_id wins.
        self.assertEqual(out.most_points.player_id, 2)


# ===========================================================================
# Identity / last-row-wins
# ===========================================================================


class TestWinnerIdentity(unittest.TestCase):
    def test_winner_carries_identity_fields(self) -> None:
        rows = [
            _row(
                9,
                name="Nova",
                role="scout",
                team_id=4,
                team_name="Blues",
                points_scored=500,
            )
        ]
        out = compute_season_awards(rows, min_games=0)
        w = out.most_points
        self.assertEqual(w.player_id, 9)
        self.assertEqual(w.player_name, "Nova")
        self.assertEqual(w.role, "scout")
        self.assertEqual(w.team_id, 4)
        self.assertEqual(w.team_name, "Blues")

    def test_last_row_supplies_displayed_identity(self) -> None:
        rows = [
            _row(1, name="Old", team_name="OldT", role="scout", points_scored=10),
            _row(1, name="New", team_name="NewT", role="medic", points_scored=10),
        ]
        out = compute_season_awards(rows, min_games=0)
        self.assertEqual(out.most_points.player_name, "New")
        self.assertEqual(out.most_points.team_name, "NewT")
        self.assertEqual(out.most_points.role, "medic")


# ===========================================================================
# compute_season_awards always returns finals_mvp=None
# ===========================================================================


class TestComputeFinalsMvpNone(unittest.TestCase):
    def test_compute_does_not_set_finals_mvp(self) -> None:
        rows = [_row(1, points_scored=100, mvp=50.0)]
        out = compute_season_awards(rows, min_games=0)
        self.assertIsNone(out.finals_mvp)


# ===========================================================================
# pick_finals_mvp — best mean MVP + empty input None
# ===========================================================================


class TestPickFinalsMvp(unittest.TestCase):
    def test_empty_input_returns_none(self) -> None:
        self.assertIsNone(pick_finals_mvp([]))

    def test_picks_best_mean_mvp(self) -> None:
        rows = [
            _row(1, mvp=10.0),
            _row(1, mvp=30.0),  # mean 20
            _row(2, mvp=25.0),  # mean 25
        ]
        winner = pick_finals_mvp(rows)
        self.assertIsNotNone(winner)
        self.assertEqual(winner.player_id, 2)
        self.assertAlmostEqual(winner.value, 25.0)

    def test_tiebreak_games_desc_then_player_id_asc(self) -> None:
        rows = [
            _row(3, mvp=10.0),  # 1 game, mean 10
            _row(2, mvp=10.0),  # 2 games, mean 10
            _row(2, mvp=10.0),
            _row(1, mvp=10.0),  # 1 game, mean 10
        ]
        winner = pick_finals_mvp(rows)
        # Equal mean → games desc (P2 has 2) wins.
        self.assertEqual(winner.player_id, 2)

    def test_winner_is_award_winner_with_identity(self) -> None:
        rows = [
            _row(7, name="Finalist", role="heavy", team_id=2, team_name="Zed", mvp=40.0)
        ]
        winner = pick_finals_mvp(rows)
        self.assertIsInstance(winner, AwardWinner)
        self.assertEqual(winner.player_id, 7)
        self.assertEqual(winner.player_name, "Finalist")
        self.assertEqual(winner.role, "heavy")
        self.assertEqual(winner.team_id, 2)
        self.assertEqual(winner.team_name, "Zed")


# ===========================================================================
# Purity — no Django imports leaked into matches.season_awards
# ===========================================================================


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """``matches.season_awards`` must not transitively import Django.

    Mirrors the ``season_player_stats`` / ``league_leaders_logic`` precedent:
    spawn a fresh subprocess, ``import matches.season_awards``, then walk
    ``sys.modules`` and assert no entry matches the ``django`` prefix.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import pathlib
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
            leaked = sorted(
                m for m in sys.modules
                if m == "django" or m.startswith("django.")
            )
            if leaked:
                print("LEAK:" + ",".join(leaked))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Django import leaked into matches.season_awards.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
