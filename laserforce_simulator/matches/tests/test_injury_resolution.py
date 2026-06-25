"""FIN-04 — DB tests for the per-fixture injury resolver (seam contract §4 / §6 / §8).

Django ``TestCase`` exercising the two module-level play-loop helpers
``matches.league_views.resolve_injuries_for_fixture(season, team_red, team_blue)``
and ``matches.league_views.restore_after_fixture(token)`` plus the
``games_unavailable`` rollover reset and the health expense line.

What is pinned here (schema-level outcomes ONLY — roster validity,
``games_unavailable`` values, in-memory mutate-then-restore, byte-identical-OFF,
expense-line presence — NEVER raw simulated point totals, which are
non-deterministic):

* **Starter-only subjects** — only the 6 active-roster ``slot_*`` starters roll
  / are tracked; bench / free-agent fill-ins NEVER roll an injury.
* **Roll** sets ``games_unavailable`` on a healthy fielded starter (persisted).
* **Decrement** — an already-unavailable starter decrements by 1 per fixture and
  does NOT re-roll.
* **auto_sub** rewrites the in-memory ``slot_*`` FK (bench → free-agent
  priority) and is restored after.
* **play_hurt** rewrites the injured Player's 19 stat fields down and is
  restored after.
* **Universal no-sub fallback** — ``auto_sub`` with no available sub falls back
  to play_hurt, so the roster ALWAYS resolves to a valid 6.
* **Never ``.save()`` the temp roster** — post-fixture DB ``slot_*`` + Player
  stats are unchanged; ONLY ``games_unavailable`` persists.
* **``next_season`` rollover** resets ``games_unavailable = 0`` over the
  developing set.
* **Byte-identical OFF** — finance disabled / sandbox / multiplayer mode ⇒ zero
  mutation, zero ``games_unavailable`` change, ``resolve_injuries_for_fixture``
  returns ``{}``.
* **Health expense** lands in ``TeamSeasonFinance.health_cost`` and flows into
  ``profit``.

These FAIL until the Code agent lands the ``games_unavailable`` /
``budget_health`` / ``injury_policy`` model fields, the ``health_cost`` snapshot
field, the two resolver helpers, and the rollover reset. NO simulator, NO
simulated point totals.
"""

from __future__ import annotations

import random
from datetime import date
from unittest import mock

from django.test import TestCase

