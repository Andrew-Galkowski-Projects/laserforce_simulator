"""ANG-01a — the project-level REST envelope (ADR-0039).

Covers the locked wire format of seam contract §2: ``EnvelopeJSONRenderer``
(object / list / paginated / 204 / 4xx passthrough / defensive
normalisation), ``EnvelopePagination`` meta, the exception-handler status /
code mapping table (§2.6), ``errors_from_detail`` flattening and
``AliasOrderingFilter`` (§3.3).

Only the public surface is asserted (contract §8): the renderer is driven
through its ``render`` signature or real HTTP round-trips, never through
branch internals or the ``envelope_meta`` attribute name.
"""

from __future__ import annotations

from typing import Any

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ErrorDetail
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory

from accounts.permissions import owned_queryset
from conftest import get_shared_manager
from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    ErrorEntry,
    errors_from_detail,
)
from laserforce_simulator.api.filters import AliasOrderingFilter
from laserforce_simulator.api.renderers import EnvelopeJSONRenderer
from teams.models import Player, Team

User = get_user_model()

_PAGINATED_META_KEYS = {"page", "page_size", "count", "total_pages"}


def _render(data: object, status: int) -> Any:
    """Render ``data`` as if it were a ``Response`` with ``status`` and decode it."""
    response = Response(data, status=status)
    raw = EnvelopeJSONRenderer().render(
        data,
        accepted_media_type="application/json",
        renderer_context={"response": response},
    )
    if raw == b"":
        return raw
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Renderer (§2.4)
# ---------------------------------------------------------------------------


class TestEnvelopeRendererUnit(TestCase):
    """Direct calls through the public ``render`` signature."""

    def test_success_object_is_wrapped_in_data_with_empty_meta(self) -> None:
        self.assertEqual(
            _render({"id": 1, "name": "X"}, 200),
            {"data": {"id": 1, "name": "X"}, "meta": {}},
        )

    def test_success_list_is_wrapped(self) -> None:
        self.assertEqual(_render([1, 2, 3], 200), {"data": [1, 2, 3], "meta": {}})

    def test_201_is_wrapped(self) -> None:
        self.assertEqual(_render({"id": 7}, 201), {"data": {"id": 7}, "meta": {}})

    def test_success_body_that_looks_enveloped_is_still_wrapped(self) -> None:
        """No "already enveloped" sniffing for 2xx — always wrapped."""
        payload = {"data": [1], "meta": {}}
        self.assertEqual(_render(payload, 200), {"data": payload, "meta": {}})

    def test_204_renders_empty_bytes(self) -> None:
        self.assertEqual(_render(None, 204), b"")

    def test_error_envelope_passes_through_unchanged(self) -> None:
        body = {
            "errors": [{"code": "not_found", "message": "Nope.", "field": None}],
            "meta": {},
        }
        self.assertEqual(_render(body, 404), body)

    def test_hand_built_detail_error_is_normalised(self) -> None:
        """Defensive path: a hand-built ``{"detail": msg}`` 400 still renders
        as an envelope error."""
        rendered = _render({"detail": "Something broke"}, 400)
        self.assertEqual(set(rendered), {"errors", "meta"})
        self.assertEqual(rendered["meta"], {})
        self.assertGreaterEqual(len(rendered["errors"]), 1)
        for entry in rendered["errors"]:
            self.assertEqual(list(entry), ["code", "message", "field"])
        self.assertIn("Something broke", [e["message"] for e in rendered["errors"]])

    def test_hand_built_string_error_is_normalised(self) -> None:
        rendered = _render("plain failure", 400)
        self.assertEqual(
            rendered,
            {
                "errors": [
                    {"code": "invalid", "message": "plain failure", "field": None}
                ],
                "meta": {},
            },
        )


