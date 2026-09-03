"""CONF-03 — Worlds qualification: the pure ``matches/worlds.py`` module and
the ``Season.worlds_qualifiers()`` derivation.

The seam contract is locked at ``.claude/worktrees/conf-03-seam-contract.md``;
the design rationale is
[ADR-0036](../../docs/adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md)
and the CONTEXT.md **Worlds** / **Worlds qualifier** / **Last-chance
qualifier** terms.

What is asserted here (contract §9.4 items 1-15):

* **The pure module** (§4) — ``qualifier_count_for_size``'s size tiers,
  ``first_unqualified`` / ``last_chance_field``'s field selection,
  ``order_worlds_qualifiers``'s tier-then-RATE ordering and ``seed`` stamping,
  ``WorldsQualifier.provenance_label``, and the frozen import allowlist
  (``dataclasses`` + ``typing``, no Django).
* **The derivation** (§5.5) — champion-is-rank-1 vs champion-is-NOT-rank-1, the
  §2.3 no-Regional-playoff fallback, the PREMATURE-FIELD regression guard, the
  all-or-nothing readiness rule, the 0/1-Conference ``[]``, and the
  nothing-is-persisted invariant.

**RATE, not raw totals (§2.5).** Conferences differ in size and therefore play
different numbers of games, so a 12-team Conference's 11-game total is not
comparable with a 5-team Conference's 4-game total. Every within-tier
comparison below is a rate comparison; ``matches_played == 0`` yields ``0.0``
for all three rates with no division attempted.

**No simulation (contract §9.1).** Every DB fixture here is hand-built — the
round-robin is a deterministic set of completed ``Match`` + ``GameRound`` rows
(shared with ``test_regional_playoffs.py``), and brackets are "drained" by
STAMPING ``Tournament.champion`` / ``state="completed"`` on the persisted rows.
That isolates the derivation from the bracket engine entirely. Assertions are
schema-level: ids, tiers, provenances, seeds, list lengths, row counts, DOM ids
— never a simulated point total.

**Internal detail is NOT asserted (contract §9.3).** ``matches.worlds._rate``
is module-private and is never imported here; ``Season._final_tournament_phase``
/ ``_build_last_chance_tournament`` are exercised only through their observable
effects on the public seam.

NOTE: this file requires the Code agent's ``matches/worlds.py`` +
``Tournament.qualifier_stage`` + migration ``0059_tournament_qualifier_stage``
+ the ``Season.worlds_qualifiers`` seam to land. Until then these tests are
EXPECTED to fail (ImportError / AttributeError) — the TDD red state, not a
defect in this file.
"""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from matches.models import BracketNode, Tournament, TournamentParticipant
from matches.tests.test_regional_playoffs import (
    _built_regional_season,
    _conf_season,
    _flat_season,
    _hand_play_rr,
    _ids,
    _stamp_bracket_completed,
)
from matches.worlds import (
    LAST_CHANCE_FIELD_SIZE,
    PROVENANCE_CHAMPION,
    PROVENANCE_LABELS,
    PROVENANCE_LAST_CHANCE,
    PROVENANCE_REGULAR_SEASON,
    QUALIFIER_TIER_CHAMPION,
    QUALIFIER_TIER_LAST_CHANCE,
    QUALIFIER_TIER_REGULAR_SEASON,
    WorldsQualifier,
    first_unqualified,
    last_chance_field,
    order_worlds_qualifiers,
    qualifier_count_for_size,
)

# ---------------------------------------------------------------------------
# Pure helpers — plain values, no DB
# ---------------------------------------------------------------------------


def _qualifier(
    team_id: int,
    *,
    tier: int = QUALIFIER_TIER_CHAMPION,
    provenance: str = PROVENANCE_CHAMPION,
    matches_played: int = 0,
    league_points: int = 0,
    round_wins: int = 0,
    total_score: int = 0,
    conference_id: int = 1,
    conference_name: str = "Conf",
    seed: int = 0,
) -> WorldsQualifier:
    """A ``WorldsQualifier`` with only the fields a test cares about set."""
    return WorldsQualifier(
        team_id=team_id,
        team_name=f"Team {team_id}",
        conference_id=conference_id,
        conference_name=conference_name,
        tier=tier,
        provenance=provenance,
        matches_played=matches_played,
        league_points=league_points,
        round_wins=round_wins,
        total_score=total_score,
        seed=seed,
    )


def _team_order(qualifiers: "list[WorldsQualifier]") -> list[int]:
    return [q.team_id for q in qualifiers]


# ===========================================================================
# 1. Tier boundaries — qualifier_count_for_size
# ===========================================================================


