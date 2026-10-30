"""ANG-01e T6 -- the Statistical Feats + Playoffs API.

``GET /api/leagues/<id>/statistical-feats/`` and ``GET .../playoffs/``, driven
through DRF ``APIClient`` (seam 1 of spec #180). Rounds are hand-built with
fixed stats and brackets are built by hand-playing the round robin and
stamping node winners -- the simulator never runs, and assertions are on
structure, ordering and counts, never simulated point totals.
"""

from __future__ import annotations

from matches.models import WatchedPlayer
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    add_round_states,
    make_league,
    ranked_career_league,
)

FEAT_ROW_KEYS = {
    "player",
    "watched",
    "player_id",
    "player_name",
    "role",
    "team_id",
    "team_name",
    "round_id",
    "opp_team_name",
    "result",
    "season_id",
    "season_name",
    "stats",
    "feats",
}


def _feats_url(league_id: int) -> str:
    return f"/api/leagues/{league_id}/statistical-feats/"


class StatisticalFeatsTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "SFeat", 2, manager=self.user_a
        )
        self.round_1 = add_round_states(self.season, self.teams[0], self.teams[1])
        self.round_2 = add_round_states(self.season, self.teams[1], self.teams[0])

    def get(self, query: str = ""):
        return self.api.get(_feats_url(self.league.id) + query)

    def test_happy_path_shape_and_meta(self) -> None:
        response = self.get()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        data, meta = body["data"], body["meta"]
        self.assertEqual(set(data), {"feats", "team_feats"})
        self.assertTrue(data["feats"])
        row = data["feats"][0]
        self.assertEqual(set(row), FEAT_ROW_KEYS)
        self.assertEqual(set(row["player"]), {"id", "name", "team"})
        self.assertEqual(row["player"]["id"], row["player_id"])
        self.assertFalse(row["watched"])
        self.assertTrue(row["feats"])
        self.assertEqual(set(row["feats"][0]), {"kind", "label", "is_season_best"})
        self.assertIn("nuke_detonations", row["stats"])
        # ANG-06-15: ``team_options`` (the Team picker) is the one new key;
        # ``season_scope`` and the page keys are unchanged.
        self.assertEqual(
            set(meta),
            {
                "season_scope",
                "team_options",
                "page",
                "page_size",
                "count",
                "total_pages",
            },
        )
        self.assertEqual(
            meta["season_scope"]["selected"],
            {"id": self.season.id, "name": self.season.name},
        )
        self.assertEqual(meta["count"], len(data["feats"]))

    def test_default_ordering_is_newest_round_first(self) -> None:
        feats = self.ok_feats()
        round_ids = [row["round_id"] for row in feats]
        self.assertEqual(round_ids, sorted(round_ids, reverse=True))
        self.assertEqual(round_ids[0], self.round_2.id)

    def test_ordering_ascending_round(self) -> None:
        feats = self.ok_feats("?ordering=round")
        round_ids = [row["round_id"] for row in feats]
        self.assertEqual(round_ids, sorted(round_ids))

    def test_ordering_by_box_score_key(self) -> None:
        feats = self.ok_feats("?ordering=-points_scored")
        points = [row["stats"]["points_scored"] for row in feats]
        self.assertEqual(points, sorted(points, reverse=True))

    def test_team_filter_limits_rows(self) -> None:
        team = self.teams[1]
        feats = self.ok_feats(f"?team_id={team.id}")
        self.assertTrue(feats)
        self.assertEqual({row["team_id"] for row in feats}, {team.id})

    def test_watched_flag_reads_account_list(self) -> None:
        player_id = self.ok_feats()[0]["player_id"]
        WatchedPlayer.objects.create(
            account=self.user_a,
            league=self.league,
            player_id=player_id,
        )
        feats = self.ok_feats()
        for row in feats:
            self.assertEqual(row["watched"], row["player_id"] == player_id)

    def test_team_feats_carry_team_ref(self) -> None:
        from matches.models import GameRound, Match

        comeback = Match.objects.create(
            team_red=self.teams[1],
            team_blue=self.teams[0],
            season=self.season,
            red_round1_points=1,
            blue_round1_points=100,
            red_round2_points=200,
            blue_round2_points=1,
            winner=self.teams[1],
            is_completed=True,
        )
        round_2 = GameRound.objects.create(
            match=comeback,
            team_red=self.teams[0],
            team_blue=self.teams[1],
            round_number=2,
            red_points=1,
            blue_points=100,
            is_completed=True,
        )
        data = self.ok(self.get())
        # ANG-06-15: each entry adds ``team_name`` (the record's own winner
        # name, what the HTML shows). ``team`` still resolves to the winner's
        # ref through the Round, so the team-ref invariant is unchanged.
        self.assertEqual(
            data["team_feats"],
            [
                {
                    "kind": "comeback_win",
                    "label": data["team_feats"][0]["label"],
                    "team_name": self.teams[1].name,
                    "team": {"id": self.teams[1].id, "name": self.teams[1].name},
                    "round_id": round_2.id,
                }
            ],
        )

    def test_team_feat_names_its_team_when_team_ref_is_null(self) -> None:
        from matches.models import Match

        # A comeback Match with no Round 2: ``round_id`` and so ``team`` are
        # null, but the record still carries the winner's name.
        Match.objects.create(
            team_red=self.teams[1],
            team_blue=self.teams[0],
            season=self.season,
            red_round1_points=1,
            blue_round1_points=100,
            red_round2_points=200,
            blue_round2_points=1,
            winner=self.teams[1],
            is_completed=True,
        )
        data = self.ok(self.get())
        self.assertEqual(
            data["team_feats"],
            [
                {
                    "kind": "comeback_win",
                    "label": data["team_feats"][0]["label"],
                    "team_name": self.teams[1].name,
                    "team": None,
                    "round_id": None,
                }
            ],
        )

    def test_page_size_pages_feats(self) -> None:
        total = len(self.ok_feats())
        response = self.get("?page_size=1&page=2")
        body = response.json()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(body["data"]["feats"]), 1)
        self.assertEqual(body["meta"]["count"], total)
        self.assertEqual(body["meta"]["page"], 2)

    def test_career_scope(self) -> None:
        response = self.get("?season=career")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["meta"]["season_scope"]["selected"], "career")
        self.assertTrue(response.json()["data"]["feats"])

    def test_bad_params_are_400(self) -> None:
        self.error(self.get("?season=999999"), 400, "invalid_choice", "season")
        self.error(self.get("?season=abc"), 400, "invalid_choice", "season")
        self.error(self.get("?ordering=bogus"), 400, "invalid_choice", "ordering")
        self.error(self.get("?team_id=abc"), 400, "invalid", "team_id")
        self.error(self.get("?team_id=999999"), 400, "invalid_choice", "team_id")

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(_feats_url(foreign.id)), 404)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(_feats_url(self.league.id)), 401)

    def test_no_season_is_empty(self) -> None:
        empty = make_league("SFeatEmpty", manager=self.user_a)
        response = self.api.get(_feats_url(empty.id))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["data"], {"feats": [], "team_feats": []})
        self.assertEqual(
            body["meta"]["season_scope"], {"selected": None, "options": []}
        )
        self.assertEqual(body["meta"]["count"], 0)

    def test_leaves_session_untouched(self) -> None:
        self.ok(self.get())
        self.assert_session_untouched()

    def ok_feats(self, query: str = "") -> list[dict]:
        return self.ok(self.get(query))["feats"]


