"""LG-02a — sandbox single-elimination Tournament views.

GET-driven list / create / detail surfaces plus three POST write endpoints
(reseed / lock / play-next). The bracket STRUCTURE lives in the pure
``matches.bracket`` module; these views are the ORM side of that seam.
"""

import random
from statistics import mean

from celery.result import AsyncResult
from kombu.exceptions import OperationalError
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render

from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.forms import RosterImportForm
from teams.models import Player, Team, get_free_agents_team
from teams.player_generator import draw_preferred_roles, draw_stats
from teams.roster_importer import RosterImportError, parse_roster_csv
from teams.views import _apply_roster, _check_db_slot_collisions, _generate_teams

from matches.views import _celery_state_to_job_status

from .bracket import (
    advance_winner,
    break_tie,
    default_seed_order,
)
from .draw import ROLE_SLOTS, compute_draw
from .models import (
    BracketNode,
    Tournament,
    TournamentParticipant,
    TournamentPlayerEntry,
    _node_to_dict,
    count_series_wins,
)
from .schedule_generator import generate_schedule
from .simulation.entrypoints import BatchSimulator
from .standings import match_score
from .tasks import play_tournament_task
from .tournament_engine import play_next_node

# LG-02c (RR->DE) — the 6 locked (wb, lb) advancer shape combos, in select order.
# value string format "wb/lb" (e.g. "4/0"). lb is 0 or wb//2.
_RRDE_COMBOS: tuple[tuple[str, int, int], ...] = (
    ("4/0", 4, 0),
    ("4/2", 4, 2),
    ("8/0", 8, 0),
    ("8/4", 8, 4),
    ("16/0", 16, 0),
    ("16/8", 16, 8),
)
_RRDE_COMBO_BY_VALUE: dict[str, tuple[int, int]] = {
    value: (wb, lb) for value, wb, lb in _RRDE_COMBOS
}


def _parse_rrde_combo(raw: "str | None", tournament_format: str) -> tuple[int, int]:
    """Parse the ``rrde_combo`` select value into ``(wb_advancers,
    lb_advancers)``.

    A non-RRDE format ignores the combo and returns ``(0, 0)``. An RRDE format
    with an absent / invalid value forgiving-falls-back to the first combo
    ``(4, 0)``.
    """
    if tournament_format != "round_robin_double_elim":
        return (0, 0)
    return _RRDE_COMBO_BY_VALUE.get(raw or "", (4, 0))


def _parse_swiss_rounds(raw: "str | None") -> int:
    """LG-02c (Swiss) — parse the ``swiss_rounds`` POST field into a
    non-negative int (forgiving: absent/blank/invalid/negative -> 0 = auto).

    Clamping happens at lock; the view just coerces to a non-negative int, 0
    meaning auto.
    """
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else 0


def _team_mean_rating(team: Team) -> float:
    """Mean active-player overall_rating for a Team (LG-01c draft-preview
    formula verbatim). 0.0 when the Team has no active players.
    """
    players = list(team.active_players)
    if not players:
        return 0.0
    return mean(p.overall_rating for p in players)


def tournament_list(request: HttpRequest) -> HttpResponse:
    """List all Tournaments newest-first."""
    tournaments = Tournament.objects.order_by("-id")
    return render(
        request,
        "matches/tournament_list.html",
        {"tournaments": tournaments},
    )


