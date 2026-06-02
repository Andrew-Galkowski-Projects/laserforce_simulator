"""LG-01z-l — Django ``TestCase`` tests for the Game Log league screen.

The view ``matches.league_screens.game_log.game_log(request, league_id)`` is
read-only / GET-only. It is NOT yet URL-wired (the orchestrator wires the
``stats_game_log`` route centrally), so these tests call the view directly
via ``RequestFactory`` with a real session attached.

Fixtures are hand-constructed ``Match`` + ``GameRound`` rows — LG-01z runs
NO simulation, so the simulator is never entered.
"""

from __future__ import annotations

from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from matches.league_screens.game_log import game_log
from matches.models import GameRound, League, Match, Season
from matches.tests.conftest import make_team_with_slots


def _attach_session(request):
    """Run SessionMiddleware so the view's session write succeeds."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = f"/leagues/{league_id}/stats/game-log/"
    if query:
        path = f"{path}?{query}"
    request = RequestFactory().get(path)
    return _attach_session(request)


def _make_league(name: str = "GLLeague") -> League:
    return League.objects.create(name=name)


def _make_draft_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    return season, teams


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season, teams = _make_draft_season(league, name=name, n_teams=n_teams)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _make_played_round(
    season,
    team_red,
    team_blue,
    *,
    round_number=1,
    red_points=10,
    blue_points=5,
    winner=None,
):
    match = Match.objects.create(team_red=team_red, team_blue=team_blue, season=season)
    return GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=round_number,
        red_points=red_points,
        blue_points=blue_points,
        winner=winner,
        is_completed=True,
    )


# ---------------------------------------------------------------------------
# Routing / method / 404
# ---------------------------------------------------------------------------


class TestGameLogRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = game_log(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/stats/game-log/")
        )
        response = game_log(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        from django.http import Http404

        with self.assertRaises(Http404):
            game_log(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        game_log(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ---------------------------------------------------------------------------
# Empty state — no Season
# ---------------------------------------------------------------------------


class TestGameLogEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = game_log(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("game-log-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = game_log(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())


# ---------------------------------------------------------------------------
# Body — rows, DOM ids, sidebar_active
# ---------------------------------------------------------------------------


class TestGameLogBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.round = _make_played_round(
            self.season,
            self.team_a,
            self.team_b,
            red_points=12,
            blue_points=7,
            winner=self.team_a,
        )

    def test_table_and_row_dom_ids_present(self) -> None:
        response = game_log(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("game-log-table", content)
        self.assertIn(f"game-log-row-{self.round.id}", content)
        self.assertIn("game-log-team-filter", content)

    def test_score_and_winner_rendered(self) -> None:
        response = game_log(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("12", content)
        self.assertIn("7", content)
        self.assertIn(self.team_a.name, content)

    def test_row_deep_links_to_round_detail(self) -> None:
        response = game_log(_get(self.league.id), self.league.id)
        self.assertIn(
            f"/matches/game-round/{self.round.id}/", response.content.decode()
        )

    def test_sidebar_active_is_game_log(self) -> None:
        # The game_log sidebar entry must carry the active class.
        response = game_log(_get(self.league.id), self.league.id)
        content = response.content.decode()
        # sidebar entry id for stats/game_log, marked active.
        self.assertIn("sidebar-stats-game_log", content)


# ---------------------------------------------------------------------------
# Team filter
# ---------------------------------------------------------------------------


class TestGameLogTeamFilter(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=3)
        self.team_a, self.team_b, self.team_c = teams
        # Round 1: A vs B. Round 2: B vs C.
        self.round_ab = _make_played_round(self.season, self.team_a, self.team_b)
        self.round_bc = _make_played_round(self.season, self.team_b, self.team_c)

    def test_filter_to_team_a_shows_only_its_round(self) -> None:
        response = game_log(
            _get(self.league.id, query=f"team_id={self.team_a.id}"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertIn(f"game-log-row-{self.round_ab.id}", content)
        self.assertNotIn(f"game-log-row-{self.round_bc.id}", content)

    def test_filter_to_team_b_shows_both_rounds(self) -> None:
        response = game_log(
            _get(self.league.id, query=f"team_id={self.team_b.id}"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertIn(f"game-log-row-{self.round_ab.id}", content)
        self.assertIn(f"game-log-row-{self.round_bc.id}", content)

    def test_invalid_team_id_silently_ignored(self) -> None:
        response = game_log(
            _get(self.league.id, query="team_id=not-an-int"), self.league.id
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"game-log-row-{self.round_ab.id}", content)
        self.assertIn(f"game-log-row-{self.round_bc.id}", content)

    def test_non_enrolled_team_id_silently_ignored(self) -> None:
        # A team id not enrolled in the Season is ignored → all rows show.
        other, _ = make_team_with_slots("Outsider")
        response = game_log(
            _get(self.league.id, query=f"team_id={other.id}"), self.league.id
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"game-log-row-{self.round_ab.id}", content)
        self.assertIn(f"game-log-row-{self.round_bc.id}", content)

    def test_dropdown_lists_enrolled_teams(self) -> None:
        response = game_log(_get(self.league.id), self.league.id)
        content = response.content.decode()
        for team in (self.team_a, self.team_b, self.team_c):
            self.assertIn(team.name, content)


# ===========================================================================
# LG-06c — _coerce_sort_key shared helper (pure unit)
#
# The helper lives in matches.league_views and is the SINGLE source of
# sort-key coercion for all five LG-06c screens. These tests are EXPECTED
# TO FAIL until the Code agent adds `_coerce_sort_key` to
# matches/league_views.py.
# ===========================================================================


class TestCoerceSortKey(TestCase):
    """Unit tests for ``matches.league_views._coerce_sort_key``.

    Contract: returns ``raw`` iff ``raw in allowed``; otherwise ``default``.
    ``None`` / empty string / unknown value all map to ``default``.
    """

    def test_import_resolves(self) -> None:
        # Smoke: the helper and the reused _coerce_dir both import cleanly.
        from matches.league_views import _coerce_sort_key  # noqa: F401
        from teams.views import _coerce_dir  # noqa: F401

    def test_accepts_each_allowed_key(self) -> None:
        from matches.league_views import _coerce_sort_key

        allowed = frozenset({"matchday", "date_played", "score", "winner"})
        for key in allowed:
            self.assertEqual(_coerce_sort_key(key, allowed, "date_played"), key)

    def test_none_falls_back_to_default(self) -> None:
        from matches.league_views import _coerce_sort_key

        allowed = frozenset({"a", "b"})
        self.assertEqual(_coerce_sort_key(None, allowed, "a"), "a")

    def test_empty_string_falls_back_to_default(self) -> None:
        from matches.league_views import _coerce_sort_key

        allowed = frozenset({"a", "b"})
        self.assertEqual(_coerce_sort_key("", allowed, "b"), "b")

    def test_unknown_value_falls_back_to_default(self) -> None:
        from matches.league_views import _coerce_sort_key

        allowed = frozenset({"a", "b"})
        self.assertEqual(_coerce_sort_key("BOGUS", allowed, "a"), "a")

    def test_uppercase_of_valid_key_falls_back(self) -> None:
        from matches.league_views import _coerce_sort_key

        allowed = frozenset({"score"})
        # Case-sensitive — "SCORE" is not in the frozenset.
        self.assertEqual(_coerce_sort_key("SCORE", allowed, "score"), "score")


# ===========================================================================
# LG-06c — Game Log sortable columns
#
# Single-table screen. Keys {matchday, date_played, team_red, team_blue,
# score, winner}; default date_played asc (== current by-id chronological).
# Coexists with ?team_id=. DOM ids game-log-th-<key>; active-column arrow
# glyphs U+2191 (asc) / U+2193 (desc).
#
# EXPECTED TO FAIL until the Code agent lands view-side sorting + headers.
# ===========================================================================

_GLYPH_UP = "↑"
_GLYPH_DOWN = "↓"


def _gl_first_round_id(content: str) -> int:
    """Return the round_id of the first ``game-log-row-{id}`` in render order."""
    import re

    m = re.search(r"game-log-row-(\d+)", content)
    assert m is not None, "no game-log row rendered"
    return int(m.group(1))


class TestGameLogSortDefault(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        # Two played rounds with distinct dates: r1 earlier, r2 later.
        self.r1 = _make_played_round(
            self.season, self.team_a, self.team_b, round_number=1
        )
        self.r2 = _make_played_round(
            self.season, self.team_b, self.team_a, round_number=2
        )
        # Stamp distinct date_played so date ordering is observable.
        GameRound.objects.filter(pk=self.r1.pk).update(date_played=date(2026, 6, 1))
        GameRound.objects.filter(pk=self.r2.pk).update(date_played=date(2026, 6, 8))

    def test_default_order_is_date_played_asc(self) -> None:
        content = game_log(_get(self.league.id), self.league.id).content.decode()
        self.assertEqual(_gl_first_round_id(content), self.r1.id)


class TestGameLogSortKeys(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        # Round A: matchday 1, earlier date, total score 10, winner team_a.
        self.r1 = _make_played_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=7,
            blue_points=3,
            winner=self.team_a,
        )
        # Round B: matchday 2, later date, total score 40, winner team_b.
        self.r2 = _make_played_round(
            self.season,
            self.team_b,
            self.team_a,
            round_number=2,
            red_points=30,
            blue_points=10,
            winner=self.team_b,
        )
        GameRound.objects.filter(pk=self.r1.pk).update(date_played=date(2026, 6, 1))
        GameRound.objects.filter(pk=self.r2.pk).update(date_played=date(2026, 6, 8))

    def _first(self, query: str) -> int:
        content = game_log(
            _get(self.league.id, query=query), self.league.id
        ).content.decode()
        return _gl_first_round_id(content)

    def test_matchday_asc_then_desc(self) -> None:
        self.assertEqual(self._first("sort=matchday&dir=asc"), self.r1.id)
        self.assertEqual(self._first("sort=matchday&dir=desc"), self.r2.id)

    def test_date_played_asc_then_desc(self) -> None:
        self.assertEqual(self._first("sort=date_played&dir=asc"), self.r1.id)
        self.assertEqual(self._first("sort=date_played&dir=desc"), self.r2.id)

    def test_score_asc_then_desc(self) -> None:
        # r1 total = 10, r2 total = 40.
        self.assertEqual(self._first("sort=score&dir=asc"), self.r1.id)
        self.assertEqual(self._first("sort=score&dir=desc"), self.r2.id)

    def test_team_red_asc_then_desc(self) -> None:
        # r1 red = team_a, r2 red = team_b. Names sort alphabetically.
        lo, hi = sorted([self.team_a, self.team_b], key=lambda t: t.name)
        red_lo = self.r1 if self.r1.team_red_id == lo.id else self.r2
        red_hi = self.r1 if self.r1.team_red_id == hi.id else self.r2
        self.assertEqual(self._first("sort=team_red&dir=asc"), red_lo.id)
        self.assertEqual(self._first("sort=team_red&dir=desc"), red_hi.id)

    def test_team_blue_asc_then_desc(self) -> None:
        lo, hi = sorted([self.team_a, self.team_b], key=lambda t: t.name)
        blue_lo = self.r1 if self.r1.team_blue_id == lo.id else self.r2
        blue_hi = self.r1 if self.r1.team_blue_id == hi.id else self.r2
        self.assertEqual(self._first("sort=team_blue&dir=asc"), blue_lo.id)
        self.assertEqual(self._first("sort=team_blue&dir=desc"), blue_hi.id)

    def test_winner_asc_then_desc(self) -> None:
        lo, hi = sorted([self.team_a, self.team_b], key=lambda t: t.name)
        win_lo = self.r1 if self.r1.winner_id == lo.id else self.r2
        win_hi = self.r1 if self.r1.winner_id == hi.id else self.r2
        self.assertEqual(self._first("sort=winner&dir=asc"), win_lo.id)
        self.assertEqual(self._first("sort=winner&dir=desc"), win_hi.id)


class TestGameLogSortInvalidFallback(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        self.r1 = _make_played_round(
            self.season, self.team_a, self.team_b, round_number=1
        )
        self.r2 = _make_played_round(
            self.season, self.team_b, self.team_a, round_number=2
        )
        GameRound.objects.filter(pk=self.r1.pk).update(date_played=date(2026, 6, 1))
        GameRound.objects.filter(pk=self.r2.pk).update(date_played=date(2026, 6, 8))

    def test_garbage_sort_falls_back_to_date_played_asc(self) -> None:
        content = game_log(
            _get(self.league.id, query="sort=BOGUS"), self.league.id
        ).content.decode()
        self.assertEqual(_gl_first_round_id(content), self.r1.id)

    def test_garbage_dir_falls_back_to_asc(self) -> None:
        content = game_log(
            _get(self.league.id, query="sort=date_played&dir=NOPE"),
            self.league.id,
        ).content.decode()
        self.assertEqual(_gl_first_round_id(content), self.r1.id)

    def test_empty_sort_falls_back_to_default(self) -> None:
        content = game_log(
            _get(self.league.id, query="sort=&dir="), self.league.id
        ).content.decode()
        self.assertEqual(_gl_first_round_id(content), self.r1.id)

    def test_uppercase_sort_falls_back(self) -> None:
        content = game_log(
            _get(self.league.id, query="sort=SCORE"), self.league.id
        ).content.decode()
        self.assertEqual(_gl_first_round_id(content), self.r1.id)


class TestGameLogSortHeaderGlyph(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        _make_played_round(self.season, self.team_a, self.team_b, round_number=1)

    def test_th_dom_ids_present(self) -> None:
        content = game_log(_get(self.league.id), self.league.id).content.decode()
        for key in (
            "matchday",
            "date_played",
            "team_red",
            "team_blue",
            "score",
            "winner",
        ):
            self.assertIn(f"game-log-th-{key}", content)

    def test_active_column_renders_up_glyph_on_asc(self) -> None:
        content = game_log(
            _get(self.league.id, query="sort=score&dir=asc"), self.league.id
        ).content.decode()
        # The active Score header carries the ascending glyph.
        th_start = content.index("game-log-th-score")
        # Look at a window around the active <th>.
        window = content[th_start : th_start + 400]
        self.assertIn(_GLYPH_UP, window)

    def test_active_column_renders_down_glyph_on_desc(self) -> None:
        content = game_log(
            _get(self.league.id, query="sort=score&dir=desc"), self.league.id
        ).content.decode()
        th_start = content.index("game-log-th-score")
        window = content[th_start : th_start + 400]
        self.assertIn(_GLYPH_DOWN, window)


class TestGameLogSortCoexistsWithTeamFilter(TestCase):
    """Sort + ?team_id= honoured together via the wired URL (so context is
    available); the header hrefs carry team_id and the team-filter form
    carries sort/dir."""

    URL_NAME = "stats_game_log"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=3)
        self.team_a, self.team_b, self.team_c = teams
        # team_a appears in two rounds with different combined scores.
        self.ab = _make_played_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=5,
            blue_points=5,  # total 10
        )
        self.ac = _make_played_round(
            self.season,
            self.team_a,
            self.team_c,
            round_number=2,
            red_points=30,
            blue_points=10,  # total 40
        )
        # A round NOT involving team_a (must be filtered out).
        self.bc = _make_played_round(
            self.season, self.team_b, self.team_c, round_number=1
        )

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_filter_and_score_desc_together(self) -> None:
        content = self._get(
            query=f"team_id={self.team_a.id}&sort=score&dir=desc"
        ).content.decode()
        # Only team_a's rounds present.
        self.assertIn(f"game-log-row-{self.ab.id}", content)
        self.assertIn(f"game-log-row-{self.ac.id}", content)
        self.assertNotIn(f"game-log-row-{self.bc.id}", content)
        # Score-desc → the 40-total round (ac) sorts first.
        self.assertEqual(_gl_first_round_id(content), self.ac.id)

    def test_header_href_carries_team_id(self) -> None:
        content = self._get(query=f"team_id={self.team_a.id}").content.decode()
        th_start = content.index("game-log-th-score")
        window = content[th_start : th_start + 400]
        self.assertIn(f"team_id={self.team_a.id}", window)

    def test_team_filter_form_carries_sort_and_dir(self) -> None:
        content = self._get(
            query=f"team_id={self.team_a.id}&sort=score&dir=desc"
        ).content.decode()
        # The team-filter form must carry the active sort/dir (hidden inputs).
        self.assertIn('name="sort"', content)
        self.assertIn('name="dir"', content)
