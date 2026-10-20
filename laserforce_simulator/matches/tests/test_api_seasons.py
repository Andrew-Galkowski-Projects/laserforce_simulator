"""ANG-01d-1 -- ``/api/seasons/`` (seam contract section 6, endpoints #12-#18).

List (``league`` / ``state`` filters, ordering, owned + Unmanaged scoping),
retrieve (nested ``phases`` / ``conferences``, no play-state fields), the
Season dashboard, standings (default + ``?sort=&dir=`` + 400s + per-Conference
groups), schedule, awards and the owner-evaluation read. Seasons are scoped
through their League (``path="league"``). Seasons are read-only via the API:
POST / PUT / PATCH / DELETE are 405.

Hand-built fixtures only; no simulated point totals.
"""

from __future__ import annotations

from datetime import date

from django.urls import reverse

from matches.models import OwnerEvaluation, SeasonPhase
from matches.tests.league_api_fixtures import (
    AWARDS_KEYS,
    CONFERENCE_SERIALIZER_KEYS,
    OWNER_EVAL_KEYS,
    PHASE_KEYS,
    PLAY_CONTROL_KEYS,
    REF_KEYS,
    ROLE_KEYS,
    SCHEDULE_FIXTURE_KEYS,
    SEASON_DASHBOARD_KEYS,
    SEASON_DETAIL_KEYS,
    SEASON_LIST_KEYS,
    STANDINGS_ROW_KEYS,
    WINNER_KEYS,
    LeagueApiTestCase,
    add_round_states,
    add_tournament_phase,
    fired_evaluation,
    make_completed_season,
    make_conference,
    make_league,
    make_open_season,
    make_teams,
    ranked_career_league,
)

LIST = "/api/seasons/"


def detail(pk: int) -> str:
    return f"/api/seasons/{pk}/"


def action(pk: int, path: str) -> str:
    return f"/api/seasons/{pk}/{path}/"


