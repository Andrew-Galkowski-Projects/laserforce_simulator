"""ORM serialisation for a completed in-memory ``BatchSimulator`` round.

Single module in ``matches/simulation/`` that imports Django ORM models.
``flush_to_db`` is the lifted ``BatchSimulator._flush_to_db`` — a free
function the entrypoints layer calls from every save path
(``simulate_match`` / ``simulate_single_round_detailed`` /
``simulate_scheduled_round`` / ``save_games``).

GEN-01: the write-blocks are factored into named reusable helpers
(``build_roster_snapshot`` / ``_players_by_id`` / ``_write_player_states`` /
``_write_combat_events`` / ``_write_highlights`` / ``_write_movement_events``
/ ``_write_cell_occupancy``) so BOTH the fresh flush AND the lazy
``ensure_fidelity`` backfill call one source. ``flush_to_db`` gains a
``fidelity`` kwarg and gates each block by ``FIDELITY_RANK``; it ALWAYS
writes ``fidelity`` + ``roster_snapshot_json`` on the GameRound create, and
ALWAYS bumps the HX-02 cache at the tail.

The function preserves the pre-split contract verbatim at ``full``: same
kwargs, same ordering, the same second ``GameRound.save(update_fields=…)``
writes for ``cell_occupancy_json`` (RES-04) and ``highlights_json`` (RV-02),
and the HX-02 ``invalidate_role_benchmarks()`` cache bump at the tail. The
``@transaction.atomic`` decorator is preserved on the function itself.
"""

from dataclasses import asdict

from django.db import transaction

from ..models import FIDELITY_RANK, GameEvent, GameRound, PlayerRoundState
from ..sim_helpers.map_loader import zone_from_cell

# GEN-01: the 13 sim-stat keys snapshotted per player. Imported from
# entrypoints (the single source of truth for the tuple) at call time to
# avoid a circular import at module load (entrypoints imports persistence).


def build_roster_snapshot(red_players, blue_players) -> dict:
    """GEN-01: build the per-side roster-stat snapshot for a flushed round.

    Returns ``{"red": [...], "blue": [...]}`` where each entry is
    ``{"player_id": int, "name": str, "role": str, "stats": {<13 sim-stats>:
    int}}``. Built from the in-memory ``PlayerState`` objects (whose stats
    were already baked through ``stat_for_simulation`` in ``_make_players``),
    so feeding them back through ``_PlayerData.stat_for_simulation`` re-bakes
    IDENTICAL ``PlayerState``s — exact reproduction. Skips falsy player ids
    (mirroring the flush_to_db player-skip rule).
    """
    from .entrypoints import _SIMULATION_STATS

    def _side(players):
        out = []
        for p in players:
            if not p.player_id:
                continue
            out.append(
                {
                    "player_id": p.player_id,
                    "name": p.name,
                    "role": p.role,
                    "stats": {s: getattr(p, s) for s in _SIMULATION_STATS},
                }
            )
        return out

    return {"red": _side(red_players), "blue": _side(blue_players)}


def _players_by_id(red_players, blue_players) -> dict:
    """GEN-01: build the id → Player ORM object map (one query).

    Lifted from the inline ``flush_to_db`` build so both the fresh flush and
    ``ensure_fidelity`` share one source. Returns ``{player.id: Player}``.
    """
    from teams.models import Player as PlayerModel

    all_pids = [p.player_id for p in red_players + blue_players if p.player_id]
    return {p.id: p for p in PlayerModel.objects.filter(id__in=all_pids)}


def _write_player_states(
    game_round, red_players, blue_players, role_starting_resources
) -> dict:
    """GEN-01: write the PlayerRoundState rows for a fresh flush.

    Returns ``players_by_id`` (id → Player ORM) for downstream blocks to
    reuse. Always called on a fresh flush (every tier persists the
    scoreboard).
    """
    players_by_id = _players_by_id(red_players, blue_players)

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

    return players_by_id


def _write_combat_events(game_round, events, players_by_id) -> None:
    """GEN-01: write the combat ``GameEvent`` rows (gated ``rank >= combat``).

    The existing ``for ev in events:`` loop verbatim — the ``events`` buffer
    already contains ONLY non-movement events (movement rows are written
    separately from ``movement_trail``).
    """
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


def _write_movement_events(
    game_round, red_players, blue_players, movement_ctx, players_by_id
) -> None:
    """GEN-01: write the movement ``GameEvent`` rows + per-Advance route
    (gated ``rank == full``).

    MOVE-01: flush each player's compact movement trail to movement
    GameEvents (start cell + end cell + timestamp). Mirrors RBS movement-
    event semantics; the exact intermediate route is recomputed at replay by
    re-running deterministic A* start->end (not stored), except the persisted
    ``metadata["route"]`` for the playback overlay.
    """
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
            # poisoned the per-player chart series.
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


