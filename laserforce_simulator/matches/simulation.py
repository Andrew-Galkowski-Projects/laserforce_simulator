import random
import logging
import threading
import uuid
from django.db import transaction
from .models import GameEvent, Match, GameRound, PlayerRoundState
from .sim_helpers.pathfinding import (
    build_movement_adjacency,
    astar_next_step,
    choose_goal_cell,
)
from .sim_helpers.weights import (
    _get_medic_weights,
    _get_ammo_weights,
    _get_scout_weights,
    _get_heavy_weights,
    _get_commander_weights,
)
from teams.models import Player, ROLE_STATS
from core.models import (
    BaseSightLineConfig,
    HeavyStrongSpotsConfig,
    MapBaseConfig,
    MapCellRankingConfig,
    SightLineConfig,
)

# Module logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


def _can_tag_through_windowed_wall(
    r1: int, c1: int, r2: int, c2: int, zone_grid: list, wall_meta: dict
) -> bool:
    """Check whether a tag can reach (r2,c2) from (r1,c1) through windowed walls.

    Windowed walls block normal LOS but have a directional aperture:
      facing N/S → aperture along N-S axis (same column required: c1 == c2).
      facing E/W → aperture along E-W axis (same row required: r1 == r2).
    High walls (0) on the path always block regardless of windowed walls.
    Returns False if any cell on the path is an unpassable high wall, or if a
    windowed wall's aperture axis does not align with the attack direction.
    """
    x0, y0 = c1, r1
    x1, y1 = c2, r2
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    cx, cy = x0, y0

    while True:
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            cx += sx
        if e2 < dx:
            err += dx
            cy += sy
        if cx == x1 and cy == y1:
            return True
        cell_val = zone_grid[cy][cx]
        if cell_val == 0:
            return False
        if cell_val == 5:
            meta = wall_meta.get(f"{cy},{cx}", {})
            facing = meta.get("facing", "")
            if facing in ("N", "S"):
                if c1 != c2:
                    return False
            elif facing in ("E", "W"):
                if r1 != r2:
                    return False
            else:
                return False


def _get_los_targets(actor, candidates: list, movement_ctx: dict | None) -> list:
    """Return subset of candidates visible to actor.

    With a map: looks up actor's cell in SightLineConfig (normal LOS), then
    additionally checks windowed-wall aperture targeting for candidates not in
    normal sight. Falls back to same-zone filtering when no map is active.
    """
    if movement_ctx is None:
        return [p for p in candidates if p.current_zone == actor.current_zone]
    sight_data = movement_ctx.get("sight_data")
    if sight_data is None or actor.cell_row is None:
        return [p for p in candidates if p.current_zone == actor.current_zone]
    actor_key = f"{actor.cell_row},{actor.cell_col}"
    visible_cells = sight_data.get(actor_key, frozenset())

    result = []
    windowed_candidates = []
    for p in candidates:
        if p.cell_row is not None:
            if f"{p.cell_row},{p.cell_col}" in visible_cells:
                result.append(p)
            else:
                windowed_candidates.append(p)

    # Windowed-wall aperture check for targets not in normal sight
    zone_grid = movement_ctx.get("zone_data")
    wall_meta: dict = movement_ctx.get("wall_meta", {})
    if zone_grid is not None and wall_meta and windowed_candidates:
        for p in windowed_candidates:
            if _can_tag_through_windowed_wall(
                actor.cell_row,
                actor.cell_col,
                p.cell_row,
                p.cell_col,
                zone_grid,
                wall_meta,
            ):
                result.append(p)

    return result


# Neutral base_types in priority order for capture/reset checks
_NEUTRAL_BASE_TYPES = ("neutral_1", "neutral_2", "neutral_3", "neutral_4")


def _get_base_interaction(player, movement_ctx: dict | None) -> int | None:
    """Return base_id of the first uncaptured base the player is in range of, or None.

    Checks neutral bases first (priority), then the opposing base. Skips bases the player
    has already captured — those are ignored for the remainder of the game.
    base_id 15=neutral, 14=red player captures blue base, 13=blue player captures red base.
    Returns None when no map is active, player has no cell position, or no capturable base
    is in range.
    """
    if movement_ctx is None:
        return None
    base_sight_data = movement_ctx.get("base_sight_data")
    if not base_sight_data or player.cell_row is None:
        return None

    cell_key = f"{player.cell_row},{player.cell_col}"

    if not player.neutral_base_destroyed:
        for neutral_type in _NEUTRAL_BASE_TYPES:
            if cell_key in base_sight_data.get(neutral_type, frozenset()):
                return 15

    if not player.opposing_base_destroyed:
        opposing_type = "blue" if player.team_color == "red" else "red"
        if cell_key in base_sight_data.get(opposing_type, frozenset()):
            return 14 if player.team_color == "red" else 13

    return None


