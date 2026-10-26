"""ANG-06-8a (#373): the read-only fields the Angular Player pages need.

``team_name`` on every ``PlayerSerializer`` payload (the Players list's Team
column) and ``manager`` on the Team detail (player detail's *This is me* rule).
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from conftest import get_shared_manager
from teams.models import Player, Team, get_free_agents_team


@pytest.mark.django_db
class TestPlayerTeamName:
    def setup_method(self) -> None:
        self.client = APIClient()
        self.client.force_login(get_shared_manager())
        self.team = Team.objects.create(name="Red Dragons")
        self.player = Player.objects.create(team=self.team, name="Ann")

    def test_list_rows_carry_the_team_name(self) -> None:
        rows = self.client.get("/api/players/").json()["data"]
        row = next(r for r in rows if r["id"] == self.player.pk)
        assert row["team_name"] == "Red Dragons"

    def test_detail_carries_the_team_name(self) -> None:
        data = self.client.get(f"/api/players/{self.player.pk}/").json()["data"]
        assert data["team_name"] == "Red Dragons"

    def test_a_free_agent_reads_free_agents(self) -> None:
        agent = Player.objects.create(team=get_free_agents_team(), name="Loner")
        rows = self.client.get("/api/players/", {"page_size": 100}).json()["data"]
        row = next(r for r in rows if r["id"] == agent.pk)
        assert row["team_name"] == "Free Agents"

    def test_team_detail_nested_players_carry_it(self) -> None:
        data = self.client.get(f"/api/teams/{self.team.pk}/").json()["data"]
        assert [p["team_name"] for p in data["players"]] == ["Red Dragons"]

    def test_the_self_insert_payload_carries_it(self) -> None:
        user = get_shared_manager()
        self.team.manager = user
        self.team.save()
        user.self_insert = self.player
        user.save()
        me = self.client.get("/api/accounts/me/").json()["data"]
        assert me["self_insert"]["team_name"] == "Red Dragons"

    def test_team_name_is_read_only(self) -> None:
        response = self.client.patch(
            f"/api/players/{self.player.pk}/", {"team_name": "Hacked"}, format="json"
        )
        assert response.status_code == 200
        self.team.refresh_from_db()
        assert self.team.name == "Red Dragons"
        assert response.json()["data"]["team_name"] == "Red Dragons"


@pytest.mark.django_db
class TestTeamManager:
    def setup_method(self) -> None:
        self.user = get_shared_manager()
        self.client = APIClient()
        self.client.force_login(self.user)

    def test_a_managed_team_names_its_manager(self) -> None:
        team = Team.objects.create(name="Mine", manager=self.user)
        data = self.client.get(f"/api/teams/{team.pk}/").json()["data"]
        assert data["manager"] == self.user.pk

    def test_an_unmanaged_team_has_a_null_manager(self) -> None:
        team = Team.objects.create(name="Nobody's")
        data = self.client.get(f"/api/teams/{team.pk}/").json()["data"]
        assert "manager" in data
        assert data["manager"] is None

    def test_the_team_list_is_unchanged(self) -> None:
        Team.objects.create(name="Mine", manager=self.user)
        rows = self.client.get("/api/teams/").json()["data"]
        assert rows
        assert all("manager" not in r for r in rows)

    def test_manager_is_read_only(self) -> None:
        other = get_user_model().objects.create(email="other@example.com")
        team = Team.objects.create(name="Mine", manager=self.user)
        response = self.client.patch(
            f"/api/teams/{team.pk}/", {"manager": other.pk}, format="json"
        )
        assert response.status_code == 200
        team.refresh_from_db()
        assert team.manager_id == self.user.pk
