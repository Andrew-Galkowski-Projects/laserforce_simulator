"""LG-02 — League Playoffs screen view.

Renders the bracket(s) for a Season's ``tournament`` :class:`SeasonPhase`(s)
INSIDE the league shell (sidebar) instead of bouncing the user out to the
standalone ``/tournaments/<id>/`` detail page. Covers both **mid-season
tournaments** and the **end-of-season playoff** — every ``phase_type ==
"tournament"`` phase of the viewed Season whose bracket has been built — laid
out zengm-style (one column per Bracket round). Read-only, GET-only; follows
the LG-01z shared view contract (§2): GET-guard → ``get_owned_or_404`` →
session write → ``displayed_season`` pick → sidebar links → screen aggregation
→ render.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_views import _build_league_sidebar_links
from matches.models import League


def _coerce_view_season(request: HttpRequest, league: League, displayed_season):
    """Pick which Season's playoffs to show.

    ``?season=<id>`` selects an explicit Season of THIS league; an absent /
    invalid / foreign id falls back to ``displayed_season`` (the LG-06d
    forgiving-coercion precedent — minus the Career option, since a bracket has
    no cross-season aggregate).
    """
    raw = request.GET.get("season")
    if raw:
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            sid = None
        if sid is not None:
            chosen = league.seasons.filter(pk=sid).first()
            if chosen is not None:
                return chosen
    return displayed_season


def playoffs(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-02 — Playoffs page for a League's tournament Season phases.

    Renders each built ``tournament`` phase of the viewed Season as a
    single-elimination bracket (the embedded playoff / mid-season tournament is
    always ``single_elimination``, so every node lives in the ``"winners"``
    slice of :func:`matches.tournament_views._build_rounds`). A tournament
    phase that exists but has not yet been seeded (the regular season is still
    in progress) renders a "not yet seeded" stub. A Season with no tournament
    phase — or no Season at all — renders the empty-state notice.

    CONF-02 — a >= 2-Conference Season's tournament phase renders ONE section
    per regional bracket, headed by its Conference name (ADR-0035). The new
    ``key`` entry keeps their DOM ids distinct while leaving a 0/1-Conference
    Season's ids byte-identical.

    CONF-03 — a Conference of 9+ Teams on the FINAL tournament phase adds one
    more section for its Last-chance qualifier bracket (ADR-0036), keyed with a
    ``-lc`` suffix and badged by ``stage_label``; while it is unseeded it
    renders the pending alert rather than an empty bracket div. The screen also
    carries the derived ``worlds_qualifiers`` field, which renders as a
    read-only panel below the brackets once qualification is complete.

    CONF-04 — a >= 2-Conference Season also renders the Season-wide Worlds
    bracket (ADR-0037), keyed with a ``-worlds`` suffix and badged
    ``"Worlds"``. Its section id ``league-playoffs-phase-<ord>-worlds`` is
    distinct from CONF-03's qualification table at ``league-playoffs-worlds``;
    the two coexist on the same page and must never be conflated.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_owned_or_404(League, request, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="playoffs"
    )

    season_options = list(league.seasons.order_by("-id"))
    view_season = _coerce_view_season(request, league, displayed_season)

    brackets: list[dict] = []
    if view_season is not None:
        # Deferred import — keep the league-screen import graph shallow and dodge
        # any tournament_views <-> views/tasks import-order surprise at load time.
        from matches.tournament_views import _build_rounds

        phases = (
            view_season.phases.filter(phase_type="tournament")
            .select_related("tournament")
            .order_by("ordinal")
        )
        for phase in phases:
            # CONF-02 — a phase may hold N regional brackets, one per Conference
            # (ADR-0035), so one phase can append N entries. A 0/1-Conference
            # Season yields the single Season-wide bracket exactly as before.
            tournaments = view_season.tournaments_for_phase(phase)
            if not tournaments:
                # Phase exists but its bracket has not been built yet (the RR
                # phase is still in progress). Surface a pending stub.
                #
                # CONF-04 — the stub gets the same Worlds treatment as the built
                # entry below (ADR-0037), so a phase's DOM id is stable across
                # the unbuilt -> built transition. A >= 2-Conference Season
                # therefore shows a "Worlds" pending section from the moment the
                # Season starts: the phase is derived at ``start_season``, and
                # the panel tells the user Worlds is coming.
                is_worlds = phase.tournament_mode == "worlds"
                brackets.append(
                    {
                        "phase": phase,
                        "tournament": None,
                        # CONF-04 — a bare "Worlds" here rendered the word TWICE
                        # (card heading + the ``stage_label`` badge beside it).
                        # Mirror the BUILT card's heading instead, which is the
                        # Tournament's own name (``"<season> Worlds"``), so the
                        # heading is stable across the unbuilt -> built
                        # transition and the badge reads as a label, not an echo.
                        "name": (
                            f"{view_season.name} Worlds" if is_worlds else "Playoffs"
                        ),
                        "rounds": [],
                        "champion": None,
                        "pending": True,
                        "conference": None,
                        "key": (
                            f"{phase.ordinal}-worlds"
                            if is_worlds
                            else str(phase.ordinal)
                        ),
                        "stage": "worlds" if is_worlds else "",
                        "stage_label": "Worlds" if is_worlds else "",
                    }
                )
                continue
            for tournament in tournaments:
                # Embedded season tournaments are single-elimination, so the
                # whole tree lives in the "winners" slice.
                rounds = _build_rounds(tournament)["winners"]
                conference = tournament.conference
                # ``key`` is the DOM-id discriminator that stops the N regional
                # brackets of one phase from colliding on ``phase.ordinal``. It
                # IS ``str(phase.ordinal)`` for a Season-wide bracket, so every
                # existing DOM id on a 0/1-Conference Season is unchanged.
                #
                # CONF-03 — the ``-lc`` suffix is the Last-chance discriminator
                # (ADR-0036). Every CONF-02 regional key and every
                # 0/1-Conference key is therefore byte-identical, and so is
                # every DOM id built from them.
                #
                # CONF-04 — the ``-worlds`` suffix is the Worlds discriminator
                # (ADR-0037), and its branch is evaluated FIRST because the
                # Worlds ``Tournament``'s ``conference`` is ``None`` and would
                # otherwise fall into the Season-wide branch. Note the two axes
                # differ: ``is_worlds`` asks the PHASE flavour, ``is_last_chance``
                # asks the BRACKET stage — there is deliberately no
                # ``qualifier_stage == "worlds"``.
                is_worlds = phase.tournament_mode == "worlds"
                is_last_chance = tournament.qualifier_stage == "last_chance"
                if is_worlds:
                    key = f"{phase.ordinal}-worlds"
                    stage = "worlds"
                elif conference is None:
                    key = str(phase.ordinal)
                    stage = ""
                elif is_last_chance:
                    key = f"{phase.ordinal}-{conference.ordinal}-lc"
                    stage = "last_chance"
                else:
                    key = f"{phase.ordinal}-{conference.ordinal}"
                    stage = "regional_playoff"
                # Deliberately EMPTY for a Season-wide and a regional bracket, so
                # no new element renders on a CONF-02 Season.
                if is_worlds:
                    stage_label = "Worlds"
                elif is_last_chance:
                    stage_label = "Last Chance Qualifier"
                else:
                    stage_label = ""
                brackets.append(
                    {
                        "phase": phase,
                        "tournament": tournament,
                        "name": tournament.name,
                        "rounds": rounds,
                        "champion": tournament.champion,
                        # An unseeded Last-chance bracket is the ONLY row that
                        # can still be in ``setup`` when it renders —
                        # ``_build_tournament_for_phase`` always calls
                        # ``lock_and_build()``, so every pre-CONF-03 bracket is
                        # ``active`` or later and this stays literally False.
                        "pending": tournament.state == "setup",
                        "conference": conference,
                        "key": key,
                        "stage": stage,
                        "stage_label": stage_label,
                    }
                )

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "playoffs",
        "season_options": season_options,
        "view_season": view_season,
        "selected_season_id": view_season.id if view_season is not None else None,
        "brackets": brackets,
        # CONF-03 — the raw ``list[WorldsQualifier]`` (ADR-0036), seeded 1..M or
        # empty. ``{% if worlds_qualifiers %}`` IS the readiness test: the
        # derivation is all-or-nothing, so there is no separate ``worlds_ready``
        # flag and no partial field to guard against. ``[]`` for a
        # 0/1-Conference Season, so the panel is absent entirely there.
        "worlds_qualifiers": (
            view_season.worlds_qualifiers() if view_season is not None else []
        ),
    }
    return render(request, "leagues/playoffs.html", context)