class ResourceBasedSimulator:
    """Enhanced simulator that tracks individual player resources"""

    TICK = 0.5  # seconds per simulation tick (matches BatchSimulator)

    def __init__(self):
        self.elimination_bonus = 10000  # Bonus points for eliminating entire team
        # Role-based starting resources
        self.role_starting_resources = {
            "commander": {"lives": 15, "shots": 30, "special": 0, "missiles": 5},
            "heavy": {"lives": 10, "shots": 20, "special": 0, "missiles": 5},
            "scout": {"lives": 15, "shots": 30, "special": 0, "missiles": 0},
            "medic": {"lives": 20, "shots": 15, "special": 0, "missiles": 0},
            "ammo": {"lives": 10, "shots": 15, "special": 0, "missiles": 0},
        }

    def simulate_match(
        self, team_red, team_blue, match_type="friendly", *, arena_map=None
    ):
        """Simulate a full 2-round match with detailed tracking"""
        match = Match.objects.create(
            team_red=team_red, team_blue=team_blue, match_type=match_type
        )

        # Round 1: team_red as red, team_blue as blue
        round1 = self.simulate_detailed_round(
            team_red, team_blue, match, 1, arena_map=arena_map
        )
        match.red_round1_points = round1.red_points
        match.blue_round1_points = round1.blue_points
        match.red_round1_eliminated = round1.red_team_eliminated
        match.blue_round1_eliminated = round1.blue_team_eliminated
        match.round1_eliminated_at = round1.eliminated_at

        # Round 2: teams switch colors
        round2 = self.simulate_detailed_round(
            team_blue, team_red, match, 2, arena_map=arena_map
        )
        match.red_round2_points = round2.blue_points  # Switched
        match.blue_round2_points = round2.red_points  # Switched
        match.red_round2_eliminated = round2.blue_team_eliminated  # Switched
        match.blue_round2_eliminated = round2.red_team_eliminated  # Switched
        match.round2_eliminated_at = round2.eliminated_at

        # Calculate bonus points
        if match.red_round1_eliminated:
            match.blue_bonus_points += self.elimination_bonus
        if match.blue_round1_eliminated:
            match.red_bonus_points += self.elimination_bonus
        if match.red_round2_eliminated:
            match.blue_bonus_points += self.elimination_bonus
        if match.blue_round2_eliminated:
            match.red_bonus_points += self.elimination_bonus

        match.is_completed = True
        match.save()

        return match

    def simulate_single_round_detailed(self, team_red, team_blue, *, arena_map=None):
        """Simulate a single round with detailed player tracking"""
        game_round = self.simulate_detailed_round(
            team_red, team_blue, None, 1, arena_map=arena_map
        )
        return game_round

    @transaction.atomic
    def simulate_detailed_round(
        self, team_red, team_blue, match=None, round_number=1, *, arena_map=None
    ):
        """Simulate a round with full player resource tracking"""
        (
            zone_size,
            spawn_cells,
            zone_data,
            sight_data,
            base_sight_data,
            cell_ranking,
            strong_spots,
            wall_meta,
        ) = self._resolve_map_data(arena_map)

        game_round = GameRound.objects.create(
            match=match,
            round_number=round_number,
            team_red=team_red,
            team_blue=team_blue,
            arena_map=arena_map,
            zone_size=zone_size,
        )

        # Initialize player states
        red_players = self._initialize_players(
            game_round, team_red, "red", spawn_cells, zone_data
        )
        blue_players = self._initialize_players(
            game_round, team_blue, "blue", spawn_cells, zone_data
        )

        # Simulate the round (pass game_round so events can be recorded)
        movement_ctx = ResourceBasedSimulator._build_movement_ctx(
            zone_data,
            spawn_cells,
            sight_data,
            base_sight_data,
            cell_ranking,
            strong_spots,
            wall_meta,
        )
        round_result = self._simulate_round_combat(
            game_round, red_players, blue_players, movement_ctx=movement_ctx
        )

        # Update game round with results
        game_round.red_points = round_result["red_points"]
        game_round.blue_points = round_result["blue_points"]
        game_round.red_team_eliminated = round_result["red_eliminated"]
        game_round.blue_team_eliminated = round_result["blue_eliminated"]
        game_round.eliminated_at = round_result["eliminated_at"]

        game_round.is_completed = True
        game_round.save()

        return game_round

    @staticmethod
    def _resolve_map_data(arena_map):
        """Load zone_size, spawn cells, zone_data, sight_data, base_sight_data,
        cell_ranking, strong_spots, and wall_meta from a map.

        Returns (zone_size, spawn_cells, zone_grid, sight_data, base_sight_data,
                 cell_ranking, strong_spots, wall_meta) where:
        - All values are None/{}/[] when no map is provided.
        - sight_data is {"r,c": frozenset(["r,c", ...])} keyed by cell — O(1) lookup.
        - base_sight_data is {"base_type": frozenset(["r,c", ...])} for O(1) cell lookup.
        - cell_ranking is [[r,c], ...] sorted highest-LOS first (empty if not yet computed).
        - strong_spots is [[r,c], ...] of Heavy defensive positions (empty if not computed).
        - wall_meta is {"r,c": {"facing": "N"|"S"|"E"|"W"}} for windowed wall apertures.
        Raises ValueError if the map is missing its zone config, a base, sight lines, or
        base sight line configs.
        """
        if arena_map is None:
            return None, {}, None, None, {}, [], [], {}

        config = arena_map.latest_confirmed_config()
        if config is None:
            raise ValueError(
                f"Map '{arena_map.name}' has no confirmed zone configuration. "
                "Please confirm a zone config in the map editor before simulating."
            )

        zone_size = config.zone_size
        raw = config.zone_data
        zone_grid = raw["zones"] if isinstance(raw, dict) else raw
        wall_meta: dict = raw.get("wall_meta", {}) if isinstance(raw, dict) else {}

        base_cfgs = {
            bc.base_type: bc
            for bc in MapBaseConfig.objects.filter(
                arena_map=arena_map, base_type__in=["red", "blue"]
            )
        }

        spawn_cells = {}
        for color in ("red", "blue"):
            base_cfg = base_cfgs.get(color)
            if base_cfg is None:
                raise ValueError(
                    f"Map '{arena_map.name}' has no {color} base placed. "
                    "Place a red and blue base in the map editor before simulating."
                )
            spawn_cells[color] = (
                base_cfg.y_px // zone_size,
                base_cfg.x_px // zone_size,
            )

        sight_config = SightLineConfig.objects.filter(
            arena_map=arena_map, zone_size=zone_size
        ).first()
        if sight_config is None:
            raise ValueError(
                f"Map '{arena_map.name}' has no sight lines computed for zone size "
                f"{zone_size}px. Click 'Compute Sight Lines' in the map editor before simulating."
            )
        sight_data = {k: frozenset(v) for k, v in sight_config.sight_data.items()}

        base_sight_configs = list(
            BaseSightLineConfig.objects.filter(arena_map=arena_map, zone_size=zone_size)
        )
        if not base_sight_configs:
            raise ValueError(
                f"Map '{arena_map.name}' has no base sight lines computed for zone size "
                f"{zone_size}px. Click 'Compute Sight Lines' in the map editor before simulating."
            )
        base_sight_data = {
            bsc.base_type: frozenset(f"{r},{c}" for r, c in bsc.visible_cells)
            for bsc in base_sight_configs
        }

        ranking_config = MapCellRankingConfig.objects.filter(
            arena_map=arena_map, zone_size=zone_size
        ).first()
        cell_ranking = ranking_config.ranked_cells if ranking_config else []

        strong_spots_config = HeavyStrongSpotsConfig.objects.filter(
            arena_map=arena_map, zone_size=zone_size
        ).first()
        strong_spots = strong_spots_config.cells if strong_spots_config else []

        return (
            zone_size,
            spawn_cells,
            zone_grid,
            sight_data,
            base_sight_data,
            cell_ranking,
            strong_spots,
            wall_meta,
        )

    @staticmethod
    def _zone_from_cell(row: int, col: int, spawn_cells: dict | None) -> int:
        """Return zone index (0=red, 1=neutral, 2=blue) via proximity to base cells.

        Nearest base type determines the zone. Neutral bases take precedence over
        team bases when equidistant or closer.
        """
        if not spawn_cells:
            return 1
        red_base = spawn_cells.get("red")
        blue_base = spawn_cells.get("blue")
        if red_base is None or blue_base is None:
            return 1
        dist_red = abs(row - red_base[0]) + abs(col - red_base[1])
        dist_blue = abs(row - blue_base[0]) + abs(col - blue_base[1])
        neutral_bases = [
            spawn_cells[f"neutral_{i}"]
            for i in range(1, 5)
            if f"neutral_{i}" in spawn_cells
        ]
        dist_neutral = min(
            (abs(row - nb[0]) + abs(col - nb[1]) for nb in neutral_bases),
            default=float("inf"),
        )
        if dist_neutral < dist_red and dist_neutral < dist_blue:
            return 1  # nearest to a neutral base
        if dist_red < dist_blue:
            return 0  # red zone
        if dist_blue < dist_red:
            return 2  # blue zone
        return 1  # equidistant = neutral

    @staticmethod
    def _build_movement_ctx(
        zone_data,
        spawn_cells,
        sight_data=None,
        base_sight_data=None,
        cell_ranking=None,
        strong_spots=None,
        wall_meta=None,
    ):
        if zone_data is None:
            return None
        # Precompute per-cell LOS count and the top-25% high-LOS cell list once per round.
        cell_los_counts: dict[str, int] = {}
        if sight_data:
            cell_los_counts = {k: len(v) for k, v in sight_data.items()}
        ranking = cell_ranking or []
        top_n = max(1, len(ranking) // 4) if ranking else 0
        high_los_cells = [tuple(rc) for rc in ranking[:top_n]]
        return {
            "adj": build_movement_adjacency(zone_data),
            "spawn_cells": spawn_cells,
            "zone_data": zone_data,
            "sight_data": sight_data,
            "base_sight_data": base_sight_data or {},
            "cell_los_counts": cell_los_counts,
            "high_los_cells": high_los_cells,
            "strong_spots": [tuple(rc) for rc in (strong_spots or [])],
            "wall_meta": wall_meta or {},
        }

    def _initialize_players(self, game_round, team, team_color, spawn_cells, zone_data):
        """Initialize player states from the team's active slot assignments."""
        player_states = []
        default_zone = 0 if team_color == "red" else 2

        spawn = spawn_cells.get(team_color)
        cell_row = spawn[0] if spawn else None
        cell_col = spawn[1] if spawn else None
        starting_zone = (
            self._zone_from_cell(cell_row, cell_col, spawn_cells)
            if spawn is not None
            else default_zone
        )

        for role, player in team.active_roster:
            resources = self.role_starting_resources[role]
            state = PlayerRoundState.objects.create(
                game_round=game_round,
                team_color=team_color,
                role=role,
                player=player,
                zone_fallback=starting_zone,
                cell_row=cell_row,
                cell_col=cell_col,
                shields=ROLE_STATS[role]["shield"],
                starting_lives=resources["lives"],
                starting_shots=resources["shots"],
                starting_special=resources["special"],
                starting_missiles=resources["missiles"],
                final_lives=resources["lives"],
                final_shots=resources["shots"],
                final_special=resources["special"],
                final_missiles=resources["missiles"],
            )
            player_states.append(state)

        return player_states

    def _simulate_round_combat(
        self, game_round, red_players, blue_players, movement_ctx=None
    ):
        """Simulate combat between two teams"""
        round_duration = 15 * 60  # 15 minutes in seconds

        pending_missiles = []  # (complete_time, attacker, defender)
        pending_nukes = []  # (complete_time, player_state)
        pending_followups = []  # (fire_at, attacker, defender, chain)
        pending_reactions = []  # (fire_at, attacker, defender)
        last_shot_times = (
            {}
        )  # player.id → float last-shot second (survives refresh_from_db)
        eliminated_at = 901

        second = 0.0
        while second < round_duration:
            db_second = int(second)

            # --- process pending missiles ---
            to_run = [m for m in pending_missiles if m[0] <= second]
            pending_missiles = [m for m in pending_missiles if m[0] > second]
            for complete_time, attacker, defender in to_run:
                ct = int(complete_time)
                if attacker.is_active_at(ct) and defender.is_taggable_at(ct):
                    self._complete_missile(attacker, defender, ct)
                else:
                    logger.debug(
                        "%s - %s: missile cancelled or failed at %s",
                        ct,
                        "missile completion",
                        ct,
                    )

            # --- process pending nukes ---
            to_run_n = [n for n in pending_nukes if n[0] <= second]
            pending_nukes = [n for n in pending_nukes if n[0] > second]
            for complete_time, player_state in to_run_n:
                self._resolve_pending_nuke(player_state, int(complete_time))

            # REFRESH player states from database after nukes/missiles
            for p in red_players + blue_players:
                p.refresh_from_db()

            # --- process pending reactions (deferred by shot cooldown) ---
            due_rx = [item for item in pending_reactions if item[0] <= second]
            pending_reactions = [item for item in pending_reactions if item[0] > second]
            for _, r_attacker, r_defender in due_rx:
                if r_attacker.final_lives <= 0 or r_defender.final_lives <= 0:
                    continue
                if not r_attacker.is_active_at(db_second):
                    continue
                if r_attacker.final_shots <= 0 and r_attacker.role != "ammo":
                    continue
                r_attacker.reaction_shots += 1
                last_shot_times[r_attacker.id] = second
                hit_chance = max(
                    10,
                    min(
                        95, 70 + r_attacker.player.accuracy - r_defender.player.survival
                    ),
                )
                react_hit = random.randint(1, 100) < hit_chance
                if r_attacker.role != "ammo":
                    r_attacker.final_shots = max(0, r_attacker.final_shots - 1)
                if react_hit:
                    r_attacker.tags_made += 1
                    if r_attacker.role != "heavy":
                        r_attacker.final_special += 1
                    r_attacker.points_scored += 100
                    if r_defender.role == "medic":
                        r_attacker.final_medic_hits += 1
                    r_defender.times_tagged += 1
                    r_defender.points_scored -= 20
                    if not r_defender.is_active_at(
                        db_second
                    ) and r_defender.is_taggable_at(db_second):
                        r_defender.times_tagged_in_reset_window += 1
                    r_defender.shields = max(
                        0, r_defender.shields - r_attacker.shot_power
                    )
                    if r_defender.shields == 0:
                        r_defender.final_lives = max(0, r_defender.final_lives - 1)
                        r_defender.last_downed_time = db_second
                        r_defender.shields = r_defender.max_shields
                        GameEvent.objects.create(
                            game_round=game_round,
                            timestamp=db_second,
                            event_type="player_downed",
                            actor=r_attacker.player,
                            target=r_defender.player,
                            points_awarded=0,
                            description=f"{r_defender.player.name} downed by {r_attacker.player.name} (reaction)",
                            metadata={
                                "cause": "reaction",
                                "actor_role": r_attacker.role,
                                "target_role": r_defender.role,
                                "target_lives": r_defender.final_lives,
                            },
                        )
                        if r_defender.final_lives <= 0:
                            r_defender.was_eliminated_at = db_second
                            GameEvent.objects.create(
                                game_round=game_round,
                                timestamp=db_second,
                                event_type="elimination",
                                actor=r_attacker.player,
                                target=r_defender.player,
                                points_awarded=0,
                                description=f"{r_defender.player.name} eliminated by {r_attacker.player.name} (reaction)",
                                metadata={
                                    "elimination_action": "reaction",
                                    "actor_role": r_attacker.role,
                                    "target_role": r_defender.role,
                                },
                            )
                    GameEvent.objects.create(
                        game_round=game_round,
                        timestamp=db_second,
                        event_type="tag",
                        actor=r_attacker.player,
                        target=r_defender.player,
                        points_awarded=100,
                        description=f"{r_attacker.player.name} reacts and zaps {r_defender.player.name}",
                        metadata={
                            "actor_role": r_attacker.role,
                            "target_role": r_defender.role,
                            "is_reaction": True,
                        },
                    )
                    r_attacker.save()
                    r_defender.save()
                else:
                    r_attacker.shots_missed += 1
                    r_attacker.save()
                    GameEvent.objects.create(
                        game_round=game_round,
                        timestamp=db_second,
                        event_type="miss",
                        actor=r_attacker.player,
                        target=r_defender.player,
                        points_awarded=0,
                        description=f"{r_attacker.player.name} reaction miss on {r_defender.player.name}",
                        metadata={"actor_role": r_attacker.role, "is_reaction": True},
                    )

            # --- process pending follow-ups (deferred by shot cooldown) ---
            due_fu = [item for item in pending_followups if item[0] <= second]
            pending_followups = [item for item in pending_followups if item[0] > second]
            for _, fu_attacker, fu_defender, chain in due_fu:
                if fu_attacker.final_lives <= 0 or fu_defender.final_lives <= 0:
                    continue
                if fu_attacker.final_shots <= 0 and fu_attacker.role != "ammo":
                    continue
                fu_attacker.follow_up_shots += 1
                last_shot_times[fu_attacker.id] = second
                hit_chance = max(
                    10,
                    min(
                        95,
                        70 + fu_attacker.player.accuracy - fu_defender.player.survival,
                    ),
                )
                fu_hit = random.randint(1, 100) < hit_chance
                if fu_attacker.role != "ammo":
                    fu_attacker.final_shots = max(0, fu_attacker.final_shots - 1)
                if fu_hit:
                    fu_attacker.tags_made += 1
                    if fu_attacker.role != "heavy":
                        fu_attacker.final_special += 1
                    fu_attacker.points_scored += 100
                    if fu_defender.role == "medic":
                        fu_attacker.final_medic_hits += 1
                    fu_defender.times_tagged += 1
                    fu_defender.points_scored -= 20
                    if not fu_defender.is_active_at(
                        db_second
                    ) and fu_defender.is_taggable_at(db_second):
                        fu_defender.times_tagged_in_reset_window += 1
                    fu_defender.shields = max(
                        0, fu_defender.shields - fu_attacker.shot_power
                    )
                    downed = fu_defender.shields == 0
                    if downed:
                        fu_defender.final_lives = max(0, fu_defender.final_lives - 1)
                        fu_defender.last_downed_time = db_second
                        fu_defender.shields = fu_defender.max_shields
                        GameEvent.objects.create(
                            game_round=game_round,
                            timestamp=db_second,
                            event_type="player_downed",
                            actor=fu_attacker.player,
                            target=fu_defender.player,
                            points_awarded=0,
                            description=f"{fu_defender.player.name} downed by {fu_attacker.player.name} (follow-up)",
                            metadata={
                                "cause": "follow_up_tag",
                                "actor_role": fu_attacker.role,
                                "target_role": fu_defender.role,
                                "target_lives": fu_defender.final_lives,
                            },
                        )
                        if fu_defender.final_lives <= 0:
                            fu_defender.was_eliminated_at = db_second
                            GameEvent.objects.create(
                                game_round=game_round,
                                timestamp=db_second,
                                event_type="elimination",
                                actor=fu_attacker.player,
                                target=fu_defender.player,
                                points_awarded=0,
                                description=f"{fu_defender.player.name} eliminated by {fu_attacker.player.name} (follow-up)",
                                metadata={
                                    "elimination_action": "follow_up_tag",
                                    "actor_role": fu_attacker.role,
                                    "target_role": fu_defender.role,
                                },
                            )
                    GameEvent.objects.create(
                        game_round=game_round,
                        timestamp=db_second,
                        event_type="tag",
                        actor=fu_attacker.player,
                        target=fu_defender.player,
                        points_awarded=100,
                        description=f"{fu_attacker.player.name} follow-up tags {fu_defender.player.name}",
                        metadata={
                            "actor_role": fu_attacker.role,
                            "target_role": fu_defender.role,
                            "is_follow_up": True,
                            "chain": chain,
                        },
                    )
                    fu_attacker.save()
                    fu_defender.save()
                    if not downed and chain < 2 and fu_defender.final_lives > 0:
                        if fu_defender.player.player_awareness < random.randint(0, 100):
                            cooldown = self._shot_cooldown(fu_attacker, second)
                            pending_followups.append(
                                (second + cooldown, fu_attacker, fu_defender, chain + 1)
                            )
                else:
                    fu_attacker.shots_missed += 1
                    fu_attacker.save()
                    GameEvent.objects.create(
                        game_round=game_round,
                        timestamp=db_second,
                        event_type="miss",
                        actor=fu_attacker.player,
                        target=fu_defender.player,
                        points_awarded=0,
                        description=f"{fu_attacker.player.name} follow-up miss on {fu_defender.player.name}",
                        metadata={"actor_role": fu_attacker.role, "is_follow_up": True},
                    )

            # Plan and resolve simultaneous actions for this tick
            self._simulate_combat_exchange(
                game_round,
                red_players,
                blue_players,
                second,
                pending_missiles,
                pending_nukes,
                pending_followups,
                pending_reactions,
                last_shot_times,
                movement_ctx=movement_ctx,
            )

            # Check for team eliminations
            red_alive = [p for p in red_players if p.final_lives > 0]
            blue_alive = [p for p in blue_players if p.final_lives > 0]

            if not red_alive or not blue_alive:
                eliminated_at = second
                logger.debug(
                    "%s - %s: Round ends at second %s, red alive %s, blue alive %s",
                    second,
                    "simulate_round_combat",
                    second,
                    red_alive,
                    blue_alive,
                )

                # award uncaptured bases to alive players on the winning team,
                # but only if eliminated with more than 1 minute remaining
                if not red_alive and second < 840:
                    for blue_player in blue_alive:
                        self._award_bases(blue_player, second)
                if not blue_alive and second < 840:
                    for red_player in red_alive:
                        self._award_bases(red_player, second)

                break  # Round ends on elimination

            second += self.TICK

        # Calculate final results
        red_points = sum(p.points_scored for p in red_players)
        blue_points = sum(p.points_scored for p in blue_players)

        # AI added survival bonuses, we don't want point bonuses here
        # but maybe we keep this in for MVP bonuses later

        # # Add survival bonuses
        # red_survivors = len([p for p in red_players if p.final_lives > 0])
        # blue_survivors = len([p for p in blue_players if p.final_lives > 0])

        # red_points += red_survivors * 50  # Survival bonus
        # blue_points += blue_survivors * 50

        # Determine eliminations
        red_eliminated = all(p.final_lives <= 0 for p in red_players)
        blue_eliminated = all(p.final_lives <= 0 for p in blue_players)
        logger.debug(
            "%s - %s: Final Results: %s red points, %s blue points, red eliminated: %s, blue eliminated: %s",
            second,
            "simulate round combat",
            red_points,
            blue_points,
            red_eliminated,
            blue_eliminated,
        )

        # Save final states
        for p in red_players + blue_players:
            p.save()

        return {
            "red_points": red_points,
            "blue_points": blue_points,
            "red_eliminated": red_eliminated,
            "blue_eliminated": blue_eliminated,
            "eliminated_at": eliminated_at,
        }

    # this simulates multiple hits between teams at random
    # TODO: once I do some testing to verify this works I want to improve this
    # I want something along the lines of 3 zones of (red, mid, blue) and have players
    # move between zones and only have the ability to hit players in adjacent zones
    # or their own zone.  target probability should change based on role and who else is in the zone
    # heavies should "tank" hits if they are in the same zone as the medic and or ammo player
    # this will be simulated by having an random roll for who is attacked and weighting it based on these factors
    # in this simulation we want to also simulate down time when tagged so that weight would change if
    # the combat exchange happens while a player is down

    def _simulate_combat_exchange(
        self,
        game_round,
        red_players,
        blue_players,
        second,
        pending_missiles=None,
        pending_nukes=None,
        pending_followups=None,
        pending_reactions=None,
        last_shot_times=None,
        movement_ctx=None,
    ):
        """Simulate a single combat exchange between teams"""
        # Get alive players
        red_alive = [
            p for p in red_players if p.final_lives > 0 and p.was_eliminated_at > second
        ]
        blue_alive = [
            p
            for p in blue_players
            if p.final_lives > 0 and p.was_eliminated_at > second
        ]
        all_alive = red_alive + blue_alive

        # new logic instead of random
        """
        get list of all alive players
        randmize the order of that list
        pick first player off list
        decide action to perform
            use player awareness, game awareness, resource awareness, speed, decision making
        peform action
        determine if follow up action
        """
        # Plan phase: decide all player actions this tick (no side-effects yet)
        if pending_missiles is None:
            pending_missiles = []
        if pending_nukes is None:
            pending_nukes = []
        if pending_followups is None:
            pending_followups = []
        if pending_reactions is None:
            pending_reactions = []
        if last_shot_times is None:
            last_shot_times = {}

        # TODO: eventually want to sort all_alive by player decision making or something
        random.shuffle(all_alive)
        plans = []
        for player in all_alive:
            plans.extend(
                self._plan_action(
                    player,
                    all_alive,
                    second,
                    last_shot_times,
                    movement_ctx=movement_ctx,
                )
            )

        zone_map = {0: "red_zone", 1: "neutral_zone", 2: "blue_zone"}

        counts = {
            ("red", "red_zone"): 0,
            ("red", "neutral_zone"): 0,
            ("red", "blue_zone"): 0,
            ("blue", "red_zone"): 0,
            ("blue", "neutral_zone"): 0,
            ("blue", "blue_zone"): 0,
        }
        r_lives = 0
        b_lives = 0
        for player in all_alive:
            if player.team_color == "red":
                r_lives += player.final_lives
            else:
                b_lives += player.final_lives
            zone_name = zone_map.get(player.current_zone)
            if zone_name and player.team_color in ["red", "blue"]:
                counts[(player.team_color, zone_name)] += 1

        logger.debug(
            "%s - %s: red zone: %s-%s Neutral zone: %s-%s blue zone: %s-%s",
            second,
            "sim-combat-exch",
            counts[("red", "red_zone")],
            counts[("blue", "red_zone")],
            counts[("red", "neutral_zone")],
            counts[("blue", "neutral_zone")],
            counts[("red", "blue_zone")],
            counts[("blue", "blue_zone")],
        )
        logger.debug(
            "%s - %s: alive: %s r-lives: %s b-lives: %s",
            second,
            "sim-combat-exch",
            len(all_alive),
            r_lives,
            b_lives,
        )

        # Apply non-combat actions immediately (resupplies, zone changes, hides, base captures)
        tag_attempts = []  # collect tag attempts for simultaneous resolution
        for plan in plans:
            ptype = plan.get("type")
            actor = plan.get("actor")
            # logger.debug(
            #     "%s - %s: actor: %s%s type: %s",
            #     second,
            #     "sim-combat-exch",
            #     actor.team_color,
            #     actor.role,
            #     ptype,
            # )
            if ptype == "resupply_ammo" or ptype == "resupply_lives":
                # use existing helper
                self._attempt_resupply(actor, plan.get("target"), second)
            elif ptype == "change_zone":
                goal_cell = plan.get("goal_cell")
                ctx = plan.get("movement_ctx")
                if goal_cell is not None and ctx is not None:
                    self._move_to_cell(actor, second, goal_cell, ctx)
                else:
                    self._change_zone(actor, second, towards=plan.get("zone"))
            elif ptype == "hide":
                actor.is_hiding = True
                actor.save()
            elif ptype == "capture_base":
                self._capture_base(
                    actor,
                    plan.get("base_id"),
                    second,
                    movement_ctx=plan.get("movement_ctx"),
                )
            elif ptype == "missile":
                scheduled = self._start_missile_lock(actor, plan.get("target"), second)
                if scheduled:
                    pending_missiles.append(scheduled)
            elif ptype == "use_special":
                # _use_special will apply resource costs / activation event and may return a scheduled nuke
                scheduled = self._use_special(actor, second)
                if scheduled and scheduled[0] == "nuke":
                    pending_nukes.append((scheduled[1], scheduled[2]))
            elif ptype == "tag":
                tag_attempts.append({"attacker": actor, "defender": plan.get("target")})

        # Combat phase: resolve all tag attempts simultaneously
        if tag_attempts:
            self._resolve_tag_attempts(
                game_round,
                tag_attempts,
                second,
                last_shot_times,
                pending_followups,
                pending_reactions,
            )

    def _shot_cooldown(self, player, second: float) -> float:
        """Seconds the player must wait between shots (mirrors BatchSimulator)."""
        if player.role == "scout" and player.special_active_until > second:
            return 0.0
        if player.role == "heavy":
            return 1.0
        return 0.5

    def _plan_action(
        self, player, all_alive, second, last_shot_times=None, movement_ctx=None
    ):
        """Return a list of planned actions (dict) for the player at this tick without applying changes.

        Each plan dict may have keys: type, actor, target, zone, base_id, goal_cell, movement_ctx
        """
        if last_shot_times is None:
            last_shot_times = {}
        action_to_weight_index = {
            "tag_player": 0,
            "change_zone": 1,
            "hide": 2,
            "capture_base": 3,
            "use_special": 4,
            "resupply_ally": 5,
            "missile_player": 6,
        }
        choices = [
            "tag_player",
            "change_zone",
            "hide",
            "capture_base",
            "use_special",
            "resupply_ally",
            "missile_player",
        ]
        weights = [70, 30, 0, 0, 0, 0, 0]
        if player.role == "medic":
            weights = _get_medic_weights(
                player, action_to_weight_index, weights, all_alive, second
            )
        if player.role == "ammo":
            weights = _get_ammo_weights(
                player, action_to_weight_index, weights, all_alive, second
            )
        if player.role == "scout":
            weights = _get_scout_weights(
                player, action_to_weight_index, weights, all_alive, second
            )
        if player.role == "heavy":
            weights = _get_heavy_weights(
                player, action_to_weight_index, weights, all_alive, second
            )
        if player.role == "commander":
            weights = _get_commander_weights(
                player, action_to_weight_index, weights, all_alive, second
            )

        # Enforce shot cooldown — zero tag weight if player fired too recently
        cooldown = self._shot_cooldown(player, second)
        if (
            cooldown > 0.0
            and (second - last_shot_times.get(player.id, -99.0)) < cooldown
        ):
            weights[action_to_weight_index["tag_player"]] = 0
            if sum(weights) == 0:
                weights[action_to_weight_index["hide"]] = 1

        prev_action = getattr(player, "last_chosen_action", "")
        choice = random.choices(choices, weights)[0]
        player.last_chosen_action = choice

        # only maintain hiding status if they are still hiding, moving, or resupplying
        if player.is_hiding and not (
            choice == "hide" or choice == "change_zone" or choice == "resupply_ally"
        ):
            player.is_hiding = False
            player.save()

        plans = []
        if choice == "tag_player":
            target = self._choose_tag_target(
                player, all_alive, second, movement_ctx=movement_ctx
            )
            if target and player.final_shots > 0:
                plans.append({"type": "tag", "actor": player, "target": target})
                # scouts may attempt a second tag immediately if rapid fire active
                if player.role == "scout" and player.special_active_until > second:
                    second_target = self._choose_tag_target(
                        player, all_alive, second, movement_ctx=movement_ctx
                    )
                    if second_target:
                        plans.append(
                            {"type": "tag", "actor": player, "target": second_target}
                        )
        elif choice == "resupply_ally":
            teammate = self._choose_resupply_target(player, all_alive, second)
            if teammate:
                # determine resupply type by role
                if player.role == "ammo":
                    plans.append(
                        {"type": "resupply_ammo", "actor": player, "target": teammate}
                    )
                else:
                    plans.append(
                        {"type": "resupply_lives", "actor": player, "target": teammate}
                    )
        elif choice == "missile_player":
            if player.final_missiles > 0:
                potential_targets = [
                    p
                    for p in all_alive
                    if p.team_color != player.team_color
                    and p.current_zone == player.current_zone
                    and p.final_lives > 0
                    and p.is_taggable_at(second)
                ]
                if potential_targets:
                    tgt = random.choice(potential_targets)
                    plans.append({"type": "missile", "actor": player, "target": tgt})
        elif choice == "change_zone":
            if movement_ctx is not None and player.cell_row is not None:
                goal = self._choose_goal_cell(
                    player, all_alive, movement_ctx, prev_action
                )
                plans.append(
                    {
                        "type": "change_zone",
                        "actor": player,
                        "goal_cell": goal,
                        "movement_ctx": movement_ctx,
                    }
                )
            else:
                zone = self._choose_zone_change(player, all_alive, second)
                plans.append({"type": "change_zone", "actor": player, "zone": zone})
        elif choice == "capture_base":
            if movement_ctx is not None and player.cell_row is not None:
                base_id = _get_base_interaction(player, movement_ctx)
                if base_id is not None:
                    plans.append(
                        {
                            "type": "capture_base",
                            "actor": player,
                            "base_id": base_id,
                            "movement_ctx": movement_ctx,
                        }
                    )
            else:
                base_id = (
                    15
                    if player.current_zone == 1
                    else (14 if player.team_color == "red" else 13)
                )
                plans.append(
                    {"type": "capture_base", "actor": player, "base_id": base_id}
                )
        elif choice == "use_special":
            if (
                player.final_special >= player.special_cost
                and player.special_active_until <= second
                and player.is_active_at(second)
            ):
                plans.append({"type": "use_special", "actor": player})
        elif choice == "hide":
            plans.append({"type": "hide", "actor": player})

        return plans

    def _resolve_tag_attempts(
        self,
        game_round,
        attempts,
        second,
        last_shot_times=None,
        pending_followups=None,
        pending_reactions=None,
    ):
        """Resolve multiple tag attempts simultaneously.

        attempts: list of {'attacker': PlayerRoundState, 'defender': PlayerRoundState}
        """
        if last_shot_times is None:
            last_shot_times = {}
        if pending_followups is None:
            pending_followups = []
        if pending_reactions is None:
            pending_reactions = []
        db_second = int(second)
        # First, determine outcomes without mutating shared state that would affect other attempts in this tick
        outcomes = []
        for a in attempts:
            attacker = a["attacker"]
            defender = a["defender"]
            # Basic checks
            if attacker.final_shots <= 0 or defender.final_lives <= 0:
                outcomes.append(
                    {"attacker": attacker, "defender": defender, "result": "invalid"}
                )
                continue
            if defender.is_hiding and random.random() > 0.5:
                outcomes.append(
                    {"attacker": attacker, "defender": defender, "result": "miss_hid"}
                )
                continue

            base_accuracy = 70
            accuracy = attacker.player.accuracy
            evasion = defender.player.survival
            hit_chance = max(10, min(95, base_accuracy + accuracy - evasion))
            rolled = random.randint(1, 100)
            hit = rolled < hit_chance
            outcomes.append(
                {
                    "attacker": attacker,
                    "defender": defender,
                    "result": "hit" if hit else "miss",
                    "rolled": rolled,
                    "hit_chance": hit_chance,
                }
            )

        # Apply outcomes: decrement shots for attackers, apply damage to defenders and create events
        for o in outcomes:
            attacker = o["attacker"]
            defender = o["defender"]

            if o["result"] == "miss_hid":
                if attacker.role != "ammo":
                    attacker.final_shots -= 1
                attacker.shots_missed += 1
                last_shot_times[attacker.id] = second
                attacker.save()
                GameEvent.objects.create(
                    game_round=game_round,
                    timestamp=db_second,
                    event_type="miss",
                    actor=attacker.player,
                    target=defender.player,
                    points_awarded=0,
                    description=f"{attacker.player.name} misses {defender.player.name}",
                    metadata={
                        "actor_role": attacker.role,
                        "actor_points": attacker.points_scored,
                        "actor_lives": attacker.final_lives,
                        "actor_shots": attacker.final_shots,
                        "target_role": defender.role,
                        "target_points": defender.points_scored,
                        "target_lives": defender.final_lives,
                        "target_shots": defender.final_shots,
                        "rolled_hit_pct": o.get("rolled", 0),
                    },
                )
                continue

            if o["result"] == "invalid":
                continue

            # Apply hit or miss
            if o["result"] == "hit":

                atk_key = attacker.get_tag_id
                def_key = defender.get_tag_id
                if attacker.specific_tags is None:
                    attacker.specific_tags = {}
                if defender.specific_tags is None:
                    defender.specific_tags = {}
                if def_key not in attacker.specific_tags:
                    attacker.specific_tags[def_key] = {
                        "tags": 0,
                        "tagged_by": 0,
                        "missiled": 0,
                        "missiled by": 0,
                    }
                if atk_key not in defender.specific_tags:
                    defender.specific_tags[atk_key] = {
                        "tags": 0,
                        "tagged_by": 0,
                        "missiled": 0,
                        "missiled by": 0,
                    }

                attacker.tags_made += 1
                if attacker.role != "heavy":
                    attacker.final_special += 1
                attacker.points_scored += 100
                attacker.specific_tags[def_key]["tags"] += 1
                attacker.last_tagged_id = def_key
                last_shot_times[attacker.id] = second
                if defender.role == "medic":
                    attacker.final_medic_hits += 1

                defender.specific_tags[atk_key]["tagged_by"] += 1

                logger.debug(
                    "%s - %s: %s %s tags %s %s atk ammo: %s def shd/lv: %s/%s",
                    db_second,
                    "attempt tag",
                    attacker.team_color,
                    attacker.role,
                    defender.team_color,
                    defender.role,
                    attacker.final_shots,
                    defender.shields,
                    defender.final_lives,
                )
                GameEvent.objects.create(
                    game_round=game_round,
                    timestamp=db_second,
                    event_type="tag",
                    actor=attacker.player,
                    target=defender.player,
                    points_awarded=100,
                    description=f"{attacker.player.name} zaps {defender.player.name}",
                    metadata={
                        "actor_role": attacker.role,
                        "actor_points": attacker.points_scored,
                        "actor_lives": attacker.final_lives,
                        "actor_shots": attacker.final_shots,
                        "actor_special": attacker.final_special,
                        "actor_last_tag_id": attacker.last_tagged_id,
                        "target_role": defender.role,
                        "target_points": defender.points_scored,
                        "target_active": defender.is_active_at(db_second),
                        "target_taggable": defender.is_taggable_at(db_second),
                        "target_id": defender.get_tag_id,
                        "target_lives": defender.final_lives,
                        "target_shields": defender.shields,
                        "target_shots": defender.final_shots,
                        "rolled_hit_pct": o.get("rolled", 0),
                    },
                )
                if not defender.is_active_at(db_second) and defender.is_taggable_at(
                    db_second
                ):
                    defender.times_tagged_in_reset_window += 1
                defender.shields = max(0, defender.shields - attacker.shot_power)
                o["downed"] = defender.shields == 0
                if defender.shields == 0:
                    # nuke cancel check
                    if (
                        defender.role == "commander"
                        and defender.special_active_until > db_second
                    ):
                        if attacker.team_color != defender.team_color:
                            attacker.enemy_nuke_cancels += 1
                        else:
                            attacker.ally_nuke_cancels += 1
                        defender.own_specials_cancelled += 1
                        defender.special_active_until = 0
                        defender.save()
                        GameEvent.objects.create(
                            game_round=game_round,
                            timestamp=db_second,
                            event_type="special",
                            actor=attacker.player,
                            target=defender.player,
                            points_awarded=0,
                            description=f"{attacker.player.name} cancels {defender.player.name}'s nuke",
                            metadata={
                                "canceled_by": "tag",
                                "actor_role": attacker.role,
                                "actor_enemy_nuke_cancels": attacker.enemy_nuke_cancels,
                                "actor_ally_nuke_cancels": attacker.ally_nuke_cancels,
                                "target_role": defender.role,
                                "target_own_specials_cancelled": defender.own_specials_cancelled,
                            },
                        )
                        attacker.save()
                    defender.final_lives -= min(1, defender.final_lives)
                    defender.last_downed_time = db_second
                    defender.shields = defender.max_shields
                    GameEvent.objects.create(
                        game_round=game_round,
                        timestamp=db_second,
                        event_type="player_downed",
                        actor=attacker.player,
                        target=defender.player,
                        points_awarded=0,
                        description=f"{defender.player.name} downed by {attacker.player.name} (tag)",
                        metadata={
                            "cause": "tag",
                            "actor_role": attacker.role,
                            "target_role": defender.role,
                            "target_lives": defender.final_lives,
                        },
                    )
                    if defender.final_lives <= 0:
                        defender.was_eliminated_at = db_second
                        GameEvent.objects.create(
                            game_round=game_round,
                            timestamp=db_second,
                            event_type="elimination",
                            actor=attacker.player,
                            target=defender.player,
                            points_awarded=0,
                            description=f"{defender.player.name} is eliminated by {attacker.player.name}",
                            metadata={
                                "elimination_action": "tag",
                                "actor_role": attacker.role,
                                "target_role": defender.role,
                                "target_lives": defender.final_lives,
                            },
                        )

                defender.times_tagged += 1
                defender.points_scored -= 20

                attacker.save()
                defender.save()

            else:
                attacker.shots_missed += 1
                last_shot_times[attacker.id] = second
                attacker.save()
                GameEvent.objects.create(
                    game_round=game_round,
                    timestamp=db_second,
                    event_type="miss",
                    actor=attacker.player,
                    target=defender.player,
                    points_awarded=0,
                    description=f"{attacker.player.name} misses {defender.player.name}",
                    metadata={
                        "actor_role": attacker.role,
                        "actor_points": attacker.points_scored,
                        "actor_lives": attacker.final_lives,
                        "actor_shots": attacker.final_shots,
                        "target_role": defender.role,
                        "target_points": defender.points_scored,
                        "target_lives": defender.final_lives,
                        "target_shots": defender.final_shots,
                        "rolled_hit_pct": o.get("rolled", 0),
                    },
                )

        # Reactions: schedule via pending_reactions so they fire after the shot cooldown.
        for o in outcomes:
            if o["result"] not in ("hit", "miss"):
                continue
            r_reactor = o["defender"]
            r_target = o["attacker"]
            if not r_reactor.is_active_at(db_second) or r_reactor.final_lives <= 0:
                continue
            if r_reactor.final_shots <= 0 and r_reactor.role != "ammo":
                continue
            if r_target.final_lives <= 0:
                continue
            if r_reactor.player.player_awareness >= random.randint(0, 100):
                cooldown = self._shot_cooldown(r_reactor, second)
                pending_reactions.append((second + cooldown, r_reactor, r_target))

        # Follow-up tags: schedule via pending_followups so they fire after the shot cooldown.
        # A hit that downs the defender (shields → 0) never generates a follow-up.
        for o in outcomes:
            if o["result"] != "hit" or o.get("downed", False):
                continue
            if o["defender"].final_lives <= 0:
                continue
            if o["attacker"].final_shots <= 0 and o["attacker"].role != "ammo":
                continue
            if o["defender"].player.player_awareness < random.randint(0, 100):
                cooldown = self._shot_cooldown(o["attacker"], second)
                pending_followups.append(
                    (second + cooldown, o["attacker"], o["defender"], 1)
                )

    def _choose_goal_cell(
        self, player, all_alive, movement_ctx, intended_action: str = ""
    ):
        return choose_goal_cell(
            player,
            all_alive,
            movement_ctx["spawn_cells"],
            movement_ctx,
            intended_action,
        )

    def _move_to_cell(self, player, second, goal_cell, movement_ctx):
        if goal_cell is None:
            return
        adj = movement_ctx["adj"]
        zone_data = movement_ctx["zone_data"]
        current = (player.cell_row, player.cell_col)
        if current == goal_cell or current not in adj:
            return
        next_cell = astar_next_step(current, goal_cell, adj)
        if next_cell == current:
            return
        player.cell_row, player.cell_col = next_cell
        player.zone_fallback = self._zone_from_cell(
            next_cell[0], next_cell[1], movement_ctx["spawn_cells"]
        )
        player.save(update_fields=["cell_row", "cell_col", "zone_fallback"])
        GameEvent.objects.create(
            game_round=player.game_round,
            timestamp=second,
            event_type="movement",
            actor=player.player,
            target=None,
            points_awarded=0,
            description=f"{player.player.name} moves to cell ({next_cell[0]}, {next_cell[1]})",
            metadata={
                "actor_role": player.role,
                "cell_row": next_cell[0],
                "cell_col": next_cell[1],
                "new_zone": player.current_zone,
            },
        )

    def _change_zone(self, player, second, towards=None):
        if player.zone_fallback == 1:
            # 50/50 chance to go to either adjacent zone
            if towards in [0, 2]:
                player.zone_fallback = towards
            else:
                player.zone_fallback = random.choice([0, 2])
        else:
            player.zone_fallback = 1
        player.save()
        GameEvent.objects.create(
            game_round=player.game_round,
            timestamp=second,
            event_type="movement",
            actor=player.player,
            target=None,
            points_awarded=0,
            description=f"{player.player.name} moves to zone {player.current_zone}",
            metadata={
                "actor_role": player.role,
                "new_zone": player.current_zone,
            },
        )

    def _choose_tag_target(self, player, all_alive, second, movement_ctx=None):
        enemies = [
            p
            for p in all_alive
            if p.team_color != player.team_color
            and p.final_lives > 0
            and (
                p.is_active_at(second)
                or (p.is_taggable_at(second) and player.last_tagged_id != p.get_tag_id)
            )
        ]
        potential_targets = _get_los_targets(player, enemies, movement_ctx)
        if potential_targets and player.final_shots > 0:
            weights = {
                "commander": 5,
                "heavy": 8,
                "scout": 3,
                "medic": 1,
                "ammo": 3,
            }
            target_weights = []
            for target in potential_targets:
                active_weighting = 10 if target.is_active_at(second) else 1
                target_weights.append(weights.get(target.role, 1) + active_weighting)
            return random.choices(potential_targets, target_weights)[0]
        return None

    def _choose_resupply_target(self, player, all_alive, second):
        potential_teammates = [
            p
            for p in all_alive
            if p.team_color == player.team_color
            and p.current_zone == player.current_zone
            and p != player
            and p.final_lives > 0
            and p.is_resupplyable_at(second)
        ]
        if potential_teammates:
            # TODO: weight based on role and resources needed
            resup_weights = {
                "commander": 5,
                "heavy": 8,
                "scout": 3,
                "medic": 1,
                "ammo": 6,
            }
            teammate_weights = []
            all_full = True
            # prioritize based on a combination of role and % full
            for teammate in potential_teammates:
                # prioritize low allies
                if player.role == "ammo":
                    resource_weighting = (
                        teammate.max_shots - teammate.final_shots
                    ) * 10
                    if resource_weighting > 0:
                        all_full = False
                else:
                    resource_weighting = (
                        teammate.max_lives - teammate.final_lives
                    ) * 10
                    if resource_weighting > 0:
                        all_full = False
                teammate_weights.append(
                    resup_weights.get(teammate.role, 1) * resource_weighting
                )
            if not all_full:
                teammate = random.choices(potential_teammates, teammate_weights)[0]
                return teammate
            else:
                # all allies in area are full on resources do nothing for now
                # TODO: if ammo they should request resup or go attack if full, if medic then hide or go attack depending on game time
                return None
        else:
            return None

    def _choose_zone_change(self, player, all_alive, second):
        # TODO: change zones differently based on role,
        # heavies want to be with medic or ammo,
        # commanders want to be with enemies,
        # scouts want to be with enemies,
        # medics and ammos want to be with each other
        # if lives low go find medic
        lives_critical = player.max_lives * 0.3
        shots_critical = player.max_shots * 0.3
        if player.final_lives <= lives_critical and player.role != "medic":
            medic = [
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p != player
                and p.role == "medic"
            ]
            # if medic alive go find them
            if medic and player.current_zone != medic[0].current_zone:
                return medic[0].current_zone
            else:
                return None
        # if shots low go find ammo
        elif player.final_shots <= shots_critical:
            ammo = [
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p != player
                and p.role == "ammo"
            ]
            # if ammo alive go find them
            if ammo and player.current_zone != ammo[0].current_zone:
                return ammo[0].current_zone
            else:
                return None
        if player.role == "heavy":
            # if heavy and not with medic and medic alive go find medic
            # if heavy and not with ammo and medic dead go find ammo
            return None
        elif player.role == "commander":
            # if commander and no enemies in zone, go find enemies (specifically medic)
            return None
        elif player.role == "scout":
            # if scout and no enemies in zone, go find enemies.  or go find commander
            return None
        elif player.role == "ammo":
            # if medic alive go find medic
            # if no 3 hit in zone, go find them
            return None
        # assume medic
        else:
            #
            return None

    def _attempt_resupply(self, tagger, teammate, second):
        """Simulate a resupply action"""
        # determine the role of the tagger and teammate
        ammo_resupply_chart = {
            "commander": 5,
            "heavy": 5,
            "scout": 10,
            "medic": 5,
        }
        medic_resupply_chart = {
            "commander": 4,
            "heavy": 3,
            "scout": 5,
            "ammo": 3,
        }
        logger.debug(
            "%s - %s: %s to %s, resupplyable: %s, %s/%s shots, %s/%s lives",
            second,
            "attempt resup",
            tagger.role,
            teammate.role,
            teammate.is_resupplyable_at(second),
            teammate.final_shots,
            teammate.max_shots,
            teammate.final_lives,
            teammate.max_lives,
        )
        if tagger.role == "ammo" and teammate.is_resupplyable_at(second):
            resupply_amount = ammo_resupply_chart[teammate.role]
            # only resupply to cap
            before_shots = teammate.final_shots
            teammate.final_shots = min(
                teammate.final_shots + resupply_amount, teammate.max_shots
            )
            shots_resupplied = teammate.final_shots - before_shots
            if teammate.final_shots + resupply_amount > teammate.max_shots:
                teammate.final_shots = teammate.max_shots
            else:
                teammate.final_shots += resupply_amount
            teammate.last_downed_time = second
            teammate.shields = teammate.max_shields
            # if scout then end their special
            if teammate.role == "scout" and teammate.special_active_until > second:
                teammate.special_active_until = second
            # resupply nuke cancel code
            if teammate.role == "commander" and teammate.special_active_until > second:
                tagger.ally_nuke_cancels += 1
                teammate.own_specials_cancelled += 1
                teammate.save()
                GameEvent.objects.create(
                    game_round=tagger.game_round,
                    timestamp=second,
                    event_type="special",
                    actor=tagger.player,
                    target=teammate.player,
                    points_awarded=0,
                    description=f"{tagger.player.name} cancels {teammate.player.name}'s nuke",
                    metadata={
                        "canceled_by": "ammo resupply",
                        "actor_role": tagger.role,
                        "actor_enemy_nuke_cancels": tagger.enemy_nuke_cancels,
                        "actor_ally_nuke_cancels": tagger.ally_nuke_cancels,
                        "target_role": teammate.role,
                        "target_own_specials_cancelled": teammate.own_specials_cancelled,
                    },
                )
            tagger.resupplies_given += 1
            tagger.save()
            teammate.save()
            # create game event for resupply
            GameEvent.objects.create(
                game_round=tagger.game_round,
                timestamp=second,
                event_type="resupply_ammo",
                actor=tagger.player,
                target=teammate.player,
                points_awarded=0,
                description=f"{tagger.player.name} resupplies {teammate.player.name} with {resupply_amount} shots",
                metadata={
                    "actor_role": tagger.role,
                    "actor_points": tagger.points_scored,
                    "actor_lives": tagger.final_lives,
                    "actor_shots": tagger.final_shots,
                    "target_role": teammate.role,
                    "target_points": teammate.points_scored,
                    "target_lives": teammate.final_lives,
                    "target_shots": teammate.final_shots,
                    "target_shots_resupplied": shots_resupplied,
                },
            )
            return
        elif (
            tagger.role == "medic"
            and tagger.final_shots > 0
            and teammate.is_resupplyable_at(second)
        ):
            resupply_amount = medic_resupply_chart[teammate.role]
            # only resupply to cap
            before_lives = teammate.final_lives
            teammate.final_lives = min(
                teammate.final_lives + resupply_amount, teammate.max_lives
            )
            lives_resupplied = teammate.final_lives - before_lives
            teammate.last_downed_time = second
            teammate.shields = teammate.max_shields
            # if scout then end their special
            if teammate.role == "scout" and teammate.special_active_until > second:
                teammate.special_active_until = second
            # resupply nuke cancel code
            if teammate.role == "commander" and teammate.special_active_until > second:
                tagger.ally_nuke_cancels += 1
                teammate.own_specials_cancelled += 1
                teammate.save()
                GameEvent.objects.create(
                    game_round=tagger.game_round,
                    timestamp=second,
                    event_type="special",
                    actor=tagger.player,
                    target=teammate.player,
                    points_awarded=0,
                    description=f"{tagger.player.name} cancels {teammate.player.name}'s nuke",
                    metadata={
                        "canceled_by": "medic resupply",
                        "actor_role": tagger.role,
                        "actor_enemy_nuke_cancels": tagger.enemy_nuke_cancels,
                        "actor_ally_nuke_cancels": tagger.ally_nuke_cancels,
                        "target_role": teammate.role,
                        "target_own_specials_cancelled": teammate.own_specials_cancelled,
                    },
                )
            tagger.resupplies_given += 1
            tagger.save()
            teammate.save()
            # create game event for resupply
            GameEvent.objects.create(
                game_round=tagger.game_round,
                timestamp=second,
                event_type="resupply_lives",
                actor=tagger.player,
                target=teammate.player,
                points_awarded=0,
                description=f"{tagger.player.name} heals {teammate.player.name} for {resupply_amount} lives",
                metadata={
                    "actor_role": tagger.role,
                    "actor_points": tagger.points_scored,
                    "actor_lives": tagger.final_lives,
                    "actor_shots": tagger.final_shots,
                    "target_role": teammate.role,
                    "target_points": teammate.points_scored,
                    "target_lives": teammate.final_lives,
                    "target_shots": teammate.final_shots,
                    "target_lives_resupplied": lives_resupplied,
                },
            )
            return
        return

    def _start_missile_lock(self, attacker, defender, second):
        """Simulate starting to missile an opponent"""
        # check that attacker is active and defender is targetable
        # roll if defender missile dodges/runs away
        # if not then perform complete missile with a 1-1.5 second delay
        if (
            attacker.is_active_at(second)
            and defender.is_taggable_at(seconds_into_round=second)
            and attacker.final_missiles > 0
            and not defender.is_hiding
        ):
            # TODO: sometimes expend missile when dodged, sometimes not, based on player stats
            # roll if defender dodges
            dodge_chance = 0.45  # base 45% chance to dodge
            # TODO: dodging should be based on player stats

            if random.random() < dodge_chance:
                # Defender dodges the missile
                # create game event for dodging missile
                GameEvent.objects.create(
                    game_round=attacker.game_round,
                    timestamp=second,
                    event_type="missile_dodge",
                    actor=defender.player,
                    target=attacker.player,
                    points_awarded=0,
                    description=f"{defender.player.name} dodges missile from {attacker.player.name}",
                    metadata={
                        "actor_role": attacker.role,
                        "actor_points": attacker.points_scored,
                        "actor_lives": attacker.final_lives,
                        "actor_shots": attacker.final_shots,
                        "actor_missiles": attacker.final_missiles,
                        "target_role": defender.role,
                        "target_points": defender.points_scored,
                        "target_lives": defender.final_lives,
                        "target_shots": defender.final_shots,
                    },
                )
                return

            # Defender does not dodge, schedule missile completion
            delay = random.randint(1, 2)  # 1-2 second delay
            # return a tuple indicating the scheduled missile completion (complete_time, attacker, defender)
            return (second + delay, attacker, defender)

        return

    def _complete_missile(self, attacker, defender, second):
        """Simulate finishing missle on opponent"""
        if attacker.is_active_at(second) and defender.is_taggable_at(second):
            # normalize role checks (roles are stored lowercase elsewhere)
            if not defender.is_active_at(second) and defender.is_taggable_at(second):
                defender.times_tagged_in_reset_window += 1
            defender.shields = defender.max_shields  # reset shields on missile hit
            defender.points_scored -= 100
            # don't go below 0 lives
            defender.final_lives -= min(defender.final_lives, 2)
            if defender.final_lives <= 0:
                defender.was_eliminated_at = second
                logger.debug(
                    "%s - %s: Player eliminated: %s by %s",
                    second,
                    "complete msl",
                    defender.player.name,
                    attacker.player.name,
                )
                GameEvent.objects.create(
                    game_round=attacker.game_round,
                    timestamp=second,
                    event_type="elimination",
                    actor=attacker.player,
                    target=defender.player,
                    points_awarded=0,
                    description=f"{defender.player.name} is eliminated by {attacker.player.name}",
                    metadata={
                        "elimination_action": "missile",
                        "target_role:": defender.role,
                        "target_lives": defender.final_lives,
                    },
                )
            defender.last_downed_time = second  # set downed time for respawn logic
            GameEvent.objects.create(
                game_round=attacker.game_round,
                timestamp=second,
                event_type="player_downed",
                actor=attacker.player,
                target=defender.player,
                points_awarded=0,
                description=f"{defender.player.name} downed by {attacker.player.name} (missile)",
                metadata={
                    "cause": "missile",
                    "actor_role": attacker.role,
                    "target_role": defender.role,
                    "target_lives": defender.final_lives,
                },
            )
            defender.times_missiled += 1
            # Ensure keys exist for missile bookkeeping
            atk_key = attacker.get_tag_id
            def_key = defender.get_tag_id
            if attacker.specific_tags is None:
                attacker.specific_tags = {}
            if defender.specific_tags is None:
                defender.specific_tags = {}
            if atk_key not in defender.specific_tags:
                defender.specific_tags[atk_key] = {
                    "tags": 0,
                    "tagged_by": 0,
                    "missiled": 0,
                    "missiled by": 0,
                }
            if def_key not in attacker.specific_tags:
                attacker.specific_tags[def_key] = {
                    "tags": 0,
                    "tagged_by": 0,
                    "missiled": 0,
                    "missiled by": 0,
                }

            defender.specific_tags[atk_key]["missiled by"] += 1

            attacker.last_tagged_id = defender.get_tag_id
            attacker.specific_tags[def_key]["missiled"] += 1
            attacker.points_scored += 500
            attacker.final_missiles -= 1
            attacker.missiles_landed += 1
            # heavies don't get specials
            if attacker.role != "heavy":
                attacker.final_special += 2
            if str(defender.role).lower() == "medic":
                attacker.final_medic_hits += 2

            defender.save()
            attacker.save()

            # create game event for attacker missiling defender
            GameEvent.objects.create(
                game_round=attacker.game_round,
                timestamp=second,
                event_type="missile_hit",
                actor=attacker.player,
                target=defender.player,
                points_awarded=500,
                description=f"{attacker.player.name} hits {defender.player.name} with a missile",
                metadata={
                    "actor_role": attacker.role,
                    "actor_points": attacker.points_scored,
                    "actor_lives": attacker.final_lives,
                    "actor_shots": attacker.final_shots,
                    "actor_missiles": attacker.final_missiles,
                    "actor_special": attacker.final_special,
                    "target_role": defender.role,
                    "target_points": defender.points_scored,
                    "target_lives": defender.final_lives,
                    "target_shots": defender.final_shots,
                    "target_shields": defender.shields,
                },
            )
            logger.debug(
                "%s - %s: missile hit completed a: %s d: %s",
                second,
                "complete msl",
                attacker.role,
                defender.role,
            )

    def _use_special(self, player_state, second):
        """Simulate using a special ability"""
        # if player has enough special points, is alive and is active, expend special points and apply effect
        logger.debug(
            "%s - %s: %s at %s, %s/%s special, active until %s, succeds: %s",
            second,
            "use special",
            player_state.player.name,
            second,
            player_state.final_special,
            player_state.special_cost,
            player_state.special_active_until,
            player_state.can_use_special
            and player_state.final_lives > 0
            and player_state.is_active_at(second),
        )
        if (
            player_state.can_use_special
            and player_state.final_lives > 0
            and player_state.is_active_at(second)
        ):
            if player_state.role == "commander":
                player_state.final_special -= player_state.special_cost
                player_state.specials_used += 1
                countdown = random.randint(4, 7)
                player_state.special_active_until = second + countdown
                player_state.save()
                GameEvent.objects.create(
                    game_round=player_state.game_round,
                    timestamp=second,
                    event_type="special",
                    actor=player_state.player,
                    points_awarded=0,
                    description=f"{player_state.player.name} activates Nuke special",
                    metadata={
                        "actor_role": player_state.role,
                        "special_active_until": player_state.special_active_until,
                        "special_points": player_state.final_special,
                        "event_subtype": "nuke_armed",
                    },
                )
                return ("nuke", second + countdown, player_state)
            elif player_state.role == "scout":
                # remove special points, set special active until to 900 (lasts whole round)
                player_state.final_special -= player_state.special_cost
                player_state.specials_used += 1
                player_state.special_active_until = 900
                player_state.save()
                GameEvent.objects.create(
                    game_round=player_state.game_round,
                    timestamp=second,
                    event_type="special",
                    actor=player_state.player,
                    points_awarded=0,
                    description=f"{player_state.player.name} activates rapid fire special",
                    metadata={
                        "actor_role": player_state.role,
                        "special_active_until": player_state.special_active_until,
                        "special_points": player_state.final_special,
                    },
                )
            elif player_state.role == "medic":
                # remove special points
                # find all teammates active at second and add lives to each based on role
                player_state.final_special -= player_state.special_cost
                player_state.specials_used += 1
                player_state.save()
                teammates = PlayerRoundState.objects.filter(
                    game_round=player_state.game_round,
                    team_color=player_state.team_color,
                    final_lives__gt=0,
                )
                teammates = [mate for mate in teammates if mate.is_active_at(second)]
                medic_heal_chart = {
                    "commander": 4,
                    "heavy": 3,
                    "scout": 5,
                    "ammo": 2,
                    "medic": 0,
                }
                total_healed = 0
                for mate in teammates:
                    heal_amount = medic_heal_chart[mate.role]
                    if mate.final_lives + heal_amount > mate.max_lives:
                        total_healed += mate.max_lives - mate.final_lives
                        mate.final_lives = mate.max_lives
                    else:
                        total_healed += heal_amount
                        mate.final_lives += heal_amount
                    mate.save()
                GameEvent.objects.create(
                    game_round=player_state.game_round,
                    timestamp=second,
                    event_type="special",
                    actor=player_state.player,
                    points_awarded=0,
                    description=f"{player_state.player.name} resupplies team",
                    metadata={
                        "actor_role": player_state.role,
                        "special_points": player_state.final_special,
                        "teammates_resupplied": len(teammates),
                        "lives_resupplied": total_healed,
                    },
                )
            elif player_state.role == "ammo":
                # remove special points
                # find all teammates active at second and add shots to each based on role
                player_state.final_special -= player_state.special_cost
                player_state.specials_used += 1
                player_state.save()
                teammates = PlayerRoundState.objects.filter(
                    game_round=player_state.game_round,
                    team_color=player_state.team_color,
                    final_lives__gt=0,
                )
                teammates = [mate for mate in teammates if mate.is_active_at(second)]
                ammo_resupply_chart = {
                    "commander": 5,
                    "heavy": 5,
                    "scout": 10,
                    "medic": 5,
                    "ammo": 0,
                }
                total_ammo = 0
                for mate in teammates:
                    resupply_amount = ammo_resupply_chart[mate.role]
                    if mate.final_shots + resupply_amount > mate.max_shots:
                        total_ammo += mate.max_shots - mate.final_shots
                        mate.final_shots = mate.max_shots
                    else:
                        total_ammo += resupply_amount
                        mate.final_shots += resupply_amount
                    mate.save()
                GameEvent.objects.create(
                    game_round=player_state.game_round,
                    timestamp=second,
                    event_type="special",
                    actor=player_state.player,
                    points_awarded=0,
                    description=f"{player_state.player.name} resupplies team",
                    metadata={
                        "actor_role": player_state.role,
                        "special_points": player_state.final_special,
                        "teammates_resupplied": len(teammates),
                        "shots_resupplied": total_ammo,
                    },
                )

    def _complete_nuke(self, player_state, second):
        """Simulate completing a nuke special ability"""
        # check if player is active and alive
        # find all opposing players, subtract 3 lives from each and set their last_downed_time to second
        # award 500 points to player_state
        # create game event for nuke
        if player_state.is_active_at(second) and player_state.final_lives > 0:
            player_state.points_scored += 500
            player_state.save()

            opposing_players = PlayerRoundState.objects.filter(
                game_round=player_state.game_round,
                team_color="blue" if player_state.team_color == "red" else "red",
                final_lives__gt=0,
            )
            lives_removed_from_nuke = 0

            # check for lives removed, medic lives removed and nuke cancels
            for opponent in opposing_players:
                lives_removed_from_nuke += min(opponent.final_lives, 3)
                if opponent.role == "medic":
                    player_state.medic_lives_removed_from_nuke += min(
                        opponent.final_lives, 3
                    )
                elif (
                    opponent.role == "commander"
                    and opponent.special_active_until > second
                ):
                    player_state.enemy_nuke_cancels += 1
                    opponent.own_specials_cancelled += 1
                    opponent.save()
                    GameEvent.objects.create(
                        game_round=player_state.game_round,
                        timestamp=second,
                        event_type="special",
                        actor=player_state.player,
                        target=opponent.player,
                        points_awarded=0,
                        description=f"{player_state.player.name} cancels {opponent.player.name}'s nuke",
                        metadata={
                            "canceled by": "nuke",
                            "actor_role": player_state.role,
                            "actor_enemy_nuke_cancels": player_state.enemy_nuke_cancels,
                            "actor_ally_nuke_cancels": player_state.ally_nuke_cancels,
                            "target_role": opponent.role,
                            "target_own_specials_cancelled": opponent.own_specials_cancelled,
                        },
                    )
                player_state.save()
            GameEvent.objects.create(
                game_round=player_state.game_round,
                timestamp=second,
                event_type="special",
                actor=player_state.player,
                points_awarded=500,
                description=f"{player_state.player.name} detonates Nuke",
                metadata={
                    "actor_role": player_state.role,
                    "special_points": player_state.final_special,
                    "opponents_affected": opposing_players.count(),
                    "lives_taken": lives_removed_from_nuke,
                },
            )

            # Apply damage to each opponent
            for opponent in opposing_players:
                if not opponent.is_active_at(second) and opponent.is_taggable_at(
                    second
                ):
                    opponent.times_tagged_in_reset_window += 1
                lives_taken = min(opponent.final_lives, 3)
                opponent.lives_lost_to_nukes += lives_taken
                opponent.final_lives -= lives_taken
                opponent.last_downed_time = second
                opponent.shields = opponent.max_shields
                GameEvent.objects.create(
                    game_round=player_state.game_round,
                    timestamp=second,
                    event_type="player_downed",
                    actor=player_state.player,
                    target=opponent.player,
                    points_awarded=0,
                    description=f"{opponent.player.name} downed by {player_state.player.name} (nuke)",
                    metadata={
                        "cause": "nuke",
                        "actor_role": player_state.role,
                        "target_role": opponent.role,
                        "target_lives": opponent.final_lives,
                    },
                )

                # Check for elimination and set was_eliminated_at
                if opponent.final_lives <= 0:
                    opponent.was_eliminated_at = second
                    logger.debug(
                        "%s - %s: Player eliminated: %s by %s",
                        second,
                        "complete nuke",
                        opponent.player.name,
                        player_state.player.name,
                    )
                    GameEvent.objects.create(
                        game_round=player_state.game_round,
                        timestamp=second,
                        event_type="elimination",
                        actor=player_state.player,
                        target=opponent.player,
                        points_awarded=0,
                        description=f"{opponent.player.name} is eliminated by {player_state.player.name}",
                        metadata={
                            "elimination_action": "nuke",
                            "actor_role": player_state.role,
                            "target_role": opponent.role,
                            "target_lives": opponent.final_lives,
                        },
                    )

                # Save once with all changes
                opponent.save()

    def _resolve_pending_nuke(self, player_state, complete_time):
        """Detonate or cancel a pending nuke at its scheduled completion time.

        Nuke fires only if the commander is alive AND the fuse was not disarmed by a
        tag-cancel (special_active_until would have been reset to 0 in that case).
        A downed-but-alive commander's nuke still fires — being temporarily down does
        not cancel the nuke per SM5 rules; only elimination or a tag-cancel does.
        """
        nuke_armed = player_state.special_active_until >= complete_time
        if player_state.final_lives > 0 and nuke_armed:
            self._complete_nuke(player_state, complete_time)
            GameEvent.objects.create(
                game_round=player_state.game_round,
                timestamp=complete_time,
                event_type="special",
                actor=player_state.player,
                points_awarded=0,
                description=f"{player_state.player.name}'s nuke detonated",
                metadata={"event_subtype": "nuke_detonated", "actor_role": "commander"},
            )
        elif nuke_armed:
            # Commander was eliminated during the fuse window — nuke cancelled
            player_state.own_specials_cancelled += 1
            player_state.save()
            GameEvent.objects.create(
                game_round=player_state.game_round,
                timestamp=complete_time,
                event_type="special",
                actor=player_state.player,
                points_awarded=0,
                description=f"{player_state.player.name}'s nuke cancelled — eliminated during fuse",
                metadata={
                    "event_subtype": "nuke_cancelled",
                    "cancelled_by": "elimination",
                    "actor_role": "commander",
                },
            )
        # else: nuke was already tag-cancelled (special_active_until == 0); counters already updated

    def _reset_base(self, player_state, base_id, second):
        """Simulate resetting off a base — deferred to a later ticket."""
        return None

    def _capture_base(self, player_state, base_id, second, movement_ctx=None):
        """Simulate capturing a base"""
        # When a map is active, verify the player's cell is in the base's visible_cells.
        if movement_ctx is not None and player_state.cell_row is not None:
            base_sight_data = movement_ctx.get("base_sight_data", {})
            cell_key = f"{player_state.cell_row},{player_state.cell_col}"
            if base_id == 15:
                in_range = any(
                    cell_key in base_sight_data.get(bt, frozenset())
                    for bt in _NEUTRAL_BASE_TYPES
                )
            else:
                opp_type = "blue" if player_state.team_color == "red" else "red"
                in_range = cell_key in base_sight_data.get(opp_type, frozenset())
            if not in_range:
                return False
        if player_state.final_shots >= 3 or player_state.role == "ammo":
            if player_state.role != "ammo":
                player_state.final_shots -= 3
            player_state.last_tagged_id = base_id
            if base_id == 15:
                player_state.neutral_base_destroyed = True
            else:
                player_state.opposing_base_destroyed = True
            player_state.points_scored += 1001  # base capture score
            # heavies don't get specials
            if player_state.role != "heavy":
                player_state.final_special += 5
            player_state.save()
            GameEvent.objects.create(
                game_round=player_state.game_round,
                timestamp=second,
                event_type="base_capture",
                actor=player_state.player,
                points_awarded=1001,
                description=f"{player_state.player.name} captures base {'neutral' if base_id == 15 else 'opposing'}",
                metadata={
                    "actor_role": player_state.role,
                    "base_id": base_id,
                    "shots_remaining": player_state.final_shots,
                    "special_points": player_state.final_special,
                    "points_scored": player_state.points_scored,
                },
            )
            return True
        return False

    def _award_bases(self, player_state, second):
        # if player is alive then award them any bases they didn't capture
        if player_state.final_lives > 0:
            if not player_state.neutral_base_destroyed:
                player_state.points_scored += 1001
                player_state.neutral_base_destroyed = True
                player_state.save()
                base_id = 15
                GameEvent.objects.create(
                    game_round=player_state.game_round,
                    timestamp=second,
                    event_type="base_capture",
                    actor=player_state.player,
                    points_awarded=1001,
                    description=f"{player_state.player.name} awarded base {'neutral' if base_id == 15 else 'opposing'}",
                    metadata={
                        "actor_role": player_state.role,
                        "base_id": base_id,
                        "shots_remaining": player_state.final_shots,
                        "special_points": player_state.final_special,
                        "points_scored": player_state.points_scored,
                    },
                )
            if not player_state.opposing_base_destroyed:
                player_state.points_scored += 1001
                player_state.opposing_base_destroyed = True
                player_state.save()
                base_id = 14 if player_state.team_color == "red" else 13
                GameEvent.objects.create(
                    game_round=player_state.game_round,
                    timestamp=second,
                    event_type="base_capture",
                    actor=player_state.player,
                    points_awarded=1001,
                    description=f"{player_state.player.name} awarded base {'neutral' if base_id == 15 else 'opposing'}",
                    metadata={
                        "actor_role": player_state.role,
                        "base_id": base_id,
                        "shots_remaining": player_state.final_shots,
                        "special_points": player_state.final_special,
                        "points_scored": player_state.points_scored,
                    },
                )

    def _missile_base(self, player_state, base_id, second):
        """Simulate using a missile on a base target"""
        player_state.missiles_fired += 1
        player_state.final_missiles -= 1
        if base_id == "neutral":
            player_state.neutral_base_destroyed = True
        else:
            player_state.opposing_base_destroyed = True
        player_state.points_scored += 1001  # base destroy score
        player_state.final_special += 5
        player_state.save()
        GameEvent.objects.create(
            game_round=player_state.game_round,
            timestamp=second,
            event_type="base_missile",
            actor=player_state.player,
            points_awarded=1001,
            description=f"{player_state.player.name} missiles base {'neutral' if base_id == 'neutral' else 'opposing'}",
            metadata={
                "actor_role": player_state.role,
                "base_id": base_id,
                "missiles_remaining": player_state.final_missiles,
                "special_points": player_state.final_special,
                "points_scored": player_state.points_scored,
            },
        )

    # TODO: need to determine if we are choosing who to reset off of first or in method
    def _attempt_reset(self, player_state, second):
        """Simulate a player resetting after being tagged"""
        return None


class BatchSimulator:
    """Pure in-memory simulator for running N rounds without DB writes.

    Every computation uses PlayerState dataclass objects so there are no
    ORM saves, refreshes, or GameEvent inserts.  One round typically runs
    in ~25 ms instead of ~9 s, making 100-round batches feasible in <3 s.
    """

    ROLE_STARTING_RESOURCES = {
        "commander": {"lives": 15, "shots": 30, "special": 0, "missiles": 5},
        "heavy": {"lives": 10, "shots": 20, "special": 0, "missiles": 5},
        "scout": {"lives": 15, "shots": 30, "special": 0, "missiles": 0},
        "medic": {"lives": 20, "shots": 15, "special": 0, "missiles": 0},
        "ammo": {"lives": 10, "shots": 15, "special": 0, "missiles": 0},
    }

    # Precomputed constants so _plan_action doesn't rebuild them each call
    _ACTION_IDX = {
        "tag_player": 0,
        "change_zone": 1,
        "hide": 2,
        "capture_base": 3,
        "use_special": 4,
        "resupply_ally": 5,
        "missile_player": 6,
    }
    _CHOICES = [
        "tag_player",
        "change_zone",
        "hide",
        "capture_base",
        "use_special",
        "resupply_ally",
        "missile_player",
    ]

    # 0.5-second tick: models real shot speeds (regular=2/s, heavy=1/s).
    TICK = 0.5

    def run(self, team_red, team_blue, n=100, *, arena_map=None):
        """Simulate n rounds and return aggregate statistics.

        Loads team rosters from the DB once upfront, then runs n purely
        in-memory rounds and aggregates the outcomes. Pass arena_map to enable
        cell-aware pathfinding movement; omit for the 3-zone fallback.
        """
        from .sim_helpers.player_state import PlayerState
        from teams.models import ROLE_STATS as _ROLE_STATS

        # Read rosters once — list of (role, Player) tuples
        red_roster = list(team_red.active_roster)
        blue_roster = list(team_blue.active_roster)

        movement_ctx = None
        if arena_map is not None:
            (
                _,
                spawn_cells,
                zone_data,
                sight_data,
                base_sight_data,
                cell_ranking,
                strong_spots,
                wall_meta,
            ) = ResourceBasedSimulator._resolve_map_data(arena_map)
            movement_ctx = ResourceBasedSimulator._build_movement_ctx(
                zone_data,
                spawn_cells,
                sight_data,
                base_sight_data,
                cell_ranking,
                strong_spots,
                wall_meta,
            )

        red_wins = blue_wins = ties = 0
        red_scores, blue_scores = [], []
        red_survivors_list, blue_survivors_list = [], []
        round_seeds = []  # (score_diff, random_state)

        for _ in range(n):
            seed_state = random.getstate()
            result, _, _ = self._simulate_round(
                red_roster, blue_roster, movement_ctx=movement_ctx
            )
            rp, bp = result["red_points"], result["blue_points"]
            round_seeds.append((rp - bp, seed_state))
            if rp > bp:
                red_wins += 1
            elif bp > rp:
                blue_wins += 1
            else:
                ties += 1
            red_scores.append(rp)
            blue_scores.append(bp)
            red_survivors_list.append(result["red_survivors"])
            blue_survivors_list.append(result["blue_survivors"])

        # Pick the 10 most average and 10 most outlier rounds by score diff
        if round_seeds:
            mean_diff = sum(d for d, _ in round_seeds) / n
            ranked = sorted(round_seeds, key=lambda x: abs(x[0] - mean_diff))
            avg_seeds = [s for _, s in ranked[:10]]
            outlier_seeds = [s for _, s in ranked[-10:]]
        else:
            avg_seeds = outlier_seeds = []

        avg = lambda lst: sum(lst) / len(lst) if lst else 0
        return {
            "n": n,
            "red_wins": red_wins,
            "blue_wins": blue_wins,
            "ties": ties,
            "red_win_pct": red_wins / n * 100,
            "blue_win_pct": blue_wins / n * 100,
            "avg_red_score": avg(red_scores),
            "avg_blue_score": avg(blue_scores),
            "avg_red_survivors": avg(red_survivors_list),
            "avg_blue_survivors": avg(blue_survivors_list),
            "red_scores": red_scores,
            "blue_scores": blue_scores,
            "avg_seeds": avg_seeds,
            "outlier_seeds": outlier_seeds,
        }

    # ------------------------------------------------------------------ #
    # Internal round simulation
    # ------------------------------------------------------------------ #

    def _make_players(
        self,
        roster,
        team_color: str,
        spawn_cells: dict[str, tuple[int, int]] | None = None,
        zone_data: list[list[int]] | None = None,
    ):
        from .sim_helpers.player_state import PlayerState
        from teams.models import ROLE_STATS as _ROLE_STATS

        spawn = spawn_cells.get(team_color) if spawn_cells else None
        default_zone = 0 if team_color == "red" else 2
        if spawn is not None and zone_data is not None:
            cell_row: int | None = spawn[0]
            cell_col: int | None = spawn[1]
            starting_zone = ResourceBasedSimulator._zone_from_cell(
                spawn[0], spawn[1], spawn_cells
            )
        else:
            cell_row = None
            cell_col = None
            starting_zone = default_zone

        scout_index = 0
        players = []
        for role, player_model in roster:
            resources = self.ROLE_STARTING_RESOURCES[role]
            if role == "scout":
                scout_index += 1
                tag_id = f"{team_color}_scout_{scout_index}"
            else:
                tag_id = f"{team_color}_{role}"
            state = PlayerState(
                tag_id=tag_id,
                player_id=player_model.id,
                name=player_model.name,
                team_color=team_color,
                role=role,
                accuracy=player_model.accuracy,
                survival=player_model.survival,
                player_awareness=player_model.player_awareness,
                starting_lives=resources["lives"],
                starting_shots=resources["shots"],
                final_lives=resources["lives"],
                final_shots=resources["shots"],
                final_special=resources["special"],
                final_missiles=resources["missiles"],
                shields=_ROLE_STATS[role]["shield"],
                current_zone=starting_zone,
                cell_row=cell_row,
                cell_col=cell_col,
            )
            players.append(state)
        return players

    def _simulate_round(
        self, red_roster, blue_roster, event_log=None, movement_ctx=None
    ):
        """Returns (result_dict, red_players, blue_players).

        When event_log is a list it is populated with event dicts suitable for
        _flush_to_db, enabling exact replay and DB persistence.
        """
        spawn_cells = movement_ctx["spawn_cells"] if movement_ctx else None
        zone_data = movement_ctx["zone_data"] if movement_ctx else None
        red_players = self._make_players(
            red_roster, "red", spawn_cells=spawn_cells, zone_data=zone_data
        )
        blue_players = self._make_players(
            blue_roster, "blue", spawn_cells=spawn_cells, zone_data=zone_data
        )

        pending_missiles = []  # (complete_time, attacker, defender)
        pending_nukes = []  # (complete_time, player_state)
        pending_followups = []  # (fire_at, attacker, defender, chain)
        pending_reactions = []  # (fire_at, attacker, defender)
        eliminated_at = 901

        for _tick in range(int(900 / self.TICK)):
            second = _tick * self.TICK
            # --- process pending missiles ---
            fired = [m for m in pending_missiles if m[0] <= second]
            pending_missiles = [m for m in pending_missiles if m[0] > second]
            for complete_time, attacker, defender in fired:
                if attacker.is_active_at(complete_time) and defender.is_taggable_at(
                    complete_time
                ):
                    self._complete_missile(attacker, defender, complete_time, event_log)

            # --- process pending nukes ---
            fired_n = [n for n in pending_nukes if n[0] <= second]
            pending_nukes = [n for n in pending_nukes if n[0] > second]
            for complete_time, ps in fired_n:
                if ps.is_active_at(complete_time) and ps.final_lives > 0:
                    opposing = blue_players if ps.team_color == "red" else red_players
                    self._complete_nuke(ps, complete_time, opposing, event_log)

            # --- process pending reactions (deferred by shot cooldown) ---
            due_rx = [item for item in pending_reactions if item[0] <= second]
            pending_reactions = [item for item in pending_reactions if item[0] > second]
            for _, r_attacker, r_defender in due_rx:
                if r_attacker.final_lives <= 0 or r_defender.final_lives <= 0:
                    continue
                if not r_attacker.is_active_at(second):
                    continue
                if r_attacker.final_shots <= 0 and r_attacker.role != "ammo":
                    continue
                r_attacker.reaction_shots += 1
                r_attacker.last_shot_time = second
                hit_chance = max(
                    10, min(95, 70 + r_attacker.accuracy - r_defender.survival)
                )
                react_hit = random.randint(1, 100) < hit_chance
                if r_attacker.role != "ammo":
                    r_attacker.final_shots = max(0, r_attacker.final_shots - 1)
                if react_hit:
                    r_attacker.tags_made += 1
                    if r_attacker.role != "heavy":
                        r_attacker.final_special = min(
                            r_attacker.max_special, r_attacker.final_special + 1
                        )
                    r_attacker.points_scored += 100
                    r_attacker.last_tagged_id = r_defender.tag_id
                    r_defender.times_tagged += 1
                    r_defender.points_scored -= 20
                    if not r_defender.is_active_at(
                        second
                    ) and r_defender.is_taggable_at(second):
                        r_defender.times_tagged_in_reset_window += 1
                    r_defender.shields = max(
                        0, r_defender.shields - r_attacker.shot_power
                    )
                    if r_defender.shields == 0:
                        r_defender.final_lives = max(0, r_defender.final_lives - 1)
                        r_defender.last_downed_time = second
                        r_defender.shields = r_defender.max_shields
                        if r_defender.final_lives <= 0:
                            r_defender.was_eliminated_at = second
                            if event_log is not None:
                                event_log.append(
                                    {
                                        "event_type": "elimination",
                                        "actor_id": r_attacker.player_id,
                                        "target_id": r_defender.player_id,
                                        "timestamp": second,
                                        "points_awarded": 0,
                                        "description": f"{r_attacker.name} eliminates {r_defender.name} (reaction)",
                                        "metadata": {"elimination_action": "reaction"},
                                    }
                                )
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "tag",
                                "actor_id": r_attacker.player_id,
                                "target_id": r_defender.player_id,
                                "timestamp": second,
                                "points_awarded": 100,
                                "description": f"{r_attacker.name} reacts to {r_defender.name}",
                                "metadata": {
                                    "actor_role": r_attacker.role,
                                    "target_role": r_defender.role,
                                    "is_reaction": True,
                                },
                            }
                        )
                else:
                    r_attacker.shots_missed += 1
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "miss",
                                "actor_id": r_attacker.player_id,
                                "target_id": r_defender.player_id,
                                "timestamp": second,
                                "points_awarded": 0,
                                "description": f"{r_attacker.name} reaction miss on {r_defender.name}",
                                "metadata": {"is_reaction": True},
                            }
                        )

            # --- process pending follow-ups (deferred by shot cooldown) ---
            due_fu = [item for item in pending_followups if item[0] <= second]
            pending_followups = [item for item in pending_followups if item[0] > second]
            for _, fu_attacker, fu_defender, chain in due_fu:
                if fu_attacker.final_lives <= 0 or fu_defender.final_lives <= 0:
                    continue
                if fu_attacker.final_shots <= 0 and fu_attacker.role != "ammo":
                    continue
                fu_attacker.follow_up_shots += 1
                fu_attacker.last_shot_time = second
                hit_chance = max(
                    10, min(95, 70 + fu_attacker.accuracy - fu_defender.survival)
                )
                fu_hit = random.randint(1, 100) < hit_chance
                if fu_attacker.role != "ammo":
                    fu_attacker.final_shots = max(0, fu_attacker.final_shots - 1)
                if fu_hit:
                    fu_attacker.tags_made += 1
                    if fu_attacker.role != "heavy":
                        fu_attacker.final_special = min(
                            fu_attacker.max_special, fu_attacker.final_special + 1
                        )
                    fu_attacker.points_scored += 100
                    fu_attacker.last_tagged_id = fu_defender.tag_id
                    fu_defender.times_tagged += 1
                    fu_defender.points_scored -= 20
                    if not fu_defender.is_active_at(
                        second
                    ) and fu_defender.is_taggable_at(second):
                        fu_defender.times_tagged_in_reset_window += 1
                    fu_defender.shields = max(
                        0, fu_defender.shields - fu_attacker.shot_power
                    )
                    downed = fu_defender.shields == 0
                    if downed:
                        fu_defender.final_lives = max(0, fu_defender.final_lives - 1)
                        fu_defender.last_downed_time = second
                        fu_defender.shields = fu_defender.max_shields
                        if fu_defender.final_lives <= 0:
                            fu_defender.was_eliminated_at = second
                            if event_log is not None:
                                event_log.append(
                                    {
                                        "event_type": "elimination",
                                        "actor_id": fu_attacker.player_id,
                                        "target_id": fu_defender.player_id,
                                        "timestamp": second,
                                        "points_awarded": 0,
                                        "description": f"{fu_attacker.name} eliminates {fu_defender.name} (follow-up)",
                                        "metadata": {
                                            "elimination_action": "follow_up_tag"
                                        },
                                    }
                                )
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "tag",
                                "actor_id": fu_attacker.player_id,
                                "target_id": fu_defender.player_id,
                                "timestamp": second,
                                "points_awarded": 100,
                                "description": f"{fu_attacker.name} follow-up tags {fu_defender.name}",
                                "metadata": {
                                    "actor_role": fu_attacker.role,
                                    "target_role": fu_defender.role,
                                    "is_follow_up": True,
                                    "chain": chain,
                                },
                            }
                        )
                    if not downed and chain < 2 and fu_defender.final_lives > 0:
                        if fu_defender.player_awareness < random.randint(0, 100):
                            cooldown = self._shot_cooldown(fu_attacker, second)
                            if cooldown == 0.0:
                                due_fu.append(
                                    (second, fu_attacker, fu_defender, chain + 1)
                                )
                            else:
                                pending_followups.append(
                                    (
                                        second + cooldown,
                                        fu_attacker,
                                        fu_defender,
                                        chain + 1,
                                    )
                                )
                else:
                    fu_attacker.shots_missed += 1
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "miss",
                                "actor_id": fu_attacker.player_id,
                                "target_id": fu_defender.player_id,
                                "timestamp": second,
                                "points_awarded": 0,
                                "description": f"{fu_attacker.name} follow-up miss on {fu_defender.name}",
                                "metadata": {"is_follow_up": True},
                            }
                        )

            # --- alive players this tick ---
            red_alive = [
                p
                for p in red_players
                if p.final_lives > 0 and p.was_eliminated_at > second
            ]
            blue_alive = [
                p
                for p in blue_players
                if p.final_lives > 0 and p.was_eliminated_at > second
            ]
            all_alive = red_alive + blue_alive

            # accumulate uptime for all living players
            for p in all_alive:
                if not p.is_active_at(second) and not p.is_taggable_at(second):
                    p.seconds_not_targetable += self.TICK
                elif not p.is_active_at(second):
                    p.seconds_reset_window += self.TICK
                else:
                    p.seconds_active += self.TICK

            random.shuffle(all_alive)

            plans = []
            for player in all_alive:
                plans.extend(
                    self._plan_action(
                        player, all_alive, second, movement_ctx=movement_ctx
                    )
                )

            tag_attempts = []
            for plan in plans:
                ptype = plan["type"]
                actor = plan["actor"]
                if ptype in ("resupply_ammo", "resupply_lives"):
                    self._attempt_resupply(actor, plan["target"], second, event_log)
                elif ptype == "change_zone":
                    goal_cell = plan.get("goal_cell")
                    ctx = plan.get("movement_ctx")
                    if goal_cell is not None and ctx is not None:
                        self._move_player_in_memory(actor, goal_cell, ctx)
                    else:
                        self._change_zone(actor, plan.get("zone"))
                elif ptype == "hide":
                    actor.is_hiding = True
                elif ptype == "capture_base":
                    self._capture_base(
                        actor,
                        plan["base_id"],
                        event_log,
                        second,
                        movement_ctx=plan.get("movement_ctx"),
                    )
                elif ptype == "missile":
                    scheduled = self._start_missile_lock(actor, plan["target"], second)
                    if scheduled:
                        pending_missiles.append(scheduled)
                elif ptype == "use_special":
                    scheduled = self._use_special(actor, second, all_alive, event_log)
                    if scheduled and scheduled[0] == "nuke":
                        pending_nukes.append((scheduled[1], scheduled[2]))
                elif ptype == "tag":
                    tag_attempts.append({"attacker": actor, "defender": plan["target"]})

            if tag_attempts:
                self._resolve_tag_attempts(
                    tag_attempts,
                    second,
                    event_log,
                    pending_followups,
                    pending_reactions,
                )

            # --- check for team elimination ---
            red_alive = [p for p in red_players if p.final_lives > 0]
            blue_alive = [p for p in blue_players if p.final_lives > 0]
            if not red_alive or not blue_alive:
                eliminated_at = second
                if not red_alive:
                    for p in blue_alive:
                        self._award_bases(p, event_log, second)
                if not blue_alive:
                    for p in red_alive:
                        self._award_bases(p, event_log, second)
                break

        red_points = sum(p.points_scored for p in red_players)
        blue_points = sum(p.points_scored for p in blue_players)
        red_survivors = sum(1 for p in red_players if p.final_lives > 0)
        blue_survivors = sum(1 for p in blue_players if p.final_lives > 0)
        result = {
            "red_points": red_points,
            "blue_points": blue_points,
            "red_survivors": red_survivors,
            "blue_survivors": blue_survivors,
            "red_eliminated": all(p.final_lives <= 0 for p in red_players),
            "blue_eliminated": all(p.final_lives <= 0 for p in blue_players),
            "eliminated_at": eliminated_at,
        }
        return result, red_players, blue_players

    # ------------------------------------------------------------------ #
    # Action planning — reuses weight functions from weights.py
    # ------------------------------------------------------------------ #

    def _plan_action(self, player, all_alive, second, movement_ctx=None):
        action_to_weight_index = self._ACTION_IDX
        choices = self._CHOICES
        weights = [70, 30, 0, 0, 0, 0, 0]

        if player.role == "medic":
            weights = _get_medic_weights(
                player, action_to_weight_index, weights, all_alive, second
            )
        elif player.role == "ammo":
            weights = _get_ammo_weights(
                player, action_to_weight_index, weights, all_alive, second
            )
        elif player.role == "scout":
            weights = _get_scout_weights(
                player, action_to_weight_index, weights, all_alive, second
            )
        elif player.role == "heavy":
            weights = _get_heavy_weights(
                player, action_to_weight_index, weights, all_alive, second
            )
        elif player.role == "commander":
            weights = _get_commander_weights(
                player, action_to_weight_index, weights, all_alive, second
            )

        # Enforce shot cooldown — zero tag weight if player fired too recently
        cooldown = self._shot_cooldown(player, second)
        if cooldown > 0.0 and (second - player.last_shot_time) < cooldown:
            weights[action_to_weight_index["tag_player"]] = 0
            if sum(weights) == 0:
                weights[action_to_weight_index["hide"]] = 1

        prev_action = player.last_chosen_action
        choice = random.choices(choices, weights)[0]
        player.last_chosen_action = choice

        if player.is_hiding and choice not in ("hide", "change_zone", "resupply_ally"):
            player.is_hiding = False

        plans = []
        if choice == "tag_player":
            target = self._choose_tag_target(
                player, all_alive, second, movement_ctx=movement_ctx
            )
            if target and player.final_shots > 0:
                plans.append({"type": "tag", "actor": player, "target": target})
                if player.role == "scout" and player.special_active_until > second:
                    second_target = self._choose_tag_target(
                        player, all_alive, second, movement_ctx=movement_ctx
                    )
                    if second_target:
                        plans.append(
                            {"type": "tag", "actor": player, "target": second_target}
                        )
        elif choice == "resupply_ally":
            teammate = self._choose_resupply_target(player, all_alive, second)
            if teammate:
                rtype = "resupply_ammo" if player.role == "ammo" else "resupply_lives"
                plans.append({"type": rtype, "actor": player, "target": teammate})
        elif choice == "missile_player":
            if player.final_missiles > 0:
                targets = [
                    p
                    for p in all_alive
                    if p.team_color != player.team_color
                    and p.current_zone == player.current_zone
                    and p.final_lives > 0
                    and p.is_taggable_at(second)
                ]
                if targets:
                    plans.append(
                        {
                            "type": "missile",
                            "actor": player,
                            "target": random.choice(targets),
                        }
                    )
        elif choice == "change_zone":
            if movement_ctx is not None and player.cell_row is not None:
                goal = self._choose_goal_cell_batch(
                    player, all_alive, movement_ctx, prev_action
                )
                plans.append(
                    {
                        "type": "change_zone",
                        "actor": player,
                        "goal_cell": goal,
                        "movement_ctx": movement_ctx,
                    }
                )
            else:
                zone = self._choose_zone_change(player, all_alive)
                plans.append({"type": "change_zone", "actor": player, "zone": zone})
        elif choice == "capture_base":
            if movement_ctx is not None and player.cell_row is not None:
                base_id = _get_base_interaction(player, movement_ctx)
                if base_id is not None:
                    plans.append(
                        {
                            "type": "capture_base",
                            "actor": player,
                            "base_id": base_id,
                            "movement_ctx": movement_ctx,
                        }
                    )
            else:
                base_id = (
                    15
                    if player.current_zone == 1
                    else (14 if player.team_color == "red" else 13)
                )
                plans.append(
                    {"type": "capture_base", "actor": player, "base_id": base_id}
                )
        elif choice == "use_special":
            if (
                player.can_use_special
                and player.final_lives > 0
                and player.is_active_at(second)
            ):
                plans.append({"type": "use_special", "actor": player})
        elif choice == "hide":
            plans.append({"type": "hide", "actor": player})

        return plans

    def _shot_cooldown(self, player, second: float) -> float:
        """Seconds the player must wait between shots.

        Rapid-fire scouts (special active) have no restriction.
        Heavies fire once per second. Everyone else twice per second.
        """
        if player.role == "scout" and player.special_active_until > second:
            return 0.0
        if player.role == "heavy":
            return 1.0
        return 0.5

    # ------------------------------------------------------------------ #
    # Target selection
    # ------------------------------------------------------------------ #

    def _choose_tag_target(self, player, all_alive, second, movement_ctx=None):
        role_weights = {"commander": 5, "heavy": 8, "scout": 3, "medic": 1, "ammo": 3}
        enemies = [
            p
            for p in all_alive
            if p.team_color != player.team_color
            and p.final_lives > 0
            and (
                p.is_active_at(second)
                or (p.is_taggable_at(second) and player.last_tagged_id != p.tag_id)
            )
        ]
        targets = _get_los_targets(player, enemies, movement_ctx)
        if not targets or player.final_shots <= 0:
            return None
        w = [
            (role_weights.get(t.role, 1) + (10 if t.is_active_at(second) else 1))
            for t in targets
        ]
        return random.choices(targets, w)[0]

    def _choose_resupply_target(self, player, all_alive, second):
        role_weights = {"commander": 5, "heavy": 8, "scout": 3, "medic": 1, "ammo": 6}
        teammates = [
            p
            for p in all_alive
            if p.team_color == player.team_color
            and p.current_zone == player.current_zone
            and p is not player
            and p.final_lives > 0
            and p.is_resupplyable_at(second)
        ]
        if not teammates:
            return None
        all_full = True
        tw = []
        for t in teammates:
            if player.role == "ammo":
                deficit = (t.max_shots - t.final_shots) * 10
            else:
                deficit = (t.max_lives - t.final_lives) * 10
            if deficit > 0:
                all_full = False
            tw.append(role_weights.get(t.role, 1) * deficit)
        return None if all_full else random.choices(teammates, tw)[0]

    def _choose_zone_change(self, player, all_alive):
        lives_critical = player.max_lives * 0.3
        shots_critical = player.max_shots * 0.3
        if player.final_lives <= lives_critical and player.role != "medic":
            medics = [
                p
                for p in all_alive
                if p.team_color == player.team_color and p.role == "medic"
            ]
            if medics and player.current_zone != medics[0].current_zone:
                return medics[0].current_zone
        elif player.final_shots <= shots_critical:
            ammos = [
                p
                for p in all_alive
                if p.team_color == player.team_color and p.role == "ammo"
            ]
            if ammos and player.current_zone != ammos[0].current_zone:
                return ammos[0].current_zone
        return None

    def _choose_goal_cell_batch(
        self, player, all_alive, movement_ctx, intended_action: str = ""
    ):
        return choose_goal_cell(
            player,
            all_alive,
            movement_ctx["spawn_cells"],
            movement_ctx,
            intended_action,
        )

    def _move_player_in_memory(self, player, goal_cell, movement_ctx):
        if goal_cell is None or player.cell_row is None:
            return
        adj = movement_ctx["adj"]
        zone_data = movement_ctx["zone_data"]
        current = (player.cell_row, player.cell_col)
        if current == goal_cell or current not in adj:
            return
        next_cell = astar_next_step(current, goal_cell, adj)
        if next_cell == current:
            return
        player.cell_row, player.cell_col = next_cell
        player.current_zone = ResourceBasedSimulator._zone_from_cell(
            next_cell[0], next_cell[1], movement_ctx["spawn_cells"]
        )

    # ------------------------------------------------------------------ #
    # Action resolution (no DB writes)
    # ------------------------------------------------------------------ #

    def _resolve_tag_attempts(
        self,
        attempts,
        second,
        event_log=None,
        pending_followups=None,
        pending_reactions=None,
    ):
        outcomes = []
        for a in attempts:
            attacker, defender = a["attacker"], a["defender"]
            if attacker.final_shots <= 0 or defender.final_lives <= 0:
                outcomes.append(
                    {"attacker": attacker, "defender": defender, "result": "invalid"}
                )
                continue
            if defender.is_hiding and random.random() > 0.5:
                outcomes.append(
                    {"attacker": attacker, "defender": defender, "result": "miss_hid"}
                )
                continue
            hit_chance = max(10, min(95, 70 + attacker.accuracy - defender.survival))
            hit = random.randint(1, 100) < hit_chance
            outcomes.append(
                {
                    "attacker": attacker,
                    "defender": defender,
                    "result": "hit" if hit else "miss",
                }
            )

        for o in outcomes:
            attacker, defender = o["attacker"], o["defender"]
            if o["result"] == "invalid":
                continue
            if o["result"] == "miss_hid":
                if attacker.role != "ammo":
                    attacker.final_shots -= 1
                attacker.shots_missed += 1
                attacker.last_shot_time = second
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "miss",
                            "actor_id": attacker.player_id,
                            "target_id": defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{attacker.name} misses {defender.name} (hiding)",
                            "metadata": {"reason": "hiding"},
                        }
                    )
                continue

            if o["result"] == "hit":
                attacker.tags_made += 1
                if attacker.role != "heavy":
                    attacker.final_special = min(
                        attacker.max_special, attacker.final_special + 1
                    )
                attacker.points_scored += 100
                attacker.last_tagged_id = defender.tag_id
                attacker.final_shots = max(0, attacker.final_shots - 1)
                attacker.last_shot_time = second

                defender.times_tagged += 1
                defender.points_scored -= 20
                defender.shields = max(0, defender.shields - attacker.shot_power)
                o["downed"] = defender.shields == 0

                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "tag",
                            "actor_id": attacker.player_id,
                            "target_id": defender.player_id,
                            "timestamp": second,
                            "points_awarded": 100,
                            "description": f"{attacker.name} tags {defender.name}",
                            "metadata": {
                                "actor_role": attacker.role,
                                "target_role": defender.role,
                                "target_lives": defender.final_lives,
                            },
                        }
                    )

                if not defender.is_active_at(second) and defender.is_taggable_at(
                    second
                ):
                    defender.times_tagged_in_reset_window += 1
                if defender.shields == 0:
                    if (
                        defender.role == "commander"
                        and defender.special_active_until > second
                    ):
                        defender.special_active_until = 0
                    defender.final_lives = max(0, defender.final_lives - 1)
                    defender.last_downed_time = second
                    defender.shields = defender.max_shields
                    if defender.final_lives <= 0:
                        defender.was_eliminated_at = second
                        if event_log is not None:
                            event_log.append(
                                {
                                    "event_type": "elimination",
                                    "actor_id": attacker.player_id,
                                    "target_id": defender.player_id,
                                    "timestamp": second,
                                    "points_awarded": 0,
                                    "description": f"{defender.name} eliminated by {attacker.name}",
                                    "metadata": {"elimination_action": "tag"},
                                }
                            )
            else:
                attacker.final_shots = max(0, attacker.final_shots - 1)
                attacker.shots_missed += 1
                attacker.last_shot_time = second
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "miss",
                            "actor_id": attacker.player_id,
                            "target_id": defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{attacker.name} misses {defender.name}",
                            "metadata": {},
                        }
                    )

        # Reactions: hit or miss may trigger a player_awareness roll.
        # Rapid-fire scouts react this tick; everyone else is scheduled for their next eligible shot.
        immediate_reactions = []
        for o in outcomes:
            if o["result"] not in ("hit", "miss"):
                continue
            r_reactor = o["defender"]
            r_target = o["attacker"]
            if not r_reactor.is_active_at(second) or r_reactor.final_lives <= 0:
                continue
            if r_reactor.final_shots <= 0 and r_reactor.role != "ammo":
                continue
            if r_target.final_lives <= 0:
                continue
            if r_reactor.player_awareness >= random.randint(0, 100):
                cooldown = self._shot_cooldown(r_reactor, second)
                if cooldown == 0.0:
                    immediate_reactions.append(
                        {"attacker": r_reactor, "defender": r_target}
                    )
                elif pending_reactions is not None:
                    pending_reactions.append((second + cooldown, r_reactor, r_target))

        for ra in immediate_reactions:
            r_attacker = ra["attacker"]
            r_defender = ra["defender"]
            if r_defender.final_lives <= 0:
                continue
            hit_chance = max(
                10, min(95, 70 + r_attacker.accuracy - r_defender.survival)
            )
            react_hit = random.randint(1, 100) < hit_chance
            r_attacker.reaction_shots += 1
            r_attacker.last_shot_time = second
            if r_attacker.role != "ammo":
                r_attacker.final_shots = max(0, r_attacker.final_shots - 1)
            if react_hit:
                r_attacker.tags_made += 1
                if r_attacker.role != "heavy":
                    r_attacker.final_special = min(
                        r_attacker.max_special, r_attacker.final_special + 1
                    )
                r_attacker.points_scored += 100
                r_attacker.last_tagged_id = r_defender.tag_id
                r_defender.times_tagged += 1
                r_defender.points_scored -= 20
                if not r_defender.is_active_at(second) and r_defender.is_taggable_at(
                    second
                ):
                    r_defender.times_tagged_in_reset_window += 1
                r_defender.shields = max(0, r_defender.shields - r_attacker.shot_power)
                if r_defender.shields == 0:
                    r_defender.final_lives = max(0, r_defender.final_lives - 1)
                    r_defender.last_downed_time = second
                    r_defender.shields = r_defender.max_shields
                    if r_defender.final_lives <= 0:
                        r_defender.was_eliminated_at = second
                        if event_log is not None:
                            event_log.append(
                                {
                                    "event_type": "elimination",
                                    "actor_id": r_attacker.player_id,
                                    "target_id": r_defender.player_id,
                                    "timestamp": second,
                                    "points_awarded": 0,
                                    "description": f"{r_attacker.name} eliminates {r_defender.name} (reaction)",
                                    "metadata": {"elimination_action": "reaction"},
                                }
                            )
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "tag",
                            "actor_id": r_attacker.player_id,
                            "target_id": r_defender.player_id,
                            "timestamp": second,
                            "points_awarded": 100,
                            "description": f"{r_attacker.name} reacts to {r_defender.name}",
                            "metadata": {
                                "actor_role": r_attacker.role,
                                "target_role": r_defender.role,
                                "is_reaction": True,
                            },
                        }
                    )
            else:
                r_attacker.shots_missed += 1
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "miss",
                            "actor_id": r_attacker.player_id,
                            "target_id": r_defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{r_attacker.name} reaction miss on {r_defender.name}",
                            "metadata": {"is_reaction": True},
                        }
                    )

        # Follow-up tags: if a hit did NOT down the defender (shields still > 0 after
        # the shot), the attacker may fire again. A hit that takes shields to 0 is
        # never eligible — a heavy one-shotting a commander never generates follow-ups.
        # Rapid-fire scouts fire this tick; everyone else is scheduled for their next eligible shot.
        immediate_follow_ups = []
        for o in outcomes:
            if o["result"] != "hit" or o.get("downed", False):
                continue
            if o["defender"].final_lives <= 0:
                continue
            if o["attacker"].final_shots <= 0 and o["attacker"].role != "ammo":
                continue
            if o["defender"].player_awareness < random.randint(0, 100):
                cooldown = self._shot_cooldown(o["attacker"], second)
                if cooldown == 0.0:
                    immediate_follow_ups.append(
                        {
                            "attacker": o["attacker"],
                            "defender": o["defender"],
                            "chain": 1,
                        }
                    )
                elif pending_followups is not None:
                    pending_followups.append(
                        (second + cooldown, o["attacker"], o["defender"], 1)
                    )

        for fu in immediate_follow_ups:
            fu_attacker = fu["attacker"]
            fu_defender = fu["defender"]
            if fu_defender.final_lives <= 0:
                continue
            if fu_attacker.final_shots <= 0 and fu_attacker.role != "ammo":
                continue
            hit_chance = max(
                10, min(95, 70 + fu_attacker.accuracy - fu_defender.survival)
            )
            fu_hit = random.randint(1, 100) < hit_chance
            fu_attacker.follow_up_shots += 1
            fu_attacker.last_shot_time = second
            if fu_attacker.role != "ammo":
                fu_attacker.final_shots = max(0, fu_attacker.final_shots - 1)
            if fu_hit:
                fu_attacker.tags_made += 1
                if fu_attacker.role != "heavy":
                    fu_attacker.final_special = min(
                        fu_attacker.max_special, fu_attacker.final_special + 1
                    )
                fu_attacker.points_scored += 100
                fu_attacker.last_tagged_id = fu_defender.tag_id
                fu_defender.times_tagged += 1
                fu_defender.points_scored -= 20
                if not fu_defender.is_active_at(second) and fu_defender.is_taggable_at(
                    second
                ):
                    fu_defender.times_tagged_in_reset_window += 1
                fu_defender.shields = max(
                    0, fu_defender.shields - fu_attacker.shot_power
                )
                downed = fu_defender.shields == 0
                if downed:
                    fu_defender.final_lives = max(0, fu_defender.final_lives - 1)
                    fu_defender.last_downed_time = second
                    fu_defender.shields = fu_defender.max_shields
                    if fu_defender.final_lives <= 0:
                        fu_defender.was_eliminated_at = second
                        if event_log is not None:
                            event_log.append(
                                {
                                    "event_type": "elimination",
                                    "actor_id": fu_attacker.player_id,
                                    "target_id": fu_defender.player_id,
                                    "timestamp": second,
                                    "points_awarded": 0,
                                    "description": f"{fu_attacker.name} eliminates {fu_defender.name} (follow-up)",
                                    "metadata": {"elimination_action": "follow_up_tag"},
                                }
                            )
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "tag",
                            "actor_id": fu_attacker.player_id,
                            "target_id": fu_defender.player_id,
                            "timestamp": second,
                            "points_awarded": 100,
                            "description": f"{fu_attacker.name} follow-up tags {fu_defender.name}",
                            "metadata": {
                                "actor_role": fu_attacker.role,
                                "target_role": fu_defender.role,
                                "is_follow_up": True,
                                "chain": fu["chain"],
                            },
                        }
                    )
                if not downed and fu["chain"] < 2 and fu_defender.final_lives > 0:
                    if fu_defender.player_awareness < random.randint(0, 100):
                        # rapid-fire: chain immediately in this loop
                        immediate_follow_ups.append(
                            {
                                "attacker": fu_attacker,
                                "defender": fu_defender,
                                "chain": fu["chain"] + 1,
                            }
                        )
            else:
                fu_attacker.shots_missed += 1
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "miss",
                            "actor_id": fu_attacker.player_id,
                            "target_id": fu_defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{fu_attacker.name} follow-up miss on {fu_defender.name}",
                            "metadata": {"is_follow_up": True},
                        }
                    )

    def _attempt_resupply(self, tagger, teammate, second, event_log=None):
        ammo_chart = {"commander": 5, "heavy": 5, "scout": 10, "medic": 5}
        medic_chart = {"commander": 4, "heavy": 3, "scout": 5, "ammo": 3}
        if tagger.role == "ammo" and teammate.is_resupplyable_at(second):
            amount = ammo_chart.get(teammate.role, 5)
            teammate.final_shots = min(
                teammate.max_shots, teammate.final_shots + amount
            )
            teammate.last_downed_time = second
            teammate.shields = teammate.max_shields
            if teammate.role == "scout" and teammate.special_active_until > second:
                teammate.special_active_until = second
            if teammate.role == "commander" and teammate.special_active_until > second:
                teammate.special_active_until = 0
            tagger.resupplies_given += 1
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "resupply_ammo",
                        "actor_id": tagger.player_id,
                        "target_id": teammate.player_id,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{tagger.name} resupplies {teammate.name}",
                        "metadata": {
                            "amount": amount,
                            "actor_role": tagger.role,
                            "target_role": teammate.role,
                        },
                    }
                )
        elif (
            tagger.role == "medic"
            and tagger.final_shots > 0
            and teammate.is_resupplyable_at(second)
        ):
            amount = medic_chart.get(teammate.role, 3)
            teammate.final_lives = min(
                teammate.max_lives, teammate.final_lives + amount
            )
            teammate.last_downed_time = second
            teammate.shields = teammate.max_shields
            if teammate.role == "scout" and teammate.special_active_until > second:
                teammate.special_active_until = second
            if teammate.role == "commander" and teammate.special_active_until > second:
                teammate.special_active_until = 0
            tagger.resupplies_given += 1
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "resupply_lives",
                        "actor_id": tagger.player_id,
                        "target_id": teammate.player_id,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{tagger.name} heals {teammate.name}",
                        "metadata": {
                            "amount": amount,
                            "actor_role": tagger.role,
                            "target_role": teammate.role,
                        },
                    }
                )

    def _change_zone(self, player, towards=None):
        if player.current_zone == 1:
            player.current_zone = (
                towards if towards in (0, 2) else random.choice([0, 2])
            )
        else:
            player.current_zone = 1

    def _capture_base(
        self, player, base_id, event_log=None, second=0, movement_ctx=None
    ):
        if movement_ctx is not None and player.cell_row is not None:
            base_sight_data = movement_ctx.get("base_sight_data", {})
            cell_key = f"{player.cell_row},{player.cell_col}"
            if base_id == 15:
                in_range = any(
                    cell_key in base_sight_data.get(bt, frozenset())
                    for bt in _NEUTRAL_BASE_TYPES
                )
            else:
                opp_type = "blue" if player.team_color == "red" else "red"
                in_range = cell_key in base_sight_data.get(opp_type, frozenset())
            if not in_range:
                return
        if player.final_shots >= 3 or player.role == "ammo":
            if player.role != "ammo":
                player.final_shots -= 3
            player.last_tagged_id = str(base_id)
            if base_id == 15:
                player.neutral_base_destroyed = True
            else:
                player.opposing_base_destroyed = True
            player.points_scored += 1001
            if player.role != "heavy":
                player.final_special = min(player.max_special, player.final_special + 5)
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "base_capture",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 1001,
                        "description": f"{player.name} captures base",
                        "metadata": {"base_id": base_id, "actor_role": player.role},
                    }
                )

    def _award_bases(self, player, event_log=None, second=0):
        if player.final_lives > 0:
            if not player.neutral_base_destroyed:
                player.points_scored += 1001
                player.neutral_base_destroyed = True
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "base_capture",
                            "actor_id": player.player_id,
                            "target_id": None,
                            "timestamp": second,
                            "points_awarded": 1001,
                            "description": f"{player.name} awarded neutral base",
                            "metadata": {"base_id": 15, "actor_role": player.role},
                        }
                    )
            if not player.opposing_base_destroyed:
                player.points_scored += 1001
                player.opposing_base_destroyed = True
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "base_capture",
                            "actor_id": player.player_id,
                            "target_id": None,
                            "timestamp": second,
                            "points_awarded": 1001,
                            "description": f"{player.name} awarded opposing base",
                            "metadata": {
                                "base_id": 14 if player.team_color == "red" else 13,
                                "actor_role": player.role,
                            },
                        }
                    )

    def _start_missile_lock(self, attacker, defender, second):
        if (
            attacker.is_active_at(second)
            and defender.is_taggable_at(second)
            and attacker.final_missiles > 0
            and not defender.is_hiding
        ):
            if random.random() < 0.45:
                return None  # dodged
            delay = random.randint(1, 2)
            return (second + delay, attacker, defender)
        return None

    def _complete_missile(self, attacker, defender, second, event_log=None):
        if attacker.is_active_at(second) and defender.is_taggable_at(second):
            if not defender.is_active_at(second) and defender.is_taggable_at(second):
                defender.times_tagged_in_reset_window += 1
            defender.shields = defender.max_shields
            defender.points_scored -= 100
            defender.final_lives = max(0, defender.final_lives - 2)
            if defender.final_lives <= 0:
                defender.was_eliminated_at = second
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "elimination",
                            "actor_id": attacker.player_id,
                            "target_id": defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{defender.name} eliminated by missile from {attacker.name}",
                            "metadata": {"elimination_action": "missile"},
                        }
                    )
            defender.last_downed_time = second
            defender.times_missiled += 1

            attacker.points_scored += 500
            attacker.missile_points += 500
            attacker.final_missiles -= 1
            attacker.missiles_landed += 1
            if attacker.role != "heavy":
                attacker.final_special = min(
                    attacker.max_special, attacker.final_special + 2
                )
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "missile",
                        "actor_id": attacker.player_id,
                        "target_id": defender.player_id,
                        "timestamp": second,
                        "points_awarded": 500,
                        "description": f"{attacker.name} hits {defender.name} with missile",
                        "metadata": {
                            "actor_role": attacker.role,
                            "target_role": defender.role,
                            "target_lives": defender.final_lives,
                        },
                    }
                )

    def _use_special(self, player, second, all_alive, event_log=None):
        if not (
            player.can_use_special
            and player.final_lives > 0
            and player.is_active_at(second)
        ):
            return None
        player.specials_used += 1
        if player.role == "commander":
            player.final_special -= player.special_cost
            countdown = random.randint(4, 7)
            player.special_active_until = second + countdown
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{player.name} activates nuke",
                        "metadata": {
                            "actor_role": player.role,
                            "fires_at": second + countdown,
                        },
                    }
                )
            return ("nuke", second + countdown, player)
        elif player.role == "scout":
            player.final_special -= player.special_cost
            player.special_active_until = 900
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{player.name} activates rapid fire",
                        "metadata": {"actor_role": player.role},
                    }
                )
        elif player.role == "medic":
            player.final_special -= player.special_cost
            heal_chart = {"commander": 4, "heavy": 3, "scout": 5, "ammo": 2, "medic": 0}
            for mate in all_alive:
                if mate.team_color == player.team_color and mate.is_active_at(second):
                    amount = heal_chart.get(mate.role, 0)
                    mate.final_lives = min(mate.max_lives, mate.final_lives + amount)
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{player.name} team heal special",
                        "metadata": {"actor_role": player.role},
                    }
                )
        elif player.role == "ammo":
            player.final_special -= player.special_cost
            shot_chart = {
                "commander": 5,
                "heavy": 5,
                "scout": 10,
                "medic": 5,
                "ammo": 0,
            }
            for mate in all_alive:
                if mate.team_color == player.team_color and mate.is_active_at(second):
                    amount = shot_chart.get(mate.role, 0)
                    mate.final_shots = min(mate.max_shots, mate.final_shots + amount)
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{player.name} team ammo special",
                        "metadata": {"actor_role": player.role},
                    }
                )
        return None

    def _complete_nuke(self, player, second, opposing_players, event_log=None):
        if player.is_active_at(second) and player.final_lives > 0:
            player.points_scored += 500
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 500,
                        "description": f"{player.name} nuke detonates",
                        "metadata": {"actor_role": player.role},
                    }
                )
            for opp in opposing_players:
                if opp.final_lives <= 0:
                    continue
                lives_taken = min(opp.final_lives, 3)
                opp.final_lives -= lives_taken
                opp.last_downed_time = second
                opp.shields = opp.max_shields
                if opp.role == "commander" and opp.special_active_until > second:
                    opp.special_active_until = 0
                if opp.final_lives <= 0:
                    opp.was_eliminated_at = second
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "elimination",
                                "actor_id": player.player_id,
                                "target_id": opp.player_id,
                                "timestamp": second,
                                "points_awarded": 0,
                                "description": f"{opp.name} eliminated by nuke",
                                "metadata": {"elimination_action": "nuke"},
                            }
                        )

    # ------------------------------------------------------------------ #
    # Seed-based exact replay and DB persistence
    # ------------------------------------------------------------------ #

    def replay_round(self, red_roster, blue_roster, seed_state):
        """Replay one round from a saved random state, collecting full event log."""
        events = []
        random.setstate(seed_state)
        result, red_players, blue_players = self._simulate_round(
            red_roster, blue_roster, event_log=events
        )
        return result, red_players, blue_players, events

    def save_games(self, team_red, team_blue, seeds, n):
        """Replay and persist n games using the provided seed states."""
        red_roster = list(team_red.active_roster)
        blue_roster = list(team_blue.active_roster)
        saved = []
        for seed_state in seeds[:n]:
            result, red_players, blue_players, events = self.replay_round(
                red_roster, blue_roster, seed_state
            )
            gr = self._flush_to_db(
                team_red, team_blue, result, red_players, blue_players, events
            )
            saved.append(gr)
        return saved

    @transaction.atomic
    def _flush_to_db(
        self, team_red, team_blue, result, red_players, blue_players, events
    ):
        """Write a replayed in-memory round to DB as a standalone GameRound."""
        from teams.models import Player as PlayerModel

        game_round = GameRound.objects.create(
            match=None,
            round_number=1,
            team_red=team_red,
            team_blue=team_blue,
            red_points=result["red_points"],
            blue_points=result["blue_points"],
            red_team_eliminated=result["red_eliminated"],
            blue_team_eliminated=result["blue_eliminated"],
            eliminated_at=result["eliminated_at"],
            is_completed=True,
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
                shields=p.shields,
                starting_lives=p.starting_lives,
                starting_shots=p.starting_shots,
                starting_special=0,
                starting_missiles=self.ROLE_STARTING_RESOURCES[p.role]["missiles"],
                final_lives=p.final_lives,
                final_shots=p.final_shots,
                final_special=p.final_special,
                final_missiles=p.final_missiles,
                neutral_base_destroyed=p.neutral_base_destroyed,
                opposing_base_destroyed=p.opposing_base_destroyed,
                special_active_until=p.special_active_until or 0,
                is_hiding=p.is_hiding,
                points_scored=p.points_scored,
                tags_made=p.tags_made,
                shots_missed=p.shots_missed,
                times_tagged=p.times_tagged,
                times_missiled=p.times_missiled,
                missiles_landed=p.missiles_landed,
                resupplies_given=p.resupplies_given,
                specials_used=p.specials_used,
                was_eliminated_at=p.was_eliminated_at,
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

        return game_round