class TestQualifierCountForSize(SimpleTestCase):
    """The size tiers of §2.1, boundary by boundary. Pure, no DB."""

    def test_degenerate_sizes_send_nobody(self) -> None:
        self.assertEqual(qualifier_count_for_size(0), 0)
        self.assertEqual(qualifier_count_for_size(1), 0)

    def test_two_to_four_teams_send_one(self) -> None:
        self.assertEqual(qualifier_count_for_size(2), 1)
        self.assertEqual(qualifier_count_for_size(3), 1)
        self.assertEqual(qualifier_count_for_size(4), 1)

    def test_five_to_eight_teams_send_two(self) -> None:
        self.assertEqual(qualifier_count_for_size(5), 2)
        self.assertEqual(qualifier_count_for_size(8), 2)

    def test_nine_or_more_teams_send_three(self) -> None:
        self.assertEqual(qualifier_count_for_size(9), 3)
        self.assertEqual(qualifier_count_for_size(20), 3)

    def test_the_boundaries_step_exactly_at_5_and_9(self) -> None:
        # The two edges the whole slice hinges on: 4|5 and 8|9.
        self.assertNotEqual(qualifier_count_for_size(4), qualifier_count_for_size(5))
        self.assertNotEqual(qualifier_count_for_size(8), qualifier_count_for_size(9))

    def test_a_negative_size_returns_zero(self) -> None:
        self.assertEqual(qualifier_count_for_size(-1), 0)
        self.assertEqual(qualifier_count_for_size(-100), 0)


# ===========================================================================
# 2. Rate ordering across UNEQUAL Conference sizes
# ===========================================================================


class TestRateOrdering(SimpleTestCase):
    """Within one tier the order is RATE, not raw totals — the whole reason
    §2.5 divides by ``matches_played``. Pure, no DB."""

    def test_fewer_matches_but_higher_rate_outranks_a_bigger_raw_total(self) -> None:
        # A 5-team Conference plays 4 games; a 12-team Conference plays 11.
        small = _qualifier(
            10,
            tier=QUALIFIER_TIER_REGULAR_SEASON,
            provenance=PROVENANCE_REGULAR_SEASON,
            matches_played=4,
            league_points=8,  # 2.00 per match
        )
        large = _qualifier(
            20,
            tier=QUALIFIER_TIER_REGULAR_SEASON,
            provenance=PROVENANCE_REGULAR_SEASON,
            matches_played=11,
            league_points=15,  # ~1.36 per match — a BIGGER raw total
        )
        ordered = order_worlds_qualifiers([large, small])
        self.assertEqual(_team_order(ordered), [10, 20])

    def test_equal_points_rate_breaks_on_round_wins_rate(self) -> None:
        better = _qualifier(
            10,
            tier=QUALIFIER_TIER_REGULAR_SEASON,
            provenance=PROVENANCE_REGULAR_SEASON,
            matches_played=4,
            league_points=8,  # 2.00
            round_wins=6,  # 1.50
        )
        worse = _qualifier(
            20,
            tier=QUALIFIER_TIER_REGULAR_SEASON,
            provenance=PROVENANCE_REGULAR_SEASON,
            matches_played=2,
            league_points=4,  # 2.00 — identical points rate
            round_wins=2,  # 1.00
        )
        ordered = order_worlds_qualifiers([worse, better])
        self.assertEqual(_team_order(ordered), [10, 20])

    def test_equal_points_and_round_win_rates_break_on_total_score_rate(self) -> None:
        better = _qualifier(
            10,
            tier=QUALIFIER_TIER_REGULAR_SEASON,
            provenance=PROVENANCE_REGULAR_SEASON,
            matches_played=4,
            league_points=8,  # 2.00
            round_wins=6,  # 1.50
            total_score=400,  # 100.0
        )
        worse = _qualifier(
            20,
            tier=QUALIFIER_TIER_REGULAR_SEASON,
            provenance=PROVENANCE_REGULAR_SEASON,
            matches_played=2,
            league_points=4,  # 2.00
            round_wins=3,  # 1.50
            total_score=150,  # 75.0
        )
        ordered = order_worlds_qualifiers([worse, better])
        self.assertEqual(_team_order(ordered), [10, 20])

    def test_all_three_rates_equal_breaks_on_team_id_ascending(self) -> None:
        low = _qualifier(
            7,
            matches_played=4,
            league_points=8,
            round_wins=6,
            total_score=400,
        )
        high = _qualifier(
            9,
            matches_played=2,
            league_points=4,
            round_wins=3,
            total_score=200,
        )
        ordered = order_worlds_qualifiers([high, low])
        self.assertEqual(_team_order(ordered), [7, 9])


# ===========================================================================
# 3. Tier beats rate
# ===========================================================================


