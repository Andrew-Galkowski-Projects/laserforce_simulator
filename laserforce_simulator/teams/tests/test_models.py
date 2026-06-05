import random

from django.test import TestCase

from teams.constants import LASERFORCE_SITES, PLAYER_NAMES
from teams.models import Player, Team, _random_player_profile


class RandomPlayerProfileTest(TestCase):
    """Verify _random_player_profile() returns a valid, fully-populated profile dict."""

    def test_returns_all_required_keys(self):
        profile = _random_player_profile()
        self.assertEqual(
            set(profile.keys()),
            {
                "name",
                "age",
                "started_playing_age",
                "total_games",
                "home_site",
                "height",
            },
        )

    def test_name_drawn_from_player_names(self):
        random.seed(0)
        for _ in range(20):
            profile = _random_player_profile()
            self.assertIn(profile["name"], PLAYER_NAMES)

    def test_age_in_valid_range(self):
        random.seed(1)
        for _ in range(50):
            profile = _random_player_profile()
            self.assertGreaterEqual(profile["age"], 16)
            self.assertLessEqual(profile["age"], 50)

    def test_started_playing_age_at_most_current_age(self):
        random.seed(2)
        for _ in range(50):
            profile = _random_player_profile()
            self.assertGreaterEqual(profile["started_playing_age"], 16)
            self.assertLessEqual(profile["started_playing_age"], profile["age"])

    def test_total_games_in_valid_range(self):
        random.seed(3)
        for _ in range(50):
            profile = _random_player_profile()
            self.assertGreaterEqual(profile["total_games"], 0)
            self.assertLessEqual(profile["total_games"], 5000)

    def test_home_site_drawn_from_laserforce_sites(self):
        random.seed(4)
        for _ in range(20):
            profile = _random_player_profile()
            self.assertIn(profile["home_site"], LASERFORCE_SITES)

    def test_height_format_is_feet_and_inches(self):
        random.seed(5)
        for _ in range(50):
            profile = _random_player_profile()
            height = profile["height"]
            self.assertRegex(
                height, r"^\d+'\d+\"$", f"Unexpected height format: {height}"
            )

    def test_height_in_valid_range(self):
        random.seed(6)
        for _ in range(100):
            profile = _random_player_profile()
            feet, inches_str = profile["height"].split("'")
            total_inches = int(feet) * 12 + int(inches_str.rstrip('"'))
            self.assertGreaterEqual(total_inches, 48, "Height below 4'0\"")
            self.assertLessEqual(total_inches, 82, "Height above 6'10\"")


class PlayerStatForSimulationTest(TestCase):
    """STAT-02: Player.stat_for_simulation returns boosted or raw stat values."""

    def setUp(self):
        self.team = Team.objects.create(name="Stat Test Team")

    def _player(
        self, name, preferred_roles=None, accuracy=50, survival=50, player_awareness=50
    ):
        return Player.objects.create(
            team=self.team,
            name=name,
            preferred_roles=preferred_roles or [],
            accuracy=accuracy,
            survival=survival,
            player_awareness=player_awareness,
        )

    # 1. Preferred role → returns min(int(raw * 1.2), 100)
    def test_preferred_role_returns_boosted_value(self):
        player = self._player("Boosted", preferred_roles=["scout"], accuracy=50)
        result = player.stat_for_simulation("accuracy", "scout")
        self.assertEqual(result, 60)  # int(50 * 1.2) = 60, min(60, 100) = 60

    # 2. Non-preferred role → returns raw value unchanged
    def test_non_preferred_role_returns_raw_value(self):
        player = self._player("Unboosted", preferred_roles=["scout"], accuracy=50)
        result = player.stat_for_simulation("accuracy", "commander")
        self.assertEqual(result, 50)

    # 3. Multiple preferred roles — boost applies when current role is any of them
    def test_multiple_preferred_roles_boost_applies_for_any_match(self):
        player = self._player(
            "MultiRole",
            preferred_roles=["scout", "medic"],
            accuracy=50,
        )
        # Both roles in the list should get the boost
        self.assertEqual(player.stat_for_simulation("accuracy", "scout"), 60)
        self.assertEqual(player.stat_for_simulation("accuracy", "medic"), 60)
        # A role not in the list should not
        self.assertEqual(player.stat_for_simulation("accuracy", "heavy"), 50)

    # 4. No preferred roles (empty list) → returns raw value
    def test_empty_preferred_roles_returns_raw_value(self):
        player = self._player("NoPref", preferred_roles=[], accuracy=70)
        result = player.stat_for_simulation("accuracy", "scout")
        self.assertEqual(result, 70)

    # 5. Stat already at 100 → capped at 100 (not 120)
    def test_stat_at_100_is_capped_at_100(self):
        player = self._player("Capped", preferred_roles=["commander"], accuracy=100)
        result = player.stat_for_simulation("accuracy", "commander")
        self.assertEqual(result, 100)  # min(int(100 * 1.2), 100) = min(120, 100) = 100

    # 6. Stat at 84 → min(100, int(84*1.2)) = min(100, 100) = 100  (boundary test)
    def test_stat_at_84_hits_cap_exactly(self):
        player = self._player("Boundary", preferred_roles=["heavy"], accuracy=84)
        result = player.stat_for_simulation("accuracy", "heavy")
        # int(84 * 1.2) == 100, so the result should be 100
        self.assertEqual(result, 100)

    # 7. Invalid stat name → raises AttributeError
    def test_invalid_stat_name_raises_attribute_error(self):
        player = self._player("BadStat", preferred_roles=["scout"])
        with self.assertRaises(AttributeError):
            player.stat_for_simulation("nonexistent_stat", "scout")

    # Extra: verify a different stat field works too (survival)
    def test_survival_stat_is_boosted_for_preferred_role(self):
        player = self._player("SurvivalBoost", preferred_roles=["ammo"], survival=75)
        result = player.stat_for_simulation("survival", "ammo")
        self.assertEqual(result, 90)  # int(75 * 1.2) = 90