@transaction.atomic
def tournament_create(request: HttpRequest) -> HttpResponse:
    """GET -> render create form. POST valid -> create Tournament + participants
    with default Seeding, redirect to detail. POST invalid -> re-render (200).
    """
    # Only Teams with a full valid roster (all 6 slots filled, no duplicate
    # player — ``Team.is_valid_roster``) may enter a Tournament; an incomplete
    # roster cannot field a 6v6 game. ``select_related`` the 6 slot FKs so
    # ``is_valid_roster`` touches no extra queries per Team.
    available_teams = [
        t
        for t in Team.objects.regular().select_related(
            "slot_commander",
            "slot_heavy",
            "slot_scout_1",
            "slot_scout_2",
            "slot_medic",
            "slot_ammo",
        )
        if t.is_valid_roster
    ]

    if request.method != "POST":
        return render(
            request,
            "matches/tournament_create.html",
            {"form": None, "available_teams": available_teams},
        )

    name = (request.POST.get("name") or "").strip()
    selected_ids = request.POST.getlist("teams")

    # LG-02x-1 — orthogonal team-assembly axis. Parse early so the preset-only
    # participant-selection validation can be skipped for random_draw (a
    # random_draw Tournament is created in setup with an EMPTY pool — the user
    # fills the pool + runs the draw on the detail page).
    team_assembly = request.POST.get("team_assembly")
    if team_assembly not in ("preset", "random_draw"):
        team_assembly = "preset"

    try:
        generate_count = int(request.POST.get("generate_count") or 0)
    except (TypeError, ValueError):
        generate_count = 0
    try:
        generate_ppt = int(request.POST.get("generate_ppt") or 6)
    except (TypeError, ValueError):
        generate_ppt = 6
    # A tournament Team must field a full 6-player roster, so generated teams
    # always get at least 6 players — a smaller request is clamped up rather
    # than producing an unplayable participant.
    generate_ppt = max(6, generate_ppt)

    errors = []
    if not name:
        errors.append("Tournament name is required.")

    teams: list[Team] = []
    if team_assembly == "preset":
        if selected_ids:
            selected = list(
                Team.objects.filter(id__in=selected_ids).select_related(
                    "slot_commander",
                    "slot_heavy",
                    "slot_scout_1",
                    "slot_scout_2",
                    "slot_medic",
                    "slot_ammo",
                )
            )
            # Reject any selected Team without a full valid roster (defends a
            # tampered/stale POST of an id the filtered select list never
            # offered).
            ineligible = [t for t in selected if not t.is_valid_roster]
            if ineligible:
                names = ", ".join(t.name for t in ineligible)
                errors.append(
                    "These teams don't have a full 6-player roster and can't "
                    f"enter a tournament: {names}. Complete their rosters first."
                )
            teams.extend(selected)

        if generate_count and generate_count > 0:
            rng = random.Random()
            team_names_pool = list(TEAM_NAMES)
            player_names_pool = list(PLAYER_NAMES)
            generated = _generate_teams(
                generate_count,
                generate_ppt if generate_ppt > 0 else 6,
                rng=rng,
                mean=50,
                std_dev=15,
                team_names_pool=team_names_pool,
                player_names_pool=player_names_pool,
            )
            teams.extend(generated)

        if len(teams) < 4:
            errors.append("A tournament requires at least 4 teams.")

    if errors:
        for err in errors:
            messages.error(request, err)
        return render(
            request,
            "matches/tournament_create.html",
            {"form": None, "available_teams": available_teams},
        )

    # LG-02b-2 — four per-depth Series length slots (final / semifinal /
    # quarterfinal / earlier rounds), each int-coerced with a forgiving fallback
    # to 1 then forced into {1,3,5}. No monotonicity — the four are independent.
    def _parse_series_length(field: str) -> int:
        try:
            value = int(request.POST.get(field) or 1)
        except (TypeError, ValueError):
            value = 1
        if value not in (1, 3, 5):
            value = 1
        return value

    final_series_length = _parse_series_length("final_series_length")
    semifinal_series_length = _parse_series_length("semifinal_series_length")
    quarterfinal_series_length = _parse_series_length("quarterfinal_series_length")
    earlier_series_length = _parse_series_length("earlier_series_length")

    # LG-02c — bracket format. Forgiving fallback (mirrors the series-length
    # parses): only the known formats are accepted, anything else (absent,
    # tampered) falls back to single-elimination.
    tournament_format = request.POST.get("format")
    if tournament_format not in (
        "single_elimination",
        "double_elimination",
        "round_robin",
        "round_robin_double_elim",
        "swiss",
    ):
        tournament_format = "single_elimination"

    # LG-02c (RR->DE) — the (wb, lb) advancer combo. A single rrde_combo select
    # enumerating the 6 locked shape combos ("4/0", "4/2", "8/0", "8/4",
    # "16/0", "16/8"). For an RRDE create an absent/invalid combo falls back to
    # the first combo (4, 0); for any non-RRDE create the combo is ignored and
    # both advancers persist 0.
    wb_advancers, lb_advancers = _parse_rrde_combo(
        request.POST.get("rrde_combo"), tournament_format
    )

    # LG-02c (Swiss) — the requested round count (0 = auto). Persisted for every
    # create (harmless — only read at lock when format == "swiss").
    swiss_rounds = _parse_swiss_rounds(request.POST.get("swiss_rounds"))

    # LG-02x-1 — role-assignment mode (meaningful only for random_draw).
    # Forgiving fallback to the default.
    role_assignment_mode = request.POST.get("role_assignment_mode")
    if role_assignment_mode not in ("random", "per_tier"):
        role_assignment_mode = "random"

    tournament = Tournament.objects.create(
        name=name,
        state="setup",
        format=tournament_format,
        final_series_length=final_series_length,
        semifinal_series_length=semifinal_series_length,
        quarterfinal_series_length=quarterfinal_series_length,
        earlier_series_length=earlier_series_length,
        wb_advancers=wb_advancers,
        lb_advancers=lb_advancers,
        swiss_rounds=swiss_rounds,
        team_assembly=team_assembly,
        role_assignment_mode=role_assignment_mode,
    )

    # Default Seeding via mean active-player overall_rating. For random_draw the
    # pool is empty at create — participants are seeded by the draw, not here.
    if team_assembly == "preset":
        team_ratings = [(t.id, _team_mean_rating(t)) for t in teams]
        seed_order = default_seed_order(team_ratings)
        team_by_id = {t.id: t for t in teams}
        for idx, team_id in enumerate(seed_order, start=1):
            TournamentParticipant.objects.create(
                tournament=tournament, team=team_by_id[team_id], seed=idx
            )

    return redirect("tournament_detail", tournament_id=tournament.id)


