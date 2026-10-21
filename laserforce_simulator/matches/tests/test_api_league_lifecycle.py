"""ANG-01d-1 -- League lifecycle actions (seam contract section 6, #9-#11).

* ``POST /api/leagues/<pk>/next-season/`` -- 201 ``SeasonSerializer`` of the new
  draft Season; 409 ``season_in_progress`` (field ``season:<id>``) /
  ``no_completed_season`` / ``manager_fired``, none of which create a Season.
* ``GET /api/leagues/<pk>/new-team-candidates/`` -- worst-first standings rows
  (<= 5, current team excluded); 409 ``not_career_league`` /
  ``no_completed_season``.
* ``POST /api/leagues/<pk>/reassign-team/`` -- guard order non-career ->
  in-progress -> no-completed -> ``ReassignTeamForm`` (400 ``team_id``) ->
  ownership 404; 201 + ``current_team`` updated.

Completed Seasons are hand-built with a strict win order (team 0 strongest),
so the worst-N ordering is deterministic. No simulated point totals.
"""

from __future__ import annotations

from matches.models import OwnerEvaluation, Season
from matches.tests.league_api_fixtures import (
    FIRING_NOT_CAREER_MSG,
    MANAGER_FIRED_MSG,
    NO_COMPLETED_MSG,
    REF_KEYS,
    SEASON_DETAIL_KEYS,
    SEASON_IN_PROGRESS_MSG,
    STANDINGS_ROW_KEYS,
    LeagueApiTestCase,
    fired_evaluation,
    make_league,
    make_open_season,
    make_teams,
    ranked_career_league,
)


def url(pk: int, path: str) -> str:
    return f"/api/leagues/{pk}/{path}/"


# ---------------------------------------------------------------------------
# next-season
# ---------------------------------------------------------------------------


class TestNextSeason(LeagueApiTestCase):
    def post(self, league):
        return self.api.post(url(league.id, "next-season"), format="json")

    def test_success_201_draft_season(self) -> None:
        league, season, teams = ranked_career_league("NsOk", 4, manager=self.user_a)
        response = self.post(league)
        data = self.ok(response, 201)
        self.assertEqual(set(data), SEASON_DETAIL_KEYS)
        self.assertEqual(data["state"], "draft")
        self.assertEqual(data["name"], "Season 2")
        self.assertEqual(data["league"], {"id": league.id, "name": league.name})
        self.assertEqual(sorted(data["team_ids"]), sorted(t.id for t in teams))
        self.assertNotIn("active_play_job_id", data)
        self.assertNotIn("play_cancel_requested", data)
        new = Season.objects.get(pk=data["id"])
        self.assertEqual(new.state, "draft")
        self.assertEqual(league.seasons.count(), 2)
        self.assert_session_untouched()

    def test_season_in_progress_409(self) -> None:
        league, _season, teams = ranked_career_league("NsProg", 4, manager=self.user_a)
        draft = make_open_season(league, teams)
        entry = self.error(
            self.post(league), 409, "season_in_progress", f"season:{draft.id}"
        )
        self.assertEqual(entry["message"], SEASON_IN_PROGRESS_MSG)
        self.assertEqual(league.seasons.count(), 2)

    def test_active_season_in_progress_409(self) -> None:
        league, _season, teams = ranked_career_league("NsAct", 4, manager=self.user_a)
        active = make_open_season(league, teams, state="active")
        self.error(self.post(league), 409, "season_in_progress", f"season:{active.id}")

    def test_no_completed_season_409(self) -> None:
        league = make_league("NsNone", manager=self.user_a)
        entry = self.error(self.post(league), 409, "no_completed_season")
        self.assertEqual(entry["message"], NO_COMPLETED_MSG)
        self.assertEqual(league.seasons.count(), 0)

    def test_manager_fired_409_creates_nothing(self) -> None:
        league, season, teams = ranked_career_league("NsFired", 4, manager=self.user_a)
        fired_evaluation(league, season, teams[0])
        entry = self.error(self.post(league), 409, "manager_fired")
        self.assertEqual(entry["message"], MANAGER_FIRED_MSG)
        self.assertEqual(league.seasons.count(), 1)
        self.assertEqual(OwnerEvaluation.objects.filter(league=league).count(), 1)

    def test_fired_but_reassigned_rolls(self) -> None:
        league, season, teams = ranked_career_league("NsReas", 4, manager=self.user_a)
        fired_evaluation(league, season, teams[0])
        league.current_team = teams[-1]
        league.save(update_fields=["current_team"])
        self.ok(self.post(league), 201)
        self.assertEqual(league.seasons.count(), 2)

    def test_non_career_league_rolls(self) -> None:
        league, _season, _teams = ranked_career_league(
            "NsMp", 4, manager=self.user_a, mode="multiplayer"
        )
        self.ok(self.post(league), 201)

    def test_foreign_is_404(self) -> None:
        league, _season, _teams = ranked_career_league(
            "NsForeign", 4, manager=self.user_b
        )
        self.error(self.post(league), 404, "not_found")
        self.assertEqual(league.seasons.count(), 1)

    def test_get_is_405(self) -> None:
        league, _season, _teams = ranked_career_league("NsGet", 4, manager=self.user_a)
        self.error(
            self.api.get(url(league.id, "next-season")), 405, "method_not_allowed"
        )

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        league, _season, _teams = ranked_career_league("NsAnon", 4)
        self.error(
            self.anon.post(url(league.id, "next-season"), format="json"),
            401,
            "not_authenticated",
        )