class TestEnvelopeOverHttp(TestCase):
    """Real HTTP round-trips through the settings-level renderer."""

    def setUp(self) -> None:
        super().setUp()
        self.api = APIClient()
        self.api.force_login(get_shared_manager())
        self.team = Team.objects.create(name="Envelope Team")

    def test_detail_is_object_envelope(self) -> None:
        body = self.api.get(f"/api/teams/{self.team.pk}/").json()
        self.assertEqual(set(body), {"data", "meta"})
        self.assertEqual(body["meta"], {})
        self.assertEqual(body["data"]["id"], self.team.pk)

    def test_list_is_paginated_envelope(self) -> None:
        body = self.api.get("/api/teams/").json()
        self.assertEqual(set(body), {"data", "meta"})
        self.assertIsInstance(body["data"], list)
        self.assertEqual(set(body["meta"]), _PAGINATED_META_KEYS)

    def test_response_content_type_is_json(self) -> None:
        response = self.api.get("/api/teams/")
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")

    def test_player_delete_is_204_with_empty_body(self) -> None:
        player = Player.objects.create(team=self.team, name="Doomed")
        response = self.api.delete(f"/api/players/{player.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")


# ---------------------------------------------------------------------------
# Pagination (§2.2 / §2.3)
# ---------------------------------------------------------------------------


