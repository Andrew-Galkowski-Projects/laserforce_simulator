"""API-03 — tests for the batch-simulate REST views + the Celery-state mapper.

Pinned by ``.claude/worktrees/api-03-seam-contract.md`` §6.4 (NEW test
classes), §5 (status mapping truth table), §8 (polling JSON shape per-key
source tables), §9 (POST response shapes) and §11 (REST views inherit
``AllowAny`` from REST_FRAMEWORK defaults). ANG-08 deleted the HTML batch
page, its JSON polls and the session save flow these tests also drove.

Runs under ``CELERY_TASK_ALWAYS_EAGER = True`` (project ``conftest.py``).
All names below are normative.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from matches.simulation import BatchSimulator
from matches.tests.conftest import FakeAsyncResult, make_team_with_slots
from conftest import get_shared_manager

# ---------------------------------------------------------------------------
# Local fixture: minimal ArenaMap.
# ---------------------------------------------------------------------------


def _make_minimal_arena_map(name: str = "Api03ViewMap"):
    from core.map_processing import compute_sight_lines
    from core.models import (
        ArenaMap,
        BaseSightLineConfig,
        MapBaseConfig,
        MapZoneConfig,
        SightLineConfig,
    )

    zone_size = 50
    zone_data = [[1] * 4 for _ in range(4)]
    arena_map = ArenaMap.objects.create(
        name=name, img_width=4 * zone_size, img_height=4 * zone_size
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        zone_data=zone_data,
        confirmed=True,
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="red",
        x_px=zone_size // 2,
        y_px=zone_size // 2,
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="blue",
        x_px=4 * zone_size - zone_size // 2,
        y_px=4 * zone_size - zone_size // 2,
    )
    SightLineConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        sight_data=compute_sight_lines(zone_data),
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="red", zone_size=zone_size, visible_cells=[]
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="blue", zone_size=zone_size, visible_cells=[]
    )
    return arena_map


_BATCH_POST_KEYS = frozenset(
    {
        "job_id",
        "team_red_id",
        "team_red_name",
        "team_blue_id",
        "team_blue_name",
        "arena_map_id",
        "n",
    }
)


_BATCH_STATUS_KEYS = frozenset(
    {
        "status",
        "completed",
        "total",
        "partial",
        "error",
        "team_red_id",
        "team_blue_id",
        "arena_map_id",
    }
)


# ---------------------------------------------------------------------------
# REST POST: /api/simulate-batch/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSimulateBatchAPIPost:
    """§6.4 — POST ``/api/simulate-batch/`` returns 200 + the locked JSON
    shape. Same-team rejection returns 400. Serializer validation failures
    return 400.
    """

    def test_post_returns_locked_json_shape(self) -> None:
        red, _ = make_team_with_slots("Api03APIPostR")
        blue, _ = make_team_with_slots("Api03APIPostB")
        client = APIClient()
        client.force_login(get_shared_manager())

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = client.post(
                "/api/simulate-batch/",
                {"team_red": red.id, "team_blue": blue.id, "n": 2},
                format="json",
            )

        assert response.status_code == 200, response.content
        envelope = response.json()
        # ANG-01a — success bodies are enveloped; ``data`` is byte-identical
        # to the pre-envelope bare JSON.
        assert envelope["meta"] == {}
        body = envelope["data"]
        assert set(body.keys()) == _BATCH_POST_KEYS, (
            f"REST POST JSON keys drifted: got {set(body.keys())!r}, "
            f"expected {_BATCH_POST_KEYS!r}"
        )
        assert isinstance(body["job_id"], str) and body["job_id"]
        assert body["team_red_id"] == red.id
        assert body["team_red_name"] == red.name
        assert body["team_blue_id"] == blue.id
        assert body["team_blue_name"] == blue.name
        assert body["arena_map_id"] is None
        assert body["n"] == 2

    def test_same_team_returns_400(self) -> None:
        red, _ = make_team_with_slots("Api03APISameTeamR")
        client = APIClient()
        client.force_login(get_shared_manager())

        response = client.post(
            "/api/simulate-batch/",
            {"team_red": red.id, "team_blue": red.id, "n": 2},
            format="json",
        )
        assert response.status_code == 400, (
            f"same-team POST must return 400; got {response.status_code}: "
            f"{response.content!r}"
        )
        # ANG-01a — the hand-built ``{"detail": ...}`` becomes an envelope
        # error with the same project-owned message.
        assert response.json() == {
            "errors": [
                {
                    "code": "invalid",
                    "message": "A team cannot play against itself!",
                    "field": None,
                }
            ],
            "meta": {},
        }

    def test_invalid_field_returns_400(self) -> None:
        client = APIClient()
        client.force_login(get_shared_manager())
        # Missing required field `team_blue` → serializer validation 400.
        response = client.post(
            "/api/simulate-batch/",
            {"team_red": 1, "n": 2},
            format="json",
        )
        assert response.status_code == 400
        errors = response.json()["errors"]
        assert {"code": "required", "field": "team_blue"} in [
            {"code": e["code"], "field": e["field"]} for e in errors
        ]

    def test_nonexistent_team_returns_400_envelope(self) -> None:
        red, _ = make_team_with_slots("Api03APIMissingR")
        client = APIClient()
        client.force_login(get_shared_manager())
        response = client.post(
            "/api/simulate-batch/",
            {"team_red": red.id, "team_blue": 999999, "n": 2},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["errors"] == [
            {
                "code": "invalid",
                "message": "Team 999999 does not exist.",
                "field": None,
            }
        ]


# ---------------------------------------------------------------------------
# REST polling: /api/simulate-batch/<job_id>/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSimulateBatchAPIStatusEager:
    """§6.4 — under EAGER, GET ``/api/simulate-batch/<job_id>/`` returns the
    locked §8.1 JSON shape.
    """

    def test_api_status_shape_identical_to_ui(self) -> None:
        red, _ = make_team_with_slots("Api03APIStatR")
        blue, _ = make_team_with_slots("Api03APIStatB")
        client = APIClient()
        client.force_login(get_shared_manager())

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            post_resp = client.post(
                "/api/simulate-batch/",
                {"team_red": red.id, "team_blue": blue.id, "n": 2},
                format="json",
            )
        assert post_resp.status_code == 200, post_resp.content
        job_id = post_resp.json()["data"]["job_id"]

        status_resp = client.get(
            f"/api/simulate-batch/{job_id}/",
            {"team_red_id": red.id, "team_blue_id": blue.id},
        )
        assert status_resp.status_code == 200, status_resp.content
        body = status_resp.json()["data"]
        assert set(body.keys()) == _BATCH_STATUS_KEYS, (
            f"REST status JSON keys drifted: got {set(body.keys())!r}, "
            f"expected {_BATCH_STATUS_KEYS!r}"
        )
        assert body["status"] == "complete"
        assert body["completed"] == 2
        assert body["total"] == 2
        assert isinstance(body["partial"], dict)
        assert body["error"] is None
        assert body["team_red_id"] == red.id
        assert body["team_blue_id"] == blue.id


@pytest.mark.django_db
class TestSimulateBatchAPIStatusUnknownJobId:
    """§6.4 — GET ``/api/simulate-batch/<bogus-id>/`` returns 200 with the
    running-with-nulls shape (§5 PENDING → 'running').
    """

    def test_unknown_job_id_returns_200_running(self) -> None:
        client = APIClient()
        client.force_login(get_shared_manager())
        response = client.get(
            "/api/simulate-batch/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == 200, response.content
        body = response.json()["data"]
        assert body["status"] == "running"
        assert body["completed"] == 0
        assert body["total"] == 0
        assert body["partial"] is None
        assert body["error"] is None


@pytest.mark.django_db
class TestSimulateBatchAPIStatusArenaMapCarry:
    """ANG-08e -- GET ``/api/simulate-batch/<job_id>/`` echoes the
    ``arena_map_id`` the POST was made with (query-param carry, §4.3)."""

    def test_status_echoes_arena_map_id_from_post(self) -> None:
        red, _ = make_team_with_slots("Ang08eMapCarryR")
        blue, _ = make_team_with_slots("Ang08eMapCarryB")
        arena_map = _make_minimal_arena_map("Ang08eMapCarryMap")
        client = APIClient()
        client.force_login(get_shared_manager())

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            post_resp = client.post(
                "/api/simulate-batch/",
                {
                    "team_red": red.id,
                    "team_blue": blue.id,
                    "n": 2,
                    "arena_map": arena_map.id,
                },
                format="json",
            )
        assert post_resp.status_code == 200, post_resp.content
        post_body = post_resp.json()["data"]
        assert post_body["arena_map_id"] == arena_map.id

        status_resp = client.get(
            f"/api/simulate-batch/{post_body['job_id']}/",
            {
                "team_red_id": red.id,
                "team_blue_id": blue.id,
                "arena_map_id": post_body["arena_map_id"],
            },
        )
        assert status_resp.status_code == 200, status_resp.content
        body = status_resp.json()["data"]
        assert body["status"] == "complete"
        assert body["arena_map_id"] == arena_map.id


@pytest.mark.django_db
class TestSimulateBatchAPIStatusProgress:
    """ANG-08e -- a PROGRESS job reports ``completed`` / ``total`` /
    ``partial`` from the task meta."""

    def test_progress_reports_completed_and_partial_from_meta(self) -> None:
        aggregate = {"n": 3, "avg_seeds": [[7, False]]}
        stub = FakeAsyncResult(
            "PROGRESS", {"completed": 3, "total": 10, "aggregate": aggregate}
        )
        client = APIClient()
        client.force_login(get_shared_manager())
        with patch("matches.api_views.AsyncResult", lambda job_id: stub):
            response = client.get("/api/simulate-batch/progress-job/")
        assert response.status_code == 200, response.content
        body = response.json()["data"]
        assert body["status"] == "running"
        assert body["completed"] == 3
        assert body["total"] == 10
        assert body["partial"] == aggregate
        assert body["error"] is None


@pytest.mark.django_db
class TestSimulateBatchAPIStatusError:
    """ANG-08e (port of the deleted HTML ``TestBatchSimulateStatusError``) --
    a batch task that raised reads ``status='error'`` with the exception
    message over the API."""

    def test_error_status_propagates_exception_message(self) -> None:
        from django.test import override_settings

        red, _ = make_team_with_slots("Ang08eApiErrR")
        blue, _ = make_team_with_slots("Ang08eApiErrB")
        client = APIClient()
        client.force_login(get_shared_manager())

        def _raises(self, *args, **kwargs):
            raise RuntimeError("contrived api-error")

        # Celery must capture the failure as a FAILURE state rather than
        # propagate it.
        with override_settings(CELERY_TASK_EAGER_PROPAGATES=False):
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                with patch.object(BatchSimulator, "run_incremental", _raises):
                    post_resp = client.post(
                        "/api/simulate-batch/",
                        {"team_red": red.id, "team_blue": blue.id, "n": 2},
                        format="json",
                    )
        assert post_resp.status_code == 200, post_resp.content
        job_id = post_resp.json()["data"]["job_id"]

        status_resp = client.get(
            f"/api/simulate-batch/{job_id}/",
            {"team_red_id": red.id, "team_blue_id": blue.id},
        )
        assert status_resp.status_code == 200, status_resp.content
        body = status_resp.json()["data"]
        assert body["status"] == "error"
        assert isinstance(body["error"], str)
        assert "contrived api-error" in body["error"]


# ---------------------------------------------------------------------------
# Pure-unit truth table — celery_state_to_job_status (§5)
# ---------------------------------------------------------------------------


class TestCeleryStateMappingHelper:
    """§6.4 / §5 — exhaustive truth table of ``celery_state_to_job_status``.
    Pure-unit, no DB. Pins the locked mapping at the view boundary.
    """

    _TABLE: list[tuple[str, str]] = [
        ("PENDING", "running"),
        ("STARTED", "running"),
        ("PROGRESS", "running"),
        ("SUCCESS", "complete"),
        ("FAILURE", "error"),
        ("REVOKED", "error"),
        ("RETRY", "running"),
        # Defensive fallback for unknown / future Celery states.
        ("WEIRD_UNKNOWN_STATE", "running"),
        ("", "running"),
    ]

    @pytest.mark.parametrize("celery_state,expected", _TABLE)
    def test_truth_table(self, celery_state: str, expected: str) -> None:
        from matches.job_status import celery_state_to_job_status

        actual = celery_state_to_job_status(celery_state)
        assert actual == expected, (
            f"celery_state_to_job_status({celery_state!r}) returned "
            f"{actual!r}; expected {expected!r} (§5 truth table)"
        )


# ---------------------------------------------------------------------------
# AllowAny permission inheritance (API-02 deferred-auth precedent)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAPIInheritsAllowAnyPermissions:
    """§6.4 — POST ``/api/simulate-batch/`` from an unauthenticated client
    succeeds (returns 200, not 401/403) — documents the API-02
    deferred-auth precedent and prevents accidental future regression.
    """

    def test_unauthenticated_post_returns_200(self) -> None:
        red, _ = make_team_with_slots("Api03AuthR")
        blue, _ = make_team_with_slots("Api03AuthB")
        # Fresh APIClient with NO force_authenticate call.
        client = APIClient()
        client.force_login(get_shared_manager())

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = client.post(
                "/api/simulate-batch/",
                {"team_red": red.id, "team_blue": blue.id, "n": 2},
                format="json",
            )

        assert response.status_code not in (401, 403), (
            f"REST POST must inherit AllowAny from REST_FRAMEWORK defaults; "
            f"unauthenticated request was rejected with {response.status_code} "
            f"(API-02 deferred-auth regression)"
        )
        assert response.status_code == 200, (
            f"unauthenticated POST should succeed with a valid payload; "
            f"got {response.status_code}: {response.content!r}"
        )
