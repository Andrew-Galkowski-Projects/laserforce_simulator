from typing import Any

# ---------------------------------------------------------------------------
# Per-role constants — all tuning numbers in one place per role
# ---------------------------------------------------------------------------

_MEDIC = dict(
    baseline_tag=-65,  # opening tag_player delta (70 -> 5)
    baseline_cz=-30,  # opening only_move delta (30 -> 0)
    baseline_hide=+30,  # opening hide delta (0 -> 30)
    baseline_resupply=+65,  # opening resupply_ally delta (0 -> 65)
    # MOVE-03: Medic does not Overwatch (it heals, not holds).
    baseline_hold=0,  # no hold weight (Medic stays support-focused)
    baseline_hold_source="tag_player",  # unused while baseline_hold == 0
    support_capture_gain=5,  # capture_base bump when a base is capturable
    support_capture_resupply_cost=5,  # resupply_ally cost paid for that bump
    low_lives_threshold=3,  # fixed life count (not a percentage)
    low_lives_hide_cost=30,  # hide cut when low on lives
    low_lives_resupply_gain=30,  # resupply_ally bump when low on lives
    not_active_resupply_cost=60,  # resupply_ally drained while downed
    endgame_capture_gain=40,  # capture_base bump in the endgame rush
    endgame_resupply_cost=40,  # resupply_ally cost paid for that rush
    special_per_ally=20,  # use_special bump per active ally
)

_AMMO = dict(
    baseline_tag=-25,  # opening tag_player delta (70 -> 45)
    baseline_cz=-30,  # opening only_move delta (30 -> 0)
    baseline_resupply=+55,  # opening resupply_ally delta (0 -> 55)
    # MOVE-03: +20 to hold, drawn from tag_player. Post-baseline tag = 70 - 25
    # = 45; 45 - 20 = 25 (>= 0, OK).
    baseline_hold=+20,  # hold weight (drawn from tag_player)
    baseline_hold_source="tag_player",  # source slot for the hold weight
    support_capture_gain=5,  # capture_base bump when a base is capturable
    support_capture_resupply_cost=5,  # resupply_ally cost paid for that bump
    low_lives_threshold=3,  # fixed life count triggering the low-lives block
    low_lives_diff_zone_resupply_cost=50,  # resupply_ally cut, medic in other zone
    low_lives_same_zone_resupply_cost=30,  # resupply_ally cut, medic same zone
    low_lives_same_zone_hide_gain=30,  # hide bump, medic same zone
    low_lives_fallback_resupply_cost=50,  # resupply_ally cut, no medic (heavy/none)
    low_lives_fallback_hide_gain=50,  # hide bump, no medic fallback
    low_lives_tag_cost=20,  # tag_player cut when low on lives
    low_lives_resupply_gain=20,  # resupply_ally bump when low on lives
    special_per_ally=20,  # use_special bump per active ally
    endgame_capture_gain=40,  # capture_base bump in the endgame rush
    endgame_resupply_cost=40,  # resupply_ally cost paid for that rush
)

_SCOUT = dict(
    baseline_tag=-30,  # opening tag_player delta (70 -> 40)
    baseline_cz=+30,  # opening only_move delta (30 -> 60)
    # MOVE-03: +10 to hold, drawn from only_move. Post-baseline only_move =
    # 30 + 30 = 60; 60 - 10 = 50 (>= 0, OK).
    baseline_hold=+10,  # hold weight (drawn from only_move)
    baseline_hold_source="only_move",  # source slot for the hold weight
    critical_pct=0.3,  # lives/shots fraction that triggers seek-ally blocks
    base_capture_gain=20,  # capture_base bump when a base is capturable
    base_capture_tag_cost=20,  # tag_player cost paid for that bump
    seek_diff_tag=30,  # tag_player cut, ally in a different zone
    seek_diff_cz=30,  # only_move bump, ally in a different zone
    seek_same_cz=20,  # only_move cut, ally in same zone
    seek_same_tag=20,  # tag_player cut, ally in same zone
    seek_same_hide=40,  # hide bump, ally in same zone
    seek_base_capture_gain=40,  # capture_base bump, no ally but base capturable
    seek_base_tag_cost=20,  # tag_player cost for that capture bump
    seek_base_cz_cost=20,  # only_move cost for that capture bump
    seek_no_ally_tag=30,  # tag_player cut, no ally (lives block)
    seek_no_ally_hide=30,  # hide bump, no ally (lives block)
    seek_no_ammo_tag=50,  # tag_player cut, no ammo ally (shots block)
    seek_no_ammo_hide=50,  # hide bump, no ammo ally (shots block)
    not_active_cz_cap=10,  # max idle tag weight redirected to only_move when downed
    endgame_capture_gain=30,  # capture_base bump in the endgame rush
    endgame_tag_cost=30,  # tag_player cost paid for that rush
)

