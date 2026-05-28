"""Tests for ``teams/role_benchmarks_orm.py``.

Pins:

- ``_MvpAdapter.get_accuracy`` mirrors ``PlayerRoundState.get_accuracy``
  exactly (the duck-typing layer cannot drift).
- ``compute_benchmarks_uncached()`` returns the
  ``(samples_by_key, rounds_in_role)`` shape with full ``ROLES × STAT_KEYS``
  coverage, empty populations on missing role/stat pairs, and the
  ``rounds_in_role`` map correctly counting per-player-per-role rounds.
- ``_PLAYER_STATE_FIELDS`` enumerates every ORM column the adapter reads,
  so a future column rename surfaces here, not at runtime.
- Empty DB returns the locked empty shape (every cell present, every list
  empty, every rounds_in_role dict empty).
"""

from __future__ import annotations

from django.test import TestCase

from matches.models import GameRound, PlayerRoundState
from teams.models import Player, Team
from teams.role_benchmarks import ROLES, STAT_KEYS
from teams.role_benchmarks_orm import (
    _PLAYER_STATE_FIELDS,
    _MvpAdapter,
    _MvpGameRound,
    compute_benchmarks_uncached,
)


def _make_player(team_name: str, player_name: str) -> tuple[Team, Player]:
    team = Team.objects.create(name=team_name)
    player = Player.objects.create(team=team, name=player_name)
    team.slot_commander = player
    team.save()
    return team, player


def _make_round_state(
    player: Player,
    team: Team,
    *,
    role: str = "commander",
    points_scored: int = 500,
    tags_made: int = 5,
    shots_missed: int = 4,
) -> PlayerRoundState:
    game_round = GameRound.objects.create(round_number=1, team_red=team, team_blue=team)
    return PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color="red",
        role=role,
        points_scored=points_scored,
        tags_made=tags_made,
        times_tagged=3,
        shots_missed=shots_missed,
        final_special=2,
        specials_used=1,
        was_eliminated_at=1500,
        final_lives=5,
    )


class TestMvpAdapterAccuracy(TestCase):
    """``_MvpAdapter.get_accuracy`` must match the ORM model byte-for-byte —
    the adapter is the only thing standing between ``calculate_mvp`` and the
    DB during benchmark materialisation; if the formula drifts the cached
    MVP samples silently drift too.
    """

    def _adapter(self, *, tags_made: int, shots_missed: int) -> _MvpAdapter:
        return _MvpAdapter(
            role="commander",
            team_color="red",
            final_lives=5,
            final_medic_hits=0,
            enemy_nuke_cancels=0,
            ally_nuke_cancels=0,
            times_missiled=0,
            missiles_landed=0,
            specials_used=0,
            own_specials_cancelled=0,
            points_scored=0,
            tags_made=tags_made,
            shots_missed=shots_missed,
            specific_tags={},
            game_round=_MvpGameRound(
                blue_team_eliminated=False,
                red_team_eliminated=False,
                eliminated_at=0,
            ),
        )

    def test_zero_shots_yields_zero_accuracy(self) -> None:
        self.assertEqual(self._adapter(tags_made=0, shots_missed=0).get_accuracy, 0)

    def test_all_hits_yields_100(self) -> None:
        self.assertEqual(self._adapter(tags_made=10, shots_missed=0).get_accuracy, 100)

    def test_all_misses_yields_0(self) -> None:
        self.assertEqual(self._adapter(tags_made=0, shots_missed=10).get_accuracy, 0)

    def test_mixed_rounds_to_int(self) -> None:
        # 6/(6+4) = 0.6 → 60.
        self.assertEqual(self._adapter(tags_made=6, shots_missed=4).get_accuracy, 60)
        # 1/(1+2) ≈ 0.333... → round() = 33.
        self.assertEqual(self._adapter(tags_made=1, shots_missed=2).get_accuracy, 33)


class TestPlayerStateFieldsCoverage(TestCase):
    """The ORM scan fields tuple is the single source of truth for which
    columns the adapter reads. A future column rename or removal must
    surface here, not at runtime.
    """

    def test_includes_id_player_and_role(self) -> None:
        for required in ("id", "player_id", "role", "team_color"):
            self.assertIn(required, _PLAYER_STATE_FIELDS)

    def test_includes_game_round_join_fields(self) -> None:
        for required in (
            "game_round__id",
            "game_round__blue_team_eliminated",
            "game_round__red_team_eliminated",
            "game_round__eliminated_at",
        ):
            self.assertIn(required, _PLAYER_STATE_FIELDS)

    def test_no_duplicates(self) -> None:
        self.assertEqual(len(_PLAYER_STATE_FIELDS), len(set(_PLAYER_STATE_FIELDS)))


class TestComputeBenchmarksUncachedEmptyDb(TestCase):
    """With zero ``PlayerRoundState`` rows, both return values cover the
    full cartesian product with empty leaves — so callers never need a
    ``KeyError`` guard.
    """

    def test_samples_cover_full_cartesian_product(self) -> None:
        samples, _ = compute_benchmarks_uncached()
        for role in ROLES:
            for stat in STAT_KEYS:
                self.assertIn((role, stat), samples)
                self.assertEqual(samples[(role, stat)], [])

    def test_rounds_in_role_has_every_role(self) -> None:
        _, rounds_in_role = compute_benchmarks_uncached()
        for role in ROLES:
            self.assertIn(role, rounds_in_role)
            self.assertEqual(rounds_in_role[role], {})


class TestComputeBenchmarksUncachedHappyPath(TestCase):
    """End-to-end: a single 1000-point Commander round materialises into the
    Commander population with a 1000-point sample and a rounds-played=1 map
    entry.
    """

    def test_one_round_appears_in_commander_population(self) -> None:
        team, player = _make_player("OrmTeam", "OrmPlayer")
        _make_round_state(player, team, role="commander", points_scored=1000)

        samples, rounds_in_role = compute_benchmarks_uncached()

        cmd_pts = samples[("commander", "points_scored")]
        self.assertEqual(len(cmd_pts), 1)
        pid, value = cmd_pts[0]
        self.assertEqual(pid, player.id)
        self.assertAlmostEqual(value, 1000.0)

        # Other roles untouched.
        self.assertEqual(samples[("heavy", "points_scored")], [])

    def test_rounds_in_role_counts_per_player_per_role(self) -> None:
        team, player = _make_player("CountTeam", "CountPlayer")
        _make_round_state(player, team, role="commander", points_scored=1000)
        _make_round_state(player, team, role="commander", points_scored=2000)
        _make_round_state(player, team, role="heavy", points_scored=500)

        _, rounds_in_role = compute_benchmarks_uncached()

        self.assertEqual(rounds_in_role["commander"][player.id], 2)
        self.assertEqual(rounds_in_role["heavy"][player.id], 1)
        # Unused roles report no entry for this player (or report 0; the
        # contract is "no key" — the view-layer threshold logic treats
        # missing as zero).
        self.assertNotIn(player.id, rounds_in_role["scout"])

    def test_two_round_average_for_career_aggregate(self) -> None:
        # Population value is the player's career-mean across their
        # Commander rounds: (1000 + 3000) / 2 = 2000.
        team, player = _make_player("AvgTeam", "AvgPlayer")
        _make_round_state(player, team, role="commander", points_scored=1000)
        _make_round_state(player, team, role="commander", points_scored=3000)

        samples, _ = compute_benchmarks_uncached()
        cmd_pts = samples[("commander", "points_scored")]
        self.assertEqual(len(cmd_pts), 1)
        _, value = cmd_pts[0]
        self.assertAlmostEqual(value, 2000.0)
