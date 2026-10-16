"""ANG-01d-2 T3 -- One Week (Live) + the play controls over ``/api/``.

Seam 1 (``/api/``, DRF ``APIClient``): ``POST /api/seasons/<id>/play-live/``
commits the manager's own game and returns ``{kind, round_ids, job}`` (``job`` a
Job ref or null); the rest of the week runs as the Season's Active play run.
``GET /api/leagues/<id>/play-controls/`` returns the structured play controls
for the displayed Season. Errors are 409 ``season_not_active`` /
``play_in_progress`` / ``no_live_cursor``; cross-Account 404, anonymous 403.

The manager's game runs against the real simulator (``ROUND_TICKS`` shortened).
The background "rest of the week" Job is stubbed at ``apply_async`` where the
test is about the Job id left on the Season (an eager task would clear it).
Assertions are on status codes, envelope shape, the Season's run-state columns
and persisted rows -- never on simulated point totals.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.urls import reverse

from matches import league_play_services
from matches.league_play_services import LivePlayResult
from matches.models import GameRound, Match, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_teams,
)
from teams.models import Player, Team

_FAST_TICKS = 20
JOB_REF_KEYS = {"job_id", "season_id", "status_url"}
LIVE_KEYS = {"kind", "round_ids", "job"}
CONTROL_KEYS = {
    "action_button_label",
    "action_button_state",
    "playoff_phase_active",
    "playoff_tournament_id",
    "playoff_completed",
    "has_following_tournament_phase",
    "following_tournament_is_final",
    "live_preview_available",
    "is_career_mode",
    "member_night_phase_active",
    "member_night_sites",
    "member_night_has_unplayed",
    "active_play_job_id",
}


def live_url(pk: int) -> str:
    return f"/api/seasons/{pk}/play-live/"


def controls_url(pk: int) -> str:
    return f"/api/leagues/{pk}/play-controls/"


def active_season(league, teams, *, phases: tuple[str, ...] = ()) -> Season:
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    season.teams.add(*teams)
    for ordinal, phase_type in enumerate(phases, start=1):
        SeasonPhase.objects.create(
            season=season, ordinal=ordinal, phase_type=phase_type
        )
    season.start_season()
    season.refresh_from_db()
    return season


def set_manager_team(season: Season, team) -> None:
    season.league.current_team = team
    season.league.save(update_fields=["current_team"])


def play_round_robin(season: Season) -> None:
    """Play every RR fixture so the tournament phase auto-builds."""
    teams = {t.id: t for t in season.teams.all()}
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                sim.simulate_scheduled_round(
                    season,
                    teams[fixture.team_a_id],
                    teams[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=phase,
                )


def stub_background_job():
    """Keep the "rest of the week" Job from running (and clearing its id)."""
    return patch("matches.tasks.play_season_task.apply_async")


class TestPlayLiveRoundRobin(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("LiveL", manager=self.user_a)
        self.teams = make_teams("Live", 4)
        self.season = active_season(self.league, self.teams)
        set_manager_team(self.season, self.teams[0])

    def post_live(self):
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            return self.api.post(live_url(self.season.id))

    def test_commits_the_managers_round_and_returns_a_job_ref(self) -> None:
        data = self.ok(self.post_live())
        self.assertEqual(set(data), LIVE_KEYS)
        self.assertEqual(data["kind"], "rr")
        self.assertEqual(len(data["round_ids"]), 1)
        watched = GameRound.objects.get(pk=data["round_ids"][0])
        self.assertEqual(watched.match.season_id, self.season.id)
        self.assertIn(self.teams[0].id, (watched.team_red_id, watched.team_blue_id))
        self.assertEqual(watched.fidelity, "full")
        self.assertEqual(set(data["job"]), JOB_REF_KEYS)
        self.assertEqual(data["job"]["season_id"], self.season.id)
        self.assertEqual(
            data["job"]["status_url"],
            reverse("season-play-job", args=[self.season.id, data["job"]["job_id"]]),
        )

    def test_leaves_the_background_job_id_on_the_season(self) -> None:
        with stub_background_job() as apply_async:
            data = self.ok(self.post_live())
        self.season.refresh_from_db()
        self.assertEqual(self.season.active_play_job_id, data["job"]["job_id"])
        self.assertFalse(self.season.play_cancel_requested)
        self.assertEqual(apply_async.call_count, 1)
        self.assertEqual(
            apply_async.call_args.kwargs["kwargs"],
            {"season_id": self.season.id, "max_matchdays": 1},
        )

    def test_returned_rounds_replay_through_the_events_endpoint(self) -> None:
        data = self.ok(self.post_live())
        events, meta = self.ok_page(
            self.api.get(f"/api/rounds/{data['round_ids'][0]}/events/")
        )
        self.assertGreater(meta["count"], 0)
        self.assertTrue(events)

    def test_a_live_run_is_409_play_in_progress_and_commits_nothing(self) -> None:
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="busy")
        self.error(self.post_live(), 409, "play_in_progress", "job:busy")
        self.assertFalse(GameRound.objects.filter(match__season=self.season).exists())

    def test_draft_season_is_409_season_not_active(self) -> None:
        draft = Season.objects.create(
            league=make_league("LiveDraftL", manager=self.user_a),
            name="D",
            start_date=date(2026, 6, 1),
        )
        draft.teams.add(*make_teams("LiveDraft", 2))
        self.error(self.api.post(live_url(draft.id)), 409, "season_not_active")

    def test_no_manager_team_is_409_no_live_cursor(self) -> None:
        set_manager_team(self.season, None)
        self.error(self.post_live(), 409, "no_live_cursor")
        self.assertFalse(GameRound.objects.filter(match__season=self.season).exists())

    def test_cross_account_is_404_and_anonymous_is_403(self) -> None:
        foreign = active_season(
            self.foreign_league("LiveForeign"), make_teams("LiveF", 2)
        )
        self.error(self.api.post(live_url(foreign.id)), 404, "not_found")
        self.error(self.anon.post(live_url(self.season.id)), 403, "not_authenticated")

    def test_get_is_405(self) -> None:
        self.error(self.api.get(live_url(self.season.id)), 405, "method_not_allowed")


class TestPlayLiveBye(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("LiveByeL", manager=self.user_a)
        self.teams = make_teams("Bye", 3)
        self.season = active_season(self.league, self.teams)
        bye_team = next(
            team for team in self.teams if self._cursor_kind_for(team) == "rr_bye"
        )
        set_manager_team(self.season, bye_team)

    def _cursor_kind_for(self, team) -> str | None:
        set_manager_team(self.season, team)
        self.season.refresh_from_db()
        cursor = league_play_services.resolve_live_cursor(self.season)
        return cursor and cursor["kind"]

    def test_a_bye_starts_only_the_job(self) -> None:
        with stub_background_job():
            data = self.ok(self.api.post(live_url(self.season.id)))
        self.assertEqual(data["kind"], "rr")
        self.assertEqual(data["round_ids"], [])
        self.assertEqual(set(data["job"]), JOB_REF_KEYS)
        self.assertFalse(GameRound.objects.filter(match__season=self.season).exists())
        self.season.refresh_from_db()
        self.assertEqual(self.season.active_play_job_id, data["job"]["job_id"])


class TestPlayLivePlayoff(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("LivePoL", manager=self.user_a)
        self.teams = make_teams("LivePo", 4)
        self.season = active_season(
            self.league, self.teams, phases=("round_robin", "tournament")
        )
        play_round_robin(self.season)
        self.season.refresh_from_db()
        set_manager_team(self.season, self.teams[0])

    def test_commits_the_managers_two_round_playoff_match(self) -> None:
        with stub_background_job():
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                data = self.ok(self.api.post(live_url(self.season.id)))
        self.assertEqual(data["kind"], "playoff")
        self.assertEqual(len(data["round_ids"]), 2)
        rounds = GameRound.objects.filter(pk__in=data["round_ids"])
        self.assertEqual(rounds.count(), 2)
        self.assertEqual({gr.match_id for gr in rounds}.__len__(), 1)
        for gr in rounds:
            self.assertIn(self.teams[0].id, (gr.team_red_id, gr.team_blue_id))
            self.assertEqual(gr.fidelity, "full")
        self.assertEqual(set(data["job"]), JOB_REF_KEYS)


class TestLivePlayResult(LeagueApiTestCase):
    def test_is_an_immutable_value(self) -> None:
        result = LivePlayResult(kind="rr", round_ids=[1], job_id="j")
        with self.assertRaises(AttributeError):
            result.kind = "playoff"  # type: ignore[misc]
        self.assertEqual(result, LivePlayResult(kind="rr", round_ids=[1], job_id="j"))

    def test_play_live_returns_the_value(self) -> None:
        league = make_league("LiveSvcL", manager=self.user_a)
        teams = make_teams("LiveSvc", 4)
        season = active_season(league, teams)
        set_manager_team(season, teams[0])
        with stub_background_job():
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                result = league_play_services.play_live(season)
        self.assertIsInstance(result, LivePlayResult)
        self.assertEqual(result.kind, "rr")
        self.assertEqual(len(result.round_ids), 1)
        self.assertIsInstance(result.job_id, str)


class TestPlayControls(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("CtlL", manager=self.user_a)
        self.teams = make_teams("Ctl", 4)
        self.season = active_season(self.league, self.teams)
        set_manager_team(self.season, self.teams[0])

    def test_returns_the_structured_controls_for_the_displayed_season(self) -> None:
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="job-1")
        data = self.ok(self.api.get(controls_url(self.league.id)))
        self.assertEqual(set(data), CONTROL_KEYS)
        self.assertEqual(data["action_button_state"], "play_next")
        self.assertEqual(data["action_button_label"], "Play Next")
        self.assertTrue(data["live_preview_available"])
        self.assertTrue(data["is_career_mode"])
        self.assertEqual(data["active_play_job_id"], "job-1")
        self.assertFalse(data["member_night_phase_active"])
        self.assertEqual(data["member_night_sites"], [])

    def test_no_season_reads_as_none(self) -> None:
        league = make_league("CtlNoneL", manager=self.user_a)
        data = self.ok(self.api.get(controls_url(league.id)))
        self.assertEqual(data["action_button_state"], "none")
        self.assertIsNone(data["active_play_job_id"])

    def test_member_night_sites_are_site_count_objects(self) -> None:
        league = make_league("CtlMnL", manager=self.user_a)
        teams = make_teams("CtlMn", 2)
        Player.objects.filter(team__in=teams).update(home_site="SiteA")
        active_season(league, teams, phases=("member_night",))
        data = self.ok(self.api.get(controls_url(league.id)))
        self.assertTrue(data["member_night_phase_active"])
        self.assertEqual(data["member_night_sites"], [{"site": "SiteA", "count": 12}])

    def test_cross_account_is_404_and_anonymous_is_403(self) -> None:
        foreign = self.foreign_league("CtlForeign")
        self.error(self.api.get(controls_url(foreign.id)), 404, "not_found")
        self.error(
            self.anon.get(controls_url(self.league.id)), 403, "not_authenticated"
        )


class TestBuildPlayControls(LeagueApiTestCase):
    def test_sites_are_site_count_dicts(self) -> None:
        league = make_league("BpcL", manager=self.user_a)
        teams = make_teams("Bpc", 2)
        Player.objects.filter(team__in=teams).update(home_site="SiteB")
        season = active_season(league, teams, phases=("member_night",))
        mn = season.current_phase()
        Match.objects.create(
            season=season,
            season_phase=mn,
            team_red=Team.objects.create(name="Bpc Draw A", is_draw_team=True),
            team_blue=Team.objects.create(name="Bpc Draw B", is_draw_team=True),
        )
        controls = league_play_services.build_play_controls(league, season)
        self.assertEqual(set(controls), CONTROL_KEYS)
        self.assertEqual(
            controls["member_night_sites"], [{"site": "SiteB", "count": 12}]
        )
        self.assertTrue(controls["member_night_has_unplayed"])
