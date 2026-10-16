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
from typing import Any

from celery.result import AsyncResult
from django.db import transaction

from matches import finance, injury
from matches.development import STAT_FIELDS
from matches.league_services import is_career_league
from matches.models import GameRound, Season
from matches.season_dashboard import select_play_fixtures
from matches.simulation import BatchSimulator
from matches.views import _celery_state_to_job_status
from teams.models import Player, Team

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


# ---------------------------------------------------------------------------
# Active play run
# ---------------------------------------------------------------------------


def enqueue_play_run(season: Season, task: Any, **task_kwargs: Any) -> str:
    """Start ``task`` as the Season's Active play run; return its job id.

    Raises ``PlayInProgressError`` when a run is already live. The job id is
    written (and the cancel flag cleared) BEFORE ``apply_async`` so a fast or
    eager task can never clear the column ahead of the write.
    """
    current = (
        Season.objects.filter(pk=season.pk)
        .values_list("active_play_job_id", flat=True)
        .first()
    )
    if current:
        raise PlayInProgressError(current)
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
        task.apply_async(kwargs={"season_id": season.pk, **task_kwargs}, task_id=job_id)
    except Exception:
        # The task never ran (or failed eagerly): release the run if we hold it.
        Season.objects.filter(pk=season.pk, active_play_job_id=job_id).update(
            active_play_job_id=None
        )
        raise
    return job_id


def request_play_cancel(season: Season) -> bool:
    """Ask the Active play run to stop; ``False`` (and no write) when idle."""
    updated = Season.objects.filter(
        pk=season.pk, active_play_job_id__isnull=False
    ).update(play_cancel_requested=True)
    if updated:
        season.play_cancel_requested = True
    return bool(updated)


def _job_status_fields(async_result: AsyncResult) -> dict:
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
        "status": _celery_state_to_job_status(state),
        "completed": completed,
        "total": total,
        "cancelled": cancelled,
        "error": error,
    }


def build_play_job_status(job_id: str) -> dict:
    """Structured progress for a Season play Job (no HTML fragments)."""
    return _job_status_fields(AsyncResult(job_id))


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


def _require_no_live_run(season: Season) -> None:
    """Raise ``PlayInProgressError`` while the Season has an Active play run."""
    current = (
        Season.objects.filter(pk=season.pk)
        .values_list("active_play_job_id", flat=True)
        .first()
    )
    if current:
        raise PlayInProgressError(current)


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

        # LG-02-Part2c-2 -- by-phase fixtures (global-continuous matchday
        # offset already applied) + phase-aware played_keys.
        # LG-02-Part2c-3c -- barrier-aware: the RR loop halts at an incomplete
        # tournament phase so a mid-season bracket drains first.
        by_phase = season.playable_fixtures_by_phase()
        phase_by_id = {phase.id: phase for phase, _ in by_phase}
        fixtures = [
            (phase.id, fixture)
            for phase, phase_fixtures in by_phase
            for fixture in phase_fixtures
        ]
        # LG-02-Part2c-3a -- played_keys gain ``leg`` so a double_round_robin
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
        to_play = select_play_fixtures(fixtures, played_keys, 1)
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
