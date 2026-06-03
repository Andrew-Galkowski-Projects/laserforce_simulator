"""LG-02a — pure single-elimination bracket structure + Seeding + byes.

This module owns bracket **STRUCTURE + Seeding + bye placement + tie-break
math** as plain data. Django objects are converted to plain ints/dicts at the
view/model boundary — the module never sees a ``Team``, ``Match``, or
``BracketNode`` ORM instance.

Frozen import allowlist (the ONLY modules this file may import):
``dataclasses``, ``typing``, ``math``, ``collections``. NO Django, NO ORM, NO
``random``, NO ``datetime``, NO I/O, NO logging. Enforced by a
``TestNoDjangoImportsLeaked`` subprocess fresh-import + ``sys.modules`` walk
(mirrors ``matches/standings.py`` / ``matches/schedule_generator.py``).
"""

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BracketNodeSpec:
    """One node in the built bracket, as plain data (pre-ORM)."""

    bracket_round: int  # 1-based
    position: int  # 0-based within the round
    team_a_id: Optional[int]  # participant team id, or None (empty/feeder slot)
    team_b_id: Optional[int]
    seed_a: Optional[int]  # Bracket seed parked with team_a_id
    seed_b: Optional[int]
    is_bye: bool
    advances_to: Optional[
        tuple[int, int]
    ]  # (bracket_round, position) parent; None=final
    advances_to_slot: Optional[str]  # "a" | "b" | None
    winner_id: Optional[int]  # pre-resolved for a bye node, else None


@dataclass(frozen=True)
class ParticipantSpec:
    """A participant as plain data crossing the view->pure-module boundary."""

    team_id: int
    seed: int  # 1-based Bracket seed


def default_seed_order(team_ratings: list[tuple[int, float]]) -> list[int]:
    """Default Seeding: team ids sorted by mean active-player overall_rating
    DESC, then team_id ASC as a deterministic tiebreak.

    ``team_ratings``: list of ``(team_id, mean_overall_rating)``. The view
    builds this from ``Team.active_players`` + ``Player.overall_rating`` (the
    SAME talent ranking the LG-01c draft-preview standings use). Returns team
    ids best-first (index 0 is Bracket seed 1).
    """
    ordered = sorted(team_ratings, key=lambda tr: (-tr[1], tr[0]))
    return [team_id for team_id, _rating in ordered]


def _seed_pairing(size: int) -> list[int]:
    """Return the standard 1vN seed-slot ordering for a bracket of ``size``.

    Produces the list of (top_seed, bottom_seed) 1-based seed pairs for the
    full first round of a power-of-two bracket, recursively built so the
    favourites only meet in the final (1 plays the lowest seed, 2 plays the
    next-lowest, the bracket halves never collide early).
    """
    # rounds[r] holds the seed order for a bracket of size 2**r.
    seeds = [1]
    while len(seeds) < size:
        n = len(seeds) * 2
        nxt = []
        for s in seeds:
            nxt.append(s)
            nxt.append(n + 1 - s)
        seeds = nxt
    return seeds


