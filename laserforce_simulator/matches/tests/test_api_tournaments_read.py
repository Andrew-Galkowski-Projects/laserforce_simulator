"""ANG-01f-2 -- the Tournaments read API (spec #190, ticket #192).

``GET /api/tournaments/`` (list + filters + ordering), ``GET
/api/tournaments/<id>/`` (the ``build_tournament_detail`` payload), ``GET
.../pool/`` and the 405s, driven through DRF ``APIClient`` under the ADR-0039
envelope. Brackets are built with ``lock_and_build`` and nodes are resolved by
stamping winners, so the simulator never runs; assertions are on structure,
codes and counts.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from matches.models import (
    Match,
    SeasonPhase,
    Tournament,
    TournamentParticipant,
    TournamentPlayerEntry,
)
from matches.simulation import BatchSimulator
from matches.standings import match_score
from matches.tests.league_api_fixtures import LeagueApiTestCase, make_league, make_teams
from matches.tournament_engine import play_next_node
from teams.models import Player, get_free_agents_team

LIST_URL = "/api/tournaments/"

TOURNAMENT_KEYS = {
    "id",
    "name",
    "format",
    "format_label",
    "state",
    "team_assembly",
    "role_assignment_mode",
    "champion",
    "embedded",
    "season",
    "final_series_length",
    "semifinal_series_length",
    "quarterfinal_series_length",
    "earlier_series_length",
    "wb_advancers",
    "lb_advancers",
    "swiss_rounds",
    "manager",
    "created_at",
}


DETAIL_KEYS = TOURNAMENT_KEYS | {
    "participants",
    "brackets",
    "rr_standings",
    "swiss_standings",
    "stage",
    "cut_labels",
    "pool_size",
    "is_drawn",
    "rr_crosstable",
}

LEG_KEYS = {
    "team_score",
    "opp_score",
    "match_team",
    "match_opp",
    "played",
    "match_id",
}

# Short simulated rounds keep a played Round robin fast (the
# ``test_tournament_views`` precedent).
_FAST_TICKS = 40

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


def _detail_url(tournament_id: int) -> str:
    return f"/api/tournaments/{tournament_id}/"


def _tournament(name: str, *, manager=None, n_teams: int = 0, **fields) -> Tournament:
    t = Tournament.objects.create(name=name, manager=manager, **fields)
    for seed, team in enumerate(make_teams(f"{name}-T", n_teams), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


class TournamentListTests(LeagueApiTestCase):
    def test_lists_owned_and_unmanaged_but_not_foreign(self) -> None:
        mine = _tournament("Mine", manager=self.user_a)
        unmanaged = _tournament("Unmanaged")
        _tournament("Foreign", manager=self.user_b)

        data, meta = self.ok_page(self.api.get(LIST_URL))

        self.assertEqual({row["id"] for row in data}, {mine.id, unmanaged.id})
        self.assertEqual(meta["count"], 2)

    def test_anonymous_is_401_not_authenticated(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.error(self.anon.get(LIST_URL), 401, "not_authenticated")

    def test_row_shape(self) -> None:
        t = _tournament(
            "Pool RRDE",
            manager=self.user_a,
            format="round_robin_double_elim",
            team_assembly="player_pool",
        )
        data, _ = self.ok_page(self.api.get(LIST_URL))
        row = data[0]
        self.assertEqual(set(row), TOURNAMENT_KEYS)
        self.assertEqual(row["id"], t.id)
        self.assertEqual(row["format_label"], "Random Draw")
        self.assertFalse(row["embedded"])
        self.assertIsNone(row["season"])
        self.assertIsNone(row["champion"])

    def ids(self, query: str) -> list[int]:
        data, _ = self.ok_page(self.api.get(LIST_URL + query))
        return [row["id"] for row in data]

    def test_filter_state_format_and_team_assembly(self) -> None:
        se = _tournament("SE", state="active")
        rr = _tournament("RR", format="round_robin")
        pool = _tournament("Pool", team_assembly="player_pool")
        self.assertEqual(self.ids("?state=active"), [se.id])
        self.assertEqual(self.ids("?format=round_robin"), [rr.id])
        self.assertEqual(self.ids("?team_assembly=player_pool"), [pool.id])

    def test_filter_embedded(self) -> None:
        sandbox = _tournament("Sandbox")
        season_wide = _tournament("Season-wide")
        regional = _tournament("Regional")
        league = make_league("Emb League")
        season = league.seasons.create(
            name="S1", start_date="2026-01-01", schedule_format="single_round_robin"
        )
        phase = SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="tournament", tournament=season_wide
        )
        regional.season_phase = phase
        regional.save(update_fields=["season_phase"])

        self.assertEqual(
            sorted(self.ids("?embedded=true")), sorted([season_wide.id, regional.id])
        )
        self.assertEqual(self.ids("?embedded=false"), [sandbox.id])
        # ANG-01f review (P2): case-insensitive, and 1 / 0 are accepted too.
        self.assertEqual(
            sorted(self.ids("?embedded=TRUE")), sorted([season_wide.id, regional.id])
        )
        self.assertEqual(
            sorted(self.ids("?embedded=1")), sorted([season_wide.id, regional.id])
        )
        self.assertEqual(self.ids("?embedded=False"), [sandbox.id])
        self.assertEqual(self.ids("?embedded=0"), [sandbox.id])

    def _mixed_tournaments(self, n: int, prefix: str) -> None:
        """``n`` Tournaments cycling sandbox / Season-wide / regional."""
        league = make_league(f"{prefix} League")
        season = league.seasons.create(
            name="S1", start_date="2026-01-01", schedule_format="single_round_robin"
        )
        for i in range(n):
            t = _tournament(f"{prefix}{i}")
            if i % 3 == 1:
                SeasonPhase.objects.create(
                    season=season, ordinal=i, phase_type="tournament", tournament=t
                )
            elif i % 3 == 2:
                t.season_phase = SeasonPhase.objects.create(
                    season=season, ordinal=100 + i, phase_type="tournament"
                )
                t.save(update_fields=["season_phase"])

    def _list_query_count(self) -> tuple[int, list[dict]]:
        with CaptureQueriesContext(connection) as ctx:
            data, _ = self.ok_page(self.api.get(LIST_URL))
        return len(ctx.captured_queries), data

    def test_list_runs_a_constant_number_of_queries(self) -> None:
        # ANG-01f review (S1): no per-row query for ``embedded`` / ``season``.
        self._mixed_tournaments(2, "Few")
        few, few_rows = self._list_query_count()
        self.assertEqual(len(few_rows), 2)
        Tournament.objects.all().delete()
        self._mixed_tournaments(6, "Many")
        with self.assertNumQueries(few):
            rows, _ = self.ok_page(self.api.get(LIST_URL))
        self.assertEqual(len(rows), 6)
        by_name = {row["name"]: row for row in rows}
        season = SeasonPhase.objects.filter(season__league__name="Many League")[
            0
        ].season
        for i in range(6):
            row = by_name[f"Many{i}"]
            self.assertEqual(row["embedded"], i % 3 != 0)
            expected = None if i % 3 == 0 else {"id": season.id, "name": "S1"}
            self.assertEqual(row["season"], expected)

    def test_bad_choice_filters_are_400_with_field(self) -> None:
        for param in ("state", "format", "team_assembly"):
            with self.subTest(param=param):
                self.error(
                    self.api.get(f"{LIST_URL}?{param}=bogus"),
                    400,
                    "invalid_choice",
                    param,
                )
        # ANG-01f review (P2): a junk ``embedded`` is a 400 too, never ignored.
        for junk in ("bogus", "yes", "2", "truee"):
            with self.subTest(embedded=junk):
                self.error(
                    self.api.get(f"{LIST_URL}?embedded={junk}"),
                    400,
                    "invalid",
                    "embedded",
                )

    def test_old_random_draw_value_is_rejected(self) -> None:
        self.error(
            self.api.get(f"{LIST_URL}?team_assembly=random_draw"),
            400,
            "invalid_choice",
            "team_assembly",
        )

    def test_default_ordering_is_newest_first(self) -> None:
        first = _tournament("A")
        second = _tournament("B")
        self.assertEqual(self.ids(""), [second.id, first.id])

    def test_ordering_by_created_at(self) -> None:
        # The higher id is the OLDER row, so created_at order != id order.
        newer = _tournament("A")
        older = _tournament("B")
        Tournament.objects.filter(pk=newer.pk).update(
            created_at=datetime(2026, 2, 1, tzinfo=timezone.utc)
        )
        Tournament.objects.filter(pk=older.pk).update(
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(self.ids("?ordering=created_at"), [older.id, newer.id])
        self.assertEqual(self.ids("?ordering=-created_at"), [newer.id, older.id])

    def test_ordering_by_name(self) -> None:
        b = _tournament("Bravo")
        a = _tournament("Alpha")
        self.assertEqual(self.ids("?ordering=name"), [a.id, b.id])
        self.assertEqual(self.ids("?ordering=-name"), [b.id, a.id])
        self.assertEqual(self.ids("?ordering=id"), [b.id, a.id])


def _locked(name: str, n_teams: int, **fields) -> Tournament:
    t = _tournament(name, n_teams=n_teams, **fields)
    t.lock_and_build()
    t.refresh_from_db()
    return t


def _bracket_types(data: dict) -> list[str]:
    return [group["bracket_type"] for group in data["brackets"]]


class TournamentRetrieveTests(LeagueApiTestCase):
    def get(self, t: Tournament) -> dict:
        return self.ok(self.api.get(_detail_url(t.id)))

    def test_detail_payload_keys(self) -> None:
        t = _locked("SE", 4, manager=self.user_a)
        data = self.get(t)
        self.assertEqual(set(data), DETAIL_KEYS)
        self.assertEqual(data["id"], t.id)
        self.assertEqual(data["format_label"], "Single elimination")
        self.assertEqual(data["stage"], "single_elimination")
        self.assertFalse(data["embedded"])
        self.assertIsNone(data["season"])
        self.assertEqual(data["pool_size"], 0)
        self.assertFalse(data["is_drawn"])

    def test_participants_are_team_refs_in_seed_order(self) -> None:
        t = _tournament("Seeds", n_teams=4)
        data = self.get(t)
        self.assertEqual([p["seed"] for p in data["participants"]], [1, 2, 3, 4])
        self.assertEqual(set(data["participants"][0]), {"team", "seed"})
        self.assertEqual(set(data["participants"][0]["team"]), {"id", "name"})
        self.assertEqual(data["stage"], "setup")
        self.assertEqual(data["brackets"], [])

    def test_single_elimination_groups_winners_with_node_keys(self) -> None:
        data = self.get(_locked("SE", 4))
        self.assertEqual(_bracket_types(data), ["winners"])
        rounds = data["brackets"][0]["rounds"]
        self.assertEqual([r["bracket_round"] for r in rounds], [1, 2])
        node = rounds[0]["nodes"][0]
        self.assertEqual(set(node), NODE_KEYS)
        self.assertEqual(set(node["team_a"]), {"id", "name"})
        self.assertEqual(node["games"], [])
        self.assertIsNone(node["winner"])

    def test_double_elimination_groups_winners_losers_grand_final(self) -> None:
        data = self.get(_locked("DE", 4, format="double_elimination"))
        self.assertEqual(_bracket_types(data), ["winners", "losers", "grand_final"])

    def test_round_robin_groups_and_standings(self) -> None:
        data = self.get(_locked("RR", 4, format="round_robin"))
        self.assertEqual(_bracket_types(data), ["round_robin"])
        self.assertEqual(len(data["rr_standings"]), 4)
        row = data["rr_standings"][0]
        self.assertIn("rank", row)
        self.assertEqual(set(row["team"]), {"id", "name"})
        self.assertEqual(data["swiss_standings"], [])
        self.assertEqual(data["cut_labels"], {})

    def test_rrde_seeding_stage_has_cut_labels(self) -> None:
        t = _locked(
            "RRDE",
            6,
            format="round_robin_double_elim",
            wb_advancers=4,
            lb_advancers=0,
        )
        data = self.get(t)
        self.assertEqual(_bracket_types(data), ["round_robin"])
        self.assertEqual(data["stage"], "seeding")
        self.assertEqual(sorted(data["cut_labels"].values()), ["out"] * 2 + ["wb"] * 4)
        participant_ids = {str(p["team"]["id"]) for p in data["participants"]}
        self.assertEqual(set(data["cut_labels"]), participant_ids)

    def test_swiss_groups_and_standings(self) -> None:
        data = self.get(_locked("Swiss", 4, format="swiss"))
        self.assertEqual(_bracket_types(data), ["swiss"])
        self.assertEqual(data["stage"], "swiss")
        self.assertEqual(len(data["swiss_standings"]), 4)
        self.assertEqual(data["rr_standings"], [])

    def test_embedded_tournament_carries_its_season_ref(self) -> None:
        t = _tournament("Season bracket")
        league = make_league("Ret League")
        season = league.seasons.create(
            name="S1", start_date="2026-01-01", schedule_format="single_round_robin"
        )
        SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="tournament", tournament=t
        )
        data = self.get(t)
        self.assertTrue(data["embedded"])
        self.assertEqual(data["season"], {"id": season.id, "name": "S1"})

    def test_player_pool_reports_pool_size_and_is_drawn(self) -> None:
        t = _tournament(
            "Pool",
            format="round_robin_double_elim",
            team_assembly="player_pool",
        )
        team = make_teams("PoolDrawn", 1)[0]
        fa = get_free_agents_team()
        players = [Player.objects.create(team=fa, name=f"PP{i}") for i in range(3)]
        for player in players:
            TournamentPlayerEntry.objects.create(tournament=t, player=player)
        TournamentPlayerEntry.objects.filter(player=players[0]).update(
            tier=1, drawn_team=team
        )
        data = self.get(t)
        self.assertEqual(data["format_label"], "Random Draw")
        self.assertEqual(data["pool_size"], 3)
        self.assertTrue(data["is_drawn"])

    def test_cross_account_is_404(self) -> None:
        t = _tournament("Foreign", manager=self.user_b)
        self.error(self.api.get(_detail_url(t.id)), 404, "not_found")

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        t = _tournament("Anon")
        self.error(self.anon.get(_detail_url(t.id)), 401, "not_authenticated")


def _play_all_rr(t: Tournament) -> None:
    """Play every Round robin node with seeded, shortened sims."""
    random.seed(42)
    rr_total = t.nodes.filter(bracket_type="round_robin").count()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(rr_total):
            play_next_node(t)
    t.refresh_from_db()


class TournamentCrosstableTests(LeagueApiTestCase):
    """ANG-06-13b-3 -- ``rr_crosstable`` on the detail payload."""

    def get(self, t: Tournament) -> dict:
        return self.ok(self.api.get(_detail_url(t.id)))

    def _side_score(self, match: Match, team_id: int) -> tuple[int, int, int, int]:
        """(team points, opp points, team match score, opp match score)."""
        red_ms, blue_ms = match_score(
            match.red_rounds_won,
            match.blue_rounds_won,
            match.winner_id,
            match.team_red_id,
            match.team_blue_id,
        )
        if match.team_red_id == team_id:
            return (
                match.red_total_points,
                match.blue_total_points,
                red_ms,
                blue_ms,
            )
        return (match.blue_total_points, match.red_total_points, blue_ms, red_ms)

    def test_played_round_robin_crosstable_shape_and_leg_mapping(self) -> None:
        t = _locked("RR Played", 4, format="round_robin")
        _play_all_rr(t)
        data = self.get(t)
        crosstable = data["rr_crosstable"]

        row_ids = [row["team"]["id"] for row in crosstable]
        self.assertEqual(row_ids, [row["team_id"] for row in data["rr_standings"]])
        self.assertEqual(set(crosstable[0]["team"]), {"id", "name"})

        for i, row in enumerate(crosstable):
            self.assertEqual(len(row["cells"]), 4)
            self.assertIsNone(row["cells"][i])
            for j, cell in enumerate(row["cells"]):
                if i == j:
                    continue
                row_id, col_id = row_ids[i], row_ids[j]
                self.assertEqual(cell["opponent_team_id"], col_id)
                # Leg 1 (team_a = the lower id) sits at [a][b], leg 2 at [b][a].
                if row_id < col_id:
                    leg, other = cell["leg1"], cell["leg2"]
                else:
                    leg, other = cell["leg2"], cell["leg1"]
                self.assertIsNone(other)
                self.assertEqual(set(leg), LEG_KEYS)
                self.assertTrue(leg["played"])
                match = Match.objects.get(pk=leg["match_id"])
                self.assertEqual(
                    {match.team_red_id, match.team_blue_id}, {row_id, col_id}
                )
                # Seen from the row Team's side.
                self.assertEqual(
                    (
                        leg["team_score"],
                        leg["opp_score"],
                        leg["match_team"],
                        leg["match_opp"],
                    ),
                    self._side_score(match, row_id),
                )

    def test_unplayed_leg_is_not_played_with_null_scores(self) -> None:
        data = self.get(_locked("RR Fresh", 4, format="round_robin"))
        cell = data["rr_crosstable"][0]["cells"][1]
        leg = cell["leg1"] or cell["leg2"]
        self.assertEqual(
            leg,
            {
                "team_score": None,
                "opp_score": None,
                "match_team": None,
                "match_opp": None,
                "played": False,
                "match_id": None,
            },
        )

    def test_rrde_carries_a_crosstable(self) -> None:
        t = _locked(
            "RRDE X",
            6,
            format="round_robin_double_elim",
            wb_advancers=4,
            lb_advancers=0,
        )
        self.assertEqual(len(self.get(t)["rr_crosstable"]), 6)

    def test_crosstable_is_empty_for_other_formats(self) -> None:
        cases = {
            "single": _locked("SE X", 4),
            "double": _locked("DE X", 4, format="double_elimination"),
            "swiss": _locked("Swiss X", 4, format="swiss"),
            "pool before draw": _tournament(
                "Pool X",
                format="round_robin_double_elim",
                team_assembly="player_pool",
            ),
        }
        for label, t in cases.items():
            with self.subTest(label):
                self.assertEqual(self.get(t)["rr_crosstable"], [])

    def _detail_query_count(self, t: Tournament) -> int:
        with CaptureQueriesContext(connection) as ctx:
            self.get(t)
        return len(ctx.captured_queries)

    def test_detail_query_count_is_flat_across_field_sizes(self) -> None:
        small = _locked("RR Q4", 4, format="round_robin")
        _play_all_rr(small)
        large = _locked("RR Q6", 6, format="round_robin")
        _play_all_rr(large)
        self.assertEqual(
            self._detail_query_count(small), self._detail_query_count(large)
        )


class TournamentPoolTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _tournament("Pool", manager=self.user_a, team_assembly="player_pool")
        fa = get_free_agents_team()
        self.players = [
            Player.objects.create(team=fa, name=f"Pool{i}") for i in range(3)
        ]
        for player in self.players:
            TournamentPlayerEntry.objects.create(tournament=self.t, player=player)
        self.drawn = make_teams("PoolDrawnTeam", 1)[0]
        TournamentPlayerEntry.objects.filter(player=self.players[1]).update(
            tier=1, drawn_team=self.drawn
        )

    def url(self, t: Tournament) -> str:
        return f"/api/tournaments/{t.id}/pool/"

    def test_pool_is_paginated_entries(self) -> None:
        data, meta = self.ok_page(self.api.get(self.url(self.t)))
        self.assertEqual(meta["count"], 3)
        self.assertEqual(set(data[0]), {"player", "tier", "drawn_team"})
        self.assertEqual(set(data[0]["player"]), {"id", "name", "team"})
        drawn = [e for e in data if e["drawn_team"] is not None]
        self.assertEqual(len(drawn), 1)
        self.assertEqual(drawn[0]["player"]["id"], self.players[1].id)
        self.assertEqual(drawn[0]["tier"], 1)
        self.assertEqual(
            drawn[0]["drawn_team"], {"id": self.drawn.id, "name": self.drawn.name}
        )

    def test_pool_page_size(self) -> None:
        data, meta = self.ok_page(self.api.get(self.url(self.t) + "?page_size=2"))
        self.assertEqual(len(data), 2)
        self.assertEqual(meta["total_pages"], 2)

    def test_pool_cross_account_is_404(self) -> None:
        foreign = _tournament("ForeignPool", manager=self.user_b)
        self.error(self.api.get(self.url(foreign)), 404, "not_found")

    def test_pool_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.error(self.anon.get(self.url(self.t)), 401, "not_authenticated")


class TournamentMethodNotAllowedTests(LeagueApiTestCase):
    def test_put_patch_delete_on_detail_are_405(self) -> None:
        t = _tournament("NoWrite", manager=self.user_a)
        url = _detail_url(t.id)
        for method in ("put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.api, method)(url, {}, format="json")
                self.error(response, 405, "method_not_allowed")
        self.assertTrue(Tournament.objects.filter(pk=t.pk).exists())
