"""ANG-01d-2 T2 -- Start Season + synchronous play over ``/api/``.

Seam 1 (``/api/``, DRF ``APIClient``): ``POST /api/seasons/<id>/start/`` /
``play-week/`` / ``play-single-round/`` return 200 with the same body as
``GET .../dashboard/``. Errors are 409 ``season_not_draft`` /
``season_not_active`` / ``wrong_phase`` / ``play_in_progress``; cross-Account
404, anonymous 403.

Runs against the real simulator (``ROUND_TICKS`` shortened); assertions are on
status codes, envelope shape, Season state and persisted rows -- never on
simulated point totals.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from matches.models import GameRound, Season
from matches.simulation import BatchSimulator
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_teams,
)
from matches.tests.test_api_play_runs import active_season, play_round_robin

_FAST_TICKS = 20


def action(pk: int, path: str) -> str:
    return f"/api/seasons/{pk}/{path}/"


def draft_season(league, teams) -> Season:
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    season.teams.add(*teams)
    return season


class TestStartSeason(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("StL", manager=self.user_a)
        self.teams = make_teams("St", 3)

    def test_starts_a_draft_and_returns_the_dashboard_payload(self) -> None:
        season = draft_season(self.league, self.teams)
        data = self.ok(self.api.post(action(season.id, "start")))
        dashboard = self.ok(self.api.get(action(season.id, "dashboard")))
        self.assertEqual(data, dashboard)
        self.assertEqual(data["season_mode"], "active")
        season.refresh_from_db()
        self.assertEqual(season.state, "active")
        self.assert_session_untouched()

    def test_an_active_season_is_409_and_is_not_re_snapshotted(self) -> None:
        season = active_season(self.league, self.teams)
        snapshot = season.starting_team_ids_json
        season.teams.add(make_teams("StLate", 1)[0])
        self.error(self.api.post(action(season.id, "start")), 409, "season_not_draft")
        season.refresh_from_db()
        self.assertEqual(season.starting_team_ids_json, snapshot)

    def test_a_completed_season_is_409_and_stays_completed(self) -> None:
        season = active_season(self.league, self.teams)
        Season.objects.filter(pk=season.pk).update(state="completed")
        self.error(self.api.post(action(season.id, "start")), 409, "season_not_draft")
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")

    def test_a_draft_with_one_team_is_400_and_stays_draft(self) -> None:
        season = draft_season(self.league, self.teams[:1])
        errors = self.errors(self.api.post(action(season.id, "start")), 400)
        self.assertEqual(errors[0]["code"], "invalid")
        season.refresh_from_db()
        self.assertEqual(season.state, "draft")

    def test_cross_account_is_404_and_anonymous_is_403(self) -> None:
        foreign = draft_season(self.foreign_league("StF"), make_teams("StF", 2))
        self.error(self.api.post(action(foreign.id, "start")), 404, "not_found")
        self.error(
            self.anon.post(action(foreign.id, "start")), 403, "not_authenticated"
        )
        foreign.refresh_from_db()
        self.assertEqual(foreign.state, "draft")


class TestPlayWeek(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("PwL", manager=self.user_a)

    def test_plays_one_matchday_and_returns_the_dashboard_payload(self) -> None:
        season = active_season(self.league, make_teams("Pw", 4))
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            data = self.ok(self.api.post(action(season.id, "play-week")))
        # Matchday 1 of a 4-team round robin = 2 Rounds.
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 2)
        self.assertEqual(data, self.ok(self.api.get(action(season.id, "dashboard"))))
        self.assertEqual(data["round_count_completed"], 2)
        self.assert_session_untouched()

    def test_a_draft_season_is_409_season_not_active(self) -> None:
        season = draft_season(self.league, make_teams("PwD", 2))
        self.error(
            self.api.post(action(season.id, "play-week")), 409, "season_not_active"
        )

    def test_while_a_run_is_live_is_409_and_plays_nothing(self) -> None:
        season = active_season(self.league, make_teams("PwB", 2))
        Season.objects.filter(pk=season.pk).update(active_play_job_id="live")
        self.error(
            self.api.post(action(season.id, "play-week")),
            409,
            "play_in_progress",
            "job:live",
        )
        self.assertFalse(GameRound.objects.filter(match__season=season).exists())

    def test_cross_account_is_404_and_anonymous_is_403(self) -> None:
        foreign = active_season(self.foreign_league("PwF"), make_teams("PwF", 2))
        self.error(self.api.post(action(foreign.id, "play-week")), 404, "not_found")
        self.error(
            self.anon.post(action(foreign.id, "play-week")), 403, "not_authenticated"
        )
        self.assertFalse(GameRound.objects.exists())


class TestPlaySingleRound(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("PsrL", manager=self.user_a)
        self.season = active_season(
            self.league, make_teams("Psr", 4), phases=("round_robin", "tournament")
        )

    def test_plays_one_bracket_match_and_returns_the_dashboard_payload(self) -> None:
        play_round_robin(self.season)
        rr_rounds = GameRound.objects.count()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            data = self.ok(self.api.post(action(self.season.id, "play-single-round")))
        # One bracket Match = 2 Rounds.
        self.assertEqual(GameRound.objects.count(), rr_rounds + 2)
        self.assertEqual(
            data, self.ok(self.api.get(action(self.season.id, "dashboard")))
        )

    def test_before_the_bracket_is_built_is_409_wrong_phase(self) -> None:
        self.error(
            self.api.post(action(self.season.id, "play-single-round")),
            409,
            "wrong_phase",
        )

    def test_a_draft_season_is_409_season_not_active(self) -> None:
        draft = draft_season(make_league("PsrD", manager=self.user_a), [])
        self.error(
            self.api.post(action(draft.id, "play-single-round")),
            409,
            "season_not_active",
        )

    def test_while_a_run_is_live_is_409_play_in_progress(self) -> None:
        play_round_robin(self.season)
        rounds = GameRound.objects.count()
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="busy")
        self.error(
            self.api.post(action(self.season.id, "play-single-round")),
            409,
            "play_in_progress",
            "job:busy",
        )
        self.assertEqual(GameRound.objects.count(), rounds)

    def test_cross_account_is_404_and_anonymous_is_403(self) -> None:
        foreign = active_season(self.foreign_league("PsrF"), make_teams("PsrF", 2))
        self.error(
            self.api.post(action(foreign.id, "play-single-round")), 404, "not_found"
        )
        self.error(
            self.anon.post(action(foreign.id, "play-single-round")),
            403,
            "not_authenticated",
        )