_HEAVY = dict(
    baseline_cz=-5,  # opening only_move delta (30 -> 25)
    baseline_hide=+5,  # opening hide delta (0 -> 5)
    # MOVE-03: +20 to hold, drawn from only_move. Post-baseline only_move =
    # 30 - 5 = 25; 25 - 20 = 5 (>= 0, OK).
    baseline_hold=+20,  # hold weight (drawn from only_move)
    baseline_hold_source="only_move",  # source slot for the hold weight
    critical_pct=0.3,  # lives/shots fraction that triggers seek-ally blocks
    missile_cz_cost=15,  # only_move cost while missiles remain
    missile_gain=15,  # missile_player bump while missiles remain
    base_capture_gain=30,  # capture_base bump when a base is capturable
    base_capture_tag_cost=20,  # tag_player cost paid for that bump
    base_capture_cz_cost=10,  # only_move cost paid for that bump
    seek_diff_tag=30,  # tag_player cut, ally in a different zone
    seek_diff_cz=30,  # only_move bump, ally in a different zone
    seek_same_cz=10,  # only_move cut, ally in same zone
    seek_same_tag=20,  # tag_player cut, ally in same zone
    seek_same_hide=30,  # hide bump, ally in same zone
    seek_base_capture_gain=40,  # capture_base bump, no ally but base capturable
    seek_base_tag_cost=20,  # tag_player cost for that capture bump
    seek_base_cz_cost=20,  # only_move cost for that capture bump
    seek_no_ally_tag=30,  # tag_player cut, no ally (lives block)
    seek_no_ally_hide=30,  # hide bump, no ally (lives block)
    seek_no_ammo_tag=50,  # tag_player cut, no ammo ally (shots block)
    seek_no_ammo_hide=50,  # hide bump, no ammo ally (shots block)
    not_active_tag_cost=70,  # tag_player drained while downed
    endgame_capture_gain=30,  # capture_base bump in the endgame rush
    endgame_tag_cost=30,  # tag_player cost paid for that rush
)

_COMMANDER = dict(
    baseline_tag=+10,  # opening tag_player delta (70 -> 80)
    baseline_cz=-15,  # opening only_move delta (30 -> 15)
    # MOVE-03: +10 to hold, drawn from only_move. Post-baseline only_move =
    # 30 - 15 = 15; 15 - 10 = 5 (>= 0, OK).
    baseline_hold=+10,  # hold weight (drawn from only_move)
    baseline_hold_source="only_move",  # source slot for the hold weight
    critical_pct=0.3,  # lives/shots fraction that triggers seek-ally blocks
    missile_cz_cost=15,  # only_move cost while missiles remain
    missile_gain=15,  # missile_player bump while missiles remain
    base_capture_gain=50,  # capture_base bump when a base is capturable
    base_capture_tag_cost=40,  # tag_player cost paid for that bump
    base_capture_cz_cost=10,  # only_move cost paid for that bump
    base_early_bonus=20,  # extra capture/tag swing before base_early_threshold
    base_early_threshold=300,  # seconds-domain cutoff for the early bonus
    seek_diff_tag=30,  # tag_player cut, ally in a different zone
    seek_diff_cz=30,  # only_move bump, ally in a different zone
    seek_same_cz=10,  # only_move cut, ally in same zone
    seek_same_tag=20,  # tag_player cut, ally in same zone
    seek_same_hide=30,  # hide bump, ally in same zone
    seek_base_capture_gain=40,  # capture_base bump, no ally but base capturable
    seek_base_tag_cost=20,  # tag_player cost for that capture bump
    seek_base_cz_cost=20,  # only_move cost for that capture bump
    seek_no_ally_tag=30,  # tag_player cut, no ally (lives block)
    seek_no_ally_hide=30,  # hide bump, no ally (lives block)
    seek_no_ammo_tag=50,  # tag_player cut, no ammo ally (shots block)
    seek_no_ammo_hide=50,  # hide bump, no ammo ally (shots block)
    not_active_tag_cost=70,  # tag_player drained while downed
    special_base=100,  # base use_special weight when the nuke gate opens
    special_per_enemy=20,  # use_special reduction per enemy in zone
    endgame_capture_gain=30,  # capture_base bump in the endgame rush
    endgame_tag_cost=30,  # tag_player cost paid for that rush
)


# ---------------------------------------------------------------------------
# Baseline action weights — the starting weight vector before any role
# adjustment.  ``plan_action`` copies this list (it mutates in place) and each
# role weight function applies its deltas on top.  Adjust these numbers to
# retune the opening behaviour without touching any logic code.
#
# The 9 entries map by index to the action array (see ``_ACTION_IDX`` /
# ``_CHOICES`` in combat.py):
#   0 tag_player        5 resupply_ally
#   1 only_move         6 missile_player
#   2 hide              7 request_resupply
#   3 capture_base      8 hold
#   4 use_special
# ---------------------------------------------------------------------------
BASELINE_ACTION_WEIGHTS = [70, 30, 0, 0, 0, 0, 0, 0, 0]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _find_ally(all_alive, team_color, role):
    return next(
        (
            p
            for p in all_alive
            if p.team_color == team_color and p.role == role and p.final_lives > 0
        ),
        None,
    )


def _apply_role_baseline(w, c, i):
    """Apply the unconditional opening role adjustments from constants."""
    w[i["tag_player"]] += c.get("baseline_tag", 0)
    w[i["only_move"]] += c.get("baseline_cz", 0)
    w[i["hide"]] += c.get("baseline_hide", 0)
    w[i["resupply_ally"]] += c.get("baseline_resupply", 0)


