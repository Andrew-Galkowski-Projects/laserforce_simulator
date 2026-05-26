from celery import shared_task

from teams.models import Team

from .simulation import BatchSimulator


def _resolve_arena_map(arena_map_id: int | None):
    """Resolve an arena_map_id to an ArenaMap or None.

    A stale id (deleted between POST and task start) is treated as None,
    mirroring the retired SIM-10 ``_run_batch_job`` / ``_run_save_job``
    semantics.
    """
    if arena_map_id is None:
        return None
    from core.models import ArenaMap

    try:
        return ArenaMap.objects.get(id=arena_map_id)
    except ArenaMap.DoesNotExist:
        return None


@shared_task(bind=True, name="matches.simulate_batch")
def simulate_batch_task(
    self,
    team_red_id: int,
    team_blue_id: int,
    n: int,
    arena_map_id: int | None = None,
    master_seed: int | None = None,
) -> dict:
    """Drive ``BatchSimulator.run_incremental``, emit each snapshot via
    ``self.update_state(state="PROGRESS", meta=snap)``, return the final
    aggregate dict on success.
    """
    import django.db

    try:
        team_red = Team.objects.get(id=team_red_id)
        team_blue = Team.objects.get(id=team_blue_id)
        arena_map = _resolve_arena_map(arena_map_id)

        last_snap: dict | None = None
        for snap in BatchSimulator().run_incremental(
            team_red,
            team_blue,
            n,
            arena_map=arena_map,
            workers=1,
            master_seed=master_seed,
        ):
            self.update_state(state="PROGRESS", meta=snap)
            last_snap = snap

        if last_snap is None:
            return {}
        return last_snap["aggregate"]
    finally:
        django.db.close_old_connections()


@shared_task(bind=True, name="matches.save_games")
def save_games_task(
    self,
    team_red_id: int,
    team_blue_id: int,
    seeds: list,
    n: int,
    arena_map_id: int | None = None,
) -> dict:
    """Replay carried ``(seed, flipped)`` pairs through
    ``BatchSimulator.save_games``, return ``{"round_ids": [...]}``.
    """
    import django.db

    try:
        team_red = Team.objects.get(id=team_red_id)
        team_blue = Team.objects.get(id=team_blue_id)
        arena_map = _resolve_arena_map(arena_map_id)
        game_rounds = BatchSimulator().save_games(
            team_red, team_blue, seeds, n, arena_map=arena_map
        )
        return {"round_ids": [gr.id for gr in game_rounds]}
    finally:
        django.db.close_old_connections()
