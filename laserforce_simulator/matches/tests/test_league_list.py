"""LG-01a — Django ``TestCase`` tests for ``matches.views.league_list``.

The seam contract is locked at ``.claude/worktrees/lg-01a-seam-contract.md``.
The view is read-only and renders three sections — active table, archived
table, and an empty notice — each gated on its own non-empty list. The
``Create League`` button renders unconditionally.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import League


class TestLeagueListView(TestCase):
    """LG-01a — ``matches.views.league_list`` flat list page.

    DOM ids, sort order, and the deferred /leagues/<id>/ link shape
    (LG-01c) and /leagues/create/ button shape (LG-01b) are locked.
    """

    def _make_active(self, name: str) -> League:
        return League.objects.create(name=name, state="active")

    def _make_archived(self, name: str) -> League:
        return League.objects.create(name=name, state="archived")

    def test_league_list_get_returns_200_with_default_context(self) -> None:
        response = self.client.get(reverse("league_list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("active_leagues", response.context)
        self.assertIn("archived_leagues", response.context)

    def test_league_list_url_reverses(self) -> None:
        self.assertEqual(reverse("league_list"), "/leagues/")

    def test_league_list_empty_shows_empty_notice_and_create_button(
        self,
    ) -> None:
        response = self.client.get(reverse("league_list"))
        self.assertContains(response, 'id="league-list-empty-notice"')
        self.assertContains(response, "No Leagues yet")
        self.assertContains(response, 'id="league-create-link"')

    def test_league_list_active_table_lists_active_leagues_sorted_by_id_desc(
        self,
    ) -> None:
        l1 = self._make_active("Alpha League")
        l2 = self._make_active("Bravo League")
        response = self.client.get(reverse("league_list"))
        body = response.content.decode()
        self.assertIn('id="league-list-active-table"', body)
        self.assertIn(l1.name, body)
        self.assertIn(l2.name, body)
        # Sorted by -id: L2 appears before L1.
        self.assertLess(body.index(l2.name), body.index(l1.name))

    def test_league_list_archived_table_lists_archived_leagues_sorted_by_id_desc(
        self,
    ) -> None:
        l1 = self._make_archived("Old Alpha")
        l2 = self._make_archived("Old Bravo")
        response = self.client.get(reverse("league_list"))
        body = response.content.decode()
        self.assertIn('id="league-list-archived-table"', body)
        self.assertIn(l1.name, body)
        self.assertIn(l2.name, body)
        # Sorted by -id: L2 appears before L1.
        self.assertLess(body.index(l2.name), body.index(l1.name))

    def test_league_list_omits_active_table_when_no_active_leagues(
        self,
    ) -> None:
        self._make_archived("Only Archived")
        response = self.client.get(reverse("league_list"))
        self.assertNotContains(response, 'id="league-list-active-table"')

    def test_league_list_omits_archived_table_when_no_archived_leagues(
        self,
    ) -> None:
        self._make_active("Only Active")
        response = self.client.get(reverse("league_list"))
        self.assertNotContains(response, 'id="league-list-archived-table"')

    def test_league_list_row_links_to_deferred_league_detail_url(self) -> None:
        active = self._make_active("Active Linked")
        archived = self._make_archived("Archived Linked")
        response = self.client.get(reverse("league_list"))
        # Deferred broken link (LG-01c) — raw URL string per row.
        self.assertContains(response, f'href="/leagues/{active.id}/"')
        self.assertContains(response, f'href="/leagues/{archived.id}/"')

    def test_create_league_link_points_to_deferred_lg01b_route(self) -> None:
        response = self.client.get(reverse("league_list"))
        # Deferred broken link to LG-01b — raw URL string.
        self.assertContains(response, 'href="/leagues/create/"')