class TestTierBeatsRate(SimpleTestCase):
    """``tier`` ASC is the FIRST sort key — a champion always seeds ahead of a
    regular-season qualifier, however bad its regular season was."""

    def test_a_terrible_champion_seeds_ahead_of_the_best_regular_season(self) -> None:
        awful_champion = _qualifier(
            50,
            tier=QUALIFIER_TIER_CHAMPION,
            provenance=PROVENANCE_CHAMPION,
            matches_played=10,
            league_points=0,
            round_wins=0,
            total_score=0,
        )
        stellar_runner_up = _qualifier(
            10,
            tier=QUALIFIER_TIER_REGULAR_SEASON,
            provenance=PROVENANCE_REGULAR_SEASON,
            matches_played=10,
            league_points=60,
            round_wins=20,
            total_score=5000,
        )
        ordered = order_worlds_qualifiers([stellar_runner_up, awful_champion])
        self.assertEqual(_team_order(ordered), [50, 10])

    def test_the_three_tiers_block_in_order(self) -> None:
        last_chance = _qualifier(
            1,
            tier=QUALIFIER_TIER_LAST_CHANCE,
            provenance=PROVENANCE_LAST_CHANCE,
            matches_played=4,
            league_points=99,
        )
        regular = _qualifier(
            2,
            tier=QUALIFIER_TIER_REGULAR_SEASON,
            provenance=PROVENANCE_REGULAR_SEASON,
            matches_played=4,
            league_points=50,
        )
        champion = _qualifier(
            3,
            tier=QUALIFIER_TIER_CHAMPION,
            provenance=PROVENANCE_CHAMPION,
            matches_played=4,
            league_points=1,
        )
        ordered = order_worlds_qualifiers([last_chance, regular, champion])
        self.assertEqual([q.tier for q in ordered], [1, 2, 3])
        self.assertEqual(_team_order(ordered), [3, 2, 1])


# ===========================================================================
# 4. matches_played == 0
# ===========================================================================


class TestZeroMatchesPlayed(SimpleTestCase):
    """§2.5's locked rule: ``matches_played <= 0`` ⇒ every rate is ``0.0``.
    No division, no ``None``, no sentinel, no exception."""

    def test_zero_matches_played_does_not_raise(self) -> None:
        unplayed = _qualifier(10, matches_played=0, league_points=99, round_wins=99)
        # The regression: a naive ``league_points / matches_played``.
        ordered = order_worlds_qualifiers([unplayed])
        self.assertEqual(_team_order(ordered), [10])
        self.assertEqual(ordered[0].seed, 1)

    def test_a_zero_rate_team_sorts_below_any_positive_rate_in_its_tier(self) -> None:
        unplayed = _qualifier(10, matches_played=0, league_points=99)
        barely_played = _qualifier(20, matches_played=1, league_points=1)
        ordered = order_worlds_qualifiers([unplayed, barely_played])
        self.assertEqual(_team_order(ordered), [20, 10])

    def test_two_zero_rate_teams_tie_and_fall_to_team_id_ascending(self) -> None:
        high = _qualifier(31, matches_played=0, league_points=500, total_score=900)
        low = _qualifier(11, matches_played=0, league_points=0, total_score=0)
        ordered = order_worlds_qualifiers([high, low])
        self.assertEqual(_team_order(ordered), [11, 31])

    def test_a_negative_matches_played_is_also_zero_rate(self) -> None:
        # Defensive: ``<= 0``, not ``== 0``.
        broken = _qualifier(10, matches_played=-3, league_points=10)
        played = _qualifier(20, matches_played=2, league_points=1)
        ordered = order_worlds_qualifiers([broken, played])
        self.assertEqual(_team_order(ordered), [20, 10])

    def test_a_whole_field_of_zero_rate_teams_orders_by_team_id(self) -> None:
        field = [_qualifier(tid, matches_played=0) for tid in (40, 12, 27)]
        ordered = order_worlds_qualifiers(field)
        self.assertEqual(_team_order(ordered), [12, 27, 40])


# ===========================================================================
# 5. order_worlds_qualifiers stamps seed and does not mutate its input
# ===========================================================================


