"""ANG-01b -- ``matches/services.py`` (seam contract §2) and its helpers.

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
from matches.models import GameEvent, GameRound, Match, PlayerRoundState
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_batch_tasks import _make_minimal_arena_map
from teams.models import Player, Team

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
        self.assertEqual(set(result), _HEATMAP_KEYS)
        self.assertEqual(result["arena_map"], arena_map)

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

    def test_round_players_data_is_in_id_order(self) -> None:
        """RV-05 review: the series port echoes this order into the chart
        legend and the per-team hue index, so the row builder pins ``id``
        (the insertion order) instead of leaving the order to the database."""
        from matches.services import _round_players_data

        game_round = _saved_round(self.red, self.blue)
        ids = [row["id"] for row in _round_players_data(game_round)]
        expected = [ps.player_id for ps in game_round.player_states.order_by("id")]
        self.assertEqual(len(ids), 12)
        self.assertEqual(ids, expected)

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
# Helper cases moved from ``views_tests.py`` (ANG-08 D2 deleted that file):
# RV-01 comparison helpers, the ``_player_row`` elimination rule and the
# ``round_playback_payload`` event shape the HTML events endpoint used to pin.
# These build minimal DB fixtures directly (no simulator run, no RNG) except
# the playback-payload cases, which need a round with persisted events.
# ---------------------------------------------------------------------------


# The 12 per-player stat keys the RV-01 delta table is contracted to expose.
_RV01_STAT_KEYS = (
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
)


def _bare_round(team_red, team_blue, round_number: int = 1) -> GameRound:
    """A bare completed GameRound for two teams (no simulator run)."""
    return GameRound.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        round_number=round_number,
        is_completed=True,
    )


def _state(game_round, player, *, team_color, role, **stats) -> PlayerRoundState:
    """A PlayerRoundState row with explicit stat values (no RNG)."""
    return PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color=team_color,
        role=role,
        **stats,
    )


class TestRv01ComparisonHelpers(TestCase):
    def test_shared_team_ids_is_side_agnostic(self) -> None:
        from matches.services import shared_team_ids

        team_x, _ = make_team_with_slots("Rv01ShareX")
        team_y, _ = make_team_with_slots("Rv01ShareY")
        team_z, _ = make_team_with_slots("Rv01ShareZ")

        # Round A: X red, Y blue.  Round B: X blue, Z red.
        round_a = _bare_round(team_x, team_y)
        round_b = _bare_round(team_z, team_x)

        self.assertEqual(shared_team_ids(round_a, round_b), [team_x.id])

    def test_shared_team_ids_distinct_teams_empty(self) -> None:
        from matches.services import shared_team_ids

        red_a, _ = make_team_with_slots("Rv01DistRA")
        blue_a, _ = make_team_with_slots("Rv01DistBA")
        red_b, _ = make_team_with_slots("Rv01DistRB")
        blue_b, _ = make_team_with_slots("Rv01DistBB")

        round_a = _bare_round(red_a, blue_a)
        round_b = _bare_round(red_b, blue_b)

        self.assertEqual(shared_team_ids(round_a, round_b), [])

    def _rows(self, game_round, team_ids: set[int]) -> list[dict]:
        from matches.services import _comparison_row

        return [
            _comparison_row(ps)
            for ps in game_round.player_states.select_related("player").all()
            if ps.player.team_id in team_ids
        ]

    def test_player_stat_deltas_row_shape_for_player_in_both_rounds(self) -> None:
        from matches.round_comparison import player_stat_deltas

        team_x, players = make_team_with_slots("Rv01DeltaX")
        blue_a, _ = make_team_with_slots("Rv01DeltaBA")
        blue_b, _ = make_team_with_slots("Rv01DeltaBB")

        round_a = _bare_round(team_x, blue_a)
        round_b = _bare_round(team_x, blue_b)
        commander = players["commander"]

        # Same player, both rounds, different points (100 -> 250 => delta 150).
        _state(
            round_a,
            commander,
            team_color="red",
            role="commander",
            points_scored=100,
            tags_made=4,
        )
        _state(
            round_b,
            commander,
            team_color="blue",
            role="commander",
            points_scored=250,
            tags_made=10,
        )

        rows = player_stat_deltas(
            self._rows(round_a, {team_x.id}), self._rows(round_b, {team_x.id})
        )
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]

        for key in ("player_id", "name", "role_a", "role_b", "side_a", "side_b"):
            self.assertIn(key, row)
        self.assertEqual(row["player_id"], commander.id)
        self.assertEqual(row["name"], commander.name)
        self.assertEqual((row["role_a"], row["role_b"]), ("commander", "commander"))
        self.assertEqual((row["side_a"], row["side_b"]), ("red", "blue"))

        stats = row["stats"]
        self.assertEqual(set(stats), set(_RV01_STAT_KEYS))
        for cell in stats.values():
            self.assertEqual(set(cell), {"a", "b", "delta"})

        ps = stats["points_scored"]
        self.assertEqual((ps["a"], ps["b"], ps["delta"]), (100, 250, 150))

        # Every present-in-both stat: delta == b - a.
        for key, cell in stats.items():
            if cell["a"] is not None and cell["b"] is not None:
                self.assertEqual(cell["delta"], cell["b"] - cell["a"], key)

    def test_player_stat_deltas_player_in_only_one_round(self) -> None:
        from matches.round_comparison import player_stat_deltas

        team_x, players = make_team_with_slots("Rv01OneSide")
        blue_a, _ = make_team_with_slots("Rv01OneSideBA")
        blue_b, _ = make_team_with_slots("Rv01OneSideBB")

        round_a = _bare_round(team_x, blue_a)
        round_b = _bare_round(team_x, blue_b)

        # Heavy appears only in round A.
        heavy = players["heavy"]
        _state(
            round_a,
            heavy,
            team_color="red",
            role="heavy",
            points_scored=80,
            tags_made=3,
        )

        rows = player_stat_deltas(
            self._rows(round_a, {team_x.id}), self._rows(round_b, {team_x.id})
        )
        matching = [r for r in rows if r["player_id"] == heavy.id]
        self.assertEqual(len(matching), 1, rows)
        row = matching[0]

        # Present in A only -> side_b / role_b are None.
        self.assertEqual((row["role_a"], row["side_a"]), ("heavy", "red"))
        self.assertIsNone(row["role_b"])
        self.assertIsNone(row["side_b"])

        # The missing side's stat values + deltas are None.
        for key, cell in row["stats"].items():
            self.assertIsNone(cell["b"], key)
            self.assertIsNone(cell["delta"], key)
        # The present side keeps its real value.
        self.assertEqual(row["stats"]["points_scored"]["a"], 80)

    def test_cumulative_team_points_running_sum_with_zero_award(self) -> None:
        from matches.services import _cumulative_team_points

        red, players = make_team_with_slots("Rv01Cum")
        blue, _ = make_team_with_slots("Rv01CumBlue")
        game_round = _bare_round(red, blue)

        commander = players["commander"]
        heavy = players["heavy"]
        _state(game_round, commander, team_color="red", role="commander")
        _state(game_round, heavy, team_color="red", role="heavy")

        # GameEvent.points_awarded is NOT NULL with default 0, so the
        # coalesce-to-0 contract is exercised by a 0-point row that must not
        # perturb the running sum.
        for ts, actor, points in (
            (10, commander, 100),
            (20, heavy, 0),
            (30, commander, 50),
        ):
            GameEvent.objects.create(
                game_round=game_round,
                timestamp=ts,
                event_type="tag",
                actor=actor,
                points_awarded=points,
            )

        series = _cumulative_team_points(game_round, red.id)
        self.assertTrue(isinstance(series, list) and series, series)
        for point in series:
            self.assertTrue(isinstance(point, list) and len(point) == 2, point)

        cumulative = [pt[1] for pt in series]
        for prev, cur in zip(cumulative, cumulative[1:]):
            self.assertGreaterEqual(cur, prev)
        self.assertEqual(cumulative[-1], 150)

    def test_cumulative_team_points_empty_round_returns_empty(self) -> None:
        from matches.services import _cumulative_team_points

        red, _ = make_team_with_slots("Rv01CumEmpty")
        blue, _ = make_team_with_slots("Rv01CumEmptyBlue")
        game_round = _bare_round(red, blue)

        self.assertEqual(_cumulative_team_points(game_round, red.id), [])


class TestPlayerRowEliminationSemantic(TestCase):
    """The ``_player_row`` ``is_eliminated`` / ``eliminated_timestamp`` rule.

    Pre-TIME-01 the survived sentinel was tick 900; a hardcoded ``val > 900``
    check wrongly painted second-half eliminations as survivors. TIME-01 raised
    the sentinel to ``SURVIVED_SENTINEL = 1801``. These cases pin the boundary.
    """

    def _row_for(self, ts: int) -> dict:
        from matches.services import _player_row

        team = Team.objects.create(name=f"PlayerRowElim-{ts!r}")
        player = Player.objects.create(team=team, name="Test", height="6'0\"")
        gr = GameRound.objects.create(
            team_red=team, team_blue=team, round_number=1, is_completed=True
        )
        state = _state(
            gr,
            player,
            team_color="red",
            role="scout",
            was_eliminated_at=ts,
            final_lives=0 if (ts < 1801 and ts != 0) else 5,
        )
        return _player_row(state)

    def test_survived_full_round_sentinel_carries_false(self) -> None:
        row = self._row_for(1801)
        self.assertIs(row["is_eliminated"], False)
        self.assertEqual(row["eliminated_timestamp"], "")

    def test_eliminated_in_first_half_carries_true(self) -> None:
        row = self._row_for(478)
        self.assertIs(row["is_eliminated"], True)
        self.assertNotEqual(row["eliminated_timestamp"], "")

    def test_eliminated_in_second_half_carries_true(self) -> None:
        # Regression: pre-TIME-01 the ``> 900`` check wrongly survived this.
        self.assertIs(self._row_for(1277)["is_eliminated"], True)

    def test_eliminated_at_old_threshold_carries_true(self) -> None:
        self.assertIs(self._row_for(920)["is_eliminated"], True)

    def test_eliminated_just_below_sentinel_carries_true(self) -> None:
        self.assertIs(self._row_for(1800)["is_eliminated"], True)

    def test_zero_eliminated_at_carries_false(self) -> None:
        # ``0`` is the legacy "never set" sentinel.
        row = self._row_for(0)
        self.assertIs(row["is_eliminated"], False)
        self.assertEqual(row["eliminated_timestamp"], "")


class TestRoundPlaybackPayload(_ServicesTestCase):
    """M-1 / RES-02b: the ``round_playback_payload`` event + player shapes.

    Was pinned through the deleted HTML ``game_round_events_data`` endpoint;
    the payload itself still feeds the Round report and the League live watch.
    """

    _EVENT_KEYS = {
        "type",
        "ts",
        "tf",
        "icon",
        "desc",
        "pts",
        "aid",
        "an",
        "at",
        "tid",
        "tn",
        "tt",
        "meta",
    }
    _PLAYER_KEYS = {"id", "name", "team", "role", "sl", "ss"}
    _RES02_SP_TYPES = {"tag", "missile", "special", "base_capture"}
    _ACTOR_BLOCK_KEYS = ("actor_role", "actor_shots", "actor_lives", "actor_points")
    _TARGET_BLOCK_KEYS = (
        "target_role",
        "target_shots",
        "target_lives",
        "target_points",
    )

    def _full_round(self) -> GameRound:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            game_round = BatchSimulator().simulate_single_round_detailed(
                self.red, self.blue
            )
            BatchSimulator().ensure_fidelity(game_round, "full")
        game_round.refresh_from_db()
        self.assertGreater(game_round.events.count(), 0)
        return game_round

    def test_events_match_db_in_order_with_full_shape(self) -> None:
        from matches.services import round_playback_payload

        game_round = self._full_round()
        events, players = round_playback_payload(game_round)
        self.assertEqual(len(events), game_round.events.count())
        ts = [e["ts"] for e in events]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(set(events[0]), self._EVENT_KEYS)
        self.assertIsInstance(events[0]["meta"], dict)
        # A targetless event uses the -1 sentinel the client expects.
        self.assertTrue(
            all(e["tid"] == -1 or isinstance(e["tid"], int) for e in events)
        )
        self.assertEqual(len(players), game_round.player_states.count())
        for p in players:
            self.assertEqual(set(p), self._PLAYER_KEYS)
            self.assertIn(p["team"], ("red", "blue"))

    def test_every_row_carries_the_universal_actor_and_target_blocks(self) -> None:
        """RES-02b: every row carries the actor block (+ ``sp``); every row with
        a target carries the target block; ``base_capture`` rows carry ``sp``,
        never ``special_points``."""
        from matches.services import round_playback_payload

        events, _players = round_playback_payload(self._full_round())
        sp_seen = 0
        targeted = 0
        for row in events:
            meta = row["meta"]
            self.assertTrue(isinstance(row["aid"], int) and row["aid"] != -1, row)
            for key in (*self._ACTOR_BLOCK_KEYS, "sp"):
                self.assertIn(key, meta, row)
            self.assertIsInstance(meta["actor_role"], str)
            self.assertGreaterEqual(meta["actor_shots"], 0)
            self.assertGreaterEqual(meta["actor_lives"], 0)
            self.assertIsInstance(meta["actor_points"], int)
            self.assertTrue(0 <= meta["sp"] <= 99, meta)
            if row["tid"] != -1:
                targeted += 1
                for key in self._TARGET_BLOCK_KEYS:
                    self.assertIn(key, meta, row)
            if row["type"] in self._RES02_SP_TYPES:
                sp_seen += 1
                if row["type"] == "base_capture":
                    self.assertNotIn("special_points", meta)
        # The fixture must exercise both branches so the asserts have bite.
        self.assertGreater(sp_seen, 0)
        self.assertGreater(targeted, 0)
