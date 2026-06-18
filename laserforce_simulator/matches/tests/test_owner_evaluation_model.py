"""CAR-02 — model/constraint tests for ``matches.models.OwnerEvaluation``
(seam contract §2 / §6.2).

Pins the locked field set / defaults / choices / ``Meta.ordering`` / the
``uniq_league_season_owner_evaluation`` UniqueConstraint, plus the
on_delete behaviour (CASCADE on League + Season delete; SET_NULL on the
managed Team delete).

These hand-build ``League`` / ``Season`` / ``Team`` rows directly — NO simulator,
NO simulated point totals. They FAIL until the Code agent lands the
``OwnerEvaluation`` model + migration ``0049_ownerevaluation``.
"""

from __future__ import annotations

from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from matches.models import League, OwnerEvaluation, Season
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "L") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_season(league: League, *, name: str = "Season 1") -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=date(2025, 1, 1),
        state="completed",
    )


def _make_team(prefix: str):
    team, _ = make_team_with_slots(prefix)
    return team


def _make_eval(
    league: League,
    season: Season,
    team,
    **overrides,
) -> OwnerEvaluation:
    defaults = dict(
        league=league,
        season=season,
        team_managed=team,
        wins_delta=0.1,
        playoffs_delta=0.2,
        wins_total=0.1,
        playoffs_total=0.2,
        verdict="retained",
        hot_seat_level=0,
    )
    defaults.update(overrides)
    return OwnerEvaluation.objects.create(**defaults)


# ---------------------------------------------------------------------------
# TestOwnerEvaluationFields
# ---------------------------------------------------------------------------


class TestOwnerEvaluationFields(TestCase):
    """Field defaults + persisted round-trip."""

    def test_money_delta_defaults_to_zero(self) -> None:
        league = _make_league("MoneyDeltaL")
        season = _make_season(league)
        team = _make_team("MD")
        ev = OwnerEvaluation.objects.create(
            league=league,
            season=season,
            team_managed=team,
            wins_delta=0.0,
            playoffs_delta=0.0,
            wins_total=0.0,
            playoffs_total=0.0,
            verdict="retained",
        )
        ev.refresh_from_db()
        self.assertEqual(ev.money_delta, 0.0)

    def test_money_total_defaults_to_zero(self) -> None:
        league = _make_league("MoneyTotalL")
        season = _make_season(league)
        team = _make_team("MT")
        ev = OwnerEvaluation.objects.create(
            league=league,
            season=season,
            team_managed=team,
            wins_delta=0.0,
            playoffs_delta=0.0,
            wins_total=0.0,
            playoffs_total=0.0,
            verdict="retained",
        )
        ev.refresh_from_db()
        self.assertEqual(ev.money_total, 0.0)

    def test_hot_seat_level_defaults_to_zero(self) -> None:
        league = _make_league("HslL")
        season = _make_season(league)
        team = _make_team("HSL")
        ev = OwnerEvaluation.objects.create(
            league=league,
            season=season,
            team_managed=team,
            wins_delta=0.0,
            playoffs_delta=0.0,
            wins_total=0.0,
            playoffs_total=0.0,
            verdict="fired",
        )
        ev.refresh_from_db()
        self.assertEqual(ev.hot_seat_level, 0)

    def test_created_at_auto_now_add(self) -> None:
        league = _make_league("CreatedAtL")
        season = _make_season(league)
        ev = _make_eval(league, season, _make_team("CA"))
        self.assertIsNotNone(ev.created_at)

    def test_full_round_trip(self) -> None:
        league = _make_league("RoundTripL")
        season = _make_season(league)
        team = _make_team("RT")
        ev = _make_eval(
            league,
            season,
            team,
            wins_delta=0.15,
            playoffs_delta=-0.2,
            wins_total=0.3,
            playoffs_total=-0.1,
            verdict="hot_seat",
            hot_seat_level=1,
        )
        ev.refresh_from_db()
        self.assertEqual(ev.league_id, league.id)
        self.assertEqual(ev.season_id, season.id)
        self.assertEqual(ev.team_managed_id, team.id)
        self.assertAlmostEqual(ev.wins_delta, 0.15, places=6)
        self.assertAlmostEqual(ev.playoffs_delta, -0.2, places=6)
        self.assertAlmostEqual(ev.wins_total, 0.3, places=6)
        self.assertAlmostEqual(ev.playoffs_total, -0.1, places=6)
        self.assertEqual(ev.verdict, "hot_seat")
        self.assertEqual(ev.hot_seat_level, 1)


# ---------------------------------------------------------------------------
# TestOwnerEvaluationVerdictChoices
# ---------------------------------------------------------------------------


