"""LG-02a-2 — Tournament per-node resolve/advance engine.

Extracts the per-node simulate/resolve/advance body that previously lived
inline in ``matches.tournament_views.tournament_play_next``. Both the sync
view and the async Celery task drive the bracket through this single
``play_next_node`` entry point.
"""

import random

from django.db import transaction

from .bracket import advance_loser, advance_winner, break_tie, series_winner_slot
from .draw import (
    ROLE_SLOTS,
    build_per_tier_role_assignment,
    build_random_role_assignment,
)
from .models import (
    BracketNode,
    SeriesMatch,
    Tournament,
    _node_to_dict,
    count_series_wins,
)


def _team_tier_map(tournament: Tournament, team) -> dict[int, int]:
    """LG-02x-1 — ``{tier: player_id}`` for a drawn Team, from its
    ``TournamentPlayerEntry`` rows in this Tournament.
    """
    return {
        entry.tier: entry.player_id
        for entry in tournament.player_entries.filter(drawn_team=team)
        if entry.tier is not None
    }


def _apply_role_assignment(team, slot_to_player: dict[str, int]) -> None:
    """LG-02x-1 — rewrite a drawn Team's 6 ``slot_*`` FKs IN MEMORY from a
    ``{slot_suffix: player_id}`` map. No ``.save()`` — the per-Round assignment
    is transient (the durable truth is the TournamentPlayerEntry tier).
    """
    for slot in ROLE_SLOTS:
        setattr(team, f"slot_{slot}_id", slot_to_player[slot])