def _apply_hold_baseline(w: list, c: dict, i: dict) -> None:
    """MOVE-03: shift ``baseline_hold`` weight into the ``hold`` slot (index 8).

    The amount is drawn from ``baseline_hold_source`` (``tag_player`` for Ammo,
    ``only_move`` for Scout/Heavy/Commander; Medic is 0 so this is a no-op).
    The per-role amounts were verified to leave the source weight >= 0 after the
    role baseline (see each role const dict comment); the ``max(0, ...)`` clamp
    is a defensive guard so ``random.choices`` never sees a negative weight.
    Additive on the 9-slot array; consumes no RNG.
    """
    amount = c.get("baseline_hold", 0)
    if amount == 0:
        return
    # Legacy 7/8-slot callers (older unit tests build a pre-MOVE-03
    # action-index dict without "hold"); the production path always passes the
    # 9-slot _ACTION_IDX. Guarding here keeps those callers KeyError-free
    # without widening every legacy fixture.
    if "hold" not in i or len(w) <= i["hold"]:
        return
    source = c.get("baseline_hold_source", "only_move")
    w[i["hold"]] += amount
    w[i[source]] = max(0, w[i[source]] - amount)


def _apply_support_base_capture(w, c, i, player):
    """Small capture incentive for support roles (medic/ammo) when a base is capturable."""
    if player.can_capture_base_in_current_zone:
        w[i["resupply_ally"]] -= c["support_capture_resupply_cost"]
        w[i["capture_base"]] += c["support_capture_gain"]


def _apply_support_special(w, c, i, player, all_alive, second):
    """use_special += special_per_ally * active_ally_count (medic/ammo formula)."""
    if player.final_special >= player.special_cost:
        active_allies = [
            p
            for p in all_alive
            if p.team_color == player.team_color
            and p.final_lives > 0
            and p.is_active_at(second)
        ]
        w[i["use_special"]] += int(
            c["special_per_ally"] * len(active_allies) * (player.special_usage / 50)
        )


def _apply_seek_ally(
    w, c, i, player, primary_ally, fallback_ally=None, no_resource=False
):
    """
    Redirect weights toward a support ally (or toward safety when none is available).

    Called for the lives-critical and shots-critical blocks of Scout, Heavy, Commander.
    - primary_ally: the first-choice ally (medic for lives, ammo for shots); may be None.
    - fallback_ally: second-choice ally when primary is dead (ammo when medic dead, lives
      block only); None for Scout and for all shots-critical calls.
    - no_resource: True for shots-critical calls; selects seek_no_ammo_* constants for the
      "no ally" fallthrough instead of seek_no_ally_*.
    """
    if primary_ally is not None:
        if player.current_zone != primary_ally.current_zone:
            w[i["tag_player"]] -= c["seek_diff_tag"]
            w[i["only_move"]] += c["seek_diff_cz"]
        else:
            w[i["only_move"]] -= c["seek_same_cz"]
            w[i["tag_player"]] -= c["seek_same_tag"]
            w[i["hide"]] += c["seek_same_hide"]
    elif player.can_capture_base_in_current_zone:
        w[i["capture_base"]] += c["seek_base_capture_gain"]
        w[i["tag_player"]] -= c["seek_base_tag_cost"]
        w[i["only_move"]] -= c["seek_base_cz_cost"]
    elif fallback_ally is not None:
        if player.current_zone != fallback_ally.current_zone:
            w[i["tag_player"]] -= c["seek_diff_tag"]
            w[i["only_move"]] += c["seek_diff_cz"]
        else:
            w[i["only_move"]] -= c["seek_same_cz"]
            w[i["tag_player"]] -= c["seek_same_tag"]
            w[i["hide"]] += c["seek_same_hide"]
    elif no_resource:
        w[i["tag_player"]] -= c["seek_no_ammo_tag"]
        w[i["hide"]] += c["seek_no_ammo_hide"]
    else:
        w[i["tag_player"]] -= c["seek_no_ally_tag"]
        w[i["hide"]] += c["seek_no_ally_hide"]


def _apply_not_active(
    w,
    c,
    i,
    player,
    all_alive,
    second,
    watch_role,
    watch_team_color,
    drain_key="tag_player",
    also_drain_resupply=False,
    always_escape=False,
):
    """
    When downed, redistribute idle weight to hide or only_move based on a nearby ally.

    - watch_role / watch_team_color: the ally to look for in the current zone.
    - drain_key: the weight index to drain from (tag_player or resupply_ally).
    - also_drain_resupply: if True (ammo), pool tag_player + resupply_ally and zero both.
    - always_escape: if True, always redirect to only_move regardless of ally presence
      (used for heavy to force movement out of line of fire during reset window).
    """
    if not player.is_active_at(second):
        ally_in_zone = next(
            (
                p
                for p in all_alive
                if p.team_color == watch_team_color
                and p.role == watch_role
                and p.current_zone == player.current_zone
                and p.final_lives > 0
            ),
            None,
        )
        if also_drain_resupply:
            idle = w[i["tag_player"]] + w[i["resupply_ally"]]
            w[i["tag_player"]] = 0
            w[i["resupply_ally"]] = 0
        else:
            cost = c[
                f"not_active_{drain_key.replace('tag_player', 'tag').replace('resupply_ally', 'resupply')}_cost"
            ]
            idle = cost
            w[i[drain_key]] -= cost

        if not always_escape and ally_in_zone:
            w[i["hide"]] += idle
        else:
            w[i["only_move"]] += idle


