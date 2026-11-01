"""ANG-01d-2 T2 -- Start Season + synchronous play over ``/api/``.

Seam 1 (``/api/``, DRF ``APIClient``): ``POST /api/seasons/<id>/start/`` /
``play-week/`` / ``play-single-round/`` return 200 with the same body as
``GET .../dashboard/``. Errors are 409 ``season_not_draft`` /
``season_not_active`` / ``wrong_phase`` / ``play_in_progress``; cross-Account
404, anonymous 401 (ADR-0041).

Runs against the real simulator (``ROUND_TICKS`` shortened); assertions are on
status codes, envelope shape, Season state and persisted rows -- never on
simulated point totals.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from matches.models import BracketNode, GameRound, Season
from matches.simulation import BatchSimulator
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_teams,
)
from matches.tests.test_api_play_runs import active_season, play_round_robin
from matches.tests.test_regional_playoffs import (
    _built_regional_season,
    _flat_season,
    _hand_play_rr,
    _ids,
    _stamp_bracket_completed,
)

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

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = draft_season(self.foreign_league("StF"), make_teams("StF", 2))
        self.error(self.api.post(action(foreign.id, "start")), 404, "not_found")
        self.error(
            self.anon.post(action(foreign.id, "start")), 401, "not_authenticated"
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

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = active_season(self.foreign_league("PwF"), make_teams("PwF", 2))
        self.error(self.api.post(action(foreign.id, "play-week")), 404, "not_found")
        self.error(
            self.anon.post(action(foreign.id, "play-week")), 401, "not_authenticated"
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

    def test_cross_account_is_404_and_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        foreign = active_season(self.foreign_league("PsrF"), make_teams("PsrF", 2))
        self.error(
            self.api.post(action(foreign.id, "play-single-round")), 404, "not_found"
        )
        self.error(
            self.anon.post(action(foreign.id, "play-single-round")),
            401,
            "not_authenticated",
        )


# ---------------------------------------------------------------------------
# ANG-08d -- regional / Worlds / Last-chance playoffs (ported from
# test_regional_playoffs_drain.py and test_last_chance_qualifier.py).
# Brackets are reached by a hand-played round robin (no simulation); the
# drain itself runs the real engine under a short ROUND_TICKS.
# ---------------------------------------------------------------------------

_PLAYOFF_TICKS = 30


def _resolved(tournament) -> int:
    """Bracket nodes carrying a winner (played or bye)."""
    return BracketNode.objects.filter(
        tournament=tournament, winner__isnull=False
    ).count()


def _first_stage_size(tournament) -> int:
    return BracketNode.objects.filter(
        tournament=tournament, bracket_type="winners", bracket_round=1
    ).count()


def _regional(phase, conference):
    """The Conference's Regional playoff (any non-Last-chance regional row)."""
    return (
        phase.regional_tournaments.filter(conference=conference)
        .exclude(qualifier_stage="last_chance")
        .first()
    )


def _last_chance(phase, conference):
    return phase.regional_tournaments.filter(
        conference=conference, qualifier_stage="last_chance"
    ).first()


def _worlds_tournament(season: Season):
    phase = season.phases.filter(tournament_mode="worlds").first()
    if phase is None:
        return None
    phase.refresh_from_db()
    return phase.tournament


def _built_flat_season(prefix: str):
    """A 0-Conference Season with its single bracket built."""
    season, teams, rr_phase, phase = _flat_season(prefix)
    _hand_play_rr(season, rr_phase, _ids(teams))
    season.refresh_from_db()
    season.activate_pending_tournament_phase()
    phase.refresh_from_db()
    return season, phase


class _PlayoffApiTestCase(LeagueApiTestCase):
    def post(self, season: Season, path: str):
        with patch.object(BatchSimulator, "ROUND_TICKS", _PLAYOFF_TICKS):
            return self.api.post(action(season.id, path))