# ---------------------------------------------------------------------------
# new-team-candidates
# ---------------------------------------------------------------------------


class TestNewTeamCandidates(LeagueApiTestCase):
    def get(self, league):
        return self.api.get(url(league.id, "new-team-candidates"))

    def test_worst_five_excluding_current_team(self) -> None:
        league, season, teams = ranked_career_league(
            "NtcOk", 8, fired=True, manager=self.user_a
        )
        data = self.ok(self.get(league))
        self.assertEqual(set(data), {"latest_completed", "candidates"})
        self.assertEqual(
            data["latest_completed"], {"id": season.id, "name": season.name}
        )
        candidates = data["candidates"]
        self.assertEqual(len(candidates), 5)
        ids = [row["team"]["id"] for row in candidates]
        self.assertNotIn(teams[0].id, ids)
        # Worst-first: the weakest team leads.
        self.assertEqual(ids, [t.id for t in reversed(teams[3:])])
        ranks = [row["rank"] for row in candidates]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        for row in candidates:
            self.assertEqual(set(row), STANDINGS_ROW_KEYS)
            self.assertEqual(set(row["team"]), REF_KEYS)
            self.assertIsInstance(row["match_streak"], list)
        self.assert_session_untouched()

    def test_excludes_current_team_even_when_weakest(self) -> None:
        league, _season, teams = ranked_career_league(
            "NtcWeak", 8, fired=True, manager=self.user_a
        )
        league.current_team = teams[-1]
        league.save(update_fields=["current_team"])
        # The fired evaluation names the team actually managed.
        OwnerEvaluation.objects.filter(league=league).update(team_managed=teams[-1])
        data = self.ok(self.get(league))
        ids = [row["team"]["id"] for row in data["candidates"]]
        self.assertNotIn(teams[-1].id, ids)
        self.assertEqual(len(ids), 5)

    def test_small_league_returns_fewer(self) -> None:
        league, _season, teams = ranked_career_league(
            "NtcSmall", 4, fired=True, manager=self.user_a
        )
        data = self.ok(self.get(league))
        self.assertEqual(len(data["candidates"]), 3)

    def test_non_career_409(self) -> None:
        league, _season, _teams = ranked_career_league(
            "NtcMp", 8, fired=True, manager=self.user_a, mode="multiplayer"
        )
        entry = self.error(self.get(league), 409, "not_career_league")
        self.assertEqual(entry["message"], FIRING_NOT_CAREER_MSG)

    def test_no_completed_409(self) -> None:
        league = make_league("NtcNone", manager=self.user_a)
        self.error(self.get(league), 409, "no_completed_season")

    def test_foreign_is_404(self) -> None:
        league, _season, _teams = ranked_career_league(
            "NtcForeign", 8, fired=True, manager=self.user_b
        )
        self.error(self.get(league), 404, "not_found")

    def test_post_is_405(self) -> None:
        league, _season, _teams = ranked_career_league(
            "NtcPost", 8, fired=True, manager=self.user_a
        )
        self.error(
            self.api.post(url(league.id, "new-team-candidates"), {}, format="json"),
            405,
            "method_not_allowed",
        )


# ---------------------------------------------------------------------------
# reassign-team
# ---------------------------------------------------------------------------