def _apply_request_resupply_weight(
    w: list,
    i: dict,
    player: Any,
    *,
    lives_only: bool = False,
    shots_only: bool = False,
) -> None:
    """Set request_resupply weight based on resupply_efficiency and resource need.

    lives_only / shots_only: restrict the trigger condition for Ammo (lives) and
    Medic (shots) respectively.  Neither flag set means either resource below max.
    """
    rr_idx = i.get("request_resupply")
    if rr_idx is None or len(w) <= rr_idx:
        return
    needs = (player.final_lives < player.max_lives and not shots_only) or (
        player.final_shots < player.max_shots and not lives_only
    )
    if needs:
        w[rr_idx] = int(getattr(player, "resupply_efficiency", 50) / 2)


def _apply_endgame_rush(w, c, i, player, second, offset_key, time_domain="seconds"):
    """End-game base rush: shift weight from offset_key to capture_base when time is low.

    TIME-01: the end-game threshold is 840 in the seconds domain (RBS / tests)
    and ENDGAME_RUSH_TICKS (1680) in the tick domain (BatchSimulator).
    """
    if time_domain == "ticks":
        from .time_constants import ENDGAME_RUSH_TICKS

        endgame_threshold = ENDGAME_RUSH_TICKS
    else:
        endgame_threshold = 840
    if second >= endgame_threshold and player.can_capture_base_in_current_zone:
        gain_key = "endgame_capture_gain"
        cost_key = f"endgame_{'tag' if offset_key == 'tag_player' else 'resupply'}_cost"
        w[i["capture_base"]] += c[gain_key]
        w[i[offset_key]] -= c[cost_key]


def _apply_score_broadcast_weights(
    w: list,
    i: dict,
    player: Any,
    all_alive: list,
    second: float,
) -> None:
    """MECH-06: apply score-broadcast behavioral weight biases.

    Called at the end of every role weight function.  Reads the transient
    ``score_broadcast_state`` attribute set by the simulator's per-tick score
    broadcast logic (every 180 s).

    Losing team:
      - tag_player += 10
      - only_move -= 10    (clamped ≥ 0)
      - hide -= 10         (clamped ≥ 0)

    Winning team + low lives (≤ 30%) + allied medic dead:
      - hide += 20
      - tag_player -= 20   (clamped ≥ 0)

    Winning team + low lives + medic alive + second ≥ 360:
      - movement goal override is handled in pathfinding.choose_goal_cell;
        no weight change needed here.
    """
    state = getattr(player, "score_broadcast_state", None)
    if not state:
        return
    winning_team = state.get("winning_team", "")
    player_team = player.team_color
    max_lives = getattr(player, "max_lives", player.starting_lives)
    low_lives = player.final_lives <= max_lives * 0.3

    if winning_team and winning_team != player_team and winning_team != "tied":
        # Losing: be more aggressive
        if i.get("tag_player") is not None:
            w[i["tag_player"]] = max(0, w[i["tag_player"]] + 10)
        if i.get("only_move") is not None:
            w[i["only_move"]] = max(0, w[i["only_move"]] - 10)
        if i.get("hide") is not None:
            w[i["hide"]] = max(0, w[i["hide"]] - 10)
    elif winning_team == player_team and low_lives:
        medic = _find_ally(all_alive, player_team, "medic")
        if medic is None:
            # Winning, low lives, medic dead → hide
            if i.get("hide") is not None:
                w[i["hide"]] = max(0, w[i["hide"]] + 20)
            if i.get("tag_player") is not None:
                w[i["tag_player"]] = max(0, w[i["tag_player"]] - 20)
        # Winning + low lives + medic alive + second >= 360 → handled in pathfinding


# ---------------------------------------------------------------------------
# Role weight functions (public API — signatures unchanged)
# ---------------------------------------------------------------------------


