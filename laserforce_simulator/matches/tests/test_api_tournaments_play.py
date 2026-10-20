"""ANG-01f-6 -- the Tournament play API (spec #190, ticket #196).

``POST /api/tournaments/<id>/play-next/`` (sync, 200 ``{node, match_ids,
state, champion}``), ``POST .../play-all/`` (200 ``{job_id, tournament_id,
status_url}``) and ``GET .../play-jobs/<job_id>/`` (the HTML polling keys),
with a single active play run per Tournament (ADR-0040, Tournaments).

Runs under eager Celery (project ``conftest.py``) against the real simulator
with ``ROUND_TICKS`` shortened; assertions are on states, ids, codes and the
``active_play_job_id`` column -- never on simulated point totals.
"""

from __future__ import annotations

from unittest.mock import patch

from django.db.models import QuerySet
from django.urls import reverse

from matches.models import Match, SeasonPhase, Tournament, TournamentParticipant
from matches.simulation import BatchSimulator
from matches.tests.league_api_fixtures import LeagueApiTestCase, make_league, make_teams

_FAST_TICKS = 20
JOB_REF_KEYS = {"job_id", "tournament_id", "status_url"}
JOB_STATUS_KEYS = {"status", "completed", "total", "error", "tournament_id"}


def _url(t: Tournament, suffix: str) -> str:
    return f"/api/tournaments/{t.id}/{suffix}"


def _setup(name: str, *, manager=None, n_teams: int = 4, **fields) -> Tournament:
    t = Tournament.objects.create(name=name, manager=manager, **fields)
    for seed, team in enumerate(make_teams(f"{name}-T", n_teams), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _active(name: str, *, manager=None, n_teams: int = 4, **fields) -> Tournament:
    t = _setup(name, manager=manager, n_teams=n_teams, **fields)
    t.lock_and_build()
    t.refresh_from_db()
    return t


def _embed(t: Tournament) -> None:
    league = make_league(f"{t.name} League")
    season = league.seasons.create(
        name="S1", start_date="2026-01-01", schedule_format="single_round_robin"
    )
    SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="tournament", tournament=t
    )


def _fast():
    return patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS)


class PlayNextTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _active("Next", manager=self.user_a)

    def post(self, t: Tournament | None = None):
        return self.api.post(_url(t or self.t, "play-next/"))

    def test_plays_one_node_and_returns_its_match_ids(self) -> None:
        with _fast():
            data = self.ok(self.post())
        self.assertEqual(set(data), {"node", "match_ids", "state", "champion"})
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(data["match_ids"], [Match.objects.get().id])
        self.assertEqual(data["state"], "active")
        self.assertIsNone(data["champion"])
        node = data["node"]
        self.assertEqual(node["bracket_type"], "winners")
        self.assertEqual(node["bracket_round"], 1)
        self.assertIsNotNone(node["winner"])
        self.assertEqual(node["games"][0]["match_id"], data["match_ids"][0])

    def test_the_final_node_completes_the_tournament_with_a_champion(self) -> None:
        with _fast():
            for _ in range(20):
                data = self.ok(self.post())
                if data["state"] == "completed":
                    break
        self.assertEqual(data["state"], "completed")
        self.t.refresh_from_db()
        self.assertEqual(data["champion"]["id"], self.t.champion_id)
        self.assertEqual(data["node"]["winner"]["id"], self.t.champion_id)

    def test_setup_tournament_is_409_not_active(self) -> None:
        t = _setup("NextSetup", manager=self.user_a)
        self.error(self.post(t), 409, "tournament_not_active")
        self.assertFalse(Match.objects.exists())

    def test_nothing_playable_is_409_no_playable_node(self) -> None:
        with _fast():
            for _ in range(20):
                if self.ok(self.post())["state"] == "completed":
                    break
        # An active Tournament whose every node has resolved.
        Tournament.objects.filter(pk=self.t.pk).update(state="active")
        count = Match.objects.count()
        self.error(self.post(), 409, "no_playable_node")
        self.assertEqual(Match.objects.count(), count)

    def test_embedded_tournament_is_409_embedded(self) -> None:
        _embed(self.t)
        self.error(self.post(), 409, "tournament_embedded")
        self.assertFalse(Match.objects.exists())

    def test_cross_account_is_404_and_anonymous_is_403(self) -> None:
        foreign = _active("NextForeign", manager=self.user_b)
        self.error(self.post(foreign), 404, "not_found")
        self.error(self.anon.post(_url(self.t, "play-next/")), 403, "not_authenticated")
        self.assertFalse(Match.objects.exists())


