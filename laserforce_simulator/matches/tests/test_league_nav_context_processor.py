"""LG-01k — Django ``TestCase`` tests for
``core.context_processors.league_nav``.

The LG-01k seam contract is locked at
``.claude/worktrees/lg-01k-seam-contract.md`` (Part b — League-nav
context processor). The processor returns the 2-key dict
``{"top_bar_links": list[dict], "top_bar_dashboard_url": str}``:

* On success (3-step League resolution chain — session pin → single
  League → fallback): ``top_bar_links`` is the 23-entry output of
  ``_build_league_sidebar_links(league, displayed_season,
  sidebar_active=None)`` and ``top_bar_dashboard_url`` resolves to
  ``reverse("league_dashboard", kwargs={"league_id": league.id})``.
* On fallback (zero or 2+ Leagues with no session pin, stale session
  pin, or ``DatabaseError`` inside a broken transaction):
  ``top_bar_links`` is ``[]`` and ``top_bar_dashboard_url`` is
  ``reverse("league_list")``.

The 5 LG-01h URL keys (``top_bar_history_url`` /
``top_bar_standings_url`` / ``top_bar_playoffs_url`` /
``top_bar_finances_url`` / ``top_bar_power_rankings_url``) are
RETIRED — they must NOT appear in the returned dict.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.context_processors import league_nav
from matches.models import League, Season

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "L") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_season(
    league: League,
    *,
    name: str = "S1",
    state: str = "active",
    start_day: int = 1,
    starting_team_ids: list[int] | None = None,
) -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=date(2025, 1, start_day),
        state=state,
        schedule_format="single_round_robin",
        starting_team_ids_json=(
            starting_team_ids if starting_team_ids is not None else []
        ),
    )


def _request_with_session(*, session_data: dict | None = None):
    """Build a ``RequestFactory()`` GET request with a real session
    attached via ``SessionMiddleware``.
    """
    factory = RequestFactory()
    request = factory.get("/leagues/")
    middleware = SessionMiddleware(lambda r: None)
    middleware.process_request(request)
    request.session.save()
    if session_data:
        for k, v in session_data.items():
            request.session[k] = v
        request.session.save()
    return request


# ---------------------------------------------------------------------------
# TestLg01kLeagueNavContextProcessor (LG-01k — 2-key return shape)
# ---------------------------------------------------------------------------


class TestLg01kLeagueNavContextProcessor(TestCase):
    """11 pinned test methods per the LG-01k seam contract."""

    # -- top_bar_links shape ------------------------------------------------

    def test_top_bar_links_is_23_entries_with_league(self) -> None:
        league = _make_league("WithLeague")
        _make_season(league, state="active")
        request = _request_with_session(session_data={"last_league_id": league.id})
        result = league_nav(request)
        self.assertIsInstance(result["top_bar_links"], list)
        self.assertEqual(len(result["top_bar_links"]), 23)

    def test_top_bar_links_is_empty_on_fallback(self) -> None:
        # Zero Leagues exist.
        request = _request_with_session()
        result = league_nav(request)
        self.assertEqual(result["top_bar_links"], [])

    def test_top_bar_links_is_empty_with_two_leagues_no_session_pin(
        self,
    ) -> None:
        _make_league("A")
        _make_league("B")
        request = _request_with_session()  # no session pin
        result = league_nav(request)
        self.assertEqual(result["top_bar_links"], [])

    # -- top_bar_dashboard_url ----------------------------------------------

    def test_top_bar_dashboard_url_resolves_to_league_dashboard(
        self,
    ) -> None:
        league = _make_league("Dash")
        request = _request_with_session(session_data={"last_league_id": league.id})
        result = league_nav(request)
        self.assertEqual(
            result["top_bar_dashboard_url"],
            reverse("league_dashboard", kwargs={"league_id": league.id}),
        )

    def test_top_bar_dashboard_url_falls_back_to_league_list(self) -> None:
        # Zero Leagues exist ⇒ list URL fallback.
        request = _request_with_session()
        result = league_nav(request)
        self.assertEqual(result["top_bar_dashboard_url"], reverse("league_list"))

    # -- session pin resolution ---------------------------------------------

    def test_session_pin_resolves_picked_league(self) -> None:
        _make_league("First")
        league2 = _make_league("Picked")
        request = _request_with_session(session_data={"last_league_id": league2.id})
        result = league_nav(request)
        # Both new keys reflect the session-pinned League.
        self.assertGreater(len(result["top_bar_links"]), 0)
        self.assertEqual(
            result["top_bar_dashboard_url"],
            reverse("league_dashboard", kwargs={"league_id": league2.id}),
        )

    # -- displayed-Season chain --------------------------------------------

    def test_displayed_season_falls_back_to_most_recent_completed(
        self,
    ) -> None:
        league = _make_league("OnlyCompleted")
        # No active Season, only a completed one.
        _make_season(league, state="completed", name="S1", start_day=1)
        request = _request_with_session(session_data={"last_league_id": league.id})
        result = league_nav(request)
        # The completed Season feeds the helper ⇒ Standings / Schedule
        # are LIVE so the full 23-entry list is emitted.
        self.assertEqual(len(result["top_bar_links"]), 23)
        # Find the Standings entry and confirm it is LIVE (url not None,
        # disabled False).
        standings_entries = [
            e
            for e in result["top_bar_links"]
            if e["section"] == "league" and e["key"] == "standings"
        ]
        self.assertEqual(len(standings_entries), 1)
        self.assertIsNotNone(standings_entries[0]["url"])
        self.assertFalse(standings_entries[0]["disabled"])

    def test_displayed_season_is_none_disables_standings_and_schedule(
        self,
    ) -> None:
        league = _make_league("NoSeason")
        # No Season at all — helper should disable Standings + Schedule.
        request = _request_with_session(session_data={"last_league_id": league.id})
        result = league_nav(request)
        self.assertEqual(len(result["top_bar_links"]), 23)
        # Find Standings + Schedule entries (both in the LEAGUE section).
        standings = next(
            e
            for e in result["top_bar_links"]
            if e["section"] == "league" and e["key"] == "standings"
        )
        schedule = next(
            e
            for e in result["top_bar_links"]
            if e["section"] == "league" and e["key"] == "schedule"
        )
        self.assertIsNone(standings["url"])
        self.assertTrue(standings["disabled"])
        self.assertIsNone(schedule["url"])
        self.assertTrue(schedule["disabled"])

    # -- retired keys -------------------------------------------------------

    def test_retired_keys_absent(self) -> None:
        """The 5 LG-01h URL keys are RETIRED in LG-01k."""
        _make_league("RetiredKeys")
        request = _request_with_session()
        result = league_nav(request)
        for retired_key in (
            "top_bar_history_url",
            "top_bar_standings_url",
            "top_bar_playoffs_url",
            "top_bar_finances_url",
            "top_bar_power_rankings_url",
        ):
            self.assertNotIn(
                retired_key,
                result,
                f"Retired LG-01h key {retired_key!r} still in dict",
            )

    # -- helper invocation --------------------------------------------------

    def test_build_helper_called_with_sidebar_active_none(self) -> None:
        """The processor MUST call
        ``_build_league_sidebar_links(..., sidebar_active=None)`` —
        the topnav has no notion of an "active" entry (that concept
        lives on the sidebar partial, not the topnav).
        """
        league = _make_league("HelperKwarg")
        request = _request_with_session(session_data={"last_league_id": league.id})
        captured: dict = {}

        original_helper = None
        # Import lazily to mirror the processor's own deferred import.
        import matches.league_views as league_views_module

        original_helper = league_views_module._build_league_sidebar_links

        def recording_helper(league_arg, displayed_season_arg, sidebar_active=None):
            captured["sidebar_active"] = sidebar_active
            return original_helper(
                league_arg, displayed_season_arg, sidebar_active=sidebar_active
            )

        with patch.object(
            league_views_module,
            "_build_league_sidebar_links",
            side_effect=recording_helper,
        ):
            league_nav(request)
        self.assertIn(
            "sidebar_active",
            captured,
            "_build_league_sidebar_links not called by processor",
        )
        self.assertIsNone(captured["sidebar_active"])

    # -- top entry shape ----------------------------------------------------

    def test_top_bar_links_top_entry_present(self) -> None:
        """The top Dashboard entry is in the helper output (the TEMPLATE
        filters it out of the regrouped iteration, NOT the processor).
        """
        league = _make_league("TopEntry")
        request = _request_with_session(session_data={"last_league_id": league.id})
        result = league_nav(request)
        self.assertGreater(len(result["top_bar_links"]), 0)
        top_entry = result["top_bar_links"][0]
        self.assertEqual(top_entry["section"], "top")
        self.assertEqual(top_entry["key"], "dashboard")


# ---------------------------------------------------------------------------
# TestLg01kProcessorRobustness (no-crash + types — keeps the LG-01f
# defensive-render and LG-01h no-crash intent alive under the LG-01k
# 2-key shape).
# ---------------------------------------------------------------------------


class TestLg01kProcessorRobustness(TestCase):
    """Defensive-render assertions adapted to the LG-01k 2-key shape."""

    def test_no_crash_when_request_has_no_session_attribute(self) -> None:
        factory = RequestFactory()
        request = factory.get("/leagues/")
        # Intentionally no SessionMiddleware applied.
        result = league_nav(request)
        self.assertIn("top_bar_links", result)
        self.assertIn("top_bar_dashboard_url", result)
        self.assertIsInstance(result["top_bar_links"], list)
        self.assertIsInstance(result["top_bar_dashboard_url"], str)


# ---------------------------------------------------------------------------
# NAV-01 — `league_nav` merges the 9 play keys + play_displayed_season_id +
# play_league_id on a league-prefix request with a resolvable league; the play
# keys are ABSENT off-league (sandbox path) and on the `_fallback()` path (no
# resolvable league).
#
# Seam contract: ``.claude/worktrees/nav-01-seam-contract.md`` §2 + §8.
#
# These assertions WILL fail until the Code agent extends ``league_nav`` to
# merge the play keys — the expected TDD red state, not a defect in this file.
# ---------------------------------------------------------------------------


def _request(path: str, *, session_data: dict | None = None):
    """A ``RequestFactory()`` GET request at ``path`` with a real session."""
    factory = RequestFactory()
    request = factory.get(path)
    middleware = SessionMiddleware(lambda r: None)
    middleware.process_request(request)
    request.session.save()
    if session_data:
        for k, v in session_data.items():
            request.session[k] = v
        request.session.save()
    return request


# The 9 play keys (§1 table) + the 2 URL/id keys (§2 table).
_NAV01_PLAY_KEYS = (
    "action_button_label",
    "action_button_state",
    "playoff_phase_active",
    "playoff_tournament_id",
    "playoff_completed",
    "has_following_tournament_phase",
    "following_tournament_is_final",
    "live_preview_available",
    "is_career_mode",
    "play_displayed_season_id",
    "play_league_id",
)


class TestNav01LeagueNavPlayKeys(TestCase):
    """NAV-01 — the play keys merge on a resolvable league-prefix request."""

    def test_play_keys_present_on_leagues_prefix_with_session_pin(self) -> None:
        league = _make_league("Nav01Pin")
        _make_season(league, state="active")
        request = _request("/leagues/", session_data={"last_league_id": league.id})
        result = league_nav(request)
        for key in _NAV01_PLAY_KEYS:
            self.assertIn(key, result, f"play key {key!r} missing from league_nav")

    def test_play_keys_present_on_seasons_prefix_with_session_pin(self) -> None:
        # A ``/seasons/<id>/`` page has no league template var — ``league_nav``
        # resolves it via the ``last_league_id`` session pin.
        league = _make_league("Nav01Season")
        season = _make_season(league, state="active")
        request = _request(
            f"/seasons/{season.id}/",
            session_data={"last_league_id": league.id},
        )
        result = league_nav(request)
        for key in _NAV01_PLAY_KEYS:
            self.assertIn(key, result)

    def test_play_league_id_and_displayed_season_id_resolve(self) -> None:
        league = _make_league("Nav01Ids")
        season = _make_season(league, state="active")
        request = _request("/leagues/", session_data={"last_league_id": league.id})
        result = league_nav(request)
        self.assertEqual(result["play_league_id"], league.id)
        self.assertEqual(result["play_displayed_season_id"], season.id)

    def test_play_displayed_season_id_none_when_no_season(self) -> None:
        league = _make_league("Nav01NoSeason")
        request = _request("/leagues/", session_data={"last_league_id": league.id})
        result = league_nav(request)
        self.assertEqual(result["play_league_id"], league.id)
        self.assertIsNone(result["play_displayed_season_id"])
        self.assertEqual(result["action_button_state"], "none")

    def test_action_button_state_matches_season_state(self) -> None:
        league = _make_league("Nav01Draft")
        _make_season(league, state="draft")
        request = _request("/leagues/", session_data={"last_league_id": league.id})
        result = league_nav(request)
        self.assertEqual(result["action_button_state"], "start_season")


class TestNav01LeagueNavPlayKeysAbsent(TestCase):
    """NAV-01 — the play keys are ABSENT off-league and on the fallback path."""

    def test_play_keys_absent_off_league_sandbox_path(self) -> None:
        # A resolvable league exists + is pinned, but the request path is the
        # sandbox ``/teams/`` page ⇒ the play work is gated off.
        league = _make_league("Nav01OffLeague")
        _make_season(league, state="active")
        request = _request("/teams/", session_data={"last_league_id": league.id})
        result = league_nav(request)
        for key in _NAV01_PLAY_KEYS:
            self.assertNotIn(
                key, result, f"play key {key!r} leaked onto a sandbox path"
            )

    def test_play_keys_absent_on_fallback_no_resolvable_league(self) -> None:
        # Zero Leagues exist ⇒ ``_fallback()`` ⇒ no play keys.
        request = _request("/leagues/")
        result = league_nav(request)
        for key in _NAV01_PLAY_KEYS:
            self.assertNotIn(key, result)

    def test_play_keys_absent_on_fallback_two_leagues_no_pin(self) -> None:
        _make_league("Nav01A")
        _make_league("Nav01B")
        request = _request("/leagues/")  # no session pin, 2+ Leagues
        result = league_nav(request)
        for key in _NAV01_PLAY_KEYS:
            self.assertNotIn(key, result)

    def test_existing_2_keys_unchanged_off_league(self) -> None:
        # The LG-01k 2-key shape (top_bar_links / top_bar_dashboard_url) is
        # still present off-league; only the play keys are gated.
        league = _make_league("Nav01TwoKeys")
        request = _request("/teams/", session_data={"last_league_id": league.id})
        result = league_nav(request)
        self.assertIn("top_bar_links", result)
        self.assertIn("top_bar_dashboard_url", result)