def _build_role_hook(tournament: Tournament):
    """LG-02x-1 — build the ``before_round_hook`` closure for a Random-Draw
    Tournament.

    The closure ``(round_number, team_red, team_blue)`` re-assigns BOTH drawn
    Teams' role slots in memory before the round simulates. Roles re-draw every
    Round (the hook fires once per round). The role draw consumes a fresh
    ``random.Random()`` (tournament sims are non-deterministic — NOT the SIM-07
    seed chain).
    """
    mode = tournament.role_assignment_mode

    def hook(round_number, team_red, team_blue):
        rng = random.Random()
        tiers_red = _team_tier_map(tournament, team_red)
        tiers_blue = _team_tier_map(tournament, team_blue)
        if mode == "per_tier":
            tier_to_slot = build_per_tier_role_assignment(rng)
            for team, tier_map in ((team_red, tiers_red), (team_blue, tiers_blue)):
                slot_to_player = {
                    tier_to_slot[tier]: player_id
                    for tier, player_id in tier_map.items()
                }
                _apply_role_assignment(team, slot_to_player)
        else:  # "random" — each team shuffles independently.
            for team, tier_map in ((team_red, tiers_red), (team_blue, tiers_blue)):
                # tier_player_ids in tier order (index 0 = tier 1 .. 5 = tier 6).
                tier_player_ids = [tier_map[tier] for tier in range(1, 7)]
                slot_to_player = build_random_role_assignment(tier_player_ids, rng)
                _apply_role_assignment(team, slot_to_player)

    return hook


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
    # this Match's decisive winner (tie-break on a true tie). LG-02x-1 —
    # Random-Draw tournaments re-assign drawn-Team role slots every Round via a
    # before_round_hook; the preset path stays byte-identical (no hook).
    if tournament.team_assembly == "random_draw":
        hook = _build_role_hook(tournament)
        match = BatchSimulator().simulate_match(
            node.team_a,
            node.team_b,
            match_type="tournament",
            before_round_hook=hook,
        )
    else:
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

    # 7. Series clinched -> stamp the node winner (and identify the loser).
    if slot == "a":
        winner_team = node.team_a
        winner_seed = node.seed_a
        loser_team = node.team_b
        loser_seed = node.seed_b
    else:
        winner_team = node.team_b
        winner_seed = node.seed_b
        loser_team = node.team_a
        loser_seed = node.seed_a
    node.winner = winner_team
    node.save(update_fields=["winner"])

    # 7a. Round-robin (seeding-stage) node: every RR node has advances_to=None,
    # so the elim "crown on advances_to is None" rule would wrongly crown on the
    # FIRST resolved node. SKIP the advance/crown block entirely. Dispatch on
    # format: a plain round-robin completes once every RR node has a winner; a
    # round-robin -> double-elim builds the deferred DE finals once the last RR
    # node resolves (the tournament stays active; the next play_next_node call
    # finds the first playable DE finals node). A DE-stage node
    # (winners/losers/grand_final) of an RRDE tournament falls through to the
    # UNCHANGED elim block below.
    if node.bracket_type == "round_robin":
        if tournament.format == "round_robin":
            tournament.complete_round_robin_if_finished()
        elif tournament.format == "round_robin_double_elim":
            tournament.build_de_finals_if_rr_finished()
        return node

    # 7b. LG-02c (Swiss) — a Swiss node has advances_to=None, so the elim
    # "crown on advances_to is None" rule would wrongly crown on the FIRST
    # resolved node. SKIP the advance/crown block entirely; build the next round
    # (or crown) only when the CURRENT round's last node resolves.
    if node.bracket_type == "swiss":
        tournament.advance_swiss_if_round_finished()
        return node

    # 8. Flatten the bracket (LG-02c widens select_related to loser_advances_to).
    # Exclude round_robin nodes: in an RR->DE tournament the (resolved) Seeding
    # stage nodes share (bracket_round, position) coords with the deferred-built
    # Finals nodes (both number from 1), and advance_winner matches on
    # (bracket_round, position) bracket-type-blind — a stray round_robin node
    # (advances_to=None) would shadow the real WB/LB node and stall Advancement.
    flat = [
        _node_to_dict(n)
        for n in tournament.nodes.exclude(bracket_type="round_robin")
        .select_related("advances_to", "loser_advances_to")
        .prefetch_related("series_matches")
    ]

    # 8a. Winner advance. advance_winner stays the engine's winner-mutation
    # source (single-elim contract + the atomic-boundary guard tests patch it),
    # but the engine resolves the PARENT NODE + destination SLOT via the resolved
    # node's OWN advances_to / advances_to_slot ORM fields — unambiguous across
    # brackets (a WB and an LB node can share (bracket_round, position), so
    # advance_winner's 2-tuple flat search is not bracket-safe; for single-elim
    # the node fields and the mutation agree exactly).
    win_muts = advance_winner(
        flat, (node.bracket_round, node.position), winner_team.id, winner_seed
    )
    if win_muts and node.advances_to_id is not None and node.advances_to_slot:
        _fill_slot(node.advances_to, node.advances_to_slot, winner_team, winner_seed)

    # 8b. Loser Drop (DE only): WB or GF1 node whose loser has a destination.
    # advance_loser is the pure contract function (it keys on the full
    # (bracket_type, round, position) triple, so it is bracket-safe); the engine
    # applies its mutation via the node's own loser_advances_to FK.
    if (
        node.bracket_type in ("winners", "grand_final")
        and node.loser_advances_to_id is not None
        and loser_team is not None
    ):
        lose_muts = advance_loser(
            flat,
            (node.bracket_type, node.bracket_round, node.position),
            loser_team.id,
            loser_seed,
        )
        if lose_muts:
            _fill_slot(
                node.loser_advances_to,
                node.loser_advances_to_slot,
                loser_team,
                lose_muts[0]["seed"],
            )
            # The Drop may complete an LB node whose other feeder was a WB Bye —
            # collapse those Drop byes so the surviving team auto-advances.
            _collapse_drop_byes(tournament)

    # 8c. Grand-final resolution (the Bracket reset). GF1 is the grand-final node
    # that still advances (its advances_to points at GF2). GF1 slot "a" is the WB
    # champion, slot "b" the LB champion.
    if node.bracket_type == "grand_final" and node.advances_to_id is not None:
        if slot == "a":
            # WB champion won GF1 -> beating the LB champ twice is unnecessary.
            # The Bracket reset auto-resolves GF2 inert (never played): stamp its
            # winner AND mark it is_bye (bye-style) so find_next_node never
            # returns it; then crown the champion immediately.
            gf2 = node.advances_to
            gf2.winner = winner_team
            gf2.is_bye = True
            gf2.save(update_fields=["winner", "is_bye"])
            tournament.champion = winner_team
            tournament.state = "completed"
            tournament.save(update_fields=["champion", "state"])
        # LB champion won GF1 -> both teams have been advanced into GF2 (the
        # winner-advance + the loser-Drop above); GF2 is now playable. No
        # champion yet — leave the tournament active.
        return node

    # 9. Final node (single-elim final OR GF2) -> stamp champion + complete.
    if node.advances_to_id is None:
        tournament.champion = winner_team
        tournament.state = "completed"
        tournament.save(update_fields=["champion", "state"])

    # 10. Return the resolved node.
    return node


