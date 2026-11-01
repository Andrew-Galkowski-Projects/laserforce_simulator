"""FIN-01 -- create-form ``finance_enabled`` tests.

``matches.forms.CreateLeagueForm`` declares a ``finance_enabled`` BooleanField
(``required=False``, ``initial=False``). ANG-08: the HTML create page went;
the persisted flag is pinned through ``POST /api/leagues/`` in
``test_api_leagues.py::TestLeagueCreate``. NO simulator.
"""

from __future__ import annotations

from django.test import TestCase

from matches.forms import CreateLeagueForm


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