def _series_games(node: BracketNode, series_matches: list) -> list:
    """Per-Match scores for a node's played Series Matches, in node-slot order.

    Each entry is ``{game_number, match_id, score_a, score_b}`` where
    ``score_a`` / ``score_b`` are the 6-point :func:`match_score` from the
    perspective of the node's ``team_a`` / ``team_b`` slots (mapped through the
    Match's physical sides — ``team_a`` plays red in a tournament Match). Skips
    SeriesMatch rows with no played ``match``. Empty list for an unplayed node.
    """
    games = []
    for sm in series_matches:
        match = sm.match
        if match is None:
            continue
        red_score, blue_score = match_score(
            match.red_rounds_won,
            match.blue_rounds_won,
            match.winner_id,
            match.team_red_id,
            match.team_blue_id,
        )
        if match.team_red_id == node.team_a_id:
            score_a, score_b = red_score, blue_score
        else:
            score_a, score_b = blue_score, red_score
        games.append(
            {
                "game_number": sm.game_number,
                "match_id": match.id,
                "score_a": score_a,
                "score_b": score_b,
            }
        )
    return games


def _build_rounds(tournament: Tournament) -> dict:
    """Group nodes into a 3-key dict (one slice per sub-bracket) for the tree
    render: ``{"winners": [...], "losers": [...], "grand_final": [...]}``, each
    slice a list of ``{bracket_round, nodes}`` ordered by round.

    For a single-elim Tournament ``"losers"`` and ``"grand_final"`` are empty
    lists and ``"winners"`` carries the whole tree (so the WB section renders
    exactly the old bracket).
    """
    nodes = list(
        tournament.nodes.select_related("team_a", "team_b", "winner")
        .prefetch_related("series_matches__match")
        .order_by("bracket_round", "position")
    )
    # by_section[bracket_type][bracket_round] -> list of node view-dicts.
    by_section: dict[str, dict[int, list]] = {
        "winners": {},
        "losers": {},
        "grand_final": {},
    }
    for node in nodes:
        # LG-02b — Series win counts per slot, derived from SeriesMatch rows.
        series_matches = list(node.series_matches.all())
        wins_a, wins_b = count_series_wins(
            series_matches, node.team_a_id, node.team_b_id
        )
        view_dict = {
            "bracket_round": node.bracket_round,
            "position": node.position,
            "bracket_type": node.bracket_type,
            "team_a": node.team_a,
            "team_b": node.team_b,
            "seed_a": node.seed_a,
            "seed_b": node.seed_b,
            "is_bye": node.is_bye,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "series_length": node.series_length,
            "series_matches": series_matches,
            "games": _series_games(node, series_matches),
            "winner": node.winner,
        }
        section = by_section.setdefault(node.bracket_type, {})
        section.setdefault(node.bracket_round, []).append(view_dict)
    return {
        bt: [{"bracket_round": r, "nodes": rounds[r]} for r in sorted(rounds)]
        for bt, rounds in by_section.items()
    }