def _fill_slot(parent: "BracketNode", slot: str, team, seed) -> None:
    """Write ``team`` / ``seed`` into ``parent``'s ``slot`` (a/b) + persist.

    Saves ONLY the two fields of the slot being written. When a node feeds the
    same parent on both its winner-advance and loser-drop (the GF1->GF2 Bracket
    reset, where ``advances_to`` and ``loser_advances_to`` coincide), each call
    loads a separate ORM instance; updating only the written slot's fields means
    the second save cannot clobber the first slot the other instance wrote.
    """
    if slot == "a":
        parent.team_a = team
        parent.seed_a = seed
        parent.save(update_fields=["team_a", "seed_a"])
    else:
        parent.team_b = team
        parent.seed_b = seed
        parent.save(update_fields=["team_b", "seed_b"])


def _collapse_drop_byes(tournament: Tournament) -> None:
    """Apply any DE Drop-bye collapses ready after a loser Drop.

    A collapsed LB node (one slot filled, the other fed only by a WB Bye that
    produces no loser) has its surviving team auto-advanced and is itself marked
    ``is_bye`` / ``winner`` so ``find_next_node`` never returns it; that promotion
    can itself complete the next LB node, so the pass loops to a fixpoint.
    """
    changed = True
    while changed:
        changed = False
        nodes = list(
            tournament.nodes.exclude(bracket_type="round_robin")
            .select_related("advances_to", "loser_advances_to")
            .prefetch_related("series_matches")
        )
        flat = [_node_to_dict(n) for n in nodes]

        def feeder_is_dead(target_btype, target_br, target_pos, slot) -> bool:
            feeders = []
            for d in flat:
                adv = d.get("advances_to")
                if (
                    adv is not None
                    and d.get("advances_to_slot") == slot
                    and adv[0] == target_br
                    and adv[1] == target_pos
                ):
                    feeders.append(("winner", d))
                ld = d.get("loser_advances_to")
                if (
                    ld is not None
                    and d.get("loser_advances_to_slot") == slot
                    and ld[0] == target_btype
                    and ld[1] == target_br
                    and ld[2] == target_pos
                ):
                    feeders.append(("loser", d))
            if not feeders:
                return False
            for kind, d in feeders:
                if kind == "loser":
                    if not d.get("is_bye"):
                        return False
                else:
                    return False
            return True

        for n in nodes:
            if n.is_bye or n.winner_id is not None:
                continue
            a = n.team_a_id is not None
            b = n.team_b_id is not None
            if a == b:
                continue
            empty = "b" if a else "a"
            if not feeder_is_dead(n.bracket_type, n.bracket_round, n.position, empty):
                continue
            # Collapse: the surviving team auto-advances; mark the node a bye.
            surv_team = n.team_a if a else n.team_b
            surv_seed = n.seed_a if a else n.seed_b
            n.is_bye = True
            n.winner = surv_team
            n.save(update_fields=["is_bye", "winner"])
            if n.advances_to_id is not None and n.advances_to_slot:
                _fill_slot(n.advances_to, n.advances_to_slot, surv_team, surv_seed)
            changed = True
