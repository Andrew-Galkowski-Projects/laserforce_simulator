"""LG-02a-2 — Tournament per-node resolve/advance engine.

Extracts the per-node simulate/resolve/advance body that previously lived
inline in ``matches.tournament_views.tournament_play_next``. Both the sync
view and the async Celery task drive the bracket through this single
``play_next_node`` entry point.
"""

from django.db import transaction

from .bracket import advance_winner, break_tie, series_winner_slot
from .models import (
    BracketNode,
    SeriesMatch,
    Tournament,
    _node_to_dict,
    count_series_wins,
)


@transaction.atomic
def play_next_node(tournament: Tournament) -> "BracketNode | None":
    """Simulate the NEXT Match of the next playable Bracket node's best-of-N
    Series, recording it as a SeriesMatch. Advances the node only once its
    Series clinches.

    Returns the node whose Series was advanced one Match (whether or not the
    Series has now clinched), or None when no node is playable (nothing ready
    / tournament complete). @transaction.atomic — one Match = one
    transactional unit (ADR-0016 per-node-atomic precedent, now per-Match).
    """
    # Defer the heavy import inside the function (not at module scope).
    from .simulation.entrypoints import BatchSimulator

    node = tournament.find_next_playable_node()
    if node is None:
        return None

    # 1-3. Simulate ONE Match (team_a plays red, team_b plays blue) and resolve
    # this Match's decisive winner (tie-break on a true tie).
    match = BatchSimulator().simulate_match(
        node.team_a, node.team_b, match_type="tournament"
    )
    match_winner = match.winner
    if match_winner is None:
        best_a = max(match.red_round1_points, match.red_round2_points)
        best_b = max(match.blue_round1_points, match.blue_round2_points)
        winning_seed = break_tie(node.seed_a, best_a, node.seed_b, best_b)
        match_winner = node.team_a if winning_seed == node.seed_a else node.team_b

    # 4. Record this Match as the next SeriesMatch game. Query the count fresh
    # off the model manager (NOT node.series_matches, whose reverse manager may
    # carry a stale prefetch cache from find_next_playable_node).
    next_game = SeriesMatch.objects.filter(node=node).count() + 1
    SeriesMatch.objects.create(
        node=node, match=match, game_number=next_game, winner=match_winner
    )

    # 5. Recompute Series wins over all of this node's SeriesMatch rows. A fresh
    # query is required: the row created at step 4 must be counted, and the
    # node's prefetched series_matches cache predates it.
    wins_a, wins_b = count_series_wins(
        SeriesMatch.objects.filter(node=node), node.team_a_id, node.team_b_id
    )

    # 6. Not yet clinched -> return without advancing (no winner write).
    slot = series_winner_slot(wins_a, wins_b, node.series_length)
    if slot is None:
        return node

    # 7. Series clinched -> stamp the node winner.
    if slot == "a":
        winner_team = node.team_a
        winner_seed = node.seed_a
    else:
        winner_team = node.team_b
        winner_seed = node.seed_b
    node.winner = winner_team
    node.save(update_fields=["winner"])

    # 8. Compute + apply parent mutations.
    flat = [
        _node_to_dict(n)
        for n in tournament.nodes.select_related("advances_to").prefetch_related(
            "series_matches"
        )
    ]
    mutations = advance_winner(
        flat, (node.bracket_round, node.position), winner_team.id, winner_seed
    )
    for mut in mutations:
        parent = tournament.nodes.get(
            bracket_round=mut["bracket_round"], position=mut["position"]
        )
        if mut["slot"] == "a":
            parent.team_a = winner_team
            parent.seed_a = mut["seed"]
        else:
            parent.team_b = winner_team
            parent.seed_b = mut["seed"]
        parent.save(update_fields=["team_a", "team_b", "seed_a", "seed_b"])

    # 9. Final node -> stamp champion + complete.
    if node.advances_to_id is None:
        tournament.champion = winner_team
        tournament.state = "completed"
        tournament.save(update_fields=["champion", "state"])

    # 10. Return the resolved node.
    return node
