"""MECH-07: Player Modes (spec #542).

A **Mode** (GLOSSARY.md) is how a player currently chooses where to go. Players
start in their role's default and switch on game-state triggers, using only what
they could know. The formulas are written out in docs/agents/matches/sim-helpers.md.

Pure, Django-free; map-less rounds never reach it. ``pathfinding`` imports this
module, so this module must not import ``pathfinding``.
"""

from __future__ import annotations

import random
from enum import StrEnum
from typing import TYPE_CHECKING, Callable, cast

if TYPE_CHECKING:
    from .map_context import MapContext
    from .player_state import PlayerState

from . import support_survival
from .follow_rule import Cell, chased_tag_ids, open_trail
from .goal_scoring import draw_goal
from .map_context import _NEUTRAL_BASE_TYPES
from .time_constants import HELD_GOAL_EXPIRY, RESPAWN_TICKS


class Mode(StrEnum):
    ATTACK = "attack"
    DEFENSE = "defense"
    CONTROL = "control"


# ── Switching (spec decisions 19, 20) ────────────────────────────────────
#: The least time in a Mode before the player may switch out of it.
MODE_DWELL_TICKS: int = 60
#: How often past the dwell the player re-reads the triggers.
MODE_CHECK_PERIOD_TICKS: int = 10
#: Row 1: lives or shots at or below this share of max.
LOW_RESOURCE_SHARE: float = 0.5
_LOW_RESOURCE_ROLES: tuple[str, ...] = ("commander", "scout")
#: Row 2: what each role wants when a recent sighting shows its own Heavy Downed.
_HEAVY_DOWNED_MODE: dict[str, Mode] = {
    "commander": Mode.CONTROL,
    "scout": Mode.ATTACK,
    "medic": Mode.CONTROL,
    "ammo": Mode.CONTROL,
}
#: The memory statuses the Heavy trigger reads as Downed (spec decision 27).
_DOWNED_STATUSES: tuple[str, ...] = ("downed", "reset_window")
#: A sighting of a Down counts for this long: after one Down's length, that Down is
#: over. Row 2 reads it, and so do Attack targets and Trail costs.
HEAVY_DOWN_WINDOW_TICKS: int = RESPAWN_TICKS

# ── Trail avoidance (spec decisions 10, 22) ──────────────────────────────
#: What a step into a band cell of a remembered Downed enemy's Trail adds.
TRAIL_COST: float = 3.0

# ── Weights (spec decisions 8, 21) ───────────────────────────────────────
MODE_WEIGHT_MULTIPLIER: float = 1.25
# Like weights._HIDE_IDX, these mirror combat._ACTION_IDX (tag_player, resupply_ally,
# capture_base, hold): player_state imports this module, so it stays free of combat.
_MODE_WEIGHT_SLOTS: dict[Mode, tuple[int, ...]] = {
    Mode.ATTACK: (0,),
    Mode.DEFENSE: (5,),
    Mode.CONTROL: (3, 8),
}

# ── Goals (spec decisions 14, 16, 17) ────────────────────────────────────
#: Attack: the value of a cell that can see a remembered target of this role. One
#: table for every attacker (CAL-02 D5, spec #563), proportional to how real
#: Commanders spread their zaps per enemy player over the 128 Internationals 2026
#: Test games (28 on the Commander, 22.5 on the Heavy, 17.4 on each Scout, 15.9 on
#: the Ammo, 7.7 on the Medic). Before CAL-02 only the Medic (1.0) and Ammo (0.7)
#: were hunted.
_ATTACK_TARGET_VALUES: dict[str, float] = {
    "commander": 1.0,
    "heavy": 0.8,
    "scout": 0.6,
    "ammo": 0.55,
    "medic": 0.3,
}
#: Base goal: a Commander needs this many Shots for a capture (``combat.capture_base``).
BASE_CAPTURE_SHOTS: int = 3
#: Control: added for a cell in range of the enemy base, for one in range of a neutral
#: base, and for one that sees a fresh enemy; the value then stops at the cap.
CONTROL_BONUS: float = 0.25
CONTROL_VALUE_CAP: float = 1.0
#: Control: how long a player stays on a drawn goal they reached.
CONTROL_HOLD_TICKS: int = 20
#: Defense: value = base weight x base + closeness weight x teamwork share x closeness.
DEFENSE_BASE_WEIGHT: float = 0.7
DEFENSE_CLOSENESS_WEIGHT: float = 0.3
#: Defense: closeness falls from 1 on the Heavy's cell to 0 at this distance.
DEFENSE_CLOSENESS_FEET: float = 30.0
#: Defense: ``resupply_ally`` sets a goal only when the neediest ally is at or below
#: this share of the resource the support restores.
DEFENSE_RESUPPLY_SHARE: float = 0.75
#: A support may not hide while an ally is at or below this share of what it restores.
#: Lower than the Defense line: at 75% a Medic could almost never hide (MECH-17).
HIDE_NEEDED_SHARE: float = 0.5

