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
    # --- LG-02c additions (appended WITH defaults; build_bracket's existing
    # 10-field construction stays valid byte-for-byte and leaves these defaulted). ---
    bracket_type: str = "winners"  # "winners" | "losers" | "grand_final"
    # Drop pointer: where THIS node's LOSER goes, as a (bracket_type,
    # bracket_round, position) triple (None for LB nodes + GF2 + single-elim).
    loser_advances_to: Optional[tuple[str, int, int]] = None
    loser_advances_to_slot: Optional[str] = None  # "a" | "b" | None
    depth: Optional[int] = None  # distance to GF1 (DE only); None for single-elim


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


def build_double_elim_bracket(
    participants: list[ParticipantSpec],
) -> list[BracketNodeSpec]:
    """Build the full two-tree (Winners + Losers + Grand final) node-spec list
    for arbitrary N >= 4 with byes.

    - Winners bracket (bracket_type="winners") = the existing single-elim tree
      (size = next pow2 >= N, top (size-N) seeds get WB byes). Reuses
      ``build_bracket``'s seeding / pairing / bye logic.
    - Losers bracket (bracket_type="losers") consumes WB-round losers via a
      NAIVE same-position drop (loser of WB-round-r position i -> the matching
      LB slot by position; NO anti-rematch folding). Each WB node's
      ``loser_advances_to`` points at its LB destination + ``loser_advances_to_slot``.
    - Grand final: GF1 (bracket_type="grand_final", the lower bracket_round)
      takes the WB champion (slot "a") + LB champion (slot "b"); GF2 (the higher
      bracket_round) is the conditionally-inert Bracket reset. GF1's
      ``loser_advances_to`` points at GF2 (so the LB-champ path Advances both
      into GF2); GF2.advances_to is None (final node).
    - Every spec carries ``bracket_type``, ``loser_advances_to`` (triple coord
      or None), ``loser_advances_to_slot``, and ``depth`` (distance to GF1:
      GF1/GF2 depth 0, WB-final & LB-final depth 1, ...).

    Returns BracketNodeSpec list. Raises ValueError on len < 4 or duplicate
    seeds/team_ids (mirrors ``build_bracket``).
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
    total_rounds = int(math.log2(size))  # W = number of WB rounds

    # --- Winners bracket: the single-elim tree, re-tagged "winners". ---
    wb_specs = build_bracket(participants)

    # GF coordinates. The grand-final bracket_round numbers are placed ABOVE the
    # LB max round (lb_last = 2W-2; GF1 = 2W-1, GF2 = 2W) so the 2-tuple
    # ``advances_to`` coord a WB/LB final carries (which omits bracket_type) is
    # unambiguous by (bracket_round, position) alone — no WB or LB node shares a
    # GF round number. GF1 is still the lower of the two.
    lb_last_round = 2 * total_rounds - 2
    gf1_coord = ("grand_final", lb_last_round + 1, 0)
    gf2_coord = ("grand_final", lb_last_round + 2, 0)

    # --- Losers bracket topology ---------------------------------------------
    # LB rounds are 1..(2W-2). Round 1 pairs WB-R1 losers among themselves;
    # thereafter minor rounds (even) consume the next WB round's losers against
    # the LB survivors, major rounds (odd, >=3) are LB-vs-LB. The final minor
    # round (2W-2) consumes the WB final loser.
    # lb_round_count[m] = number of nodes in LB round m.
    lb_round_count: dict[int, int] = {}
    if total_rounds >= 2:
        lb_round_count[1] = size // 4
        for k in range(1, total_rounds - 1):
            lb_round_count[2 * k] = size // (2 ** (k + 1))  # minor: WB-R(k+1) losers
            lb_round_count[2 * k + 1] = size // (2 ** (k + 2))  # major: LB-vs-LB
        lb_round_count[2 * total_rounds - 2] = 1  # final minor: WB final loser

    def lb_parent(m: int, j: int) -> tuple[tuple[str, int, int], str]:
        """Advancement of LB round m position j -> (coord, slot)."""
        last = 2 * total_rounds - 2
        if m == last:
            # LB final winner -> GF1 slot "b" (the LB champion).
            return gf1_coord, "b"
        if m == 1:
            # LB-R1 winner -> LB-R2 (minor) slot "a".
            return ("losers", 2, j), "a"
        if m % 2 == 0:
            # Minor round winner -> next major round, paired two-at-a-time.
            return ("losers", m + 1, j // 2), ("a" if j % 2 == 0 else "b")
        # Major round winner -> next minor round slot "a".
        return ("losers", m + 1, j), "a"

    lb_last = 2 * total_rounds - 2  # the LB final round (>= 2 since N >= 4)
    lb_specs: list[BracketNodeSpec] = []
    for m in sorted(lb_round_count):
        for j in range(lb_round_count[m]):
            adv, slot = lb_parent(m, j)
            lb_specs.append(
                BracketNodeSpec(
                    bracket_round=m,
                    position=j,
                    team_a_id=None,
                    team_b_id=None,
                    seed_a=None,
                    seed_b=None,
                    is_bye=False,
                    advances_to=(adv[1], adv[2]),
                    advances_to_slot=slot,
                    winner_id=None,
                    bracket_type="losers",
                    loser_advances_to=None,  # an LB loss eliminates
                    loser_advances_to_slot=None,
                    depth=lb_last - m + 1,  # LB final depth 1, earlier deeper
                )
            )

    # --- WB loser-drop wiring ------------------------------------------------
    # WB-R1 loser pos i -> LB-R1 pos i//2, slot "a" if i even else "b".
    # WB-R r loser (r>=2) pos i -> LB-R(2r-2) pos i, slot "b" (the survivor is "a").
    def wb_loser_dest(r: int, i: int) -> tuple[tuple[str, int, int], str]:
        if r == 1:
            return ("losers", 1, i // 2), ("a" if i % 2 == 0 else "b")
        return ("losers", 2 * r - 2, i), "b"

    rebuilt_wb: list[BracketNodeSpec] = []
    for spec in wb_specs:
        if spec.advances_to is None:
            # WB final: its winner feeds GF1 slot "a"; its loser feeds GF1 "b"...
            # no — the WB final LOSER feeds the LB final, the WB CHAMPION feeds
            # GF1 slot "a". advances_to (winner) -> GF1 "a"; loser -> LB.
            adv = (gf1_coord[1], gf1_coord[2])
            adv_slot = "a"
        else:
            adv = spec.advances_to
            adv_slot = spec.advances_to_slot
        ldest, lslot = wb_loser_dest(spec.bracket_round, spec.position)
        depth = total_rounds - spec.bracket_round + 1
        rebuilt_wb.append(
            BracketNodeSpec(
                bracket_round=spec.bracket_round,
                position=spec.position,
                team_a_id=spec.team_a_id,
                team_b_id=spec.team_b_id,
                seed_a=spec.seed_a,
                seed_b=spec.seed_b,
                is_bye=spec.is_bye,
                advances_to=adv,
                advances_to_slot=adv_slot,
                winner_id=spec.winner_id,
                bracket_type="winners",
                loser_advances_to=ldest,
                loser_advances_to_slot=lslot,
                depth=depth,
            )
        )

    # --- Grand final: GF1 + GF2 ---------------------------------------------
    gf1 = BracketNodeSpec(
        bracket_round=gf1_coord[1],
        position=gf1_coord[2],
        team_a_id=None,
        team_b_id=None,
        seed_a=None,
        seed_b=None,
        is_bye=False,
        advances_to=(gf2_coord[1], gf2_coord[2]),  # GF1 winner -> GF2 slot...
        advances_to_slot="b",  # the GF1 winner (LB champ path) fills GF2 "b"
        winner_id=None,
        bracket_type="grand_final",
        loser_advances_to=gf2_coord,  # GF1 loser -> GF2 slot "a" (WB champ holds)
        loser_advances_to_slot="a",
        depth=0,
    )
    gf2 = BracketNodeSpec(
        bracket_round=gf2_coord[1],
        position=gf2_coord[2],
        team_a_id=None,
        team_b_id=None,
        seed_a=None,
        seed_b=None,
        is_bye=False,
        advances_to=None,  # the final node
        advances_to_slot=None,
        winner_id=None,
        bracket_type="grand_final",
        loser_advances_to=None,
        loser_advances_to_slot=None,
        depth=0,
    )

    return rebuilt_wb + lb_specs + [gf1, gf2]


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


def series_length_for_depth(
    depth: int,
    *,
    final: int,
    semifinal: int,
    quarterfinal: int,
    earlier: int,
) -> int:
    """Resolve a best-of-N Series length from a node's depth below the final.

    DE anchors depth to the distance to GF1 (GF1/GF2 = depth 0, WB-final &
    LB-final = depth 1, ...). depth 0 -> final, 1 -> semifinal,
    2 -> quarterfinal, depth >= 3 -> earlier. Pure integer dispatch; total,
    never raises (the if/elif/elif/else chain makes ``earlier`` the catch-all,
    so any out-of-range / defensive depth resolves to ``earlier``). No
    validation of the four slot values (callers pass the locked 1/3/5 choices).
    """
    if depth == 0:
        return final
    elif depth == 1:
        return semifinal
    elif depth == 2:
        return quarterfinal
    else:
        return earlier


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
    Delegates to ``series_length_for_depth`` (single-elim behaviour identical).
    """
    return series_length_for_depth(
        total_rounds - bracket_round,
        final=final,
        semifinal=semifinal,
        quarterfinal=quarterfinal,
        earlier=earlier,
    )


