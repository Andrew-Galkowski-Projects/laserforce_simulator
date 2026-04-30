import pytest
from unittest.mock import patch

from teams.models import Team, Player
from matches.models import GameRound, PlayerRoundState, GameEvent
from matches.simulation import ResourceBasedSimulator


@pytest.mark.django_db
class TestSimulation:
    def create_team_with_roster(self, prefix):
        team = Team.objects.create(name=f"{prefix} Team")
        roles = ["commander", "heavy", "scout", "scout", "medic", "ammo"]
        players = []
        for i, role in enumerate(roles):
            p = Player.objects.create(team=team, name=f"{prefix} {role} {i}", role=role)
            players.append(p)
        return team, players

    def test_get_tag_id_scout_ordering(self):
        team, players = self.create_team_with_roster("Alpha")
        scouts = list(team.players.filter(role="scout").order_by("name"))
        assert len(scouts) == 2

        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        s1 = PlayerRoundState.objects.create(
            game_round=gr, player=scouts[0], team_color="red", role="scout",
            final_lives=10, final_shots=10,
        )
        s2 = PlayerRoundState.objects.create(
            game_round=gr, player=scouts[1], team_color="red", role="scout",
            final_lives=10, final_shots=10,
        )

        assert s1.get_tag_id == PlayerRoundState.tag_id.red_scout_1
        assert s2.get_tag_id == PlayerRoundState.tag_id.red_scout_2

    def test_resupply_ammo_caps_shots_and_creates_event(self):
        simulator = ResourceBasedSimulator()
        team_red, red_players = self.create_team_with_roster("Red")
        team_blue, blue_players = self.create_team_with_roster("Blue")

        game_round = GameRound.objects.create(
            team_red=team_red, team_blue=team_blue, round_number=1
        )
        red_states = simulator._initialize_players(game_round, team_red.players.all(), "red")

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
        ).exists(), "Resupply action should create a GameEvent with actor and target set"

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

        medic_player = next(p for p in team.players.all() if p.role == "medic")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        medic_state = PlayerRoundState.objects.create(
            game_round=gr, player=medic_player, team_color="red", role="medic",
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
        assert captured["weights"] == [0, 0, 30, 0, 0, 70, 0]

    def test_tag_event_created_when_hit(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("TagTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="commander").first(),
            team_color="red", role="commander", current_zone=0, final_shots=10, final_lives=10,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="scout").first(),
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
            game_round=gr, player=team.players.filter(role="commander").first(),
            team_color="red", role="commander", current_zone=0,
            final_shots=10, final_lives=10, final_missiles=2,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="scout").first(),
            team_color="blue", role="scout", current_zone=0, final_shots=10, final_lives=10,
        )

        # Dodge: random.random < dodge_chance (0.2)
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

        # Hit: random.random >= dodge_chance -> missile is scheduled
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

        player_obj = team.players.first()
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
            game_round=gr, player=team.players.filter(role="commander").first(),
            team_color="red", role="commander", current_zone=0, final_shots=10, final_lives=10,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="scout").first(),
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
        team, players = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        red_states = simulator._initialize_players(gr, team.players.all(), "red")
        ammo = next(s for s in red_states if s.role == "ammo")
        scout_state = next(s for s in red_states if s.role == "scout")
        scout_state.final_shots = scout_state.max_shots
        scout_state.save()

        simulator._attempt_resupply(ammo, scout_state, second=10)
        scout_state.refresh_from_db()

        assert scout_state.final_shots <= scout_state.max_shots

    def test_resupply_medic_no_shots_no_heal(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        red_states = simulator._initialize_players(gr, team.players.all(), "red")
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
            game_round=gr, player=team.players.first(), team_color="red", role="scout",
            current_zone=0, final_shots=5, final_lives=5,
        )

        assert state.is_active_at(0)
        assert state.is_taggable_at(0)

        state.last_downed_time = 10
        state.final_lives = 1
        state.save()

        # Within 3 seconds -> not taggable (< 4s) and not active (< 8s)
        assert not state.is_taggable_at(12)
        assert not state.is_active_at(12)
        assert not state.is_resupplyable_at(12)

        # After 5 seconds -> taggable but not active until 8s
        assert state.is_taggable_at(15)
        assert not state.is_active_at(15)
        assert not state.is_resupplyable_at(15)

        # After 9 seconds -> fully active
        assert state.is_active_at(20)
        assert state.is_resupplyable_at(20)

    def test_takes_3_tags_to_down_commander_and_heavy(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("DownTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        commander = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="commander").first(),
            team_color="red", role="commander", current_zone=0,
            final_shots=10, final_lives=3, shields=3,
        )
        heavy = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="heavy").first(),
            team_color="red", role="heavy", current_zone=0,
            final_shots=10, final_lives=3, shields=3,
        )
        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="scout").first(),
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

        roles_to_test = ["scout", "medic", "ammo"]
        role_states = {}
        for role in roles_to_test:
            state = PlayerRoundState.objects.create(
                game_round=gr, player=team.players.filter(role=role).first(),
                team_color="red", role=role, current_zone=0,
                final_shots=10, final_lives=1, shields=1,
            )
            role_states[role] = state

        attacker = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="scout").first(),
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
            game_round=gr, player=team.players.filter(role="heavy").first(),
            team_color="red", role="heavy", current_zone=0,
            final_shots=10, final_lives=2, shields=3,
        )
        commander = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="commander").first(),
            team_color="blue", role="commander", shot_power=2,
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
        team, players = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        red_states = simulator._initialize_players(gr, team.players.all(), "red")
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
            game_round=gr, player=team.players.filter(role="scout").first(),
            team_color="red", role="scout", current_zone=0, final_shots=10, final_lives=10,
        )
        dead = PlayerRoundState.objects.create(
            game_round=gr, player=team.players.filter(role="medic").first(),
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
        team_red, _ = self.create_team_with_roster("NukeRed")
        team_blue, _ = self.create_team_with_roster("NukeBlue")
        gr = GameRound.objects.create(team_red=team_red, team_blue=team_blue, round_number=1)

        commander = PlayerRoundState.objects.create(
            game_round=gr, player=team_red.players.filter(role="commander").first(),
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
        team_red, _ = self.create_team_with_roster("NukeRed")
        team_blue, _ = self.create_team_with_roster("NukeBlue")
        gr = GameRound.objects.create(team_red=team_red, team_blue=team_blue, round_number=1)

        blue_commander = PlayerRoundState.objects.create(
            game_round=gr, player=team_blue.players.filter(role="commander").first(),
            team_color="blue", role="heavy", current_zone=0, final_shots=10, final_lives=4,
        )
        heavy = PlayerRoundState.objects.create(
            game_round=gr, player=team_blue.players.filter(role="heavy").first(),
            team_color="blue", role="heavy", current_zone=0, final_shots=10, final_lives=3,
        )
        scout = PlayerRoundState.objects.create(
            game_round=gr, player=team_blue.players.filter(role="scout").first(),
            team_color="blue", role="scout", current_zone=0, final_shots=10, final_lives=2,
        )
        ammo = PlayerRoundState.objects.create(
            game_round=gr, player=team_blue.players.filter(role="ammo").first(),
            team_color="blue", role="ammo", current_zone=0, final_shots=15, final_lives=1,
        )
        medic = PlayerRoundState.objects.create(
            game_round=gr, player=team_blue.players.filter(role="medic").first(),
            team_color="blue", role="medic", current_zone=0,
            final_shots=10, final_lives=0, was_eliminated_at=15,
        )
        commander = PlayerRoundState.objects.create(
            game_round=gr, player=team_red.players.filter(role="commander").first(),
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
        team = Team.objects.create(name=f"{prefix} Team")
        roles = ["commander", "heavy", "scout", "scout", "medic", "ammo"]
        for i, role in enumerate(roles):
            Player.objects.create(team=team, name=f"{prefix} {role} {i}", role=role)
        return team

    def _make_round(self, team_red, team_blue):
        return GameRound.objects.create(team_red=team_red, team_blue=team_blue, round_number=1)

    def _make_state(self, gr, player, team_color, role, **kwargs):
        return PlayerRoundState.objects.create(
            game_round=gr, player=player, team_color=team_color, role=role,
            final_shots=10, **kwargs
        )

    # --- property unit tests (no simulation calls) ---

    def test_lives_lost_no_nukes(self):
        team = self.create_team_with_roster("Unit")
        gr = self._make_round(team, team)
        state = self._make_state(gr, team.players.filter(role="scout").first(), "red", "scout",
                                 final_lives=10, times_tagged=3, times_missiled=1,
                                 lives_lost_to_nukes=0)
        # 3 tags + 1 missile * 2 = 5
        assert state.lives_lost == 5

    def test_lives_lost_includes_nuke_field(self):
        team = self.create_team_with_roster("Unit2")
        gr = self._make_round(team, team)
        state = self._make_state(gr, team.players.filter(role="scout").first(), "red", "scout",
                                 final_lives=4, times_tagged=1, times_missiled=0,
                                 lives_lost_to_nukes=3)
        # 1 tag + 3 nuke = 4
        assert state.lives_lost == 4

    def test_lives_lost_never_negative(self):
        team = self.create_team_with_roster("Unit3")
        gr = self._make_round(team, team)
        state = self._make_state(gr, team.players.filter(role="scout").first(), "red", "scout",
                                 final_lives=10, times_tagged=0, times_missiled=0,
                                 lives_lost_to_nukes=0)
        assert state.lives_lost == 0

    # --- integration tests via _complete_nuke ---

    def test_nuke_removes_3_lives_from_healthy_opponent(self):
        simulator = ResourceBasedSimulator()
        red = self.create_team_with_roster("NukeA_Red")
        blue = self.create_team_with_roster("NukeA_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(gr, red.players.filter(role="commander").first(),
                                     "red", "commander", final_lives=10, final_special=20)
        target = self._make_state(gr, blue.players.filter(role="scout").first(),
                                  "blue", "scout", final_lives=5)

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

        commander = self._make_state(gr, red.players.filter(role="commander").first(),
                                     "red", "commander", final_lives=10, final_special=20)
        target = self._make_state(gr, blue.players.filter(role="scout").first(),
                                  "blue", "scout", final_lives=2)

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

        commander = self._make_state(gr, red.players.filter(role="commander").first(),
                                     "red", "commander", final_lives=10, final_special=20)
        target = self._make_state(gr, blue.players.filter(role="scout").first(),
                                  "blue", "scout", final_lives=1)

        simulator._complete_nuke(commander, second=30)
        target.refresh_from_db()

        assert target.lives_lost_to_nukes == 1
        assert target.final_lives == 0

    def test_nuke_skips_already_eliminated_player(self):
        simulator = ResourceBasedSimulator()
        red = self.create_team_with_roster("NukeD_Red")
        blue = self.create_team_with_roster("NukeD_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(gr, red.players.filter(role="commander").first(),
                                     "red", "commander", final_lives=10, final_special=20)
        dead = self._make_state(gr, blue.players.filter(role="scout").first(),
                                "blue", "scout", final_lives=0, was_eliminated_at=50)

        simulator._complete_nuke(commander, second=30)
        dead.refresh_from_db()

        assert dead.lives_lost_to_nukes == 0
        assert dead.lives_lost == 0

    def test_nuke_accumulates_across_multiple_nukes(self):
        simulator = ResourceBasedSimulator()
        red = self.create_team_with_roster("NukeE_Red")
        blue = self.create_team_with_roster("NukeE_Blue")
        gr = self._make_round(red, blue)

        commander = self._make_state(gr, red.players.filter(role="commander").first(),
                                     "red", "commander", final_lives=10, final_special=40)
        target = self._make_state(gr, blue.players.filter(role="heavy").first(),
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

    def _add_player(self, team, role, name=None):
        return Player.objects.create(
            team=team, name=name or f"{role}-{team.players.count()}", role=role
        )

    def _full_roster(self, team, double_role="scout"):
        """Add a complete valid 6-player roster, doubling the given role."""
        roles = ["commander", "heavy", "scout", "medic", "ammo", double_role]
        for i, role in enumerate(roles):
            Player.objects.create(team=team, name=f"p{i}", role=role)

    # --- is_valid_roster ---

    def test_valid_roster_with_two_scouts(self):
        team = self._make_team("Valid")
        self._full_roster(team, double_role="scout")
        assert team.is_valid_roster

    def test_valid_roster_with_one_scout(self):
        team = self._make_team("OneScout")
        for i, role in enumerate(["commander", "heavy", "scout", "medic", "ammo"]):
            Player.objects.create(team=team, name=f"p{i}", role=role)
        # 5 players — not valid (needs 6)
        assert not team.is_valid_roster

    def test_invalid_roster_two_commanders(self):
        team = self._make_team("2Cmd")
        self._full_roster(team, double_role="commander")
        assert not team.is_valid_roster

    def test_invalid_roster_two_medics(self):
        team = self._make_team("2Med")
        self._full_roster(team, double_role="medic")
        assert not team.is_valid_roster

    def test_bench_players_excluded_from_roster_check(self):
        team = self._make_team("WithBench")
        self._full_roster(team, double_role="scout")
        # Add 3 bench players — should still be valid
        for i in range(3):
            Player.objects.create(team=team, name=f"bench{i}", role="bench")
        assert team.is_valid_roster

    def test_bench_players_dont_count_as_active(self):
        team = self._make_team("BenchOnly")
        for i in range(6):
            Player.objects.create(team=team, name=f"bench{i}", role="bench")
        assert not team.is_valid_roster

    # --- Player.clean() ---

    def test_clean_rejects_second_commander(self):
        from django.core.exceptions import ValidationError
        team = self._make_team("CleanCmd")
        self._add_player(team, "commander")
        p = Player(team=team, name="cmd2", role="commander")
        with pytest.raises(ValidationError, match="Only the Scout role"):
            p.clean()

    def test_clean_rejects_second_medic(self):
        from django.core.exceptions import ValidationError
        team = self._make_team("CleanMed")
        self._add_player(team, "medic")
        p = Player(team=team, name="med2", role="medic")
        with pytest.raises(ValidationError, match="Only the Scout role"):
            p.clean()

    def test_clean_allows_second_scout(self):
        team = self._make_team("CleanScout")
        self._add_player(team, "scout")
        p = Player(team=team, name="scout2", role="scout")
        p.clean()  # should not raise

    def test_clean_rejects_third_scout(self):
        from django.core.exceptions import ValidationError
        team = self._make_team("3Scouts")
        self._add_player(team, "scout", "s1")
        self._add_player(team, "scout", "s2")
        p = Player(team=team, name="s3", role="scout")
        with pytest.raises(ValidationError):
            p.clean()

    def test_clean_rejects_more_than_6_bench(self):
        from django.core.exceptions import ValidationError
        team = self._make_team("ManyBench")
        for i in range(6):
            Player.objects.create(team=team, name=f"b{i}", role="bench")
        p = Player(team=team, name="b7", role="bench")
        with pytest.raises(ValidationError, match="6 bench"):
            p.clean()

    def test_clean_allows_bench_alongside_full_roster(self):
        team = self._make_team("BenchOK")
        self._full_roster(team, double_role="scout")
        p = Player(team=team, name="sub", role="bench")
        p.clean()  # should not raise

    # --- roster_errors ---

    def test_roster_errors_empty_for_valid_roster(self):
        team = self._make_team("ErrValid")
        self._full_roster(team, double_role="scout")
        assert team.roster_errors == []

    def test_roster_errors_reports_missing_role(self):
        team = self._make_team("ErrMissing")
        # Add 6 players but omit medic, double scout instead
        for i, role in enumerate(["commander", "heavy", "scout", "scout", "ammo", "scout"]):
            Player.objects.create(team=team, name=f"p{i}", role=role)
        errors = team.roster_errors
        assert any("medic" in e.lower() for e in errors)

    def test_roster_errors_reports_duplicate_non_scout(self):
        team = self._make_team("ErrDupe")
        self._full_roster(team, double_role="commander")
        errors = team.roster_errors
        assert any("commander" in e.lower() for e in errors)

    def test_roster_errors_reports_too_few_players(self):
        team = self._make_team("ErrFew")
        for i, role in enumerate(["commander", "heavy", "scout", "medic", "ammo"]):
            Player.objects.create(team=team, name=f"p{i}", role=role)
        errors = team.roster_errors
        assert any("5" in e for e in errors)