_DEFAULT_MODE: dict[str, Mode] = {
    "commander": Mode.ATTACK,
    "scout": Mode.ATTACK,
    "heavy": Mode.CONTROL,
    "medic": Mode.DEFENSE,
    "ammo": Mode.DEFENSE,
}


def default_mode(role: str) -> Mode:
    """The Mode *role* starts in and returns to; Attack for an unknown role."""
    return _DEFAULT_MODE.get(role, Mode.ATTACK)


def _fresh_entry(
    player, tag_id: str, tick: float, time_domain: str = "ticks"
) -> dict | None:
    """*tag_id*'s memory entry while fresh, by the role table ``_cell_from_memory``
    uses (an entry with no role goes stale quickly), else None."""
    entry = player.player_memory.get(tag_id)
    if entry is None:
        return None
    table = support_survival.stale_table(time_domain)
    threshold = table.get(entry.get("role", "scout"), table["scout"])
    return None if tick - entry.get("timestamp", 0) > threshold else entry


def _shows_downed(entry: dict) -> bool:
    """True for ``downed`` and ``reset_window``. Nuke and medic-alert entries have
    no ``status``, which counts as ``active``."""
    return entry.get("status", "active") in _DOWNED_STATUSES


def _down_could_be_on(entry: dict, tick: float) -> bool:
    """True within one Down's length of the sighting: after that, that Down is over."""
    return tick - entry.get("timestamp", 0) <= HEAVY_DOWN_WINDOW_TICKS


def _own_heavy_seen_downed(player, tick: float) -> bool:
    """True while a remembered Down of the own Heavy could still be going on."""
    entry = player.player_memory.get(f"{player.team_color}_heavy")
    return entry is not None and _down_could_be_on(entry, tick) and _shows_downed(entry)


def _team_is_losing(player) -> bool:
    """True when a score broadcast named the other team. ``"tied"`` is not losing."""
    winning_team = player.score_broadcast_state.get("winning_team")
    return winning_team not in (None, "tied", player.team_color)


def _low_on_resources(player) -> bool:
    return (
        player.final_lives <= player.max_lives * LOW_RESOURCE_SHARE
        or player.final_shots <= player.max_shots * LOW_RESOURCE_SHARE
    )


def _wanted_mode(player, tick: float) -> Mode:
    """The Mode the triggers ask for; the first matching row wins."""
    role = player.role
    if role in _LOW_RESOURCE_ROLES and _low_on_resources(player):
        return Mode.CONTROL
    if role in _HEAVY_DOWNED_MODE and _own_heavy_seen_downed(player, tick):
        return _HEAVY_DOWNED_MODE[role]
    if role == "heavy" and _team_is_losing(player):
        return Mode.ATTACK
    return default_mode(role)


def update_mode(player: PlayerState, tick: float) -> None:
    """One tick of Mode switching: count the tick, then, past the dwell and on the
    check cadence, switch toward the wanted Mode. See sim-helpers.md."""
    player.mode_ticks[player.mode] = player.mode_ticks.get(player.mode, 0) + 1
    if tick < player.mode_since + MODE_DWELL_TICKS or tick < player.mode_next_check:
        return
    player.mode_next_check = int(tick) + MODE_CHECK_PERIOD_TICKS
    wanted = _wanted_mode(player, tick)
    if wanted is player.mode or random.random() >= player.game_awareness / 100:
        return
    player.mode = wanted
    player.mode_since = int(tick)
    player.mode_hold_until = None
    # An action goal (committed[1]) stays: it interrupts the Mode's goals anyway.
    committed = player._committed_goal
    if committed is not None and not committed[1]:
        player._committed_goal = None


