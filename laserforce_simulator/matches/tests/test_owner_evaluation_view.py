"""CAR-02 — view tests for the owner-evaluation screen
(seam contract §3.3 / §4.2 / §6.4).

``GET /seasons/<int:season_id>/owner-evaluation/`` (URL name
``owner_evaluation``) is a GET-only league-screen mirroring the ``player_detail``
shell: 405 on non-GET, 404 on missing/non-completed Season, ``last_league_id``
session write, and the LOCKED DOM ids — the verdict badge, the hot-seat warning
(only on ``hot_seat``), the 3 ``-factor-*`` rows, the ``-total``, and the two
CTAs gated on ``is_fired`` / ``reassigned``.

Verdict-per-mood matrix is driven by hand-written ``OwnerEvaluation`` rows (the
writer's lazy fill is exercised in ``test_owner_evaluations_writer.py``); past-
Season browsability is exercised by reading a prior Season's eval screen. NO
simulator, NO simulated point totals.

These FAIL until the Code agent lands the ``owner_evaluation`` view + URL +
``templates/seasons/owner_evaluation.html`` + the writer + the model.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Match, OwnerEvaluation, Season
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(prefix: str):
    team, _ = make_team_with_slots(prefix)
    return team


def _make_league(name: str, *, current_team=None) -> League:
    return League.objects.create(
        name=name, mode="league", state="active", current_team=current_team
    )


def _make_completed_season(league, *, name, start_date, team_ids):
    return Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format="single_round_robin",
        state="completed",
        starting_team_ids_json=sorted(team_ids),
    )


def _add_win(season, team, opp) -> None:
    """Give ``team`` one dominant completed Match so the standings are
    well-defined for the writer's lazy fill."""
    match = Match.objects.create(
        team_red=team,
        team_blue=opp,
        season=season,
        red_round1_points=100,
        blue_round1_points=1,
        red_round2_points=100,
        blue_round2_points=1,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team,
        team_blue=opp,
        round_number=1,
        red_points=100,
        blue_points=1,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=opp,
        team_blue=team,
        round_number=2,
        red_points=1,
        blue_points=100,
        is_completed=True,
    )


def _write_eval(league, season, team, *, verdict, hot_seat_level=0):
    return OwnerEvaluation.objects.create(
        league=league,
        season=season,
        team_managed=team,
        wins_delta=0.1,
        playoffs_delta=0.2,
        wins_total=0.3,
        playoffs_total=0.4,
        verdict=verdict,
        hot_seat_level=hot_seat_level,
    )


def _url(season) -> str:
    return reverse("owner_evaluation", kwargs={"season_id": season.id})


# ---------------------------------------------------------------------------
# TestOwnerEvaluationRouting
# ---------------------------------------------------------------------------


class TestOwnerEvaluationRouting(TestCase):
    """URL reverse, 200, 404 (missing + non-completed), 405."""

    def _completed_with_eval(self):
        team = _make_team("RouteT")
        opp = _make_team("RouteO")
        league = _make_league("RouteL", current_team=team)
        season = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_win(season, team, opp)
        return league, season, team

    def test_reverse_resolves_to_expected_path(self) -> None:
        _league, season, _team = self._completed_with_eval()
        self.assertEqual(
            _url(season),
            f"/seasons/{season.id}/owner-evaluation/",
        )

    def test_get_completed_returns_200(self) -> None:
        _league, season, _team = self._completed_with_eval()
        response = self.client.get(_url(season))
        self.assertEqual(response.status_code, 200)

    def test_get_uses_owner_evaluation_template(self) -> None:
        _league, season, _team = self._completed_with_eval()
        response = self.client.get(_url(season))
        self.assertTemplateUsed(response, "seasons/owner_evaluation.html")

    def test_missing_season_returns_404(self) -> None:
        response = self.client.get(
            reverse("owner_evaluation", kwargs={"season_id": 99999})
        )
        self.assertEqual(response.status_code, 404)

    def test_non_completed_season_returns_404(self) -> None:
        # A draft Season has no eval row and is not completed ⇒ 404 (the eval
        # screen is only meaningful for a completed Season).
        league = _make_league("DraftL")
        draft = Season.objects.create(
            league=league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            state="draft",
        )
        response = self.client.get(_url(draft))
        self.assertEqual(response.status_code, 404)

    def test_post_returns_405(self) -> None:
        _league, season, _team = self._completed_with_eval()
        response = self.client.post(_url(season))
        self.assertEqual(response.status_code, 405)