class TestOwnerEvaluationVerdictChoices(TestCase):
    """``verdict`` choices == the locked 3 strings."""

    def test_verdict_choices_locked(self) -> None:
        choices = dict(OwnerEvaluation._meta.get_field("verdict").choices)
        self.assertEqual(set(choices), {"retained", "hot_seat", "fired"})

    def test_each_verdict_value_persists(self) -> None:
        league = _make_league("VerdictsL")
        team = _make_team("VC")
        for i, verdict in enumerate(("retained", "hot_seat", "fired")):
            season = _make_season(league, name=f"Season {i + 1}")
            ev = _make_eval(league, season, team, verdict=verdict)
            ev.refresh_from_db()
            self.assertEqual(ev.verdict, verdict)


# ---------------------------------------------------------------------------
# TestOwnerEvaluationMetaOrdering
# ---------------------------------------------------------------------------


class TestOwnerEvaluationMetaOrdering(TestCase):
    """``Meta.ordering == ["league_id", "season_id"]``."""

    def test_meta_ordering_declared(self) -> None:
        self.assertEqual(
            list(OwnerEvaluation._meta.ordering), ["league_id", "season_id"]
        )

    def test_default_queryset_ordered_by_league_then_season(self) -> None:
        # Two leagues, two seasons each, rows inserted out of order.
        la = _make_league("OrderA")
        lb = _make_league("OrderB")
        ta = _make_team("OA")
        tb = _make_team("OB")
        sa1 = _make_season(la, name="A-S1")
        sa2 = _make_season(la, name="A-S2")
        sb1 = _make_season(lb, name="B-S1")
        # Insert in a scrambled order.
        _make_eval(lb, sb1, tb)
        _make_eval(la, sa2, ta)
        _make_eval(la, sa1, ta)
        ordered = list(OwnerEvaluation.objects.values_list("league_id", "season_id"))
        self.assertEqual(ordered, sorted(ordered))


# ---------------------------------------------------------------------------
# TestUniqLeagueSeasonOwnerEvaluation
# ---------------------------------------------------------------------------


class TestUniqLeagueSeasonOwnerEvaluation(TestCase):
    """The ``uniq_league_season_owner_evaluation`` UniqueConstraint rejects a
    duplicate ``(league, season)`` row; same season across different leagues is
    fine."""

    def test_duplicate_league_season_rejected(self) -> None:
        league = _make_league("UniqL")
        season = _make_season(league)
        team = _make_team("UQ")
        _make_eval(league, season, team)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _make_eval(league, season, team)

    def test_constraint_name_declared(self) -> None:
        names = {c.name for c in OwnerEvaluation._meta.constraints}
        self.assertIn("uniq_league_season_owner_evaluation", names)

    def test_same_season_different_league_allowed(self) -> None:
        # A (league, season) pair is unique; the SAME season FK can never be
        # in two leagues, so we use distinct seasons to prove the constraint is
        # keyed on the PAIR (not on season alone). Two leagues, one row each.
        la = _make_league("UqLA")
        lb = _make_league("UqLB")
        sa = _make_season(la)
        sb = _make_season(lb)
        ta = _make_team("UQA")
        tb = _make_team("UQB")
        _make_eval(la, sa, ta)
        # A different (league, season) pair must succeed.
        _make_eval(lb, sb, tb)
        self.assertEqual(OwnerEvaluation.objects.count(), 2)


# ---------------------------------------------------------------------------
# TestOwnerEvaluationOnDelete
# ---------------------------------------------------------------------------


class TestOwnerEvaluationOnDelete(TestCase):
    """CASCADE on League / Season delete; SET_NULL on ``team_managed`` delete."""

    def test_league_delete_cascades(self) -> None:
        league = _make_league("CascadeLeagueL")
        season = _make_season(league)
        _make_eval(league, season, _make_team("CL"))
        self.assertEqual(OwnerEvaluation.objects.count(), 1)
        league.delete()
        self.assertEqual(OwnerEvaluation.objects.count(), 0)

    def test_season_delete_cascades(self) -> None:
        league = _make_league("CascadeSeasonL")
        season = _make_season(league)
        _make_eval(league, season, _make_team("CS"))
        self.assertEqual(OwnerEvaluation.objects.count(), 1)
        season.delete()
        self.assertEqual(OwnerEvaluation.objects.count(), 0)

    def test_team_managed_delete_set_null(self) -> None:
        league = _make_league("SetNullL")
        season = _make_season(league)
        team = _make_team("SN")
        ev = _make_eval(league, season, team)
        team.delete()
        ev.refresh_from_db()
        # The eval row survives; team_managed is nulled (snapshot history kept).
        self.assertIsNone(ev.team_managed_id)
        self.assertEqual(OwnerEvaluation.objects.count(), 1)

    def test_team_managed_nullable(self) -> None:
        # A null team_managed is valid at the ORM level (SET_NULL target).
        league = _make_league("NullTeamL")
        season = _make_season(league)
        ev = OwnerEvaluation.objects.create(
            league=league,
            season=season,
            team_managed=None,
            wins_delta=0.0,
            playoffs_delta=0.0,
            wins_total=0.0,
            playoffs_total=0.0,
            verdict="retained",
        )
        ev.refresh_from_db()
        self.assertIsNone(ev.team_managed_id)


