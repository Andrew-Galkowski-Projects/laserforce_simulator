"""CRE-02 — Django ``TestCase`` tests for the ``tier_means=`` keyword-only
parameter on ``teams.views._generate_teams``.

The seam contract is locked at ``.claude/worktrees/cre-02-seam-contract.md``
(§4.2 for the signature and semantics, §8.4 for the determinism obligations).

These tests exercise the REAL generator — no ``mock.patch`` of
``_generate_teams`` or ``compute_tier_means`` — so signature drift on the seam
surfaces here.

DETERMINISM NOTE. ``_generate_teams`` consumes TWO RNG sources: the injected
``rng`` (which drives ``draw_stats`` + ``draw_preferred_roles`` in the pure
``teams.player_generator`` module) and the GLOBAL ``random`` module (which
drives ``teams.models._random_player_profile`` — age / height / home_site /
total_games). Only the injected ``rng`` is deterministic here, so every
determinism assertion below compares **stat + preferred_roles** values, never
profile fields and never PKs.
"""

from __future__ import annotations

import random

from django.test import TestCase

from teams.models import Player, Team
from teams.player_generator import compute_tier_means
from teams.views import _generate_teams

SEED = 42

# The 19 Player stat fields in canonical order — hard-coded (mirroring the
# ``_EXPECTED_STAT_FIELDS`` precedent in ``test_player_generator.py``) rather
# than imported from the production module. ``Offensive_synergy`` is
# intentionally capital-O; it matches the field name on ``Player``.
_STAT_FIELDS: tuple[str, ...] = (
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)


def _team_pool(prefix: str, size: int = 40) -> list[str]:
    """A deterministic, collision-free Team-name pool.

    ``_pop_unique_name`` pops from the END of the list, so the pool is
    supplied fresh per call and never shared between runs.
    """
    return [f"{prefix} Team {i:02d}" for i in range(size)]


def _player_pool(prefix: str, size: int = 400) -> list[str]:
    return [f"{prefix} Player {i:03d}" for i in range(size)]


def _mean_overall(team: Team) -> float:
    """Mean active-roster ``overall_rating`` — recomputed here deliberately.

    NOT ``matches.league_views._rank_teams_by_strength`` / ``_compute_team_overall``:
    the tier assertions must be independent of the league-ranking helper.
    """
    actives = team.active_players
    if not actives:
        return 0.0
    return sum(p.overall_rating for p in actives) / len(actives)


def _stat_signature(teams: "list[Team]") -> list[tuple]:
    """Ordered per-player (19 stats + preferred_roles) tuples, creation order.

    Both stat draws and preferred-role draws come from the INJECTED ``rng``,
    so this signature is the RNG-consumption fingerprint of a generation run.
    Names and PKs are excluded on purpose (§8.4).
    """
    signature: list[tuple] = []
    for team in teams:
        for player in team.players.order_by("id"):
            signature.append(
                tuple(getattr(player, field) for field in _STAT_FIELDS)
                + (tuple(player.preferred_roles or []),)
            )
    return signature