from matches.injury import PLAY_HURT_STAT_PENALTY
from matches.league_views import (
    resolve_injuries_for_fixture,
    restore_after_fixture,
)
from matches.models import (
    GameRound,
    League,
    Match,
    Season,
    TeamSeasonFinance,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

# The 19 stat field names play_hurt rewrites (pinned equal to
# ``teams.player_generator._STAT_FIELDS``; imported lazily where asserted).
from teams.player_generator import _STAT_FIELDS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(prefix: str) -> Team:
    team, _ = make_team_with_slots(prefix)
    return team


def _make_league(
    name: str,
    *,
    current_team=None,
    mode: str = "league",
    finance_enabled: bool = True,
) -> League:
    league = League.objects.create(
        name=name,
        mode=mode,
        state="active",
        current_team=current_team,
        finance_enabled=finance_enabled,
    )
    pool = Team.objects.create(name=f"{name} Free Agents")
    league.free_agent_pool = pool
    league.save(update_fields=["free_agent_pool"])
    return league


def _make_active_season(league: League, teams) -> Season:
    season = Season.objects.create(
        league=league,
        name="S1",
        start_date=date(2026, 6, 1),
    )
    for t in teams:
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season


def _set_starter_ages(team: Team, age: int = 35) -> None:
    """Give every active-roster starter a fixed age (so injury rolls are
    well-defined / deterministic under a forced RNG)."""
    for player in team.active_players:
        player.age = age
        player.save(update_fields=["age"])


def _add_bench_player(team: Team, name: str) -> Player:
    """Add a bench player (on the team, not in any slot) so auto_sub has a
    bench source."""
    return Player.objects.create(team=team, name=name)


# A forced RNG whose ``random()`` always returns 0.0 — so every injury roll is a
# guaranteed hit (``0.0 < injury_probability(age)`` for any positive base rate).
class _AlwaysInjureRandom(random.Random):
    def random(self):  # type: ignore[override]
        return 0.0


# A forced RNG whose ``random()`` always returns 1.0 — so no injury ever fires.
class _NeverInjureRandom(random.Random):
    def random(self):  # type: ignore[override]
        return 1.0


# ===========================================================================
# §4 — TestModelFields (the three FIN-04 model fields + their defaults)
# ===========================================================================


class TestModelFields(TestCase):
    """``Player.games_unavailable`` / ``Team.budget_health`` /
    ``Team.injury_policy`` exist with the locked defaults."""

    def test_player_games_unavailable_defaults_zero(self) -> None:
        team = _make_team("MF")
        for player in team.active_players:
            player.refresh_from_db()
            self.assertEqual(player.games_unavailable, 0)

    def test_team_budget_health_defaults_to_neutral_level(self) -> None:
        from matches.finance import DEFAULT_LEVEL

        team = _make_team("MFH")
        team.refresh_from_db()
        self.assertEqual(team.budget_health, DEFAULT_LEVEL)

    def test_team_injury_policy_defaults_auto_sub(self) -> None:
        team = _make_team("MFP")
        team.refresh_from_db()
        self.assertEqual(team.injury_policy, "auto_sub")

    def test_injury_policy_accepts_play_hurt(self) -> None:
        team = _make_team("MFP2")
        team.injury_policy = "play_hurt"
        team.save(update_fields=["injury_policy"])
        team.refresh_from_db()
        self.assertEqual(team.injury_policy, "play_hurt")


# ===========================================================================
# §6 — TestGateOff (byte-identical OFF — finance disabled / sandbox / mp)
# ===========================================================================


class TestGateOff(TestCase):
    """Outside ``_is_career_league AND finance_enabled`` the resolver is a no-op
    returning ``{}`` — zero mutation, zero ``games_unavailable`` change."""

    def _two_teams_season(self, **league_kwargs):
        red = _make_team("GoR")
        blue = _make_team("GoB")
        league = _make_league("GoL", current_team=red, **league_kwargs)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 45)
        _set_starter_ages(blue, 45)
        return league, season, red, blue

    def test_finance_disabled_returns_empty_token_and_no_mutation(self) -> None:
        _league, season, red, blue = self._two_teams_season(finance_enabled=False)
        # Even under a forced always-injure RNG, the gate short-circuits.
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        self.assertEqual(token, {})
        for team in (red, blue):
            for player in team.active_players:
                player.refresh_from_db()
                self.assertEqual(player.games_unavailable, 0)

    def test_sandbox_mode_returns_empty_token(self) -> None:
        _league, season, red, blue = self._two_teams_season(
            mode="sandbox", finance_enabled=True
        )
        token = resolve_injuries_for_fixture(season, red, blue)
        self.assertEqual(token, {})

    def test_multiplayer_mode_returns_empty_token(self) -> None:
        _league, season, red, blue = self._two_teams_season(
            mode="multiplayer", finance_enabled=True
        )
        token = resolve_injuries_for_fixture(season, red, blue)
        self.assertEqual(token, {})


# ===========================================================================
# §4 — TestRollSetsGamesUnavailable (healthy fielded starter rolls + persists)
# ===========================================================================


