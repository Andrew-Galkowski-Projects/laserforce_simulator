"""ANG-01d-2 T2 -- the shared guards on the HTML Start Season + sync play views.

Seam 2 (Django test client): Start Season on a non-draft Season keeps its
idempotent 302 but never re-snapshots; Play One Week / Play Single Round
redirect back to the dashboard with a ``play_error`` while an Active play run
is live (ADR-0040); Play Single Round re-renders the 400 dashboard on a
non-active Season. Assertions are on status codes, redirects, the rendered
banner, the Season's state + snapshot and persisted rows -- never on simulated
point totals.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Season, SeasonPhase
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_play_run_guard_views import _active_season, _play_round_robin


def _draft_season(prefix: str, n_teams: int) -> Season:
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    for i in range(n_teams):
        team, _ = make_team_with_slots(f"{prefix}{i}")
        season.teams.add(team)
    return season


class TestStartSeasonDraftGuard(TestCase):
    def test_active_season_keeps_its_302_and_is_not_re_snapshotted(self) -> None:
        season = _active_season("SgA", 2)
        snapshot = list(season.starting_team_ids_json)
        late, _ = make_team_with_slots("SgALate")
        season.teams.add(late)
        response = self.client.post(reverse("start_season", args=[season.id]))
        self.assertRedirects(
            response,
            reverse("season_dashboard", args=[season.id]),
            fetch_redirect_response=False,
        )
        season.refresh_from_db()
        # Before ANG-01d-2 the model method re-snapshotted an active Season,
        # silently enrolling the late team in the frozen schedule.
        self.assertEqual(season.starting_team_ids_json, snapshot)

    def test_completed_season_keeps_its_302_and_stays_completed(self) -> None:
        season = _active_season("SgC", 2)
        Season.objects.filter(pk=season.pk).update(state="completed")
        response = self.client.post(reverse("start_season", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")

    def test_a_draft_still_starts(self) -> None:
        season = _draft_season("SgD", 2)
        response = self.client.post(reverse("start_season", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        season.refresh_from_db()
        self.assertEqual(season.state, "active")


class TestPlayWeekRunGuard(TestCase):
    def test_live_run_redirects_with_play_error_and_plays_nothing(self) -> None:
        season = _active_season("PwG", 2)
        Season.objects.filter(pk=season.pk).update(active_play_job_id="live-job")
        response = self.client.post(reverse("play_week", args=[season.id]))
        dashboard = reverse("season_dashboard", args=[season.id])
        self.assertRedirects(response, dashboard, fetch_redirect_response=False)
        self.assertFalse(GameRound.objects.filter(match__season=season).exists())
        page = self.client.get(dashboard)
        self.assertContains(page, 'id="season-dashboard-play-error"')
        self.assertContains(page, "already has a play run in progress")
        # The flash is consumed: a second load shows no banner.
        again = self.client.get(dashboard)
        self.assertNotContains(again, 'id="season-dashboard-play-error"')


class TestPlaySingleRoundGuards(TestCase):
    def test_live_run_redirects_with_play_error(self) -> None:
        season = _active_season("PsG", 4, phases=("round_robin", "tournament"))
        _play_round_robin(season)
        rounds = GameRound.objects.count()
        Season.objects.filter(pk=season.pk).update(active_play_job_id="busy")
        response = self.client.post(reverse("play_single_round", args=[season.id]))
        dashboard = reverse("season_dashboard", args=[season.id])
        self.assertRedirects(response, dashboard, fetch_redirect_response=False)
        self.assertEqual(GameRound.objects.count(), rounds)
        self.assertContains(self.client.get(dashboard), "already has a play run")

    def test_non_active_season_re_renders_the_400_dashboard(self) -> None:
        season = _draft_season("PsD", 4)
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        response = self.client.post(reverse("play_single_round", args=[season.id]))
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'id="season-dashboard-play-error"', status_code=400
        )
        self.assertContains(response, "Season must be active", status_code=400)


class TestStartSeasonConfirmation(TestCase):
    """UXP-13 -- Start Season flashes a success message with the Round count."""

    def test_starting_a_draft_flashes_the_scheduled_round_count(self) -> None:
        season = _draft_season("SgM", 4)
        response = self.client.post(
            reverse("start_season", args=[season.id]), follow=True
        )
        season.refresh_from_db()
        n_rounds = len(season.scheduled_fixtures())
        # 4 teams, single round-robin: 6 pairings x 2 Rounds each.
        self.assertEqual(n_rounds, 12)
        self.assertContains(response, "S1 started — 12 Rounds scheduled.")

    def test_a_non_draft_start_flashes_nothing(self) -> None:
        season = _active_season("SgN", 2)
        response = self.client.post(
            reverse("start_season", args=[season.id]), follow=True
        )
        self.assertNotContains(response, "started —")
