ROLE_STATS: dict[str, dict[str, int]] = {
    "commander": {"shot_power": 2, "shield": 3},
    "heavy": {"shot_power": 3, "shield": 3},
    "scout": {"shot_power": 1, "shield": 1},
    "medic": {"shot_power": 1, "shield": 1},
    "ammo": {"shot_power": 1, "shield": 1},
}

MAX_LIVES: dict[str, int] = {
    "commander": 30,
    "heavy": 20,
    "scout": 30,
    "medic": 20,
    "ammo": 20,
}

MAX_SHOTS: dict[str, int] = {
    "commander": 60,
    "heavy": 40,
    "scout": 60,
    "medic": 30,
    "ammo": 15,
}

SPECIAL_COST: dict[str, int] = {
    "commander": 20,
    "scout": 10,
    "medic": 10,
    "ammo": 15,
}
