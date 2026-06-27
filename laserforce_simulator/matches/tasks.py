import multiprocessing
import os
import random
from typing import TYPE_CHECKING

from celery import shared_task

from teams.models import Team

from .simulation import BatchSimulator

if TYPE_CHECKING:
    from core.models import ArenaMap

    from .models import Season
    from .schedule_generator import ScheduleFixture


def _resolve_fixture_map(
    season: "Season",
    fixture: "ScheduleFixture",
    pool_by_id: "dict[int, ArenaMap]",
) -> "ArenaMap | None":
    """LG-01j — per-fixture map resolver (pure, no Django ORM access).

    Reads ``season.map_mode`` and ``season.starting_map_pool_ids_json``
    (the frozen-at-activation snapshot) plus ``fixture``'s identity
    fields, and returns the resolved :class:`~core.models.ArenaMap`
    (or ``None`` for the 3-zone fallback).

    Locked algorithm:
        * ``mode == "none"`` ⇒ ``None`` (LG-01d 3-zone fallback).
        * ``mode == "single"`` ⇒ first id of the snapshot, looked up
          in ``pool_by_id``; ``None`` when the snapshot is empty or the
          row was deleted after activation.
        * ``mode == "random_per_round"`` ⇒ ``random.Random(seed_str)
          .choice(pool_ids)`` where ``seed_str = f"{season.id}|{
          fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{
          fixture.team_b_id}"`` — deterministic by fixture identity,
          replay-faithful, and isolated from the simulator's own RNG.
          ``None`` when the snapshot is empty or the chosen row was
          deleted.
        * Any other value ⇒ ``ValueError(f"Unknown map_mode: {mode!r}")``.

    The caller resolves ``pool_by_id`` once per call site via a single
    ``ArenaMap.objects.in_bulk(pool_ids)``; this helper itself touches
    no ORM and is unit-testable with hand-built dataclass stubs.
    """
    mode = season.map_mode
    if mode == "none":
        return None
    if mode == "single":
        pool_ids = season.starting_map_pool_ids_json or []
        if not pool_ids:
            return None
        chosen_id = pool_ids[0]
        return pool_by_id.get(chosen_id)
    if mode == "random_per_round":
        pool_ids = season.starting_map_pool_ids_json or []
        if not pool_ids:
            return None
        seed_str = (
            f"{season.id}|{fixture.matchday}|{fixture.round_number}|"
            f"{fixture.team_a_id}|{fixture.team_b_id}"
        )
        rng = random.Random(seed_str)
        chosen_id = rng.choice(pool_ids)
        return pool_by_id.get(chosen_id)
    if mode == "rotate_by_matchday":
        ids = season.starting_map_rotation_ids_json or []
        if not ids:
            return None
        return pool_by_id.get(ids[fixture.matchday % len(ids)])
    raise ValueError(f"Unknown map_mode: {mode!r}")


def _play_cancel_requested(season_id: int) -> bool:
    """PLAY-01 — cooperative cancel check (a single-column EXISTS query).

    Returns ``True`` when ``Season.play_cancel_requested`` is set for the
    given Season. Read by ``play_season_task`` / ``play_playoffs_task`` at
    their top AND between fixtures; the task never WRITES the flag.
    """
    from matches.models import Season

    return Season.objects.filter(id=season_id, play_cancel_requested=True).exists()


def _resolve_arena_map(arena_map_id: int | None):
    """Resolve an arena_map_id to an ArenaMap or None.

    A stale id (deleted between POST and task start) is treated as None,
    mirroring the retired SIM-10 ``_run_batch_job`` / ``_run_save_job``
    semantics.
    """
    if arena_map_id is None:
        return None
    from core.models import ArenaMap

    try:
        return ArenaMap.objects.get(id=arena_map_id)
    except ArenaMap.DoesNotExist:
        return None


