"""ANG-01d-2 -- League play-loop logic shared by the HTML views and ``/api/``.

A Season has at most one **Active play run** (ADR-0040): "live" means
``Season.active_play_job_id`` is non-null. ``enqueue_play_run`` is the only
enqueue path for Season play tasks; it writes the job id BEFORE the task is
enqueued, and each task clears the column only while it still holds its own id.

Must not import ``matches.league_views`` (the views import this module).
Errors are plain ``Exception`` subclasses; the HTML views and the API map them
to their own response shapes.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from celery.result import AsyncResult
from django.db import transaction
from kombu.exceptions import OperationalError

from matches import finance, injury, member_night
from matches.development import STAT_FIELDS
from matches.draw import ROLE_SLOTS
from matches.job_status import celery_state_to_job_status
from matches.league_services import _playoff_cursor_keys, is_career_league
from matches.models import GameRound, League, Match, Season, SeasonPhase, Tournament
from matches.season_dashboard import select_play_fixtures
from matches.simulation import BatchSimulator
from teams.models import Player, Team

if TYPE_CHECKING:
    from celery import Task

    from accounts.models import User
    from matches.models import BracketNode

# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------


class PlayInProgressError(Exception):
    """The Season already has an Active play run (``.job_id``)."""

    def __init__(self, job_id: str) -> None:
        super().__init__("The Season already has a play run in progress.")
        self.job_id = job_id


class SeasonNotActiveError(Exception):
    """A play action on a Season that is not ``active``."""


class SeasonNotDraftError(Exception):
    """Start Season on a Season that is not a draft."""


class WrongPhaseError(Exception):
    """The play action does not apply to the Season's current phase."""


class NoMemberNightGamesError(Exception):
    """The member-night phase has no unplayed game."""


class NoLiveCursorError(Exception):
    """There is no game for the manager's team to play live."""


BROKER_UNAVAILABLE_MESSAGE = (
    "Couldn't start the play run: the background task queue is unavailable. "
    "Start a Celery worker + broker, or run the server with LF_CELERY_EAGER=1 "
    "for local play."
)


class BrokerUnavailableError(Exception):
    """The Celery broker / result backend refused the play-run enqueue."""

    def __init__(self) -> None:
        super().__init__(BROKER_UNAVAILABLE_MESSAGE)


# ---------------------------------------------------------------------------
# Active play run
# ---------------------------------------------------------------------------


def _require_no_live_run(season: Season) -> None:
    """Raise ``PlayInProgressError`` while the Season has an Active play run."""
    current = (
        Season.objects.filter(pk=season.pk)
        .values_list("active_play_job_id", flat=True)
        .first()
    )
    if current:
        raise PlayInProgressError(current)


def enqueue_play_run(season: Season, task: Task, **task_kwargs: Any) -> str:
    """Start ``task`` as the Season's Active play run; return its job id.

    Raises ``PlayInProgressError`` when a run is already live. The job id is
    written (and the cancel flag cleared) BEFORE ``apply_async`` so a fast or
    eager task can never clear the column ahead of the write.
    """
    _require_no_live_run(season)
    job_id = str(uuid.uuid4())
    claimed = Season.objects.filter(
        pk=season.pk, active_play_job_id__isnull=True
    ).update(active_play_job_id=job_id, play_cancel_requested=False)
    if not claimed:
        # Lost a race to another enqueue between the read and the write.
        winner = (
            Season.objects.filter(pk=season.pk)
            .values_list("active_play_job_id", flat=True)
            .first()
        )
        raise PlayInProgressError(winner or "")
    season.active_play_job_id = job_id
    season.play_cancel_requested = False
    try:
        # retry=False: an unreachable broker fails after one bounded attempt
        # (the tournament Play All precedent) instead of hanging the request.
        task.apply_async(
            kwargs={"season_id": season.pk, **task_kwargs},
            task_id=job_id,
            retry=False,
        )
    except Exception as exc:
        # The task never ran (or failed eagerly): release the run if we hold it.
        Season.objects.filter(pk=season.pk, active_play_job_id=job_id).update(
            active_play_job_id=None
        )
        season.active_play_job_id = None
        if _is_broker_failure(task, exc):
            raise BrokerUnavailableError() from exc
        raise
    return job_id


def _ensure_broker_reachable(task: Task) -> None:
    """Raise ``BrokerUnavailableError`` unless ``task``'s broker accepts a
    connection.

    A play path that commits a game before enqueueing (live play) probes
    first, so a down broker fails the request with nothing played rather than
    half a week (L-7). Eager Celery runs tasks in-process and never touches
    the broker, so the probe is a no-op there.
    """
    if task.app.conf.task_always_eager:
        return
    try:
        with task.app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1)
    except OperationalError as exc:
        raise BrokerUnavailableError() from exc


def _is_broker_failure(task: Task, exc: Exception) -> bool:
    """True when ``apply_async`` failed on the broker / result backend.

    ``OperationalError`` is kombu's broker-down error. The Redis result
    backend raises a bare ``RuntimeError`` once its reconnect retries run out;
    that only counts when not eager, because an eager run executes the task
    body inside ``apply_async`` and its own errors must propagate unchanged.
    """
    if isinstance(exc, OperationalError):
        return True
    return isinstance(exc, RuntimeError) and not task.app.conf.task_always_eager


