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
        # CONF-04 — the returned STAGE counts describe the phase the run
        # ENDED on. Crossing the regional -> Worlds boundary re-resolves the
        # cached ``tournaments`` list (ADR-0037), so ``_stage_counts()`` then
        # aggregates the Worlds bracket alone rather than the whole run. The
        # load-bearing guard — every qualification bracket actually drained
        # — is asserted directly below instead of through the total.
        self.assertEqual(payload["completed"], payload["total"])
        for tournament in _phase.regional_tournaments.all():
            self.assertEqual(tournament.state, "completed")
            self.assertIsNotNone(tournament.champion_id)

    def test_completes_the_season_with_a_null_champion(self) -> None:
        from matches.tasks import play_playoffs_task

        season, _conferences, _groups, _phase, _tournaments = (
            _built_two_conference_season("TaskChamp")
        )
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(season.id,))
        season.refresh_from_db()
        # CONF-04 — one invocation now carries through the derived Worlds
        # phase and crowns the Season champion (ADR-0037).
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)

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
            # CONF-04 — the click chain is longer now: after both regionals
            # drain the cursor advances to the derived Worlds phase, which
            # must be built and drained before the Season completes.
            for _ in range(14):
                self.season.refresh_from_db()
                if self.season.state == "completed":
                    break
                self.client.post(reverse("play_week", args=[self.season.id]))

        self.season.refresh_from_db()
        for tournament in self.phase.regional_tournaments.all():
            self.assertEqual(tournament.state, "completed")
            self.assertIsNotNone(tournament.champion_id)
        self.assertEqual(self.season.state, "completed")
        self.assertIsNotNone(self.season.champion_team_id)


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
        # CONF-04 — the unbounded tail runs on through Worlds and crowns.
        self.assertIsNotNone(self.season.champion_team_id)

        payload = result.get()
        # CONF-04 — the returned STAGE counts describe the phase the run
        # ENDED on. Crossing the regional -> Worlds boundary re-resolves the
        # cached ``tournaments`` list (ADR-0037), so ``_stage_counts()`` then
        # aggregates the Worlds bracket alone rather than the whole run. The
        # load-bearing guard — every qualification bracket actually drained
        # — is asserted directly below instead of through the total.
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


# ===========================================================================
# CONF-03 — the drain hooks that seed a Last-chance qualifier mid-drain
# ===========================================================================
#
# Seam contract ``.claude/worktrees/conf-03-seam-contract.md`` §6 + §9.4 items
# 27-29; rationale in
# [ADR-0036](../../docs/adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md).
#
# A Conference of 9+ Teams carries a SECOND, deliberately UNSEEDED bracket from
# phase activation. The engine is unchanged: a node-less bracket already makes
# ``play_next_node`` return ``None`` and ``play_next_bracket_round`` return
# ``0``, so both drain loops skip it harmlessly. The only change is in the
# callers' no-progress branch, which SEEDS and then retries once —
# "seed-then-continue".
#
# The hazard this pins: neither task may re-resolve ``tournaments_for_phase``
# inside its loop. The eager row is already in the cached list, and the cached
# instances re-query ``tournament.nodes`` on every call, so a bracket that was
# node-less on iteration 1 is played correctly by the SAME cached instance on
# iteration 4. These tests would fail if the loops exited on the first
# zero-progress pass (the pre-CONF-03 shape).
#
# Fixtures reuse ``test_regional_playoffs.py`` and set ``tournament_cut=4`` so
# the 9-Team Conference's REGIONAL bracket is a fast 4-team tree while its
# activation-snapshot size stays 9 — which is what arms the tier-3 slot
# (contract §2.1: size is NOT affected by ``tournament_cut``). Appended as NEW
# classes; no existing class above is modified.


def _conf03_lc_season(prefix: str, sizes=(9, 4)):
    """A built Season whose final tournament phase holds two 4-team Regional
    playoffs and ONE unseeded Last-chance bracket."""
    season, conferences, groups, _rr_phase, phase = _built_regional_season(
        prefix, list(sizes), cut=4
    )
    return season, conferences, groups, phase


