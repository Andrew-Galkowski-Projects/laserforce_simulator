"""HX-01 — Tests for per-player career stats.

This file holds both the pure-unit tests for ``teams/career_stats.py`` (no DB,
no Django imports) and the DB/view tests for ``teams.views.player_career_stats``
(Django ``TestCase`` + test client).

The seam contract is locked at ``.claude/worktrees/hx-01-seam-contract.md``.
Until the Code agent lands ``teams/career_stats.py`` and the view/url/template,
some of these tests will fail at import time or at assertion — that is expected
TDD precedent (mirrors ``test_res04_cell_occupancy.py`` / ``test_rv03_pdf_report.py``).

The round-dict schema crossing the pure-module seam is the §2b 10-key shape:

    {
        "role":              str,
        "points_scored":     int,
        "tags_made":         int,
        "times_tagged":      int,
        "shots_missed":      int,
        "final_special":     int,
        "specials_used":     int,
        "was_eliminated_at": int,
        "date_played":       datetime | str,
        "game_round_id":     int,
    }
"""

from __future__ import annotations

import unittest

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, PlayerRoundState
from matches.sim_helpers.role_constants import SPECIAL_COST
from teams.career_stats import (
    points_trend,
    rolling_mean,
    summarize,
    summarize_by_role,
)
from teams.models import Player, Team

# ---------------------------------------------------------------------------
# Pure-unit fixture helper
# ---------------------------------------------------------------------------


def _round_dict(
    *,
    role: str = "scout",
    points_scored: int = 0,
    tags_made: int = 0,
    times_tagged: int = 0,
    shots_missed: int = 0,
    final_special: int = 0,
    specials_used: int = 0,
    was_eliminated_at: int = 1801,
    date_played: object = "2026-05-22T12:00:00",
    game_round_id: int = 1,
) -> dict:
    """Build one §2b round-dict with every key populated.

    Keyword-only so each test case can be read at a glance — the keys it
    overrides are the ones that matter for that case.
    """
    return {
        "role": role,
        "points_scored": points_scored,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "shots_missed": shots_missed,
        "final_special": final_special,
        "specials_used": specials_used,
        "was_eliminated_at": was_eliminated_at,
        "date_played": date_played,
        "game_round_id": game_round_id,
    }


# ---------------------------------------------------------------------------
# §7.1 Pure-unit cases (no DB, no Django imports in the assertion path)
# ---------------------------------------------------------------------------