class RosterValidationTests(TestCase):
    """Test FIX-01: Enforce Scout-only role doubling in rosters."""

    def setUp(self):
        self.team = Team.objects.create(name="Test Team")
        self.commander = Player.objects.create(team=self.team, name="Commander Player")
        self.heavy = Player.objects.create(team=self.team, name="Heavy Player")
        self.scout1 = Player.objects.create(team=self.team, name="Scout 1")
        self.scout2 = Player.objects.create(team=self.team, name="Scout 2")
        self.medic = Player.objects.create(team=self.team, name="Medic Player")
        self.ammo = Player.objects.create(team=self.team, name="Ammo Player")

    def test_valid_roster_with_two_scouts(self):
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        errors = self.team.roster_errors
        self.assertEqual(
            errors, [], f"Valid roster should have no errors, got: {errors}"
        )

    def test_invalid_roster_same_scout_twice(self):
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout1
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        errors = self.team.roster_errors
        self.assertIn("cannot fill multiple slots", " ".join(errors))

    def test_invalid_roster_commander_twice(self):
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.commander
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        errors = self.team.roster_errors
        self.assertIn("cannot fill multiple slots", " ".join(errors))

    def test_invalid_roster_heavy_in_scout_slot(self):
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.heavy
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        errors = self.team.roster_errors
        self.assertIn("cannot fill multiple slots", " ".join(errors))

    def test_invalid_roster_medic_in_scout_slot(self):
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.medic
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        errors = self.team.roster_errors
        self.assertIn("cannot fill multiple slots", " ".join(errors))

    def test_invalid_roster_ammo_in_scout_slot(self):
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.ammo
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        errors = self.team.roster_errors
        self.assertIn("cannot fill multiple slots", " ".join(errors))

    def test_invalid_roster_missing_slots(self):
        self.team.slot_commander = self.commander
        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = None
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        errors = self.team.roster_errors
        self.assertIn("missing Scout 2", " ".join(errors))

    def test_invalid_roster_all_missing(self):
        self.team.save()
        errors = self.team.roster_errors
        missing_errors = [e for e in errors if "missing" in e]
        self.assertEqual(len(missing_errors), 6, "Should have 6 missing slot errors")
        for slot_name in ["Commander", "Heavy", "Scout 1", "Scout 2", "Medic", "Ammo"]:
            self.assertIn(f"missing {slot_name}", " ".join(errors))

    def test_invalid_roster_player_from_different_team(self):
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
        self.team.slot_commander = self.commander
        self.team.slot_heavy = None
        self.team.save()
        self.assertFalse(self.team.is_valid_roster)

        self.team.slot_heavy = self.heavy
        self.team.slot_scout_1 = self.scout1
        self.team.slot_scout_2 = self.scout2
        self.team.slot_medic = self.medic
        self.team.slot_ammo = self.ammo
        self.team.save()
        self.assertTrue(self.team.is_valid_roster)


# ===========================================================================
# LG-02x-1 — Team.is_draw_team relaxes the belongs-to-team roster rule
# ===========================================================================
#
# NEW class appended below (existing classes above are NOT modified). Seam
# contract: ``.claude/worktrees/lg-02x-1-seam-contract.md`` §1b / §1d / §7.
#
# A draw team (``is_draw_team=True``) references BORROWED Players via its slot
# FKs (``player.team_id != team.pk``); ownership lives on
# ``TournamentPlayerEntry``, and ``Player.team`` is never reassigned by the
# draw. So a draw team with foreign players has NO "does not belong" error —
# but the duplicate-player check and the Scout-only role-distribution check
# STILL apply (only the ownership check is relaxed). A NON-draw team with a
# foreign player STILL errors (the default behaviour is unchanged).
#
# These assertions WILL fail until the Code agent lands the ``is_draw_team``
# field + the ``if not self.is_draw_team:`` wrap around the belongs-to-team
# loop in ``Team.roster_errors``.


