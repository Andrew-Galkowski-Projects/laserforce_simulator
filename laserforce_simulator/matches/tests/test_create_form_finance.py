"""FIN-01 — create-form ``finance_enabled`` tests (seam contract §6 / §8).

``matches.forms.CreateLeagueForm`` gains a ``finance_enabled`` BooleanField
(``required=False``, ``initial=False``, DOM id ``league-create-finance-enabled``).
``league_create`` passes ``finance_enabled=form.cleaned_data["finance_enabled"]``
into ``League.objects.create(...)``; the template renders the field row.

Builds on the LG-01b ``_valid_payload`` create-flow pattern (the ``map_mode``
default included so the existing 7+ field validation surface is satisfied). NO
simulator, NO simulated point totals.

These FAIL until the Code agent lands the form field + the view wiring + the
template row.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from matches.forms import CreateLeagueForm
from matches.models import League


def _valid_payload(**overrides) -> dict:
    payload = {
        "league_name": "Finance League",
        "season_name": "Season 1",
        "start_date": "2026-06-01",
        "num_teams": "4",
        "schedule_format": "single_round_robin",
        "mean": "50",
        "std_dev": "15",
        "map_mode": "none",
    }
    payload.update(overrides)
    return payload


# ===========================================================================
# §6 — TestCreateLeagueFormFinanceField
# ===========================================================================


class TestCreateLeagueFormFinanceField(TestCase):
    """The form declares ``finance_enabled`` (optional, default off)."""

    def test_form_has_finance_enabled_field(self) -> None:
        self.assertIn("finance_enabled", CreateLeagueForm().fields)

    def test_finance_enabled_not_required(self) -> None:
        self.assertFalse(CreateLeagueForm().fields["finance_enabled"].required)

    def test_finance_enabled_initial_is_false(self) -> None:
        self.assertFalse(CreateLeagueForm().fields["finance_enabled"].initial)

    def test_unchecked_payload_is_valid_and_false(self) -> None:
        form = CreateLeagueForm(data=_valid_payload())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["finance_enabled"])

    def test_checked_payload_is_valid_and_true(self) -> None:
        form = CreateLeagueForm(data=_valid_payload(finance_enabled="on"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["finance_enabled"])


# ===========================================================================
# §6 — TestCreateLeagueFinanceDomId
# ===========================================================================


class TestCreateLeagueFinanceDomId(TestCase):
    """The create page renders the ``league-create-finance-enabled`` DOM id."""

    def test_finance_enabled_dom_id_rendered(self) -> None:
        response = self.client.get(reverse("league_create"))
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="league-create-finance-enabled"', response.content.decode())


# ===========================================================================
# §6 — TestCreateLeaguePersistsFinanceEnabled
# ===========================================================================


class TestCreateLeaguePersistsFinanceEnabled(TestCase):
    """A checked POST persists ``League.finance_enabled=True``; an unchecked POST
    persists ``False`` (the default)."""

    def test_checked_post_persists_true(self) -> None:
        response = self.client.post(
            reverse("league_create"),
            data=_valid_payload(league_name="FinOnLeague", finance_enabled="on"),
        )
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="FinOnLeague")
        self.assertTrue(league.finance_enabled)

    def test_unchecked_post_persists_false(self) -> None:
        response = self.client.post(
            reverse("league_create"),
            data=_valid_payload(league_name="FinOffLeague"),
        )
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="FinOffLeague")
        self.assertFalse(league.finance_enabled)