class TestCareerStatsPure(unittest.TestCase):
    """Pure-unit coverage of ``teams.career_stats`` (§7.1)."""

    # ----- 1: summarize empty input ----------------------------------------

    def test_summarize_empty_input(self) -> None:
        """Empty input → games=0 and every numeric key == 0.0 (no div-by-zero)."""
        result = summarize([])
        self.assertEqual(
            result,
            {
                "games": 0,
                "avg_points": 0.0,
                "tag_ratio": 0.0,
                "avg_survival_ticks": 0.0,
                "avg_accuracy_pct": 0.0,
                "avg_sp_earned": 0.0,
            },
        )

    # ----- 2: points_trend empty input -------------------------------------

    def test_points_trend_empty_input(self) -> None:
        """Empty input → empty list."""
        self.assertEqual(points_trend([]), [])

    # ----- 3: summarize_by_role empty input --------------------------------

    def test_summarize_by_role_empty_input(self) -> None:
        """Empty input → empty list."""
        self.assertEqual(summarize_by_role([]), [])

    # ----- 4: single-round happy path --------------------------------------

    def test_summarize_single_round_happy_path(self) -> None:
        """One round; every key in the result matches the locked formula.

        Scout: ``SPECIAL_COST["scout"] = 10``.
        Round: 1000 pts, 8 tags / 4 tagged, 2 shots missed, final_special=5,
        specials_used=2, eliminated at tick 1500.

        Expected:
          games               = 1
          avg_points          = 1000.0
          tag_ratio           = 8 / 4 = 2.0
          avg_survival_ticks  = 1500.0   (min(1500, 1800) = 1500)
          avg_accuracy_pct    = 8 / (8 + 2) * 100 = 80.0
          avg_sp_earned       = 5 + 10 * 2 = 25.0
        """
        rounds = [
            _round_dict(
                role="scout",
                points_scored=1000,
                tags_made=8,
                times_tagged=4,
                shots_missed=2,
                final_special=5,
                specials_used=2,
                was_eliminated_at=1500,
            )
        ]
        result = summarize(rounds)
        self.assertEqual(result["games"], 1)
        self.assertAlmostEqual(result["avg_points"], 1000.0)
        self.assertAlmostEqual(result["tag_ratio"], 2.0)
        self.assertAlmostEqual(result["avg_survival_ticks"], 1500.0)
        self.assertAlmostEqual(result["avg_accuracy_pct"], 80.0)
        self.assertAlmostEqual(result["avg_sp_earned"], 25.0)

    # ----- 5: tag_ratio formula direction (sum/sum, NOT mean of ratios) ----

    def test_tag_ratio_is_sum_over_sum_not_mean_of_ratios(self) -> None:
        """Pins the §2c tag_ratio formula direction.

        Round A: tags_made=10, times_tagged=1 → per-round ratio 10/1 = 10.0
        Round B: tags_made=0,  times_tagged=100 → per-round ratio 0/100 = 0.0

        Correct sum/sum:        10 / 101 ≈ 0.099
        Wrong mean-of-ratios:   (10 + 0) / 2 = 5.0

        Assert ≈ 0.099, NOT 5.0.
        """
        rounds = [
            _round_dict(tags_made=10, times_tagged=1),
            _round_dict(tags_made=0, times_tagged=100),
        ]
        result = summarize(rounds)
        # The contract: sum(tags_made) / max(sum(times_tagged), 1) = 10/101.
        self.assertAlmostEqual(result["tag_ratio"], 10 / 101, places=4)
        # And explicitly NOT the mean-of-per-round-ratios value.
        self.assertNotAlmostEqual(result["tag_ratio"], 5.0, places=2)

    # ----- 6: avg_sp_earned with mixed roles, incl. Heavy fallback ---------

    def test_avg_sp_earned_mixed_roles_includes_heavy_fallback(self) -> None:
        """Heavy has no ``SPECIAL_COST`` entry so its term is ``final_special``
        only (``SPECIAL_COST.get("heavy", 0) == 0`` contributes nothing for
        ``specials_used``). The result must equal the manually-computed mean.

        Scout (cost=10): final_special=4, specials_used=2 → 4 + 10*2 = 24
        Heavy (cost=0):  final_special=7, specials_used=3 → 7 + 0*3 = 7
        Medic (cost=10): final_special=2, specials_used=1 → 2 + 10*1 = 12
        Mean = (24 + 7 + 12) / 3 = 43 / 3 ≈ 14.333…
        """
        # Sanity-check the role-constant assumption the contract relies on.
        self.assertEqual(SPECIAL_COST.get("heavy", 0), 0)
        rounds = [
            _round_dict(role="scout", final_special=4, specials_used=2),
            _round_dict(role="heavy", final_special=7, specials_used=3),
            _round_dict(role="medic", final_special=2, specials_used=1),
        ]
        result = summarize(rounds)
        expected = (
            (4 + SPECIAL_COST["scout"] * 2)
            + (7 + SPECIAL_COST.get("heavy", 0) * 3)
            + (2 + SPECIAL_COST["medic"] * 1)
        ) / 3
        self.assertAlmostEqual(result["avg_sp_earned"], expected)

    # ----- 7: survival capped at 1800 (1801 = SURVIVED_SENTINEL) -----------

    def test_survival_capping_at_1800(self) -> None:
        """The sentinel 1801 must be capped to 1800 in the mean."""
        rounds = [_round_dict(was_eliminated_at=1801)]
        result = summarize(rounds)
        self.assertAlmostEqual(result["avg_survival_ticks"], 1800.0)
        self.assertNotAlmostEqual(result["avg_survival_ticks"], 1801.0)

    # ----- 8: accuracy with all misses (no NaN, no inf) --------------------

    def test_accuracy_with_all_misses(self) -> None:
        """tags_made=0, shots_missed=10 → 0 / max(10, 1) * 100 = 0.0."""
        rounds = [_round_dict(tags_made=0, shots_missed=10)]
        result = summarize(rounds)
        self.assertAlmostEqual(result["avg_accuracy_pct"], 0.0)

    # ----- 9: by-role role order is Commander, Heavy, Scout, Medic, Ammo ---

    def test_summarize_by_role_order_commander_heavy_scout_medic_ammo(self) -> None:
        """Roles are returned in the locked order regardless of input order."""
        rounds = [
            _round_dict(role="medic", game_round_id=1),
            _round_dict(role="ammo", game_round_id=2),
            _round_dict(role="commander", game_round_id=3),
            _round_dict(role="scout", game_round_id=4),
            _round_dict(role="heavy", game_round_id=5),
        ]
        result = summarize_by_role(rounds)
        self.assertEqual(
            [r["role"] for r in result],
            ["commander", "heavy", "scout", "medic", "ammo"],
        )

    # ----- 10: by-role omits roles never played ----------------------------

    def test_summarize_by_role_omits_roles_not_played(self) -> None:
        """Only roles actually played are returned, in the locked order."""
        rounds = [
            _round_dict(role="medic", game_round_id=1),
            _round_dict(role="scout", game_round_id=2),
        ]
        result = summarize_by_role(rounds)
        self.assertEqual(len(result), 2)
        # Scout precedes Medic in the locked Commander→Heavy→Scout→Medic→Ammo order.
        self.assertEqual([r["role"] for r in result], ["scout", "medic"])

    # ----- 10b: summarize_by_role numeric correctness (per-role avg_points) -

    def test_summarize_by_role_numeric_correctness(self) -> None:
        """Per-role bucketing computes the right numbers per bucket.

        Two Commander rounds (1000 + 2000 pts) and one Scout round (300 pts):
          Commander: games=2, avg_points=1500.0
          Scout:     games=1, avg_points=300.0
        Pure-unit cases #4/#5 cover the full formula via ``summarize``; this
        case pins that ``summarize_by_role`` correctly applies those formulas
        to a per-role subset rather than over the full input.
        """
        rounds = [
            _round_dict(role="commander", points_scored=1000, game_round_id=1),
            _round_dict(role="commander", points_scored=2000, game_round_id=2),
            _round_dict(role="scout", points_scored=300, game_round_id=3),
        ]
        result = summarize_by_role(rounds)
        # Locked role order — Commander precedes Scout.
        self.assertEqual([r["role"] for r in result], ["commander", "scout"])
        commander, scout = result
        self.assertEqual(commander["games"], 2)
        self.assertAlmostEqual(commander["avg_points"], 1500.0)
        self.assertEqual(scout["games"], 1)
        self.assertAlmostEqual(scout["avg_points"], 300.0)

    # ----- 11: rolling_mean partial trailing window for the first 9 --------

    def test_rolling_mean_partial_window_for_first_nine(self) -> None:
        """[1,2,3] with window=10 → [1.0, 1.5, 2.0] (partial window for i<window)."""
        result = rolling_mean([1, 2, 3], window=10)
        self.assertEqual(result, [1.0, 1.5, 2.0])

    # ----- 12: rolling_mean full window kicks in at index window-1 ---------

    def test_rolling_mean_full_window_at_ten_plus(self) -> None:
        """For i >= window the window is full: mean(values[i-window+1 : i+1]).

        12 values, window=10:
          result[9]  = mean(values[0:10])
          result[10] = mean(values[1:11])
          result[11] = mean(values[2:12])
        """
        values = [float(i) for i in range(12)]
        result = rolling_mean(values, window=10)
        self.assertEqual(len(result), 12)
        self.assertAlmostEqual(result[9], sum(values[0:10]) / 10)
        self.assertAlmostEqual(result[10], sum(values[1:11]) / 10)
        self.assertAlmostEqual(result[11], sum(values[2:12]) / 10)

    # ----- 13: rolling_mean empty input ------------------------------------

    def test_rolling_mean_empty_input(self) -> None:
        self.assertEqual(rolling_mean([], window=10), [])

    # ----- 14: ordering tiebreaker is game_round_id ascending --------------

    def test_points_trend_ordering_ties_broken_by_game_round_id(self) -> None:
        """Two rounds with identical date_played: lower game_round_id first.

        The trend output is ``[[round_idx, mean_points], ...]`` with 1-based
        ``round_idx``; round 1 is the lower-id round, round 2 is the higher.
        Their cumulative means must reflect that ordering.
        """
        shared_date = "2026-05-22T12:00:00"
        rounds = [
            # Feed the higher id first to verify the function sorts.
            _round_dict(
                points_scored=200,
                date_played=shared_date,
                game_round_id=5,
            ),
            _round_dict(
                points_scored=100,
                date_played=shared_date,
                game_round_id=2,
            ),
        ]
        result = points_trend(rounds)
        # 1-based round_idx, ascending; lower id (2) becomes round 1.
        self.assertEqual(result[0][0], 1)
        self.assertEqual(result[1][0], 2)
        # round 1 trend value is the id=2 round's points (100), not 200.
        self.assertAlmostEqual(result[0][1], 100.0)
        # round 2 trend value is the cumulative mean of both → 150.0.
        self.assertAlmostEqual(result[1][1], 150.0)

    # ----- 15: defensive — no Django imports leaked into the pure module ---

    def test_no_django_imports_leaked(self) -> None:
        """The pure module must not leak ``django`` / ``models`` names and
        must import cleanly without ``django.setup()``.

        Mirrors the RES-04 and RV-03 defensive checks.
        """
        import importlib
        import sys

        import teams.career_stats as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))

        # And the module imports cleanly on a fresh load — no implicit
        # Django setup or ORM access on import.
        sys.modules.pop("teams.career_stats", None)
        importlib.import_module("teams.career_stats")


