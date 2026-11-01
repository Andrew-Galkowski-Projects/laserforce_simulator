"""ANG-01d-1 -- ``/api/leagues/`` (seam contract section 6, endpoints #1-#8).

List scoping + filters + ordering, retrieve, templates, both create paths
(template / Advanced), DELETE (career-only teardown), the League dashboard,
paginated history and the per-Team schedule. Every row resolves through the
ownership seam: cross-Account => 404, anonymous => 401 (ADR-0041). The API never writes
``request.session["last_league_id"]``.

League create runs the real (unseeded) generator, so only structure is
asserted. No simulated point totals anywhere.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from django.urls import reverse

from core.map_test_fixtures import TempMediaMixin
from laserforce_simulator.testing import assert_owner_only
from matches.development import STAT_FIELDS
from matches.league_services import _rank_teams_by_strength
from matches.league_templates import LEAGUE_TEMPLATES
from matches.models import (
    BracketNode,
    Conference,
    GameRound,
    League,
    Match,
    OwnerEvaluation,
    PlayerSeasonRating,
    Season,
    SeasonPhase,
    SeriesMatch,
    TeamSeasonFinance,
    Tournament,
)
from matches.tests.conftest import make_team_with_slots
from matches.tests.league_api_fixtures import (
    DELETE_NOT_CAREER_MSG,
    HISTORY_ROW_KEYS,
    LEADER_KEYS,
    LEAGUE_CREATE_KEYS,
    LEAGUE_DASHBOARD_KEYS,
    LEAGUE_KEYS,
    PLAY_CONTROL_KEYS,
    REF_KEYS,
    SNIPPET_ROW_KEYS,
    TEAM_SCHEDULE_KEYS,
    TEMPLATE_KEYS,
    WINNER_KEYS,
    COMPLETED_ROW_KEYS,
    UPCOMING_ROW_KEYS,
    LeagueApiTestCase,
    add_round_states,
    confirmed_map,
    make_completed_season,
    make_league,
    make_open_season,
    make_teams,
    ranked_career_league,
)
from matches.tests.test_api_member_night import mn_season, mn_url
from teams.models import Player, Team

LIST = "/api/leagues/"


def detail(pk: int) -> str:
    return f"/api/leagues/{pk}/"


def action(pk: int, path: str) -> str:
    return f"/api/leagues/{pk}/{path}/"


def advanced_body(**overrides) -> dict:
    body = {
        "league_name": "Advanced League",
        "season_name": "Season 1",
        "start_date": "2026-01-01",
        "num_teams": 4,
        "mean": 50,
        "std_dev": 15,
        "map_mode": "none",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# ANG-08c -- populated-League builders for the DELETE teardown cases (ported
# from ``test_league_delete.py``). Hand-built rows; no simulator runs.
# ---------------------------------------------------------------------------


def del_season(league: League, *, state: str = "completed") -> Season:
    return Season.objects.create(
        league=league, name="Season 1", start_date=date(2025, 1, 1), state=state
    )


def del_rr_phase(season: Season, *, ordinal: int = 1) -> SeasonPhase:
    return SeasonPhase.objects.create(
        season=season,
        ordinal=ordinal,
        phase_type="round_robin",
        schedule_format="single_round_robin",
    )


def del_match(season: Season | None, red: Team, blue: Team) -> Match:
    """A completed Match (season-scoped, or sandbox when ``season`` is None)
    with one GameRound."""
    match = Match.objects.create(
        team_red=red, team_blue=blue, season=season, is_completed=True
    )
    GameRound.objects.create(
        match=match, round_number=1, team_red=red, team_blue=blue, is_completed=True
    )
    return match


def del_rating(player: Player, season: Season) -> PlayerSeasonRating:
    return PlayerSeasonRating.objects.create(
        player=player,
        season=season,
        age=25,
        overall_rating=50.0,
        **{name: 50 for name in STAT_FIELDS},
    )


def populated_career_league(manager) -> dict:
    """A career League with a completed Season, an RR phase, 2 enrolled Teams,
    a free-agent pool (current_team + pool set), a season Match + GameRound and
    the three Season-scoped career rows."""
    league = make_league("DelPop", manager=manager)
    season = del_season(league)
    phase = del_rr_phase(season)
    t1, _ = make_team_with_slots("DelPopA")
    t2, _ = make_team_with_slots("DelPopB")
    season.teams.add(t1, t2)
    pool = Team.objects.create(name="DelPopPool")
    Player.objects.create(team=pool, name="DelPopPoolP")
    league.current_team = t1
    league.free_agent_pool = pool
    league.save(update_fields=["current_team", "free_agent_pool"])
    match = del_match(season, t1, t2)
    return {
        "league": league,
        "season": season,
        "phase": phase,
        "teams": [t1, t2, pool],
        "match": match,
        "rating": del_rating(t1.slot_commander, season),
        "finance": TeamSeasonFinance.objects.create(team=t1, season=season),
        "evaluation": OwnerEvaluation.objects.create(
            league=league,
            season=season,
            team_managed=t1,
            wins_delta=0.0,
            playoffs_delta=0.0,
            wins_total=0.0,
            playoffs_total=0.0,
            verdict="retained",
        ),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes(LeagueApiTestCase):
    def test_route_names(self) -> None:
        self.assertEqual(reverse("league-list"), "/api/leagues/")
        self.assertEqual(reverse("league-templates"), "/api/leagues/templates/")
        self.assertEqual(reverse("league-detail", args=[7]), "/api/leagues/7/")
        for name, path in (
            ("league-dashboard", "dashboard"),
            ("league-history", "history"),
            ("league-next-season", "next-season"),
            ("league-new-team-candidates", "new-team-candidates"),
            ("league-reassign-team", "reassign-team"),
        ):
            with self.subTest(name=name):
                self.assertEqual(reverse(name, args=[7]), f"/api/leagues/7/{path}/")
        self.assertEqual(
            reverse("league-team-schedule", kwargs={"pk": 7, "team_id": 9}),
            "/api/leagues/7/teams/9/schedule/",
        )


# ---------------------------------------------------------------------------
# List + filters + ordering
# ---------------------------------------------------------------------------


class TestLeagueList(LeagueApiTestCase):
    def ids(self, params: dict | None = None) -> list[int]:
        data, _meta = self.ok_page(self.api.get(LIST, params or {}))
        return [row["id"] for row in data]

    def test_scoping_hides_foreign(self) -> None:
        own = make_league("Own", manager=self.user_a)
        unmanaged = make_league("Unmanaged")
        foreign = self.foreign_league()
        ids = self.ids()
        self.assertIn(own.id, ids)
        self.assertIn(unmanaged.id, ids)
        self.assertNotIn(foreign.id, ids)

    def test_row_shape(self) -> None:
        teams = make_teams("LstShape", 2)
        league = make_league("Shape", manager=self.user_a, current_team=teams[0])
        draft = make_open_season(league, teams)
        data, meta = self.ok_page(self.api.get(LIST))
        self.assertEqual(meta["count"], 1)
        (row,) = data
        self.assertEqual(set(row), LEAGUE_KEYS)
        self.assertEqual(row["manager"], self.user_a.pk)
        self.assertEqual(
            row["current_team"], {"id": teams[0].id, "name": teams[0].name}
        )
        self.assertEqual(row["active_season"], {"id": draft.id, "name": draft.name})
        self.assertEqual(row["visibility"], "closed")

    def test_active_season_null_without_open_season(self) -> None:
        make_league("NoSeason", manager=self.user_a)
        data, _meta = self.ok_page(self.api.get(LIST))
        self.assertIsNone(data[0]["active_season"])
        self.assertIsNone(data[0]["current_team"])

    def test_state_and_mode_filters(self) -> None:
        active = make_league("Active", manager=self.user_a)
        archived = make_league("Archived", manager=self.user_a, state="archived")
        multi = make_league("Multi", manager=self.user_a, mode="multiplayer")
        self.assertEqual(self.ids({"state": "archived"}), [archived.id])
        self.assertEqual(self.ids({"mode": "multiplayer"}), [multi.id])
        self.assertIn(active.id, self.ids({"state": "active", "mode": "league"}))

    def test_bad_filter_values_are_400(self) -> None:
        for param in ("state", "mode"):
            with self.subTest(param=param):
                self.error(
                    self.api.get(LIST, {param: "bogus"}), 400, "invalid_choice", param
                )

    def test_default_ordering_newest_first(self) -> None:
        a = make_league("A", manager=self.user_a)
        b = make_league("B", manager=self.user_a)
        self.assertEqual(self.ids(), [b.id, a.id])

    def test_ordering_by_name(self) -> None:
        c = make_league("Charlie", manager=self.user_a)
        a = make_league("Alpha", manager=self.user_a)
        b = make_league("Bravo", manager=self.user_a)
        self.assertEqual(self.ids({"ordering": "name"}), [a.id, b.id, c.id])
        self.assertEqual(self.ids({"ordering": "-name"}), [c.id, b.id, a.id])

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.error(self.anon.get(LIST), 401, "not_authenticated")
        self.error(self.anon.post(LIST, {}, format="json"), 401, "not_authenticated")

    def test_list_does_not_touch_session(self) -> None:
        make_league("Sess", manager=self.user_a)
        self.ok_page(self.api.get(LIST))
        self.assert_session_untouched()


# ---------------------------------------------------------------------------
# Retrieve / update / destroy
# ---------------------------------------------------------------------------


class TestLeagueDetail(LeagueApiTestCase):
    def test_retrieve(self) -> None:
        league = make_league("Ret", manager=self.user_a)
        data = self.ok(self.api.get(detail(league.id)))
        self.assertEqual(set(data), LEAGUE_KEYS)
        self.assertEqual(data["id"], league.id)
        self.assertEqual(data["mode"], "league")
        self.assertEqual(data["state"], "active")

    def test_retrieve_unmanaged(self) -> None:
        league = make_league("Unm")
        self.assertEqual(self.ok(self.api.get(detail(league.id)))["id"], league.id)

    def test_foreign_and_unknown_are_404(self) -> None:
        self.error(self.api.get(detail(self.foreign_league().id)), 404, "not_found")
        self.error(self.api.get(detail(999_999)), 404, "not_found")

    def test_anonymous_retrieve_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        league = make_league("AnonRet")
        self.error(self.anon.get(detail(league.id)), 401, "not_authenticated")

    def test_put_and_patch_are_405(self) -> None:
        league = make_league("NoUpd", manager=self.user_a)
        self.error(
            self.api.put(detail(league.id), {"name": "X"}, format="json"),
            405,
            "method_not_allowed",
        )
        self.error(
            self.api.patch(detail(league.id), {"name": "X"}, format="json"),
            405,
            "method_not_allowed",
        )
        league.refresh_from_db()
        self.assertEqual(league.name, "NoUpd")

    def test_delete_career_league_tears_down(self) -> None:
        league, season, teams = ranked_career_league("DelOk", 4, manager=self.user_a)
        response = self.api.delete(detail(league.id))
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(response.content, b"")
        self.assertFalse(League.objects.filter(pk=league.pk).exists())
        self.assertFalse(Season.objects.filter(pk=season.pk).exists())
        self.assert_session_untouched()

    def test_delete_non_career_is_409(self) -> None:
        league = make_league("DelMp", manager=self.user_a, mode="multiplayer")
        entry = self.error(self.api.delete(detail(league.id)), 409, "not_career_league")
        self.assertEqual(entry["message"], DELETE_NOT_CAREER_MSG)
        self.assertTrue(League.objects.filter(pk=league.pk).exists())

    def test_delete_foreign_is_404(self) -> None:
        league = self.foreign_league("DelForeign")
        self.error(self.api.delete(detail(league.id)), 404, "not_found")
        self.assertTrue(League.objects.filter(pk=league.pk).exists())

    # --- ANG-08c: the DEL-01 teardown cases from test_league_delete.py ------

    def delete_ok(self, league: League) -> None:
        response = self.api.delete(detail(league.id))
        self.assertEqual(response.status_code, 204, response.content)
        self.assertFalse(League.objects.filter(pk=league.pk).exists())

    def test_delete_removes_seasons_and_phases(self) -> None:
        f = populated_career_league(self.user_a)
        self.delete_ok(f["league"])
        self.assertFalse(Season.objects.filter(pk=f["season"].pk).exists())
        self.assertFalse(SeasonPhase.objects.filter(pk=f["phase"].pk).exists())

    def test_delete_removes_matches_and_game_rounds(self) -> None:
        f = populated_career_league(self.user_a)
        round_ids = list(f["match"].game_rounds.values_list("id", flat=True))
        self.assertTrue(round_ids)
        self.delete_ok(f["league"])
        self.assertFalse(Match.objects.filter(pk=f["match"].pk).exists())
        self.assertFalse(GameRound.objects.filter(id__in=round_ids).exists())

    def test_delete_removes_owned_teams_and_players(self) -> None:
        f = populated_career_league(self.user_a)
        team_ids = [t.id for t in f["teams"]]
        player_ids = list(
            Player.objects.filter(team_id__in=team_ids).values_list("id", flat=True)
        )
        self.assertEqual(len(player_ids), 13)  # 2 x 6 rostered + 1 pool Player
        self.delete_ok(f["league"])
        self.assertFalse(Team.objects.filter(id__in=team_ids).exists())
        self.assertFalse(Player.objects.filter(id__in=player_ids).exists())

    def test_delete_removes_season_scoped_career_rows(self) -> None:
        f = populated_career_league(self.user_a)
        self.delete_ok(f["league"])
        self.assertFalse(PlayerSeasonRating.objects.filter(pk=f["rating"].pk).exists())
        self.assertFalse(TeamSeasonFinance.objects.filter(pk=f["finance"].pk).exists())
        self.assertFalse(OwnerEvaluation.objects.filter(pk=f["evaluation"].pk).exists())

    def test_delete_keeps_a_team_enrolled_in_another_league(self) -> None:
        league_a = make_league("DelCrossA", manager=self.user_a)
        season_a = del_season(league_a)
        league_b = make_league("DelCrossB", manager=self.user_a)
        season_b = del_season(league_b, state="active")
        shared, _ = make_team_with_slots("DelShared")
        season_a.teams.add(shared)
        season_b.teams.add(shared)
        self.delete_ok(league_a)
        self.assertTrue(League.objects.filter(pk=league_b.pk).exists())
        self.assertTrue(Season.objects.filter(pk=season_b.pk).exists())
        self.assertTrue(Team.objects.filter(pk=shared.pk).exists())
        self.assertEqual(
            set(shared.enrolled_seasons.values_list("id", flat=True)), {season_b.id}
        )

    def test_delete_keeps_a_team_with_a_sandbox_match_and_the_match(self) -> None:
        league = make_league("DelSandbox", manager=self.user_a)
        season = del_season(league)
        candidate, _ = make_team_with_slots("DelSbCand")
        opponent, _ = make_team_with_slots("DelSbOpp")
        season.teams.add(candidate)
        sandbox_match = del_match(None, candidate, opponent)
        self.delete_ok(league)
        self.assertTrue(Team.objects.filter(pk=candidate.pk).exists())
        self.assertTrue(Match.objects.filter(pk=sandbox_match.pk).exists())
        self.assertTrue(Team.objects.filter(pk=opponent.pk).exists())

    def test_delete_removes_embedded_tournament_and_playoff_match(self) -> None:
        league = make_league("DelTourney", manager=self.user_a)
        season = del_season(league)
        del_rr_phase(season)
        t1, _ = make_team_with_slots("DelTtA")
        t2, _ = make_team_with_slots("DelTtB")
        season.teams.add(t1, t2)
        tournament = Tournament.objects.create(
            name="DelPlayoffs", format="single_elimination", state="active"
        )
        SeasonPhase.objects.create(
            season=season, ordinal=2, phase_type="tournament", tournament=tournament
        )
        node = BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, team_a=t1, team_b=t2
        )
        playoff = del_match(None, t1, t2)
        playoff_round_ids = list(playoff.game_rounds.values_list("id", flat=True))
        series = SeriesMatch.objects.create(node=node, match=playoff, game_number=1)
        self.delete_ok(league)
        self.assertFalse(Tournament.objects.filter(pk=tournament.pk).exists())
        self.assertFalse(BracketNode.objects.filter(pk=node.pk).exists())
        self.assertFalse(SeriesMatch.objects.filter(pk=series.pk).exists())
        self.assertFalse(Match.objects.filter(pk=playoff.pk).exists())
        self.assertFalse(GameRound.objects.filter(id__in=playoff_round_ids).exists())

    def test_delete_removes_member_night_drawn_teams(self) -> None:
        league = make_league("DelMn", manager=self.user_a)
        season = mn_season(league, "DelMn")
        self.ok(self.api.post(mn_url(season.id), {"sites": ["SiteA"]}, format="json"))
        self.assertTrue(Team.objects.filter(is_draw_team=True).exists())
        self.delete_ok(league)
        self.assertFalse(Team.objects.filter(is_draw_team=True).exists())

    def test_delete_sandbox_league_is_409_and_deletes_nothing(self) -> None:
        league = make_league("DelGate", manager=self.user_a, mode="sandbox")
        season = del_season(league)
        team, _ = make_team_with_slots("DelGateA")
        season.teams.add(team)
        entry = self.error(self.api.delete(detail(league.id)), 409, "not_career_league")
        self.assertEqual(entry["message"], DELETE_NOT_CAREER_MSG)
        self.assertTrue(League.objects.filter(pk=league.pk).exists())
        self.assertTrue(Season.objects.filter(pk=season.pk).exists())
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())


# ---------------------------------------------------------------------------
# ANG-06-14 B2 -- delete-summary
# ---------------------------------------------------------------------------


class TestLeagueDeleteSummary(LeagueApiTestCase):
    def test_route_name(self) -> None:
        self.assertEqual(
            reverse("league-delete-summary", args=[7]),
            "/api/leagues/7/delete-summary/",
        )

    def test_counts_what_delete_removes(self) -> None:
        # 4 enrolled Teams (6 players each), a ranked single round robin
        # (4 * 3 / 2 = 6 Matches), no embedded Tournament.
        league, _season, _teams = ranked_career_league("DsOk", 4, manager=self.user_a)
        data = self.ok(self.api.get(action(league.id, "delete-summary")))
        self.assertEqual(
            data,
            {"seasons": 1, "matches": 6, "tournaments": 0, "teams": 4, "players": 24},
        )
        self.assertTrue(League.objects.filter(pk=league.pk).exists())
        self.assert_session_untouched()

    def test_non_career_is_409(self) -> None:
        league = make_league("DsMp", manager=self.user_a, mode="multiplayer")
        entry = self.error(
            self.api.get(action(league.id, "delete-summary")), 409, "not_career_league"
        )
        self.assertEqual(entry["message"], DELETE_NOT_CAREER_MSG)

    def test_owner_only(self) -> None:
        league = make_league("DsOwn", manager=self.user_a)
        assert_owner_only(
            action(league.id, "delete-summary"), owner=self.user_a, other=self.user_b
        )

    def test_anonymous_is_401(self) -> None:
        league = make_league("DsAnon", manager=self.user_a)
        self.error(
            self.anon.get(action(league.id, "delete-summary")),
            401,
            "not_authenticated",
        )

    def test_post_is_405(self) -> None:
        league = make_league("DsPost", manager=self.user_a)
        self.error(
            self.api.post(action(league.id, "delete-summary"), {}, format="json"),
            405,
            "method_not_allowed",
        )


# ---------------------------------------------------------------------------
# ANG-06-14 B4 -- nav
# ---------------------------------------------------------------------------


class TestLeagueNav(LeagueApiTestCase):
    def get(self, league: League) -> dict:
        data = self.ok(self.api.get(action(league.id, "nav")))
        self.assertEqual(
            set(data),
            {"league", "displayed_season", "schedule_team", "finance_enabled"},
        )
        return data

    def test_finance_enabled_follows_the_league(self) -> None:
        # ANG-06-17 B3: both values, before any Season exists.
        off = make_league("NavFinOff", manager=self.user_a)
        on = make_league("NavFinOn", manager=self.user_a)
        League.objects.filter(pk=off.pk).update(finance_enabled=False)
        League.objects.filter(pk=on.pk).update(finance_enabled=True)
        self.assertIs(self.get(off)["finance_enabled"], False)
        self.assertIs(self.get(on)["finance_enabled"], True)

    def test_finance_enabled_is_owner_only(self) -> None:
        # ANG-06-17 B3: the flag is read through the same owned nav route.
        league = make_league("NavFinOwn", manager=self.user_a)
        League.objects.filter(pk=league.pk).update(finance_enabled=True)
        self.assertIs(self.get(league)["finance_enabled"], True)
        assert_owner_only(
            action(league.id, "nav"), owner=self.user_a, other=self.user_b
        )

    def test_route_name(self) -> None:
        self.assertEqual(reverse("league-nav", args=[7]), "/api/leagues/7/nav/")

    def test_no_season_is_all_null(self) -> None:
        league = make_league("NavNone", manager=self.user_a)
        data = self.get(league)
        self.assertEqual(
            data["league"], {"id": league.id, "name": "NavNone", "mode": "league"}
        )
        self.assertIsNone(data["displayed_season"])
        self.assertIsNone(data["schedule_team"])
        self.assert_session_untouched()

    def test_current_team_when_enrolled(self) -> None:
        teams = make_teams("NavCur", 3)
        league = make_league(
            "NavCurL", manager=self.user_a, mode="multiplayer", current_team=teams[2]
        )
        draft = make_open_season(league, teams)
        data = self.get(league)
        self.assertEqual(data["league"]["mode"], "multiplayer")
        self.assertEqual(data["displayed_season"], {"id": draft.id, "name": draft.name})
        self.assertEqual(
            data["schedule_team"], {"id": teams[2].id, "name": teams[2].name}
        )

    def test_first_enrolled_alphabetically_when_current_not_enrolled(self) -> None:
        (outsider,) = make_teams("NavOut", 1)
        bravo, alpha = make_teams("NavB", 1) + make_teams("NavA", 1)
        league = make_league("NavAlpha", manager=self.user_a, current_team=outsider)
        make_open_season(league, [bravo, alpha])
        data = self.get(league)
        self.assertEqual(data["schedule_team"], {"id": alpha.id, "name": alpha.name})

    def test_season_without_teams_has_null_team(self) -> None:
        league = make_league("NavEmpty", manager=self.user_a)
        season = make_open_season(league, [])
        data = self.get(league)
        self.assertEqual(data["displayed_season"]["id"], season.id)
        self.assertIsNone(data["schedule_team"])

    def test_owner_only(self) -> None:
        league = make_league("NavOwn", manager=self.user_a)
        assert_owner_only(
            action(league.id, "nav"), owner=self.user_a, other=self.user_b
        )

    def test_anonymous_is_401(self) -> None:
        league = make_league("NavAnon", manager=self.user_a)
        self.error(self.anon.get(action(league.id, "nav")), 401, "not_authenticated")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class TestLeagueTemplates(LeagueApiTestCase):
    def test_templates(self) -> None:
        response = self.api.get("/api/leagues/templates/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        data = body["data"]
        self.assertEqual(len(data), 5)
        self.assertEqual([t["key"] for t in data], [t.key for t in LEAGUE_TEMPLATES])
        for entry in data:
            self.assertEqual(set(entry), TEMPLATE_KEYS)

    def test_templates_post_is_405(self) -> None:
        self.error(
            self.api.post("/api/leagues/templates/", {}, format="json"),
            405,
            "method_not_allowed",
        )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestLeagueCreate(TempMediaMixin, LeagueApiTestCase):
    # TempMediaMixin: the ANG-08c map-mode cases build confirmed maps.

    def post(self, body, **kwargs):
        return self.api.post(LIST, body, format="json", **kwargs)

    def assert_created(self, response) -> dict:
        data = self.ok(response, 201)
        self.assertEqual(set(data), LEAGUE_CREATE_KEYS)
        self.assertEqual(data["manager"], self.user_a.pk)
        self.assertEqual(set(data["draft_season"]), REF_KEYS)
        season = Season.objects.get(pk=data["draft_season"]["id"])
        self.assertEqual(season.state, "draft")
        self.assertEqual(season.league_id, data["id"])
        self.assertEqual(League.objects.get(pk=data["id"]).manager_id, self.user_a.pk)
        return data

    def test_template_path_201(self) -> None:
        data = self.assert_created(
            self.post({"template": "4_team_quick", "league_name": "Quick"})
        )
        self.assertEqual(data["name"], "Quick")
        self.assertEqual(data["conference_ids"], [])
        season = Season.objects.get(pk=data["draft_season"]["id"])
        self.assertEqual(season.teams.count(), 4)
        self.assert_session_untouched()

    def test_template_path_ignores_other_keys(self) -> None:
        data = self.assert_created(
            self.post(
                {
                    "template": "4_team_quick",
                    "league_name": "Ignore",
                    "num_teams": 16,
                    "number_of_conferences": 2,
                    "finance_enabled": True,
                }
            )
        )
        season = Season.objects.get(pk=data["draft_season"]["id"])
        self.assertEqual(season.teams.count(), 4)
        self.assertEqual(data["conference_ids"], [])
        self.assertFalse(data["finance_enabled"])

    def test_template_finance_flag_from_template(self) -> None:
        data = self.assert_created(
            self.post({"template": "8_team_career", "league_name": "Career"})
        )
        self.assertTrue(data["finance_enabled"])

    def test_unknown_template_is_400(self) -> None:
        self.error(
            self.post({"template": "nope", "league_name": "X"}),
            400,
            "invalid_choice",
            "template",
        )
        self.assertEqual(League.objects.count(), 0)

    def test_empty_template_is_required(self) -> None:
        self.error(self.post({"template": ""}), 400, "required", "template")
        self.assertEqual(League.objects.count(), 0)

    def test_template_path_missing_league_name(self) -> None:
        self.error(
            self.post({"template": "4_team_quick"}), 400, "required", "league_name"
        )
        self.assertEqual(League.objects.count(), 0)

    def test_advanced_path_201(self) -> None:
        data = self.assert_created(self.post(advanced_body()))
        self.assertEqual(data["name"], "Advanced League")
        self.assertEqual(data["draft_season"]["name"], "Season 1")
        self.assertEqual(data["conference_ids"], [])

    def test_advanced_path_with_conferences(self) -> None:
        data = self.assert_created(self.post(advanced_body(number_of_conferences=2)))
        self.assertEqual(len(data["conference_ids"]), 2)
        confs = Conference.objects.filter(season_id=data["draft_season"]["id"])
        self.assertEqual(sorted(data["conference_ids"]), sorted(c.id for c in confs))

    def test_advanced_path_missing_fields_are_required(self) -> None:
        errors = self.errors(self.post({}), 400)
        required = {e["field"] for e in errors if e["code"] == "required"}
        self.assertTrue(
            {
                "league_name",
                "season_name",
                "start_date",
                "num_teams",
                "mean",
                "std_dev",
                "map_mode",
            }
            <= required,
            errors,
        )
        self.assertEqual(League.objects.count(), 0)

    def test_advanced_path_clean_rule_error(self) -> None:
        self.error(
            self.post(advanced_body(map_mode="single")), 400, "invalid", "map_pool"
        )
        self.assertEqual(League.objects.count(), 0)

    def test_advanced_path_field_validation(self) -> None:
        self.error(
            self.post(advanced_body(num_teams=5)), 400, "invalid_choice", "num_teams"
        )
        self.error(self.post(advanced_body(mean=101)), 400, "max_value", "mean")
        self.error(
            self.post(advanced_body(number_of_conferences=4)),
            400,
            "invalid",
            "number_of_conferences",
        )
        self.assertEqual(League.objects.count(), 0)

    def test_non_object_body_is_parse_error(self) -> None:
        self.error(self.post([1, 2]), 400, "parse_error")
        self.assertEqual(League.objects.count(), 0)

    # --- ANG-08c: the create_league_and_season cases ported from the HTML
    #     ``test_league_create.py`` / ``test_create_form_finance.py`` -------

    def test_advanced_path_range_validation_writes_nothing(self) -> None:
        # ANG-08c: the rest of the HTML TestLeagueCreateFormValidation bounds.
        before = self.row_counts()
        for overrides, code, field in (
            ({"num_teams": 0}, "invalid_choice", "num_teams"),
            ({"mean": -1}, "min_value", "mean"),
            ({"std_dev": 0}, "min_value", "std_dev"),
            ({"std_dev": 41}, "max_value", "std_dev"),
        ):
            with self.case(**overrides):
                self.error(self.post(advanced_body(**overrides)), 400, code, field)
        body = advanced_body()
        del body["start_date"]
        self.error(self.post(body), 400, "required", "start_date")
        self.assertEqual(self.row_counts(), before)

    @contextmanager
    def case(self, **params) -> Iterator[None]:
        """A loop case that fails the whole test, labelled with ``params``.

        Not ``subTest``: a failed subtest prints no ``FAILED`` line, so
        ``scripts/mutate.py`` would read a caught mutation as BROKEN.
        """
        try:
            yield
        except AssertionError as exc:
            raise AssertionError(f"{params}: {exc}") from exc

    def create(self, **overrides) -> Season:
        """POST an Advanced body; return the created draft Season."""
        data = self.assert_created(self.post(advanced_body(**overrides)))
        return Season.objects.get(pk=data["draft_season"]["id"])

    def phases(self, wire: str) -> list[SeasonPhase]:
        return list(self.create(phases=wire).phases.all())

    def row_counts(self) -> tuple[int, int, int, int]:
        return (
            League.objects.count(),
            Season.objects.count(),
            SeasonPhase.objects.count(),
            Team.objects.count(),
        )

    def assert_rejected(self, body: dict, field: str) -> None:
        """A 400 ``invalid`` on ``field`` that writes no row at all."""
        before = self.row_counts()
        self.error(self.post(body), 400, "invalid", field)
        self.assertEqual(self.row_counts(), before)

    def test_advanced_creates_career_league_and_draft_season(self) -> None:
        season = self.create()
        league = season.league
        self.assertEqual((league.mode, league.state), ("league", "active"))
        self.assertEqual(season.name, "Season 1")
        self.assertEqual(season.state, "draft")
        self.assertEqual(season.schedule_format, "single_round_robin")
        self.assertEqual(season.start_date, date(2026, 1, 1))
        self.assertIsNone(season.champion_team)
        self.assertIsNone(season.starting_team_ids_json)

    def test_advanced_enrols_num_teams_of_six_slotted_players(self) -> None:
        season = self.create()
        self.assertEqual(season.teams.count(), 4)
        for team in season.teams.all():
            self.assertEqual(team.players.count(), 6, team.name)
            slots = {
                team.slot_commander_id,
                team.slot_heavy_id,
                team.slot_scout_1_id,
                team.slot_scout_2_id,
                team.slot_medic_id,
                team.slot_ammo_id,
            }
            self.assertNotIn(None, slots, team.name)
            self.assertEqual(len(slots), 6, team.name)
            for player in team.players.all():
                for attr in ("accuracy", "survival", "decision_making"):
                    self.assertTrue(0 <= getattr(player, attr) <= 100, attr)

    def test_sixteen_teams_enrol_ninety_six_players(self) -> None:
        season = self.create(num_teams=16)
        self.assertEqual(season.teams.count(), 16)
        self.assertEqual(Player.objects.filter(team__in=season.teams.all()).count(), 96)

    def test_each_league_gets_its_own_free_agent_pool(self) -> None:
        s1 = self.create(league_name="Pool1")
        s2 = self.create(league_name="Pool2")
        pools = []
        for season in (s1, s2):
            pool = season.league.free_agent_pool
            self.assertIsNotNone(pool)
            self.assertTrue(100 <= pool.players.count() <= 200)
            self.assertNotIn(pool.id, season.teams.values_list("id", flat=True))
            self.assertNotIn(
                pool.id, Team.objects.regular().values_list("id", flat=True)
            )
            pools.append(pool.id)
        self.assertNotEqual(pools[0], pools[1])

    def test_tampered_schedule_format_still_single_round_robin(self) -> None:
        season = self.create(schedule_format="double_round_robin")
        self.assertEqual(season.schedule_format, "single_round_robin")
        self.assertEqual(season.phases.get().schedule_format, "single_round_robin")

    def test_season_create_failure_rolls_back_every_row(self) -> None:
        before = self.row_counts()
        with patch(
            "matches.league_services.Season.objects.create",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self.post(advanced_body())
        self.assertEqual(self.row_counts(), before)

    def test_current_team_is_the_medium_difficulty_pick(self) -> None:
        season = self.create()
        ranked = _rank_teams_by_strength(list(season.teams.all()))
        self.assertEqual(season.league.current_team_id, ranked[len(ranked) // 2].id)

    def test_map_mode_none_persists_an_empty_pool(self) -> None:
        season = self.create(map_mode="none")
        self.assertEqual(season.map_mode, "none")
        self.assertEqual(season.map_pool.count(), 0)

    def test_map_mode_single_persists_its_map(self) -> None:
        arena = confirmed_map("ApiSingle")
        season = self.create(map_mode="single", map_pool=[arena.id])
        self.assertEqual(season.map_mode, "single")
        self.assertEqual(list(season.map_pool.values_list("id", flat=True)), [arena.id])

    def test_map_mode_random_per_round_persists_the_pool(self) -> None:
        maps = [confirmed_map(f"ApiRand{i}") for i in range(3)]
        season = self.create(map_mode="random_per_round", map_pool=[m.id for m in maps])
        self.assertEqual(season.map_mode, "random_per_round")
        self.assertEqual(
            sorted(season.map_pool.values_list("id", flat=True)),
            sorted(m.id for m in maps),
        )

    def test_invalid_map_pool_writes_nothing(self) -> None:
        a, b = confirmed_map("ApiBadA"), confirmed_map("ApiBadB")
        for mode, pool in (
            ("none", [a.id]),
            ("single", []),
            ("single", [a.id, b.id]),
            ("random_per_round", []),
            ("rotate_by_matchday", [a.id]),
        ):
            with self.case(mode=mode, pool=pool):
                body = advanced_body(map_mode=mode, map_pool=pool)
                if mode == "rotate_by_matchday":
                    body["map_rotation"] = str(b.id)
                self.assert_rejected(body, "map_pool")

    def test_invalid_map_rotation_writes_nothing(self) -> None:
        arena = confirmed_map("ApiBadRot")
        for mode, pool, rotation in (
            ("none", [], str(arena.id)),
            ("single", [arena.id], str(arena.id)),
            ("random_per_round", [arena.id], str(arena.id)),
            ("rotate_by_matchday", [], ""),
            ("rotate_by_matchday", [], f"{arena.id},999999"),
        ):
            with self.case(mode=mode, rotation=rotation):
                self.assert_rejected(
                    advanced_body(map_mode=mode, map_pool=pool, map_rotation=rotation),
                    "map_rotation",
                )

    def test_rotate_by_matchday_keeps_the_author_order(self) -> None:
        a, b, c = (confirmed_map(f"ApiRot{x}") for x in "ABC")
        order = [c.id, a.id, b.id]
        season = self.create(
            map_mode="rotate_by_matchday", map_rotation=",".join(map(str, order))
        )
        self.assertEqual(season.map_mode, "rotate_by_matchday")
        self.assertEqual(season.map_rotation_ids_json, order)
        self.assertEqual(season.map_pool.count(), 0)

    def test_rotate_by_conference_persists_with_no_season_level_maps(self) -> None:
        season = self.create(
            map_mode="rotate_by_conference", num_teams=8, number_of_conferences=2
        )
        self.assertEqual(season.map_mode, "rotate_by_conference")
        self.assertEqual(season.map_pool.count(), 0)
        self.assertEqual(season.map_rotation_ids_json or [], [])
        self.assertEqual(season.conferences.count(), 2)

    def test_rotate_by_conference_without_two_conferences_writes_nothing(
        self,
    ) -> None:
        for n_conf in (0, None):
            with self.case(number_of_conferences=n_conf):
                body = advanced_body(map_mode="rotate_by_conference")
                if n_conf is not None:
                    body["number_of_conferences"] = n_conf
                self.assert_rejected(body, "number_of_conferences")

    def test_conferences_split_the_teams_evenly(self) -> None:
        season = self.create(num_teams=8, number_of_conferences=2)
        confs = list(season.conferences.order_by("ordinal"))
        self.assertEqual([c.ordinal for c in confs], [1, 2])
        self.assertEqual([c.name for c in confs], ["Conference 1", "Conference 2"])
        self.assertEqual([c.teams.count() for c in confs], [4, 4])
        assigned = set()
        for conf in confs:
            assigned |= set(conf.teams.values_list("id", flat=True))
        self.assertEqual(assigned, set(season.teams.values_list("id", flat=True)))

    def test_default_creates_one_round_robin_phase(self) -> None:
        for overrides in ({}, {"phases": ""}):
            with self.case(overrides=overrides):
                (phase,) = self.create(**overrides).phases.all()
                self.assertEqual(phase.ordinal, 1)
                self.assertEqual(phase.phase_type, "round_robin")
                self.assertEqual(phase.schedule_format, "single_round_robin")
                self.assertEqual(phase.tournament_mode, "standings")
                self.assertIsNone(phase.tournament_id)

    def test_composer_persists_ordered_phase_rows(self) -> None:
        phases = self.phases("round_robin,tournament,round_robin")
        self.assertEqual([p.ordinal for p in phases], [1, 2, 3])
        self.assertEqual(
            [p.phase_type for p in phases], ["round_robin", "tournament", "round_robin"]
        )
        self.assertEqual(
            [p.schedule_format for p in phases],
            ["single_round_robin", None, "single_round_robin"],
        )
        self.assertEqual([p.tournament_mode for p in phases], ["standings"] * 3)
        self.assertEqual([p.tournament_id for p in phases], [None] * 3)

    def test_double_round_robin_token_persists(self) -> None:
        phases = self.phases("round_robin:double_round_robin,tournament")
        self.assertEqual([p.phase_type for p in phases], ["round_robin", "tournament"])
        self.assertEqual(phases[0].schedule_format, "double_round_robin")
        self.assertIsNone(phases[1].schedule_format)
        (bare,) = self.phases("round_robin")
        self.assertEqual(bare.schedule_format, "single_round_robin")

    def test_invalid_composition_writes_nothing(self) -> None:
        for wire in (
            "tournament",  # no round robin
            "round_robin:triple_round_robin",  # unknown RR format
            "round_robin,tournament:random_draw",  # deferred mode
            "tournament:standings,round_robin",  # standings before any RR
            "round_robin,tournament:standings:2",  # cut under the floor
            # RR -> DE with an invalid wb / lb combo.
            "round_robin,tournament:standings:0:round_robin_double_elim:1:1:1:1:8:2:0",
            # A best-of-2 final series.
            "round_robin,tournament:standings:0:single_elimination:2:1:1:1:0:0:0",
        ):
            with self.case(phases=wire):
                self.assert_rejected(advanced_body(phases=wire), "phases")

    def test_tournament_mode_persists(self) -> None:
        for wire, ordinal, mode in (
            ("tournament:strength,round_robin", 0, "strength"),
            ("tournament:unseeded,round_robin", 0, "unseeded"),
            ("round_robin,tournament:standings,round_robin", 1, "standings"),
        ):
            with self.case(phases=wire):
                phases = self.phases(wire)
                self.assertEqual(phases[ordinal].phase_type, "tournament")
                self.assertEqual(phases[ordinal].tournament_mode, mode)
                # The round robin beside it keeps the standings default.
                self.assertEqual(phases[1 - ordinal].tournament_mode, "standings")

    def test_tournament_cut_persists(self) -> None:
        for wire, ordinal, cut in (
            ("round_robin,tournament:standings:8", 1, 8),
            ("tournament:strength:4,round_robin", 0, 4),
            ("round_robin,tournament", 1, 0),
        ):
            with self.case(phases=wire):
                self.assertEqual(self.phases(wire)[ordinal].tournament_cut, cut)

    def test_tournament_sub_config_persists(self) -> None:
        keys = (
            "tournament_format",
            "final_series_length",
            "semifinal_series_length",
            "quarterfinal_series_length",
            "earlier_series_length",
            "wb_advancers",
            "lb_advancers",
            "swiss_rounds",
        )
        for token, expected in (
            (
                "tournament:standings:0:double_elimination:3:3:1:1:0:0:0",
                ("double_elimination", 3, 3, 1, 1, 0, 0, 0),
            ),
            (
                "tournament:standings:0:round_robin_double_elim:1:1:1:1:8:4:0",
                ("round_robin_double_elim", 1, 1, 1, 1, 8, 4, 0),
            ),
            (
                "tournament:standings:0:swiss:1:1:1:1:0:0:6",
                ("swiss", 1, 1, 1, 1, 0, 0, 6),
            ),
            ("tournament", ("single_elimination", 1, 1, 1, 1, 0, 0, 0)),
        ):
            with self.case(token=token):
                rr, tournament = self.phases(f"round_robin,{token}")
                self.assertEqual(tuple(getattr(tournament, k) for k in keys), expected)
                self.assertEqual(rr.tournament_format, "single_elimination")

    def founding_player_ids(self, season: Season) -> set[int]:
        teams = list(season.teams.all()) + [season.league.free_agent_pool]
        return set(Player.objects.filter(team__in=teams).values_list("id", flat=True))

    def test_baseline_rating_per_founding_player(self) -> None:
        season = self.create()
        rows = list(PlayerSeasonRating.objects.select_related("player"))
        self.assertEqual({r.season_id for r in rows}, {season.id})
        self.assertEqual(len(rows), len(self.founding_player_ids(season)))
        self.assertEqual({r.player_id for r in rows}, self.founding_player_ids(season))
        for row in rows:
            self.assertEqual(row.age, row.player.age)
            self.assertAlmostEqual(
                row.overall_rating, row.player.overall_rating, places=4
            )
            for name in STAT_FIELDS:
                self.assertEqual(getattr(row, name), getattr(row.player, name), name)

    def test_baseline_potential_is_filled(self) -> None:
        season = self.create()
        players = Player.objects.filter(id__in=self.founding_player_ids(season))
        for player in players:
            self.assertIsNotNone(player.potential, player.name)
            self.assertTrue(
                player.overall_rating - 1e-6 <= player.potential <= 100.0, player.name
            )
        rows = PlayerSeasonRating.objects.filter(season=season).select_related("player")
        self.assertTrue(rows.exists())
        for row in rows:
            self.assertIsNotNone(row.potential)
            self.assertAlmostEqual(row.potential, row.player.potential, places=4)

    def test_named_manager_team_is_one_of_the_n(self) -> None:
        for given, stored in (("Galkowski FC", "Galkowski FC"), ("  X  ", "X")):
            with self.case(manager_team_name=given):
                season = self.create(manager_team_name=given)
                current = season.league.current_team
                self.assertEqual(current.name, stored)
                self.assertIn(current.id, season.teams.values_list("id", flat=True))
                self.assertEqual(season.teams.count(), 4)

    def test_finance_on_seeds_budgets_by_strength(self) -> None:
        season = self.create(finance_enabled=True)
        teams = list(season.teams.all())
        levels = sorted(t.budget_scouting for t in teams)
        self.assertEqual((levels[0], levels[-1]), (20, 90))  # weakest, strongest
        for team in teams:
            self.assertEqual(team.budget_scouting, team.budget_coaching)
            self.assertEqual(team.budget_scouting, team.budget_facilities)
        current = Team.objects.get(pk=season.league.current_team_id)
        self.assertNotEqual(current.budget_scouting, 34)

    def test_finance_off_leaves_budgets_at_the_default(self) -> None:
        season = self.create()
        for team in season.teams.all():
            self.assertEqual(
                (team.budget_scouting, team.budget_coaching, team.budget_facilities),
                (34, 34, 34),
            )

    def test_advanced_finance_enabled_persists(self) -> None:
        # Ported from test_create_form_finance.py (advanced path).
        for value in (True, False):
            with self.case(finance_enabled=value):
                data = self.assert_created(
                    self.post(advanced_body(finance_enabled=value))
                )
                self.assertIs(data["finance_enabled"], value)
                self.assertIs(League.objects.get(pk=data["id"]).finance_enabled, value)
        data = self.assert_created(self.post(advanced_body()))
        self.assertIs(League.objects.get(pk=data["id"]).finance_enabled, False)

    def test_challenge_luxury_tax_persists(self) -> None:
        for value in (True, False):
            with self.case(challenge_fired_luxury_tax=value):
                data = self.assert_created(
                    self.post(advanced_body(challenge_fired_luxury_tax=value))
                )
                self.assertIs(data["challenge_fired_luxury_tax"], value)
                self.assertIs(
                    League.objects.get(pk=data["id"]).challenge_fired_luxury_tax,
                    value,
                )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestLeagueDashboard(LeagueApiTestCase):
    def get(self, league: League) -> dict:
        data = self.ok(self.api.get(action(league.id, "dashboard")))
        self.assertEqual(set(data), LEAGUE_DASHBOARD_KEYS)
        self.assertFalse(PLAY_CONTROL_KEYS & set(data))
        self.assertEqual(data["league"], {"id": league.id, "name": league.name})
        return data

    def test_none_mode(self) -> None:
        league = make_league("DashNone", manager=self.user_a)
        data = self.get(league)
        self.assertEqual(data["season_mode"], "none")
        self.assertIsNone(data["displayed_season"])
        self.assertEqual(data["standings_snippet"], [])
        self.assertIsNone(data["next_fixture"])
        self.assertEqual(data["round_count_completed"], 0)
        self.assertEqual(data["round_count_total"], 0)
        for key in ("leaders_points", "leaders_tags", "leaders_ratio"):
            self.assertEqual(data[key], [])
        self.assertIsInstance(data["map_config_label"], str)
        self.assert_session_untouched()

    def test_draft_mode(self) -> None:
        teams = make_teams("DashDraft", 4)
        league = make_league("DashDraftL", manager=self.user_a)
        draft = make_open_season(league, teams)
        data = self.get(league)
        self.assertEqual(data["season_mode"], "draft")
        self.assertEqual(data["displayed_season"], {"id": draft.id, "name": draft.name})
        self.assertEqual(len(data["standings_snippet"]), 3)
        for row in data["standings_snippet"]:
            self.assertEqual(set(row), SNIPPET_ROW_KEYS)
            self.assertEqual(set(row["team"]), REF_KEYS)
        self.assertIsNone(data["playoff_tournament_id"])

    def test_completed_mode(self) -> None:
        league, season, teams = ranked_career_league("DashDone", 4, manager=self.user_a)
        add_round_states(season, teams[0], teams[1])
        data = self.get(league)
        self.assertEqual(data["season_mode"], "completed")
        self.assertEqual(data["displayed_season"]["id"], season.id)
        self.assertEqual(len(data["standings_snippet"]), 3)
        self.assertEqual(data["standings_snippet"][0]["team"]["id"], teams[0].id)
        self.assertEqual(data["round_count_completed"], data["round_count_total"])
        self.assertIsNone(data["next_fixture"])
        self.assertTrue(data["leaders_points"])
        for key in ("leaders_points", "leaders_tags", "leaders_ratio"):
            for row in data[key]:
                self.assertEqual(set(row), LEADER_KEYS)

    def test_active_mode_has_next_fixture(self) -> None:
        teams = make_teams("DashAct", 4)
        league = make_league("DashActL", manager=self.user_a)
        make_open_season(league, teams, state="active")
        data = self.get(league)
        self.assertEqual(data["season_mode"], "active")
        self.assertIsNotNone(data["next_fixture"])
        self.assertIn("matchday", data["next_fixture"])
        self.assertGreater(data["round_count_total"], 0)

    # --- ANG-06-14 B5: the five League-only keys -----------------------

    def test_career_league_mode_and_managed_team(self) -> None:
        teams = make_teams("DashMg", 2)
        league = make_league("DashMgL", manager=self.user_a, current_team=teams[1])
        data = self.get(league)
        self.assertEqual(data["mode"], "league")
        self.assertEqual(
            data["managed_team"], {"id": teams[1].id, "name": teams[1].name}
        )

    def test_non_career_league_has_no_managed_team(self) -> None:
        teams = make_teams("DashMp", 2)
        league = make_league(
            "DashMpL", manager=self.user_a, mode="multiplayer", current_team=teams[0]
        )
        data = self.get(league)
        self.assertEqual(data["mode"], "multiplayer")
        self.assertIsNone(data["managed_team"])

    def test_career_league_without_current_team_has_no_managed_team(self) -> None:
        data = self.get(make_league("DashNoCur", manager=self.user_a))
        self.assertIsNone(data["managed_team"])

    def test_has_owner_evaluations_false_without_any(self) -> None:
        data = self.get(make_league("DashNoEval", manager=self.user_a))
        self.assertIs(data["has_owner_evaluations"], False)

    def test_has_owner_evaluations_true_with_one(self) -> None:
        league, _season, _teams = ranked_career_league(
            "DashEval", 4, manager=self.user_a, fired=True
        )
        self.assertIs(self.get(league)["has_owner_evaluations"], True)

    def test_member_night_progress_null_off_a_member_night(self) -> None:
        teams = make_teams("DashNoMn", 4)
        league = make_league("DashNoMnL", manager=self.user_a)
        make_open_season(league, teams, state="active")
        self.assertIsNone(self.get(league)["member_night_progress"])

    def test_member_night_progress_counts_the_drawn_games(self) -> None:
        league = make_league("DashMnL", manager=self.user_a)
        season = mn_season(league, "DashMn")
        phase = season.current_phase()
        red, blue = make_teams("DashMnDraw", 2)
        for completed in (True, False):
            Match.objects.create(
                team_red=red,
                team_blue=blue,
                season=season,
                season_phase=phase,
                is_completed=completed,
            )
        self.assertEqual(
            self.get(league)["member_night_progress"], {"played": 1, "total": 2}
        )

    def test_manager_awaiting_reassignment_true_when_fired(self) -> None:
        league, _season, _teams = ranked_career_league(
            "DashFired", 4, manager=self.user_a, fired=True
        )
        self.assertIs(self.get(league)["manager_awaiting_reassignment"], True)

    def test_manager_awaiting_reassignment_false_when_retained(self) -> None:
        league, _season, _teams = ranked_career_league(
            "DashKept", 4, manager=self.user_a
        )
        data = self.get(league)
        self.assertEqual(data["season_mode"], "completed")
        self.assertIs(data["manager_awaiting_reassignment"], False)

    def test_manager_awaiting_reassignment_false_once_reassigned(self) -> None:
        league, _season, teams = ranked_career_league(
            "DashMoved", 4, manager=self.user_a, fired=True
        )
        league.current_team = teams[-1]
        league.save(update_fields=["current_team"])
        self.assertIs(self.get(league)["manager_awaiting_reassignment"], False)

    def test_manager_awaiting_reassignment_false_for_non_career(self) -> None:
        league, _season, _teams = ranked_career_league(
            "DashMpFired", 4, manager=self.user_a, mode="multiplayer", fired=True
        )
        self.assertIs(self.get(league)["manager_awaiting_reassignment"], False)

    def test_manager_awaiting_reassignment_false_while_season_open(self) -> None:
        league, _season, teams = ranked_career_league(
            "DashOpen", 4, manager=self.user_a, fired=True
        )
        make_open_season(league, teams, state="active")
        self.assertIs(self.get(league)["manager_awaiting_reassignment"], False)

    def test_foreign_is_404(self) -> None:
        self.error(
            self.api.get(action(self.foreign_league().id, "dashboard")),
            404,
            "not_found",
        )

    def test_post_is_405(self) -> None:
        league = make_league("DashPost", manager=self.user_a)
        self.error(
            self.api.post(action(league.id, "dashboard"), {}, format="json"),
            405,
            "method_not_allowed",
        )


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


class TestLeagueHistory(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teams = make_teams("Hist", 4)
        self.league = make_league(
            "HistL", manager=self.user_a, current_team=self.teams[0]
        )
        self.completed = [
            make_completed_season(
                self.league,
                self.teams,
                name=f"Season {i + 1}",
                start_date=date(2023 + i, 1, 1),
            )
            for i in range(3)
        ]
        self.draft = make_open_season(
            self.league, self.teams, name="Season 4", start_date=date(2026, 1, 1)
        )

    def get(self, params: dict | None = None):
        return self.api.get(action(self.league.id, "history"), params or {})

    def test_paginated_with_in_progress_first(self) -> None:
        data, meta = self.ok_page(self.get({"page_size": 2}))
        self.assertEqual(
            meta, {"page": 1, "page_size": 2, "count": 4, "total_pages": 2}
        )
        self.assertEqual(len(data), 2)
        self.assertTrue(data[0]["is_in_progress"])
        self.assertEqual(data[0]["season_id"], self.draft.id)
        self.assertIsNone(data[0]["champion"])
        self.assertFalse(data[1]["is_in_progress"])
        self.assertEqual(data[1]["season_id"], self.completed[-1].id)

    def test_second_page_has_no_in_progress_row(self) -> None:
        data, meta = self.ok_page(self.get({"page_size": 2, "page": 2}))
        self.assertEqual(meta["page"], 2)
        self.assertEqual(len(data), 2)
        self.assertFalse(any(row["is_in_progress"] for row in data))
        self.assertEqual(
            [row["season_id"] for row in data],
            [self.completed[1].id, self.completed[0].id],
        )

    def test_row_shape(self) -> None:
        data, _meta = self.ok_page(self.get())
        self.assertEqual(len(data), 4)
        for row in data:
            self.assertEqual(set(row), HISTORY_ROW_KEYS)
            self.assertEqual(len(row["top_three"]), 3)
            self.assertIsInstance(row["start_date"], str)
            for slot in ("season_mvp", "finals_mvp"):
                if row[slot] is not None:
                    self.assertEqual(set(row[slot]), WINNER_KEYS)
        completed = data[1]
        self.assertEqual(
            completed["champion"], {"id": self.teams[0].id, "name": self.teams[0].name}
        )
        self.assertEqual(completed["runner_up"]["id"], self.teams[1].id)
        self.assertEqual(
            [t["id"] for t in completed["top_three"]], [t.id for t in self.teams[:3]]
        )
        self.assertEqual(completed["teams_enrolled"], 4)

    def test_bad_page_is_404(self) -> None:
        self.error(self.get({"page": 9}), 404, "not_found")
        self.error(self.get({"page": "abc"}), 404, "not_found")

    def test_empty_league(self) -> None:
        league = make_league("HistEmpty", manager=self.user_a)
        data, meta = self.ok_page(self.api.get(action(league.id, "history")))
        self.assertEqual(data, [])
        self.assertEqual(meta["count"], 0)

    def test_foreign_is_404(self) -> None:
        self.error(
            self.api.get(action(self.foreign_league().id, "history")), 404, "not_found"
        )

    def test_does_not_touch_session(self) -> None:
        self.ok_page(self.get())
        self.assert_session_untouched()


# ---------------------------------------------------------------------------
# Team schedule
# ---------------------------------------------------------------------------


class TestLeagueTeamSchedule(LeagueApiTestCase):
    def url(self, league_id: int, team_id: int) -> str:
        return f"/api/leagues/{league_id}/teams/{team_id}/schedule/"

    def test_completed_schedule(self) -> None:
        league, season, teams = ranked_career_league("TsDone", 4, manager=self.user_a)
        data = self.ok(self.api.get(self.url(league.id, teams[0].id)))
        self.assertEqual(set(data), TEAM_SCHEDULE_KEYS)
        self.assertEqual(data["league"], {"id": league.id, "name": league.name})
        self.assertEqual(data["season"], {"id": season.id, "name": season.name})
        self.assertEqual(data["team"], {"id": teams[0].id, "name": teams[0].name})
        self.assertTrue(data["completed"])
        for row in data["completed"]:
            self.assertEqual(set(row), COMPLETED_ROW_KEYS)
        self.assertEqual(data["member_night_appearances"], [])
        self.assert_session_untouched()

    def test_meta_team_options_are_the_enrolled_teams_by_name(self) -> None:
        # ANG-06-16 B2. ``ok()`` ignores ``meta``, so read the body directly.
        league, _season, teams = ranked_career_league("TsMeta", 3, manager=self.user_a)
        make_teams("TsMetaOut", 1)  # Not enrolled: never an option.
        response = self.api.get(self.url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["meta"],
            {
                "team_options": [
                    {"id": t.id, "name": t.name}
                    for t in sorted(teams, key=lambda t: t.name)
                ]
            },
        )

    def test_draft_schedule_has_upcoming(self) -> None:
        teams = make_teams("TsDraft", 4)
        league = make_league("TsDraftL", manager=self.user_a)
        make_open_season(league, teams)
        data = self.ok(self.api.get(self.url(league.id, teams[1].id)))
        self.assertTrue(data["upcoming"])
        for row in data["upcoming"]:
            self.assertEqual(set(row), UPCOMING_ROW_KEYS)

    def test_no_season_is_404(self) -> None:
        (team,) = make_teams("TsNoSeason", 1)
        league = make_league("TsNoSeasonL", manager=self.user_a)
        self.error(self.api.get(self.url(league.id, team.id)), 404, "not_found")

    def test_foreign_or_unknown_team_is_404(self) -> None:
        league, _season, _teams = ranked_career_league(
            "TsForeign", 4, manager=self.user_a
        )
        (foreign_team,) = make_teams("TsForeignT", 1, manager=self.user_b)
        self.error(self.api.get(self.url(league.id, foreign_team.id)), 404, "not_found")
        self.error(self.api.get(self.url(league.id, 999_999)), 404, "not_found")

    def test_foreign_league_is_404(self) -> None:
        (team,) = make_teams("TsFl", 1)
        league = self.foreign_league("TsFlL")
        self.error(self.api.get(self.url(league.id, team.id)), 404, "not_found")

    def test_post_is_405(self) -> None:
        league, _season, teams = ranked_career_league("TsPost", 4, manager=self.user_a)
        self.error(
            self.api.post(self.url(league.id, teams[0].id), {}, format="json"),
            405,
            "method_not_allowed",
        )
