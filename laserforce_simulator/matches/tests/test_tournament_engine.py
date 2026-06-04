"""LG-02a-2 — Django ``TestCase`` tests for ``matches/tournament_engine.py``.

The engine extracts the per-node resolve/advance body of the LG-02a inline
``tournament_play_next`` view into one pure-ORM function:

    play_next_node(tournament: Tournament) -> BracketNode | None

Seam contract locked at ``.claude/worktrees/lg-02a-2-seam-contract.md`` §2.
``@transaction.atomic`` — one node = one transactional unit (ADR-0016).

Tests assert schema-level outcomes (resolved node, stamped winner, advanced
parent slot, champion + ``state="completed"`` on the final, tie-break path,
``None`` when nothing playable) — NOT exact point totals. ``ROUND_TICKS`` is
patched small for speed; the REAL ``simulate_match`` seam is exercised on the
happy paths (no ``mock.patch`` on it) so signature drift fails loudly. The
forced-tie path patches ``simulate_match`` to return a pre-built tied Match so
the ``break_tie`` branch fires deterministically.

These assertions WILL fail until the Code agent lands
``matches/tournament_engine.py``; that is expected for the parallel build.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from matches.models import Match, Tournament, TournamentParticipant
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# Small tick window so a played Match round terminates fast.
_FAST_TICKS = 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teams(n: int) -> list[Team]:
    return [make_team_with_slots(f"TE{i}")[0] for i in range(n)]


def _setup_tournament(n: int, *, name: str = "EngCup") -> Tournament:
    """A setup-state Tournament with ``n`` seeded participants."""
    t = Tournament.objects.create(name=name)
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _active_tournament(n: int, *, name: str = "EngCup") -> Tournament:
    """A locked/active Tournament with its bracket built."""
    t = _setup_tournament(n, name=name)
    t.lock_and_build()
    t.refresh_from_db()
    return t


# ---------------------------------------------------------------------------
# TestPlayNextNodeResolvesOneNode
# ---------------------------------------------------------------------------


class TestPlayNextNodeResolvesOneNode(TestCase):
    """``play_next_node`` resolves exactly one node + stamps its winner."""

    def test_returns_the_resolved_node(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        self.assertIsNotNone(node)
        # The returned node is one of this Tournament's nodes.
        self.assertEqual(node.tournament_id, t.id)

    def test_resolves_exactly_one_node(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_next_node(t)
        # Bo1: one Match per node is recorded as a SeriesMatch row.
        played = t.nodes.filter(series_matches__isnull=False).distinct()
        self.assertEqual(played.count(), 1)

    def test_stamps_winner_on_resolved_node(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        node.refresh_from_db()
        self.assertIsNotNone(node.winner)
        self.assertTrue(node.series_matches.exists())

    def test_winner_is_one_of_the_two_node_teams(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        node.refresh_from_db()
        self.assertIn(node.winner_id, {node.team_a_id, node.team_b_id})

    def test_resolves_lowest_playable_node_first(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        # Round 1 resolves before round 2.
        self.assertEqual(node.bracket_round, 1)


# ---------------------------------------------------------------------------
# TestPlayNextNodeAdvancesWinner
# ---------------------------------------------------------------------------


class TestPlayNextNodeAdvancesWinner(TestCase):
    """The winner is advanced into the parent slot (``team_*`` + ``seed_*``)."""

    def test_winner_fills_parent_slot_team(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        node.refresh_from_db()
        parent = node.advances_to
        self.assertIsNotNone(parent)
        parent.refresh_from_db()
        slot_team = getattr(parent, f"team_{node.advances_to_slot}")
        self.assertIsNotNone(slot_team)
        self.assertEqual(slot_team.id, node.winner_id)

    def test_winner_seed_fills_parent_slot_seed(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        node.refresh_from_db()
        parent = node.advances_to
        parent.refresh_from_db()
        slot_seed = getattr(parent, f"seed_{node.advances_to_slot}")
        # The advanced seed is whichever seed the winning team carried.
        expected_seed = node.seed_a if node.winner_id == node.team_a_id else node.seed_b
        self.assertEqual(slot_seed, expected_seed)


# ---------------------------------------------------------------------------
# TestPlayNextNodeChampion
# ---------------------------------------------------------------------------


class TestPlayNextNodeChampion(TestCase):
    """The final node stamps ``champion`` + ``state="completed"``."""

    def test_play_to_completion_stamps_champion_and_completed(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            # 4-team bracket: 3 playable games (2 round-1 + 1 final).
            for _ in range(10):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)

    def test_champion_is_the_final_node_winner(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(10):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                play_next_node(t)
        t.refresh_from_db()
        final = t.nodes.get(advances_to__isnull=True)
        self.assertEqual(t.champion_id, final.winner_id)


# ---------------------------------------------------------------------------
# TestPlayNextNodeReturnsNone
# ---------------------------------------------------------------------------


class TestPlayNextNodeReturnsNone(TestCase):
    """``play_next_node`` returns ``None`` when no node is playable."""

    def test_returns_none_when_bracket_complete(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(10):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                play_next_node(t)
            # Everything is played now — the next call is a no-op None.
            t.refresh_from_db()
            result = play_next_node(t)
        self.assertIsNone(result)

    def test_returns_none_writes_no_match(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(10):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                play_next_node(t)
            match_count = Match.objects.count()
            t.refresh_from_db()
            play_next_node(t)
        # A None return added no new Match.
        self.assertEqual(Match.objects.count(), match_count)


# ---------------------------------------------------------------------------
# TestPlayNextNodeTieBreak
# ---------------------------------------------------------------------------


class TestPlayNextNodeTieBreak(TestCase):
    """A hand-forced true tie (rounds split 1-1, equal totals) drives the
    ``break_tie`` path: on equal best single-Round score the lower Bracket
    seed advances; otherwise the higher best single-Round score advances.

    ``simulate_match`` is patched to return a pre-built tied Match so the
    branch fires deterministically without depending on RNG.
    """

    def _forced_tie_match(
        self,
        team_red: Team,
        team_blue: Team,
        *,
        red_best: int,
        blue_best: int,
    ) -> Match:
        """A completed Match with rounds split 1-1 and equal totals so
        ``calculate_winner`` returns ``None`` (a true tie). ``red_best`` /
        ``blue_best`` set each team's max single-round total.

        red_total = red_best + low ; blue_total = low + blue_best ⇒ totals
        equal iff red_best == blue_best.
        """
        low = 10
        return Match.objects.create(
            team_red=team_red,
            team_blue=team_blue,
            match_type="tournament",
            red_round1_points=red_best,
            blue_round1_points=low,
            red_round2_points=low,
            blue_round2_points=blue_best,
            is_completed=True,
        )

    def test_equal_best_lower_seed_advances(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        node = t.find_next_playable_node()
        lower_seed = min(node.seed_a, node.seed_b)
        lower_team_id = node.team_a_id if node.seed_a == lower_seed else node.team_b_id

        def _fake_simulate_match(sim_self, team_red, team_blue, *args, **kwargs):
            return self._forced_tie_match(
                team_red, team_blue, red_best=500, blue_best=500
            )

        with patch.object(
            BatchSimulator,
            "simulate_match",
            autospec=True,
            side_effect=_fake_simulate_match,
        ):
            resolved = play_next_node(t)

        self.assertIsNotNone(resolved)
        resolved.refresh_from_db()
        self.assertIsNotNone(resolved.winner)
        # Equal best-round-score ⇒ higher Bracket seed (lower seed int) wins.
        self.assertEqual(resolved.winner_id, lower_team_id)

    def test_higher_best_round_score_advances(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4, name="EngHigherBest")
        node = t.find_next_playable_node()
        # team_a is passed as team_red; give team_a the higher best-round score.
        team_a_id = node.team_a_id

        def _fake_simulate_match(sim_self, team_red, team_blue, *args, **kwargs):
            # red: r1=800, r2=100 -> best 800, total 900
            # blue: r1=200, r2=700 -> best 700, total 900
            # 1-1 rounds, equal totals -> winner None.
            return Match.objects.create(
                team_red=team_red,
                team_blue=team_blue,
                match_type="tournament",
                red_round1_points=800,
                blue_round1_points=200,
                red_round2_points=100,
                blue_round2_points=700,
                is_completed=True,
            )

        with patch.object(
            BatchSimulator,
            "simulate_match",
            autospec=True,
            side_effect=_fake_simulate_match,
        ):
            resolved = play_next_node(t)

        self.assertIsNotNone(resolved)
        resolved.refresh_from_db()
        # team_a's best (800) beats team_b's (700) ⇒ team_a advances.
        self.assertEqual(resolved.winner_id, team_a_id)


# ---------------------------------------------------------------------------
# TestPlayNextNodeAtomicity
# ---------------------------------------------------------------------------


class TestPlayNextNodeAtomicity(TestCase):
    """Per-node atomicity — one node = one transaction (``@transaction.atomic``).

    If the parent-advance step raises mid-resolution, the whole node resolution
    rolls back: no winner stamped, no Match leaks into the bracket.
    """

    def test_mid_resolution_raise_rolls_back_node(self) -> None:
        from matches import tournament_engine
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4)
        winners_before = t.nodes.filter(winner__isnull=False).count()

        # Force a failure AFTER the Match sim + winner resolve, during the
        # parent-advance mutation step. Patch the bracket ``advance_winner``
        # the engine calls so it raises.
        def _boom(*args, **kwargs):
            raise RuntimeError("contrived advance failure")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(tournament_engine, "advance_winner", _boom):
                with self.assertRaises(RuntimeError):
                    play_next_node(t)

        t.refresh_from_db()
        # No node gained a winner; the atomic unit rolled back.
        self.assertEqual(t.nodes.filter(winner__isnull=False).count(), winners_before)
        # No SeriesMatch row survived either — the atomic unit rolled back.
        self.assertEqual(t.nodes.filter(series_matches__isnull=False).count(), 0)


# ===========================================================================
# LG-02b — Best-of-N series ``play_next_node``
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified).
# Seam contract: ``.claude/worktrees/lg-02b-seam-contract.md``.
#
# A series node clinches only once a team reaches ``clinch_threshold`` game
# wins; each ``play_next_node`` call resolves ONE game (one SeriesMatch row).
# The sims are RANDOM, so we never assert WHICH team clinches or exact points —
# we drive the node to resolution via repeated calls and assert the clinch
# invariants (winner has the threshold, loser is below it, no dead-rubber game).


def _series_tournament(n: int, series_length: int, *, name: str) -> Tournament:
    """A locked/active Tournament with ALL FOUR depth slots set to
    ``series_length`` (LG-02b-2 migration: the single param sets every slot,
    so ``lock_and_build`` stamps every node's ``series_length`` to that value
    and the per-node clinch reads ``node.series_length``)."""
    t = Tournament.objects.create(
        name=name,
        final_series_length=series_length,
        semifinal_series_length=series_length,
        quarterfinal_series_length=series_length,
        earlier_series_length=series_length,
    )
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


class TestPlayNextNodeSeries(TestCase):
    """Bo3 node: one game per call; node.winner only set at the clinch."""

    def test_first_call_creates_one_series_match_no_winner_yet(self) -> None:
        from matches.bracket import clinch_threshold
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = _series_tournament(4, 3, name="EngBo3First")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        node.refresh_from_db()
        series = SeriesMatch.objects.filter(node=node)
        # Exactly ONE game played so far.
        self.assertEqual(series.count(), 1)
        self.assertEqual(series.get().game_number, 1)
        # 1 win < threshold 2 ⇒ node.winner still None.
        self.assertEqual(clinch_threshold(3), 2)
        self.assertIsNone(node.winner)

    def test_drives_node_to_clinch_invariant(self) -> None:
        from matches.bracket import clinch_threshold
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = _series_tournament(4, 3, name="EngBo3Clinch")
        threshold = clinch_threshold(3)  # 2

        # Resolve the SAME first node repeatedly until it clinches. Each call
        # resolves the lowest playable node; while this node is unclinched it is
        # the lowest, so repeated calls keep playing IT until it clinches.
        target = t.find_next_playable_node()
        target_id = target.id
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):  # Bo3 resolves in at most 3 games; guard loop
                node = t.nodes.get(pk=target_id)
                if node.winner_id is not None:
                    break
                play_next_node(t)

        node = t.nodes.get(pk=target_id)
        self.assertIsNotNone(node.winner, "node must clinch within Bo3 games")

        series = list(SeriesMatch.objects.filter(node=node))
        wins_a = sum(1 for s in series if s.winner_id == node.team_a_id)
        wins_b = sum(1 for s in series if s.winner_id == node.team_b_id)
        winner_wins = max(wins_a, wins_b)
        loser_wins = min(wins_a, wins_b)

        # Clinch invariants:
        self.assertEqual(winner_wins, threshold, "winner has exactly the threshold")
        self.assertLess(loser_wins, threshold, "loser is below the threshold")
        # No dead rubber: total games == winner_wins + loser_wins and never
        # exceeds the series length.
        self.assertEqual(len(series), winner_wins + loser_wins)
        # The series never exceeds the NODE's stamped series_length.
        self.assertLessEqual(len(series), node.series_length)
        # node.winner is the team with >= threshold SeriesMatch wins.
        if wins_a >= threshold:
            self.assertEqual(node.winner_id, node.team_a_id)
        else:
            self.assertEqual(node.winner_id, node.team_b_id)

    def test_total_series_match_rows_between_threshold_and_length(self) -> None:
        from matches.bracket import clinch_threshold
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = _series_tournament(4, 3, name="EngBo3Count")
        threshold = clinch_threshold(3)
        target_id = t.find_next_playable_node().id
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):
                node = t.nodes.get(pk=target_id)
                if node.winner_id is not None:
                    break
                play_next_node(t)
        count = SeriesMatch.objects.filter(node_id=target_id).count()
        self.assertGreaterEqual(count, threshold)
        node = t.nodes.get(pk=target_id)
        self.assertLessEqual(count, node.series_length)

    def test_clinch_advances_winner_into_parent_slot(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _series_tournament(4, 3, name="EngBo3Advance")
        target_id = t.find_next_playable_node().id
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):
                node = t.nodes.get(pk=target_id)
                if node.winner_id is not None:
                    break
                play_next_node(t)
        node = t.nodes.get(pk=target_id)
        parent = node.advances_to
        self.assertIsNotNone(parent)
        parent.refresh_from_db()
        slot_team = getattr(parent, f"team_{node.advances_to_slot}")
        self.assertIsNotNone(slot_team)
        self.assertEqual(slot_team.id, node.winner_id)

    def test_final_clinch_stamps_champion_and_completed(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _series_tournament(4, 3, name="EngBo3Final")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            # Bo3 over a 4-team bracket: up to 3 games × 3 nodes = 9 calls.
            for _ in range(30):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)


class TestPlayNextNodeBo1Equivalence(TestCase):
    """``series_length=1`` ⇒ one game per node clinches immediately + advances
    (the LG-02a single-Match-per-node behaviour)."""

    def test_single_call_clinches_and_advances(self) -> None:
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = _series_tournament(4, 1, name="EngBo1Single")
        target_id = t.find_next_playable_node().id
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_next_node(t)
        node = t.nodes.get(pk=target_id)
        node.refresh_from_db()
        # Exactly one SeriesMatch and the node is immediately resolved.
        self.assertEqual(SeriesMatch.objects.filter(node=node).count(), 1)
        self.assertIsNotNone(node.winner)
        # The winner advanced into the parent slot.
        parent = node.advances_to
        parent.refresh_from_db()
        slot_team = getattr(parent, f"team_{node.advances_to_slot}")
        self.assertEqual(slot_team.id, node.winner_id)

    def test_resolved_tree_shape_after_full_play(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _series_tournament(4, 1, name="EngBo1Tree")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(10):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)
        # Every non-bye node has exactly one SeriesMatch (Bo1) and a winner.
        from matches.models import SeriesMatch

        for node in t.nodes.filter(is_bye=False):
            self.assertIsNotNone(node.winner_id)
            self.assertEqual(SeriesMatch.objects.filter(node=node).count(), 1)


class TestPlayNextNodeSeriesTieBreak(TestCase):
    """A single game within a series that ends a true Match tie resolves via
    ``break_tie`` so the SeriesMatch.winner is non-null.

    Existing ``TestPlayNextNodeTieBreak`` above covers the Bo1 view-level path;
    this asserts the same for one game of a Bo3 (patching ``simulate_match`` to
    return a pre-built tied Match so the seed-based break fires deterministically).
    """

    def _forced_tie_match(self, team_red: Team, team_blue: Team) -> Match:
        # rounds split 1-1, equal totals ⇒ Match.winner is None (true tie).
        return Match.objects.create(
            team_red=team_red,
            team_blue=team_blue,
            match_type="tournament",
            red_round1_points=500,
            blue_round1_points=10,
            red_round2_points=10,
            blue_round2_points=500,
            is_completed=True,
        )

    def test_tied_game_records_non_null_series_match_winner(self) -> None:
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = _series_tournament(4, 3, name="EngBo3Tie")
        node = t.find_next_playable_node()
        lower_seed = min(node.seed_a, node.seed_b)
        lower_team_id = node.team_a_id if node.seed_a == lower_seed else node.team_b_id

        def _fake_simulate_match(sim_self, team_red, team_blue, *args, **kwargs):
            return self._forced_tie_match(team_red, team_blue)

        with patch.object(
            BatchSimulator,
            "simulate_match",
            autospec=True,
            side_effect=_fake_simulate_match,
        ):
            play_next_node(t)

        # The first game of the series recorded a winner chosen by break_tie:
        # equal best single-Round score ⇒ the lower Bracket seed advances.
        game1 = SeriesMatch.objects.get(node=node, game_number=1)
        self.assertIsNotNone(game1.winner_id)
        self.assertEqual(game1.winner_id, lower_team_id)


# ===========================================================================
# LG-02b-2 — engine reads node.series_length (NOT tournament-level)
# ===========================================================================
#
# NEW class appended below (existing classes above are NOT modified).
# Seam contract: ``.claude/worktrees/lg-02b-2-seam-contract.md`` §3 / §6d.
#
# The clinch check moved from ``series_winner_slot(..., node.tournament.
# series_length)`` to ``series_winner_slot(..., node.series_length)``. The
# load-bearing assertion constructs a tournament whose four depth slots DIFFER
# and verifies a DEEP node clinches at ITS OWN depth's N — not any
# tournament-level value (the old flat field is gone, so reading it would
# AttributeError; reading the wrong slot would clinch at the wrong count).


def _escalation_tournament(
    n: int,
    *,
    final: int,
    semifinal: int,
    quarterfinal: int,
    earlier: int,
    name: str,
) -> Tournament:
    """A locked/active Tournament with four DISTINCT depth slots, so each
    Bracket round's nodes stamp a different ``series_length``."""
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


