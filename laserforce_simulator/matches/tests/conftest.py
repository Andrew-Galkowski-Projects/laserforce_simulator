from teams.models import Team, Player


def make_team_with_slots(prefix: str) -> tuple:
    """Create a fully-slotted team with 6 players (commander/heavy/2 scouts/medic/ammo).

    Returns (team, players) where players maps role keys to Player instances.
    """
    team = Team.objects.create(name=f"{prefix} Team")
    p_cmd  = Player.objects.create(team=team, name=f"{prefix} commander")
    p_hvy  = Player.objects.create(team=team, name=f"{prefix} heavy")
    p_s1   = Player.objects.create(team=team, name=f"{prefix} scout1")
    p_s2   = Player.objects.create(team=team, name=f"{prefix} scout2")
    p_med  = Player.objects.create(team=team, name=f"{prefix} medic")
    p_ammo = Player.objects.create(team=team, name=f"{prefix} ammo")
    team.slot_commander = p_cmd
    team.slot_heavy     = p_hvy
    team.slot_scout_1   = p_s1
    team.slot_scout_2   = p_s2
    team.slot_medic     = p_med
    team.slot_ammo      = p_ammo
    team.save()
    players = {
        "commander": p_cmd,
        "heavy":     p_hvy,
        "scout":     p_s1,
        "scout_2":   p_s2,
        "medic":     p_med,
        "ammo":      p_ammo,
    }
    return team, players