_BRACKET_RANK = {"winners": 0, "losers": 1, "grand_final": 2}


def find_next_node(nodes: list[dict]) -> Optional[dict]:
    """Return the next PLAYABLE node dict (deterministic total order across
    both brackets): both team slots filled (team_a_id and team_b_id not None),
    is_bye False, and the Series has not yet been clinched. None when nothing
    is ready.

    Tiebreak ordering is ``(bracket_type rank winners<losers<grand_final,
    bracket_round asc, position asc)``. For a single-elim field every node is
    ``bracket_type="winners"`` (rank 0), so the order collapses to
    ``(bracket_round, position)`` exactly as before.

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
    playable.sort(
        key=lambda nd: (
            _BRACKET_RANK.get(nd.get("bracket_type", "winners"), 0),
            nd["bracket_round"],
            nd["position"],
        )
    )
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


def advance_loser(
    nodes: list[dict],
    node_position: tuple[str, int, int],
    loser_id: int,
    loser_seed: int,
) -> list[dict]:
    """Given the flattened node dicts and the (bracket_type, bracket_round,
    position) of a node that just resolved, return the parent-slot mutations
    that DROP the loser into that node's ``loser_advances_to`` slot.

    Each mutation dict:
    ``{"bracket_type", "bracket_round", "position", "slot", "team_id", "seed"}``
    (note the extra ``bracket_type`` key — the WB->LB Drop crosses brackets, so
    the coord must carry the destination bracket).

    Empty list when ``loser_advances_to`` is None (an LB node whose loser is
    eliminated, GF2, or a single-elim WB node). Pure: reads
    ``loser_advances_to`` / ``loser_advances_to_slot`` off the resolved node
    carried in ``nodes`` (matched on the full (bracket_type, round, position)
    triple — DE keys cross brackets).
    """
    btype, br, pos = node_position
    resolved = None
    for nd in nodes:
        if (
            nd.get("bracket_type", "winners") == btype
            and nd["bracket_round"] == br
            and nd["position"] == pos
        ):
            resolved = nd
            break
    if resolved is None:
        return []
    loser_dest = resolved.get("loser_advances_to")
    slot = resolved.get("loser_advances_to_slot")
    if loser_dest is None or slot is None:
        return []
    dest_btype, parent_round, parent_pos = loser_dest
    return [
        {
            "bracket_type": dest_btype,
            "bracket_round": parent_round,
            "position": parent_pos,
            "slot": slot,
            "team_id": loser_id,
            "seed": loser_seed,
        }
    ]


def _resolve_bye_chain_de(nodes: list[dict]) -> list[dict]:
    """DE generalization of ``resolve_bye_chain``.

    Cascades winner-side byes within each bracket AND collapses **Drop byes**:
    an LB node one of whose feeder slots is fed only by a WB **Bye** (which
    produces no loser) collapses, auto-advancing the surviving opponent. All
    mutations carry a ``bracket_type`` key (the destination bracket).
    """
    by_key = {
        (nd.get("bracket_type", "winners"), nd["bracket_round"], nd["position"]): dict(
            nd
        )
        for nd in nodes
    }
    mutations: list[dict] = []

    def winner_of(nd):
        if not nd.get("is_bye"):
            return None
        wid = nd.get("winner_id")
        if wid is None:
            if nd.get("team_a_id") is not None and nd.get("team_b_id") is None:
                return nd["team_a_id"], nd.get("seed_a")
            if nd.get("team_b_id") is not None and nd.get("team_a_id") is None:
                return nd["team_b_id"], nd.get("seed_b")
            return None
        if nd.get("team_a_id") == wid:
            return wid, nd.get("seed_a")
        if nd.get("team_b_id") == wid:
            return wid, nd.get("seed_b")
        return wid, (
            nd.get("seed_a") if nd.get("seed_a") is not None else nd.get("seed_b")
        )

    def parent_btype_of(nd):
        """The bracket_type of nd's winner-advance parent (cross-bracket safe)."""
        adv = nd.get("advances_to")
        if adv is None:
            return None
        # Search for the parent by (bracket_round, position) within the same
        # bracket first, then any bracket (WB/LB finals cross into grand_final).
        own_bt = nd.get("bracket_type", "winners")
        if (own_bt, adv[0], adv[1]) in by_key:
            return own_bt
        for bt, br, pos in by_key:
            if br == adv[0] and pos == adv[1]:
                return bt
        return own_bt

    def promote(nd, wid, wseed):
        """Apply nd's winner into its advances_to parent slot. Returns True if a
        new mutation was emitted."""
        adv = nd.get("advances_to")
        slot = nd.get("advances_to_slot")
        if adv is None or slot is None:
            return False
        ptype = parent_btype_of(nd)
        parent = by_key.get((ptype, adv[0], adv[1]))
        if parent is None:
            return False
        if slot == "a":
            if parent["team_a_id"] is not None:
                return False
            parent["team_a_id"] = wid
            parent["seed_a"] = wseed
        else:
            if parent["team_b_id"] is not None:
                return False
            parent["team_b_id"] = wid
            parent["seed_b"] = wseed
        mutations.append(
            {
                "bracket_type": ptype,
                "bracket_round": adv[0],
                "position": adv[1],
                "slot": slot,
                "team_id": wid,
                "seed": wseed,
            }
        )
        return True

    def feeder_is_dead(parent_key, slot):
        """True iff the only feeder of parent_key's ``slot`` is a WB Bye (a node
        with no loser) — i.e. that slot can never fill."""
        ptype, pbr, ppos = parent_key
        feeders = []
        for nd in by_key.values():
            # winner-advance feeder
            adv = nd.get("advances_to")
            if (
                adv is not None
                and nd.get("advances_to_slot") == slot
                and parent_btype_of(nd) == ptype
                and adv[0] == pbr
                and adv[1] == ppos
            ):
                feeders.append(("winner", nd))
            # loser-drop feeder
            ld = nd.get("loser_advances_to")
            if (
                ld is not None
                and nd.get("loser_advances_to_slot") == slot
                and ld[0] == ptype
                and ld[1] == pbr
                and ld[2] == ppos
            ):
                feeders.append(("loser", nd))
        if not feeders:
            return False
        # The slot can never fill iff EVERY feeder is dead: a loser-drop feeder
        # whose source is a Bye (no loser) is dead; a winner feeder that is a Bye
        # is NOT dead (the bye's team auto-advances and fills the slot).
        for kind, nd in feeders:
            if kind == "loser":
                if not nd.get("is_bye"):
                    return False
            else:
                return False
        return True

    # Fixpoint: keep cascading winner byes and collapsing Drop byes until stable.
    changed = True
    while changed:
        changed = False
        # Winner-side bye cascade (ordered for determinism).
        for key in sorted(by_key, key=lambda k: (k[1], k[2], k[0])):
            nd = by_key[key]
            win = winner_of(nd)
            if win is None:
                continue
            wid, wseed = win
            if promote(nd, wid, wseed):
                changed = True
        # Drop-bye collapse: an LB/GF node with one slot filled whose other slot
        # can never fill auto-advances the filled team.
        for key in sorted(by_key, key=lambda k: (k[1], k[2], k[0])):
            nd = by_key[key]
            if nd.get("is_bye"):
                continue
            a_filled = nd.get("team_a_id") is not None
            b_filled = nd.get("team_b_id") is not None
            if a_filled == b_filled:
                continue  # both or neither filled -> not a one-sided collapse
            filled_slot = "a" if a_filled else "b"
            empty_slot = "b" if a_filled else "a"
            if not feeder_is_dead(key, empty_slot):
                continue
            wid = nd["team_a_id"] if a_filled else nd["team_b_id"]
            wseed = nd.get("seed_a") if a_filled else nd.get("seed_b")
            nd["is_bye"] = True
            nd["winner_id"] = wid
            # Promote into the parent immediately (the collapsed node is a bye).
            if promote(nd, wid, wseed):
                changed = True
            else:
                changed = True  # the is_bye/winner flip itself is a change

    return mutations


