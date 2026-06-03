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
    """A locked/active best-of-``series_length`` Tournament."""
    t = _setup_tournament(n, name=name)
    t.series_length = series_length
    t.save(update_fields=["series_length"])
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
        self.assertLessEqual(len(series), t.series_length)
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
        self.assertLessEqual(count, t.series_length)

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