class DrawTeamRosterRelaxationTests(TestCase):
    """The borrowed-player ownership check is relaxed for ``is_draw_team`` teams
    only; duplicate + role-distribution checks remain enforced."""

    def setUp(self):
        # Six Players that belong to a DIFFERENT team — "borrowed" players, as
        # a drawn Team references them via slot FKs without owning them.
        self.owner = Team.objects.create(name="Owner Team")
        self.p_cmd = Player.objects.create(team=self.owner, name="Borrowed Commander")
        self.p_hvy = Player.objects.create(team=self.owner, name="Borrowed Heavy")
        self.p_s1 = Player.objects.create(team=self.owner, name="Borrowed Scout 1")
        self.p_s2 = Player.objects.create(team=self.owner, name="Borrowed Scout 2")
        self.p_med = Player.objects.create(team=self.owner, name="Borrowed Medic")
        self.p_ammo = Player.objects.create(team=self.owner, name="Borrowed Ammo")

    def _fill_slots(self, team, *, scout_2=None) -> None:
        team.slot_commander = self.p_cmd
        team.slot_heavy = self.p_hvy
        team.slot_scout_1 = self.p_s1
        team.slot_scout_2 = scout_2 if scout_2 is not None else self.p_s2
        team.slot_medic = self.p_med
        team.slot_ammo = self.p_ammo
        team.save()

    def test_is_draw_team_defaults_false(self):
        team = Team.objects.create(name="Default Flag")
        self.assertFalse(team.is_draw_team)

    def test_is_draw_team_persists(self):
        team = Team.objects.create(name="Draw Flag", is_draw_team=True)
        team.refresh_from_db()
        self.assertTrue(team.is_draw_team)

    def test_draw_team_with_borrowed_players_has_no_belong_error(self):
        # Every slot player belongs to ``self.owner`` (team_id != draw_team.pk),
        # yet a draw team produces NO "does not belong" error.
        draw_team = Team.objects.create(name="Draw Team A", is_draw_team=True)
        self._fill_slots(draw_team)
        errors = draw_team.roster_errors
        self.assertNotIn(
            "does not belong to this team",
            " ".join(errors),
            f"a draw team must not flag borrowed players; got: {errors}",
        )

    def test_draw_team_with_full_borrowed_roster_is_valid(self):
        # Relaxation makes a fully-slotted borrowed roster a VALID draw roster.
        draw_team = Team.objects.create(name="Draw Team Valid", is_draw_team=True)
        self._fill_slots(draw_team)
        self.assertEqual(
            draw_team.roster_errors,
            [],
            "a complete borrowed draw roster must have no errors",
        )
        self.assertTrue(draw_team.is_valid_roster)

    def test_draw_team_with_duplicate_player_still_errors(self):
        # The duplicate-player (multiple-slots) check is KEPT for draw teams.
        draw_team = Team.objects.create(name="Draw Team Dup", is_draw_team=True)
        # Same borrowed player in both Scout slots.
        self._fill_slots(draw_team, scout_2=self.p_s1)
        errors = draw_team.roster_errors
        self.assertIn(
            "cannot fill multiple slots",
            " ".join(errors),
            f"draw team must still reject a duplicate player; got: {errors}",
        )

    def test_draw_team_with_third_non_scout_role_still_errors(self):
        # The Scout-only role-distribution check is KEPT for draw teams: a 3rd
        # non-Scout role (Commander filling a Scout slot) still errors.
        draw_team = Team.objects.create(name="Draw Team Role", is_draw_team=True)
        # Put the borrowed Commander into the scout_2 slot -> the Commander role
        # would appear in two slots (commander + a scout slot), which is the
        # non-Scout doubling the distribution check rejects.
        draw_team.slot_commander = self.p_cmd
        draw_team.slot_heavy = self.p_hvy
        draw_team.slot_scout_1 = self.p_s1
        draw_team.slot_scout_2 = self.p_med  # medic in a scout slot -> non-Scout dup
        draw_team.slot_medic = self.p_med
        draw_team.slot_ammo = self.p_ammo
        draw_team.save()
        errors = draw_team.roster_errors
        self.assertIn(
            "cannot fill multiple slots",
            " ".join(errors),
            f"draw team must still enforce Scout-only doubling; got: {errors}",
        )

    def test_non_draw_team_with_foreign_player_still_errors(self):
        # The default (is_draw_team=False) behaviour is UNCHANGED: a non-draw
        # team referencing a foreign player still flags "does not belong".
        non_draw = Team.objects.create(name="Regular Team")  # is_draw_team False
        self._fill_slots(non_draw)
        errors = non_draw.roster_errors
        self.assertIn(
            "does not belong to this team",
            " ".join(errors),
            f"a non-draw team must still flag foreign players; got: {errors}",
        )
