"""LG-02a-2 — Tournament per-node resolve/advance engine.

Extracts the per-node simulate/resolve/advance body that previously lived
inline in ``matches.tournament_views.tournament_play_next``. Both the sync
view and the async Celery task drive the bracket through this single
``play_next_node`` entry point.
"""

from django.db import transaction

from .bracket import advance_winner, break_tie
from .models import BracketNode, Tournament, _node_to_dict


@transaction.atomic
def play_next_node(tournament: Tournament) -> "BracketNode | None":
    """Resolve and advance the next playable Bracket node.

    Returns the resolved BracketNode, or None when no node is playable
    (nothing ready / tournament complete). @transaction.atomic — one node =
    one transactional unit (ADR-0016 per-node-atomic precedent).
    """
    # Defer the heavy import inside the function (not at module scope).
    from .simulation.entrypoints import BatchSimulator

    node = tournament.find_next_playable_node()
    if node is None:
        return None

    # 1. Simulate one Match (team_a plays red, team_b plays blue).
    match = BatchSimulator().simulate_match(
        node.team_a, node.team_b, match_type="tournament"
    )
    node.match = match

    # 2-4. Resolve winner (with tie-break on a true tie).
    winner_team = match.winner
    if winner_team is None:
        best_a = max(match.red_round1_points, match.red_round2_points)
        best_b = max(match.blue_round1_points, match.blue_round2_points)
        winning_seed = break_tie(node.seed_a, best_a, node.seed_b, best_b)
        if winning_seed == node.seed_a:
            winner_team = node.team_a
            winner_seed = node.seed_a
        else:
            winner_team = node.team_b
            winner_seed = node.seed_b
    else:
        if winner_team.id == node.team_a_id:
            winner_seed = node.seed_a
        else:
            winner_seed = node.seed_b

    # 5. Set winner, save node.
    node.winner = winner_team
    node.save(update_fields=["match", "winner"])

    # 6. Compute + apply parent mutations.
    flat = [_node_to_dict(n) for n in tournament.nodes.select_related("advances_to")]
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

    # 7. Final node -> stamp champion + complete.
    if node.advances_to_id is None:
        tournament.champion = winner_team
        tournament.state = "completed"
        tournament.save(update_fields=["champion", "state"])

    # 8. Return the resolved node.
    return node
