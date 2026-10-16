"""ANG-01b -- GameRound analytics actions (seam contract §6 #3/#4/#5 + events fix).

``/api/rounds/<pk>/missile-log/`` upgrades the round to ``combat``,
``/heatmap/`` and ``/events/`` to ``full``, and ``/report/`` streams a raw PDF
with no fidelity upgrade. Fidelity is asserted as real persisted state on
batch-saved (``scores``-tier) rounds that carry a roster snapshot, so
``ensure_fidelity`` actually runs.

Every simulating test caps ``BatchSimulator.ROUND_TICKS``.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.text import slugify
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import GameRound
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_batch_tasks import _make_minimal_arena_map

User = get_user_model()

_FAST_TICKS = 40

_MISSILE_LOG_KEYS = {"round_id", "fired", "hit", "efficiency", "rows"}
_HEATMAP_KEYS = {
    "round_id",
    "has_map",
    "arena_map",
    "zone_size",
    "processed_image_url",
    "cell_occupancy_json",
    "player_roster",
}
_ROSTER_ENTRY_KEYS = {"id", "name", "role", "team_color"}


def _saved_round(red, blue, *, seed: int = 12345, arena_map=None) -> GameRound:
    """A real batch-saved round: ``scores`` fidelity + roster snapshot."""
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        rounds = BatchSimulator().save_games(
            red, blue, [[seed, False]], 1, arena_map=arena_map
        )
    game_round = GameRound.objects.get(pk=rounds[0].pk)
    assert game_round.fidelity == "scores"
    assert game_round.roster_snapshot_json is not None
    return game_round


class _AnalyticsTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01b-analytics-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, _ = make_team_with_slots("AnaRed")
        self.blue, _ = make_team_with_slots("AnaBlue")

    def get(self, url: str):
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            return self.api.get(url)

    def foreign_round(self) -> GameRound:
        return GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
            manager=self.user_b,
        )

    def assert_404(self, response) -> None:
        self.assertEqual(response.status_code, 404, response.content)
        errors = response.json()["errors"]
        self.assertEqual(errors[0]["code"], "not_found")
        self.assertIsNone(errors[0]["field"])

    def assert_405(self, response) -> None:
        self.assertEqual(response.status_code, 405, response.content)
        self.assertEqual(response.json()["errors"][0]["code"], "method_not_allowed")


class TestRouteNames(TestCase):
    def test_action_route_names(self) -> None:
        self.assertEqual(
            reverse("gameround-missile-log", args=[7]), "/api/rounds/7/missile-log/"
        )
        self.assertEqual(
            reverse("gameround-heatmap", args=[7]), "/api/rounds/7/heatmap/"
        )
        self.assertEqual(reverse("gameround-report", args=[7]), "/api/rounds/7/report/")
        self.assertEqual(reverse("gameround-events", args=[7]), "/api/rounds/7/events/")


# ---------------------------------------------------------------------------
# missile-log
# ---------------------------------------------------------------------------


class TestMissileLog(_AnalyticsTestCase):
    def test_data_key_set(self) -> None:
        game_round = _saved_round(self.red, self.blue)
        response = self.get(f"/api/rounds/{game_round.pk}/missile-log/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        data = body["data"]
        self.assertEqual(set(data), _MISSILE_LOG_KEYS)
        self.assertEqual(data["round_id"], game_round.pk)
        self.assertIsInstance(data["rows"], list)
        self.assertGreaterEqual(data["fired"], data["hit"])

    def test_upgrades_scores_round_to_combat(self) -> None:
        game_round = _saved_round(self.red, self.blue)
        self.get(f"/api/rounds/{game_round.pk}/missile-log/")
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "combat")

    def test_hand_built_round_without_snapshot_is_200(self) -> None:
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        response = self.get(f"/api/rounds/{game_round.pk}/missile-log/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["fired"], 0)
        self.assertEqual(response.json()["data"]["rows"], [])

    def test_foreign_round_is_404(self) -> None:
        self.assert_404(self.get(f"/api/rounds/{self.foreign_round().pk}/missile-log/"))

    def test_unknown_round_is_404(self) -> None:
        self.assert_404(self.get("/api/rounds/999999/missile-log/"))

    def test_post_is_405(self) -> None:
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.assert_405(
            self.api.post(
                f"/api/rounds/{game_round.pk}/missile-log/", {}, format="json"
            )
        )


# ---------------------------------------------------------------------------
# heatmap
# ---------------------------------------------------------------------------


class TestHeatmap(_AnalyticsTestCase):
    def test_data_key_set_without_map(self) -> None:
        game_round = _saved_round(self.red, self.blue)
        response = self.get(f"/api/rounds/{game_round.pk}/heatmap/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(set(data), _HEATMAP_KEYS)
        self.assertEqual(data["round_id"], game_round.pk)
        self.assertFalse(data["has_map"])
        self.assertIsNone(data["arena_map"])
        self.assertIsNone(data["processed_image_url"])
        self.assertIsInstance(data["cell_occupancy_json"], dict)
        self.assertEqual(len(data["player_roster"]), 12)
        for entry in data["player_roster"]:
            self.assertEqual(set(entry), _ROSTER_ENTRY_KEYS)

    def test_roster_is_red_then_blue(self) -> None:
        game_round = _saved_round(self.red, self.blue)
        roster = self.get(f"/api/rounds/{game_round.pk}/heatmap/").json()["data"][
            "player_roster"
        ]
        colors = [entry["team_color"] for entry in roster]
        self.assertEqual(colors, sorted(colors, key=lambda c: 0 if c == "red" else 1))

    def test_with_map_flattens_arena_map(self) -> None:
        arena_map = _make_minimal_arena_map("Ang01bHeatMap")
        game_round = _saved_round(self.red, self.blue, arena_map=arena_map)
        response = self.get(f"/api/rounds/{game_round.pk}/heatmap/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertTrue(data["has_map"])
        self.assertEqual(
            data["arena_map"], {"id": arena_map.pk, "name": arena_map.name}
        )
        self.assertIsInstance(data["processed_image_url"], str)
        self.assertTrue(data["processed_image_url"])

    def test_upgrades_scores_round_to_full(self) -> None:
        game_round = _saved_round(self.red, self.blue)
        self.get(f"/api/rounds/{game_round.pk}/heatmap/")
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "full")

    def test_foreign_round_is_404(self) -> None:
        self.assert_404(self.get(f"/api/rounds/{self.foreign_round().pk}/heatmap/"))

    def test_unknown_round_is_404(self) -> None:
        self.assert_404(self.get("/api/rounds/999999/heatmap/"))

    def test_delete_is_405(self) -> None:
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.assert_405(self.api.delete(f"/api/rounds/{game_round.pk}/heatmap/"))


# ---------------------------------------------------------------------------
# events -- fidelity regression fix
# ---------------------------------------------------------------------------


class TestEventsFidelityFix(_AnalyticsTestCase):
    def test_events_upgrades_scores_round_to_full(self) -> None:
        """Regression: before ANG-01b a ``scores`` round served an empty list."""
        game_round = _saved_round(self.red, self.blue)
        response = self.get(f"/api/rounds/{game_round.pk}/events/")
        self.assertEqual(response.status_code, 200, response.content)
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "full")
        self.assertEqual(response.json()["meta"]["count"], game_round.events.count())

    def test_events_foreign_round_is_404(self) -> None:
        self.assert_404(self.get(f"/api/rounds/{self.foreign_round().pk}/events/"))


# ---------------------------------------------------------------------------
# report (raw PDF)
# ---------------------------------------------------------------------------


class TestRoundReport(_AnalyticsTestCase):
    def test_report_is_raw_pdf_attachment(self) -> None:
        game_round = _saved_round(self.red, self.blue)
        response = self.get(f"/api/rounds/{game_round.pk}/report/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        expected = (
            f'attachment; filename="round-{game_round.pk}-'
            f"{slugify(game_round.team_red.name)}-vs-"
            f'{slugify(game_round.team_blue.name)}.pdf"'
        )
        self.assertEqual(response["Content-Disposition"], expected)
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_report_is_not_enveloped(self) -> None:
        game_round = _saved_round(self.red, self.blue)
        response = self.get(f"/api/rounds/{game_round.pk}/report/")
        self.assertFalse(response.content.lstrip().startswith(b"{"))

    def test_report_does_not_upgrade_fidelity(self) -> None:
        game_round = _saved_round(self.red, self.blue)
        self.get(f"/api/rounds/{game_round.pk}/report/")
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "scores")

    def test_report_slug_falls_back_for_unsluggable_names(self) -> None:
        self.red.name = "!!!"
        self.red.save()
        self.blue.name = "???"
        self.blue.save()
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        response = self.get(f"/api/rounds/{game_round.pk}/report/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="round-{game_round.pk}-red-vs-blue.pdf"',
        )

    def test_foreign_round_is_404(self) -> None:
        self.assert_404(self.get(f"/api/rounds/{self.foreign_round().pk}/report/"))

    def test_unknown_round_is_404(self) -> None:
        self.assert_404(self.get("/api/rounds/999999/report/"))

    def test_post_is_405(self) -> None:
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.assert_405(
            self.api.post(f"/api/rounds/{game_round.pk}/report/", {}, format="json")
        )

    def test_anonymous_is_403(self) -> None:
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        response = APIClient().get(f"/api/rounds/{game_round.pk}/report/")
        self.assertEqual(response.status_code, 403)
