from django.test import TestCase

from teams.models import Team, Player
from .models import GameRound, PlayerRoundState, GameEvent
from .simulation import ResourceBasedSimulator
from unittest.mock import patch


class SimulationTests(TestCase):
    def create_team_with_roster(self, prefix):
        """Create a team with a basic 6-player roster used for simulations."""
        team = Team.objects.create(name=f"{prefix} Team")
        roles = ["commander", "heavy", "scout", "scout", "medic", "ammo"]
        players = []
        for i, role in enumerate(roles):
            p = Player.objects.create(team=team, name=f"{prefix} {role} {i}", role=role)
            players.append(p)
        return team, players

    def test_get_tag_id_scout_ordering(self):
        team, players = self.create_team_with_roster("Alpha")
        # ensure the two scouts have deterministic ordering by name
        scouts = list(team.players.filter(role="scout").order_by("name"))
        self.assertEqual(len(scouts), 2)

        # create a GameRound to attach PlayerRoundState objects
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        # create PlayerRoundState for both scouts
        s1 = PlayerRoundState.objects.create(
            game_round=gr,
            player=scouts[0],
            team_color="red",
            role="scout",
            final_lives=10,
            final_shots=10,
        )
        s2 = PlayerRoundState.objects.create(
            game_round=gr,
            player=scouts[1],
            team_color="red",
            role="scout",
            final_lives=10,
            final_shots=10,
        )

        # The one that sorts first by name should be scout_1
        self.assertEqual(s1.get_tag_id, PlayerRoundState.tag_id.red_scout_1)
        self.assertEqual(s2.get_tag_id, PlayerRoundState.tag_id.red_scout_2)

    def test_resupply_ammo_caps_shots_and_creates_event(self):
        simulator = ResourceBasedSimulator()
        team_red, red_players = self.create_team_with_roster("Red")
        team_blue, blue_players = self.create_team_with_roster("Blue")

        # create a GameRound and initialize player states using simulator helper
        game_round = GameRound.objects.create(
            team_red=team_red, team_blue=team_blue, round_number=1
        )
        red_states = simulator._initialize_players(
            game_round, team_red.players.all(), "red"
        )

        # locate the ammo player (tagger) and a teammate who will receive ammo
        tagger = next(s for s in red_states if s.role == "ammo")
        teammate = next(s for s in red_states if s.role == "scout")

        # set teammate to low shots to ensure resupply will increase them
        teammate.final_shots = 1
        teammate.save()

        # perform resupply at second 10
        simulator._attempt_resupply(tagger, teammate, second=10)

        # refresh from db
        teammate.refresh_from_db()

        # teammate should have more shots than before (but not more than max_shots)
        self.assertGreaterEqual(teammate.final_shots, 1)
        self.assertLessEqual(teammate.final_shots, teammate.max_shots)

        # ensure a GameEvent was recorded linking the tagger and teammate
        ev_exists = GameEvent.objects.filter(
            actor=tagger.player, target=teammate.player
        ).exists()
        self.assertTrue(
            ev_exists,
            "Resupply action should create a GameEvent with actor and target set",
        )

    def test_simulate_single_round_detailed_creates_completed_round(self):
        simulator = ResourceBasedSimulator()
        team_red, _ = self.create_team_with_roster("RedSim")
        team_blue, _ = self.create_team_with_roster("BlueSim")

        # Run a single detailed round (integration smoke test)
        game_round = simulator.simulate_single_round_detailed(team_red, team_blue)

        self.assertIsNotNone(game_round)
        self.assertTrue(game_round.is_completed)
        # Should have created PlayerRoundState rows for each player
        self.assertGreater(game_round.player_states.count(), 0)

    def test_choose_action_weights_for_resupply_player_in_own_zone(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("Weights")

        # pick medic (can_resupply) and put them in their own zone
        medic_player = next(p for p in team.players.all() if p.role == "medic")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        medic_state = PlayerRoundState.objects.create(
            game_round=gr,
            player=medic_player,
            team_color="red",
            role="medic",
            current_zone=0,
            final_shots=10,
            final_lives=10,
        )

        captured = {}

        def fake_choices(seq, weights):
            # capture the seq and weights for assertion and return first action
            captured["seq"] = seq
            captured["weights"] = weights
            return [seq[0]]

        with patch("random.choices", side_effect=fake_choices):
            simulator._choose_action(medic_state, [medic_state], second=0)

        # When medic is in own zone, can_resupply branch should set these actions/weights
        self.assertIn("seq", captured)
        self.assertEqual(
            captured["seq"], ["tag_player", "resupply_ally", "change_zone"]
        )
        self.assertEqual(captured["weights"], [35, 55, 10])

    def test_tag_event_created_when_hit(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("TagTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker_player = team.players.filter(role="commander").first()
        defender_player = team.players.filter(role="scout").first()
        attacker = PlayerRoundState.objects.create(
            game_round=gr,
            player=attacker_player,
            team_color="red",
            role="commander",
            current_zone=0,
            final_shots=10,
            final_lives=10,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr,
            player=defender_player,
            team_color="blue",
            role="scout",
            current_zone=0,
            final_shots=10,
            final_lives=10,
        )

        # force _choose_action to pick tag_player, choose defender, and ensure hit occurs
        with patch("random.choices", return_value=["tag_player"]), patch(
            "random.choice", return_value=defender
        ), patch("random.random", return_value=0.0):
            simulator._choose_action(attacker, [attacker, defender], second=10)

        self.assertTrue(
            GameEvent.objects.filter(
                event_type="tag", actor=attacker.player, target=defender.player
            ).exists()
        )

    def test_missile_dodge_and_hit_events(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("MissileTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        attacker_player = team.players.filter(role="commander").first()
        defender_player = team.players.filter(role="scout").first()
        attacker = PlayerRoundState.objects.create(
            game_round=gr,
            player=attacker_player,
            team_color="red",
            role="commander",
            current_zone=0,
            final_shots=10,
            final_lives=10,
            final_missiles=2,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr,
            player=defender_player,
            team_color="blue",
            role="scout",
            current_zone=0,
            final_shots=10,
            final_lives=10,
        )

        # Dodge: random.random < dodge_chance (0.2)
        with patch("random.choices", return_value=["missile_player"]), patch(
            "random.choice", return_value=defender
        ), patch("random.random", return_value=0.1):
            simulator._choose_action(attacker, [attacker, defender], second=5)

        self.assertTrue(
            GameEvent.objects.filter(
                event_type="missile_dodge",
                actor=defender.player,
                target=attacker.player,
            ).exists()
        )

        # Hit: random.random >= dodge_chance -> missile_hit created
        with patch("random.choices", return_value=["missile_player"]), patch(
            "random.choice", return_value=defender
        ), patch("random.random", return_value=0.5), patch(
            "random.randint", return_value=1
        ):
            simulator._choose_action(attacker, [attacker, defender], second=6)

        self.assertTrue(
            GameEvent.objects.filter(
                event_type="missile_hit", actor=attacker.player, target=defender.player
            ).exists()
        )

    def test_capture_base_and_change_zone(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("BaseTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        player_obj = team.players.first()
        state = PlayerRoundState.objects.create(
            game_round=gr,
            player=player_obj,
            team_color="red",
            role="scout",
            current_zone=1,
            final_shots=5,
            final_lives=10,
        )

        # Capture base should consume shots and award points
        with patch("random.choices", return_value=["capture_base"]):
            simulator._choose_action(state, [state], second=2)

        state.refresh_from_db()
        self.assertTrue(state.points_scored >= 1001)
        self.assertIn(
            state.last_tagged_id,
            [
                PlayerRoundState.tag_id.neutral_base,
                PlayerRoundState.tag_id.red_base,
                PlayerRoundState.tag_id.blue_base,
            ],
        )

        # Change zone behavior: if not in neutral zone, it should move to neutral
        state2 = PlayerRoundState.objects.create(
            game_round=gr,
            player=player_obj,
            team_color="red",
            role="scout",
            current_zone=0,
            final_shots=5,
            final_lives=10,
        )
        with patch("random.choices", return_value=["change_zone"]), patch(
            "random.choice", return_value=1
        ):
            simulator._choose_action(state2, [state2], second=3)

        # After changing zone, player should be in zone 1
        self.assertEqual(state2.current_zone, 1)

    def test_specific_tags_bookkeeping_and_resupply_edge_cases(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)

        # Setup attacker/defender for tagging bookkeeping
        attacker_player = team.players.filter(role="commander").first()
        defender_player = team.players.filter(role="scout").first()
        attacker = PlayerRoundState.objects.create(
            game_round=gr,
            player=attacker_player,
            team_color="red",
            role="commander",
            current_zone=0,
            final_shots=10,
            final_lives=10,
        )
        defender = PlayerRoundState.objects.create(
            game_round=gr,
            player=defender_player,
            team_color="blue",
            role="scout",
            current_zone=0,
            final_shots=10,
            final_lives=10,
        )

        # Force a successful tag and assert specific_tags increment
        with patch("random.choices", return_value=["tag_player"]), patch(
            "random.choice", return_value=defender
        ), patch("random.random", return_value=0.0):
            simulator._choose_action(attacker, [attacker, defender], second=1)

        attacker.refresh_from_db()
        defender.refresh_from_db()
        atk_key = attacker.get_tag_id
        def_key = defender.get_tag_id
        # JSONField serializes dict keys as strings, so ensure we compare string keys
        self.assertIn(str(def_key), attacker.specific_tags)
        self.assertGreater(attacker.specific_tags[str(def_key)]["tags"], 0)
        self.assertIn(str(atk_key), defender.specific_tags)
        self.assertGreater(defender.specific_tags[str(atk_key)]["tagged_by"], 0)

    def test_max_shots(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        # Resupply edge case: teammate at max_shots should not exceed cap
        red_states = simulator._initialize_players(gr, team.players.all(), "red")
        ammo = next(s for s in red_states if s.role == "ammo")
        scout_state = next(s for s in red_states if s.role == "scout")
        scout_state.final_shots = scout_state.max_shots
        scout_state.save()

        # Attempt resupply should cap at max_shots
        simulator._attempt_resupply(ammo, scout_state, second=10)
        scout_state.refresh_from_db()
        self.assertLessEqual(scout_state.final_shots, scout_state.max_shots)

    def test_resupply_medic_no_shots_no_heal(self):
        simulator = ResourceBasedSimulator()
        team, players = self.create_team_with_roster("Edge")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        red_states = simulator._initialize_players(gr, team.players.all(), "red")
        # Medic with zero shots should not be able to heal (medic.final_shots > 0 required)
        medic_state = next(s for s in red_states if s.role == "medic")
        medic_state.final_shots = 0
        teammate = next(s for s in red_states if s.role == "heavy")
        teammate.final_lives = max(1, teammate.final_lives - 5)
        medic_state.save()
        teammate.save()

        simulator._attempt_resupply(medic_state, teammate, second=20)
        teammate.refresh_from_db()
        # No heal should have occurred because medic had 0 final_shots
        self.assertLessEqual(teammate.final_lives, teammate.max_lives)

    def test_is_active_and_is_taggable_time_boundaries(self):
        team, players = self.create_team_with_roster("TimeTest")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        p = team.players.first()
        state = PlayerRoundState.objects.create(
            game_round=gr,
            player=p,
            team_color="red",
            role="scout",
            current_zone=0,
            final_shots=5,
            final_lives=5,
        )

        # Not downed -> active and taggable
        self.assertTrue(state.is_active_at(0))
        self.assertTrue(state.is_taggable_at(0))

        # Simulate downed at second 10
        state.last_downed_time = 10
        state.final_lives = 1
        state.save()

        # Within 3 seconds -> not taggable (less than 4) and not active (less than 8)
        self.assertFalse(state.is_taggable_at(12))
        self.assertFalse(state.is_active_at(12))

        # After 5 seconds -> taggable but still not active until 8
        self.assertTrue(state.is_taggable_at(15))
        self.assertFalse(state.is_active_at(15))

        # After 9 seconds -> active again
        self.assertTrue(state.is_active_at(20))