class TestEnvelopePagination(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = get_shared_manager()
        self.api = APIClient()
        self.api.force_login(self.user)
        for i in range(25):
            Team.objects.create(name=f"Paged {i:02d}", manager=self.user)
        self.expected_count = owned_queryset(Team.objects.regular(), self.user).count()

    def test_first_page_meta(self) -> None:
        body = self.api.get("/api/teams/").json()
        self.assertEqual(body["meta"]["page"], 1)
        self.assertEqual(body["meta"]["page_size"], 20)
        self.assertEqual(body["meta"]["count"], self.expected_count)
        self.assertEqual(
            body["meta"]["total_pages"], -(-self.expected_count // 20)
        )  # ceil
        self.assertEqual(len(body["data"]), 20)

    def test_second_page_meta(self) -> None:
        body = self.api.get("/api/teams/", {"page": 2}).json()
        self.assertEqual(body["meta"]["page"], 2)
        self.assertEqual(len(body["data"]), self.expected_count - 20)

    def test_page_size_param_is_honoured(self) -> None:
        body = self.api.get("/api/teams/", {"page_size": 5}).json()
        self.assertEqual(body["meta"]["page_size"], 5)
        self.assertEqual(len(body["data"]), 5)
        self.assertEqual(body["meta"]["total_pages"], -(-self.expected_count // 5))

    def test_page_size_over_max_is_clamped_to_100(self) -> None:
        response = self.api.get("/api/teams/", {"page_size": 500})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["meta"]["page_size"], 100)

    def test_non_integer_page_size_falls_back_to_default(self) -> None:
        response = self.api.get("/api/teams/", {"page_size": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["meta"]["page_size"], 20)

    def test_zero_page_size_falls_back_to_default(self) -> None:
        response = self.api.get("/api/teams/", {"page_size": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["meta"]["page_size"], 20)

    def test_page_last_is_legal(self) -> None:
        response = self.api.get("/api/teams/", {"page": "last"})
        self.assertEqual(response.status_code, 200)
        meta = response.json()["meta"]
        self.assertEqual(meta["page"], meta["total_pages"])

    def test_out_of_range_page_is_404_not_found(self) -> None:
        for bad in ("999", "0", "abc"):
            with self.subTest(page=bad):
                response = self.api.get("/api/teams/", {"page": bad})
                self.assertEqual(response.status_code, 404)
                body = response.json()
                self.assertEqual(set(body), {"errors", "meta"})
                self.assertEqual(body["errors"][0]["code"], "not_found")
                self.assertIsNone(body["errors"][0]["field"])

    def test_empty_result_meta(self) -> None:
        """Django semantics: empty → ``count 0, total_pages 1, page 1``."""
        body = self.api.get("/api/teams/", {"name": "zz-no-such-team-zz"}).json()
        self.assertEqual(body["data"], [])
        self.assertEqual(
            body["meta"], {"page": 1, "page_size": 20, "count": 0, "total_pages": 1}
        )


# ---------------------------------------------------------------------------
# Exception handler mapping (§2.5 / §2.6)
# ---------------------------------------------------------------------------


class TestExceptionHandlerMapping(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.api = APIClient()
        self.api.force_login(get_shared_manager())
        self.team = Team.objects.create(name="Mapping Team")

    def _assert_single_error(self, response, status: int, code: str) -> dict:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        self.assertEqual(body["meta"], {})
        self.assertTrue(body["errors"], "errors must never be empty")
        entry = body["errors"][0]
        self.assertEqual(list(entry), ["code", "message", "field"])
        self.assertEqual(entry["code"], code)
        self.assertIsInstance(entry["message"], str)
        self.assertIsNone(entry["field"])
        return body

    def test_missing_row_is_404_not_found(self) -> None:
        self._assert_single_error(self.api.get("/api/teams/999999/"), 404, "not_found")

    def test_non_numeric_pk_is_enveloped_404(self) -> None:
        self._assert_single_error(self.api.get("/api/teams/abc/"), 404, "not_found")

    def test_cross_account_row_is_404_not_found_never_403(self) -> None:
        other = User.objects.create_user(
            email="envelope-other@example.com", password="Str0ng-Passphrase-9"
        )
        theirs = Team.objects.create(name="Theirs", manager=other)
        self._assert_single_error(
            self.api.get(f"/api/teams/{theirs.pk}/"), 404, "not_found"
        )

    def test_anonymous_is_401_not_authenticated(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self._assert_single_error(
            APIClient().get("/api/teams/"), 401, "not_authenticated"
        )

    def test_method_not_allowed_is_405_with_allow_header(self) -> None:
        response = self.api.put(
            f"/api/teams/{self.team.pk}/", {"name": "X"}, format="json"
        )
        self._assert_single_error(response, 405, "method_not_allowed")
        self.assertTrue(response.has_header("Allow"))
        allowed = {m.strip() for m in response["Allow"].split(",")}
        self.assertIn("GET", allowed)
        self.assertNotIn("PUT", allowed)
        self.assertNotIn("DELETE", allowed)

    def test_malformed_json_is_400_parse_error(self) -> None:
        response = self.api.post(
            "/api/teams/", data="{not json", content_type="application/json"
        )
        self._assert_single_error(response, 400, "parse_error")

    def test_non_object_json_body_is_400_parse_error_not_500(self) -> None:
        # Valid JSON that is not an object (an array) cannot bind a Form;
        # `plain_form_data` must reject it rather than crash on `.items()`.
        response = self.api.post(
            "/api/teams/", data="[1, 2]", content_type="application/json"
        )
        self._assert_single_error(response, 400, "parse_error")

    def test_csrf_failure_is_403_permission_denied(self) -> None:
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.force_login(get_shared_manager())
        response = csrf_client.post("/api/teams/", {"name": "NoCsrf"}, format="json")
        self._assert_single_error(response, 403, "permission_denied")
        self.assertFalse(Team.objects.filter(name="NoCsrf").exists())

    def test_wrong_content_type_on_import_is_415(self) -> None:
        response = self.api.post("/api/teams/import/", {"csv_file": "x"}, format="json")
        self._assert_single_error(response, 415, "unsupported_media_type")

    def test_form_validation_error_carries_field_and_code(self) -> None:
        response = self.api.post("/api/teams/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertEqual(
            [(e["code"], e["field"]) for e in body["errors"]], [("required", "name")]
        )


class TestEnvelopeValidationError(TestCase):
    def test_status_and_errors_are_stored_verbatim(self) -> None:
        errors: list[ErrorEntry] = [
            {"code": "roster_invalid", "message": "bad", "field": "row:1"}
        ]
        exc = EnvelopeValidationError(errors)
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.errors, errors)


# ---------------------------------------------------------------------------
# errors_from_detail flattening (§2.5)
# ---------------------------------------------------------------------------


class TestErrorsFromDetail(TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(
            errors_from_detail("boom"),
            [{"code": "invalid", "message": "boom", "field": None}],
        )

    def test_error_detail_keeps_its_code(self) -> None:
        self.assertEqual(
            errors_from_detail(ErrorDetail("gone", code="not_found")),
            [{"code": "not_found", "message": "gone", "field": None}],
        )

    def test_default_code_is_used_when_detail_has_none(self) -> None:
        self.assertEqual(
            errors_from_detail("x", default_code="custom"),
            [{"code": "custom", "message": "x", "field": None}],
        )

    def test_list_gives_one_entry_per_item_with_null_field(self) -> None:
        result = errors_from_detail(
            [ErrorDetail("a", code="c1"), ErrorDetail("b", code="c2")]
        )
        self.assertEqual(
            result,
            [
                {"code": "c1", "message": "a", "field": None},
                {"code": "c2", "message": "b", "field": None},
            ],
        )

    def test_dict_gives_one_entry_per_message_keyed_by_field(self) -> None:
        detail = {
            "name": [
                ErrorDetail("required!", code="required"),
                ErrorDetail("too long", code="max_length"),
            ],
            "n": [ErrorDetail("too big", code="max_value")],
        }
        self.assertEqual(
            errors_from_detail(detail),
            [
                {"code": "required", "message": "required!", "field": "name"},
                {"code": "max_length", "message": "too long", "field": "name"},
                {"code": "max_value", "message": "too big", "field": "n"},
            ],
        )

    def test_non_field_keys_map_to_null(self) -> None:
        detail = {
            "non_field_errors": [ErrorDetail("nf", code="invalid")],
            "__all__": [ErrorDetail("all", code="invalid")],
        }
        self.assertEqual([e["field"] for e in errors_from_detail(detail)], [None, None])

    def test_nested_dict_joins_with_dot(self) -> None:
        detail = {"a": {"b": [ErrorDetail("deep", code="invalid")]}}
        self.assertEqual(
            errors_from_detail(detail),
            [{"code": "invalid", "message": "deep", "field": "a.b"}],
        )


# ---------------------------------------------------------------------------
# AliasOrderingFilter (§3.3)
# ---------------------------------------------------------------------------


class _FakeView:
    """Minimal view carrying the ordering attributes the filter reads."""

    ordering_fields = ("name", "rating", "id")
    ordering_aliases = {"rating": "wins"}
    ordering = ("name",)
    ordering_tiebreak = ("id",)


class TestAliasOrderingFilter(TestCase):
    def _ordering(self, query: dict) -> list[str]:
        request = Request(APIRequestFactory().get("/", query))
        result = AliasOrderingFilter().get_ordering(
            request, Team.objects.all(), _FakeView()
        )
        return list(result or [])

    def test_alias_is_mapped_to_orm_name(self) -> None:
        self.assertEqual(self._ordering({"ordering": "rating"}), ["wins", "id"])

    def test_descending_prefix_is_preserved_through_alias(self) -> None:
        self.assertEqual(self._ordering({"ordering": "-rating"}), ["-wins", "id"])

    def test_unaliased_term_passes_through_with_tiebreak(self) -> None:
        self.assertEqual(self._ordering({"ordering": "-name"}), ["-name", "id"])

    def test_tiebreak_not_duplicated_when_requested(self) -> None:
        self.assertEqual(self._ordering({"ordering": "id"}), ["id"])

    def test_comma_list_is_supported(self) -> None:
        self.assertEqual(
            self._ordering({"ordering": "rating,-name"}), ["wins", "-name", "id"]
        )

    def test_invalid_term_is_dropped_silently(self) -> None:
        self.assertEqual(self._ordering({"ordering": "bogus,-rating"}), ["-wins", "id"])

    def test_orm_name_is_not_a_public_term(self) -> None:
        """``wins`` is the ORM target, not a public ordering field."""
        result = self._ordering({"ordering": "wins"})
        self.assertEqual(result[0], "name")

    def test_nothing_valid_falls_back_to_view_ordering(self) -> None:
        result = self._ordering({"ordering": "bogus"})
        self.assertEqual(result[0], "name")

    def test_no_param_falls_back_to_view_ordering(self) -> None:
        self.assertEqual(self._ordering({})[0], "name")

    def test_ordering_over_http_orders_and_ignores_bad_terms(self) -> None:
        api = APIClient()
        user = get_shared_manager()
        api.force_login(user)
        Team.objects.create(name="Ord A", wins=1, manager=user)
        Team.objects.create(name="Ord B", wins=9, manager=user)
        Team.objects.create(name="Ord C", wins=5, manager=user)
        response = api.get("/api/teams/", {"ordering": "-wins", "name": "Ord "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [t["name"] for t in response.json()["data"]], ["Ord B", "Ord C", "Ord A"]
        )
        bad = api.get("/api/teams/", {"ordering": "nonsense", "name": "Ord "})
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(
            [t["name"] for t in bad.json()["data"]], ["Ord A", "Ord B", "Ord C"]
        )
