import random


class SimpleMatchSimulator:
    """Basic match simulator with random results for Phase 2"""

    def __init__(self):
        self.base_points_range = (17000, 45000)  # Typical point range per round
        self.elimination_bonus = 10000  # Bonus for eliminating opposing team
        # elimination probability will be arena dependent in future
        self.elimination_probability = 0.15  # 15% chance of elimination

    def simulate_match(self, team_red, team_blue, match_type="friendly"):
        """Simulate a full 2-round match"""
        from .models import Match

        match = Match.objects.create(
            team_red=team_red, team_blue=team_blue, match_type=match_type
        )

        # Simulate Round 1 (team_red as red, team_blue as blue)
        round1_result = self._simulate_round()
        match.red_round1_points = round1_result["red_points"]
        match.blue_round1_points = round1_result["blue_points"]
        match.red_round1_eliminated = round1_result["red_eliminated"]
        match.blue_round1_eliminated = round1_result["blue_eliminated"]

        # Simulate Round 2 (teams switch colors)
        round2_result = self._simulate_round()
        match.red_round2_points = round2_result["blue_points"]  # Switched
        match.blue_round2_points = round2_result["red_points"]  # Switched
        match.red_round2_eliminated = round2_result["blue_eliminated"]  # Switched
        match.blue_round2_eliminated = round2_result["red_eliminated"]  # Switched

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
        match.save()  # This will trigger winner calculation

        return match

    def simulate_single_round(self, team_red, team_blue):
        """Simulate a single round game"""
        from .models import SingleRound

        single_round = SingleRound.objects.create(
            team_red=team_red, team_blue=team_blue
        )

        result = self._simulate_round()
        single_round.red_points = result["red_points"]
        single_round.blue_points = result["blue_points"]
        single_round.red_eliminated = result["red_eliminated"]
        single_round.blue_eliminated = result["blue_eliminated"]
        single_round.is_completed = True
        single_round.save()  # This will trigger winner calculation

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


class AdvancedMatchSimulator(SimpleMatchSimulator):
    """Enhanced simulator that considers team composition (for future phases)"""

    def __init__(self):
        super().__init__()
        # Future: Add role-based modifiers, player skill factors, etc.

    def _calculate_team_strength(self, team):
        """Calculate relative team strength based on roster (placeholder for future)"""
        # For now, just return random strength
        # Future: Factor in player skills, role distribution, etc.
        return random.uniform(0.8, 1.2)

    def _simulate_round_with_team_factors(self, team_red, team_blue):
        """Enhanced round simulation considering team strengths"""
        red_strength = self._calculate_team_strength(team_red)
        blue_strength = self._calculate_team_strength(team_blue)

        # Adjust base points based on team strength
        red_base = int(random.randint(*self.base_points_range) * red_strength)
        blue_base = int(random.randint(*self.base_points_range) * blue_strength)

        # Adjust elimination probability based on relative strength
        strength_diff = red_strength - blue_strength
        red_elim_prob = max(0.05, self.elimination_probability - (strength_diff * 0.1))
        blue_elim_prob = max(0.05, self.elimination_probability + (strength_diff * 0.1))

        red_eliminated = random.random() < red_elim_prob
        blue_eliminated = random.random() < blue_elim_prob

        # Apply elimination penalties
        if red_eliminated:
            red_base = int(red_base * random.uniform(0.3, 0.7))
        if blue_eliminated:
            blue_base = int(blue_base * random.uniform(0.3, 0.7))

        # Prevent double elimination
        if red_eliminated and blue_eliminated:
            if random.choice([True, False]):
                red_eliminated = False
            else:
                blue_eliminated = False

        return {
            "red_points": red_base,
            "blue_points": blue_base,
            "red_eliminated": red_eliminated,
            "blue_eliminated": blue_eliminated,
        }
