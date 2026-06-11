"""LG-04 — Django ``TestCase`` tests for the ``matches.models.PlayerSeasonRating``
model + migration (seam contract §1 / §7.2).

``PlayerSeasonRating`` is an immutable per-Season snapshot of a Player's 19 stat
ratings + age + overall_rating, written as a baseline row at ``league_create``
and a developed row at each ``next_season`` rollover. Read-only audit trail —
the live ``teams.Player`` fields stay the Simulator's source of truth.

These assertions pin the SCHEMA (field shape, nullability, the
``uniq_player_season_rating`` constraint, CASCADE on both FKs, ``Meta.ordering``,
the ``Player.season_ratings`` / ``Season.player_ratings`` related names) — NOT
any simulated value. They FAIL until the Code agent lands the model + the
``0048_playerseasonrating`` migration; that is expected for the parallel build.
"""

from __future__ import annotations

from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from matches.development import STAT_FIELDS
from matches.models import League, PlayerSeasonRating, Season
from teams.models import Player, Team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_season(name: str = "S1") -> Season:
    league = League.objects.create(name=f"{name}-league")
    return Season.objects.create(league=league, name=name, start_date=date(2026, 1, 1))


def _make_player(name: str = "P") -> Player:
    team = Team.objects.create(name=f"{name}-team")
    return Player.objects.create(team=team, name=name)


def _rating_kwargs(**overrides) -> dict:
    """A complete, valid set of 19 stat fields + age + overall_rating."""
    kwargs = {name: 50 for name in STAT_FIELDS}
    kwargs["age"] = 25
    kwargs["overall_rating"] = 50.0
    kwargs["potential"] = None
    kwargs.update(overrides)
    return kwargs


# ===========================================================================
# §7.2 — Field shape
# ===========================================================================


class TestPlayerSeasonRatingFields(TestCase):
    """19 stat fields present (incl. capital-O ``Offensive_synergy``), ``age``
    nullable, ``overall_rating`` float, ``potential`` nullable."""

    def test_all_19_stat_fields_present_on_model(self) -> None:
        field_names = {f.name for f in PlayerSeasonRating._meta.get_fields()}
        for name in STAT_FIELDS:
            self.assertIn(name, field_names, f"missing stat field {name!r}")

    def test_offensive_synergy_capital_o_present(self) -> None:
        field_names = {f.name for f in PlayerSeasonRating._meta.get_fields()}
        self.assertIn("Offensive_synergy", field_names)

    def test_age_is_nullable(self) -> None:
        season = _make_season()
        player = _make_player()
        # A baseline row may copy a None age verbatim.
        row = PlayerSeasonRating.objects.create(
            player=player, season=season, **_rating_kwargs(age=None)
        )
        row.refresh_from_db()
        self.assertIsNone(row.age)

    def test_overall_rating_is_float(self) -> None:
        season = _make_season()
        player = _make_player()
        row = PlayerSeasonRating.objects.create(
            player=player, season=season, **_rating_kwargs(overall_rating=63.4)
        )
        row.refresh_from_db()
        self.assertIsInstance(row.overall_rating, float)
        self.assertAlmostEqual(row.overall_rating, 63.4, places=4)

    def test_potential_is_nullable_and_defaults_none(self) -> None:
        season = _make_season()
        player = _make_player()
        row = PlayerSeasonRating.objects.create(
            player=player, season=season, **_rating_kwargs(potential=None)
        )
        row.refresh_from_db()
        self.assertIsNone(row.potential)

    def test_stat_values_round_trip(self) -> None:
        season = _make_season()
        player = _make_player()
        kwargs = _rating_kwargs()
        kwargs["accuracy"] = 88
        kwargs["Offensive_synergy"] = 12
        row = PlayerSeasonRating.objects.create(player=player, season=season, **kwargs)
        row.refresh_from_db()
        self.assertEqual(row.accuracy, 88)
        self.assertEqual(row.Offensive_synergy, 12)


