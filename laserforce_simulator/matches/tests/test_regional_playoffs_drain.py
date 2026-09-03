"""CONF-02 — the three drain callers generalised over N regional brackets.

Seam contract ``.claude/worktrees/conf-02-seam-contract.md`` §4 + §9.4 items
10-14; rationale in
[ADR-0035](../../docs/adr/0035-regional-playoffs-one-tournament-per-conference.md).

The bracket ENGINE is unchanged — ``play_next_bracket_round`` and
``play_next_node`` still take exactly one ``Tournament``. Every generalisation
lives in the three callers, which now resolve their brackets through the one
public seam ``Season.tournaments_for_phase(phase)``:

* ``matches.tasks.play_playoffs_task`` — drains EVERY bracket, aggregating the
  STAGE counts across all N.
* ``matches.tasks.play_season_task`` — its tournament tail spends **one budget
  unit per stage across all N brackets**, not one per bracket.
* ``matches.league_views.play_week`` — one "Play One Week" click advances
  EVERY Conference's bracket by one stage.

That is the parallel-overlay pacing rule of ADR-0035: California and Nevada
play their semifinals in the same week, not one after the other.

Fixtures are shared with ``test_regional_playoffs.py`` (same slice, same
ownership lane) so the two files cannot drift on how a regional Season is
composed. The round-robin is hand-built and deterministic — **no simulation**
is used to reach the build (contract §9.1). The bracket drain itself DOES run
the real engine, because the drain is exactly what these tests exercise; ticks
are patched small, and every assertion is schema-level (node-resolution deltas,
tournament state, champion ids, return-dict keys, status codes) — never a
simulated point total.

NOTE: this file requires the Code agent's ``Tournament`` linkage FKs +
migration ``0058_tournament_regional_linkage`` + the generalised callers to
land. Until then these tests are EXPECTED to fail — the TDD red state.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import BracketNode, Tournament
from matches.simulation import BatchSimulator
from matches.tests.test_regional_playoffs import (
    _built_regional_season,
    _conf_season,
    _flat_season,
    _hand_play_rr,
    _ids,
)

_FAST_TICKS = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolved_node_count(tournament: Tournament) -> int:
    """Bracket nodes carrying a winner (resolved or auto-advanced bye)."""
    return BracketNode.objects.filter(
        tournament=tournament, winner__isnull=False
    ).count()


def _resolved_by_tournament(phase) -> dict[int, int]:
    """``{tournament_id: resolved_node_count}`` across a phase's brackets."""
    return {
        tournament.id: _resolved_node_count(tournament)
        for tournament in phase.regional_tournaments.all()
    }


def _stage_size(tournament: Tournament, bracket_round: int = 1) -> int:
    return BracketNode.objects.filter(
        tournament=tournament, bracket_type="winners", bracket_round=bracket_round
    ).count()


def _built_two_conference_season(prefix: str):
    """A 2-Conference Season whose tournament phase holds TWO built brackets.

    Returns ``(season, conferences, groups, phase, tournaments)`` with
    ``tournaments`` in Conference-ordinal order.
    """
    season, conferences, groups, _rr_phase, phase = _built_regional_season(prefix)
    tournaments = season.tournaments_for_phase(phase)
    assert len(tournaments) == 2, "fixture precondition: two regional brackets"
    return season, conferences, groups, phase, tournaments


def _built_flat_season(prefix: str):
    """The 0-Conference regression shape with its single bracket built."""
    season, teams, rr_phase, phase = _flat_season(prefix)
    _hand_play_rr(season, rr_phase, _ids(teams))
    season.refresh_from_db()
    season.activate_pending_tournament_phase()
    phase.refresh_from_db()
    return season, teams, phase


# ===========================================================================
# 10 + 11. play_playoffs_task — drains every bracket, aggregates the counts
# ===========================================================================


