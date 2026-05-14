import pytest
from unittest.mock import patch
from django.test import Client
from django.urls import reverse, NoReverseMatch

from matches.models import GameRound, Match
from matches.simulation import ResourceBasedSimulator
from matches.tests.conftest import make_team_with_slots


@pytest.mark.django_db
class TestSingleRoundRemoval:
    def test_single_round_detail_url_removed(self):
        """The legacy /matches/round/<id>/ URL should not exist."""
        with pytest.raises(NoReverseMatch):
            reverse("single_round_detail", kwargs={"round_id": 1})

    def test_create_single_round_always_creates_game_round(self):
        """POST to create_single_round must always produce a GameRound."""
        red, _ = make_team_with_slots("Red")
        blue, _ = make_team_with_slots("Blue")
        client = Client()
        before = GameRound.objects.count()
        with patch.object(ResourceBasedSimulator, "ROUND_SECONDS", 20):
            response = client.post(
                reverse("create_single_round"),
                {"team_red": red.id, "team_blue": blue.id},
            )
        assert GameRound.objects.count() == before + 1
        assert response.status_code == 302

    def test_create_single_round_same_team_returns_form(self):
        """Submitting the same team for both sides must return the form (200), no GameRound."""
        team, _ = make_team_with_slots("SameTeam")
        client = Client()
        before = GameRound.objects.count()
        response = client.post(
            reverse("create_single_round"),
            {"team_red": team.id, "team_blue": team.id},
        )
        assert response.status_code == 200
        assert GameRound.objects.count() == before

    def test_match_list_has_no_single_rounds_context(self):
        """match_list view must not pass single_rounds to the template context."""
        client = Client()
        response = client.get(reverse("match_list"))
        assert response.status_code == 200
        assert "single_rounds" not in response.context

    def test_team_match_history_has_no_single_rounds_context(self):
        """team_match_history view must not pass single_rounds to the template context."""
        team, _ = make_team_with_slots("History")
        client = Client()
        response = client.get(
            reverse("team_match_history", kwargs={"team_id": team.id})
        )
        assert response.status_code == 200
        assert "single_rounds" not in response.context


@pytest.mark.django_db
class TestCreateMatchView:
    def test_create_match_same_team_returns_form(self):
        """Submitting the same team for both sides must return the form (200), no Match created."""
        team, _ = make_team_with_slots("SameMatch")
        client = Client()
        before = Match.objects.count()
        response = client.post(
            reverse("create_match"),
            {"team_red": team.id, "team_blue": team.id, "match_type": "friendly"},
        )
        assert response.status_code == 200
        assert Match.objects.count() == before
