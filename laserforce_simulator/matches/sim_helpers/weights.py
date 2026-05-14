from typing import Any

# ---------------------------------------------------------------------------
# Per-role constants — all tuning numbers in one place per role
# ---------------------------------------------------------------------------

_MEDIC = dict(
    baseline_tag=-65,
    baseline_cz=-30,
    baseline_hide=+30,
    baseline_resupply=+65,
    support_capture_gain=5,
    support_capture_resupply_cost=5,
    low_lives_threshold=3,  # fixed life count (not a percentage)
    low_lives_hide_cost=30,
    low_lives_resupply_gain=30,
    not_active_resupply_cost=60,
    endgame_capture_gain=40,
    endgame_resupply_cost=40,
    special_per_ally=20,
)

_AMMO = dict(
    baseline_tag=-25,
    baseline_cz=-30,
    baseline_resupply=+55,
    support_capture_gain=5,
    support_capture_resupply_cost=5,
    low_lives_threshold=3,
    low_lives_diff_zone_resupply_cost=50,
    low_lives_same_zone_resupply_cost=30,
    low_lives_same_zone_hide_gain=30,
    low_lives_fallback_resupply_cost=50,
    low_lives_fallback_hide_gain=50,
    low_lives_tag_cost=20,
    low_lives_resupply_gain=20,
    special_per_ally=20,
    endgame_capture_gain=40,
    endgame_resupply_cost=40,
)

_SCOUT = dict(
    baseline_tag=-30,
    baseline_cz=+30,
    critical_pct=0.3,
    base_capture_gain=20,
    base_capture_tag_cost=20,
    seek_diff_tag=30,
    seek_diff_cz=30,
    seek_same_cz=20,
    seek_same_tag=20,
    seek_same_hide=40,
    seek_base_capture_gain=40,
    seek_base_tag_cost=20,
    seek_base_cz_cost=20,
    seek_no_ally_tag=30,
    seek_no_ally_hide=30,
    seek_no_ammo_tag=50,
    seek_no_ammo_hide=50,
    not_active_cz_cap=10,
    endgame_capture_gain=30,
    endgame_tag_cost=30,
)

_HEAVY = dict(
    baseline_cz=-5,
    baseline_hide=+5,
    critical_pct=0.3,
    missile_cz_cost=15,
    missile_gain=15,
    base_capture_gain=30,
    base_capture_tag_cost=20,
    base_capture_cz_cost=10,
    seek_diff_tag=30,
    seek_diff_cz=30,
    seek_same_cz=10,
    seek_same_tag=20,
    seek_same_hide=30,
    seek_base_capture_gain=40,
    seek_base_tag_cost=20,
    seek_base_cz_cost=20,
    seek_no_ally_tag=30,
    seek_no_ally_hide=30,
    seek_no_ammo_tag=50,
    seek_no_ammo_hide=50,
    not_active_tag_cost=70,
    endgame_capture_gain=30,
    endgame_tag_cost=30,
)

_COMMANDER = dict(
    baseline_tag=+10,
    baseline_cz=-15,
    critical_pct=0.3,
    missile_cz_cost=15,
    missile_gain=15,
    base_capture_gain=50,
    base_capture_tag_cost=40,
    base_capture_cz_cost=10,
    base_early_bonus=20,
    base_early_threshold=300,
    seek_diff_tag=30,
    seek_diff_cz=30,
    seek_same_cz=10,
    seek_same_tag=20,
    seek_same_hide=30,
    seek_base_capture_gain=40,
    seek_base_tag_cost=20,
    seek_base_cz_cost=20,
    seek_no_ally_tag=30,
    seek_no_ally_hide=30,
    seek_no_ammo_tag=50,
    seek_no_ammo_hide=50,
    not_active_tag_cost=70,
    special_base=100,
    special_per_enemy=20,
    endgame_capture_gain=30,
    endgame_tag_cost=30,
)


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
    w[i["change_zone"]] += c.get("baseline_cz", 0)
    w[i["hide"]] += c.get("baseline_hide", 0)
    w[i["resupply_ally"]] += c.get("baseline_resupply", 0)


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
            w[i["change_zone"]] += c["seek_diff_cz"]
        else:
            w[i["change_zone"]] -= c["seek_same_cz"]
            w[i["tag_player"]] -= c["seek_same_tag"]
            w[i["hide"]] += c["seek_same_hide"]
    elif player.can_capture_base_in_current_zone:
        w[i["capture_base"]] += c["seek_base_capture_gain"]
        w[i["tag_player"]] -= c["seek_base_tag_cost"]
        w[i["change_zone"]] -= c["seek_base_cz_cost"]
    elif fallback_ally is not None:
        if player.current_zone != fallback_ally.current_zone:
            w[i["tag_player"]] -= c["seek_diff_tag"]
            w[i["change_zone"]] += c["seek_diff_cz"]
        else:
            w[i["change_zone"]] -= c["seek_same_cz"]
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
    When downed, redistribute idle weight to hide or change_zone based on a nearby ally.

    - watch_role / watch_team_color: the ally to look for in the current zone.
    - drain_key: the weight index to drain from (tag_player or resupply_ally).
    - also_drain_resupply: if True (ammo), pool tag_player + resupply_ally and zero both.
    - always_escape: if True, always redirect to change_zone regardless of ally presence
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
            w[i["change_zone"]] += idle


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


def _apply_endgame_rush(w, c, i, player, second, offset_key):
    """End-game base rush: shift weight from offset_key to capture_base when time is low."""
    if second >= 840 and player.can_capture_base_in_current_zone:
        gain_key = "endgame_capture_gain"
        cost_key = f"endgame_{'tag' if offset_key == 'tag_player' else 'resupply'}_cost"
        w[i["capture_base"]] += c[gain_key]
        w[i[offset_key]] -= c[cost_key]


