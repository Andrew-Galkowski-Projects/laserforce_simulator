"""L-6 -- a down Celery broker turns a play enqueue into a 503, not a 500.

``enqueue_play_run`` maps a broker failure to ``BrokerUnavailableError`` and
releases the Active play run column. The HTML async play views answer the
tournament Play All 503 JSON shape (``{"error": ...}``); the API answers a 503
``broker_unavailable`` envelope. Live play (sync manager game + background
enqueue) maps the same way.

Only the task's ``apply_async`` (the broker boundary) is patched to raise the
broker exception class; assertions are on status codes and real DB state.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from kombu.exceptions import OperationalError

from matches.models import League, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_teams,
)
from matches.tests.test_api_play_runs import play_round_robin
from matches.tests.test_member_night_views import (
    _member_night_active_season,
    _post_setup,
)

_FAST_TICKS = 20


def _broker_down(task_path: str):
    return patch(
        f"matches.tasks.{task_path}.apply_async",
        side_effect=OperationalError("broker unreachable"),
    )


def _html_season(prefix: str, *, n_teams: int = 2, phases=()) -> Season:
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    teams = []
    for i in range(n_teams):
        team, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(team)
        season.teams.add(team)
    for ordinal, phase_type in enumerate(phases, start=1):
        SeasonPhase.objects.create(
            season=season, ordinal=ordinal, phase_type=phase_type
        )
    season.start_season()
    season.refresh_from_db()
    return season


def _api_season(league, teams, *, phases=()) -> Season:
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


class TestHtmlAsyncPlayBrokerDown(TestCase):
    def _assert_503_and_released(self, response, season: Season) -> None:
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", json.loads(response.content.decode()))
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)

    def test_play_two_months_and_until_end_are_503(self) -> None:
        season = _html_season("BrkSeason")
        for url_name in ("play_two_months", "play_until_end"):
            with _broker_down("play_season_task"):
                response = self.client.post(reverse(url_name, args=[season.id]))
            self._assert_503_and_released(response, season)

    def test_play_playoffs_is_503(self) -> None:
        season = _html_season(
            "BrkPo", n_teams=4, phases=("round_robin", "tournament")
        )
        play_round_robin(season)
        with _broker_down("play_playoffs_task"):
            response = self.client.post(reverse("play_playoffs", args=[season.id]))
        self._assert_503_and_released(response, season)

    def test_member_night_play_all_is_503(self) -> None:
        _league, season, _teams, _mn = _member_night_active_season("BrkMn")
        _post_setup(self.client, season)
        with _broker_down("play_member_night_task"):
            response = self.client.post(
                reverse("play_member_night", args=[season.id])
            )
        self._assert_503_and_released(response, season)

    def test_play_week_live_is_503(self) -> None:
        season = _html_season("BrkLive", n_teams=4)
        league = season.league
        league.current_team = season.teams.order_by("id").first()
        league.save(update_fields=["current_team"])
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with _broker_down("play_season_task"):
                response = self.client.post(
                    reverse("play_week_live", args=[season.id])
                )
        self.assertEqual(response.status_code, 503)
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)


def _action(pk: int, path: str) -> str:
    return f"/api/seasons/{pk}/{path}/"


class TestApiPlayBrokerDown(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("BrkApiL", manager=self.user_a)

    def _assert_503(self, response, season: Season) -> None:
        self.error(response, 503, "broker_unavailable")
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)

    def test_play_two_months_and_until_end_are_503(self) -> None:
        season = _api_season(self.league, make_teams("BrkA", 3))
        for path in ("play-two-months", "play-until-end"):
            with _broker_down("play_season_task"):
                response = self.api.post(_action(season.id, path))
            self._assert_503(response, season)

    def test_play_playoffs_is_503(self) -> None:
        season = _api_season(
            self.league, make_teams("BrkP", 4), phases=("round_robin", "tournament")
        )
        play_round_robin(season)
        with _broker_down("play_playoffs_task"):
            response = self.api.post(_action(season.id, "play-playoffs"))
        self._assert_503(response, season)

    def test_play_live_is_503(self) -> None:
        teams = make_teams("BrkL", 4)
        season = _api_season(self.league, teams)
        self.league.current_team = teams[0]
        self.league.save(update_fields=["current_team"])
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with _broker_down("play_season_task"):
                response = self.api.post(_action(season.id, "play-live"))
        self._assert_503(response, season)