def _build_rr_crosstable(tournament: Tournament, rows: list) -> list:
    """LG-02c — N x N round-robin crosstable in Standings order.

    ``rows`` is the already-computed ``round_robin_standings()`` output (passed
    in by the caller so it is computed once and shared with the standings table).

    Each persisted RR node carries the two legs of a fixture across two nodes
    (round_number 1 and round_number 2). The node only stores
    bracket_round=matchday + position, so the leg's round_number is recovered
    by re-deriving the schedule (``generate_schedule(team_ids)``) and matching
    each persisted node by ``(matchday, position-within-matchday)`` — the exact
    key ``lock_and_build`` used.

    Cell-mapping rule: leg round_number==1 -> cell[team_a][team_b]; leg
    round_number==2 -> cell[team_b][team_a]; diagonal blank.

    Returns ``list[{"team": Team, "cells": [<cell | None>, ...]}]`` — one row
    per team in Standings order, each ``cells`` the N-long row of per-opponent
    cells in the same team order. A ``<cell>`` is ``None`` (diagonal) or a dict
    ``{"opponent_team_id", "leg1", "leg2"}`` where a leg is ``None`` or
    ``{"node_id", "team_score", "opp_score", "match_team", "match_opp",
    "played", "match_id"}`` from the row team's perspective — ``team_score`` /
    ``opp_score`` are total points, ``match_team`` / ``match_opp`` the 6-point
    Match score.
    """
    participants = list(tournament.participants.select_related("team"))
    team_by_id = {p.team_id: p.team for p in participants}
    team_ids = [p.team_id for p in participants]

    # A crosstable needs at least 2 teams. A random_draw Tournament sits in
    # `setup` with NO participants until the draw runs, so the detail page is
    # rendered (pool stage) before any team exists — short-circuit to an empty
    # crosstable rather than letting `generate_schedule([])` raise.
    if len(team_ids) < 2:
        return []

    # Order teams by Standings rank (rows already ranked); fall back to any
    # enrolled team not yet ranked (defensive — compute_standings returns all).
    ordered_team_ids = [r.team_id for r in rows]
    for tid in team_ids:
        if tid not in ordered_team_ids:
            ordered_team_ids.append(tid)
    order_index = {tid: i for i, tid in enumerate(ordered_team_ids)}
    n = len(ordered_team_ids)

    # Map each persisted RR node to its fixture's round_number via the
    # (matchday, position-within-matchday) key the builder used.
    fixtures = generate_schedule(team_ids)
    fixture_round_by_key: dict[tuple[int, int], int] = {}
    pos_by_matchday: dict[int, int] = {}
    for fixture in fixtures:
        pos = pos_by_matchday.get(fixture.matchday, 0)
        fixture_round_by_key[(fixture.matchday, pos)] = fixture.round_number
        pos_by_matchday[fixture.matchday] = pos + 1

    nodes = list(
        tournament.nodes.filter(bracket_type="round_robin")
        .select_related("team_a", "team_b")
        .prefetch_related("series_matches")
        .order_by("bracket_round", "position")
    )

    # cell[row_index][col_index] -> {"opponent_team_id", "leg1", "leg2"} | None
    grid: list[list] = [[None] * n for _ in range(n)]

    def _leg_dict(node, row_is_team_a: bool):
        series = list(node.series_matches.all())
        played = bool(node.winner_id is not None and series and series[0].match)
        team_score = None
        opp_score = None
        match_team = None  # 6-point Match score (this leg), row-team perspective
        match_opp = None
        match_id = None
        if played:
            match = series[0].match
            match_id = match.id
            red_match, blue_match = match_score(
                match.red_rounds_won,
                match.blue_rounds_won,
                match.winner_id,
                match.team_red_id,
                match.team_blue_id,
            )
            # Read from the persisted Match to be side-faithful: map node.team_a
            # / team_b to physical points by which physical side each held.
            if match.team_red_id == node.team_a_id:
                a_points, b_points = match.red_total_points, match.blue_total_points
                a_match, b_match = red_match, blue_match
            else:
                a_points, b_points = match.blue_total_points, match.red_total_points
                a_match, b_match = blue_match, red_match
            if row_is_team_a:
                team_score, opp_score = a_points, b_points
                match_team, match_opp = a_match, b_match
            else:
                team_score, opp_score = b_points, a_points
                match_team, match_opp = b_match, a_match
        return {
            "node_id": node.id,
            "team_score": team_score,
            "opp_score": opp_score,
            "match_team": match_team,
            "match_opp": match_opp,
            "played": played,
            "match_id": match_id,
        }

    for node in nodes:
        a_id = node.team_a_id
        b_id = node.team_b_id
        if a_id not in order_index or b_id not in order_index:
            continue
        # Recover the leg's round_number; position is the per-matchday index.
        # Re-derive the position within the node's matchday from node ordering.
        round_number = fixture_round_by_key.get((node.bracket_round, node.position))
        if round_number == 1:
            row_id, col_id = a_id, b_id
        elif round_number == 2:
            row_id, col_id = b_id, a_id
        else:
            # Defensive: unmatched node — skip rather than crash.
            continue
        ri = order_index[row_id]
        ci = order_index[col_id]
        cell = grid[ri][ci]
        if cell is None:
            cell = {"opponent_team_id": col_id, "leg1": None, "leg2": None}
            grid[ri][ci] = cell
        # The row team is whichever of (a, b) equals row_id.
        row_is_team_a = row_id == a_id
        leg = _leg_dict(node, row_is_team_a)
        if round_number == 1:
            cell["leg1"] = leg
        else:
            cell["leg2"] = leg

    crosstable = []
    for ri, row_id in enumerate(ordered_team_ids):
        crosstable.append({"team": team_by_id.get(row_id), "cells": grid[ri]})
    return crosstable