def _remembered_cell(entry: dict) -> Cell:
    return (int(entry["cell"][0]), int(entry["cell"][1]))


def _as_cells(cells: list) -> list[Cell]:
    """A map's stored cells as tuples, which ``draw_goal`` needs (JSON gives lists)."""
    return [(c[0], c[1]) for c in cells]


def _fresh_teammate_cell(
    player, role: str, all_alive: list, tick: float, time_domain: str
) -> Cell | None:
    """The remembered cell of the player's own *role*, or None when the entry is not
    fresh or the teammate is eliminated: memory has no eliminated status, and
    ``all_alive`` is built at tick start, so lives are checked too."""
    tag = f"{player.team_color}_{role}"
    if not any(p.tag_id_key == tag and p.final_lives > 0 for p in all_alive):
        return None
    entry = _fresh_entry(player, tag, tick, time_domain)
    if entry is None or entry.get("cell") is None:
        return None
    return _remembered_cell(entry)


def _fresh_enemies(
    player, all_alive: list, enemy_color: str, tick: float, time_domain: str
) -> list[tuple[str, dict]]:
    """``(tag_id, entry)`` per fresh enemy entry, by tag id. Memory has no eliminated
    status, so an enemy who is not alive is dropped here (spec decision 26)."""
    alive = sorted(
        p.tag_id_key
        for p in all_alive
        if p.team_color == enemy_color and p.final_lives > 0
    )
    entries = ((tag, _fresh_entry(player, tag, tick, time_domain)) for tag in alive)
    return [
        (tag, entry)
        for tag, entry in entries
        if entry is not None and entry.get("cell") is not None
    ]


def _enemy_color(player) -> str:
    return "blue" if player.team_color == "red" else "red"


def known_threats(
    player: PlayerState,
    all_alive: list,
    movement_ctx: MapContext,
    tick: float,
    time_domain: str,
) -> int:
    """How many fresh remembered enemies have a remembered cell that can see the
    player's cell (spec #550). Reads memory only; no true enemy cell and no RNG."""
    here = cast(Cell, (player.cell_row, player.cell_col))
    return sum(
        movement_ctx.can_see(_remembered_cell(entry), here)
        for _, entry in _fresh_enemies(
            player, all_alive, _enemy_color(player), tick, time_domain
        )
    )


def _resupply_share(ally, supporter_role: str) -> float:
    """How full *ally* is of what the supporter restores: a Medic lives, an Ammo shots."""
    if supporter_role == "medic":
        return ally.final_lives / max(
            1, getattr(ally, "max_lives", ally.starting_lives)
        )
    return ally.final_shots / max(1, getattr(ally, "max_shots", ally.starting_shots))


def hide_allowed(
    player: PlayerState,
    all_alive: list,
    movement_ctx: MapContext,
    tick: float,
    time_domain: str,
) -> bool:
    """Whether an Active Medic or Ammo may hide: ``not needed and (threatened or
    settled)`` (spec #550). Ally reads stay true-state until MECH-14. No RNG.

    Support-survival switches 3 (Medic) and 4 (Ammo): always, whatever the allies
    need; a low ally comes to the support instead."""
    if support_survival.hide_gate_open(player.role):
        return True
    needed = any(
        p.team_color == player.team_color
        and p is not player
        and p.final_lives > 0
        and _resupply_share(p, player.role) <= HIDE_NEEDED_SHARE
        for p in all_alive
    )
    if needed:
        return False
    committed = player._committed_goal
    if committed is None or committed[0] == (player.cell_row, player.cell_col):
        return True
    threats = player.known_threats
    if threats is None:
        threats = known_threats(player, all_alive, movement_ctx, tick, time_domain)
    return threats > 0


def _attack_targets(
    player,
    all_alive: list,
    enemy_color: str,
    tick: float,
    time_domain: str,
    exclude: set[str],
) -> list[tuple[str, Cell]]:
    """``(role, cell)`` of the remembered enemies worth hunting: every role in
    ``_ATTACK_TARGET_VALUES`` (CAL-02 D5). Only ``downed`` is out, and only for as
    long as a Down lasts: a player in ``reset_window`` can still be hunted (spec
    decision 27)."""
    return [
        (entry["role"], _remembered_cell(entry))
        for tag, entry in _fresh_enemies(
            player, all_alive, enemy_color, tick, time_domain
        )
        if entry.get("role") in _ATTACK_TARGET_VALUES
        and not (entry.get("status") == "downed" and _down_could_be_on(entry, tick))
        and tag not in exclude
    ]