def _workers_for(n: int) -> int:
    """Intra-task worker count for a single batch run (revived SIM-11 heuristic).

    Returns ``1`` for ``n < 50`` (ProcessPoolExecutor spawn cost dominates a
    small batch) and ``min(os.cpu_count() or 1, n)`` otherwise — i.e. **all
    cores**, bounded only by the game count (never spawn more workers than
    games). GEN-01 originally re-capped this at 4 (SIM-11's CI-box bound), but
    the ``n < 50`` gate already keeps every test/CI batch serial, so the cap
    only ever throttled real user runs; it is removed. API-03 retired this
    helper when it set ``workers=1`` and pushed scaling to Celery
    ``--concurrency``; that only parallelises *across* tasks (multiple runs),
    so a single "simulate N" click stayed single-core. The revival is
    daemon-guarded by :func:`_batch_workers`, so one run uses multiple cores.
    """
    return 1 if n < 50 else min(os.cpu_count() or 1, n)


def _batch_workers(n: int) -> int:
    """Production-safe worker count for ``simulate_batch_task``.

    A Celery **prefork** worker runs each task in a *daemonic* child process,
    and a daemonic process may not spawn its own children (Python raises
    ``AssertionError: daemonic processes are not allowed to have children``),
    so a ``ProcessPoolExecutor`` inside such a task would crash. Guard on
    ``multiprocessing.current_process().daemon``: parallelise only when we are
    NOT daemonic — i.e. EAGER mode (the task runs in the Django process) or a
    ``solo``/``threads`` Celery pool. Under prefork the task stays serial
    (``workers=1``, unchanged from API-03) and horizontal scaling remains the
    ``--concurrency`` knob across workers.
    """
    if multiprocessing.current_process().daemon:
        return 1
    return _workers_for(n)


@shared_task(bind=True, name="matches.simulate_batch")
def simulate_batch_task(
    self,
    team_red_id: int,
    team_blue_id: int,
    n: int,
    arena_map_id: int | None = None,
    master_seed: int | None = None,
) -> dict:
    """Drive ``BatchSimulator.run_incremental``, emit each snapshot via
    ``self.update_state(state="PROGRESS", meta=snap)``, return the final
    aggregate dict on success.
    """
    import django.db

    try:
        team_red = Team.objects.get(id=team_red_id)
        team_blue = Team.objects.get(id=team_blue_id)
        arena_map = _resolve_arena_map(arena_map_id)

        last_snap: dict | None = None
        for snap in BatchSimulator().run_incremental(
            team_red,
            team_blue,
            n,
            arena_map=arena_map,
            workers=_batch_workers(n),
            master_seed=master_seed,
        ):
            self.update_state(state="PROGRESS", meta=snap)
            last_snap = snap

        if last_snap is None:
            return {}
        return last_snap["aggregate"]
    finally:
        django.db.close_old_connections()


@shared_task(bind=True, name="matches.save_games")
def save_games_task(
    self,
    team_red_id: int,
    team_blue_id: int,
    seeds: list,
    n: int,
    arena_map_id: int | None = None,
) -> dict:
    """Replay carried ``(seed, flipped)`` pairs through
    ``BatchSimulator.save_games``, return ``{"round_ids": [...]}``.
    """
    import django.db

    try:
        team_red = Team.objects.get(id=team_red_id)
        team_blue = Team.objects.get(id=team_blue_id)
        arena_map = _resolve_arena_map(arena_map_id)
        game_rounds = BatchSimulator().save_games(
            team_red, team_blue, seeds, n, arena_map=arena_map
        )
        return {"round_ids": [gr.id for gr in game_rounds]}
    finally:
        django.db.close_old_connections()