#: Celery states of a job a worker is executing. ``STARTED`` needs
#: ``CELERY_TASK_TRACK_STARTED``; ``PROGRESS`` is the play tasks' custom state.
_RUNNING_JOB_STATES = frozenset({"STARTED", "RETRY", "PROGRESS"})


def request_play_cancel(season: Season) -> bool:
    """Ask the Active play run to stop; ``False`` (and no write) when idle.

    When Celery reports the job is not running (``PENDING`` -- unknown to the
    backend -- or a terminal state), the run is stale: also release the
    column, filtered on that exact id so a newer run's id is never cleared.
    """
    job_id = (
        Season.objects.filter(pk=season.pk)
        .values_list("active_play_job_id", flat=True)
        .first()
    )
    if not job_id:
        return False
    updated = Season.objects.filter(
        pk=season.pk, active_play_job_id__isnull=False
    ).update(play_cancel_requested=True)
    if not updated:
        return False
    season.play_cancel_requested = True
    if AsyncResult(job_id).state not in _RUNNING_JOB_STATES:
        released = Season.objects.filter(
            pk=season.pk, active_play_job_id=job_id
        ).update(active_play_job_id=None)
        if released:
            season.active_play_job_id = None
    return True


def job_status_fields(async_result: AsyncResult) -> dict:
    """``status`` / ``completed`` / ``total`` / ``cancelled`` / ``error`` for a
    Season play Job, mapped from Celery as the LG-01d poll does."""
    state = async_result.state
    completed = 0
    total = 0
    cancelled = False
    error: str | None = None
    if state == "PROGRESS":
        info = async_result.info or {}
        if isinstance(info, dict):
            completed = int(info.get("completed", 0) or 0)
            total = int(info.get("total", 0) or 0)
    elif state == "SUCCESS":
        result = async_result.result or {}
        if isinstance(result, dict):
            completed = int(result.get("completed", 0) or 0)
            total = int(result.get("total", 0) or 0)
            cancelled = bool(result.get("cancelled", False))
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)
    return {
        "status": celery_state_to_job_status(state),
        "completed": completed,
        "total": total,
        "cancelled": cancelled,
        "error": error,
    }


def build_play_job_status(job_id: str) -> dict:
    """Structured progress for a Season play Job (no HTML fragments)."""
    return job_status_fields(AsyncResult(job_id))


# ---------------------------------------------------------------------------
# Async play actions (two months / until end / playoffs)
# ---------------------------------------------------------------------------


def _require_active(season: Season) -> None:
    if season.state != "active":
        raise SeasonNotActiveError(
            f"Season must be active to play; got state={season.state!r}"
        )


def play_season_run(season: Season, *, max_matchdays: int | None) -> str:
    """Start a ``play_season_task`` run (two months = 8, until end = None)."""
    from matches.tasks import play_season_task

    _require_active(season)
    return enqueue_play_run(season, play_season_task, max_matchdays=max_matchdays)


def play_playoffs_run(season: Season) -> str:
    """Start a ``play_playoffs_task`` run over the built tournament phase."""
    from matches.tasks import play_playoffs_task

    _require_active(season)
    # CONF-04 -- build the Worlds bracket BEFORE the cursor read (ADR-0037).
    season.build_pending_worlds_bracket()
    phase = season.current_phase()
    if (
        phase is None
        or phase.phase_type != "tournament"
        or not season.tournaments_for_phase(phase)
    ):
        raise WrongPhaseError("No active playoff bracket to play.")
    return enqueue_play_run(season, play_playoffs_task)


# ---------------------------------------------------------------------------
# Start Season + synchronous play (one week / one bracket round)
# ---------------------------------------------------------------------------


def start_season(season: Season) -> None:
    """Flip a draft Season to active (``Season.start_season``).

    Raises ``SeasonNotDraftError`` unless the Season is a draft: the model
    method would otherwise silently re-snapshot an active Season (or reopen a
    completed one). The guard lives here, not on the model, so model-level
    tests and fixtures stay unaffected. A ``ValidationError`` from the model
    (e.g. fewer than 2 teams) propagates.
    """
    if season.state != "draft":
        raise SeasonNotDraftError(
            f"Only a draft Season can be started; got state={season.state!r}"
        )
    season.start_season()


