"""LG-01a — League URL patterns mounted at ``/leagues/`` by the project
URLconf. No ``app_name`` so reverse uses the bare name ``league_list``.

LG-01h extends with 15 placeholder routes (3 League-scoped, 3
Team-scoped, 6 Players-scoped, 6 Stats-scoped) routed through the
shared :func:`matches.views.coming_soon` view.
"""

from django.urls import path

from . import league_screens, league_views, views

urlpatterns = [
    path("create/", league_views.league_create, name="league_create"),
    path("<int:league_id>/", league_views.league_dashboard, name="league_dashboard"),
    path(
        "<int:league_id>/next-season/",
        league_views.next_season,
        name="next_season",
    ),
    path(
        "<int:league_id>/history/",
        league_views.league_history,
        name="league_history",
    ),
    path(
        "<int:league_id>/team_schedule/<int:team_id>/",
        league_views.team_schedule,
        name="team_schedule",
    ),
    # ---- LG-01z live screens (real read-only pages) ----
    # League-scoped
    path(
        "<int:league_id>/power-rankings/",
        league_screens.power_rankings,
        name="league_power_rankings",
    ),
    # Team-scoped — the Team is resolved internally via ``?team_id=``
    # (default ``league.current_team`` / the LG-01g resolver chain).
    path(
        "<int:league_id>/team/roster/",
        league_screens.team_roster,
        name="team_roster",
    ),
    path(
        "<int:league_id>/team/history/",
        league_screens.team_history,
        name="team_history",
    ),
    # Players-scoped
    path(
        "<int:league_id>/players/free-agents/",
        league_screens.free_agents,
        name="players_free_agents",
    ),
    path(
        "<int:league_id>/players/watch-list/",
        league_screens.watch_list,
        name="players_watch_list",
    ),
    # Stats-scoped
    path(
        "<int:league_id>/stats/game-log/",
        league_screens.game_log,
        name="stats_game_log",
    ),
    path(
        "<int:league_id>/stats/league-leaders/",
        league_screens.league_leaders,
        name="stats_league_leaders",
    ),
    path(
        "<int:league_id>/stats/player-ratings/",
        league_screens.player_ratings,
        name="stats_player_ratings",
    ),
    path(
        "<int:league_id>/stats/player-stats/",
        league_screens.player_stats,
        name="stats_player_stats",
    ),
    path(
        "<int:league_id>/stats/team-stats/",
        league_screens.team_stats,
        name="stats_team_stats",
    ),
    path(
        "<int:league_id>/stats/statistical-feats/",
        league_screens.statistical_feats,
        name="stats_statistical_feats",
    ),
    # ---- Remaining LG-01h placeholder routes (still blocked) ----
    # league_playoffs is LG-02; the other 6 await a roster-economy /
    # potential / awards model and render an explainer via coming_soon.
    path(
        "<int:league_id>/playoffs/",
        views.coming_soon,
        {"feature_key": "league_playoffs"},
        name="coming_soon_playoffs",
    ),
    path(
        "<int:league_id>/finances/",
        views.coming_soon,
        {"feature_key": "league_finances"},
        name="coming_soon_finances",
    ),
    path(
        "<int:league_id>/team/finances/",
        views.coming_soon,
        {"feature_key": "team_finances"},
        name="coming_soon_team_finances",
    ),
    path(
        "<int:league_id>/players/trade/",
        views.coming_soon,
        {"feature_key": "players_trade"},
        name="coming_soon_trade",
    ),
    path(
        "<int:league_id>/players/trading-block/",
        views.coming_soon,
        {"feature_key": "players_trading_block"},
        name="coming_soon_trading_block",
    ),
    path(
        "<int:league_id>/players/prospects/",
        views.coming_soon,
        {"feature_key": "players_prospects"},
        name="coming_soon_prospects",
    ),
    path(
        "<int:league_id>/players/hall-of-fame/",
        views.coming_soon,
        {"feature_key": "players_hall_of_fame"},
        name="coming_soon_hall_of_fame",
    ),
    path("", league_views.league_list, name="league_list"),
]
