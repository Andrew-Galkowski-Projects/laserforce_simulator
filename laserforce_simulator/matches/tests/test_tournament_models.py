"""LG-02a — Django ``TestCase`` tests for the Tournament models.

Seam contract locked at ``.claude/worktrees/lg-02a-seam-contract.md`` (§1).
Models ``Tournament`` / ``TournamentParticipant`` / ``BracketNode`` live in
``matches/models.py``; the state machine lives on ``Tournament``
(``lock_and_build`` / ``is_locked`` / ``find_next_playable_node``).

Uses ``make_team_with_slots`` for real fully-slotted Teams. These assertions
WILL fail until the Code agent lands the three models + the migration; that
is expected for the parallel build.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from matches.models import BracketNode, Tournament, TournamentParticipant
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teams(n: int) -> list:
    return [make_team_with_slots(f"T{i}")[0] for i in range(n)]


def _tournament_with_participants(n: int, *, name: str = "Cup") -> Tournament:
    """A setup-state Tournament with ``n`` seeded participants (seed 1..n)."""
    tournament = Tournament.objects.create(name=name)
    teams = _make_teams(n)
    for seed, team in enumerate(teams, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=seed
        )
    return tournament


# ---------------------------------------------------------------------------
# TestTournamentModel — defaults / is_locked / __str__
# ---------------------------------------------------------------------------


class TestTournamentModel(TestCase):
    """Locked field defaults + is_locked property + __str__."""

    def test_format_defaults_to_single_elimination(self) -> None:
        t = Tournament.objects.create(name="Cup")
        self.assertEqual(t.format, "single_elimination")

    def test_state_defaults_to_setup(self) -> None:
        t = Tournament.objects.create(name="Cup")
        self.assertEqual(t.state, "setup")

    def test_champion_defaults_to_none(self) -> None:
        t = Tournament.objects.create(name="Cup")
        self.assertIsNone(t.champion)

    def test_str_returns_name(self) -> None:
        t = Tournament.objects.create(name="Spring Cup")
        self.assertEqual(str(t), "Spring Cup")

    def test_is_locked_false_in_setup(self) -> None:
        t = Tournament.objects.create(name="Cup")
        self.assertFalse(t.is_locked)

    def test_is_locked_true_when_active(self) -> None:
        t = Tournament.objects.create(name="Cup", state="active")
        self.assertTrue(t.is_locked)

    def test_is_locked_true_when_completed(self) -> None:
        t = Tournament.objects.create(name="Cup", state="completed")
        self.assertTrue(t.is_locked)


# ---------------------------------------------------------------------------
# TestTournamentParticipantConstraints
# ---------------------------------------------------------------------------


class TestTournamentParticipantConstraints(TestCase):
    """uniq_tournament_seed + uniq_tournament_team."""

    def test_str_format(self) -> None:
        t = Tournament.objects.create(name="Cup")
        team = make_team_with_slots("Solo")[0]
        p = TournamentParticipant.objects.create(tournament=t, team=team, seed=1)
        self.assertEqual(str(p), f"Cup #1 {team.name}")

    def test_duplicate_seed_within_tournament_rejected(self) -> None:
        t = Tournament.objects.create(name="Cup")
        t1 = make_team_with_slots("A")[0]
        t2 = make_team_with_slots("B")[0]
        TournamentParticipant.objects.create(tournament=t, team=t1, seed=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TournamentParticipant.objects.create(tournament=t, team=t2, seed=1)

    def test_duplicate_team_within_tournament_rejected(self) -> None:
        t = Tournament.objects.create(name="Cup")
        team = make_team_with_slots("A")[0]
        TournamentParticipant.objects.create(tournament=t, team=team, seed=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TournamentParticipant.objects.create(tournament=t, team=team, seed=2)

    def test_same_seed_allowed_across_different_tournaments(self) -> None:
        t1 = Tournament.objects.create(name="Cup1")
        t2 = Tournament.objects.create(name="Cup2")
        team_a = make_team_with_slots("A")[0]
        team_b = make_team_with_slots("B")[0]
        TournamentParticipant.objects.create(tournament=t1, team=team_a, seed=1)
        # No collision — different Tournament.
        TournamentParticipant.objects.create(tournament=t2, team=team_b, seed=1)
        self.assertEqual(TournamentParticipant.objects.filter(seed=1).count(), 2)

    def test_participants_ordered_by_seed(self) -> None:
        t = _tournament_with_participants(4)
        seeds = list(t.participants.values_list("seed", flat=True))
        self.assertEqual(seeds, [1, 2, 3, 4])

    def test_related_name_participants(self) -> None:
        t = _tournament_with_participants(4)
        self.assertEqual(t.participants.count(), 4)


# ---------------------------------------------------------------------------
# TestBracketNodeConstraints
# ---------------------------------------------------------------------------


class TestBracketNodeConstraints(TestCase):
    """uniq_tournament_round_position + __str__ + related names."""

    def test_str_format(self) -> None:
        t = Tournament.objects.create(name="Cup")
        node = BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        self.assertEqual(str(node), "Cup R1/0")

    def test_duplicate_round_position_rejected(self) -> None:
        t = Tournament.objects.create(name="Cup")
        BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BracketNode.objects.create(tournament=t, bracket_round=1, position=0)

    def test_same_position_allowed_across_tournaments(self) -> None:
        t1 = Tournament.objects.create(name="Cup1")
        t2 = Tournament.objects.create(name="Cup2")
        BracketNode.objects.create(tournament=t1, bracket_round=1, position=0)
        BracketNode.objects.create(tournament=t2, bracket_round=1, position=0)
        self.assertEqual(
            BracketNode.objects.filter(bracket_round=1, position=0).count(), 2
        )

    def test_nodes_ordered_by_round_then_position(self) -> None:
        t = Tournament.objects.create(name="Cup")
        BracketNode.objects.create(tournament=t, bracket_round=2, position=0)
        BracketNode.objects.create(tournament=t, bracket_round=1, position=1)
        BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        keys = [(n.bracket_round, n.position) for n in t.nodes.all()]
        self.assertEqual(keys, [(1, 0), (1, 1), (2, 0)])

    def test_advances_to_self_fk_and_feeders_related_name(self) -> None:
        t = Tournament.objects.create(name="Cup")
        parent = BracketNode.objects.create(tournament=t, bracket_round=2, position=0)
        child = BracketNode.objects.create(
            tournament=t,
            bracket_round=1,
            position=0,
            advances_to=parent,
            advances_to_slot="a",
        )
        self.assertIn(child, parent.feeders.all())

    def test_is_bye_defaults_false(self) -> None:
        t = Tournament.objects.create(name="Cup")
        node = BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        self.assertFalse(node.is_bye)


# ---------------------------------------------------------------------------
# TestTournamentLockAndBuild
# ---------------------------------------------------------------------------


class TestTournamentLockAndBuild(TestCase):
    """setup -> active, node persistence, byes pre-resolved, error paths."""

    def test_lock_and_build_flips_state_to_active(self) -> None:
        t = _tournament_with_participants(4)
        t.lock_and_build()
        t.refresh_from_db()
        self.assertEqual(t.state, "active")

    def test_lock_and_build_persists_nodes(self) -> None:
        t = _tournament_with_participants(4)
        t.lock_and_build()
        # 4-team single-elimination ⇒ 3 nodes.
        self.assertEqual(t.nodes.count(), 3)

    def test_lock_and_build_n8_persists_seven_nodes(self) -> None:
        t = _tournament_with_participants(8)
        t.lock_and_build()
        self.assertEqual(t.nodes.count(), 7)

    def test_lock_and_build_raises_validation_error_below_four(self) -> None:
        t = _tournament_with_participants(3)
        with self.assertRaises(ValidationError):
            t.lock_and_build()

    def test_lock_and_build_below_four_does_not_flip_state(self) -> None:
        t = _tournament_with_participants(3)
        with self.assertRaises(ValidationError):
            t.lock_and_build()
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.nodes.count(), 0)

    def test_lock_and_build_rejected_when_already_locked(self) -> None:
        t = _tournament_with_participants(4)
        t.lock_and_build()
        with self.assertRaises(ValidationError):
            t.lock_and_build()

    def test_byes_preresolved_on_non_power_of_two(self) -> None:
        t = _tournament_with_participants(5)
        t.lock_and_build()
        byes = t.nodes.filter(is_bye=True)
        # Next power of two >= 5 is 8 ⇒ 3 byes.
        self.assertEqual(byes.count(), 3)
        for node in byes:
            self.assertIsNotNone(node.winner)

    def test_round1_nodes_carry_seed_and_team(self) -> None:
        t = _tournament_with_participants(4)
        t.lock_and_build()
        r1 = t.nodes.filter(bracket_round=1)
        for node in r1:
            self.assertIsNotNone(node.team_a)
            self.assertIsNotNone(node.team_b)
            self.assertIsNotNone(node.seed_a)
            self.assertIsNotNone(node.seed_b)


# ---------------------------------------------------------------------------
# TestFindNextPlayableNode
# ---------------------------------------------------------------------------


class TestFindNextPlayableNode(TestCase):
    """Delegates to bracket.find_next_node over this Tournament's nodes."""

    def test_returns_a_playable_round1_node_after_build(self) -> None:
        t = _tournament_with_participants(4)
        t.lock_and_build()
        node = t.find_next_playable_node()
        self.assertIsNotNone(node)
        self.assertIsInstance(node, BracketNode)
        self.assertEqual(node.bracket_round, 1)
        self.assertFalse(node.is_bye)

    def test_returns_lowest_position_first(self) -> None:
        t = _tournament_with_participants(4)
        t.lock_and_build()
        node = t.find_next_playable_node()
        # Lowest (bracket_round, position) playable.
        self.assertEqual((node.bracket_round, node.position), (1, 0))

    def test_returns_none_in_setup_state_with_no_nodes(self) -> None:
        t = _tournament_with_participants(4)
        self.assertIsNone(t.find_next_playable_node())

    def test_skips_bye_nodes(self) -> None:
        t = _tournament_with_participants(5)
        t.lock_and_build()
        node = t.find_next_playable_node()
        # The only round-1 playable node is the 4v5 game; byes are skipped.
        self.assertIsNotNone(node)
        self.assertFalse(node.is_bye)


# ---------------------------------------------------------------------------
# TestTournamentChampionStamping (shape only — full flow in views tests)
# ---------------------------------------------------------------------------


class TestTournamentChampionStamping(TestCase):
    """The champion FK accepts a Team and survives a Team delete (SET_NULL)."""

    def test_champion_can_be_set_to_a_team(self) -> None:
        t = Tournament.objects.create(name="Cup")
        team = make_team_with_slots("Winner")[0]
        t.champion = team
        t.state = "completed"
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.champion_id, team.id)

    def test_champion_set_null_on_team_delete(self) -> None:
        t = Tournament.objects.create(name="Cup")
        team = make_team_with_slots("Winner")[0]
        t.champion = team
        t.save()
        team.delete()
        t.refresh_from_db()
        # SET_NULL — deleting the Team must not cascade-delete the Tournament.
        self.assertIsNone(t.champion)
        self.assertTrue(Tournament.objects.filter(pk=t.pk).exists())

    def test_tournaments_won_reverse_accessor(self) -> None:
        t = Tournament.objects.create(name="Cup")
        team = make_team_with_slots("Winner")[0]
        t.champion = team
        t.save()
        self.assertIn(t, team.tournaments_won.all())
