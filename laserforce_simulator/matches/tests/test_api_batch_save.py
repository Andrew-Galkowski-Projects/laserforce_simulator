"""ANG-01b -- batch save over the API (seam contract §6 #11/#12 + batch scoping).

``POST /api/simulate-batch/<job_id>/save/`` binds ``SaveBatchGamesForm``,
resolves the two teams through ``resolve_batch_teams`` (ownership-scoped; a
foreign id reads exactly like a missing one), requires the batch Job to be
``SUCCESS`` (else 409 ``job_not_complete``), slices the category's seeds and
enqueues ``save_games_task`` stamped with the requesting Account. ``GET
/api/simulate-batch/save/<save_job_id>/`` returns the HTML save-status JSON.

Runs under eager Celery (project conftest). Every simulating call caps
``BatchSimulator.ROUND_TICKS``.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import GameRound
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Team, get_free_agents_team

User = get_user_model()

_FAST_TICKS = 40
_UNKNOWN_JOB = "00000000-0000-0000-0000-000000000000"
_SAVE_STATUS_KEYS = {"status", "error", "round_ids"}


class _StubAsyncResult:
    """A hand-rolled ``AsyncResult`` stand-in carrying a fixed state/result."""

    def __init__(self, state: str, result=None) -> None:
        self.state = state
        self.result = result
        self.info = result

    def get(self, *args, **kwargs):  # pragma: no cover - defensive
        return self.result


def _stub_async_result(state: str, result=None):
    def _factory(job_id, *args, **kwargs):
        return _StubAsyncResult(state, result)

    return _factory


class _BatchSaveTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01b-batchsave-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, _ = make_team_with_slots("BSRed")
        self.blue, _ = make_team_with_slots("BSBlue")

    def save_url(self, job_id: str) -> str:
        return f"/api/simulate-batch/{job_id}/save/"

    def payload(self, **overrides) -> dict:
        data = {
            "team_red": self.red.pk,
            "team_blue": self.blue.pk,
            "game_type": "avg",
            "n": 1,
        }
        data.update(overrides)
        return data

    def post_save(self, job_id: str, payload: dict):
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            return self.api.post(self.save_url(job_id), payload, format="json")

    def errors(self, response, status: int) -> list[dict]:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        return body["errors"]

    def codes_fields(self, response, status: int = 400):
        return [(e["code"], e["field"]) for e in self.errors(response, status)]

    def foreign_team(self) -> Team:
        team, _ = make_team_with_slots("BSForeign")
        team.manager = self.user_b
        team.save()
        return team


class TestRouteNames(TestCase):
    def test_route_names(self) -> None:
        self.assertEqual(
            reverse("api_save_batch_games", args=["abc"]),
            "/api/simulate-batch/abc/save/",
        )
        self.assertEqual(
            reverse("api_save_batch_status", args=["abc"]),
            "/api/simulate-batch/save/abc/",
        )


# ---------------------------------------------------------------------------
# End-to-end (eager): batch POST -> save POST -> save status
# ---------------------------------------------------------------------------


class TestBatchSaveEndToEnd(_BatchSaveTestCase):
    def _run_batch(self) -> str:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.api.post(
                "/api/simulate-batch/",
                {"team_red": self.red.pk, "team_blue": self.blue.pk, "n": 4},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]["job_id"]

    def test_save_returns_200_job_id_then_status_complete(self) -> None:
        batch_job = self._run_batch()
        response = self.post_save(batch_job, self.payload(n=1))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertEqual(set(body["data"]), {"job_id"})
        save_job = body["data"]["job_id"]
        self.assertIsInstance(save_job, str)
        self.assertTrue(save_job)

        status = self.api.get(f"/api/simulate-batch/save/{save_job}/")
        self.assertEqual(status.status_code, 200, status.content)
        data = status.json()["data"]
        self.assertEqual(set(data), _SAVE_STATUS_KEYS)
        self.assertEqual(data["status"], "complete")
        self.assertIsNone(data["error"])
        self.assertEqual(len(data["round_ids"]), 1)

    def test_saved_rounds_are_owned_by_requester(self) -> None:
        batch_job = self._run_batch()
        save_job = self.post_save(batch_job, self.payload()).json()["data"]["job_id"]
        round_ids = self.api.get(f"/api/simulate-batch/save/{save_job}/").json()[
            "data"
        ]["round_ids"]
        self.assertTrue(round_ids)
        for game_round in GameRound.objects.filter(id__in=round_ids):
            self.assertEqual(game_round.manager_id, self.user_a.pk)

    def test_status_matches_html_save_status_json(self) -> None:
        batch_job = self._run_batch()
        save_job = self.post_save(batch_job, self.payload()).json()["data"]["job_id"]
        api_data = self.api.get(f"/api/simulate-batch/save/{save_job}/").json()["data"]
        html = Client()
        html.force_login(self.user_a)
        html_data = html.get(reverse("save_batch_status", args=[save_job])).json()
        self.assertEqual(api_data, html_data)


# ---------------------------------------------------------------------------
# Job state + seed handling (stubbed AsyncResult)
# ---------------------------------------------------------------------------


class TestBatchSaveJobState(_BatchSaveTestCase):
    def test_unknown_job_is_409_job_not_complete(self) -> None:
        errors = self.errors(self.post_save(_UNKNOWN_JOB, self.payload()), 409)
        self.assertEqual(
            errors,
            [
                {
                    "code": "job_not_complete",
                    "message": "The batch job is not complete.",
                    "field": None,
                }
            ],
        )

    def test_failed_job_is_409(self) -> None:
        with patch(
            "matches.api_views.AsyncResult",
            _stub_async_result("FAILURE", ValueError("boom")),
        ):
            response = self.post_save("failed-job", self.payload())
        errors = self.errors(response, 409)
        self.assertEqual(errors[0]["code"], "job_not_complete")

    def test_running_job_is_409(self) -> None:
        with patch(
            "matches.api_views.AsyncResult",
            _stub_async_result("PROGRESS", {"completed": 1, "total": 4}),
        ):
            response = self.post_save("running-job", self.payload())
        self.assertEqual(self.errors(response, 409)[0]["code"], "job_not_complete")

    def test_empty_seeds_is_400(self) -> None:
        with patch(
            "matches.api_views.AsyncResult",
            _stub_async_result("SUCCESS", {"avg_seeds": [], "outlier_seeds": []}),
        ):
            response = self.post_save("done-job", self.payload())
        self.assertEqual(
            self.errors(response, 400),
            [
                {
                    "code": "invalid",
                    "message": "No saved seeds for this category.",
                    "field": None,
                }
            ],
        )

    def test_missing_category_key_is_400(self) -> None:
        with patch(
            "matches.api_views.AsyncResult",
            _stub_async_result("SUCCESS", {"avg_seeds": [[1, False]]}),
        ):
            response = self.post_save("done-job", self.payload(game_type="outlier"))
        self.assertEqual(
            self.errors(response, 400)[0]["message"],
            "No saved seeds for this category.",
        )

    def test_seeds_sliced_to_n(self) -> None:
        seeds = [[101, False], [202, True], [303, False]]
        with patch(
            "matches.api_views.AsyncResult",
            _stub_async_result("SUCCESS", {"avg_seeds": seeds, "outlier_seeds": []}),
        ):
            response = self.post_save("done-job", self.payload(n=2))
        self.assertEqual(response.status_code, 200, response.content)
        save_job = response.json()["data"]["job_id"]
        data = self.api.get(f"/api/simulate-batch/save/{save_job}/").json()["data"]
        self.assertEqual(data["status"], "complete")
        self.assertEqual(len(data["round_ids"]), 2)

    def test_outlier_category_saves(self) -> None:
        with patch(
            "matches.api_views.AsyncResult",
            _stub_async_result(
                "SUCCESS", {"avg_seeds": [], "outlier_seeds": [[909, False]]}
            ),
        ):
            response = self.post_save("done-job", self.payload(game_type="outlier"))
        self.assertEqual(response.status_code, 200, response.content)

    def test_team_check_runs_before_job_check(self) -> None:
        foreign = self.foreign_team()
        errors = self.errors(
            self.post_save(_UNKNOWN_JOB, self.payload(team_blue=foreign.pk)), 400
        )
        self.assertEqual(errors[0]["message"], f"Team {foreign.pk} does not exist.")

    def test_unknown_save_status_is_running(self) -> None:
        response = self.api.get(f"/api/simulate-batch/save/{_UNKNOWN_JOB}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"],
            {"status": "running", "error": None, "round_ids": []},
        )


# ---------------------------------------------------------------------------
# Form + team errors
# ---------------------------------------------------------------------------


class TestBatchSaveErrors(_BatchSaveTestCase):
    def test_missing_fields_are_required(self) -> None:
        response = self.post_save(_UNKNOWN_JOB, {})
        self.assertEqual(
            sorted(self.codes_fields(response), key=lambda cf: cf[1]),
            [
                ("required", "game_type"),
                ("required", "n"),
                ("required", "team_blue"),
                ("required", "team_red"),
            ],
        )

    def test_bad_int_is_invalid(self) -> None:
        self.assertEqual(
            self.codes_fields(self.post_save(_UNKNOWN_JOB, self.payload(team_red="x"))),
            [("invalid", "team_red")],
        )

    def test_n_zero_is_min_value(self) -> None:
        self.assertEqual(
            self.codes_fields(self.post_save(_UNKNOWN_JOB, self.payload(n=0))),
            [("min_value", "n")],
        )

    def test_n_eleven_is_max_value(self) -> None:
        self.assertEqual(
            self.codes_fields(self.post_save(_UNKNOWN_JOB, self.payload(n=11))),
            [("max_value", "n")],
        )

    def test_bad_game_type_is_invalid_choice(self) -> None:
        self.assertEqual(
            self.codes_fields(
                self.post_save(_UNKNOWN_JOB, self.payload(game_type="best"))
            ),
            [("invalid_choice", "game_type")],
        )

    def test_same_team_is_invalid(self) -> None:
        errors = self.errors(
            self.post_save(_UNKNOWN_JOB, self.payload(team_blue=self.red.pk)), 400
        )
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "A team cannot play against itself!",
                    "field": None,
                }
            ],
        )

    def test_missing_team_is_does_not_exist(self) -> None:
        errors = self.errors(
            self.post_save(_UNKNOWN_JOB, self.payload(team_red=999999)), 400
        )
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "Team 999999 does not exist.",
                    "field": None,
                }
            ],
        )

    def test_foreign_team_reads_like_missing(self) -> None:
        foreign = self.foreign_team()
        errors = self.errors(
            self.post_save(_UNKNOWN_JOB, self.payload(team_red=foreign.pk)), 400
        )
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": f"Team {foreign.pk} does not exist.",
                    "field": None,
                }
            ],
        )

    def test_roster_error_is_invalid(self) -> None:
        empty = Team.objects.create(name="BS No Slots")
        errors = self.errors(
            self.post_save(_UNKNOWN_JOB, self.payload(team_blue=empty.pk)), 400
        )
        self.assertEqual(errors[0]["code"], "invalid")
        self.assertIsNone(errors[0]["field"])
        self.assertTrue(
            errors[0]["message"].startswith(f"{empty.name} has an invalid roster: "),
            errors[0]["message"],
        )

    def test_free_agents_team_is_roster_error(self) -> None:
        fa = get_free_agents_team()
        errors = self.errors(
            self.post_save(_UNKNOWN_JOB, self.payload(team_red=fa.pk)), 400
        )
        self.assertTrue(
            errors[0]["message"].startswith(f"{fa.name} has an invalid roster")
        )

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        response = APIClient().post(
            self.save_url(_UNKNOWN_JOB), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, 401)


# ---------------------------------------------------------------------------
# Batch POST scoping regression (ANG-01a follow-up closed)
# ---------------------------------------------------------------------------


class TestSimulateBatchPostScoping(_BatchSaveTestCase):
    def test_foreign_team_in_batch_post_reads_like_missing(self) -> None:
        foreign = self.foreign_team()
        response = self.api.post(
            "/api/simulate-batch/",
            {"team_red": foreign.pk, "team_blue": self.blue.pk, "n": 2},
            format="json",
        )
        self.assertEqual(
            self.errors(response, 400),
            [
                {
                    "code": "invalid",
                    "message": f"Team {foreign.pk} does not exist.",
                    "field": None,
                }
            ],
        )

    def test_same_team_message_unchanged(self) -> None:
        response = self.api.post(
            "/api/simulate-batch/",
            {"team_red": self.red.pk, "team_blue": self.red.pk, "n": 2},
            format="json",
        )
        self.assertEqual(
            self.errors(response, 400)[0]["message"],
            "A team cannot play against itself!",
        )

    def test_roster_error_message_unchanged(self) -> None:
        empty = Team.objects.create(name="Batch No Slots")
        response = self.api.post(
            "/api/simulate-batch/",
            {"team_red": self.red.pk, "team_blue": empty.pk, "n": 2},
            format="json",
        )
        self.assertTrue(
            self.errors(response, 400)[0]["message"].startswith(
                f"{empty.name} has an invalid roster: "
            )
        )