# ---------------------------------------------------------------------------
# Role weight functions (public API — signatures unchanged)
# ---------------------------------------------------------------------------


def _get_medic_weights(player, action_to_weight_index, weights, all_alive, second):
    w, c, i = weights, _MEDIC, action_to_weight_index

    _apply_role_baseline(w, c, i)
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
    _apply_endgame_rush(w, c, i, player, second, "resupply_ally")

    # resupply_synergy: scales resupply_ally weight
    resupply_synergy = getattr(player, "resupply_synergy", 50)
    w[i["resupply_ally"]] = max(0, int(w[i["resupply_ally"]] * (resupply_synergy / 50)))

    _apply_request_resupply_weight(w, i, player, shots_only=True)

    return weights


def _get_ammo_weights(player, action_to_weight_index, weights, all_alive, second):
    w, c, i = weights, _AMMO, action_to_weight_index

    _apply_role_baseline(w, c, i)
    _apply_support_base_capture(w, c, i, player)

    if player.final_lives <= c["low_lives_threshold"]:
        medic = _find_ally(all_alive, player.team_color, "medic")
        if medic is not None:
            if player.current_zone != medic.current_zone:
                w[i["resupply_ally"]] -= c["low_lives_diff_zone_resupply_cost"]
                w[i["change_zone"]] += c["low_lives_diff_zone_resupply_cost"]
            else:
                w[i["resupply_ally"]] -= c["low_lives_same_zone_resupply_cost"]
                w[i["hide"]] += c["low_lives_same_zone_hide_gain"]
        else:
            heavy = _find_ally(all_alive, player.team_color, "heavy")
            if heavy is not None:
                if player.current_zone != heavy.current_zone:
                    w[i["resupply_ally"]] -= c["low_lives_fallback_resupply_cost"]
                    w[i["change_zone"]] += c["low_lives_fallback_resupply_cost"]
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
    _apply_endgame_rush(w, c, i, player, second, "resupply_ally")

    # resupply_synergy: scales resupply_ally weight
    resupply_synergy = getattr(player, "resupply_synergy", 50)
    w[i["resupply_ally"]] = max(0, int(w[i["resupply_ally"]] * (resupply_synergy / 50)))

    _apply_request_resupply_weight(w, i, player, lives_only=True)

    return weights


def _get_scout_weights(player, action_to_weight_index, weights, all_alive, second):
    w, c, i = weights, _SCOUT, action_to_weight_index

    _apply_role_baseline(w, c, i)

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

    # Not active: redistribute tag weight — 20% to change_zone (capped), 80% to hide
    if not player.is_active_at(second):
        tag_weight = max(0, w[i["tag_player"]])
        w[i["tag_player"]] = 0
        cz_bonus = min(c["not_active_cz_cap"], tag_weight)
        w[i["change_zone"]] += cz_bonus
        w[i["hide"]] += tag_weight - cz_bonus

    _apply_endgame_rush(w, c, i, player, second, "tag_player")

    _apply_request_resupply_weight(w, i, player)

    return weights


def _get_heavy_weights(player, action_to_weight_index, weights, all_alive, second):
    w, c, i = weights, _HEAVY, action_to_weight_index

    _apply_role_baseline(w, c, i)

    if player.missiles_used < 5:
        w[i["change_zone"]] -= c["missile_cz_cost"]
        w[i["missile_player"]] += c["missile_gain"]

    if player.can_capture_base_in_current_zone:
        w[i["change_zone"]] -= c["base_capture_cz_cost"]
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
        w, c, i, player, all_alive, second, "medic", player.team_color, always_escape=True
    )
    _apply_endgame_rush(w, c, i, player, second, "tag_player")

    _apply_request_resupply_weight(w, i, player)

    return weights


def _get_commander_weights(player, action_to_weight_index, weights, all_alive, second):
    w, c, i = weights, _COMMANDER, action_to_weight_index

    _apply_role_baseline(w, c, i)

    if player.missiles_used < 5:
        w[i["change_zone"]] -= c["missile_cz_cost"]
        w[i["missile_player"]] += c["missile_gain"]

    if player.can_capture_base_in_current_zone:
        early_bonus = c["base_early_bonus"] if second < c["base_early_threshold"] else 0
        w[i["change_zone"]] -= c["base_capture_cz_cost"]
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
        enemies_in_zone = [
            p
            for p in all_alive
            if p.team_color != player.team_color
            and p.current_zone == player.current_zone
            and p.is_active_at(second)
        ]
        raw = c["special_base"] - c["special_per_enemy"] * len(enemies_in_zone)
        w[i["use_special"]] = int(raw * (player.special_usage / 50))

    _apply_not_active(w, c, i, player, all_alive, second, "medic", player.team_color)
    _apply_endgame_rush(w, c, i, player, second, "tag_player")

    _apply_request_resupply_weight(w, i, player)

    return weights


# ---------------------------------------------------------------------------
# STAT-03 post-processing helpers (public so tests can import them directly)
# ---------------------------------------------------------------------------

_ROUND_DURATION = 900


def check_stamina_penalty(player, second: float, round_duration: int = 900) -> None:
    """Evaluate 10%-checkpoint stamina penalties for *player* at *second*.

    Increments ``player.stamina_penalty_count`` once for every 10% checkpoint
    that has elapsed where ``player.stamina < checkpoint_percent``.  Penalties
    stack; each one later reduces ``change_zone`` weight (in ``combat.py``) and
    ``stamina_hit_modifier`` on the player object.

    Idempotent across repeated calls within the same 10% window — the
    ``stamina_next_check_pct`` cursor only advances forward.
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