def play_week(season: Season) -> None:
    """Play One Week: the next matchday, or one bracket stage on a playoff cursor.

    Raises ``SeasonNotActiveError`` / ``PlayInProgressError``. A
    ``ValidationError`` / ``ValueError`` from the simulator propagates after
    the whole matchday rolls back. A Season with nothing left to play is a
    no-op.
    """
    _require_active(season)
    _require_no_live_run(season)

    # LG-02-Part2c-3f -- weekly playoff pacing. When the cursor sits on a
    # built+active tournament phase, "One Week" drains exactly ONE bracket
    # STAGE (the lowest incomplete (bracket_type, bracket_round) group) and
    # then crowns the Season champion if the final node resolved. Otherwise
    # the RR matchday path below runs unchanged. play_next_bracket_round
    # carries its own per-Match atomicity, so the playoff branch needs no
    # transaction.atomic wrapper.
    #
    # CONF-02 -- "One Week" advances EVERY bracket of the phase by one stage
    # (ADR-0035). CONF-04 -- build the Worlds bracket BEFORE the cursor read
    # (ADR-0037), else the click falls through to the round-robin path and
    # does nothing.
    season.build_pending_worlds_bracket()

    phase = season.current_phase()
    tournaments = season.tournaments_for_phase(phase) if phase is not None else []
    if phase is not None and phase.phase_type == "tournament" and bool(tournaments):
        from matches.tournament_engine import play_next_bracket_round

        for tournament in tournaments:
            play_next_bracket_round(tournament)
        season.complete_if_finished()
        return

    with transaction.atomic():
        # LG-01j -- deferred import of ArenaMap + the per-fixture map-resolver
        # helper. Mirrors the ``play_season_task`` pattern: ``in_bulk`` runs
        # ONCE outside the per-fixture loop, the helper is called per fixture.
        from core.models import ArenaMap
        from matches.tasks import _fixture_map_ids, _resolve_fixture_map

        # LG-02-Part2c-2/3a/3c -- the next unplayed matchday (by-phase,
        # barrier-aware fixtures + leg-bearing played keys).
        to_play, phase_by_id = _next_matchday_to_play(season)
        if not to_play:
            return
        team_ids = {f.team_a_id for _pid, f in to_play} | {
            f.team_b_id for _pid, f in to_play
        }
        team_by_id = Team.objects.in_bulk(team_ids)
        # CONF-01 -- build the team->Conference map ONCE outside the loop.
        # CONF-06 -- built BEFORE the bulk-load: the per-Conference rotation
        # snapshots are part of the resolvable id union.
        conf_by_team = season.conference_by_team_id()
        # LG-01j / SUB-01 -- bulk-load the frozen pool + rotation snapshots once.
        pool_by_id: dict[int, ArenaMap] = ArenaMap.objects.in_bulk(
            _fixture_map_ids(season, conf_by_team)
        )
        for phase_id, fixture in to_play:
            team_a = team_by_id[fixture.team_a_id]
            team_b = team_by_id[fixture.team_b_id]
            arena_map = _resolve_fixture_map(
                season, fixture, pool_by_id, conf_by_team=conf_by_team
            )
            # FIN-04 -- roll injuries / resolve rosters in memory before the
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
                    conference=conf_by_team.get(fixture.team_a_id),
                )
            finally:
                restore_after_fixture(token)


def play_single_round(season: Season) -> None:
    """Play Single Round: one playoff node across the current phase's brackets.

    Raises ``SeasonNotActiveError`` / ``PlayInProgressError``, and
    ``WrongPhaseError`` when the cursor is not on a built tournament phase.
    """
    _require_active(season)
    _require_no_live_run(season)

    phase = season.current_phase()
    tournaments = (
        season.tournaments_for_phase(phase)
        if phase is not None and phase.phase_type == "tournament"
        else []
    )
    if not tournaments:
        raise WrongPhaseError("No active playoff bracket to play.")

    from matches.tournament_engine import play_next_node

    # CONF-02 -- play ONE node, from the first bracket that still has a playable
    # one. For a 0/1-Conference Season ``tournaments`` is ``[phase.tournament]``;
    # for a regional phase it walks the Conferences in ordinal order, keeping
    # "Play Single Round" a single-node step across the whole phase.
    progressed = False
    for tournament in tournaments:
        if play_next_node(tournament) is not None:
            progressed = True
            break
    # CONF-03 -- seed UNCONDITIONALLY after the click (idempotent, returns 0 when
    # nothing is ready). This is what stops "Play Single Round" from becoming a
    # DEAD CLICK: the click that resolves a Regional playoff's final node leaves
    # that bracket ``completed`` and its Last-chance sibling ``setup``, so the
    # dashboard would read neither "active" nor "completed" and hide the control.
    seeded = season.seed_pending_last_chance_brackets(phase)
    if not progressed and seeded:
        # Nothing was playable this click, but seeding just made something
        # playable -- play one node so the click is never wasted. ``tournaments``
        # was resolved BEFORE the seed and already contains the eager row, so it
        # sees the newly-seeded bracket; do not re-resolve.
        for tournament in tournaments:
            if play_next_node(tournament) is not None:
                break
    # CONF-04 -- build the Worlds bracket if the click just finished the last
    # Regional playoff (ADR-0037); the NEXT click drains it. The ``phase`` /
    # ``tournaments`` locals above are stale afterwards; do NOT re-resolve.
    season.build_pending_worlds_bracket()
    season.complete_if_finished()


# ---------------------------------------------------------------------------
# LG-01i -- "One Week (Live)" cursor resolution (moved from ``league_views``)
# ---------------------------------------------------------------------------


