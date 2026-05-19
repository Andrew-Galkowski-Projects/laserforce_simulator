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


@pytest.mark.django_db
class TestM1EventLogWindowing:
    """M-1: the HTML event log no longer server-renders one DOM row per
    event (the ~20k-node blow-up). Events and players are emitted once as
    JSON; the timeline is windowed client-side. These tests pin the
    server-side contract the client windowing depends on.
    """

    # Keys every serialized event dict must carry (the client reads only
    # these — see game_round_events.html). Keeping this list in a test
    # makes the JSON contract explicit and fails loudly if a key is
    # renamed out from under the template.
    _EVENT_KEYS = {
        "type",
        "ts",
        "tf",
        "icon",
        "desc",
        "pts",
        "aid",
        "an",
        "at",
        "tid",
        "tn",
        "tt",
        "meta",
    }
    _PLAYER_KEYS = {"id", "name", "team", "role", "sl", "ss"}

    def _round_with_events(self):
        red, _ = make_team_with_slots("M1Red")
        blue, _ = make_team_with_slots("M1Blue")
        # Short round for speed (mirrors the TIME-01 view-test pattern).
        with patch.object(ResourceBasedSimulator, "ROUND_TICKS", 40):
            gr = ResourceBasedSimulator().simulate_single_round_detailed(red, blue)
        return gr

    def test_view_emits_json_not_per_event_rows(self):
        """The response carries the JSON script blocks and does NOT
        server-render a ``data-event-type`` div per event."""
        gr = self._round_with_events()
        assert gr.events.count() > 0, "fixture round produced no events"

        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        assert resp.status_code == 200

        body = resp.content.decode()
        assert 'id="events-data"' in body, "events JSON script block missing"
        assert 'id="players-data"' in body, "players JSON script block missing"
        # The old design emitted one `data-event-type="..."` attribute per
        # event into the server HTML. The windowed design renders rows in
        # JS, so no such attribute should appear in the served markup.
        assert "data-event-type=" not in body, (
            "events are still being server-rendered as DOM rows — the M-1 "
            "DOM blow-up is not fixed"
        )

    def test_events_data_matches_db_and_has_full_shape(self):
        gr = self._round_with_events()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        events_data = resp.context["events_data"]
        assert len(events_data) == gr.events.count()
        # Events are emitted in chronological (timestamp) order.
        ts = [e["ts"] for e in events_data]
        assert ts == sorted(ts)
        sample = events_data[0]
        assert (
            set(sample) == self._EVENT_KEYS
        ), f"event JSON shape drifted: {set(sample) ^ self._EVENT_KEYS}"
        assert isinstance(sample["meta"], dict)
        # A targetless event must use the -1 sentinel the client expects.
        assert all(e["tid"] == -1 or isinstance(e["tid"], int) for e in events_data)

    def test_players_data_matches_round_states(self):
        gr = self._round_with_events()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        players_data = resp.context["players_data"]
        assert len(players_data) == gr.player_states.count()
        for p in players_data:
            assert set(p) == self._PLAYER_KEYS
            assert p["team"] in ("red", "blue")

    def test_empty_round_renders_without_error(self):
        """A round with zero events still renders 200 with an empty list
        (the client must handle the no-events path)."""
        gr = self._round_with_events()
        gr.events.all().delete()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        assert resp.status_code == 200
        assert resp.context["events_data"] == []
        assert "data-event-type=" not in resp.content.decode()
