"""ANG-01a — Form-bound write helpers (seam contract §3.1 / §3.2).

``form_errors_to_envelope``, ``django_validation_error_to_envelope``,
``validate_form_or_raise`` (``laserforce_simulator.api.exceptions``) and
``plain_form_data`` / ``merged_form_data`` (``laserforce_simulator.api.forms``).
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase

from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    django_validation_error_to_envelope,
    form_errors_to_envelope,
    validate_form_or_raise,
)
from laserforce_simulator.api.forms import merged_form_data, plain_form_data
from teams.forms import PlayerForm, TeamForm, TeamSlotForm
from teams.models import Player, Team


def _fail_a(value: str) -> None:
    raise ValidationError("first problem", code="code_a")


def _fail_b(value: str) -> None:
    raise ValidationError("second problem", code="code_b")


class _SampleForm(forms.Form):
    title = forms.CharField(max_length=5)
    count = forms.IntegerField(min_value=1)
    doubled = forms.CharField(required=False, validators=[_fail_a, _fail_b])

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("title") == "bad":
            # ``code`` deliberately omitted → envelope code ``"invalid"``.
            raise ValidationError("cross-field failure")
        return cleaned


# ---------------------------------------------------------------------------
# form_errors_to_envelope
# ---------------------------------------------------------------------------


class TestFormErrorsToEnvelope(SimpleTestCase):
    def test_field_errors_carry_field_name_and_django_code(self) -> None:
        form = _SampleForm(data={"title": "toolongvalue", "count": "0"})
        self.assertFalse(form.is_valid())
        errors = form_errors_to_envelope(form)
        by_field = {e["field"]: e for e in errors}
        self.assertEqual(by_field["title"]["code"], "max_length")
        self.assertEqual(by_field["count"]["code"], "min_value")
        for entry in errors:
            self.assertEqual(list(entry), ["code", "message", "field"])

    def test_messages_match_form_errors_byte_for_byte(self) -> None:
        form = _SampleForm(data={"title": "toolongvalue", "count": "0"})
        form.is_valid()
        errors = form_errors_to_envelope(form)
        self.assertEqual(
            [e["message"] for e in errors if e["field"] == "title"],
            list(form.errors["title"]),
        )

    def test_required_code(self) -> None:
        form = _SampleForm(data={})
        form.is_valid()
        codes = {(e["field"], e["code"]) for e in form_errors_to_envelope(form)}
        self.assertIn(("title", "required"), codes)
        self.assertIn(("count", "required"), codes)

    def test_non_field_error_maps_to_null_field_and_none_code_to_invalid(
        self,
    ) -> None:
        form = _SampleForm(data={"title": "bad", "count": "3"})
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form_errors_to_envelope(form),
            [{"code": "invalid", "message": "cross-field failure", "field": None}],
        )

    def test_one_entry_per_message(self) -> None:
        form = _SampleForm(data={"title": "ok", "count": "3", "doubled": "x"})
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form_errors_to_envelope(form),
            [
                {"code": "code_a", "message": "first problem", "field": "doubled"},
                {"code": "code_b", "message": "second problem", "field": "doubled"},
            ],
        )

    def test_valid_form_gives_no_errors(self) -> None:
        form = _SampleForm(data={"title": "ok", "count": "3"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form_errors_to_envelope(form), [])


# ---------------------------------------------------------------------------
# django_validation_error_to_envelope
# ---------------------------------------------------------------------------


class TestDjangoValidationErrorToEnvelope(SimpleTestCase):
    def test_error_dict(self) -> None:
        exc = ValidationError(
            {
                "name": [ValidationError("taken", code="unique")],
                NON_FIELD_ERRORS: [ValidationError("whole row bad")],
            }
        )
        result = django_validation_error_to_envelope(exc)
        self.assertIn({"code": "unique", "message": "taken", "field": "name"}, result)
        self.assertIn(
            {"code": "invalid", "message": "whole row bad", "field": None}, result
        )
        self.assertEqual(len(result), 2)

    def test_error_list(self) -> None:
        exc = ValidationError(
            [ValidationError("one", code="c1"), ValidationError("two")]
        )
        self.assertEqual(
            django_validation_error_to_envelope(exc),
            [
                {"code": "c1", "message": "one", "field": None},
                {"code": "invalid", "message": "two", "field": None},
            ],
        )

    def test_single_message(self) -> None:
        exc = ValidationError("Invalid role(s) in preferred_roles: bogus")
        self.assertEqual(
            django_validation_error_to_envelope(exc),
            [
                {
                    "code": "invalid",
                    "message": "Invalid role(s) in preferred_roles: bogus",
                    "field": None,
                }
            ],
        )


# ---------------------------------------------------------------------------
# validate_form_or_raise
# ---------------------------------------------------------------------------


class TestValidateFormOrRaise(SimpleTestCase):
    def test_valid_form_does_not_raise(self) -> None:
        form = _SampleForm(data={"title": "ok", "count": "3"})
        validate_form_or_raise(form)  # must not raise
        self.assertTrue(form.is_valid())

    def test_invalid_form_raises_envelope_validation_error(self) -> None:
        form = _SampleForm(data={"title": "bad", "count": "3"})
        with self.assertRaises(EnvelopeValidationError) as ctx:
            validate_form_or_raise(form)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(
            ctx.exception.errors,
            [{"code": "invalid", "message": "cross-field failure", "field": None}],
        )


# ---------------------------------------------------------------------------
# plain_form_data
# ---------------------------------------------------------------------------


class TestPlainFormData(SimpleTestCase):
    def test_json_dict_restricted_to_form_fields(self) -> None:
        self.assertEqual(
            plain_form_data(TeamForm, {"name": "X", "bogus": 1, "wins": 99}),
            {"name": "X"},
        )

    def test_querydict_multi_value_field_keeps_full_list(self) -> None:
        qd = QueryDict(
            "preferred_roles=scout&preferred_roles=medic&name=A&name=B&bogus=1"
        )
        self.assertEqual(
            plain_form_data(PlayerForm, qd),
            {"preferred_roles": ["scout", "medic"], "name": "B"},
        )

    def test_querydict_single_value_field_takes_last_value(self) -> None:
        qd = QueryDict("name=First&name=Last")
        self.assertEqual(plain_form_data(TeamForm, qd), {"name": "Last"})

    def test_empty_input(self) -> None:
        self.assertEqual(plain_form_data(TeamForm, {}), {})
        self.assertEqual(plain_form_data(TeamForm, QueryDict("")), {})


# ---------------------------------------------------------------------------
# merged_form_data (PATCH semantics)
# ---------------------------------------------------------------------------


class TestMergedFormData(TestCase):
    def test_sent_fields_override_instance_values(self) -> None:
        team = Team.objects.create(name="Merge Team")
        player = Player.objects.create(
            team=team, name="Merger", accuracy=40, preferred_roles=["scout"]
        )
        merged = merged_form_data(PlayerForm, player, {"accuracy": 90})
        self.assertEqual(merged["accuracy"], 90)
        self.assertEqual(merged["name"], "Merger")
        self.assertEqual(merged["preferred_roles"], ["scout"])
        self.assertEqual(merged["stamina"], 50)
        self.assertEqual(set(merged), set(PlayerForm.base_fields))

    def test_unknown_keys_are_dropped(self) -> None:
        team = Team.objects.create(name="Merge Team 2")
        merged = merged_form_data(TeamForm, team, {"wins": 7, "bogus": "x"})
        self.assertEqual(merged, {"name": "Merge Team 2"})

    def test_foreign_keys_come_out_as_pks(self) -> None:
        team = Team.objects.create(name="Slot Merge")
        cmd = Player.objects.create(team=team, name="Cmd")
        team.slot_commander = cmd
        team.save()
        merged = merged_form_data(TeamSlotForm, team, {})
        self.assertEqual(merged["slot_commander"], cmd.pk)
        self.assertIsNone(merged["slot_heavy"])
