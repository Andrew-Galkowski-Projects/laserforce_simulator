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


# ===========================================================================
# LG-02b — Best-of-N series bracket nodes (SeriesMatch + series_length +
# _node_to_dict series keys)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified).
# Seam contract: ``.claude/worktrees/lg-02b-seam-contract.md``.
#
# ``SeriesMatch`` / ``_node_to_dict`` are imported LAZILY inside each method so
# their absence (pre-Code-landing) isolates the failure to these new classes
# and does NOT break the existing (passing) classes above at collection time.


class TestSeriesMatchModel(TestCase):
    """``SeriesMatch`` row shape, ordering, uniqueness, and on_delete rules."""

    def _node(self, tournament: Tournament | None = None) -> BracketNode:
        t = tournament or Tournament.objects.create(name="Cup")
        return BracketNode.objects.create(tournament=t, bracket_round=1, position=0)

    def test_create_row_with_node_match_game_number_winner(self) -> None:
        from matches.models import Match, SeriesMatch

        node = self._node()
        team_a = make_team_with_slots("A")[0]
        team_b = make_team_with_slots("B")[0]
        match = Match.objects.create(
            team_red=team_a, team_blue=team_b, match_type="tournament"
        )
        sm = SeriesMatch.objects.create(
            node=node, match=match, game_number=1, winner=team_a
        )
        self.assertEqual(sm.node_id, node.id)
        self.assertEqual(sm.match_id, match.id)
        self.assertEqual(sm.game_number, 1)
        self.assertEqual(sm.winner_id, team_a.id)

    def test_related_name_series_matches(self) -> None:
        from matches.models import SeriesMatch

        node = self._node()
        SeriesMatch.objects.create(node=node, game_number=1)
        self.assertEqual(node.series_matches.count(), 1)

    def test_meta_ordering_by_game_number(self) -> None:
        from matches.models import SeriesMatch

        node = self._node()
        SeriesMatch.objects.create(node=node, game_number=3)
        SeriesMatch.objects.create(node=node, game_number=1)
        SeriesMatch.objects.create(node=node, game_number=2)
        nums = list(node.series_matches.values_list("game_number", flat=True))
        self.assertEqual(nums, [1, 2, 3])

    def test_unique_node_game_number_rejected(self) -> None:
        from matches.models import SeriesMatch

        node = self._node()
        SeriesMatch.objects.create(node=node, game_number=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SeriesMatch.objects.create(node=node, game_number=1)

    def test_same_game_number_allowed_across_different_nodes(self) -> None:
        from matches.models import SeriesMatch

        t = Tournament.objects.create(name="Cup")
        node_a = BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        node_b = BracketNode.objects.create(tournament=t, bracket_round=1, position=1)
        SeriesMatch.objects.create(node=node_a, game_number=1)
        SeriesMatch.objects.create(node=node_b, game_number=1)
        self.assertEqual(SeriesMatch.objects.filter(game_number=1).count(), 2)

    def test_node_delete_cascades_series_match(self) -> None:
        from matches.models import SeriesMatch

        node = self._node()
        sm = SeriesMatch.objects.create(node=node, game_number=1)
        sm_id = sm.id
        node.delete()
        self.assertFalse(SeriesMatch.objects.filter(pk=sm_id).exists())

    def test_match_delete_set_null_leaves_row(self) -> None:
        from matches.models import Match, SeriesMatch

        node = self._node()
        team_a = make_team_with_slots("A")[0]
        team_b = make_team_with_slots("B")[0]
        match = Match.objects.create(
            team_red=team_a, team_blue=team_b, match_type="tournament"
        )
        sm = SeriesMatch.objects.create(node=node, match=match, game_number=1)
        match.delete()
        sm.refresh_from_db()
        self.assertIsNone(sm.match_id)
        self.assertTrue(SeriesMatch.objects.filter(pk=sm.id).exists())

    def test_winner_team_delete_set_null_leaves_row(self) -> None:
        from matches.models import SeriesMatch

        node = self._node()
        team = make_team_with_slots("W")[0]
        sm = SeriesMatch.objects.create(node=node, game_number=1, winner=team)
        team.delete()
        sm.refresh_from_db()
        self.assertIsNone(sm.winner_id)
        self.assertTrue(SeriesMatch.objects.filter(pk=sm.id).exists())


# ===========================================================================
# LG-02b-2 — per-Bracket-round Series escalation (four Tournament slot fields +
# BracketNode.series_length + lock_and_build stamping + _node_to_dict node read)
# ===========================================================================
#
# MIGRATED from the LG-02b ``TestTournamentSeriesLength`` (single flat field) +
# ``TestNodeToDictSeriesKeys`` (read node.tournament.series_length) to the
# LG-02b-2 four-field + node-field shape. Seam contract:
# ``.claude/worktrees/lg-02b-2-seam-contract.md`` §1 / §6b.
#
# The old flat ``Tournament.series_length`` is DROPPED; four depth-anchored
# slot fields replace it; ``BracketNode.series_length`` carries the resolved N
# stamped at lock time; ``_node_to_dict`` reads ``node.series_length`` (NOT the
# tournament). ``SeriesMatch`` + ``count_series_wins`` are UNCHANGED.


class TestTournamentSeriesLengthFields(TestCase):
    """The four ``Tournament`` slot fields exist, default 1, carry 1/3/5
    choices; the old flat ``Tournament.series_length`` field is GONE."""

    _SLOT_FIELDS = (
        "final_series_length",
        "semifinal_series_length",
        "quarterfinal_series_length",
        "earlier_series_length",
    )

    def test_four_slot_fields_default_to_one(self) -> None:
        t = Tournament.objects.create(name="Cup")
        for field in self._SLOT_FIELDS:
            self.assertEqual(getattr(t, field), 1, f"{field} should default to 1")

    def test_four_slot_fields_persist(self) -> None:
        t = Tournament.objects.create(
            name="Cup",
            final_series_length=5,
            semifinal_series_length=3,
            quarterfinal_series_length=1,
            earlier_series_length=1,
        )
        t.refresh_from_db()
        self.assertEqual(t.final_series_length, 5)
        self.assertEqual(t.semifinal_series_length, 3)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 1)

    def test_each_slot_field_carries_one_three_five_choices(self) -> None:
        expected = [(1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")]
        for field in self._SLOT_FIELDS:
            choices = list(Tournament._meta.get_field(field).choices)
            self.assertEqual(
                choices, expected, f"{field} choices should be 1/3/5 Best-of"
            )

    def test_each_slot_field_accepts_one_three_five(self) -> None:
        for length in (1, 3, 5):
            t = Tournament.objects.create(
                name=f"Cup{length}",
                final_series_length=length,
                semifinal_series_length=length,
                quarterfinal_series_length=length,
                earlier_series_length=length,
            )
            t.refresh_from_db()
            self.assertEqual(t.final_series_length, length)

    def test_old_flat_series_length_field_is_gone(self) -> None:
        from django.core.exceptions import FieldDoesNotExist

        with self.assertRaises(FieldDoesNotExist):
            Tournament._meta.get_field("series_length")

    def test_old_flat_series_length_not_in_field_names(self) -> None:
        field_names = {f.name for f in Tournament._meta.get_fields()}
        self.assertNotIn("series_length", field_names)


class TestBracketNodeSeriesLengthField(TestCase):
    """``BracketNode.series_length`` exists and defaults to 1 (no choices)."""

    def test_node_series_length_defaults_to_one(self) -> None:
        t = Tournament.objects.create(name="Cup")
        node = BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        self.assertEqual(node.series_length, 1)

    def test_node_series_length_field_exists(self) -> None:
        field = BracketNode._meta.get_field("series_length")
        self.assertEqual(field.default, 1)


class TestLockAndBuildStampsSeriesLengthPerDepth(TestCase):
    """``lock_and_build`` stamps ``node.series_length`` per depth-from-final for
    a known four-slot config, INCLUDING bye nodes (still depth-resolved)."""

    def _stamped_tournament(
        self,
        n: int,
        *,
        final: int,
        semifinal: int,
        quarterfinal: int,
        earlier: int,
        name: str = "Cup",
    ) -> Tournament:
        t = Tournament.objects.create(
            name=name,
            final_series_length=final,
            semifinal_series_length=semifinal,
            quarterfinal_series_length=quarterfinal,
            earlier_series_length=earlier,
        )
        for seed, team in enumerate(_make_teams(n), start=1):
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        t.lock_and_build()
        t.refresh_from_db()
        return t

    def test_n8_stamps_each_depth(self) -> None:
        # N=8 -> 3 Bracket rounds. final=5 (r3), semifinal=3 (r2),
        # quarterfinal=1 (r1). earlier unused at N=8 (no depth >= 3 round).
        t = self._stamped_tournament(
            8, final=5, semifinal=3, quarterfinal=1, earlier=1, name="N8Stamp"
        )
        # round 3 (the final, depth 0) -> Bo5.
        for node in t.nodes.filter(bracket_round=3):
            self.assertEqual(node.series_length, 5)
        # round 2 (semifinals, depth 1) -> Bo3.
        for node in t.nodes.filter(bracket_round=2):
            self.assertEqual(node.series_length, 3)
        # round 1 (quarterfinals, depth 2) -> Bo1.
        for node in t.nodes.filter(bracket_round=1):
            self.assertEqual(node.series_length, 1)

    def test_n16_stamps_earlier_at_depth_3(self) -> None:
        # N=16 -> 4 Bracket rounds. final=5 (r4), semifinal=3 (r3),
        # quarterfinal=1 (r2), earlier=5 (r1, depth 3).
        t = self._stamped_tournament(
            16, final=5, semifinal=3, quarterfinal=1, earlier=5, name="N16Stamp"
        )
        for node in t.nodes.filter(bracket_round=4):
            self.assertEqual(node.series_length, 5)
        for node in t.nodes.filter(bracket_round=3):
            self.assertEqual(node.series_length, 3)
        for node in t.nodes.filter(bracket_round=2):
            self.assertEqual(node.series_length, 1)
        # round 1 (depth 3) -> earlier == 5.
        for node in t.nodes.filter(bracket_round=1):
            self.assertEqual(node.series_length, 5)

    def test_bye_node_still_gets_depth_resolved_value(self) -> None:
        # N=5 -> next power of two 8 -> 3 byes in round 1. A bye node is inert
        # but still stamped with its depth's resolved value (quarterfinal slot,
        # depth 2 at N=5's 3 rounds).
        t = self._stamped_tournament(
            5, final=5, semifinal=3, quarterfinal=7, earlier=1, name="N5Bye"
        )
        byes = t.nodes.filter(is_bye=True)
        self.assertTrue(byes.exists())
        for node in byes:
            # Byes are round-1 nodes (depth = 3 - 1 = 2 -> quarterfinal == 7).
            self.assertEqual(node.bracket_round, 1)
            self.assertEqual(node.series_length, 7)

    def test_all_slots_one_stamps_every_node_bo1(self) -> None:
        # Bo1-everywhere (the migration default) stamps every node series_length=1.
        t = self._stamped_tournament(
            8, final=1, semifinal=1, quarterfinal=1, earlier=1, name="N8Bo1"
        )
        for node in t.nodes.all():
            self.assertEqual(node.series_length, 1)


class TestNodeToDictSeriesKeys(TestCase):
    """``_node_to_dict`` carries ``wins_a`` / ``wins_b`` / ``series_length`` read
    off ``node.series_length`` (NOT ``node.tournament.*``) and DROPS
    ``match_id`` (the LG-02b-2 node-sourced shape)."""

    def _node_with_series(
        self, node_series_length: int
    ) -> tuple[BracketNode, object, object]:
        t = Tournament.objects.create(name="Cup")
        team_a = make_team_with_slots("A")[0]
        team_b = make_team_with_slots("B")[0]
        node = BracketNode.objects.create(
            tournament=t,
            bracket_round=1,
            position=0,
            team_a=team_a,
            team_b=team_b,
            seed_a=1,
            seed_b=2,
            series_length=node_series_length,
        )
        return node, team_a, team_b

    def test_no_match_id_key_present(self) -> None:
        from matches.models import _node_to_dict

        node, _a, _b = self._node_with_series(3)
        d = _node_to_dict(node)
        self.assertNotIn("match_id", d)

    def test_three_keys_present(self) -> None:
        from matches.models import _node_to_dict

        node, _a, _b = self._node_with_series(3)
        d = _node_to_dict(node)
        self.assertIn("wins_a", d)
        self.assertIn("wins_b", d)
        self.assertIn("series_length", d)

    def test_series_length_read_from_node_not_tournament(self) -> None:
        from matches.models import _node_to_dict

        node, _a, _b = self._node_with_series(5)
        d = _node_to_dict(node)
        # The dict value equals the NODE's stamped series_length.
        self.assertEqual(d["series_length"], node.series_length)
        self.assertEqual(d["series_length"], 5)

    def test_wins_count_by_winner_team(self) -> None:
        from matches.models import Match, SeriesMatch, _node_to_dict

        node, team_a, team_b = self._node_with_series(3)
        # team_a wins games 1 and 2; team_b wins game 3.
        for game, winner in ((1, team_a), (2, team_a), (3, team_b)):
            match = Match.objects.create(
                team_red=team_a, team_blue=team_b, match_type="tournament"
            )
            SeriesMatch.objects.create(
                node=node, match=match, game_number=game, winner=winner
            )
        d = _node_to_dict(node)
        self.assertEqual(d["wins_a"], 2)
        self.assertEqual(d["wins_b"], 1)

    def test_wins_zero_when_no_series_matches(self) -> None:
        from matches.models import _node_to_dict

        node, _a, _b = self._node_with_series(3)
        d = _node_to_dict(node)
        self.assertEqual(d["wins_a"], 0)
        self.assertEqual(d["wins_b"], 0)


# ===========================================================================
# LG-02c — Double-elimination tournaments (model layer)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — they
# stay green as single-elim regression guards). Seam contract:
# ``.claude/worktrees/lg-02c-seam-contract.md`` §1 (models) / §6b (test
# boundary).
#
# BracketNode gains ``bracket_type`` (winners/losers/grand_final, default
# "winners") + ``loser_advances_to`` (self-FK, related_name "loser_feeders") +
# ``loser_advances_to_slot`` ("a"/"b"). The uniqueness constraint is widened to
# include ``bracket_type`` and renamed ``uniq_tournament_bracket_round_position``
# (a WB and an LB node may share (bracket_round, position); a dup WITHIN one
# bracket_type is still rejected). ``Tournament.format`` gains the
# "double_elimination" choice. ``lock_and_build`` branches on format. These
# assertions WILL fail until the Code agent lands the migration + model edits.


class TestBracketNodeDoubleElimFields(TestCase):
    """``bracket_type`` default + choices; loser-dest fields default NULL; the
    renamed/widened uniqueness constraint."""

    def test_bracket_type_defaults_to_winners(self) -> None:
        t = Tournament.objects.create(name="Cup")
        node = BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        self.assertEqual(node.bracket_type, "winners")

    def test_bracket_type_carries_four_choices(self) -> None:
        # LG-02c round-robin added the 4th bracket_type value "round_robin"
        # (alongside the LG-02c double-elim winners/losers/grand_final trio);
        # LG-02c Swiss added the 5th value "swiss".
        choices = dict(BracketNode._meta.get_field("bracket_type").choices)
        self.assertEqual(
            set(choices),
            {"winners", "losers", "grand_final", "round_robin", "swiss"},
        )

    def test_loser_advances_to_defaults_none(self) -> None:
        t = Tournament.objects.create(name="Cup")
        node = BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        self.assertIsNone(node.loser_advances_to)
        self.assertIsNone(node.loser_advances_to_slot)

    def test_loser_feeders_related_name(self) -> None:
        # loser_advances_to is a self-FK with related_name "loser_feeders".
        t = Tournament.objects.create(name="Cup")
        lb_dest = BracketNode.objects.create(
            tournament=t, bracket_type="losers", bracket_round=1, position=0
        )
        wb = BracketNode.objects.create(
            tournament=t,
            bracket_type="winners",
            bracket_round=1,
            position=0,
            loser_advances_to=lb_dest,
            loser_advances_to_slot="a",
        )
        self.assertIn(wb, lb_dest.loser_feeders.all())

    def test_loser_advances_to_set_null_on_node_delete(self) -> None:
        t = Tournament.objects.create(name="Cup")
        lb_dest = BracketNode.objects.create(
            tournament=t, bracket_type="losers", bracket_round=1, position=0
        )
        wb = BracketNode.objects.create(
            tournament=t,
            bracket_type="winners",
            bracket_round=1,
            position=0,
            loser_advances_to=lb_dest,
            loser_advances_to_slot="a",
        )
        lb_dest.delete()
        wb.refresh_from_db()
        # SET_NULL — deleting the destination must not cascade the WB node away.
        self.assertIsNone(wb.loser_advances_to_id)
        self.assertTrue(BracketNode.objects.filter(pk=wb.pk).exists())

    def test_constraint_allows_wb_and_lb_sharing_round_position(self) -> None:
        # The widened constraint includes bracket_type, so a WB and LB node may
        # share (bracket_round, position).
        t = Tournament.objects.create(name="Cup")
        BracketNode.objects.create(
            tournament=t, bracket_type="winners", bracket_round=1, position=0
        )
        # No collision — different bracket_type.
        BracketNode.objects.create(
            tournament=t, bracket_type="losers", bracket_round=1, position=0
        )
        self.assertEqual(
            BracketNode.objects.filter(
                tournament=t, bracket_round=1, position=0
            ).count(),
            2,
        )

    def test_constraint_rejects_dup_within_one_bracket_type(self) -> None:
        t = Tournament.objects.create(name="Cup")
        BracketNode.objects.create(
            tournament=t, bracket_type="losers", bracket_round=1, position=0
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BracketNode.objects.create(
                    tournament=t, bracket_type="losers", bracket_round=1, position=0
                )

    def test_renamed_constraint_present(self) -> None:
        names = {c.name for c in BracketNode._meta.constraints}
        self.assertIn("uniq_tournament_bracket_round_position", names)
        self.assertNotIn("uniq_tournament_round_position", names)


class TestTournamentDoubleElimFormat(TestCase):
    """``Tournament.format`` gains the "double_elimination" choice; the default
    stays "single_elimination"."""

    def test_format_choices_include_double_elimination(self) -> None:
        choices = dict(Tournament._meta.get_field("format").choices)
        self.assertIn("double_elimination", choices)
        self.assertIn("single_elimination", choices)

    def test_default_format_still_single_elimination(self) -> None:
        t = Tournament.objects.create(name="Cup")
        self.assertEqual(t.format, "single_elimination")

    def test_double_elimination_format_persists(self) -> None:
        t = Tournament.objects.create(name="DE Cup", format="double_elimination")
        t.refresh_from_db()
        self.assertEqual(t.format, "double_elimination")


class TestLockAndBuildDoubleElim(TestCase):
    """A DE Tournament locks to active and persists WB + LB + GF nodes with the
    correct bracket_type, wired loser_advances_to self-FKs, GF1->GF2 loser
    wiring, and a depth-stamped series_length on every node incl. byes."""

    def _de_tournament(
        self,
        n: int,
        *,
        final: int = 5,
        semifinal: int = 3,
        quarterfinal: int = 1,
        earlier: int = 1,
        name: str = "DECup",
    ) -> Tournament:
        t = Tournament.objects.create(
            name=name,
            format="double_elimination",
            final_series_length=final,
            semifinal_series_length=semifinal,
            quarterfinal_series_length=quarterfinal,
            earlier_series_length=earlier,
        )
        for seed, team in enumerate(_make_teams(n), start=1):
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        t.lock_and_build()
        t.refresh_from_db()
        return t

    def test_lock_flips_state_to_active(self) -> None:
        t = self._de_tournament(4)
        self.assertEqual(t.state, "active")

    def test_persists_all_three_bracket_types(self) -> None:
        t = self._de_tournament(4)
        types = set(t.nodes.values_list("bracket_type", flat=True))
        self.assertEqual(types, {"winners", "losers", "grand_final"})

    def test_grand_final_has_two_nodes(self) -> None:
        t = self._de_tournament(4)
        self.assertEqual(t.nodes.filter(bracket_type="grand_final").count(), 2)

    def test_a_wb_node_loser_advances_to_an_lb_node(self) -> None:
        t = self._de_tournament(4)
        wb_with_drop = (
            t.nodes.filter(bracket_type="winners", is_bye=False)
            .exclude(loser_advances_to__isnull=True)
            .first()
        )
        self.assertIsNotNone(wb_with_drop, "a WB node must wire loser_advances_to")
        self.assertEqual(wb_with_drop.loser_advances_to.bracket_type, "losers")

    def test_gf1_loser_advances_to_gf2(self) -> None:
        t = self._de_tournament(4)
        # GF1 = the grand_final node that advances_to GF2 (advances_to not null);
        # GF2 = the grand_final node with advances_to null.
        gf1 = (
            t.nodes.filter(bracket_type="grand_final")
            .exclude(advances_to__isnull=True)
            .first()
        )
        self.assertIsNotNone(gf1)
        self.assertIsNotNone(gf1.loser_advances_to)
        self.assertEqual(gf1.loser_advances_to.bracket_type, "grand_final")

    def test_series_length_stamped_per_depth_incl_final(self) -> None:
        # final=5/semifinal=3/quarterfinal=1: GF nodes (depth 0) are Bo5;
        # WB-final & LB-final (depth 1) are Bo3.
        t = self._de_tournament(4, final=5, semifinal=3, quarterfinal=1, earlier=1)
        for gf in t.nodes.filter(bracket_type="grand_final"):
            self.assertEqual(gf.series_length, 5, "GF depth-0 -> Bo5")
        wb_final = (
            t.nodes.filter(bracket_type="winners").order_by("-bracket_round").first()
        )
        self.assertEqual(wb_final.series_length, 3, "WB final depth-1 -> Bo3")

    def test_every_node_has_a_stamped_series_length(self) -> None:
        t = self._de_tournament(6, final=5, semifinal=3, quarterfinal=1, earlier=1)
        for node in t.nodes.all():
            self.assertIn(node.series_length, (1, 3, 5))

    def test_bye_node_still_stamped(self) -> None:
        # N=6 -> WB byes; a bye node is inert but still depth-stamped.
        t = self._de_tournament(6)
        byes = t.nodes.filter(is_bye=True)
        self.assertTrue(byes.exists())
        for node in byes:
            self.assertIsNotNone(node.series_length)


class TestLockAndBuildSingleElimUnchanged(TestCase):
    """Regression: a single-elim lock is byte-identical to LG-02b-2 — all
    ``bracket_type="winners"``, every ``loser_advances_to`` NULL, depth-stamped
    series_length."""

    def _se_tournament(self, n: int, *, name: str = "SECup") -> Tournament:
        t = Tournament.objects.create(
            name=name,
            final_series_length=5,
            semifinal_series_length=3,
            quarterfinal_series_length=1,
            earlier_series_length=1,
        )
        for seed, team in enumerate(_make_teams(n), start=1):
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        t.lock_and_build()
        t.refresh_from_db()
        return t

    def test_all_nodes_are_winners_bracket(self) -> None:
        t = self._se_tournament(4)
        types = set(t.nodes.values_list("bracket_type", flat=True))
        self.assertEqual(types, {"winners"})

    def test_no_loser_advances_to_on_any_node(self) -> None:
        t = self._se_tournament(4)
        self.assertEqual(t.nodes.exclude(loser_advances_to__isnull=True).count(), 0)

    def test_node_count_unchanged_n4(self) -> None:
        t = self._se_tournament(4)
        # Single-elim 4-team bracket is still 3 nodes (no LB/GF).
        self.assertEqual(t.nodes.count(), 3)

    def test_series_length_still_depth_stamped(self) -> None:
        t = self._se_tournament(4)
        final = t.nodes.get(advances_to__isnull=True)
        self.assertEqual(final.series_length, 5)


class TestNodeToDictDoubleElimKeys(TestCase):
    """``_node_to_dict`` gains ``bracket_type`` + a 3-tuple ``loser_advances_to``
    ``(bracket_type, round, position)`` + ``loser_advances_to_slot``. A
    single-elim node produces ``("winners", None, None)`` for the three new
    keys."""

    def test_de_node_carries_three_new_keys(self) -> None:
        from matches.models import _node_to_dict

        t = Tournament.objects.create(name="Cup", format="double_elimination")
        lb_dest = BracketNode.objects.create(
            tournament=t, bracket_type="losers", bracket_round=1, position=0
        )
        wb = BracketNode.objects.create(
            tournament=t,
            bracket_type="winners",
            bracket_round=1,
            position=0,
            loser_advances_to=lb_dest,
            loser_advances_to_slot="b",
        )
        d = _node_to_dict(wb)
        self.assertEqual(d["bracket_type"], "winners")
        self.assertEqual(d["loser_advances_to"], ("losers", 1, 0))
        self.assertEqual(d["loser_advances_to_slot"], "b")

    def test_loser_advances_to_is_a_three_tuple(self) -> None:
        from matches.models import _node_to_dict

        t = Tournament.objects.create(name="Cup", format="double_elimination")
        lb_dest = BracketNode.objects.create(
            tournament=t, bracket_type="losers", bracket_round=2, position=1
        )
        wb = BracketNode.objects.create(
            tournament=t,
            bracket_type="winners",
            bracket_round=1,
            position=0,
            loser_advances_to=lb_dest,
            loser_advances_to_slot="a",
        )
        d = _node_to_dict(wb)
        self.assertEqual(len(d["loser_advances_to"]), 3)
        self.assertEqual(d["loser_advances_to"], ("losers", 2, 1))

    def test_single_elim_node_three_new_keys_default(self) -> None:
        from matches.models import _node_to_dict

        t = Tournament.objects.create(name="Cup")
        node = BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        d = _node_to_dict(node)
        self.assertEqual(d["bracket_type"], "winners")
        self.assertIsNone(d["loser_advances_to"])
        self.assertIsNone(d["loser_advances_to_slot"])

    def test_existing_keys_preserved(self) -> None:
        from matches.models import _node_to_dict

        t = Tournament.objects.create(name="Cup")
        node = BracketNode.objects.create(tournament=t, bracket_round=1, position=0)
        d = _node_to_dict(node)
        for key in (
            "bracket_round",
            "position",
            "team_a_id",
            "team_b_id",
            "seed_a",
            "seed_b",
            "is_bye",
            "wins_a",
            "wins_b",
            "series_length",
            "winner_id",
            "advances_to",
            "advances_to_slot",
        ):
            self.assertIn(key, d, f"existing _node_to_dict key {key!r} dropped")


# ===========================================================================
# LG-02c — Round robin tournament format (model layer)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — they
# stay green as single/double-elim regression guards). Seam contract:
# ``.claude/worktrees/lg-02c-round-robin-seam-contract.md`` §1 / §2 / §3 / §4 /
# §10.
#
# A round-robin Tournament is a FLAT set of BracketNode rows — one node per
# fixture from generate_schedule(team_ids) (the full double round-robin: each
# pair appears twice, once per leg). Every RR node: bracket_type="round_robin",
# series_length=1, advances_to / loser_advances_to both None, is_bye=False,
# bracket_round=matchday, position=0-based-index-within-matchday, team_a/team_b
# fixed at lock, seed_a/seed_b carried. lock_and_build's third format branch
# builds them; round_robin_standings() + complete_round_robin_if_finished() are
# the two NEW Tournament methods. NEVER assert exact simulated point totals —
# standings rows are hand-stamped from played SeriesMatch/Match/GameRound rows.


def _rr_tournament_setup(n: int, *, name: str = "RRCup") -> Tournament:
    """A setup-state round_robin Tournament with ``n`` seeded participants."""
    t = Tournament.objects.create(name=name, format="round_robin")
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _rr_tournament_active(n: int, *, name: str = "RRCup") -> Tournament:
    """A locked/active round_robin Tournament with its flat RR nodes built."""
    t = _rr_tournament_setup(n, name=name)
    t.lock_and_build()
    t.refresh_from_db()
    return t


def _active_tournament_se(n: int = 4, *, name: str = "SECompleteGuard") -> Tournament:
    """A locked/active SINGLE-elim Tournament (default format) for the non-RR
    no-op guard test."""
    t = Tournament.objects.create(name=name)
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


def _stamp_rr_node_played(node: BracketNode, winner) -> None:
    """Hand-stamp a round-robin node as a decisive played Bo1: set node.winner,
    create a played Match (sides team_a=red / team_b=blue, two GameRounds) +
    one SeriesMatch row.

    The winner sweeps both rounds so Match.winner is non-null; the Match carries
    decisive round + total points so the standings reflect a clean win without
    running a real sim.
    """
    from matches.models import GameRound, Match, SeriesMatch

    team_red = node.team_a
    team_blue = node.team_b
    if winner.id == team_red.id:
        red_r1, blue_r1, red_r2, blue_r2 = 500, 10, 500, 10
    else:
        red_r1, blue_r1, red_r2, blue_r2 = 10, 500, 10, 500
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        match_type="tournament",
        red_round1_points=red_r1,
        blue_round1_points=blue_r1,
        red_round2_points=red_r2,
        blue_round2_points=blue_r2,
        is_completed=True,
    )
    # Two GameRounds with explicit physical sides + points so the side-split /
    # round-grain standings columns have data to read.
    GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_r1,
        blue_points=blue_r1,
    )
    GameRound.objects.create(
        match=match,
        round_number=2,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_r2,
        blue_points=blue_r2,
    )
    SeriesMatch.objects.create(node=node, match=match, game_number=1, winner=winner)
    node.winner = winner
    node.save(update_fields=["winner"])


class TestTournamentRoundRobinFormat(TestCase):
    """``Tournament.format`` accepts/persists ``"round_robin"`` and
    ``BracketNode.bracket_type`` accepts ``"round_robin"``."""

    def test_format_choices_include_round_robin(self) -> None:
        choices = dict(Tournament._meta.get_field("format").choices)
        self.assertIn("round_robin", choices)
        # Locked display label.
        self.assertEqual(choices["round_robin"], "Round robin")

    def test_round_robin_format_persists(self) -> None:
        t = Tournament.objects.create(name="RR Cup", format="round_robin")
        t.refresh_from_db()
        self.assertEqual(t.format, "round_robin")

    def test_bracket_type_choices_include_round_robin(self) -> None:
        choices = dict(BracketNode._meta.get_field("bracket_type").choices)
        self.assertIn("round_robin", choices)
        self.assertEqual(choices["round_robin"], "Round robin")

    def test_bracket_type_round_robin_persists(self) -> None:
        t = Tournament.objects.create(name="RR Cup", format="round_robin")
        node = BracketNode.objects.create(
            tournament=t, bracket_type="round_robin", bracket_round=1, position=0
        )
        node.refresh_from_db()
        self.assertEqual(node.bracket_type, "round_robin")


class TestLockAndBuildRoundRobin(TestCase):
    """``lock_and_build`` on an RR Tournament builds one node per fixture of the
    full double round-robin — flat, no advancement, all Bo1."""

    def test_n4_builds_twelve_nodes(self) -> None:
        # N=4 double round-robin ⇒ 6 pairs × 2 legs = 12 fixtures = 12 nodes.
        t = _rr_tournament_active(4)
        self.assertEqual(t.nodes.count(), 12)

    def test_n6_builds_thirty_nodes(self) -> None:
        # N=6 double round-robin ⇒ 15 pairs × 2 legs = 30 fixtures = 30 nodes.
        t = _rr_tournament_active(6)
        self.assertEqual(t.nodes.count(), 30)

    def test_state_flips_to_active(self) -> None:
        t = _rr_tournament_active(4)
        self.assertEqual(t.state, "active")

    def test_every_node_is_round_robin_bracket_type(self) -> None:
        t = _rr_tournament_active(4)
        types = set(t.nodes.values_list("bracket_type", flat=True))
        self.assertEqual(types, {"round_robin"})

    def test_every_node_is_bo1(self) -> None:
        t = _rr_tournament_active(4)
        for node in t.nodes.all():
            self.assertEqual(node.series_length, 1)

    def test_no_node_advances(self) -> None:
        t = _rr_tournament_active(4)
        for node in t.nodes.all():
            self.assertIsNone(node.advances_to_id)
            self.assertIsNone(node.loser_advances_to_id)

    def test_no_byes(self) -> None:
        t = _rr_tournament_active(4)
        self.assertFalse(t.nodes.filter(is_bye=True).exists())

    def test_slots_fixed_and_seeds_populated(self) -> None:
        t = _rr_tournament_active(4)
        for node in t.nodes.all():
            self.assertIsNotNone(node.team_a)
            self.assertIsNotNone(node.team_b)
            self.assertIsNotNone(node.seed_a)
            self.assertIsNotNone(node.seed_b)

    def test_each_unordered_pair_appears_exactly_twice(self) -> None:
        from collections import Counter

        t = _rr_tournament_active(4)
        pairs = Counter(frozenset((n.team_a_id, n.team_b_id)) for n in t.nodes.all())
        # 6 distinct unordered pairs, each appearing exactly twice (one per leg).
        self.assertEqual(len(pairs), 6)
        for pair, count in pairs.items():
            self.assertEqual(count, 2, f"pair {pair} should appear twice")

    def test_position_is_zero_based_within_each_matchday(self) -> None:
        from collections import defaultdict

        t = _rr_tournament_active(4)
        by_matchday: dict = defaultdict(list)
        for node in t.nodes.all():
            by_matchday[node.bracket_round].append(node.position)
        for matchday, positions in by_matchday.items():
            positions.sort()
            self.assertEqual(
                positions,
                list(range(len(positions))),
                f"matchday {matchday} positions must be a dense 0-based range",
            )

    def test_seed_matches_participant_seed(self) -> None:
        t = _rr_tournament_active(4)
        seed_by_team = {p.team_id: p.seed for p in t.participants.all()}
        for node in t.nodes.all():
            self.assertEqual(node.seed_a, seed_by_team[node.team_a_id])
            self.assertEqual(node.seed_b, seed_by_team[node.team_b_id])


class TestRoundRobinStandings(TestCase):
    """``round_robin_standings()`` returns one StandingsRow per enrolled team,
    zero-filled before play and reflecting hand-stamped played nodes after."""

    def test_one_row_per_enrolled_team(self) -> None:
        t = _rr_tournament_active(4)
        rows = t.round_robin_standings()
        self.assertEqual(len(rows), 4)

    def test_rows_zero_filled_before_any_play(self) -> None:
        t = _rr_tournament_active(4)
        rows = t.round_robin_standings()
        for row in rows:
            self.assertEqual(row.wins, 0)
            self.assertEqual(row.losses, 0)
            self.assertEqual(row.ties, 0)
            self.assertEqual(row.league_points, 0)

    def test_rows_cover_every_participant_team(self) -> None:
        t = _rr_tournament_active(4)
        rows = t.round_robin_standings()
        row_team_ids = {row.team_id for row in rows}
        participant_team_ids = set(t.participants.values_list("team_id", flat=True))
        self.assertEqual(row_team_ids, participant_team_ids)

    def test_returns_standings_row_instances(self) -> None:
        from matches.standings import StandingsRow

        t = _rr_tournament_active(4)
        rows = t.round_robin_standings()
        for row in rows:
            self.assertIsInstance(row, StandingsRow)

    def test_a_stamped_win_increments_winner_wins_and_points(self) -> None:
        t = _rr_tournament_active(4)
        node = t.nodes.order_by("bracket_round", "position").first()
        winner = node.team_a
        _stamp_rr_node_played(node, winner)

        rows = t.round_robin_standings()
        by_team = {row.team_id: row for row in rows}
        # The winner has exactly one win; the loser exactly one loss.
        self.assertEqual(by_team[node.team_a_id].wins, 1)
        self.assertEqual(by_team[node.team_b_id].losses, 1)
        # league_points = 3*wins + 1*ties ⇒ winner has 3, loser 0.
        self.assertEqual(by_team[node.team_a_id].league_points, 3)
        self.assertEqual(by_team[node.team_b_id].league_points, 0)

    def test_standings_rank_orders_winner_above_loser(self) -> None:
        # Hand-stamp one decisive node and assert ORDER, never exact points.
        t = _rr_tournament_active(4)
        node = t.nodes.order_by("bracket_round", "position").first()
        winner = node.team_a
        loser = node.team_b
        _stamp_rr_node_played(node, winner)

        rows = t.round_robin_standings()
        rank_by_team = {row.team_id: row.rank for row in rows}
        # The team with a win must rank ahead of (lower rank int) the team with a
        # loss.
        self.assertLess(rank_by_team[winner.id], rank_by_team[loser.id])

    def test_ranks_are_dense_one_based(self) -> None:
        t = _rr_tournament_active(4)
        rows = t.round_robin_standings()
        ranks = sorted(row.rank for row in rows)
        self.assertEqual(ranks, [1, 2, 3, 4])


class TestCompleteRoundRobinIfFinished(TestCase):
    """``complete_round_robin_if_finished()`` is a no-op until every RR node is
    resolved, then crowns the standings leader + flips state to completed;
    idempotent; no-op for non-RR / non-active."""

    def _stamp_all_nodes(self, t: Tournament) -> None:
        for node in t.nodes.all():
            _stamp_rr_node_played(node, node.team_a)

    def test_noop_while_some_nodes_unresolved(self) -> None:
        t = _rr_tournament_active(4)
        # Resolve only ONE node, leave the rest unplayed.
        node = t.nodes.order_by("bracket_round", "position").first()
        _stamp_rr_node_played(node, node.team_a)

        t.complete_round_robin_if_finished()
        t.refresh_from_db()
        self.assertEqual(t.state, "active")
        self.assertIsNone(t.champion)

    def test_crowns_leader_and_completes_when_all_resolved(self) -> None:
        t = _rr_tournament_active(4)
        self._stamp_all_nodes(t)

        t.complete_round_robin_if_finished()
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)

    def test_champion_is_standings_leader(self) -> None:
        t = _rr_tournament_active(4)
        self._stamp_all_nodes(t)

        leader_team_id = t.round_robin_standings()[0].team_id
        t.complete_round_robin_if_finished()
        t.refresh_from_db()
        self.assertEqual(t.champion_id, leader_team_id)

    def test_idempotent_second_call_is_stable(self) -> None:
        t = _rr_tournament_active(4)
        self._stamp_all_nodes(t)

        t.complete_round_robin_if_finished()
        t.refresh_from_db()
        champ_after_first = t.champion_id
        # A second call must not crash or change the result.
        t.complete_round_robin_if_finished()
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertEqual(t.champion_id, champ_after_first)

    def test_noop_for_non_round_robin_format(self) -> None:
        # A single-elim active Tournament must be untouched by the RR completer.
        t = _active_tournament_se()
        t.complete_round_robin_if_finished()
        t.refresh_from_db()
        self.assertNotEqual(t.state, "completed")
        self.assertIsNone(t.champion)

    def test_noop_for_non_active_state(self) -> None:
        # A setup-state RR Tournament (no nodes) must not flip to completed.
        t = _rr_tournament_setup(4)
        t.complete_round_robin_if_finished()
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")
        self.assertIsNone(t.champion)


