"""Pure-unit tests for ``sim_helpers.round_series.build_round_series`` (RV-05 T1).

The function is a one-for-one port of the events page's per-player series
build (``templates/matches/game_round_events.html``, the RES-02b block), so
the PDF charts plot exactly what the page charts. Inputs are the server rows
the page already receives: ``_round_players_data`` rows (``id, name, team,
role, sl, ss``) and ``round_playback_payload`` rows (``ts`` in ticks, ``aid``,
``tid``, ``meta`` with the RES-02b actor / target / ``targets[]`` snapshots).

No Django ORM or test DB required -- hand-built dicts in the same shapes the
RES-02b view tests (``views_tests.py``) pin on ``events_data``.
"""

from __future__ import annotations

import unittest

from laserforce_simulator.testing import run_project_python
from matches.sim_helpers.round_series import build_round_series

# ---------------------------------------------------------------------------
# Factories -- the 6-key player row and the 13-key playback row
# ---------------------------------------------------------------------------


def _player(
    *,
    pid: int,
    name: str = "P",
    team: str = "red",
    role: str = "scout",
    sl: int = 15,
    ss: int = 30,
) -> dict:
    return {"id": pid, "name": name, "team": team, "role": role, "sl": sl, "ss": ss}


def _event(
    *,
    ts: int,
    aid: int,
    tid: int = -1,
    etype: str = "tag",
    meta: dict | None = None,
) -> dict:
    """A 13-key ``round_playback_payload`` row with the keys the series build
    ignores filled with placeholders."""
    return {
        "type": etype,
        "ts": ts,
        "tf": "00:00",
        "icon": "",
        "desc": "",
        "pts": 0,
        "aid": aid,
        "an": f"P{aid}",
        "at": 1,
        "tid": tid,
        "tn": f"P{tid}" if tid != -1 else "",
        "tt": 2 if tid != -1 else "",
        "meta": meta if meta is not None else {},
    }


def _actor_block(*, shots: int, lives: int, points: int, sp: int) -> dict:
    """The universal RES-02b actor block every row carries."""
    return {
        "actor_role": "scout",
        "actor_shots": shots,
        "actor_lives": lives,
        "actor_points": points,
        "sp": sp,
    }


def _target_block(*, shots: int, lives: int, points: int) -> dict:
    """The RES-02b target block every ``tid != -1`` row carries."""
    return {
        "target_role": "heavy",
        "target_shots": shots,
        "target_lives": lives,
        "target_points": points,
    }


# ---------------------------------------------------------------------------
# Start points
# ---------------------------------------------------------------------------


class TestStartPoints(unittest.TestCase):
    def test_five_keys_with_start_points_from_ss_sl_zero_zero(self) -> None:
        players = [
            _player(pid=7, name="Alice", team="red", sl=15, ss=30),
            _player(pid=9, name="Bob", team="blue", sl=10, ss=25),
        ]
        result = build_round_series(players, [])

        self.assertEqual(
            set(result.keys()), {"players", "shots", "lives", "points", "sp"}
        )
        self.assertEqual(result["shots"], {"7": [(0.0, 30)], "9": [(0.0, 25)]})
        self.assertEqual(result["lives"], {"7": [(0.0, 15)], "9": [(0.0, 10)]})
        self.assertEqual(result["points"], {"7": [(0.0, 0)], "9": [(0.0, 0)]})
        self.assertEqual(result["sp"], {"7": [(0.0, 0)], "9": [(0.0, 0)]})

    def test_players_echoes_id_name_team_in_input_order(self) -> None:
        players = [
            _player(pid=9, name="Bob", team="blue", role="heavy"),
            _player(pid=7, name="Alice", team="red", role="scout"),
        ]
        result = build_round_series(players, [])

        self.assertEqual(
            result["players"],
            [
                {"id": 9, "name": "Bob", "team": "blue"},
                {"id": 7, "name": "Alice", "team": "red"},
            ],
        )


# ---------------------------------------------------------------------------
# Snapshots -- actor / target / targets[]
# ---------------------------------------------------------------------------


