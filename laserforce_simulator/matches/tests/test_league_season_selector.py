"""LG-06d — View tests for the Season selector (6 screens) + rate toggle.

Builds a League carrying **two Seasons** each with persisted completed
Rounds, then asserts, per the APPROVED LG-06d seam contract:

* §3 scope rules — default (no ``?season=``) scopes to ``displayed_season``;
  ``?season=<id>`` scopes to that Season; ``?season=career`` aggregates
  across **both** of this League's Seasons; an invalid ``?season=foo`` falls
  back to the default.
* §5 context keys — ``season_options`` + ``selected_season`` on all 6;
  ``rate`` + ``rate_options`` on player_stats; ``season`` (and ``rate``)
  carried across pagination / sort / team-filter links with ``page`` reset.
* §6 DOM ids — ``<screen>-season-filter-form`` / ``-season-filter-select``
  on all 6; ``player-stats-rate-form`` / ``-rate-select`` on player_stats.
* player_stats §4 — each ``?rate=`` mode changes the displayed numbers AND
  the resulting sort order; rate form DOM ids present.

Fixtures hand-construct League / Season / Match / GameRound /
PlayerRoundState rows — LG-06d runs NO simulation. The displayed Season is
the **active** one (``league.active_season`` wins the per-screen
``displayed_season`` pick); the other Season is ``completed`` so a
``?season=<completed_id>`` request exercises the non-default scope and
``?season=career`` exercises the cross-Season aggregate.

The views are URL-wired (the LG-01z orchestrator mounts them). These tests
drive them through the Django test ``Client`` against the wired
``reverse``-d URLs so ``response.context`` is populated (the context-key
assertions in §5 need it); the directly-called ``RequestFactory`` pattern
used by the older LG-01z screen tests does not capture context.

Expected to FAIL until the Code agent lands the ``?season=`` selector +
``?rate=`` toggle wiring (TDD — red first).
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.tests.conftest import make_team_with_slots

# ===========================================================================
# Shared fixture helpers
# ===========================================================================


def _make_round(
    season,
    team_red,
    team_blue,
    *,
    red_points,
    blue_points,
    round_number=1,
    states=None,
):
    """Persist a completed Match + GameRound under ``season`` + PRS rows.

    ``states`` is a list of (player, color, stat_kwargs) tuples.
    """
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=season,
        is_completed=True,
    )
    game_round = GameRound.objects.create(
        match=match,
        round_number=round_number,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_points,
        blue_points=blue_points,
        is_completed=True,
    )
    for player, color, kwargs in states or []:
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=color,
            role=kwargs.pop("role", "scout"),
            **kwargs,
        )
    return game_round


class _TwoSeasonLeague:
    """Build a League with two Seasons sharing the same two Teams.

    Season 1 (``s_done``) is completed; Season 2 (``s_active``) is active.
    Each Season has one completed Round with distinct point totals so a
    scoped request renders only that Season's numbers and a ``career``
    request renders both summed.
    """

    def __init__(self, name: str = "LG06DLeague") -> None:
        self.league = League.objects.create(name=name)
        # Two shared Teams (so the same players appear in both Seasons —
        # the career aggregate then sums their per-Season rows).
        self.team_a, self.players_a = make_team_with_slots(f"{name[:3]}A")
        self.team_b, self.players_b = make_team_with_slots(f"{name[:3]}B")

        # --- Season 1: completed ---
        self.s_done = Season.objects.create(
            league=self.league, name="Season 1", start_date=date(2026, 1, 1)
        )
        self.s_done.teams.add(self.team_a, self.team_b)
        self.s_done.start_season()
        self.s_done.refresh_from_db()
        self.gr_done = _make_round(
            self.s_done,
            self.team_a,
            self.team_b,
            red_points=100,
            blue_points=40,
            states=[
                (
                    self.players_a["commander"],
                    "red",
                    {
                        "points_scored": 100,
                        "tags_made": 10,
                        "times_tagged": 4,
                        "was_eliminated_at": 1801,
                    },
                ),
                (
                    self.players_b["commander"],
                    "blue",
                    {
                        "points_scored": 40,
                        "tags_made": 4,
                        "times_tagged": 10,
                        "was_eliminated_at": 900,
                    },
                ),
            ],
        )
        # Force-complete Season 1 so it is no longer the active Season.
        self.s_done.state = "completed"
        self.s_done.save(update_fields=["state"])

        # --- Season 2: active (the default displayed Season) ---
        self.s_active = Season.objects.create(
            league=self.league, name="Season 2", start_date=date(2026, 6, 1)
        )
        self.s_active.teams.add(self.team_a, self.team_b)
        self.s_active.start_season()
        self.s_active.refresh_from_db()
        self.gr_active = _make_round(
            self.s_active,
            self.team_a,
            self.team_b,
            red_points=500,
            blue_points=300,
            states=[
                (
                    self.players_a["commander"],
                    "red",
                    {
                        "points_scored": 500,
                        "tags_made": 30,
                        "times_tagged": 6,
                        "was_eliminated_at": 1801,
                    },
                ),
                (
                    self.players_b["commander"],
                    "blue",
                    {
                        "points_scored": 300,
                        "tags_made": 12,
                        "times_tagged": 20,
                        "was_eliminated_at": 600,
                    },
                ),
            ],
        )


# Per-screen URL name + DOM-id prefix.
_SCREENS = {
    "power_rankings": ("league_power_rankings", "power-rankings"),
    "team_stats": ("stats_team_stats", "team-stats"),
    "league_leaders": ("stats_league_leaders", "league-leaders"),
    "statistical_feats": ("stats_statistical_feats", "statistical-feats"),
    "game_log": ("stats_game_log", "game-log"),
    "player_stats": ("stats_player_stats", "player-stats"),
}


def _option_value(opt):
    """Extract the *value* from a select-option of any reasonable shape.

    The seam contract pins ``season_options`` / ``rate_options`` as
    iterables of options but leaves the per-option shape to the Code agent.
    Tolerate the three plausible shapes: a ``(value, label)`` tuple, a dict
    with a ``"value"`` key, or a namedtuple / object with a ``.value`` attr.
    """
    if isinstance(opt, dict):
        # §5 pins the season-option dict shape as {"id", "name", "year"};
        # tolerate a generic {"value": ...} too.
        if "value" in opt:
            return opt["value"]
        return opt["id"]
    if isinstance(opt, (tuple, list)):
        return opt[0]
    return getattr(opt, "value", opt)


def _has_standalone_page_param(qs: str) -> bool:
    """True iff ``qs`` carries a standalone ``page=`` param.

    Guards against the ``per_page=`` substring false-positive — a naive
    ``"page=" in qs`` matches ``per_page=10`` too.
    """
    from urllib.parse import parse_qs

    return "page" in parse_qs(qs, keep_blank_values=True)


class _ClientMixin:
    """Shared GET driver — resolves a screen's wired URL and hits it via
    the test ``Client`` so ``response.context`` is populated.
    """

    def _get(self, url_name: str, league_id: int, *, query: str = ""):
        url = reverse(url_name, kwargs={"league_id": league_id})
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)


# ===========================================================================
# §5/§6 — selector renders on all 6 screens (options + DOM ids)
# ===========================================================================


class TestSeasonSelectorRendersOnAllScreens(_ClientMixin, TestCase):
    def setUp(self) -> None:
        self.fx = _TwoSeasonLeague()

    def test_season_filter_form_and_select_dom_ids_present(self) -> None:
        for key, (url_name, prefix) in _SCREENS.items():
            response = self._get(url_name, self.fx.league.id)
            content = response.content.decode()
            self.assertIn(
                f"{prefix}-season-filter-form",
                content,
                msg=f"{key} missing season-filter-form",
            )
            self.assertIn(
                f"{prefix}-season-filter-select",
                content,
                msg=f"{key} missing season-filter-select",
            )

    def test_season_options_context_lists_both_seasons_plus_career(self) -> None:
        for key, (url_name, _prefix) in _SCREENS.items():
            response = self._get(url_name, self.fx.league.id)
            options = response.context["season_options"]
            # Both this League's Seasons appear by id in season_options.
            ids = [
                int(_option_value(opt))
                for opt in options
                if _option_value(opt) != "career"
            ]
            self.assertIn(self.fx.s_done.id, ids, msg=key)
            self.assertIn(self.fx.s_active.id, ids, msg=key)
            # Career is a template-appended <option value="career"> (§5 leaves
            # its placement to the Code agent — it lives in the template, not
            # necessarily in the season_options context list).
            self.assertIn('value="career"', response.content.decode(), msg=key)

    def test_career_option_label_rendered(self) -> None:
        for key, (url_name, _prefix) in _SCREENS.items():
            response = self._get(url_name, self.fx.league.id)
            self.assertIn("Career", response.content.decode(), msg=key)

    def test_selected_season_defaults_to_displayed_season(self) -> None:
        # No ?season= → selected_season is the displayed (active) Season's id.
        for key, (url_name, _prefix) in _SCREENS.items():
            response = self._get(url_name, self.fx.league.id)
            self.assertEqual(
                response.context["selected_season"],
                self.fx.s_active.id,
                msg=key,
            )

    def test_selected_season_reflects_query_id(self) -> None:
        for key, (url_name, _prefix) in _SCREENS.items():
            response = self._get(
                url_name, self.fx.league.id, query=f"season={self.fx.s_done.id}"
            )
            self.assertEqual(
                response.context["selected_season"], self.fx.s_done.id, msg=key
            )

    def test_selected_season_career(self) -> None:
        for key, (url_name, _prefix) in _SCREENS.items():
            response = self._get(url_name, self.fx.league.id, query="season=career")
            self.assertEqual(response.context["selected_season"], "career", msg=key)

    def test_invalid_season_falls_back_to_default(self) -> None:
        for key, (url_name, _prefix) in _SCREENS.items():
            response = self._get(url_name, self.fx.league.id, query="season=foo")
            self.assertEqual(response.status_code, 200, msg=key)
            self.assertEqual(
                response.context["selected_season"],
                self.fx.s_active.id,
                msg=key,
            )

    def test_non_enrolled_season_id_falls_back_to_default(self) -> None:
        other_league = League.objects.create(name="Other")
        other_season = Season.objects.create(
            league=other_league, name="X", start_date=date(2026, 1, 1)
        )
        for key, (url_name, _prefix) in _SCREENS.items():
            response = self._get(
                url_name, self.fx.league.id, query=f"season={other_season.id}"
            )
            self.assertEqual(
                response.context["selected_season"],
                self.fx.s_active.id,
                msg=key,
            )


# ===========================================================================
# §3 — Player Stats scope (data-bearing screen; assert on rendered numbers)
# ===========================================================================


class TestPlayerStatsSeasonScope(_ClientMixin, TestCase):
    def setUp(self) -> None:
        self.fx = _TwoSeasonLeague()
        self.cmd_a = self.fx.players_a["commander"]

    def _content(self, query: str = "") -> str:
        response = self._get("stats_player_stats", self.fx.league.id, query=query)
        return response.content.decode()

    def test_default_scopes_to_active_season(self) -> None:
        # Active Season: cmd_a scored 500. Completed Season: 100.
        content = self._content()
        self.assertIn("500", content)
        # The completed-Season-only total should not be the displayed figure.
        # (Career would show 600; active-only shows 500.)
        self.assertNotIn("600", content)

    def test_season_id_scopes_to_that_season(self) -> None:
        content = self._content(query=f"season={self.fx.s_done.id}")
        # Completed Season: cmd_a scored 100, tags 10.
        self.assertIn("100", content)
        self.assertNotIn("600", content)

    def test_career_aggregates_across_both_seasons(self) -> None:
        content = self._content(query="season=career")
        # Career: cmd_a points 100 + 500 = 600, tags 10 + 30 = 40.
        self.assertIn("600", content)

    def test_career_games_is_two_for_shared_player(self) -> None:
        # cmd_a played one Round in each Season → career games == 2.
        response = self._get(
            "stats_player_stats", self.fx.league.id, query="season=career"
        )
        rows = list(response.context["page_obj"].object_list)
        cmd_rows = [r for r in rows if r.player_id == self.cmd_a.id]
        self.assertEqual(len(cmd_rows), 1)
        self.assertEqual(cmd_rows[0].games, 2)

    def test_scoped_games_is_one(self) -> None:
        response = self._get(
            "stats_player_stats",
            self.fx.league.id,
            query=f"season={self.fx.s_done.id}",
        )
        rows = list(response.context["page_obj"].object_list)
        cmd_rows = [r for r in rows if r.player_id == self.cmd_a.id]
        self.assertEqual(len(cmd_rows), 1)
        self.assertEqual(cmd_rows[0].games, 1)


# ===========================================================================
# §3 — Game Log scope (row count differs per Season / career)
# ===========================================================================


class TestGameLogSeasonScope(_ClientMixin, TestCase):
    def setUp(self) -> None:
        self.fx = _TwoSeasonLeague()

    def _rows(self, query: str = ""):
        response = self._get("stats_game_log", self.fx.league.id, query=query)
        return response.context["rows"]

    def test_default_shows_only_active_season_round(self) -> None:
        rows = self._rows()
        ids = {
            (
                r["round_id"]
                if isinstance(r, dict) and "round_id" in r
                else getattr(r, "round_id", None)
            )
            for r in rows
        }
        # The active Season's Round is present; the completed one is not.
        self.assertEqual(len(rows), 1)
        self.assertIn(self.fx.gr_active.id, ids)
        self.assertNotIn(self.fx.gr_done.id, ids)

    def test_season_id_scopes_to_completed_round(self) -> None:
        response = self._get(
            "stats_game_log", self.fx.league.id, query=f"season={self.fx.s_done.id}"
        )
        content = response.content.decode()
        self.assertIn(f"/matches/game-round/{self.fx.gr_done.id}/", content)
        self.assertNotIn(f"/matches/game-round/{self.fx.gr_active.id}/", content)

    def test_career_shows_rounds_from_both_seasons(self) -> None:
        rows = self._rows(query="season=career")
        self.assertEqual(len(rows), 2)


# ===========================================================================
# §3 — Team Stats / Power Rankings / League Leaders / Feats scope smoke
# ===========================================================================


class TestOtherScreensSeasonScope(_ClientMixin, TestCase):
    def setUp(self) -> None:
        self.fx = _TwoSeasonLeague()

    def test_team_stats_career_avg_points_differs_from_scoped(self) -> None:
        # Active-only: team_a avg points_for = 500. Career (2 rounds):
        # (100 + 500) / 2 = 300.
        active_resp = self._get("stats_team_stats", self.fx.league.id)
        career_resp = self._get(
            "stats_team_stats", self.fx.league.id, query="season=career"
        )
        active_content = active_resp.content.decode()
        career_content = career_resp.content.decode()
        a_start = active_content.index(f"team-stats-row-{self.fx.team_a.id}")
        a_chunk = active_content[a_start : a_start + 800]
        c_start = career_content.index(f"team-stats-row-{self.fx.team_a.id}")
        c_chunk = career_content[c_start : c_start + 800]
        self.assertIn("500.0", a_chunk)
        self.assertIn("300.0", c_chunk)

    def test_power_rankings_renders_under_each_scope(self) -> None:
        for q in ("", f"season={self.fx.s_done.id}", "season=career"):
            response = self._get("league_power_rankings", self.fx.league.id, query=q)
            self.assertEqual(response.status_code, 200, msg=q)

    def test_league_leaders_renders_under_each_scope(self) -> None:
        for q in ("", f"season={self.fx.s_done.id}", "season=career"):
            response = self._get("stats_league_leaders", self.fx.league.id, query=q)
            self.assertEqual(response.status_code, 200, msg=q)

    def test_statistical_feats_renders_under_each_scope(self) -> None:
        for q in ("", f"season={self.fx.s_done.id}", "season=career"):
            response = self._get("stats_statistical_feats", self.fx.league.id, query=q)
            self.assertEqual(response.status_code, 200, msg=q)


# ===========================================================================
# §5 — season carried in pagination / sort / team-filter links (page reset)
# ===========================================================================


class TestSeasonCarriedInLinks(_ClientMixin, TestCase):
    def setUp(self) -> None:
        self.fx = _TwoSeasonLeague()

    def test_player_stats_season_in_querystring_without_page(self) -> None:
        response = self._get(
            "stats_player_stats",
            self.fx.league.id,
            query=f"season={self.fx.s_done.id}&per_page=25",
        )
        qs = response.context["querystring_without_page"]
        self.assertIn(f"season={self.fx.s_done.id}", qs)
        self.assertFalse(_has_standalone_page_param(qs))

    def test_player_stats_career_in_querystring_without_page(self) -> None:
        response = self._get(
            "stats_player_stats",
            self.fx.league.id,
            query="season=career&per_page=25",
        )
        qs = response.context["querystring_without_page"]
        self.assertIn("season=career", qs)

    def test_player_stats_season_in_querystring_without_sort_dir_page(self) -> None:
        response = self._get(
            "stats_player_stats",
            self.fx.league.id,
            query=f"season={self.fx.s_done.id}&sort=points_scored",
        )
        qs = response.context["querystring_without_sort_dir_page"]
        self.assertIn(f"season={self.fx.s_done.id}", qs)
        self.assertFalse(_has_standalone_page_param(qs))

    def test_game_log_season_in_sort_querystring(self) -> None:
        response = self._get(
            "stats_game_log", self.fx.league.id, query=f"season={self.fx.s_done.id}"
        )
        qs = response.context["querystring_without_sort_dir"]
        self.assertIn(f"season={self.fx.s_done.id}", qs)


# ===========================================================================
# §4/§5/§6 — Player Stats rate toggle
# ===========================================================================


class TestPlayerStatsRateToggle(_ClientMixin, TestCase):
    def setUp(self) -> None:
        self.fx = _TwoSeasonLeague()
        self.cmd_a = self.fx.players_a["commander"]

    def test_rate_form_and_select_dom_ids_present(self) -> None:
        response = self._get("stats_player_stats", self.fx.league.id)
        content = response.content.decode()
        self.assertIn("player-stats-rate-form", content)
        self.assertIn("player-stats-rate-select", content)

    def test_rate_context_keys_present(self) -> None:
        response = self._get("stats_player_stats", self.fx.league.id)
        self.assertIn("rate", response.context)
        self.assertIn("rate_options", response.context)

    def test_rate_defaults_to_total(self) -> None:
        response = self._get("stats_player_stats", self.fx.league.id)
        self.assertEqual(response.context["rate"], "total")

    def test_rate_options_cover_three_modes(self) -> None:
        response = self._get("stats_player_stats", self.fx.league.id)
        values = [_option_value(opt) for opt in response.context["rate_options"]]
        self.assertIn("total", values)
        self.assertIn("per_game", values)
        self.assertIn("per_10", values)

    def test_invalid_rate_falls_back_to_total(self) -> None:
        response = self._get(
            "stats_player_stats", self.fx.league.id, query="rate=bogus"
        )
        self.assertEqual(response.context["rate"], "total")
        self.assertEqual(response.status_code, 200)

    def _cmd_a_points(self, query: str):
        response = self._get("stats_player_stats", self.fx.league.id, query=query)
        rows = list(response.context["page_obj"].object_list)
        cmd_rows = [r for r in rows if r.player_id == self.cmd_a.id]
        self.assertEqual(len(cmd_rows), 1)
        return cmd_rows[0].stats["points_scored"]

    def test_per_game_changes_displayed_points(self) -> None:
        # Career: cmd_a total points 600 over 2 games → per_game = 300.
        total = self._cmd_a_points("season=career&rate=total")
        per_game = self._cmd_a_points("season=career&rate=per_game")
        self.assertAlmostEqual(total, 600)
        self.assertAlmostEqual(per_game, 300.0)

    def test_per_10_changes_displayed_points(self) -> None:
        # cmd_a survived both Rounds (was_eliminated_at=1801 → capped 1800,
        # ÷2 = 900s survival per Round, mean 900). Career points 600, games 2.
        # per_10 = 600 * 600 / (900 * 2) = 200.
        per_10 = self._cmd_a_points("season=career&rate=per_10")
        self.assertAlmostEqual(per_10, 200.0)

    def test_rate_runs_on_rate_adjusted_values_for_sort(self) -> None:
        # §4 — sort runs on rate-adjusted values. cmd_a (active Season,
        # high uptime) vs cmd_b (low uptime). Under per_10 the player with
        # less uptime gets a larger per-10 figure, which can flip the order
        # relative to totals. Assert the per_10 ordering is by per-10 value.
        response = self._get(
            "stats_player_stats",
            self.fx.league.id,
            query="season=career&rate=per_10&sort=points_scored&dir=desc",
        )
        rows = list(response.context["page_obj"].object_list)
        values = [r.stats["points_scored"] for r in rows]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_rate_carried_in_querystring_without_page(self) -> None:
        response = self._get(
            "stats_player_stats",
            self.fx.league.id,
            query="rate=per_game&per_page=25",
        )
        qs = response.context["querystring_without_page"]
        self.assertIn("rate=per_game", qs)
