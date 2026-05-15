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
    """
    from matches.simulation import BatchSimulator  # noqa: PLC0415

    red_data, blue_data, seed_state = args
    random.setstate(seed_state)
    _, red_players, blue_players = BatchSimulator()._simulate_round(red_data, blue_data)
    return [
        {
            "role": p.role,
            "points_scored": p.points_scored,
            "tags_made": p.tags_made,
            "times_tagged": p.times_tagged,
            "missile_points": p.missile_points,
            "times_tagged_in_reset_window": p.times_tagged_in_reset_window,
            "ticks_active": p.ticks_active,
            "ticks_not_targetable": p.ticks_not_targetable,
            "ticks_reset_window": p.ticks_reset_window,
            "was_eliminated_at": p.was_eliminated_at,
            "follow_up_shots": p.follow_up_shots,
            "reaction_shots": p.reaction_shots,
        }
        for p in red_players + blue_players
    ]


def batch_round_worker(args: tuple) -> dict:
    """Run one simulation round for BatchSimulator.run() with workers > 1.

    Returns the same aggregate result dict as the serial path.
    """
    from matches.simulation import BatchSimulator  # noqa: PLC0415

    red_data, blue_data, movement_ctx, seed_state = args
    random.setstate(seed_state)
    result, _, _ = BatchSimulator()._simulate_round(
        red_data, blue_data, movement_ctx=movement_ctx
    )
    return result