class TestRoutes(LeagueApiTestCase):
    def test_route_names(self) -> None:
        self.assertEqual(reverse("season-list"), "/api/seasons/")
        self.assertEqual(reverse("season-detail", args=[3]), "/api/seasons/3/")
        for name, path in (
            ("season-dashboard", "dashboard"),
            ("season-standings", "standings"),
            ("season-schedule", "schedule"),
            ("season-awards", "awards"),
            ("season-owner-evaluation", "owner-evaluation"),
            ("season-conferences", "conferences"),
        ):
            with self.subTest(name=name):
                self.assertEqual(reverse(name, args=[3]), f"/api/seasons/3/{path}/")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestSeasonList(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teams = make_teams("SlT", 2)
        self.own = make_league("SlOwn", manager=self.user_a)
        self.s1 = make_completed_season(self.own, self.teams, ranked=False)
        self.s2 = make_open_season(self.own, self.teams, name="Season 2")
        self.unmanaged = make_league("SlUnm")
        self.s3 = make_completed_season(self.unmanaged, self.teams, ranked=False)
        self.foreign = self.foreign_league("SlForeign")
        self.s4 = make_completed_season(self.foreign, self.teams, ranked=False)

    def ids(self, params: dict | None = None) -> list[int]:
        data, _meta = self.ok_page(self.api.get(LIST, params or {}))
        return [row["id"] for row in data]

    def test_scoping_and_default_order(self) -> None:
        self.assertEqual(self.ids(), [self.s3.id, self.s2.id, self.s1.id])

    def test_row_shape(self) -> None:
        data, _meta = self.ok_page(self.api.get(LIST, {"league": self.own.id}))
        for row in data:
            self.assertEqual(set(row), SEASON_LIST_KEYS)
            self.assertEqual(row["league"], {"id": self.own.id, "name": self.own.name})
            self.assertIsNone(row["champion_team"])

    def test_champion_team_ref(self) -> None:
        self.s1.champion_team = self.teams[0]
        self.s1.save(update_fields=["champion_team"])
        data, _meta = self.ok_page(self.api.get(LIST, {"state": "completed"}))
        row = next(r for r in data if r["id"] == self.s1.id)
        self.assertEqual(
            row["champion_team"], {"id": self.teams[0].id, "name": self.teams[0].name}
        )

    def test_league_and_state_filters(self) -> None:
        self.assertEqual(self.ids({"league": self.own.id}), [self.s2.id, self.s1.id])
        self.assertEqual(self.ids({"state": "draft"}), [self.s2.id])
        self.assertEqual(
            self.ids({"league": self.own.id, "state": "completed"}), [self.s1.id]
        )

    def test_foreign_league_filter_is_empty(self) -> None:
        self.assertEqual(self.ids({"league": self.foreign.id}), [])

    def test_bad_filters_are_400(self) -> None:
        self.error(
            self.api.get(LIST, {"state": "bogus"}), 400, "invalid_choice", "state"
        )
        self.error(self.api.get(LIST, {"league": "abc"}), 400, "invalid", "league")

    def test_ordering_by_start_date(self) -> None:
        self.s1.start_date = date(2030, 1, 1)
        self.s1.save(update_fields=["start_date"])
        ids = self.ids({"ordering": "start_date"})
        self.assertEqual(ids[-1], self.s1.id)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.error(self.anon.get(LIST), 401, "not_authenticated")

    def test_post_is_405(self) -> None:
        self.error(self.api.post(LIST, {}, format="json"), 405, "method_not_allowed")


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


class TestSeasonRetrieve(LeagueApiTestCase):
    def test_nested_phases_and_conferences(self) -> None:
        teams = make_teams("SrT", 4)
        league = make_league("SrL", manager=self.user_a)
        draft = make_open_season(league, teams)
        add_tournament_phase(draft)
        conf_a = make_conference(draft, "West", 1, teams[:2])
        make_conference(draft, "East", 2, teams[2:])
        data = self.ok(self.api.get(detail(draft.id)))
        self.assertEqual(set(data), SEASON_DETAIL_KEYS)
        self.assertNotIn("active_play_job_id", data)
        self.assertNotIn("play_cancel_requested", data)
        self.assertEqual(sorted(data["team_ids"]), sorted(t.id for t in teams))
        self.assertEqual(data["map_pool_ids"], [])
        self.assertEqual(len(data["phases"]), 2)
        for phase in data["phases"]:
            self.assertEqual(set(phase), PHASE_KEYS)
        self.assertEqual(
            [p["phase_type"] for p in data["phases"]], ["round_robin", "tournament"]
        )
        self.assertIsNone(data["phases"][1]["tournament"])
        self.assertEqual([c["name"] for c in data["conferences"]], ["West", "East"])
        first = data["conferences"][0]
        self.assertEqual(set(first), CONFERENCE_SERIALIZER_KEYS)
        self.assertEqual(first["id"], conf_a.id)
        self.assertEqual(sorted(first["team_ids"]), sorted(t.id for t in teams[:2]))

    def test_foreign_and_unknown_are_404(self) -> None:
        teams = make_teams("SrF", 2)
        season = make_completed_season(self.foreign_league("SrFL"), teams, ranked=False)
        self.error(self.api.get(detail(season.id)), 404, "not_found")
        self.error(self.api.get(detail(999_999)), 404, "not_found")

    def test_writes_are_405(self) -> None:
        teams = make_teams("SrW", 2)
        season = make_completed_season(
            make_league("SrWL", manager=self.user_a), teams, ranked=False
        )
        for method in ("put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.api, method)(
                    detail(season.id), {"name": "X"}, format="json"
                )
                self.error(response, 405, "method_not_allowed")
        season.refresh_from_db()
        self.assertEqual(season.name, "Season 1")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestSeasonDashboard(LeagueApiTestCase):
    def test_completed_dashboard(self) -> None:
        league, season, teams = ranked_career_league("SdOk", 4, manager=self.user_a)
        add_round_states(season, teams[0], teams[1])
        data = self.ok(self.api.get(action(season.id, "dashboard")))
        self.assertEqual(set(data), SEASON_DASHBOARD_KEYS)
        self.assertFalse(PLAY_CONTROL_KEYS & set(data))
        self.assertEqual(data["season"], {"id": season.id, "name": season.name})
        self.assertEqual(data["league"], {"id": league.id, "name": league.name})
        self.assertEqual(data["season_mode"], "completed")
        self.assertEqual(len(data["standings_snippet"]), 3)
        self.assert_session_untouched()

    def test_draft_dashboard_mode(self) -> None:
        teams = make_teams("SdDraft", 4)
        draft = make_open_season(make_league("SdDraftL", manager=self.user_a), teams)
        data = self.ok(self.api.get(action(draft.id, "dashboard")))
        self.assertEqual(data["season_mode"], "draft")

    def test_foreign_is_404(self) -> None:
        teams = make_teams("SdF", 2)
        season = make_completed_season(self.foreign_league("SdFL"), teams, ranked=False)
        self.error(self.api.get(action(season.id, "dashboard")), 404, "not_found")


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------


class TestSeasonStandings(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "SsT", 4, manager=self.user_a
        )

    def get(self, params: dict | None = None):
        return self.api.get(action(self.season.id, "standings"), params or {})

    def test_default(self) -> None:
        data = self.ok(self.get())
        self.assertEqual(
            set(data), {"season", "is_draft_preview", "sort", "dir", "groups"}
        )
        self.assertEqual(data["sort"], "rank")
        self.assertEqual(data["dir"], "asc")
        self.assertFalse(data["is_draft_preview"])
        (group,) = data["groups"]
        self.assertEqual(set(group), {"conference", "name", "rows"})
        self.assertIsNone(group["conference"])
        rows = group["rows"]
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(set(row), STANDINGS_ROW_KEYS)
            self.assertIsInstance(row["match_l5"], list)
            self.assertIsInstance(row["red_wlt"], list)
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3, 4])
        self.assertEqual([r["team"]["id"] for r in rows], [t.id for t in self.teams])
        self.assert_session_untouched()

    def test_sort_team_desc_keeps_rank(self) -> None:
        data = self.ok(self.get({"sort": "team", "dir": "desc"}))
        self.assertEqual(data["sort"], "team")
        self.assertEqual(data["dir"], "desc")
        rows = data["groups"][0]["rows"]
        names = [r["team"]["name"] for r in rows]
        self.assertEqual(names, sorted(names, reverse=True))
        rank_by_team = {t.id: i + 1 for i, t in enumerate(self.teams)}
        for row in rows:
            self.assertEqual(row["rank"], rank_by_team[row["team"]["id"]])

    def test_bad_params_are_400(self) -> None:
        self.error(self.get({"sort": "bogus"}), 400, "invalid_choice", "sort")
        self.error(self.get({"dir": "sideways"}), 400, "invalid_choice", "dir")

    def test_per_conference_groups_on_draft(self) -> None:
        teams = make_teams("SsConf", 4)
        draft = make_open_season(make_league("SsConfL", manager=self.user_a), teams)
        west = make_conference(draft, "West", 1, teams[:2])
        east = make_conference(draft, "East", 2, teams[2:])
        data = self.ok(self.api.get(action(draft.id, "standings")))
        self.assertTrue(data["is_draft_preview"])
        groups = data["groups"]
        self.assertEqual(
            [g["conference"] for g in groups],
            [{"id": west.id, "name": "West"}, {"id": east.id, "name": "East"}],
        )
        self.assertEqual([g["name"] for g in groups], ["West", "East"])
        self.assertEqual([len(g["rows"]) for g in groups], [2, 2])
        for group in groups:
            for row in group["rows"]:
                self.assertEqual(set(row), STANDINGS_ROW_KEYS)

    def test_foreign_is_404(self) -> None:
        teams = make_teams("SsF", 2)
        season = make_completed_season(self.foreign_league("SsFL"), teams, ranked=False)
        self.error(self.api.get(action(season.id, "standings")), 404, "not_found")


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