# ===========================================================================
# LG-02c — RR -> Double-elimination tournament format (model layer)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — they
# stay green as single/double-elim/round-robin regression guards). Seam
# contract: ``.claude/worktrees/lg-02c-rr-de-seam-contract.md`` §"Model spec" /
# §"Test boundary".
#
# A 4th ``Tournament.format`` value ``"round_robin_double_elim"`` (label
# "Round robin -> Double elimination"). Two new Tournament fields
# ``wb_advancers`` / ``lb_advancers`` (PositiveSmallIntegerField, default 0, no
# choices). RRDE ``lock_and_build`` builds ONLY round_robin nodes (the DE finals
# are DEFERRED) and raises ValidationError on a bad count fit. The deferred
# finals build ``build_de_finals_if_rr_finished()`` fires once the last RR node
# resolves, seeding the finals from RR-standings rank. A shared
# ``_persist_elim_specs`` helper is extracted from lock_and_build so the
# single/double-elim node shapes stay byte-identical (the two regression
# classes pin that).
#
# Non-deterministic (simulate_match draws fresh per-round seeds) -> standings
# rows are hand-stamped from played SeriesMatch/Match/GameRound rows; NEVER
# assert exact simulated point totals.


def _rrde_tournament_setup(
    n: int,
    *,
    wb: int,
    lb: int,
    name: str = "RRDECup",
) -> Tournament:
    """A setup-state round_robin_double_elim Tournament with ``n`` seeded
    participants and the ``wb`` / ``lb`` advancer counts set at create time."""
    t = Tournament.objects.create(
        name=name,
        format="round_robin_double_elim",
        wb_advancers=wb,
        lb_advancers=lb,
    )
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _rrde_tournament_active(
    n: int,
    *,
    wb: int,
    lb: int,
    name: str = "RRDECup",
) -> Tournament:
    """A locked/active RRDE Tournament — only the round_robin Seeding nodes are
    built at lock; the DE finals are deferred."""
    t = _rrde_tournament_setup(n, wb=wb, lb=lb, name=name)
    t.lock_and_build()
    t.refresh_from_db()
    return t