class TestGenerateTeamsTierMeans(TestCase):
    """CRE-02 — ``_generate_teams(..., tier_means=[...])``."""

    # -- happy path: shape --------------------------------------------------

    def test_tier_means_creates_num_teams_teams(self) -> None:
        tier_means = compute_tier_means(8, 50, 16)
        created = _generate_teams(
            8,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("A"),
            player_names_pool=_player_pool("A"),
            tier_means=tier_means,
        )
        self.assertEqual(len(created), 8)
        self.assertEqual(Team.objects.count(), 8)

    def test_tier_means_creates_six_players_per_team(self) -> None:
        created = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("B"),
            player_names_pool=_player_pool("B"),
            tier_means=compute_tier_means(4, 50, 16),
        )
        for team in created:
            with self.subTest(team=team.name):
                self.assertEqual(team.players.count(), 6)
                self.assertEqual(len(team.active_players), 6)

    def test_returned_objects_are_teams_in_creation_order(self) -> None:
        created = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("C"),
            player_names_pool=_player_pool("C"),
            tier_means=compute_tier_means(4, 50, 8),
        )
        ids = [team.id for team in created]
        self.assertEqual(ids, sorted(ids))

    # -- happy path: strength separation (seeded, direction + magnitude) ----

    def test_steep_tier_zero_meaningfully_stronger_than_last(self) -> None:
        """Contract §9.1 — tier-0 vs tier-(N-1), gap comfortably > 10.

        The delta-16 ramp spans 66 -> 34 (a ~32-point expectation), so a
        ``> 10`` floor is robust against the seeded stat noise while still
        proving the tiers actually separate.
        """
        created = _generate_teams(
            8,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("D"),
            player_names_pool=_player_pool("D"),
            tier_means=compute_tier_means(8, 50, 16),
        )
        gap = _mean_overall(created[0]) - _mean_overall(created[-1])
        self.assertGreater(gap, 10.0, f"steep head-tail gap was only {gap:.2f}")

    def test_tiered_tier_zero_stronger_than_last(self) -> None:
        """The delta-8 ramp spans 58 -> 42 (~16 points); floor at > 5."""
        created = _generate_teams(
            8,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("E"),
            player_names_pool=_player_pool("E"),
            tier_means=compute_tier_means(8, 50, 8),
        )
        gap = _mean_overall(created[0]) - _mean_overall(created[-1])
        self.assertGreater(gap, 5.0, f"tiered head-tail gap was only {gap:.2f}")

    def test_steep_gap_exceeds_tiered_gap(self) -> None:
        """A steeper ramp separates the extremes further than a shallow one."""
        steep = _generate_teams(
            8,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("F"),
            player_names_pool=_player_pool("F"),
            tier_means=compute_tier_means(8, 50, 16),
        )
        tiered = _generate_teams(
            8,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("G"),
            player_names_pool=_player_pool("G"),
            tier_means=compute_tier_means(8, 50, 8),
        )
        steep_gap = _mean_overall(steep[0]) - _mean_overall(steep[-1])
        tiered_gap = _mean_overall(tiered[0]) - _mean_overall(tiered[-1])
        self.assertGreater(steep_gap, tiered_gap)

    # -- determinism --------------------------------------------------------

    def test_same_seed_produces_same_per_team_mean_overall(self) -> None:
        tier_means = compute_tier_means(4, 50, 16)
        first = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("H1"),
            player_names_pool=_player_pool("H1"),
            tier_means=tier_means,
        )
        second = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("H2"),
            player_names_pool=_player_pool("H2"),
            tier_means=tier_means,
        )
        self.assertEqual(
            [_mean_overall(t) for t in first],
            [_mean_overall(t) for t in second],
        )

    def test_same_seed_produces_identical_stat_signature(self) -> None:
        tier_means = compute_tier_means(4, 50, 16)
        first = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("I1"),
            player_names_pool=_player_pool("I1"),
            tier_means=tier_means,
        )
        second = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("I2"),
            player_names_pool=_player_pool("I2"),
            tier_means=tier_means,
        )
        self.assertEqual(_stat_signature(first), _stat_signature(second))

    # -- the byte-identical-Even regression pin (§4.2 / §8.4) ---------------

    def test_tier_means_none_matches_kwarg_omitted(self) -> None:
        """``tier_means=None`` must consume the RNG exactly as pre-CRE-02.

        Run A omits the kwarg entirely (the pre-CRE-02 call shape); run B
        passes ``tier_means=None`` explicitly. Same seed, distinct name pools
        (name popping does not touch the injected ``rng``), so the ordered
        stat + preferred-role signatures must be identical.
        """
        omitted = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("J1"),
            player_names_pool=_player_pool("J1"),
        )
        explicit_none = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("J2"),
            player_names_pool=_player_pool("J2"),
            tier_means=None,
        )
        self.assertEqual(_stat_signature(omitted), _stat_signature(explicit_none))

    def test_flat_tier_means_matches_the_none_path(self) -> None:
        """A flat ramp (delta = 0) draws every team from the same ``mean``.

        ``compute_tier_means(n, 50, 0)`` is ``[50.0] * n``, so passing it must
        produce the same stat signature as the ``None`` path at the same seed.
        This is the pin behind the create-writer's ``if delta else None`` step.
        """
        none_path = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("K1"),
            player_names_pool=_player_pool("K1"),
            tier_means=None,
        )
        flat_path = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("K2"),
            player_names_pool=_player_pool("K2"),
            tier_means=compute_tier_means(4, 50, 0),
        )
        self.assertEqual(_stat_signature(none_path), _stat_signature(flat_path))

    def test_tier_means_changes_generation_versus_none(self) -> None:
        """Sanity guard: a NON-flat ramp must NOT match the ``None`` path.

        Without this, a production bug that silently ignored ``tier_means``
        would still pass every regression pin above.
        """
        none_path = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("L1"),
            player_names_pool=_player_pool("L1"),
            tier_means=None,
        )
        tiered_path = _generate_teams(
            4,
            6,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("L2"),
            player_names_pool=_player_pool("L2"),
            tier_means=compute_tier_means(4, 50, 16),
        )
        self.assertNotEqual(_stat_signature(none_path), _stat_signature(tiered_path))

    # -- failure mode: length mismatch raises ValueError (§4.2) -------------

    def test_short_tier_means_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _generate_teams(
                4,
                6,
                rng=random.Random(SEED),
                mean=50,
                std_dev=15,
                team_names_pool=_team_pool("M"),
                player_names_pool=_player_pool("M"),
                tier_means=[60.0, 50.0, 40.0],
            )

    def test_long_tier_means_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _generate_teams(
                4,
                6,
                rng=random.Random(SEED),
                mean=50,
                std_dev=15,
                team_names_pool=_team_pool("N"),
                player_names_pool=_player_pool("N"),
                tier_means=[70.0, 60.0, 50.0, 40.0, 30.0],
            )

    def test_length_mismatch_message_mentions_tier_means(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _generate_teams(
                4,
                6,
                rng=random.Random(SEED),
                mean=50,
                std_dev=15,
                team_names_pool=_team_pool("O"),
                player_names_pool=_player_pool("O"),
                tier_means=[60.0, 50.0, 40.0],
            )
        self.assertIn("tier_means", str(ctx.exception))

    def test_length_mismatch_writes_nothing(self) -> None:
        """The guard runs BEFORE the loop and before any ORM write."""
        for bad in ([60.0, 50.0, 40.0], [70.0, 60.0, 50.0, 40.0, 30.0]):
            with self.subTest(length=len(bad)):
                teams_before = Team.objects.count()
                players_before = Player.objects.count()
                with self.assertRaises(ValueError):
                    _generate_teams(
                        4,
                        6,
                        rng=random.Random(SEED),
                        mean=50,
                        std_dev=15,
                        team_names_pool=_team_pool("P"),
                        player_names_pool=_player_pool("P"),
                        tier_means=bad,
                    )
                self.assertEqual(Team.objects.count(), teams_before)
                self.assertEqual(Player.objects.count(), players_before)
                self.assertEqual(Team.objects.count(), 0)
                self.assertEqual(Player.objects.count(), 0)

    def test_empty_tier_means_with_nonzero_num_teams_raises(self) -> None:
        with self.assertRaises(ValueError):
            _generate_teams(
                4,
                6,
                rng=random.Random(SEED),
                mean=50,
                std_dev=15,
                team_names_pool=_team_pool("Q"),
                player_names_pool=_player_pool("Q"),
                tier_means=[],
            )

    # -- edge case: clamping still applies at extreme tier means -----------

    def test_player_stats_stay_in_0_100_under_high_tier_means(self) -> None:
        """``draw_stats`` clamps at the extremes even at a 100.0 tier mean."""
        _generate_teams(
            8,
            6,
            rng=random.Random(SEED),
            mean=95,
            std_dev=15,
            team_names_pool=_team_pool("R"),
            player_names_pool=_player_pool("R"),
            tier_means=compute_tier_means(8, 95, 16),
        )
        for player in Player.objects.all():
            for field in _STAT_FIELDS:
                value = getattr(player, field)
                self.assertGreaterEqual(value, 0, f"{field}={value} on {player.name}")
                self.assertLessEqual(value, 100, f"{field}={value} on {player.name}")

    def test_player_stats_stay_in_0_100_under_low_tier_means(self) -> None:
        _generate_teams(
            8,
            6,
            rng=random.Random(SEED),
            mean=5,
            std_dev=15,
            team_names_pool=_team_pool("S"),
            player_names_pool=_player_pool("S"),
            tier_means=compute_tier_means(8, 5, 16),
        )
        for player in Player.objects.all():
            for field in _STAT_FIELDS:
                value = getattr(player, field)
                self.assertGreaterEqual(value, 0, f"{field}={value} on {player.name}")
                self.assertLessEqual(value, 100, f"{field}={value} on {player.name}")

    # -- edge case: bench players share their team's tier mean --------------

    def test_bench_players_are_drawn_from_the_same_tier_mean(self) -> None:
        """§4.2 — a Team draws ALL its players (bench 7+ included) at its tier.

        With 8 players per team, players 7 and 8 sit on the bench. The
        tier-0 team's FULL roster (bench included) must still out-rate the
        tier-(N-1) team's full roster.
        """
        created = _generate_teams(
            4,
            8,
            rng=random.Random(SEED),
            mean=50,
            std_dev=15,
            team_names_pool=_team_pool("T"),
            player_names_pool=_player_pool("T"),
            tier_means=compute_tier_means(4, 50, 16),
        )
        for team in created:
            self.assertEqual(team.players.count(), 8)
            self.assertEqual(len(team.bench_players), 2)

        def full_roster_mean(team: Team) -> float:
            players = list(team.players.all())
            return sum(p.overall_rating for p in players) / len(players)

        self.assertGreater(
            full_roster_mean(created[0]) - full_roster_mean(created[-1]), 10.0
        )
