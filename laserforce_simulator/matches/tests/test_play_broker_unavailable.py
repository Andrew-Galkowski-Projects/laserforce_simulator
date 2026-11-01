"""L-6 -- a down Celery broker turns a play enqueue into a 503, not a 500.

``enqueue_play_run`` maps a broker failure to ``BrokerUnavailableError`` and
releases the Active play run column. The API answers a 503
``broker_unavailable`` envelope. Live play (sync manager game + background
enqueue) maps the same way.

Only the task's ``apply_async`` (the broker boundary) is patched to raise the
broker exception class; assertions are on status codes and real DB state.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from kombu.exceptions import OperationalError

from matches.league_play_services import (
    BrokerUnavailableError,
    _ensure_broker_reachable,
)
from matches.models import GameRound, Match, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_teams,
)
from matches.tests.test_api_play_runs import play_round_robin

_FAST_TICKS = 20


def _broker_down(task_path: str):
    return patch(
        f"matches.tasks.{task_path}.apply_async",
        side_effect=OperationalError("broker unreachable"),
    )


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

    def test_member_night_play_is_503(self) -> None:
        from matches.tests.test_api_member_night import mn_season

        season = mn_season(self.league, "BrkMnApi")
        self.ok(self.api.post(_action(season.id, "member-night"), {"sites": ["SiteA"]}))
        with _broker_down("play_member_night_task"):
            response = self.api.post(_action(season.id, "member-night/play"))
        self._assert_503(response, season)
        self.assertFalse(
            Match.objects.filter(season=season, is_completed=True).exists()
        )


def _broker_unreachable():
    """Patch the pre-commit broker probe to report the broker down."""
    return patch(
        "matches.league_play_services._ensure_broker_reachable",
        side_effect=BrokerUnavailableError(),
    )


def _season_rounds(season: Season) -> int:
    return GameRound.objects.filter(match__season=season).count()


class TestLiveBrokerDownCommitsNothing(LeagueApiTestCase):
    """L-7 -- an unreachable broker fails live play BEFORE the manager's game
    is committed, so the week is never left half-played."""

    def _live_season(self, prefix: str) -> Season:
        teams = make_teams(prefix, 4)
        league = make_league(f"{prefix}L", manager=self.user_a)
        league.current_team = teams[0]
        league.save(update_fields=["current_team"])
        return _api_season(league, teams)

    def test_api_live_commits_no_round(self) -> None:
        season = self._live_season("L7Api")
        with _broker_unreachable():
            response = self.api.post(_action(season.id, "play-live"))
        self.error(response, 503, "broker_unavailable")
        self.assertEqual(_season_rounds(season), 0)


def _stub_task(*, eager: bool, ensure_error: Exception | None = None):
    """A task stand-in exposing only the ``app`` surface the probe reads."""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.ensure_connection.side_effect = ensure_error
    app = MagicMock()
    app.conf.task_always_eager = eager
    app.connection_for_write.return_value = conn
    return SimpleNamespace(app=app), conn


class TestEnsureBrokerReachable(TestCase):
    """The probe is a no-op under eager Celery and maps a broker connection
    failure to ``BrokerUnavailableError`` otherwise."""

    def test_eager_is_a_no_op(self) -> None:
        task, _conn = _stub_task(eager=True)
        _ensure_broker_reachable(task)
        task.app.connection_for_write.assert_not_called()

    def test_unreachable_broker_raises(self) -> None:
        task, _conn = _stub_task(eager=False, ensure_error=OperationalError("down"))
        with self.assertRaises(BrokerUnavailableError):
            _ensure_broker_reachable(task)

    def test_reachable_broker_passes(self) -> None:
        task, conn = _stub_task(eager=False)
        _ensure_broker_reachable(task)
        conn.ensure_connection.assert_called_once()
