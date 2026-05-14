"""
Pure game-mechanic functions shared by ResourceBasedSimulator and BatchSimulator.
No Django ORM imports or DB writes. Both player types (PlayerRoundState and PlayerState)
satisfy the duck-type interface required by these functions.
"""

import random


def shot_cooldown(player, second: float) -> float:
    """Seconds a player must wait between shots.

    Rapid-fire scouts (special active) have no restriction.
    Heavies fire once per second. Everyone else twice per second.
    """
    if player.role == "scout" and player.special_active_until > second:
        return 0.0
    if player.role == "heavy":
        return 1.0
    return 0.5


def choose_tag_target(player, all_alive, second, movement_ctx=None, *, los_filter=None):
    """Return a random enemy target weighted by role, or None.

    los_filter: callable(actor, candidates, movement_ctx) -> filtered_candidates.
    Falls back to same-zone filtering when not provided (useful in tests).
    """
    if los_filter is None:
        los_filter = lambda actor, candidates, ctx: [
            c for c in candidates if c.current_zone == actor.current_zone
        ]
    role_weights = {"commander": 5, "heavy": 5, "scout": 3, "medic": 1, "ammo": 3}
    enemies = [
        p
        for p in all_alive
        if p.team_color != player.team_color
        and p.final_lives > 0
        and (
            p.is_active_at(second)
            or (p.is_taggable_at(second) and player.last_tagged_id != p.tag_id_key)
        )
    ]
    targets = los_filter(player, enemies, movement_ctx)
    if not targets or player.final_shots <= 0:
        return None
    w = [
        role_weights.get(t.role, 1) + (10 if t.is_active_at(second) else 1)
        for t in targets
    ]
    return random.choices(targets, w)[0]


def choose_resupply_target(player, all_alive, second):
    """Return a teammate to resupply (lives or shots), or None.

    Prioritizes teammates with the greatest resource deficit, weighted by role.
    Returns None when all in-zone teammates are at full resources.
    """
    role_weights = {"commander": 5, "heavy": 8, "scout": 3, "medic": 1, "ammo": 6}
    teammates = [
        p
        for p in all_alive
        if p.team_color == player.team_color
        and p.current_zone == player.current_zone
        and p is not player
        and p.final_lives > 0
        and p.is_resupplyable_at(second)
    ]
    if not teammates:
        return None
    all_full = True
    tw = []
    for t in teammates:
        deficit = (
            (t.max_shots - t.final_shots)
            if player.role == "ammo"
            else (t.max_lives - t.final_lives)
        ) * 10
        if deficit > 0:
            all_full = False
        tw.append(role_weights.get(t.role, 1) * deficit)
    return None if all_full else random.choices(teammates, tw)[0]


def choose_zone_change(player, all_alive) -> int | None:
    """Return the zone index to move toward (0=red, 1=neutral, 2=blue), or None.

    Seeks an allied Medic when critically low on lives, or an Ammo when critically
    low on shots. Returns None when no reactive movement is warranted.
    """
    lives_critical = player.max_lives * 0.3
    shots_critical = player.max_shots * 0.3
    if player.final_lives <= lives_critical and player.role != "medic":
        medics = [
            p
            for p in all_alive
            if p.team_color == player.team_color and p.role == "medic"
        ]
        if medics and player.current_zone != medics[0].current_zone:
            return medics[0].current_zone
    elif player.final_shots <= shots_critical:
        ammos = [
            p
            for p in all_alive
            if p.team_color == player.team_color and p.role == "ammo"
        ]
        if ammos and player.current_zone != ammos[0].current_zone:
            return ammos[0].current_zone
    return None
