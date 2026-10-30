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

from datetime import date

from django.urls import reverse

from laserforce_simulator.testing import assert_owner_only
from matches.league_templates import LEAGUE_TEMPLATES
from matches.models import Conference, League, Match, Season
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
    make_completed_season,
    make_league,
    make_open_season,
    make_teams,
    ranked_career_league,
)
from matches.tests.test_api_member_night import mn_season

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
        self.assertEqual(set(data), {"league", "displayed_season", "schedule_team"})
        return data

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


class TestLeagueCreate(LeagueApiTestCase):
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
            self.assertIsInstance(row["season_url"], str)
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