class TestPlayPlayoffsTaskRegional(TestCase):
    """``play_playoffs_task`` on a 2-Conference tournament phase."""

    def test_drains_both_brackets_to_conference_champions(self) -> None:
        from matches.tasks import play_playoffs_task

        season, conferences, groups, phase, _tournaments = _built_two_conference_season(
            "TaskBoth"
        )
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(season.id,))

        for conference, group in zip(conferences, groups):
            tournament = phase.regional_tournaments.get(conference=conference)
            self.assertEqual(tournament.state, "completed")
            self.assertIsNotNone(tournament.champion_id)
            # Each Conference champion comes from its OWN Conference.
            self.assertIn(tournament.champion_id, _ids(group))

    def test_returns_aggregated_stage_counts(self) -> None:
        from matches.tasks import play_playoffs_task

        season, _conferences, _groups, _phase, _tournaments = (
            _built_two_conference_season("TaskCounts")
        )
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(season.id,))
        payload = result.get()

        self.assertIn("completed", payload)
        self.assertIn("total", payload)
        self.assertIsInstance(payload["completed"], int)
        self.assertIsInstance(payload["total"], int)
        # Two 4-team single-elim brackets = 2 stages each ⇒ 4 stages summed.
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["completed"], payload["total"])

    def test_completes_the_season_with_a_null_champion(self) -> None:
        from matches.tasks import play_playoffs_task

        season, _conferences, _groups, _phase, _tournaments = (
            _built_two_conference_season("TaskChamp")
        )
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(season.id,))
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNone(season.champion_team_id)

    def test_guard_returns_zero_counts_when_the_phase_has_no_brackets(self) -> None:
        from matches.tasks import play_playoffs_task

        # RR not played ⇒ the cursor is the RR phase and no bracket exists.
        season, _conferences, _groups, _rr, _phase = _conf_season("TaskNoop", [4, 4])
        result = play_playoffs_task.apply(args=(season.id,))
        self.assertEqual(result.get(), {"completed": 0, "total": 0})
        season.refresh_from_db()
        self.assertEqual(season.state, "active")


# ===========================================================================
# 12. play_week — one click advances EVERY bracket by one stage
# ===========================================================================


