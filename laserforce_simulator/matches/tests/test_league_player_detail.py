"""LG-06h — tests for the League player page (per-Player, league-pinned detail).

The League player page (``matches.league_screens.player_detail.player_detail``)
is a read-only, GET-only page at ``/leagues/<league_id>/players/<player_id>/`` —
the in-League destination of every player-name link on the 8 LG-06f league
screens. It renders a header (watch flag + an EXTERNAL link to the global HX-01
career page), a Regular-Season stats table (per-Season rows + a league-wide
Career row, built view-side by reusing existing modules), a "Potential"
placeholder, and 5 inline "coming soon" stub blocks.

Tests assert the §9 seam (public behaviour) ONLY — routing / 405 / 404 /
session write / lenient empty-state / RS rows when Rounds exist / per-Season
team derived from Rounds / watch flag + script-once / external career link /
Potential placeholder / 5 stubs / sidebar with zero active entries. The
per-Season aggregation loop INTERNALS are covered by the existing
``season_player_stats`` / ``player_stats`` tests — here we assert the RENDERED
rows.

Fixtures hand-construct League / Season / Team / Match / GameRound /
PlayerRoundState rows with the real ORM — LG-06h runs NO simulation, so the
simulator is never entered. The view is wired via the URL name
``league_player_detail``; tests use the Django test ``Client`` (via
``reverse``) so the routing + session-cookie + context-processor wiring is
exercised end-to-end, plus a couple of direct-call checks for 405 / 404.

Written test-first against the LG-06h seam contract; these FAIL until the Code
agent lands the view + URL + template.
"""

from __future__ import annotations

import re
from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "DetailLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    """Create + activate a Season with ``n_teams`` fully-slotted teams."""
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}{name}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _make_round_with_states(season, team_red, team_blue, states):
    """Persist a Match + GameRound under ``season`` plus the given PRS rows.

    ``states`` is a list of ``(player, team_color, stat_kwargs)`` tuples.
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


def _detail_path(league_id: int, player_id: int) -> str:
    return f"/leagues/{league_id}/players/{player_id}/"


# ===========================================================================
# Routing / method / 404 / template
# ===========================================================================


