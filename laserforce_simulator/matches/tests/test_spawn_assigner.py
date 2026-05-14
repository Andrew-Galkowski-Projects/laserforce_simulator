"""
Unit tests for matches/sim_helpers/spawn_assigner.py.

No Django ORM required — pure-Python module, no DB.
"""

import pytest

from matches.sim_helpers.spawn_assigner import assign_spawn_cells

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SPAWN_CELLS = {
    "red": (0, 0),
    "blue": (10, 10),
}

# Red spawn pool: 5 cells at increasing distance from the blue base.
# Sorted by Manhattan distance to blue base (10,10):
#   (0,0)=20, (0,2)=18, (0,4)=16, (0,6)=14, (0,8)=12
# So sorted_pool (ascending dist to enemy) = [(0,8),(0,6),(0,4),(0,2),(0,0)]
RED_POOL = [(0, 0), (0, 2), (0, 4), (0, 6), (0, 8)]
TEAM_SPAWN_POOLS = {"red": RED_POOL}


# ---------------------------------------------------------------------------
# Happy path: roles map to expected spawn cells
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_commander_gets_front_cell(self):
        """Commander/Heavy draw from the front (closest to enemy)."""
        result = assign_spawn_cells(
            roster_roles=["commander"],
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=TEAM_SPAWN_POOLS,
        )
        # Front of pool sorted by ascending dist to blue base = (0,8)
        assert result[0] == (0, 8)

    def test_medic_gets_back_cell(self):
        """Medic/Ammo draw from the back (farthest from enemy)."""
        result = assign_spawn_cells(
            roster_roles=["medic"],
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=TEAM_SPAWN_POOLS,
        )
        # Back of pool sorted by ascending dist to blue base = (0,0)
        assert result[0] == (0, 0)

    def test_scout_gets_remaining_front_cell(self):
        """Scout draws from the front of whatever is left after priority groups."""
        result = assign_spawn_cells(
            roster_roles=["scout"],
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=TEAM_SPAWN_POOLS,
        )
        # No commander/medic consumed anything; scout takes front = (0,8)
        assert result[0] == (0, 8)

    def test_mixed_roster_role_priority_ordering(self):
        """Priority groups are processed in order: commander/heavy first,
        then medic/ammo, then scouts — regardless of roster order."""
        roster_roles = ["scout", "medic", "commander", "ammo", "scout"]
        result = assign_spawn_cells(
            roster_roles=roster_roles,
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=TEAM_SPAWN_POOLS,
        )
        # Sorted pool (ascending dist to enemy): [(0,8),(0,6),(0,4),(0,2),(0,0)]
        # Priority group 0 (commander at index 2): draws front → (0,8)
        # Priority group 1 (medic at idx 1, ammo at idx 3):
        #   medic draws back → (0,0)
        #   ammo draws back → (0,2)
        # Priority group 2 (scout idx 0, scout idx 4):
        #   scout idx 0 draws front → (0,6)
        #   scout idx 4 draws front → (0,4)
        assert result[2] == (0, 8), "commander should get front cell"
        assert result[1] == (0, 0), "medic should get back cell"
        assert result[3] == (0, 2), "ammo should get second-back cell"
        assert result[0] == (0, 6), "first scout should get next front cell"
        assert result[4] == (0, 4), "second scout should get remaining cell"

    def test_all_cells_assigned_are_unique(self):
        """Every player in a roster smaller than or equal to pool gets a unique cell."""
        roster_roles = ["commander", "heavy", "scout", "medic", "ammo"]
        result = assign_spawn_cells(
            roster_roles=roster_roles,
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=TEAM_SPAWN_POOLS,
        )
        assigned = [v for v in result.values() if v is not None]
        assert len(assigned) == len(set(assigned)), "cells must be unique"

    def test_result_keys_match_roster_indices(self):
        """Return dict must have exactly one key per roster index."""
        roster_roles = ["commander", "scout", "medic"]
        result = assign_spawn_cells(
            roster_roles=roster_roles,
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=TEAM_SPAWN_POOLS,
        )
        assert set(result.keys()) == {0, 1, 2}


# ---------------------------------------------------------------------------
# Pool exhaustion: more players than spawn cells falls back to None
# ---------------------------------------------------------------------------