def _build_swiss_rounds(tournament: Tournament) -> list:
    """LG-02c (Swiss) — group the Swiss nodes by ``bracket_round`` into the
    per-round pairing sections for the detail render.

    Returns ``list[{"round_number": int, "pairings": [<node_view_dict>, ...]}]``
    in ``bracket_round`` order, each ``<node_view_dict>`` the SAME shape
    ``_build_rounds`` builds for RR/elim nodes (so the node-card include is
    reused). Each section dict also carries the include-friendly aliases
    ``bracket_round`` (== round_number) and ``nodes`` (== pairings) so it can be
    handed straight to ``matches/_tournament_round.html``. Empty list for a
    non-Swiss Tournament.
    """
    nodes = list(
        tournament.nodes.filter(bracket_type="swiss")
        .select_related("team_a", "team_b", "winner")
        .prefetch_related("series_matches__match")
        .order_by("bracket_round", "position")
    )
    by_round: dict[int, list] = {}
    for node in nodes:
        series_matches = list(node.series_matches.all())
        wins_a, wins_b = count_series_wins(
            series_matches, node.team_a_id, node.team_b_id
        )
        view_dict = {
            "bracket_round": node.bracket_round,
            "position": node.position,
            "bracket_type": node.bracket_type,
            "team_a": node.team_a,
            "team_b": node.team_b,
            "seed_a": node.seed_a,
            "seed_b": node.seed_b,
            "is_bye": node.is_bye,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "series_length": node.series_length,
            "series_matches": series_matches,
            "games": _series_games(node, series_matches),
            "winner": node.winner,
        }
        by_round.setdefault(node.bracket_round, []).append(view_dict)
    return [
        {
            "round_number": r,
            "pairings": by_round[r],
            # Include-friendly aliases for matches/_tournament_round.html.
            "bracket_round": r,
            "nodes": by_round[r],
        }
        for r in sorted(by_round)
    ]


def _tournament_stage(tournament: Tournament, has_finals: bool) -> str:
    """LG-02c (RR->DE) — derive the display stage (NOT stored).

    - ``"setup"`` when state == setup.
    - ``"seeding"`` when RRDE, active, and no finals nodes exist yet.
    - ``"finals"`` when RRDE and the finals nodes exist.
    - ``"completed"`` when state == completed.

    For non-RRDE formats the badge does not render meaningfully; we return a
    benign value (the format name) so the key is always present.
    """
    if tournament.state == "setup":
        return "setup"
    if tournament.state == "completed":
        return "completed"
    if tournament.format == "round_robin_double_elim":
        return "finals" if has_finals else "seeding"
    if tournament.format == "swiss":
        return "swiss"
    return tournament.format


def _rrde_cut_labels(tournament: Tournament, rr_rows: list) -> dict:
    """LG-02c (RR->DE) — team_id -> "wb" | "lb" | "out" cut markers, built from
    ``round_robin_standings()`` rank order.

    Top ``wb_advancers`` rows -> "wb", next ``lb_advancers`` -> "lb", the rest
    -> "out". Computed only in the RRDE seeding stage (caller passes ``[]``
    otherwise so the result is empty).
    """
    labels: dict[int, str] = {}
    wb = tournament.wb_advancers
    lb = tournament.lb_advancers
    for i, row in enumerate(rr_rows):
        if i < wb:
            labels[row.team_id] = "wb"
        elif i < wb + lb:
            labels[row.team_id] = "lb"
        else:
            labels[row.team_id] = "out"
    return labels


def _detail_context(tournament: Tournament) -> dict:
    """Shared tournament_detail context (LG-02a keys + LG-02a-2 import keys +
    LG-02c RR / RR->DE keys)."""
    participants = list(tournament.participants.select_related("team").order_by("seed"))
    rounds = _build_rounds(tournament)
    next_node = tournament.find_next_playable_node()
    # LG-02c (RR->DE) — finals exist iff any non-RR node has been persisted.
    has_finals = tournament.nodes.exclude(bracket_type="round_robin").exists()
    stage = _tournament_stage(tournament, has_finals)
    # LG-02c — round-robin crosstable + standings (also rendered during the
    # RR->DE seeding stage). Empty for the elim formats.
    is_rr_like = tournament.format in ("round_robin", "round_robin_double_elim")
    cut_labels: dict[int, str] = {}
    if is_rr_like:
        # Compute the standings ONCE and feed every surface (the crosstable
        # orders teams by rank, the table renders the rows, the cut markers tag
        # them) — avoids a second multi-join + compute_standings pass per render.
        rr_rows = tournament.round_robin_standings()
        rr_crosstable = _build_rr_crosstable(tournament, rr_rows)
        # Pair each StandingsRow with its Team so the template can render the
        # team NAME (StandingsRow carries only team_id) — the LG-01
        # season-standings `rows_with_teams` precedent.
        team_by_id = {p.team_id: p.team for p in participants}
        rr_standings = [(row, team_by_id.get(row.team_id)) for row in rr_rows]
        # Cut markers only in the RRDE seeding stage.
        if tournament.format == "round_robin_double_elim" and stage == "seeding":
            cut_labels = _rrde_cut_labels(tournament, rr_rows)
    else:
        rr_crosstable = []
        rr_standings = []
    # LG-02c (Swiss) — per-round pairing sections + Buchholz-ranked standings.
    # Empty for non-Swiss formats so the template references them unconditionally.
    if tournament.format == "swiss":
        swiss_rounds_view = _build_swiss_rounds(tournament)
        swiss_rows = tournament.swiss_standings()
        team_by_id = {p.team_id: p.team for p in participants}
        swiss_standings = [(row, team_by_id.get(row.team_id)) for row in swiss_rows]
    else:
        swiss_rounds_view = []
        swiss_standings = []
    # LG-02x-1 — Random-Draw pool / draw surface. Empty / defaulted for preset.
    if tournament.team_assembly == "random_draw":
        pool_entries = list(
            tournament.player_entries.select_related("player", "drawn_team").order_by(
                "tier", "player_id"
            )
        )
        is_drawn = tournament.player_entries.filter(drawn_team__isnull=False).exists()
        all_players = list(Player.objects.order_by("name"))
    else:
        pool_entries = []
        is_drawn = False
        all_players = []
    return {
        "tournament": tournament,
        "participants": participants,
        "rounds": rounds,
        "next_node": next_node,
        "is_locked": tournament.is_locked,
        "can_play": tournament.state == "active" and next_node is not None,
        "import_form": RosterImportForm(),
        "import_row_errors": [],
        "rr_crosstable": rr_crosstable,
        "rr_standings": rr_standings,
        "tournament_stage": stage,
        "cut_labels": cut_labels,
        "swiss_rounds_view": swiss_rounds_view,
        "swiss_standings": swiss_standings,
        # LG-02x-1 — Random-Draw keys.
        "team_assembly": tournament.team_assembly,
        "role_assignment_mode": tournament.role_assignment_mode,
        "pool_entries": pool_entries,
        "pool_size": len(pool_entries),
        "is_drawn": is_drawn,
        "all_players": all_players,
        "pool_import_form": RosterImportForm(),
        "pool_import_row_errors": [],
    }