def _async_state(state: str):
    """Patch the play service's ``AsyncResult`` to report ``state``."""
    return patch(
        "matches.tournament_services.play.AsyncResult",
        **{"return_value.state": state},
    )


class RunGuardTests(LeagueApiTestCase):
    """While ``active_play_job_id`` is set both play actions are a 409
    ``play_in_progress`` naming the Job, unless the Job is terminal."""

    def setUp(self) -> None:
        super().setUp()
        self.t = _active("Guard", manager=self.user_a)
        Tournament.objects.filter(pk=self.t.pk).update(active_play_job_id="live-job")

    def column(self) -> str | None:
        self.t.refresh_from_db()
        return self.t.active_play_job_id

    def test_running_job_blocks_play_all_and_play_next(self) -> None:
        for state in ("STARTED", "PENDING", "RETRY", "PROGRESS"):
            with self.subTest(state=state), _async_state(state):
                for suffix in ("play-all/", "play-next/"):
                    err = self.error(
                        self.api.post(_url(self.t, suffix)),
                        409,
                        "play_in_progress",
                        "job:live-job",
                    )
                    self.assertTrue(err["message"])
        self.assertEqual(self.column(), "live-job")
        self.assertFalse(Match.objects.exists())

    def test_terminal_job_is_released_and_play_next_proceeds(self) -> None:
        for state in ("SUCCESS", "FAILURE", "REVOKED"):
            with self.subTest(state=state):
                Tournament.objects.filter(pk=self.t.pk).update(
                    active_play_job_id="live-job"
                )
                with _async_state(state), _fast():
                    data = self.ok(self.api.post(_url(self.t, "play-next/")))
                self.assertTrue(data["match_ids"])
                self.assertIsNone(self.column())

    def test_terminal_job_is_released_and_play_all_proceeds(self) -> None:
        with _async_state("SUCCESS"), _fast():
            data = self.ok(self.api.post(_url(self.t, "play-all/")))
        self.assertNotEqual(data["job_id"], "live-job")
        self.t.refresh_from_db()
        self.assertEqual(self.t.state, "completed")
        self.assertIsNone(self.t.active_play_job_id)


class PlayAllTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _active("All", manager=self.user_a)

    def post(self, t: Tournament | None = None):
        return self.api.post(_url(t or self.t, "play-all/"))

    def test_drains_to_a_champion_and_clears_the_run(self) -> None:
        with _fast():
            data = self.ok(self.post())
        self.assertEqual(set(data), JOB_REF_KEYS)
        self.assertEqual(data["tournament_id"], self.t.id)
        self.assertEqual(
            data["status_url"],
            reverse("tournament-play-job", args=[self.t.id, data["job_id"]]),
        )
        self.t.refresh_from_db()
        self.assertEqual(self.t.state, "completed")
        self.assertIsNotNone(self.t.champion_id)
        self.assertIsNone(self.t.active_play_job_id)

    def test_status_endpoint_reports_the_html_polling_keys(self) -> None:
        with _fast():
            data = self.ok(self.post())
        status = self.ok(self.api.get(data["status_url"]))
        self.assertEqual(set(status), JOB_STATUS_KEYS)
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["tournament_id"], self.t.id)
        self.assertGreater(status["total"], 0)
        self.assertEqual(status["completed"], status["total"])
        self.assertIsNone(status["error"])
        html = self.api.get(
            reverse("tournament_play_status", args=[self.t.id, data["job_id"]])
        )
        self.assertEqual(html.json(), status)

    def test_the_column_is_written_before_apply_async(self) -> None:
        seen: dict[str, str | None] = {}

        def fake_apply_async(args, *, task_id, retry):
            seen["column"] = Tournament.objects.get(pk=self.t.pk).active_play_job_id
            seen["task_id"] = task_id
            self.assertEqual(args, (self.t.pk,))
            self.assertFalse(retry)

        with patch(
            "matches.tournament_services.play.play_tournament_task.apply_async",
            side_effect=fake_apply_async,
        ):
            data = self.ok(self.post())
        self.assertEqual(seen["column"], data["job_id"])
        self.assertEqual(seen["task_id"], data["job_id"])
        self.t.refresh_from_db()
        self.assertEqual(self.t.active_play_job_id, data["job_id"])

    def test_broker_down_is_503_and_leaves_the_column_null(self) -> None:
        from kombu.exceptions import OperationalError

        with patch(
            "matches.tournament_services.play.play_tournament_task.apply_async",
            side_effect=OperationalError("broker unreachable"),
        ):
            self.error(self.post(), 503, "broker_unavailable")
        self.t.refresh_from_db()
        self.assertIsNone(self.t.active_play_job_id)

    def test_setup_tournament_is_409_not_active(self) -> None:
        t = _setup("AllSetup", manager=self.user_a)
        self.error(self.post(t), 409, "tournament_not_active")
        t.refresh_from_db()
        self.assertIsNone(t.active_play_job_id)

    def test_embedded_tournament_is_409_embedded(self) -> None:
        _embed(self.t)
        self.error(self.post(), 409, "tournament_embedded")
        self.assertFalse(Match.objects.exists())

    def test_cross_account_is_404_and_anonymous_is_403(self) -> None:
        foreign = _active("AllForeign", manager=self.user_b)
        self.error(self.post(foreign), 404, "not_found")
        self.error(self.anon.post(_url(self.t, "play-all/")), 403, "not_authenticated")
        self.error(self.api.get(_url(foreign, "play-jobs/abc/")), 404, "not_found")
        self.assertFalse(Match.objects.exists())


_real_update = QuerySet.update


def _lose_claims(t: Tournament, winners: list[str | None]):
    """Patch ``QuerySet.update`` so the next ``len(winners)`` play-run claims on
    ``t`` claim 0 rows. Before each lost claim the column is set to that
    winner's id (``None`` = the winner already finished and cleared it)."""
    remaining = list(winners)

    def fake_update(qs: QuerySet, **kwargs):
        value = kwargs.get("active_play_job_id")
        if qs.model is Tournament and isinstance(value, str) and remaining:
            _real_update(
                Tournament.objects.filter(pk=t.pk),
                active_play_job_id=remaining.pop(0),
            )
            return 0
        return _real_update(qs, **kwargs)

    return patch.object(QuerySet, "update", fake_update)


class LostClaimRaceTests(LeagueApiTestCase):
    """ANG-01f review (P7): ``enqueue_play_all`` losing the guarded claim."""

    def setUp(self) -> None:
        super().setUp()
        self.t = _active("Race", manager=self.user_a)

    def enqueue(self, winners: list[str | None]):
        from matches.tournament_services.play import enqueue_play_all

        with _lose_claims(self.t, winners), patch(
            "matches.tournament_services.play.play_tournament_task.apply_async"
        ) as apply_async:
            return enqueue_play_all(self.t), apply_async

    def column(self) -> str | None:
        return Tournament.objects.get(pk=self.t.pk).active_play_job_id

    def test_a_live_winner_is_reported(self) -> None:
        from matches.tournament_services.errors import PlayInProgressError

        with self.assertRaises(PlayInProgressError) as ctx:
            self.enqueue(["winner-job"])
        self.assertEqual(ctx.exception.job_id, "winner-job")
        self.assertEqual(self.column(), "winner-job")

    def test_a_finished_winner_means_one_retry_that_claims(self) -> None:
        job_id, apply_async = self.enqueue([None])
        self.assertEqual(self.column(), job_id)
        apply_async.assert_called_once_with((self.t.pk,), task_id=job_id, retry=False)

    def test_a_failed_retry_reports_the_current_id(self) -> None:
        from matches.tournament_services.errors import PlayInProgressError

        with self.assertRaises(PlayInProgressError) as ctx:
            self.enqueue([None, "late-job"])
        self.assertEqual(ctx.exception.job_id, "late-job")

    def test_a_failed_retry_with_an_empty_column_reports_unknown(self) -> None:
        from matches.tournament_services.errors import PlayInProgressError

        with self.assertRaises(PlayInProgressError) as ctx:
            self.enqueue([None, None])
        self.assertEqual(ctx.exception.job_id, "unknown")
        self.assertIsNone(self.column())


