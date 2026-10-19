"""ANG-01e fix pass -- one definition of "enrolled" across the League screens.

An active / completed Season's enrolment is its activation snapshot
(``starting_team_ids_json``); a Team added to the live ``Season.teams`` M2M
after the Season started is NOT enrolled, on every screen and API action.
"""

from __future__ import annotations

from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_open_season,
    make_teams,
)


class LateM2MTeamIsNotEnrolledTests(LeagueApiTestCase):
    """A Team added to an active Season's M2M after it started."""

    def setUp(self) -> None:
        super().setUp()
        self.teams = make_teams("Snap", 2)
        self.league = make_league(
            "Snap League", manager=self.user_a, current_team=self.teams[0]
        )
        self.league.finance_enabled = True
        self.league.save(update_fields=["finance_enabled"])
        self.season = make_open_season(self.league, self.teams, state="active")
        self.late = make_teams("Late", 1)[0]
        self.season.teams.add(self.late)
        self.base = f"/api/leagues/{self.league.id}"

    def test_roster_is_404(self) -> None:
        self.errors(self.api.get(f"{self.base}/teams/{self.late.id}/roster/"), 404)

    def test_team_finances_is_404(self) -> None:
        self.errors(self.api.get(f"{self.base}/teams/{self.late.id}/finances/"), 404)

    def test_league_finances_rows_exclude_it(self) -> None:
        data = self.ok(self.api.get(f"{self.base}/finances/"))
        self.assertEqual(
            {r["team"]["id"] for r in data["rows"]}, {t.id for t in self.teams}
        )

    def test_player_ratings_team_id_is_400(self) -> None:
        self.error(
            self.api.get(f"{self.base}/player-ratings/?team_id={self.late.id}"),
            400,
            "invalid_choice",
            "team_id",
        )

    def test_player_ratings_rows_exclude_its_players(self) -> None:
        rows, _ = self.ok_page(
            self.api.get(f"{self.base}/player-ratings/?page_size=100")
        )
        self.assertNotIn(self.late.id, {r["player"]["team"]["id"] for r in rows})

    def test_statistical_feats_team_id_is_400(self) -> None:
        self.error(
            self.api.get(f"{self.base}/statistical-feats/?team_id={self.late.id}"),
            400,
            "invalid_choice",
            "team_id",
        )

    def test_feats_and_ratings_reject_with_the_same_message(self) -> None:
        feats = self.error(
            self.api.get(f"{self.base}/statistical-feats/?team_id={self.late.id}"),
            400,
            "invalid_choice",
            "team_id",
        )
        ratings = self.error(
            self.api.get(f"{self.base}/player-ratings/?team_id={self.late.id}"),
            400,
            "invalid_choice",
            "team_id",
        )
        self.assertEqual(feats["message"], ratings["message"])

    def test_html_roster_picker_and_selection_exclude_it(self) -> None:
        self.client.force_login(self.user_a)
        response = self.client.get(
            f"/leagues/{self.league.id}/team/roster/?team_id={self.late.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [t.id for t in response.context["enrolled_teams"]],
            [t.id for t in sorted(self.teams, key=lambda t: t.name)],
        )
        self.assertEqual(response.context["team"].id, self.teams[0].id)

    def test_html_feats_picker_excludes_it(self) -> None:
        self.client.force_login(self.user_a)
        response = self.client.get(
            f"/leagues/{self.league.id}/stats/statistical-feats/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            self.late.id, {t.id for t in response.context["enrolled_teams"]}
        )