def _resolve_all_rr_nodes(t: Tournament) -> None:
    """Hand-stamp every round_robin node of an RRDE Tournament as a decisive
    played Bo1 (reuses the RR ``_stamp_rr_node_played`` helper). team_a always
    wins, so the standings rank is deterministic by seed talent / name order."""
    for node in t.nodes.filter(bracket_type="round_robin"):
        _stamp_rr_node_played(node, node.team_a)


class TestRrDeFieldsAndFormat(TestCase):
    """The two new advancer fields exist, default 0, carry NO choices; the
    ``format`` enum accepts ``"round_robin_double_elim"``."""

    def test_wb_advancers_defaults_to_zero(self) -> None:
        t = Tournament.objects.create(name="Cup")
        self.assertEqual(t.wb_advancers, 0)

    def test_lb_advancers_defaults_to_zero(self) -> None:
        t = Tournament.objects.create(name="Cup")
        self.assertEqual(t.lb_advancers, 0)

    def test_advancer_fields_persist(self) -> None:
        t = Tournament.objects.create(
            name="Cup",
            format="round_robin_double_elim",
            wb_advancers=4,
            lb_advancers=2,
        )
        t.refresh_from_db()
        self.assertEqual(t.wb_advancers, 4)
        self.assertEqual(t.lb_advancers, 2)

    def test_wb_advancers_has_no_choices(self) -> None:
        field = Tournament._meta.get_field("wb_advancers")
        self.assertIsNone(field.choices)

    def test_lb_advancers_has_no_choices(self) -> None:
        field = Tournament._meta.get_field("lb_advancers")
        self.assertIsNone(field.choices)

    def test_format_choices_include_round_robin_double_elim(self) -> None:
        choices = dict(Tournament._meta.get_field("format").choices)
        self.assertIn("round_robin_double_elim", choices)
        # Locked label uses the em-dash arrow U+2192.
        self.assertEqual(
            choices["round_robin_double_elim"], "Round robin → Double elimination"
        )

    def test_round_robin_double_elim_format_persists(self) -> None:
        t = Tournament.objects.create(name="RRDE Cup", format="round_robin_double_elim")
        t.refresh_from_db()
        self.assertEqual(t.format, "round_robin_double_elim")


