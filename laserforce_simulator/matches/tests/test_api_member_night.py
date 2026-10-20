"""ANG-01d-2 T4 -- member nights over ``/api/``.

Seam 1 (``/api/``, DRF ``APIClient``): ``GET /api/seasons/<id>/member-night/``
reads the Sites state; ``POST`` draws games for chosen Sites (400 ``sites``
field error from ``MemberNightSetupForm``); ``member-night/play/`` starts the
play-all Active play run (Job ref); ``member-night/play-single/`` plays one
game (dashboard payload); ``member-night/play-live/`` plays one game at full
fidelity (``{kind: "member_night", round_ids, job: null}``). Errors are 409
``season_not_active`` / ``wrong_phase`` / ``no_member_night_games`` /
``play_in_progress``; cross-Account 404, anonymous 401 (ADR-0041).

The draw uses a fresh ``random.Random()``, so tests assert game COUNTS against
the locked ``MIN_GAMES..MAX_GAMES`` band, never identities or point totals.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from matches.member_night import MAX_GAMES, MIN_GAMES
from matches.models import GameRound, Match, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_teams,
)
from teams.models import Player

_FAST_TICKS = 20
JOB_REF_KEYS = {"job_id", "season_id", "status_url"}


def mn_url(pk: int, suffix: str = "") -> str:
    return f"/api/seasons/{pk}/member-night/{suffix}"


def mn_season(league, prefix: str, *, site: str = "SiteA", phases=("member_night",)):
    """An active Season on a member-night cursor with one viable Site."""
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    teams = make_teams(prefix, 2)
    season.teams.add(*teams)
    Player.objects.filter(team__in=teams).update(home_site=site)
    for ordinal, phase_type in enumerate(phases, start=1):
        SeasonPhase.objects.create(
            season=season, ordinal=ordinal, phase_type=phase_type
        )
    season.start_season()
    season.refresh_from_db()
    return season


class MemberNightApiTestCase(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league = make_league("MnApiL", manager=self.user_a)
        self.season = mn_season(self.league, "MnApi")
        self.phase = self.season.current_phase()

    def setup_games(self) -> int:
        self.ok(self.api.post(mn_url(self.season.id), {"sites": ["SiteA"]}))
        return Match.objects.filter(season_phase=self.phase).count()

    def shells(self):
        return Match.objects.filter(season_phase=self.phase)


class TestMemberNightState(MemberNightApiTestCase):
    def test_reads_the_viable_sites_and_their_games(self) -> None:
        data = self.ok(self.api.get(mn_url(self.season.id)))
        self.assertEqual(data["season"]["id"], self.season.id)
        self.assertTrue(data["phase_active"])
        self.assertEqual(data["sites"], [{"site": "SiteA", "count": 12, "games": 0}])
        self.assertEqual(data["games_total"], 0)
        self.assertEqual(data["games_unplayed"], 0)

    def test_after_setup_counts_the_drawn_games_per_site(self) -> None:
        n = self.setup_games()
        data = self.ok(self.api.get(mn_url(self.season.id)))
        self.assertEqual(data["sites"][0]["games"], n)
        self.assertEqual(data["games_total"], n)
        self.assertEqual(data["games_unplayed"], n)

    def test_site_counts_survive_a_drawn_team_rename(self) -> None:
        # The Site is derived from the drawn Teams' players (their home_site),
        # not parsed out of the "MN <site> G<n> A" Team name.
        n = self.setup_games()
        for shell in self.shells().select_related("team_red", "team_blue"):
            for team in (shell.team_red, shell.team_blue):
                team.name = f"Renamed {team.pk}"
                team.save(update_fields=["name"])
        data = self.ok(self.api.get(mn_url(self.season.id)))
        self.assertEqual(data["sites"], [{"site": "SiteA", "count": 12, "games": n}])

    def test_off_a_member_night_cursor_reads_inactive(self) -> None:
        season = mn_season(
            make_league("MnOffL", manager=self.user_a), "MnOff", phases=()
        )
        data = self.ok(self.api.get(mn_url(season.id)))
        self.assertFalse(data["phase_active"])
        self.assertEqual(data["sites"], [])

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = mn_season(self.foreign_league("MnForeign"), "MnF")
        self.error(self.api.get(mn_url(foreign.id)), 404, "not_found")
        self.error(self.anon.get(mn_url(self.season.id)), 401, "not_authenticated")


class TestMemberNightPlayAll(MemberNightApiTestCase):
    def test_starts_the_active_play_run_and_returns_a_job_ref(self) -> None:
        self.setup_games()
        with patch("matches.tasks.play_member_night_task.apply_async") as enqueue:
            data = self.ok(self.api.post(mn_url(self.season.id, "play/")))
        self.assertEqual(set(data), JOB_REF_KEYS)
        self.assertEqual(data["season_id"], self.season.id)
        self.season.refresh_from_db()
        self.assertEqual(self.season.active_play_job_id, data["job_id"])
        self.assertEqual(enqueue.call_args.kwargs["task_id"], data["job_id"])

    def test_an_eager_run_drains_every_game_and_clears_the_run(self) -> None:
        n = self.setup_games()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            self.ok(self.api.post(mn_url(self.season.id, "play/")))
        self.assertEqual(self.shells().filter(is_completed=True).count(), n)
        self.season.refresh_from_db()
        self.assertIsNone(self.season.active_play_job_id)

    def test_no_unplayed_games_is_409(self) -> None:
        self.error(
            self.api.post(mn_url(self.season.id, "play/")), 409, "no_member_night_games"
        )
        self.season.refresh_from_db()
        self.assertIsNone(self.season.active_play_job_id)

    def test_wrong_phase_is_409(self) -> None:
        season = mn_season(
            make_league("MnAllRrL", manager=self.user_a), "MnAllRr", phases=()
        )
        self.error(self.api.post(mn_url(season.id, "play/")), 409, "wrong_phase")

    def test_a_live_run_is_409_play_in_progress(self) -> None:
        self.setup_games()
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="busy")
        self.error(
            self.api.post(mn_url(self.season.id, "play/")),
            409,
            "play_in_progress",
            "job:busy",
        )

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = mn_season(self.foreign_league("MnAllForeign"), "MnAllF")
        self.error(self.api.post(mn_url(foreign.id, "play/")), 404, "not_found")
        self.error(
            self.anon.post(mn_url(self.season.id, "play/")), 401, "not_authenticated"
        )


class TestMemberNightPlaySingle(MemberNightApiTestCase):
    def post_single(self, season=None):
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            return self.api.post(mn_url((season or self.season).id, "play-single/"))

    def test_plays_exactly_one_game_and_returns_the_dashboard(self) -> None:
        n = self.setup_games()
        data = self.ok(self.post_single())
        self.assertEqual(data["season"]["id"], self.season.id)
        self.assertIn("standings_snippet", data)
        self.assertEqual(self.shells().filter(is_completed=True).count(), 1)
        self.assertEqual(self.shells().filter(is_completed=False).count(), n - 1)

    def test_no_unplayed_games_is_409(self) -> None:
        self.error(self.post_single(), 409, "no_member_night_games")

    def test_wrong_phase_is_409(self) -> None:
        season = mn_season(
            make_league("MnOneRrL", manager=self.user_a), "MnOneRr", phases=()
        )
        self.error(self.post_single(season), 409, "wrong_phase")

    def test_draft_season_is_409_season_not_active(self) -> None:
        draft = Season.objects.create(
            league=make_league("MnOneDraftL", manager=self.user_a),
            name="D",
            start_date=date(2026, 6, 1),
        )
        self.error(self.post_single(draft), 409, "season_not_active")

    def test_a_live_run_is_409_play_in_progress_and_plays_nothing(self) -> None:
        self.setup_games()
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="busy")
        self.error(self.post_single(), 409, "play_in_progress", "job:busy")
        self.assertFalse(self.shells().filter(is_completed=True).exists())

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = mn_season(self.foreign_league("MnOneForeign"), "MnOneF")
        self.error(self.post_single(foreign), 404, "not_found")
        self.error(
            self.anon.post(mn_url(self.season.id, "play-single/")),
            401,
            "not_authenticated",
        )


class TestMemberNightPlayLive(MemberNightApiTestCase):
    def post_live(self):
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            return self.api.post(mn_url(self.season.id, "play-live/"))

    def test_plays_one_full_fidelity_game_and_returns_its_rounds(self) -> None:
        self.setup_games()
        data = self.ok(self.post_live())
        self.assertEqual(data, {**data, "kind": "member_night", "job": None})
        self.assertEqual(set(data), {"kind", "round_ids", "job"})
        self.assertEqual(len(data["round_ids"]), 2)
        rounds = GameRound.objects.filter(pk__in=data["round_ids"])
        self.assertEqual({r.fidelity for r in rounds}, {"full"})
        self.assertEqual({r.match.season_phase_id for r in rounds}, {self.phase.id})
        self.season.refresh_from_db()
        self.assertIsNone(self.season.active_play_job_id)

    def test_no_unplayed_games_is_409(self) -> None:
        self.error(self.post_live(), 409, "no_member_night_games")

    def test_a_live_run_is_409_play_in_progress(self) -> None:
        self.setup_games()
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="busy")
        self.error(self.post_live(), 409, "play_in_progress", "job:busy")

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = mn_season(self.foreign_league("MnLiveForeign"), "MnLiveF")
        self.error(self.api.post(mn_url(foreign.id, "play-live/")), 404, "not_found")
        self.error(
            self.anon.post(mn_url(self.season.id, "play-live/")),
            401,
            "not_authenticated",
        )


class TestMemberNightSetup(MemberNightApiTestCase):
    def test_draws_games_and_returns_the_dashboard_payload(self) -> None:
        data = self.ok(self.api.post(mn_url(self.season.id), {"sites": ["SiteA"]}))
        self.assertEqual(data["season"]["id"], self.season.id)
        self.assertIn("standings_snippet", data)
        n = self.shells().count()
        self.assertGreaterEqual(n, MIN_GAMES)
        self.assertLessEqual(n, MAX_GAMES)
        for shell in self.shells().select_related("team_red", "team_blue"):
            self.assertEqual(shell.season_id, self.season.id)
            self.assertFalse(shell.is_completed)
            self.assertTrue(shell.team_red.is_draw_team)
            self.assertEqual(shell.team_red.manager, self.user_a)

    def test_a_repeat_setup_appends_games(self) -> None:
        first = self.setup_games()
        self.ok(self.api.post(mn_url(self.season.id), {"sites": ["SiteA"]}))
        second = self.shells().count() - first
        self.assertGreaterEqual(second, MIN_GAMES)
        self.assertLessEqual(second, MAX_GAMES)

    def test_no_sites_is_a_400_sites_field_error(self) -> None:
        self.error(self.api.post(mn_url(self.season.id), {}), 400, "required", "sites")
        self.assertFalse(self.shells().exists())

    def test_a_non_viable_site_is_a_400_sites_field_error(self) -> None:
        self.error(
            self.api.post(mn_url(self.season.id), {"sites": ["Nowhere"]}),
            400,
            "invalid_choice",
            "sites",
        )
        self.assertFalse(self.shells().exists())

    def test_wrong_phase_is_409(self) -> None:
        season = mn_season(
            make_league("MnSetRrL", manager=self.user_a), "MnSetRr", phases=()
        )
        self.error(
            self.api.post(mn_url(season.id), {"sites": ["SiteA"]}), 409, "wrong_phase"
        )

    def test_draft_season_is_409_season_not_active(self) -> None:
        draft = Season.objects.create(
            league=make_league("MnSetDraftL", manager=self.user_a),
            name="D",
            start_date=date(2026, 6, 1),
        )
        self.error(
            self.api.post(mn_url(draft.id), {"sites": ["SiteA"]}),
            409,
            "season_not_active",
        )

    def test_a_live_run_is_409_play_in_progress(self) -> None:
        Season.objects.filter(pk=self.season.pk).update(active_play_job_id="busy")
        self.error(
            self.api.post(mn_url(self.season.id), {"sites": ["SiteA"]}),
            409,
            "play_in_progress",
            "job:busy",
        )
        self.assertFalse(self.shells().exists())

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = mn_season(self.foreign_league("MnSetForeign"), "MnSetF")
        self.error(
            self.api.post(mn_url(foreign.id), {"sites": ["SiteA"]}), 404, "not_found"
        )
        self.error(
            self.anon.post(mn_url(self.season.id), {"sites": ["SiteA"]}),
            401,
            "not_authenticated",
        )