class TestSeasonSchedule(LeagueApiTestCase):
    def test_draft_schedule_shape(self) -> None:
        teams = make_teams("SchT", 4)
        draft = make_open_season(make_league("SchL", manager=self.user_a), teams)
        data = self.ok(self.api.get(action(draft.id, "schedule")))
        self.assertEqual(set(data), {"season", "matchdays", "member_night_games"})
        self.assertEqual(data["season"], {"id": draft.id, "name": draft.name})
        self.assertTrue(data["matchdays"])
        self.assertEqual(data["member_night_games"], [])
        for matchday in data["matchdays"]:
            self.assertEqual(set(matchday), {"matchday", "date", "fixtures"})
            self.assertIsInstance(matchday["date"], str)
            for fixture in matchday["fixtures"]:
                self.assertEqual(set(fixture), SCHEDULE_FIXTURE_KEYS)
                self.assertEqual(set(fixture["team_a"]), REF_KEYS)
                self.assertEqual(set(fixture["team_b"]), REF_KEYS)
                self.assertEqual(fixture["team_a"]["id"], fixture["team_a_id"])
        self.assert_session_untouched()

    def test_completed_schedule_played(self) -> None:
        _league, season, _teams = ranked_career_league(
            "SchDone", 4, manager=self.user_a
        )
        data = self.ok(self.api.get(action(season.id, "schedule")))
        fixtures = [f for md in data["matchdays"] for f in md["fixtures"]]
        self.assertTrue(all(f["played"] for f in fixtures))
        self.assertTrue(all(f["game_round_id"] for f in fixtures))

    def test_single_team_has_no_matchdays(self) -> None:
        teams = make_teams("SchOne", 1)
        draft = make_open_season(make_league("SchOneL", manager=self.user_a), teams)
        data = self.ok(self.api.get(action(draft.id, "schedule")))
        self.assertEqual(data["matchdays"], [])


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------


