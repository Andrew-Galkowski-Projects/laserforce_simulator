from django.test import TestCase
from teams.models import Team, Player


class RosterValidationTests(TestCase):
    """Test FIX-01: Enforce Scout-only role doubling in rosters."""

    def setUp(self):
        """Create a team and players for testing."""
        self.team = Team.objects.create(name="Test Team")
        self.commander = Player.objects.create(team=self.team, name="Commander Player")
        self.heavy = Player.objects.create(team=self.team, name="Heavy Player")
        self.scout1 = Player.objects.create(team=self.team, name="Scout 1")
        self.scout2 = Player.objects.create(team=self.team, name="Scout 2")
        self.medic = Player.objects.create(team=self.team, name="Medic Player")
        self.ammo = Player.objects.create(team=self.team, name="Ammo Player")

    def test_valid_roster_with_two_scouts(self):
        """Valid: Standard SM5 roster with two different Scouts."""
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()

        errors = self.team.roster_errors
        self.assertEqual(errors, [], f"Valid roster should have no errors, got: {errors}")

    def test_valid_roster_with_same_scout_twice(self):
        """Valid: Same Scout player in both Scout slots (edge case allowed by model)."""
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout1  # Same scout twice
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()

        errors = self.team.roster_errors
        # Same scout twice should be allowed (scout-only rule permits doubling)
        self.assertEqual(errors, [], f"Same Scout twice should be allowed, got: {errors}")

    def test_invalid_roster_commander_twice(self):
        """Invalid: Commander appears in two different slots."""
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.commander  # Commander also in heavy slot!
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()

        errors = self.team.roster_errors
        error_str = " ".join(errors)
        self.assertIn("Commander", error_str)
        self.assertIn("cannot appear twice", error_str)

    def test_invalid_roster_heavy_in_scout_slot(self):
        """Invalid: Heavy player assigned to Scout slot (mixed roles)."""
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.heavy  # Heavy also in scout slot
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()

        errors = self.team.roster_errors
        error_str = " ".join(errors)
        self.assertIn("Heavy", error_str)
        self.assertIn("cannot appear twice", error_str)

    def test_invalid_roster_medic_in_scout_slot(self):
        """Invalid: Medic player assigned to Scout slot (mixed roles)."""
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.medic  # Medic in scout slot
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic  # Medic also in medic slot
        self.team.slot_ammo = self.ammo
        self.team.save()

        errors = self.team.roster_errors
        error_str = " ".join(errors)
        self.assertIn("Medic", error_str)
        self.assertIn("cannot appear twice", error_str)

    def test_invalid_roster_ammo_in_scout_slot(self):
        """Invalid: Ammo player assigned to Scout slot (mixed roles)."""
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.ammo  # Ammo in scout slot
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo  # Ammo also in ammo slot
        self.team.save()

        errors = self.team.roster_errors
        error_str = " ".join(errors)
        self.assertIn("Ammo", error_str)
        self.assertIn("cannot appear twice", error_str)

    def test_invalid_roster_missing_slots(self):
        """Invalid: Missing required slots."""
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = None  # Missing scout 2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()

        errors = self.team.roster_errors
        self.assertIn("missing Scout 2", " ".join(errors))

    def test_invalid_roster_all_missing(self):
        """Invalid: All slots missing."""
        self.team.save()

        errors = self.team.roster_errors
        missing_errors = [e for e in errors if "missing" in e]
        self.assertEqual(len(missing_errors), 6, "Should have 6 missing slot errors")
        for slot_name in ["Commander", "Heavy", "Scout 1", "Scout 2", "Medic", "Ammo"]:
            self.assertIn(f"missing {slot_name}", " ".join(errors))

    def test_invalid_roster_player_from_different_team(self):
        """Invalid: Player assigned that belongs to a different team."""
        other_team = Team.objects.create(name="Other Team")
        other_player = Player.objects.create(team=other_team, name="Other Player")

        self.team.slot_commander = other_player
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()

        errors = self.team.roster_errors
        self.assertIn("does not belong to this team", " ".join(errors))

    def test_is_valid_roster_property(self):
        """Test is_valid_roster property reflects roster_errors."""
        # Invalid roster (missing heavy)
        self.team.slot_commander = self.commander
        self.team.slot_heavy = None
        self.team.save()
        self.assertFalse(self.team.is_valid_roster)

        # Valid roster
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        self.assertTrue(self.team.is_valid_roster)
