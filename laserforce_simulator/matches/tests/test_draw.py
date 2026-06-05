"""LG-02x-1 — Pure-unit tests for ``matches/draw.py``.

No DB, no Django imports in the assertion path. Seam contract locked at
``.claude/worktrees/lg-02x-1-seam-contract.md`` (§2 / §7). Mirrors the LG-02a
``test_bracket.py`` / LG-01 ``test_standings.py`` precedent — pure
``SimpleTestCase`` with hand-crafted inputs, plus the
``TestNoDjangoImportsLeaked`` subprocess fresh-import check.

The pure module owns the tier-balanced draw math (``compute_draw`` +
``DrawnTeamPlan``) and the two role-assignment-mode bijection builders
(``build_random_role_assignment`` / ``build_per_tier_role_assignment``). Frozen
import allowlist: ``dataclasses`` / ``typing`` / ``random`` / ``collections`` —
NO Django, NO ORM, NO ``datetime``, NO file I/O. ``random`` is allowed only
because the role builders consume an INJECTED ``random.Random``; the draw
computation itself consumes NO RNG (deterministic straight-tiers + greedy
balance).

These assertions WILL fail / ImportError until the Code agent lands
``matches/draw.py`` (the module may not yet exist); that is expected for the
parallel build.
"""

from __future__ import annotations

import random

from django.test import SimpleTestCase