class TestPlayNextNodeReadsNodeSeriesLength(TestCase):
    """The engine clinches each node at ITS OWN stamped ``node.series_length``,
    NOT a tournament-level value."""

    def test_bo1_node_clinches_on_first_match(self) -> None:
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        # final=5 (r3 nodes), but the round-1 quarterfinal slot is Bo1. The
        # FIRST playable node is a round-1 Bo1 node ⇒ it clinches on game 1.
        t = _escalation_tournament(
            8, final=5, semifinal=3, quarterfinal=1, earlier=1, name="EngEscBo1"
        )
        target = t.find_next_playable_node()
        self.assertEqual(target.series_length, 1)
        target_id = target.id
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_next_node(t)
        node = t.nodes.get(pk=target_id)
        # Exactly one SeriesMatch and the Bo1 node clinched immediately.
        self.assertEqual(SeriesMatch.objects.filter(node=node).count(), 1)
        self.assertIsNotNone(node.winner)

    def test_bo3_node_clinches_at_two_match_wins(self) -> None:
        from matches.bracket import clinch_threshold
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        # All round-1 nodes are Bo3 (quarterfinal=3 at N=8). The first playable
        # node clinches at 2 wins, advancing only on clinch.
        t = _escalation_tournament(
            8, final=1, semifinal=1, quarterfinal=3, earlier=1, name="EngEscBo3"
        )
        target = t.find_next_playable_node()
        self.assertEqual(target.series_length, 3)
        target_id = target.id
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):
                node = t.nodes.get(pk=target_id)
                if node.winner_id is not None:
                    break
                play_next_node(t)
        node = t.nodes.get(pk=target_id)
        self.assertIsNotNone(node.winner, "Bo3 node must clinch")
        series = list(SeriesMatch.objects.filter(node=node))
        wins = [
            sum(1 for s in series if s.winner_id == node.team_a_id),
            sum(1 for s in series if s.winner_id == node.team_b_id),
        ]
        # Winner reached exactly the Bo3 clinch threshold (2); no dead rubber.
        self.assertEqual(max(wins), clinch_threshold(3))
        self.assertLessEqual(len(series), node.series_length)

    def test_deep_node_clinches_at_its_own_depth_N(self) -> None:
        # LOAD-BEARING: the four slots DIFFER. Round-1 (quarterfinal) is Bo1 so
        # the round-1 nodes resolve in one game each; round-2 (the FINAL of an
        # N=4 bracket, depth 0) is Bo3. After the two round-1 Bo1 games, the
        # only playable node is the deep final, stamped Bo3 — it must clinch at
        # 2 wins (its OWN depth's N), proving the engine reads node.series_length
        # and not a tournament-level value.
        from matches.bracket import clinch_threshold
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = _escalation_tournament(
            4, final=3, semifinal=1, quarterfinal=1, earlier=1, name="EngEscDeep"
        )
        final_node = t.nodes.get(advances_to__isnull=True)
        self.assertEqual(final_node.series_length, 3)
        # round-1 (semifinal slot at N=4, depth 1) nodes are Bo1.
        for r1 in t.nodes.filter(bracket_round=1):
            self.assertEqual(r1.series_length, 1)

        final_id = final_node.id
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(20):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break

        final_node = t.nodes.get(pk=final_id)
        self.assertIsNotNone(final_node.winner)
        series = list(SeriesMatch.objects.filter(node=final_node))
        wins = [
            sum(1 for s in series if s.winner_id == final_node.team_a_id),
            sum(1 for s in series if s.winner_id == final_node.team_b_id),
        ]
        # The deep Bo3 final clinched at 2 wins — its OWN stamped N, not the
        # round-1 Bo1 value.
        self.assertEqual(max(wins), clinch_threshold(3))
        self.assertLessEqual(len(series), 3)
        self.assertEqual(t.champion_id, final_node.winner_id)