def resolve_bye_chain(nodes: list[dict]) -> list[dict]:
    """Cascade byes at build time: for every is_bye node, return the
    parent-slot mutations (same shape as advance_winner's output) that promote
    the bye team into the next round, recursively if a later-round node ends
    up with one filled slot and the other slot's feeder is also a bye.

    Used by build/persist so a top seed's bye is reflected in round 2
    immediately. Empty list when no byes.

    Single-elim path (every node ``bracket_type="winners"`` with no
    ``loser_advances_to``) is **byte-identical** to LG-02a/b. A
    double-elimination field additionally collapses **Drop byes** (an empty LB
    slot whose feeding WB node was a Bye, so produced no loser) — the surviving
    LB opponent auto-advances; those loser-side mutations carry a
    ``bracket_type`` key.
    """
    is_de = any(
        nd.get("bracket_type", "winners") != "winners"
        or nd.get("loser_advances_to") is not None
        for nd in nodes
    )
    if is_de:
        return _resolve_bye_chain_de(nodes)
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
      - total_stages   = count of distinct ``(bracket_type, bracket_round)``
                         groups across ``nodes``. For a single-elim field every
                         group is ``("winners", r)`` so this equals the old
                         ``max(bracket_round)`` value (byte-unchanged). 0 when
                         ``nodes`` is empty.
      - completed_stages = count of those groups where EVERY non-bye, non-inert
                         node has ``winner_id`` set. A group with zero such
                         nodes counts as completed (mirrors the all-bye-round
                         rule). An inert auto-resolved GF2 already has
                         ``winner_id`` set, so the existing check covers it.
    """
    if not nodes:
        return (0, 0)
    groups: dict[tuple[str, int], list[dict]] = {}
    for nd in nodes:
        key = (nd.get("bracket_type", "winners"), nd["bracket_round"])
        groups.setdefault(key, []).append(nd)
    total = len(groups)
    completed = 0
    for group_nodes in groups.values():
        non_bye = [nd for nd in group_nodes if not nd["is_bye"]]
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