def alive_playoff_node(tournament: Tournament, team: Team) -> BracketNode | None:
    """LG-01i §5a — the manager team's next undecided, non-bye bracket node it
    currently occupies, or ``None``.

    A node qualifies when: ``winner_id is None``, ``is_bye is False``, both team
    slots filled, and (``team_a_id == team.id`` or ``team_b_id == team.id``). Of
    the qualifying nodes, the one with the lowest
    ``(_BRACKET_RANK[bracket_type], bracket_round, position)`` is the next
    playable node for that team. Pin to a Series that has NOT started
    (``series_matches.count() == 0``) so the watched Match is game 1.

    ``None`` ⇒ the team is eliminated, not a participant, or has no undecided
    node it occupies ⇒ no live entry.
    """
    from .bracket import _BRACKET_RANK

    candidates = []
    for node in tournament.nodes.select_related("team_a", "team_b").all():
        if node.winner_id is not None or node.is_bye:
            continue
        if node.team_a_id is None or node.team_b_id is None:
            continue
        if node.team_a_id != team.id and node.team_b_id != team.id:
            continue
        candidates.append(node)

    if not candidates:
        return None

    candidates.sort(
        key=lambda n: (
            _BRACKET_RANK.get(n.bracket_type, 0),
            n.bracket_round,
            n.position,
        )
    )
    node = candidates[0]

    # Pin to a Series that has not started so the watched Match is game 1.
    if node.series_matches.count() != 0:
        return None
    return node


def _next_matchday_to_play(season: Season) -> tuple[list, dict]:
    """LG-01i — the next unplayed matchday's fixtures for ``season``.

    Returns ``(to_play, phase_by_id)`` where ``to_play`` is the
    ``list[(phase_id, ScheduleFixture)]`` of the next single unplayed matchday
    (the ``play_week`` RR machinery — by-phase fixtures + Side-agnostic
    ``played_keys``) and ``phase_by_id`` maps each phase id to its
    ``SeasonPhase``. Shared by ``resolve_live_cursor`` and the RR commit so the
    fixture/played-key computation lives in one place.
    """
    by_phase = season.playable_fixtures_by_phase()
    phase_by_id = {phase.id: phase for phase, _ in by_phase}
    fixtures = [
        (phase.id, fixture)
        for phase, phase_fixtures in by_phase
        for fixture in phase_fixtures
    ]
    played_keys = {
        (
            gr.match.season_phase_id,
            frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
            gr.round_number,
            gr.match.leg,
        )
        for gr in GameRound.objects.filter(match__season=season).select_related("match")
    }
    return select_play_fixtures(fixtures, played_keys, 1), phase_by_id


def resolve_live_cursor(season: Season) -> Optional[dict]:
    """LG-01i §5 — resolve the manager's watchable live cursor for ``season``.

    Returns a cursor descriptor, the ``{"kind": "rr_bye"}`` degrade marker, or
    ``None`` (no live entry). Algorithm (LOCKED order):

      1. ``team = season.league.current_team``; ``None`` ⇒ ``None``.
      2. ``phase = season.current_phase()``.
         * **Playoff cursor** — a built + active tournament phase: resolve the
           team's ``alive_playoff_node``; found ⇒ a ``{"kind": "playoff", ...}``
           descriptor; else ``None``.
         * **RR cursor** — an RR phase (or the implicit fallback): compute the
           next unplayed matchday's fixtures via the ``play_week`` machinery and
           find the fixture whose Side-agnostic pair contains the team; found ⇒
           ``{"kind": "rr", ...}``; bye ⇒ ``{"kind": "rr_bye"}``.
      3. Anything else ⇒ ``None``.
    """
    team = season.league.current_team
    if team is None:
        return None

    phase = season.current_phase()
    if phase is None:
        return None

    # --- Playoff cursor ----------------------------------------------------
    if (
        phase.phase_type == "tournament"
        and phase.tournament_id is not None
        and phase.tournament.state == "active"
    ):
        node = alive_playoff_node(phase.tournament, team)
        if node is None:
            return None
        return {
            "kind": "playoff",
            "node": node,
            "tournament": phase.tournament,
            "cursor": {
                "type": "playoff",
                "tournament_id": phase.tournament_id,
                "bracket_type": node.bracket_type,
                "bracket_round": node.bracket_round,
                "position": node.position,
            },
            "red_team": node.team_a,
            "blue_team": node.team_b,
        }

    # --- RR cursor ---------------------------------------------------------
    if phase.phase_type == "round_robin":
        to_play, phase_by_id = _next_matchday_to_play(season)

        watched = None
        watched_phase_id = None
        for phase_id, fixture in to_play:
            if team.id in (fixture.team_a_id, fixture.team_b_id):
                watched = fixture
                watched_phase_id = phase_id
                break

        if watched is None:
            # Manager has a bye this matchday ⇒ degrade to a plain commit.
            return {"kind": "rr_bye"}

        watched_phase = phase_by_id.get(watched_phase_id)
        team_by_id = Team.objects.in_bulk([watched.team_a_id, watched.team_b_id])
        team_a = team_by_id.get(watched.team_a_id)
        team_b = team_by_id.get(watched.team_b_id)
        return {
            "kind": "rr",
            "fixture": watched,
            "season_phase": watched_phase,
            "phase_id": watched_phase_id,
            "cursor": {
                "type": "rr",
                "season_id": season.id,
                "season_phase_id": watched_phase_id,
                "matchday": watched.matchday,
                "pair": sorted([watched.team_a_id, watched.team_b_id]),
                "round_number": watched.round_number,
                "leg": watched.leg,
            },
            "red_team": team_a,
            "blue_team": team_b,
        }

    return None