class TestActorSnapshot(unittest.TestCase):
    def test_actor_block_feeds_the_actor_series(self) -> None:
        players = [_player(pid=7, sl=15, ss=30), _player(pid=9, sl=10, ss=25)]
        events = [
            _event(
                ts=10,
                aid=7,
                meta=_actor_block(shots=29, lives=15, points=100, sp=1),
            ),
            _event(
                ts=24,
                aid=7,
                meta=_actor_block(shots=28, lives=14, points=100, sp=1),
            ),
        ]
        result = build_round_series(players, events)

        self.assertEqual(result["shots"]["7"], [(0.0, 30), (5.0, 29), (12.0, 28)])
        self.assertEqual(result["lives"]["7"], [(0.0, 15), (12.0, 14)])
        self.assertEqual(result["points"]["7"], [(0.0, 0), (5.0, 100)])
        self.assertEqual(result["sp"]["7"], [(0.0, 0), (5.0, 1)])
        # The other player is untouched by a foreign actor block.
        self.assertEqual(result["shots"]["9"], [(0.0, 25)])
        self.assertEqual(result["sp"]["9"], [(0.0, 0)])

    def test_unchanged_value_appends_nothing(self) -> None:
        players = [_player(pid=7, sl=15, ss=30)]
        events = [
            # Same values as the start state: a miss-like row that changes
            # nothing must add no point to any chart.
            _event(
                ts=4,
                aid=7,
                etype="miss",
                meta=_actor_block(shots=30, lives=15, points=0, sp=0),
            ),
            # A row with no snapshot keys at all (e.g. a stripped meta)
            # likewise adds nothing.
            _event(ts=6, aid=7, etype="movement", meta={}),
            # Repeating an already-pushed value adds nothing either.
            _event(
                ts=8,
                aid=7,
                meta=_actor_block(shots=29, lives=15, points=0, sp=0),
            ),
            _event(
                ts=10,
                aid=7,
                meta=_actor_block(shots=29, lives=15, points=0, sp=0),
            ),
        ]
        result = build_round_series(players, events)

        self.assertEqual(result["shots"]["7"], [(0.0, 30), (4.0, 29)])
        self.assertEqual(result["lives"]["7"], [(0.0, 15)])
        self.assertEqual(result["points"]["7"], [(0.0, 0)])
        self.assertEqual(result["sp"]["7"], [(0.0, 0)])

    def test_x_value_is_ts_over_two_as_float(self) -> None:
        players = [_player(pid=7, sl=15, ss=30)]
        events = [
            _event(ts=5, aid=7, meta=_actor_block(shots=29, lives=15, points=0, sp=0))
        ]
        result = build_round_series(players, events)

        sec, _ = result["shots"]["7"][1]
        self.assertEqual(sec, 2.5)
        self.assertIsInstance(sec, float)
        sec0, _ = result["shots"]["7"][0]
        self.assertIsInstance(sec0, float)


class TestTargetSnapshot(unittest.TestCase):
    def test_target_block_feeds_the_target_series(self) -> None:
        players = [_player(pid=7, sl=15, ss=30), _player(pid=9, sl=10, ss=25)]
        # A tag: the actor spends a shot and scores; the target loses a life
        # and points. Shapes as the RES-02b contract pins them.
        events = [
            _event(
                ts=20,
                aid=7,
                tid=9,
                meta={
                    **_actor_block(shots=29, lives=15, points=100, sp=1),
                    **_target_block(shots=25, lives=9, points=-20),
                },
            ),
        ]
        result = build_round_series(players, events)

        self.assertEqual(result["shots"]["9"], [(0.0, 25)])
        self.assertEqual(result["lives"]["9"], [(0.0, 10), (10.0, 9)])
        self.assertEqual(result["points"]["9"], [(0.0, 0), (10.0, -20)])
        # The target block carries no sp; the target's SP series stays put.
        self.assertEqual(result["sp"]["9"], [(0.0, 0)])
        # The actor is fed by the actor block only.
        self.assertEqual(result["shots"]["7"], [(0.0, 30), (10.0, 29)])
        self.assertEqual(result["points"]["7"], [(0.0, 0), (10.0, 100)])

    def test_tid_minus_one_touches_no_target(self) -> None:
        players = [_player(pid=7, sl=15, ss=30), _player(pid=9, sl=10, ss=25)]
        # An untargeted row that (defensively) still carries a target block:
        # with tid == -1 nobody's series may take those values.
        events = [
            _event(
                ts=20,
                aid=7,
                tid=-1,
                etype="miss",
                meta={
                    **_actor_block(shots=29, lives=15, points=0, sp=0),
                    **_target_block(shots=1, lives=1, points=-999),
                },
            ),
        ]
        result = build_round_series(players, events)

        self.assertEqual(result["shots"]["7"], [(0.0, 30), (10.0, 29)])
        self.assertEqual(result["lives"]["7"], [(0.0, 15)])
        self.assertEqual(result["points"]["7"], [(0.0, 0)])
        for key in ("shots", "lives", "points", "sp"):
            self.assertEqual(len(result[key]["9"]), 1, key)
        self.assertNotIn("-1", result["shots"])


