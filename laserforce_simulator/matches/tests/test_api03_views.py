"""API-03 — view-layer tests for the rewritten UI views + the new REST views.

Pinned by ``.claude/worktrees/api-03-seam-contract.md`` §6.4 (NEW test
classes), §5 (status mapping truth table), §8 (polling JSON shape per-key
source tables), §9 (POST response shapes), §10 (DOM-id preservation note —
not exercised here; lives in the existing template), and §11 (REST views
inherit ``AllowAny`` from REST_FRAMEWORK defaults).

Runs under ``CELERY_TASK_ALWAYS_EAGER = True`` (project ``conftest.py``).
All names below are normative.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Local fixture: minimal ArenaMap — mirrors views_tests.py's helper so the
# API-03 tests do not import the legacy view-tests module (which still has
# pre-API-03 names that the Code agent is in the process of deleting).
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


# ---------------------------------------------------------------------------
# UI POST: /matches/simulate-batch/
# ---------------------------------------------------------------------------


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


_SAVE_STATUS_KEYS = frozenset({"status", "error", "round_ids"})


@pytest.mark.django_db
class TestSimulateBatchPostUIReturnsJobId:
    """§6.4 — POST ``/matches/simulate-batch/`` returns 200 + the locked
    JSON shape; ``job_id`` is a non-empty string. Patches
    ``simulate_batch_task.delay`` to capture the call args.
    """

    def test_post_returns_locked_json_shape_and_enqueues_task(self) -> None:
        red, _ = make_team_with_slots("Api03UIPostR")
        blue, _ = make_team_with_slots("Api03UIPostB")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = client.post(
                reverse("simulate_batch"),
                {"team_red": red.id, "team_blue": blue.id, "n": "10"},
            )

        assert response.status_code == 200, response.content
        assert response["Content-Type"].startswith(
            "application/json"
        ), f"POST must return JSON; got Content-Type={response['Content-Type']!r}"
        body = json.loads(response.content.decode())
        assert set(body.keys()) == _BATCH_POST_KEYS, (
            f"POST JSON keys drifted: got {set(body.keys())!r}, "
            f"expected {_BATCH_POST_KEYS!r}"
        )
        assert (
            isinstance(body["job_id"], str) and body["job_id"]
        ), f"job_id must be a non-empty string; got {body['job_id']!r}"
        assert body["team_red_id"] == red.id
        assert body["team_red_name"] == red.name
        assert body["team_blue_id"] == blue.id
        assert body["team_blue_name"] == blue.name
        assert body["arena_map_id"] is None
        assert body["n"] == 10


# ---------------------------------------------------------------------------
# UI polling: /matches/simulate-batch/status/<job_id>/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBatchSimulateStatusEager:
    """§6.4 — under EAGER, a status poll after the POST shows
    ``status='complete'``, ``completed == n``, ``partial`` carries the
    final aggregate; ``team_red_id`` / ``team_blue_id`` / ``arena_map_id``
    are echoed from the query-param carry (§4.3).
    """

    def test_status_complete_after_post_with_query_param_carry(self) -> None:
        red, _ = make_team_with_slots("Api03StatEagerR")
        blue, _ = make_team_with_slots("Api03StatEagerB")
        arena_map = _make_minimal_arena_map("Api03StatEagerMap")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            post_resp = client.post(
                reverse("simulate_batch"),
                {
                    "team_red": red.id,
                    "team_blue": blue.id,
                    "n": "10",
                    "arena_map": arena_map.id,
                },
            )
        assert post_resp.status_code == 200, post_resp.content
        post_body = json.loads(post_resp.content.decode())
        job_id = post_body["job_id"]

        # Polling JS appends team / map ids as query params (§4.3 — locked
        # query-param carry).
        status_resp = client.get(
            reverse("batch_simulate_status", args=[job_id]),
            {
                "team_red_id": red.id,
                "team_blue_id": blue.id,
                "arena_map_id": arena_map.id,
            },
        )
        assert status_resp.status_code == 200
        body = json.loads(status_resp.content.decode())
        assert set(body.keys()) == _BATCH_STATUS_KEYS, (
            f"status JSON keys drifted: got {set(body.keys())!r}, "
            f"expected {_BATCH_STATUS_KEYS!r}"
        )
        assert (
            body["status"] == "complete"
        ), f"under EAGER the SUCCESS state maps to 'complete'; got {body['status']!r}"
        assert body["completed"] == 10
        assert body["total"] == 10
        assert isinstance(body["partial"], dict), (
            f"partial must carry the final aggregate dict on SUCCESS; "
            f"got {body['partial']!r}"
        )
        assert body["error"] is None
        assert body["team_red_id"] == red.id
        assert body["team_blue_id"] == blue.id
        assert body["arena_map_id"] == arena_map.id


@pytest.mark.django_db
class TestBatchSimulateStatusError:
    """§6.4 — a task that raised propagates as ``status='error'`` with
    ``error == str(exc)`` (mapped at the view boundary — raw FAILURE state
    is never exposed).
    """

    def test_error_status_propagates_exception_message(self) -> None:
        from django.test import override_settings

        red, _ = make_team_with_slots("Api03ErrR")
        blue, _ = make_team_with_slots("Api03ErrB")
        client = Client()

        # Patch BatchSimulator.run_incremental to raise a generator-shaped
        # exception that fires on first consumption — surfaces as task
        # FAILURE state in the EagerResult.
        def _raises(self, *args, **kwargs):
            raise RuntimeError("contrived view-error")

        # The global pytest settings have CELERY_TASK_EAGER_PROPAGATES=True
        # so task body exceptions surface immediately in most tests. For the
        # error-path test we need Celery to *capture* the failure as a
        # FAILURE state on the EagerResult instead — that is what the view's
        # AsyncResult read will then map to status="error".
        with override_settings(CELERY_TASK_EAGER_PROPAGATES=False):
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                with patch.object(BatchSimulator, "run_incremental", _raises):
                    post_resp = client.post(
                        reverse("simulate_batch"),
                        {"team_red": red.id, "team_blue": blue.id, "n": "10"},
                    )

        # POST must still return 200 + job_id (the failure is observed via
        # the polling endpoint, not on POST).
        assert post_resp.status_code == 200, post_resp.content
        job_id = json.loads(post_resp.content.decode())["job_id"]

        status_resp = client.get(
            reverse("batch_simulate_status", args=[job_id]),
            {"team_red_id": red.id, "team_blue_id": blue.id},
        )
        assert status_resp.status_code == 200
        body = json.loads(status_resp.content.decode())
        assert (
            body["status"] == "error"
        ), f"expected status='error' after RuntimeError; got {body['status']!r}"
        assert (
            isinstance(body["error"], str) and "contrived view-error" in body["error"]
        ), f"expected error to contain the exception message; got {body['error']!r}"


@pytest.mark.django_db
class TestBatchSimulateStatusUnknownJobId:
    """§6.4 — GET ``/matches/simulate-batch/status/<bogus-uuid>/`` returns
    200 with ``status='running'`` / ``completed=0`` / ``total=0`` /
    ``partial=null`` / ``error=null`` (Celery PENDING for unknown id maps
    to 'running' per §5).
    """

    def test_unknown_job_id_returns_running_with_zeros(self) -> None:
        client = Client()
        # Query params not supplied — view echoes None for each.
        resp = client.get(
            reverse(
                "batch_simulate_status", args=["00000000-0000-0000-0000-000000000000"]
            )
        )

        assert resp.status_code == 200, (
            f"unknown job id must map to 200 (the §5 PENDING→running mapping "
            f"means the polling UI keeps polling forever); got {resp.status_code}"
        )
        body = json.loads(resp.content.decode())
        assert body["status"] == "running"
        assert body["completed"] == 0
        assert body["total"] == 0
        assert body["partial"] is None
        assert body["error"] is None
        assert body["team_red_id"] is None
        assert body["team_blue_id"] is None
        assert body["arena_map_id"] is None


# ---------------------------------------------------------------------------
# UI save flow: /matches/save-batch-games/ + /matches/save-batch-status/<id>/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveBatchGamesPost:
    """§6.4 — POST ``/matches/save-batch-games/`` returns 200 + ``{job_id}``.
    Patches ``save_games_task.delay`` to assert the seeds + ``arena_map_id``
    from ``request.session["batch_seeds"]`` are threaded into the task
    call. Empty-session and missing-seeds 400 branches preserved.
    """

    def _populate_session_batch_seeds(
        self,
        client,
        *,
        team_red_id,
        team_blue_id,
        arena_map_id,
        avg_seeds,
        outlier_seeds,
    ):
        """Stash a batch_seeds entry in the session so save_batch_games
        can read it (mirrors what the SIM-10 → API-03 complete-poll guard
        does in production).
        """
        session = client.session
        session["batch_seeds"] = {
            "job_id": "test-job-id",
            "team_red_id": team_red_id,
            "team_blue_id": team_blue_id,
            "arena_map_id": arena_map_id,
            "avg_seeds": avg_seeds,
            "outlier_seeds": outlier_seeds,
        }
        session.save()

    def test_post_returns_job_id_and_enqueues_with_session_seeds(self) -> None:
        red, _ = make_team_with_slots("Api03SaveUIPostR")
        blue, _ = make_team_with_slots("Api03SaveUIPostB")
        arena_map = _make_minimal_arena_map("Api03SaveUIPostMap")
        client = Client()

        # Stash a batch_seeds session entry the view will consume.
        self._populate_session_batch_seeds(
            client,
            team_red_id=red.id,
            team_blue_id=blue.id,
            arena_map_id=arena_map.id,
            avg_seeds=[[12345, False], [67890, True]],
            outlier_seeds=[[11111, False]],
        )

        captured: dict = {}

        class _FakeAsyncResult:
            id = "fake-save-task-id"

        def _spy_delay(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _FakeAsyncResult()

        # Patch save_games_task.delay where the view imports it from.
        with patch("matches.tasks.save_games_task.delay", _spy_delay):
            response = client.post(
                reverse("save_batch_games"),
                {"game_type": "avg", "n": "1"},
            )

        assert response.status_code == 200, response.content
        body = json.loads(response.content.decode())
        assert "job_id" in body and isinstance(body["job_id"], str) and body["job_id"]

        assert "args" in captured, (
            "save_games_task.delay was never called from save_batch_games; "
            "the view must enqueue the task via .delay(...)"
        )
        # Combined positional + keyword inspection so we don't pin the exact
        # arg style. The view may call either:
        #   save_games_task.delay(team_red_id, team_blue_id, seeds, n, arena_map_id)
        # or keyword form. We assert both shapes via a merged-kwargs dict.
        merged: dict = dict(captured["kwargs"])
        for i, name in enumerate(
            ("team_red_id", "team_blue_id", "seeds", "n", "arena_map_id")
        ):
            if i < len(captured["args"]):
                merged[name] = captured["args"][i]

        assert merged["team_red_id"] == red.id
        assert merged["team_blue_id"] == blue.id
        # seeds is the first `n` entries of avg_seeds — game_type="avg" + n=1.
        # Celery serialisation coerces tuples to lists so we accept either.
        seeds_val = merged["seeds"]
        assert len(seeds_val) == 1
        first = seeds_val[0]
        assert list(first) == [12345, False], (
            f"first carried seed must be the first avg_seed pair from the "
            f"session stash; got {first!r}"
        )
        assert merged["n"] == 1
        assert merged["arena_map_id"] == arena_map.id, (
            f"save_batch_games must thread session arena_map_id into the task; "
            f"got {merged.get('arena_map_id')!r}"
        )

    def test_empty_session_returns_400(self) -> None:
        client = Client()
        # No session["batch_seeds"] populated.
        response = client.post(
            reverse("save_batch_games"),
            {"game_type": "avg", "n": "1"},
        )
        assert (
            response.status_code == 400
        ), f"empty-session POST must return 400; got {response.status_code}"

    def test_missing_seeds_for_category_returns_400(self) -> None:
        red, _ = make_team_with_slots("Api03SaveEmptySeedsR")
        blue, _ = make_team_with_slots("Api03SaveEmptySeedsB")
        client = Client()
        # Populate session but with empty avg_seeds list.
        self._populate_session_batch_seeds(
            client,
            team_red_id=red.id,
            team_blue_id=blue.id,
            arena_map_id=None,
            avg_seeds=[],
            outlier_seeds=[],
        )

        response = client.post(
            reverse("save_batch_games"),
            {"game_type": "avg", "n": "1"},
        )
        assert (
            response.status_code == 400
        ), f"missing-seeds POST must return 400; got {response.status_code}"


@pytest.mark.django_db
class TestSaveBatchStatusEager:
    """§6.4 — under EAGER, a status poll after a real ``save_games_task``
    run returns ``{status: "complete", error: null, round_ids: [<int>, ...]}``.
    The legacy ``"done"`` vocabulary is renamed to ``"complete"`` (locked).
    """

    def _stash_session_and_save(self, client, red, blue, *, arena_map_id=None):
        """Stash a batch_seeds entry then POST save-batch-games, returning
        the job_id of the enqueued save task.
        """
        session = client.session
        session["batch_seeds"] = {
            "job_id": "test-job-id",
            "team_red_id": red.id,
            "team_blue_id": blue.id,
            "arena_map_id": arena_map_id,
            "avg_seeds": [[12345, False]],
            "outlier_seeds": [],
        }
        session.save()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = client.post(
                reverse("save_batch_games"),
                {"game_type": "avg", "n": "1"},
            )
        assert response.status_code == 200, response.content
        return json.loads(response.content.decode())["job_id"]

    def test_status_complete_after_save_eager_renames_done_to_complete(self) -> None:
        red, _ = make_team_with_slots("Api03SaveStatR")
        blue, _ = make_team_with_slots("Api03SaveStatB")
        client = Client()

        job_id = self._stash_session_and_save(client, red, blue)

        resp = client.get(reverse("save_batch_status", args=[job_id]))
        assert resp.status_code == 200, resp.content
        body = json.loads(resp.content.decode())
        assert set(body.keys()) == _SAVE_STATUS_KEYS, (
            f"save-status JSON keys drifted: got {set(body.keys())!r}, "
            f"expected {_SAVE_STATUS_KEYS!r}"
        )
        # API-03 rename: SUCCESS → "complete" (not the pre-API-03 "done").
        assert body["status"] == "complete", (
            f"save-status SUCCESS must map to 'complete' (API-03 rename); "
            f"got {body['status']!r}"
        )
        assert body["error"] is None
        assert isinstance(body["round_ids"], list)
        assert len(body["round_ids"]) >= 1, (
            f"save-status round_ids must carry the persisted round PKs; "
            f"got {body['round_ids']!r}"
        )


# ---------------------------------------------------------------------------
# REST POST: /api/simulate-batch/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSimulateBatchAPIPost:
    """§6.4 — POST ``/api/simulate-batch/`` returns 200 + the same JSON
    shape as the UI POST. Same-team rejection returns 400. Serializer
    validation failures return 400.
    """

    def test_post_returns_locked_json_shape(self) -> None:
        red, _ = make_team_with_slots("Api03APIPostR")
        blue, _ = make_team_with_slots("Api03APIPostB")
        client = APIClient()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = client.post(
                "/api/simulate-batch/",
                {"team_red": red.id, "team_blue": blue.id, "n": 2},
                format="json",
            )

        assert response.status_code == 200, response.content
        body = response.json()
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

        response = client.post(
            "/api/simulate-batch/",
            {"team_red": red.id, "team_blue": red.id, "n": 2},
            format="json",
        )
        assert response.status_code == 400, (
            f"same-team POST must return 400; got {response.status_code}: "
            f"{response.content!r}"
        )

    def test_invalid_field_returns_400(self) -> None:
        client = APIClient()
        # Missing required field `team_blue` → serializer validation 400.
        response = client.post(
            "/api/simulate-batch/",
            {"team_red": 1, "n": 2},
            format="json",
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# REST polling: /api/simulate-batch/<job_id>/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSimulateBatchAPIStatusEager:
    """§6.4 — under EAGER, GET ``/api/simulate-batch/<job_id>/`` returns
    the SAME JSON shape as the UI ``batch_simulate_status`` endpoint
    (locked in §8.1 — identical shape).
    """

    def test_api_status_shape_identical_to_ui(self) -> None:
        red, _ = make_team_with_slots("Api03APIStatR")
        blue, _ = make_team_with_slots("Api03APIStatB")
        client = APIClient()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            post_resp = client.post(
                "/api/simulate-batch/",
                {"team_red": red.id, "team_blue": blue.id, "n": 2},
                format="json",
            )
        assert post_resp.status_code == 200, post_resp.content
        job_id = post_resp.json()["job_id"]

        # Same query-param carry as the UI status endpoint.
        status_resp = client.get(
            f"/api/simulate-batch/{job_id}/",
            {"team_red_id": red.id, "team_blue_id": blue.id},
        )
        assert status_resp.status_code == 200, status_resp.content
        body = status_resp.json()
        assert set(body.keys()) == _BATCH_STATUS_KEYS, (
            f"REST status JSON keys drifted: got {set(body.keys())!r}, "
            f"expected (UI-identical) {_BATCH_STATUS_KEYS!r}"
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
    running-with-nulls shape (same as the UI endpoint — §5 PENDING → 'running').
    """

    def test_unknown_job_id_returns_200_running(self) -> None:
        client = APIClient()
        response = client.get(
            "/api/simulate-batch/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["status"] == "running"
        assert body["completed"] == 0
        assert body["total"] == 0
        assert body["partial"] is None
        assert body["error"] is None


# ---------------------------------------------------------------------------
# Pure-unit truth table — _celery_state_to_job_status (§5)
# ---------------------------------------------------------------------------


class TestCeleryStateMappingHelper:
    """§6.4 / §5 — exhaustive truth table of ``_celery_state_to_job_status``.
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
        from matches.views import _celery_state_to_job_status

        actual = _celery_state_to_job_status(celery_state)
        assert actual == expected, (
            f"_celery_state_to_job_status({celery_state!r}) returned "
            f"{actual!r}; expected {expected!r} (§5 truth table)"
        )


# ---------------------------------------------------------------------------
# Session-handover guard (preserved from SIM-10)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSessionHandoverPreservedOnComplete:
    """§6.4 — the SIM-10 session-handover guard is preserved verbatim: the
    FIRST poll observing ``complete`` writes ``request.session["batch_seeds"]``
    with the ``job_id`` guard marker; subsequent polls observing ``complete``
    skip the write (so user-mutations between polls survive).
    """

    def test_session_handover_writes_once_and_guard_holds(self) -> None:
        red, _ = make_team_with_slots("Api03HandoverR")
        blue, _ = make_team_with_slots("Api03HandoverB")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            post_resp = client.post(
                reverse("simulate_batch"),
                {"team_red": red.id, "team_blue": blue.id, "n": "10"},
            )
        assert post_resp.status_code == 200, post_resp.content
        job_id = json.loads(post_resp.content.decode())["job_id"]

        # First poll → triggers single write.
        first = client.get(
            reverse("batch_simulate_status", args=[job_id]),
            {"team_red_id": red.id, "team_blue_id": blue.id},
        )
        assert first.status_code == 200
        first_body = json.loads(first.content.decode())
        assert first_body["status"] == "complete"

        session_seeds = client.session.get("batch_seeds")
        assert (
            session_seeds is not None
        ), "first complete-poll must write request.session['batch_seeds']"
        for key in (
            "job_id",
            "team_red_id",
            "team_blue_id",
            "arena_map_id",
            "avg_seeds",
            "outlier_seeds",
        ):
            assert (
                key in session_seeds
            ), f"batch_seeds missing locked key {key!r}: {session_seeds!r}"
        assert session_seeds["job_id"] == job_id
        assert session_seeds["team_red_id"] == red.id
        assert session_seeds["team_blue_id"] == blue.id
        partial = first_body["partial"]
        assert session_seeds["avg_seeds"] == partial["avg_seeds"]
        assert session_seeds["outlier_seeds"] == partial["outlier_seeds"]

        # Mutate session between polls — guard must hit on the second
        # complete-observing poll and skip the write.
        session = client.session
        session["batch_seeds"]["avg_seeds"] = "SENTINEL"
        session.save()

        second = client.get(
            reverse("batch_simulate_status", args=[job_id]),
            {"team_red_id": red.id, "team_blue_id": blue.id},
        )
        assert second.status_code == 200
        assert json.loads(second.content.decode())["status"] == "complete"

        session_after = client.session.get("batch_seeds")
        assert session_after is not None
        assert session_after["avg_seeds"] == "SENTINEL", (
            "second complete-poll overwrote session despite matching `job_id` "
            "guard; the single-write contract is broken"
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
