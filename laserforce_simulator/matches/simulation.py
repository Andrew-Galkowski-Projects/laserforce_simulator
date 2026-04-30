import random
import logging
from .models import GameEvent, Match, GameRound, PlayerRoundState, SingleRound
from .sim_helpers.weights import (
    _get_medic_weights,
    _get_ammo_weights,
    _get_scout_weights,
    _get_heavy_weights,
    _get_commander_weights,
)
from teams.models import Player

# Module logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class ResourceBasedSimulator:
    """Enhanced simulator that tracks individual player resources"""

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

        # Role effectiveness modifiers
        self.role_modifiers = {
            "commander": {
                "shot_power": 2,
                "shield": 3,
            },
            "heavy": {
                "shot_power": 3,
                "shield": 3,
            },
            "scout": {
                "shot_power": 1,
                "shield": 1,
            },
            "medic": {
                "shot_power": 1,
                "shield": 1,
            },
            "ammo": {
                "shot_power": 1,
                "shield": 1,
            },
        }

    def simulate_match(self, team_red, team_blue, match_type="friendly"):
        """Simulate a full 2-round match with detailed tracking"""
        match = Match.objects.create(
            team_red=team_red, team_blue=team_blue, match_type=match_type
        )

        # Round 1: team_red as red, team_blue as blue
        round1 = self.simulate_detailed_round(team_red, team_blue, match, 1)
        match.red_round1_points = round1.red_points
        match.blue_round1_points = round1.blue_points
        match.red_round1_eliminated = round1.red_team_eliminated
        match.blue_round1_eliminated = round1.blue_team_eliminated
        match.round1_eliminated_at = round1.eliminated_at

        # Round 2: teams switch colors
        round2 = self.simulate_detailed_round(team_blue, team_red, match, 2)
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

    def simulate_single_round_detailed(self, team_red, team_blue):
        """Simulate a single round with detailed player tracking"""
        game_round = self.simulate_detailed_round(team_red, team_blue, None, 1)
        return game_round

    def simulate_detailed_round(self, team_red, team_blue, match=None, round_number=1):
        """Simulate a round with full player resource tracking"""
        game_round = GameRound.objects.create(
            match=match,
            round_number=round_number,
            team_red=team_red,
            team_blue=team_blue,
        )

        # Initialize player states
        red_players = self._initialize_players(
            game_round, team_red.players.all(), "red"
        )
        blue_players = self._initialize_players(
            game_round, team_blue.players.all(), "blue"
        )

        # Simulate the round (pass game_round so events can be recorded)
        round_result = self._simulate_round_combat(
            game_round, red_players, blue_players
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

    def _initialize_players(self, game_round, players, team_color):
        """Initialize player states with starting resources"""
        player_states = []

        for player in players:
            resources = self.role_starting_resources.get(
                player.role, self.role_starting_resources[player.role]
            )

            modifiers = self.role_modifiers.get(
                player.role, self.role_modifiers[player.role]
            )
            starting_zone = 0 if team_color == "red" else 2

            state = PlayerRoundState.objects.create(
                game_round=game_round,
                team_color=team_color,
                role=player.role,
                player=player,
                current_zone=starting_zone,
                shot_power=modifiers["shot_power"],
                shields=modifiers["shield"],
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

    def _simulate_round_combat(self, game_round, red_players, blue_players):
        """Simulate combat between two teams"""
        # Combat simulation over time (simplified)
        round_duration = 15 * 60  # 15 minutes in seconds

        # We'll support scheduled delayed actions (missiles, nukes) that complete on a later tick
        pending_missiles = []  # list of tuples (complete_time, attacker, defender)
        pending_nukes = []  # list of tuples (complete_time, player_state)
        eliminated_at = 901
        # TODO: we want to simulate combat faster than every 2 seconds but this is ok for now to test
        for second in range(0, round_duration, 2):  # Check every 2 seconds
            # First, process any scheduled missile completions for this second
            to_run = [m for m in pending_missiles if m[0] <= second]
            pending_missiles = [m for m in pending_missiles if m[0] > second]
            for complete_time, attacker, defender in to_run:
                # missile only completes if attacker still active at completion and defender still taggable
                if attacker.is_active_at(complete_time) and defender.is_taggable_at(
                    complete_time
                ):
                    self._complete_missile(attacker, defender, complete_time)
                else:
                    logger.debug(
                        "%s - %s: missile cancelled or failed at %s (attacker active: %s, defender taggable: %s)",
                        complete_time,
                        "missile completion",
                        complete_time,
                        (
                            getattr(attacker, "is_active_at")(complete_time)
                            if hasattr(attacker, "is_active_at")
                            else False
                        ),
                        (
                            getattr(defender, "is_taggable_at")(complete_time)
                            if hasattr(defender, "is_taggable_at")
                            else False
                        ),
                    )

            # Next, process scheduled nukes
            to_run_n = [n for n in pending_nukes if n[0] <= second]
            pending_nukes = [n for n in pending_nukes if n[0] > second]
            for complete_time, player_state in to_run_n:
                if (
                    player_state.is_active_at(complete_time)
                    and player_state.final_lives > 0
                ):
                    self._complete_nuke(player_state, complete_time)

            # REFRESH player states from database after nukes/missiles to get updated was_eliminated_at values
            for p in red_players + blue_players:
                p.refresh_from_db()

            # Plan and resolve simultaneous actions for this tick. New signature accepts pending lists so it may append scheduled delayed actions.
            self._simulate_combat_exchange(
                game_round,
                red_players,
                blue_players,
                second,
                pending_missiles,
                pending_nukes,
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

                # award any non-captured bases to alive players on winning team
                if not red_alive:
                    for blue_player in blue_alive:
                        self._award_bases(blue_player, second)
                if not blue_alive:
                    for red_player in red_alive:
                        self._award_bases(red_player, second)

                # for p in red_players + blue_players:
                #     logger.debug(
                #         "%s tags: %s, tagged %s details: %s",
                #         p.player.name,
                #         p.tags_made,
                #         p.times_tagged,
                #         p.specific_tags,
                #     )
                break  # Round ends on elimination

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
        result = ", ".join(str(obj) for obj in all_alive)

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

        # TODO: eventually want to sort all_alive by player decision making or something
        random.shuffle(all_alive)
        plans = []
        for player in all_alive:
            plans.extend(self._plan_action(player, all_alive, second))

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
                self._change_zone(actor, second, towards=plan.get("zone"))
            elif ptype == "hide":
                actor.is_hiding = True
                actor.save()
            elif ptype == "capture_base":
                self._capture_base(actor, plan.get("base_id"), second)
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
            self._resolve_tag_attempts(game_round, tag_attempts, second)

    def _plan_action(self, player, all_alive, second):
        """Return a list of planned actions (dict) for the player at this tick without applying changes.

        Each plan dict may have keys: type, actor, target, zone, base_id
        """
        actions = []
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

        choice = random.choices(choices, weights)[0]

        # only maintain hiding status if they are still hiding, moving, or resupplying
        if player.is_hiding and not (
            choice == "hide" or choice == "change_zone" or choice == "resupply_ally"
        ):
            player.is_hiding = False
            player.save()

        plans = []
        if choice == "tag_player":
            target = self._choose_tag_target(player, all_alive, second)
            if target and player.final_shots > 0:
                plans.append({"type": "tag", "actor": player, "target": target})
                # scouts may attempt a second tag immediately if rapid fire active
                if player.role == "scout" and player.special_active_until > second:
                    second_target = self._choose_tag_target(player, all_alive, second)
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
            zone = self._choose_zone_change(player, all_alive, second)
            plans.append({"type": "change_zone", "actor": player, "zone": zone})
        elif choice == "capture_base":
            # simple heuristic
            base_id = (
                15
                if player.current_zone == 1
                else (14 if player.team_color == "red" else 13)
            )
            plans.append({"type": "capture_base", "actor": player, "base_id": base_id})
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

    def _resolve_tag_attempts(self, game_round, attempts, second):
        """Resolve multiple tag attempts simultaneously.

        attempts: list of {'attacker': PlayerRoundState, 'defender': PlayerRoundState}
        """
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
            if o["result"] in ("invalid", "miss_hid"):
                # use a shot unless ammo
                if attacker.role != "ammo":
                    attacker.final_shots -= 1
                # record miss if appropriate
                if o["result"] == "miss_hid":
                    attacker = o["attacker"]
                    defender = o["defender"]
                    attacker.shots_missed += 1
                    attacker.save()
                    GameEvent.objects.create(
                        game_round=game_round,
                        timestamp=second,
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

            attacker = o["attacker"]
            defender = o["defender"]

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
                if defender.role == "medic":
                    attacker.final_medic_hits += 1

                defender.specific_tags[atk_key]["tagged_by"] += 1

                logger.debug(
                    "%s - %s: %s %s tags %s %s atk ammo: %s def shd/lv: %s/%s",
                    second,
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
                    timestamp=second,
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
                        "target_active": defender.is_active_at(second),
                        "target_taggable": defender.is_taggable_at(second),
                        "target_id": defender.get_tag_id,
                        "target_lives": defender.final_lives,
                        "target_shields": defender.shields,
                        "target_shots": defender.final_shots,
                        "rolled_hit_pct": o.get("rolled", 0),
                    },
                )
                defender.shields = max(0, defender.shields - attacker.shot_power)
                if defender.shields == 0:
                    # code for nuke cancels
                    if (
                        defender.role == "commander"
                        and defender.special_active_until > second
                    ):
                        if attacker.team_color != defender.team_color:
                            attacker.enemy_nuke_cancels += 1
                        else:
                            attacker.ally_nuke_cancels += 1
                        defender.own_specials_cancelled += 1
                        defender.save()
                        GameEvent.objects.create(
                            game_round=game_round,
                            timestamp=second,
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
                    # never want to display below 0 even if they were at 1 and took simultaneous tags
                    defender.final_lives -= min(1, defender.final_lives)
                    defender.last_downed_time = second
                    defender.shields = defender.max_shields
                    if defender.final_lives <= 0:
                        defender.was_eliminated_at = second
                        GameEvent.objects.create(
                            game_round=game_round,
                            timestamp=second,
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
                attacker.save()
                # logger.debug(
                #     "%s - %s: Tag missed: %s to %s, rolled %s vs chance %s",
                #     second,
                #     "attempt tag",
                #     attacker.player.name,
                #     defender.player.name,
                #     hit,
                #     hit_chance,
                # )
                GameEvent.objects.create(
                    game_round=game_round,
                    timestamp=second,
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

    def _change_zone(self, player, second, towards=None):
        if player.current_zone == 1:
            # 50/50 chance to go to either adjacent zone
            if towards in [0, 2]:
                player.current_zone = towards
            else:
                player.current_zone = random.choice([0, 2])
        else:
            player.current_zone = 1
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

    def _choose_tag_target(self, player, all_alive, second):
        potential_targets = [
            p
            for p in all_alive
            if p.team_color != player.team_color
            and p.current_zone == player.current_zone
            and p.final_lives > 0
            and (
                p.is_active_at(second)
                or p.is_taggable_at(second)
                and player.last_tagged_id != p.get_tag_id
            )
        ]
        if potential_targets and player.final_shots > 0:
            # set target weights based on role
            weights = {
                "commander": 5,
                "heavy": 8,
                "scout": 3,
                "medic": 1,
                "ammo": 3,
            }
            target_weights = []
            for target in potential_targets:
                # prioritize active targets more
                active_weighting = 10 if target.is_active_at(second) else 1
                target_weights.append(weights.get(target.role, 1) + active_weighting)

            target = random.choices(potential_targets, target_weights)[0]
            return target
        else:
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
                # remove special points, set special active until to seconds + 4-7 seconds
                player_state.final_special -= player_state.special_cost
                player_state.specials_used += 1
                countdown = random.randint(4, 7)
                player_state.special_active_until = second + countdown
                # schedule nuke completion instead of running immediately
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
                    },
                )
                # return a tuple indicating a scheduled nuke: ('nuke', complete_time, player_state)
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
                lives_taken = min(opponent.final_lives, 3)
                opponent.lives_lost_to_nukes += lives_taken
                opponent.final_lives -= lives_taken
                opponent.last_downed_time = second
                opponent.shields = opponent.max_shields

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

    def _reset_base(self, player_state, base_id, second):
        """Simulate resetting off a base"""
        # check if player can reset base (is alive, is in zone, hasn't already captured base)
        # if so, expend 1 ammo and set last_tagged_id to base id
        return None

    def _capture_base(self, player_state, base_id, second):
        """Simulate capturing a base"""
        # check if player can capture base (is alive, is in zone, hasn't already captured base)
        # if so, expend 3 shots, set last_tagged_id to base id, and award 1001 points
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


# Legacy simple simulator for backward compatibility
class SimpleMatchSimulator:
    """Basic simulator for Phase 2 compatibility"""

    def __init__(self):
        self.base_points_range = (800, 1500)
        self.elimination_bonus = 500
        self.elimination_probability = 0.15

    def simulate_match(self, team_red, team_blue, match_type="friendly"):
        """Use resource-based simulator but return simple results"""
        simulator = ResourceBasedSimulator()
        return simulator.simulate_match(team_red, team_blue, match_type)

    def simulate_single_round(self, team_red, team_blue):
        """Legacy single round simulation"""
        single_round = SingleRound.objects.create(
            team_red=team_red, team_blue=team_blue
        )

        result = self._simulate_round()
        single_round.red_points = result["red_points"]
        single_round.blue_points = result["blue_points"]
        single_round.red_eliminated = result["red_eliminated"]
        single_round.blue_eliminated = result["blue_eliminated"]
        single_round.is_completed = True
        single_round.save()

        return single_round

    def _simulate_round(self):
        """Simulate a single round with random results"""
        # Generate random base points for each team
        red_points = random.randint(*self.base_points_range)
        blue_points = random.randint(*self.base_points_range)

        # Check for eliminations
        red_eliminated = random.random() < self.elimination_probability
        blue_eliminated = random.random() < self.elimination_probability

        # If a team is eliminated, they typically score fewer points
        if red_eliminated:
            red_points = int(red_points * random.uniform(0.3, 0.7))
        if blue_eliminated:
            blue_points = int(blue_points * random.uniform(0.3, 0.7))

        # Ensure eliminated teams don't both happen (very rare)
        if red_eliminated and blue_eliminated:
            # Randomly pick one to not be eliminated
            if random.choice([True, False]):
                red_eliminated = False
            else:
                blue_eliminated = False

        return {
            "red_points": red_points,
            "blue_points": blue_points,
            "red_eliminated": red_eliminated,
            "blue_eliminated": blue_eliminated,
        }
