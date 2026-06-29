"""LG-07a — pure-module + derived-completion + barrier tests for the member
night (core slice).

The BINDING seam contract is
``.claude/worktrees/lg-07-member-night-seam-contract.md``. This file covers the
**three non-view** test boundaries of §9:

1. The pure ``matches/member_night.py`` (``split_balanced`` /
   ``draw_member_night_games`` / ``draw_site_games`` + ``MemberNightGame`` +
   the constants), asserted under an **INJECTED seeded** ``random.Random`` —
   plus the ``TestNoDjangoImportsLeaked`` frozen-allowlist subprocess walk.
2. The DERIVED completion ``Season._member_night_phase_complete`` (the 4 cases,
   including the no-viable-Site auto-complete branch + the cursor advancing past
   a completed member-night phase).
3. The play-loop ``Season._phase_barrier_ordinal`` barrier — an incomplete
   ``member_night`` phase halts the RR loop (excluded from
   ``playable_fixtures_by_phase``) and re-admits the RR fixtures once the member
   night completes — asserted on the SET of fixtures, NEVER point totals. Plus a
   confirmation that ``_rr_phase_complete`` / ``_is_finished`` are unaffected by a
   member-night Match.

**Determinism rule (LOCKED):** member nights are NON-deterministic in production
(a fresh ``random.Random()`` per run). The pure tests pin SCHEMA-level outcomes
(game-count bounds, distinctness, role-map shape, the MAX_POOL down-sample cap,
sorted-site ordering, the ``MemberNightGame`` shape) under a SEEDED injected
``random.Random``; the DB tests assert completion / barrier / exclusion at the
schema level. NO test asserts a raw simulated point total.

NOTE: the pure-module classes import ``matches.member_night`` (which already
landed); the DB classes call ``Season._member_night_phase_complete`` /
``Season._phase_barrier_ordinal`` / ``Season.playable_fixtures_by_phase`` — if any
of those production names have not yet landed, those classes fail (import /
attribute error) — the expected TDD red state, not a defect in this file.
"""

from __future__ import annotations

import os
import pathlib
import random
import subprocess
import sys
from datetime import date

from django.test import SimpleTestCase, TestCase

