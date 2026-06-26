"""ORM serialisation for a completed in-memory ``BatchSimulator`` round.

Single module in ``matches/simulation/`` that imports Django ORM models.
``flush_to_db`` is the lifted ``BatchSimulator._flush_to_db`` — a free
function the entrypoints layer calls from every save path
(``simulate_match`` / ``simulate_single_round_detailed`` /
``simulate_scheduled_round`` / ``save_games``).

The function preserves the pre-split contract verbatim: same kwargs, same
ordering, the same second ``GameRound.save(update_fields=…)`` writes for
``cell_occupancy_json`` (RES-04) and ``highlights_json`` (RV-02), and the
HX-02 ``invalidate_role_benchmarks()`` cache bump at the tail. The
``@transaction.atomic`` decorator is preserved on the function itself.
"""

from dataclasses import asdict

from django.db import transaction

from ..models import GameEvent, GameRound, PlayerRoundState
from ..sim_helpers.map_loader import zone_from_cell


@transaction.atomic
def flush_to_db(
    team_red,
    team_blue,
    result,
    red_players,
    blue_players,
    events,
    *,
    role_starting_resources: dict,
    rng_seed: int | None = None,
    movement_ctx=None,
    match=None,
    round_number: int = 1,
    arena_map=None,
    zone_size: int | None = None,
):
    """Write a replayed in-memory round to DB as a ``GameRound``.

    MOVE-01: ``movement_ctx`` (optional) is used only to resolve each
    movement step's end-cell zone for the compact movement GameEvents
    flushed from each player's ``movement_trail``. When ``None`` the
    per-move ``new_zone`` falls back to the player's final zone.

    SIM-09: ``match`` / ``round_number`` allow the same flush path to
    persist either a standalone round (default: ``match=None``,
    ``round_number=1``) or the two rounds of a full Match (via
    ``simulate_match``). ``arena_map`` / ``zone_size`` are persisted on
    ``GameRound`` so saved batch / match / single-round games all carry
    the same map metadata.

    ``role_starting_resources`` is the ``BatchSimulator.ROLE_STARTING_RESOURCES``
    class attribute, passed in so this module does not depend on the simulator
    class for the starting-missile lookup.
    """
    from teams.models import Player as PlayerModel

    game_round = GameRound.objects.create(
        match=match,
        round_number=round_number,
        team_red=team_red,
        team_blue=team_blue,
        red_points=result["red_points"],
        blue_points=result["blue_points"],
        red_team_eliminated=result["red_eliminated"],
        blue_team_eliminated=result["blue_eliminated"],
        eliminated_at=result["eliminated_at"],
        is_completed=True,
        rng_seed=rng_seed,
        arena_map=arena_map,
        zone_size=zone_size,
    )
    # Trigger winner calculation
    game_round.save()

    # Build id → Player ORM object map (one query)
    all_pids = [p.player_id for p in red_players + blue_players if p.player_id]
    players_by_id = {p.id: p for p in PlayerModel.objects.filter(id__in=all_pids)}

    # Create PlayerRoundState rows
    for p in red_players + blue_players:
        player_obj = players_by_id.get(p.player_id)
        if not player_obj:
            continue
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=player_obj,
            team_color=p.team_color,
            role=p.role,
            zone_fallback=p.current_zone,
            cell_row=p.cell_row,
            cell_col=p.cell_col,
            shields=p.shields,
            starting_lives=p.starting_lives,
            starting_shots=p.starting_shots,
            starting_special=0,
            starting_missiles=role_starting_resources[p.role]["missiles"],
            final_lives=p.final_lives,
            final_shots=p.final_shots,
            final_special=p.final_special,
            final_missiles=p.final_missiles,
            neutral_base_destroyed=p.neutral_base_destroyed,
            opposing_base_destroyed=p.opposing_base_destroyed,
            special_active_until=p.special_active_until or 0,
            is_hiding=p.is_hiding,
            final_medic_hits=p.medic_hits,
            ticks_active=p.ticks_active,
            ticks_not_targetable=p.ticks_not_targetable,
            ticks_reset_window=p.ticks_reset_window,
            was_eliminated_at=p.was_eliminated_at,
            # 18 counter columns splatted from PlayerCounters (ADR-0018).
            # The 5 nuke counters are 0 today (no simulator writer); the
            # model defaults match so this is behaviour-neutral.
            **asdict(p.counters),
        )

    # Create GameEvent rows
    for ev in events:
        actor_obj = players_by_id.get(ev["actor_id"])
        if not actor_obj:
            continue
        target_obj = (
            players_by_id.get(ev.get("target_id")) if ev.get("target_id") else None
        )
        GameEvent.objects.create(
            game_round=game_round,
            timestamp=ev["timestamp"],
            event_type=ev["event_type"],
            actor=actor_obj,
            target=target_obj,
            points_awarded=ev.get("points_awarded", 0),
            description=ev.get("description", ""),
            metadata=ev.get("metadata", {}),
        )

    # MOVE-01: flush each player's compact movement trail to movement
    # GameEvents (start cell + end cell + timestamp). Mirrors RBS
    # movement-event semantics; the exact intermediate route is recomputed
    # at replay by re-running deterministic A* start->end (not stored).
    spawn_cells = movement_ctx.get_spawn_cells() if movement_ctx else None
    for p in red_players + blue_players:
        actor_obj = players_by_id.get(p.player_id)
        if not actor_obj:
            continue
        routes = getattr(p, "movement_routes", None) or []
        for i, (start_cell, end_cell, ts) in enumerate(p.movement_trail):
            if spawn_cells is not None:
                new_zone = zone_from_cell(end_cell[0], end_cell[1], spawn_cells)
            else:
                new_zone = p.current_zone
            # Movement events carry only movement-specific metadata. Per-tick
            # actor snapshots (shots/lives/points/sp) are NOT tracked on the
            # trail; using the player's end-of-round values here previously
            # poisoned the per-player chart series (every movement event
            # stamped the final value, so chart lines jumped to end-of-round
            # values immediately after spawn).
            metadata = {
                "actor_role": p.role,
                "start_row": start_cell[0],
                "start_col": start_cell[1],
                "end_row": end_cell[0],
                "end_col": end_cell[1],
                "cell_row": end_cell[0],
                "cell_col": end_cell[1],
                "new_zone": new_zone,
            }
            # Playback overlay: persist the EXACT cells walked this Advance
            # (excludes the start, ends at the end cell) so the replay map
            # draws the true path. Appended in lockstep with movement_trail.
            route = routes[i] if i < len(routes) else None
            if route:
                metadata["route"] = route
            GameEvent.objects.create(
                game_round=game_round,
                timestamp=ts,
                event_type="movement",
                actor=actor_obj,
                target=None,
                points_awarded=0,
                description=f"{p.name} moves to cell ({end_cell[0]}, {end_cell[1]})",
                metadata=metadata,
            )

    # RES-04: cell-occupancy snapshot. Only populated when a map is active
    # (movement_ctx is not None); map-less rounds leave cell_occupancy_json
    # null. The map-active gate is required because reconstruct_cell_occupancy
    # needs an A* adjacency dict.
    if movement_ctx is not None:
        from matches.sim_helpers.cell_occupancy import reconstruct_cell_occupancy
        from matches.sim_helpers.time_constants import TICKS_PER_ROUND

        adj = movement_ctx.get_adjacency()
        elevation_data = movement_ctx.elevation_grid  # may be None — that's fine

        occupancy_json: dict[str, dict[str, int]] = {}
        for p in red_players + blue_players:
            if not p.player_id:
                continue
            spawn_cell = (
                p.movement_trail[0][0] if p.movement_trail else (p.cell_row, p.cell_col)
            )
            # Skip players who never had a cell position (no map, edge case).
            if spawn_cell[0] is None or spawn_cell[1] is None:
                continue

            per_cell = reconstruct_cell_occupancy(
                movement_trail=p.movement_trail,
                spawn_cell=spawn_cell,
                round_ticks=TICKS_PER_ROUND,
                eliminated_at=p.was_eliminated_at,
                adj=adj,
                elevation_data=elevation_data,
            )

            occupancy_json[str(p.player_id)] = {
                f"{r},{c}": ticks for (r, c), ticks in per_cell.items()
            }

        game_round.cell_occupancy_json = occupancy_json
        game_round.save(update_fields=["cell_occupancy_json"])

    # RV-02: build auto-flagged highlights from the in-memory event buffer
    # + result dict and persist. Runs on every path (map or 3-zone). Pure
    # function (no RNG); id->name / id->team maps keep it Django-free.
    from matches.sim_helpers.highlights import build_highlights
    from matches.sim_helpers.time_constants import TICKS_PER_ROUND

    name_by_id = {
        p.player_id: p.name for p in red_players + blue_players if p.player_id
    }
    team_by_id = {
        p.player_id: p.team_color for p in red_players + blue_players if p.player_id
    }
    game_round.highlights_json = build_highlights(
        events,
        result,
        round_ticks=TICKS_PER_ROUND,
        name_by_id=name_by_id,
        team_by_id=team_by_id,
    )
    game_round.save(update_fields=["highlights_json"])

    # HX-02: bump the global role-benchmark cache version. bulk_create
    # skips post_save, so this hook covers the batch save path; the
    # call is cheap (one cache op) and monotonic — if the surrounding
    # @transaction.atomic rolls back, the next view request just
    # re-scans against the new version (invalidation is never wrong).
    from teams.role_benchmarks_cache import invalidate_role_benchmarks

    invalidate_role_benchmarks()

    return game_round
