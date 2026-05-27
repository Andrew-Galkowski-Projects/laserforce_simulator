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


@shared_task(bind=True, name="matches.play_season")
def play_season_task(
    self,
    season_id: int,
    max_matchdays: int | None = None,
) -> dict:
    """LG-01d — Drive the Play Two Months / Play Until End async run.

    Loads the Season, materialises fixtures via ``generate_schedule``,
    builds ``played_keys`` from persisted ``GameRound``s, calls the pure
    ``select_play_fixtures(fixtures, played_keys, max_matchdays)`` to
    decide which Rounds to play, then loops calling
    ``BatchSimulator().simulate_scheduled_round(...)`` per fixture with
    a ``self.update_state(state="PROGRESS", meta={"completed": k+1,
    "total": n})`` after each Round.

    Per-Round commits — the task body has NO outer ``@transaction.atomic``
    wrapper. ``simulate_scheduled_round`` is already
    ``@transaction.atomic`` so each Round is its own transactional unit.
    A mid-loop exception propagates (Celery records ``FAILURE``); every
    completed Round survives because it was its own atomic commit. See
    ADR-0016 for the load-bearing decision.

    Returns:
        ``{"completed": n, "total": n}`` on success (``n = len(to_play)``).
    """
    import django.db

    try:
        from matches.models import GameRound, Season
        from matches.schedule_generator import generate_schedule
        from matches.season_dashboard import select_play_fixtures
        from matches.simulation import BatchSimulator
        from teams.models import Team

        season = Season.objects.get(id=season_id)

        fixtures = generate_schedule(
            season.starting_team_ids_json or [], season.schedule_format
        )
        played_keys = {
            (
                frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
                gr.round_number,
            )
            for gr in GameRound.objects.filter(match__season=season).select_related(
                "match"
            )
        }
        to_play = select_play_fixtures(fixtures, played_keys, max_matchdays)
        n = len(to_play)

        if n == 0:
            return {"completed": 0, "total": 0}

        team_ids = {f.team_a_id for f in to_play} | {f.team_b_id for f in to_play}
        team_by_id = Team.objects.in_bulk(team_ids)

        for k, fixture in enumerate(to_play):
            team_a = team_by_id[fixture.team_a_id]
            team_b = team_by_id[fixture.team_b_id]
            BatchSimulator().simulate_scheduled_round(
                season, team_a, team_b, fixture.round_number
            )
            self.update_state(
                state="PROGRESS",
                meta={"completed": k + 1, "total": n},
            )

        return {"completed": n, "total": n}
    finally:
        django.db.close_old_connections()