@shared_task(bind=True, name="matches.play_season")
def play_season_task(
    self,
    season_id: int,
    max_matchdays: int | None = None,
) -> dict:
    """LG-01d — Drive the Play Two Months / Play Until End async run.

    Loads the Season, materialises fixtures via
    ``Season.scheduled_fixtures()``,
    builds ``played_keys`` from persisted ``GameRound``s, calls the pure
    ``select_play_fixtures(fixtures, played_keys, max_matchdays)`` to
    decide which Rounds to play, then loops calling
    ``BatchSimulator().simulate_scheduled_round(...)`` per fixture with
    a ``self.update_state(state="PROGRESS", meta={"completed": k+1,
    "total": n})`` after each Round.

    Per-Round commits — the task body has NO outer ``@transaction.atomic``
    wrapper. ``simulate_scheduled_round`` is already
    ``@transaction.atomic`` so each Round is its own transactional unit.
    A mid-loop exception propagates (Celery records ``FAILURE``); every
    completed Round survives because it was its own atomic commit. See
    ADR-0016 for the load-bearing decision.

    Returns:
        ``{"completed": n, "total": n}`` on success (``n = len(to_play)``).
    """
    import django.db

    try:
        from core.models import ArenaMap
        from matches.league_views import (
            resolve_injuries_for_fixture,
            restore_after_fixture,
        )
        from matches.models import GameRound, Season
        from matches.season_dashboard import select_play_fixtures
        from matches.simulation import BatchSimulator
        from teams.models import Team

        season = Season.objects.get(id=season_id)

        # PLAY-01 — top (queued) cancel check, BEFORE the loop. Stop cleanly
        # and return NORMALLY (Celery SUCCESS ⇒ "complete") with a partial
        # {completed, total, cancelled}.
        if _play_cancel_requested(season_id):
            return {"completed": 0, "total": 0, "cancelled": True}

        # LG-02-Part2c-2 — by-phase fixtures (global-continuous matchday
        # offset already applied) + phase-aware played_keys.
        # LG-02-Part2c-3c — the barrier-aware variant halts the RR loop at an
        # incomplete tournament phase so a mid-season bracket drains first.
        by_phase = season.playable_fixtures_by_phase()
        phase_by_id = {phase.id: phase for phase, _ in by_phase}
        fixtures = [
            (phase.id, fixture)
            for phase, phase_fixtures in by_phase
            for fixture in phase_fixtures
        ]
        # LG-02-Part2c-3a — played_keys gain ``leg`` so a double_round_robin
        # phase's two legs are distinct.
        played_keys = {
            (
                gr.match.season_phase_id,
                frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
                gr.round_number,
                gr.match.leg,
            )
            for gr in GameRound.objects.filter(match__season=season).select_related(
                "match"
            )
        }
        to_play = select_play_fixtures(fixtures, played_keys, max_matchdays)
        n = len(to_play)

        if n:
            team_ids = {f.team_a_id for _pid, f in to_play} | {
                f.team_b_id for _pid, f in to_play
            }
            team_by_id = Team.objects.in_bulk(team_ids)
            # LG-01j — bulk-load the frozen-snapshot map pool ONCE outside
            # the per-fixture loop (single ORM query regardless of
            # ``len(to_play)``). ``in_bulk`` on an empty list is a no-op
            # returning an empty dict.
            # SUB-01 — UNION of the pool snapshot and the rotation snapshot so
            # ``rotate_by_matchday`` resolves its maps from the same bulk-load.
            pool_by_id: dict[int, ArenaMap] = ArenaMap.objects.in_bulk(
                (season.starting_map_pool_ids_json or [])
                + (season.starting_map_rotation_ids_json or [])
            )

            for k, (phase_id, fixture) in enumerate(to_play):
                # PLAY-01 — between-fixtures (running) cancel check, BEFORE
                # simulating this Round. Break + return NORMALLY with the
                # k Rounds committed so far; already-played Rounds stay
                # committed, the Season stays active, the run is resumable.
                if _play_cancel_requested(season_id):
                    return {"completed": k, "total": n, "cancelled": True}

                team_a = team_by_id[fixture.team_a_id]
                team_b = team_by_id[fixture.team_b_id]
                # LG-01j — resolve the per-Round arena_map via the locked
                # algorithm (3-zone for ``none``, fixed map for ``single``,
                # deterministic per-fixture draw for ``random_per_round``).
                arena_map = _resolve_fixture_map(season, fixture, pool_by_id)
                # FIN-04 — roll injuries / resolve rosters in memory before the
                # round sims, then restore the temporary roster afterwards.
                token = resolve_injuries_for_fixture(season, team_a, team_b)
                try:
                    BatchSimulator().simulate_scheduled_round(
                        season,
                        team_a,
                        team_b,
                        fixture.round_number,
                        arena_map=arena_map,
                        season_phase=phase_by_id.get(phase_id),
                        leg=fixture.leg,
                    )
                finally:
                    restore_after_fixture(token)
                self.update_state(
                    state="PROGRESS",
                    meta={"completed": k + 1, "total": n},
                )

        # LG-02-Part2c-3f — phase-aware tail. After the RR fixture loop, if
        # the cursor sits on a built+active tournament phase, drain bracket
        # STAGES with the SHARED budget. ``rr_weeks_played`` is the count of
        # distinct matchdays simulated this run (matchday is global-continuous
        # post-Part2c-2, so a bare matchday count IS the distinct-week count).
        # Budget: unbounded when ``max_matchdays is None`` (drain until
        # ``play_next_bracket_round`` returns 0); else
        # ``max(0, max_matchdays - rr_weeks_played)`` stages. PROGRESS + the
        # final return switch to STAGE counts the moment the bracket drain
        # begins (the ``play_playoffs_task`` precedent); the RR-shape return
        # is kept only when no tournament tail runs.
        from matches.bracket import stage_progress
        from matches.models import _node_to_dict
        from matches.tournament_engine import play_next_bracket_round

        phase = season.current_phase()
        if (
            phase is not None
            and phase.phase_type == "tournament"
            and phase.tournament_id is not None
        ):
            tournament = phase.tournament

            def _stage_counts() -> tuple[int, int]:
                flat = [
                    _node_to_dict(node)
                    for node in tournament.nodes.select_related(
                        "advances_to", "tournament"
                    ).prefetch_related("series_matches")
                ]
                return stage_progress(flat)

            rr_weeks_played = len({fixture.matchday for _pid, fixture in to_play})

            if max_matchdays is None:
                while play_next_bracket_round(tournament) > 0:
                    completed, total = _stage_counts()
                    self.update_state(
                        state="PROGRESS",
                        meta={"completed": completed, "total": total},
                    )
            else:
                bracket_budget = max(0, max_matchdays - rr_weeks_played)
                for _ in range(bracket_budget):
                    if play_next_bracket_round(tournament) == 0:
                        break
                    completed, total = _stage_counts()
                    self.update_state(
                        state="PROGRESS",
                        meta={"completed": completed, "total": total},
                    )

            season.complete_if_finished()
            completed, total = _stage_counts()
            return {"completed": completed, "total": total}

        return {"completed": n, "total": n}
    finally:
        # PLAY-01 — clear the active-run marker on success / cancel / failure.
        # The cancel flag is NOT cleared here (the next enqueue clears it).
        from matches.models import Season

        Season.objects.filter(id=season_id).update(active_play_job_id=None)
        django.db.close_old_connections()