def tournament_detail(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Render the bracket tree + (in setup) the Seeding-edit form + play
    controls.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    return render(
        request, "matches/tournament_detail.html", _detail_context(tournament)
    )


@transaction.atomic
def tournament_reseed(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Persist a manually reordered Seeding (new seed ints from POST)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.is_locked:
        messages.error(request, "Seeding cannot be edited once the bracket is locked.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    participants = list(tournament.participants.all())
    # Collect (participant, new_seed) from POST keys seed_<team_id>.
    new_seeds: dict[int, int] = {}
    for participant in participants:
        raw = request.POST.get(f"seed_{participant.team_id}")
        if raw is None:
            continue
        try:
            new_seeds[participant.id] = int(raw)
        except (TypeError, ValueError):
            continue

    # Two-phase write to dodge the unique (tournament, seed) constraint: offset
    # every seed first, then write the final values.
    offset = 1000000
    for participant in participants:
        participant.seed = participant.seed + offset
        participant.save(update_fields=["seed"])
    for participant in participants:
        if participant.id in new_seeds:
            participant.seed = new_seeds[participant.id]
        else:
            participant.seed = participant.seed - offset
        participant.save(update_fields=["seed"])

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_lock(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Call ``tournament.lock_and_build()`` (setup->active, builds + persists
    nodes). On ValidationError redirect back with a flash.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    try:
        tournament.lock_and_build()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("tournament_detail", tournament_id=tournament.id)


def tournament_play_next(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Find next playable node, sim ONE Match, resolve winner (incl. tie-break),
    Advance, stamp champion if final. Delegates to ``play_next_node``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.state != "active":
        messages.error(request, "The tournament is not active.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    node = play_next_node(tournament)
    if node is None:
        messages.error(request, "No playable match is ready.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_import_participants(
    request: HttpRequest, tournament_id: int
) -> HttpResponse:
    """Import participants from a roster CSV (LG-00b reuse). Setup-only.

    Only brand-new Teams (``created_teams``) become participants; appended
    Teams are created/extended but NOT auto-added. The whole field is then
    re-seeded by talent.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.is_locked:
        messages.error(request, "Participants can only be imported during setup.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    form = RosterImportForm(request.POST, request.FILES)

    def _render_error(import_row_errors: list) -> HttpResponse:
        # Build the read-only detail context BEFORE flagging rollback — a
        # rolled-back atomic block forbids further queries, so set_rollback
        # must be the last DB-touching action before the render.
        ctx = _detail_context(tournament)
        ctx["import_form"] = form
        ctx["import_row_errors"] = import_row_errors
        transaction.set_rollback(True)
        return render(request, "matches/tournament_detail.html", ctx)

    if not form.is_valid():
        return _render_error([])

    try:
        parsed = parse_roster_csv(form.cleaned_data["csv_file"])
        _check_db_slot_collisions(parsed)
        created_teams, _appended_teams, _player_count = _apply_roster(parsed)
    except RosterImportError as exc:
        return _render_error(exc.errors)

    # Only brand-new Teams become participants (no uniq collision possible).
    existing_team_ids = set(tournament.participants.values_list("team_id", flat=True))
    next_seed = tournament.participants.count() + 1
    for team in created_teams:
        if team.id in existing_team_ids:
            continue
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=next_seed
        )
        next_seed += 1

    # Re-seed the WHOLE field by talent.
    participants = list(tournament.participants.select_related("team"))
    team_ratings = [(p.team_id, _team_mean_rating(p.team)) for p in participants]
    seed_order = default_seed_order(team_ratings)
    seed_by_team = {team_id: idx for idx, team_id in enumerate(seed_order, start=1)}

    # Two-phase offset write to dodge the uniq (tournament, seed) constraint.
    offset = 1000000
    for participant in participants:
        participant.seed = participant.seed + offset
        participant.save(update_fields=["seed"])
    for participant in participants:
        participant.seed = seed_by_team[participant.team_id]
        participant.save(update_fields=["seed"])

    return redirect("tournament_detail", tournament_id=tournament.id)


def tournament_play_all(request: HttpRequest, tournament_id: int) -> JsonResponse:
    """Enqueue the async Play Tournament job. POST-only. 202 + job JSON."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.state != "active":
        return JsonResponse({"error": "Tournament is not active."}, status=409)

    try:
        # retry=False so an unreachable broker raises OperationalError after a
        # single bounded attempt instead of retry-hanging the request.
        result = play_tournament_task.apply_async((tournament_id,), retry=False)
    except OperationalError:
        # The Celery broker (Redis) is unreachable. Return a clean JSON error
        # (503) instead of a 500 HTML page so the UI can show a clear message
        # rather than a JSON-parse failure on the error page.
        return JsonResponse(
            {
                "error": (
                    "Couldn't start the Play All job — the background task "
                    "queue is unavailable. Start a Celery worker + broker, or "
                    "run the server with LF_CELERY_EAGER=1 for local play."
                )
            },
            status=503,
        )
    return JsonResponse(
        {"job_id": result.id, "tournament_id": tournament.id}, status=202
    )


def _build_tournament_play_status_response(
    async_result: AsyncResult, *, tournament_id: int
) -> dict:
    """Locked 5-key polling JSON for a Play Tournament job."""
    state = async_result.state
    status = _celery_state_to_job_status(state)

    completed = 0
    total = 0
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
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)

    return {
        "status": status,
        "completed": completed,
        "total": total,
        "error": error,
        "tournament_id": tournament_id,
    }


def tournament_play_status(
    request: HttpRequest, tournament_id: int, job_id: str
) -> JsonResponse:
    """Poll a Play Tournament job. GET-only. Returns the locked 5-key JSON."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    get_object_or_404(Tournament, pk=tournament_id)
    async_result = AsyncResult(job_id)
    return JsonResponse(
        _build_tournament_play_status_response(
            async_result, tournament_id=tournament_id
        )
    )


# ---------------------------------------------------------------------------
# LG-02x-1 — Random-Draw player-pool intake, draw, re-roll, hand-edit.
# ---------------------------------------------------------------------------


def _pool_setup_guard(request: HttpRequest, tournament: Tournament):
    """Shared guard for the pool-mutating views: reject once the bracket is
    locked. Returns a redirect HttpResponse when blocked, else None.
    """
    if tournament.is_locked:
        messages.error(request, "The player pool can only be edited during setup.")
        return redirect("tournament_detail", tournament_id=tournament.id)
    return None


@transaction.atomic
def tournament_pool_add_existing(
    request: HttpRequest, tournament_id: int
) -> HttpResponse:
    """Add selected existing Players to the Random-Draw pool. POST-only,
    setup-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    player_ids = request.POST.getlist("players")
    existing = set(tournament.player_entries.values_list("player_id", flat=True))
    for player in Player.objects.filter(id__in=player_ids):
        if player.id in existing:
            continue
        TournamentPlayerEntry.objects.create(tournament=tournament, player=player)
        existing.add(player.id)

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_pool_generate(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Generate N fresh Players (LG-00 pure generator) on the Free Agents Team
    and add them to the pool. POST-only, setup-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    try:
        count = int(request.POST.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    try:
        mean_val = int(request.POST.get("mean") or 50)
    except (TypeError, ValueError):
        mean_val = 50
    try:
        std_dev = int(request.POST.get("std_dev") or 15)
    except (TypeError, ValueError):
        std_dev = 15

    if count <= 0:
        messages.error(request, "Enter how many players to generate.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    rng = random.Random()
    free_agents = get_free_agents_team()
    name_pool = list(PLAYER_NAMES)
    rng.shuffle(name_pool)

    for i in range(count):
        if name_pool:
            base_name = name_pool.pop()
        else:
            base_name = f"Player {i + 1}"
        # Dedupe within the Free Agents Team (unique_together team+name).
        name = base_name
        k = 2
        while Player.objects.filter(team=free_agents, name=name).exists():
            name = f"{base_name} #{k}"
            k += 1
        stats = draw_stats(rng, mean_val, std_dev)
        preferred_roles = draw_preferred_roles(rng)
        player = Player.objects.create(
            team=free_agents,
            name=name,
            preferred_roles=preferred_roles,
            **stats,
        )
        TournamentPlayerEntry.objects.create(tournament=tournament, player=player)

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_pool_import(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Import pool Players from a roster CSV (LG-00b reuse). Each CSV ROW = one
    pool Player on the Free Agents Team; team-grouping is IGNORED. POST-only,
    setup-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    form = RosterImportForm(request.POST, request.FILES)

    def _render_error(pool_import_row_errors: list) -> HttpResponse:
        # Build the read-only detail context BEFORE flagging rollback — a
        # rolled-back atomic block forbids further queries.
        ctx = _detail_context(tournament)
        ctx["pool_import_form"] = form
        ctx["pool_import_row_errors"] = pool_import_row_errors
        transaction.set_rollback(True)
        return render(request, "matches/tournament_detail.html", ctx)

    if not form.is_valid():
        return _render_error([])

    try:
        parsed = parse_roster_csv(form.cleaned_data["csv_file"])
    except RosterImportError as exc:
        return _render_error(exc.errors)

    free_agents = get_free_agents_team()
    # Each ParsedRow -> one pool Player. The CSV team / role columns are NOT
    # used (slots are assigned per-Round by the draw; the pool is the team).
    for row in parsed.rows:
        name = row.name
        k = 2
        while Player.objects.filter(team=free_agents, name=name).exists():
            name = f"{row.name} #{k}"
            k += 1
        player = Player.objects.create(
            team=free_agents,
            name=name,
            preferred_roles=row.preferred_roles,
            **row.profile,
            **row.stats,
        )
        TournamentPlayerEntry.objects.create(tournament=tournament, player=player)

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_pool_remove(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Remove a pool entry (by player_id). POST-only, setup-only."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    player_id = request.POST.get("player_id")
    if player_id:
        tournament.player_entries.filter(player_id=player_id).delete()

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_draw(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Run the tier-balanced draw: validate the pool, build drawn Teams +
    participants, fill each entry's tier + drawn_team. Re-runnable (re-roll)
    while in setup. POST-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    entries = list(tournament.player_entries.select_related("player"))
    n = len(entries)
    if n % 6 != 0 or n < 24:
        messages.error(
            request,
            "The pool must be divisible by 6 and have at least 24 players "
            f"(>= 4 teams). It currently has {n}.",
        )
        return redirect("tournament_detail", tournament_id=tournament.id)

    # Build the (player_id, overall_rating) pool list.
    pool = [(e.player_id, e.player.overall_rating) for e in entries]
    plans = compute_draw(pool)

    # Re-roll cleanup: drop any prior drawn Teams + participants for this
    # tournament and null the entries' tier / drawn_team.
    prior_team_ids = list(
        tournament.player_entries.filter(drawn_team__isnull=False)
        .values_list("drawn_team_id", flat=True)
        .distinct()
    )
    if prior_team_ids:
        tournament.participants.filter(team_id__in=prior_team_ids).delete()
        tournament.player_entries.update(tier=None, drawn_team=None)
        Team.objects.filter(id__in=prior_team_ids, is_draw_team=True).delete()

    entry_by_player = {e.player_id: e for e in entries}
    for plan in plans:
        team = Team.objects.create(
            name=f"Draw Team {plan.team_index + 1}", is_draw_team=True
        )
        # Initial valid assignment: tier order -> ROLE_SLOTS order (any valid
        # no-duplicate assignment satisfies the relaxed roster_errors).
        for slot, player_id in zip(ROLE_SLOTS, plan.player_ids):
            setattr(team, f"slot_{slot}_id", player_id)
        team.save()
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=plan.team_index + 1
        )
        for player_id, tier in zip(plan.player_ids, plan.tiers):
            entry = entry_by_player[player_id]
            entry.tier = tier
            entry.drawn_team = team
            entry.save(update_fields=["tier", "drawn_team"])

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_draw_edit(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Admin hand-edit of a drawn entry's tier / drawn_team (the variation
    mechanism — the draw itself is deterministic). POST-only, setup-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    player_id = request.POST.get("player_id")
    entry = tournament.player_entries.filter(player_id=player_id).first()
    if entry is None:
        messages.error(request, "That player is not in the pool.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    update_fields = []
    raw_tier = request.POST.get("tier")
    if raw_tier is not None and raw_tier != "":
        try:
            entry.tier = int(raw_tier)
            update_fields.append("tier")
        except (TypeError, ValueError):
            pass
    raw_team = request.POST.get("drawn_team")
    if raw_team is not None and raw_team != "":
        team = Team.objects.filter(id=raw_team, is_draw_team=True).first()
        if team is not None:
            entry.drawn_team = team
            update_fields.append("drawn_team")
    if update_fields:
        entry.save(update_fields=update_fields)

    return redirect("tournament_detail", tournament_id=tournament.id)