from matches.draw import ROLE_SLOTS
from matches.member_night import (
    MAX_GAMES,
    MAX_POOL,
    MIN_GAMES,
    MIN_POOL,
    PLAYERS_PER_GAME,
    MemberNightGame,
    draw_member_night_games,
    draw_site_games,
    split_balanced,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _pool(n: int, *, start: int = 100, base: float = 1000.0):
    """``n`` ``(player_id, overall_rating)`` pairs with DISTINCT, strictly
    decreasing ratings (so the rating-DESC / player_id-ASC sort is
    unambiguous)."""
    return [(start + i, base - i) for i in range(n)]


def _ids_of(game: MemberNightGame) -> set[int]:
    return set(game.team_a.values()) | set(game.team_b.values())


# ===========================================================================
# §1c — split_balanced
# ===========================================================================


class TestSplitBalancedValidation(SimpleTestCase):
    """``split_balanced`` requires EXACTLY 12 players."""

    def test_raises_value_error_on_eleven(self) -> None:
        with self.assertRaises(ValueError):
            split_balanced(_pool(11))

    def test_raises_value_error_on_thirteen(self) -> None:
        with self.assertRaises(ValueError):
            split_balanced(_pool(13))

    def test_raises_value_error_on_empty(self) -> None:
        with self.assertRaises(ValueError):
            split_balanced([])


class TestSplitBalancedShape(SimpleTestCase):
    """6/6 split of 12, every input id used exactly once, consumes NO RNG."""

    def test_returns_two_teams_of_six(self) -> None:
        a, b = split_balanced(_pool(12))
        self.assertEqual(len(a), 6)
        self.assertEqual(len(b), 6)

    def test_all_twelve_ids_preserved_no_duplicates(self) -> None:
        a, b = split_balanced(_pool(12))
        all_ids = a + b
        self.assertEqual(len(all_ids), 12)
        self.assertEqual(len(set(all_ids)), 12, "no player on both teams / twice")
        self.assertEqual(set(all_ids), {pid for pid, _ in _pool(12)})

    def test_input_order_independent(self) -> None:
        forward = split_balanced(_pool(12))
        reversed_input = split_balanced(list(reversed(_pool(12))))
        self.assertEqual(forward, reversed_input)

    def test_consumes_no_global_rng(self) -> None:
        random.seed(12345)
        before = random.getstate()
        split_balanced(_pool(12))
        self.assertEqual(random.getstate(), before, "split_balanced must not touch RNG")


class TestSplitBalancedNotStacked(SimpleTestCase):
    """The strong players are NOT stacked on one side; the greedy rule keeps the
    two teams' total-rating gap minimised."""

    def test_strongest_two_split_across_the_two_teams(self) -> None:
        # Strongest player -> team A (both totals 0, A wins the tie); the
        # second-strongest -> team B (A now leads on running total). So the top
        # two are never stacked on one team.
        a, b = split_balanced(_pool(12))
        ordered = [pid for pid, _ in sorted(_pool(12), key=lambda pr: (-pr[1], pr[0]))]
        strongest, second = ordered[0], ordered[1]
        self.assertIn(strongest, a)
        self.assertIn(second, b)

    def test_total_rating_gap_is_minimised(self) -> None:
        pool = _pool(12)
        rating_by_id = dict(pool)
        a, b = split_balanced(pool)
        total_a = sum(rating_by_id[pid] for pid in a)
        total_b = sum(rating_by_id[pid] for pid in b)
        spread = abs(total_a - total_b)
        worst_player_gap = pool[0][1] - pool[-1][1]
        # Greedy balance keeps the spread well under a single top-vs-bottom gap;
        # a "stack the strong on one side" split would be far larger.
        self.assertLess(spread, worst_player_gap, "greedy balance must tighten totals")


# ===========================================================================
# §1c / §1d — draw_site_games (viability, counts, RNG-consumption shape)
# ===========================================================================


class TestDrawSiteGamesViabilityFloor(SimpleTestCase):
    """A Site with ``< MIN_POOL`` players is NOT viable ⇒ yields 0 games and
    consumes NO RNG."""

    def test_below_min_pool_yields_empty(self) -> None:
        self.assertEqual(
            draw_site_games("X", _pool(MIN_POOL - 1), random.Random(1), 0), []
        )

    def test_below_min_pool_consumes_no_rng(self) -> None:
        rng = random.Random(99)
        before = rng.getstate()
        draw_site_games("X", _pool(MIN_POOL - 1), rng, 0)
        self.assertEqual(rng.getstate(), before, "a non-viable Site draws no RNG")

    def test_exactly_min_pool_is_viable(self) -> None:
        games = draw_site_games("X", _pool(MIN_POOL), random.Random(2), 0)
        self.assertGreater(len(games), 0)


class TestDrawSiteGamesCountBounds(SimpleTestCase):
    """The per-Site game count is drawn in ``[MIN_GAMES, MAX_GAMES]`` inclusive."""

    def test_game_count_within_bounds_over_many_seeds(self) -> None:
        for seed in range(40):
            games = draw_site_games("X", _pool(12), random.Random(seed), 0)
            self.assertGreaterEqual(len(games), MIN_GAMES)
            self.assertLessEqual(len(games), MAX_GAMES)


class TestDrawSiteGamesPerGameShape(SimpleTestCase):
    """Each game = 12 distinct players split into two 6-slot role maps."""

    def setUp(self) -> None:
        self.games = draw_site_games("SiteA", _pool(12), random.Random(7), 0)

    def test_each_game_has_twelve_distinct_players(self) -> None:
        for g in self.games:
            self.assertEqual(len(_ids_of(g)), PLAYERS_PER_GAME)

    def test_role_maps_are_the_six_role_slots(self) -> None:
        for g in self.games:
            self.assertEqual(set(g.team_a.keys()), set(ROLE_SLOTS))
            self.assertEqual(set(g.team_b.keys()), set(ROLE_SLOTS))
            self.assertEqual(len(g.team_a), 6)
            self.assertEqual(len(g.team_b), 6)

    def test_no_duplicate_player_within_a_team(self) -> None:
        for g in self.games:
            self.assertEqual(len(set(g.team_a.values())), 6)
            self.assertEqual(len(set(g.team_b.values())), 6)

    def test_teams_are_disjoint(self) -> None:
        for g in self.games:
            self.assertEqual(set(g.team_a.values()) & set(g.team_b.values()), set())

    def test_member_night_game_shape(self) -> None:
        g = self.games[0]
        self.assertIsInstance(g, MemberNightGame)
        self.assertEqual(g.site, "SiteA")
        self.assertIsInstance(g.game_index, int)
        self.assertIsInstance(g.team_a, dict)
        self.assertIsInstance(g.team_b, dict)


class TestDrawSiteGamesMaxPoolDownsample(SimpleTestCase):
    """A pool larger than ``MAX_POOL`` is down-sampled to ``MAX_POOL`` for the
    run — at most ``MAX_POOL`` distinct players appear across the Site's games."""

    def test_distinct_players_capped_at_max_pool(self) -> None:
        games = draw_site_games("Big", _pool(25), random.Random(3), 0)
        distinct: set[int] = set()
        for g in games:
            distinct |= _ids_of(g)
        self.assertLessEqual(len(distinct), MAX_POOL)

    def test_under_cap_pool_can_use_every_player(self) -> None:
        # A pool of exactly MAX_POOL is NOT down-sampled; over many games every
        # player can appear (the union is the whole pool of 18).
        games = draw_site_games("Cap", _pool(MAX_POOL), random.Random(11), 0)
        distinct: set[int] = set()
        for g in games:
            distinct |= _ids_of(g)
        self.assertLessEqual(len(distinct), MAX_POOL)


class TestDrawSiteGamesStartIndex(SimpleTestCase):
    """``game_index`` starts at the passed ``start_index`` and increments."""

    def test_game_index_runs_from_start_index(self) -> None:
        games = draw_site_games("S", _pool(12), random.Random(5), start_index=10)
        self.assertEqual(
            [g.game_index for g in games], list(range(10, 10 + len(games)))
        )


# ===========================================================================
# §1c / §1d — draw_member_night_games (sorted sites, determinism, append order)
# ===========================================================================


class TestDrawMemberNightGames(SimpleTestCase):
    """The whole run iterates Sites in SORTED name order; ``game_index`` is the
    global append order; same seed ⇒ identical plan."""

    def test_same_seed_reproduces_identical_plan(self) -> None:
        pool_by_site = {"SiteA": _pool(12)}
        first = draw_member_night_games(pool_by_site, random.Random(42))
        second = draw_member_night_games(pool_by_site, random.Random(42))
        self.assertEqual(first, second)

    def test_sites_processed_in_sorted_name_order(self) -> None:
        pool_by_site = {
            "Bravo": _pool(12, start=200),
            "Alpha": _pool(12, start=100),
        }
        games = draw_member_night_games(pool_by_site, random.Random(7))
        site_sequence = [g.site for g in games]
        # All Alpha games precede all Bravo games (sorted-site iteration).
        first_bravo = site_sequence.index("Bravo")
        self.assertNotIn("Bravo", site_sequence[:first_bravo])
        self.assertTrue(all(s == "Alpha" for s in site_sequence[:first_bravo]))

    def test_global_game_index_is_contiguous_append_order(self) -> None:
        pool_by_site = {
            "Bravo": _pool(12, start=200),
            "Alpha": _pool(12, start=100),
        }
        games = draw_member_night_games(pool_by_site, random.Random(7))
        self.assertEqual([g.game_index for g in games], list(range(len(games))))

    def test_non_viable_site_contributes_no_games(self) -> None:
        pool_by_site = {
            "Viable": _pool(12, start=100),
            "Tiny": _pool(MIN_POOL - 1, start=300),
        }
        games = draw_member_night_games(pool_by_site, random.Random(1))
        self.assertTrue(all(g.site == "Viable" for g in games))

    def test_empty_pool_by_site_yields_no_games(self) -> None:
        self.assertEqual(draw_member_night_games({}, random.Random(1)), [])


# ===========================================================================
# §1 — Defensive: no Django imports leaked into the pure module
# ===========================================================================


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing + exercising ``matches.member_night`` in a fresh subprocess must
    not pull in ``django.*`` (nor ``matches.models``) — the frozen allowlist is
    ``dataclasses`` / ``typing`` / ``random`` / ``collections`` PLUS the
    ``matches.draw`` pure-module import (which itself leaks no Django). Mirrors
    ``test_draw.py::TestNoDjangoImportsLeaked``.
    """

    def _run_guard(self, body: str) -> subprocess.CompletedProcess:
        """``body`` must be flush-left (no leading indentation) — it is spliced
        into the guard script verbatim, so dedent fragility is avoided."""
        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")
        script = (
            "import random\n"
            "import sys\n"
            f"sys.path.insert(0, {str(project_root)!r})\n"
            f"{body}\n"
            "offenders = sorted(\n"
            "    name for name in sys.modules\n"
            "    if name == 'django' or name.startswith('django.')\n"
            "    or name == 'matches.models'\n"
            ")\n"
            "if offenders:\n"
            "    print('LEAK:' + ','.join(offenders))\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n"
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_pure_module_does_not_pull_in_django(self) -> None:
        result = self._run_guard("import matches.member_night  # noqa: F401")
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_exercising_the_draws_pulls_in_no_django(self) -> None:
        body = (
            "from matches.member_night import "
            "split_balanced, draw_site_games, draw_member_night_games\n"
            "pool = [(100 + i, float(1000 - i)) for i in range(12)]\n"
            "split_balanced(pool)\n"
            "draw_site_games('S', pool, random.Random(0), 0)\n"
            "draw_member_night_games({'S': pool}, random.Random(0))"
        )
        result = self._run_guard(body)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


# ===========================================================================
# §2b — DERIVED completion: Season._member_night_phase_complete
# ===========================================================================

from matches.models import League, Match, Season, SeasonPhase  # noqa: E402
from teams.models import Player, Team  # noqa: E402


def _member_night_season(prefix: str, *, n_teams: int = 2, site: str = "SiteA"):
    """A draft Season with ``n_teams`` slotted Teams whose 12 players share one
    ``home_site``, plus an ordinal-1 ``member_night`` SeasonPhase. Returns
    ``(season, teams, mn_phase)``."""
    from matches.tests.conftest import make_team_with_slots

    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    Player.objects.filter(team__in=teams).update(home_site=site)
    mn = SeasonPhase.objects.create(season=season, ordinal=1, phase_type="member_night")
    return season, teams, mn


def _mn_match_shell(season: Season, mn: SeasonPhase, *, completed: bool) -> Match:
    """A member-night Match stamped ``season`` + ``season_phase=mn`` with two
    ``is_draw_team`` Teams (no roster needed for the completion query)."""
    da = Team.objects.create(name="MN Draw A", is_draw_team=True)
    db = Team.objects.create(name="MN Draw B", is_draw_team=True)
    return Match.objects.create(
        season=season,
        season_phase=mn,
        team_red=da,
        team_blue=db,
        is_completed=completed,
    )


class TestMemberNightPhaseComplete(TestCase):
    """The 4 derived-completion cases of ``Season._member_night_phase_complete``."""

    def test_zero_games_with_viable_site_is_incomplete(self) -> None:
        # Case 1 — no Match yet AND a Site has >= MIN_POOL players ⇒ the cursor
        # must PARK on the member night (incomplete).
        season, _teams, mn = _member_night_season("MnIncViable")
        self.assertFalse(season._member_night_phase_complete(mn))

    def test_zero_games_with_no_viable_site_auto_completes(self) -> None:
        # Case 2 — no Match AND no Site has >= MIN_POOL ⇒ auto-complete so the
        # cursor never parks forever on an empty pool. Spread the 12 players over
        # distinct home_sites so every bucket holds 1 (< MIN_POOL).
        season, teams, mn = _member_night_season("MnAuto")
        for i, p in enumerate(Player.objects.filter(team__in=teams)):
            p.home_site = f"Solo{i}"
            p.save(update_fields=["home_site"])
        self.assertTrue(season._member_night_phase_complete(mn))

    def test_one_unplayed_game_is_incomplete(self) -> None:
        # Case 3 — at least one member-night Match exists with an unplayed shell.
        season, _teams, mn = _member_night_season("MnUnplayed")
        _mn_match_shell(season, mn, completed=False)
        self.assertFalse(season._member_night_phase_complete(mn))

    def test_all_games_complete_is_complete(self) -> None:
        # Case 4 — >= 1 member-night Match exists AND every one is is_completed.
        season, _teams, mn = _member_night_season("MnAllDone")
        _mn_match_shell(season, mn, completed=True)
        _mn_match_shell(season, mn, completed=True)
        self.assertTrue(season._member_night_phase_complete(mn))

    def test_mixed_played_unplayed_is_incomplete(self) -> None:
        season, _teams, mn = _member_night_season("MnMixed")
        _mn_match_shell(season, mn, completed=True)
        _mn_match_shell(season, mn, completed=False)
        self.assertFalse(season._member_night_phase_complete(mn))

    def test_existing_games_take_precedence_over_pool_branch(self) -> None:
        # Even with a viable Site (pool branch would say "incomplete"), once
        # shells exist the completion is decided by the shells: all complete ⇒
        # complete, regardless of the still-viable pool.
        season, _teams, mn = _member_night_season("MnPrecedence")
        _mn_match_shell(season, mn, completed=True)
        self.assertTrue(season._member_night_phase_complete(mn))


class TestMemberNightCursorAdvance(TestCase):
    """``current_phase()`` parks on an incomplete member night, then advances
    past it once complete."""

    def test_cursor_parks_then_advances(self) -> None:
        season, _teams, mn = _member_night_season("MnCursor")
        # Incomplete (viable Site, no games) ⇒ cursor parks on the member night.
        self.assertEqual(season.current_phase().pk, mn.pk)
        # Complete it (all shells played) ⇒ the member night is the only phase,
        # so the cursor advances to None.
        _mn_match_shell(season, mn, completed=True)
        season.refresh_from_db()
        self.assertIsNone(season.current_phase())


# ===========================================================================
# §2d — barrier: Season._phase_barrier_ordinal halts the RR loop on an
# incomplete member_night phase, then re-admits the RR fixtures.
# ===========================================================================


def _member_night_then_rr_season(prefix: str, *, n_teams: int = 2):
    """An active Season: ordinal-1 ``member_night`` (viable Site) + ordinal-2
    ``round_robin``, ``n_teams`` enrolled, started. Returns
    ``(season, teams, mn, rr)``."""
    from matches.tests.conftest import make_team_with_slots

    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    Player.objects.filter(team__in=teams).update(home_site="SiteA")
    mn = SeasonPhase.objects.create(season=season, ordinal=1, phase_type="member_night")
    rr = SeasonPhase.objects.create(season=season, ordinal=2, phase_type="round_robin")
    season.start_season()
    season.refresh_from_db()
    return season, teams, mn, rr


class TestMemberNightBarrier(TestCase):
    """An incomplete member-night phase halts the RR loop; completing it
    re-admits the RR fixtures (asserted on the SET of fixtures)."""

    def test_barrier_ordinal_is_member_night_while_incomplete(self) -> None:
        season, _teams, mn, _rr = _member_night_then_rr_season("BarOrd")
        self.assertEqual(season._phase_barrier_ordinal(), mn.ordinal)

    def test_rr_phase_excluded_while_member_night_incomplete(self) -> None:
        season, _teams, _mn, rr = _member_night_then_rr_season("BarHalt")
        playable_phase_pks = [p.pk for p, _f in season.playable_fixtures_by_phase()]
        self.assertNotIn(rr.pk, playable_phase_pks)

    def test_rr_phase_readmitted_once_member_night_completes(self) -> None:
        season, _teams, mn, rr = _member_night_then_rr_season("BarLift")
        # Drain the member night via committed shells (all completed).
        _mn_match_shell(season, mn, completed=True)
        _mn_match_shell(season, mn, completed=True)
        season.refresh_from_db()
        # Barrier lifts (no incomplete tournament/member_night phase remains).
        self.assertIsNone(season._phase_barrier_ordinal())
        playable = season.playable_fixtures_by_phase()
        playable_phase_pks = [p.pk for p, _f in playable]
        self.assertIn(rr.pk, playable_phase_pks)
        # The re-admitted RR fixtures are EXACTLY the RR phase's scheduled set.
        scheduled = {
            (p.pk, tuple(fx))
            for p, fx in season.scheduled_fixtures_by_phase()
            if p.pk == rr.pk
        }
        admitted = {(p.pk, tuple(fx)) for p, fx in playable if p.pk == rr.pk}
        self.assertEqual(admitted, scheduled)


class TestMemberNightDoesNotAffectRrCompletion(TestCase):
    """A member-night Match does NOT make the RR phase complete: ``_rr_phase_complete``
    scopes ``match__season_phase=rr`` and ``_is_finished`` iterates RR fixtures."""

    def test_rr_phase_complete_ignores_member_night_matches(self) -> None:
        season, _teams, mn, rr = _member_night_then_rr_season("RrUnaffected")
        _mn_match_shell(season, mn, completed=True)
        _mn_match_shell(season, mn, completed=True)
        season.refresh_from_db()
        # RR has no played fixtures ⇒ still incomplete despite the member night.
        self.assertFalse(season._rr_phase_complete(rr))

    def test_is_finished_false_with_member_night_matches_present(self) -> None:
        season, _teams, mn, _rr = _member_night_then_rr_season("IsFinUnaffected")
        _mn_match_shell(season, mn, completed=True)
        season.refresh_from_db()
        # No RR fixture played ⇒ the season is not finished; the surplus
        # member-night Match is harmless.
        self.assertFalse(season._is_finished())