# ---------------------------------------------------------------------------
# §7.2 DB/view cases (Django TestCase + test client)
# ---------------------------------------------------------------------------


class TestPlayerCareerStatsView(TestCase):
    """View-level coverage for ``teams.views.player_career_stats`` (§7.2)."""

    def _make_player(self, name: str = "Career Player") -> Player:
        """Create one team + one player attached to the Commander slot."""
        team = Team.objects.create(name="Career Team")
        player = Player.objects.create(team=team, name=name)
        team.slot_commander = player
        team.save()
        return player

    def _make_round_with_state(
        self,
        player: Player,
        *,
        role: str = "commander",
        points_scored: int = 500,
        tags_made: int = 5,
        times_tagged: int = 3,
        shots_missed: int = 4,
        final_special: int = 2,
        specials_used: int = 1,
        was_eliminated_at: int = 1500,
    ) -> PlayerRoundState:
        """Create a real ``GameRound`` and one ``PlayerRoundState`` row."""
        game_round = GameRound.objects.create(
            round_number=1,
            team_red=player.team,
            team_blue=player.team,
        )
        return PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color="red",
            role=role,
            points_scored=points_scored,
            tags_made=tags_made,
            times_tagged=times_tagged,
            shots_missed=shots_missed,
            final_special=final_special,
            specials_used=specials_used,
            was_eliminated_at=was_eliminated_at,
        )

    # ----- 1: 200 OK with rounds — all context keys present ---------------

    def test_player_career_stats_view_200_with_rounds(self) -> None:
        """GET on a player with ≥ 2 rounds returns 200 and all 6 context keys."""
        player = self._make_player()
        self._make_round_with_state(player, role="commander")
        self._make_round_with_state(player, role="scout", points_scored=300)

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # All six locked context keys must be present.
        for key in (
            "player",
            "total_rounds",
            "career",
            "per_role",
            "trend",
            "has_rounds",
        ):
            self.assertIn(key, response.context, f"context key {key!r} missing")
        self.assertIs(response.context["has_rounds"], True)
        self.assertEqual(response.context["total_rounds"], 2)

        # Sanity-check one value through the view ↔ pure-module seam.
        # Two rounds at 500 + 300 pts → avg_points = 400.0. The pure
        # formulas themselves are exhaustively covered at the unit layer
        # (TestCareerStatsPure); this is just a wiring check.
        self.assertAlmostEqual(response.context["career"]["avg_points"], 400.0)
        self.assertEqual(response.context["career"]["games"], 2)
        # Roles played: Commander then Scout (locked order).
        self.assertEqual(
            [r["role"] for r in response.context["per_role"]],
            ["commander", "scout"],
        )
        # Trend has one entry per round, 1-based.
        self.assertEqual(len(response.context["trend"]), 2)
        self.assertEqual(response.context["trend"][0][0], 1)
        self.assertEqual(response.context["trend"][1][0], 2)

    # ----- 2: 200 OK on empty state — "No rounds played yet" substring ----

    def test_player_career_stats_view_200_empty_state(self) -> None:
        """GET on a player with zero rounds: 200, has_rounds=False, empty-state copy."""
        player = self._make_player(name="Rookie")
        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context["has_rounds"], False)
        self.assertIn("No rounds played yet", response.content.decode())

    # ----- 3: 404 on missing player ---------------------------------------

    def test_player_career_stats_view_404_for_missing_player(self) -> None:
        """A bogus player_id returns 404 (via ``get_object_or_404``)."""
        url = reverse("player_career_stats", args=[999_999_999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # ----- 4: "Career stats" anchor on the player-detail page -------------

    def test_career_stats_link_rendered_on_player_detail_page(self) -> None:
        """The existing player-detail page contains a 'Career stats' link."""
        player = self._make_player(name="Linkable")
        url = reverse(
            "player_detail",
            kwargs={"team_id": player.team_id, "player_id": player.id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Career stats", response.content.decode())


if __name__ == "__main__":
    unittest.main()