def build_bracket(participants: list[ParticipantSpec]) -> list[BracketNodeSpec]:
    """Build the full single-elimination bracket node-spec list for N >= 4
    participants (arbitrary N, byes for non-powers-of-2).

    Standard 1vN, 2v(N-1), ... seed pairing. The bracket size is the next
    power of two >= N. The top (size - N) Bracket seeds receive a Bye in
    round 1 (is_bye=True nodes pre-resolved to that seed's team_id, with
    winner_id set). advances_to / advances_to_slot wire every node to its
    parent; the final node has advances_to=None.

    Returns BracketNodeSpec list ordered by (bracket_round, position). Raises
    ValueError on len(participants) < 4 or duplicate seeds/team_ids.
    """
    if len(participants) < 4:
        raise ValueError("A tournament requires at least 4 participants.")

    seeds = [p.seed for p in participants]
    team_ids = [p.team_id for p in participants]
    if len(set(seeds)) != len(seeds):
        raise ValueError("Duplicate Bracket seeds in participants.")
    if len(set(team_ids)) != len(team_ids):
        raise ValueError("Duplicate team ids in participants.")

    n = len(participants)
    size = 2 ** math.ceil(math.log2(n))
    total_rounds = int(math.log2(size))

    # Map Bracket seed -> team_id (seeds need not be contiguous; sort to assign).
    by_seed = sorted(participants, key=lambda p: p.seed)
    # Re-rank to dense 1..N seeds for pairing while keeping each team's
    # ORIGINAL seed integer parked on the node.
    seed_slot = _seed_pairing(size)  # 1-based seeds in slot order, length == size

    # dense rank (1..N) -> ParticipantSpec
    rank_to_part = {i + 1: by_seed[i] for i in range(n)}

    # First-round slot occupancy: a slot whose dense-seed > N is empty (the
    # opponent got a bye). Pair slots two-at-a-time into round-1 nodes.
    specs = []

    # --- round 1 nodes ---
    r1_nodes = []  # (position, part_a|None, part_b|None)
    for pos in range(size // 2):
        slot_a = seed_slot[2 * pos]
        slot_b = seed_slot[2 * pos + 1]
        part_a = rank_to_part.get(slot_a)
        part_b = rank_to_part.get(slot_b)
        r1_nodes.append((pos, part_a, part_b))

    # Parent wiring: round r position p feeds round r+1 position p//2, slot
    # "a" when p is even else "b".
    def parent_of(bracket_round, position):
        if bracket_round == total_rounds:
            return None, None
        parent_pos = position // 2
        slot = "a" if position % 2 == 0 else "b"
        return (bracket_round + 1, parent_pos), slot

    for pos, part_a, part_b in r1_nodes:
        a_id = part_a.team_id if part_a else None
        b_id = part_b.team_id if part_b else None
        sa = part_a.seed if part_a else None
        sb = part_b.seed if part_b else None
        # A bye occurs when exactly one slot is filled.
        is_bye = (part_a is None) != (part_b is None)
        winner_id = None
        if is_bye:
            winner_id = a_id if a_id is not None else b_id
        adv, slot = parent_of(1, pos)
        specs.append(
            BracketNodeSpec(
                bracket_round=1,
                position=pos,
                team_a_id=a_id,
                team_b_id=b_id,
                seed_a=sa,
                seed_b=sb,
                is_bye=is_bye,
                advances_to=adv,
                advances_to_slot=slot,
                winner_id=winner_id,
            )
        )

    # --- later-round nodes (all slots empty at build time) ---
    for r in range(2, total_rounds + 1):
        node_count = size // (2**r)
        for pos in range(node_count):
            adv, slot = parent_of(r, pos)
            specs.append(
                BracketNodeSpec(
                    bracket_round=r,
                    position=pos,
                    team_a_id=None,
                    team_b_id=None,
                    seed_a=None,
                    seed_b=None,
                    is_bye=False,
                    advances_to=adv,
                    advances_to_slot=slot,
                    winner_id=None,
                )
            )

    specs.sort(key=lambda s: (s.bracket_round, s.position))
    return specs


def clinch_threshold(series_length: int) -> int:
    """Games one slot must win to clinch a best-of-``series_length`` Series."""
    return (series_length // 2) + 1


def series_winner_slot(wins_a: int, wins_b: int, series_length: int) -> Optional[str]:
    """Return the clinching slot ("a"/"b") for a best-of-N Series, or None
    when neither slot has reached the clinch threshold yet.
    """
    threshold = clinch_threshold(series_length)
    if wins_a >= threshold:
        return "a"
    if wins_b >= threshold:
        return "b"
    return None


def series_length_for_round(
    bracket_round: int,
    total_rounds: int,
    *,
    final: int,
    semifinal: int,
    quarterfinal: int,
    earlier: int,
) -> int:
    """Resolve a Bracket node's best-of-N Series length from its depth below
    the final.

    depth = total_rounds - bracket_round. depth 0 -> final, 1 -> semifinal,
    2 -> quarterfinal, depth >= 3 -> earlier. Pure integer dispatch; no
    validation of the four slot values (callers pass the locked 1/3/5 choices).
    """
    depth = total_rounds - bracket_round
    if depth == 0:
        return final
    elif depth == 1:
        return semifinal
    elif depth == 2:
        return quarterfinal
    else:
        return earlier


def find_next_node(nodes: list[dict]) -> Optional[dict]:
    """Return the lowest (bracket_round, position) node dict that is PLAYABLE:
    both team slots filled (team_a_id and team_b_id not None), is_bye False,
    and the Series has not yet been clinched. None when nothing is ready.

    ``nodes``: list of plain dicts (the view flattens BracketNode rows to
    dicts via ``_node_to_dict``). The pure function never touches the ORM.
    """
    playable = [
        nd
        for nd in nodes
        if nd.get("team_a_id") is not None
        and nd.get("team_b_id") is not None
        and not nd.get("is_bye")
        and series_winner_slot(
            nd.get("wins_a", 0), nd.get("wins_b", 0), nd.get("series_length", 1)
        )
        is None
    ]
    if not playable:
        return None
    playable.sort(key=lambda nd: (nd["bracket_round"], nd["position"]))
    return playable[0]


def advance_winner(
    nodes: list[dict], node_position: tuple[int, int], winner_id: int, winner_seed: int
) -> list[dict]:
    """Given the flattened node dicts and the (bracket_round, position) of a
    node that just resolved to winner_id (Bracket seed winner_seed), return
    the list of PARENT mutations needed.

    Each mutation is a dict
    ``{"bracket_round", "position", "slot", "team_id", "seed"}``.

    Empty list when the resolved node is the final (advances_to is None). The
    view applies these mutations to the ORM. Pure: computes the target slot
    from the resolved node's advances_to / advances_to_slot fields carried in
    ``nodes``.
    """
    br, pos = node_position
    resolved = None
    for nd in nodes:
        if nd["bracket_round"] == br and nd["position"] == pos:
            resolved = nd
            break
    if resolved is None:
        return []
    advances_to = resolved.get("advances_to")
    slot = resolved.get("advances_to_slot")
    if advances_to is None or slot is None:
        return []
    parent_round, parent_pos = advances_to
    return [
        {
            "bracket_round": parent_round,
            "position": parent_pos,
            "slot": slot,
            "team_id": winner_id,
            "seed": winner_seed,
        }
    ]


def resolve_bye_chain(nodes: list[dict]) -> list[dict]:
    """Cascade byes at build time: for every is_bye node, return the
    parent-slot mutations (same shape as advance_winner's output) that promote
    the bye team into the next round, recursively if a later-round node ends
    up with one filled slot and the other slot's feeder is also a bye.

    Used by build/persist so a top seed's bye is reflected in round 2
    immediately. Empty list when no byes.
    """
    # Work on a mutable copy keyed by (bracket_round, position).
    by_pos = {(nd["bracket_round"], nd["position"]): dict(nd) for nd in nodes}

    mutations = []

    def winner_of(nd):
        """Return (team_id, seed) of an auto-resolvable node, or None."""
        if nd.get("is_bye"):
            wid = nd.get("winner_id")
            if wid is None:
                # derive from the single filled slot
                if nd.get("team_a_id") is not None and nd.get("team_b_id") is None:
                    return nd["team_a_id"], nd.get("seed_a")
                if nd.get("team_b_id") is not None and nd.get("team_a_id") is None:
                    return nd["team_b_id"], nd.get("seed_b")
                return None
            # bye winner_id set; figure out which seed it carries
            if nd.get("team_a_id") == wid:
                return wid, nd.get("seed_a")
            if nd.get("team_b_id") == wid:
                return wid, nd.get("seed_b")
            return wid, (
                nd.get("seed_a") if nd.get("seed_a") is not None else nd.get("seed_b")
            )
        return None

    # Process round by round; a promotion can create a new auto-resolvable
    # node (one filled slot whose sibling feeder was a bye into an empty
    # opponent) one round up, so loop until no change.
    rounds = sorted({k[0] for k in by_pos})
    for r in rounds:
        for (br, pos), nd in sorted(by_pos.items()):
            if br != r:
                continue
            win = winner_of(nd)
            if win is None:
                continue
            wid, wseed = win
            advances_to = nd.get("advances_to")
            slot = nd.get("advances_to_slot")
            if advances_to is None or slot is None:
                continue
            parent_round, parent_pos = advances_to
            parent = by_pos.get((parent_round, parent_pos))
            if parent is None:
                continue
            # Apply the promotion into the working copy so a downstream
            # single-filled-slot node can itself become an auto-advance.
            if slot == "a":
                if parent["team_a_id"] is None:
                    parent["team_a_id"] = wid
                    parent["seed_a"] = wseed
            else:
                if parent["team_b_id"] is None:
                    parent["team_b_id"] = wid
                    parent["seed_b"] = wseed
            mutations.append(
                {
                    "bracket_round": parent_round,
                    "position": parent_pos,
                    "slot": slot,
                    "team_id": wid,
                    "seed": wseed,
                }
            )
            # If the parent now has exactly one filled slot AND its other
            # feeder is a bye-into-empty (i.e. the other child cannot ever
            # fill it), mark the parent as an auto-advance bye so the next
            # round-loop pass promotes it too.
            other_slot_filled = (
                parent["team_b_id"] is not None
                if slot == "a"
                else parent["team_a_id"] is not None
            )
            if not other_slot_filled:
                feeders_to_parent = [
                    c
                    for c in by_pos.values()
                    if c.get("advances_to") == (parent_round, parent_pos)
                ]
                other_feeder_slot = "b" if slot == "a" else "a"
                other_feeder = next(
                    (
                        c
                        for c in feeders_to_parent
                        if c.get("advances_to_slot") == other_feeder_slot
                    ),
                    None,
                )
                if other_feeder is None:
                    # No opponent feeder exists at all -> this is a true bye
                    # carry; the parent's single slot wins automatically.
                    parent["is_bye"] = True
                    parent["winner_id"] = wid

    return mutations


def stage_progress(nodes: list[dict]) -> tuple[int, int]:
    """STAGE-based progress for a Tournament bracket.

    Returns (completed_stages, total_stages):
      - total_stages   = max ``bracket_round`` across ``nodes`` = the number of
                         Bracket rounds = ceil(log2(size)). 0 when ``nodes`` is
                         empty.
      - completed_stages = count of Bracket rounds (1..total) where EVERY
                         non-bye node in that round has ``winner_id`` set.
                         A round with zero non-bye nodes counts as completed.
    """
    if not nodes:
        return (0, 0)
    total = max(nd["bracket_round"] for nd in nodes)
    completed = 0
    for r in range(1, total + 1):
        round_nodes = [nd for nd in nodes if nd["bracket_round"] == r]
        non_bye = [nd for nd in round_nodes if not nd["is_bye"]]
        if all(nd["winner_id"] is not None for nd in non_bye):
            completed += 1
    return (completed, total)


def break_tie(
    seed_a: int, best_round_score_a: int, seed_b: int, best_round_score_b: int
) -> int:
    """Deterministic tie-break for a node whose Match has winner_id IS NULL.

    Returns the Bracket seed (seed_a or seed_b) of the team that advances:
      1. Higher best single-Round score advances (best_round_score_* = the
         team's max of its two GameRound point totals in this Match).
      2. If still equal, the higher Bracket seed (LOWER seed int) advances.
    No re-sim. Pure integer comparison.
    """
    if best_round_score_a > best_round_score_b:
        return seed_a
    if best_round_score_b > best_round_score_a:
        return seed_b
    # Equal best-round scores: higher Bracket seed = lower seed int.
    return seed_a if seed_a <= seed_b else seed_b