def _get_medic_weights(
    player: Any,
    action_to_weight_index: dict[str, int],
    weights: list[int],
    all_alive: list[Any],
    second: float,
    time_domain: str = "seconds",
) -> list[int]:
    """Compute the Medic action weights in place from ``_MEDIC`` and return them.

    Baseline after role adjustment (before situational modifiers):
    ``tag_player=5, only_move=0, hide=30, resupply_ally=65, hold=0`` — Medic
    never holds (no Overwatch source), staying support-focused.

    Situational blocks, in application order (only the ones Medic calls):
    baseline -> hold baseline -> support capture incentive -> low-lives
    (stop tagging, dump tag weight into resupply) -> support special -> not-active
    (downed; drains ``resupply_ally``, watches for an allied heavy) -> endgame
    rush -> ``resupply_synergy`` stat scaling on ``resupply_ally`` -> request
    resupply (shots only) -> MECH-04 nuke reaction (tag weight -> resupply) ->
    MECH-06 score broadcast.

    Invariant (production): ``plan_action`` needs only the *total* weight > 0,
    which is all ``random.choices`` enforces — it tolerates an unreachable
    negative bucket and raises only when the total is <= 0. Most slots stay
    >= 0; a few branches deliberately drive one slot negative (Heavy/Commander
    ``only_move`` while missiles remain, Heavy capture, Scout ``tag_player``
    when shots-critical), which is harmless. See ``test_plan_action_*`` in
    test_weights.py.
    """
    w, c, i = weights, _MEDIC, action_to_weight_index

    _apply_role_baseline(w, c, i)
    _apply_hold_baseline(w, c, i)
    _apply_support_base_capture(w, c, i, player)

    if player.final_lives <= c["low_lives_threshold"]:
        # low lives: stop tagging entirely and maximise resupply
        w[i["hide"]] -= c["low_lives_hide_cost"]
        w[i["resupply_ally"]] += c["low_lives_resupply_gain"]
        w[i["resupply_ally"]] += w[i["tag_player"]]
        w[i["tag_player"]] = 0

    _apply_support_special(w, c, i, player, all_alive, second)
    _apply_not_active(
        w,
        c,
        i,
        player,
        all_alive,
        second,
        "heavy",
        player.team_color,
        drain_key="resupply_ally",
    )
    _apply_endgame_rush(w, c, i, player, second, "resupply_ally", time_domain)

    # resupply_synergy: scales resupply_ally weight
    resupply_synergy = getattr(player, "resupply_synergy", 50)
    w[i["resupply_ally"]] = max(0, int(w[i["resupply_ally"]] * (resupply_synergy / 50)))

    _apply_request_resupply_weight(w, i, player, shots_only=True)

    # MECH-04: nuke incoming — transfer tag weight into resupply to maximise output
    if getattr(player, "reacting_to_nuke", False):
        w[i["resupply_ally"]] += w[i["tag_player"]] + 20
        w[i["tag_player"]] = 0

    # MECH-06: score broadcast behavioral bias
    _apply_score_broadcast_weights(w, i, player, all_alive, second)

    return weights


def _get_ammo_weights(
    player: Any,
    action_to_weight_index: dict[str, int],
    weights: list[int],
    all_alive: list[Any],
    second: float,
    time_domain: str = "seconds",
) -> list[int]:
    """Compute the Ammo action weights in place from ``_AMMO`` and return them.

    Baseline after role adjustment (before situational modifiers):
    ``tag_player=25, only_move=0, resupply_ally=55, hold=20`` — the hold weight
    is drawn from ``tag_player`` (45 - 20 = 25).

    Situational blocks, in application order (only the ones Ammo calls):
    baseline -> hold baseline -> support capture incentive -> low-lives
    (seek the medic, else a heavy; cut tag, boost resupply) -> support special
    -> not-active (downed; pools and zeroes ``tag_player`` + ``resupply_ally``,
    watches for an allied heavy) -> endgame rush -> ``resupply_synergy`` stat
    scaling on ``resupply_ally`` -> request resupply (lives only) -> MECH-04
    nuke reaction (tag weight -> resupply) -> MECH-06 score broadcast.

    Invariant (production): ``plan_action`` needs only the *total* weight > 0,
    which is all ``random.choices`` enforces — it tolerates an unreachable
    negative bucket and raises only when the total is <= 0. Most slots stay
    >= 0; a few branches deliberately drive one slot negative (Heavy/Commander
    ``only_move`` while missiles remain, Heavy capture, Scout ``tag_player``
    when shots-critical), which is harmless. See ``test_plan_action_*`` in
    test_weights.py.
    """
    w, c, i = weights, _AMMO, action_to_weight_index

    _apply_role_baseline(w, c, i)
    _apply_hold_baseline(w, c, i)
    _apply_support_base_capture(w, c, i, player)

    if player.final_lives <= c["low_lives_threshold"]:
        medic = _find_ally(all_alive, player.team_color, "medic")
        if medic is not None:
            if player.current_zone != medic.current_zone:
                w[i["resupply_ally"]] -= c["low_lives_diff_zone_resupply_cost"]
                w[i["only_move"]] += c["low_lives_diff_zone_resupply_cost"]
            else:
                w[i["resupply_ally"]] -= c["low_lives_same_zone_resupply_cost"]
                w[i["hide"]] += c["low_lives_same_zone_hide_gain"]
        else:
            heavy = _find_ally(all_alive, player.team_color, "heavy")
            if heavy is not None:
                if player.current_zone != heavy.current_zone:
                    w[i["resupply_ally"]] -= c["low_lives_fallback_resupply_cost"]
                    w[i["only_move"]] += c["low_lives_fallback_resupply_cost"]
                else:
                    w[i["resupply_ally"]] -= c["low_lives_fallback_resupply_cost"]
                    w[i["hide"]] += c["low_lives_fallback_hide_gain"]
            else:
                w[i["resupply_ally"]] -= c["low_lives_fallback_resupply_cost"]
                w[i["hide"]] += c["low_lives_fallback_hide_gain"]
        w[i["tag_player"]] -= c["low_lives_tag_cost"]
        w[i["resupply_ally"]] += c["low_lives_resupply_gain"]

    _apply_support_special(w, c, i, player, all_alive, second)
    _apply_not_active(
        w,
        c,
        i,
        player,
        all_alive,
        second,
        "heavy",
        player.team_color,
        also_drain_resupply=True,
    )
    _apply_endgame_rush(w, c, i, player, second, "resupply_ally", time_domain)

    # resupply_synergy: scales resupply_ally weight
    resupply_synergy = getattr(player, "resupply_synergy", 50)
    w[i["resupply_ally"]] = max(0, int(w[i["resupply_ally"]] * (resupply_synergy / 50)))

    _apply_request_resupply_weight(w, i, player, lives_only=True)

    # MECH-04: nuke incoming — transfer tag weight into resupply to maximise output
    if getattr(player, "reacting_to_nuke", False):
        w[i["resupply_ally"]] += w[i["tag_player"]] + 20
        w[i["tag_player"]] = 0

    # MECH-06: score broadcast behavioral bias
    _apply_score_broadcast_weights(w, i, player, all_alive, second)

    return weights