@shared_task(bind=True, name="matches.play_playoffs")
def play_playoffs_task(self, season_id: int) -> dict:
    """LG-02-Part2c-1 — drain a Season's embedded playoff bracket to a champion.

    Mirrors ``play_tournament_task``: loads the Season, resolves the
    current (tournament) phase's ``Tournament``, then loops
    ``play_next_node(tournament)`` until it returns ``None``, emitting
    STAGE-based progress (``stage_progress``) after each resolved node.
    After draining, calls ``season.complete_if_finished()`` so the Season
    champion is crowned once the final bracket node resolves.

    Per-node commits — NO outer ``@transaction.atomic`` (``play_next_node``
    is already per-node atomic; ADR-0016). Inactive / unbuilt guard returns
    ``{"completed": 0, "total": 0}``.

    Returns:
        ``{"completed": int, "total": int}`` (STAGE counts, NOT node counts).
    """
    import django.db

    try:
        from matches.bracket import stage_progress
        from matches.models import Season, _node_to_dict
        from matches.tournament_engine import play_next_node

        season = Season.objects.get(id=season_id)

        # PLAY-01 — top (queued) cancel check, BEFORE the drain.
        if _play_cancel_requested(season_id):
            return {"completed": 0, "total": 0, "cancelled": True}

        phase = season.current_phase()
        if (
            phase is None
            or phase.phase_type != "tournament"
            or phase.tournament_id is None
        ):
            return {"completed": 0, "total": 0}
        tournament = phase.tournament

        def _stage_counts() -> tuple[int, int]:
            flat = [
                _node_to_dict(n)
                for n in tournament.nodes.select_related(
                    "advances_to", "tournament"
                ).prefetch_related("series_matches")
            ]
            return stage_progress(flat)

        while True:
            # PLAY-01 — between-stage (running) cancel check, BEFORE draining
            # the next bracket node. Stop cleanly with the stages resolved so
            # far; resolved nodes stay committed and the run is resumable.
            if _play_cancel_requested(season_id):
                completed, total = _stage_counts()
                return {"completed": completed, "total": total, "cancelled": True}

            if play_next_node(tournament) is None:
                break
            completed, total = _stage_counts()
            self.update_state(
                state="PROGRESS",
                meta={"completed": completed, "total": total},
            )

        season.complete_if_finished()
        completed, total = _stage_counts()
        return {"completed": completed, "total": total}
    finally:
        # PLAY-01 — clear the active-run marker on success / cancel / failure.
        from matches.models import Season

        Season.objects.filter(id=season_id).update(active_play_job_id=None)
        django.db.close_old_connections()


