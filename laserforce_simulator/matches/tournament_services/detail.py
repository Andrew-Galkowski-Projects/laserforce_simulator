"""ANG-01f — Tournament detail builders.

The bracket rounds, Series games, round-robin crosstable, Swiss rounds, stage
and RR->DE cut labels behind the Tournament detail page, moved out of the view
layer (ANG-01f-1) so the HTML views, the League playoffs screen and the API all
build them from one place. This module imports no view module.
"""

from teams.forms import RosterImportForm
from teams.models import Player

from matches.models import BracketNode, Tournament, count_series_wins
from matches.schedule_generator import generate_schedule
from matches.standings import match_score

# The three elimination sub-brackets the detail tree always renders, in order.
_ELIM_BRACKET_TYPES: tuple[str, ...] = ("winners", "losers", "grand_final")


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


def _node_view(node: BracketNode) -> dict:
    """The node-card view dict shared by the elimination tree, RR and Swiss
    sections (one shape, so the ``_tournament_round.html`` include is reused).
    """
    # LG-02b — Series win counts per slot, derived from SeriesMatch rows.
    series_matches = list(node.series_matches.all())
    wins_a, wins_b = count_series_wins(series_matches, node.team_a_id, node.team_b_id)
    return {
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


def _rounds_by_type(nodes) -> dict[str, list]:
    """Group node view dicts by ``bracket_type`` then ``bracket_round``.

    Returns ``{bracket_type: [{bracket_round, nodes}, ...]}`` with each slice
    ordered by round; the three elimination keys are always present.
    """
    by_section: dict[str, dict[int, list]] = {bt: {} for bt in _ELIM_BRACKET_TYPES}
    for node in nodes:
        section = by_section.setdefault(node.bracket_type, {})
        section.setdefault(node.bracket_round, []).append(_node_view(node))
    return {
        bt: [{"bracket_round": r, "nodes": rounds[r]} for r in sorted(rounds)]
        for bt, rounds in by_section.items()
    }


def _ordered_nodes(tournament: Tournament):
    """The Tournament's nodes with the relations every node card reads."""
    return (
        tournament.nodes.select_related("team_a", "team_b", "winner")
        .prefetch_related("series_matches__match")
        .order_by("bracket_round", "position")
    )


def build_rounds(tournament: Tournament, bracket_type: str) -> list:
    """One sub-bracket's rounds for the tree render: a list of
    ``{bracket_round, nodes}`` ordered by round, each node the node-card view
    dict. An empty list when the Tournament has no node of ``bracket_type``.
    """
    nodes = _ordered_nodes(tournament).filter(bracket_type=bracket_type)
    return _rounds_by_type(nodes).get(bracket_type, [])


def _build_all_rounds(tournament: Tournament) -> dict:
    """Every sub-bracket's rounds in one query: ``{"winners": [...], "losers":
    [...], "grand_final": [...], ...}``.

    For a single-elim Tournament ``"losers"`` and ``"grand_final"`` are empty
    lists and ``"winners"`` carries the whole tree (so the WB section renders
    exactly the old bracket).
    """
    return _rounds_by_type(list(_ordered_nodes(tournament)))


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
    :func:`build_rounds` builds for RR/elim nodes (so the node-card include is
    reused). Each section dict also carries the include-friendly aliases
    ``bracket_round`` (== round_number) and ``nodes`` (== pairings) so it can be
    handed straight to ``matches/_tournament_round.html``. Empty list for a
    non-Swiss Tournament.
    """
    return [
        {
            "round_number": section["bracket_round"],
            "pairings": section["nodes"],
            # Include-friendly aliases for matches/_tournament_round.html.
            "bracket_round": section["bracket_round"],
            "nodes": section["nodes"],
        }
        for section in build_rounds(tournament, "swiss")
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


def build_detail_context(tournament: Tournament) -> dict:
    """The Tournament detail page's template context (LG-02a keys + LG-02a-2
    import keys + LG-02c RR / RR->DE / Swiss keys + LG-02x-1 pool keys)."""
    participants = list(tournament.participants.select_related("team").order_by("seed"))
    rounds = _build_all_rounds(tournament)
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