from matches.draw import (
    ROLE_SLOTS,
    DrawnTeamPlan,
    build_per_tier_role_assignment,
    build_random_role_assignment,
    compute_draw,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pool(n: int, *, descending: bool = True) -> list[tuple[int, float]]:
    """``n`` (player_id, rating) pairs with DISTINCT ratings.

    player_id = 100 + i; ratings strictly decreasing (so the rating-DESC sort
    is unambiguous and the resulting tiers are predictable). ``descending``
    flips the input order so we can prove the sort is applied regardless of
    incoming order.
    """
    pairs = [(100 + i, float(1000 - i)) for i in range(n)]
    if not descending:
        pairs = list(reversed(pairs))
    return pairs


# ===========================================================================
# §2b — compute_draw: validation
# ===========================================================================


class TestComputeDrawValidation(SimpleTestCase):
    """Precondition: ``len(pool) % 6 == 0`` and ``len(pool) >= 24``."""

    def test_raises_value_error_when_not_divisible_by_six(self) -> None:
        # 25 is >= 24 but 25 % 6 != 0.
        with self.assertRaises(ValueError):
            compute_draw(_pool(25))

    def test_raises_value_error_when_below_24(self) -> None:
        # 18 is divisible by 6 but < 24 (only 3 teams).
        with self.assertRaises(ValueError):
            compute_draw(_pool(18))

    def test_raises_value_error_at_12(self) -> None:
        with self.assertRaises(ValueError):
            compute_draw(_pool(12))

    def test_raises_value_error_on_empty_pool(self) -> None:
        with self.assertRaises(ValueError):
            compute_draw([])

    def test_n23_not_divisible_raises(self) -> None:
        with self.assertRaises(ValueError):
            compute_draw(_pool(23))

    def test_n24_is_the_minimum_valid_size(self) -> None:
        # 24 is exactly 4 teams — the smallest legal pool; must NOT raise.
        plans = compute_draw(_pool(24))
        self.assertEqual(len(plans), 4)


# ===========================================================================
# §2b — compute_draw: shape (team count / 6-per-team / one-per-tier)
# ===========================================================================


class TestComputeDrawShape(SimpleTestCase):
    """Worked cases N=24 / 30 / 48: team count N/6, every team 6 players,
    one player per tier per team."""

    def test_returns_drawn_team_plan_instances(self) -> None:
        for plan in compute_draw(_pool(24)):
            self.assertIsInstance(plan, DrawnTeamPlan)

    def test_n24_yields_four_teams(self) -> None:
        self.assertEqual(len(compute_draw(_pool(24))), 4)

    def test_n30_yields_five_teams(self) -> None:
        self.assertEqual(len(compute_draw(_pool(30))), 5)

    def test_n48_yields_eight_teams(self) -> None:
        self.assertEqual(len(compute_draw(_pool(48))), 8)

    def test_every_team_has_exactly_six_players(self) -> None:
        for n in (24, 30, 48):
            for plan in compute_draw(_pool(n)):
                self.assertEqual(
                    len(plan.player_ids), 6, f"N={n} team must have 6 players"
                )

    def test_every_team_has_six_parallel_tiers(self) -> None:
        for n in (24, 30, 48):
            for plan in compute_draw(_pool(n)):
                self.assertEqual(len(plan.tiers), 6, f"N={n} tiers parallel to ids")

    def test_each_team_has_one_player_per_tier(self) -> None:
        # Every team carries exactly one player from each tier 1..6.
        for n in (24, 30, 48):
            for plan in compute_draw(_pool(n)):
                self.assertEqual(
                    sorted(plan.tiers),
                    [1, 2, 3, 4, 5, 6],
                    f"N={n} team must have one player per tier 1..6",
                )

    def test_team_index_is_zero_based_and_dense(self) -> None:
        for n in (24, 30, 48):
            plans = compute_draw(_pool(n))
            indices = sorted(p.team_index for p in plans)
            self.assertEqual(indices, list(range(n // 6)))

    def test_every_player_assigned_exactly_once(self) -> None:
        for n in (24, 30, 48):
            plans = compute_draw(_pool(n))
            all_ids = [pid for p in plans for pid in p.player_ids]
            self.assertEqual(len(all_ids), n)
            self.assertEqual(len(set(all_ids)), n, "no player drawn twice")
            self.assertEqual(set(all_ids), {pid for pid, _ in _pool(n)})

    def test_player_ids_and_tiers_are_tuples(self) -> None:
        plan = compute_draw(_pool(24))[0]
        self.assertIsInstance(plan.player_ids, tuple)
        self.assertIsInstance(plan.tiers, tuple)


# ===========================================================================
# §2b — compute_draw: straight tiers + sort
# ===========================================================================


class TestComputeDrawTiers(SimpleTestCase):
    """Sort rating-DESC (player_id ASC tiebreak); 6 contiguous tiers of T."""

    def test_tier1_is_the_strongest_band(self) -> None:
        # N=24 ⇒ T=4. Sorted DESC, the first 4 ids are the top-rated band; each
        # must be a tier-1 player across the 4 teams.
        n = 24
        plans = compute_draw(_pool(n))
        sorted_ids = [pid for pid, _ in sorted(_pool(n), key=lambda x: (-x[1], x[0]))]
        top_band = set(sorted_ids[:4])
        tier1_ids = {
            pid
            for plan in plans
            for pid, tier in zip(plan.player_ids, plan.tiers)
            if tier == 1
        }
        self.assertEqual(tier1_ids, top_band, "tier 1 = strongest T players")

    def test_tier6_is_the_weakest_band(self) -> None:
        n = 24
        plans = compute_draw(_pool(n))
        sorted_ids = [pid for pid, _ in sorted(_pool(n), key=lambda x: (-x[1], x[0]))]
        weakest_band = set(sorted_ids[-4:])
        tier6_ids = {
            pid
            for plan in plans
            for pid, tier in zip(plan.player_ids, plan.tiers)
            if tier == 6
        }
        self.assertEqual(tier6_ids, weakest_band, "tier 6 = weakest T players")

    def test_player_ids_within_a_team_ordered_tier_one_to_six(self) -> None:
        # The contract pins player_ids/tiers ordered tier 1..6 within each team.
        for plan in compute_draw(_pool(30)):
            self.assertEqual(list(plan.tiers), [1, 2, 3, 4, 5, 6])

    def test_player_id_ascending_tiebreak_on_equal_rating(self) -> None:
        # All-equal ratings ⇒ pure player_id-ASC ordering decides the tiers.
        # N=24, every rating identical ⇒ ids 0..23 sort ascending; tier 1 = the
        # 4 LOWEST ids.
        pool = [(100 + i, 50.0) for i in range(24)]
        plans = compute_draw(pool)
        tier1_ids = sorted(
            pid
            for plan in plans
            for pid, tier in zip(plan.player_ids, plan.tiers)
            if tier == 1
        )
        self.assertEqual(tier1_ids, [100, 101, 102, 103])


# ===========================================================================
# §2b — compute_draw: greedy balance to the currently-weakest team
# ===========================================================================


class TestComputeDrawGreedyBalance(SimpleTestCase):
    """Within each tier the strongest-remaining player goes to the
    currently-weakest team (team_index ASC tiebreak on equal totals)."""

    def test_tier1_strongest_to_team_index_zero_on_equal_start(self) -> None:
        # All teams start at running-total 0 ⇒ tier-1's strongest player goes to
        # the team_index-0 team (ASC tiebreak), the next to team 1, etc.
        n = 24
        plans = compute_draw(_pool(n))
        sorted_pairs = sorted(_pool(n), key=lambda x: (-x[1], x[0]))
        strongest_id = sorted_pairs[0][0]
        team0 = next(p for p in plans if p.team_index == 0)
        # team 0's tier-1 player is the single strongest player overall.
        tier1_of_team0 = team0.player_ids[team0.tiers.index(1)]
        self.assertEqual(tier1_of_team0, strongest_id)

    def test_snake_balance_keeps_totals_close(self) -> None:
        # Greedy "to the weakest team" should keep per-team rating totals tight.
        # With strictly-decreasing ratings, a snake-style assignment keeps the
        # spread small relative to a single tier's spread.
        n = 24
        pool = _pool(n)
        rating_by_id = dict(pool)
        plans = compute_draw(pool)
        totals = [sum(rating_by_id[pid] for pid in p.player_ids) for p in plans]
        spread = max(totals) - min(totals)
        # A pathological "all strong on one team" split would have a spread of
        # roughly one tier's worth of rating per slot; greedy balance keeps it
        # well under a single top-vs-bottom player gap.
        worst_player_gap = pool[0][1] - pool[-1][1]
        self.assertLess(spread, worst_player_gap, "greedy balance must tighten totals")

    def test_balance_assigns_one_per_team_per_tier(self) -> None:
        # The greedy pass must never double-assign a tier player to one team:
        # each team gets exactly one of the T tier-k players.
        n = 30
        plans = compute_draw(_pool(n))
        for tier in range(1, 7):
            teams_with_this_tier = [p.team_index for p in plans if tier in p.tiers]
            self.assertEqual(
                sorted(teams_with_this_tier),
                sorted(p.team_index for p in plans),
                f"every team gets exactly one tier-{tier} player",
            )


# ===========================================================================
# §2b — compute_draw: deterministic / idempotent (byte-equal across calls)
# ===========================================================================


class TestComputeDrawDeterministic(SimpleTestCase):
    """Same pool ⇒ identical output across repeated calls (consumes no RNG;
    a re-roll is a no-op — admin hand-edits are the variation mechanism)."""

    def test_two_calls_same_pool_byte_equal(self) -> None:
        pool = _pool(48)
        first = compute_draw(pool)
        second = compute_draw(pool)
        # DrawnTeamPlan is a frozen dataclass ⇒ value-equality; list-equality
        # asserts identical team_index / player_ids / tiers in identical order.
        self.assertEqual(first, second)

    def test_input_order_independent(self) -> None:
        # The same SET of (id, rating) pairs in a different incoming order
        # yields the same draw (the sort canonicalises the order).
        ascending = compute_draw(_pool(24, descending=False))
        descending = compute_draw(_pool(24, descending=True))
        self.assertEqual(ascending, descending)

    def test_repeated_calls_consume_no_global_rng(self) -> None:
        # Pin the global RNG; if compute_draw consumed it, the global state
        # would advance and a subsequent draw of the same pool could differ.
        random.seed(12345)
        before = random.getstate()
        compute_draw(_pool(30))
        after = random.getstate()
        self.assertEqual(before, after, "compute_draw must not touch the global RNG")

    def test_does_not_mutate_input_pool(self) -> None:
        pool = _pool(24)
        snapshot = list(pool)
        compute_draw(pool)
        self.assertEqual(pool, snapshot, "compute_draw must not mutate its input")


# ===========================================================================
# §2c — build_random_role_assignment
# ===========================================================================


class TestBuildRandomRoleAssignment(SimpleTestCase):
    """`random` mode, per TEAM: shuffle the team's 6 tier-ordered ids into the
    6 ROLE_SLOTS. Returns {slot_suffix: player_id} over all 6 slots; consumes
    one rng shuffle; no duplicate slot / player."""

    _IDS = [11, 22, 33, 44, 55, 66]  # tier 1..6 order

    def test_keys_are_exactly_the_six_role_slots(self) -> None:
        rng = random.Random(0)
        result = build_random_role_assignment(self._IDS, rng)
        self.assertEqual(set(result.keys()), set(ROLE_SLOTS))
        self.assertEqual(len(result), 6)

    def test_values_are_a_permutation_of_the_input_ids(self) -> None:
        rng = random.Random(0)
        result = build_random_role_assignment(self._IDS, rng)
        self.assertEqual(sorted(result.values()), sorted(self._IDS))

    def test_no_duplicate_player_across_slots(self) -> None:
        rng = random.Random(7)
        result = build_random_role_assignment(self._IDS, rng)
        self.assertEqual(len(set(result.values())), 6, "no player fills two slots")

    def test_role_slots_has_six_distinct_entries(self) -> None:
        # Defensive: ROLE_SLOTS is the 6 fixed suffixes in order.
        self.assertEqual(len(ROLE_SLOTS), 6)
        self.assertEqual(len(set(ROLE_SLOTS)), 6)

    def test_role_slots_locked_order(self) -> None:
        self.assertEqual(
            tuple(ROLE_SLOTS),
            ("commander", "heavy", "scout_1", "scout_2", "medic", "ammo"),
        )

    def test_consumes_one_shuffle_deterministic_per_seed(self) -> None:
        # Two builders seeded identically produce identical assignments (the
        # single shuffle is the only RNG consumption).
        a = build_random_role_assignment(self._IDS, random.Random(99))
        b = build_random_role_assignment(self._IDS, random.Random(99))
        self.assertEqual(a, b)

    def test_advances_rng_by_one_shuffle(self) -> None:
        # After the call the injected rng's state must equal a fresh rng that
        # performed exactly one shuffle of a 6-element list (pins "one shuffle").
        rng = random.Random(2024)
        build_random_role_assignment(self._IDS, rng)
        probe = random.Random(2024)
        probe.shuffle([0, 0, 0, 0, 0, 0])
        self.assertEqual(rng.getstate(), probe.getstate())

    def test_different_seeds_can_differ(self) -> None:
        # Sanity: the assignment actually depends on the rng (not a no-op).
        seen = {
            tuple(build_random_role_assignment(self._IDS, random.Random(s)).items())
            for s in range(20)
        }
        self.assertGreater(len(seen), 1, "the shuffle must vary with the seed")


# ===========================================================================
# §2c — build_per_tier_role_assignment
# ===========================================================================


class TestBuildPerTierRoleAssignment(SimpleTestCase):
    """`per_tier` mode: ONE {tier: slot} bijection for the Round, applied to
    BOTH teams. Returns {tier (1..6): slot_suffix} — a permutation of
    ROLE_SLOTS keyed by tier; consumes one rng shuffle."""

    def test_keys_are_tiers_one_through_six(self) -> None:
        result = build_per_tier_role_assignment(random.Random(0))
        self.assertEqual(set(result.keys()), {1, 2, 3, 4, 5, 6})
        self.assertEqual(len(result), 6)

    def test_values_are_a_permutation_of_role_slots(self) -> None:
        result = build_per_tier_role_assignment(random.Random(0))
        self.assertEqual(sorted(result.values()), sorted(ROLE_SLOTS))

    def test_is_a_bijection_no_slot_used_twice(self) -> None:
        result = build_per_tier_role_assignment(random.Random(3))
        self.assertEqual(len(set(result.values())), 6, "each slot used exactly once")

    def test_deterministic_per_seed(self) -> None:
        a = build_per_tier_role_assignment(random.Random(42))
        b = build_per_tier_role_assignment(random.Random(42))
        self.assertEqual(a, b)

    def test_advances_rng_by_one_shuffle(self) -> None:
        rng = random.Random(2024)
        build_per_tier_role_assignment(rng)
        probe = random.Random(2024)
        probe.shuffle([0, 0, 0, 0, 0, 0])
        self.assertEqual(rng.getstate(), probe.getstate())

    def test_different_seeds_can_differ(self) -> None:
        seen = {
            tuple(build_per_tier_role_assignment(random.Random(s)).items())
            for s in range(20)
        }
        self.assertGreater(len(seen), 1, "the bijection must vary with the seed")


# ===========================================================================
# §2 — Defensive: no Django imports leaked into the pure module
# ===========================================================================


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.draw`` in a fresh subprocess must not pull in
    ``django.*`` (nor ``matches.models``) — the frozen allowlist is
    ``dataclasses`` / ``typing`` / ``random`` / ``collections``. Mirrors the
    LG-02a ``test_bracket.py::TestNoDjangoImportsLeaked`` precedent.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.draw  # noqa: F401

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_role_builders_pull_in_no_django(self) -> None:
        """Importing + exercising the role builders in a fresh subprocess must
        not pull in ``django.*`` — they import nothing new (``random`` only).
        Mirrors the ``test_stage_progress_pulls_in_no_django`` precedent."""
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import random
            import sys
            sys.path.insert(0, {str(project_root)!r})
            from matches.draw import (
                build_per_tier_role_assignment,
                build_random_role_assignment,
                compute_draw,
            )

            # Exercise them so a lazy import inside any body would fire.
            compute_draw([(100 + i, float(1000 - i)) for i in range(24)])
            build_random_role_assignment([1, 2, 3, 4, 5, 6], random.Random(0))
            build_per_tier_role_assignment(random.Random(0))

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