# ---------------------------------------------------------------------------
# Playoffs
# ---------------------------------------------------------------------------

BRACKET_KEYS = {
    "key",
    "name",
    "stage",
    "stage_label",
    "conference",
    "champion",
    "pending",
    "rounds",
}
NODE_KEYS = {
    "position",
    "team_a",
    "team_b",
    "seed_a",
    "seed_b",
    "is_bye",
    "wins_a",
    "wins_b",
    "series_length",
    "winner",
    "games",
}


def _playoffs_url(league_id: int) -> str:
    return f"/api/leagues/{league_id}/playoffs/"


def _own(league, user) -> None:
    league.manager = user
    league.save(update_fields=["manager"])


def _built_flat_season(prefix: str, user):
    """A 4-Team zero-Conference Season whose RR is hand-played and whose
    playoff bracket is built by the real activation -- no simulator."""
    from matches.tests.test_regional_playoffs import (
        _flat_season,
        _hand_play_rr,
    )

    season, teams, rr_phase, phase = _flat_season(prefix)
    _hand_play_rr(season, rr_phase, [t.id for t in teams])
    season.refresh_from_db()
    season.activate_pending_tournament_phase()
    phase.refresh_from_db()
    _own(season.league, user)
    return season, teams, phase


class PlayoffsTests(LeagueApiTestCase):
    def test_built_bracket_shape(self) -> None:
        season, teams, phase = _built_flat_season("PoBuilt", self.user_a)
        response = self.api.get(_playoffs_url(season.league_id))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        data = body["data"]
        self.assertEqual(set(data), {"season", "brackets", "worlds_qualifiers"})
        self.assertEqual(data["season"], {"id": season.id, "name": season.name})
        self.assertEqual(len(data["brackets"]), 1)
        bracket = data["brackets"][0]
        self.assertEqual(set(bracket), BRACKET_KEYS)
        self.assertEqual(bracket["key"], str(phase.ordinal))
        self.assertEqual(bracket["stage"], "")
        self.assertIsNone(bracket["conference"])
        self.assertIsNone(bracket["champion"])
        self.assertFalse(bracket["pending"])
        self.assertEqual([r["bracket_round"] for r in bracket["rounds"]], [1, 2])
        first_round = bracket["rounds"][0]["nodes"]
        self.assertEqual(len(first_round), 2)
        node = first_round[0]
        self.assertEqual(set(node), NODE_KEYS)
        self.assertEqual(set(node["team_a"]), {"id", "name"})
        self.assertIn(node["team_a"]["id"], {t.id for t in teams})
        self.assertIsNone(node["winner"])
        self.assertEqual(node["games"], [])
        self.assertEqual(data["worlds_qualifiers"], [])
        self.assertEqual(
            body["meta"]["season_scope"]["selected"],
            {"id": season.id, "name": season.name},
        )

    def test_stamped_winners_and_champion_go_out_as_refs(self) -> None:
        season, teams, phase = _built_flat_season("PoChamp", self.user_a)
        tournament = season.tournaments_for_phase(phase)[0]
        node = tournament.nodes.order_by("bracket_round", "position").first()
        node.winner = node.team_a
        node.save(update_fields=["winner"])
        tournament.champion = node.team_a
        tournament.state = "completed"
        tournament.save(update_fields=["champion", "state"])

        bracket = self.ok(self.api.get(_playoffs_url(season.league_id)))["brackets"][0]
        champion = {"id": node.team_a.id, "name": node.team_a.name}
        self.assertEqual(bracket["champion"], champion)
        self.assertEqual(bracket["rounds"][0]["nodes"][0]["winner"], champion)

    def test_pending_phase_is_a_stub(self) -> None:
        from matches.tests.test_regional_playoffs import _flat_season

        season, _teams, _rr, phase = _flat_season("PoPend")
        _own(season.league, self.user_a)
        data = self.ok(self.api.get(_playoffs_url(season.league_id)))
        self.assertEqual(len(data["brackets"]), 1)
        stub = data["brackets"][0]
        self.assertTrue(stub["pending"])
        self.assertEqual(stub["rounds"], [])
        self.assertEqual(stub["key"], str(phase.ordinal))
        self.assertIsNone(stub["champion"])

    def test_season_without_playoff_phase_has_empty_brackets(self) -> None:
        league, season, _teams = ranked_career_league("PoNone", 2, manager=self.user_a)
        data = self.ok(self.api.get(_playoffs_url(league.id)))
        self.assertEqual(data["season"], {"id": season.id, "name": season.name})
        self.assertEqual(data["brackets"], [])
        self.assertEqual(data["worlds_qualifiers"], [])

    def test_explicit_season_param_selects_that_season(self) -> None:
        season, _teams, _phase = _built_flat_season("PoPick", self.user_a)
        older = make_completed_season_for(season.league)
        data = self.ok(
            self.api.get(f"{_playoffs_url(season.league_id)}?season={older.id}")
        )
        self.assertEqual(data["season"], {"id": older.id, "name": older.name})
        self.assertEqual(data["brackets"], [])

    def test_career_is_400(self) -> None:
        league, _season, _teams = ranked_career_league("PoCar", 2, manager=self.user_a)
        self.error(
            self.api.get(f"{_playoffs_url(league.id)}?season=career"),
            400,
            "invalid_choice",
            "season",
        )

    def test_unknown_or_foreign_season_is_400(self) -> None:
        league, _season, _teams = ranked_career_league("PoBad", 2, manager=self.user_a)
        foreign_league, foreign_season, _ = ranked_career_league(
            "PoFor", 2, manager=self.user_b
        )
        url = _playoffs_url(league.id)
        self.error(
            self.api.get(f"{url}?season=999999"), 400, "invalid_choice", "season"
        )
        self.error(self.api.get(f"{url}?season=abc"), 400, "invalid_choice", "season")
        self.error(
            self.api.get(f"{url}?season={foreign_season.id}"),
            400,
            "invalid_choice",
            "season",
        )

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(_playoffs_url(foreign.id)), 404)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        league, _season, _teams = ranked_career_league("PoAnon", 2, manager=self.user_a)
        self.errors(self.anon.get(_playoffs_url(league.id)), 401)

    def test_no_season_is_empty(self) -> None:
        empty = make_league("PoEmpty", manager=self.user_a)
        response = self.api.get(_playoffs_url(empty.id))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(
            body["data"], {"season": None, "brackets": [], "worlds_qualifiers": []}
        )
        self.assertEqual(
            body["meta"], {"season_scope": {"selected": None, "options": []}}
        )

    def test_leaves_session_untouched(self) -> None:
        league, _season, _teams = ranked_career_league("PoSess", 2, manager=self.user_a)
        self.ok(self.api.get(_playoffs_url(league.id)))
        self.assert_session_untouched()