def _get_scout_weights(
    player: Any,
    action_to_weight_index: dict[str, int],
    weights: list[int],
    all_alive: list[Any],
    second: float,
    time_domain: str = "seconds",
) -> list[int]:
    """Compute the Scout action weights in place from ``_SCOUT`` and return them.

    Baseline after role adjustment (before situational modifiers):
    ``tag_player=40, only_move=50, hold=10`` — the hold weight is drawn from
    ``only_move`` (60 - 10 = 50).

    Situational blocks, in application order (only the ones Scout calls):
    baseline -> hold baseline -> capture incentive (when a base is capturable)
    -> lives-critical seek (medic; no fallback ally) -> shots-critical seek
    (ammo; ``no_resource``) -> use_special (when special is charged and idle)
    -> not-active (downed; zero ``tag_player``, ~20% to ``only_move`` capped,
    rest to ``hide``) -> endgame rush -> request resupply (either resource) ->
    MECH-06 score broadcast.

    Invariant (production): ``plan_action`` needs only the *total* weight > 0,
    which is all ``random.choices`` enforces — it tolerates an unreachable
    negative bucket and raises only when the total is <= 0. Most slots stay
    >= 0; a few branches deliberately drive one slot negative (Heavy/Commander
    ``only_move`` while missiles remain, Heavy capture, Scout ``tag_player``
    when shots-critical), which is harmless. See ``test_plan_action_*`` in
    test_weights.py.
    """
    w, c, i = weights, _SCOUT, action_to_weight_index

    _apply_role_baseline(w, c, i)
    _apply_hold_baseline(w, c, i)

    if player.can_capture_base_in_current_zone:
        w[i["tag_player"]] -= c["base_capture_tag_cost"]
        w[i["capture_base"]] += c["base_capture_gain"]

    lives_critical = player.starting_lives * c["critical_pct"]
    shots_critical = player.starting_shots * c["critical_pct"]

    if player.final_lives <= lives_critical:
        medic = _find_ally(all_alive, player.team_color, "medic")
        _apply_seek_ally(w, c, i, player, medic)

    if player.final_shots <= shots_critical:
        ammo = _find_ally(all_alive, player.team_color, "ammo")
        _apply_seek_ally(w, c, i, player, ammo, no_resource=True)

    if (
        player.final_special >= player.special_cost
        and player.special_active_until <= second
    ):
        w[i["use_special"]] += int(
            100 * (player.final_shots / player.max_shots) * (player.special_usage / 50)
        )

    # Not active: redistribute tag weight — 20% to only_move (capped), 80% to hide
    if not player.is_active_at(second):
        tag_weight = max(0, w[i["tag_player"]])
        w[i["tag_player"]] = 0
        cz_bonus = min(c["not_active_cz_cap"], tag_weight)
        w[i["only_move"]] += cz_bonus
        w[i["hide"]] += tag_weight - cz_bonus

    _apply_endgame_rush(w, c, i, player, second, "tag_player", time_domain)

    _apply_request_resupply_weight(w, i, player)

    # MECH-06: score broadcast behavioral bias
    _apply_score_broadcast_weights(w, i, player, all_alive, second)

    return weights