def _attack_candidates(
    targets: list[tuple[str, Cell]], movement_ctx: MapContext
) -> dict[Cell, float]:
    """Every cell that can see a target, worth the best target it sees."""
    values: dict[Cell, float] = {}
    for role, cell in targets:
        for candidate in movement_ctx.cells_visible_from(cell):
            values[candidate] = max(
                values.get(candidate, 0.0), _ATTACK_TARGET_VALUES[role]
            )
    return values


def attack_tag_goal(
    player: PlayerState,
    all_alive: list,
    enemy_color: str,
    tick: float,
    time_domain: str = "ticks",
    exclude: set[str] | None = None,
) -> Cell | None:
    """The nearest Attack target's remembered cell, for a Tag or Missile action;
    ties by (row, col). ``exclude``: tag ids to skip (the Follow rule's Chased)."""
    targets = _attack_targets(
        player, all_alive, enemy_color, tick, time_domain, exclude or set()
    )
    row, col = cast(Cell, (player.cell_row, player.cell_col))
    return min(
        (cell for _, cell in targets),
        key=lambda c: (abs(c[0] - row) + abs(c[1] - col), c),
        default=None,
    )


def _enemy_base_keys(player, enemy_color: str, movement_ctx: MapContext) -> frozenset:
    """The range cells of the enemy Base unless the player captured it. Reads
    ``base_sight_data[enemy_color]``, because ``base_in_range`` answers 14 for either
    team's base."""
    if player.opposing_base_destroyed:
        return frozenset()
    return movement_ctx.base_sight_data.get(enemy_color, frozenset())


def _neutral_base_keys(player, movement_ctx: MapContext) -> frozenset:
    """The range cells of the neutral Bases unless the player captured one: a capture
    is per player, and one flag covers every neutral Base."""
    if player.neutral_base_destroyed:
        return frozenset()
    return frozenset().union(
        *(
            movement_ctx.base_sight_data.get(base_type, frozenset())
            for base_type in _NEUTRAL_BASE_TYPES
        )
    )


def base_goal(
    player: PlayerState,
    all_alive: list,
    enemy_color: str,
    movement_ctx: MapContext,
    tick: float,
) -> Cell | None:
    """A Commander's goal while it has 3+ Shots and a Base left: a drawn range cell of
    an uncaptured Base (spec #550). A goal it committed to on one of those cells stays
    until reached. None for any other role, or with nothing to go for."""
    if player.role != "commander" or player.final_shots < BASE_CAPTURE_SHOTS:
        return None
    keys = _neutral_base_keys(player, movement_ctx) | _enemy_base_keys(
        player, enemy_color, movement_ctx
    )
    if not keys:
        return None
    committed = player._committed_goal
    if (
        committed is not None
        and committed[2] == HELD_GOAL_EXPIRY
        and committed[0] != (player.cell_row, player.cell_col)
        and f"{committed[0][0]},{committed[0][1]}" in keys
    ):
        return committed[0]
    cells = sorted(
        (int(row), int(col)) for row, col in (key.split(",") for key in keys)
    )
    return draw_goal(player, cells, lambda cell: 1.0, all_alive, movement_ctx, tick)


def _control_value(
    player,
    all_alive: list,
    enemy_color: str,
    movement_ctx: MapContext,
    tick: float,
    time_domain: str,
) -> Callable[[Cell], float]:
    """The Control value rule."""
    base_keys = _enemy_base_keys(player, enemy_color, movement_ctx)
    neutral_keys = _neutral_base_keys(player, movement_ctx)
    enemy_cells = [
        _remembered_cell(entry)
        for _, entry in _fresh_enemies(
            player, all_alive, enemy_color, tick, time_domain
        )
    ]

    def value_of(cell: Cell) -> float:
        value = movement_ctx.los_rank(cell)
        key = f"{cell[0]},{cell[1]}"
        if key in base_keys:
            value += CONTROL_BONUS
        if key in neutral_keys:
            value += CONTROL_BONUS
        if any(movement_ctx.can_see(cell, seen) for seen in enemy_cells):
            value += CONTROL_BONUS
        return min(CONTROL_VALUE_CAP, value)

    return value_of