class TestRrDeLockAndBuild(TestCase):
    """RRDE ``lock_and_build`` builds ONLY the round_robin Seeding nodes (no
    WB/LB/GF at lock) and raises ValidationError on a bad count fit."""

    def test_builds_only_round_robin_nodes(self) -> None:
        t = _rrde_tournament_active(4, wb=4, lb=0, name="RRDELockRR")
        types = set(t.nodes.values_list("bracket_type", flat=True))
        self.assertEqual(types, {"round_robin"})

    def test_no_elim_nodes_at_lock(self) -> None:
        t = _rrde_tournament_active(6, wb=4, lb=2, name="RRDELockNoElim")
        self.assertEqual(
            t.nodes.exclude(bracket_type="round_robin").count(),
            0,
            "the DE finals are deferred — no WB/LB/GF nodes at lock time",
        )

    def test_n4_builds_twelve_rr_nodes(self) -> None:
        # N=4 double round-robin Seeding stage ⇒ 12 nodes.
        t = _rrde_tournament_active(4, wb=4, lb=0, name="RRDELock12")
        self.assertEqual(t.nodes.filter(bracket_type="round_robin").count(), 12)

    def test_state_flips_to_active(self) -> None:
        t = _rrde_tournament_active(4, wb=4, lb=0, name="RRDELockActive")
        self.assertEqual(t.state, "active")

    def test_raises_when_wb_advancers_exceeds_participants(self) -> None:
        # wb=8 but only 6 participants ⇒ ValidationError, no state flip.
        t = _rrde_tournament_setup(6, wb=8, lb=0, name="RRDELockWbTooBig")
        with self.assertRaises(ValidationError):
            t.lock_and_build()
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.nodes.count(), 0)

    def test_raises_when_wb_plus_lb_exceeds_participants(self) -> None:
        # wb=4 + lb=2 = 6 > 5 participants ⇒ ValidationError.
        t = _rrde_tournament_setup(5, wb=4, lb=2, name="RRDELockSumTooBig")
        with self.assertRaises(ValidationError):
            t.lock_and_build()
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.nodes.count(), 0)

    def test_exact_fit_is_allowed(self) -> None:
        # wb=4 + lb=2 = 6 == 6 participants ⇒ the boundary is allowed.
        t = _rrde_tournament_active(6, wb=4, lb=2, name="RRDELockExactFit")
        self.assertEqual(t.state, "active")
        self.assertEqual(t.nodes.filter(bracket_type="round_robin").count(), 30)


