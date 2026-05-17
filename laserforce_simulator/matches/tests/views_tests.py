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
        # TIME-01: ROUND_SECONDS → ROUND_TICKS; 20 s → 40 ticks (short round
        # for test speed).
        with patch.object(ResourceBasedSimulator, "ROUND_TICKS", 40):
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


@pytest.mark.django_db
class TestSim08BatchSideAdvantageView:
    """SIM-08: the batch-simulate view exposes the physical-side advantage
    panel alongside the de-flipped team-position results.
    """

    def test_batch_simulate_renders_side_advantage(self):
        """POST two valid rosters → 200 and ``side_advantage`` in results.

        The view stashes ``avg_seeds``/``outlier_seeds`` in the session and
        passes ``results`` (including the new ``side_advantage`` sub-dict) to
        the template context. Keep this light per existing view-test patterns.
        """
        red, _ = make_team_with_slots("Sim08ViewRed")
        blue, _ = make_team_with_slots("Sim08ViewBlue")
        client = Client()

        response = client.post(
            reverse("simulate_batch"),
            {"team_red": red.id, "team_blue": blue.id, "n": "10"},
        )

        assert response.status_code == 200
        assert (
            "results" in response.context
        ), "batch view must pass aggregate 'results' to the template context"
        results = response.context["results"]
        assert "side_advantage" in results, (
            "results must include the SIM-08 'side_advantage' physical-side "
            "panel data"
        )
        sa = results["side_advantage"]
        for key in (
            "red_side_wins",
            "blue_side_wins",
            "side_ties",
            "red_side_win_pct",
            "blue_side_win_pct",
            "avg_red_side_score",
            "avg_blue_side_score",
            "n",
        ):
            assert key in sa, f"side_advantage missing documented key {key!r}"
        assert sa["n"] == 10
        assert sa["red_side_wins"] + sa["blue_side_wins"] + sa["side_ties"] == 10
        # Team-position aggregates remain present and consistent.
        assert results["red_wins"] + results["blue_wins"] + results["ties"] == 10