def make_completed_season_for(league):
    """An extra completed (bracket-less) Season in ``league``, dated earlier."""
    from datetime import date

    from matches.tests.league_api_fixtures import make_completed_season, make_teams

    return make_completed_season(
        league,
        make_teams(f"Old{league.id}x", 2),
        name="Old Season",
        start_date=date(2020, 1, 1),
    )


class RegionalPlayoffsTests(LeagueApiTestCase):
    """CONF-02/03/04: regional brackets carry a Conference ref and their
    ``key`` / ``stage``; the derived Worlds phase is a pending stub; the Worlds
    qualifiers go out flattened with ``provenance_label``."""

    def test_regional_brackets_and_worlds_qualifiers(self) -> None:
        from matches.tests.test_regional_playoffs import (
            _built_regional_season,
            _stamp_bracket_completed,
        )

        season, conferences, groups, _rr, phase = _built_regional_season("PoReg")
        _own(season.league, self.user_a)
        url = _playoffs_url(season.league_id)

        data = self.ok(self.api.get(url))
        brackets = data["brackets"]
        regional = [b for b in brackets if b["stage"] == "regional_playoff"]
        self.assertEqual(
            [b["key"] for b in regional],
            [f"{phase.ordinal}-{c.ordinal}" for c in conferences],
        )
        self.assertEqual(
            [b["conference"] for b in regional],
            [{"id": c.id, "name": c.name} for c in conferences],
        )
        worlds = [b for b in brackets if b["stage"] == "worlds"]
        self.assertEqual(len(worlds), 1)
        self.assertEqual(worlds[0]["stage_label"], "Worlds")
        self.assertTrue(worlds[0]["key"].endswith("-worlds"))
        self.assertEqual(data["worlds_qualifiers"], [])

        for tournament, group in zip(
            season.tournaments_for_phase(phase), groups, strict=True
        ):
            _stamp_bracket_completed(tournament, group[0])
        qualifiers = self.ok(self.api.get(url))["worlds_qualifiers"]
        self.assertEqual(len(qualifiers), len(conferences))
        self.assertEqual(
            {q["team_id"] for q in qualifiers}, {group[0].id for group in groups}
        )
        for q in qualifiers:
            self.assertIn("provenance", q)
            self.assertIn("seed", q)
            self.assertTrue(q["provenance_label"])