@shared_task(bind=True, name="matches.play_tournament")
def play_tournament_task(self, tournament_id: int) -> dict:
    """LG-02a-2 — Play every remaining decisive Bracket node to a champion.

    Loads the Tournament, then loops ``play_next_node(tournament)`` until it
    returns ``None``; after each resolved node, recomputes STAGE-based
    progress and emits ``self.update_state(state="PROGRESS",
    meta={"completed", "total"})``. Returns the final stage counts.

    Per-node commits — the task body has NO outer ``@transaction.atomic``
    wrapper. ``play_next_node`` is already ``@transaction.atomic`` so each
    node is its own transactional unit (ADR-0016 per-node-atomic precedent):
    a mid-loop exception propagates as Celery ``FAILURE`` and every node
    already resolved survives because it was its own atomic commit.

    Returns:
        ``{"completed": int, "total": int}`` (STAGE counts, NOT node counts).
    """
    import django.db

    try:
        from matches.bracket import stage_progress
        from matches.models import Tournament, _node_to_dict
        from matches.tournament_engine import play_next_node

        tournament = Tournament.objects.get(id=tournament_id)

        def _stage_counts() -> tuple[int, int]:
            # select_related avoids an N+1 on advances_to / tournament and the
            # prefetch avoids one on series_matches inside _node_to_dict.
            flat = [
                _node_to_dict(n)
                for n in tournament.nodes.select_related(
                    "advances_to", "tournament"
                ).prefetch_related("series_matches")
            ]
            return stage_progress(flat)

        if tournament.state != "active":
            completed, total = _stage_counts()
            return {"completed": completed, "total": total}

        while play_next_node(tournament) is not None:
            completed, total = _stage_counts()
            self.update_state(
                state="PROGRESS",
                meta={"completed": completed, "total": total},
            )

        completed, total = _stage_counts()
        return {"completed": completed, "total": total}
    finally:
        django.db.close_old_connections()