# ---------------------------------------------------------------------------
# FIN-04 -- per-fixture injury resolution (moved from ``league_views``; the
# views re-export every name, and ``tasks.play_season_task`` imports it there)
# ---------------------------------------------------------------------------

# FIN-04 — the 6 (slot field, role) pairs of the active roster, in slot order.
_INJURY_SLOT_FIELDS = (
    ("slot_commander", "commander"),
    ("slot_heavy", "heavy"),
    ("slot_scout_1", "scout"),
    ("slot_scout_2", "scout"),
    ("slot_medic", "medic"),
    ("slot_ammo", "ammo"),
)


def _resolve_injuries_for_team(season: Season, team: Team, token: dict) -> None:
    """FIN-04 — resolve injuries for ONE Team of a fixture, in-memory.

    Steps (LOCKED order): the subjects are the 6 active-roster STARTERS only
    (the ``slot_*`` players BEFORE substitution); an unavailable starter
    decrements by 1 (no re-roll); a healthy starter who fields rolls a NEW
    injury (fresh ``random.Random()`` per fixture, never the SIM seed chain) and
    on a hit draws ``N = injury.draw_duration(...)`` from the LIVE current
    ``Team.budget_health``; each now-unavailable starter resolves via the Team's
    ``injury_policy`` (``auto_sub`` → bench → free-agent pool, ``play_hurt`` the
    universal no-sub fallback). Stages the in-memory ``slot_*`` / 19-stat
    mutations into ``token`` for ``restore_after_fixture`` and persists only the
    ``games_unavailable`` change (``bulk_update`` with ``update_fields``).
    """
    # Snapshot the starters as they stood BEFORE any substitution (the
    # subjects — fill-ins are never injury subjects, avoiding orphan injuries).
    starters: list[tuple[str, "Player"]] = []
    for slot_field, _role in _INJURY_SLOT_FIELDS:
        player = getattr(team, slot_field)
        if player is not None:
            starters.append((slot_field, player))

    rng = random.Random()  # FRESH per fixture — never the SIM seed chain.
    health_effect = finance.health_effect(team.budget_health)

    persisted: list[Player] = []  # players whose games_unavailable changed
    for _slot_field, player in starters:
        if player.games_unavailable > 0:
            # DECREMENT an already-unavailable starter (whether sub'd out or
            # playing hurt) — does NOT re-roll.
            player.games_unavailable -= 1
            persisted.append(player)
        else:
            # ROLL a healthy starter who actually fields.
            age = player.age if player.age is not None else 25
            if injury.roll_injury(age, rng):
                n = injury.draw_duration(health_effect, age, rng)
                player.games_unavailable = n
                persisted.append(player)

    if persisted:
        Player.objects.bulk_update(persisted, ["games_unavailable"])

    # RESOLVE the roster to a valid 6 for each now-unavailable starter.
    bench_pool = list(team.bench_players)
    league = season.league
    free_agent_pool = (
        list(league.free_agent_pool.players.all())
        if league.free_agent_pool is not None
        else []
    )
    used_sub_pks: set[int] = set()

    for slot_field, player in starters:
        if player.games_unavailable == 0:
            continue  # this starter is fine — fields normally

        if team.injury_policy == "auto_sub":
            sub = _pick_substitute(bench_pool, free_agent_pool, used_sub_pks)
            if sub is not None:
                used_sub_pks.add(sub.pk)
                # In-memory slot rewrite — restore records the ORIGINAL FK.
                token["slot_restore"].append((team, slot_field, player))
                setattr(team, slot_field, sub)
                continue
            # No available sub ⇒ play_hurt is the universal no-sub fallback.

        # play_hurt (explicit policy OR the auto_sub no-sub fallback): rewrite
        # the injured Player's 19 stat fields down by the penalty (clamp 0..100).
        _apply_play_hurt(player, token)


def _pick_substitute(
    bench_pool: list, free_agent_pool: list, used_sub_pks: set
) -> "Player | None":
    """FIN-04 — pick an available substitute (``games_unavailable == 0``),
    bench first then the League free-agent pool, never re-using one already
    picked this fixture. Returns ``None`` when none is available."""
    for source in (bench_pool, free_agent_pool):
        for candidate in source:
            if candidate.pk in used_sub_pks:
                continue
            if candidate.games_unavailable == 0:
                return candidate
    return None


def _apply_play_hurt(player: "Player", token: dict) -> None:
    """FIN-04 — subtract the flat play-hurt penalty from the injured Player's
    19 stat fields in memory (clamped ``[0, 100]``), staging the originals into
    ``token`` for restore. Never ``.save()``."""
    penalty = injury.play_hurt_penalty()
    originals: dict[str, int] = {}
    for name in STAT_FIELDS:
        current = getattr(player, name)
        originals[name] = current
        setattr(player, name, max(0, min(100, current - penalty)))
    token["stat_restore"].append((player, originals))