def _get_heavy_weights(
    player: Any,
    action_to_weight_index: dict[str, int],
    weights: list[int],
    all_alive: list[Any],
    second: float,
    time_domain: str = "seconds",
) -> list[int]:
    """Compute the Heavy action weights in place from ``_HEAVY`` and return them.

    Baseline after role adjustment (before situational modifiers):
    ``tag_player=70, only_move=5, hide=5, hold=20`` — the hold weight is drawn
    from ``only_move`` (25 - 20 = 5).

    Situational blocks, in application order (only the ones Heavy calls):
    baseline -> hold baseline -> missile incentive (while missiles remain) ->
    capture incentive (when a base is capturable) -> lives-critical seek (medic,
    ammo fallback) -> shots-critical seek (ammo; ``no_resource``) -> not-active
    (downed; drains ``tag_player``, ``always_escape`` to ``only_move``) ->
    endgame rush -> request resupply (either resource) -> MECH-06 score
    broadcast.

    Invariant (production): ``plan_action`` needs only the *total* weight > 0,
    which is all ``random.choices`` enforces — it tolerates an unreachable
    negative bucket and raises only when the total is <= 0. Most slots stay
    >= 0; a few branches deliberately drive one slot negative (Heavy/Commander
    ``only_move`` while missiles remain, Heavy capture, Scout ``tag_player``
    when shots-critical), which is harmless. See ``test_plan_action_*`` in
    test_weights.py.
    """
    w, c, i = weights, _HEAVY, action_to_weight_index

    _apply_role_baseline(w, c, i)
    _apply_hold_baseline(w, c, i)

    if player.missiles_used < 5:
        w[i["only_move"]] -= c["missile_cz_cost"]
        w[i["missile_player"]] += c["missile_gain"]

    if player.can_capture_base_in_current_zone:
        w[i["only_move"]] -= c["base_capture_cz_cost"]
        w[i["tag_player"]] -= c["base_capture_tag_cost"]
        w[i["capture_base"]] += c["base_capture_gain"]

    lives_critical = player.starting_lives * c["critical_pct"]
    shots_critical = player.starting_shots * c["critical_pct"]

    if player.final_lives <= lives_critical:
        medic = _find_ally(all_alive, player.team_color, "medic")
        ammo = (
            _find_ally(all_alive, player.team_color, "ammo") if medic is None else None
        )
        _apply_seek_ally(w, c, i, player, medic, fallback_ally=ammo)

    if player.final_shots <= shots_critical:
        ammo = _find_ally(all_alive, player.team_color, "ammo")
        _apply_seek_ally(w, c, i, player, ammo, no_resource=True)

    _apply_not_active(
        w,
        c,
        i,
        player,
        all_alive,
        second,
        "medic",
        player.team_color,
        always_escape=True,
    )
    _apply_endgame_rush(w, c, i, player, second, "tag_player", time_domain)

    _apply_request_resupply_weight(w, i, player)

    # MECH-06: score broadcast behavioral bias
    _apply_score_broadcast_weights(w, i, player, all_alive, second)

    return weights


def _commander_nuke_gate(sp: int, ga: int) -> bool:
    """Return True when a Commander should consider firing a nuke.

    Low-awareness Commanders fire at the minimum 20-SP threshold.
    High-awareness Commanders stack until a higher threshold, enabling
    back-to-back nukes. Thresholds:
      ga < 30  → fire at sp > 20
      ga < 50  → fire at sp > 40
      ga < 70  → fire at sp > 60
      ga >= 70 → fire at sp > 80 (regardless of ga)
    """
    if sp > 80:
        return True
    if sp > 60 and ga < 70:
        return True
    if sp > 40 and ga < 50:
        return True
    if sp > 20 and ga < 30:
        return True
    return False


