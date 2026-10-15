import pytest
from rest_framework.test import APIClient

from teams.models import Player, Team
from conftest import get_shared_manager


@pytest.mark.django_db
class TestTeamAPI:
    """API-01: Read-only REST endpoints for /api/teams/."""

    def setup_method(self):
        self.client = APIClient()
        self.client.force_login(get_shared_manager())
        self.team = Team.objects.create(name="API Test Team", wins=3, losses=1)
        self.player = Player.objects.create(team=self.team, name="API Player")

    def test_list_returns_200(self):
        response = self.client.get("/api/teams/")
        assert response.status_code == 200

    def test_list_is_paginated(self):
        body = self.client.get("/api/teams/").json()
        assert isinstance(body["data"], list)
        assert body["meta"]["count"] >= 1
        assert set(body["meta"]) == {"page", "page_size", "count", "total_pages"}

    def test_detail_returns_200(self):
        response = self.client.get(f"/api/teams/{self.team.pk}/")
        assert response.status_code == 200

    def test_detail_includes_players(self):
        data = self.client.get(f"/api/teams/{self.team.pk}/").json()["data"]
        assert "players" in data
        assert any(p["name"] == "API Player" for p in data["players"])

    def test_detail_includes_expected_fields(self):
        data = self.client.get(f"/api/teams/{self.team.pk}/").json()["data"]
        for field in ("id", "name", "wins", "losses", "created_date"):
            assert field in data, f"Missing field: {field}"

    def test_detail_404_for_missing(self):
        assert self.client.get("/api/teams/99999/").status_code == 404

    def test_put_and_delete_detail_not_allowed(self):
        """ANG-01a: POST list / PATCH detail are now valid writes (covered in
        ``test_api_writes.py``); PUT and DELETE on the detail route stay 405
        (no full-replace, no team delete)."""
        put = self.client.put(
            f"/api/teams/{self.team.pk}/", {"name": "X"}, format="json"
        )
        assert put.status_code == 405
        assert put.json()["errors"][0]["code"] == "method_not_allowed"
        delete = self.client.delete(f"/api/teams/{self.team.pk}/")
        assert delete.status_code == 405
        assert Team.objects.filter(pk=self.team.pk).exists()


@pytest.mark.django_db
class TestPlayerAPI:
    """API-01: Read-only REST endpoints for /api/players/."""

    def setup_method(self):
        self.client = APIClient()
        self.client.force_login(get_shared_manager())
        self.team = Team.objects.create(name="Player API Team")
        self.player = Player.objects.create(
            team=self.team, name="Player Alpha", accuracy=70
        )

    def test_list_returns_200(self):
        assert self.client.get("/api/players/").status_code == 200

    def test_list_is_paginated(self):
        body = self.client.get("/api/players/").json()
        assert isinstance(body["data"], list)
        assert body["meta"]["count"] >= 1

    def test_detail_returns_200(self):
        assert self.client.get(f"/api/players/{self.player.pk}/").status_code == 200

    def test_detail_includes_all_stats(self):
        data = self.client.get(f"/api/players/{self.player.pk}/").json()["data"]
        for stat in ("accuracy", "survival", "stamina", "speed", "decision_making"):
            assert stat in data, f"Missing stat: {stat}"

    def test_detail_404_for_missing(self):
        assert self.client.get("/api/players/99999/").status_code == 404

    def test_write_methods_not_allowed(self):
        assert self.client.post("/api/players/", {}, format="json").status_code == 405
