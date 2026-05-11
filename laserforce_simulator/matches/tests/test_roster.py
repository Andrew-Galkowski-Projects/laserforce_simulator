"""
Team and player model validation tests: roster slots, bench, active_roster,
Player.clean(), and roster_errors.
"""

import pytest

from teams.models import Team, Player
from matches.tests.conftest import make_team_with_slots


@pytest.mark.django_db
class TestRosterValidation:
    def _make_team(self, name="Test"):
        return Team.objects.create(name=name)

    def _add_player(self, team, name=None):
        return Player.objects.create(
            team=team, name=name or f"player-{team.players.count()}"
        )

    def _fill_all_slots(self, team, players=None):
        """Assign 6 players to all slots; creates them if not provided."""
        if players is None:
            players = [self._add_player(team, f"p{i}") for i in range(6)]
        team.slot_commander = players[0]
        team.slot_heavy = players[1]
        team.slot_scout_1 = players[2]
        team.slot_scout_2 = players[3]
        team.slot_medic = players[4]
        team.slot_ammo = players[5]
        team.save()
        return players

    # --- is_valid_roster ---

    def test_valid_roster_all_slots_filled(self):
        team = self._make_team("Valid")
        self._fill_all_slots(team)
        assert team.is_valid_roster

    def test_invalid_roster_missing_ammo_slot(self):
        team = self._make_team("MissingAmmo")
        players = [self._add_player(team, f"p{i}") for i in range(5)]
        team.slot_commander = players[0]
        team.slot_heavy = players[1]
        team.slot_scout_1 = players[2]
        team.slot_scout_2 = players[3]
        team.slot_medic = players[4]
        team.save()
        assert not team.is_valid_roster

    def test_invalid_roster_missing_scout_2(self):
        team = self._make_team("NoScout2")
        players = [self._add_player(team, f"p{i}") for i in range(5)]
        team.slot_commander = players[0]
        team.slot_heavy = players[1]
        team.slot_scout_1 = players[2]
        team.slot_medic = players[3]
        team.slot_ammo = players[4]
        team.save()
        assert not team.is_valid_roster

    def test_invalid_roster_duplicate_player_in_two_slots(self):
        team = self._make_team("Dupe")
        players = [self._add_player(team, f"p{i}") for i in range(5)]
        team.slot_commander = players[0]
        team.slot_heavy = players[0]  # same player!
        team.slot_scout_1 = players[1]
        team.slot_scout_2 = players[2]
        team.slot_medic = players[3]
        team.slot_ammo = players[4]
        team.save()
        assert not team.is_valid_roster

    def test_bench_players_are_unslotted_team_members(self):
        team = self._make_team("WithBench")
        players = [self._add_player(team, f"p{i}") for i in range(8)]
        self._fill_all_slots(team, players[:6])
        bench = team.bench_players
        assert len(bench) == 2
        bench_ids = {p.pk for p in bench}
        assert players[6].pk in bench_ids
        assert players[7].pk in bench_ids

    def test_active_players_excludes_bench(self):
        team = self._make_team("ActiveVsBench")
        players = [self._add_player(team, f"p{i}") for i in range(8)]
        self._fill_all_slots(team, players[:6])
        active = team.active_players
        assert len(active) == 6
        active_ids = {p.pk for p in active}
        assert players[6].pk not in active_ids

    def test_active_roster_contains_correct_roles(self):
        team = self._make_team("Roster")
        players = [self._add_player(team, f"p{i}") for i in range(6)]
        self._fill_all_slots(team, players)
        roster = team.active_roster
        assert len(roster) == 6
        roles = [r for r, _ in roster]
        assert roles.count("scout") == 2
        assert "commander" in roles
        assert "heavy" in roles
        assert "medic" in roles
        assert "ammo" in roles

    # --- Player.clean() (preferred_roles validation) ---

    def test_clean_rejects_invalid_preferred_role(self):
        from django.core.exceptions import ValidationError

        team = self._make_team("CleanBad")
        p = Player(team=team, name="bad", preferred_roles=["not_a_real_role"])
        with pytest.raises(ValidationError):
            p.clean()

    def test_clean_accepts_valid_preferred_roles(self):
        team = self._make_team("CleanGood")
        p = Player(team=team, name="good", preferred_roles=["commander", "scout"])
        p.clean()  # should not raise

    def test_clean_accepts_empty_preferred_roles(self):
        team = self._make_team("CleanEmpty")
        p = Player(team=team, name="empty", preferred_roles=[])
        p.clean()  # should not raise

    # --- roster_errors ---

    def test_roster_errors_empty_for_valid_roster(self):
        team = self._make_team("ErrValid")
        self._fill_all_slots(team)
        assert team.roster_errors == []

    def test_roster_errors_reports_missing_slot(self):
        team = self._make_team("ErrMissing")
        players = [self._add_player(team, f"p{i}") for i in range(5)]
        team.slot_commander = players[0]
        team.slot_heavy = players[1]
        team.slot_scout_1 = players[2]
        team.slot_scout_2 = players[3]
        team.slot_medic = players[4]
        team.save()
        errors = team.roster_errors
        assert any("Ammo" in e for e in errors)

    def test_roster_errors_reports_duplicate_player(self):
        team = self._make_team("ErrDupe")
        players = [self._add_player(team, f"p{i}") for i in range(5)]
        team.slot_commander = players[0]
        team.slot_heavy = players[0]  # duplicate
        team.slot_scout_1 = players[1]
        team.slot_scout_2 = players[2]
        team.slot_medic = players[3]
        team.slot_ammo = players[4]
        team.save()
        errors = team.roster_errors
        assert any("multiple slots" in e for e in errors)

    def test_roster_errors_reports_all_missing_when_no_slots(self):
        team = self._make_team("ErrFew")
        errors = team.roster_errors
        assert len(errors) == 6