def _defense_value(
    player, heavy_cell: Cell, movement_ctx: MapContext
) -> Callable[[Cell], float]:
    """The Defense value rule: hidden cells for a Medic, open ones for an Ammo, and
    the player's ``teamwork`` pulls them toward the Heavy. Support-survival switch 2:
    hidden cells for both."""
    hidden_for_both = support_survival.HIDDEN_SUPPORT_CELLS

    def value_of(cell: Cell) -> float:
        rank = movement_ctx.los_rank(cell)
        base = 1.0 - rank if player.role == "medic" or hidden_for_both else rank
        squares = max(abs(cell[0] - heavy_cell[0]), abs(cell[1] - heavy_cell[1]))
        feet = squares * movement_ctx.feet_per_square
        closeness = max(0.0, 1.0 - feet / DEFENSE_CLOSENESS_FEET)
        return (
            DEFENSE_BASE_WEIGHT * base
            + DEFENSE_CLOSENESS_WEIGHT * (player.teamwork / 100) * closeness
        )

    return value_of


def _update_control_hold(player, tick: float) -> bool:
    """Start or end the player's hold, and return True while it stands: a Control
    player stays on the drawn goal they reached.

    Reaching one starts ``mode_hold_until``. ``choose_goal_cell`` then recommits the
    current cell, so the hold only stands while the commitment is still on this cell
    and not an action goal: an override or action goal that moved the player on ends
    it, and they are not frozen wherever that goal stopped.
    """
    committed = player._committed_goal
    on_goal = (
        committed is not None
        and committed[0] == (player.cell_row, player.cell_col)
        and not committed[1]
    )
    if player.mode_hold_until is None:
        if on_goal and committed[2] == HELD_GOAL_EXPIRY:
            player.mode_hold_until = int(tick) + CONTROL_HOLD_TICKS
            return True
        return False
    if on_goal and tick < player.mode_hold_until:
        return True
    player.mode_hold_until = None
    return False


def _draw(
    player: PlayerState,
    candidates: list[Cell],
    value_of: Callable[[Cell], float],
    all_alive: list,
    movement_ctx: MapContext,
    tick: float,
) -> Cell | None:
    """``draw_goal``, recording on the player whether it produced a goal."""
    pick = draw_goal(player, candidates, value_of, all_alive, movement_ctx, tick)
    player._goal_was_drawn = pick is not None
    return pick


def _low_health_heavy_goal(
    player: PlayerState, all_alive: list, tick: float, time_domain: str
) -> Cell | None:
    """A low Heavy's remembered Medic, then Ammo. Not drawn; ends any hold."""
    for support in ("medic", "ammo"):
        cell = _fresh_teammate_cell(player, support, all_alive, tick, time_domain)
        if cell is not None:
            player.mode_hold_until = None
            return cell
    return None


def _attack_goal(
    player: PlayerState,
    all_alive: list,
    enemy_color: str,
    movement_ctx: MapContext,
    tick: float,
    time_domain: str,
) -> Cell | None:
    """A cell that sees a target, else a high-LOS cell."""
    targets = _attack_targets(
        player,
        all_alive,
        enemy_color,
        tick,
        time_domain,
        chased_tag_ids(player, all_alive, tick),
    )
    values = _attack_candidates(targets, movement_ctx)
    goal = (
        _draw(player, list(values), values.__getitem__, all_alive, movement_ctx, tick)
        if values
        else None
    )
    if goal is not None:
        return goal
    return _draw(
        player,
        _as_cells(movement_ctx.get_high_los_cells()),
        movement_ctx.los_rank,
        all_alive,
        movement_ctx,
        tick,
    )