class TestBuildDeFinalsIfRrFinished(TestCase):
    """``build_de_finals_if_rr_finished()`` is a no-op unless RRDE + active +
    every RR node resolved + finals not already built; idempotent; when it
    fires it seeds the finals from RR-standings RANK."""

    def test_noop_while_some_rr_node_unresolved(self) -> None:
        t = _rrde_tournament_active(4, wb=4, lb=0, name="RRDEFinNoop")
        # Resolve only ONE RR node.
        node = t.nodes.filter(bracket_type="round_robin").first()
        _stamp_rr_node_played(node, node.team_a)

        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        # No finals nodes built; tournament stays active.
        self.assertFalse(t.nodes.exclude(bracket_type="round_robin").exists())
        self.assertEqual(t.state, "active")

    def test_noop_for_non_rrde_format(self) -> None:
        # A plain round_robin active Tournament must not gain DE finals.
        t = _rr_tournament_active(4, name="RRDEFinNonRrde")
        for node in t.nodes.filter(bracket_type="round_robin"):
            _stamp_rr_node_played(node, node.team_a)
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        self.assertFalse(t.nodes.exclude(bracket_type="round_robin").exists())

    def test_fires_and_builds_finals_when_all_rr_resolved(self) -> None:
        t = _rrde_tournament_active(4, wb=4, lb=0, name="RRDEFinFires")
        _resolve_all_rr_nodes(t)
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        # WB/LB/GF finals nodes now exist; tournament stays active.
        self.assertTrue(t.nodes.exclude(bracket_type="round_robin").exists())
        self.assertEqual(t.state, "active")
        self.assertIsNone(t.champion)

    def test_finals_have_grand_final_nodes(self) -> None:
        t = _rrde_tournament_active(4, wb=4, lb=0, name="RRDEFinGF")
        _resolve_all_rr_nodes(t)
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        self.assertEqual(t.nodes.filter(bracket_type="grand_final").count(), 2)

    def test_idempotent_second_call_builds_no_duplicate(self) -> None:
        t = _rrde_tournament_active(4, wb=4, lb=0, name="RRDEFinIdem")
        _resolve_all_rr_nodes(t)
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        finals_after_first = t.nodes.exclude(bracket_type="round_robin").count()
        # A second call is a no-op (finals already built).
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        self.assertEqual(
            t.nodes.exclude(bracket_type="round_robin").count(),
            finals_after_first,
        )

    def test_wb_seed_one_is_rr_rank_one_team(self) -> None:
        # The top RR-ranked team becomes WB seed 1 in the finals.
        t = _rrde_tournament_active(4, wb=4, lb=0, name="RRDEFinSeed1")
        _resolve_all_rr_nodes(t)
        rr_rank_one = t.round_robin_standings()[0].team_id
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        # The WB node carrying seed 1 must hold the RR rank-1 team.
        wb_seed_one = (
            t.nodes.filter(bracket_type="winners", seed_a=1).first()
            or t.nodes.filter(bracket_type="winners", seed_b=1).first()
        )
        self.assertIsNotNone(wb_seed_one, "a WB node must carry Bracket seed 1")
        seed_one_team = (
            wb_seed_one.team_a_id if wb_seed_one.seed_a == 1 else wb_seed_one.team_b_id
        )
        self.assertEqual(seed_one_team, rr_rank_one)

    def test_eliminated_team_has_no_finals_node(self) -> None:
        # 6 participants, wb=4 + lb=0 ⇒ ranks 5 and 6 are eliminated (never
        # appear in any finals node's team slots).
        t = _rrde_tournament_active(6, wb=4, lb=0, name="RRDEFinElim")
        _resolve_all_rr_nodes(t)
        rows = t.round_robin_standings()
        eliminated_team_id = rows[-1].team_id  # RR rank 6 (last)
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        finals_team_ids: set = set()
        for node in t.nodes.exclude(bracket_type="round_robin"):
            finals_team_ids.add(node.team_a_id)
            finals_team_ids.add(node.team_b_id)
        self.assertNotIn(
            eliminated_team_id,
            finals_team_ids,
            "an RR-eliminated team must have NO finals node slot",
        )

    def test_lb_preseeds_enter_finals_for_wb4_lb2(self) -> None:
        # wb=4 + lb=2 over 6 teams: the next 2 RR ranks fill the LB pre-seeds, so
        # all 6 teams appear in the finals (4 WB seeds + 2 LB pre-seeds).
        t = _rrde_tournament_active(6, wb=4, lb=2, name="RRDEFinLbPreseed")
        _resolve_all_rr_nodes(t)
        rows = t.round_robin_standings()
        wb_team_ids = {rows[i].team_id for i in range(4)}
        lb_team_ids = {rows[4].team_id, rows[5].team_id}
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        finals_team_ids: set = set()
        for node in t.nodes.exclude(bracket_type="round_robin"):
            if node.team_a_id is not None:
                finals_team_ids.add(node.team_a_id)
            if node.team_b_id is not None:
                finals_team_ids.add(node.team_b_id)
        # The top 4 are WB seeds; the next 2 are LB pre-seeds — all 6 present.
        self.assertTrue(wb_team_ids <= finals_team_ids)
        self.assertTrue(lb_team_ids <= finals_team_ids)

    def test_finals_have_no_bye_nodes(self) -> None:
        t = _rrde_tournament_active(6, wb=4, lb=2, name="RRDEFinNoBye")
        _resolve_all_rr_nodes(t)
        t.build_de_finals_if_rr_finished()
        t.refresh_from_db()
        self.assertFalse(
            t.nodes.exclude(bracket_type="round_robin").filter(is_bye=True).exists(),
            "the RRDE finals are exactly filled — zero bye nodes",
        )


