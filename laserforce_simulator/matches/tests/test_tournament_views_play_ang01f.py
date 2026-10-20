"""ANG-01f-6 -- HTML regressions for the play views (ticket #196).

The HTML play-next / play-all / play-status views now delegate to
``matches.tournament_services.play`` and share its guards: one active play run
per Tournament (ADR-0040, Tournaments) and no play on an embedded Tournament.
Play-all keeps its 202 ``{job_id, tournament_id}`` and answers a guard with a
409 JSON ``{"error"}``; play-next flashes and redirects.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from conftest import get_shared_manager
from matches.models import Match, Tournament
from matches.tests.test_api_tournaments_play import _active, _embed, _fast
from matches.tournament_services.errors import TournamentEmbeddedError

EMBEDDED_MSG = str(TournamentEmbeddedError())
IN_PROGRESS_MSG = "The Tournament already has a play run in progress."


def _flashes(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


class _PlayViewTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = get_shared_manager()
        self.client.force_login(self.user)
        self.t = _active("HtmlPlay", manager=self.user)

    def post(self, name: str):
        return self.client.post(reverse(name, args=[self.t.id]))


class DoublePlayAllTests(_PlayViewTestCase):
    def test_a_second_play_all_while_the_first_is_queued_is_409(self) -> None:
        # The first Job is accepted but never consumed (no worker yet), so the
        # run stays live and the Celery state reads PENDING.
        with patch("matches.tournament_services.play.play_tournament_task.apply_async"):
            first = self.post("tournament_play_all")
            second = self.post("tournament_play_all")
        self.assertEqual(first.status_code, 202)
        job_id = first.json()["job_id"]
        self.assertEqual(first.json(), {"job_id": job_id, "tournament_id": self.t.id})
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json(), {"error": IN_PROGRESS_MSG})
        self.t.refresh_from_db()
        self.assertEqual(self.t.active_play_job_id, job_id)

    def test_play_next_while_a_run_is_live_flashes_and_plays_nothing(self) -> None:
        Tournament.objects.filter(pk=self.t.pk).update(active_play_job_id="live")
        response = self.post("tournament_play_next")
        self.assertRedirects(response, reverse("tournament_detail", args=[self.t.id]))
        self.assertIn(IN_PROGRESS_MSG, _flashes(response))
        self.assertFalse(Match.objects.exists())

    def test_eager_play_all_clears_the_run(self) -> None:
        with _fast():
            response = self.post("tournament_play_all")
        self.assertEqual(response.status_code, 202)
        self.t.refresh_from_db()
        self.assertEqual(self.t.state, "completed")
        self.assertIsNone(self.t.active_play_job_id)


class EmbeddedPlayTests(_PlayViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        _embed(self.t)

    def test_play_next_flashes_and_plays_nothing(self) -> None:
        response = self.post("tournament_play_next")
        self.assertRedirects(response, reverse("tournament_detail", args=[self.t.id]))
        self.assertIn(EMBEDDED_MSG, _flashes(response))
        self.assertFalse(Match.objects.exists())

    def test_play_all_is_409_json_and_plays_nothing(self) -> None:
        response = self.post("tournament_play_all")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": EMBEDDED_MSG})
        self.assertFalse(Match.objects.exists())
        self.t.refresh_from_db()
        self.assertIsNone(self.t.active_play_job_id)