class TestPlayWeekRegional(TestCase):
    """POST ``/seasons/<id>/play-week/`` on a 2-Conference tournament phase."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.tournaments,
        ) = _built_two_conference_season("Week")

    def test_one_post_advances_every_bracket_by_one_stage(self) -> None:
        before = _resolved_by_tournament(self.phase)
        stage_sizes = {t.id: _stage_size(t) for t in self.tournaments}
        self.assertEqual(set(stage_sizes.values()), {2})

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_week", args=[self.season.id]))
        self.assertEqual(response.status_code, 302)

        after = _resolved_by_tournament(self.phase)
        for tournament in self.tournaments:
            self.assertEqual(
                after[tournament.id] - before[tournament.id],
                stage_sizes[tournament.id],
                "every Conference's bracket must advance one stage per click",
            )

    def test_one_post_leaves_both_finals_unresolved(self) -> None:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            self.client.post(reverse("play_week", args=[self.season.id]))
        for tournament in self.tournaments:
            final = tournament.nodes.get(advances_to__isnull=True)
            self.assertIsNone(final.winner_id)

    def test_one_post_does_not_complete_the_season_early(self) -> None:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            self.client.post(reverse("play_week", args=[self.season.id]))
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")
        self.assertEqual(self.season.current_phase().id, self.phase.id)

    def test_repeated_posts_drain_both_brackets_to_champions(self) -> None:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):
                self.season.refresh_from_db()
                if self.season.state == "completed":
                    break
                self.client.post(reverse("play_week", args=[self.season.id]))

        self.season.refresh_from_db()
        for tournament in self.phase.regional_tournaments.all():
            self.assertEqual(tournament.state, "completed")
            self.assertIsNotNone(tournament.champion_id)
        self.assertEqual(self.season.state, "completed")
        self.assertIsNone(self.season.champion_team_id)


# ===========================================================================
# 13. play_season_task — one budget unit per stage ACROSS all brackets
# ===========================================================================


class TestPlaySeasonTaskRegionalTail(TestCase):
    """The budgeted / unbounded tournament tail of ``play_season_task``."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.tournaments,
        ) = _built_two_conference_season("Tail")

    def test_budgeted_tail_spends_one_unit_per_stage_across_all_brackets(self) -> None:
        from matches.tasks import play_season_task

        before = _resolved_by_tournament(self.phase)
        stage_sizes = {t.id: _stage_size(t) for t in self.tournaments}

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=1)

        after = _resolved_by_tournament(self.phase)
        for tournament in self.tournaments:
            self.assertEqual(
                after[tournament.id] - before[tournament.id],
                stage_sizes[tournament.id],
                "one budget unit == one stage in EVERY bracket",
            )
        # One unit bought one stage, not one whole bracket.
        for tournament in self.phase.regional_tournaments.all():
            self.assertNotEqual(tournament.state, "completed")
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")

    def test_budgeted_tail_advances_both_brackets_equally(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=1)
        deltas = set(_resolved_by_tournament(self.phase).values())
        # Both brackets sit at the same resolution depth — neither raced ahead.
        self.assertEqual(len(deltas), 1)

    def test_unbounded_tail_drains_every_bracket(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_season_task.delay(self.season.id, max_matchdays=None)

        for conference, group in zip(self.conferences, self.groups):
            tournament = self.phase.regional_tournaments.get(conference=conference)
            self.assertEqual(tournament.state, "completed")
            self.assertIn(tournament.champion_id, _ids(group))

        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertIsNone(self.season.champion_team_id)

        payload = result.get()
        # STAGE counts aggregated across both brackets (2 stages each).
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["completed"], payload["total"])


# ===========================================================================
# 14. The 0-Conference drain regression pin
# ===========================================================================


class TestZeroConferenceDrainRegressionPin(TestCase):
    """The drain path is unchanged for a single Season-wide bracket: the task
    still drains it, still crowns ``Season.champion_team``."""

    def test_play_playoffs_task_drains_the_single_bracket(self) -> None:
        from matches.tasks import play_playoffs_task

        season, _teams, phase = _built_flat_season("ZeroDrain")
        self.assertIsNotNone(phase.tournament_id)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(season.id,))

        phase.refresh_from_db()
        tournament = phase.tournament
        season.refresh_from_db()

        self.assertEqual(tournament.state, "completed")
        self.assertIsNotNone(tournament.champion_id)
        self.assertEqual(season.state, "completed")
        # A 0-Conference Season STILL crowns a Season champion.
        self.assertEqual(season.champion_team_id, tournament.champion_id)

        payload = result.get()
        # One 4-team bracket = 2 stages — the un-aggregated shape of today.
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["completed"], payload["total"])

    def test_play_week_drains_one_stage_of_the_single_bracket(self) -> None:
        season, _teams, phase = _built_flat_season("ZeroWeek")
        tournament = phase.tournament
        before = _resolved_node_count(tournament)
        stage_size = _stage_size(tournament)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_week", args=[season.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(_resolved_node_count(tournament) - before, stage_size)


# ===========================================================================
# CONF-02 review fix - the three UI entry points that guarded on
# ``phase.tournament_id`` directly.
#
# The seam contract's SS4 enumerated only three drain callers. It missed the
# view-layer entry points, which each guarded on ``phase.tournament_id is
# None`` - always true for a regional phase, whose N brackets hang off
# ``regional_tournaments`` instead. Net effect before the fix: a >= 2-Conference
# Season's brackets were drainable ONLY via "Play One Week"; the Play Playoffs
# button returned 409 (making the already-generalised ``play_playoffs_task``
# unreachable) and Play Single Round rendered the no-bracket error. All three
# now read through ``Season.tournaments_for_phase(phase)``.
# ===========================================================================