def resolve_injuries_for_fixture(
    season: Season, team_red: Team, team_blue: Team
) -> dict:
    """FIN-04 — the single per-fixture injury resolver.

    Called by every play path BEFORE ``simulate_scheduled_round(...)``. Returns
    a RESTORE TOKEN (an opaque dict the caller hands to
    ``restore_after_fixture(token)``). First-line GATE — a no-op ``{}`` when not
    ``_is_career_league AND finance_enabled`` ⇒ byte-identical OFF. Operates on
    the two in-memory Team objects the play loop already holds: rolls / decrements
    availability (the only persisted write), substitutes or plays-hurt in memory.
    """
    league = season.league
    if not (is_career_league(league) and league.finance_enabled):
        return {}

    token: dict = {"slot_restore": [], "stat_restore": []}
    _resolve_injuries_for_team(season, team_red, token)
    _resolve_injuries_for_team(season, team_blue, token)
    return token


def restore_after_fixture(token: dict) -> None:
    """FIN-04 — undo every in-memory mutation ``resolve_injuries_for_fixture``
    applied (Team ``slot_*`` FKs and injured-Player 19-stat fields), restoring
    the pre-fixture in-memory state. NEVER ``.save()`` — the only persisted
    writes are the ``games_unavailable`` decrement/set done explicitly above."""
    if not token:
        return
    for team, slot_field, original_player in token.get("slot_restore", []):
        setattr(team, slot_field, original_player)
    for player, originals in token.get("stat_restore", []):
        for name, value in originals.items():
            setattr(player, name, value)


# ---------------------------------------------------------------------------
# One Week (Live) -- play the manager's game now, the rest in the background
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LivePlayResult:
    """What a live play committed and started.

    ``kind`` is ``"rr"``, ``"playoff"`` or ``"member_night"``; ``round_ids``
    are the committed Rounds to watch (empty when the manager had a bye);
    ``job_id`` is the background play run started for the rest of the week
    (``None`` when nothing was enqueued).
    """

    kind: str
    round_ids: list[int]
    job_id: str | None


def _commit_live_rr_round(season: Season, cursor: dict) -> list[int]:
    """Play the manager's round-robin Round at full fidelity; return its id."""
    from core.models import ArenaMap
    from matches.tasks import _fixture_map_ids, _resolve_fixture_map

    fixture = cursor["fixture"]
    # CONF-01 / CONF-06 -- the Conference map is built before the bulk-load:
    # per-Conference rotation snapshots are part of the resolvable id union.
    conf_by_team = season.conference_by_team_id()
    pool_by_id = ArenaMap.objects.in_bulk(_fixture_map_ids(season, conf_by_team))
    arena_map = _resolve_fixture_map(
        season, fixture, pool_by_id, conf_by_team=conf_by_team
    )
    # FIN-04 -- roll injuries before the sim, restore the roster afterwards.
    token = resolve_injuries_for_fixture(
        season, cursor["red_team"], cursor["blue_team"]
    )
    try:
        game_round = BatchSimulator().simulate_scheduled_round(
            season,
            cursor["red_team"],
            cursor["blue_team"],
            fixture.round_number,
            arena_map=arena_map,
            season_phase=cursor["season_phase"],
            leg=fixture.leg,
            conference=conf_by_team.get(fixture.team_a_id),
            fidelity="full",
        )
    finally:
        restore_after_fixture(token)
    return [game_round.id]


def _commit_live_playoff_match(cursor: dict) -> list[int]:
    """Play the manager's bracket node (its 2-round Match); return Round ids."""
    from matches.tournament_engine import play_specific_node

    node = cursor["node"]
    play_specific_node(node, fidelity="full")
    node.refresh_from_db()
    latest = (
        node.series_matches.select_related("match").order_by("-game_number").first()
    )
    if latest is None or latest.match_id is None:
        return []
    return list(
        latest.match.game_rounds.order_by("round_number").values_list("id", flat=True)
    )


def play_live(season: Season) -> LivePlayResult:
    """One Week (Live): commit the manager's game, then run the rest of the week.

    Raises ``SeasonNotActiveError``, ``PlayInProgressError``,
    ``NoLiveCursorError`` or ``BrokerUnavailableError`` (all checked BEFORE
    anything is committed). On a manager bye only the
    background run starts. A ``ValidationError`` / ``ValueError`` from the
    simulator propagates with no run started.
    """
    from matches.tasks import play_season_task

    _require_active(season)
    _require_no_live_run(season)
    cursor = resolve_live_cursor(season)
    if cursor is None:
        raise NoLiveCursorError("No live game to play for your team.")
    # L-7: probe the broker before committing the manager's game, so a down
    # broker can't leave the week half-played.
    _ensure_broker_reachable(play_season_task)
    if cursor["kind"] == "rr_bye":
        round_ids: list[int] = []
        kind = "rr"
    elif cursor["kind"] == "rr":
        round_ids = _commit_live_rr_round(season, cursor)
        kind = "rr"
    else:
        round_ids = _commit_live_playoff_match(cursor)
        kind = "playoff"
    # The background run skips the manager's just-played game via its
    # played-keys / find-next-playable-node checks.
    job_id = enqueue_play_run(season, play_season_task, max_matchdays=1)
    return LivePlayResult(kind=kind, round_ids=round_ids, job_id=job_id)


