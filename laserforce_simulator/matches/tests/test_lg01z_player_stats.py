"""LG-01z-o — tests for the Player Stats (performance) league screen.

Two layers:

1. **Pure-unit** (``matches.season_player_stats``) — aggregation /
   sum-vs-average split / sorting / forgiving coercers, plus the
   ``TestNoDjangoImportsLeaked`` purity seam (subprocess fresh-import +
   ``sys.modules`` walk, mirroring the HX-01 / HX-03 / RES-04 precedent).
2. **View** (``matches.league_screens.player_stats.player_stats``) — a
   Django ``TestCase`` exercising routing / 405 / 404 / session write /
   empty-state / DOM ids / scope / sort / pagination. The view is not yet
   URL-wired (the orchestrator wires ``stats_player_stats`` centrally), so
   tests call it directly via ``RequestFactory`` with a real session.

Fixtures hand-construct League / Season / Match / GameRound /
PlayerRoundState rows — LG-01z runs NO simulation, so the simulator is
never entered.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_screens.player_stats import player_stats
from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.season_player_stats import (
    AVERAGED_KEYS,
    DERIVED_KEYS,
    STAT_KEYS,
    SUMMED_KEYS,
    aggregate_player_stats,
    coerce_dir,
    coerce_sort,
    sort_player_stats,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Player

# ===========================================================================
# Pure-unit — aggregation
# ===========================================================================


def _round_dict(pid, name="P", tid=1, tname="T", role="scout", **stats) -> dict:
    """Build one round-dict with all 12 STAT_KEYS defaulted to 0."""
    base = {k: 0 for k in STAT_KEYS}
    base.update(stats)
    return {
        "player_id": pid,
        "player_name": name,
        "team_id": tid,
        "team_name": tname,
        "role": role,
        **base,
    }


class TestAggregateEmpty(TestCase):
    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(aggregate_player_stats([]), [])


class TestAggregateSumVsAverage(TestCase):
    def test_count_keys_are_summed(self) -> None:
        rounds = [
            _round_dict(1, points_scored=100, tags_made=5),
            _round_dict(1, points_scored=200, tags_made=7),
        ]
        rows = aggregate_player_stats(rounds)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stats["points_scored"], 300)
        self.assertEqual(rows[0].stats["tags_made"], 12)
        self.assertEqual(rows[0].games, 2)

    def test_mvp_and_accuracy_are_averaged(self) -> None:
        rounds = [
            _round_dict(1, mvp=10.0, accuracy=80.0),
            _round_dict(1, mvp=30.0, accuracy=100.0),
        ]
        rows = aggregate_player_stats(rounds)
        self.assertAlmostEqual(rows[0].stats["mvp"], 20.0)
        self.assertAlmostEqual(rows[0].stats["accuracy"], 90.0)

    def test_summed_and_averaged_key_sets_partition_stat_keys(self) -> None:
        # The two groups together cover exactly the 12 STAT_KEYS, disjoint.
        self.assertEqual(set(SUMMED_KEYS) | set(AVERAGED_KEYS), set(STAT_KEYS))
        self.assertEqual(set(SUMMED_KEYS) & set(AVERAGED_KEYS), set())

    def test_every_stat_key_present_on_row(self) -> None:
        rows = aggregate_player_stats([_round_dict(1)])
        for k in STAT_KEYS:
            self.assertIn(k, rows[0].stats)


class TestAggregateGrouping(TestCase):
    def test_distinct_players_each_get_a_row(self) -> None:
        rows = aggregate_player_stats(
            [_round_dict(1, name="Amy"), _round_dict(2, name="Bob")]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.player_id for r in rows}, {1, 2})

    def test_last_seen_identity_wins(self) -> None:
        rows = aggregate_player_stats(
            [
                _round_dict(1, name="Old", tname="OldTeam", role="scout"),
                _round_dict(1, name="New", tname="NewTeam", role="medic"),
            ]
        )
        self.assertEqual(rows[0].player_name, "New")
        self.assertEqual(rows[0].team_name, "NewTeam")
        self.assertEqual(rows[0].role, "medic")


class TestDerivedStats(TestCase):
    def test_derived_keys_are_not_in_stat_keys(self) -> None:
        # Derived keys are computed separately; they must not pollute the
        # 12-key SUMMED ∪ AVERAGED partition.
        self.assertEqual(set(DERIVED_KEYS) & set(STAT_KEYS), set())

    def test_tag_ratio_is_sum_over_sum(self) -> None:
        # 10 tags / 0 tagged then 0 tags / 4 tagged ⇒ 10 / max(4,1) = 2.5,
        # NOT the mean of per-round ratios (which would be (inf + 0)/2).
        rounds = [
            _round_dict(1, tags_made=10, times_tagged=0),
            _round_dict(1, tags_made=0, times_tagged=4),
        ]
        rows = aggregate_player_stats(rounds)
        self.assertAlmostEqual(rows[0].stats["tag_ratio"], 2.5)

    def test_tag_ratio_zero_tagged_clamps_denominator(self) -> None:
        rows = aggregate_player_stats([_round_dict(1, tags_made=7, times_tagged=0)])
        self.assertAlmostEqual(rows[0].stats["tag_ratio"], 7.0)

    def test_survival_is_mean_of_per_round_seconds(self) -> None:
        rounds = [
            _round_dict(1, survival_seconds=900.0),
            _round_dict(1, survival_seconds=300.0),
        ]
        rows = aggregate_player_stats(rounds)
        self.assertAlmostEqual(rows[0].stats["survival"], 600.0)

    def test_survival_defaults_to_zero_when_absent(self) -> None:
        # Round dicts without survival_seconds (defensive) contribute 0.
        rows = aggregate_player_stats([_round_dict(1)])
        self.assertAlmostEqual(rows[0].stats["survival"], 0.0)


# ===========================================================================
# Pure-unit — coercers + sorting
# ===========================================================================


class TestCoercers(TestCase):
    def test_coerce_sort_accepts_every_stat_key(self) -> None:
        for k in STAT_KEYS:
            self.assertEqual(coerce_sort(k), k)

    def test_coerce_sort_accepts_name_team_games(self) -> None:
        self.assertEqual(coerce_sort("name"), "name")
        self.assertEqual(coerce_sort("team"), "team")
        self.assertEqual(coerce_sort("games"), "games")

    def test_coerce_sort_accepts_derived_keys(self) -> None:
        for k in DERIVED_KEYS:
            self.assertEqual(coerce_sort(k), k)

    def test_coerce_sort_falls_back(self) -> None:
        self.assertEqual(coerce_sort("BOGUS"), "points_scored")
        self.assertEqual(coerce_sort(None), "points_scored")
        self.assertEqual(coerce_sort(""), "points_scored")

    def test_coerce_dir_accepts_asc_desc(self) -> None:
        self.assertEqual(coerce_dir("asc"), "asc")
        self.assertEqual(coerce_dir("desc"), "desc")

    def test_coerce_dir_falls_back_default_desc(self) -> None:
        self.assertEqual(coerce_dir("SIDEWAYS"), "desc")
        self.assertEqual(coerce_dir(None), "desc")
        self.assertEqual(coerce_dir("ASC"), "desc")  # case-sensitive


class TestSorting(TestCase):
    def _rows(self):
        return aggregate_player_stats(
            [
                _round_dict(1, name="Zed", tname="Bravo", points_scored=50, mvp=10.0),
                _round_dict(2, name="Amy", tname="Alpha", points_scored=90, mvp=30.0),
                _round_dict(3, name="Bob", tname="Alpha", points_scored=70, mvp=20.0),
            ]
        )

    def test_sort_by_points_desc(self) -> None:
        rows = sort_player_stats(self._rows(), "points_scored", "desc")
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])

    def test_sort_by_points_asc(self) -> None:
        rows = sort_player_stats(self._rows(), "points_scored", "asc")
        self.assertEqual([r.player_name for r in rows], ["Zed", "Bob", "Amy"])

    def test_sort_by_mvp_desc_averaged_key(self) -> None:
        rows = sort_player_stats(self._rows(), "mvp", "desc")
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])

    def test_sort_by_name_asc(self) -> None:
        rows = sort_player_stats(self._rows(), "name", "asc")
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])

    def test_sort_by_team_asc_name_tiebreak(self) -> None:
        rows = sort_player_stats(self._rows(), "team", "asc")
        # Alpha team players first (Amy then Bob), then Bravo (Zed).
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])

    def test_unknown_sort_falls_back_to_points(self) -> None:
        rows = sort_player_stats(self._rows(), "nonsense", "desc")
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])


# ===========================================================================
# Pure-unit — purity seam
# ===========================================================================


class TestNoDjangoImportsLeaked(TestCase):
    def test_no_django_imported_by_pure_module(self) -> None:
        code = (
            "import sys; import matches.season_player_stats; "
            "leaked = [m for m in sys.modules if m == 'django' "
            "or m.startswith('django.')]; "
            "print('LEAKED' if leaked else 'CLEAN')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        )
        self.assertIn("CLEAN", result.stdout, msg=result.stderr)


# ===========================================================================
# View helpers
# ===========================================================================


def _attach_session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = f"/leagues/{league_id}/stats/player-stats/"
    if query:
        path = f"{path}?{query}"
    return _attach_session(RequestFactory().get(path))


def _make_league(name: str = "PSLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _make_round_with_states(season, team_red, team_blue, states):
    """Persist a Match + GameRound under ``season`` and the given PRS rows.

    ``states`` is a list of (player, team_color, stat_kwargs) tuples.
    Returns the GameRound.
    """
    match = Match.objects.create(
        team_red=team_red, team_blue=team_blue, season=season, is_completed=True
    )
    game_round = GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        red_points=100,
        blue_points=80,
        is_completed=True,
    )
    for player, color, kwargs in states:
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=color,
            role=kwargs.pop("role", "scout"),
            **kwargs,
        )
    return game_round


# ===========================================================================
# View — routing / 405 / 404 / session
# ===========================================================================


class TestPlayerStatsRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = player_stats(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/stats/player-stats/")
        )
        response = player_stats(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            player_stats(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        player_stats(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ===========================================================================
# View — empty state
# ===========================================================================


class TestPlayerStatsEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = player_stats(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("player-stats-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = player_stats(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())

    def test_active_season_no_rounds_renders_empty_notice(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = player_stats(_get(league.id), league.id)
        self.assertIn("player-stats-empty-notice", response.content.decode())


# ===========================================================================
# View — body / DOM ids / scope
# ===========================================================================


class TestPlayerStatsBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.red, self.blue = self.teams
        self.red_player = self.red.active_players[0]
        self.blue_player = self.blue.active_players[0]
        _make_round_with_states(
            self.season,
            self.red,
            self.blue,
            [
                (self.red_player, "red", {"points_scored": 500, "tags_made": 12}),
                (self.blue_player, "blue", {"points_scored": 300, "tags_made": 8}),
            ],
        )

    def test_table_dom_id_present(self) -> None:
        response = player_stats(_get(self.league.id), self.league.id)
        self.assertIn("player-stats-table", response.content.decode())

    def test_sortable_header_dom_ids_present(self) -> None:
        response = player_stats(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("player-stats-th-points_scored", content)
        self.assertIn("player-stats-th-mvp", content)
        self.assertIn("player-stats-th-accuracy", content)
        self.assertIn("player-stats-th-name", content)

    def test_tag_ratio_and_survival_columns_present(self) -> None:
        response = player_stats(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("player-stats-th-tag_ratio", content)
        self.assertIn("player-stats-th-survival", content)
        self.assertIn("Tag Ratio", content)
        self.assertIn("Survival", content)

    def test_sidebar_active_is_player_stats(self) -> None:
        response = player_stats(_get(self.league.id), self.league.id)
        self.assertIn("sidebar-stats-player_stats", response.content.decode())

    def test_enrolled_player_appears_with_career_link(self) -> None:
        response = player_stats(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn(self.red_player.name, content)
        self.assertIn(f"/players/{self.red_player.id}/stats/", content)

    def test_player_with_no_rounds_excluded(self) -> None:
        # A player on an enrolled team but with zero PlayerRoundState rows
        # does not appear (this screen is performance-scoped, not roster).
        bench = Player.objects.create(team=self.red, name="Benched Ghost")
        response = player_stats(_get(self.league.id), self.league.id)
        self.assertNotIn("Benched Ghost", response.content.decode())
        self.assertIsNotNone(bench.id)


# ===========================================================================
# View — sort + pagination
# ===========================================================================


class TestPlayerStatsSorting(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.red, self.blue = self.teams
        # Two enrolled players with distinct point totals.
        self.hi = self.red.active_players[0]
        self.lo = self.blue.active_players[0]
        _make_round_with_states(
            self.season,
            self.red,
            self.blue,
            [
                (self.hi, "red", {"points_scored": 900}),
                (self.lo, "blue", {"points_scored": 100}),
            ],
        )

    def test_default_sort_points_desc_high_scorer_first(self) -> None:
        response = player_stats(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertLess(content.index(self.hi.name), content.index(self.lo.name))

    def test_sort_points_asc_low_scorer_first(self) -> None:
        response = player_stats(
            _get(self.league.id, query="sort=points_scored&dir=asc"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertLess(content.index(self.lo.name), content.index(self.hi.name))

    def test_invalid_sort_falls_back_returns_200(self) -> None:
        response = player_stats(
            _get(self.league.id, query="sort=BOGUS&dir=SIDEWAYS"), self.league.id
        )
        self.assertEqual(response.status_code, 200)


class TestPlayerStatsPagination(TestCase):
    def test_per_page_paginates_and_carries_querystring(self) -> None:
        league = _make_league()
        season, teams = _make_active_season(league, n_teams=2)
        red, blue = teams
        # 15 distinct players each with one PRS row → 15 aggregated rows.
        states = []
        for i in range(15):
            p = Player.objects.create(team=red, name=f"Extra{i:02d}")
            states.append((p, "red", {"points_scored": i}))
        _make_round_with_states(season, red, blue, states)
        response = player_stats(_get(league.id, query="per_page=10&page=2"), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("player-stats-pagination", content)
        self.assertIn("per_page=10", content)


# ===========================================================================
# View — LG-06a page-size <select> selector
# ===========================================================================


class TestPlayerStatsPerPageSelector(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.red, self.blue = self.teams
        # One scoring player so the body (+ per-page form) renders.
        _make_round_with_states(
            self.season,
            self.red,
            self.blue,
            [(self.red.active_players[0], "red", {"points_scored": 100})],
        )

    def test_per_page_select_dom_id_present(self) -> None:
        response = player_stats(_get(self.league.id), self.league.id)
        self.assertIn("player-stats-per-page-select", response.content.decode())

    def test_selected_option_reflects_requested_per_page(self) -> None:
        response = player_stats(
            _get(self.league.id, query="per_page=25"), self.league.id
        )
        self.assertIn('value="25" selected', response.content.decode())

    def test_per_page_form_carries_hidden_sort_and_dir(self) -> None:
        response = player_stats(
            _get(self.league.id, query="sort=points_scored&dir=asc&per_page=25"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertIn('name="sort"', content)
        self.assertIn('name="dir"', content)