class TestRegionalUiEntryPointsReachTheBrackets(TestCase):
    """``play_playoffs`` / ``play_single_round`` / the playoff cursor keys."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.tournaments,
        ) = _built_two_conference_season("UiEntry")

    def test_play_playoffs_enqueues_instead_of_409(self) -> None:
        """The regression this fix exists for: the enqueue view used to 409."""
        with patch("matches.league_views.play_playoffs_task.delay") as delay:
            delay.return_value.id = "job-regional-1"
            response = self.client.post(reverse("play_playoffs", args=[self.season.id]))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["season_id"], self.season.id)
        delay.assert_called_once_with(self.season.id)

    def test_play_single_round_resolves_exactly_one_node(self) -> None:
        before = sum(_resolved_by_tournament(self.phase).values())
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(
                reverse("play_single_round", args=[self.season.id])
            )
        self.assertEqual(response.status_code, 302)
        after = sum(_resolved_by_tournament(self.phase).values())
        self.assertEqual(
            after - before,
            1,
            "Play Single Round is a ONE-node step across the whole phase",
        )

    def test_playoff_cursor_keys_report_the_phase_active(self) -> None:
        from matches.league_views import _playoff_cursor_keys

        active, tournament_id, completed, _following, _final = _playoff_cursor_keys(
            self.season
        )
        self.assertTrue(active, "regional phase must drive the playoff controls")
        self.assertFalse(completed)
        # The link target is the first bracket in Conference-ordinal order.
        self.assertEqual(tournament_id, self.tournaments[0].id)

    def test_cursor_reports_completed_only_when_every_bracket_drains(self) -> None:
        from matches.league_views import _playoff_cursor_keys
        from matches.tournament_engine import play_next_bracket_round

        # Drain ONE Conference's bracket fully; the other stays untouched.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):
                if play_next_bracket_round(self.tournaments[0]) == 0:
                    break
        self.tournaments[0].refresh_from_db()
        self.assertEqual(self.tournaments[0].state, "completed")

        _active, _tid, completed, _following, _final = _playoff_cursor_keys(self.season)
        self.assertFalse(
            completed,
            "one drained bracket is not a completed playoff phase - all N must drain",
        )


class TestZeroConferenceUiEntryPointsUnchanged(TestCase):
    """The same three entry points on the 0-Conference shape - byte-identical."""

    def setUp(self) -> None:
        self.season, self.teams, self.phase = _built_flat_season("UiFlat")

    def test_play_playoffs_still_enqueues(self) -> None:
        with patch("matches.league_views.play_playoffs_task.delay") as delay:
            delay.return_value.id = "job-flat-1"
            response = self.client.post(reverse("play_playoffs", args=[self.season.id]))
        self.assertEqual(response.status_code, 202)

    def test_play_single_round_still_resolves_one_node(self) -> None:
        before = _resolved_node_count(self.phase.tournament)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(
                reverse("play_single_round", args=[self.season.id])
            )
        self.assertEqual(response.status_code, 302)
        self.phase.tournament.refresh_from_db()
        self.assertEqual(_resolved_node_count(self.phase.tournament) - before, 1)

    def test_cursor_keys_still_point_at_the_single_embedded_bracket(self) -> None:
        from matches.league_views import _playoff_cursor_keys

        active, tournament_id, _completed, _following, _final = _playoff_cursor_keys(
            self.season
        )
        self.assertTrue(active)
        self.assertEqual(tournament_id, self.phase.tournament_id)

    def test_play_playoffs_409s_when_the_phase_has_no_bracket(self) -> None:
        """The guard still guards - an unbuilt phase is still a 409."""
        season, _teams, _rr_phase, phase = _flat_season("UiUnbuilt")
        self.assertIsNone(phase.tournament_id)
        response = self.client.post(reverse("play_playoffs", args=[season.id]))
        self.assertEqual(response.status_code, 409)