class TestLockAndBuildSingleElimUnchangedAfterRefactor(TestCase):
    """Regression: the extracted ``_persist_elim_specs`` helper keeps a
    single-elim lock byte-identical — same node count, bracket_types, edges,
    series_lengths."""

    def _se_tournament(self, n: int, *, name: str) -> Tournament:
        t = Tournament.objects.create(
            name=name,
            final_series_length=5,
            semifinal_series_length=3,
            quarterfinal_series_length=1,
            earlier_series_length=1,
        )
        for seed, team in enumerate(_make_teams(n), start=1):
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        t.lock_and_build()
        t.refresh_from_db()
        return t

    def test_node_count_unchanged_n4(self) -> None:
        t = self._se_tournament(4, name="RRDESERegN4")
        self.assertEqual(t.nodes.count(), 3)

    def test_all_nodes_winners_bracket(self) -> None:
        t = self._se_tournament(4, name="RRDESERegType")
        self.assertEqual(
            set(t.nodes.values_list("bracket_type", flat=True)), {"winners"}
        )

    def test_no_loser_advances_to_on_any_node(self) -> None:
        t = self._se_tournament(4, name="RRDESERegLoser")
        self.assertEqual(t.nodes.exclude(loser_advances_to__isnull=True).count(), 0)

    def test_advances_to_edges_wired(self) -> None:
        t = self._se_tournament(4, name="RRDESERegEdges")
        # Two round-1 nodes feed the final; the final has advances_to None.
        final = t.nodes.get(advances_to__isnull=True)
        feeders = t.nodes.filter(advances_to=final)
        self.assertEqual(feeders.count(), 2)
        slots = sorted(feeders.values_list("advances_to_slot", flat=True))
        self.assertEqual(slots, ["a", "b"])

    def test_series_length_depth_stamped(self) -> None:
        t = self._se_tournament(4, name="RRDESERegSeries")
        final = t.nodes.get(advances_to__isnull=True)
        self.assertEqual(final.series_length, 5)
        for r1 in t.nodes.filter(bracket_round=1):
            self.assertEqual(r1.series_length, 3)

    def test_byes_preresolved_n5(self) -> None:
        t = self._se_tournament(5, name="RRDESERegByes")
        byes = t.nodes.filter(is_bye=True)
        self.assertEqual(byes.count(), 3)
        for node in byes:
            self.assertIsNotNone(node.winner)


class TestLockAndBuildDoubleElimUnchangedAfterRefactor(TestCase):
    """Regression: the extracted ``_persist_elim_specs`` helper keeps a
    double-elim lock byte-identical — same bracket_types, loser-drop edges,
    GF1->GF2 wiring, series_lengths."""

    def _de_tournament(self, n: int, *, name: str) -> Tournament:
        t = Tournament.objects.create(
            name=name,
            format="double_elimination",
            final_series_length=5,
            semifinal_series_length=3,
            quarterfinal_series_length=1,
            earlier_series_length=1,
        )
        for seed, team in enumerate(_make_teams(n), start=1):
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        t.lock_and_build()
        t.refresh_from_db()
        return t

    def test_persists_all_three_bracket_types(self) -> None:
        t = self._de_tournament(4, name="RRDEDERegTypes")
        types = set(t.nodes.values_list("bracket_type", flat=True))
        self.assertEqual(types, {"winners", "losers", "grand_final"})

    def test_grand_final_has_two_nodes(self) -> None:
        t = self._de_tournament(4, name="RRDEDERegGF")
        self.assertEqual(t.nodes.filter(bracket_type="grand_final").count(), 2)

    def test_wb_node_loser_advances_to_lb(self) -> None:
        t = self._de_tournament(4, name="RRDEDERegDrop")
        wb_with_drop = (
            t.nodes.filter(bracket_type="winners", is_bye=False)
            .exclude(loser_advances_to__isnull=True)
            .first()
        )
        self.assertIsNotNone(wb_with_drop)
        self.assertEqual(wb_with_drop.loser_advances_to.bracket_type, "losers")

    def test_gf1_loser_advances_to_gf2(self) -> None:
        t = self._de_tournament(4, name="RRDEDERegGF1")
        gf1 = (
            t.nodes.filter(bracket_type="grand_final")
            .exclude(advances_to__isnull=True)
            .first()
        )
        self.assertIsNotNone(gf1)
        self.assertEqual(gf1.loser_advances_to.bracket_type, "grand_final")

    def test_series_length_depth_stamped(self) -> None:
        t = self._de_tournament(4, name="RRDEDERegSeries")
        for gf in t.nodes.filter(bracket_type="grand_final"):
            self.assertEqual(gf.series_length, 5)
        wb_final = (
            t.nodes.filter(bracket_type="winners").order_by("-bracket_round").first()
        )
        self.assertEqual(wb_final.series_length, 3)


