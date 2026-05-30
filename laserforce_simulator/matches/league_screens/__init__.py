"""LG-01z — per-screen league sidebar view modules.

Each module exposes one read-only ``GET`` view for a league-scoped
sidebar screen that LG-01h shipped as a ``coming_soon`` placeholder.
Routes are wired in ``matches/league_urls.py``; the sidebar entry is
repointed in ``matches/league_views.py::_build_league_sidebar_links``.

The view callables are re-exported here so ``league_urls`` can import
them directly from the package.
"""

from .free_agents import free_agents
from .game_log import game_log
from .league_leaders import league_leaders
from .player_ratings import player_ratings
from .player_stats import player_stats
from .power_rankings import power_rankings
from .statistical_feats import statistical_feats
from .team_history import team_history
from .team_roster import team_roster
from .team_stats import team_stats
from .watch_list import watch_list

__all__ = [
    "free_agents",
    "game_log",
    "league_leaders",
    "player_ratings",
    "player_stats",
    "power_rankings",
    "statistical_feats",
    "team_history",
    "team_roster",
    "team_stats",
    "watch_list",
]