# ===========================================================================
# LG-02c — Double-elimination tournaments (engine)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — the
# single-elim engine tests stay green as regression guards). Seam contract:
# ``.claude/worktrees/lg-02c-seam-contract.md`` §3 (engine) / §6d.
#
# On a WB / GF1 clinch, ``play_next_node`` Advances the winner AND Drops the
# loser into ``loser_advances_to``. The Grand-final Bracket reset has two
# branches: WB-champ-wins-GF1 -> champion immediately + GF2 inert; LB-champ-
# wins-GF1 -> GF2 becomes playable, GF2 winner is champion.
#
# To force deterministic GF outcomes WITHOUT asserting point totals, we patch
# ``simulate_match`` to return a Match whose ``winner`` is a chosen Team (the
# project idiom from ``TestPlayNextNodeForcedTie`` — a pre-built Match returned
# by the patched bound method). A Bo1 DE field (all four slots Bo1) clinches
# every node in one Match so a single ``simulate_match`` controls each node.


def _de_active_tournament(n: int, *, name: str):
    """A locked/active DOUBLE-elim Tournament (all slots Bo1) with its two-tree
    bracket built."""
    t = Tournament.objects.create(name=name, format="double_elimination")
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


def _winner_match(team_red: Team, team_blue: Team, winner: Team) -> Match:
    """A completed Match decided in favour of ``winner`` (red sweeps both rounds
    if winner is team_red, else blue sweeps) so ``Match.winner`` is non-null and
    no tie-break fires."""
    if winner.id == team_red.id:
        red_r1, blue_r1, red_r2, blue_r2 = 500, 10, 500, 10
    else:
        red_r1, blue_r1, red_r2, blue_r2 = 10, 500, 10, 500
    return Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        match_type="tournament",
        red_round1_points=red_r1,
        blue_round1_points=blue_r1,
        red_round2_points=red_r2,
        blue_round2_points=blue_r2,
        is_completed=True,
    )