# ---------------------------------------------------------------------------
# TestOwnerEvaluationSessionWrite
# ---------------------------------------------------------------------------


class TestOwnerEvaluationSessionWrite(TestCase):
    """GET writes ``last_league_id`` (int)."""

    def test_get_writes_last_league_id(self) -> None:
        team = _make_team("SessT")
        opp = _make_team("SessO")
        league = _make_league("SessL", current_team=team)
        season = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_win(season, team, opp)
        self.client.get(_url(season))
        self.assertEqual(self.client.session["last_league_id"], league.id)


# ---------------------------------------------------------------------------
# TestOwnerEvaluationDomIds
# ---------------------------------------------------------------------------


class TestOwnerEvaluationDomIds(TestCase):
    """The LOCKED DOM ids render."""

    def _setup(self, *, verdict, hot_seat_level=0):
        team = _make_team("DomT")
        opp = _make_team("DomO")
        league = _make_league("DomL", current_team=team)
        season = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_win(season, team, opp)
        # Hand-write the eval row to pin the verdict deterministically (the
        # writer's idempotent get_or_create leaves it untouched).
        _write_eval(
            league, season, team, verdict=verdict, hot_seat_level=hot_seat_level
        )
        return league, season, team

    def test_root_and_verdict_and_factor_and_total_ids_present(self) -> None:
        _league, season, _team = self._setup(verdict="retained")
        response = self.client.get(_url(season))
        for dom_id in (
            "owner-evaluation",
            "owner-evaluation-verdict",
            "owner-evaluation-factor-wins",
            "owner-evaluation-factor-playoffs",
            "owner-evaluation-factor-money",
            "owner-evaluation-total",
        ):
            self.assertContains(response, f'id="{dom_id}"')

    def test_verdict_badge_text_matches_verdict_value(self) -> None:
        _league, season, _team = self._setup(verdict="retained")
        response = self.client.get(_url(season))
        body = response.content.decode()
        idx = body.find('id="owner-evaluation-verdict"')
        self.assertGreater(idx, -1)
        # The literal verdict string is rendered in/near the badge element.
        self.assertIn("retained", body[idx : idx + 200])

    def test_money_factor_renders_dormant_zero(self) -> None:
        _league, season, _team = self._setup(verdict="retained")
        response = self.client.get(_url(season))
        self.assertContains(response, 'id="owner-evaluation-factor-money"')

    def test_overall_mood_total_renders_float_sum(self) -> None:
        # Regression: the overall-mood total must be the FLOAT sum of the three
        # cumulative totals (0.3 + 0.4 + 0.0 = 0.700). The original template
        # chained Django's ``add`` filter, which truncates each operand to int
        # (int(0.3)+int(0.4)+int(0.0)=0) and wrongly rendered "0.000". The view
        # now computes ``overall_mood`` and the template renders it directly.
        _league, season, _team = self._setup(verdict="retained")
        response = self.client.get(_url(season))
        self.assertContains(response, "0.700")
        # The buggy add-filter truncation rendered the total as "0.000".
        self.assertNotContains(response, "0.000")


# ---------------------------------------------------------------------------
# TestOwnerEvaluationVerdictMatrix
# ---------------------------------------------------------------------------