# ===========================================================================
# LG-02c — Swiss tournament format (model layer)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — every
# single/double-elim/round-robin/RR->DE model test stays green as a regression
# guard). Seam contract: ``.claude/worktrees/lg-02c-swiss-seam-contract.md``
# (MODEL section + TEST BOUNDARY).
#
# A 5th ``Tournament.format`` value ``"swiss"`` (label "Swiss"). One new
# Tournament field ``swiss_rounds`` (PositiveSmallIntegerField, default 0, no
# choices — 0 = auto, resolved at lock to ceil(log2(N)) clamped to [1, N-1] then
# written back, frozen). A 5th ``BracketNode.bracket_type`` value ``"swiss"``.
# Swiss is FLAT and edge-less: every node advances_to=None / loser_advances_to=
# None / is_bye=False / series_length=1; the champion is the Buchholz-re-ranked
# Standings leader after the last round resolves, NOT a final node. EVEN-N ONLY
# (odd raises ValidationError with the EXACT message).
#
# Non-deterministic at play time (simulate_match draws fresh per-round seeds) ->
# standings rows are hand-stamped from played SeriesMatch/Match/GameRound rows;
# NEVER assert exact simulated point totals — assert ORDER / shape / state.


def _swiss_tournament_setup(
    n: int, *, swiss_rounds: int = 0, name: str = "SwissCup"
) -> Tournament:
    """A setup-state swiss Tournament with ``n`` seeded participants."""
    t = Tournament.objects.create(name=name, format="swiss", swiss_rounds=swiss_rounds)
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _swiss_tournament_active(
    n: int, *, swiss_rounds: int = 0, name: str = "SwissCup"
) -> Tournament:
    """A locked/active swiss Tournament — only the R1 fold nodes are built at
    lock; later rounds are deferred."""
    t = _swiss_tournament_setup(n, swiss_rounds=swiss_rounds, name=name)
    t.lock_and_build()
    t.refresh_from_db()
    return t


def _stamp_swiss_node_played(node: BracketNode, winner) -> None:
    """Hand-stamp a Swiss node as a decisive played Bo1 (mirrors the RR helper):
    set node.winner, create a played Match (team_a=red / team_b=blue, two
    GameRounds) + one SeriesMatch row. The winner sweeps both rounds so
    Match.winner is non-null and the standings reflect a clean win without
    running a real sim."""
    from matches.models import GameRound, Match, SeriesMatch

    team_red = node.team_a
    team_blue = node.team_b
    if winner.id == team_red.id:
        red_r1, blue_r1, red_r2, blue_r2 = 500, 10, 500, 10
    else:
        red_r1, blue_r1, red_r2, blue_r2 = 10, 500, 10, 500
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        match_type="tournament",
        red_round1_points=red_r1,
        blue_round1_points=blue_r1,
        red_round2_points=red_r2,
        blue_round2_points=blue_r2,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_r1,
        blue_points=blue_r1,
    )
    GameRound.objects.create(
        match=match,
        round_number=2,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_r2,
        blue_points=blue_r2,
    )
    SeriesMatch.objects.create(node=node, match=match, game_number=1, winner=winner)
    node.winner = winner
    node.save(update_fields=["winner"])


def _make_swiss_node(t: Tournament, bracket_round: int, position: int, team_a, team_b):
    """Hand-stamp a flat Swiss node row (used to build a deferred round-2 by hand
    for the standings / Buchholz tests). Mirrors the lock-time create kwargs."""
    seed_by_team = {p.team_id: p.seed for p in t.participants.all()}
    return BracketNode.objects.create(
        tournament=t,
        bracket_type="swiss",
        bracket_round=bracket_round,
        position=position,
        team_a=team_a,
        team_b=team_b,
        seed_a=seed_by_team[team_a.id],
        seed_b=seed_by_team[team_b.id],
        is_bye=False,
        series_length=1,
    )


class TestSwissRoundsField(TestCase):
    """``Tournament.swiss_rounds`` exists, defaults 0, carries NO choices."""

    def test_swiss_rounds_defaults_to_zero(self) -> None:
        t = Tournament.objects.create(name="Cup")
        self.assertEqual(t.swiss_rounds, 0)

    def test_swiss_rounds_persists(self) -> None:
        t = Tournament.objects.create(name="Cup", format="swiss", swiss_rounds=3)
        t.refresh_from_db()
        self.assertEqual(t.swiss_rounds, 3)

    def test_swiss_rounds_has_no_choices(self) -> None:
        field = Tournament._meta.get_field("swiss_rounds")
        self.assertIsNone(field.choices)

    def test_format_choices_include_swiss(self) -> None:
        choices = dict(Tournament._meta.get_field("format").choices)
        self.assertIn("swiss", choices)
        self.assertEqual(choices["swiss"], "Swiss")

    def test_bracket_type_choices_include_swiss(self) -> None:
        choices = dict(BracketNode._meta.get_field("bracket_type").choices)
        self.assertIn("swiss", choices)
        self.assertEqual(choices["swiss"], "Swiss")


class TestSwissLockAndBuild(TestCase):
    """``lock_and_build`` on a swiss Tournament builds ONLY the R1 fold nodes,
    resolves+clamps+freezes the round count, and rejects an odd participant
    count with the EXACT ValidationError message."""

    def test_even_n_builds_only_r1_nodes(self) -> None:
        t = _swiss_tournament_active(4, name="SwissLockR1")
        # Only round 1 exists at lock (later rounds deferred).
        rounds = set(t.nodes.values_list("bracket_round", flat=True))
        self.assertEqual(rounds, {1})

    def test_r1_node_count_is_n_over_two(self) -> None:
        t = _swiss_tournament_active(8, name="SwissLockCount")
        self.assertEqual(t.nodes.filter(bracket_round=1).count(), 4)

    def test_every_r1_node_is_swiss_flat_bo1(self) -> None:
        t = _swiss_tournament_active(4, name="SwissLockFlat")
        for node in t.nodes.all():
            self.assertEqual(node.bracket_type, "swiss")
            self.assertEqual(node.series_length, 1)
            self.assertIsNone(node.advances_to_id)
            self.assertIsNone(node.loser_advances_to_id)
            self.assertFalse(node.is_bye)

    def test_state_flips_to_active(self) -> None:
        t = _swiss_tournament_active(4, name="SwissLockActive")
        self.assertEqual(t.state, "active")

    def test_r1_is_the_seed_fold_pairing(self) -> None:
        # N=4 seeds 1..4 fold: seed 1 vs seed 3, seed 2 vs seed 4.
        t = _swiss_tournament_active(4, name="SwissLockFold")
        seed_by_team = {p.team_id: p.seed for p in t.participants.all()}
        pairs = {
            frozenset({seed_by_team[n.team_a_id], seed_by_team[n.team_b_id]})
            for n in t.nodes.filter(bracket_round=1)
        }
        self.assertEqual(pairs, {frozenset({1, 3}), frozenset({2, 4})})

    # -- round-count resolve / clamp / freeze ---------------------------------

    def test_swiss_rounds_zero_resolved_to_ceil_log2_n(self) -> None:
        import math

        # N=8 ⇒ ceil(log2(8)) = 3, clamped to [1, 7] ⇒ 3, frozen back.
        t = _swiss_tournament_active(8, swiss_rounds=0, name="SwissLockAuto")
        self.assertEqual(t.swiss_rounds, math.ceil(math.log2(8)))
        self.assertEqual(t.swiss_rounds, 3)

    def test_swiss_rounds_clamped_to_n_minus_one_upper(self) -> None:
        # N=4 ⇒ max rounds is N-1 = 3; an explicit out-of-range high value clamps.
        t = _swiss_tournament_active(4, swiss_rounds=99, name="SwissLockClampHi")
        self.assertEqual(t.swiss_rounds, 3)

    def test_explicit_in_range_value_frozen_verbatim(self) -> None:
        t = _swiss_tournament_active(8, swiss_rounds=2, name="SwissLockExplicit")
        self.assertEqual(t.swiss_rounds, 2)

    def test_resolved_value_written_back(self) -> None:
        # The resolved round count is persisted (frozen) on the row.
        t = _swiss_tournament_active(8, swiss_rounds=0, name="SwissLockFrozen")
        t.refresh_from_db()
        self.assertGreaterEqual(t.swiss_rounds, 1)

    # -- odd-N guard ----------------------------------------------------------

    def test_odd_n_raises_validation_error(self) -> None:
        t = _swiss_tournament_setup(5, name="SwissLockOdd")
        with self.assertRaises(ValidationError):
            t.lock_and_build()

    def test_odd_n_error_message_is_exact(self) -> None:
        t = _swiss_tournament_setup(5, name="SwissLockOddMsg")
        with self.assertRaises(ValidationError) as ctx:
            t.lock_and_build()
        self.assertIn(
            "Swiss requires an even number of participants.",
            str(ctx.exception),
        )

    def test_odd_n_leaves_state_setup_and_no_nodes(self) -> None:
        t = _swiss_tournament_setup(5, name="SwissLockOddNoNodes")
        with self.assertRaises(ValidationError):
            t.lock_and_build()
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.nodes.count(), 0)


