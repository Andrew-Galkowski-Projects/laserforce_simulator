"""ANG-01d-2 T1 -- the Active play run guard + async play API (ADR-0040).

Seam 1 (``/api/``, DRF ``APIClient``): ``POST /api/seasons/<id>/play-two-months/``
/ ``play-until-end/`` / ``play-playoffs/`` return a Job ref, ``play-cancel/``
returns ``{cancel_requested}``, ``GET play-jobs/<job_id>/`` the structured Job
status. Errors are 409 ``season_not_active`` / ``wrong_phase`` /
``play_in_progress`` (``field = job:<id>``); cross-Account 404, anonymous 401 (ADR-0041).

Runs under eager Celery against the real simulator (``ROUND_TICKS`` shortened);
assertions are on status codes, envelope shape, the Season's run-state columns
and persisted rows -- never on simulated point totals.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from matches.models import GameRound, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_teams,
)

_FAST_TICKS = 20
JOB_REF_KEYS = {"job_id", "season_id", "status_url"}
JOB_STATUS_KEYS = {"status", "completed", "total", "cancelled", "error"}


def action(pk: int, path: str) -> str:
    return f"/api/seasons/{pk}/{path}/"


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


class TestPlayTwoMonths(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("PtmL", manager=self.user_a)
        self.season = active_season(self.league, make_teams("Ptm", 3))

    def test_returns_job_ref_and_clears_the_run_after_the_eager_job(self) -> None:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            data = self.ok(self.api.post(action(self.season.id, "play-two-months")))
        self.assertEqual(set(data), JOB_REF_KEYS)
        self.assertEqual(data["season_id"], self.season.id)
        self.assertEqual(
            data["status_url"],
            reverse("season-play-job", args=[self.season.id, data["job_id"]]),
        )
        self.assertTrue(GameRound.objects.filter(match__season=self.season).exists())
        self.season.refresh_from_db()
        self.assertIsNone(self.season.active_play_job_id)

    def test_a_second_start_while_a_run_is_live_is_409_play_in_progress(self) -> None:
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="live-job")
        err = self.error(
            self.api.post(action(self.season.id, "play-two-months")),
            409,
            "play_in_progress",
            "job:live-job",
        )
        self.assertTrue(err["message"])
        self.assertFalse(GameRound.objects.filter(match__season=self.season).exists())
        self.season.refresh_from_db()
        self.assertEqual(self.season.active_play_job_id, "live-job")

    def test_draft_season_is_409_season_not_active(self) -> None:
        draft = Season.objects.create(
            league=self.league, name="S2", start_date=date(2027, 1, 1)
        )
        self.error(
            self.api.post(action(draft.id, "play-two-months")),
            409,
            "season_not_active",
        )
        draft.refresh_from_db()
        self.assertIsNone(draft.active_play_job_id)

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = active_season(self.foreign_league("PtmF"), make_teams("PtmF", 2))
        self.error(
            self.api.post(action(foreign.id, "play-two-months")), 404, "not_found"
        )
        self.error(
            self.anon.post(action(self.season.id, "play-two-months")),
            401,
            "not_authenticated",
        )
        self.assertFalse(GameRound.objects.exists())


class TestPlayUntilEnd(LeagueApiTestCase):
    def test_plays_the_regular_season_out_and_clears_the_run(self) -> None:
        league = make_league("PueL", manager=self.user_a)
        season = active_season(league, make_teams("Pue", 3))
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            data = self.ok(self.api.post(action(season.id, "play-until-end")))
        self.assertEqual(set(data), JOB_REF_KEYS)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNone(season.active_play_job_id)
        status = self.ok(self.api.get(data["status_url"]))
        self.assertEqual(set(status), JOB_STATUS_KEYS)
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["completed"], status["total"])
        self.assertGreater(status["total"], 0)
        self.assertFalse(status["cancelled"])
        self.assertIsNone(status["error"])


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


class TestPlayPlayoffs(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        league = make_league("PpoL", manager=self.user_a)
        self.season = active_season(
            league, make_teams("Ppo", 4), phases=("round_robin", "tournament")
        )

    def test_before_the_bracket_is_built_is_409_wrong_phase(self) -> None:
        self.error(
            self.api.post(action(self.season.id, "play-playoffs")), 409, "wrong_phase"
        )
        self.season.refresh_from_db()
        self.assertIsNone(self.season.active_play_job_id)

    def test_drains_the_bracket_to_a_champion_and_clears_the_run(self) -> None:
        play_round_robin(self.season)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            data = self.ok(self.api.post(action(self.season.id, "play-playoffs")))
        self.assertEqual(set(data), JOB_REF_KEYS)
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertIsNotNone(self.season.champion_team_id)
        self.assertIsNone(self.season.active_play_job_id)

    def test_while_a_run_is_live_is_409_play_in_progress(self) -> None:
        play_round_robin(self.season)
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="busy")
        self.error(
            self.api.post(action(self.season.id, "play-playoffs")),
            409,
            "play_in_progress",
            "job:busy",
        )


class TestPlayCancel(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        league = make_league("PcL", manager=self.user_a)
        self.season = active_season(league, make_teams("Pc", 2))

    def test_with_a_live_run_sets_the_flag(self) -> None:
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="run-1")
        data = self.ok(self.api.post(action(self.season.id, "play-cancel")))
        self.assertEqual(data, {"cancel_requested": True})
        self.season.refresh_from_db()
        self.assertTrue(self.season.play_cancel_requested)

    def test_without_a_run_is_a_harmless_no_op(self) -> None:
        data = self.ok(self.api.post(action(self.season.id, "play-cancel")))
        self.assertEqual(data, {"cancel_requested": False})
        self.season.refresh_from_db()
        self.assertFalse(self.season.play_cancel_requested)

    def test_cross_account_is_404(self) -> None:
        foreign = active_season(self.foreign_league("PcF"), make_teams("PcF", 2))
        self.error(self.api.post(action(foreign.id, "play-cancel")), 404, "not_found")


class TestPlayJobs(LeagueApiTestCase):
    def test_an_unknown_job_reads_as_running(self) -> None:
        league = make_league("PjL", manager=self.user_a)
        season = active_season(league, make_teams("Pj", 2))
        data = self.ok(self.api.get(action(season.id, "play-jobs/no-such-job")))
        self.assertEqual(
            data,
            {
                "status": "running",
                "completed": 0,
                "total": 0,
                "cancelled": False,
                "error": None,
            },
        )

    def test_anonymous_is_401_and_cross_account_is_404(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = active_season(self.foreign_league("PjF"), make_teams("PjF", 2))
        self.error(self.api.get(action(foreign.id, "play-jobs/abc")), 404, "not_found")
        self.error(
            self.anon.get(action(foreign.id, "play-jobs/abc")),
            401,
            "not_authenticated",
        )


class TestServiceModuleBoundary(SimpleTestCase):
    """``league_play_services`` must not import the League views module (the
    views import the service), so the dependency only points one way."""

    def test_does_not_import_league_views(self) -> None:
        import matches.league_play_services as services

        tree = ast.parse(Path(services.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        self.assertFalse(
            [name for name in imported if "league_views" in name], imported
        )


class TestTaskClearsOnlyItsOwnRun(LeagueApiTestCase):
    """A finishing task never clears another run's job id."""

    def test_a_task_leaves_another_runs_id_in_place(self) -> None:
        from matches.tasks import play_season_task

        league = make_league("OwnL", manager=self.user_a)
        season = active_season(league, make_teams("Own", 2))
        Season.objects.filter(pk=season.pk).update(active_play_job_id="other-run")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.apply_async(
                kwargs={"season_id": season.pk, "max_matchdays": 1},
                task_id="this-run",
            )
        season.refresh_from_db()
        self.assertEqual(season.active_play_job_id, "other-run")

    def test_a_task_clears_its_own_id(self) -> None:
        from matches.tasks import play_season_task

        league = make_league("Own2L", manager=self.user_a)
        season = active_season(league, make_teams("Own2", 2))
        Season.objects.filter(pk=season.pk).update(active_play_job_id="this-run")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.apply_async(
                kwargs={"season_id": season.pk, "max_matchdays": 1},
                task_id="this-run",
            )
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)