def _get_commander_weights(
    player: Any,
    action_to_weight_index: dict[str, int],
    weights: list[int],
    all_alive: list[Any],
    second: float,
    time_domain: str = "seconds",
) -> list[int]:
    """Compute the Commander action weights in place from ``_COMMANDER``.

    Baseline after role adjustment (before situational modifiers):
    ``tag_player=80, only_move=15, hold=10`` — the hold weight is drawn from
    ``only_move`` (15 - 10 = 5).

    Situational blocks, in application order (only the ones Commander calls):
    baseline -> hold baseline -> missile incentive (while missiles remain) ->
    capture incentive (with an early-game capture/tag bonus before
    ``base_early_threshold``) -> lives-critical seek (medic, ammo fallback) ->
    shots-critical seek (ammo; ``no_resource``) -> nuke special (gated by
    ``_commander_nuke_gate`` on SP/awareness; weight reduced per enemy in zone)
    -> not-active (downed; drains ``tag_player``, watches for an allied medic)
    -> endgame rush -> request resupply (either resource) -> MECH-06 score
    broadcast.

    Invariant (production): ``plan_action`` needs only the *total* weight > 0,
    which is all ``random.choices`` enforces — it tolerates an unreachable
    negative bucket and raises only when the total is <= 0. Most slots stay
    >= 0; a few branches deliberately drive one slot negative (Heavy/Commander
    ``only_move`` while missiles remain, Heavy capture, Scout ``tag_player``
    when shots-critical), which is harmless. See ``test_plan_action_*`` in
    test_weights.py.
    """
    w, c, i = weights, _COMMANDER, action_to_weight_index

    _apply_role_baseline(w, c, i)
    _apply_hold_baseline(w, c, i)

    if player.missiles_used < 5:
        w[i["only_move"]] -= c["missile_cz_cost"]
        w[i["missile_player"]] += c["missile_gain"]

    if player.can_capture_base_in_current_zone:
        # TIME-01: base-early threshold is 300 in the seconds domain (RBS /
        # tests, _COMMANDER["base_early_threshold"]) and
        # COMMANDER_BASE_EARLY_TICKS (600) in the tick domain (BatchSimulator).
        if time_domain == "ticks":
            from .time_constants import COMMANDER_BASE_EARLY_TICKS

            base_early_threshold = COMMANDER_BASE_EARLY_TICKS
        else:
            base_early_threshold = c["base_early_threshold"]
        early_bonus = c["base_early_bonus"] if second < base_early_threshold else 0
        w[i["only_move"]] -= c["base_capture_cz_cost"]
        w[i["tag_player"]] -= c["base_capture_tag_cost"] + early_bonus
        w[i["capture_base"]] += c["base_capture_gain"] + early_bonus

    lives_critical = player.starting_lives * c["critical_pct"]
    shots_critical = player.starting_shots * c["critical_pct"]

    if player.final_lives <= lives_critical:
        medic = _find_ally(all_alive, player.team_color, "medic")
        ammo = (
            _find_ally(all_alive, player.team_color, "ammo") if medic is None else None
        )
        _apply_seek_ally(w, c, i, player, medic, fallback_ally=ammo)

    if player.final_shots <= shots_critical:
        ammo = _find_ally(all_alive, player.team_color, "ammo")
        _apply_seek_ally(w, c, i, player, ammo, no_resource=True)

    if player.final_special >= player.special_cost:
        # Default 50 = mid-awareness tier (fires at sp > 40). Players should always
        # have game_awareness set; this fallback guards legacy/test objects only.
        game_awareness = getattr(player, "game_awareness", 50)
        if _commander_nuke_gate(player.final_special, game_awareness):
            enemies_in_zone = [
                p
                for p in all_alive
                if p.team_color != player.team_color
                and p.current_zone == player.current_zone
                and p.is_active_at(second)
            ]
            raw = c["special_base"] - c["special_per_enemy"] * len(enemies_in_zone)
            w[i["use_special"]] = int(raw * (player.special_usage / 50))
        # MECH-06: memory system can override the gate here when situational factors
        # (enemy nuke incoming, allied medic/ammo separated, or multi-nuke window open)
        # justify firing earlier or suppressing the nuke entirely.

    _apply_not_active(w, c, i, player, all_alive, second, "medic", player.team_color)
    _apply_endgame_rush(w, c, i, player, second, "tag_player", time_domain)

    _apply_request_resupply_weight(w, i, player)

    # MECH-06: score broadcast behavioral bias
    _apply_score_broadcast_weights(w, i, player, all_alive, second)

    return weights


# ---------------------------------------------------------------------------
# STAT-03 post-processing helpers (public so tests can import them directly)
# ---------------------------------------------------------------------------

# Seconds in a full round — the seconds-domain round duration used by the
# byte-identical ResourceBasedSimulator and all existing seconds-domain tests.
_ROUND_DURATION = 900


def check_stamina_penalty(player, second: float, round_duration: int = 900) -> None:
    """Evaluate 10%-checkpoint stamina penalties for *player* at *second*.

    Increments ``player.stamina_penalty_count`` once for every 10% checkpoint
    that has elapsed where ``player.stamina < checkpoint_percent``.  Penalties
    stack; each one later reduces ``only_move`` weight (in ``combat.py``) and
    ``stamina_hit_modifier`` on the player object.

    Idempotent across repeated calls within the same 10% window — the
    ``stamina_next_check_pct`` cursor only advances forward.

    TIME-01: the schedule is purely proportional (``second / round_duration``)
    and therefore unit-agnostic. The two simulators feed a matched
    (time, round_duration) pair: ResourceBasedSimulator stays second-internal
    and uses the seconds default (900) so it remains byte-identical; the
    tick-native BatchSimulator passes ``round_duration=TICKS_PER_ROUND`` (1800)
    together with a tick-valued ``second``. No checkpoint-constant conversion
    is needed — only a consistent domain on both arguments.
    """
    elapsed_pct = int(second / round_duration * 100)
    next_check_pct = getattr(player, "stamina_next_check_pct", 10)
    penalty_count = getattr(player, "stamina_penalty_count", 0)
    while next_check_pct <= elapsed_pct and next_check_pct <= 100:
        if player.stamina < next_check_pct:
            penalty_count += 1
        next_check_pct += 10
    player.stamina_penalty_count = penalty_count
    player.stamina_next_check_pct = next_check_pct


def apply_decision_making_spread(weights: list, dm: int) -> list:
    """Apply a linear spread multiplier to *weights* in-place based on *dm* (0-100).

    factor = 1 + dm / 100.  The highest-weight action is multiplied by
    *factor*; all other weights are divided by *factor* (floored at 0).
    dm=0 → factor=1.0 → weights unchanged.  dm=100 → factor=2.0.

    Mutates and returns *weights*; callers must not rely on the original values.
    """
    if dm <= 0:
        return weights
    factor = 1.0 + dm / 100.0
    max_w = max(weights)
    if max_w <= 0:
        return weights
    max_idx = weights.index(max_w)
    inv = 1.0 / factor
    for idx in range(len(weights)):
        w = weights[idx]
        if idx == max_idx:
            weights[idx] = int(w * factor)
        elif w > 0:
            weights[idx] = int(w * inv)
    return weights
