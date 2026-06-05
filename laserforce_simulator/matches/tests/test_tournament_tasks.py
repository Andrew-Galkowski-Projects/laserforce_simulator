"""LG-02a-2 — direct task-level tests for ``play_tournament_task``.

Pinned by ``.claude/worktrees/lg-02a-2-seam-contract.md`` §3 (task signature +
``name="matches.play_tournament"``, body, return shape ``{"completed","total"}``
as STAGE counts, ``update_state`` meta shape, inactive-state early return).

Runs under ``CELERY_TASK_ALWAYS_EAGER = True`` (set by the project
``conftest.py`` via ``LF_CELERY_EAGER=1``) so ``task.apply(args=...)`` executes
synchronously in-process — no Redis required. Mirrors the API-03
``test_batch_tasks.py`` EAGER idiom (``.apply(...)`` + the task registry under
the pinned ``name=`` string for ``update_state`` spying).

``ROUND_TICKS`` is patched small for speed; the REAL ``simulate_match`` seam
(via ``play_next_node``) is exercised — no ``mock.patch`` on it — so signature
drift fails loudly.

These assertions WILL fail until the Code agent lands
``matches/tasks.py::play_tournament_task``; that is expected.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# Small tick window so a played Match round terminates fast.
_FAST_TICKS = 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teams(n: int, prefix: str) -> list:
    return [make_team_with_slots(f"{prefix}{i}")[0] for i in range(n)]


def _active_tournament(n: int, *, name: str, prefix: str):
    """A locked/active Tournament with its bracket built."""
    from matches.models import Tournament, TournamentParticipant

    t = Tournament.objects.create(name=name)
    for seed, team in enumerate(_make_teams(n, prefix), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


# ---------------------------------------------------------------------------
# TestPlayTournamentTaskHappyPath
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPlayTournamentTaskHappyPath:
    """§3 — ``play_tournament_task.apply(args=(tournament_id,))`` under EAGER
    plays an active Tournament to a champion and returns
    ``{"completed": int, "total": int}`` (STAGE counts).
    """

    def test_plays_to_champion(self) -> None:
        from matches.models import Tournament
        from matches.tasks import play_tournament_task

        t = _active_tournament(4, name="TaskHappy", prefix="TtHappy")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert result.state == "SUCCESS", (
            f"expected EagerResult.state=='SUCCESS', got {result.state!r}; "
            f"info={result.info!r}"
        )
        t.refresh_from_db()
        assert (
            t.state == "completed"
        ), f"tournament must be completed after the task; got {t.state!r}"
        assert t.champion_id is not None, "a champion must be stamped"

    def test_return_shape_is_two_stage_counts(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_tournament(4, name="TaskShape", prefix="TtShape")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert result.state == "SUCCESS"
        payload = result.result
        assert isinstance(payload, dict)
        assert set(payload.keys()) == {"completed", "total"}, (
            f"return must carry exactly {{'completed','total'}} stage counts; "
            f"got keys={set(payload.keys())!r}"
        )
        assert isinstance(payload["completed"], int)
        assert isinstance(payload["total"], int)

    def test_final_completed_equals_total_stages(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_tournament(4, name="TaskFinal", prefix="TtFinal")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        payload = result.result
        # N=4 ⇒ 2 Bracket stages; played to completion ⇒ completed == total == 2.
        assert (
            payload["total"] == 2
        ), f"a 4-team bracket has 2 Bracket stages; got total={payload['total']!r}"
        assert payload["completed"] == payload["total"], (
            f"a completed tournament must have completed == total; got "
            f"completed={payload['completed']!r} total={payload['total']!r}"
        )


# ---------------------------------------------------------------------------
# TestPlayTournamentTaskProgress
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPlayTournamentTaskProgress:
    """§3 — under EAGER the task calls ``self.update_state(state='PROGRESS',
    meta={"completed": int, "total": int})`` (STAGE counts, NOT node counts)
    at least once.
    """

    def test_progress_emits_stage_count_meta(self) -> None:
        from laserforce_simulator.celery_app import celery_app
        from matches.tasks import play_tournament_task

        t = _active_tournament(4, name="TaskProgress", prefix="TtProg")

        calls: list[dict] = []

        # The Proxy from ``@shared_task`` is not the bound Task; the real Task
        # lives in the app registry under the pinned ``name=`` string.
        actual_task = celery_app.tasks["matches.play_tournament"]

        def _spy_update_state(*args, **kwargs) -> None:
            calls.append({"args": args, "kwargs": kwargs})
            return None

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(actual_task, "update_state", _spy_update_state):
                result = play_tournament_task.apply(args=(t.id,))

        assert result.state == "SUCCESS", (
            f"task must succeed even with update_state spied; "
            f"state={result.state!r} info={result.info!r}"
        )

        progress_calls = [c for c in calls if c["kwargs"].get("state") == "PROGRESS"]
        assert progress_calls, (
            "play_tournament_task did not emit any state='PROGRESS' "
            f"update_state calls; observed calls={calls!r}"
        )
        for call in progress_calls:
            meta = call["kwargs"].get("meta")
            assert isinstance(
                meta, dict
            ), f"PROGRESS meta must be a dict; got {type(meta).__name__}"
            assert set(meta.keys()) == {"completed", "total"}, (
                f"PROGRESS meta must be exactly {{'completed','total'}} stage "
                f"counts; got {set(meta.keys())!r}"
            )
            assert isinstance(meta["completed"], int)
            assert isinstance(meta["total"], int)
            # Stage counts, not node counts: total never exceeds the 2 stages.
            assert meta["total"] == 2, (
                f"meta['total'] is the STAGE count (2 for N=4), not the node "
                f"count; got {meta['total']!r}"
            )


# ---------------------------------------------------------------------------
# TestPlayTournamentTaskResumable
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPlayTournamentTaskResumable:
    """§3 — resumable after a partial run: a first invocation that plays only
    some nodes leaves the Tournament active; a second invocation finishes the
    rest to a champion.
    """

    def test_second_invocation_finishes_after_partial(self) -> None:
        from matches.models import Tournament
        from matches.tasks import play_tournament_task
        from matches.tournament_engine import play_next_node

        t = _active_tournament(4, name="TaskResume", prefix="TtResume")

        # Manually resolve ONE node (a partial run) so the task picks up the
        # remainder. The Tournament stays active.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_next_node(t)
        t.refresh_from_db()
        assert t.state == "active", "partial run must leave the tournament active"
        played_before = t.nodes.filter(series_matches__isnull=False).distinct().count()
        assert played_before >= 1

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert result.state == "SUCCESS"
        t.refresh_from_db()
        assert t.state == "completed", "task must finish the remaining nodes"
        assert t.champion_id is not None
        # The earlier-resolved node was NOT re-played (its SeriesMatch persists).
        assert (
            t.nodes.filter(series_matches__isnull=False).distinct().count()
            >= played_before
        )


# ---------------------------------------------------------------------------
# TestPlayTournamentTaskInactiveEarlyReturn
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPlayTournamentTaskInactiveEarlyReturn:
    """§3 — an inactive-state Tournament (setup / completed) is an early-return
    no-op: no nodes played, returns the CURRENT stage progress.
    """

    def test_setup_state_is_noop_returns_zero_progress(self) -> None:
        from matches.models import (
            Match,
            Tournament,
            TournamentParticipant,
        )
        from matches.tasks import play_tournament_task

        # A setup-state (unlocked) Tournament has no nodes yet.
        t = Tournament.objects.create(name="TaskSetup")
        for seed, team in enumerate(_make_teams(4, "TtSetup"), start=1):
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        matches_before = Match.objects.count()

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert result.state == "SUCCESS"
        payload = result.result
        assert set(payload.keys()) == {"completed", "total"}
        # No nodes built ⇒ stage_progress over an empty bracket ⇒ (0, 0).
        assert payload == {"completed": 0, "total": 0}, (
            f"setup-state early return must report current (empty) stage "
            f"progress; got {payload!r}"
        )
        t.refresh_from_db()
        assert t.state == "setup", "setup tournament must stay setup"
        assert Match.objects.count() == matches_before, "no Match may be played"

    def test_completed_state_is_noop_returns_final_progress(self) -> None:
        from matches.models import Match
        from matches.tasks import play_tournament_task

        t = _active_tournament(4, name="TaskDone", prefix="TtDone")
        # Play it to completion first.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))
        t.refresh_from_db()
        assert t.state == "completed"
        matches_before = Match.objects.count()

        # Re-invoking on a completed Tournament is a no-op.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert result.state == "SUCCESS"
        payload = result.result
        assert payload == {"completed": 2, "total": 2}, (
            f"completed-state early return must report final stage progress "
            f"(2/2 for N=4); got {payload!r}"
        )
        assert (
            Match.objects.count() == matches_before
        ), "a completed-state re-invocation must play no further Matches"


# ---------------------------------------------------------------------------
# LG-02b — best-of-N series via the Play Tournament task
# ---------------------------------------------------------------------------
#
# NEW class appended below (existing classes above are NOT modified).
# Seam contract: ``.claude/worktrees/lg-02b-seam-contract.md`` §task.


def _active_series_tournament(n: int, series_length: int, *, name: str, prefix: str):
    """A locked/active Tournament with ALL FOUR depth slots set to
    ``series_length`` (LG-02b-2 migration: the single param sets every slot so
    ``lock_and_build`` stamps every node's ``series_length`` to that value)."""
    from matches.models import Tournament, TournamentParticipant

    t = Tournament.objects.create(
        name=name,
        final_series_length=series_length,
        semifinal_series_length=series_length,
        quarterfinal_series_length=series_length,
        earlier_series_length=series_length,
    )
    for seed, team in enumerate(_make_teams(n, prefix), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


@pytest.mark.django_db
class TestPlayTournamentTaskSeries:
    """§task — a Bo3 Tournament plays to a champion via ``play_tournament_task``
    and every decisive node clinches (its winner reaches clinch_threshold
    SeriesMatch wins) before advancing.
    """

    def test_bo3_plays_to_champion(self) -> None:
        from matches.models import Tournament
        from matches.tasks import play_tournament_task

        t = _active_series_tournament(4, 3, name="TaskBo3", prefix="TtBo3")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert (
            result.state == "SUCCESS"
        ), f"expected SUCCESS, got {result.state!r}; info={result.info!r}"
        t.refresh_from_db()
        assert t.state == "completed"
        assert t.champion_id is not None

    def test_bo3_return_shape_is_stage_counts(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_series_tournament(4, 3, name="TaskBo3Shape", prefix="TtBo3S")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        payload = result.result
        assert set(payload.keys()) == {"completed", "total"}
        # N=4 ⇒ 2 Bracket stages regardless of series length.
        assert payload["total"] == 2
        assert payload["completed"] == payload["total"]

    def test_each_decisive_node_clinched_before_advancing(self) -> None:
        from matches.bracket import clinch_threshold
        from matches.models import SeriesMatch
        from matches.tasks import play_tournament_task

        t = _active_series_tournament(4, 3, name="TaskBo3Clinch", prefix="TtBo3C")
        threshold = clinch_threshold(3)  # 2

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))

        t.refresh_from_db()
        # Every node with a stamped winner is a decisive (non-bye) series that
        # clinched: its winner holds exactly clinch_threshold SeriesMatch wins,
        # the loser is below it, and there is no dead-rubber game.
        decisive = t.nodes.filter(winner__isnull=False, is_bye=False)
        assert decisive.exists()
        for node in decisive:
            series = list(SeriesMatch.objects.filter(node=node))
            wins_a = sum(1 for s in series if s.winner_id == node.team_a_id)
            wins_b = sum(1 for s in series if s.winner_id == node.team_b_id)
            winner_wins = wins_a if node.winner_id == node.team_a_id else wins_b
            loser_wins = wins_b if node.winner_id == node.team_a_id else wins_a
            assert winner_wins == threshold, (
                f"node {node.bracket_round}/{node.position} winner has "
                f"{winner_wins} wins, expected clinch_threshold {threshold}"
            )
            assert loser_wins < threshold
            assert len(series) == winner_wins + loser_wins
            # The series never exceeds the NODE's stamped series_length.
            assert len(series) <= node.series_length


# ---------------------------------------------------------------------------
# LG-02c — Double-elimination via the Play Tournament task
# ---------------------------------------------------------------------------
#
# NEW class appended below (existing classes above are NOT modified — the
# single-elim task tests stay green). Seam contract:
# ``.claude/worktrees/lg-02c-seam-contract.md`` §9 (async) / §6e.
#
# ``play_tournament_task`` drains a full DOUBLE-elim field (N=4) to a champion
# under CELERY_TASK_ALWAYS_EAGER; the final {"completed","total"} reflects
# ``stage_progress`` over BOTH brackets + GF (distinct (bracket_type,
# bracket_round) group counts, NOT node counts).


def _active_de_tournament(n: int, *, name: str, prefix: str):
    """A locked/active DOUBLE-elim Tournament (default Bo1 everywhere) with its
    two-tree bracket built."""
    from matches.models import Tournament, TournamentParticipant

    t = Tournament.objects.create(name=name, format="double_elimination")
    for seed, team in enumerate(_make_teams(n, prefix), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


@pytest.mark.django_db
class TestPlayTournamentTaskDoubleElim:
    """§6e — a DE Tournament plays to a champion via ``play_tournament_task``
    and the final stage counts span both brackets + GF."""

    def test_de_plays_to_champion(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_de_tournament(4, name="TaskDE", prefix="TtDE")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert (
            result.state == "SUCCESS"
        ), f"expected SUCCESS, got {result.state!r}; info={result.info!r}"
        t.refresh_from_db()
        assert t.state == "completed"
        assert t.champion_id is not None

    def test_de_return_shape_is_stage_counts(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_de_tournament(4, name="TaskDEShape", prefix="TtDES")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        payload = result.result
        assert set(payload.keys()) == {"completed", "total"}
        assert isinstance(payload["completed"], int)
        assert isinstance(payload["total"], int)

    def test_de_stage_counts_span_both_brackets(self) -> None:
        from matches.bracket import stage_progress
        from matches.models import _node_to_dict
        from matches.tasks import play_tournament_task

        t = _active_de_tournament(4, name="TaskDESpan", prefix="TtDESpan")

        # total = distinct (bracket_type, bracket_round) groups across the WB +
        # LB + GF trees -> strictly more than the WB-only round count (a
        # single-elim 4-team field would report total=2; DE spans more).
        flat = [
            _node_to_dict(n)
            for n in t.nodes.select_related("advances_to").prefetch_related(
                "series_matches"
            )
        ]
        _completed_before, total_groups = stage_progress(flat)
        assert total_groups > 2, (
            "a DE field's stage total must span WB+LB+GF groups (> the 2 WB "
            f"rounds of a single-elim 4-team field); got {total_groups}"
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        payload = result.result
        # A completed DE tournament reports completed == total over all groups.
        assert payload["total"] == total_groups
        assert payload["completed"] == payload["total"]


# ---------------------------------------------------------------------------
# LG-02c — Round robin via the Play Tournament task
# ---------------------------------------------------------------------------
#
# NEW class appended below (existing classes above are NOT modified — the
# single/double-elim task tests stay green). Seam contract:
# ``.claude/worktrees/lg-02c-round-robin-seam-contract.md`` §5 / §10.
#
# ``play_tournament_task`` drains a full round-robin field (N=4) to a champion
# under CELERY_TASK_ALWAYS_EAGER: every RR node resolved, champion stamped,
# state='completed'. The while-loop calls play_next_node once per node (RR is
# Bo1) and completion is decided by complete_round_robin_if_finished once every
# node has resolved — NOT by the elim crown-on-advances_to-None rule.


def _active_rr_tournament(n: int, *, name: str, prefix: str):
    """A locked/active round_robin Tournament with its flat RR nodes built."""
    from matches.models import Tournament, TournamentParticipant

    t = Tournament.objects.create(name=name, format="round_robin")
    for seed, team in enumerate(_make_teams(n, prefix), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


@pytest.mark.django_db
class TestPlayTournamentTaskRoundRobin:
    """§5 — a round_robin Tournament plays to completion via
    ``play_tournament_task`` under EAGER: every node resolved, champion stamped,
    state='completed'."""

    def test_rr_plays_to_completion(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_rr_tournament(4, name="TaskRR", prefix="TtRR")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert (
            result.state == "SUCCESS"
        ), f"expected SUCCESS, got {result.state!r}; info={result.info!r}"
        t.refresh_from_db()
        assert t.state == "completed"
        assert t.champion_id is not None

    def test_rr_every_node_resolved(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_rr_tournament(4, name="TaskRRNodes", prefix="TtRRN")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))

        t.refresh_from_db()
        # N=4 double round-robin ⇒ 12 nodes, all resolved with a winner.
        assert t.nodes.count() == 12
        assert t.nodes.filter(winner__isnull=True).count() == 0

    def test_rr_champion_is_standings_leader(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_rr_tournament(4, name="TaskRRLeader", prefix="TtRRL")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))

        t.refresh_from_db()
        leader_team_id = t.round_robin_standings()[0].team_id
        assert t.champion_id == leader_team_id

    def test_rr_return_shape_is_stage_counts(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_rr_tournament(4, name="TaskRRShape", prefix="TtRRS")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        payload = result.result
        assert set(payload.keys()) == {"completed", "total"}
        assert isinstance(payload["completed"], int)
        assert isinstance(payload["total"], int)
        # A completed RR reports completed == total over its matchday stages.
        assert payload["completed"] == payload["total"]


# ---------------------------------------------------------------------------
# LG-02c — RR -> Double-elimination via the Play Tournament task
# ---------------------------------------------------------------------------
#
# NEW class appended below (existing classes above are NOT modified — the
# single/double-elim/round-robin task tests stay green). Seam contract:
# ``.claude/worktrees/lg-02c-rr-de-seam-contract.md`` §"Engine spec" (Play-All
# progress extends mid-run as the finals materialize) / §"Test boundary".
#
# ``play_tournament_task`` drains a full round_robin_double_elim Tournament
# through BOTH stages — the RR Seeding nodes THEN the auto-built DE finals — to
# a champion + state='completed' under CELERY_TASK_ALWAYS_EAGER. The deferred
# finals build fires when the last RR node resolves, so the WB/LB/GF stage
# groups appear only mid-run; the final {"completed","total"} reports
# stage_progress over the RR groups PLUS the WB/LB/GF groups.


def _active_rrde_tournament(n: int, *, wb: int, lb: int, name: str, prefix: str):
    """A locked/active round_robin_double_elim Tournament — only the RR Seeding
    nodes built at lock; the DE finals are deferred to the last RR node."""
    from matches.models import Tournament, TournamentParticipant

    t = Tournament.objects.create(
        name=name,
        format="round_robin_double_elim",
        wb_advancers=wb,
        lb_advancers=lb,
    )
    for seed, team in enumerate(_make_teams(n, prefix), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


@pytest.mark.django_db
class TestPlayTournamentTaskRrDe:
    """A round_robin_double_elim Tournament plays through BOTH stages to a
    champion via ``play_tournament_task`` under EAGER."""

    def test_rrde_plays_to_completion(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_rrde_tournament(4, wb=4, lb=0, name="TaskRRDE", prefix="TtRRDE")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert (
            result.state == "SUCCESS"
        ), f"expected SUCCESS, got {result.state!r}; info={result.info!r}"
        t.refresh_from_db()
        assert t.state == "completed"
        assert t.champion_id is not None

    def test_rrde_finals_built_during_run(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_rrde_tournament(
            4, wb=4, lb=0, name="TaskRRDEFinals", prefix="TtRRDEF"
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))

        t.refresh_from_db()
        # The deferred DE finals materialized during the run.
        assert t.nodes.exclude(bracket_type="round_robin").exists()
        assert t.nodes.filter(bracket_type="grand_final").count() == 2

    def test_rrde_champion_is_a_grand_final_winner(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_rrde_tournament(
            4, wb=4, lb=0, name="TaskRRDEChamp", prefix="TtRRDEC"
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))

        t.refresh_from_db()
        gf_winner_ids = set(
            t.nodes.filter(
                bracket_type="grand_final", winner__isnull=False
            ).values_list("winner_id", flat=True)
        )
        assert t.champion_id in gf_winner_ids

    def test_rrde_return_shape_is_stage_counts_spanning_both_stages(self) -> None:
        from matches.bracket import stage_progress
        from matches.models import _node_to_dict
        from matches.tasks import play_tournament_task

        t = _active_rrde_tournament(
            4, wb=4, lb=0, name="TaskRRDESpan", prefix="TtRRDES"
        )

        # Before the run only the RR matchday groups exist.
        rr_flat = [
            _node_to_dict(n)
            for n in t.nodes.select_related("advances_to").prefetch_related(
                "series_matches"
            )
        ]
        _c, rr_only_groups = stage_progress(rr_flat)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        payload = result.result
        assert set(payload.keys()) == {"completed", "total"}
        # After the deferred finals materialize, the total spans the RR matchday
        # groups PLUS the WB/LB/GF groups -> strictly more than the RR-only count.
        assert payload["total"] > rr_only_groups, (
            "the final stage total must span the RR groups plus the WB/LB/GF "
            f"finals groups; got total={payload['total']} vs RR-only "
            f"{rr_only_groups}"
        )
        # A completed RRDE reports completed == total over all groups.
        assert payload["completed"] == payload["total"]

    def test_rrde_with_lb_preseeds_plays_to_completion(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_rrde_tournament(6, wb=4, lb=2, name="TaskRRDELb", prefix="TtRRDEL")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert (
            result.state == "SUCCESS"
        ), f"expected SUCCESS, got {result.state!r}; info={result.info!r}"
        t.refresh_from_db()
        assert t.state == "completed"
        assert t.champion_id is not None


# ---------------------------------------------------------------------------
# LG-02c — Swiss via the Play Tournament task
# ---------------------------------------------------------------------------
#
# NEW class appended below (existing classes above are NOT modified — the
# single/double-elim/round-robin/RR->DE task tests stay green). Seam contract:
# ``.claude/worktrees/lg-02c-swiss-seam-contract.md`` (ENGINE + TEST BOUNDARY).
#
# ``play_tournament_task`` drains a FULL swiss Tournament (all rounds) to a
# champion + state='completed' under CELERY_TASK_ALWAYS_EAGER. Each round's
# pairings are deferred — built when the prior round's last node resolves — so
# the while-loop naturally extends as later-round nodes materialize.
# stage_progress reports per-round stage counts (each Swiss round is one
# (bracket_type, bracket_round) group).


def _active_swiss_tournament(n: int, *, swiss_rounds: int, name: str, prefix: str):
    """A locked/active swiss Tournament — only the R1 fold nodes built at lock;
    later rounds are deferred to advance_swiss_if_round_finished()."""
    from matches.models import Tournament, TournamentParticipant

    t = Tournament.objects.create(name=name, format="swiss", swiss_rounds=swiss_rounds)
    for seed, team in enumerate(_make_teams(n, prefix), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


@pytest.mark.django_db
class TestPlayTournamentTaskSwiss:
    """A swiss Tournament plays all rounds to a champion via
    ``play_tournament_task`` under EAGER."""

    def test_swiss_plays_to_completion(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_swiss_tournament(4, swiss_rounds=2, name="TaskSwiss", prefix="TtSw")

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert (
            result.state == "SUCCESS"
        ), f"expected SUCCESS, got {result.state!r}; info={result.info!r}"
        t.refresh_from_db()
        assert t.state == "completed"
        assert t.champion_id is not None

    def test_swiss_all_rounds_built_and_resolved(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_swiss_tournament(
            4, swiss_rounds=2, name="TaskSwissRounds", prefix="TtSwR"
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))

        t.refresh_from_db()
        # Both Swiss rounds materialized and every node has a winner.
        rounds = set(t.nodes.values_list("bracket_round", flat=True))
        assert rounds == {1, 2}
        assert t.nodes.filter(winner__isnull=True).count() == 0

    def test_swiss_champion_is_standings_leader(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_swiss_tournament(
            4, swiss_rounds=2, name="TaskSwissLeader", prefix="TtSwL"
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))

        t.refresh_from_db()
        leader_team_id = t.swiss_standings()[0].team_id
        assert t.champion_id == leader_team_id

    def test_swiss_return_shape_is_stage_counts_per_round(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_swiss_tournament(
            4, swiss_rounds=2, name="TaskSwissShape", prefix="TtSwS"
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        payload = result.result
        assert set(payload.keys()) == {"completed", "total"}
        assert isinstance(payload["completed"], int)
        assert isinstance(payload["total"], int)
        # A completed swiss reports completed == total; both Swiss rounds are
        # distinct (bracket_type, bracket_round) stage groups ⇒ total == 2.
        assert payload["completed"] == payload["total"]
        assert payload["total"] == 2


# ---------------------------------------------------------------------------
# LG-02x-1 — Random Draw player-pool RR->DE via the Play Tournament task
# ---------------------------------------------------------------------------
#
# NEW class appended below (existing classes above are NOT modified). Seam
# contract: ``.claude/worktrees/lg-02x-1-seam-contract.md`` §4 / §7.
#
# A ``team_assembly="random_draw"`` Tournament running the existing RR->DE
# bracket drains to a champion under CELERY_TASK_ALWAYS_EAGER. The per-Round
# role hook (built by ``_build_role_hook`` and passed through ``simulate_match``)
# rewrites the drawn Teams' slot FKs each Round — but tournament sims are
# NON-DETERMINISTIC (fresh per-round seeds + fresh role RNG), so we assert ONLY
# structure: champion stamped + ``state="completed"``. NEVER exact point totals.


def _draw_team_with_entries(tournament, *, prefix: str):
    """Create one ``is_draw_team`` Team (6 borrowed Players) and the 6
    ``TournamentPlayerEntry`` rows linking those players to it, one per tier.

    The drawn Team's slot FKs hold an initial valid assignment (tier order ->
    the 6 role slots); the durable (player, tier, drawn_team) truth lives on the
    entries, which the role hook reads to rebuild each Round's roster.
    """
    from matches.models import TournamentPlayerEntry

    team, players = make_team_with_slots(prefix)
    team.is_draw_team = True
    team.save(update_fields=["is_draw_team"])
    # players maps role keys -> Player; assign each to a distinct tier 1..6.
    for tier, player in enumerate(
        [
            players["commander"],
            players["heavy"],
            players["scout"],
            players["scout_2"],
            players["medic"],
            players["ammo"],
        ],
        start=1,
    ):
        TournamentPlayerEntry.objects.create(
            tournament=tournament, player=player, tier=tier, drawn_team=team
        )
    return team


def _active_random_draw_rrde(n_teams: int, *, wb: int, lb: int, name: str, prefix: str):
    """A locked/active random_draw RR->DE Tournament: ``n_teams`` drawn Teams,
    each with 6 entries, only the RR Seeding nodes built at lock (DE finals
    deferred to the last RR node)."""
    from matches.models import Tournament, TournamentParticipant

    t = Tournament.objects.create(
        name=name,
        format="round_robin_double_elim",
        team_assembly="random_draw",
        role_assignment_mode="random",
        wb_advancers=wb,
        lb_advancers=lb,
    )
    for seed in range(1, n_teams + 1):
        team = _draw_team_with_entries(t, prefix=f"{prefix}{seed}")
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


@pytest.mark.django_db
class TestPlayTournamentTaskRandomDraw:
    """A random_draw RR->DE Tournament drains to a champion via
    ``play_tournament_task`` under EAGER — structure only, never point totals."""

    def test_random_draw_rrde_plays_to_completion(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_random_draw_rrde(
            4, wb=4, lb=0, name="TaskDrawRRDE", prefix="TtDraw"
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert (
            result.state == "SUCCESS"
        ), f"expected SUCCESS, got {result.state!r}; info={result.info!r}"
        t.refresh_from_db()
        assert t.state == "completed"
        assert t.champion_id is not None

    def test_random_draw_rrde_per_tier_mode_plays_to_completion(self) -> None:
        # The per_tier role-assignment mode drains identically — same RR->DE
        # bracket, a different (single bijection) role draw per Round.
        from matches.models import Tournament, TournamentParticipant
        from matches.tasks import play_tournament_task

        t = Tournament.objects.create(
            name="TaskDrawPerTier",
            format="round_robin_double_elim",
            team_assembly="random_draw",
            role_assignment_mode="per_tier",
            wb_advancers=4,
            lb_advancers=0,
        )
        for seed in range(1, 5):
            team = _draw_team_with_entries(t, prefix=f"TtPerTier{seed}")
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        t.lock_and_build()

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_tournament_task.apply(args=(t.id,))

        assert (
            result.state == "SUCCESS"
        ), f"expected SUCCESS, got {result.state!r}; info={result.info!r}"
        t.refresh_from_db()
        assert t.state == "completed"
        assert t.champion_id is not None

    def test_random_draw_rrde_champion_is_a_grand_final_winner(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active_random_draw_rrde(
            4, wb=4, lb=0, name="TaskDrawGFChamp", prefix="TtDrawGF"
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_tournament_task.apply(args=(t.id,))

        t.refresh_from_db()
        gf_winner_ids = set(
            t.nodes.filter(
                bracket_type="grand_final", winner__isnull=False
            ).values_list("winner_id", flat=True)
        )
        assert t.champion_id in gf_winner_ids
