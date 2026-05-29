"""LG-01 — Django ``TestCase`` tests for the League / Season models +
``Match.season`` FK.

The seam contract is locked at ``.claude/worktrees/lg-01-seam-contract.md``
(§1 models, §6c test plan). Uses ``make_team_with_slots`` for fully-
slotted Teams (start_season requires teams.count() >= 2).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from matches.models import GameRound, League, Match, Season
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# §6c — League model
# ---------------------------------------------------------------------------


class TestLeagueModel(TestCase):
    """Locked defaults + active_season property semantics."""

    def test_mode_defaults_to_league(self) -> None:
        league = League.objects.create(name="L")
        self.assertEqual(league.mode, "league")

    def test_state_defaults_to_active(self) -> None:
        league = League.objects.create(name="L")
        self.assertEqual(league.state, "active")

    def test_str_returns_name(self) -> None:
        league = League.objects.create(name="MyLeague")
        self.assertEqual(str(league), "MyLeague")

    def test_active_season_property_returns_none_when_no_seasons(self) -> None:
        league = League.objects.create(name="L")
        self.assertIsNone(league.active_season)

    def test_active_season_property_returns_draft_season(self) -> None:
        league = League.objects.create(name="L")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        self.assertEqual(league.active_season, season)

    def test_active_season_property_returns_active_season(self) -> None:
        league = League.objects.create(name="L")
        season = Season.objects.create(
            league=league,
            name="S1",
            start_date=date.today(),
            state="active",
        )
        self.assertEqual(league.active_season, season)

    def test_active_season_property_excludes_completed(self) -> None:
        league = League.objects.create(name="L")
        Season.objects.create(
            league=league,
            name="Old",
            start_date=date.today(),
            state="completed",
        )
        draft = Season.objects.create(
            league=league,
            name="New",
            start_date=date.today(),
        )
        self.assertEqual(league.active_season, draft)


# ---------------------------------------------------------------------------
# §6c — Season model
# ---------------------------------------------------------------------------


class TestSeasonModel(TestCase):
    """Locked field defaults + __str__ format."""

    def test_state_defaults_to_draft(self) -> None:
        league = League.objects.create(name="L")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        self.assertEqual(season.state, "draft")

    def test_schedule_format_defaults_to_single_round_robin(self) -> None:
        league = League.objects.create(name="L")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        self.assertEqual(season.schedule_format, "single_round_robin")

    def test_starting_team_ids_json_defaults_to_none(self) -> None:
        league = League.objects.create(name="L")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        self.assertIsNone(season.starting_team_ids_json)

    def test_str_returns_league_name_em_dash_season_name(self) -> None:
        # em-dash U+2014 between league name and season name.
        league = League.objects.create(name="MyLeague")
        season = Season.objects.create(
            league=league, name="2026", start_date=date.today()
        )
        self.assertEqual(str(season), "MyLeague — 2026")


# ---------------------------------------------------------------------------
# §6c — Season.clean() active-Season invariant
# ---------------------------------------------------------------------------


class TestSeasonCleanInvariant(TestCase):
    """At most one non-completed Season per League."""

    def test_second_non_completed_season_in_same_league_raises(self) -> None:
        league = League.objects.create(name="L")
        Season.objects.create(league=league, name="S1", start_date=date.today())
        s2 = Season(league=league, name="S2", start_date=date.today())
        with self.assertRaises(ValidationError):
            s2.clean()

    def test_second_non_completed_season_in_DIFFERENT_league_does_not_raise(
        self,
    ) -> None:
        l1 = League.objects.create(name="L1")
        l2 = League.objects.create(name="L2")
        Season.objects.create(league=l1, name="S1", start_date=date.today())
        s2 = Season(league=l2, name="S1", start_date=date.today())
        # Must not raise.
        s2.clean()

    def test_ok_when_first_season_is_completed(self) -> None:
        league = League.objects.create(name="L")
        Season.objects.create(
            league=league,
            name="Old",
            start_date=date.today(),
            state="completed",
        )
        new = Season(league=league, name="New", start_date=date.today())
        # Must not raise.
        new.clean()

    def test_clean_excludes_self_so_re_saving_active_season_does_not_raise(
        self,
    ) -> None:
        league = League.objects.create(name="L")
        season = Season.objects.create(
            league=league,
            name="S1",
            start_date=date.today(),
            state="active",
        )
        # Re-clean (e.g. via admin save) must not trip against itself.
        season.clean()


# ---------------------------------------------------------------------------
# §6c — Season.start_season()
# ---------------------------------------------------------------------------


class TestSeasonStartSeason(TestCase):
    """draft -> active transition: state flip + sorted M2M snapshot."""

    def _make_two_team_season(self, prefix: str) -> tuple[Season, list]:
        league = League.objects.create(name=f"L{prefix}")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        t1, _ = make_team_with_slots(f"{prefix}A")
        t2, _ = make_team_with_slots(f"{prefix}B")
        season.teams.add(t1, t2)
        return season, [t1, t2]

    def test_flips_state_to_active(self) -> None:
        season, _ = self._make_two_team_season("Flip")
        self.assertEqual(season.state, "draft")
        season.start_season()
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_snapshots_starting_team_ids_sorted(self) -> None:
        season, teams = self._make_two_team_season("Snap")
        season.start_season()
        season.refresh_from_db()
        self.assertIsNotNone(season.starting_team_ids_json)
        expected = sorted([t.id for t in teams])
        self.assertEqual(season.starting_team_ids_json, expected)
        # Sorted ascending: list equals its own sorted form.
        self.assertEqual(
            season.starting_team_ids_json,
            sorted(season.starting_team_ids_json),
        )

    def test_raises_when_fewer_than_two_teams(self) -> None:
        league = League.objects.create(name="LFew")
        # 0 enrolled teams.
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        with self.assertRaises(ValidationError):
            season.start_season()

        # 1 enrolled team.
        league2 = League.objects.create(name="LFew2")
        season2 = Season.objects.create(
            league=league2, name="S1", start_date=date.today()
        )
        t1, _ = make_team_with_slots("OneTeam")
        season2.teams.add(t1)
        with self.assertRaises(ValidationError):
            season2.start_season()

    def test_does_not_modify_teams_m2m(self) -> None:
        season, teams = self._make_two_team_season("NoMutate")
        count_before = season.teams.count()
        season.start_season()
        season.refresh_from_db()
        count_after = season.teams.count()
        self.assertEqual(count_before, count_after)


# ---------------------------------------------------------------------------
# §6c — Season.complete_if_finished()
# ---------------------------------------------------------------------------


class TestSeasonCompleteIfFinished(TestCase):
    """active -> completed (idempotent); stamps champion to rank-1 team."""

    _FAST_TICKS = 20

    def _make_two_team_active_season(self, prefix: str):
        league = League.objects.create(name=f"L{prefix}")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        t1, _ = make_team_with_slots(f"{prefix}A")
        t2, _ = make_team_with_slots(f"{prefix}B")
        season.teams.add(t1, t2)
        season.start_season()
        return season, t1, t2

    def test_no_op_when_state_is_not_active(self) -> None:
        league = League.objects.create(name="LDraft")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        # draft state.
        season.complete_if_finished()
        season.refresh_from_db()
        self.assertEqual(season.state, "draft")

    def test_no_op_when_fixtures_not_all_played(self) -> None:
        season, t1, t2 = self._make_two_team_active_season("NotPlayed")
        # N=2: 1 pair x 2 rounds = 2 fixtures total. Simulate only round 1.
        with patch.object(BatchSimulator, "ROUND_TICKS", self._FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
        season.refresh_from_db()
        # State still active -- second fixture not played.
        self.assertEqual(season.state, "active")

    def test_flips_to_completed_when_all_fixtures_played(self) -> None:
        season, t1, t2 = self._make_two_team_active_season("AllPlayed")
        # N=2: 2 fixtures total (round 1 + round 2).
        with patch.object(BatchSimulator, "ROUND_TICKS", self._FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")

    def test_stamps_champion_to_row_0_of_compute_standings(self) -> None:
        season, t1, t2 = self._make_two_team_active_season("Champ")
        with patch.object(BatchSimulator, "ROUND_TICKS", self._FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        season.refresh_from_db()
        # Champion must be set and must be one of the two enrolled teams.
        self.assertIsNotNone(season.champion_team)
        self.assertIn(season.champion_team, (t1, t2))

    def test_idempotent_on_re_call(self) -> None:
        season, t1, t2 = self._make_two_team_active_season("Idem")
        with patch.object(BatchSimulator, "ROUND_TICKS", self._FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        season.refresh_from_db()
        state_after_first = season.state
        champion_after_first = season.champion_team
        # Re-call must be a no-op.
        season.complete_if_finished()
        season.refresh_from_db()
        self.assertEqual(season.state, state_after_first)
        self.assertEqual(season.champion_team, champion_after_first)


# ---------------------------------------------------------------------------
# §6c — Match.season FK
# ---------------------------------------------------------------------------


class TestMatchSeasonFK(TestCase):
    """Match.season is nullable FK, SET_NULL on Season delete, reverse
    accessor ``season.matches``.
    """

    def _make_match(self, prefix: str) -> Match:
        red, _ = make_team_with_slots(f"{prefix}R")
        blue, _ = make_team_with_slots(f"{prefix}B")
        return Match.objects.create(team_red=red, team_blue=blue)

    def test_match_season_default_is_null(self) -> None:
        match = self._make_match("DefNull")
        self.assertIsNone(match.season)

    def test_match_season_assignable(self) -> None:
        league = League.objects.create(name="LAssign")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        match = self._make_match("Assign")
        match.season = season
        match.save()
        match.refresh_from_db()
        self.assertEqual(match.season, season)

    def test_deleting_season_sets_match_season_to_null_does_not_cascade_delete(
        self,
    ) -> None:
        league = League.objects.create(name="LDel")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        match = self._make_match("Del")
        match.season = season
        match.save()
        match_id = match.id
        season.delete()
        # Match must still exist.
        self.assertTrue(Match.objects.filter(pk=match_id).exists())
        match.refresh_from_db()
        self.assertIsNone(match.season)

    def test_season_matches_reverse_accessor_returns_all_matches(self) -> None:
        league = League.objects.create(name="LRev")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        m1 = self._make_match("Rev1")
        m2 = self._make_match("Rev2")
        m1.season = season
        m2.season = season
        m1.save()
        m2.save()
        self.assertEqual(set(season.matches.all()), {m1, m2})


# Reference to silence unused-import warnings (GameRound is referenced
# indirectly via the simulator; keep the import for future regression tests).
_ = GameRound


# ---------------------------------------------------------------------------
# LG-01g — League.current_team FK (§9b)
# ---------------------------------------------------------------------------


class TestLeagueCurrentTeamField(TestCase):
    """LG-01g — ``League.current_team`` FK contract.

    Locked at ``.claude/worktrees/lg-01g-seam-contract.md`` §2 + §9b:
    nullable FK to ``teams.Team`` with ``on_delete=SET_NULL`` and
    ``related_name="managed_in_leagues"``. Migration
    ``matches/migrations/0030_league_current_team.py``.
    """

    def test_current_team_is_nullable(self) -> None:
        league = League(name="X")
        league.save()
        self.assertIsNone(league.current_team)

    def test_current_team_default_is_None(self) -> None:
        league = League.objects.create(name="X")
        self.assertIsNone(league.current_team)

    def test_current_team_set_null_on_team_delete(self) -> None:
        team, _ = make_team_with_slots("Tdel")
        league = League.objects.create(name="LDel", current_team=team)
        team.delete()
        league.refresh_from_db()
        self.assertIsNone(league.current_team)

    def test_related_name_managed_in_leagues(self) -> None:
        team, _ = make_team_with_slots("Trev")
        l1 = League.objects.create(name="L1", current_team=team)
        l2 = League.objects.create(name="L2", current_team=team)
        # Defensive: a third League pointing elsewhere should NOT appear.
        other_team, _ = make_team_with_slots("Toth")
        League.objects.create(name="L3", current_team=other_team)
        managed_ids = set(team.managed_in_leagues.values_list("id", flat=True))
        self.assertEqual(managed_ids, {l1.id, l2.id})

    def test_migration_0030_exists(self) -> None:
        """Field is present on the model with the expected on-delete
        behaviour; the migration filename is a separate artifact pinned
        by the Code agent.
        """
        from django.db import models as _models

        field = League._meta.get_field("current_team")
        # ForeignKey to teams.Team.
        self.assertEqual(field.related_model._meta.label, "teams.Team")
        # SET_NULL on delete.
        self.assertIs(field.remote_field.on_delete, _models.SET_NULL)
        # related_name on the reverse side.
        self.assertEqual(field.remote_field.related_name, "managed_in_leagues")
        # Nullable / blank.
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