class TestOwnerEvaluationVerdictMatrix(TestCase):
    """The verdict-per-mood matrix: retained / hot_seat / fired drive the
    hot-seat warning + the two CTAs."""

    def _setup(self, *, verdict, hot_seat_level=0, reassigned=False):
        team = _make_team("MatT")
        other = _make_team("MatOther")
        opp = _make_team("MatO")
        league = _make_league("MatL", current_team=team)
        season = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_win(season, team, opp)
        _write_eval(
            league, season, team, verdict=verdict, hot_seat_level=hot_seat_level
        )
        if reassigned:
            # A fired-and-already-reassigned manager: current_team moved off the
            # row's team_managed.
            league.current_team = other
            league.save(update_fields=["current_team"])
        return league, season, team

    def test_retained_shows_start_next_cta_no_warning(self) -> None:
        _league, season, _team = self._setup(verdict="retained")
        response = self.client.get(_url(season))
        self.assertContains(response, 'id="owner-evaluation-cta-start-next"')
        self.assertNotContains(response, 'id="owner-evaluation-hot-seat-warning"')
        self.assertNotContains(response, 'id="owner-evaluation-cta-choose-team"')

    def test_hot_seat_shows_warning_and_start_next_cta(self) -> None:
        _league, season, _team = self._setup(verdict="hot_seat", hot_seat_level=1)
        response = self.client.get(_url(season))
        # hot_seat is NOT fired ⇒ the Start-Next CTA still renders.
        self.assertContains(response, 'id="owner-evaluation-cta-start-next"')
        # The hot-seat warning renders only on hot_seat.
        self.assertContains(response, 'id="owner-evaluation-hot-seat-warning"')
        self.assertNotContains(response, 'id="owner-evaluation-cta-choose-team"')

    def test_hot_seat_level_2_renders_warning(self) -> None:
        _league, season, _team = self._setup(verdict="hot_seat", hot_seat_level=2)
        response = self.client.get(_url(season))
        self.assertContains(response, 'id="owner-evaluation-hot-seat-warning"')

    def test_fired_unreassigned_shows_choose_team_cta(self) -> None:
        _league, season, _team = self._setup(verdict="fired")
        response = self.client.get(_url(season))
        # fired + not yet reassigned ⇒ Choose New Team CTA, no Start-Next.
        self.assertContains(response, 'id="owner-evaluation-cta-choose-team"')
        self.assertNotContains(response, 'id="owner-evaluation-cta-start-next"')
        # No hot-seat warning on a fired verdict.
        self.assertNotContains(response, 'id="owner-evaluation-hot-seat-warning"')

    def test_fired_unreassigned_choose_team_links_new_team_picker(self) -> None:
        league, season, _team = self._setup(verdict="fired")
        response = self.client.get(_url(season))
        expected = reverse("new_team_picker", kwargs={"league_id": league.id})
        self.assertContains(response, f'href="{expected}"')

    def test_fired_already_reassigned_shows_start_next_cta(self) -> None:
        # fired but already reassigned (current_team moved off team_managed) ⇒
        # the screen offers Start Next Season (the manager can roll now).
        _league, season, _team = self._setup(verdict="fired", reassigned=True)
        response = self.client.get(_url(season))
        self.assertContains(response, 'id="owner-evaluation-cta-start-next"')
        self.assertNotContains(response, 'id="owner-evaluation-cta-choose-team"')

    def test_context_is_fired_and_reassigned_flags(self) -> None:
        league, season, _team = self._setup(verdict="fired", reassigned=True)
        response = self.client.get(_url(season))
        self.assertTrue(response.context["is_fired"])
        self.assertTrue(response.context["reassigned"])

    def test_context_evaluation_row_present(self) -> None:
        league, season, team = self._setup(verdict="retained")
        response = self.client.get(_url(season))
        evaluation = response.context["evaluation"]
        self.assertEqual(evaluation.season_id, season.id)
        self.assertEqual(evaluation.verdict, "retained")


# ---------------------------------------------------------------------------
# TestOwnerEvaluationPastSeasonBrowsable
# ---------------------------------------------------------------------------