# ---------------------------------------------------------------------------
# Member nights (LG-07a) -- setup, play-all, single, live
# ---------------------------------------------------------------------------

# Display prefix for drawn-Team names (``MN <site> G<n> A`` / ``... B``).
# The Site is never parsed back out of the name -- see ``_site_by_drawn_team``.
_MN_TEAM_PREFIX = "MN "

# The six ``Team.slot_*_id`` columns a drawn Team's players sit in.
_DRAWN_SLOT_ID_FIELDS = tuple(f"slot_{slot}_id" for slot in ROLE_SLOTS)


def viable_member_night_sites(season: Season) -> list[tuple[str, int]]:
    """``(site, available-player count)`` for every viable Site, sorted."""
    return sorted(
        (site, len(pool))
        for site, pool in season._member_night_pool_by_site().items()
        if len(pool) >= member_night.MIN_POOL
    )


def _site_by_drawn_team(teams: list[Team]) -> dict[int, str]:
    """The Site of each drawn Team, from its players' ``home_site``.

    ``setup_member_night`` draws every player of a game from one Site's pool
    (``Season._member_night_pool_by_site`` groups by ``Player.home_site``), so
    any filled slot's player names the Site. A Team with no filled slot is
    omitted. One query for all the Teams' players.
    """
    first_player_by_team: dict[int, int] = {}
    for team in teams:
        for field in _DRAWN_SLOT_ID_FIELDS:
            player_id = getattr(team, field)
            if player_id is not None:
                first_player_by_team[team.pk] = player_id
                break
    home_site_by_player = dict(
        Player.objects.filter(pk__in=first_player_by_team.values()).values_list(
            "pk", "home_site"
        )
    )
    return {
        team_id: home_site_by_player[player_id]
        for team_id, player_id in first_player_by_team.items()
        if player_id in home_site_by_player
    }


NO_MEMBER_NIGHT_TO_SET_UP = "No member night to set up."
_NO_MEMBER_NIGHT_GAME = "No member-night game to play."


def require_member_night(
    season: Season, wrong_phase_message: str = _NO_MEMBER_NIGHT_GAME
) -> SeasonPhase:
    """The current ``member_night`` phase of an active, idle Season.

    Raises ``SeasonNotActiveError``, ``PlayInProgressError`` or
    ``WrongPhaseError`` (cursor not on a member night), in that order.
    """
    _require_active(season)
    _require_no_live_run(season)
    phase = season.current_phase()
    if phase is None or phase.phase_type != "member_night":
        raise WrongPhaseError(wrong_phase_message)
    return phase


def _next_member_night_shell(season: Season) -> tuple[SeasonPhase, Match]:
    """The member-night phase + its first unplayed game shell.

    Raises the ``require_member_night`` errors, then
    ``NoMemberNightGamesError`` when every shell is played (or none exist).
    """
    phase = require_member_night(season)
    shell = Match.objects.filter(season_phase=phase, is_completed=False).first()
    if shell is None:
        raise NoMemberNightGamesError(_NO_MEMBER_NIGHT_GAME)
    return phase, shell


def setup_member_night(
    season: Season, sites: list[str], *, manager: User | None = None
) -> int:
    """Draw member-night games for ``sites``; return how many were drawn.

    Creates two ``is_draw_team`` Teams per game (roles fixed once, onto the
    drawn Teams' ``slot_*`` FKs, stamped ``manager``) plus the unplayed
    ``Match`` shell stamped ``season`` + ``season_phase`` (build-then-drain).
    The draw consumes a FRESH ``random.Random()`` (outside the SIM seed
    chain). A repeat call APPENDS more games. ``sites`` is pre-validated by
    ``MemberNightSetupForm``. Raises the ``require_member_night`` errors.
    """
    phase = require_member_night(season, NO_MEMBER_NIGHT_TO_SET_UP)
    full_pool = season._member_night_pool_by_site()
    pool_by_site = {site: full_pool.get(site, []) for site in sites}

    with transaction.atomic():
        games = member_night.draw_member_night_games(pool_by_site, random.Random())
        for game in games:
            base_name = f"{_MN_TEAM_PREFIX}{game.site} G{game.game_index + 1}"
            drawn = []
            for suffix, roles in (("A", game.team_a), ("B", game.team_b)):
                team = Team.objects.create(
                    name=f"{base_name} {suffix}", is_draw_team=True, manager=manager
                )
                for slot, player_id in roles.items():
                    setattr(team, f"slot_{slot}_id", player_id)
                team.save()
                drawn.append(team)
            Match.objects.create(
                season=season,
                season_phase=phase,
                team_red=drawn[0],
                team_blue=drawn[1],
                is_completed=False,
            )
    return len(games)


def play_member_night_run(season: Season) -> str:
    """Start a ``play_member_night_task`` run draining every unplayed game.

    Raises the ``_next_member_night_shell`` errors (incl.
    ``NoMemberNightGamesError``) before anything is enqueued.
    """
    from matches.tasks import play_member_night_task

    _next_member_night_shell(season)
    return enqueue_play_run(season, play_member_night_task)