class TestPlayWeekRegional(_PlayoffApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        (
            self.season,
            self.conferences,
            self.groups,
            _rr,
            self.phase,
        ) = _built_regional_season("ApiWeekReg")
        self.tournaments = self.season.tournaments_for_phase(self.phase)
        self.assertEqual(len(self.tournaments), 2)

    def test_one_click_advances_every_bracket_by_one_stage(self) -> None:
        before = {t.id: _resolved(t) for t in self.tournaments}
        sizes = {t.id: _first_stage_size(t) for t in self.tournaments}
        self.assertEqual(set(sizes.values()), {2})
        self.ok(self.post(self.season, "play-week"))
        for tournament in self.tournaments:
            self.assertEqual(
                _resolved(tournament) - before[tournament.id], sizes[tournament.id]
            )
            # One stage only: the final is still open.
            final = tournament.nodes.get(advances_to__isnull=True)
            self.assertIsNone(final.winner_id)
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")
        self.assertEqual(self.season.current_phase().id, self.phase.id)

    def test_repeated_clicks_drain_every_bracket_and_worlds_to_a_champion(
        self,
    ) -> None:
        for _ in range(14):
            self.season.refresh_from_db()
            if self.season.state == "completed":
                break
            self.ok(self.post(self.season, "play-week"))
        self.season.refresh_from_db()
        for conference, group in zip(self.conferences, self.groups):
            regional = _regional(self.phase, conference)
            self.assertEqual(regional.state, "completed")
            self.assertIn(regional.champion_id, _ids(group))
        self.assertEqual(self.season.state, "completed")
        worlds = _worlds_tournament(self.season)
        self.assertIsNotNone(worlds)
        self.assertEqual(self.season.champion_team_id, worlds.champion_id)


class TestPlayWeekZeroConference(_PlayoffApiTestCase):
    def test_one_click_drains_one_stage_of_the_single_bracket(self) -> None:
        season, phase = _built_flat_season("ApiWeekFlat")
        tournament = phase.tournament
        before = _resolved(tournament)
        stage = _first_stage_size(tournament)
        self.ok(self.post(season, "play-week"))
        self.assertEqual(_resolved(tournament) - before, stage)


class TestPlaySingleRoundRegional(_PlayoffApiTestCase):
    def test_a_regional_click_resolves_exactly_one_node(self) -> None:
        season, _conferences, _groups, _rr, phase = _built_regional_season("ApiSrReg")
        tournaments = season.tournaments_for_phase(phase)
        before = sum(_resolved(t) for t in tournaments)
        self.ok(self.post(season, "play-single-round"))
        self.assertEqual(sum(_resolved(t) for t in tournaments) - before, 1)

    def test_a_flat_click_resolves_exactly_one_node(self) -> None:
        season, phase = _built_flat_season("ApiSrFlat")
        before = _resolved(phase.tournament)
        self.ok(self.post(season, "play-single-round"))
        self.assertEqual(_resolved(phase.tournament) - before, 1)


class TestPlaySingleRoundCrossesIntoWorlds(_PlayoffApiTestCase):
    """CONF-04: two 4-Team regionals hold 3 nodes each, so click 6 finishes
    qualification and must leave Worlds built and active; click 7 plays it."""

    def setUp(self) -> None:
        super().setUp()
        (
            self.season,
            self.conferences,
            _groups,
            _rr,
            self.phase,
        ) = _built_regional_season("ApiSrWorlds")

    def _click(self, times: int) -> None:
        for _ in range(times):
            # A 409 here is the "cursor parked on an unbuilt Worlds" trap.
            self.ok(self.post(self.season, "play-single-round"))

    def test_the_regionals_finishing_click_builds_worlds_without_playing_it(
        self,
    ) -> None:
        self._click(6)
        for conference in self.conferences:
            self.assertEqual(_regional(self.phase, conference).state, "completed")
        worlds = _worlds_tournament(self.season)
        self.assertIsNotNone(worlds)
        self.assertEqual(worlds.state, "active")
        self.assertEqual(worlds.nodes.count(), 1)
        self.assertEqual(_resolved(worlds), 0)
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")

    def test_the_next_click_plays_worlds_and_crowns_the_season(self) -> None:
        self._click(7)
        worlds = _worlds_tournament(self.season)
        self.assertEqual(worlds.state, "completed")
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, worlds.champion_id)


class TestPlaySingleRoundSeedsTheLastChanceBracket(_PlayoffApiTestCase):
    """CONF-03: the click that drains the last Regional playoff seeds the
    Last-chance bracket, so the playoff controls never vanish (no dead click).

    Conference 2's Regional playoff is stamped complete up front so the final
    Conference-1 node is the last regional node of the phase.
    """

    def setUp(self) -> None:
        super().setUp()
        (
            self.season,
            self.conferences,
            self.groups,
            _rr,
            self.phase,
        ) = _built_regional_season("ApiSrLc", [9, 4], cut=4)
        self.big, small = self.conferences
        _stamp_bracket_completed(_regional(self.phase, small), self.groups[1][0])

    def _click(self):
        return self.ok(self.post(self.season, "play-single-round"))

    def _click_until_the_regional_drains(self) -> None:
        for _ in range(20):
            self._click()
            if _regional(self.phase, self.big).state == "completed":
                return
        self.fail("the Regional playoff never drained")

    def test_the_draining_click_activates_a_four_team_last_chance_bracket(
        self,
    ) -> None:
        self._click_until_the_regional_drains()
        last_chance = _last_chance(self.phase, self.big)
        self.assertEqual(last_chance.state, "active")
        self.assertEqual(last_chance.participants.count(), 4)
        controls = self.ok(
            self.api.get(f"/api/leagues/{self.season.league_id}/play-controls/")
        )
        self.assertIs(controls["playoff_phase_active"], True)
        self.assertIs(controls["playoff_completed"], False)

    def test_clicks_drain_the_last_chance_bracket_then_crown_the_season(
        self,
    ) -> None:
        for _ in range(40):
            self.season.refresh_from_db()
            if self.season.state == "completed":
                break
            self._click()
        last_chance = _last_chance(self.phase, self.big)
        self.assertEqual(last_chance.state, "completed")
        self.assertIn(last_chance.champion_id, _ids(self.groups[0]))
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertIsNotNone(self.season.champion_team_id)

    def test_the_click_after_seeding_resolves_a_last_chance_node(self) -> None:
        self._click_until_the_regional_drains()
        last_chance = _last_chance(self.phase, self.big)
        before = _resolved(last_chance)
        self._click()
        self.assertGreater(_resolved(last_chance), before)