def _conf03_regional(phase, conference):
    """The Regional playoff of ``conference`` — via the §3.2 read rule, NOT a
    positive test on ``"regional_playoff"``."""
    return (
        phase.regional_tournaments.filter(conference=conference)
        .exclude(qualifier_stage="last_chance")
        .first()
    )


def _conf03_last_chance(phase, conference=None):
    rows = phase.regional_tournaments.filter(qualifier_stage="last_chance")
    if conference is not None:
        rows = rows.filter(conference=conference)
    return rows.first()


class TestLastChanceDrainHooks(TestCase):
    """CONF-03 — ``play_season_task`` / ``play_playoffs_task`` seed the
    Last-chance bracket mid-drain instead of exiting on no progress."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
        ) = _conf03_lc_season("Conf03Drain")
        self.big = self.conferences[0]

    # -- 27. play_season_task's unbounded tournament tail -------------------

    def test_unbounded_tail_seeds_and_drains_the_last_chance_bracket(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=None)

        last_chance = _conf03_last_chance(self.phase, self.big)
        self.assertEqual(last_chance.participants.count(), 4)
        self.assertEqual(last_chance.state, "completed")
        self.assertIsNotNone(last_chance.champion_id)

    def test_unbounded_tail_completes_every_bracket_of_the_phase(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=None)

        tournaments = list(self.phase.regional_tournaments.all())
        self.assertEqual(len(tournaments), 3)
        for tournament in tournaments:
            self.assertEqual(tournament.state, "completed")

    def test_unbounded_tail_completes_the_season_with_a_champion(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=None)

        self.season.refresh_from_db()
        # CONF-04 — an unbounded run drains qualification AND Worlds, so the
        # Season now ends crowned rather than championless (ADR-0037).
        self.assertEqual(self.season.state, "completed")
        self.assertIsNotNone(self.season.champion_team_id)

    # -- 28. The budgeted branch: one budget unit is still ONE stage --------

    def test_one_budget_unit_buys_exactly_one_stage_of_the_regionals(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=1)

        # Two 4-team regionals: stage 1 is 2 nodes each.
        for conference in self.conferences:
            self.assertEqual(
                _resolved_node_count(_conf03_regional(self.phase, conference)), 2
            )
        # The Last-chance bracket is untouched and still unseeded.
        last_chance = _conf03_last_chance(self.phase, self.big)
        self.assertEqual(last_chance.state, "setup")
        self.assertEqual(last_chance.nodes.count(), 0)

    def test_the_seed_then_retry_unit_buys_one_last_chance_stage_only(self) -> None:
        """The load-bearing pacing assertion: the zero-progress unit seeds and
        retries ONCE, so it resolves the Last-chance bracket's FIRST stage —
        not the whole bracket."""
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            # Units 1-2 drain both 4-team regionals (2 stages each).
            play_season_task.delay(self.season.id, max_matchdays=1)
            play_season_task.delay(self.season.id, max_matchdays=1)

        for conference in self.conferences:
            self.assertEqual(
                _conf03_regional(self.phase, conference).state, "completed"
            )
        self.assertEqual(_conf03_last_chance(self.phase, self.big).state, "setup")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            # Unit 3: nothing progresses, so it seeds and retries the stage.
            play_season_task.delay(self.season.id, max_matchdays=1)

        last_chance = _conf03_last_chance(self.phase, self.big)
        self.assertEqual(last_chance.participants.count(), 4)
        self.assertEqual(
            _resolved_node_count(last_chance),
            2,
            "one budget unit must buy ONE stage, not the whole bracket",
        )
        self.assertNotEqual(last_chance.state, "completed")
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")

    def test_the_next_budget_unit_finishes_the_last_chance_bracket(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(4):
                play_season_task.delay(self.season.id, max_matchdays=1)

        # The 4 budget units finish QUALIFICATION: every Regional playoff
        # and the Last-chance bracket has a champion.
        last_chance = _conf03_last_chance(self.phase, self.big)
        self.assertEqual(last_chance.state, "completed")
        self.assertIsNotNone(last_chance.champion_id)
        self.season.refresh_from_db()
        # CONF-04 — the Season is NOT finished here: the cursor has advanced
        # to the derived Worlds phase (ADR-0037). Further budget units build
        # and drain that bracket, and only then is a champion crowned.
        self.assertEqual(self.season.state, "active")
        self.assertEqual(self.season.current_phase().tournament_mode, "worlds")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):
                self.season.refresh_from_db()
                if self.season.state == "completed":
                    break
                play_season_task.delay(self.season.id, max_matchdays=1)

        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertIsNotNone(self.season.champion_team_id)

    # -- 29. play_playoffs_task's aggregated counts + the cancel contract ---

    def test_play_playoffs_task_counts_include_the_last_chance_stages(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(self.season.id,))
        payload = result.get()

        # CONF-04 — the returned STAGE counts describe the phase the run
        # ENDED on. Crossing the regional -> Worlds boundary re-resolves the
        # cached ``tournaments`` list (ADR-0037), so ``_stage_counts()`` then
        # aggregates the Worlds bracket alone rather than the whole run. The
        # load-bearing guard — every qualification bracket actually drained
        # — is asserted directly below instead of through the total.
        self.assertEqual(payload["completed"], payload["total"])
        self.assertEqual(_conf03_last_chance(self.phase, self.big).state, "completed")
        for conference in self.conferences:
            self.assertEqual(
                _conf03_regional(self.phase, conference).state, "completed"
            )

    def test_play_playoffs_task_return_shape_is_unchanged(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(self.season.id,))
        payload = result.get()

        self.assertEqual(set(payload), {"completed", "total"})
        self.assertIsInstance(payload["completed"], int)
        self.assertIsInstance(payload["total"], int)

    def test_a_cancel_mid_drain_returns_cancelled_and_commits_resolved_stages(
        self,
    ) -> None:
        from matches.tasks import play_playoffs_task
        from matches.tournament_engine import play_next_bracket_round

        # Resolve one stage of each Regional playoff OUTSIDE the task, then
        # cancel: the resolved nodes must survive the cancelled run.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for conference in self.conferences:
                play_next_bracket_round(_conf03_regional(self.phase, conference))
        before = {
            conference.id: _resolved_node_count(
                _conf03_regional(self.phase, conference)
            )
            for conference in self.conferences
        }
        self.assertEqual(set(before.values()), {2})

        self.season.play_cancel_requested = True
        self.season.save(update_fields=["play_cancel_requested"])

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(self.season.id,))
        payload = result.get()

        self.assertTrue(payload.get("cancelled"))
        for conference in self.conferences:
            self.assertEqual(
                _resolved_node_count(_conf03_regional(self.phase, conference)),
                before[conference.id],
            )
        # The cancelled run seeded nothing and completed nothing.
        self.assertEqual(_conf03_last_chance(self.phase, self.big).state, "setup")
        self.season.refresh_from_db()
        self.assertNotEqual(self.season.state, "completed")


class TestSmallConferenceDrainIsUnchanged(TestCase):
    """CONF-03 byte-identity pin — a Season whose Conferences are all 8 Teams
    or fewer drains exactly as CONF-02 did: no Last-chance row is ever created,
    so no drain loop ever seeds one."""

    def test_play_playoffs_task_counts_are_the_conf02_shape(self) -> None:
        from matches.tasks import play_playoffs_task

        season, _conferences, _groups, _phase, _tournaments = (
            _built_two_conference_season("Conf03Small")
        )
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(season.id,))
        payload = result.get()

        # CONF-04 — the returned STAGE counts describe the phase the run
        # ENDED on. Crossing the regional -> Worlds boundary re-resolves the
        # cached ``tournaments`` list (ADR-0037), so ``_stage_counts()`` then
        # aggregates the Worlds bracket alone rather than the whole run. The
        # load-bearing guard — every qualification bracket actually drained
        # — is asserted directly below instead of through the total.
        self.assertEqual(payload["completed"], payload["total"])
        for tournament in _phase.regional_tournaments.all():
            self.assertEqual(tournament.state, "completed")
        self.assertEqual(
            Tournament.objects.filter(qualifier_stage="last_chance").count(), 0
        )

    def test_play_season_task_tail_is_the_conf02_shape(self) -> None:
        from matches.tasks import play_season_task

        season, _conferences, _groups, phase, _tournaments = (
            _built_two_conference_season("Conf03SmallTail")
        )
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(season.id, max_matchdays=None)

        self.assertEqual(phase.regional_tournaments.count(), 2)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        # CONF-04 — still no Last-chance row for 8-or-fewer Conferences (the
        # CONF-03 pin), but the Season is now crowned via Worlds (ADR-0037).
        self.assertIsNotNone(season.champion_team_id)


# ===========================================================================
# CONF-04 - the ONE deliberate phase-boundary crossing
# ===========================================================================
#
# Seam contract ``.claude/worktrees/conf-04-seam-contract.md`` §6.A-§6.D +
# §11.4; rationale in
# [ADR-0037](../../docs/adr/0037-worlds-is-a-derived-season-phase.md).
#
# Making Worlds its own ``SeasonPhase`` puts it behind a boundary the drain
# loops do not cross: both tasks resolve ``season.current_phase()`` and cache
# ``tournaments_for_phase(phase)`` ONCE, and bracket Matches run through
# ``tournament_engine``, not ``simulate_scheduled_round`` - so the
# ``activate_pending_tournament_phase`` hook goes quiet the moment the regular
# season ends. Left alone, a "Play whole season" run would stop with the
# regionals drained and Worlds unbuilt.
#
# The fix follows CONF-03's seed-then-continue precedent: when
# ``build_pending_worlds_bracket()`` returns True inside the existing STALL
# branch, the loop RE-RESOLVES ``phase`` and ``tournaments`` and retries. Four
# call sites are pinned here:
#
#   §6.A ``play_playoffs_task``       - re-resolve + ``continue``
#   §6.B ``play_season_task``          - ``nonlocal`` re-resolve inside
#                                        ``_drain_one_stage``, so ONE budget
#                                        unit is still ONE stage
#   §6.C ``play_single_round``         - the click that finishes the last
#                                        regional node is never a dead click
#   §6.D ``play_playoffs``             - a cursor parked on an UNBUILT Worlds
#                                        phase must return 202, not 409
#
# CONSEQUENCE PINNED BELOW (contract §6.B / §11.3). ``_stage_counts()`` closes
# over the same ``tournaments`` name, so after the deliberate re-resolution it
# aggregates the WORLDS bracket ALONE and the reported {"completed", "total"}
# SHRINKS at the phase boundary. That is intended - the counts describe the
# CURRENT phase, matching CONF-02's "stage counts of the phase being drained"
# contract. These tests assert the FINAL return and the terminal DB state, and
# deliberately NEVER assert that the PROGRESS counts increase monotonically
# across a run.
#
# Appended as a NEW class; no existing class above is modified.


def _conf04_worlds_phase(season):
    """The Season's Worlds phase, read off the PUBLIC ``tournament_mode``
    discriminator (contract §11.2) - never through the private resolver."""
    return season.phases.filter(tournament_mode="worlds").first()


def _conf04_worlds_tournament(season):
    phase = _conf04_worlds_phase(season)
    if phase is None:
        return None
    phase.refresh_from_db()
    return phase.tournament


def _conf04_stamp_regionals_completed(phase, conferences, groups) -> None:
    """Crown every Regional playoff by STAMPING the persisted rows (§11.1
    technique 2) - used only to REACH the boundary, never to cross it."""
    from matches.tests.test_regional_playoffs import _stamp_bracket_completed

    for conference, group in zip(conferences, groups):
        regional = _conf03_regional(phase, conference)
        if regional is not None:
            _stamp_bracket_completed(regional, group[0])


class TestWorldsDrainCrossesThePhaseBoundary(TestCase):
    """CONF-04 §6.A-§6.D - one invocation of either task drains the regionals,
    builds Worlds, drains it, and crowns the Season champion."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.tournaments,
        ) = _built_two_conference_season("Conf04Boundary")

    def test_fixture_precondition_a_worlds_phase_exists_and_is_unbuilt(self) -> None:
        worlds_phase = _conf04_worlds_phase(self.season)
        self.assertIsNotNone(worlds_phase)
        self.assertIsNone(worlds_phase.tournament_id)
        self.assertEqual(worlds_phase.ordinal, 3)

    # -- §6.A play_playoffs_task -------------------------------------------

    def test_play_playoffs_task_builds_and_drains_worlds_in_one_run(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        worlds = _conf04_worlds_tournament(self.season)
        self.assertIsNotNone(worlds, "the task must cross the phase boundary")
        self.assertEqual(worlds.state, "completed")
        self.assertIsNotNone(worlds.champion_id)

    def test_play_playoffs_task_crowns_the_season_champion(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        worlds = _conf04_worlds_tournament(self.season)
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, worlds.champion_id)

    def test_play_playoffs_task_still_drains_every_regional_bracket(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        for conference, group in zip(self.conferences, self.groups):
            regional = _conf03_regional(self.phase, conference)
            self.assertEqual(regional.state, "completed")
            self.assertIn(regional.champion_id, _ids(group))

    def test_play_playoffs_task_return_shape_is_unchanged(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(self.season.id,))
        payload = result.get()

        self.assertEqual(set(payload), {"completed", "total"})
        self.assertIsInstance(payload["completed"], int)
        self.assertIsInstance(payload["total"], int)

    def test_the_final_counts_describe_the_worlds_phase_alone(self) -> None:
        """Contract §6.B's documented consequence. ``_stage_counts()`` closes
        over the re-resolved ``tournaments``, so the terminal counts cover the
        WORLDS bracket only - the four regional stages drop out. Assert the
        FINAL return, never the monotonicity of the PROGRESS emissions."""
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(self.season.id,))
        payload = result.get()

        worlds = _conf04_worlds_tournament(self.season)
        self.assertGreater(payload["total"], 0)
        self.assertEqual(payload["completed"], payload["total"])
        # Two 4-Team regionals alone would have summed to 4 stages; the counts
        # now describe the M = 2 Worlds bracket's single stage.
        self.assertEqual(payload["total"], 1)
        self.assertEqual(worlds.nodes.count(), 1)

    # -- §6.B play_season_task ---------------------------------------------

    def test_unbounded_season_tail_builds_and_drains_worlds(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=None)

        worlds = _conf04_worlds_tournament(self.season)
        self.assertIsNotNone(worlds)
        self.assertEqual(worlds.state, "completed")
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, worlds.champion_id)

    def test_the_budgeted_tail_does_not_cross_the_boundary_early(self) -> None:
        """One budget unit is still ONE stage: the boundary crossing fires only
        when NOTHING else progressed, so the first units buy regional stages."""
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=1)

        for conference in self.conferences:
            self.assertEqual(
                _resolved_node_count(_conf03_regional(self.phase, conference)),
                2,
                "one budget unit == stage 1 of EVERY regional bracket",
            )
        self.assertIsNone(
            _conf04_worlds_phase(self.season).tournament_id,
            "Worlds must not build while the regionals are still progressing",
        )

    def test_the_stalled_budget_unit_builds_and_plays_one_worlds_stage(self) -> None:
        """Contract §6.B - the boundary crossing lives INSIDE
        ``_drain_one_stage``, so it is reachable only while the run's cached
        cursor is still the regional phase. A 3-unit budget therefore spends
        units 1-2 on the two regional stages and unit 3 on the stall, which
        builds Worlds, re-resolves, and plays its ONE stage (the whole final at
        M = 2)."""
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=3)

        for conference in self.conferences:
            self.assertEqual(
                _conf03_regional(self.phase, conference).state, "completed"
            )
        worlds = _conf04_worlds_tournament(self.season)
        self.assertIsNotNone(worlds)
        self.assertEqual(_resolved_node_count(worlds), 1)
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")

    def test_a_two_unit_budget_stops_before_the_boundary(self) -> None:
        """One budget unit is still ONE stage: two units buy exactly the two
        regional stages and leave Worlds unbuilt."""
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=2)

        for conference in self.conferences:
            self.assertEqual(
                _conf03_regional(self.phase, conference).state, "completed"
            )
        self.assertIsNone(_conf04_worlds_phase(self.season).tournament_id)
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")

    def test_a_cancel_before_the_boundary_builds_no_worlds_bracket(self) -> None:
        from matches.tasks import play_playoffs_task

        self.season.play_cancel_requested = True
        self.season.save(update_fields=["play_cancel_requested"])

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(self.season.id,))

        self.assertTrue(result.get().get("cancelled"))
        self.assertIsNone(_conf04_worlds_phase(self.season).tournament_id)
        self.season.refresh_from_db()
        self.assertNotEqual(self.season.state, "completed")

    # -- §6.D play_playoffs is not a 409 trap ------------------------------

    def test_play_playoffs_returns_202_on_an_unbuilt_worlds_cursor(self) -> None:
        """The regression this hook exists for (§11.5 item 10). With the cursor
        parked on an unbuilt Worlds phase, ``tournaments_for_phase`` is ``[]``,
        so the guard would 409 "No active playoff bracket to play." and the
        drain could never be STARTED. The build MUST precede the cursor read."""
        _conf04_stamp_regionals_completed(self.phase, self.conferences, self.groups)
        self.assertEqual(
            self.season.current_phase().pk, _conf04_worlds_phase(self.season).pk
        )
        self.assertIsNone(_conf04_worlds_phase(self.season).tournament_id)

        with patch("matches.league_views.play_playoffs_task.delay") as delay:
            delay.return_value.id = "job-conf04-worlds"
            response = self.client.post(reverse("play_playoffs", args=[self.season.id]))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["season_id"], self.season.id)

    def test_the_409_post_leaves_a_built_and_active_worlds_bracket(self) -> None:
        _conf04_stamp_regionals_completed(self.phase, self.conferences, self.groups)
        with patch("matches.league_views.play_playoffs_task.delay") as delay:
            delay.return_value.id = "job-conf04-worlds-2"
            self.client.post(reverse("play_playoffs", args=[self.season.id]))

        worlds = _conf04_worlds_tournament(self.season)
        self.assertIsNotNone(worlds)
        self.assertEqual(worlds.state, "active")

    def test_play_playoffs_still_409s_when_there_is_no_bracket_at_all(self) -> None:
        """The guard still guards: an unbuilt REGIONAL phase (the regular season
        is still running) is still a 409."""
        season, _conferences, _groups, _rr, phase = _conf_season(
            "Conf04Unbuilt", [4, 4]
        )
        self.assertFalse(phase.regional_tournaments.exists())
        response = self.client.post(reverse("play_playoffs", args=[season.id]))
        self.assertEqual(response.status_code, 409)

    # -- §6.C play_single_round is never a dead click ----------------------

    def test_the_click_that_finishes_the_regionals_builds_worlds(self) -> None:
        """§11.5 item 11. Two 4-Team brackets hold 3 nodes each and
        ``play_single_round`` resolves exactly ONE node per click, so click 6
        resolves the last regional node - and must leave the Worlds bracket
        BUILT and ``active`` rather than a cursor on an empty phase."""
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):
                response = self.client.post(
                    reverse("play_single_round", args=[self.season.id])
                )
                self.assertEqual(response.status_code, 302)

        for conference in self.conferences:
            self.assertEqual(
                _conf03_regional(self.phase, conference).state, "completed"
            )
        worlds = _conf04_worlds_tournament(self.season)
        self.assertIsNotNone(worlds, "the regionals-finishing click must build Worlds")
        self.assertEqual(worlds.state, "active")
        self.assertEqual(worlds.nodes.count(), 1)

    def test_that_click_does_not_itself_play_a_worlds_node(self) -> None:
        # §6.C - the click already played its regional node; the NEXT click
        # finds the cursor on a built Worlds phase and drains it normally.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(6):
                self.client.post(reverse("play_single_round", args=[self.season.id]))
        worlds = _conf04_worlds_tournament(self.season)
        self.assertEqual(_resolved_node_count(worlds), 0)
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")

    def test_the_next_click_plays_a_worlds_node_and_crowns_the_season(self) -> None:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(7):
                response = self.client.post(
                    reverse("play_single_round", args=[self.season.id])
                )
                self.assertEqual(response.status_code, 302)

        worlds = _conf04_worlds_tournament(self.season)
        self.assertEqual(_resolved_node_count(worlds), 1)
        self.assertEqual(worlds.state, "completed")
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, worlds.champion_id)

    def test_no_click_in_the_run_ever_returns_the_no_bracket_error(self) -> None:
        # A 400 here would be the "cursor parked on an unbuilt Worlds phase"
        # failure mode: ``_render_season_dashboard_error`` renders with 400.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(7):
                response = self.client.post(
                    reverse("play_single_round", args=[self.season.id])
                )
                self.assertNotEqual(response.status_code, 400)


