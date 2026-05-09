import pytest
from rest_framework.test import APIClient

from teams.models import Player, Team


@pytest.mark.django_db
class TestTeamAPI:
    """API-01: Read-only REST endpoints for /api/teams/."""

    def setup_method(self):
        self.client = APIClient()
        self.team = Team.objects.create(name="API Test Team", wins=3, losses=1)
        self.player = Player.objects.create(team=self.team, name="API Player")

    def test_list_returns_200(self):
        response = self.client.get("/api/teams/")
        assert response.status_code == 200

    def test_list_is_paginated(self):
        data = self.client.get("/api/teams/").json()
        assert "count" in data
        assert "results" in data

    def test_detail_returns_200(self):
        response = self.client.get(f"/api/teams/{self.team.pk}/")
        assert response.status_code == 200

    def test_detail_includes_players(self):
        data = self.client.get(f"/api/teams/{self.team.pk}/").json()
        assert "players" in data
        assert any(p["name"] == "API Player" for p in data["players"])

    def test_detail_includes_expected_fields(self):
        data = self.client.get(f"/api/teams/{self.team.pk}/").json()
        for field in ("id", "name", "wins", "losses", "created_date"):
            assert field in data, f"Missing field: {field}"

    def test_detail_404_for_missing(self):
        assert self.client.get("/api/teams/99999/").status_code == 404

    def test_write_methods_not_allowed(self):
        assert (
            self.client.post("/api/teams/", {"name": "X"}, format="json").status_code
            == 405
        )
        assert (
            self.client.patch(
                f"/api/teams/{self.team.pk}/", {}, format="json"
            ).status_code
            == 405
        )
        assert self.client.delete(f"/api/teams/{self.team.pk}/").status_code == 405


@pytest.mark.django_db
class TestPlayerAPI:
    """API-01: Read-only REST endpoints for /api/players/."""

    def setup_method(self):
        self.client = APIClient()
        self.team = Team.objects.create(name="Player API Team")
        self.player = Player.objects.create(
            team=self.team, name="Player Alpha", accuracy=70
        )

    def test_list_returns_200(self):
        assert self.client.get("/api/players/").status_code == 200

    def test_list_is_paginated(self):
        data = self.client.get("/api/players/").json()
        assert "count" in data and "results" in data

    def test_detail_returns_200(self):
        assert self.client.get(f"/api/players/{self.player.pk}/").status_code == 200

    def test_detail_includes_all_stats(self):
        data = self.client.get(f"/api/players/{self.player.pk}/").json()
        for stat in ("accuracy", "survival", "stamina", "speed", "decision_making"):
            assert stat in data, f"Missing stat: {stat}"

    def test_detail_404_for_missing(self):
        assert self.client.get("/api/players/99999/").status_code == 404

    def test_write_methods_not_allowed(self):
        assert self.client.post("/api/players/", {}, format="json").status_code == 405
