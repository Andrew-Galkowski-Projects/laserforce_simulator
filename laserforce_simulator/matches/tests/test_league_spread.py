"""CRE-02 — the transient **League spread** selector on the Advanced create form.

The seam contract is locked at ``.claude/worktrees/cre-02-seam-contract.md``
(§5 for the form field, §6 for the create writer + ``_template_to_form_data``,
§7 for the template block, §8 for the test boundary).

THE VIEW-LAYER FLAKINESS RULE (contract §8.3, MANDATORY). The single create
writer ``_create_league_and_season`` builds ``rng = random.Random()``
**UNSEEDED**. No test in this file may therefore assert anything about team
strength, tier ordering, or the strong-vs-weak gap. These tests assert form
validity, HTTP status, object counts and DOM ids only. All strength assertions
live at the ``_generate_teams`` layer with an explicitly seeded
``random.Random(42)`` — see ``teams/tests/test_generate_teams_tiered.py``.

These tests exercise the REAL generator and the REAL form (no ``mock.patch``
of ``_generate_teams`` or ``compute_tier_means``), so signature drift on the
seam surfaces here.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from matches.forms import CreateLeagueForm
from matches.league_templates import LEAGUE_TEMPLATES
from matches.league_views import _template_to_form_data
from matches.models import League, Season
from teams.models import Team
from teams.player_generator import LEAGUE_SPREAD_DELTAS

# Hard-coded contract pin (§5.1) — deliberately NOT imported from
# ``CreateLeagueForm.LEAGUE_SPREAD_CHOICES`` / ``LEAGUE_SPREAD_DELTAS``, which
# would be tautological. Exactly three tuples, in this order, with these exact
# value tokens and human labels.
_EXPECTED_SPREAD_CHOICES = (
    ("even", "Even"),
    ("tiered", "Tiered"),
    ("steep", "Steep"),
)

_SPREAD_TOKENS = tuple(token for token, _ in _EXPECTED_SPREAD_CHOICES)

# The locked widget DOM id (§5.2 / §7).
_SPREAD_DOM_ID = "league-create-league-spread"


def _advanced_payload(**overrides) -> dict:
    """A full valid Advanced-create POST payload.

    Mirrors ``matches/tests/test_league_create.py::_valid_payload`` — the
    field set the pre-CRE-02 Advanced form already accepts. ``league_spread``
    is deliberately NOT included by default so the omission path is the
    baseline; callers opt in via ``_advanced_payload(league_spread="tiered")``.
    """
    payload = {
        "league_name": "Spread League",
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


def _chooser_post(key: str, *, league_name: str, difficulty: str = "medium") -> dict:
    return {
        "template": key,
        "league_name": league_name,
        "difficulty": difficulty,
    }


# ---------------------------------------------------------------------------
# §5 — the form field
# ---------------------------------------------------------------------------


class TestCre02LeagueSpreadFormField(TestCase):
    """CRE-02 — ``CreateLeagueForm.league_spread`` declaration + validation."""

    def test_form_has_a_league_spread_field(self) -> None:
        self.assertIn("league_spread", CreateLeagueForm().fields)

    def test_league_spread_is_not_required(self) -> None:
        self.assertFalse(CreateLeagueForm().fields["league_spread"].required)

    def test_league_spread_initial_is_even(self) -> None:
        self.assertEqual(CreateLeagueForm().fields["league_spread"].initial, "even")

    def test_league_spread_choices_are_exactly_the_three_tokens(self) -> None:
        choices = CreateLeagueForm().fields["league_spread"].choices
        self.assertEqual(
            [(value, label) for value, label in choices],
            list(_EXPECTED_SPREAD_CHOICES),
        )

    def test_league_spread_choices_agree_with_the_delta_table(self) -> None:
        """CRE-02 review fix — the form's token vocabulary and the generator's
        delta table are two independently-pinned literals. If they ever drift,
        ``_create_league_and_season`` silently builds an Even league: the form
        validates the new token, then ``LEAGUE_SPREAD_DELTAS.get(spread, 0.0)``
        falls back to delta 0. Nothing else in the suite would catch that, so
        pin the two key sets to each other here.
        """
        choice_tokens = {
            value
            for value, _label in CreateLeagueForm().fields["league_spread"].choices
        }
        self.assertEqual(choice_tokens, set(LEAGUE_SPREAD_DELTAS))

    def test_league_spread_label_is_league_spread(self) -> None:
        self.assertEqual(
            CreateLeagueForm().fields["league_spread"].label, "League spread"
        )

    def test_league_spread_widget_carries_the_locked_dom_id_and_class(self) -> None:
        attrs = CreateLeagueForm().fields["league_spread"].widget.attrs
        self.assertEqual(attrs.get("id"), _SPREAD_DOM_ID)
        self.assertIn("form-select", attrs.get("class", ""))

    # -- validation ---------------------------------------------------------

    def test_form_is_valid_with_each_spread_token(self) -> None:
        for token in _SPREAD_TOKENS:
            with self.subTest(league_spread=token):
                form = CreateLeagueForm(data=_advanced_payload(league_spread=token))
                self.assertTrue(form.is_valid(), form.errors.as_json())
                self.assertEqual(form.cleaned_data["league_spread"], token)

    def test_form_is_valid_with_league_spread_omitted(self) -> None:
        form = CreateLeagueForm(data=_advanced_payload())
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["league_spread"], "")

    def test_form_is_valid_with_league_spread_blank(self) -> None:
        form = CreateLeagueForm(data=_advanced_payload(league_spread=""))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["league_spread"], "")

    def test_form_rejects_an_unknown_league_spread_token(self) -> None:
        form = CreateLeagueForm(data=_advanced_payload(league_spread="insane"))
        self.assertFalse(form.is_valid())
        self.assertIn("league_spread", form.errors)

    # -- rendered GET -------------------------------------------------------

    def test_advanced_get_renders_the_locked_dom_id(self) -> None:
        response = self.client.get(reverse("league_create_advanced"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'id="{_SPREAD_DOM_ID}"', response.content.decode())

    def test_advanced_get_renders_every_spread_option(self) -> None:
        body = self.client.get(reverse("league_create_advanced")).content.decode()
        for token, label in _EXPECTED_SPREAD_CHOICES:
            with self.subTest(league_spread=token):
                self.assertIn(f'value="{token}"', body)
                self.assertIn(label, body)

    def test_advanced_get_renders_a_label_bound_to_the_widget(self) -> None:
        body = self.client.get(reverse("league_create_advanced")).content.decode()
        self.assertIn(f'for="{_SPREAD_DOM_ID}"', body)

    def test_chooser_get_does_not_render_a_spread_selector(self) -> None:
        """Decision 5 — the one-click chooser has no spread selector."""
        body = self.client.get(reverse("league_create")).content.decode()
        self.assertNotIn(f'id="{_SPREAD_DOM_ID}"', body)


# ---------------------------------------------------------------------------
# §6.2 — the Advanced create POST
#
# NO STRENGTH ASSERTIONS IN THIS CLASS (contract §8.3) — the writer's RNG is
# unseeded, so only form validity, HTTP status, object counts and DOM ids are
# assertable here.
# ---------------------------------------------------------------------------


class TestCre02AdvancedCreateWithSpread(TestCase):
    """CRE-02 — ``POST /leagues/create/advanced/`` with each spread value."""

    def _post(self, name: str, **overrides):
        return self.client.post(
            reverse("league_create_advanced"),
            _advanced_payload(league_name=name, **overrides),
        )

    def _assert_created(self, name: str, expected_teams: int = 4) -> None:
        league = League.objects.get(name=name)
        season = league.seasons.get()
        self.assertEqual(season.state, "draft")
        self.assertEqual(season.teams.count(), expected_teams)

    def test_tiered_post_redirects(self) -> None:
        response = self._post("Tiered League", league_spread="tiered")
        self.assertEqual(response.status_code, 302)

    def test_tiered_post_creates_league_season_and_teams(self) -> None:
        self._post("Tiered League", league_spread="tiered")
        self._assert_created("Tiered League")

    def test_steep_post_redirects(self) -> None:
        response = self._post("Steep League", league_spread="steep")
        self.assertEqual(response.status_code, 302)

    def test_steep_post_creates_league_season_and_teams(self) -> None:
        self._post("Steep League", league_spread="steep")
        self._assert_created("Steep League")

    def test_even_post_creates_league_season_and_teams(self) -> None:
        self._post("Even League", league_spread="even")
        self._assert_created("Even League")

    def test_omitted_spread_still_succeeds(self) -> None:
        """``required=False`` — the Even fallback path."""
        response = self._post("Omitted League")
        self.assertEqual(response.status_code, 302)
        self._assert_created("Omitted League")

    def test_blank_spread_still_succeeds(self) -> None:
        response = self._post("Blank League", league_spread="")
        self.assertEqual(response.status_code, 302)
        self._assert_created("Blank League")

    def test_every_spread_token_creates_a_league(self) -> None:
        for token in _SPREAD_TOKENS:
            with self.subTest(league_spread=token):
                name = f"L-{token}"
                response = self._post(name, league_spread=token)
                self.assertEqual(response.status_code, 302)
                self._assert_created(name)

    def test_spread_works_at_eight_teams(self) -> None:
        response = self._post("Eight Steep", league_spread="steep", num_teams="8")
        self.assertEqual(response.status_code, 302)
        self._assert_created("Eight Steep", expected_teams=8)

    def test_redirect_target_is_season_standings(self) -> None:
        response = self._post("Redirect League", league_spread="tiered")
        season = League.objects.get(name="Redirect League").seasons.get()
        self.assertEqual(
            response["Location"], reverse("season_standings", args=[season.id])
        )

    def test_spread_creates_no_extra_leagues_or_seasons(self) -> None:
        self._post("Single League", league_spread="steep")
        self.assertEqual(League.objects.count(), 1)
        self.assertEqual(Season.objects.count(), 1)

    # -- rejection path -----------------------------------------------------

    def test_unknown_spread_value_re_renders_the_form(self) -> None:
        response = self._post("Bad League", league_spread="insane")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "leagues/create_advanced.html")

    def test_unknown_spread_value_creates_nothing(self) -> None:
        self._post("Bad League", league_spread="insane")
        self.assertEqual(League.objects.count(), 0)
        self.assertEqual(Season.objects.count(), 0)
        self.assertEqual(Team.objects.count(), 0)


# ---------------------------------------------------------------------------
# §6.1 — the chooser is ALWAYS Even
# ---------------------------------------------------------------------------


class TestCre02TemplateFormDataIsAlwaysEven(TestCase):
    """CRE-02 — ``_template_to_form_data`` hard-codes ``league_spread="even"``."""

    def test_every_template_form_data_carries_even(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                data = _template_to_form_data(
                    template, league_name="X", difficulty="medium"
                )
                self.assertEqual(data["league_spread"], "even")

    def test_league_spread_key_is_present_on_every_template(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                data = _template_to_form_data(
                    template, league_name="X", difficulty="medium"
                )
                self.assertIn("league_spread", data)

    def test_every_template_form_data_still_validates(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                form = CreateLeagueForm(
                    data=_template_to_form_data(
                        template, league_name="X", difficulty="medium"
                    )
                )
                self.assertTrue(form.is_valid(), form.errors.as_json())
                self.assertEqual(form.cleaned_data["league_spread"], "even")

    def test_template_to_form_data_signature_takes_no_spread_argument(self) -> None:
        """§6.1 — the signature does NOT gain a parameter."""
        import inspect

        params = inspect.signature(_template_to_form_data).parameters
        self.assertEqual(list(params), ["template", "league_name", "difficulty"])

    def test_chooser_post_still_creates_league_and_draft_season(self) -> None:
        for template in LEAGUE_TEMPLATES:
            with self.subTest(template=template.key):
                name = f"C-{template.key}"
                response = self.client.post(
                    reverse("league_create"),
                    _chooser_post(template.key, league_name=name),
                )
                self.assertEqual(response.status_code, 302)
                season = League.objects.get(name=name).seasons.get()
                self.assertEqual(season.state, "draft")
                self.assertEqual(season.teams.count(), template.num_teams)