class TestMultiTargetSnapshot(unittest.TestCase):
    def test_targets_entries_feed_their_own_players(self) -> None:
        players = [
            _player(pid=7, sl=15, ss=30),
            _player(pid=9, sl=10, ss=25),
            _player(pid=11, sl=10, ss=25),
        ]
        # A nuke: one actor, two targets in ``meta.targets[]`` (the page's
        # multi-target shape ``{pid, shots, lives, points}``), ``tid == -1``.
        # An entry for an unknown pid is skipped, as on the page.
        events = [
            _event(
                ts=30,
                aid=7,
                tid=-1,
                etype="nuke_detonated",
                meta={
                    **_actor_block(shots=30, lives=15, points=500, sp=0),
                    "targets": [
                        {"pid": 9, "shots": 25, "lives": 7, "points": -60},
                        {"pid": 11, "shots": 20, "lives": 7, "points": 0},
                        {"pid": 999, "shots": 0, "lives": 0, "points": 0},
                    ],
                },
            ),
        ]
        result = build_round_series(players, events)

        self.assertEqual(result["lives"]["9"], [(0.0, 10), (15.0, 7)])
        self.assertEqual(result["points"]["9"], [(0.0, 0), (15.0, -60)])
        self.assertEqual(result["shots"]["9"], [(0.0, 25)])
        self.assertEqual(result["lives"]["11"], [(0.0, 10), (15.0, 7)])
        self.assertEqual(result["shots"]["11"], [(0.0, 25), (15.0, 20)])
        self.assertEqual(result["points"]["11"], [(0.0, 0)])
        self.assertEqual(result["points"]["7"], [(0.0, 0), (15.0, 500)])
        self.assertNotIn("999", result["lives"])


# ---------------------------------------------------------------------------
# Empties
# ---------------------------------------------------------------------------


class TestEmpties(unittest.TestCase):
    def test_player_with_no_events_keeps_only_its_start_point(self) -> None:
        players = [_player(pid=7, sl=15, ss=30), _player(pid=9, sl=10, ss=25)]
        events = [
            _event(
                ts=10,
                aid=7,
                tid=-1,
                meta=_actor_block(shots=29, lives=14, points=50, sp=2),
            ),
        ]
        result = build_round_series(players, events)

        self.assertEqual(result["shots"]["9"], [(0.0, 25)])
        self.assertEqual(result["lives"]["9"], [(0.0, 10)])
        self.assertEqual(result["points"]["9"], [(0.0, 0)])
        self.assertEqual(result["sp"]["9"], [(0.0, 0)])

    def test_empty_event_list_gives_start_points_only(self) -> None:
        players = [_player(pid=7, sl=15, ss=30)]
        result = build_round_series(players, [])

        for key in ("shots", "lives", "points", "sp"):
            self.assertEqual(list(result[key].keys()), ["7"], key)
            self.assertEqual(len(result[key]["7"]), 1, key)
            self.assertEqual(result[key]["7"][0][0], 0.0, key)

    def test_empty_player_list_gives_empty_dicts(self) -> None:
        # Events naming unknown players are ignored, as on the page.
        events = [
            _event(
                ts=10,
                aid=7,
                tid=9,
                meta={
                    **_actor_block(shots=29, lives=15, points=100, sp=1),
                    **_target_block(shots=25, lives=9, points=-20),
                    "targets": [{"pid": 11, "shots": 1, "lives": 1, "points": 1}],
                },
            )
        ]
        result = build_round_series([], events)

        self.assertEqual(
            result,
            {"players": [], "shots": {}, "lives": {}, "points": {}, "sp": {}},
        )


# ---------------------------------------------------------------------------
# Defensive: no Django imports leaked
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """Subprocess fresh-import ``matches.sim_helpers.round_series`` and assert
    ``sys.modules`` has no module name starting with ``django``. Mirrors the
    event-log / bracket precedent.
    """

    def test_round_series_imports_no_django(self) -> None:
        code = (
            "import sys\n"
            "import matches.sim_helpers.round_series\n"
            "leaked = [m for m in sys.modules if m == 'django' or m.startswith('django.')]\n"
            "assert not leaked, f'leaked django modules: {leaked}'\n"
        )
        result = run_project_python(code, timeout=15)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