class TestPlayNextNodeDoubleElimDrop(TestCase):
    """Clinching a WB node Advances the winner into its WB parent slot AND Drops
    the loser into its ``loser_advances_to`` LB slot. Asserted on the resolved
    tree / SeriesMatch rows — NOT on point totals."""

    def test_wb_clinch_drops_loser_into_lb_slot(self) -> None:
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = _de_active_tournament(4, name="DEDropCup")
        node = t.find_next_playable_node()
        self.assertEqual(node.bracket_type, "winners")
        self.assertIsNotNone(node.loser_advances_to_id)
        lb_dest = node.loser_advances_to
        lb_slot = node.loser_advances_to_slot

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            resolved = play_next_node(t)

        resolved.refresh_from_db()
        self.assertEqual(resolved.id, node.id)
        self.assertIsNotNone(resolved.winner)
        # One SeriesMatch recorded (Bo1).
        self.assertEqual(SeriesMatch.objects.filter(node=resolved).count(), 1)
        # The loser is the non-winning slot's team.
        loser_id = (
            resolved.team_b_id
            if resolved.winner_id == resolved.team_a_id
            else resolved.team_a_id
        )
        lb_dest.refresh_from_db()
        dropped_team = getattr(lb_dest, f"team_{lb_slot}")
        self.assertIsNotNone(
            dropped_team, "the WB loser must Drop into the LB destination slot"
        )
        self.assertEqual(dropped_team.id, loser_id)

    def test_wb_clinch_advances_winner_into_wb_parent(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _de_active_tournament(4, name="DEDropAdvance")
        node = t.find_next_playable_node()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_next_node(t)
        node.refresh_from_db()
        parent = node.advances_to
        self.assertIsNotNone(parent)
        parent.refresh_from_db()
        # The winner advanced INSIDE the winners bracket.
        self.assertEqual(parent.bracket_type, "winners")
        slot_team = getattr(parent, f"team_{node.advances_to_slot}")
        self.assertEqual(slot_team.id, node.winner_id)


class TestPlayNextNodeGrandFinalReset(TestCase):
    """The Bracket reset, both branches.

    WB champ wins GF1 -> champion immediately + GF2 inert (never playable).
    LB champ wins GF1 -> GF2 becomes playable, GF2 winner is champion.

    The GF1 Match outcome is forced by patching ``simulate_match`` to favour the
    WB champ (slot a) or the LB champ (slot b)."""

    def _play_to_grand_final(self, t: Tournament) -> Tournament:
        """Play every WB/LB node (random sims) until GF1 is the next playable
        node, returning the tournament with GF1 ready."""
        from matches.tournament_engine import play_next_node

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(60):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                nxt = t.find_next_playable_node()
                if nxt is None:
                    break
                if nxt.bracket_type == "grand_final":
                    break
                play_next_node(t)
        t.refresh_from_db()
        return t

    def test_wb_champ_wins_gf1_completes_immediately(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _de_active_tournament(4, name="DEGFWbWins")
        t = self._play_to_grand_final(t)
        gf1 = t.find_next_playable_node()
        self.assertIsNotNone(gf1)
        self.assertEqual(gf1.bracket_type, "grand_final")
        # GF1 slot "a" is the WB champion; favour it so the WB champ wins GF1.
        wb_champ = gf1.team_a

        def _fake(sim_self, team_red, team_blue, *args, **kwargs):
            return _winner_match(team_red, team_blue, wb_champ)

        with patch.object(
            BatchSimulator, "simulate_match", autospec=True, side_effect=_fake
        ):
            play_next_node(t)

        t.refresh_from_db()
        # WB champ wins GF1 -> champion stamped immediately, tournament complete.
        self.assertEqual(t.state, "completed")
        self.assertEqual(t.champion_id, wb_champ.id)
        # GF2 is inert: find_next_playable_node returns None (never playable).
        self.assertIsNone(t.find_next_playable_node())

    def test_lb_champ_wins_gf1_forces_gf2(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _de_active_tournament(4, name="DEGFLbWins")
        t = self._play_to_grand_final(t)
        gf1 = t.find_next_playable_node()
        self.assertEqual(gf1.bracket_type, "grand_final")
        # GF1 slot "b" is the LB champion; favour it so the LB champ wins GF1.
        lb_champ = gf1.team_b

        def _fake_lb(sim_self, team_red, team_blue, *args, **kwargs):
            return _winner_match(team_red, team_blue, lb_champ)

        with patch.object(
            BatchSimulator, "simulate_match", autospec=True, side_effect=_fake_lb
        ):
            play_next_node(t)

        t.refresh_from_db()
        # LB champ wins GF1 -> no champion yet; GF2 becomes playable.
        self.assertIsNone(t.champion_id)
        self.assertNotEqual(t.state, "completed")
        gf2 = t.find_next_playable_node()
        self.assertIsNotNone(gf2, "GF2 must be playable after the LB champ wins GF1")
        self.assertEqual(gf2.bracket_type, "grand_final")
        # Both teams advanced into GF2.
        self.assertIsNotNone(gf2.team_a)
        self.assertIsNotNone(gf2.team_b)

        # Now clinch GF2 -> its winner is the champion.
        gf2_winner = gf2.team_a

        def _fake_gf2(sim_self, team_red, team_blue, *args, **kwargs):
            return _winner_match(team_red, team_blue, gf2_winner)

        with patch.object(
            BatchSimulator, "simulate_match", autospec=True, side_effect=_fake_gf2
        ):
            play_next_node(t)

        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertEqual(t.champion_id, gf2_winner.id)


class TestPlayNextNodeSingleElimUnchanged(TestCase):
    """Regression: a single-elim node clinch advances the winner and does NOT
    Drop a loser (loser eliminated). Bo1 clinch behaviour byte-identical to
    LG-02b."""

    def _se_active_tournament(self, n: int, *, name: str) -> Tournament:
        t = Tournament.objects.create(name=name)  # default single_elimination
        for seed, team in enumerate(_make_teams(n), start=1):
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        t.lock_and_build()
        t.refresh_from_db()
        return t

    def test_single_elim_clinch_advances_winner_no_loser_drop(self) -> None:
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = self._se_active_tournament(4, name="SERegressionCup")
        node = t.find_next_playable_node()
        # Single-elim WB node has no loser destination.
        self.assertIsNone(node.loser_advances_to_id)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            resolved = play_next_node(t)

        resolved.refresh_from_db()
        self.assertIsNotNone(resolved.winner)
        self.assertEqual(SeriesMatch.objects.filter(node=resolved).count(), 1)
        # The winner advanced; no LB exists (single-elim has no losers bracket).
        self.assertFalse(t.nodes.filter(bracket_type="losers").exists())
        parent = resolved.advances_to
        parent.refresh_from_db()
        slot_team = getattr(parent, f"team_{resolved.advances_to_slot}")
        self.assertEqual(slot_team.id, resolved.winner_id)

    def test_single_elim_plays_to_champion(self) -> None:
        from matches.tournament_engine import play_next_node

        t = self._se_active_tournament(4, name="SERegressionChamp")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(10):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)


# ===========================================================================
# LG-02c — Round robin tournament format (engine)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — the
# single/double-elim engine tests stay green as regression guards). Seam
# contract: ``.claude/worktrees/lg-02c-round-robin-seam-contract.md`` §5 / §10.
#
# RR nodes have advances_to=None on EVERY node, so the elim "crown when
# advances_to is None" rule would WRONGLY crown on the FIRST resolved node.
# play_next_node's RR guard (``if tournament.format == "round_robin":`` after
# stamping node.winner) SKIPS the advance/crown block and instead defers
# completion to complete_round_robin_if_finished() after ALL nodes resolve.
#
# The sims are RANDOM — we never assert WHICH team wins or exact points. We
# assert: ONE node resolves per call, NO early crown, and draining to None
# crowns the standings leader + completes with no advance_winner mutation ever
# applied to any node's parent slot (RR nodes have no parent).


def _rr_active_tournament(n: int, *, name: str = "RREngCup") -> Tournament:
    """A locked/active round_robin Tournament with its flat RR nodes built."""
    t = Tournament.objects.create(name=name, format="round_robin")
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


class TestPlayNextNodeRoundRobinNoEarlyCrown(TestCase):
    """The FIRST play_next_node on a locked RR Tournament resolves exactly ONE
    node and does NOT crown a champion or complete the Tournament, despite every
    RR node having advances_to=None (the elim crown-on-None rule is skipped)."""

    def test_first_call_resolves_one_node(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _rr_active_tournament(4, name="RRNoCrownOne")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        self.assertIsNotNone(node)
        node.refresh_from_db()
        self.assertEqual(node.bracket_type, "round_robin")
        # Exactly one node has a SeriesMatch recorded so far.
        played = t.nodes.filter(series_matches__isnull=False).distinct()
        self.assertEqual(played.count(), 1)

    def test_first_call_stamps_one_series_match_and_winner(self) -> None:
        from matches.models import SeriesMatch
        from matches.tournament_engine import play_next_node

        t = _rr_active_tournament(4, name="RRNoCrownSM")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            node = play_next_node(t)
        node.refresh_from_db()
        # RR is Bo1 ⇒ one SeriesMatch clinches the node.
        self.assertEqual(SeriesMatch.objects.filter(node=node).count(), 1)
        self.assertIsNotNone(node.winner)

    def test_first_call_does_not_crown_or_complete(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _rr_active_tournament(4, name="RRNoCrownState")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_next_node(t)
        t.refresh_from_db()
        # No early crown despite advances_to=None on the resolved node.
        self.assertEqual(t.state, "active")
        self.assertIsNone(t.champion)


class TestPlayNextNodeRoundRobinCompletes(TestCase):
    """Draining play_next_node until None resolves every RR node and exactly
    then crowns the standings leader + state='completed'; no advance_winner
    mutation is ever applied (RR nodes have no parent)."""

    def _drain(self, t: Tournament) -> None:
        from matches.tournament_engine import play_next_node

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            # N=4 RR ⇒ 12 nodes; guard the loop generously.
            for _ in range(40):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break

    def test_drains_every_node(self) -> None:
        t = _rr_active_tournament(4, name="RRDrainAll")
        self._drain(t)
        # Every RR node resolved (has a winner).
        self.assertEqual(t.nodes.filter(winner__isnull=True).count(), 0)
        self.assertEqual(
            t.nodes.filter(series_matches__isnull=False).distinct().count(),
            t.nodes.count(),
        )

    def test_crowns_and_completes_after_full_drain(self) -> None:
        t = _rr_active_tournament(4, name="RRDrainComplete")
        self._drain(t)
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)

    def test_champion_is_the_standings_leader(self) -> None:
        t = _rr_active_tournament(4, name="RRDrainLeader")
        self._drain(t)
        t.refresh_from_db()
        leader_team_id = t.round_robin_standings()[0].team_id
        self.assertEqual(t.champion_id, leader_team_id)

    def test_no_advance_mutation_applied_to_any_node(self) -> None:
        # RR nodes never gain a parent or a slot team via advancement: every node
        # keeps advances_to=None and its team_a/team_b are the FIXED lock-time
        # pair (no node was ever filled by an advance_winner mutation).
        t = _rr_active_tournament(4, name="RRDrainNoAdvance")
        # Snapshot the fixed (team_a_id, team_b_id) pairs before play.
        before = {
            n.id: (n.team_a_id, n.team_b_id)
            for n in t.nodes.order_by("bracket_round", "position")
        }
        self._drain(t)
        for node in t.nodes.all():
            self.assertIsNone(node.advances_to_id)
            self.assertIsNone(node.loser_advances_to_id)
            # The slots are unchanged from lock time — nothing advanced into them.
            self.assertEqual((node.team_a_id, node.team_b_id), before[node.id])

    def test_next_playable_is_none_when_complete(self) -> None:
        t = _rr_active_tournament(4, name="RRDrainNoNext")
        self._drain(t)
        t.refresh_from_db()
        self.assertIsNone(t.find_next_playable_node())


# ===========================================================================
# LG-02c — RR -> Double-elimination tournament format (engine)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — the
# single/double-elim/round-robin engine tests stay green as regression guards).
# Seam contract: ``.claude/worktrees/lg-02c-rr-de-seam-contract.md``
# §"Engine spec" / §"Test boundary".
#
# In an RRDE tournament every RR (Seeding) node has advances_to=None, so the
# elim "crown when advances_to is None" rule would WRONGLY crown / complete on a
# resolved RR node. play_next_node's RR guard rekeys on
# ``node.bracket_type == "round_robin"`` and dispatches on format:
# round_robin_double_elim -> tournament.build_de_finals_if_rr_finished(), then
# returns the node WITHOUT falling through to the elim advance/drop/crown block.
# When the LAST RR node resolves, the deferred finals materialize (WB/LB/GF now
# exist) while state stays "active"; subsequent play_next_node calls drain the
# DE finals normally and the Grand final crowns the champion.
#
# The sims are RANDOM — we never assert WHICH team wins or exact points. We
# assert structure: the deferred build fires, a resolved RR node never gets an
# advance_winner mutation, and draining the finals crowns a champion +
# state="completed".


def _rrde_active_tournament(n: int, *, wb: int, lb: int, name: str) -> Tournament:
    """A locked/active round_robin_double_elim Tournament — only the
    round_robin Seeding nodes are built at lock; the DE finals are deferred."""
    t = Tournament.objects.create(
        name=name,
        format="round_robin_double_elim",
        wb_advancers=wb,
        lb_advancers=lb,
    )
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


class TestPlayNextNodeRrDeDeferredBuild(TestCase):
    """Resolving the LAST RR node of an RRDE tournament triggers the deferred
    finals build: WB/LB/GF nodes now exist, state stays "active", no champion."""

    def _resolve_all_but_last_rr_node(self, t: Tournament) -> None:
        from matches.tournament_engine import play_next_node

        rr_total = t.nodes.filter(bracket_type="round_robin").count()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(rr_total - 1):
                play_next_node(t)

    def test_no_finals_before_last_rr_node_resolves(self) -> None:
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEEngBefore")
        self._resolve_all_but_last_rr_node(t)
        t.refresh_from_db()
        # One RR node still unresolved -> no finals built yet.
        self.assertFalse(t.nodes.exclude(bracket_type="round_robin").exists())
        self.assertEqual(t.state, "active")

    def test_last_rr_node_triggers_finals_build(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEEngTrigger")
        self._resolve_all_but_last_rr_node(t)
        # Resolve the final RR node -> deferred build fires.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_next_node(t)
        t.refresh_from_db()
        self.assertTrue(
            t.nodes.exclude(bracket_type="round_robin").exists(),
            "the DE finals must materialize once the last RR node resolves",
        )
        # The tournament stays active; the champion is crowned later by the GF.
        self.assertEqual(t.state, "active")
        self.assertIsNone(t.champion)

    def test_finals_have_grand_final_after_build(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEEngGF")
        rr_total = t.nodes.filter(bracket_type="round_robin").count()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(rr_total):
                play_next_node(t)
        t.refresh_from_db()
        self.assertEqual(t.nodes.filter(bracket_type="grand_final").count(), 2)

    def test_resolved_rr_node_never_advances_a_parent(self) -> None:
        # A resolved RR (seeding) node never receives an advance_winner mutation:
        # it has advances_to=None and its team slots are the FIXED lock-time pair.
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEEngNoAdvance")
        before = {
            n.id: (n.team_a_id, n.team_b_id)
            for n in t.nodes.filter(bracket_type="round_robin")
        }
        from matches.tournament_engine import play_next_node

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(t.nodes.filter(bracket_type="round_robin").count()):
                play_next_node(t)
        for node in t.nodes.filter(bracket_type="round_robin"):
            self.assertIsNone(node.advances_to_id)
            self.assertEqual((node.team_a_id, node.team_b_id), before[node.id])


class TestPlayNextNodeRrDeDrainsToChampion(TestCase):
    """Draining play_next_node through the Seeding RR stage THEN the deferred DE
    finals crowns a champion via the Grand final and flips state='completed'."""

    def _drain(self, t: Tournament) -> None:
        from matches.tournament_engine import play_next_node

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            # N=4 RR (12 nodes) + a small DE finals; guard the loop generously.
            for _ in range(120):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break

    def test_wb4_lb0_drains_to_champion(self) -> None:
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEEngDrain40")
        self._drain(t)
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)

    def test_wb4_lb2_drains_to_champion(self) -> None:
        t = _rrde_active_tournament(6, wb=4, lb=2, name="RRDEEngDrain42")
        self._drain(t)
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)

    def test_champion_is_a_grand_final_winner(self) -> None:
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEEngGFChamp")
        self._drain(t)
        t.refresh_from_db()
        # The champion is the winner of a grand_final node (GF2, or GF1 when the
        # WB champ wins GF1 and GF2 is inert).
        gf_winner_ids = set(
            t.nodes.filter(
                bracket_type="grand_final", winner__isnull=False
            ).values_list("winner_id", flat=True)
        )
        self.assertIn(t.champion_id, gf_winner_ids)

    def test_next_playable_is_none_when_complete(self) -> None:
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEEngNoNext")
        self._drain(t)
        t.refresh_from_db()
        self.assertIsNone(t.find_next_playable_node())
