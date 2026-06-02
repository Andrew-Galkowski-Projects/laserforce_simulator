"""LG-06f — tests for the ``watch_flag.html`` partial render + the
``core.context_processors.watch_list`` resolution.

Two surfaces (seam contract §10 ``test_watch_flag.py`` boundary):

* **Partial render** — rendering a league screen (Player Stats) with a
  watched player ⇒ that player's flag carries the ``watch-flag-on`` class
  (red); an unwatched player's flag carries ``watch-flag`` but NOT
  ``watch-flag-on`` (grey). We assert only on the load-bearing classes,
  ``data-player-id`` and ``data-toggle-url`` — NOT the glyph, colour, or
  Bootstrap utilities (Code-agent discretion per §9).
* **Context-processor resolution** — ``watch_list(request)`` returns
  ``{"watched_player_ids": <set[int]>}`` matching the per-League session
  store when ``request.resolver_match.kwargs`` carries ``league_id``, and an
  EMPTY set off-League (no ``resolver_match`` / no ``league_id`` kwarg). We
  drive the processor directly via ``RequestFactory`` + a stubbed
  ``resolver_match``.

NO JS behaviour is asserted here (the toggle is exercised in
``test_watch_toggle.py``).

These tests are written test-first against the LG-06f seam contract
(``.claude/worktrees/lg-06f-seam-contract.md``); they FAIL until the Code
agent lands the context processor + partial + screen wiring.
"""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from core.context_processors import watch_list as watch_list_context
from matches.league_screens.player_stats import player_stats
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Player

# ---------------------------------------------------------------------------
# Fixtures (hand-built — LG-06f runs NO simulation)
# ---------------------------------------------------------------------------


def _attach_session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _make_league(name: str = "FlagLeague") -> League:
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
    from matches.models import GameRound, Match

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
    from matches.models import PlayerRoundState

    defaults = dict(
        game_round=game_round, player=player, team_color=team_color, role=role
    )
    defaults.update(stats)
    return PlayerRoundState.objects.create(**defaults)


# The ``.watch-flag-on`` CSS rule + the JS ``setAll`` string in
# ``watch_flag_script.html`` mean the bare substring is ALWAYS in the page.
# The load-bearing surface (contract §3 / §9) is the class ON THE BUTTON, so
# these helpers inspect the actual ``<button …>`` tags only.
_BUTTON_RE = re.compile(r"<button\b[^>]*>", re.IGNORECASE)


def _button_is_watched(content: str, player_id: int) -> bool:
    """True iff the watch-flag button for ``player_id`` carries the
    ``watch-flag-on`` class in its ``class`` attribute."""
    for tag in _BUTTON_RE.findall(content):
        if (
            f'data-player-id="{player_id}"' in tag
            and "watch-flag" in tag
            and "watch-flag-on" in tag
        ):
            return True
    return False


def _any_button_watched(content: str) -> bool:
    """True iff ANY watch-flag button carries ``watch-flag-on``."""
    for tag in _BUTTON_RE.findall(content):
        if "watch-flag-on" in tag and "data-player-id" in tag:
            return True
    return False


# ---------------------------------------------------------------------------
# Partial render — watch-flag-on vs plain watch-flag
# ---------------------------------------------------------------------------


class TestWatchFlagPartialRender(TestCase):
    """Render Player Stats (which hosts the flag in its name cell, §8) and
    assert the per-player flag class reflects watched membership."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        gr = _make_round(self.season, self.team_a, self.team_b)
        # Two players with rounds in scope so both render as table rows.
        self.watched = self.team_a.active_players[0]
        self.unwatched = self.team_b.active_players[0]
        _make_prs(gr, self.watched, "red", "scout", tags_made=5, points_scored=100)
        _make_prs(gr, self.unwatched, "blue", "scout", tags_made=3, points_scored=80)

    def _render(self, *watched_ids: int) -> str:
        path = f"/leagues/{self.league.id}/stats/player-stats/"
        request = _attach_session(RequestFactory().get(path))
        request.session["watch_lists"] = {str(self.league.id): list(watched_ids)}
        request.session.save()
        # The screen view renders through Django's template engine; the
        # context processor injects ``watched_player_ids``. The view's
        # render() runs context processors only when the request carries a
        # resolver_match, so stub one carrying league_id.
        request.resolver_match = SimpleNamespace(kwargs={"league_id": self.league.id})
        return player_stats(request, self.league.id).content.decode()

    def test_watched_player_flag_has_watch_flag_on(self) -> None:
        content = self._render(self.watched.id)
        # The flag button for the watched player carries watch-flag-on on the
        # button tag itself (CSS-robust check).
        self.assertTrue(_button_is_watched(content, self.watched.id))

    def test_unwatched_player_flag_has_plain_watch_flag(self) -> None:
        content = self._render(self.watched.id)
        # The base class is always present (the JS delegated-handler hook).
        self.assertIn("watch-flag", content)
        # The unwatched player's button exists but is NOT the watched-state
        # class.
        self.assertIn(f'data-player-id="{self.unwatched.id}"', content)
        self.assertFalse(_button_is_watched(content, self.unwatched.id))

    def test_flag_carries_toggle_url(self) -> None:
        content = self._render(self.watched.id)
        # The toggle URL is baked onto the button via data-toggle-url
        # (league id already substituted).
        self.assertIn(f"/leagues/{self.league.id}/players/watch-list/toggle/", content)
        self.assertIn("data-toggle-url", content)

    def test_no_watched_ids_renders_no_watch_flag_on(self) -> None:
        content = self._render()  # empty watch list for this League
        self.assertIn("watch-flag", content)  # buttons still render
        # No BUTTON carries the watched-state class (the CSS rule / JS string
        # mentioning ``watch-flag-on`` is not a button tag, so this is robust).
        self.assertFalse(_any_button_watched(content))

    def test_script_renders_nonempty_csrf_token(self) -> None:
        # Regression (code-review WARNING): the league screens render GET-only
        # forms with no {% csrf_token %}, so a fresh session has no csrftoken
        # cookie. The watch-flag script must render the token server-side (which
        # also sets the cookie via get_token()) so the toggle POST does not 403.
        content = self._render(self.watched.id)
        match = re.search(r'CSRF_TOKEN\s*=\s*"([^"]*)"', content)
        self.assertIsNotNone(match, "watch-flag script must render a CSRF_TOKEN")
        self.assertNotEqual(match.group(1), "", "CSRF_TOKEN must be non-empty")


# ---------------------------------------------------------------------------
# Context processor — core.context_processors.watch_list
# ---------------------------------------------------------------------------


class TestWatchListContextProcessor(TestCase):
    """Drive ``watch_list(request)`` directly with a stubbed resolver_match."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.other_league = _make_league("OtherFlagLeague")

    def _request(
        self,
        *,
        session_data: dict | None = None,
        resolver_kwargs=None,
        has_resolver: bool = True,
    ):
        request = _attach_session(RequestFactory().get("/"))
        if session_data is not None:
            for k, v in session_data.items():
                request.session[k] = v
            request.session.save()
        if has_resolver:
            request.resolver_match = SimpleNamespace(
                kwargs=resolver_kwargs if resolver_kwargs is not None else {}
            )
        return request

    def test_returns_watched_player_ids_key(self) -> None:
        request = self._request(resolver_kwargs={"league_id": self.league.id})
        result = watch_list_context(request)
        self.assertIn("watched_player_ids", result)

    def test_resolves_per_league_set_from_session(self) -> None:
        request = self._request(
            session_data={"watch_lists": {str(self.league.id): [12, 47, 105]}},
            resolver_kwargs={"league_id": self.league.id},
        )
        result = watch_list_context(request)
        self.assertEqual(result["watched_player_ids"], {12, 47, 105})

    def test_returns_set_type(self) -> None:
        request = self._request(
            session_data={"watch_lists": {str(self.league.id): [1, 2]}},
            resolver_kwargs={"league_id": self.league.id},
        )
        result = watch_list_context(request)
        self.assertIsInstance(result["watched_player_ids"], set)

    def test_only_this_leagues_ids_returned(self) -> None:
        # League A watches {1,2}; League B watches {9}. Resolving for A must
        # not leak B's ids.
        request = self._request(
            session_data={
                "watch_lists": {
                    str(self.league.id): [1, 2],
                    str(self.other_league.id): [9],
                }
            },
            resolver_kwargs={"league_id": self.league.id},
        )
        result = watch_list_context(request)
        self.assertEqual(result["watched_player_ids"], {1, 2})

    def test_empty_set_when_no_resolver_match(self) -> None:
        # Off-League: a request without a resolver_match (e.g. a
        # RequestFactory-built request) resolves to an EMPTY set.
        request = self._request(
            session_data={"watch_lists": {str(self.league.id): [1, 2]}},
            has_resolver=False,
        )
        result = watch_list_context(request)
        self.assertEqual(result["watched_player_ids"], set())

    def test_empty_set_when_no_league_id_kwarg(self) -> None:
        # Off-League: resolver_match exists but carries no league_id kwarg.
        request = self._request(
            session_data={"watch_lists": {str(self.league.id): [1, 2]}},
            resolver_kwargs={"some_other": 5},
        )
        result = watch_list_context(request)
        self.assertEqual(result["watched_player_ids"], set())

    def test_empty_set_when_league_has_no_watch_list(self) -> None:
        # On-League but nothing watched for this League ⇒ empty set, no crash.
        request = self._request(
            session_data={"watch_lists": {}},
            resolver_kwargs={"league_id": self.league.id},
        )
        result = watch_list_context(request)
        self.assertEqual(result["watched_player_ids"], set())

    def test_empty_set_when_no_watch_lists_key(self) -> None:
        # On-League but the session has no watch_lists at all ⇒ empty set.
        request = self._request(
            resolver_kwargs={"league_id": self.league.id},
        )
        result = watch_list_context(request)
        self.assertEqual(result["watched_player_ids"], set())
