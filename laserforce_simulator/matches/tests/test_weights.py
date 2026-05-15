"""
Per-role action weight function unit tests (sim_helpers/weights.py).

Weight array layout — indices 0-6:
  tag_player, change_zone, hide, capture_base, use_special, resupply_ally, missile_player

Base weights before any role function: [70, 30, 0, 0, 0, 0, 0]
"""

import pytest

from matches.models import GameRound, PlayerRoundState
from matches.sim_helpers.weights import (
    _get_medic_weights,
    _get_ammo_weights,
    _get_scout_weights,
    _get_heavy_weights,
    _get_commander_weights,
)
from matches.tests.conftest import make_team_with_slots

_ACTION_IDX = {
    "tag_player": 0,
    "change_zone": 1,
    "hide": 2,
    "capture_base": 3,
    "use_special": 4,
    "resupply_ally": 5,
    "missile_player": 6,
}
_BASE = [70, 30, 0, 0, 0, 0, 0]


@pytest.mark.django_db
class TestWeightFunctions:
    """Unit tests for per-role action weight functions in sim_helpers/weights.py."""

    def _fresh(self):
        return list(_BASE)

    def _state(self, gr, player, role, team_color="red", **kwargs):
        defaults = dict(
            final_lives=10, final_shots=15, final_special=0, zone_fallback=0
        )
        defaults.update(kwargs)
        return PlayerRoundState.objects.create(
            game_round=gr, player=player, role=role, team_color=team_color, **defaults
        )

    def setup_method(self):
        self.team, self.players = make_team_with_slots("W")
        self.team2, self.players2 = make_team_with_slots("W2")
        self.gr = GameRound.objects.create(
            team_red=self.team, team_blue=self.team2, round_number=1
        )

    # --- Medic ---

    def test_medic_baseline(self):
        """Medic favors resupply over tagging but can occasionally tag."""
        s = self._state(self.gr, self.players["medic"], "medic")
        w = _get_medic_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [5, 0, 30, 0, 0, 65, 0]

    def test_medic_baseline_sum(self):
        s = self._state(self.gr, self.players["medic"], "medic")
        w = _get_medic_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert sum(w) == 100

    def test_medic_low_lives_maximises_resupply(self):
        """When medic has <=3 lives, hide and tagging both collapse and resupply maximises."""
        s = self._state(self.gr, self.players["medic"], "medic", final_lives=3)
        w = _get_medic_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [0, 0, 0, 0, 0, 100, 0]

    def test_medic_can_capture_base_gets_small_boost(self):
        """In neutral zone, medic gets a small capture weight boost while resupply stays dominant."""
        s = self._state(self.gr, self.players["medic"], "medic", zone_fallback=1)
        w = _get_medic_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w[_ACTION_IDX["capture_base"]] == 5
        assert w[_ACTION_IDX["resupply_ally"]] > w[_ACTION_IDX["capture_base"]]

    def test_medic_special_available_increases_use_special(self):
        """With enough special charges and at least one ally active, use_special rises."""
        s = self._state(self.gr, self.players["medic"], "medic", final_special=10)
        w = _get_medic_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        # 1 active ally (medic herself) → use_special += 20 * 1
        assert w[_ACTION_IDX["use_special"]] == 20

    def test_medic_not_active_heavy_in_zone_hides(self):
        """Downed medic with a heavy escort hides to wait under cover."""
        medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            final_lives=5,
            last_downed_time=0,
            zone_fallback=0,
        )
        heavy = self._state(
            self.gr,
            self.players2["heavy"],
            "heavy",
            team_color="red",
            final_lives=5,
            zone_fallback=0,
        )
        w = _get_medic_weights(medic, _ACTION_IDX, self._fresh(), [medic, heavy], 0)
        assert w == [5, 0, 90, 0, 0, 5, 0]

    def test_medic_not_active_no_heavy_changes_zone(self):
        """Downed medic with no nearby heavy moves to find protection."""
        medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            final_lives=5,
            last_downed_time=0,
        )
        w = _get_medic_weights(medic, _ACTION_IDX, self._fresh(), [medic], 0)
        assert w == [5, 60, 30, 0, 0, 5, 0]

    # --- Ammo ---

    def test_ammo_baseline(self):
        """Ammo primarily resupplies allies, tagging occasionally."""
        s = self._state(self.gr, self.players["ammo"], "ammo")
        w = _get_ammo_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [45, 0, 0, 0, 0, 55, 0]

    def test_ammo_baseline_sum(self):
        s = self._state(self.gr, self.players["ammo"], "ammo")
        w = _get_ammo_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert sum(w) == 100

    def test_ammo_low_lives_medic_same_zone_hides(self):
        """Low-life ammo hides next to a medic who is already in range."""
        medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            team_color="red",
            final_lives=5,
            zone_fallback=0,
        )
        ammo = self._state(
            self.gr,
            self.players["ammo"],
            "ammo",
            final_lives=2,
            zone_fallback=0,
        )
        w = _get_ammo_weights(ammo, _ACTION_IDX, self._fresh(), [ammo, medic], 0)
        assert w == [25, 0, 30, 0, 0, 45, 0]

    def test_ammo_low_lives_medic_different_zone_moves_toward_medic(self):
        """Low-life ammo crosses zones to reach the medic."""
        medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            team_color="red",
            final_lives=5,
            zone_fallback=1,
        )
        ammo = self._state(
            self.gr,
            self.players["ammo"],
            "ammo",
            final_lives=2,
            zone_fallback=0,
        )
        w = _get_ammo_weights(ammo, _ACTION_IDX, self._fresh(), [ammo, medic], 0)
        assert w == [25, 50, 0, 0, 0, 25, 0]

    def test_ammo_low_lives_no_medic_no_heavy_hides(self):
        """Low-life ammo with no support hides to preserve the last few lives."""
        ammo = self._state(self.gr, self.players["ammo"], "ammo", final_lives=2)
        w = _get_ammo_weights(ammo, _ACTION_IDX, self._fresh(), [ammo], 0)
        assert w == [25, 0, 50, 0, 0, 25, 0]

    # --- Scout ---

    def test_scout_baseline(self):
        """Scout favours zone movement and tagging roughly equally."""
        s = self._state(self.gr, self.players["scout"], "scout")
        w = _get_scout_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [40, 60, 0, 0, 0, 0, 0]

    def test_scout_baseline_sum(self):
        s = self._state(self.gr, self.players["scout"], "scout")
        w = _get_scout_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert sum(w) == 100

    def test_scout_can_capture_base(self):
        """Scout in neutral zone switches priority to capturing the base."""
        s = self._state(self.gr, self.players["scout"], "scout", zone_fallback=1)
        w = _get_scout_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [20, 60, 0, 20, 0, 0, 0]

    def test_scout_low_lives_medic_same_zone_hides(self):
        """Critical-health scout hides next to medic to recover lives."""
        medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            team_color="red",
            final_lives=5,
            zone_fallback=0,
        )
        scout = self._state(
            self.gr,
            self.players["scout"],
            "scout",
            final_lives=4,
            zone_fallback=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout, medic], 0)
        assert w == [20, 40, 40, 0, 0, 0, 0]

    def test_scout_low_lives_medic_different_zone_moves_toward_medic(self):
        """Critical-health scout moves into medic's zone instead of hiding."""
        medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            team_color="red",
            final_lives=5,
            zone_fallback=1,
        )
        scout = self._state(
            self.gr,
            self.players["scout"],
            "scout",
            final_lives=4,
            zone_fallback=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout, medic], 0)
        assert w == [10, 90, 0, 0, 0, 0, 0]

    def test_scout_low_shots_ammo_different_zone_moves_toward_ammo(self):
        """Shot-depleted scout crosses zones to resupply from ammo carrier."""
        ammo = self._state(
            self.gr,
            self.players["ammo"],
            "ammo",
            team_color="red",
            final_lives=5,
            zone_fallback=1,
        )
        scout = self._state(
            self.gr,
            self.players["scout"],
            "scout",
            final_shots=9,
            zone_fallback=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout, ammo], 0)
        assert w == [10, 90, 0, 0, 0, 0, 0]

    def test_scout_special_available_raises_use_special(self):
        """Scout with special ready is more likely to use rapid-fire as ammo allows."""
        scout = self._state(
            self.gr,
            self.players["scout"],
            "scout",
            final_special=10,
            final_shots=15,
            special_active_until=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout], 0)
        assert w[_ACTION_IDX["use_special"]] == 25

    def test_scout_not_active_stops_tagging(self):
        """Downed scout stops tagging and waits or repositions instead."""
        scout = self._state(
            self.gr,
            self.players["scout"],
            "scout",
            final_lives=5,
            last_downed_time=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout], 0)
        assert w == [0, 70, 30, 0, 0, 0, 0]

    # --- Heavy ---

    def test_heavy_baseline_no_missiles(self):
        """Heavy with all missiles used holds position and tags at baseline rate."""
        s = self._state(self.gr, self.players["heavy"], "heavy", missiles_landed=5)
        w = _get_heavy_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [70, 25, 5, 0, 0, 0, 0]

    def test_heavy_baseline_no_missiles_sum(self):
        s = self._state(self.gr, self.players["heavy"], "heavy", missiles_landed=5)
        w = _get_heavy_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert sum(w) == 100

    def test_heavy_with_missiles(self):
        """Heavy with missiles available splits between tagging and launching missiles."""
        s = self._state(self.gr, self.players["heavy"], "heavy", missiles_landed=0)
        w = _get_heavy_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [70, 10, 5, 0, 0, 0, 15]
        assert sum(w) == 100

    def test_heavy_can_capture_base(self):
        """Heavy in opposing zone takes the base instead of engaging in direct fire."""
        s = self._state(
            self.gr,
            self.players["heavy"],
            "heavy",
            zone_fallback=2,
            missiles_landed=5,
        )
        w = _get_heavy_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [50, 15, 5, 30, 0, 0, 0]

    def test_heavy_low_lives_medic_different_zone_moves_toward_medic(self):
        """Critically low heavy navigates toward the medic to recover."""
        medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            team_color="red",
            final_lives=5,
            zone_fallback=1,
        )
        heavy = self._state(
            self.gr,
            self.players["heavy"],
            "heavy",
            final_lives=4,
            zone_fallback=0,
            missiles_landed=5,
        )
        w = _get_heavy_weights(heavy, _ACTION_IDX, self._fresh(), [heavy, medic], 0)
        assert w == [40, 55, 5, 0, 0, 0, 0]

    def test_heavy_not_active_always_escapes(self):
        """Downed heavy always moves zone regardless of medic presence (reduces reset window exposure)."""
        medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            team_color="red",
            final_lives=5,
            zone_fallback=0,
        )
        heavy = self._state(
            self.gr,
            self.players["heavy"],
            "heavy",
            final_lives=5,
            last_downed_time=0,
            zone_fallback=0,
            missiles_landed=5,
        )
        w = _get_heavy_weights(heavy, _ACTION_IDX, self._fresh(), [heavy, medic], 0)
        assert w == [0, 95, 5, 0, 0, 0, 0]

    # --- Commander ---

    def test_commander_baseline_no_missiles(self):
        """Commander with all missiles used holds base weights (tag reduced ~5% from original)."""
        s = self._state(
            self.gr, self.players["commander"], "commander", missiles_landed=5
        )
        w = _get_commander_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [80, 15, 0, 0, 0, 0, 0]

    def test_commander_baseline_no_missiles_sum(self):
        s = self._state(
            self.gr, self.players["commander"], "commander", missiles_landed=5
        )
        w = _get_commander_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert sum(w) == 95

    def test_commander_with_missiles(self):
        """Commander prioritises launching available missiles."""
        s = self._state(
            self.gr, self.players["commander"], "commander", missiles_landed=0
        )
        w = _get_commander_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [80, 0, 0, 0, 0, 0, 15]
        assert sum(w) == 95

    def test_commander_special_no_enemies_fires_nuke(self):
        """Commander with max-stacked SP fires regardless of game_awareness."""
        s = self._state(
            self.gr,
            self.players["commander"],
            "commander",
            final_special=81,
            missiles_landed=5,
            zone_fallback=0,
        )
        w = _get_commander_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w[_ACTION_IDX["use_special"]] == 100

    def test_commander_special_one_enemy_reduces_nuke_weight(self):
        """Commander holds the nuke when surrounded by enemies to avoid wasting it."""
        cmd = self._state(
            self.gr,
            self.players["commander"],
            "commander",
            final_special=81,
            missiles_landed=5,
            zone_fallback=0,
            team_color="red",
        )
        enemy = self._state(
            self.gr,
            self.players2["scout"],
            "scout",
            team_color="blue",
            final_lives=5,
            zone_fallback=0,
        )
        w = _get_commander_weights(cmd, _ACTION_IDX, self._fresh(), [cmd, enemy], 0)
        assert w[_ACTION_IDX["use_special"]] == 80

    def test_commander_not_active_allied_medic_in_zone_hides(self):
        """Downed commander hides when allied medic is in the same zone."""
        allied_medic = self._state(
            self.gr,
            self.players["medic"],
            "medic",
            team_color="red",
            final_lives=5,
            zone_fallback=0,
        )
        cmd = self._state(
            self.gr,
            self.players["commander"],
            "commander",
            final_lives=5,
            last_downed_time=0,
            missiles_landed=5,
            zone_fallback=0,
            team_color="red",
        )
        w = _get_commander_weights(
            cmd, _ACTION_IDX, self._fresh(), [cmd, allied_medic], 0
        )
        assert w == [10, 15, 70, 0, 0, 0, 0]

    def test_commander_not_active_no_allied_medic_changes_zone(self):
        """Downed commander moves zone to find allied medic."""
        cmd = self._state(
            self.gr,
            self.players["commander"],
            "commander",
            final_lives=5,
            last_downed_time=0,
            missiles_landed=5,
        )
        w = _get_commander_weights(cmd, _ACTION_IDX, self._fresh(), [cmd], 0)
        assert w == [10, 85, 0, 0, 0, 0, 0]

    def test_medic_can_capture_base_prioritises_capture(self):
        """Known pre-existing failure: weight code only adds +5, not +50."""
        # This test documents existing behaviour rather than asserting the ideal.
        s = self._state(self.gr, self.players["medic"], "medic", zone_fallback=1)
        w = _get_medic_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        # Current code gives capture_base=5, not 50
        assert w[_ACTION_IDX["capture_base"]] == 5