class PlayJobTests(LeagueApiTestCase):
    def test_an_unknown_job_reads_as_running(self) -> None:
        t = _active("Job", manager=self.user_a)
        data = self.ok(self.api.get(_url(t, "play-jobs/no-such-job/")))
        self.assertEqual(
            data,
            {
                "status": "running",
                "completed": 0,
                "total": 0,
                "error": None,
                "tournament_id": t.id,
            },
        )


class TaskClearsOnlyItsOwnRunTests(LeagueApiTestCase):
    def test_a_task_leaves_another_runs_id_in_place(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active("Own", manager=self.user_a)
        Tournament.objects.filter(pk=t.pk).update(active_play_job_id="other-run")
        with _fast():
            play_tournament_task.apply_async((t.pk,), task_id="this-run")
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertEqual(t.active_play_job_id, "other-run")

    def test_a_task_clears_its_own_id(self) -> None:
        from matches.tasks import play_tournament_task

        t = _active("Own2", manager=self.user_a)
        Tournament.objects.filter(pk=t.pk).update(active_play_job_id="this-run")
        with _fast():
            play_tournament_task.apply_async((t.pk,), task_id="this-run")
        t.refresh_from_db()
        self.assertIsNone(t.active_play_job_id)


_FORMATS = (
    ("single_elimination", {}),
    ("double_elimination", {}),
    ("round_robin", {}),
    ("round_robin_double_elim", {"rrde_combo": "4/0"}),
    ("swiss", {}),
)


class PlayerPoolDrawFiveFormatsTests(LeagueApiTestCase):
    """A Player-pool draw in each format locks and plays to a champion
    through the API play-all (spec #190 story 53)."""

    def drawn(self, fmt: str, extra: dict, pool: int) -> int:
        created = self.ok(
            self.api.post(
                "/api/tournaments/",
                {
                    "name": f"Pool {fmt} {pool}",
                    "format": fmt,
                    "team_assembly": "player_pool",
                    **extra,
                },
                format="json",
            ),
            201,
        )
        tid = created["id"]
        self.ok(
            self.api.post(
                f"/api/tournaments/{tid}/pool/generate/",
                {"count": pool},
                format="json",
            ),
            201,
        )
        self.ok(self.api.post(f"/api/tournaments/{tid}/draw/"))
        return tid

    def test_each_format_plays_to_a_champion(self) -> None:
        for fmt, extra in _FORMATS:
            with self.subTest(format=fmt):
                tid = self.drawn(fmt, extra, 24)
                locked = self.ok(self.api.post(f"/api/tournaments/{tid}/lock/"))
                self.assertEqual(locked["state"], "active")
                with _fast():
                    job = self.ok(self.api.post(f"/api/tournaments/{tid}/play-all/"))
                self.assertEqual(job["tournament_id"], tid)
                t = Tournament.objects.get(pk=tid)
                self.assertEqual(t.state, "completed")
                self.assertIsNotNone(t.champion_id)
                self.assertIsNone(t.active_play_job_id)

    def test_a_30_player_pool_on_swiss_is_400_at_lock(self) -> None:
        tid = self.drawn("swiss", {}, 30)
        self.error(self.api.post(f"/api/tournaments/{tid}/lock/"), 400, "invalid")
        self.assertEqual(Tournament.objects.get(pk=tid).state, "setup")