class TestOrderStampsSeeds(SimpleTestCase):
    """``seed`` is stamped 1..M on a NEW list, exactly as ``compute_standings``
    stamps ``rank``."""

    def _field(self) -> "list[WorldsQualifier]":
        return [
            _qualifier(
                30,
                tier=QUALIFIER_TIER_REGULAR_SEASON,
                provenance=PROVENANCE_REGULAR_SEASON,
                matches_played=4,
                league_points=4,
            ),
            _qualifier(10, matches_played=4, league_points=8),
            _qualifier(20, matches_played=4, league_points=4),
        ]

    def test_seeds_are_stamped_one_through_m(self) -> None:
        ordered = order_worlds_qualifiers(self._field())
        self.assertEqual([q.seed for q in ordered], [1, 2, 3])

    def test_returns_a_new_list_and_does_not_mutate_the_input(self) -> None:
        field = self._field()
        ordered = order_worlds_qualifiers(field)
        self.assertIsNot(ordered, field)
        self.assertEqual([q.seed for q in field], [0, 0, 0])

    def test_the_input_entries_are_not_the_output_entries(self) -> None:
        field = self._field()
        ordered = order_worlds_qualifiers(field)
        # ``dataclasses.replace`` produces fresh frozen instances.
        for entry in ordered:
            self.assertNotIn(entry, [f for f in field if f.seed == entry.seed])

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(order_worlds_qualifiers([]), [])

    def test_a_single_entry_gets_seed_one(self) -> None:
        ordered = order_worlds_qualifiers([_qualifier(10, matches_played=4)])
        self.assertEqual(ordered[0].seed, 1)

    def test_an_already_seeded_input_is_restamped_not_trusted(self) -> None:
        stale = [
            _qualifier(10, matches_played=4, league_points=8, seed=9),
            _qualifier(20, matches_played=4, league_points=4, seed=1),
        ]
        ordered = order_worlds_qualifiers(stale)
        self.assertEqual([(q.team_id, q.seed) for q in ordered], [(10, 1), (20, 2)])


# ===========================================================================
# 6. first_unqualified / last_chance_field
# ===========================================================================


class TestFirstUnqualified(SimpleTestCase):
    """The tier-2 rule and the §2.3 fallback's rank-1 rule in one function."""

    def test_returns_rank_one_when_nobody_is_qualified(self) -> None:
        self.assertEqual(first_unqualified([5, 6, 7], set()), 5)

    def test_skips_an_already_qualified_leader(self) -> None:
        self.assertEqual(first_unqualified([5, 6, 7], {5}), 6)

    def test_skips_a_run_of_qualified_ids(self) -> None:
        self.assertEqual(first_unqualified([5, 6, 7, 8], {5, 6, 7}), 8)

    def test_returns_none_when_every_ranked_id_is_qualified(self) -> None:
        self.assertIsNone(first_unqualified([5, 6], {5, 6}))

    def test_returns_none_on_an_empty_ranked_list(self) -> None:
        self.assertIsNone(first_unqualified([], {5}))
        self.assertIsNone(first_unqualified([], set()))

    def test_accepts_any_container_supporting_in(self) -> None:
        self.assertEqual(first_unqualified([5, 6, 7], [5, 6]), 7)
        self.assertEqual(first_unqualified([5, 6, 7], (5,)), 6)


class TestLastChanceField(SimpleTestCase):
    """UP TO ``LAST_CHANCE_FIELD_SIZE`` ids, rank order, never padded, never
    raising."""

    def test_field_size_constant_is_four(self) -> None:
        self.assertEqual(LAST_CHANCE_FIELD_SIZE, 4)

    def test_takes_exactly_four_excluding_both_qualified_ids(self) -> None:
        ranked = [1, 2, 3, 4, 5, 6, 7, 8, 9]
        # 3 won the Regional playoff; 1 took the tier-2 regular-season slot.
        field = last_chance_field(ranked, {3, 1})
        self.assertEqual(field, [2, 4, 5, 6])
        self.assertEqual(len(field), LAST_CHANCE_FIELD_SIZE)

    def test_preserves_rank_order_so_index_zero_is_bracket_seed_one(self) -> None:
        field = last_chance_field([9, 8, 7, 6, 5], set())
        self.assertEqual(field, [9, 8, 7, 6])

    def test_returns_fewer_than_four_on_a_short_list_without_padding(self) -> None:
        field = last_chance_field([1, 2, 3], {1})
        self.assertEqual(field, [2, 3])

    def test_returns_empty_list_on_an_empty_ranked_list(self) -> None:
        self.assertEqual(last_chance_field([], {1}), [])

    def test_returns_empty_list_when_everyone_is_qualified(self) -> None:
        self.assertEqual(last_chance_field([1, 2], {1, 2}), [])

    def test_does_not_mutate_the_ranked_list(self) -> None:
        ranked = [1, 2, 3, 4, 5, 6]
        last_chance_field(ranked, {1})
        self.assertEqual(ranked, [1, 2, 3, 4, 5, 6])


# ===========================================================================
# 7. provenance_label
# ===========================================================================


