"""ANG-01b -- ``matches/services.py`` (seam contract §2) + the moved-name re-exports.

Covers ``check_matchup`` order/messages, ``resolve_batch_teams`` (a foreign id
reads exactly like a missing one), the two sandbox creates (Manager stamp +
``ValueError`` -> ``MatchSetupError``), and each builder's key set. Builders
that upgrade fidelity are checked against real batch-saved ``scores`` rounds.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Model
from django.http import Http404
from django.test import TestCase

from conftest import get_shared_manager
from matches.models import GameRound, Match
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_batch_tasks import _make_minimal_arena_map
from teams.models import Team

User = get_user_model()

_FAST_TICKS = 40

_TEAM_H2H_KEYS = {
    "match_record",
    "round_record",
    "score_margin",
    "avg_survivors",
    "top_impactful",
    "per_map_breakdown",
    "detail_list",
    "margin_series",
    "cumulative_wl_series",
}
_PLAYER_H2H_KEYS = {
    "round_record",
    "score_margin",
    "tag_stats",
    "per_role_breakdown",
    "per_map_breakdown",
    "detail_list",
    "margin_series",
    "cumulative_wl_series",
}
_HEATMAP_KEYS = {
    "cell_occupancy_json",
    "player_roster",
    "has_map",
    "arena_map",
    "zone_size",
    "processed_image_url",
}
_REPORT_KEYS = {
    "round_id",
    "round_label",
    "date_played",
    "map_name",
    "red_team_name",
    "blue_team_name",
    "red_points",
    "blue_points",
    "red_eliminated",
    "blue_eliminated",
    "winner_name",
    "red_players",
    "blue_players",
    "red_totals",
    "blue_totals",
    # RV-05 (#286): the series-port result the PDF charts are drawn from.
    "chart_series",
}
_SERIES_KEYS = {"players", "shots", "lives", "points", "sp"}


def _saved_round(red, blue, *, seed: int = 777, arena_map=None) -> GameRound:
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        rounds = BatchSimulator().save_games(
            red, blue, [[seed, False]], 1, arena_map=arena_map
        )
    return GameRound.objects.get(pk=rounds[0].pk)


def _contains_model_instance(value) -> bool:
    if isinstance(value, Model):
        return True
    if isinstance(value, dict):
        return any(_contains_model_instance(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_model_instance(v) for v in value)
    return False


class _ServicesTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01b-services-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.red, self.red_players = make_team_with_slots("SvcRed")
        self.blue, self.blue_players = make_team_with_slots("SvcBlue")


# ---------------------------------------------------------------------------
# check_matchup
# ---------------------------------------------------------------------------


class TestCheckMatchup(_ServicesTestCase):
    def test_valid_matchup_returns_none(self) -> None:
        from matches.services import check_matchup

        self.assertIsNone(check_matchup(self.red, self.blue))

    def test_same_team_message(self) -> None:
        from matches.services import MatchSetupError, check_matchup

        with self.assertRaises(MatchSetupError) as ctx:
            check_matchup(self.red, self.red)
        self.assertEqual(str(ctx.exception), "A team cannot play against itself!")

    def test_same_team_checked_before_roster(self) -> None:
        from matches.services import MatchSetupError, check_matchup

        empty = Team.objects.create(name="Svc Empty")
        with self.assertRaises(MatchSetupError) as ctx:
            check_matchup(empty, empty)
        self.assertEqual(str(ctx.exception), "A team cannot play against itself!")

    def test_red_roster_error_message(self) -> None:
        from matches.services import MatchSetupError, check_matchup

        empty = Team.objects.create(name="Svc Red Empty")
        with self.assertRaises(MatchSetupError) as ctx:
            check_matchup(empty, self.blue)
        self.assertEqual(
            str(ctx.exception),
            f"{empty.name} has an invalid roster: {'; '.join(empty.roster_errors)}",
        )

    def test_blue_roster_error_message(self) -> None:
        from matches.services import MatchSetupError, check_matchup

        empty = Team.objects.create(name="Svc Blue Empty")
        with self.assertRaises(MatchSetupError) as ctx:
            check_matchup(self.red, empty)
        self.assertEqual(
            str(ctx.exception),
            f"{empty.name} has an invalid roster: {'; '.join(empty.roster_errors)}",
        )

    def test_red_checked_before_blue(self) -> None:
        from matches.services import MatchSetupError, check_matchup

        red_empty = Team.objects.create(name="Svc First Empty")
        blue_empty = Team.objects.create(name="Svc Second Empty")
        with self.assertRaises(MatchSetupError) as ctx:
            check_matchup(red_empty, blue_empty)
        self.assertTrue(str(ctx.exception).startswith(red_empty.name))


# ---------------------------------------------------------------------------
# resolve_batch_teams
# ---------------------------------------------------------------------------


class TestResolveBatchTeams(_ServicesTestCase):
    def test_returns_owned_teams_in_order(self) -> None:
        from matches.services import resolve_batch_teams

        red, blue = resolve_batch_teams(self.red.pk, self.blue.pk, self.user_a)
        self.assertEqual((red, blue), (self.red, self.blue))

    def test_equal_ids_is_same_team_error(self) -> None:
        from matches.services import MatchSetupError, resolve_batch_teams

        with self.assertRaises(MatchSetupError) as ctx:
            resolve_batch_teams(999999, 999999, self.user_a)
        self.assertEqual(str(ctx.exception), "A team cannot play against itself!")

    def test_missing_team_message(self) -> None:
        from matches.services import MatchSetupError, resolve_batch_teams

        with self.assertRaises(MatchSetupError) as ctx:
            resolve_batch_teams(self.red.pk, 999999, self.user_a)
        self.assertEqual(str(ctx.exception), "Team 999999 does not exist.")

    def test_foreign_team_reads_like_missing(self) -> None:
        from matches.services import MatchSetupError, resolve_batch_teams

        foreign, _ = make_team_with_slots("SvcForeign")
        foreign.manager = self.user_b
        foreign.save()
        with self.assertRaises(MatchSetupError) as ctx:
            resolve_batch_teams(foreign.pk, self.blue.pk, self.user_a)
        self.assertEqual(str(ctx.exception), f"Team {foreign.pk} does not exist.")

    def test_red_resolved_before_blue(self) -> None:
        from matches.services import MatchSetupError, resolve_batch_teams

        with self.assertRaises(MatchSetupError) as ctx:
            resolve_batch_teams(888881, 888882, self.user_a)
        self.assertEqual(str(ctx.exception), "Team 888881 does not exist.")

    def test_user_none_sees_only_unmanaged(self) -> None:
        from matches.services import MatchSetupError, resolve_batch_teams

        self.red.manager = self.user_a
        self.red.save()
        with self.assertRaises(MatchSetupError) as ctx:
            resolve_batch_teams(self.red.pk, self.blue.pk, None)
        self.assertEqual(str(ctx.exception), f"Team {self.red.pk} does not exist.")

    def test_roster_error_after_lookup(self) -> None:
        from matches.services import MatchSetupError, resolve_batch_teams

        empty = Team.objects.create(name="Svc Batch Empty")
        with self.assertRaises(MatchSetupError) as ctx:
            resolve_batch_teams(self.red.pk, empty.pk, self.user_a)
        self.assertTrue(
            str(ctx.exception).startswith(f"{empty.name} has an invalid roster")
        )


# ---------------------------------------------------------------------------
# create_sandbox_match / create_sandbox_round
# ---------------------------------------------------------------------------


class TestCreateSandbox(_ServicesTestCase):
    def test_create_match_stamps_manager(self) -> None:
        from matches.services import create_sandbox_match

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            match = create_sandbox_match(
                self.red, self.blue, "friendly", arena_map=None, user=self.user_a
            )
        self.assertIsInstance(match, Match)
        match.refresh_from_db()
        self.assertEqual(match.manager_id, self.user_a.pk)
        self.assertEqual(match.game_rounds.count(), 2)

    def test_create_match_user_none_is_unmanaged(self) -> None:
        from matches.services import create_sandbox_match

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            match = create_sandbox_match(
                self.red, self.blue, "friendly", arena_map=None, user=None
            )
        match.refresh_from_db()
        self.assertIsNone(match.manager_id)

    def test_create_match_same_team_raises_without_simulating(self) -> None:
        from matches.services import MatchSetupError, create_sandbox_match

        before = Match.objects.count()
        with self.assertRaises(MatchSetupError):
            create_sandbox_match(
                self.red, self.red, "friendly", arena_map=None, user=self.user_a
            )
        self.assertEqual(Match.objects.count(), before)

    def test_create_match_maps_value_error(self) -> None:
        from matches.services import MatchSetupError, create_sandbox_match

        def _refuse(self, *args, **kwargs):
            raise ValueError("no sight lines")

        with patch.object(BatchSimulator, "simulate_match", _refuse):
            with self.assertRaises(MatchSetupError) as ctx:
                create_sandbox_match(
                    self.red, self.blue, "friendly", arena_map=None, user=self.user_a
                )
        self.assertEqual(str(ctx.exception), "no sight lines")

    def test_create_round_stamps_manager(self) -> None:
        from matches.services import create_sandbox_round

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            game_round = create_sandbox_round(
                self.red, self.blue, arena_map=None, user=self.user_a
            )
        self.assertIsInstance(game_round, GameRound)
        game_round.refresh_from_db()
        self.assertEqual(game_round.manager_id, self.user_a.pk)
        self.assertIsNone(game_round.match_id)

    def test_create_round_maps_value_error(self) -> None:
        from matches.services import MatchSetupError, create_sandbox_round

        def _refuse(self, *args, **kwargs):
            raise ValueError("bad map")

        with patch.object(BatchSimulator, "simulate_single_round_detailed", _refuse):
            with self.assertRaises(MatchSetupError) as ctx:
                create_sandbox_round(
                    self.red, self.blue, arena_map=None, user=self.user_a
                )
        self.assertEqual(str(ctx.exception), "bad map")

    def test_create_round_invalid_roster_raises(self) -> None:
        from matches.services import MatchSetupError, create_sandbox_round

        empty = Team.objects.create(name="Svc Round Empty")
        with self.assertRaises(MatchSetupError):
            create_sandbox_round(empty, self.blue, arena_map=None, user=self.user_a)


# ---------------------------------------------------------------------------
# Comparison builders
# ---------------------------------------------------------------------------


class TestRoundComparison(_ServicesTestCase):
    def test_shared_team_ids(self) -> None:
        from matches.services import shared_team_ids

        green, _ = make_team_with_slots("SvcGreen")
        a = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        b = GameRound.objects.create(round_number=1, team_red=green, team_blue=self.red)
        self.assertEqual(shared_team_ids(a, b), [self.red.pk])
        c = GameRound.objects.create(
            round_number=1, team_red=self.blue, team_blue=self.red
        )
        self.assertEqual(shared_team_ids(a, c), sorted([self.red.pk, self.blue.pk]))

    def test_build_round_comparison_key_set(self) -> None:
        from matches.services import build_round_comparison

        a = _saved_round(self.red, self.blue, seed=1)
        b = _saved_round(self.red, self.blue, seed=2)
        result = build_round_comparison(a, b, self.user_a)
        self.assertEqual(set(result), {"team_ids", "points_series", "deltas"})
        self.assertEqual(result["team_ids"], sorted([self.red.pk, self.blue.pk]))
        for series in result["points_series"]:
            self.assertEqual(set(series), {"team_id", "team_name", "a", "b"})
        self.assertEqual(len(result["deltas"]), 12)

    def test_no_shared_team_raises_value_error(self) -> None:
        from matches.services import build_round_comparison

        green, _ = make_team_with_slots("SvcGreen2")
        yellow, _ = make_team_with_slots("SvcYellow2")
        a = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        b = GameRound.objects.create(round_number=1, team_red=green, team_blue=yellow)
        with self.assertRaises(ValueError):
            build_round_comparison(a, b, self.user_a)

    def test_same_round_raises_value_error(self) -> None:
        from matches.services import build_round_comparison

        a = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        with self.assertRaises(ValueError):
            build_round_comparison(a, a, self.user_a)

    def test_unowned_shared_team_is_http404(self) -> None:
        from matches.services import build_round_comparison

        theirs, _ = make_team_with_slots("SvcTheirs")
        theirs.manager = self.user_b
        theirs.save()
        a = GameRound.objects.create(
            round_number=1, team_red=theirs, team_blue=self.red
        )
        b = GameRound.objects.create(
            round_number=1, team_red=theirs, team_blue=self.blue
        )
        with self.assertRaises(Http404):
            build_round_comparison(a, b, self.user_a)


# ---------------------------------------------------------------------------
# H2H builders
# ---------------------------------------------------------------------------


class TestH2HBuilders(_ServicesTestCase):
    def test_team_h2h_key_set_and_no_model_instances(self) -> None:
        from matches.services import build_team_h2h

        _saved_round(self.red, self.blue)
        result = build_team_h2h(
            self.red, self.blue, provenance="all", date_from=None, date_to=None
        )
        self.assertEqual(set(result), _TEAM_H2H_KEYS)
        self.assertEqual(result["round_record"]["n"], 1)
        self.assertFalse(_contains_model_instance(result))

    def test_team_h2h_empty(self) -> None:
        from matches.services import build_team_h2h

        result = build_team_h2h(
            self.red, self.blue, provenance="all", date_from=None, date_to=None
        )
        self.assertEqual(set(result), _TEAM_H2H_KEYS)
        self.assertEqual(result["round_record"]["n"], 0)
        self.assertEqual(result["detail_list"], [])

    def test_player_h2h_key_set(self) -> None:
        from matches.services import build_player_h2h

        _saved_round(self.red, self.blue)
        result = build_player_h2h(
            self.red_players["commander"],
            self.blue_players["commander"],
            role=None,
            provenance="all",
            date_from=None,
            date_to=None,
        )
        self.assertEqual(set(result), _PLAYER_H2H_KEYS)
        self.assertEqual(result["round_record"]["n"], 1)
        self.assertFalse(_contains_model_instance(result))

    def test_player_h2h_unknown_role_passthrough(self) -> None:
        from matches.services import build_player_h2h

        _saved_round(self.red, self.blue)
        args = (self.red_players["commander"], self.blue_players["commander"])
        kwargs = {"provenance": "all", "date_from": None, "date_to": None}
        unfiltered = build_player_h2h(*args, role=None, **kwargs)
        bogus = build_player_h2h(*args, role="goalie", **kwargs)
        self.assertEqual(bogus["round_record"], unfiltered["round_record"])


# ---------------------------------------------------------------------------
# Round analytics builders
# ---------------------------------------------------------------------------


class TestRoundAnalyticsBuilders(_ServicesTestCase):
    def test_missile_log_keys_and_upgrade(self) -> None:
        from matches.services import build_missile_log

        game_round = _saved_round(self.red, self.blue)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = build_missile_log(game_round)
        self.assertEqual(set(result), {"fired", "hit", "efficiency", "rows"})
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "combat")

    def test_heatmap_keys_and_upgrade(self) -> None:
        from matches.services import build_heatmap

        game_round = _saved_round(self.red, self.blue)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = build_heatmap(game_round)
        self.assertEqual(set(result), _HEATMAP_KEYS)
        self.assertFalse(result["has_map"])
        self.assertIsNone(result["arena_map"])
        self.assertIsNone(result["processed_image_url"])
        for entry in result["player_roster"]:
            self.assertEqual(set(entry), {"id", "name", "role", "team_color"})
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "full")

    def test_heatmap_returns_arena_map_instance(self) -> None:
        from matches.services import build_heatmap

        arena_map = _make_minimal_arena_map("SvcHeatMap")
        game_round = _saved_round(self.red, self.blue, arena_map=arena_map)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = build_heatmap(game_round)
        self.assertTrue(result["has_map"])
        self.assertEqual(result["arena_map"], arena_map)
        self.assertIsInstance(result["processed_image_url"], str)

    def test_report_data_keys_without_upgrade(self) -> None:
        from matches.services import build_round_report_data

        game_round = _saved_round(self.red, self.blue)
        result = build_round_report_data(game_round)
        self.assertEqual(set(result), _REPORT_KEYS)
        self.assertEqual(result["round_id"], game_round.pk)
        self.assertEqual(result["round_label"], "Single Round")
        self.assertIsInstance(result["date_played"], str)
        self.assertIsNone(result["map_name"])
        self.assertEqual(len(result["red_players"]), 6)
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "scores")

    # ----- RV-05 (#286): chart_series -----

    def test_report_data_chart_series_for_round_with_events(self) -> None:
        """RV-05: a ``full``-fidelity round with GameEvent rows yields
        ``chart_series`` in the series-port shape, built from the same rows
        the events page sends: every player keyed by ``str(id)``, every
        series starting at second 0, and at least one series that moved."""
        from matches.services import build_round_report_data

        game_round = _saved_round(self.red, self.blue)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().ensure_fidelity(game_round, "full")
        game_round.refresh_from_db()
        self.assertGreater(game_round.events.count(), 0)

        series = build_round_report_data(game_round)["chart_series"]

        self.assertEqual(set(series), _SERIES_KEYS)
        player_ids = {str(ps.player_id) for ps in game_round.player_states.all()}
        self.assertEqual(len(player_ids), 12)
        self.assertEqual({str(p["id"]) for p in series["players"]}, player_ids)
        for entry in series["players"]:
            self.assertEqual(set(entry), {"id", "name", "team"})
            self.assertIn(entry["team"], {"red", "blue"})
        for key in ("shots", "lives", "points", "sp"):
            self.assertEqual(set(series[key]), player_ids, key)
            for points in series[key].values():
                self.assertEqual(points[0][0], 0.0, key)
        moved = sum(
            len(points) > 1
            for key in ("shots", "lives", "points", "sp")
            for points in series[key].values()
        )
        self.assertGreater(moved, 0, "a simulated round must move some series")

    def test_report_data_chart_series_start_points_only_without_events(self) -> None:
        """RV-05: a round with no GameEvent rows yields ``chart_series`` with
        exactly the start point per player per series (shots at ``ss``,
        lives at ``sl``, points 0, SP 0)."""
        from matches.services import build_round_report_data

        game_round = _saved_round(self.red, self.blue)
        self.assertEqual(game_round.events.count(), 0)

        series = build_round_report_data(game_round)["chart_series"]

        self.assertEqual(set(series), _SERIES_KEYS)
        states = list(game_round.player_states.all())
        self.assertEqual(len(series["players"]), len(states))
        for state in states:
            pid = str(state.player_id)
            self.assertEqual(series["shots"][pid], [(0.0, state.starting_shots)])
            self.assertEqual(series["lives"][pid], [(0.0, state.starting_lives)])
            self.assertEqual(series["points"][pid], [(0.0, 0)])
            self.assertEqual(series["sp"][pid], [(0.0, 0)])
        # The builder itself still performs no fidelity upgrade (the callers do).
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "scores")

    def test_round_report_filename(self) -> None:
        from matches.services import round_report_filename

        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.assertEqual(
            round_report_filename(game_round),
            f"round-{game_round.pk}-svcred-team-vs-svcblue-team.pdf",
        )

    def test_round_report_filename_slug_fallback(self) -> None:
        from matches.services import round_report_filename

        self.red.name = "!!!"
        self.red.save()
        self.blue.name = "***"
        self.blue.save()
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.assertEqual(
            round_report_filename(game_round), f"round-{game_round.pk}-red-vs-blue.pdf"
        )


# ---------------------------------------------------------------------------
# Re-exports kept for existing importers (contract §2 / §4)
# ---------------------------------------------------------------------------


class TestViewsReExports(TestCase):
    def test_moved_helpers_reexported_from_views(self) -> None:
        from matches import services, views

        self.assertIs(views._shared_team_ids, services.shared_team_ids)
        self.assertIs(views._comparison_row, services._comparison_row)
        self.assertIs(views._cumulative_team_points, services._cumulative_team_points)
        self.assertIs(views._player_row, services._player_row)

    def test_round_row_builders_live_in_services(self) -> None:
        """RV-05 (#286): the two playback row builders the series port
        consumes moved to ``services`` (which must not import ``views``);
        ``views`` keeps re-exporting them for its existing importers."""
        from matches import services, views

        self.assertIs(views._round_players_data, services._round_players_data)
        self.assertIs(views.round_playback_payload, services.round_playback_payload)

    def test_build_save_status_response_alias(self) -> None:
        from matches import views

        self.assertIs(
            views._build_save_status_response, views.build_save_status_response
        )
