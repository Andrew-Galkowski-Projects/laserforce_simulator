import random
import logging
from .models import GameEvent, Match, GameRound, PlayerRoundState, SingleRound
from teams.models import Player

# Module logger
logger = logging.getLogger(__name__)


class ResourceBasedSimulator:
    """Enhanced simulator that tracks individual player resources"""

    def __init__(self):
        self.elimination_bonus = 10000  # Bonus points for eliminating entire team
        # Role-based starting resources
        self.role_resources = {
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
                "special": "nuke",
                "special_cost": 20,
            },
            "heavy": {
                "shot_power": 3,
                "shield": 3,
                "special": "none",
                "special_cost": 0,
            },
            "scout": {
                "shot_power": 1,
                "shield": 1,
                "special": "rapid_fire",
                "special_cost": 10,
            },
            "medic": {
                "shot_power": 1,
                "shield": 1,
                "special": "life_boost",
                "special_cost": 10,
            },
            "ammo": {
                "shot_power": 1,
                "shield": 1,
                "special": "ammo_boost",
                "special_cost": 15,
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
            resources = self.role_resources.get(
                player.role, self.role_resources[player.role]
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

        # TODO: we want to simulate combat much faster than every 3 seconds but this is ok for now to test
        for second in range(0, round_duration, 3):  # Check every 3 seconds
            self._simulate_combat_exchange(
                game_round, red_players, blue_players, second
            )

            # Check for team eliminations
            red_alive = [p for p in red_players if p.final_lives > 0]
            blue_alive = [p for p in blue_players if p.final_lives > 0]

            if not red_alive or not blue_alive:
                logger.debug(
                    "Round ends at second %s, red alive %s, blue alive %s",
                    second,
                    red_alive,
                    blue_alive,
                )
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
            "Final Results: %s red points, %s blue points, red eliminated: %s, blue eliminated: %s",
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
        if player.player.can_resupply:
            if (player.team_color == "red" and player.current_zone != 0) or (
                player.team_color == "blue" and player.current_zone != 2
            ):
                actions = ["tag_player", "resupply_ally", "change_zone", "capture_base"]
                weights = [20, 30, 5, 50]  # When able, more likely to capture base
            else:
                actions = ["tag_player", "resupply_ally", "change_zone"]
                weights = [
                    35,
                    55,
                    10,
                ]  # when in own zone, more likely to tag or resupply
        if player.role == "commander" or player.role == "heavy":
            if (player.team_color == "red" and player.current_zone != 0) or (
                player.team_color == "blue" and player.current_zone != 2
            ):
                actions = [
                    "tag_player",
                    "missile_player",
                    "change_zone",
                    "capture_base",
                ]
                weights = [
                    30,
                    10,
                    10,
                    50,
                ]  # When able, more likely to capture base, missiles are rare
            else:
                actions = ["tag_player", "missile_player", "change_zone"]
                weights = [70, 10, 20]  # when in own zone, more likely to tag
        if player.role == "scout" or player.role == "scout":
            if (player.team_color == "red" and player.current_zone != 0) or (
                player.team_color == "blue" and player.current_zone != 2
            ):
                actions = ["tag_player", "change_zone", "capture_base"]
                weights = [50, 10, 40]  # When able, more likely to capture base
            else:
                actions = ["tag_player", "change_zone"]
                weights = [80, 20]  # when in own zone, more likely to tag
        logger.debug(
            "%s, zone: %s, team: %s, bases: %s,%s %s, %s",
            player.role,
            player.current_zone,
            player.team_color,
            player.neutral_base_destroyed,
            player.opposing_base_destroyed,
            actions,
            weights,
        )
        action = random.choices(actions, weights)[0]
        logger.debug("chosen action: %s", action)

        if action == "tag_player":
            # choose a target from the opposing team in the same zone
            potential_targets = [
                p
                for p in all_alive
                if p.team_color != player.team_color
                and p.current_zone == player.current_zone
                and p.final_lives > 0
            ]
            if potential_targets and player.final_shots > 0:
                target = random.choice(potential_targets)
                self._attempt_tag(
                    player.game_round, player, target, second=second
                )  # second is not tracked here
            else:
                # if no valid targets, change zone instead
                self._change_zone(player)
        elif action == "resupply_ally":
            # choose a teammate in the same zone to resupply
            potential_teammates = [
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.current_zone == player.current_zone
                and p != player
                and p.final_lives > 0
            ]
            if potential_teammates:
                teammate = random.choice(potential_teammates)
                self._attempt_resupply(player, teammate, second)
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
            self._change_zone(player)
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
                self._change_zone(player)

    def _change_zone(self, player):
        if player.current_zone == 1:
            # 50/50 chance to go to either adjacent zone
            player.current_zone = random.choice([0, 2])
        else:
            player.current_zone = 1

    def _attempt_tag(self, game_round, attacker, defender, second):
        """Simulate a tag attempt between two players and record events on game_round"""
        if attacker.final_shots <= 0 or defender.final_lives <= 0:
            return

        # Get role modifiers
        # TODO: make sure this works? we should get the player role and modifiers based on that
        att_mods = self.role_modifiers.get(
            attacker.player.role, self.role_modifiers["scout"]
        )
        def_mods = self.role_modifiers.get(
            defender.player.role, self.role_modifiers["scout"]
        )

        # Calculate hit probability
        # TODO: accuracy and evasion should be on player model
        # base_accuracy = 0.6  # 60% for now, will change later to
        accuracy = attacker.player.accuracy
        # accuracy = base_accuracy * att_mods.get("accuracy", 1.0)
        evasion = def_mods.get("survival", 1.0)

        hit_chance = accuracy / evasion
        hit_chance = max(0.1, min(0.95, hit_chance))  # Clamp between 10% and 95%

        # Use a shot, except if ammo, they have unlimited
        if attacker.role != "ammo":
            attacker.final_shots -= 1
        attacker.save()
        rolled_chance = (
            random.random()
        )  # may need to tweak this, should be between 1-100

        if rolled_chance < hit_chance:

            # TODO: we also want to randomize/log where the defender was tagged

            # Hit! Remove defender life and award points
            defender.shields = max(
                0, defender.shields - attacker.shot_power
            )  # lose shields proportional to shot power
            if defender.shields == 0:
                defender.final_lives -= 1
                defender.last_downed_time = second  # set downed time for respawn logic
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
            GameEvent.objects.create(
                game_round=game_round,
                timestamp=second,
                event_type="tag",
                actor=attacker.player,
                target=defender.player,
                points_awarded=100,
                description=f"{attacker.player.name} zaps {defender.player.name}",
                metadata={
                    "attacker_points": attacker.points_scored - 100,  # before tag
                    "attacker_lives": attacker.final_lives,
                    "attacker_shots": attacker.final_shots + 1,  # before tag
                    "defender_points": defender.points_scored + 20,  # before tag
                    "defender_lives": defender.final_lives + 1,  # before tag
                    "defender_shots": defender.final_shots,
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
            "resupply attempt: %s to %s, %s, %s/%s shots, %s/%s lives",
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
                    "tagger_points": tagger.points_scored,  # before resupply
                    "tagger_lives": tagger.final_lives,
                    "tagger_shots": tagger.final_shots,
                    "teammate_points": teammate.points_scored,
                    "teammate_lives": teammate.final_lives,
                    "teammate_shots": teammate.final_shots
                    - resupply_amount,  # before resupply
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
                    "tagger_points": tagger.points_scored,  # before resupply
                    "tagger_lives": tagger.final_lives,
                    "tagger_shots": tagger.final_shots,
                    "teammate_points": teammate.points_scored,
                    "teammate_lives": teammate.final_lives
                    - resupply_amount,  # before resupply
                    "teammate_shots": teammate.final_shots,
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
        ):
            # roll if defender dodges
            dodge_chance = 0.2  # base 20% chance to dodge
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
                        "attacker_points": attacker.points_scored,
                        "attacker_lives": attacker.final_lives,
                        "attacker_shots": attacker.final_shots,
                        "defender_points": defender.points_scored,
                        "defender_lives": defender.final_lives,
                        "defender_shots": defender.final_shots,
                    },
                )
                return

            # Defender does not dodge, schedule missile completion
            delay = random.randint(1, 2)  # 1-2 second delay
            logger.debug("about to complete missile")
            self._complete_missile(attacker, defender, second + delay)
            return
        return

    def _complete_missile(self, attacker, defender, second):
        """Simulate finishing missle on opponent"""
        if attacker.is_active_at(second) and defender.is_taggable_at(second):
            # normalize role checks (roles are stored lowercase elsewhere)
            if str(defender.role).lower() in ("commander", "heavy"):
                defender.shields = 3
            else:
                defender.shields = 1
            defender.points_scored -= 100
            defender.final_lives -= 2
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
                    "attacker_points": attacker.points_scored
                    - 500,  # before missile hit
                    "attacker_lives": attacker.final_lives,
                    "attacker_shots": attacker.final_shots,
                    "defender_points": defender.points_scored
                    + 100,  # before missile hit
                    "defender_lives": defender.final_lives + 2,  # before missile hit
                    "defender_shots": defender.final_shots,
                },
            )
            logger.debug("missile hit completed")

    def _use_special(self, player_state, second):
        """Simulate using a special ability"""
        # get player_state.role
        # depending on role check special point count
        # if enough specials, do action based on role.
        return None

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
            player_state.final_special += 5
            player_state.save()
            return True
        return False

    def _missile_base(self, player_state, base_id, second):
        """Simulate using a missile on a base target"""
        player_state.missiles_fired += 1
        player_state.final_missiles -= 1
        if base_id == "neutral":
            player_state.neutral_base_destroyed = True
        else:
            player_state.opposing_base_destroyed = True
        player_state.points_scored += 1001  # base destroy score

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
