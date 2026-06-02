"""LG-06f — tests for the reshaped Watch List screen + the pure
``zero_fill_watched`` helper.

The Watch List screen (``matches.league_screens.watch_list.watch_list``) was
reshaped from the LG-01z 3-column bookmark table into the **Player-Stats
column set** filtered to watched players, with a **zero-fill** row for each
watched player with no Round in scope. The per-League watch lists live in
``request.session["watch_lists"]`` (``{str(league_id): [ids]}``); the per-row
flag (``watch-flag`` button) replaces the old Remove control. ``?action=clear``
is retained (now per-League) and redirects.

Two surfaces (seam contract §10 ``test_league_watch_list.py`` boundary):

* **Screen** — 200 / 405 / 404 / empty-states / zero-fill row / the kit
  (sort headers, per-page, season, rate) / Remove All clear+redirect / the
  flag replacing Remove / flag-present smoke on sibling screens + the script
  partial once.
* **Pure-unit ``zero_fill_watched``** (LOCKED to live in THIS file) — filters
  to watched ids, appends zero rows for missing-but-watched ids in
  ascending-id order, each zero row carrying every ``STAT_KEYS + DERIVED_KEYS``
  key at ``0.0`` with ``games == 0``, a watched id absent from
  ``identity_by_id`` silently skipped, deterministic aggregated-first /
  zero-second order.

Fixtures are hand-constructed League / Season / Team / PlayerRoundState rows
— LG-06f runs NO simulation. Tests are written test-first against the LG-06f
seam contract; the screen tests FAIL until the Code agent lands the rewrite.
"""

from __future__ import annotations

import re
from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase

from matches.league_screens.watch_list import watch_list
from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.season_player_stats import (
    DERIVED_KEYS,
    STAT_KEYS,
    PlayerStatRow,
    zero_fill_watched,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Player

WATCH_LIST_PATH = "/leagues/{lid}/players/watch-list/"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _attach_session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = "", watched=None):
    path = WATCH_LIST_PATH.format(lid=league_id)
    if query:
        path = f"{path}?{query}"
    request = _attach_session(RequestFactory().get(path))
    if watched is not None:
        request.session["watch_lists"] = {str(league_id): list(watched)}
        request.session.save()
    return request


def _make_league(name: str = "WatchLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}{name}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _make_round(season, team_red, team_blue, *, red_points=10, blue_points=5):
    match, _ = Match.objects.get_or_create(
        team_red=team_red, team_blue=team_blue, season=season
    )
    return GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=1,
        red_points=red_points,
        blue_points=blue_points,
        is_completed=True,
    )


def _make_prs(game_round, player, team_color, role, **stats):
    defaults = dict(
        game_round=game_round, player=player, team_color=team_color, role=role
    )
    defaults.update(stats)
    return PlayerRoundState.objects.create(**defaults)


def _wl_row_player_ids(content: str) -> list[int]:
    """Player ids in render order from the per-row career-stats links."""
    return [int(m) for m in re.findall(r"/players/(\d+)/stats/", content)]


# ===========================================================================
# Screen — routing / method / 404 / session
# ===========================================================================


class TestWatchListRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = watch_list(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(WATCH_LIST_PATH.format(lid=league.id))
        )
        response = watch_list(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            watch_list(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        watch_list(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)

    def test_sidebar_active_is_watch_list(self) -> None:
        league = _make_league()
        _make_active_season(league)
        content = watch_list(_get(league.id), league.id).content.decode()
        self.assertIn("sidebar-players-watch_list", content)
        self.assertIn("league-sidebar", content)


# ===========================================================================
# Screen — empty states
# ===========================================================================


class TestWatchListEmptyStates(TestCase):
    def test_no_season_renders_empty_notice_with_no_season(self) -> None:
        league = _make_league()
        response = watch_list(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("watch-list-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        content = watch_list(_get(league.id), league.id).content.decode()
        self.assertIn("league-sidebar", content)


# ===========================================================================
# Screen — zero-fill row for a watched player with no Rounds in scope
# ===========================================================================


class TestWatchListZeroFill(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        # A watched player who NEVER played a Round in scope ⇒ zero row.
        self.player = self.team_a.active_players[0]

    def test_watched_player_no_rounds_renders_zero_row(self) -> None:
        content = watch_list(
            _get(self.league.id, watched=[self.player.id]), self.league.id
        ).content.decode()
        self.assertIn("watch-list-table", content)
        # The watched player's career link is present (a row was rendered)...
        self.assertIn(f"/players/{self.player.id}/stats/", content)
        # ...and the player's name renders in that row.
        self.assertIn(self.player.name, content)

    def test_zero_row_player_present_in_render_order(self) -> None:
        content = watch_list(
            _get(self.league.id, watched=[self.player.id]), self.league.id
        ).content.decode()
        self.assertIn(self.player.id, _wl_row_player_ids(content))

    def test_unwatched_player_with_rounds_not_in_table(self) -> None:
        # A player with Rounds but NOT watched must not appear.
        gr = _make_round(self.season, self.team_a, self.team_b)
        other = self.team_b.active_players[0]
        _make_prs(gr, other, "blue", "scout", tags_made=5)
        content = watch_list(
            _get(self.league.id, watched=[self.player.id]), self.league.id
        ).content.decode()
        self.assertNotIn(f"/players/{other.id}/stats/", content)


# ===========================================================================
# Screen — the kit (sort / per-page / season / rate DOM ids)
# ===========================================================================


class TestWatchListKit(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        gr = _make_round(self.season, self.team_a, self.team_b)
        self.player = self.team_a.active_players[0]
        _make_prs(gr, self.player, "red", "scout", tags_made=7, points_scored=120)

    def _content(self, *, query: str = ""):
        return watch_list(
            _get(self.league.id, query=query, watched=[self.player.id]),
            self.league.id,
        ).content.decode()

    def test_per_page_form_and_select_present(self) -> None:
        content = self._content()
        self.assertIn("watch-list-per-page-form", content)
        self.assertIn("watch-list-per-page-select", content)

    def test_season_filter_form_and_select_present(self) -> None:
        content = self._content()
        self.assertIn("watch-list-season-filter-form", content)
        self.assertIn("watch-list-season-filter-select", content)

    def test_rate_form_and_select_present(self) -> None:
        content = self._content()
        self.assertIn("watch-list-rate-form", content)
        self.assertIn("watch-list-rate-select", content)

    def test_sort_header_dom_ids_present(self) -> None:
        content = self._content()
        for key in ("points_scored", "tags_made", "mvp"):
            self.assertIn(f"watch-list-th-{key}", content)

    def test_pagination_dom_id_present_when_multiple_pages(self) -> None:
        # Watch 12 players that each have a Round so >1 page at per_page=10.
        ids = []
        for i, p in enumerate(self.team_a.active_players + self.team_b.active_players):
            gr = _make_round(self.season, self.team_a, self.team_b)
            _make_prs(gr, p, "red", "scout", tags_made=i + 1)
            ids.append(p.id)
        # Pad to >10 rows by adding more watched zero-fill players.
        extra = [
            Player.objects.create(team=self.team_a, name=f"Extra {i}").id
            for i in range(12)
        ]
        request = _get(self.league.id, query="per_page=10")
        request.session["watch_lists"] = {str(self.league.id): ids + extra}
        request.session.save()
        content = watch_list(request, self.league.id).content.decode()
        self.assertIn("watch-list-pagination", content)

    def test_no_team_filter_on_watch_list(self) -> None:
        # The Watch List is a personal cross-team set — no team filter control.
        content = self._content()
        self.assertNotIn("watch-list-team-filter", content)


# ===========================================================================
# Screen — Remove All (?action=clear) clears this League's list + redirects
# ===========================================================================


class TestWatchListClear(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.other_league = _make_league("OtherWatchLeague")
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.player = teams[0].active_players[0]

    def test_clear_empties_this_league_and_redirects(self) -> None:
        request = _get(self.league.id, query="action=clear")
        request.session["watch_lists"] = {str(self.league.id): [self.player.id]}
        request.session.save()
        response = watch_list(request, self.league.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], WATCH_LIST_PATH.format(lid=self.league.id)
        )
        self.assertEqual(
            request.session.get("watch_lists", {}).get(str(self.league.id), []),
            [],
        )

    def test_clear_leaves_other_league_intact(self) -> None:
        request = _get(self.league.id, query="action=clear")
        request.session["watch_lists"] = {
            str(self.league.id): [self.player.id],
            str(self.other_league.id): [self.player.id],
        }
        request.session.save()
        watch_list(request, self.league.id)
        self.assertEqual(
            request.session["watch_lists"].get(str(self.other_league.id)),
            [self.player.id],
        )

    def test_clear_on_bad_league_404s(self) -> None:
        with self.assertRaises(Http404):
            watch_list(_get(999999, query="action=clear"), 999999)


# ===========================================================================
# Screen — the flag replaces the old Remove control
# ===========================================================================


class TestWatchListFlagReplacesRemove(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        gr = _make_round(self.season, self.team_a, self.team_b)
        self.player = self.team_a.active_players[0]
        _make_prs(gr, self.player, "red", "scout", tags_made=4, points_scored=60)

    def _content(self):
        return watch_list(
            _get(self.league.id, watched=[self.player.id]), self.league.id
        ).content.decode()

    def test_old_add_and_row_ids_absent(self) -> None:
        content = self._content()
        self.assertNotIn("watch-list-add", content)
        self.assertNotIn(f"watch-list-row-{self.player.id}", content)

    def test_flag_present_in_name_cell(self) -> None:
        content = self._content()
        self.assertIn("watch-flag", content)
        self.assertIn(f'data-player-id="{self.player.id}"', content)


# ===========================================================================
# Screen — flag-present smoke on sibling screens + script partial once
# ===========================================================================


class TestWatchFlagSmokeOnSiblingScreens(TestCase):
    def setUp(self) -> None:
        from teams.models import Team

        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        gr = _make_round(self.season, self.team_a, self.team_b)
        self.player = self.team_a.active_players[0]
        _make_prs(gr, self.player, "red", "scout", tags_made=5, points_scored=100)
        # Pin team_roster onto a fully-rostered enrolled team.
        self.league.current_team = self.team_a
        # Give the League a free-agent pool with one free agent so the Free
        # Agents screen renders at least one row (and thus a flag).
        pool = Team.objects.create(name=f"{self.league.name} Free Agents")
        self.free_agent = Player.objects.create(team=pool, name="Solo Agent")
        self.league.free_agent_pool = pool
        self.league.save()

    def _render(self, view_fn):
        request = _get(self.league.id)
        return view_fn(request, self.league.id).content.decode()

    def test_player_stats_renders_flag_and_script_once(self) -> None:
        from matches.league_screens.player_stats import player_stats

        content = self._render(player_stats)
        self.assertIn("watch-flag", content)
        # The once-per-page script partial is included exactly once. The
        # delegated-click ``addEventListener("click"`` handler it binds is a
        # per-include marker — exactly one binding per page (the script body
        # itself is Code-agent discretion, but the single delegated handler
        # is the contract-pinned "included exactly once" surface).
        self.assertEqual(content.count('addEventListener("click"'), 1)

    def test_free_agents_renders_flag(self) -> None:
        from matches.league_screens.free_agents import free_agents

        content = self._render(free_agents)
        self.assertIn("watch-flag", content)
        # The free agent's flag button is present.
        self.assertIn(f'data-player-id="{self.free_agent.id}"', content)

    def test_team_roster_renders_flag(self) -> None:
        from matches.league_screens.team_roster import team_roster

        content = self._render(team_roster)
        self.assertIn("watch-flag", content)


# ===========================================================================
# Pure-unit — zero_fill_watched (LOCKED to live in THIS file)
# ===========================================================================

_ALL_KEYS = tuple(STAT_KEYS) + tuple(DERIVED_KEYS)


def _row(pid: int, **stat_overrides) -> PlayerStatRow:
    """An aggregated PlayerStatRow with the full stat key set populated."""
    stats = {k: 1.0 for k in _ALL_KEYS}
    stats.update(stat_overrides)
    return PlayerStatRow(
        player_id=pid,
        player_name=f"Player {pid}",
        team_id=pid * 10,
        team_name=f"Team {pid}",
        role="scout",
        games=3,
        stats=stats,
    )


def _identity(pid: int) -> dict:
    return {
        "player_name": f"Player {pid}",
        "team_id": pid * 10,
        "team_name": f"Team {pid}",
        "role": "scout",
    }


class TestZeroFillWatched(SimpleTestCase):
    def test_filters_rows_to_watched_ids(self) -> None:
        rows = [_row(1), _row(2), _row(3)]
        out = zero_fill_watched(rows, {1, 3}, {})
        self.assertEqual({r.player_id for r in out}, {1, 3})

    def test_unwatched_aggregated_row_dropped(self) -> None:
        rows = [_row(1), _row(2)]
        out = zero_fill_watched(rows, {1}, {})
        self.assertEqual([r.player_id for r in out], [1])

    def test_missing_but_watched_id_gets_zero_row(self) -> None:
        rows = [_row(1)]
        out = zero_fill_watched(rows, {1, 5}, {5: _identity(5)})
        ids = [r.player_id for r in out]
        self.assertIn(5, ids)
        zero_row = next(r for r in out if r.player_id == 5)
        self.assertEqual(zero_row.games, 0)

    def test_zero_row_carries_every_key_at_zero(self) -> None:
        out = zero_fill_watched([], {7}, {7: _identity(7)})
        zero_row = out[0]
        for key in _ALL_KEYS:
            self.assertIn(key, zero_row.stats)
            self.assertEqual(zero_row.stats[key], 0.0)

    def test_zero_row_identity_from_identity_by_id(self) -> None:
        out = zero_fill_watched([], {9}, {9: _identity(9)})
        zero_row = out[0]
        self.assertEqual(zero_row.player_id, 9)
        self.assertEqual(zero_row.player_name, "Player 9")
        self.assertEqual(zero_row.team_id, 90)
        self.assertEqual(zero_row.team_name, "Team 9")
        self.assertEqual(zero_row.role, "scout")

    def test_missing_ids_emitted_in_ascending_order(self) -> None:
        out = zero_fill_watched(
            [], {30, 10, 20}, {10: _identity(10), 20: _identity(20), 30: _identity(30)}
        )
        self.assertEqual([r.player_id for r in out], [10, 20, 30])

    def test_watched_id_absent_from_identity_silently_skipped(self) -> None:
        # 5 is watched and missing from aggregated rows but absent from
        # identity_by_id ⇒ no zero row, no crash.
        out = zero_fill_watched([_row(1)], {1, 5}, {})
        self.assertEqual([r.player_id for r in out], [1])

    def test_aggregated_first_then_zero_rows(self) -> None:
        # Aggregated rows keep incoming order; zero rows follow in ascending id.
        rows = [_row(3), _row(1)]
        out = zero_fill_watched(rows, {1, 3, 2, 4}, {2: _identity(2), 4: _identity(4)})
        ids = [r.player_id for r in out]
        # Aggregated 3, 1 (incoming order) first, then zero 2, 4 ascending.
        self.assertEqual(ids, [3, 1, 2, 4])

    def test_empty_inputs_return_empty_list(self) -> None:
        self.assertEqual(zero_fill_watched([], set(), {}), [])

    def test_returns_player_stat_rows(self) -> None:
        out = zero_fill_watched([_row(1)], {1, 2}, {2: _identity(2)})
        for r in out:
            self.assertIsInstance(r, PlayerStatRow)

    def test_deterministic_repeated_calls(self) -> None:
        rows = [_row(2), _row(1)]
        ident = {3: _identity(3), 4: _identity(4)}
        first = zero_fill_watched(list(rows), {1, 2, 3, 4}, ident)
        second = zero_fill_watched(list(rows), {1, 2, 3, 4}, ident)
        self.assertEqual([r.player_id for r in first], [r.player_id for r in second])