class TestOwnerEvaluationPastSeasonBrowsable(TestCase):
    """A PRIOR (past) Season's eval screen is reachable — the view ensures +
    reads that Season's row even though a later Season exists."""

    def test_prior_season_eval_renders_200(self) -> None:
        team = _make_team("PastT")
        opp = _make_team("PastO")
        league = _make_league("PastL", current_team=team)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[team.id, opp.id],
        )
        s2 = _make_completed_season(
            league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_win(s1, team, opp)
        _add_win(s2, team, opp)
        # Browse the PRIOR Season's eval screen directly.
        response = self.client.get(_url(s1))
        self.assertEqual(response.status_code, 200)
        # The displayed_season context is the prior Season.
        self.assertEqual(response.context["displayed_season"].id, s1.id)
        # Its eval row exists (the lazy writer ensured the in-tenure chain).
        self.assertTrue(
            OwnerEvaluation.objects.filter(league=league, season=s1).exists()
        )

    def test_sidebar_active_is_none(self) -> None:
        team = _make_team("SbT")
        opp = _make_team("SbO")
        league = _make_league("SbL", current_team=team)
        season = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_win(season, team, opp)
        response = self.client.get(_url(season))
        self.assertIsNone(response.context["sidebar_active"])


# ---------------------------------------------------------------------------
# FIN-05 — TestOwnerEvaluationFiredReasonFlavour
# ---------------------------------------------------------------------------
#
# Seam contract `.claude/worktrees/fin-05-luxury-tax-firing-seam-contract.md`
# §6 / §7.6: the eval screen renders a distinct flavour element (DOM id
# ``owner-evaluation-fired-reason``) keyed on ``evaluation.fired_reason``:
#   - ``"luxury_tax"`` ⇒ the luxury-tax flavour (stable substring "luxury tax")
#   - ``"owner_mood"`` AND legacy ``""`` ⇒ the mood-firing message
#   - a non-fired (retained / hot_seat) eval ⇒ NO fired-reason element
#
# We hand-write the eval row to pin the verdict + fired_reason deterministically
# (the writer's idempotent get_or_create leaves it untouched). Appended as a NEW
# class; no existing class above is modified. These WILL fail until the Code
# agent lands the model field + the template flavour element — the TDD red state.


def _write_eval_with_reason(
    league, season, team, *, verdict, fired_reason="", hot_seat_level=0
):
    return OwnerEvaluation.objects.create(
        league=league,
        season=season,
        team_managed=team,
        wins_delta=0.1,
        playoffs_delta=0.2,
        wins_total=0.3,
        playoffs_total=0.4,
        verdict=verdict,
        hot_seat_level=hot_seat_level,
        fired_reason=fired_reason,
    )


class TestOwnerEvaluationFiredReasonFlavour(TestCase):
    """The ``owner-evaluation-fired-reason`` flavour element keyed on
    ``fired_reason``."""

    def _setup(self, *, verdict, fired_reason="", hot_seat_level=0):
        team = _make_team("FrFlavT")
        opp = _make_team("FrFlavO")
        league = _make_league("FrFlavL", current_team=team)
        season = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_win(season, team, opp)
        _write_eval_with_reason(
            league,
            season,
            team,
            verdict=verdict,
            fired_reason=fired_reason,
            hot_seat_level=hot_seat_level,
        )
        return league, season, team

    def test_luxury_tax_renders_luxury_flavour(self) -> None:
        _league, season, _team = self._setup(
            verdict="fired", fired_reason="luxury_tax"
        )
        response = self.client.get(_url(season))
        self.assertContains(response, 'id="owner-evaluation-fired-reason"')
        # Stable substring of the luxury-tax flavour (case-insensitive guard).
        self.assertIn("luxury tax", response.content.decode().lower())

    def test_owner_mood_renders_mood_message(self) -> None:
        _league, season, _team = self._setup(
            verdict="fired", fired_reason="owner_mood"
        )
        response = self.client.get(_url(season))
        self.assertContains(response, 'id="owner-evaluation-fired-reason"')
        # The mood message is NOT the luxury-tax flavour.
        self.assertNotIn("luxury tax", response.content.decode().lower())

    def test_legacy_empty_reason_renders_mood_message(self) -> None:
        # A pre-FIN-05 fired row defaults fired_reason="" and renders as the
        # mood message (the template treats "" and "owner_mood" identically).
        _league, season, _team = self._setup(verdict="fired", fired_reason="")
        response = self.client.get(_url(season))
        self.assertContains(response, 'id="owner-evaluation-fired-reason"')
        self.assertNotIn("luxury tax", response.content.decode().lower())

    def test_retained_renders_no_fired_reason_element(self) -> None:
        _league, season, _team = self._setup(verdict="retained")
        response = self.client.get(_url(season))
        self.assertNotContains(response, 'id="owner-evaluation-fired-reason"')

    def test_hot_seat_renders_no_fired_reason_element(self) -> None:
        _league, season, _team = self._setup(
            verdict="hot_seat", hot_seat_level=1
        )
        response = self.client.get(_url(season))
        self.assertNotContains(response, 'id="owner-evaluation-fired-reason"')
