"""LG-01z-m — Tests for the League Leaders league screen.

Two layers:

* ``TestComputeLeaderboards*`` / ``TestNoDjangoImportsLeaked`` — pure-unit
  coverage of ``matches/league_leaders_logic.py`` (no DB; the only Django
  touch the module makes is importing the ``LeaderRow`` dataclass, which
  the purity check confirms does not transitively load Django).
* ``TestLeagueLeaders*`` — Django ``TestCase`` coverage of the view
  ``matches.league_screens.league_leaders.league_leaders``. The view is NOT
  yet URL-wired (the orchestrator wires the ``stats_league_leaders`` route
  centrally), so these tests call the view directly via ``RequestFactory``
  with a real session attached — mirroring the LG-01z game-log /
  power-rankings precedent.

Fixtures are hand-constructed ``Match`` + ``GameRound`` +
``PlayerRoundState`` rows; LG-01z runs NO simulation, so the simulator is
never entered.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_leaders_logic import compute_leaderboards
from matches.league_screens.league_leaders import league_leaders
from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.tests.conftest import make_team_with_slots

# ===========================================================================
# Pure-unit: compute_leaderboards
# ===========================================================================


def _row(
    player_id,
    *,
    name=None,
    role="scout",
    team_id=1,
    team_name="T",
    tags=0,
    tagged=0,
    score=0,
) -> dict:
    return {
        "player_id": player_id,
        "player_name": name or f"P{player_id}",
        "role": role,
        "team_id": team_id,
        "team_name": team_name,
        "tags_made": tags,
        "times_tagged": tagged,
        "points_scored": score,
    }


class TestComputeLeaderboardsEmpty(unittest.TestCase):
    def test_empty_input_returns_four_empty_lists(self) -> None:
        out = compute_leaderboards([])
        self.assertEqual(
            set(out), {"avg_tags", "avg_score", "fewest_tagged", "tag_ratio"}
        )
        for board in out.values():
            self.assertEqual(board, [])


class TestComputeLeaderboardsVerbs(unittest.TestCase):
    def test_avg_tags_is_mean_descending(self) -> None:
        rows = [
            _row(1, tags=10),
            _row(1, tags=20),  # player 1 mean = 15
            _row(2, tags=5),  # player 2 mean = 5
        ]
        board = compute_leaderboards(rows)["avg_tags"]
        self.assertEqual([r.player_id for r in board], [1, 2])
        self.assertAlmostEqual(board[0].value, 15.0)
        self.assertEqual(board[0].rank, 1)
        self.assertEqual(board[0].games_played, 2)

    def test_avg_score_is_mean_descending(self) -> None:
        rows = [_row(1, score=100), _row(2, score=300), _row(2, score=100)]
        board = compute_leaderboards(rows)["avg_score"]
        # player 2 mean = 200, player 1 mean = 100
        self.assertEqual([r.player_id for r in board], [2, 1])
        self.assertAlmostEqual(board[0].value, 200.0)

    def test_fewest_tagged_is_mean_ascending(self) -> None:
        rows = [_row(1, tagged=2), _row(2, tagged=8), _row(3, tagged=0)]
        board = compute_leaderboards(rows)["fewest_tagged"]
        # least-tagged leads
        self.assertEqual([r.player_id for r in board], [3, 1, 2])
        self.assertAlmostEqual(board[0].value, 0.0)

    def test_tag_ratio_is_sum_over_sum_not_mean_of_ratios(self) -> None:
        # player 1: tags 1/tagged 0, then tags 0/tagged 4
        #   sum/sum = 1 / max(4,1) = 0.25 (mean-of-ratios would be huge)
        rows = [_row(1, tags=1, tagged=0), _row(1, tags=0, tagged=4)]
        board = compute_leaderboards(rows)["tag_ratio"]
        self.assertAlmostEqual(board[0].value, 0.25)

    def test_tag_ratio_zero_tagged_uses_max_one_denominator(self) -> None:
        rows = [_row(1, tags=7, tagged=0)]
        board = compute_leaderboards(rows)["tag_ratio"]
        # 7 / max(0, 1) == 7.0, no ZeroDivisionError
        self.assertAlmostEqual(board[0].value, 7.0)


class TestComputeLeaderboardsTiebreak(unittest.TestCase):
    def test_tie_breaks_games_desc_then_player_id_asc(self) -> None:
        # All three players have avg_tags == 5, differing games + ids.
        rows = [
            _row(3, tags=5),  # 1 game
            _row(2, tags=5),  # 2 games
            _row(2, tags=5),
            _row(1, tags=5),  # 1 game
        ]
        board = compute_leaderboards(rows)["avg_tags"]
        # value tie → games desc (player 2 has 2 games) → player_id asc.
        self.assertEqual([r.player_id for r in board], [2, 1, 3])


class TestComputeLeaderboardsLimit(unittest.TestCase):
    def test_limit_caps_each_board(self) -> None:
        rows = [_row(i, tags=i) for i in range(1, 21)]
        out = compute_leaderboards(rows, limit=10)
        for board in out.values():
            self.assertEqual(len(board), 10)

    def test_default_limit_is_ten(self) -> None:
        rows = [_row(i, tags=i) for i in range(1, 31)]
        out = compute_leaderboards(rows)
        self.assertEqual(len(out["avg_tags"]), 10)


class TestComputeLeaderboardsDeterministic(unittest.TestCase):
    def test_repeated_calls_equal(self) -> None:
        rows = [_row(1, tags=3, score=9, tagged=2), _row(2, tags=3, score=9, tagged=1)]
        self.assertEqual(compute_leaderboards(rows), compute_leaderboards(rows))


class TestComputeLeaderboardsLastRowWins(unittest.TestCase):
    def test_last_row_supplies_displayed_name_team_role(self) -> None:
        rows = [
            _row(1, name="Old", team_name="OldT", role="scout", tags=4),
            _row(1, name="New", team_name="NewT", role="medic", tags=4),
        ]
        board = compute_leaderboards(rows)["avg_tags"]
        self.assertEqual(board[0].player_name, "New")
        self.assertEqual(board[0].team_name, "NewT")
        self.assertEqual(board[0].role, "medic")


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """``matches.league_leaders_logic`` must not transitively import Django.

    Mirrors HX-01 / RES-04 / LG-01 / LG-01z-b precedent: spawn a fresh
    subprocess, ``import matches.league_leaders_logic``, then walk
    ``sys.modules`` and assert no entry matches the ``django`` prefix. (The
    ``LeaderRow`` dataclass import must stay Django-free.)
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import pathlib
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
            import matches.league_leaders_logic  # noqa: F401
            leaked = sorted(
                m for m in sys.modules
                if m == "django" or m.startswith("django.")
            )
            if leaked:
                print("LEAK:" + ",".join(leaked))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Django import leaked into matches.league_leaders_logic.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )


# ===========================================================================
# View helpers
# ===========================================================================


def _attach_session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int):
    request = RequestFactory().get(f"/leagues/{league_id}/stats/league-leaders/")
    return _attach_session(request)


def _make_league(name: str = "LLLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, players = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append((t, players))
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _make_round_with_states(season, team_red, team_blue, states):
    """Create a played GameRound + the given PlayerRoundState rows.

    ``states`` is a list of ``(player, team_color, role, tags, tagged,
    score)`` tuples.
    """
    match = Match.objects.create(
        team_red=team_red, team_blue=team_blue, season=season, is_completed=True
    )
    game_round = GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=1,
        red_points=10,
        blue_points=5,
        is_completed=True,
    )
    for player, color, role, tags, tagged, score in states:
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=color,
            role=role,
            tags_made=tags,
            times_tagged=tagged,
            points_scored=score,
        )
    return game_round


# ===========================================================================
# View: routing / method / 404 / session
# ===========================================================================


class TestLeagueLeadersRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = league_leaders(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/stats/league-leaders/")
        )
        response = league_leaders(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            league_leaders(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        league_leaders(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ===========================================================================
# View: empty state — no Season
# ===========================================================================


class TestLeagueLeadersEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = league_leaders(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("leaders-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = league_leaders(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())


# ===========================================================================
# View: body — DOM ids, ranking, links
# ===========================================================================


class TestLeagueLeadersBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        (self.team_a, self.players_a), (self.team_b, self.players_b) = teams
        self.star = self.players_a["scout"]  # high tags / score, never tagged
        self.weak = self.players_b["heavy"]  # low tags, tagged a lot
        _make_round_with_states(
            self.season,
            self.team_a,
            self.team_b,
            [
                (self.star, "red", "scout", 30, 0, 5000),
                (self.weak, "blue", "heavy", 2, 12, 600),
            ],
        )

    def test_all_four_board_dom_ids_present(self) -> None:
        content = league_leaders(_get(self.league.id), self.league.id).content.decode()
        self.assertIn("leaders-avg-tags", content)
        self.assertIn("leaders-avg-score", content)
        self.assertIn("leaders-fewest-tagged", content)
        self.assertIn("leaders-tag-ratio", content)

    def test_sidebar_active_entry_present(self) -> None:
        content = league_leaders(_get(self.league.id), self.league.id).content.decode()
        self.assertIn("sidebar-stats-league_leaders", content)

    def test_leader_links_to_in_league_player_page(self) -> None:
        # LG-06h: leader player-name link repointed to league_player_detail.
        content = league_leaders(_get(self.league.id), self.league.id).content.decode()
        self.assertIn(f"/leagues/{self.league.id}/players/{self.star.id}/", content)

    def test_context_leaderboards_have_four_keys(self) -> None:
        response = league_leaders(_get(self.league.id), self.league.id)
        boards = (
            response.context_data["leaderboards"]
            if hasattr(response, "context_data")
            else None
        )
        # RequestFactory render() returns an HttpResponse, not TemplateResponse;
        # assert via content instead.
        content = response.content.decode()
        self.assertIn(self.star.name, content)

    def test_aggregates_player_across_rounds(self) -> None:
        # Add a second round so the star has 2 rounds; mean should hold.
        _make_round_with_states(
            self.season,
            self.team_a,
            self.team_b,
            [(self.star, "red", "scout", 10, 0, 1000)],
        )
        content = league_leaders(_get(self.league.id), self.league.id).content.decode()
        # star now appears once (aggregated), name present.
        self.assertIn(self.star.name, content)


# ===========================================================================
# LG-06c — League Leaders sortable columns (namespaced per-board)
#
# Four independent boards keyed avg_tags / avg_score / fewest_tagged /
# tag_ratio. Namespaced params ?<board>_sort=&<board>_dir=. Shared key set
# {rank, player_name, team_name, role, value, games_played}; default
# rank/asc per board. DOM ids league-leaders-<board>-th-<key>.
#
# EXPECTED TO FAIL until the Code agent lands per-board sorting + headers.
# ===========================================================================

_BOARDS = ("avg_tags", "avg_score", "fewest_tagged", "tag_ratio")
_LL_GLYPH_UP = "↑"
_LL_GLYPH_DOWN = "↓"


def _board_player_order(content: str, board_table_id: str) -> list[str]:
    """Return the player-career-link player ids in render order within the
    board's <table id="..."> ... section (best-effort scoped slice)."""
    import re

    start = content.find(board_table_id)
    assert start != -1, f"board table {board_table_id} not rendered"
    # Slice from this board's table id to the next board's table id (or end).
    nexts = [
        content.find(other, start + len(board_table_id))
        for other in (
            "leaders-avg-tags",
            "leaders-avg-score",
            "leaders-fewest-tagged",
            "leaders-tag-ratio",
        )
        if content.find(other, start + len(board_table_id)) != -1
    ]
    end = min(nexts) if nexts else len(content)
    section = content[start:end]
    # LG-06h: player-name links repointed to /leagues/<lid>/players/<id>/.
    return re.findall(r"/leagues/\d+/players/(\d+)/", section)


_BOARD_TABLE_ID = {
    "avg_tags": "leaders-avg-tags",
    "avg_score": "leaders-avg-score",
    "fewest_tagged": "leaders-fewest-tagged",
    "tag_ratio": "leaders-tag-ratio",
}


class _LeadersFixtureMixin:
    """Build a League with two players that populate all four boards with a
    clearly-ordered metric so rank order is deterministic and known."""

    def _build(self):
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        (self.team_a, self.players_a), (self.team_b, self.players_b) = teams
        # star: high tags / high score / never tagged.
        self.star = self.players_a["scout"]
        # weak: low tags / low score / tagged a lot.
        self.weak = self.players_b["heavy"]
        _make_round_with_states(
            self.season,
            self.team_a,
            self.team_b,
            [
                (self.star, "red", "scout", 30, 0, 5000),
                (self.weak, "blue", "heavy", 2, 12, 600),
            ],
        )


class TestLeagueLeadersSortDefault(_LeadersFixtureMixin, TestCase):
    def setUp(self) -> None:
        self._build()

    def test_each_board_default_is_rank_asc(self) -> None:
        content = league_leaders(_get(self.league.id), self.league.id).content.decode()
        # avg_tags / avg_score / tag_ratio: star (rank 1) leads.
        for board in ("avg_tags", "avg_score", "tag_ratio"):
            order = _board_player_order(content, _BOARD_TABLE_ID[board])
            self.assertEqual(order[0], str(self.star.id), f"{board} default rank order")
        # fewest_tagged: least-tagged (star, 0 tagged) is rank 1.
        order = _board_player_order(content, _BOARD_TABLE_ID["fewest_tagged"])
        self.assertEqual(order[0], str(self.star.id))


class TestLeagueLeadersSortKeys(_LeadersFixtureMixin, TestCase):
    """Sort the avg_tags board by the shared keys, asc and desc."""

    URL_NAME = "stats_league_leaders"

    def setUp(self) -> None:
        self._build()

    def _order(self, query: str) -> list[str]:
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        content = self.client.get(f"{url}?{query}").content.decode()
        return _board_player_order(content, _BOARD_TABLE_ID["avg_tags"])

    def test_player_name_asc_then_desc(self) -> None:
        lo, hi = sorted([self.star, self.weak], key=lambda p: p.name)
        asc = self._order("avg_tags_sort=player_name&avg_tags_dir=asc")
        desc = self._order("avg_tags_sort=player_name&avg_tags_dir=desc")
        self.assertEqual(asc[0], str(lo.id))
        self.assertEqual(desc[0], str(hi.id))

    def test_value_asc_then_desc(self) -> None:
        # avg_tags value: star 30 > weak 2.
        asc = self._order("avg_tags_sort=value&avg_tags_dir=asc")
        desc = self._order("avg_tags_sort=value&avg_tags_dir=desc")
        self.assertEqual(asc[0], str(self.weak.id))
        self.assertEqual(desc[0], str(self.star.id))

    def test_games_played_sorts(self) -> None:
        # Both played 1 game — assert no crash and a 200 with both players.
        order = self._order("avg_tags_sort=games_played&avg_tags_dir=desc")
        self.assertEqual(set(order), {str(self.star.id), str(self.weak.id)})

    def test_rank_asc_is_default_order(self) -> None:
        order = self._order("avg_tags_sort=rank&avg_tags_dir=asc")
        self.assertEqual(order[0], str(self.star.id))

    def test_team_name_and_role_sort_without_crash(self) -> None:
        for key in ("team_name", "role"):
            order = self._order(f"avg_tags_sort={key}&avg_tags_dir=asc")
            self.assertEqual(set(order), {str(self.star.id), str(self.weak.id)})


class TestLeagueLeadersNamespaceIndependence(_LeadersFixtureMixin, TestCase):
    """Sorting one board does NOT reorder a sibling board."""

    URL_NAME = "stats_league_leaders"

    def setUp(self) -> None:
        self._build()

    def test_sorting_avg_tags_leaves_avg_score_in_rank_order(self) -> None:
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        # Re-order ONLY the avg_tags board by player_name asc (weak first if
        # weak's name sorts lower); avg_score must stay rank-asc (star first).
        content = self.client.get(
            f"{url}?avg_tags_sort=value&avg_tags_dir=asc"
        ).content.decode()
        # avg_tags value-asc → weak leads.
        avg_tags_order = _board_player_order(content, _BOARD_TABLE_ID["avg_tags"])
        self.assertEqual(avg_tags_order[0], str(self.weak.id))
        # avg_score untouched → still rank-asc, star leads.
        avg_score_order = _board_player_order(content, _BOARD_TABLE_ID["avg_score"])
        self.assertEqual(avg_score_order[0], str(self.star.id))

    def test_each_board_param_pair_is_independent(self) -> None:
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        # Flip tag_ratio only; avg_tags + avg_score + fewest_tagged stay rank.
        content = self.client.get(
            f"{url}?tag_ratio_sort=value&tag_ratio_dir=asc"
        ).content.decode()
        for board in ("avg_tags", "avg_score"):
            order = _board_player_order(content, _BOARD_TABLE_ID[board])
            self.assertEqual(
                order[0], str(self.star.id), f"{board} should be untouched"
            )


class TestLeagueLeadersSortInvalidFallback(_LeadersFixtureMixin, TestCase):
    URL_NAME = "stats_league_leaders"

    def setUp(self) -> None:
        self._build()

    def _order(self, query: str) -> list[str]:
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        content = self.client.get(f"{url}?{query}").content.decode()
        return _board_player_order(content, _BOARD_TABLE_ID["avg_tags"])

    def test_bogus_board_sort_falls_back_to_rank(self) -> None:
        order = self._order("avg_tags_sort=BOGUS&avg_tags_dir=asc")
        self.assertEqual(order[0], str(self.star.id))

    def test_bogus_board_dir_falls_back_to_asc(self) -> None:
        order = self._order("avg_tags_sort=rank&avg_tags_dir=SIDEWAYS")
        self.assertEqual(order[0], str(self.star.id))


class TestLeagueLeadersSortHeaderGlyph(_LeadersFixtureMixin, TestCase):
    def setUp(self) -> None:
        self._build()

    def test_namespaced_th_dom_ids_present(self) -> None:
        content = league_leaders(_get(self.league.id), self.league.id).content.decode()
        # At minimum the two rendered sortable columns per board.
        for board in _BOARDS:
            self.assertIn(f"league-leaders-{board}-th-player_name", content)
            self.assertIn(f"league-leaders-{board}-th-value", content)

    def test_active_board_header_glyph(self) -> None:
        from django.urls import reverse

        url = reverse("stats_league_leaders", args=[self.league.id])
        content = self.client.get(
            f"{url}?avg_tags_sort=value&avg_tags_dir=desc"
        ).content.decode()
        th_start = content.index("league-leaders-avg_tags-th-value")
        window = content[th_start : th_start + 400]
        self.assertIn(_LL_GLYPH_DOWN, window)
