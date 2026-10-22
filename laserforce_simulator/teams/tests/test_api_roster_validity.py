"""ANG-06a T1 -- server-side roster validity on every Team API response.

``TeamListSerializer`` and ``TeamSerializer`` carry ``is_valid_roster`` and
``roster_errors`` straight from ``Team.is_valid_roster`` / ``Team.roster_errors``
so the Angular app never re-implements the SM5 roster rules (spec #263).
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.tests.conftest import make_team_with_slots
from teams.models import Team


class _RosterApiTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = get_shared_manager()
        self.api = APIClient()
        self.api.force_login(self.user)

    def owned_team(self, prefix: str) -> Team:
        team, _ = make_team_with_slots(prefix)
        team.manager = self.user
        team.save()
        return team

    def detail(self, team: Team) -> dict:
        response = self.api.get(reverse("team-detail", args=[team.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def list_row(self, team: Team) -> dict:
        response = self.api.get(reverse("team-list"))
        self.assertEqual(response.status_code, 200, response.content)
        rows = [r for r in response.json()["data"] if r["id"] == team.pk]
        self.assertEqual(len(rows), 1)
        return rows[0]


class TestValidRoster(_RosterApiTestCase):
    def test_detail_reports_a_full_roster_as_valid(self) -> None:
        team = self.owned_team("Valid")
        data = self.detail(team)
        self.assertIs(data["is_valid_roster"], True)
        self.assertEqual(data["roster_errors"], [])

    def test_list_reports_a_full_roster_as_valid(self) -> None:
        team = self.owned_team("Valid")
        row = self.list_row(team)
        self.assertIs(row["is_valid_roster"], True)
        self.assertEqual(row["roster_errors"], [])


class TestIncompleteRoster(_RosterApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.team = self.owned_team("NoCdr")
        self.team.slot_commander = None
        self.team.save()

    def test_detail_reports_the_missing_commander(self) -> None:
        data = self.detail(self.team)
        self.assertIs(data["is_valid_roster"], False)
        self.assertEqual(data["roster_errors"], ["missing Commander"])

    def test_list_reports_the_missing_commander(self) -> None:
        row = self.list_row(self.team)
        self.assertIs(row["is_valid_roster"], False)
        self.assertEqual(row["roster_errors"], ["missing Commander"])


class TestDuplicateSlotRoster(_RosterApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.team = self.owned_team("Dup")
        # One player in two slots -- set on the model, since the slot form rejects it.
        self.team.slot_heavy = self.team.slot_commander
        self.team.save()

    def test_detail_reports_the_duplicate_player(self) -> None:
        data = self.detail(self.team)
        self.assertIs(data["is_valid_roster"], False)
        self.assertEqual(
            data["roster_errors"],
            ["Dup commander cannot fill multiple slots: Commander, Heavy"],
        )

    def test_list_reports_the_duplicate_player(self) -> None:
        row = self.list_row(self.team)
        self.assertIs(row["is_valid_roster"], False)
        self.assertEqual(
            row["roster_errors"],
            ["Dup commander cannot fill multiple slots: Commander, Heavy"],
        )


class TestWriteResponses(_RosterApiTestCase):
    def test_create_response_reports_an_empty_roster(self) -> None:
        response = self.api.post(reverse("team-list"), {"name": "Fresh"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertIs(data["is_valid_roster"], False)
        self.assertEqual(
            data["roster_errors"],
            [
                "missing Commander",
                "missing Heavy",
                "missing Scout 1",
                "missing Scout 2",
                "missing Medic",
                "missing Ammo",
            ],
        )

    def test_slots_response_reflects_the_cleared_slot(self) -> None:
        team = self.owned_team("Slots")
        body = {
            "slot_commander": None,
            "slot_heavy": team.slot_heavy_id,
            "slot_scout_1": team.slot_scout_1_id,
            "slot_scout_2": team.slot_scout_2_id,
            "slot_medic": team.slot_medic_id,
            "slot_ammo": team.slot_ammo_id,
        }
        response = self.api.put(
            reverse("team-slots", args=[team.pk]), body, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertIs(data["is_valid_roster"], False)
        self.assertEqual(data["roster_errors"], ["missing Commander"])


class TestListQueryCount(_RosterApiTestCase):
    def test_list_query_count_does_not_grow_with_teams(self) -> None:
        self.owned_team("Q1")
        baseline = self._count_list_queries()
        for prefix in ("Q2", "Q3", "Q4"):
            self.owned_team(prefix)
        self.assertEqual(self._count_list_queries(), baseline)

    def _count_list_queries(self) -> int:
        with CaptureQueriesContext(connection) as ctx:
            response = self.api.get(reverse("team-list"))
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)