class TestPlayerDetailRouting(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league)
        self.player = self.teams[0].active_players[0]

    def test_reverse_resolves_to_locked_path(self) -> None:
        url = reverse("league_player_detail", args=[self.league.id, self.player.id])
        self.assertEqual(url, _detail_path(self.league.id, self.player.id))

    def test_get_returns_200(self) -> None:
        url = reverse("league_player_detail", args=[self.league.id, self.player.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_uses_player_detail_template(self) -> None:
        url = reverse("league_player_detail", args=[self.league.id, self.player.id])
        response = self.client.get(url)
        self.assertTemplateUsed(response, "leagues/player_detail.html")

    def test_post_returns_405(self) -> None:
        url = reverse("league_player_detail", args=[self.league.id, self.player.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 405)

    def test_missing_league_id_returns_404(self) -> None:
        url = reverse("league_player_detail", args=[999999, self.player.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_missing_player_id_returns_404(self) -> None:
        url = reverse("league_player_detail", args=[self.league.id, 999999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# Session write
# ===========================================================================


class TestPlayerDetailSessionWrite(TestCase):
    def test_get_writes_last_league_id(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        player = teams[0].active_players[0]
        url = reverse("league_player_detail", args=[league.id, player.id])
        self.client.get(url)
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_last_league_id_is_int(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        player = teams[0].active_players[0]
        url = reverse("league_player_detail", args=[league.id, player.id])
        self.client.get(url)
        self.assertIsInstance(self.client.session.get("last_league_id"), int)


# ===========================================================================
# Lenient empty-state — a Player with NO Rounds in this League
# ===========================================================================


class TestPlayerDetailEmptyState(TestCase):
    """A current free agent / a player whose only Rounds are in another League
    still renders 200 with the header, Potential, and all 5 stubs, plus the
    empty-state notice in place of the RS table."""

    def setUp(self) -> None:
        self.league = _make_league()
        # No Season at all in this League — the player has zero Rounds here.
        pool = Team.objects.create(name=f"{self.league.name} Free Agents")
        self.player = Player.objects.create(team=pool, name="Free Agent Joe")
        self.url = reverse(
            "league_player_detail", args=[self.league.id, self.player.id]
        )

    def test_returns_200(self) -> None:
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_empty_notice_present(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertIn("league-player-rs-stats-empty", content)

    def test_rs_stats_table_absent(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertNotIn("league-player-rs-stats-table", content)

    def test_header_still_renders(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertIn("league-player-header", content)
        self.assertIn(self.player.name, content)

    def test_potential_still_renders(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertIn("league-player-potential", content)

    def test_all_five_stubs_still_render(self) -> None:
        content = self.client.get(self.url).content.decode()
        for stub in (
            "league-player-playoffs-stub",
            "league-player-ratings-history-stub",
            "league-player-awards-stub",
            "league-player-salaries-stub",
            "league-player-transactions-stub",
        ):
            self.assertIn(stub, content)

    def test_player_with_rounds_only_in_other_league_is_empty_here(self) -> None:
        # The player physically played Rounds, but in a DIFFERENT League.
        other = _make_league("OtherDetailLeague")
        season, teams = _make_active_season(other)
        team_a, team_b = teams
        player = team_a.active_players[0]
        _make_round_with_states(
            season, team_a, team_b, [(player, "red", {"points_scored": 50})]
        )
        url = reverse("league_player_detail", args=[self.league.id, player.id])
        content = self.client.get(url).content.decode()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn("league-player-rs-stats-empty", content)
        self.assertNotIn("league-player-rs-stats-table", content)


# ===========================================================================
# RS rows present when Rounds exist
# ===========================================================================


class TestPlayerDetailRsRows(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, name="S1")
        self.team_a, self.team_b = self.teams
        self.player = self.team_a.active_players[0]
        _make_round_with_states(
            self.season,
            self.team_a,
            self.team_b,
            [(self.player, "red", {"points_scored": 500, "tags_made": 12})],
        )
        self.url = reverse(
            "league_player_detail", args=[self.league.id, self.player.id]
        )

    def test_rs_stats_table_present(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertIn("league-player-rs-stats-table", content)

    def test_empty_notice_absent(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertNotIn("league-player-rs-stats-empty", content)

    def test_career_row_present(self) -> None:
        response = self.client.get(self.url)
        career_row = response.context["career_row"]
        self.assertIsNotNone(career_row)
        self.assertEqual(career_row["year"], "Career")

    def test_one_rs_row_per_season_with_rounds(self) -> None:
        # One per-Season row for the single Season the player has Rounds in.
        response = self.client.get(self.url)
        rs_rows = response.context["rs_rows"]
        self.assertEqual(len(rs_rows), 1)
        self.assertEqual(rs_rows[0]["year"], self.season.name)

    def test_two_seasons_yield_two_rs_rows(self) -> None:
        # Start a second Season in the same League and play the player again.
        season2, teams2 = _make_active_season(self.league, name="S2")
        # Re-use the same player (he can play across Seasons via PRS history).
        ta2, tb2 = teams2
        _make_round_with_states(
            season2, ta2, tb2, [(self.player, "red", {"points_scored": 200})]
        )
        response = self.client.get(self.url)
        rs_rows = response.context["rs_rows"]
        years = {row["year"] for row in rs_rows}
        self.assertEqual(years, {self.season.name, season2.name})

    def test_season_with_no_rounds_has_no_row(self) -> None:
        # A second Season exists but the player never played in it ⇒ no row.
        season2, _teams2 = _make_active_season(self.league, name="S2")
        response = self.client.get(self.url)
        rs_rows = response.context["rs_rows"]
        years = {row["year"] for row in rs_rows}
        self.assertNotIn(season2.name, years)


# ===========================================================================
# Per-Season Team derived from Rounds (NOT current Player.team)
# ===========================================================================


class TestPlayerDetailTeamDerivedFromRounds(TestCase):
    def test_season_row_team_is_team_actually_played_for(self) -> None:
        league = _make_league()
        season, teams = _make_active_season(league, name="S1")
        team_a, team_b = teams
        # The player physically played for team_a in Season 1...
        player = team_a.active_players[0]
        _make_round_with_states(
            season, team_a, team_b, [(player, "red", {"points_scored": 300})]
        )
        # ...but his CURRENT team is moved to a free-agent pool (so current
        # Player.team is NOT team_a).
        pool = Team.objects.create(name=f"{league.name} Free Agents")
        player.team = pool
        player.save(update_fields=["team"])

        url = reverse("league_player_detail", args=[league.id, player.id])
        response = self.client.get(url)
        rs_rows = response.context["rs_rows"]
        season_row = next(r for r in rs_rows if r["year"] == season.name)
        # The Season-1 row's team cell shows team_a (the team he played for),
        # not the current pool team.
        self.assertEqual(season_row["team_name"], team_a.name)
        content = response.content.decode()
        self.assertIn(team_a.name, content)


# ===========================================================================
# Watch flag rendered + script once
# ===========================================================================


class TestPlayerDetailWatchFlag(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league)
        self.player = self.teams[0].active_players[0]
        self.url = reverse(
            "league_player_detail", args=[self.league.id, self.player.id]
        )

    def test_watch_flag_button_present_with_player_id(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertIn("watch-flag", content)
        self.assertIn(f'data-player-id="{self.player.id}"', content)

    def test_watch_flag_script_bound_exactly_once(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertEqual(content.count("__lfWatchFlagBound"), 2)
        # ``__lfWatchFlagBound`` appears twice in the single once-bound script
        # (the guard read + the guard set); the load-bearing fact is that the
        # script partial is included EXACTLY ONCE — assert the delegated
        # handler binds a single click listener.
        self.assertEqual(content.count('addEventListener("click"'), 1)


# ===========================================================================
# External career link present (the global HX-01 page)
# ===========================================================================


class TestPlayerDetailExternalCareerLink(TestCase):
    def test_external_career_href_present(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        player = teams[0].active_players[0]
        url = reverse("league_player_detail", args=[league.id, player.id])
        content = self.client.get(url).content.decode()
        # The header carries an EXTERNAL link to the global career page.
        self.assertIn(f"/players/{player.id}/stats/", content)


# ===========================================================================
# Potential placeholder
# ===========================================================================


class TestPlayerDetailPotential(TestCase):
    def test_potential_block_contains_em_dash(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        player = teams[0].active_players[0]
        url = reverse("league_player_detail", args=[league.id, player.id])
        content = self.client.get(url).content.decode()
        # The Potential block renders the em-dash (U+2014) placeholder — either
        # as the literal character or as an HTML entity (Django may emit the
        # non-ASCII char as a numeric / named entity).
        block = _extract_dom_block(content, "league-player-potential")
        self.assertTrue(
            ("—" in block) or ("&#8212;" in block) or ("&mdash;" in block),
            msg=f"em-dash placeholder not found in Potential block: {block!r}",
        )


# ===========================================================================
# All 5 stubs present + "Coming soon"
# ===========================================================================


class TestPlayerDetailStubs(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league)
        self.player = self.teams[0].active_players[0]
        self.url = reverse(
            "league_player_detail", args=[self.league.id, self.player.id]
        )

    def test_all_five_stub_dom_ids_present(self) -> None:
        content = self.client.get(self.url).content.decode()
        for stub in (
            "league-player-playoffs-stub",
            "league-player-ratings-history-stub",
            "league-player-awards-stub",
            "league-player-salaries-stub",
            "league-player-transactions-stub",
        ):
            self.assertIn(stub, content)

    def test_each_stub_contains_coming_soon(self) -> None:
        content = self.client.get(self.url).content.decode().lower()
        # 5 stubs each carry the case-insensitive "coming soon" substring.
        self.assertGreaterEqual(content.count("coming soon"), 5)


# ===========================================================================
# Sidebar rendered with zero active entries
# ===========================================================================


class TestPlayerDetailSidebar(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league)
        self.player = self.teams[0].active_players[0]
        self.url = reverse(
            "league_player_detail", args=[self.league.id, self.player.id]
        )

    def test_sidebar_rendered(self) -> None:
        content = self.client.get(self.url).content.decode()
        self.assertIn("league-sidebar", content)

    def test_sidebar_active_is_none(self) -> None:
        response = self.client.get(self.url)
        self.assertIsNone(response.context["sidebar_active"])

    def test_no_sidebar_entry_is_active(self) -> None:
        response = self.client.get(self.url)
        sidebar_links = response.context["sidebar_links"]
        self.assertTrue(sidebar_links)  # the 23-entry sidebar is non-empty
        self.assertEqual([e for e in sidebar_links if e["active"]], [])


# ---------------------------------------------------------------------------
# Small DOM-block extractor (no HTML parser dependency)
# ---------------------------------------------------------------------------


def _extract_dom_block(content: str, dom_id: str) -> str:
    """Return a slice of ``content`` around the element carrying ``dom_id``.

    Crude on purpose — used only to scope the em-dash assertion to the
    Potential block without pulling in an HTML parser. Returns the substring
    from the ``id="<dom_id>"`` occurrence to the next 400 chars (enough to
    span the element body for a small placeholder block).
    """
    match = re.search(re.escape(dom_id), content)
    if match is None:
        return ""
    start = match.start()
    return content[start : start + 800]
