"""Worker functions for multiprocessing parallel simulation.

No top-level Django imports — this module is imported by spawned worker
processes on Windows before django.setup() has been called.  Django is
initialised by worker_django_init(), which is registered as the
ProcessPoolExecutor initializer and runs before any task function executes.
"""

import random


def worker_django_init() -> None:
    """ProcessPoolExecutor initializer: run once per worker before any tasks."""
    import django

    django.setup()


def score_round_worker(args: tuple) -> list[dict]:
    """Run one simulation round and return per-player stats as plain dicts.

    Used by the score_averages management command parallel path.
    Lazy-imports BatchSimulator so the import happens after django.setup().

    ``movement_ctx`` is the optional map context built in the parent (a
    picklable ``MapContext`` or ``None`` for the 3-zone fallback); it is
    threaded through so ``--map`` works under ``--workers > 1``. This is the
    only worker change for the map flag — seeding stays state-based and out of
    SIM-07/SIM-08 scope.
    """
    from matches.simulation import BatchSimulator  # noqa: PLC0415

    red_data, blue_data, seed_state, movement_ctx = args
    random.setstate(seed_state)
    _, red_players, blue_players = BatchSimulator()._simulate_round(
        red_data, blue_data, movement_ctx=movement_ctx
    )
    return [
        {
            "role": p.role,
            "points_scored": p.counters.points_scored,
            "tags_made": p.counters.tags_made,
            "times_tagged": p.counters.times_tagged,
            "missile_points": p.counters.missile_points,
            "times_tagged_in_reset_window": p.counters.times_tagged_in_reset_window,
            "ticks_active": p.ticks_active,
            "ticks_not_targetable": p.ticks_not_targetable,
            "ticks_reset_window": p.ticks_reset_window,
            "was_eliminated_at": p.was_eliminated_at,
            "follow_up_shots": p.counters.follow_up_shots,
            "reaction_shots": p.counters.reaction_shots,
        }
        for p in red_players + blue_players
    ]


def calibration_round_worker(args: tuple) -> dict:
    """Run one Calibration harness round (LFS-01) and return its metrics.

    ``args`` is ``(red_data, blue_data, movement_ctx, seed)``: two rosters of
    ``(role, _PlayerData)`` pairs, the parent's picklable ``MapContext`` (or
    ``None``) and an int seed. Seeds with ``random.seed(seed)``, so the serial
    path (which calls this in-process) and a worker give the same round.

    Returns ``{"red": [...], "blue": [...], "winner", "red_score",
    "blue_score"}``: one ``lfstats_calibration.sim_metrics`` dict per player in
    roster order, ``winner`` as ``"red"`` / ``"blue"`` / ``"tie"`` by points,
    and each side's points.
    """
    from matches.lfstats_calibration import sim_metrics  # noqa: PLC0415
    from matches.simulation import BatchSimulator  # noqa: PLC0415

    red_data, blue_data, movement_ctx, seed = args
    random.seed(seed)
    result, red_players, blue_players = BatchSimulator()._simulate_round(
        red_data, blue_data, movement_ctx=movement_ctx
    )
    red_score, blue_score = result["red_points"], result["blue_points"]
    if red_score > blue_score:
        winner = "red"
    elif blue_score > red_score:
        winner = "blue"
    else:
        winner = "tie"
    return {
        "red": [sim_metrics(p, result) for p in red_players],
        "blue": [sim_metrics(p, result) for p in blue_players],
        "winner": winner,
        "red_score": red_score,
        "blue_score": blue_score,
    }


def batch_round_worker(args: tuple) -> dict:
    """Run one simulation round for BatchSimulator.run() with workers > 1.

    Returns the same aggregate result dict as the serial path.

    SIM-08: ``flipped`` is the per-game orientation computed in the parent
    from the ordered game index. When ``flipped`` is ``True`` the precomputed
    rosters are swapped before simulating (canonical blue plays the physical
    red side) so the worker produces the exact game the serial path would for
    the same (seed, orientation). The parent de-flips the result identically,
    keeping serial/parallel team-position aggregates and side_advantage in
    lockstep for a given master_seed.
    """
    from matches.simulation import BatchSimulator  # noqa: PLC0415

    red_data, blue_data, movement_ctx, seed, flipped = args
    random.seed(seed)
    if flipped:
        sim_red, sim_blue = blue_data, red_data
    else:
        sim_red, sim_blue = red_data, blue_data
    result, _, _ = BatchSimulator()._simulate_round(
        sim_red, sim_blue, movement_ctx=movement_ctx
    )
    return result