def _play_member_night_shell(
    season: Season, phase: SeasonPhase, shell: Match, *, fidelity: str = "scores"
) -> list[int]:
    """Play both Rounds of one member-night shell; return their Round ids."""
    round_ids = [
        BatchSimulator()
        .simulate_scheduled_round(
            season,
            shell.team_red,
            shell.team_blue,
            round_number,
            season_phase=phase,
            fidelity=fidelity,
        )
        .id
        for round_number in (1, 2)
    ]
    season.complete_if_finished()
    return round_ids


def play_member_night_single(season: Season) -> None:
    """Play ONE unplayed member-night game (both Rounds, default fidelity)."""
    phase, shell = _next_member_night_shell(season)
    _play_member_night_shell(season, phase, shell)


def play_member_night_live(season: Season) -> LivePlayResult:
    """Play ONE member-night game at full fidelity for a live replay.

    No background run is started (``job_id`` is ``None``).
    """
    phase, shell = _next_member_night_shell(season)
    round_ids = _play_member_night_shell(season, phase, shell, fidelity="full")
    return LivePlayResult(kind="member_night", round_ids=round_ids, job_id=None)


def build_member_night_state(season: Season) -> dict:
    """The member-night setup state for ``season``.

    ``phase_active`` is whether the cursor sits on a ``member_night`` phase;
    ``sites`` lists the viable Sites as ``{site, count, games}`` (``count`` =
    available players, ``games`` = games drawn for that Site so far in the
    current phase); ``games_total`` / ``games_unplayed`` count the phase's
    game shells. All empty / zero off a member-night cursor.
    """
    phase = season.current_phase()
    if phase is None or phase.phase_type != "member_night":
        return {
            "phase_active": False,
            "sites": [],
            "games_total": 0,
            "games_unplayed": 0,
        }
    shells = list(Match.objects.filter(season_phase=phase).select_related("team_red"))
    site_by_team = _site_by_drawn_team(
        [shell.team_red for shell in shells if shell.team_red_id]
    )
    games_by_site: dict[str, int] = {}
    for shell in shells:
        site = site_by_team.get(shell.team_red_id)
        if site is not None:
            games_by_site[site] = games_by_site.get(site, 0) + 1
    return {
        "phase_active": True,
        "sites": [
            {"site": site, "count": count, "games": games_by_site.get(site, 0)}
            for site, count in viable_member_night_sites(season)
        ],
        "games_total": len(shells),
        "games_unplayed": sum(1 for shell in shells if not shell.is_completed),
    }


# ---------------------------------------------------------------------------
# Play controls (NAV-01) -- structured, no HTML
# ---------------------------------------------------------------------------


def _action_button(season_mode: str) -> tuple[str, str]:
    """The NAV-01 action-button ``(label, state)`` for a Season mode."""
    return {
        "none": ("No Season", "none"),
        "draft": ("Start Season", "start_season"),
        "active": ("Play Next", "play_next"),
    }.get(season_mode, ("Start Next Season", "start_next_season"))


def build_play_controls(league: League, displayed_season: Season | None) -> dict:
    """The play-control state for ``league`` and its resolved displayed Season.

    Returns 13 keys: ``action_button_label`` / ``action_button_state``, the
    five ``_playoff_cursor_keys`` values, ``live_preview_available``,
    ``is_career_mode``, ``member_night_phase_active``, ``member_night_sites``
    (``[{"site", "count"}]`` -- viable Sites only, sorted by Site),
    ``member_night_has_unplayed`` and ``active_play_job_id``.
    """
    season_mode = "none" if displayed_season is None else displayed_season.state
    action_button_label, action_button_state = _action_button(season_mode)
    (
        playoff_phase_active,
        playoff_tournament_id,
        playoff_completed,
        has_following_tournament_phase,
        following_tournament_is_final,
    ) = _playoff_cursor_keys(displayed_season)

    live_preview_available = False
    if displayed_season is not None and season_mode == "active":
        cursor = resolve_live_cursor(displayed_season)
        live_preview_available = cursor is not None and cursor["kind"] in (
            "rr",
            "playoff",
        )

    member_night_phase_active = False
    member_night_sites: list[dict] = []
    member_night_has_unplayed = False
    if displayed_season is not None:
        mn_phase = displayed_season.current_phase()
        if mn_phase is not None and mn_phase.phase_type == "member_night":
            member_night_phase_active = True
            member_night_sites = [
                {"site": site, "count": count}
                for site, count in viable_member_night_sites(displayed_season)
            ]
            member_night_has_unplayed = Match.objects.filter(
                season_phase=mn_phase, is_completed=False
            ).exists()

    return {
        "action_button_label": action_button_label,
        "action_button_state": action_button_state,
        "playoff_phase_active": playoff_phase_active,
        "playoff_tournament_id": playoff_tournament_id,
        "playoff_completed": playoff_completed,
        "has_following_tournament_phase": has_following_tournament_phase,
        "following_tournament_is_final": following_tournament_is_final,
        "live_preview_available": live_preview_available,
        "is_career_mode": is_career_league(league),
        "member_night_phase_active": member_night_phase_active,
        "member_night_sites": member_night_sites,
        "member_night_has_unplayed": member_night_has_unplayed,
        "active_play_job_id": (
            displayed_season.active_play_job_id
            if displayed_season is not None
            else None
        ),
    }