# ===========================================================================
# §7.2 — unique(player, season)
# ===========================================================================


class TestPlayerSeasonRatingUniqueConstraint(TestCase):
    """``uniq_player_season_rating`` rejects a duplicate (player, season) but
    allows the same player across different seasons."""

    def test_duplicate_player_season_rejected(self) -> None:
        season = _make_season()
        player = _make_player()
        PlayerSeasonRating.objects.create(
            player=player, season=season, **_rating_kwargs()
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PlayerSeasonRating.objects.create(
                    player=player, season=season, **_rating_kwargs()
                )

    def test_same_player_across_different_seasons_allowed(self) -> None:
        s1 = _make_season("Sa")
        # A second Season in the SAME league.
        s2 = Season.objects.create(
            league=s1.league, name="Sb", start_date=date(2027, 1, 1)
        )
        player = _make_player()
        PlayerSeasonRating.objects.create(player=player, season=s1, **_rating_kwargs())
        PlayerSeasonRating.objects.create(player=player, season=s2, **_rating_kwargs())
        self.assertEqual(PlayerSeasonRating.objects.filter(player=player).count(), 2)

    def test_constraint_is_named_uniq_player_season_rating(self) -> None:
        names = {c.name for c in PlayerSeasonRating._meta.constraints}
        self.assertIn("uniq_player_season_rating", names)


# ===========================================================================
# §7.2 — CASCADE on Player + Season delete
# ===========================================================================


class TestPlayerSeasonRatingCascade(TestCase):
    """Deleting a Player OR a Season drops its rating snapshots (CASCADE)."""

    def test_deleting_player_cascades_rating_rows(self) -> None:
        season = _make_season()
        player = _make_player()
        PlayerSeasonRating.objects.create(
            player=player, season=season, **_rating_kwargs()
        )
        player.delete()
        self.assertEqual(PlayerSeasonRating.objects.count(), 0)

    def test_deleting_season_cascades_rating_rows(self) -> None:
        season = _make_season()
        player = _make_player()
        PlayerSeasonRating.objects.create(
            player=player, season=season, **_rating_kwargs()
        )
        season.delete()
        self.assertEqual(PlayerSeasonRating.objects.count(), 0)


# ===========================================================================
# §7.2 — Meta.ordering + related_names
# ===========================================================================


class TestPlayerSeasonRatingMeta(TestCase):
    """``Meta.ordering == ["player_id", "season_id"]``; related names
    ``Player.season_ratings`` / ``Season.player_ratings``."""

    def test_meta_ordering(self) -> None:
        self.assertEqual(
            list(PlayerSeasonRating._meta.ordering), ["player_id", "season_id"]
        )

    def test_player_related_name_season_ratings(self) -> None:
        season = _make_season()
        player = _make_player()
        row = PlayerSeasonRating.objects.create(
            player=player, season=season, **_rating_kwargs()
        )
        self.assertIn(row, player.season_ratings.all())

    def test_season_related_name_player_ratings(self) -> None:
        season = _make_season()
        player = _make_player()
        row = PlayerSeasonRating.objects.create(
            player=player, season=season, **_rating_kwargs()
        )
        self.assertIn(row, season.player_ratings.all())

    def test_ordering_applied_in_queryset(self) -> None:
        season = _make_season()
        p1 = _make_player("Pa")
        p2 = _make_player("Pb")
        # Insert out of player-id order; Meta.ordering should re-sort ascending.
        PlayerSeasonRating.objects.create(player=p2, season=season, **_rating_kwargs())
        PlayerSeasonRating.objects.create(player=p1, season=season, **_rating_kwargs())
        ordered_player_ids = list(
            PlayerSeasonRating.objects.values_list("player_id", flat=True)
        )
        self.assertEqual(ordered_player_ids, sorted([p1.id, p2.id]))
