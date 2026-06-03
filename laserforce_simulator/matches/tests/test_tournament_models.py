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