class TestReassignTeam(LeagueApiTestCase):
    def post(self, league, body):
        return self.api.post(url(league.id, "reassign-team"), body, format="json")

    def assert_unchanged(self, league, current_team_id, season_count) -> None:
        league.refresh_from_db()
        self.assertEqual(league.current_team_id, current_team_id)
        self.assertEqual(league.seasons.count(), season_count)

    def test_success_201(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtOk", 8, fired=True, manager=self.user_a
        )
        data = self.ok(self.post(league, {"team_id": teams[-1].id}), 201)
        self.assertEqual(set(data), SEASON_DETAIL_KEYS)
        self.assertEqual(data["state"], "draft")
        league.refresh_from_db()
        self.assertEqual(league.current_team_id, teams[-1].id)
        self.assertEqual(league.seasons.count(), 2)
        self.assert_session_untouched()

    def test_missing_team_id_is_required(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtMiss", 8, fired=True, manager=self.user_a
        )
        self.error(self.post(league, {}), 400, "required", "team_id")
        self.assert_unchanged(league, teams[0].id, 1)

    def test_non_int_team_id_is_invalid_choice(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtStr", 8, fired=True, manager=self.user_a
        )
        self.error(
            self.post(league, {"team_id": "abc"}), 400, "invalid_choice", "team_id"
        )
        self.assert_unchanged(league, teams[0].id, 1)

    def test_ineligible_team_id_is_invalid_choice(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtInel", 8, fired=True, manager=self.user_a
        )
        # teams[0] is the just-left (current) team; teams[1] is not worst-5.
        for team in (teams[0], teams[1]):
            with self.subTest(team=team.name):
                self.error(
                    self.post(league, {"team_id": team.id}),
                    400,
                    "invalid_choice",
                    "team_id",
                )
        self.error(
            self.post(league, {"team_id": 999_999}), 400, "invalid_choice", "team_id"
        )
        self.assert_unchanged(league, teams[0].id, 1)

    def test_foreign_eligible_team_is_404(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtForeign", 8, fired=True, manager=self.user_a
        )
        teams[-1].manager = self.user_b
        teams[-1].save(update_fields=["manager"])
        self.error(self.post(league, {"team_id": teams[-1].id}), 404, "not_found")
        self.assert_unchanged(league, teams[0].id, 1)

    def test_season_in_progress_409(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtProg", 8, fired=True, manager=self.user_a
        )
        draft = make_open_season(league, teams)
        entry = self.error(
            self.post(league, {"team_id": teams[-1].id}),
            409,
            "season_in_progress",
            f"season:{draft.id}",
        )
        self.assertEqual(entry["message"], SEASON_IN_PROGRESS_MSG)
        self.assert_unchanged(league, teams[0].id, 2)

    def test_in_progress_checked_before_body(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtProgBody", 8, fired=True, manager=self.user_a
        )
        make_open_season(league, teams)
        self.error(
            self.post(league, {}),
            409,
            "season_in_progress",
            f"season:{league.active_season.id}",
        )

    def test_non_career_409(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtMp", 8, fired=True, manager=self.user_a, mode="multiplayer"
        )
        entry = self.error(
            self.post(league, {"team_id": teams[-1].id}), 409, "not_career_league"
        )
        self.assertEqual(entry["message"], FIRING_NOT_CAREER_MSG)
        self.assert_unchanged(league, teams[0].id, 1)

    def test_non_career_checked_before_in_progress(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtMpProg", 8, fired=True, manager=self.user_a, mode="multiplayer"
        )
        make_open_season(league, teams)
        self.error(
            self.post(league, {"team_id": teams[-1].id}), 409, "not_career_league"
        )

    def test_no_completed_409(self) -> None:
        (team,) = make_teams("RtNone", 1)
        league = make_league("RtNoneL", manager=self.user_a)
        self.error(self.post(league, {"team_id": team.id}), 409, "no_completed_season")

    def test_non_object_body_is_parse_error(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtArr", 8, fired=True, manager=self.user_a
        )
        self.error(self.post(league, [teams[-1].id]), 400, "parse_error")
        self.assert_unchanged(league, teams[0].id, 1)

    def test_foreign_league_is_404(self) -> None:
        league, _season, teams = ranked_career_league(
            "RtFl", 8, fired=True, manager=self.user_b
        )
        self.error(self.post(league, {"team_id": teams[-1].id}), 404, "not_found")

    def test_get_is_405(self) -> None:
        league, _season, _teams = ranked_career_league(
            "RtGet", 8, fired=True, manager=self.user_a
        )
        self.error(
            self.api.get(url(league.id, "reassign-team")), 405, "method_not_allowed"
        )


# ---------------------------------------------------------------------------
# UXP-03 — a Manager who was not fired cannot pick a new team
# ---------------------------------------------------------------------------


class TestUxp03NotFired(LeagueApiTestCase):
    def test_candidates_409_when_retained(self) -> None:
        league, _season, _teams = ranked_career_league(
            "Uxp03Ntc", 8, manager=self.user_a
        )
        self.error(
            self.api.get(url(league.id, "new-team-candidates")),
            409,
            "manager_not_fired",
        )

    def test_reassign_409_when_retained_and_changes_nothing(self) -> None:
        league, _season, teams = ranked_career_league("Uxp03Rt", 8, manager=self.user_a)
        self.error(
            self.api.post(
                url(league.id, "reassign-team"),
                {"team_id": teams[-1].id},
                format="json",
            ),
            409,
            "manager_not_fired",
        )
        league.refresh_from_db()
        self.assertEqual(league.current_team_id, teams[0].id)
        self.assertEqual(Season.objects.filter(league=league).count(), 1)