def _defense_goal(
    player: PlayerState,
    all_alive: list,
    movement_ctx: MapContext,
    tick: float,
    time_domain: str,
) -> Cell | None:
    """A cell that sees the remembered Heavy, or None to fall back on Control.
    Support-survival switch 2: a cell within ``SUPPORT_CELL_REACH`` steps of one."""
    heavy_cell = _fresh_teammate_cell(player, "heavy", all_alive, tick, time_domain)
    if heavy_cell is None:
        return None
    candidates = movement_ctx.cells_visible_from(heavy_cell)
    if support_survival.HIDDEN_SUPPORT_CELLS:
        candidates = _within_reach(
            candidates, support_survival.SUPPORT_CELL_REACH, movement_ctx
        )
    goal = _draw(
        player,
        candidates,
        _defense_value(player, heavy_cell, movement_ctx),
        all_alive,
        movement_ctx,
        tick,
    )
    if goal is not None:
        player.mode_hold_until = None
    return goal


def _within_reach(
    cells: list[Cell], steps: int, movement_ctx: MapContext
) -> list[Cell]:
    """*cells* and every floor cell within *steps* moves of one, sorted: where a
    support can stand and still step into sight of the Heavy."""
    adj = movement_ctx.get_adjacency()
    reached = set(cells)
    frontier = list(cells)
    for _ in range(steps):
        nxt = []
        for cell in frontier:
            for neighbour in adj.get(cell, ()):
                if neighbour not in reached:
                    reached.add(neighbour)
                    nxt.append(neighbour)
        frontier = nxt
    return sorted(reached)


def _control_goal(
    player: PlayerState,
    all_alive: list,
    enemy_color: str,
    movement_ctx: MapContext,
    tick: float,
    time_domain: str,
) -> Cell | None:
    """The cell being held, else a draw over strong spots or high-LOS cells."""
    if _update_control_hold(player, tick):
        return cast(Cell, (player.cell_row, player.cell_col))
    spots = movement_ctx.get_strong_spots() if player.role == "heavy" else []
    return _draw(
        player,
        _as_cells(spots or movement_ctx.get_high_los_cells()),
        _control_value(player, all_alive, enemy_color, movement_ctx, tick, time_domain),
        all_alive,
        movement_ctx,
        tick,
    )


def mode_goal(
    player: PlayerState,
    all_alive: list,
    enemy_color: str,
    movement_ctx: MapContext,
    tick: float,
    time_domain: str = "ticks",
) -> Cell | None:
    """The player's positioning goal under their Mode, or None for the enemy-base
    default. Sets ``_goal_was_drawn``, so a drawn goal is held until reached."""
    player._goal_was_drawn = False
    if player.role == "heavy" and _low_on_resources(player):
        goal = _low_health_heavy_goal(player, all_alive, tick, time_domain)
        if goal is not None:
            return goal
    if player.mode is Mode.ATTACK:
        return _attack_goal(
            player, all_alive, enemy_color, movement_ctx, tick, time_domain
        )
    if player.mode is Mode.DEFENSE:
        goal = _defense_goal(player, all_alive, movement_ctx, tick, time_domain)
        if goal is not None:
            return goal
    # Control, and Defense with no fresh Heavy or no reachable cell that sees it.
    return _control_goal(
        player, all_alive, enemy_color, movement_ctx, tick, time_domain
    )


def apply_mode_weights(player: PlayerState, weights: list) -> None:
    """Nudge the Mode's Actions in place by ``MODE_WEIGHT_MULTIPLIER``. No RNG.

    A zero stays zero. A negative raw slot is left for ``finalize_action_weights``
    to clamp rather than pushed further down.
    """
    for idx in _MODE_WEIGHT_SLOTS[player.mode]:
        if weights[idx] > 0:
            weights[idx] = round(weights[idx] * MODE_WEIGHT_MULTIPLIER)


def trail_costs(
    player: PlayerState, all_alive: list, tick: float, time_domain: str = "ticks"
) -> dict[Cell, float]:
    """The route cost of each cell in the open Trail of an enemy in *all_alive* the
    player freshly remembers as Downed, within one Down's length of that sighting.
    Trails that overlap cost the same as one, so the order of *all_alive* cannot
    change the result. No RNG."""
    costs: dict[Cell, float] = {}
    for other in all_alive:
        if other.team_color == player.team_color:
            continue
        trail = open_trail(other, tick)
        if trail is None:
            continue
        entry = _fresh_entry(player, other.tag_id_key, tick, time_domain)
        if (
            entry is not None
            and _shows_downed(entry)
            and _down_could_be_on(entry, tick)
        ):
            costs.update(dict.fromkeys(trail.band, TRAIL_COST))
    return costs