class TestProvenanceLabel(SimpleTestCase):
    """The zero-arg display property Django templates call directly."""

    def test_labels_for_all_three_provenances(self) -> None:
        self.assertEqual(
            _qualifier(1, provenance=PROVENANCE_CHAMPION).provenance_label,
            "Conference champion",
        )
        self.assertEqual(
            _qualifier(1, provenance=PROVENANCE_REGULAR_SEASON).provenance_label,
            "Regular season",
        )
        self.assertEqual(
            _qualifier(1, provenance=PROVENANCE_LAST_CHANCE).provenance_label,
            "Last-chance qualifier",
        )

    def test_unknown_provenance_yields_the_empty_string(self) -> None:
        self.assertEqual(_qualifier(1, provenance="nope").provenance_label, "")
        self.assertEqual(_qualifier(1, provenance="").provenance_label, "")

    def test_labels_dict_covers_exactly_the_three_constants(self) -> None:
        self.assertEqual(
            set(PROVENANCE_LABELS),
            {PROVENANCE_CHAMPION, PROVENANCE_REGULAR_SEASON, PROVENANCE_LAST_CHANCE},
        )

    def test_tier_and_provenance_are_separate_axes(self) -> None:
        # §2.3's fallback is exactly where they disagree: tier 1, but the slot
        # was earned in the regular season.
        fallback = _qualifier(
            1,
            tier=QUALIFIER_TIER_CHAMPION,
            provenance=PROVENANCE_REGULAR_SEASON,
        )
        self.assertEqual(fallback.tier, QUALIFIER_TIER_CHAMPION)
        self.assertEqual(fallback.provenance, PROVENANCE_REGULAR_SEASON)
        self.assertEqual(fallback.provenance_label, "Regular season")

    def test_tier_constants_are_one_two_three(self) -> None:
        self.assertEqual(QUALIFIER_TIER_CHAMPION, 1)
        self.assertEqual(QUALIFIER_TIER_REGULAR_SEASON, 2)
        self.assertEqual(QUALIFIER_TIER_LAST_CHANCE, 3)


# ===========================================================================
# 8. The frozen import allowlist
# ===========================================================================


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.worlds`` in a fresh subprocess must not pull in
    ``django.*`` or ``matches.models`` — the allowlist is ``dataclasses`` +
    ``typing`` (contract §4), mirroring ``matches.standings`` /
    ``matches.bracket``.
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
            import matches.worlds  # noqa: F401

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


# ===========================================================================
# DB fixtures for Season.worlds_qualifiers()
# ===========================================================================


def _by_tier(qualifiers, tier: int) -> "list[WorldsQualifier]":
    return [q for q in qualifiers if q.tier == tier]


def _for_conference(qualifiers, conference) -> "list[WorldsQualifier]":
    return [q for q in qualifiers if q.conference_id == conference.id]


def _stamp_last_chance_champion(phase, conference, champion) -> None:
    """Crown a Last-chance bracket by STAMPING the persisted row (§9.1
    technique 2) — the derivation tests never seed or drain it."""
    row = phase.regional_tournaments.filter(
        conference=conference, qualifier_stage="last_chance"
    ).first()
    assert row is not None, "fixture precondition: a Last-chance row exists"
    row.champion = champion
    row.state = "completed"
    row.save(update_fields=["champion", "state"])


# ===========================================================================
# 9 + 10. Champion IS / IS NOT Standings rank 1
# ===========================================================================