class TestSeasonAwards(LeagueApiTestCase):
    def test_awards_with_rounds(self) -> None:
        teams = make_teams("AwT", 2)
        season = make_completed_season(
            make_league("AwL", manager=self.user_a), teams, ranked=False
        )
        add_round_states(season, teams[0], teams[1])
        data = self.ok(self.api.get(action(season.id, "awards")))
        self.assertEqual(set(data), AWARDS_KEYS)
        self.assertTrue(data["has_rounds"])
        self.assertEqual(set(data["kd_by_role"]), set(ROLE_KEYS))
        self.assertEqual(set(data["most_points"]), WINNER_KEYS)
        self.assertIsNone(data["finals_mvp"])
        for winner in data["kd_by_role"].values():
            if winner is not None:
                self.assertEqual(set(winner), WINNER_KEYS)

    def test_awards_without_rounds(self) -> None:
        teams = make_teams("AwNone", 2)
        season = make_completed_season(
            make_league("AwNoneL", manager=self.user_a), teams, ranked=False
        )
        data = self.ok(self.api.get(action(season.id, "awards")))
        self.assertFalse(data["has_rounds"])
        self.assertIsNone(data["most_points"])
        self.assertEqual(set(data["kd_by_role"]), set(ROLE_KEYS))
        self.assertTrue(all(v is None for v in data["kd_by_role"].values()))


# ---------------------------------------------------------------------------
# Owner evaluation
# ---------------------------------------------------------------------------


class TestSeasonOwnerEvaluation(LeagueApiTestCase):
    def get(self, season):
        return self.api.get(action(season.id, "owner-evaluation"))

    def test_completed_career_writes_row(self) -> None:
        league, season, teams = ranked_career_league("OeOk", 4, manager=self.user_a)
        self.assertEqual(OwnerEvaluation.objects.count(), 0)
        data = self.ok(self.get(season))
        self.assertEqual(set(data), OWNER_EVAL_KEYS)
        self.assertEqual(OwnerEvaluation.objects.filter(season=season).count(), 1)
        self.assertEqual(data["season"], {"id": season.id, "name": season.name})
        self.assertEqual(data["league"], {"id": league.id, "name": league.name})
        self.assertEqual(
            data["team_managed"], {"id": teams[0].id, "name": teams[0].name}
        )
        self.assertAlmostEqual(
            data["overall_mood"],
            data["wins_total"] + data["playoffs_total"] + data["money_total"],
        )
        self.assertIn(data["verdict"], {"retained", "hot_seat", "fired"})
        self.assertIsInstance(data["is_fired"], bool)
        self.assertIsInstance(data["reassigned"], bool)
        self.assert_session_untouched()

    def test_fired_and_reassigned_flags(self) -> None:
        league, season, teams = ranked_career_league("OeFired", 4, manager=self.user_a)
        fired_evaluation(league, season, teams[0])
        data = self.ok(self.get(season))
        self.assertTrue(data["is_fired"])
        self.assertFalse(data["reassigned"])
        self.assertEqual(data["verdict"], "fired")
        self.assertEqual(data["fired_reason"], "owner_mood")
        league.current_team = teams[-1]
        league.save(update_fields=["current_team"])
        data = self.ok(self.get(season))
        self.assertTrue(data["reassigned"])

    def test_draft_is_404(self) -> None:
        league, _season, teams = ranked_career_league("OeDraft", 4, manager=self.user_a)
        draft = make_open_season(league, teams)
        self.error(self.get(draft), 404, "not_found")

    def test_non_career_is_404(self) -> None:
        _league, season, _teams = ranked_career_league(
            "OeMp", 4, manager=self.user_a, mode="multiplayer"
        )
        self.error(self.get(season), 404, "not_found")
        self.assertEqual(OwnerEvaluation.objects.count(), 0)

    def test_foreign_is_404(self) -> None:
        _league, season, _teams = ranked_career_league(
            "OeForeign", 4, manager=self.user_b
        )
        self.error(self.get(season), 404, "not_found")
        self.assertEqual(OwnerEvaluation.objects.count(), 0)

    def test_post_is_405(self) -> None:
        _league, season, _teams = ranked_career_league("OePost", 4, manager=self.user_a)
        self.error(
            self.api.post(action(season.id, "owner-evaluation"), {}, format="json"),
            405,
            "method_not_allowed",
        )


class TestSeasonPhaseSerializerTournamentFk(LeagueApiTestCase):
    """``SeasonPhaseSerializer.tournament`` is a pk (null until built)."""

    def test_unbuilt_tournament_phase_is_null(self) -> None:
        teams = make_teams("SpT", 4)
        draft = make_open_season(make_league("SpL", manager=self.user_a), teams)
        SeasonPhase.objects.create(season=draft, ordinal=1, phase_type="round_robin")
        data = self.ok(self.api.get(detail(draft.id)))
        (phase,) = data["phases"]
        self.assertIsNone(phase["tournament"])
        self.assertEqual(phase["ordinal"], 1)
