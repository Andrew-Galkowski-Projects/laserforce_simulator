"""
MVP score formula tests for all five roles.
"""

import pytest

from matches.models import GameRound, PlayerRoundState
from matches.sim_helpers.score_calculator import calculate_mvp
from matches.tests.conftest import make_team_with_slots


@pytest.mark.django_db
class TestMVP:
    def setup_method(self):
        self.team_red, self.players_red = make_team_with_slots("MVPRed")
        self.team_blue, self.players_blue = make_team_with_slots("MVPBlue")
        self.gr = GameRound.objects.create(
            team_red=self.team_red,
            team_blue=self.team_blue,
            round_number=1,
        )

    def _state(self, player, team_color, role, **kwargs):
        kwargs.setdefault("final_lives", 3)
        kwargs.setdefault("final_shots", 10)
        return PlayerRoundState.objects.create(
            game_round=self.gr,
            player=player,
            team_color=team_color,
            role=role,
            **kwargs,
        )

    # --- get_accuracy ---

    def test_accuracy_zero_shots(self):
        s = self._state(
            self.players_red["scout"], "red", "scout", tags_made=0, shots_missed=0
        )
        assert s.get_accuracy == 0

    def test_accuracy_all_hits(self):
        s = self._state(
            self.players_red["scout"], "red", "scout", tags_made=10, shots_missed=0
        )
        assert s.get_accuracy == 100

    def test_accuracy_three_quarters(self):
        s = self._state(
            self.players_red["scout"], "red", "scout", tags_made=75, shots_missed=25
        )
        assert s.get_accuracy == 75

    # --- All-roles components ---

    def test_accuracy_bonus_100pct(self):
        s = self._state(
            self.players_red["ammo"],
            "red",
            "ammo",
            tags_made=10,
            shots_missed=0,
            points_scored=0,
            specials_used=0,
        )
        assert s.get_mvp == 10.0

    def test_medic_hit_bonus(self):
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            final_medic_hits=3,
            points_scored=0,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 3.0

    def test_enemy_nuke_cancel_bonus(self):
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            enemy_nuke_cancels=2,
            points_scored=0,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 6.0

    def test_ally_nuke_cancel_penalty(self):
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            ally_nuke_cancels=1,
            points_scored=0,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == -3.0

    def test_times_missiled_penalty(self):
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            times_missiled=3,
            points_scored=0,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == -3.0

    def test_elimination_penalty_non_medic(self):
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            final_lives=0,
            points_scored=0,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == -1.0

    def test_no_elimination_penalty_for_medic(self):
        s = self._state(
            self.players_red["medic"],
            "red",
            "medic",
            final_lives=0,
            points_scored=0,
            specials_used=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 0.0

    def test_elimination_bonus_minimum(self):
        # TIME-01: eliminated_at is now ticks. 720 s → 1440 ticks; 360 ticks
        # (180 s) remaining → exactly 4 pts (no extra above the 360-tick / 3-min
        # threshold). Expected MVP value is unchanged by the unit migration —
        # this is the elimination-bonus formula genuinely under test.
        self.gr.blue_team_eliminated = True
        self.gr.eliminated_at = 1440
        self.gr.save()
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            points_scored=0,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 4.0

    def test_elimination_bonus_with_extra_time(self):
        # TIME-01: eliminated_at is now ticks. 540 s → 1080 ticks; 720 ticks
        # (360 s) remaining → 4 + (720 - 360 extra ticks)/120 = 7.0. Expected
        # MVP value is unchanged by the unit migration — this is the
        # elimination-bonus formula genuinely under test.
        self.gr.blue_team_eliminated = True
        self.gr.eliminated_at = 1080
        self.gr.save()
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            points_scored=0,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 7.0

    # --- Commander ---

    def test_commander_missile_bonus(self):
        s = self._state(
            self.players_red["commander"],
            "red",
            "commander",
            missiles_landed=3,
            specials_used=0,
            own_specials_cancelled=0,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 3.0

    def test_commander_nuke_bonus(self):
        s = self._state(
            self.players_red["commander"],
            "red",
            "commander",
            specials_used=2,
            own_specials_cancelled=0,
            missiles_landed=0,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 2.0

    def test_commander_own_nuke_cancelled_penalty(self):
        # 2 nukes used, 1 cancelled: successful=1 (+1), cancelled=1 (-1) → net 0
        s = self._state(
            self.players_red["commander"],
            "red",
            "commander",
            specials_used=2,
            own_specials_cancelled=1,
            missiles_landed=0,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 0.0

    def test_commander_points_bonus(self):
        s = self._state(
            self.players_red["commander"],
            "red",
            "commander",
            points_scored=12_000,
            specials_used=0,
            own_specials_cancelled=0,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 2.0

    # --- Heavy ---

    def test_heavy_missile_bonus(self):
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            missiles_landed=2,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 4.0

    def test_heavy_points_bonus(self):
        s = self._state(
            self.players_red["heavy"],
            "red",
            "heavy",
            points_scored=9_000,
            missiles_landed=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 2.0

    # --- Scout ---

    def test_scout_cmd_heavy_hit_bonus(self):
        cmd_key = str(PlayerRoundState.tag_id.blue_commander)
        hvy_key = str(PlayerRoundState.tag_id.blue_heavy)
        # 80% accuracy → 8.0; (5+3)*0.2 = 1.6; total = 9.6
        s = self._state(
            self.players_red["scout"],
            "red",
            "scout",
            specific_tags={
                cmd_key: {"tags": 5, "tagged_by": 0},
                hvy_key: {"tags": 3, "tagged_by": 0},
            },
            tags_made=8,
            shots_missed=2,
            points_scored=0,
        )
        assert s.get_mvp == 9.6

    def test_scout_points_bonus(self):
        s = self._state(
            self.players_red["scout"],
            "red",
            "scout",
            points_scored=8_000,
            specific_tags={},
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 2.0

    # --- Ammo ---

    def test_ammo_power_boost_bonus(self):
        s = self._state(
            self.players_red["ammo"],
            "red",
            "ammo",
            specials_used=3,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 9.0

    def test_ammo_points_bonus(self):
        s = self._state(
            self.players_red["ammo"],
            "red",
            "ammo",
            points_scored=5_000,
            specials_used=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 2.0

    # --- Medic ---

    def test_medic_power_boost_bonus(self):
        # 4 specials * 3 = 12; survival bonus +2 = 14
        s = self._state(
            self.players_red["medic"],
            "red",
            "medic",
            specials_used=4,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
            final_lives=3,
        )
        assert s.get_mvp == 14.0

    def test_medic_survival_bonus(self):
        s = self._state(
            self.players_red["medic"],
            "red",
            "medic",
            final_lives=5,
            specials_used=0,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 2.0

    def test_medic_no_survival_bonus_when_eliminated(self):
        s = self._state(
            self.players_red["medic"],
            "red",
            "medic",
            final_lives=0,
            specials_used=0,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 0.0

    def test_medic_points_bonus(self):
        # 2 * (4000 - 2000) / 1000 = 4.0; no survival (lives=0)
        s = self._state(
            self.players_red["medic"],
            "red",
            "medic",
            points_scored=4_000,
            specials_used=0,
            final_lives=0,
            tags_made=0,
            shots_missed=0,
        )
        assert s.get_mvp == 4.0


@pytest.mark.django_db
class TestCalculateMvp:
    """Tests for the pure calculate_mvp function extracted from PlayerRoundState."""

    def setup_method(self):
        self.team_red, self.players_red = make_team_with_slots("CalcMVPRed")
        self.team_blue, self.players_blue = make_team_with_slots("CalcMVPBlue")
        self.gr = GameRound.objects.create(
            team_red=self.team_red,
            team_blue=self.team_blue,
            round_number=1,
        )

    def _state(self, player, team_color, role, **kwargs):
        kwargs.setdefault("final_lives", 3)
        kwargs.setdefault("final_shots", 10)
        return PlayerRoundState.objects.create(
            game_round=self.gr,
            player=player,
            team_color=team_color,
            role=role,
            **kwargs,
        )

    def test_commander_nuke_cancel_bonus_via_calculate_mvp(self):
        # Commander fires 3 nukes; opponent cancels 2 of them → enemy_nuke_cancels=2 → +6
        # specials_used=3, own_specials_cancelled=0 → successful nukes=3 → +3
        # total = 9.0
        s = self._state(
            self.players_red["commander"],
            "red",
            "commander",
            specials_used=3,
            own_specials_cancelled=0,
            enemy_nuke_cancels=2,
            missiles_landed=0,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        result = calculate_mvp(s)
        assert result == s.get_mvp
        assert result == 9.0

    def test_calculate_mvp_matches_get_mvp_for_medic_survival(self):
        # Medic alive at end: survival bonus +2; specials_used=2 → +6; total = 8.0
        s = self._state(
            self.players_red["medic"],
            "red",
            "medic",
            final_lives=5,
            specials_used=2,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        result = calculate_mvp(s)
        assert result == s.get_mvp
        assert result == 8.0

    def test_calculate_mvp_own_nuke_cancelled_reduces_commander_score(self):
        # specials_used=2, own_specials_cancelled=2 → successful_nukes=0 (+0), -2 penalty → -2
        s = self._state(
            self.players_red["commander"],
            "red",
            "commander",
            specials_used=2,
            own_specials_cancelled=2,
            missiles_landed=0,
            points_scored=0,
            tags_made=0,
            shots_missed=0,
        )
        result = calculate_mvp(s)
        assert result == s.get_mvp
        assert result == -2.0
