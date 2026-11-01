"""LG-06f -- pure-unit tests for ``matches.season_player_stats.zero_fill_watched``.

The helper filters to watched ids, appends zero rows for missing-but-watched
ids in ascending-id order, each zero row carrying every ``STAT_KEYS +
DERIVED_KEYS`` key at ``0.0`` with ``games == 0``; a watched id absent from
``identity_by_id`` is silently skipped; order is aggregated-first /
zero-second. The HTML Watch List screen's tests went with the screen
(ANG-08); the API serves the list (``test_api_watch_list.py``).
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches.season_player_stats import (
    DERIVED_KEYS,
    STAT_KEYS,
    PlayerStatRow,
    zero_fill_watched,
)

_ALL_KEYS = tuple(STAT_KEYS) + tuple(DERIVED_KEYS)


def _row(pid: int, **stat_overrides) -> PlayerStatRow:
    """An aggregated PlayerStatRow with the full stat key set populated."""
    stats = {k: 1.0 for k in _ALL_KEYS}
    stats.update(stat_overrides)
    return PlayerStatRow(
        player_id=pid,
        player_name=f"Player {pid}",
        team_id=pid * 10,
        team_name=f"Team {pid}",
        role="scout",
        games=3,
        stats=stats,
    )


def _identity(pid: int) -> dict:
    return {
        "player_name": f"Player {pid}",
        "team_id": pid * 10,
        "team_name": f"Team {pid}",
        "role": "scout",
    }


class TestZeroFillWatched(SimpleTestCase):
    def test_filters_rows_to_watched_ids(self) -> None:
        rows = [_row(1), _row(2), _row(3)]
        out = zero_fill_watched(rows, {1, 3}, {})
        self.assertEqual({r.player_id for r in out}, {1, 3})

    def test_unwatched_aggregated_row_dropped(self) -> None:
        rows = [_row(1), _row(2)]
        out = zero_fill_watched(rows, {1}, {})
        self.assertEqual([r.player_id for r in out], [1])

    def test_missing_but_watched_id_gets_zero_row(self) -> None:
        rows = [_row(1)]
        out = zero_fill_watched(rows, {1, 5}, {5: _identity(5)})
        ids = [r.player_id for r in out]
        self.assertIn(5, ids)
        zero_row = next(r for r in out if r.player_id == 5)
        self.assertEqual(zero_row.games, 0)

    def test_zero_row_carries_every_key_at_zero(self) -> None:
        out = zero_fill_watched([], {7}, {7: _identity(7)})
        zero_row = out[0]
        for key in _ALL_KEYS:
            self.assertIn(key, zero_row.stats)
            self.assertEqual(zero_row.stats[key], 0.0)

    def test_zero_row_identity_from_identity_by_id(self) -> None:
        out = zero_fill_watched([], {9}, {9: _identity(9)})
        zero_row = out[0]
        self.assertEqual(zero_row.player_id, 9)
        self.assertEqual(zero_row.player_name, "Player 9")
        self.assertEqual(zero_row.team_id, 90)
        self.assertEqual(zero_row.team_name, "Team 9")
        self.assertEqual(zero_row.role, "scout")

    def test_missing_ids_emitted_in_ascending_order(self) -> None:
        out = zero_fill_watched(
            [], {30, 10, 20}, {10: _identity(10), 20: _identity(20), 30: _identity(30)}
        )
        self.assertEqual([r.player_id for r in out], [10, 20, 30])

    def test_watched_id_absent_from_identity_silently_skipped(self) -> None:
        # 5 is watched and missing from aggregated rows but absent from
        # identity_by_id => no zero row, no crash.
        out = zero_fill_watched([_row(1)], {1, 5}, {})
        self.assertEqual([r.player_id for r in out], [1])

    def test_aggregated_first_then_zero_rows(self) -> None:
        # Aggregated rows keep incoming order; zero rows follow in ascending id.
        rows = [_row(3), _row(1)]
        out = zero_fill_watched(rows, {1, 3, 2, 4}, {2: _identity(2), 4: _identity(4)})
        ids = [r.player_id for r in out]
        # Aggregated 3, 1 (incoming order) first, then zero 2, 4 ascending.
        self.assertEqual(ids, [3, 1, 2, 4])

    def test_empty_inputs_return_empty_list(self) -> None:
        self.assertEqual(zero_fill_watched([], set(), {}), [])

    def test_returns_player_stat_rows(self) -> None:
        out = zero_fill_watched([_row(1)], {1, 2}, {2: _identity(2)})
        for r in out:
            self.assertIsInstance(r, PlayerStatRow)

    def test_deterministic_repeated_calls(self) -> None:
        rows = [_row(2), _row(1)]
        ident = {3: _identity(3), 4: _identity(4)}
        first = zero_fill_watched(list(rows), {1, 2, 3, 4}, ident)
        second = zero_fill_watched(list(rows), {1, 2, 3, 4}, ident)
        self.assertEqual([r.player_id for r in first], [r.player_id for r in second])