class TestWorldsQualifiersChampionIsRankOne(TestCase):
    """A Conference whose rank-1 Team also won its Regional playoff sends that
    Team as tier 1 and RANK 2 as tier 2 — never the same Team twice."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _built_regional_season("WqRank1", [5, 5])
        self.first = self.phase.regional_tournaments.get(conference=self.conferences[0])
        self.second = self.phase.regional_tournaments.get(
            conference=self.conferences[1]
        )
        # Both Conferences' rank-1 Teams win their own brackets.
        _stamp_bracket_completed(self.first, self.groups[0][0])
        _stamp_bracket_completed(self.second, self.groups[1][0])
        self.field = self.season.worlds_qualifiers()

    def test_two_qualifiers_per_five_team_conference(self) -> None:
        self.assertEqual(len(self.field), 4)
        self.assertEqual(len(_for_conference(self.field, self.conferences[0])), 2)
        self.assertEqual(len(_for_conference(self.field, self.conferences[1])), 2)

    def test_tier_one_is_the_regional_playoff_champion(self) -> None:
        tier1 = _for_conference(
            _by_tier(self.field, QUALIFIER_TIER_CHAMPION), self.conferences[0]
        )
        self.assertEqual([q.team_id for q in tier1], [self.groups[0][0].id])
        self.assertEqual(tier1[0].provenance, PROVENANCE_CHAMPION)

    def test_tier_two_is_standings_rank_two(self) -> None:
        tier2 = _for_conference(
            _by_tier(self.field, QUALIFIER_TIER_REGULAR_SEASON), self.conferences[0]
        )
        self.assertEqual([q.team_id for q in tier2], [self.groups[0][1].id])
        self.assertEqual(tier2[0].provenance, PROVENANCE_REGULAR_SEASON)

    def test_no_team_appears_twice(self) -> None:
        ids = [q.team_id for q in self.field]
        self.assertEqual(len(ids), len(set(ids)))

    def test_seeds_are_contiguous_from_one(self) -> None:
        self.assertEqual([q.seed for q in self.field], [1, 2, 3, 4])

    def test_champions_block_ahead_of_regular_season_qualifiers(self) -> None:
        self.assertEqual([q.tier for q in self.field], [1, 1, 2, 2])

    def test_conference_name_is_carried_on_every_entry(self) -> None:
        names = {c.id: c.name for c in self.conferences}
        for entry in self.field:
            self.assertEqual(entry.conference_name, names[entry.conference_id])

    def test_regular_season_integers_come_off_the_standings_row(self) -> None:
        # A 5-team Conference's round-robin: every Team has played, so no rate
        # is zero-by-construction. Schema-level only — never a point total.
        for entry in self.field:
            self.assertGreater(entry.matches_played, 0)
            self.assertGreaterEqual(entry.league_points, 0)
            self.assertGreaterEqual(entry.round_wins, 0)
            self.assertGreaterEqual(entry.total_score, 0)


class TestWorldsQualifiersChampionIsNotRankOne(TestCase):
    """When rank 1 LOSES the bracket it takes the tier-2 slot; the champion
    takes tier 1."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _built_regional_season("WqUpset", [5, 5])
        self.first = self.phase.regional_tournaments.get(conference=self.conferences[0])
        self.second = self.phase.regional_tournaments.get(
            conference=self.conferences[1]
        )
        # Conference 1's rank-THREE Team wins the bracket (the upset).
        _stamp_bracket_completed(self.first, self.groups[0][2])
        _stamp_bracket_completed(self.second, self.groups[1][0])
        self.field = self.season.worlds_qualifiers()

    def test_tier_one_is_the_upset_champion_not_rank_one(self) -> None:
        tier1 = _for_conference(
            _by_tier(self.field, QUALIFIER_TIER_CHAMPION), self.conferences[0]
        )
        self.assertEqual([q.team_id for q in tier1], [self.groups[0][2].id])
        self.assertEqual(tier1[0].provenance, PROVENANCE_CHAMPION)

    def test_tier_two_is_the_beaten_rank_one(self) -> None:
        tier2 = _for_conference(
            _by_tier(self.field, QUALIFIER_TIER_REGULAR_SEASON), self.conferences[0]
        )
        self.assertEqual([q.team_id for q in tier2], [self.groups[0][0].id])
        self.assertEqual(tier2[0].provenance, PROVENANCE_REGULAR_SEASON)

    def test_rank_two_does_not_qualify(self) -> None:
        conf_ids = [q.team_id for q in _for_conference(self.field, self.conferences[0])]
        self.assertNotIn(self.groups[0][1].id, conf_ids)

    def test_still_exactly_two_qualifiers_from_that_conference(self) -> None:
        self.assertEqual(len(_for_conference(self.field, self.conferences[0])), 2)


# ===========================================================================
# 11. The no-Regional-playoff fallback, alongside DRAINED peers
# ===========================================================================