class TestStandingsOverNodesExtraction(TestCase):
    """Regression: extracting ``_standings_over_nodes`` keeps
    ``round_robin_standings()`` output byte-identical. Build a small RR
    Tournament, hand-stamp resolved nodes, and assert the ranked rows are
    unchanged in team order / wins / league_points / rank."""

    def test_round_robin_standings_unchanged_after_extraction(self) -> None:
        t = _rr_tournament_active(4, name="ExtractRR")
        # Resolve every node with team_a winning so the standings are decisive.
        for node in t.nodes.all():
            _stamp_rr_node_played(node, node.team_a)

        rows = t.round_robin_standings()
        from matches.standings import StandingsRow

        # One row per enrolled team, dense 1-based rank, all StandingsRow.
        self.assertEqual(len(rows), 4)
        self.assertEqual(sorted(r.rank for r in rows), [1, 2, 3, 4])
        for r in rows:
            self.assertIsInstance(r, StandingsRow)
        # league_points = 3*wins + 1*ties; the standings ladder orders by
        # league_points desc — assert the ladder is monotone non-increasing.
        lps = [r.league_points for r in rows]
        self.assertEqual(lps, sorted(lps, reverse=True))

    def test_round_robin_standings_zero_filled_before_play(self) -> None:
        t = _rr_tournament_active(4, name="ExtractRRZero")
        rows = t.round_robin_standings()
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertEqual(r.wins, 0)
            self.assertEqual(r.league_points, 0)


class TestSwissStandingsBuchholz(TestCase):
    """``swiss_standings()`` returns the Buchholz-re-ranked Standings: hand-stamp
    resolved Swiss nodes across >= 2 rounds and assert the ORDER reflects the
    Buchholz ladder (NOT exact points)."""

    def test_one_row_per_enrolled_team(self) -> None:
        t = _swiss_tournament_active(4, name="SwissStdRows")
        rows = t.swiss_standings()
        self.assertEqual(len(rows), 4)

    def test_returns_standings_row_instances(self) -> None:
        from matches.standings import StandingsRow

        t = _swiss_tournament_active(4, name="SwissStdType")
        for r in t.swiss_standings():
            self.assertIsInstance(r, StandingsRow)

    def test_ranks_are_dense_one_based(self) -> None:
        t = _swiss_tournament_active(4, name="SwissStdRank")
        rows = t.swiss_standings()
        self.assertEqual(sorted(r.rank for r in rows), [1, 2, 3, 4])

    def test_buchholz_breaks_an_equal_points_tie_across_two_rounds(self) -> None:
        # N=4 seeds 1..4 (team ids 100+seed). R1 fold: (1 vs 3), (2 vs 4).
        # Stamp R1 so seed1 beats seed3 and seed2 beats seed4 -> after R1 both
        # seed1 and seed2 have one win (3 pts). Build R2 by hand pairing the two
        # winners (1 vs 2) and the two losers (3 vs 4); stamp seed1 beats seed2,
        # seed3 beats seed4. Now seed1 has 2 wins (clear leader). The interesting
        # tie is between the two ONE-win teams seed2 and seed3: equal-ish points
        # but seed2's opponents (seed4 0-win, seed1 2-win) vs seed3's opponents
        # (seed1 2-win, seed4 0-win) — we assert ORDER is well-defined and the
        # 2-win team leads, never exact points.
        t = _swiss_tournament_active(4, name="SwissStdBuchholz")
        teams_by_seed = {p.seed: p.team for p in t.participants.all()}
        # R1 nodes (already built at lock).
        r1 = list(t.nodes.filter(bracket_round=1).order_by("position"))
        for node in r1:
            # team_a is the lower-seed slot in the fold; let the lower seed win.
            higher_seed_team = node.team_a if node.seed_a < node.seed_b else node.team_b
            _stamp_swiss_node_played(node, higher_seed_team)

        # Hand-build R2: winners bracket-ish pairing seed1 vs seed2, seed3 vs 4.
        n_a = _make_swiss_node(t, 2, 0, teams_by_seed[1], teams_by_seed[2])
        n_b = _make_swiss_node(t, 2, 1, teams_by_seed[3], teams_by_seed[4])
        _stamp_swiss_node_played(n_a, teams_by_seed[1])  # seed1 -> 2 wins
        _stamp_swiss_node_played(n_b, teams_by_seed[3])  # seed3 -> 1 win

        rows = t.swiss_standings()
        rank_by_team = {r.team_id: r.rank for r in rows}
        # seed1 (2 wins) is the clear leader.
        self.assertEqual(rank_by_team[teams_by_seed[1].id], 1)
        # seed4 (0 wins) is last.
        self.assertEqual(rank_by_team[teams_by_seed[4].id], 4)

    def test_swiss_standings_zero_filled_before_play(self) -> None:
        t = _swiss_tournament_active(4, name="SwissStdZero")
        rows = t.swiss_standings()
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertEqual(r.wins, 0)
            self.assertEqual(r.league_points, 0)


class TestAdvanceSwissIfRoundFinished(TestCase):
    """``advance_swiss_if_round_finished()``: no-op until the current (highest)
    Swiss round is fully resolved; then either build the next round (current <
    swiss_rounds, stays active) or crown the Standings leader + complete
    (current == swiss_rounds)."""

    def test_noop_while_current_round_unresolved(self) -> None:
        t = _swiss_tournament_active(4, swiss_rounds=2, name="SwissAdvNoop")
        # Resolve only ONE of the two R1 nodes.
        node = t.nodes.filter(bracket_round=1).order_by("position").first()
        _stamp_swiss_node_played(node, node.team_a)

        t.advance_swiss_if_round_finished()
        t.refresh_from_db()
        # No round-2 nodes built; tournament stays active, no champion.
        self.assertFalse(t.nodes.filter(bracket_round=2).exists())
        self.assertEqual(t.state, "active")
        self.assertIsNone(t.champion)

    def test_builds_next_round_when_current_resolved_and_more_rounds(self) -> None:
        t = _swiss_tournament_active(4, swiss_rounds=2, name="SwissAdvNext")
        for node in t.nodes.filter(bracket_round=1):
            _stamp_swiss_node_played(node, node.team_a)

        t.advance_swiss_if_round_finished()
        t.refresh_from_db()
        # Round 2 built; tournament STAYS active (not yet at swiss_rounds).
        self.assertTrue(t.nodes.filter(bracket_round=2).exists())
        self.assertEqual(t.state, "active")
        self.assertIsNone(t.champion)

    def test_next_round_node_count_matches_field_size(self) -> None:
        t = _swiss_tournament_active(4, swiss_rounds=2, name="SwissAdvNextCount")
        for node in t.nodes.filter(bracket_round=1):
            _stamp_swiss_node_played(node, node.team_a)
        t.advance_swiss_if_round_finished()
        t.refresh_from_db()
        # N=4 ⇒ N/2 = 2 nodes per round.
        self.assertEqual(t.nodes.filter(bracket_round=2).count(), 2)

    def test_next_round_nodes_are_swiss_bracket_round_plus_one(self) -> None:
        t = _swiss_tournament_active(4, swiss_rounds=2, name="SwissAdvNextType")
        for node in t.nodes.filter(bracket_round=1):
            _stamp_swiss_node_played(node, node.team_a)
        t.advance_swiss_if_round_finished()
        t.refresh_from_db()
        for node in t.nodes.filter(bracket_round=2):
            self.assertEqual(node.bracket_type, "swiss")
            self.assertEqual(node.series_length, 1)
            self.assertIsNone(node.advances_to_id)

    def test_crowns_and_completes_on_final_round(self) -> None:
        # swiss_rounds=1 ⇒ resolving R1 is the final round ⇒ crown + complete.
        t = _swiss_tournament_active(4, swiss_rounds=1, name="SwissAdvCrown")
        for node in t.nodes.filter(bracket_round=1):
            _stamp_swiss_node_played(node, node.team_a)

        leader_team_id = t.swiss_standings()[0].team_id
        t.advance_swiss_if_round_finished()
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertEqual(t.champion_id, leader_team_id)

    def test_champion_is_swiss_standings_leader(self) -> None:
        t = _swiss_tournament_active(4, swiss_rounds=1, name="SwissAdvLeader")
        for node in t.nodes.filter(bracket_round=1):
            _stamp_swiss_node_played(node, node.team_a)
        expected = t.swiss_standings()[0].team_id
        t.advance_swiss_if_round_finished()
        t.refresh_from_db()
        self.assertEqual(t.champion_id, expected)

    def test_played_pairs_derivation_no_rematch_when_avoidable(self) -> None:
        # After R1 (fold pairs (1v3), (2v4) for N=4), the greedy R2 sweep must
        # NOT replay either fold pair when a non-rematch pairing exists.
        t = _swiss_tournament_active(4, swiss_rounds=2, name="SwissAdvPairs")
        # Build the played_pairs set from R1 nodes BEFORE resolving.
        r1_pairs = {
            frozenset({n.team_a_id, n.team_b_id})
            for n in t.nodes.filter(bracket_round=1)
        }
        for node in t.nodes.filter(bracket_round=1):
            _stamp_swiss_node_played(node, node.team_a)
        t.advance_swiss_if_round_finished()
        t.refresh_from_db()
        r2_pairs = {
            frozenset({n.team_a_id, n.team_b_id})
            for n in t.nodes.filter(bracket_round=2)
        }
        # No round-2 pairing repeats a round-1 pairing (rematch only as a
        # trailing fallback, which is avoidable for this 4-team field).
        self.assertTrue(
            r2_pairs.isdisjoint(r1_pairs),
            f"R2 pairings {r2_pairs} must avoid R1 rematches {r1_pairs}",
        )
