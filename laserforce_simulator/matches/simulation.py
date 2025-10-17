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
        match.red_round1_eliminated = round1.red_eliminated
        match.blue_round1_eliminated = round1.blue_eliminated

        # Round 2: teams switch colors
        round2 = self.simulate_detailed_round(team_blue, team_red, match, 2)
        match.red_round2_points = round2.blue_points  # Switched
        match.blue_round2_points = round2.red_points  # Switched
        match.red_round2_eliminated = round2.blue_eliminated  # Switched
        match.blue_round2_eliminated = round2.red_eliminated  # Switched

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
        game_round.red_eliminated = round_result["red_eliminated"]
        game_round.blue_eliminated = round_result["blue_eliminated"]
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

        # TODO: we want to simulate combat faster than every 2 seconds but this is ok for now to test
        for second in range(0, round_duration, 2):  # Check every 2 seconds
            self._simulate_combat_exchange(
                game_round, red_players, blue_players, second
            )

            # Check for team eliminations
            red_alive = [p for p in red_players if p.final_lives > 0]
            blue_alive = [p for p in blue_players if p.final_lives > 0]

            if not red_alive or not blue_alive:
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
            p.was_eliminated = p.final_lives <= 0
            p.save()

        return {
            "red_points": red_points,
            "blue_points": blue_points,
            "red_eliminated": red_eliminated,
            "blue_eliminated": blue_eliminated,
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

    def _simulate_combat_exchange(self, game_round, red_players, blue_players, second):
        """Simulate a single combat exchange between teams"""
        # Get alive players
        red_alive = [p for p in red_players if p.final_lives > 0]
        blue_alive = [p for p in blue_players if p.final_lives > 0]
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
        # TODO: change order of players based on some stats
        # randomize the list of players
        random.shuffle(all_alive)
        for player in all_alive:
            # choose action to perform
            self._choose_action(player, all_alive, second)

            # check if one team is eliminated, and stop simulation
            if not red_alive or not blue_alive:
                return

            # Update alive lists after potential eliminations
            red_alive = [p for p in red_players if p.final_lives > 0]
            blue_alive = [p for p in blue_players if p.final_lives > 0]
            all_alive = red_alive + blue_alive

            if not red_alive or not blue_alive:
                break

    def _choose_action(self, player, all_alive, second):
        # TODO: improve action choice based on player stats and situation
        action_to_weight_index = {
            "tag_player": 0,
            "change_zone": 1,
            "hide": 2,
            "capture_base": 3,
            "use_special": 4,
            "resupply_ally": 5,
            "missile_player": 6,
        }
        actions = [
            "tag_player",
            "change_zone",
            "hide",
            "capture_base",
            "use_special",
            "resupply_ally",
            "missile_player",
        ]
        weights = [70, 30, 0, 0, 0, 0, 0]  # default weights
        # mofify weights based on role, zone, resources, active_status,
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

        # logger.debug(
        #     "%s - %s: %s, zone: %s, team: %s, bases: %s,%s weights: %s",
        #     second,
        #     "choose action",
        #     player.role,
        #     player.current_zone,
        #     player.team_color,
        #     player.neutral_base_destroyed,
        #     player.opposing_base_destroyed,
        #     weights,
        # )
        action = random.choices(actions, weights)[0]
        # if player is no longer hiding, set false
        if action != "hide":
            player.is_hiding = False
        # logger.debug(
        #     "%s - %s: role: %s team: %s chosen action: %s",
        #     second,
        #     "choose action",
        #     player.role,
        #     player.team_color,
        #     action,
        # )

        if action == "tag_player":
            # choose a target from the opposing team in the same zone
            target = self._choose_tag_target(player, all_alive, second)
            if target:
                self._attempt_tag(player.game_round, player, target, second=second)
                if player.role == "scout" and player.special_active_until > second:
                    # if scout with rapid fire, choose 2 targets to tag
                    logger.debug(
                        "%s - %s: scout %s attempts second tag due to rapid fire",
                        second,
                        "choose action",
                        player.player.name,
                    )
                    second_target = self._choose_tag_target(player, all_alive, second)
                    if second_target:
                        self._attempt_tag(
                            player.game_round, player, second_target, second=second
                        )
            # else:
            #     # if no valid targets, change zone instead
            #     logger.debug("%s - %s: no valid targets, do nothing", second, "choose action/no_target")
            # TODO: change zones differently based on role, heavies want to be with medic or ammo, commanders want to be with enemies, scouts want to be with enemies
            # self._change_zone(player, second)
        elif action == "resupply_ally":
            # choose a teammate in the same zone to resupply
            teammate = self._choose_resupply_target(player, all_alive, second)
            if teammate:
                self._attempt_resupply(player, teammate, second)
            else:
                # no teammates need healing or none in zone
                pass
        elif action == "missile_player":
            if player.final_missiles > 0:
                # choose a target from the opposing team in the same zone
                potential_targets = [
                    p
                    for p in all_alive
                    if p.team_color != player.team_color
                    and p.current_zone == player.current_zone
                    and p.final_lives > 0
                    and p.is_taggable_at(second)
                ]
                if potential_targets:
                    target = random.choice(potential_targets)
                    self._start_missile_lock(player, target, second)
        elif action == "change_zone":
            # change to an adjacent zone

            zone = self._choose_zone_change(player, all_alive, second)
            if zone:
                self._change_zone(player, second, towards=zone)
            else:
                self._change_zone(player, second)
        elif action == "capture_base":
            # assumes already in correct zone to capture base
            if player.current_zone == 1 and not player.neutral_base_destroyed:
                base_id = 15
                self._capture_base(player, base_id, second)
            elif not player.opposing_base_destroyed:
                # base id is 14 if player is red, and 13 if blue
                base_id = 14 if player.team_color == "red" else 13
                self._capture_base(player, base_id, second)
            else:
                # if bases are destroyed, change zone instead
                self._change_zone(player, second)
        elif action == "use_special":
            if (
                player.final_special >= player.special_cost
                and player.special_active_until <= second
                and player.is_active_at(second)
            ):
                self._use_special(player, second)
        elif action == "hide":
            # hiding reduces chance of being targeted next round
            player.is_hiding = True
            pass

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
                "ammo": 2,
            }
            target_weights = []
            for target in potential_targets:
                # prioritize active targets more
                active_weighting = 5 if target.is_active_at(second) else 1
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

    def _attempt_tag(self, game_round, attacker, defender, second):
        """Simulate a tag attempt between two players and record events on game_round"""
        if attacker.final_shots <= 0 or defender.final_lives <= 0:
            return

        # if defender is hiding then 50% chance they don't get tagged
        if defender.is_hiding and random.random() > 0.5:
            logger.debug(
                "%s - %s: defender %s%s hid from %s%s",
                second,
                "attempt tag",
                defender.team_color,
                defender.role,
                attacker.team_color,
                attacker.role,
            )
            return
        # if defender.attacker.last_tagged_id == defender.get_tag_id:

        # Calculate hit probability
        # TODO: accuracy and evasion should be on player model
        # base_accuracy = 0.6  # 60% for now, will change later to
        accuracy = attacker.player.accuracy
        # accuracy = base_accuracy * att_mods.get("accuracy", 1.0)
        evasion = defender.player.survival

        hit_chance = accuracy / evasion
        hit_chance = max(0.1, min(0.95, hit_chance))  # Clamp between 10% and 95%

        # Use a shot, except if ammo, they have unlimited
        if attacker.role != "ammo":
            attacker.final_shots -= 1
        attacker.save()

        # make rolled chance between 1-100
        rolled_chance = random.randint(1, 100)

        if rolled_chance < (hit_chance * 100):

            # TODO: we also want to randomize/log where the defender was tagged

            # Hit! Remove defender life and award points
            defender.shields = max(
                0, defender.shields - attacker.shot_power
            )  # lose shields proportional to shot power
            if defender.shields == 0:
                defender.final_lives -= 1
                defender.last_downed_time = second  # set downed time for respawn logic
                defender.shields = defender.max_shields  # reset shields on life lost
                if defender.final_lives <= 0:
                    defender.was_eliminated = True
                    logger.debug(
                        "%s - %s: Player eliminated: %s by %s",
                        second,
                        "attempt tag",
                        defender.player.name,
                        attacker.player.name,
                    )
                    GameEvent.objects.create(
                        game_round=game_round,
                        timestamp=second,
                        event_type="elimination",
                        actor=attacker.player,
                        target=defender.player,
                        points_awarded=0,
                        description=f"{defender.player.name} is eliminated by {attacker.player.name}",
                        metadata={
                            "actor_role": attacker.role,
                            "target_role": defender.role,
                            "target_lives": defender.final_lives},
                    )

            defender.times_tagged += 1
            defender.points_scored -= 20  # Penalty for being tagged

            # Safe-upsert keys in specific_tags to avoid KeyError
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

            defender.specific_tags[atk_key]["tagged_by"] += 1

            attacker.tags_made += 1
            # heavies don't get specials
            if attacker.role != "heavy":
                attacker.final_special += 1
            attacker.points_scored += 100
            attacker.specific_tags[def_key]["tags"] += 1
            attacker.last_tagged_id = def_key
            if defender.role == "medic":
                attacker.final_medic_hits += 1

            # Save states
            attacker.save()
            defender.save()

            # create game event
            # TODO: figure out where we are getting this game_round object from,
            # it exists in def simulate_detailed_round but not in outer scope currently.
            # do we want do put metadata in from pre or post tag? probably post?
            # does this automatically put these things in order?
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
                    "target_role": defender.role,
                    "target_points": defender.points_scored,
                    "target_lives": defender.final_lives,
                    "target_shields": defender.shields,
                    "target_shots": defender.final_shots,
                    "rolled_hit_pct": rolled_chance,
                },
            )
        else:
            attacker.shots_missed += 1
            attacker.save()
            logger.debug(
                "%s - %s: Tag missed: %s to %s, rolled %s vs chance %s",
                second,
                "attempt tag",
                attacker.player.name,
                defender.player.name,
                rolled_chance,
                hit_chance,
            )
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
                    "target_role": defender.role
                    "target_points": defender.points_scored,
                    "target_lives": defender.final_lives,
                    "target_shots": defender.final_shots,
                    "rolled_hit_pct": rolled_chance,
                },
            )

            # TODO: determine if there is some use of synergy that we want to use to decide
            # if there are follow up actions to this action

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
            if teammate.final_shots + resupply_amount > teammate.max_shots:
                teammate.final_shots = teammate.max_shots
            else:
                teammate.final_shots += resupply_amount
            teammate.last_downed_time = second
            teammate.shields = teammate.max_shields
            # if scout then end their special
            if teammate.role == "scout" and teammate.special_active_until > second:
                teammate.special_active_until = second
            tagger.resupplies_given += 1
            tagger.save()
            teammate.save()
            # create game event for resupply
            GameEvent.objects.create(
                game_round=tagger.game_round,
                timestamp=second,
                event_type="resupply",
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
            if teammate.final_lives + resupply_amount > teammate.max_lives:
                teammate.final_lives = teammate.max_lives
            else:
                teammate.final_lives += resupply_amount
            teammate.last_downed_time = second
            teammate.shields = teammate.max_shields
            tagger.resupplies_given += 1
            tagger.save()
            teammate.save()
            # create game event for resupply
            GameEvent.objects.create(
                game_round=tagger.game_round,
                timestamp=second,
                event_type="resupply",
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
            logger.debug(
                "%s - %s: about to complete missile delay: %s",
                second,
                "start msl lock",
                delay,
            )
            self._complete_missile(attacker, defender, second + delay)
            return
        return

    def _complete_missile(self, attacker, defender, second):
        """Simulate finishing missle on opponent"""
        if attacker.is_active_at(second) and defender.is_taggable_at(second):
            # normalize role checks (roles are stored lowercase elsewhere)
            defender.shields = defender.max_shields  # reset shields on missile hit
            defender.points_scored -= 100
            defender.final_lives -= 2
            if defender.final_lives <= 0:
                defender.was_eliminated = True
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
                        "target_role:": defender.role,
                        "target_lives": defender.final_lives
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
                # TODO: call with delay to complete nuke

                self._complete_nuke(player_state, second + countdown)
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
                for mate in teammates:
                    total_healed = 0
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
                for mate in teammates:
                    total_ammo = 0
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
            opposing_players = PlayerRoundState.objects.filter(
                game_round=player_state.game_round,
                team_color="blue" if player_state.team_color == "red" else "red",
                final_lives__gt=0,
            )
            for opponent in opposing_players:
                print(f"nuke: {opponent.role}, {opponent.final_lives}")
                opponent.final_lives -= 3
                opponent.last_downed_time = second
                opponent.shields = opponent.max_shields
                if opponent.final_lives <= 0:
                    print(
                        f"nuke result: {opponent.role} eliminated {opponent.final_lives}"
                    )
                    opponent.final_lives = 0
                    opponent.was_eliminated = True
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
                            "actor_role": player_state.role,
                            "target_role": opponent.role,
                            "defender_lives": opponent.final_lives},
                    )
                opponent.save()

            player_state.points_scored += 500
            player_state.save()

            GameEvent.objects.create(
                game_round=player_state.game_round,
                timestamp=second,
                event_type="nuke_detonated",
                actor=player_state.player,
                points_awarded=500,
                description=f"{player_state.player.name} detonates Nuke",
                metadata={
                    "actor_role": player_state.role,
                    "special_points": player_state.final_special,
                    "opponents_affected": opposing_players.count(),
                },
            )

    def _reset_base(self, player_state, base_id, second):
        """Simulate resetting off a base"""
        # check if player can reset base (is alive, is in zone, hasn't already captured base)
        # if so, expend 1 ammo and set last_tagged_id to base id
        return None

    def _capture_base(self, player_state, base_id, second):
        """Simulate capturing a base"""
        # check if player can capture base (is alive, is in zone, hasn't already captured base)
        # if so, expend 3 shots, set last_tagged_id to base id, and award 1001 points
        if player_state.final_shots >= 3:
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