class TestPoolExhaustion:
    def test_overflow_players_get_base_cell_when_base_not_drawn(self):
        """When pool is exhausted and base cell has not been drawn from the pool,
        overflow players share the base cell as a fallback."""
        # Pool has only 1 cell (not the base cell itself).
        small_pool = [(0, 5)]
        pools = {"red": small_pool}
        # 3 players — pool of 1 means 2 will overflow.
        roster_roles = ["commander", "scout", "scout"]
        result = assign_spawn_cells(
            roster_roles=roster_roles,
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=pools,
        )
        # commander (priority 0) draws (0,5) from front.
        assert result[0] == (0, 5)
        # Two scouts overflow; base cell (0,0) not yet drawn → share base.
        assert result[1] == (0, 0)
        assert result[2] == (0, 0)

    def test_overflow_players_get_none_when_base_already_drawn(self):
        """When the only pool cell IS the base cell and gets drawn, overflow
        players have nowhere to go and receive None (3-zone fallback)."""
        # Pool contains only the base cell.
        pools = {"red": [(0, 0)]}
        roster_roles = ["commander", "scout", "scout"]
        result = assign_spawn_cells(
            roster_roles=roster_roles,
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=pools,
        )
        # commander draws (0,0) from front; base is now in drawn_cells.
        assert result[0] == (0, 0)
        # scouts overflow — base cell already drawn, so None.
        assert result[1] is None
        assert result[2] is None

    def test_partial_overflow_shares_base_cell_when_not_drawn(self):
        """Overflow players get the base cell when it was not drawn from the pool."""
        # Pool has 2 cells that are not the base cell; base is (0,0).
        pools = {"red": [(0, 8), (0, 6)]}  # 2 cells for 3 players
        roster_roles = ["scout", "scout", "scout"]
        result = assign_spawn_cells(
            roster_roles=roster_roles,
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=pools,
        )
        # Two scouts draw (0,8) and (0,6); third scout overflows to base (0,0).
        assigned = [v for v in result.values() if v is not None]
        assert len(assigned) == 3, "all players assigned — overflow shares base cell"
        assert (0, 0) in assigned, "overflow player should share base cell"


# ---------------------------------------------------------------------------
# Empty spawn pools: all players get None
# ---------------------------------------------------------------------------


class TestEmptySpawnPools:
    def test_no_pool_entry_for_team_returns_all_none(self):
        """When there is no pool at all for the team, everyone gets None."""
        result = assign_spawn_cells(
            roster_roles=["commander", "scout", "medic"],
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools={},  # no red pool
        )
        assert all(v is None for v in result.values())
        assert set(result.keys()) == {0, 1, 2}

    def test_empty_pool_list_returns_all_none(self):
        """An explicitly empty pool list is equivalent to no pool."""
        result = assign_spawn_cells(
            roster_roles=["commander", "heavy"],
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools={"red": []},
        )
        assert all(v is None for v in result.values())

    def test_missing_base_cell_returns_all_none(self):
        """If the team's base cell is absent from spawn_cells, all get None."""
        result = assign_spawn_cells(
            roster_roles=["commander", "scout"],
            team_color="red",
            spawn_cells={"blue": (10, 10)},  # no red base
            team_spawn_pools={"red": [(0, 5)]},
        )
        assert all(v is None for v in result.values())

    def test_missing_enemy_base_returns_all_none(self):
        """If the enemy base cell is absent (can't sort pool), all get None."""
        result = assign_spawn_cells(
            roster_roles=["commander", "scout"],
            team_color="red",
            spawn_cells={"red": (0, 0)},  # no blue base
            team_spawn_pools={"red": [(0, 5)]},
        )
        assert all(v is None for v in result.values())

    def test_empty_roster_returns_empty_dict(self):
        """No players → no assignments."""
        result = assign_spawn_cells(
            roster_roles=[],
            team_color="red",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools=TEAM_SPAWN_POOLS,
        )
        assert result == {}


# ---------------------------------------------------------------------------
# Blue team: verify enemy colour flipping works symmetrically
# ---------------------------------------------------------------------------


class TestBlueTeam:
    def test_blue_team_commander_gets_front_cell_closest_to_red_base(self):
        """For blue team the enemy is red; front = closest to (0,0)."""
        blue_pool = [(10, 10), (10, 8), (10, 6)]
        # Distances to red base (0,0): (10,10)=20, (10,8)=18, (10,6)=16
        # Sorted ascending → [(10,6),(10,8),(10,10)]
        result = assign_spawn_cells(
            roster_roles=["commander"],
            team_color="blue",
            spawn_cells=SPAWN_CELLS,
            team_spawn_pools={"blue": blue_pool},
        )
        assert result[0] == (10, 6)