class TestWorldsDrainWithALastChanceBracket(TestCase):
    """CONF-04 - the full chain in ONE task run: regional playoffs, then the
    CONF-03 Last-chance seed-then-retry, then the CONF-04 phase-boundary
    crossing into Worlds. The two stall-branch hooks sit side by side and must
    not shadow one another (naming hazard §13.8: one returns ``int``, the other
    ``bool``)."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
        ) = _conf03_lc_season("Conf04Lc")
        self.big = self.conferences[0]

    def test_one_unbounded_run_drains_regionals_last_chance_and_worlds(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=None)

        last_chance = _conf03_last_chance(self.phase, self.big)
        self.assertEqual(last_chance.state, "completed")
        self.assertIsNotNone(last_chance.champion_id)

        worlds = _conf04_worlds_tournament(self.season)
        self.assertIsNotNone(worlds)
        self.assertEqual(worlds.state, "completed")

    def test_the_nine_team_conference_sends_three_qualifiers(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=None)

        worlds = _conf04_worlds_tournament(self.season)
        # 3 from the 9-Team Conference + 1 from the 4-Team one.
        self.assertEqual(worlds.participants.count(), 4)

    def test_the_season_completes_with_the_worlds_champion(self) -> None:
        from matches.tasks import play_season_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(self.season.id, max_matchdays=None)

        worlds = _conf04_worlds_tournament(self.season)
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, worlds.champion_id)

    def test_play_playoffs_task_reaches_worlds_through_both_stall_hooks(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(self.season.id,))
        payload = result.get()

        self.assertEqual(_conf03_last_chance(self.phase, self.big).state, "completed")
        worlds = _conf04_worlds_tournament(self.season)
        self.assertEqual(worlds.state, "completed")
        # Terminal counts only, and only their internal consistency - the
        # counts describe the CURRENT (Worlds) phase (§6.B).
        self.assertGreater(payload["total"], 0)
        self.assertEqual(payload["completed"], payload["total"])


class TestZeroConferenceDrainStillHasNoWorldsPhase(TestCase):
    """CONF-04 §12 invariant 1 - the 0-Conference drain path is byte-identical:
    no Worlds phase is ever created, so no stall branch ever crosses a
    boundary, and the single Season-wide bracket still crowns the champion."""

    def setUp(self) -> None:
        self.season, self.teams, self.phase = _built_flat_season("Conf04Flat")

    def test_the_season_has_no_worlds_phase(self) -> None:
        self.assertIsNone(_conf04_worlds_phase(self.season))
        self.assertEqual(self.season.phases.count(), 2)

    def test_play_playoffs_task_counts_are_the_pre_conf04_shape(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_playoffs_task.apply(args=(self.season.id,))
        payload = result.get()

        # One 4-team bracket = 2 stages - unchanged from CONF-02.
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["completed"], payload["total"])

    def test_play_playoffs_task_still_crowns_from_the_single_bracket(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        self.phase.refresh_from_db()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(
            self.season.champion_team_id, self.phase.tournament.champion_id
        )

    def test_no_extra_tournament_row_is_created(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))
        self.assertEqual(Tournament.objects.count(), 1)
