"""LG-06d — Pure-unit tests for the Season selector + rate toggle seam.

Three pure surfaces (no DB / no Django in the assertion path):

* ``matches.season_player_stats.apply_rate(rows, rate)`` — transforms the
  10 SUMMED_KEYS only; ``total`` is identity, ``per_game`` divides by
  ``games``, ``per_10`` scales by ``600 / (survival * games)`` with a
  ``<= 0``-denominator guard → ``0.0``. AVERAGED_KEYS (mvp / accuracy) +
  DERIVED_KEYS (tag_ratio / survival) + ``games`` are untouched. Returns
  NEW rows (no mutation of the inputs).
* ``matches.league_views._coerce_season(raw, valid_season_ids, default)``
  — ``"career"`` sentinel, valid id → ``int``, invalid / non-enrolled /
  non-int → default, ``None`` default supported.
* ``matches.league_views._coerce_rate(raw, default="total")`` — each of
  the three valid values passes through; anything else → ``default``.

These assert against the APPROVED LG-06d seam contract verbatim. They are
expected to FAIL until the Code agent lands ``apply_rate`` /
``_coerce_season`` / ``_coerce_rate`` (TDD — red first).
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

from matches.season_player_stats import (
    AVERAGED_KEYS,
    DERIVED_KEYS,
    STAT_KEYS,
    SUMMED_KEYS,
    PlayerStatRow,
    apply_rate,
)

# ===========================================================================
# apply_rate — fixtures
# ===========================================================================


def _row(
    pid: int = 1,
    name: str = "P",
    tid: int = 1,
    tname: str = "T",
    role: str = "scout",
    games: int = 1,
    **stats,
) -> PlayerStatRow:
    """Build one ``PlayerStatRow`` with all STAT_KEYS + DERIVED_KEYS defaulted.

    ``survival`` defaults to a non-zero value so per_10 has a live
    denominator unless a test overrides it.
    """
    base: dict[str, float] = {k: 0.0 for k in STAT_KEYS}
    base["tag_ratio"] = 0.0
    base["survival"] = 100.0
    base.update(stats)
    return PlayerStatRow(
        player_id=pid,
        player_name=name,
        team_id=tid,
        team_name=tname,
        role=role,
        games=games,
        stats=base,
    )


# ===========================================================================
# apply_rate — total (identity)
# ===========================================================================


class TestApplyRateTotal(unittest.TestCase):
    def test_total_is_identity_on_summed_keys(self) -> None:
        row = _row(games=4, points_scored=400, tags_made=20)
        out = apply_rate([row], "total")
        self.assertEqual(out[0].stats["points_scored"], 400)
        self.assertEqual(out[0].stats["tags_made"], 20)

    def test_total_leaves_games_unchanged(self) -> None:
        out = apply_rate([_row(games=4)], "total")
        self.assertEqual(out[0].games, 4)

    def test_total_returns_one_row_per_input(self) -> None:
        out = apply_rate([_row(pid=1), _row(pid=2)], "total")
        self.assertEqual([r.player_id for r in out], [1, 2])


# ===========================================================================
# apply_rate — per_game
# ===========================================================================


class TestApplyRatePerGame(unittest.TestCase):
    def test_per_game_divides_summed_keys_by_games(self) -> None:
        row = _row(games=4, points_scored=400, tags_made=20, times_tagged=8)
        out = apply_rate([row], "per_game")
        self.assertAlmostEqual(out[0].stats["points_scored"], 100.0)
        self.assertAlmostEqual(out[0].stats["tags_made"], 5.0)
        self.assertAlmostEqual(out[0].stats["times_tagged"], 2.0)

    def test_per_game_transforms_every_summed_key(self) -> None:
        kwargs = {k: 30.0 for k in SUMMED_KEYS}
        out = apply_rate([_row(games=3, **kwargs)], "per_game")
        for k in SUMMED_KEYS:
            self.assertAlmostEqual(out[0].stats[k], 10.0, msg=k)

    def test_per_game_zero_games_guard_returns_zero(self) -> None:
        # games == 0 is a <= 0 denominator → 0.0 (defensive; the view never
        # passes a zero-games player, but the pure fn must not divide by 0).
        out = apply_rate([_row(games=0, points_scored=400)], "per_game")
        self.assertEqual(out[0].stats["points_scored"], 0.0)


# ===========================================================================
# apply_rate — per_10 (per 10 minutes of uptime)
# ===========================================================================


class TestApplyRatePer10(unittest.TestCase):
    def test_per_10_numeric_correctness(self) -> None:
        # value * 600 / (survival * games).
        # points 400, survival 300, games 2 → 400*600/(300*2) = 400.
        row = _row(games=2, points_scored=400)
        row.stats["survival"] = 300.0
        out = apply_rate([row], "per_10")
        self.assertAlmostEqual(out[0].stats["points_scored"], 400.0)

    def test_per_10_second_example(self) -> None:
        # tags 60, survival 600, games 1 → 60*600/(600*1) = 60.
        row = _row(games=1, tags_made=60)
        row.stats["survival"] = 600.0
        out = apply_rate([row], "per_10")
        self.assertAlmostEqual(out[0].stats["tags_made"], 60.0)

    def test_per_10_zero_survival_guard_returns_zero(self) -> None:
        row = _row(games=2, points_scored=400)
        row.stats["survival"] = 0.0
        out = apply_rate([row], "per_10")
        self.assertEqual(out[0].stats["points_scored"], 0.0)

    def test_per_10_negative_survival_guard_returns_zero(self) -> None:
        row = _row(games=2, points_scored=400)
        row.stats["survival"] = -5.0
        out = apply_rate([row], "per_10")
        self.assertEqual(out[0].stats["points_scored"], 0.0)

    def test_per_10_zero_games_guard_returns_zero(self) -> None:
        row = _row(games=0, points_scored=400)
        row.stats["survival"] = 300.0
        out = apply_rate([row], "per_10")
        self.assertEqual(out[0].stats["points_scored"], 0.0)

    def test_per_10_transforms_every_summed_key(self) -> None:
        kwargs = {k: 30.0 for k in SUMMED_KEYS}
        row = _row(games=1, **kwargs)
        row.stats["survival"] = 600.0
        out = apply_rate([row], "per_10")
        # 30 * 600 / (600 * 1) = 30 for each summed key.
        for k in SUMMED_KEYS:
            self.assertAlmostEqual(out[0].stats[k], 30.0, msg=k)


# ===========================================================================
# apply_rate — summed-only invariant + no mutation
# ===========================================================================


class TestApplyRateInvariants(unittest.TestCase):
    def _untouched_row(self):
        return _row(
            games=4,
            points_scored=400,
            tags_made=20,
            times_tagged=8,
            mvp=12.5,
            accuracy=88.0,
            tag_ratio=2.5,
            survival=300.0,
        )

    def test_averaged_keys_untouched_per_game(self) -> None:
        out = apply_rate([self._untouched_row()], "per_game")
        for k in AVERAGED_KEYS:
            self.assertAlmostEqual(
                out[0].stats[k], self._untouched_row().stats[k], msg=k
            )

    def test_averaged_keys_untouched_per_10(self) -> None:
        out = apply_rate([self._untouched_row()], "per_10")
        self.assertAlmostEqual(out[0].stats["mvp"], 12.5)
        self.assertAlmostEqual(out[0].stats["accuracy"], 88.0)

    def test_derived_keys_untouched_across_all_modes(self) -> None:
        for rate in ("total", "per_game", "per_10"):
            out = apply_rate([self._untouched_row()], rate)
            for k in DERIVED_KEYS:
                self.assertAlmostEqual(
                    out[0].stats[k],
                    self._untouched_row().stats[k],
                    msg=f"{k} changed under {rate}",
                )

    def test_games_unchanged_across_all_modes(self) -> None:
        for rate in ("total", "per_game", "per_10"):
            out = apply_rate([self._untouched_row()], rate)
            self.assertEqual(out[0].games, 4, msg=rate)

    def test_returns_new_rows_no_mutation(self) -> None:
        row = self._untouched_row()
        original_points = row.stats["points_scored"]
        out = apply_rate([row], "per_game")
        # Input row's summed value is unchanged (new dict / new row produced).
        self.assertEqual(row.stats["points_scored"], original_points)
        # The output row is a distinct object whose value differs.
        self.assertIsNot(out[0], row)
        self.assertNotEqual(out[0].stats["points_scored"], original_points)

    def test_identity_keys_carried_through(self) -> None:
        row = self._untouched_row()
        out = apply_rate([row], "per_game")
        self.assertEqual(out[0].player_id, row.player_id)
        self.assertEqual(out[0].player_name, row.player_name)
        self.assertEqual(out[0].team_id, row.team_id)
        self.assertEqual(out[0].team_name, row.team_name)
        self.assertEqual(out[0].role, row.role)

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(apply_rate([], "per_game"), [])


# ===========================================================================
# _coerce_season
# ===========================================================================


class TestCoerceSeason(unittest.TestCase):
    def setUp(self) -> None:
        from matches.league_views import _coerce_season

        self._coerce = _coerce_season
        self.valid = {10, 20, 30}

    def test_career_sentinel_returns_career(self) -> None:
        self.assertEqual(self._coerce("career", self.valid, 20), "career")

    def test_valid_id_returns_int(self) -> None:
        self.assertEqual(self._coerce("20", self.valid, 10), 20)

    def test_unenrolled_id_returns_default(self) -> None:
        self.assertEqual(self._coerce("999", self.valid, 10), 10)

    def test_non_int_returns_default(self) -> None:
        self.assertEqual(self._coerce("abc", self.valid, 10), 10)

    def test_none_returns_default(self) -> None:
        self.assertEqual(self._coerce(None, self.valid, 10), 10)

    def test_empty_string_returns_default(self) -> None:
        self.assertEqual(self._coerce("", self.valid, 10), 10)

    def test_none_default_supported(self) -> None:
        self.assertIsNone(self._coerce("abc", self.valid, None))
        self.assertIsNone(self._coerce(None, self.valid, None))

    def test_career_takes_precedence_over_default(self) -> None:
        # Even with a None default, "career" still resolves to the sentinel.
        self.assertEqual(self._coerce("career", self.valid, None), "career")

    def test_empty_valid_set_falls_back(self) -> None:
        self.assertEqual(self._coerce("20", set(), 99), 99)


# ===========================================================================
# _coerce_rate
# ===========================================================================


class TestCoerceRate(unittest.TestCase):
    def setUp(self) -> None:
        from matches.league_views import _coerce_rate

        self._coerce = _coerce_rate

    def test_total_passthrough(self) -> None:
        self.assertEqual(self._coerce("total"), "total")

    def test_per_game_passthrough(self) -> None:
        self.assertEqual(self._coerce("per_game"), "per_game")

    def test_per_10_passthrough(self) -> None:
        self.assertEqual(self._coerce("per_10"), "per_10")

    def test_invalid_falls_back_to_total(self) -> None:
        self.assertEqual(self._coerce("bogus"), "total")

    def test_none_falls_back_to_total(self) -> None:
        self.assertEqual(self._coerce(None), "total")

    def test_empty_string_falls_back_to_total(self) -> None:
        self.assertEqual(self._coerce(""), "total")

    def test_custom_default_honoured(self) -> None:
        self.assertEqual(self._coerce("bogus", default="per_game"), "per_game")


# ===========================================================================
# Pure-module purity (apply_rate must not pull in Django)
# ===========================================================================


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """``matches.season_player_stats`` (now hosting ``apply_rate``) must
    not transitively import Django — mirrors the HX-01 / LG-01z precedent.
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
            import matches.season_player_stats  # noqa: F401
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
            f"Django import leaked into matches.season_player_stats.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )
