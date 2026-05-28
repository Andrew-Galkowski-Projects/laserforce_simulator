"""LG-01f — Django ``TestCase`` tests for
``core.context_processors.league_nav``.

The seam contract is locked at ``.claude/worktrees/lg-01f-seam-contract.md``
(§7b processor signature + chain, §9c class list). The processor returns
``{"top_bar_history_url": <url>}`` resolved via the chain:

1. ``request.session["last_league_id"]`` ⇒ ``league_history`` of that
   League iff it still exists.
2. Else if exactly one League exists ⇒ ``league_history`` of that
   League.
3. Else ⇒ ``league_list``.
"""

from __future__ import annotations

from unittest.mock import patch

from django.db import DatabaseError
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.context_processors import league_nav
from matches.models import League

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "L") -> League:
    return League.objects.create(name=name, mode="league", state="active")


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


def _request_no_session():
    """Build a ``RequestFactory()`` GET request with NO session attribute.

    Per the seam contract's no-crash defensive rule.
    """
    factory = RequestFactory()
    request = factory.get("/leagues/")
    # Intentionally no SessionMiddleware applied.
    return request


# ---------------------------------------------------------------------------
# TestLeagueNavContextProcessor
# ---------------------------------------------------------------------------


class TestLeagueNavContextProcessor(TestCase):
    """8 pinned test methods per §9c."""

    def test_session_pin_with_existing_league_returns_history_url(self) -> None:
        _ = _make_league("A")
        lb = _make_league("B")
        request = _request_with_session(session_data={"last_league_id": lb.id})
        result = league_nav(request)
        self.assertEqual(
            result["top_bar_history_url"],
            reverse("league_history", kwargs={"league_id": lb.id}),
        )

    def test_session_pin_with_stale_league_id_falls_through_to_single_league_branch(
        self,
    ) -> None:
        only_one = _make_league("Only")
        # Session pin points at a non-existent League id.
        request = _request_with_session(session_data={"last_league_id": 99999})
        result = league_nav(request)
        # Should fall through to the "exactly one League" branch.
        self.assertEqual(
            result["top_bar_history_url"],
            reverse("league_history", kwargs={"league_id": only_one.id}),
        )

    def test_session_pin_with_stale_league_id_falls_through_to_list_when_zero_leagues(
        self,
    ) -> None:
        # Zero Leagues in DB.
        request = _request_with_session(session_data={"last_league_id": 99999})
        result = league_nav(request)
        self.assertEqual(result["top_bar_history_url"], reverse("league_list"))

    def test_single_league_no_session_returns_that_leagues_history_url(self) -> None:
        only_one = _make_league("Only")
        request = _request_with_session()
        result = league_nav(request)
        self.assertEqual(
            result["top_bar_history_url"],
            reverse("league_history", kwargs={"league_id": only_one.id}),
        )

    def test_multiple_leagues_no_session_returns_list_url(self) -> None:
        _make_league("A")
        _make_league("B")
        request = _request_with_session()
        result = league_nav(request)
        self.assertEqual(result["top_bar_history_url"], reverse("league_list"))

    def test_zero_leagues_no_session_returns_list_url(self) -> None:
        request = _request_with_session()
        result = league_nav(request)
        self.assertEqual(result["top_bar_history_url"], reverse("league_list"))

    def test_returned_key_is_top_bar_history_url(self) -> None:
        _make_league("OnlyKey")
        request = _request_with_session()
        result = league_nav(request)
        self.assertEqual(list(result.keys()), ["top_bar_history_url"])
        self.assertIsInstance(result["top_bar_history_url"], str)

    def test_no_crash_when_request_has_no_session_attribute(self) -> None:
        request = _request_no_session()
        # Must not raise.
        result = league_nav(request)
        self.assertIn("top_bar_history_url", result)
        self.assertIsInstance(result["top_bar_history_url"], str)

    def test_falls_back_when_session_pin_query_raises_database_error(self) -> None:
        """Regression for the LG01f-9 fix.

        ``roster_import`` (and any other ``@transaction.atomic``-wrapped
        view that catches an exception and re-renders with
        ``transaction.set_rollback(True)``) leaves the connection
        unable to execute further queries. The session-pin ``.exists()``
        query then raises ``DatabaseError``; ``league_nav`` must catch
        and fall through to the list URL so the response still renders.
        """
        only_one = _make_league("OnlyOne")
        request = _request_with_session(session_data={"last_league_id": only_one.id})
        with patch(
            "matches.models.League.objects",
        ) as mock_objects:
            mock_objects.filter.return_value.exists.side_effect = DatabaseError(
                "transaction is broken"
            )
            mock_objects.values_list.return_value.__getitem__.return_value = []
            result = league_nav(request)
        # Falls through past step 1 (DatabaseError on session pin) and
        # step 2 (mocked to return zero league_ids) → step 3 list URL.
        self.assertEqual(result["top_bar_history_url"], reverse("league_list"))

    def test_falls_back_when_single_league_query_raises_database_error(self) -> None:
        """Regression for the LG01f-9 fix on the fallback branch.

        When no session pin is set and the single-League ``values_list``
        query raises ``DatabaseError``, ``league_nav`` falls through to
        the list URL.
        """
        request = _request_with_session()  # no session pin
        with patch("matches.models.League.objects") as mock_objects:
            mock_objects.values_list.side_effect = DatabaseError(
                "transaction is broken"
            )
            result = league_nav(request)
        self.assertEqual(result["top_bar_history_url"], reverse("league_list"))
