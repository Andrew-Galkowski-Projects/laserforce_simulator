import pytest
from unittest.mock import patch

from teams.models import Team, Player
from matches.models import GameRound, PlayerRoundState, GameEvent
from matches.simulation import ResourceBasedSimulator, BatchSimulator
from matches.sim_helpers.weights import (
    _get_medic_weights,
    _get_ammo_weights,
    _get_scout_weights,
    _get_heavy_weights,
    _get_commander_weights,
)
from matches.tests.conftest import make_team_with_slots


@pytest.mark.django_db
class TestSimulation:
    def create_team_with_roster(self, prefix):
        return make_team_with_slots(prefix)

    def test_get_tag_id_scout_ordering(self):
        team, players = self.create_team_with_roster("Alpha")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        s1 = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout"], team_color="red", role="scout",
            final_lives=10, final_shots=10,
        )
        s2 = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout_2"], team_color="red", role="scout",
            final_lives=10, final_shots=10,
        )

        assert s1.get_tag_id == PlayerRoundState.tag_id.red_scout_1
        assert s2.get_tag_id == PlayerRoundState.tag_id.red_scout_2

    def test_resupply_ammo_caps_shots_and_creates_event(self):
        simulator = ResourceBasedSimulator()
        team_red, _ = self.create_team_with_roster("Red")
        team_blue, _ = self.create_team_with_roster("Blue")

        game_round = GameRound.objects.create(
            team_red=team_red, team_blue=team_blue, round_number=1
        )
        red_states = simulator._initialize_players(game_round, team_red, "red")

        tagger   = next(s for s in red_states if s.role == "ammo")
        teammate = next(s for s in red_states if s.role == "scout")

        teammate.final_shots = 1
        teammate.save()

        simulator._attempt_resupply(tagger, teammate, second=10)
        teammate.refresh_from_db()

        assert teammate.final_shots >= 1
        assert teammate.final_shots <= teammate.max_shots
        assert GameEvent.objects.filter(
            actor=tagger.player, target=teammate.player
        ).exists(), "Resupply action should create a GameEvent with actor and target set"

    def test_simulate_single_round_detailed_creates_completed_round(self):
        simulator = ResourceBasedSimulator()
        team_red, _  = self.create_team_with_roster("RedSim")
        team_blue, _ = self.create_team_with_roster("BlueSim")

        game_round = simulator.simulate_single_round_detailed(team_red, team_blue)

        assert game_round is not None
        assert game_round.is_completed
        assert game_round.player_states.count() > 0

    def test_plan_action_weights_for_resupply_player_in_own_zone(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("Weights")

        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        medic_state = PlayerRoundState.objects.create(
            game_round=gr, player=players["medic"], team_color="red", role="medic",
            current_zone=0, final_shots=10, final_lives=10,
        )

        captured = {}

        def fake_choices(seq, weights):
            captured["seq"] = seq
            captured["weights"] = weights
            return [seq[0]]

        with patch("random.choices", side_effect=fake_choices):
            simulator._plan_action(medic_state, [medic_state], second=0)

        assert "seq" in captured
        assert captured["seq"] == [
            "tag_player", "change_zone", "hide", "capture_base",
            "use_special", "resupply_ally", "missile_player",
        ]
        assert captured["weights"] == [5, 0, 30, 0, 0, 65, 0]

    def test_tag_event_created_when_hit(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("TagTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=players["commander"],
            team_color="red", role="commander", current_zone=0, final_shots=10, final_lives=10,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout"],
            team_color="blue", role="scout", current_zone=0, final_shots=10, final_lives=10,
        )

        with patch("random.randint", return_value=0):
            simulator._resolve_tag_attempts(gr, [{"attacker": attacker, "defender": defender}], 0)

        assert GameEvent.objects.filter(
            event_type="tag", actor=attacker.player, target=defender.player
        ).exists()

    def test_missile_dodge_and_hit_events(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("MissileTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=players["commander"],
            team_color="red", role="commander", current_zone=0,
            final_shots=10, final_lives=10, final_missiles=2,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout"],
            team_color="blue", role="scout", current_zone=0, final_shots=10, final_lives=10,
        )

        # Dodge
        with patch("random.choices", return_value=["missile_player"]), \
             patch("random.choice", return_value=defender), \
             patch("random.random", return_value=0.1):
            simulator._simulate_combat_exchange(
                gr, [attacker], [defender], second=5,
                pending_missiles=[], pending_nukes=[],
            )

        assert GameEvent.objects.filter(
            event_type="missile_dodge", actor=defender.player, target=attacker.player,
        ).exists()

        # Hit
        pending_missiles = []
        with patch("random.choices", return_value=["missile_player"]), \
             patch("random.choice", return_value=defender), \
             patch("random.random", return_value=0.5), \
             patch("random.randint", return_value=1):
            simulator._simulate_combat_exchange(
                gr, [attacker], [defender], second=6,
                pending_missiles=pending_missiles, pending_nukes=[],
            )

        assert len(pending_missiles) >= 1
        complete_time, att, defn = pending_missiles[0]
        simulator._complete_missile(att, defn, complete_time)

        assert GameEvent.objects.filter(
            event_type="missile_hit", actor=attacker.player, target=defender.player
        ).exists()

    def test_capture_base_and_change_zone(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("BaseTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        player_obj = players["scout"]
        state = PlayerRoundState.objects.create(
            game_round=gr, player=player_obj, team_color="red", role="scout",
            current_zone=1, final_shots=5, final_lives=10,
        )

        with patch("random.choices", return_value=["capture_base"]):
            simulator._simulate_combat_exchange(
                gr, [state], [], second=2, pending_missiles=[], pending_nukes=[],
            )

        state.refresh_from_db()
        assert state.points_scored >= 1001
        assert state.last_tagged_id in [
            PlayerRoundState.tag_id.neutral_base,
            PlayerRoundState.tag_id.red_base,
            PlayerRoundState.tag_id.blue_base,
        ]

        state2 = PlayerRoundState.objects.create(
            game_round=gr, player=player_obj, team_color="red", role="scout",
            current_zone=0, final_shots=5, final_lives=10,
        )
        with patch("random.choices", return_value=["change_zone"]), \
             patch("random.choice", return_value=1):
            simulator._simulate_combat_exchange(
                gr, [state2], [], second=3, pending_missiles=[], pending_nukes=[],
            )

        assert state2.current_zone == 1

    def test_specific_tags_bookkeeping_and_resupply_edge_cases(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=players["commander"],
            team_color="red", role="commander", current_zone=0, final_shots=10, final_lives=10,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout"],
            team_color="blue", role="scout", current_zone=0, final_shots=10, final_lives=10,
        )

        with patch("random.randint", return_value=0):
            simulator._resolve_tag_attempts(gr, [{"attacker": attacker, "defender": defender}], 0)

        attacker.refresh_from_db()
        defender.refresh_from_db()
        atk_key = str(attacker.get_tag_id)
        def_key = str(defender.get_tag_id)

        assert def_key in attacker.specific_tags
        assert attacker.specific_tags[def_key]["tags"] > 0
        assert atk_key in defender.specific_tags
        assert defender.specific_tags[atk_key]["tagged_by"] > 0

    def test_max_shots(self):
        simulator = ResourceBasedSimulator()
        team, _ = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        red_states = simulator._initialize_players(gr, team, "red")
        ammo = next(s for s in red_states if s.role == "ammo")
        scout_state = next(s for s in red_states if s.role == "scout")
        scout_state.final_shots = scout_state.max_shots
        scout_state.save()

        simulator._attempt_resupply(ammo, scout_state, second=10)
        scout_state.refresh_from_db()

        assert scout_state.final_shots <= scout_state.max_shots

    def test_resupply_medic_no_shots_no_heal(self):
        simulator = ResourceBasedSimulator()
        team, _ = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        red_states = simulator._initialize_players(gr, team, "red")
        medic_state = next(s for s in red_states if s.role == "medic")
        medic_state.final_shots = 0
        teammate = next(s for s in red_states if s.role == "heavy")
        teammate.final_lives = max(1, teammate.final_lives - 5)
        medic_state.save()
        teammate.save()

        simulator._attempt_resupply(medic_state, teammate, second=20)
        teammate.refresh_from_db()

        assert teammate.final_lives <= teammate.max_lives

    def test_is_active_and_is_taggable_time_boundaries(self):
        team, players = self.create_team_with_roster("TimeTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        state = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout"], team_color="red", role="scout",
            current_zone=0, final_shots=5, final_lives=5,
        )

        assert state.is_active_at(0)
        assert state.is_taggable_at(0)

        state.last_downed_time = 10
        state.final_lives = 1
        state.save()

        assert not state.is_taggable_at(12)
        assert not state.is_active_at(12)
        assert not state.is_resupplyable_at(12)

        assert state.is_taggable_at(15)
        assert not state.is_active_at(15)
        assert not state.is_resupplyable_at(15)

        assert state.is_active_at(20)
        assert state.is_resupplyable_at(20)

    def test_takes_3_tags_to_down_commander_and_heavy(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("DownTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        commander = PlayerRoundState.objects.create(
            game_round=gr, player=players["commander"],
            team_color="red", role="commander", current_zone=0,
            final_shots=10, final_lives=3, shields=3,
        )
        heavy = PlayerRoundState.objects.create(
            game_round=gr, player=players["heavy"],
            team_color="red", role="heavy", current_zone=0,
            final_shots=10, final_lives=3, shields=3,
        )
        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout"],
            team_color="blue", role="scout", current_zone=0, final_shots=10, final_lives=10,
        )

        for _ in range(3):
            with patch("random.randint", return_value=0):
                simulator._resolve_tag_attempts(gr, [{"attacker": attacker, "defender": commander}], 0)

        commander.refresh_from_db()
        assert commander.final_lives == 2
        assert not commander.is_active_at(3)

        attacker.final_shots = 10
        attacker.save()

        for _ in range(3):
            with patch("random.randint", return_value=0):
                simulator._resolve_tag_attempts(gr, [{"attacker": attacker, "defender": heavy}], 0)

        heavy.refresh_from_db()
        assert heavy.final_lives == 2
        assert not heavy.is_active_at(3)

    def test_takes_1_tags_to_down_other_roles(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("DownTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        role_states = {}
        for role in ["scout", "medic", "ammo"]:
            state = PlayerRoundState.objects.create(
                game_round=gr, player=players[role],
                team_color="red", role=role, current_zone=0,
                final_shots=10, final_lives=1, shields=1,
            )
            role_states[role] = state

        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout_2"],
            team_color="blue", role="scout", current_zone=0, final_shots=10, final_lives=10,
        )

        for role, state in role_states.items():
            with patch("random.randint", return_value=0):
                simulator._resolve_tag_attempts(gr, [{"attacker": attacker, "defender": state}], 0)

            state.refresh_from_db()
            assert state.final_lives == 0
            assert not state.is_active_at(1), f"{role} should be downed after 1 tag"

    def test_takes_2_tags_from_commander_to_down_heavy(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("DownTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        heavy = PlayerRoundState.objects.create(
            game_round=gr, player=players["heavy"],
            team_color="red", role="heavy", current_zone=0,
            final_shots=10, final_lives=2, shields=3,
        )
        commander = PlayerRoundState.objects.create(
            game_round=gr, player=players["commander"],
            team_color="blue", role="commander",
            current_zone=0, final_shots=10, final_lives=10,
        )

        for _ in range(2):
            with patch("random.randint", return_value=0):
                simulator._resolve_tag_attempts(gr, [{"attacker": commander, "defender": heavy}], 0)

        heavy.refresh_from_db()
        assert heavy.final_lives == 1
        assert not heavy.is_active_at(2)

    def test_cannot_be_resupplied_while_downed(self):
        simulator = ResourceBasedSimulator()
        team, _ = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        red_states = simulator._initialize_players(gr, team, "red")
        medic    = next(s for s in red_states if s.role == "medic")
        teammate = next(s for s in red_states if s.role == "heavy")
        teammate.final_lives    = 1
        teammate.last_downed_time = 5
        medic.save()
        teammate.save()

        simulator._attempt_resupply(medic, teammate, second=10)
        teammate.refresh_from_db()

        assert teammate.final_lives <= 1

    def test_cannot_tag_player_with_zero_lives(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("NoTag")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=players["scout"],
            team_color="red", role="scout", current_zone=0, final_shots=10, final_lives=10,
        )
        dead = PlayerRoundState.objects.create(
            game_round=gr, player=players["medic"],
            team_color="blue", role="medic", current_zone=0, final_shots=0, final_lives=0,
        )

        with patch("random.choices", return_value=["tag_player"]), \
             patch("random.choice", return_value=dead), \
             patch("random.random", return_value=0.0):
            plans = simulator._plan_action(attacker, [attacker, dead], second=1)

        tag_plans = [p for p in plans if p.get("type") == "tag"]
        assert len(tag_plans) == 0, "Dead player should not be selectable as tag target"

    def test_nuke_scheduling_and_cancellation(self):
        simulator = ResourceBasedSimulator()
        team_red, players_red   = self.create_team_with_roster("NukeRed")
        team_blue, _ = self.create_team_with_roster("NukeBlue")
        gr = GameRound.objects.create(team_red=team_red, team_blue=team_blue, round_number=1)

        commander = PlayerRoundState.objects.create(
            game_round=gr, player=players_red["commander"],
            team_color="red", role="commander", current_zone=0,
            final_shots=10, final_lives=10, final_special=20,
        )

        with patch("random.choices", return_value=["use_special"]):
            scheduled = simulator._use_special(commander, second=5)

        assert scheduled is not None
        assert scheduled[0] == "nuke"

        complete_time = scheduled[1]
        commander.last_downed_time = complete_time - 1
        commander.save()

        simulator._complete_nuke(commander, complete_time)

        assert not GameEvent.objects.filter(
            event_type="nuke_detonated", actor=commander.player
        ).exists()

    def test_nuke_elim_cannot_be_tagged_after(self):
        simulator = ResourceBasedSimulator()
        team_red, players_red   = self.create_team_with_roster("NukeRed")
        team_blue, players_blue = self.create_team_with_roster("NukeBlue")
        gr = GameRound.objects.create(team_red=team_red, team_blue=team_blue, round_number=1)

        blue_commander = PlayerRoundState.objects.create(
            game_round=gr, player=players_blue["commander"],
            team_color="blue", role="heavy", current_zone=0, final_shots=10, final_lives=4,
        )
        heavy = PlayerRoundState.objects.create(
            game_round=gr, player=players_blue["heavy"],
            team_color="blue", role="heavy", current_zone=0, final_shots=10, final_lives=3,
        )
        scout = PlayerRoundState.objects.create(
            game_round=gr, player=players_blue["scout"],
            team_color="blue", role="scout", current_zone=0, final_shots=10, final_lives=2,
        )
        ammo = PlayerRoundState.objects.create(
            game_round=gr, player=players_blue["ammo"],
            team_color="blue", role="ammo", current_zone=0, final_shots=15, final_lives=1,
        )
        medic = PlayerRoundState.objects.create(
            game_round=gr, player=players_blue["medic"],
            team_color="blue", role="medic", current_zone=0,
            final_shots=10, final_lives=0, was_eliminated_at=15,
        )
        commander = PlayerRoundState.objects.create(
            game_round=gr, player=players_red["commander"],
            team_color="red", role="commander", current_zone=0,
            final_shots=10, final_lives=10, final_special=20, points_scored=0,
        )

        simulator._complete_nuke(commander, 25)

        for player in [commander, blue_commander, heavy, scout, ammo, medic]:
            player.refresh_from_db()

        assert commander.points_scored == 500
        assert blue_commander.final_lives == 1
        assert blue_commander.was_eliminated_at == 901
        assert heavy.final_lives == 0
        assert heavy.was_eliminated_at == 25
        assert scout.final_lives == 0
        assert scout.was_eliminated_at == 25
        assert ammo.final_lives == 0
        assert ammo.was_eliminated_at == 25
        assert medic.final_lives == 0
        assert medic.was_eliminated_at == 15


@pytest.mark.django_db
class TestLivesLost:
    def create_team_with_roster(self, prefix):
        team, _ = make_team_with_slots(prefix)
        return team

    def _make_round(self, team_red, team_blue):
        return GameRound.objects.create(team_red=team_red, team_blue=team_blue, round_number=1)

    def _make_state(self, gr, player, team_color, role, **kwargs):
        return PlayerRoundState.objects.create(
            game_round=gr, player=player, team_color=team_color, role=role,
            final_shots=10, **kwargs
        )

    # --- property unit tests ---

    def test_lives_lost_no_nukes(self):
        team = self.create_team_with_roster("Unit")
        gr = self._make_round(team, team)
        state = self._make_state(gr, team.slot_scout_1, "red", "scout",
                                 final_lives=10, times_tagged=3, times_missiled=1,
                                 lives_lost_to_nukes=0)
        assert state.lives_lost == 5

    def test_lives_lost_includes_nuke_field(self):
        team = self.create_team_with_roster("Unit2")
        gr = self._make_round(team, team)
        state = self._make_state(gr, team.slot_scout_1, "red", "scout",
                                 final_lives=4, times_tagged=1, times_missiled=0,
                                 lives_lost_to_nukes=3)
        assert state.lives_lost == 4

    def test_lives_lost_never_negative(self):
        team = self.create_team_with_roster("Unit3")
        gr = self._make_round(team, team)
        state = self._make_state(gr, team.slot_scout_1, "red", "scout",
                                 final_lives=10, times_tagged=0, times_missiled=0,
                                 lives_lost_to_nukes=0)
        assert state.lives_lost == 0

    # --- integration tests via _complete_nuke ---

    def test_nuke_removes_3_lives_from_healthy_opponent(self):
        simulator = ResourceBasedSimulator()
        red  = self.create_team_with_roster("NukeA_Red")
        blue = self.create_team_with_roster("NukeA_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(gr, red.slot_commander,
                                     "red", "commander", final_lives=10, final_special=20)
        target = self._make_state(gr, blue.slot_scout_1,
                                  "blue", "scout", final_lives=5)

        simulator._complete_nuke(commander, second=30)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 3
        assert target.final_lives == 2
        assert target.lives_lost == 3

    def test_nuke_exception_player_has_2_lives(self):
        simulator = ResourceBasedSimulator()
        red  = self.create_team_with_roster("NukeB_Red")
        blue = self.create_team_with_roster("NukeB_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(gr, red.slot_commander,
                                     "red", "commander", final_lives=10, final_special=20)
        target = self._make_state(gr, blue.slot_scout_1,
                                  "blue", "scout", final_lives=2)

        simulator._complete_nuke(commander, second=30)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 2
        assert target.final_lives == 0
        assert target.lives_lost == 2

    def test_nuke_exception_player_has_1_life(self):
        simulator = ResourceBasedSimulator()
        red  = self.create_team_with_roster("NukeC_Red")
        blue = self.create_team_with_roster("NukeC_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(gr, red.slot_commander,
                                     "red", "commander", final_lives=10, final_special=20)
        target = self._make_state(gr, blue.slot_scout_1,
                                  "blue", "scout", final_lives=1)

        simulator._complete_nuke(commander, second=30)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 1
        assert target.final_lives == 0

    def test_nuke_skips_already_eliminated_player(self):
        simulator = ResourceBasedSimulator()
        red  = self.create_team_with_roster("NukeD_Red")
        blue = self.create_team_with_roster("NukeD_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(gr, red.slot_commander,
                                     "red", "commander", final_lives=10, final_special=20)
        dead = self._make_state(gr, blue.slot_scout_1,
                                "blue", "scout", final_lives=0, was_eliminated_at=50)

        simulator._complete_nuke(commander, second=30)
        dead.refresh_from_db()

        assert dead.lives_lost_to_nukes == 0
        assert dead.lives_lost == 0

    def test_nuke_accumulates_across_multiple_nukes(self):
        simulator = ResourceBasedSimulator()
        red  = self.create_team_with_roster("NukeE_Red")
        blue = self.create_team_with_roster("NukeE_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(gr, red.slot_commander,
                                     "red", "commander", final_lives=10, final_special=40)
        target = self._make_state(gr, blue.slot_heavy,
                                  "blue", "heavy", final_lives=10)

        simulator._complete_nuke(commander, second=20)
        target.refresh_from_db()
        simulator._complete_nuke(commander, second=40)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 6
        assert target.final_lives == 4
        assert target.lives_lost == 6


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
        team.slot_heavy     = players[1]
        team.slot_scout_1   = players[2]
        team.slot_scout_2   = players[3]
        team.slot_medic     = players[4]
        team.slot_ammo      = players[5]
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
        team.slot_heavy     = players[1]
        team.slot_scout_1   = players[2]
        team.slot_scout_2   = players[3]
        team.slot_medic     = players[4]
        team.save()
        assert not team.is_valid_roster

    def test_invalid_roster_missing_scout_2(self):
        team = self._make_team("NoScout2")
        players = [self._add_player(team, f"p{i}") for i in range(5)]
        team.slot_commander = players[0]
        team.slot_heavy     = players[1]
        team.slot_scout_1   = players[2]
        team.slot_medic     = players[3]
        team.slot_ammo      = players[4]
        team.save()
        assert not team.is_valid_roster

    def test_invalid_roster_duplicate_player_in_two_slots(self):
        team = self._make_team("Dupe")
        players = [self._add_player(team, f"p{i}") for i in range(5)]
        team.slot_commander = players[0]
        team.slot_heavy     = players[0]  # same player!
        team.slot_scout_1   = players[1]
        team.slot_scout_2   = players[2]
        team.slot_medic     = players[3]
        team.slot_ammo      = players[4]
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
        team.slot_heavy     = players[1]
        team.slot_scout_1   = players[2]
        team.slot_scout_2   = players[3]
        team.slot_medic     = players[4]
        team.save()
        errors = team.roster_errors
        assert any("Ammo" in e for e in errors)

    def test_roster_errors_reports_duplicate_player(self):
        team = self._make_team("ErrDupe")
        players = [self._add_player(team, f"p{i}") for i in range(5)]
        team.slot_commander = players[0]
        team.slot_heavy     = players[0]  # duplicate
        team.slot_scout_1   = players[1]
        team.slot_scout_2   = players[2]
        team.slot_medic     = players[3]
        team.slot_ammo      = players[4]
        team.save()
        errors = team.roster_errors
        assert any("multiple slots" in e for e in errors)

    def test_roster_errors_reports_all_missing_when_no_slots(self):
        team = self._make_team("ErrFew")
        errors = team.roster_errors
        assert len(errors) == 6


@pytest.mark.django_db
class TestMVP:
    def setup_method(self):
        self.team_red, self.players_red = make_team_with_slots("MVPRed")
        self.team_blue, self.players_blue = make_team_with_slots("MVPBlue")
        self.gr = GameRound.objects.create(
            team_red=self.team_red, team_blue=self.team_blue, round_number=1,
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
        s = self._state(self.players_red["scout"], "red", "scout",
                        tags_made=0, shots_missed=0)
        assert s.get_accuracy == 0

    def test_accuracy_all_hits(self):
        s = self._state(self.players_red["scout"], "red", "scout",
                        tags_made=10, shots_missed=0)
        assert s.get_accuracy == 100

    def test_accuracy_three_quarters(self):
        s = self._state(self.players_red["scout"], "red", "scout",
                        tags_made=75, shots_missed=25)
        assert s.get_accuracy == 75

    # --- All-roles components ---

    def test_accuracy_bonus_100pct(self):
        # ceil(100 * 0.1 * 2) / 2 = 10.0; ammo with no specials or extra points
        s = self._state(self.players_red["ammo"], "red", "ammo",
                        tags_made=10, shots_missed=0, points_scored=0, specials_used=0)
        assert s.get_mvp == 10.0

    def test_medic_hit_bonus(self):
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        final_medic_hits=3, points_scored=0, missiles_landed=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 3.0

    def test_enemy_nuke_cancel_bonus(self):
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        enemy_nuke_cancels=2, points_scored=0, missiles_landed=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 6.0

    def test_ally_nuke_cancel_penalty(self):
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        ally_nuke_cancels=1, points_scored=0, missiles_landed=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == -3.0

    def test_times_missiled_penalty(self):
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        times_missiled=3, points_scored=0, missiles_landed=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == -3.0

    def test_elimination_penalty_non_medic(self):
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        final_lives=0, points_scored=0, missiles_landed=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == -1.0

    def test_no_elimination_penalty_for_medic(self):
        s = self._state(self.players_red["medic"], "red", "medic",
                        final_lives=0, points_scored=0, specials_used=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 0.0

    def test_elimination_bonus_minimum(self):
        # Eliminated at second 720 → 180 s remaining → exactly 4 pts (no extra above 3 min)
        self.gr.blue_team_eliminated = True
        self.gr.eliminated_at = 720
        self.gr.save()
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        points_scored=0, missiles_landed=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 4.0

    def test_elimination_bonus_with_extra_time(self):
        # Eliminated at second 540 → 360 s remaining → 4 + (360-180)/60 = 7.0
        self.gr.blue_team_eliminated = True
        self.gr.eliminated_at = 540
        self.gr.save()
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        points_scored=0, missiles_landed=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 7.0

    # --- Commander ---

    def test_commander_missile_bonus(self):
        s = self._state(self.players_red["commander"], "red", "commander",
                        missiles_landed=3, specials_used=0, own_specials_cancelled=0,
                        points_scored=0, tags_made=0, shots_missed=0)
        assert s.get_mvp == 3.0

    def test_commander_nuke_bonus(self):
        s = self._state(self.players_red["commander"], "red", "commander",
                        specials_used=2, own_specials_cancelled=0, missiles_landed=0,
                        points_scored=0, tags_made=0, shots_missed=0)
        assert s.get_mvp == 2.0

    def test_commander_own_nuke_cancelled_penalty(self):
        # 2 nukes used, 1 cancelled: successful=1 (+1), cancelled=1 (-1) → net 0
        s = self._state(self.players_red["commander"], "red", "commander",
                        specials_used=2, own_specials_cancelled=1, missiles_landed=0,
                        points_scored=0, tags_made=0, shots_missed=0)
        assert s.get_mvp == 0.0

    def test_commander_points_bonus(self):
        s = self._state(self.players_red["commander"], "red", "commander",
                        points_scored=12_000, specials_used=0, own_specials_cancelled=0,
                        missiles_landed=0, tags_made=0, shots_missed=0)
        assert s.get_mvp == 2.0

    # --- Heavy ---

    def test_heavy_missile_bonus(self):
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        missiles_landed=2, points_scored=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 4.0

    def test_heavy_points_bonus(self):
        s = self._state(self.players_red["heavy"], "red", "heavy",
                        points_scored=9_000, missiles_landed=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 2.0

    # --- Scout ---

    def test_scout_cmd_heavy_hit_bonus(self):
        cmd_key = str(PlayerRoundState.tag_id.blue_commander)  # "7"
        hvy_key = str(PlayerRoundState.tag_id.blue_heavy)      # "8"
        # 80% accuracy → 8.0; (5+3)*0.2 = 1.6; total = 9.6
        s = self._state(self.players_red["scout"], "red", "scout",
                        specific_tags={cmd_key: {"tags": 5, "tagged_by": 0},
                                       hvy_key: {"tags": 3, "tagged_by": 0}},
                        tags_made=8, shots_missed=2, points_scored=0)
        assert s.get_mvp == 9.6

    def test_scout_points_bonus(self):
        s = self._state(self.players_red["scout"], "red", "scout",
                        points_scored=8_000, specific_tags={},
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 2.0

    # --- Ammo ---

    def test_ammo_power_boost_bonus(self):
        s = self._state(self.players_red["ammo"], "red", "ammo",
                        specials_used=3, points_scored=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 9.0

    def test_ammo_points_bonus(self):
        s = self._state(self.players_red["ammo"], "red", "ammo",
                        points_scored=5_000, specials_used=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 2.0

    # --- Medic ---

    def test_medic_power_boost_bonus(self):
        # 4 specials * 3 = 12; survival bonus +2 = 14
        s = self._state(self.players_red["medic"], "red", "medic",
                        specials_used=4, points_scored=0,
                        tags_made=0, shots_missed=0, final_lives=3)
        assert s.get_mvp == 14.0

    def test_medic_survival_bonus(self):
        s = self._state(self.players_red["medic"], "red", "medic",
                        final_lives=5, specials_used=0, points_scored=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 2.0

    def test_medic_no_survival_bonus_when_eliminated(self):
        s = self._state(self.players_red["medic"], "red", "medic",
                        final_lives=0, specials_used=0, points_scored=0,
                        tags_made=0, shots_missed=0)
        assert s.get_mvp == 0.0

    def test_medic_points_bonus(self):
        # 2 * (4000 - 2000) / 1000 = 4.0; no survival (lives=0)
        s = self._state(self.players_red["medic"], "red", "medic",
                        points_scored=4_000, specials_used=0,
                        final_lives=0, tags_made=0, shots_missed=0)
        assert s.get_mvp == 4.0


# ---------------------------------------------------------------------------
# Weight function tests
# ---------------------------------------------------------------------------
#
# Base weights before any role function is applied:
#   [70, 30, 0, 0, 0, 0, 0]
#   indices: [tag_player, change_zone, hide, capture_base, use_special, resupply_ally, missile_player]
#
# Each role function receives a copy of those base weights and adjusts them
# according to the player's state and surroundings.

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
        defaults = dict(final_lives=10, final_shots=15, final_special=0, current_zone=0)
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
        # current_zone=1 (neutral_zone) → can_capture_base_in_current_zone = True
        # Medic role: resupply=60, capture=0 → after +5/-5: capture=5, resupply=55
        s = self._state(self.gr, self.players["medic"], "medic", current_zone=1)
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
            self.gr, self.players["medic"], "medic",
            final_lives=5, last_downed_time=0, current_zone=0,
        )
        heavy = self._state(
            self.gr, self.players2["heavy"], "heavy",
            team_color="red", final_lives=5, current_zone=0,
        )
        w = _get_medic_weights(medic, _ACTION_IDX, self._fresh(), [medic, heavy], 0)
        assert w == [5, 0, 90, 0, 0, 5, 0]

    def test_medic_not_active_no_heavy_changes_zone(self):
        """Downed medic with no nearby heavy moves to find protection."""
        medic = self._state(
            self.gr, self.players["medic"], "medic",
            final_lives=5, last_downed_time=0,
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
            self.gr, self.players["medic"], "medic",
            team_color="red", final_lives=5, current_zone=0,
        )
        ammo = self._state(
            self.gr, self.players["ammo"], "ammo",
            final_lives=2, current_zone=0,
        )
        w = _get_ammo_weights(ammo, _ACTION_IDX, self._fresh(), [ammo, medic], 0)
        assert w == [25, 0, 30, 0, 0, 45, 0]

    def test_ammo_low_lives_medic_different_zone_moves_toward_medic(self):
        """Low-life ammo crosses zones to reach the medic."""
        medic = self._state(
            self.gr, self.players["medic"], "medic",
            team_color="red", final_lives=5, current_zone=1,
        )
        ammo = self._state(
            self.gr, self.players["ammo"], "ammo",
            final_lives=2, current_zone=0,
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
        s = self._state(self.gr, self.players["scout"], "scout", current_zone=1)
        w = _get_scout_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        # tag -=30 (role) -=20 (base) = 20; change_zone +=30 (role); capture +=20
        assert w == [20, 60, 0, 20, 0, 0, 0]

    def test_scout_low_lives_medic_same_zone_hides(self):
        """Critical-health scout hides next to medic to recover lives."""
        medic = self._state(
            self.gr, self.players["medic"], "medic",
            team_color="red", final_lives=5, current_zone=0,
        )
        # starting_lives=15 → lives_critical=4.5; final_lives=4 triggers the branch
        scout = self._state(
            self.gr, self.players["scout"], "scout",
            final_lives=4, current_zone=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout, medic], 0)
        # role: tag-=30, change_zone+=30 → [40,60,...]; medic same zone: change_zone-=20,tag-=20,hide+=40
        assert w == [20, 40, 40, 0, 0, 0, 0]

    def test_scout_low_lives_medic_different_zone_moves_toward_medic(self):
        """Critical-health scout moves into medic's zone instead of hiding."""
        medic = self._state(
            self.gr, self.players["medic"], "medic",
            team_color="red", final_lives=5, current_zone=1,
        )
        scout = self._state(
            self.gr, self.players["scout"], "scout",
            final_lives=4, current_zone=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout, medic], 0)
        # role: [40,60,...]; medic different zone: tag-=30, change_zone+=30
        assert w == [10, 90, 0, 0, 0, 0, 0]

    def test_scout_low_shots_ammo_different_zone_moves_toward_ammo(self):
        """Shot-depleted scout crosses zones to resupply from ammo carrier."""
        ammo = self._state(
            self.gr, self.players["ammo"], "ammo",
            team_color="red", final_lives=5, current_zone=1,
        )
        # starting_shots=30 → shots_critical=9.0; final_shots=9 ≤ 9.0 triggers
        scout = self._state(
            self.gr, self.players["scout"], "scout",
            final_shots=9, current_zone=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout, ammo], 0)
        # role: [40,60,...]; ammo different zone: tag-=30, change_zone+=30
        assert w == [10, 90, 0, 0, 0, 0, 0]

    def test_scout_special_available_raises_use_special(self):
        """Scout with special ready is more likely to use rapid-fire as ammo allows."""
        scout = self._state(
            self.gr, self.players["scout"], "scout",
            final_special=10, final_shots=15, special_active_until=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout], 0)
        # 100 * (15 / 60) = 25
        assert w[_ACTION_IDX["use_special"]] == 25

    def test_scout_not_active_stops_tagging(self):
        """Downed scout stops tagging and waits or repositions instead."""
        scout = self._state(
            self.gr, self.players["scout"], "scout",
            final_lives=5, last_downed_time=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, self._fresh(), [scout], 0)
        # role: [40,60,...]; not active: tag zeroed, 10 to change_zone, rest to hide
        assert w == [0, 70, 30, 0, 0, 0, 0]

    # --- Heavy ---

    def test_heavy_baseline_no_missiles(self):
        """Heavy with all missiles used holds position and tags at baseline rate."""
        # missiles_used = missiles_landed; set >=5 to exhaust missile budget
        s = self._state(self.gr, self.players["heavy"], "heavy", missiles_landed=5)
        w = _get_heavy_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        # role: change_zone-=5 (25), hide+=5 (5); tag stays at base 70
        assert w == [70, 25, 5, 0, 0, 0, 0]

    def test_heavy_baseline_no_missiles_sum(self):
        s = self._state(self.gr, self.players["heavy"], "heavy", missiles_landed=5)
        w = _get_heavy_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert sum(w) == 100

    def test_heavy_with_missiles(self):
        """Heavy with missiles available splits between tagging and launching missiles."""
        s = self._state(self.gr, self.players["heavy"], "heavy", missiles_landed=0)
        w = _get_heavy_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        # role: [70,25,5,...]; missiles: change_zone-=15, missile+=15 → [70,10,5,0,0,0,15]
        assert w == [70, 10, 5, 0, 0, 0, 15]
        assert sum(w) == 100

    def test_heavy_can_capture_base(self):
        """Heavy in opposing zone takes the base instead of engaging in direct fire."""
        # Red heavy in blue_zone (2) → can_capture_base = True
        s = self._state(
            self.gr, self.players["heavy"], "heavy",
            current_zone=2, missiles_landed=5,
        )
        w = _get_heavy_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        # role (no missiles): [70,25,5,...]; base capture: change_zone-=10,tag-=20,capture+=30
        assert w == [50, 15, 5, 30, 0, 0, 0]

    def test_heavy_low_lives_medic_different_zone_moves_toward_medic(self):
        """Critically low heavy navigates toward the medic to recover."""
        medic = self._state(
            self.gr, self.players["medic"], "medic",
            team_color="red", final_lives=5, current_zone=1,
        )
        # starting_lives=15 → lives_critical=4.5; final_lives=4 triggers
        heavy = self._state(
            self.gr, self.players["heavy"], "heavy",
            final_lives=4, current_zone=0, missiles_landed=5,
        )
        w = _get_heavy_weights(heavy, _ACTION_IDX, self._fresh(), [heavy, medic], 0)
        # role (no missiles): [70,25,5,...]; medic different zone: tag-=30, change_zone+=30
        assert w == [40, 55, 5, 0, 0, 0, 0]

    def test_heavy_not_active_medic_in_zone_hides(self):
        """Downed heavy hides when its medic is in the same zone."""
        medic = self._state(
            self.gr, self.players["medic"], "medic",
            team_color="red", final_lives=5, current_zone=0,
        )
        heavy = self._state(
            self.gr, self.players["heavy"], "heavy",
            final_lives=5, last_downed_time=0, current_zone=0, missiles_landed=5,
        )
        w = _get_heavy_weights(heavy, _ACTION_IDX, self._fresh(), [heavy, medic], 0)
        # role (no missiles): [70,25,5,...]; not active + medic in zone: tag-=70, hide+=70
        assert w == [0, 25, 75, 0, 0, 0, 0]

    # --- Commander ---

    def test_commander_baseline_no_missiles(self):
        """Commander with all missiles used holds base weights."""
        s = self._state(self.gr, self.players["commander"], "commander", missiles_landed=5)
        w = _get_commander_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [85, 15, 0, 0, 0, 0, 0]

    def test_commander_baseline_no_missiles_sum(self):
        s = self._state(self.gr, self.players["commander"], "commander", missiles_landed=5)
        w = _get_commander_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert sum(w) == 100

    def test_commander_with_missiles(self):
        """Commander prioritises launching available missiles."""
        s = self._state(self.gr, self.players["commander"], "commander", missiles_landed=0)
        w = _get_commander_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        assert w == [85, 0, 0, 0, 0, 0, 15]
        assert sum(w) == 100

    def test_commander_special_no_enemies_fires_nuke(self):
        """Commander with nuke charged and no enemies in zone fires immediately."""
        s = self._state(
            self.gr, self.players["commander"], "commander",
            final_special=20, missiles_landed=5, current_zone=0,
        )
        w = _get_commander_weights(s, _ACTION_IDX, self._fresh(), [s], 0)
        # enemies_in_zone=0 → use_special = 100 - 20*0 = 100
        assert w[_ACTION_IDX["use_special"]] == 100

    def test_commander_special_one_enemy_reduces_nuke_weight(self):
        """Commander holds the nuke when surrounded by enemies to avoid wasting it."""
        cmd = self._state(
            self.gr, self.players["commander"], "commander",
            final_special=20, missiles_landed=5, current_zone=0, team_color="red",
        )
        enemy = self._state(
            self.gr, self.players2["scout"], "scout",
            team_color="blue", final_lives=5, current_zone=0,
        )
        w = _get_commander_weights(cmd, _ACTION_IDX, self._fresh(), [cmd, enemy], 0)
        # enemies_in_zone=1 → use_special = 100 - 20*1 = 80
        assert w[_ACTION_IDX["use_special"]] == 80

    def test_commander_not_active_enemy_medic_in_zone_hides(self):
        """Downed commander waits in zone to eliminate the enemy medic on respawn."""
        enemy_medic = self._state(
            self.gr, self.players2["medic"], "medic",
            team_color="blue", final_lives=5, current_zone=0,
        )
        cmd = self._state(
            self.gr, self.players["commander"], "commander",
            final_lives=5, last_downed_time=0, missiles_landed=5, current_zone=0,
        )
        w = _get_commander_weights(cmd, _ACTION_IDX, self._fresh(), [cmd, enemy_medic], 0)
        # not active, enemy medic in zone: tag-=70, hide+=70; base [85,15]
        assert w == [15, 15, 70, 0, 0, 0, 0]

    def test_commander_not_active_no_enemy_medic_changes_zone(self):
        """Downed commander moves zone to hunt the enemy medic."""
        cmd = self._state(
            self.gr, self.players["commander"], "commander",
            final_lives=5, last_downed_time=0, missiles_landed=5,
        )
        w = _get_commander_weights(cmd, _ACTION_IDX, self._fresh(), [cmd], 0)
        # not active, no enemy medic found: tag-=70, change_zone+=70; base [85,15]
        assert w == [15, 85, 0, 0, 0, 0, 0]


@pytest.mark.django_db
class TestSimulationChangesWithWeights:
    """Verify that changing action weights produces different simulation outcomes."""

    def test_patching_medic_weights_changes_resupply_event_count(self):
        """Forcing medic to always tag eliminates resupply events that normally occur."""
        import random

        def all_tag_weights(player, action_to_weight_index, weights, all_alive, second):
            return [100, 0, 0, 0, 0, 0, 0]

        simulator = ResourceBasedSimulator()

        team_r1, _ = make_team_with_slots("WS_R1")
        team_b1, _ = make_team_with_slots("WS_B1")
        random.seed(42)
        round_normal = simulator.simulate_single_round_detailed(team_r1, team_b1)
        normal_resupply = GameEvent.objects.filter(
            game_round=round_normal,
            event_type__in=["resupply_ammo", "resupply_lives"],
        ).count()

        team_r2, _ = make_team_with_slots("WS_R2")
        team_b2, _ = make_team_with_slots("WS_B2")
        random.seed(42)
        with patch("matches.simulation._get_medic_weights", side_effect=all_tag_weights):
            round_patched = simulator.simulate_single_round_detailed(team_r2, team_b2)
        patched_resupply = GameEvent.objects.filter(
            game_round=round_patched,
            event_type__in=["resupply_ammo", "resupply_lives"],
        ).count()

        assert normal_resupply != patched_resupply


# ---------------------------------------------------------------------------
# Batch simulator seed reproducibility
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBatchSimulatorSeedReproducibility:
    """Verify that capturing/restoring RNG state reproduces identical rounds."""

    def _rosters(self, prefix):
        team, _ = make_team_with_slots(prefix)
        return list(team.active_roster), team

    def test_same_state_produces_identical_round(self):
        """Restoring the same random.getstate() before _simulate_round gives the same scores."""
        import random
        red_roster, _ = self._rosters("SeedR1")
        blue_roster, _ = self._rosters("SeedB1")
        sim = BatchSimulator()

        random.seed(42)
        state = random.getstate()

        random.setstate(state)
        r1, _, _ = sim._simulate_round(red_roster, blue_roster)

        random.setstate(state)
        r2, _, _ = sim._simulate_round(red_roster, blue_roster)

        assert r1["red_points"] == r2["red_points"]
        assert r1["blue_points"] == r2["blue_points"]
        assert r1["red_survivors"] == r2["red_survivors"]
        assert r1["blue_survivors"] == r2["blue_survivors"]

    def test_different_seeds_produce_different_outcomes(self):
        """Sanity check: across many seeds at least some rounds produce different scores."""
        import random
        red_roster, _ = self._rosters("SeedR2")
        blue_roster, _ = self._rosters("SeedB2")
        sim = BatchSimulator()

        outcomes = set()
        for seed_val in range(20):
            random.seed(seed_val)
            r, _, _ = sim._simulate_round(red_roster, blue_roster)
            outcomes.add((r["red_points"], r["blue_points"]))

        assert len(outcomes) > 1, "Expected varied results across different seeds"

    def test_serialized_seed_reproduces_round(self):
        """Seeds round-trip through the JSON-serializable format used by views.py."""
        import random

        def serialize(state):
            v, internal, gauss = state
            return [v, list(internal), gauss]

        def deserialize(data):
            v, internal, gauss = data
            return (v, tuple(internal), gauss)

        red_roster, _ = self._rosters("SeedR3")
        blue_roster, _ = self._rosters("SeedB3")
        sim = BatchSimulator()

        random.seed(7)
        state = random.getstate()

        random.setstate(state)
        r1, _, _ = sim._simulate_round(red_roster, blue_roster)

        random.setstate(deserialize(serialize(state)))
        r2, _, _ = sim._simulate_round(red_roster, blue_roster)

        assert r1["red_points"] == r2["red_points"]
        assert r1["blue_points"] == r2["blue_points"]

    def test_mid_run_seed_replays_specific_round(self):
        """State captured after several rounds replays only that specific round."""
        import random
        red_roster, _ = self._rosters("SeedR4")
        blue_roster, _ = self._rosters("SeedB4")
        sim = BatchSimulator()

        random.seed(99)
        # Burn through 3 rounds to advance the RNG
        for _ in range(3):
            sim._simulate_round(red_roster, blue_roster)

        # Capture state before round 4
        state = random.getstate()
        round4, _, _ = sim._simulate_round(red_roster, blue_roster)

        # Replay round 4 from the saved state
        random.setstate(state)
        replay, _, _ = sim._simulate_round(red_roster, blue_roster)

        assert round4["red_points"] == replay["red_points"]
        assert round4["blue_points"] == replay["blue_points"]
        assert round4["red_survivors"] == replay["red_survivors"]
        assert round4["blue_survivors"] == replay["blue_survivors"]


# ---------------------------------------------------------------------------
# BatchSimulator — shot cooldown, follow-ups, and reactions
# ---------------------------------------------------------------------------

def _make_ps(role, team_color="red", **kwargs):
    """Create a PlayerState with sensible defaults for unit tests."""
    from matches.sim_helpers.player_state import PlayerState
    tag_id = kwargs.pop("tag_id", f"{team_color}_{role}")
    defaults = dict(
        tag_id=tag_id,
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=0,
        player_awareness=50,
        starting_lives=10,
        starting_shots=20,
        final_lives=10,
        final_shots=20,
    )
    defaults.update(kwargs)
    return PlayerState(**defaults)


class TestSimulatorTicks:
    def test_batch_simulator_tick_is_half_second(self):
        assert BatchSimulator.TICK == 0.5

    def test_resource_based_simulator_tick_is_half_second(self):
        assert ResourceBasedSimulator.TICK == 0.5


class TestBatchSimulatorShotCooldown:
    """_shot_cooldown values and per-tick tag-weight suppression in _plan_action."""

    def _sim(self):
        return BatchSimulator()

    def test_regular_roles_return_half_second(self):
        sim = self._sim()
        for role in ("commander", "medic", "ammo"):
            assert sim._shot_cooldown(_make_ps(role), 0.0) == 0.5

    def test_heavy_returns_one_second(self):
        sim = self._sim()
        assert sim._shot_cooldown(_make_ps("heavy"), 0.0) == 1.0

    def test_scout_without_special_returns_half_second(self):
        sim = self._sim()
        assert sim._shot_cooldown(_make_ps("scout", special_active_until=0), 1.0) == 0.5

    def test_rapid_fire_scout_returns_zero(self):
        sim = self._sim()
        # special_active_until=10 > second=2.0 → rapid fire active
        assert sim._shot_cooldown(_make_ps("scout", special_active_until=10), 2.0) == 0.0

    def test_plan_action_zeroes_tag_weight_when_fired_too_recently(self):
        sim = self._sim()
        # Commander cooldown=0.5; last shot at 5.0; tick=5.3 → gap=0.3 < 0.5
        p = _make_ps("commander", last_shot_time=5.0, final_shots=20)
        captured = []

        def capture(ch, wt):
            captured.append(list(wt))
            return ["change_zone"]

        with patch("random.choices", side_effect=capture):
            sim._plan_action(p, [p], 5.3)

        assert len(captured) == 1
        assert captured[0][0] == 0, "tag_player weight must be zeroed when cooldown has not elapsed"

    def test_plan_action_allows_tag_after_cooldown_elapsed(self):
        sim = self._sim()
        # Gap=0.6 > 0.5 cooldown → tag weight should be non-zero
        p = _make_ps("commander", last_shot_time=5.0, final_shots=20)
        captured = []

        def capture(ch, wt):
            captured.append(list(wt))
            return ["change_zone"]

        with patch("random.choices", side_effect=capture):
            sim._plan_action(p, [p], 5.6)

        assert len(captured) == 1
        assert captured[0][0] > 0, "tag_player weight must be non-zero after cooldown has elapsed"

    def test_last_shot_time_updated_on_hit(self):
        sim = self._sim()
        attacker = _make_ps("commander", team_color="red", final_shots=20)
        defender = _make_ps("scout", team_color="blue")
        defender.shields = 3  # commander shot_power=2; 3-2=1, not downed
        with patch("random.randint", return_value=1):  # roll 1 < hit_chance=95 → always hit
            sim._resolve_tag_attempts([{"attacker": attacker, "defender": defender}], second=7.0)
        assert attacker.last_shot_time == 7.0

    def test_last_shot_time_updated_on_miss(self):
        sim = self._sim()
        attacker = _make_ps("commander", team_color="red", final_shots=20)
        defender = _make_ps("scout", team_color="blue")
        # survival=100 → hit_chance=max(10,min(95,70+50-100))=20; roll 99 ≥ 20 → miss
        defender.survival = 100
        with patch("random.randint", return_value=99):
            sim._resolve_tag_attempts([{"attacker": attacker, "defender": defender}], second=7.0)
        assert attacker.last_shot_time == 7.0


class TestBatchSimulatorFollowUps:
    """Follow-up shot scheduling in _resolve_tag_attempts."""

    def test_non_downed_hit_schedules_follow_up(self):
        sim = BatchSimulator()
        # Scout (shot_power=1) vs commander (shields=3): 3-1=2, not downed → follow-up eligible
        attacker = _make_ps("scout", team_color="red", final_shots=20, player_awareness=0)
        defender = _make_ps("commander", team_color="blue", player_awareness=0)
        defender.shields = 3

        pending_fu = []
        with patch("random.randint", return_value=1):
            # hit: 1<95 ✓; follow-up: defender_awareness=0 < 1 → True ✓
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_followups=pending_fu,
            )

        assert len(pending_fu) == 1
        fire_at, fu_atk, fu_def, chain = pending_fu[0]
        assert fire_at == pytest.approx(10.5)  # scout cooldown = 0.5s
        assert fu_atk is attacker
        assert fu_def is defender
        assert chain == 1

    def test_downed_hit_no_follow_up(self):
        sim = BatchSimulator()
        # Heavy (shot_power=3) vs medic (shields=1): max(0,1-3)=0, downed → no follow-up
        attacker = _make_ps("heavy", team_color="red", final_shots=20)
        defender = _make_ps("medic", team_color="blue", player_awareness=0)
        defender.shields = 1

        pending_fu = []
        with patch("random.randint", return_value=1):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_followups=pending_fu,
            )

        assert len(pending_fu) == 0

    def test_rapid_fire_scout_follow_up_fires_immediately(self):
        sim = BatchSimulator()
        # Rapid-fire scout (cooldown=0.0) executes follow-ups inline, not via pending list
        attacker = _make_ps("scout", team_color="red", special_active_until=20, final_shots=20, player_awareness=0)
        defender = _make_ps("commander", team_color="blue", player_awareness=0)
        defender.shields = 3  # 3 hits to down (3→2→1→0)

        pending_fu = []
        with patch("random.randint", return_value=1):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_followups=pending_fu,
            )

        # Immediate execution, not scheduled
        assert len(pending_fu) == 0
        # Chain 1 and chain 2 both fired (commander shields: 3→2→1→0, chain stops when downed)
        assert attacker.follow_up_shots == 2

    def test_high_defender_awareness_suppresses_follow_up(self):
        sim = BatchSimulator()
        # defender_awareness=100: 100 < randint(0,100)=50 → False → no follow-up
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps("commander", team_color="blue", player_awareness=100)
        defender.shields = 3

        pending_fu = []
        with patch("random.randint", return_value=50):
            # hit: 50<95 ✓; follow-up: 100<50 → False → not scheduled
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_followups=pending_fu,
            )

        assert len(pending_fu) == 0

    def test_initial_hit_uses_one_shot_follow_up_not_yet(self):
        sim = BatchSimulator()
        # After initial hit, exactly one shot consumed; pending follow-up has NOT fired yet
        attacker = _make_ps("scout", team_color="red", final_shots=10, player_awareness=0)
        defender = _make_ps("commander", team_color="blue", player_awareness=0)
        defender.shields = 3

        shots_before = attacker.final_shots
        pending_fu = []
        with patch("random.randint", return_value=1):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_followups=pending_fu,
            )

        assert attacker.final_shots == shots_before - 1
        assert len(pending_fu) == 1  # scheduled, not yet fired


class TestBatchSimulatorReactions:
    """Reaction shot scheduling in _resolve_tag_attempts."""

    def test_reaction_scheduled_on_hit_when_awareness_passes(self):
        sim = BatchSimulator()
        # Scout (shot_power=1) vs commander (shields=3): not downed
        # Commander awareness=100 ≥ roll=50 → reaction triggered
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps("commander", team_color="blue", player_awareness=100, final_shots=20)
        defender.shields = 3

        pending_rx = []
        with patch("random.randint", return_value=50):
            # hit: 50<95 ✓; reaction: 100≥50 → True; follow-up: 100<50 → False
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 1
        fire_at, r_atk, r_def = pending_rx[0]
        assert fire_at == pytest.approx(10.5)  # commander cooldown = 0.5s
        assert r_atk is defender
        assert r_def is attacker

    def test_no_reaction_when_awareness_roll_fails(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        # awareness=0: 0 ≥ roll=50 → False → no reaction
        defender = _make_ps("commander", team_color="blue", player_awareness=0, final_shots=20)
        defender.shields = 3

        pending_rx = []
        with patch("random.randint", return_value=50):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 0

    def test_heavy_defender_reaction_has_1s_delay(self):
        sim = BatchSimulator()
        # Scout vs heavy (shields=3): not downed; heavy cooldown=1.0s
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps("heavy", team_color="blue", player_awareness=100, final_shots=20)
        defender.shields = 3

        pending_rx = []
        with patch("random.randint", return_value=50):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 1
        assert pending_rx[0][0] == pytest.approx(11.0)  # 10.0 + 1.0

    def test_inactive_defender_does_not_react(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        # last_downed_time=5 at second=10: 10-5=5 < 8 → inactive → no reaction
        defender = _make_ps("commander", team_color="blue", player_awareness=100, final_shots=20,
                            last_downed_time=5)
        defender.shields = 3

        pending_rx = []
        with patch("random.randint", return_value=50):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 0

    def test_reaction_scheduled_on_miss_when_awareness_passes(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        # survival=100 → hit_chance=20; roll=99 → miss; then awareness=100 ≥ 99 → reaction
        defender = _make_ps("commander", team_color="blue", player_awareness=100, final_shots=20)
        defender.shields = 3
        defender.survival = 100

        pending_rx = []
        with patch("random.randint", return_value=99):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0, pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 1
