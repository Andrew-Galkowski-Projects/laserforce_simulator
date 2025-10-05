import random
from .models import Match, GameRound, PlayerRoundState, SingleRound
from teams.models import Player


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

        # Simulate the round
        round_result = self._simulate_round_combat(red_players, blue_players)

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

            state = PlayerRoundState.objects.create(
                game_round=game_round,
                team_color=team_color,
                role=player.role,
                player=player,
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

    def _simulate_round_combat(self, red_players, blue_players):
        """Simulate combat between two teams"""
        # Combat simulation over time (simplified)
        round_duration = 15 * 60  # 15 minutes in seconds

        # TODO: we want to simulate combat much faster than every 5 seconds but this is ok for now to test
        for second in range(0, round_duration, 5):  # Check every 5 seconds
            # Random combat events
            if random.random() < 0.7:  # 70% chance of combat per 5 second interval
                self._simulate_combat_exchange(red_players, blue_players)

            # Check for team eliminations
            red_alive = [p for p in red_players if p.final_lives > 0]
            blue_alive = [p for p in blue_players if p.final_lives > 0]

            if not red_alive or not blue_alive:
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

    def _simulate_combat_exchange(self, red_players, blue_players):
        """Simulate a single combat exchange between teams"""
        # Get alive players
        red_alive = [p for p in red_players if p.final_lives > 0 and p.final_shots > 0]
        blue_alive = [
            p for p in blue_players if p.final_lives > 0 and p.final_shots > 0
        ]

        if not red_alive or not blue_alive:
            return

        # Random combat
        for _ in range(random.randint(1, 3)):  # 1-3 tag attempts per exchange
            # TODO: improve this logic to be less random, we probably want
            if random.random() < 0.5:  # 50% chance red team attacks
                attacker = random.choice(red_alive)
                defenders = [p for p in blue_alive if p.final_lives > 0]
                if defenders:
                    defender = random.choice(defenders)
                    self._attempt_tag(attacker, defender)
            else:  # Blue team attacks
                attacker = random.choice(blue_alive)
                defenders = [p for p in red_alive if p.final_lives > 0]
                if defenders:
                    defender = random.choice(defenders)
                    self._attempt_tag(attacker, defender)

            # Update alive lists after potential eliminations
            red_alive = [
                p for p in red_players if p.final_lives > 0 and p.final_shots > 0
            ]
            blue_alive = [
                p for p in blue_players if p.final_lives > 0 and p.final_shots > 0
            ]

            if not red_alive or not blue_alive:
                break

    def _attempt_tag(self, attacker, defender):
        """Simulate a tag attempt between two players"""
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
        base_accuracy = 0.6  # 60% for now, will change later to
        # accuracy = attacker.player.accuracy
        accuracy = base_accuracy * att_mods.get("accuracy", 1.0)
        evasion = def_mods.get("survival", 1.0)

        hit_chance = accuracy / evasion
        hit_chance = max(0.1, min(0.8, hit_chance))  # Clamp between 10% and 80%

        # Use a shot
        attacker.final_shots -= 1
        attacker.save()

        if random.random() < hit_chance:

            # TODO: we also want to randomize/log where the defender was tagged

            # Hit! Remove defender life and award points
            defender.final_lives -= 1
            defender.times_tagged += 1
            defender.points_scored -= 20  # Penalty for being tagged
            defender.specific_tags[attacker.get_tag_id]["tagged_by"] += 1

            attacker.tags_made += 1
            attacker.points_scored += 100
            attacker.final_shots -= 1
            attacker.specific_tags[defender.get_tag_id]["tags"] += 1
            attacker.last_tag_entity

            # Save states
            attacker.save()
            defender.save()

    def _attempt_resupply(self, tagger, teammate):
        """Simulate a resupply action"""
        # determine the role of the tagger and teammate

        return None

    def _attempt_missile(self, attacker, defender):
        return None

    def _use_special(self, player_state):
        """Simulate using a special ability"""
        return None
    
    def _missile_base(self, player_state, base_id):
        """Simulate using a missile on a base target"""
        player_state.missiles_fired += 1
        player_state.final_missiles -= 1
        if base_id == "neutral":
            player_state.neutral_base_destroyed = True
        else:
            player_state.opposing_base_destroyed = True
        player_state.points_scored += 1001 # base destroy score

    # TODO: need to determine if we are choosing who to reset off of first or in method
    def _attempt_reset(self, player_state):
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
