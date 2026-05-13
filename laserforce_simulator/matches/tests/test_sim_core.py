"""
Core ResourceBasedSimulator tests: resupply, tag resolution, nuke mechanics,
zone changes, time boundaries, and end-to-end round creation.
"""

import pytest
from unittest.mock import patch

from teams.models import Team, Player
from matches.models import GameRound, PlayerRoundState, GameEvent
from matches.simulation import ResourceBasedSimulator
from matches.tests.conftest import make_team_with_slots


@pytest.mark.django_db
class TestSimulation:
    def create_team_with_roster(self, prefix):
        return make_team_with_slots(prefix)

    def test_get_tag_id_scout_ordering(self):
        team, players = self.create_team_with_roster("Alpha")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        s1 = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout"],
            team_color="red",
            role="scout",
            final_lives=10,
            final_shots=10,
        )
        s2 = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout_2"],
            team_color="red",
            role="scout",
            final_lives=10,
            final_shots=10,
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
        red_states = simulator._initialize_players(
            game_round, team_red, "red", {}, None
        )

        tagger = next(s for s in red_states if s.role == "ammo")
        teammate = next(s for s in red_states if s.role == "scout")

        teammate.final_shots = 1
        teammate.save()

        simulator._attempt_resupply(tagger, teammate, second=10)
        teammate.refresh_from_db()

        assert teammate.final_shots >= 1
        assert teammate.final_shots <= teammate.max_shots
        assert GameEvent.objects.filter(
            actor=tagger.player, target=teammate.player
        ).exists(), (
            "Resupply action should create a GameEvent with actor and target set"
        )

    def test_simulate_single_round_detailed_creates_completed_round(self):
        simulator = ResourceBasedSimulator()
        team_red, _ = self.create_team_with_roster("RedSim")
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
            game_round=gr,
            player=players["medic"],
            team_color="red",
            role="medic",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
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
            "tag_player",
            "change_zone",
            "hide",
            "capture_base",
            "use_special",
            "resupply_ally",
            "missile_player",
        ]
        assert captured["weights"] == [5, 0, 30, 0, 0, 65, 0]

    def test_tag_event_created_when_hit(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("TagTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["commander"],
            team_color="red",
            role="commander",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout"],
            team_color="blue",
            role="scout",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )

        with patch("random.randint", return_value=0):
            simulator._resolve_tag_attempts(
                gr, [{"attacker": attacker, "defender": defender}], 0
            )

        assert GameEvent.objects.filter(
            event_type="tag", actor=attacker.player, target=defender.player
        ).exists()

    def test_missile_dodge_and_hit_events(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("MissileTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["commander"],
            team_color="red",
            role="commander",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
            final_missiles=2,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout"],
            team_color="blue",
            role="scout",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )

        # Dodge
        with patch("random.choices", return_value=["missile_player"]), patch(
            "random.choice", return_value=defender
        ), patch("random.random", return_value=0.1):
            simulator._simulate_combat_exchange(
                gr,
                [attacker],
                [defender],
                second=5,
                pending_missiles=[],
                pending_nukes=[],
            )

        assert GameEvent.objects.filter(
            event_type="missile_dodge",
            actor=defender.player,
            target=attacker.player,
        ).exists()

        # Hit
        pending_missiles = []
        with patch("random.choices", return_value=["missile_player"]), patch(
            "random.choice", return_value=defender
        ), patch("random.random", return_value=0.5), patch(
            "random.randint", return_value=1
        ):
            simulator._simulate_combat_exchange(
                gr,
                [attacker],
                [defender],
                second=6,
                pending_missiles=pending_missiles,
                pending_nukes=[],
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
            game_round=gr,
            player=player_obj,
            team_color="red",
            role="scout",
            zone_fallback=1,
            final_shots=5,
            final_lives=10,
        )

        with patch("random.choices", return_value=["capture_base"]):
            simulator._simulate_combat_exchange(
                gr,
                [state],
                [],
                second=2,
                pending_missiles=[],
                pending_nukes=[],
            )

        state.refresh_from_db()
        assert state.points_scored >= 1001
        assert state.last_tagged_id in [
            PlayerRoundState.tag_id.neutral_base,
            PlayerRoundState.tag_id.red_base,
            PlayerRoundState.tag_id.blue_base,
        ]

        state2 = PlayerRoundState.objects.create(
            game_round=gr,
            player=player_obj,
            team_color="red",
            role="scout",
            zone_fallback=0,
            final_shots=5,
            final_lives=10,
        )
        with patch("random.choices", return_value=["change_zone"]), patch(
            "random.choice", return_value=1
        ):
            simulator._simulate_combat_exchange(
                gr,
                [state2],
                [],
                second=3,
                pending_missiles=[],
                pending_nukes=[],
            )

        assert state2.current_zone == 1

    def test_specific_tags_bookkeeping_and_resupply_edge_cases(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["commander"],
            team_color="red",
            role="commander",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout"],
            team_color="blue",
            role="scout",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )

        with patch("random.randint", return_value=0):
            simulator._resolve_tag_attempts(
                gr, [{"attacker": attacker, "defender": defender}], 0
            )

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
        red_states = simulator._initialize_players(gr, team, "red", {}, None)
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
        red_states = simulator._initialize_players(gr, team, "red", {}, None)
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
            game_round=gr,
            player=players["scout"],
            team_color="red",
            role="scout",
            zone_fallback=0,
            final_shots=5,
            final_lives=5,
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
            game_round=gr,
            player=players["commander"],
            team_color="red",
            role="commander",
            zone_fallback=0,
            final_shots=10,
            final_lives=3,
            shields=3,
        )
        heavy = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["heavy"],
            team_color="red",
            role="heavy",
            zone_fallback=0,
            final_shots=10,
            final_lives=3,
            shields=3,
        )
        attacker = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout"],
            team_color="blue",
            role="scout",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )

        for _ in range(3):
            with patch("random.randint", return_value=0):
                simulator._resolve_tag_attempts(
                    gr, [{"attacker": attacker, "defender": commander}], 0
                )

        commander.refresh_from_db()
        assert commander.final_lives == 2
        assert not commander.is_active_at(3)

        attacker.final_shots = 10
        attacker.save()

        for _ in range(3):
            with patch("random.randint", return_value=0):
                simulator._resolve_tag_attempts(
                    gr, [{"attacker": attacker, "defender": heavy}], 0
                )

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
                game_round=gr,
                player=players[role],
                team_color="red",
                role=role,
                zone_fallback=0,
                final_shots=10,
                final_lives=1,
                shields=1,
            )
            role_states[role] = state

        attacker = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout_2"],
            team_color="blue",
            role="scout",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )

        for role, state in role_states.items():
            with patch("random.randint", return_value=0):
                simulator._resolve_tag_attempts(
                    gr, [{"attacker": attacker, "defender": state}], 0
                )

            state.refresh_from_db()
            assert state.final_lives == 0
            assert not state.is_active_at(1), f"{role} should be downed after 1 tag"

    def test_takes_2_tags_from_commander_to_down_heavy(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("DownTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        heavy = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["heavy"],
            team_color="red",
            role="heavy",
            zone_fallback=0,
            final_shots=10,
            final_lives=2,
            shields=3,
        )
        commander = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["commander"],
            team_color="blue",
            role="commander",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )

        for _ in range(2):
            with patch("random.randint", return_value=0):
                simulator._resolve_tag_attempts(
                    gr, [{"attacker": commander, "defender": heavy}], 0
                )

        heavy.refresh_from_db()
        assert heavy.final_lives == 1
        assert not heavy.is_active_at(2)

    def test_cannot_be_resupplied_while_downed(self):
        simulator = ResourceBasedSimulator()
        team, _ = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        red_states = simulator._initialize_players(gr, team, "red", {}, None)
        medic = next(s for s in red_states if s.role == "medic")
        teammate = next(s for s in red_states if s.role == "heavy")
        teammate.final_lives = 1
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
            game_round=gr,
            player=players["scout"],
            team_color="red",
            role="scout",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
        )
        dead = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["medic"],
            team_color="blue",
            role="medic",
            zone_fallback=0,
            final_shots=0,
            final_lives=0,
        )

        with patch("random.choices", return_value=["tag_player"]), patch(
            "random.choice", return_value=dead
        ), patch("random.random", return_value=0.0):
            plans = simulator._plan_action(attacker, [attacker, dead], second=1)

        tag_plans = [p for p in plans if p.get("type") == "tag"]
        assert len(tag_plans) == 0, "Dead player should not be selectable as tag target"

    def test_nuke_scheduling_and_cancellation(self):
        simulator = ResourceBasedSimulator()
        team_red, players_red = self.create_team_with_roster("NukeRed")
        team_blue, _ = self.create_team_with_roster("NukeBlue")
        gr = GameRound.objects.create(
            team_red=team_red, team_blue=team_blue, round_number=1
        )

        commander = PlayerRoundState.objects.create(
            game_round=gr,
            player=players_red["commander"],
            team_color="red",
            role="commander",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
            final_special=20,
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
        team_red, players_red = self.create_team_with_roster("NukeRed")
        team_blue, players_blue = self.create_team_with_roster("NukeBlue")
        gr = GameRound.objects.create(
            team_red=team_red, team_blue=team_blue, round_number=1
        )

        blue_commander = PlayerRoundState.objects.create(
            game_round=gr,
            player=players_blue["commander"],
            team_color="blue",
            role="heavy",
            zone_fallback=0,
            final_shots=10,
            final_lives=4,
        )
        heavy = PlayerRoundState.objects.create(
            game_round=gr,
            player=players_blue["heavy"],
            team_color="blue",
            role="heavy",
            zone_fallback=0,
            final_shots=10,
            final_lives=3,
        )
        scout = PlayerRoundState.objects.create(
            game_round=gr,
            player=players_blue["scout"],
            team_color="blue",
            role="scout",
            zone_fallback=0,
            final_shots=10,
            final_lives=2,
        )
        ammo = PlayerRoundState.objects.create(
            game_round=gr,
            player=players_blue["ammo"],
            team_color="blue",
            role="ammo",
            zone_fallback=0,
            final_shots=15,
            final_lives=1,
        )
        medic = PlayerRoundState.objects.create(
            game_round=gr,
            player=players_blue["medic"],
            team_color="blue",
            role="medic",
            zone_fallback=0,
            final_shots=10,
            final_lives=0,
            was_eliminated_at=15,
        )
        commander = PlayerRoundState.objects.create(
            game_round=gr,
            player=players_red["commander"],
            team_color="red",
            role="commander",
            zone_fallback=0,
            final_shots=10,
            final_lives=10,
            final_special=20,
            points_scored=0,
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
        return GameRound.objects.create(
            team_red=team_red, team_blue=team_blue, round_number=1
        )

    def _make_state(self, gr, player, team_color, role, **kwargs):
        return PlayerRoundState.objects.create(
            game_round=gr,
            player=player,
            team_color=team_color,
            role=role,
            final_shots=10,
            **kwargs,
        )

    def test_lives_lost_no_nukes(self):
        team = self.create_team_with_roster("Unit")
        gr = self._make_round(team, team)
        state = self._make_state(
            gr,
            team.slot_scout_1,
            "red",
            "scout",
            final_lives=10,
            times_tagged=3,
            times_missiled=1,
            lives_lost_to_nukes=0,
        )
        assert state.lives_lost == 5

    def test_lives_lost_includes_nuke_field(self):
        team = self.create_team_with_roster("Unit2")
        gr = self._make_round(team, team)
        state = self._make_state(
            gr,
            team.slot_scout_1,
            "red",
            "scout",
            final_lives=4,
            times_tagged=1,
            times_missiled=0,
            lives_lost_to_nukes=3,
        )
        assert state.lives_lost == 4

    def test_lives_lost_never_negative(self):
        team = self.create_team_with_roster("Unit3")
        gr = self._make_round(team, team)
        state = self._make_state(
            gr,
            team.slot_scout_1,
            "red",
            "scout",
            final_lives=10,
            times_tagged=0,
            times_missiled=0,
            lives_lost_to_nukes=0,
        )
        assert state.lives_lost == 0

    def test_nuke_removes_3_lives_from_healthy_opponent(self):
        simulator = ResourceBasedSimulator()
        red = self.create_team_with_roster("NukeA_Red")
        blue = self.create_team_with_roster("NukeA_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(
            gr, red.slot_commander, "red", "commander", final_lives=10, final_special=20
        )
        target = self._make_state(gr, blue.slot_scout_1, "blue", "scout", final_lives=5)

        simulator._complete_nuke(commander, second=30)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 3
        assert target.final_lives == 2
        assert target.lives_lost == 3

    def test_nuke_exception_player_has_2_lives(self):
        simulator = ResourceBasedSimulator()
        red = self.create_team_with_roster("NukeB_Red")
        blue = self.create_team_with_roster("NukeB_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(
            gr, red.slot_commander, "red", "commander", final_lives=10, final_special=20
        )
        target = self._make_state(gr, blue.slot_scout_1, "blue", "scout", final_lives=2)

        simulator._complete_nuke(commander, second=30)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 2
        assert target.final_lives == 0
        assert target.lives_lost == 2

    def test_nuke_exception_player_has_1_life(self):
        simulator = ResourceBasedSimulator()
        red = self.create_team_with_roster("NukeC_Red")
        blue = self.create_team_with_roster("NukeC_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(
            gr, red.slot_commander, "red", "commander", final_lives=10, final_special=20
        )
        target = self._make_state(gr, blue.slot_scout_1, "blue", "scout", final_lives=1)

        simulator._complete_nuke(commander, second=30)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 1
        assert target.final_lives == 0

    def test_nuke_skips_already_eliminated_player(self):
        simulator = ResourceBasedSimulator()
        red = self.create_team_with_roster("NukeD_Red")
        blue = self.create_team_with_roster("NukeD_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(
            gr, red.slot_commander, "red", "commander", final_lives=10, final_special=20
        )
        dead = self._make_state(
            gr, blue.slot_scout_1, "blue", "scout", final_lives=0, was_eliminated_at=50
        )

        simulator._complete_nuke(commander, second=30)
        dead.refresh_from_db()

        assert dead.lives_lost_to_nukes == 0
        assert dead.lives_lost == 0

    def test_nuke_accumulates_across_multiple_nukes(self):
        simulator = ResourceBasedSimulator()
        red = self.create_team_with_roster("NukeE_Red")
        blue = self.create_team_with_roster("NukeE_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(
            gr, red.slot_commander, "red", "commander", final_lives=10, final_special=40
        )
        target = self._make_state(gr, blue.slot_heavy, "blue", "heavy", final_lives=10)

        simulator._complete_nuke(commander, second=20)
        target.refresh_from_db()
        simulator._complete_nuke(commander, second=40)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 6
        assert target.final_lives == 4
        assert target.lives_lost == 6


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
        with patch(
            "matches.sim_helpers.combat._get_medic_weights", side_effect=all_tag_weights
        ):
            round_patched = simulator.simulate_single_round_detailed(team_r2, team_b2)
        patched_resupply = GameEvent.objects.filter(
            game_round=round_patched,
            event_type__in=["resupply_ammo", "resupply_lives"],
        ).count()

        assert normal_resupply != patched_resupply


class TestSimulatorTicks:
    def test_batch_simulator_tick_is_half_second(self):
        from matches.simulation import BatchSimulator

        assert BatchSimulator.TICK == 0.5

    def test_resource_based_simulator_tick_is_half_second(self):
        assert ResourceBasedSimulator.TICK == 0.5