# ---------------------------------------------------------------------------
# TestOwnerEvaluationRelatedNames
# ---------------------------------------------------------------------------


class TestOwnerEvaluationRelatedNames(TestCase):
    """``league.owner_evaluations`` / ``season.owner_evaluations`` reverse
    accessors; ``team_managed`` uses ``related_name="+"`` (no reverse)."""

    def test_league_reverse_accessor(self) -> None:
        league = _make_league("RevLeagueL")
        season = _make_season(league)
        ev = _make_eval(league, season, _make_team("RL"))
        self.assertEqual(list(league.owner_evaluations.all()), [ev])

    def test_season_reverse_accessor(self) -> None:
        league = _make_league("RevSeasonL")
        season = _make_season(league)
        ev = _make_eval(league, season, _make_team("RS"))
        self.assertEqual(list(season.owner_evaluations.all()), [ev])

    def test_team_managed_has_no_reverse_accessor(self) -> None:
        team = _make_team("NoRev")
        # related_name="+" suppresses the reverse accessor.
        self.assertFalse(hasattr(team, "owner_evaluations"))


# ---------------------------------------------------------------------------
# FIN-05 — TestOwnerEvaluationFiredReason
# ---------------------------------------------------------------------------
#
# Seam contract `.claude/worktrees/fin-05-luxury-tax-firing-seam-contract.md`
# §2.1 / §7.2: `OwnerEvaluation.fired_reason` is a CharField with default `""`
# and the three FIRED_REASON_CHOICES (`""`, `"owner_mood"`, `"luxury_tax"`).
# Appended as a NEW class; no existing class above is modified.


class TestOwnerEvaluationFiredReason(TestCase):
    """``fired_reason`` default ``""`` + the three FIRED_REASON_CHOICES."""

    def test_fired_reason_defaults_to_empty_string(self) -> None:
        league = _make_league("FrDefaultL")
        season = _make_season(league)
        team = _make_team("FRD")
        # Create WITHOUT specifying fired_reason — it must default to "".
        ev = OwnerEvaluation.objects.create(
            league=league,
            season=season,
            team_managed=team,
            wins_delta=0.0,
            playoffs_delta=0.0,
            wins_total=0.0,
            playoffs_total=0.0,
            verdict="retained",
        )
        ev.refresh_from_db()
        self.assertEqual(ev.fired_reason, "")

    def test_fired_reason_choices_locked(self) -> None:
        choices = dict(OwnerEvaluation._meta.get_field("fired_reason").choices)
        self.assertEqual(set(choices), {"", "owner_mood", "luxury_tax"})

    def test_each_fired_reason_value_persists(self) -> None:
        league = _make_league("FrPersistL")
        team = _make_team("FRP")
        for i, reason in enumerate(("", "owner_mood", "luxury_tax")):
            season = _make_season(league, name=f"Season {i + 1}")
            ev = _make_eval(
                league, season, team, verdict="fired", fired_reason=reason
            )
            ev.refresh_from_db()
            self.assertEqual(ev.fired_reason, reason)


# ---------------------------------------------------------------------------
# FIN-05 — TestLeagueChallengeFiredLuxuryTax
# ---------------------------------------------------------------------------
#
# Seam contract §2.2 / §7.2: `League.challenge_fired_luxury_tax` is a
# BooleanField defaulting `False`. Appended as a NEW class.


class TestLeagueChallengeFiredLuxuryTax(TestCase):
    """``League.challenge_fired_luxury_tax`` default ``False``."""

    def test_default_false(self) -> None:
        league = League.objects.create(name="ChalDefaultL", mode="league")
        league.refresh_from_db()
        self.assertFalse(league.challenge_fired_luxury_tax)

    def test_persists_true(self) -> None:
        league = League.objects.create(
            name="ChalTrueL", mode="league", challenge_fired_luxury_tax=True
        )
        league.refresh_from_db()
        self.assertTrue(league.challenge_fired_luxury_tax)