def _write_cell_occupancy(game_round, red_players, blue_players, movement_ctx) -> None:
    """GEN-01: write ``cell_occupancy_json`` (gated ``rank == full`` AND
    ``movement_ctx is not None``).

    RES-04: cell-occupancy snapshot. Only populated when a map is active
    (the map-active gate is required because reconstruct_cell_occupancy needs
    an A* adjacency dict). A ``full`` map-less round still leaves
    ``cell_occupancy_json`` null, matching today. Persisted by a second
    ``game_round.save(update_fields=["cell_occupancy_json"])``.
    """
    if movement_ctx is None:
        return

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


def _write_highlights(game_round, events, red_players, blue_players) -> None:
    """GEN-01: write ``highlights_json`` (gated ``rank >= combat``).

    RV-02: build auto-flagged highlights from the in-memory event buffer +
    result-derived team_elimination and persist. Pure function (no RNG);
    id->name / id->team maps keep it Django-free. Persisted by a second
    ``game_round.save(update_fields=["highlights_json"])``.

    NOTE: ``build_highlights`` also needs the round ``result`` for the
    team-elimination + scoring-burst kinds; the result is reconstructed from
    the persisted GameRound scalars so this helper's signature stays the same
    for the fresh-flush and backfill call sites.
    """
    from matches.sim_helpers.highlights import build_highlights
    from matches.sim_helpers.time_constants import TICKS_PER_ROUND

    result = {
        "red_points": game_round.red_points,
        "blue_points": game_round.blue_points,
        "red_eliminated": game_round.red_team_eliminated,
        "blue_eliminated": game_round.blue_team_eliminated,
        "eliminated_at": game_round.eliminated_at,
    }

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
    fidelity: str = "scores",
):
    """Write a replayed in-memory round to DB as a ``GameRound``.

    GEN-01: ``fidelity`` selects how much to persist. ``scores`` writes only
    the GameRound + PlayerRoundState scoreboard; ``combat`` adds the combat
    GameEvent rows + highlights_json; ``full`` adds movement GameEvents +
    per-Advance routes + cell_occupancy_json. The roster snapshot and the
    ``fidelity`` tag are ALWAYS written on the GameRound create. At ``scores``
    the caller passes ``events=None`` (no buffer collected); the combat /
    highlights helpers only run at ``rank >= combat`` where ``events`` is a
    real list.

    MOVE-01: ``movement_ctx`` (optional) is used only to resolve each
    movement step's end-cell zone for the compact movement GameEvents
    flushed from each player's ``movement_trail``.

    SIM-09: ``match`` / ``round_number`` allow the same flush path to
    persist either a standalone round or the two rounds of a full Match.
    ``arena_map`` / ``zone_size`` are persisted so saved games carry the
    same map metadata.

    ``role_starting_resources`` is the
    ``BatchSimulator.ROLE_STARTING_RESOURCES`` class attribute, passed in so
    this module does not depend on the simulator class for the
    starting-missile lookup.
    """
    rank = FIDELITY_RANK[fidelity]
    combat_rank = FIDELITY_RANK["combat"]
    full_rank = FIDELITY_RANK["full"]

    # GEN-01: build the snapshot always — every tier persists it so the
    # round is upgradeable later via ensure_fidelity.
    roster_snapshot = build_roster_snapshot(red_players, blue_players)

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
        fidelity=fidelity,
        roster_snapshot_json=roster_snapshot,
    )
    # Trigger winner calculation
    game_round.save()

    # ALWAYS: PlayerRoundState scoreboard. Returns players_by_id for the
    # higher-tier event / movement blocks to reuse.
    players_by_id = _write_player_states(
        game_round, red_players, blue_players, role_starting_resources
    )

    # rank >= combat: combat GameEvent rows + highlights_json.
    if rank >= combat_rank:
        _write_combat_events(game_round, events, players_by_id)
        _write_highlights(game_round, events, red_players, blue_players)

    # rank == full: movement GameEvents + per-Advance routes + cell occupancy.
    if rank >= full_rank:
        _write_movement_events(
            game_round, red_players, blue_players, movement_ctx, players_by_id
        )
        _write_cell_occupancy(game_round, red_players, blue_players, movement_ctx)

    # HX-02: bump the global role-benchmark cache version. ALWAYS (runs on
    # every tier). bulk_create skips post_save, so this hook covers the batch
    # save path; the call is cheap (one cache op) and monotonic — if the
    # surrounding @transaction.atomic rolls back, the next view request just
    # re-scans against the new version (invalidation is never wrong).
    from teams.role_benchmarks_cache import invalidate_role_benchmarks

    invalidate_role_benchmarks()

    return game_round
