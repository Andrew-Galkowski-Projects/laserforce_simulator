"""LG-01z-j — Django ``TestCase`` tests for the Watch List league screen.

The view ``matches.league_screens.watch_list.watch_list(request, league_id)``
is session-scoped (no model, no migration). It follows the shared LG-01z
view contract EXCEPT for the documented GET-toggle exception: a plain GET
renders the list, while ``?action=add|remove&player_id=<id>`` mutates
``request.session["watch_list"]`` and redirects back to the bare
watch-list URL.

The view is NOT yet URL-wired (the orchestrator wires the
``players_watch_list`` route centrally), so these tests call the view
directly via ``RequestFactory`` with a real session attached. Fixtures are
hand-constructed League / Season / Team rows — LG-01z runs NO simulation.
"""

from __future__ import annotations

from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_screens.watch_list import watch_list
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Player

WATCH_LIST_PATH = "/leagues/{lid}/players/watch-list/"


def _attach_session(request):
    """Run SessionMiddleware so the view's session reads/writes succeed."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = WATCH_LIST_PATH.format(lid=league_id)
    if query:
        path = f"{path}?{query}"
    request = RequestFactory().get(path)
    return _attach_session(request)


def _make_league(name: str = "WatchLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


# ---------------------------------------------------------------------------
# Routing / method / 404 / session
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Empty states — no Season + empty watch list
# ---------------------------------------------------------------------------


class TestWatchListEmptyStates(TestCase):
    def test_no_season_renders_empty_notice_with_no_season_substring(self) -> None:
        league = _make_league()
        response = watch_list(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("watch-list-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = watch_list(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())

    def test_empty_watch_list_renders_empty_notice(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = watch_list(_get(league.id), league.id)
        content = response.content.decode()
        self.assertIn("watch-list-empty-notice", content)
        # The add control is still present so the user can add a player.
        self.assertIn("watch-list-add", content)


# ---------------------------------------------------------------------------
# Body — DOM ids, sidebar_active, watched players, add control
# ---------------------------------------------------------------------------


class TestWatchListBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.player = self.team_a.active_players[0]

    def _get_with_watched(self, *player_ids: int):
        request = _get(self.league.id)
        request.session["watch_list"] = list(player_ids)
        request.session.save()
        return request

    def test_sidebar_active_is_watch_list(self) -> None:
        response = watch_list(_get(self.league.id), self.league.id)
        self.assertIn("sidebar-players-watch_list", response.content.decode())

    def test_add_control_dom_id_present(self) -> None:
        response = watch_list(_get(self.league.id), self.league.id)
        self.assertIn("watch-list-add", response.content.decode())

    def test_watched_player_renders_row_and_career_link(self) -> None:
        request = self._get_with_watched(self.player.id)
        response = watch_list(request, self.league.id)
        content = response.content.decode()
        self.assertIn("watch-list-table", content)
        self.assertIn(f"watch-list-row-{self.player.id}", content)
        self.assertIn(self.player.name, content)
        self.assertIn(f"/players/{self.player.id}/stats/", content)

    def test_watched_row_has_remove_control(self) -> None:
        request = self._get_with_watched(self.player.id)
        response = watch_list(request, self.league.id)
        content = response.content.decode()
        self.assertIn(f"action=remove&player_id={self.player.id}", content)

    def test_add_control_offers_unwatched_players(self) -> None:
        # With nothing watched, an addable option for a real player exists.
        # The add control is a GET form whose hidden field carries action=add.
        response = watch_list(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn('name="action" value="add"', content)
        self.assertIn(f'value="{self.player.id}"', content)

    def test_watched_player_not_offered_in_add_control(self) -> None:
        request = self._get_with_watched(self.player.id)
        response = watch_list(request, self.league.id)
        content = response.content.decode()
        # The watched player's option must not be selectable to add again.
        self.assertNotIn(f'<option value="{self.player.id}">', content)

    def test_remove_all_control_present_when_watching(self) -> None:
        request = self._get_with_watched(self.player.id)
        response = watch_list(request, self.league.id)
        content = response.content.decode()
        self.assertIn("watch-list-remove-all", content)
        self.assertIn("action=clear", content)

    def test_remove_all_control_absent_when_empty(self) -> None:
        # No "Remove All" affordance when the watch list is empty.
        response = watch_list(_get(self.league.id), self.league.id)
        self.assertNotIn("watch-list-remove-all", response.content.decode())


# ---------------------------------------------------------------------------
# GET toggle — add / remove / invalid id, with redirect + session mutation
# ---------------------------------------------------------------------------


class TestWatchListToggle(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.player = self.team_a.active_players[0]
        self.other = self.team_b.active_players[0]

    def test_add_appends_player_and_redirects(self) -> None:
        request = _get(self.league.id, query=f"action=add&player_id={self.player.id}")
        response = watch_list(request, self.league.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], WATCH_LIST_PATH.format(lid=self.league.id)
        )
        self.assertEqual(request.session["watch_list"], [self.player.id])

    def test_add_is_idempotent_no_duplicate(self) -> None:
        request = _get(self.league.id, query=f"action=add&player_id={self.player.id}")
        request.session["watch_list"] = [self.player.id]
        request.session.save()
        watch_list(request, self.league.id)
        self.assertEqual(request.session["watch_list"], [self.player.id])

    def test_remove_drops_player_and_redirects(self) -> None:
        request = _get(
            self.league.id, query=f"action=remove&player_id={self.player.id}"
        )
        request.session["watch_list"] = [self.player.id, self.other.id]
        request.session.save()
        response = watch_list(request, self.league.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session["watch_list"], [self.other.id])

    def test_remove_missing_player_is_noop(self) -> None:
        request = _get(
            self.league.id, query=f"action=remove&player_id={self.player.id}"
        )
        request.session["watch_list"] = [self.other.id]
        request.session.save()
        watch_list(request, self.league.id)
        self.assertEqual(request.session["watch_list"], [self.other.id])

    def test_add_invalid_player_id_string_is_ignored(self) -> None:
        request = _get(self.league.id, query="action=add&player_id=not-an-int")
        response = watch_list(request, self.league.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session.get("watch_list", []), [])

    def test_add_nonexistent_player_id_is_ignored(self) -> None:
        request = _get(self.league.id, query="action=add&player_id=999999")
        response = watch_list(request, self.league.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session.get("watch_list", []), [])

    def test_clear_empties_watch_list_and_redirects(self) -> None:
        request = _get(self.league.id, query="action=clear")
        request.session["watch_list"] = [self.player.id, self.other.id]
        request.session.save()
        response = watch_list(request, self.league.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], WATCH_LIST_PATH.format(lid=self.league.id)
        )
        self.assertEqual(request.session["watch_list"], [])

    def test_clear_on_empty_list_is_noop_and_redirects(self) -> None:
        request = _get(self.league.id, query="action=clear")
        response = watch_list(request, self.league.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session.get("watch_list", []), [])

    def test_clear_ignores_player_id_param(self) -> None:
        # action=clear empties everything regardless of any player_id supplied.
        request = _get(self.league.id, query=f"action=clear&player_id={self.player.id}")
        request.session["watch_list"] = [self.player.id, self.other.id]
        request.session.save()
        watch_list(request, self.league.id)
        self.assertEqual(request.session["watch_list"], [])

    def test_toggle_fires_before_no_season_render(self) -> None:
        # A toggle action redirects even when the League has no Season.
        league = _make_league("NoSeasonLeague")
        request = _get(league.id, query=f"action=add&player_id={self.player.id}")
        response = watch_list(request, league.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session["watch_list"], [self.player.id])

    def test_toggle_on_bad_league_still_404s(self) -> None:
        # The League 404 fires before the toggle mutation.
        with self.assertRaises(Http404):
            watch_list(_get(999999, query="action=add&player_id=1"), 999999)


# ---------------------------------------------------------------------------
# LG-06c — Watch List sortable columns
#
# Single ?sort=&dir= pair; keys {name, team, overall_rating}; default
# name/asc. addable_players stays name-ordered; the add/remove/clear GET
# toggle redirect is UNCHANGED and DROPS sort/dir on the bare-URL redirect.
# DOM ids watch-list-th-<key>; active glyph U+2191 / U+2193.
#
# EXPECTED TO FAIL until the Code agent lands watched_players sorting +
# headers.
# ---------------------------------------------------------------------------

import re  # noqa: E402

_WL_GLYPH_UP = "↑"
_WL_GLYPH_DOWN = "↓"


def _wl_row_ids_in_order(content: str) -> list[int]:
    return [int(m) for m in re.findall(r"watch-list-row-(\d+)", content)]


class _WatchSortFixtureMixin:
    """Seed the session with two watched players whose name / team / overall
    orderings DIFFER, so each sort key picks a distinguishable first row."""

    def _build(self):
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        # Two players with distinct names; team_a name < team_b name (set up
        # below). Give them clearly different overall ratings.
        self.lo_name = Player.objects.create(team=self.team_a, name="Aaron Watched")
        self.hi_name = Player.objects.create(team=self.team_b, name="Zed Watched")
        # Make overall_rating differ: set every stat high on hi_name's player
        # but actually we want name-vs-rating to diverge. Aaron (name-first)
        # gets the LOWER overall so name-asc and rating-asc disagree on order.
        for field in (
            "player_awareness",
            "game_awareness",
            "resource_awareness",
            "decision_making",
            "positioning",
            "stamina",
            "speed",
            "flexibility",
            "adaptability",
            "communication",
            "teamwork",
            "Offensive_synergy",
            "defensive_synergy",
            "midfield_synergy",
            "resupply_synergy",
            "resupply_efficiency",
            "accuracy",
            "survival",
            "special_usage",
        ):
            setattr(self.lo_name, field, 10)
            setattr(self.hi_name, field, 90)
        self.lo_name.save()
        self.hi_name.save()
        # team_a / team_b names: ensure a known alphabetical relationship.
        self.team_lo, self.team_hi = sorted(
            [self.team_a, self.team_b], key=lambda t: t.name
        )

    def _get_sorted(self, query: str = ""):
        request = _get(self.league.id, query=query)
        request.session["watch_list"] = [self.lo_name.id, self.hi_name.id]
        request.session.save()
        return watch_list(request, self.league.id)


class TestWatchListSortDefault(_WatchSortFixtureMixin, TestCase):
    def setUp(self) -> None:
        self._build()

    def test_default_order_is_name_asc(self) -> None:
        content = self._get_sorted().content.decode()
        order = _wl_row_ids_in_order(content)
        # "Aaron Watched" < "Zed Watched" → lo_name first.
        self.assertEqual(order[0], self.lo_name.id)


class TestWatchListSortKeys(_WatchSortFixtureMixin, TestCase):
    def setUp(self) -> None:
        self._build()

    def test_name_asc_then_desc(self) -> None:
        asc = _wl_row_ids_in_order(
            self._get_sorted("sort=name&dir=asc").content.decode()
        )
        desc = _wl_row_ids_in_order(
            self._get_sorted("sort=name&dir=desc").content.decode()
        )
        self.assertEqual(asc[0], self.lo_name.id)
        self.assertEqual(desc[0], self.hi_name.id)

    def test_overall_rating_asc_then_desc(self) -> None:
        # lo_name has overall 10, hi_name has overall 90.
        asc = _wl_row_ids_in_order(
            self._get_sorted("sort=overall_rating&dir=asc").content.decode()
        )
        desc = _wl_row_ids_in_order(
            self._get_sorted("sort=overall_rating&dir=desc").content.decode()
        )
        self.assertEqual(asc[0], self.lo_name.id)
        self.assertEqual(desc[0], self.hi_name.id)

    def test_team_asc_then_desc(self) -> None:
        # Determine which watched player belongs to the lower-named team.
        lo_player = (
            self.lo_name if self.lo_name.team_id == self.team_lo.id else self.hi_name
        )
        hi_player = (
            self.lo_name if self.lo_name.team_id == self.team_hi.id else self.hi_name
        )
        asc = _wl_row_ids_in_order(
            self._get_sorted("sort=team&dir=asc").content.decode()
        )
        desc = _wl_row_ids_in_order(
            self._get_sorted("sort=team&dir=desc").content.decode()
        )
        self.assertEqual(asc[0], lo_player.id)
        self.assertEqual(desc[0], hi_player.id)


class TestWatchListSortInvalidFallback(_WatchSortFixtureMixin, TestCase):
    def setUp(self) -> None:
        self._build()

    def test_garbage_sort_falls_back_to_name_asc(self) -> None:
        order = _wl_row_ids_in_order(self._get_sorted("sort=BOGUS").content.decode())
        self.assertEqual(order[0], self.lo_name.id)

    def test_garbage_dir_falls_back_to_asc(self) -> None:
        order = _wl_row_ids_in_order(
            self._get_sorted("sort=name&dir=NOPE").content.decode()
        )
        self.assertEqual(order[0], self.lo_name.id)

    def test_uppercase_sort_falls_back(self) -> None:
        order = _wl_row_ids_in_order(self._get_sorted("sort=NAME").content.decode())
        self.assertEqual(order[0], self.lo_name.id)


class TestWatchListSortHeaderGlyph(_WatchSortFixtureMixin, TestCase):
    def setUp(self) -> None:
        self._build()

    def test_th_dom_ids_present_when_watching(self) -> None:
        content = self._get_sorted().content.decode()
        for key in ("name", "team", "overall_rating"):
            self.assertIn(f"watch-list-th-{key}", content)

    def test_active_overall_rating_header_glyph(self) -> None:
        content = self._get_sorted("sort=overall_rating&dir=desc").content.decode()
        th_start = content.index("watch-list-th-overall_rating")
        window = content[th_start : th_start + 400]
        self.assertIn(_WL_GLYPH_DOWN, window)


class TestWatchListSortDoesNotAffectAddControl(_WatchSortFixtureMixin, TestCase):
    """addable_players (the add <select>) stays name-ordered regardless of
    ?sort=."""

    def setUp(self) -> None:
        self._build()

    def test_add_control_unaffected_by_sort(self) -> None:
        # With nothing watched, both players are addable; sorting the (empty)
        # watched table by overall_rating desc must not reorder the add list.
        request = _get(self.league.id, query="sort=overall_rating&dir=desc")
        request.session["watch_list"] = []
        request.session.save()
        content = watch_list(request, self.league.id).content.decode()
        # The add control's options stay name-ordered: the option for
        # "Aaron Watched" (lo_name) appears before "Zed Watched" (hi_name).
        lo_pos = content.find(f'value="{self.lo_name.id}"')
        hi_pos = content.find(f'value="{self.hi_name.id}"')
        self.assertNotEqual(lo_pos, -1)
        self.assertNotEqual(hi_pos, -1)
        self.assertLess(lo_pos, hi_pos)


class TestWatchListMutationDropsSort(_WatchSortFixtureMixin, TestCase):
    """The add/remove/clear GET toggle still 302-redirects to the BARE URL,
    dropping sort/dir (locked behaviour)."""

    def setUp(self) -> None:
        self._build()

    def test_add_redirect_drops_sort_and_dir(self) -> None:
        request = _get(
            self.league.id,
            query=f"action=add&player_id={self.lo_name.id}&sort=team&dir=desc",
        )
        response = watch_list(request, self.league.id)
        self.assertEqual(response.status_code, 302)
        # Redirect target is the bare watch-list URL — no query string.
        self.assertEqual(
            response["Location"], WATCH_LIST_PATH.format(lid=self.league.id)
        )
        self.assertNotIn("sort=", response["Location"])
        self.assertNotIn("dir=", response["Location"])