class TestRollSetsGamesUnavailable(TestCase):
    """A healthy fielded starter that rolls an injury gets ``games_unavailable``
    set to the drawn duration (persisted), within the clamp bounds."""

    def _setup(self):
        red = _make_team("RsR")
        blue = _make_team("RsB")
        league = _make_league("RsL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 45)
        _set_starter_ages(blue, 45)
        return league, season, red, blue

    def test_always_injure_sets_positive_counter(self) -> None:
        from matches.injury import DURATION_MAX_GAMES, DURATION_MIN_GAMES

        _league, season, red, blue = self._setup()
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        # At least one starter on each team is now unavailable; every set value
        # is within the duration clamp.
        red.refresh_from_db()
        any_out = False
        for player in Player.objects.filter(team=red):
            player.refresh_from_db()
            if player.games_unavailable > 0:
                any_out = True
                self.assertGreaterEqual(player.games_unavailable, DURATION_MIN_GAMES)
                self.assertLessEqual(player.games_unavailable, DURATION_MAX_GAMES)
        self.assertTrue(any_out, "an always-injure RNG must injure >= 1 starter")
        # The token is non-empty (real work happened) so restore is meaningful.
        self.assertNotEqual(token, {})
        restore_after_fixture(token)

    def test_never_injure_leaves_counter_zero(self) -> None:
        _league, season, red, blue = self._setup()
        with mock.patch("matches.league_views.random.Random", _NeverInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        for team in (red, blue):
            for player in team.active_players:
                player.refresh_from_db()
                self.assertEqual(player.games_unavailable, 0)
        restore_after_fixture(token)


# ===========================================================================
# §4 — TestStarterOnlySubjects (bench / free-agent fill-ins NEVER roll)
# ===========================================================================


class TestStarterOnlySubjects(TestCase):
    """Only the 6 active-roster STARTERS are injury subjects — bench players and
    free-agent-pool players never roll an injury (never tracked)."""

    def test_bench_player_never_injured(self) -> None:
        red = _make_team("SoR")
        blue = _make_team("SoB")
        league = _make_league("SoL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 45)
        _set_starter_ages(blue, 45)
        # A bench player on red (not in any slot).
        bench = _add_bench_player(red, "Benchy")
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        bench.refresh_from_db()
        # Even under an always-injure RNG the bench player is never a subject.
        self.assertEqual(bench.games_unavailable, 0)
        restore_after_fixture(token)

    def test_free_agent_pool_player_never_injured(self) -> None:
        red = _make_team("FaR")
        blue = _make_team("FaB")
        league = _make_league("FaL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 45)
        _set_starter_ages(blue, 45)
        # A free-agent-pool player.
        fa = Player.objects.create(team=league.free_agent_pool, name="Freebie")
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        fa.refresh_from_db()
        self.assertEqual(fa.games_unavailable, 0)
        restore_after_fixture(token)


# ===========================================================================
# §4 — TestDecrementUnavailableStarter (decrement 1/fixture, no re-roll)
# ===========================================================================


class TestDecrementUnavailableStarter(TestCase):
    """A starter already at ``games_unavailable > 0`` decrements by 1 per
    fixture (persisted) and does NOT re-roll a fresh injury."""

    def test_decrements_by_one(self) -> None:
        red = _make_team("DeR")
        blue = _make_team("DeB")
        league = _make_league("DeL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 20)  # young ⇒ low injury chance
        _set_starter_ages(blue, 20)
        # Pin one red starter as already out for 4 games.
        starter = red.active_players[0]
        starter.games_unavailable = 4
        starter.save(update_fields=["games_unavailable"])
        # Use a never-injure RNG so the OTHER healthy starters don't muddy this.
        with mock.patch("matches.league_views.random.Random", _NeverInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        starter.refresh_from_db()
        # Decremented by exactly 1 — and NOT re-rolled to a fresh duration.
        self.assertEqual(starter.games_unavailable, 3)
        restore_after_fixture(token)

    def test_decrement_does_not_go_below_zero(self) -> None:
        red = _make_team("Dz R")
        blue = _make_team("DzB")
        league = _make_league("DzL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 20)
        _set_starter_ages(blue, 20)
        starter = red.active_players[0]
        starter.games_unavailable = 1
        starter.save(update_fields=["games_unavailable"])
        with mock.patch("matches.league_views.random.Random", _NeverInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        starter.refresh_from_db()
        self.assertEqual(starter.games_unavailable, 0)
        restore_after_fixture(token)


# ===========================================================================
# §4 — TestAutoSubRoster (bench → free-agent priority; restored after)
# ===========================================================================


class TestAutoSubRoster(TestCase):
    """``auto_sub`` rewrites the in-memory ``slot_*`` FK to a substitute (bench
    first, then the League free-agent pool); ``restore_after_fixture`` restores
    the original ``slot_*`` FK, and the DB roster is never ``.save()``-d."""

    def _setup_with_bench(self):
        red = _make_team("AsR")
        blue = _make_team("AsB")
        league = _make_league("AsL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 45)
        _set_starter_ages(blue, 45)
        red.injury_policy = "auto_sub"
        red.save(update_fields=["injury_policy"])
        blue.injury_policy = "auto_sub"
        blue.save(update_fields=["injury_policy"])
        # Bench depth so auto_sub has substitutes available.
        for i in range(6):
            _add_bench_player(red, f"RedBench{i}")
            _add_bench_player(blue, f"BlueBench{i}")
        return league, season, red, blue

    def test_roster_stays_valid_six_after_resolution(self) -> None:
        _league, season, red, blue = self._setup_with_bench()
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        # In-memory the roster the simulator will read must still be 6 starters.
        self.assertEqual(len(red.active_players), 6)
        self.assertEqual(len(blue.active_players), 6)
        restore_after_fixture(token)

    def test_slot_fks_restored_after_fixture(self) -> None:
        _league, season, red, blue = self._setup_with_bench()
        before = {
            "commander": red.slot_commander_id,
            "heavy": red.slot_heavy_id,
            "scout_1": red.slot_scout_1_id,
            "scout_2": red.slot_scout_2_id,
            "medic": red.slot_medic_id,
            "ammo": red.slot_ammo_id,
        }
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        restore_after_fixture(token)
        after = {
            "commander": red.slot_commander_id,
            "heavy": red.slot_heavy_id,
            "scout_1": red.slot_scout_1_id,
            "scout_2": red.slot_scout_2_id,
            "medic": red.slot_medic_id,
            "ammo": red.slot_ammo_id,
        }
        self.assertEqual(before, after, "in-memory slot_* FKs must be restored")

    def test_db_slot_fks_unchanged_never_saved(self) -> None:
        _league, season, red, blue = self._setup_with_bench()
        db_before = list(
            Team.objects.filter(pk=red.pk).values_list(
                "slot_commander_id",
                "slot_heavy_id",
                "slot_scout_1_id",
                "slot_scout_2_id",
                "slot_medic_id",
                "slot_ammo_id",
            )
        )
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        restore_after_fixture(token)
        db_after = list(
            Team.objects.filter(pk=red.pk).values_list(
                "slot_commander_id",
                "slot_heavy_id",
                "slot_scout_1_id",
                "slot_scout_2_id",
                "slot_medic_id",
                "slot_ammo_id",
            )
        )
        # The DB roster was NEVER persisted — only the in-memory copy mutated.
        self.assertEqual(db_before, db_after)


# ===========================================================================
# §4 — TestPlayHurtStats (rewrites the 19 stats down; restored after)
# ===========================================================================


class TestPlayHurtStats(TestCase):
    """``play_hurt`` rewrites the injured in-memory Player's 19 stat fields down
    by ``play_hurt_penalty()`` (clamp ``[0, 100]``); ``restore_after_fixture``
    restores them, and the DB stats are never ``.save()``-d."""

    def _setup_play_hurt(self):
        red = _make_team("PhR")
        blue = _make_team("PhB")
        league = _make_league("PhL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 45)
        _set_starter_ages(blue, 45)
        red.injury_policy = "play_hurt"
        red.save(update_fields=["injury_policy"])
        blue.injury_policy = "play_hurt"
        blue.save(update_fields=["injury_policy"])
        # Pin all starter stats to a known mid value so the penalty is visible.
        for team in (red, blue):
            for player in team.active_players:
                for name in _STAT_FIELDS:
                    setattr(player, name, 60)
                player.save()
        return league, season, red, blue

    def test_in_memory_stats_dropped_by_penalty(self) -> None:
        _league, season, red, blue = self._setup_play_hurt()
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        # Some in-memory red starter has every stat at most 60 - penalty (it was
        # rewritten down for the play-hurt sim).
        injured = [p for p in red.active_players if getattr(p, _STAT_FIELDS[0]) < 60]
        self.assertTrue(injured, "play_hurt must drop >= 1 in-memory starter's stats")
        hurt = injured[0]
        for name in _STAT_FIELDS:
            self.assertLessEqual(
                getattr(hurt, name),
                60 - PLAY_HURT_STAT_PENALTY,
                f"{name} not dropped by the play-hurt penalty",
            )
            self.assertGreaterEqual(getattr(hurt, name), 0, f"{name} below 0")
        restore_after_fixture(token)

    def test_stats_restored_after_fixture(self) -> None:
        _league, season, red, blue = self._setup_play_hurt()
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        restore_after_fixture(token)
        for player in red.active_players:
            for name in _STAT_FIELDS:
                self.assertEqual(
                    getattr(player, name), 60, f"{name} not restored on {player.name}"
                )

    def test_db_stats_unchanged_never_saved(self) -> None:
        _league, season, red, blue = self._setup_play_hurt()
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        restore_after_fixture(token)
        # Re-read every red starter's stats straight from the DB — unchanged.
        for player in red.active_players:
            fresh = Player.objects.get(pk=player.pk)
            for name in _STAT_FIELDS:
                self.assertEqual(
                    getattr(fresh, name),
                    60,
                    f"{name} persisted to DB for {player.name}",
                )


# ===========================================================================
# §4 — TestNoSubFallbackToPlayHurt (auto_sub with no sub ⇒ valid 6-roster)
# ===========================================================================


class TestNoSubFallbackToPlayHurt(TestCase):
    """``auto_sub`` is the policy but no substitute is available (no bench, no
    free agents): the injured starter plays hurt as the universal fallback, so
    the roster ALWAYS resolves to a valid 6."""

    def test_auto_sub_no_sub_yields_valid_six(self) -> None:
        red = _make_team("NfR")
        blue = _make_team("NfB")
        league = _make_league("NfL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 45)
        _set_starter_ages(blue, 45)
        red.injury_policy = "auto_sub"
        red.save(update_fields=["injury_policy"])
        blue.injury_policy = "auto_sub"
        blue.save(update_fields=["injury_policy"])
        # NO bench players added, and the free-agent pool is empty ⇒ no subs.
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        # Even with every starter injured and no sub source, the in-memory
        # roster is still a valid 6.
        self.assertEqual(len(red.active_players), 6)
        self.assertEqual(len(blue.active_players), 6)
        restore_after_fixture(token)


# ===========================================================================
# §4 — TestGamesUnavailablePersisted (only the counter persists, not the roster)
# ===========================================================================


class TestGamesUnavailablePersisted(TestCase):
    """The ONLY persisted write is the ``games_unavailable`` decrement/set; the
    temporary roster (slot_* + stats) is never ``.save()``-d."""

    def test_only_games_unavailable_changes_in_db(self) -> None:
        red = _make_team("GpR")
        blue = _make_team("GpB")
        league = _make_league("GpL", current_team=red)
        season = _make_active_season(league, [red, blue])
        _set_starter_ages(red, 45)
        _set_starter_ages(blue, 45)
        red.injury_policy = "play_hurt"
        red.save(update_fields=["injury_policy"])
        blue.injury_policy = "play_hurt"
        blue.save(update_fields=["injury_policy"])
        for team in (red, blue):
            for player in team.active_players:
                for name in _STAT_FIELDS:
                    setattr(player, name, 50)
                player.save()
        with mock.patch("matches.league_views.random.Random", _AlwaysInjureRandom):
            token = resolve_injuries_for_fixture(season, red, blue)
        restore_after_fixture(token)
        # In the DB: stats unchanged (50), but games_unavailable may be > 0.
        out_count = 0
        for player in Player.objects.filter(team__in=[red, blue]):
            fresh = Player.objects.get(pk=player.pk)
            for name in _STAT_FIELDS:
                self.assertEqual(getattr(fresh, name), 50)
            if fresh.games_unavailable > 0:
                out_count += 1
        self.assertGreater(
            out_count, 0, "the counter must persist for injured starters"
        )


# ===========================================================================
# §4 — TestNextSeasonResetsGamesUnavailable (rollover zeroes the counter)
# ===========================================================================


class TestNextSeasonResetsGamesUnavailable(TestCase):
    """The ``next_season`` rollover (via ``_develop_league_for_new_season``)
    resets ``games_unavailable = 0`` over the developing set."""

    def test_rollover_zeroes_counter_over_developing_set(self) -> None:
        from matches.league_views import _develop_league_for_new_season

        team = _make_team("NsT")
        opp = _make_team("NsO")
        league = _make_league("NsL", current_team=team)
        # A completed Season to develop from.
        latest = Season.objects.create(
            league=league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            schedule_format="single_round_robin",
            state="completed",
            starting_team_ids_json=sorted([team.id, opp.id]),
        )
        # The new draft Season the rollover develops INTO.
        new_season = Season.objects.create(
            league=league,
            name="Season 2",
            start_date=date(2026, 1, 1),
            schedule_format="single_round_robin",
            state="draft",
            starting_team_ids_json=sorted([team.id, opp.id]),
        )
        new_season.teams.add(team, opp)
        # Pin several players (active + bench + free-agent pool) as unavailable.
        marked = []
        for t in (team, opp):
            starter = t.active_players[0]
            starter.games_unavailable = 5
            starter.save(update_fields=["games_unavailable"])
            marked.append(starter)
            bench = _add_bench_player(t, f"{t.name} bench")
            bench.games_unavailable = 3
            bench.save(update_fields=["games_unavailable"])
            marked.append(bench)
        fa = Player.objects.create(
            team=league.free_agent_pool, name="FA out", games_unavailable=7
        )
        marked.append(fa)

        _develop_league_for_new_season(league, new_season, latest)

        for player in marked:
            player.refresh_from_db()
            self.assertEqual(
                player.games_unavailable,
                0,
                f"{player.name} games_unavailable not reset at rollover",
            )


# ===========================================================================
# §3 / §6 — TestHealthCostFlowsIntoProfit (TeamSeasonFinance.health_cost)
# ===========================================================================


class TestHealthCostFlowsIntoProfit(TestCase):
    """The health expense line lands in ``TeamSeasonFinance.health_cost`` and
    flows into ``profit`` (the seventh expense line)."""

    def _completed_season_league(self):
        team = _make_team("HcT")
        opp = _make_team("HcO")
        league = _make_league("HcL", current_team=team)
        s1 = Season.objects.create(
            league=league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            schedule_format="single_round_robin",
            state="completed",
            starting_team_ids_json=sorted([team.id, opp.id]),
        )
        match = Match.objects.create(
            team_red=team,
            team_blue=opp,
            season=s1,
            red_round1_points=100,
            blue_round1_points=10,
            red_round2_points=100,
            blue_round2_points=10,
            is_completed=True,
        )
        GameRound.objects.create(
            match=match,
            team_red=team,
            team_blue=opp,
            round_number=1,
            red_points=100,
            blue_points=10,
            is_completed=True,
        )
        GameRound.objects.create(
            match=match,
            team_red=opp,
            team_blue=team,
            round_number=2,
            red_points=10,
            blue_points=100,
            is_completed=True,
        )
        return league, team, s1

    def test_health_cost_snapshot_present_and_positive(self) -> None:
        from matches.league_views import _ensure_team_finances

        league, team, s1 = self._completed_season_league()
        # A non-neutral health budget so the cost line is clearly non-trivial.
        team.budget_health = 80
        team.save(update_fields=["budget_health"])
        _ensure_team_finances(league, s1)
        row = TeamSeasonFinance.objects.get(team=team, season=s1)
        self.assertGreater(row.health_cost, 0.0)

    def test_health_cost_equals_level_to_amount(self) -> None:
        from matches.finance import level_to_amount
        from matches.league_views import _ensure_team_finances

        league, team, s1 = self._completed_season_league()
        team.budget_health = 80
        team.save(update_fields=["budget_health"])
        _ensure_team_finances(league, s1)
        row = TeamSeasonFinance.objects.get(team=team, season=s1)
        self.assertAlmostEqual(row.health_cost, level_to_amount(80), places=4)

    def test_higher_health_budget_lowers_profit(self) -> None:
        from matches.league_views import _ensure_team_finances

        # Two structurally-identical leagues, differing only in the manager
        # team's health budget; the higher budget yields a lower profit.
        league_lo, team_lo, s_lo = self._completed_season_league()
        team_lo.budget_health = 1
        team_lo.save(update_fields=["budget_health"])
        _ensure_team_finances(league_lo, s_lo)
        row_lo = TeamSeasonFinance.objects.get(team=team_lo, season=s_lo)

        league_hi, team_hi, s_hi = self._completed_season_league()
        team_hi.budget_health = 100
        team_hi.save(update_fields=["budget_health"])
        _ensure_team_finances(league_hi, s_hi)
        row_hi = TeamSeasonFinance.objects.get(team=team_hi, season=s_hi)

        self.assertGreater(row_hi.health_cost, row_lo.health_cost)
        # Higher health cost ⇒ lower profit (revenue side is health-independent).
        self.assertLessEqual(row_hi.profit, row_lo.profit + 1e-6)