class TestWorldsQualifiersSmallConferenceFallback(TestCase):
    """§2.3 row 1 — a 3-Team Conference cannot field a bracket, so it sends its
    Standings RANK 1 with ``tier == QUALIFIER_TIER_CHAMPION`` and
    ``provenance == PROVENANCE_REGULAR_SEASON``. No Conference is ever
    unrepresented at Worlds."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _built_regional_season("WqTiny", [3, 5])
        self.tiny, self.normal = self.conferences
        # Only the 5-Team Conference could build a bracket at all.
        self.regional = self.phase.regional_tournaments.get(conference=self.normal)
        _stamp_bracket_completed(self.regional, self.groups[1][0])
        self.field = self.season.worlds_qualifiers()

    def test_fixture_precondition_the_tiny_conference_has_no_bracket(self) -> None:
        self.assertEqual(
            self.phase.regional_tournaments.filter(conference=self.tiny).count(), 0
        )
        # ...but the phase HAS been built, which is what arms the fallback.
        self.assertTrue(self.phase.regional_tournaments.exists())

    def test_the_field_is_complete_not_empty(self) -> None:
        # 1 from the 3-Team Conference + 2 from the 5-Team one.
        self.assertEqual(len(self.field), 3)

    def test_no_conference_is_missing_from_the_field(self) -> None:
        self.assertEqual(
            {q.conference_id for q in self.field},
            {self.tiny.id, self.normal.id},
        )

    def test_the_tiny_conference_sends_its_standings_rank_one(self) -> None:
        entries = _for_conference(self.field, self.tiny)
        self.assertEqual([q.team_id for q in entries], [self.groups[0][0].id])

    def test_the_fallback_slot_is_tier_one_with_regular_season_provenance(self) -> None:
        entry = _for_conference(self.field, self.tiny)[0]
        self.assertEqual(entry.tier, QUALIFIER_TIER_CHAMPION)
        self.assertEqual(entry.provenance, PROVENANCE_REGULAR_SEASON)
        self.assertEqual(entry.provenance_label, "Regular season")

    def test_the_fallback_seeds_among_the_champions(self) -> None:
        # Both tier-1 slots come before the tier-2 slot.
        self.assertEqual([q.tier for q in self.field], [1, 1, 2])

    def test_the_normal_conference_still_sends_two(self) -> None:
        self.assertEqual(len(_for_conference(self.field, self.normal)), 2)


# ===========================================================================
# 12. PREMATURE-FIELD REGRESSION GUARD — do not omit
# ===========================================================================


class TestWorldsQualifiersPrematureFieldGuard(TestCase):
    """**The guard the whole three-branch tier-1 rule exists for (§5.5 step 4
    branch 2).**

    A ``>= 2``-Conference Season in which EVERY Conference has 5-8 Teams never
    reaches a tier-3 slot, so nothing ELSE in the derivation would return
    ``[]``. With the cursor still on the round-robin phase and the tournament
    phase NOT built, a bare ``regional is None`` fallback would emit a
    complete, plausible-looking Worlds field mid-regular-season, before a
    single playoff Match had been played — and the Worlds panel would render
    it. Without this test that bug is invisible.
    """

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf_season("WqPremature", [5, 6])
        # Deliberately NOT played and NOT activated: the regular season is
        # still running.

    def test_fixture_precondition_the_tournament_phase_is_unbuilt(self) -> None:
        self.assertFalse(self.phase.regional_tournaments.exists())
        self.assertIsNone(self.phase.tournament_id)
        self.assertEqual(self.season.current_phase().id, self.rr_phase.id)

    def test_fixture_precondition_no_conference_reaches_a_tier_three_slot(self) -> None:
        for conference in self.conferences:
            size = len(conference.starting_team_ids_json or [])
            self.assertLess(qualifier_count_for_size(size), 3)

    def test_worlds_qualifiers_is_empty_mid_regular_season(self) -> None:
        self.assertEqual(self.season.worlds_qualifiers(), [])

    def test_the_worlds_panel_is_absent_from_the_playoffs_screen(self) -> None:
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.season.league_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["worlds_qualifiers"], [])
        self.assertNotContains(response, 'id="league-playoffs-worlds"')

    def test_still_empty_after_the_round_robin_but_before_activation(self) -> None:
        # The RR is finished but nothing has built the brackets yet — still
        # "not started", not "complete".
        _hand_play_rr(
            self.season,
            self.rr_phase,
            _ids(self.groups[0]) + _ids(self.groups[1]),
        )
        self.season.refresh_from_db()
        self.assertFalse(self.phase.regional_tournaments.exists())
        self.assertEqual(self.season.worlds_qualifiers(), [])


# ===========================================================================
# 13. Readiness is all-or-nothing
# ===========================================================================


class TestWorldsQualifiersReadinessIsAllOrNothing(TestCase):
    """§5.5's locked rule — ``[]`` the moment ANY required bracket of the final
    tournament phase is missing its champion. Never a partial field."""

    def test_one_drained_bracket_and_one_undrained_returns_empty(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "WqHalf", [5, 5]
        )
        first = phase.regional_tournaments.get(conference=conferences[0])
        _stamp_bracket_completed(first, groups[0][0])
        self.assertEqual(season.worlds_qualifiers(), [])

    def test_both_drained_returns_the_full_field(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "WqBoth", [5, 5]
        )
        for conference, group in zip(conferences, groups):
            _stamp_bracket_completed(
                phase.regional_tournaments.get(conference=conference), group[0]
            )
        self.assertEqual(len(season.worlds_qualifiers()), 4)

    def test_an_uncrowned_last_chance_bracket_returns_empty(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "WqLcPending", [9, 5]
        )
        for conference, group in zip(conferences, groups):
            _stamp_bracket_completed(
                phase.regional_tournaments.filter(conference=conference)
                .exclude(qualifier_stage="last_chance")
                .first(),
                group[0],
            )
        # Every REGIONAL playoff has a champion, but the 9-Team Conference's
        # Last-chance bracket has not crowned one.
        self.assertEqual(season.worlds_qualifiers(), [])

    def test_crowning_the_last_chance_bracket_completes_the_field(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "WqLcDone", [9, 5]
        )
        for conference, group in zip(conferences, groups):
            _stamp_bracket_completed(
                phase.regional_tournaments.filter(conference=conference)
                .exclude(qualifier_stage="last_chance")
                .first(),
                group[0],
            )
        # Rank 1 won the bracket ⇒ rank 2 takes tier 2 ⇒ the Last-chance field
        # starts at rank 3. Crown rank 3.
        _stamp_last_chance_champion(phase, conferences[0], groups[0][2])

        field = season.worlds_qualifiers()
        # 3 from the 9-Team Conference + 2 from the 5-Team one.
        self.assertEqual(len(field), 5)
        tier3 = _by_tier(field, QUALIFIER_TIER_LAST_CHANCE)
        self.assertEqual([q.team_id for q in tier3], [groups[0][2].id])
        self.assertEqual(tier3[0].provenance, PROVENANCE_LAST_CHANCE)
        self.assertEqual(tier3[0].seed, 5, "tier 3 seeds last")

    def test_a_missing_last_chance_row_entirely_returns_empty(self) -> None:
        """A legacy Season with no Last-chance row for a 9-Team Conference
        returns ``[]`` forever (§5.5's accepted ADR-0004 consequence)."""
        season, conferences, groups, _rr, phase = _built_regional_season(
            "WqLcGone", [9, 5]
        )
        phase.regional_tournaments.filter(qualifier_stage="last_chance").delete()
        for conference, group in zip(conferences, groups):
            _stamp_bracket_completed(
                phase.regional_tournaments.get(conference=conference), group[0]
            )
        self.assertEqual(season.worlds_qualifiers(), [])


# ===========================================================================
# 14. 0 and 1 Conference ⇒ []
# ===========================================================================


class TestWorldsQualifiersDegenerateSeasons(TestCase):
    """A Season with 0 or 1 Conference has no Worlds — its season-ending
    playoff crowns the Season champion directly (contract §5.5 step 1)."""

    def test_zero_conference_season_returns_empty(self) -> None:
        season, teams, rr_phase, phase = _flat_season("WqZero")
        _hand_play_rr(season, rr_phase, _ids(teams))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        self.assertEqual(season.worlds_qualifiers(), [])

    def test_zero_conference_season_still_returns_empty_once_drained(self) -> None:
        season, teams, rr_phase, phase = _flat_season("WqZeroDone")
        _hand_play_rr(season, rr_phase, _ids(teams))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        _stamp_bracket_completed(phase.tournament, teams[0])
        season.complete_if_finished()
        season.refresh_from_db()

        # The Season champion IS stamped — and there is still no Worlds field.
        self.assertEqual(season.champion_team_id, teams[0].id)
        self.assertEqual(season.worlds_qualifiers(), [])

    def test_one_conference_season_returns_empty(self) -> None:
        season, conferences, groups, rr_phase, phase = _conf_season("WqOne", [5])
        _hand_play_rr(season, rr_phase, _ids(groups[0]))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        self.assertEqual(season.worlds_qualifiers(), [])

    def test_one_conference_season_still_returns_empty_once_drained(self) -> None:
        season, conferences, groups, rr_phase, phase = _conf_season("WqOneDone", [5])
        _hand_play_rr(season, rr_phase, _ids(groups[0]))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        _stamp_bracket_completed(phase.tournament, groups[0][0])
        season.complete_if_finished()
        season.refresh_from_db()

        self.assertEqual(season.champion_team_id, groups[0][0].id)
        self.assertEqual(season.worlds_qualifiers(), [])

    def test_a_season_with_no_tournament_phase_returns_empty(self) -> None:
        season, conferences, groups, rr_phase, phase = _conf_season("WqNoT", [5, 5])
        phase.delete()
        season.refresh_from_db()
        self.assertEqual(season.worlds_qualifiers(), [])


# ===========================================================================
# 15. Nothing is persisted
# ===========================================================================


class TestWorldsQualifiersPersistsNothing(TestCase):
    """The derivation is on-demand: no qualifier table, no bracket, no
    champion stamp (contract §1 / invariant 4)."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _built_regional_season("WqPersist", [5, 5])
        for conference, group in zip(self.conferences, self.groups):
            _stamp_bracket_completed(
                self.phase.regional_tournaments.get(conference=conference), group[0]
            )

    def test_no_rows_are_created(self) -> None:
        before = (
            Tournament.objects.count(),
            TournamentParticipant.objects.count(),
            BracketNode.objects.count(),
        )
        field = self.season.worlds_qualifiers()
        self.assertEqual(len(field), 4)
        after = (
            Tournament.objects.count(),
            TournamentParticipant.objects.count(),
            BracketNode.objects.count(),
        )
        self.assertEqual(before, after)

    def test_repeated_calls_are_stable_and_still_create_nothing(self) -> None:
        before = Tournament.objects.count()
        first = self.season.worlds_qualifiers()
        second = self.season.worlds_qualifiers()
        self.assertEqual(
            [(q.seed, q.team_id, q.tier) for q in first],
            [(q.seed, q.team_id, q.tier) for q in second],
        )
        self.assertEqual(Tournament.objects.count(), before)

    def test_season_champion_team_stays_null(self) -> None:
        # This slice crowns NOTHING for a >= 2-Conference Season (CONF-04 does).
        self.season.worlds_qualifiers()
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertIsNone(self.season.champion_team_id)
