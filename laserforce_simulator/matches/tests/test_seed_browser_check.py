"""``seed_browser_check``: throwaway data for a browser check, and its cleanup.

Seeding costs seconds (the map's sight-line compute, the League's Seasons), so
the read-only tests share one seed per class (``setUpTestData``, with a
class-scoped ``MEDIA_ROOT``; PERF-02). Tests that run ``--clean`` (its on-commit
hook deletes map files, which a rollback does not restore) or need an unseeded
database live in the ``*CleanTests`` classes and seed per test.
"""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from rest_framework.test import APIClient

from core.map_test_fixtures import ClassTempMediaMixin, TempMediaMixin
from core.models import ArenaMap
from matches.models import (
    GameRound,
    League,
    Match,
    PlayerRoundState,
    Season,
    Tournament,
)
from matches.sim_helpers.map_loader import load_map_context
from matches.simulation import BatchSimulator
from teams.models import Player, Team

_A = "browser-a@example.com"
_B = "browser-b@example.com"
_MAP_NAME = "Browser Check Arena"
_LEAGUE_NAME = "Browser Check League"


def _seed(*args: str) -> str:
    out = StringIO()
    call_command("seed_browser_check", *args, stdout=out)
    return out.getvalue()


class SeedBrowserCheckTests(ClassTempMediaMixin, TestCase):
    """The default seed (no ``--league``), made once for the class."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed()

    def test_seeds_two_accounts_and_account_a_data(self) -> None:
        user_model = get_user_model()
        a = user_model.objects.get(email=_A)
        b = user_model.objects.get(email=_B)
        self.assertTrue(a.check_password("browser-check-pass"))
        self.assertTrue(b.check_password("browser-check-pass"))
        self.assertEqual(Team.objects.filter(manager=a).count(), 4)
        self.assertTrue(all(t.is_valid_roster for t in Team.objects.filter(manager=a)))
        self.assertFalse(Team.objects.filter(manager=b).exists())

        matches = Match.objects.filter(manager=a)
        self.assertEqual(matches.filter(is_completed=True).count(), 1)
        unfinished = matches.get(is_completed=False)
        self.assertIsNone(unfinished.winner)
        self.assertGreater(unfinished.red_round1_points, 0)
        self.assertEqual(
            GameRound.objects.filter(
                manager=a, match__isnull=True, arena_map__isnull=True
            ).count(),
            1,
        )
        self.assertIn(_A, self.output)
        self.assertIn("browser-check-pass", self.output)

    def test_refuses_to_seed_twice(self) -> None:
        with self.assertRaisesMessage(CommandError, "--clean"):
            _seed()

    def test_without_the_flag_no_league_is_seeded(self) -> None:
        self.assertFalse(League.objects.exists())
        self.assertFalse(Season.objects.exists())
        self.assertNotIn("League", self.output)


class SeedBrowserCheckCleanTests(TempMediaMixin, TestCase):
    def test_clean_removes_only_the_seeded_accounts_rows(self) -> None:
        other = get_user_model().objects.create_user(
            email="owner@example.com", password="x"
        )
        kept = Team.objects.create(name="Owner Team", manager=other)
        _seed()

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertFalse(get_user_model().objects.filter(email__in=[_A, _B]).exists())
        self.assertFalse(Match.objects.exclude(manager=other).exists())
        self.assertFalse(GameRound.objects.exclude(manager=other).exists())
        self.assertFalse(Player.objects.exclude(team=kept).exists())
        self.assertEqual(list(Team.objects.all()), [kept])
        self.assertIn("removed", output)

    def test_clean_counts_each_labelled_model_not_its_cascade(self) -> None:
        # A GameRound delete also cascades its GameEvents and PlayerRoundStates;
        # the summary counts only the rows its label names.
        _seed()
        users = get_user_model().objects.filter(email__in=[_A, _B])
        rounds = GameRound.objects.filter(manager__in=users).count()
        matches = Match.objects.filter(manager__in=users).count()
        players = Player.objects.filter(team__manager__in=users).count()

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertIn(
            f"removed Single Rounds: {rounds}, Matches: {matches}, "
            f"Players: {players}, Teams: 4, Accounts: 2, Maps: 1",
            output,
        )


class SeedBrowserCheckMapTests(ClassTempMediaMixin, TestCase):
    """The configured map and the full-fidelity Single Round played on it."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed()

    def _mapped_round(self) -> GameRound:
        a = get_user_model().objects.get(email=_A)
        return GameRound.objects.get(
            manager=a, match__isnull=True, arena_map__name=_MAP_NAME
        )

    def test_seeded_map_image_is_under_the_class_media_root(self) -> None:
        arena_map = ArenaMap.objects.get(name=_MAP_NAME)

        self.assertTrue(self.media_root.joinpath(arena_map.image.name).exists())

    def test_seeded_map_resolves_via_load_map_context(self) -> None:
        arena_map = ArenaMap.objects.get(name=_MAP_NAME)
        movement_ctx, zone_size = load_map_context(arena_map)
        self.assertIsNotNone(movement_ctx)
        self.assertEqual(zone_size, 20)
        self.assertIn(f"Map {arena_map.pk}", self.output)

    def test_mapped_round_is_full_fidelity_with_movement_events(self) -> None:
        game_round = self._mapped_round()
        self.assertIsNotNone(game_round.arena_map_id)
        self.assertEqual(game_round.fidelity, "full")
        self.assertTrue(game_round.events.filter(event_type="movement").exists())
        self.assertIn(f"mapped Single Round {game_round.pk}", self.output)

    def test_compare_rounds_has_points_for_both_single_rounds(self) -> None:
        # The two Single Rounds share their Teams, so they are the Compare
        # Rounds check pair; both need events for the points-over-time chart.
        mapped = self._mapped_round()
        mapless = GameRound.objects.get(
            manager=mapped.manager, match__isnull=True, arena_map__isnull=True
        )
        api = APIClient()
        api.force_login(mapped.manager)

        response = api.get(
            "/api/rounds/compare/", {"round_a": mapless.pk, "round_b": mapped.pk}
        )

        self.assertEqual(response.status_code, 200, response.content)
        series = response.json()["data"]["points_series"]
        self.assertTrue(series)
        for team in series:
            self.assertTrue(team["a"], team["team_name"])
            self.assertTrue(team["b"], team["team_name"])

    def test_missile_log_has_one_friendly_fire_row_beside_ordinary_ones(
        self,
    ) -> None:
        # A simulated Round rarely fires a missile at a teammate, so the seed
        # marks one: the Missile log check needs its tinted row (ANG-06-10 retro).
        game_round = self._mapped_round()
        api = APIClient()
        api.force_login(game_round.manager)

        response = api.get(f"/api/rounds/{game_round.pk}/missile-log/")

        self.assertEqual(response.status_code, 200, response.content)
        flags = [row["friendly_fire"] for row in response.json()["data"]["rows"]]
        self.assertEqual(flags.count(True), 1, flags)
        self.assertIn(False, flags)

    def test_playback_api_returns_a_map_for_the_mapped_round(self) -> None:
        game_round = self._mapped_round()
        api = APIClient()
        api.force_login(game_round.manager)

        response = api.get(f"/api/rounds/{game_round.pk}/playback/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()["data"]["map"])


class SeedBrowserCheckMapCleanTests(TempMediaMixin, TestCase):
    def test_clean_removes_the_map_and_its_files(self) -> None:
        _seed()
        arena_map = ArenaMap.objects.get(name=_MAP_NAME)
        self.assertTrue(self.media_root.joinpath(arena_map.image.name).exists())

        with self.captureOnCommitCallbacks(execute=True):
            _seed("--clean")

        self.assertFalse(ArenaMap.objects.filter(name=_MAP_NAME).exists())
        self.assertFalse(self.media_root.joinpath(arena_map.image.name).exists())
        self.assertFalse(self.processed_cache_path(arena_map).exists())

    def test_clean_twice_is_harmless(self) -> None:
        _seed()
        with self.captureOnCommitCallbacks(execute=True):
            _seed("--clean")
        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")
        self.assertIn("removed", output)


def _api_for_a() -> APIClient:
    api = APIClient()
    api.force_login(get_user_model().objects.get(email=_A))
    return api


class SeedBrowserCheckLeagueTests(ClassTempMediaMixin, TestCase):
    """``--league``: one League with two completed Seasons and an active one."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed("--league")

    def test_league_flag_seeds_a_league_with_completed_and_active_seasons(
        self,
    ) -> None:
        a = get_user_model().objects.get(email=_A)
        league = League.objects.get(name=_LEAGUE_NAME)
        self.assertEqual(league.manager, a)
        seasons = list(league.seasons.order_by("id"))
        self.assertEqual(
            [s.state for s in seasons], ["completed", "completed", "active"]
        )
        for season in seasons:
            self.assertTrue(season.matches.filter(is_completed=True).exists())
            self.assertTrue(
                PlayerRoundState.objects.filter(
                    game_round__match__season=season
                ).exists()
            )
        api = _api_for_a()
        for season in seasons[:2]:
            standings = api.get(f"/api/seasons/{season.pk}/standings/").json()
            self.assertTrue(standings["data"]["groups"][0]["rows"])
            awards = api.get(f"/api/seasons/{season.pk}/awards/").json()["data"]
            self.assertTrue(awards["has_rounds"])
            self.assertIsNotNone(awards["most_points"])
        season_ids = ", ".join(str(s.pk) for s in seasons)
        self.assertIn(f"League {league.pk} ({_LEAGUE_NAME})", self.output)
        self.assertIn(f"Seasons {season_ids}", self.output)

    def test_active_season_has_a_built_playoff_bracket(self) -> None:
        # ANG-06-15 retro: the Playoffs screen needs a bracket to draw.
        league = League.objects.get(name=_LEAGUE_NAME)
        response = _api_for_a().get(f"/api/leagues/{league.pk}/playoffs/")
        self.assertEqual(response.status_code, 200, response.content)
        brackets = response.json()["data"]["brackets"]
        self.assertEqual(len(brackets), 1)
        self.assertFalse(brackets[0]["pending"])
        first_round = brackets[0]["rounds"][0]["nodes"]
        self.assertTrue(first_round)
        self.assertTrue(all(n["team_a"] and n["team_b"] for n in first_round))

    def test_league_history_and_player_apis_return_data_for_account_a(
        self,
    ) -> None:
        league = League.objects.get(name=_LEAGUE_NAME)
        player = league.current_team.slot_commander
        api = _api_for_a()

        history = api.get(f"/api/leagues/{league.pk}/history/")
        player_page = api.get(f"/api/leagues/{league.pk}/players/{player.pk}/")
        owner_eval = api.get(
            f"/api/seasons/{league.seasons.order_by('id')[0].pk}/owner-evaluation/"
        )

        for response in (history, player_page, owner_eval):
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.json()["data"])
        self.assertEqual(len(history.json()["data"]), 3)
        self.assertEqual(len(player_page.json()["data"]["seasons"]), 3)
        self.assertIsNotNone(player_page.json()["data"]["career"])

    def test_free_agents_api_lists_the_seeded_pool(self) -> None:
        # ANG-06-16 retro: the Free Agents table needs a pool to draw.
        league = League.objects.get(name=_LEAGUE_NAME)

        response = _api_for_a().get(f"/api/leagues/{league.pk}/free-agents/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["meta"]["count"], 3)

    def test_player_page_has_a_ratings_history_line(self) -> None:
        # ANG-06-16 retro: the ratings-history chart needs one point per Season
        # and a line that moves.
        league = League.objects.get(name=_LEAGUE_NAME)
        player = league.current_team.slot_commander

        response = _api_for_a().get(f"/api/leagues/{league.pk}/players/{player.pk}/")

        history = response.json()["data"]["ratings_history"]
        self.assertEqual(len(history), 3)
        self.assertEqual(len({row["overall_rating"] for row in history}), 3)


class SeedBrowserCheckLeagueCleanTests(TempMediaMixin, TestCase):
    """``--league`` reruns and ``--clean``, each from its own seed or none."""

    def test_clean_removes_the_league_and_everything_under_it(self) -> None:
        _seed("--league")

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertFalse(League.objects.exists())
        self.assertFalse(Season.objects.exists())
        self.assertFalse(Match.objects.exists())
        self.assertFalse(GameRound.objects.exists())
        self.assertFalse(PlayerRoundState.objects.exists())
        self.assertFalse(Team.objects.exists())
        self.assertIn("Leagues: 1", output)
        # The League goes without taking A's Teams with it, so the rest of the
        # line still counts them (teardown_league's orphan sweep would not):
        # A's four Teams plus the free-agent pool Team (ANG-06-16 retro).
        self.assertIn("Teams: 5", output)

    def test_rerun_refuses_and_leaves_one_league(self) -> None:
        _seed("--league")
        with self.assertRaisesMessage(CommandError, "--clean"):
            _seed("--league")
        self.assertEqual(League.objects.filter(name=_LEAGUE_NAME).count(), 1)

        with self.captureOnCommitCallbacks(execute=True):
            _seed("--clean")
        _seed("--league")
        self.assertEqual(League.objects.filter(name=_LEAGUE_NAME).count(), 1)

    def test_refuses_when_a_stray_league_of_that_name_exists(self) -> None:
        League.objects.create(name=_LEAGUE_NAME)

        with self.assertRaisesMessage(CommandError, "--clean"):
            _seed("--league")
        self.assertEqual(League.objects.filter(name=_LEAGUE_NAME).count(), 1)

    def test_clean_removes_a_stray_unmanaged_league_of_that_name(self) -> None:
        League.objects.create(name=_LEAGUE_NAME)

        with self.captureOnCommitCallbacks(execute=True):
            _seed("--clean")

        self.assertFalse(League.objects.filter(name=_LEAGUE_NAME).exists())


# Short Rounds: the Tournament seed plays real Matches (ANG-06-13b retro).
_FAST_TICKS = 40


def _seed_tournaments() -> str:
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        return _seed("--tournaments")


class SeedBrowserCheckTournamentsTests(ClassTempMediaMixin, TestCase):
    """``--tournaments``: one sandbox Tournament per format and stage, for A."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed_tournaments()

    def _detail(self, name: str) -> dict:
        tournament = Tournament.objects.get(name=name)
        response = _api_for_a().get(f"/api/tournaments/{tournament.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def test_every_tournament_belongs_to_account_a(self) -> None:
        a = get_user_model().objects.get(email=_A)
        names = set(Tournament.objects.values_list("name", flat=True))
        self.assertEqual(names, set(_TOURNAMENT_NAMES))
        self.assertFalse(Tournament.objects.exclude(manager=a).exists())

    def test_each_tournament_sits_at_its_stage(self) -> None:
        single = self._detail("BC Single Elim")
        self.assertEqual(single["state"], "active")
        self.assertTrue(single["can_play"])
        self.assertTrue(
            any(n["games"] for r in single["brackets"][0]["rounds"] for n in r["nodes"])
        )

        double = self._detail("BC Double Elim")
        self.assertEqual(double["state"], "completed")
        self.assertIsNotNone(double["champion"])

        round_robin = self._detail("BC Round Robin")
        self.assertEqual(round_robin["state"], "active")
        self.assertTrue(round_robin["rr_crosstable"])

        rrde = self._detail("BC RR to DE")
        self.assertEqual(rrde["stage"], "seeding")
        self.assertTrue(rrde["cut_labels"])

        swiss = self._detail("BC Swiss")
        self.assertEqual(swiss["stage"], "swiss")
        self.assertEqual(len(swiss["brackets"][0]["rounds"]), 2)

        setup = self._detail("BC Setup")
        self.assertEqual(setup["state"], "setup")
        self.assertEqual(len(setup["participants"]), 4)

        pool = self._detail("BC Player Pool")
        self.assertEqual(pool["state"], "setup")
        self.assertTrue(pool["is_drawn"])
        self.assertEqual(pool["pool_size"], 24)

    def test_summary_names_each_tournament_id(self) -> None:
        for name in _TOURNAMENT_NAMES:
            pk = Tournament.objects.get(name=name).pk
            self.assertIn(f"{pk} ({name})", self.output)


_TOURNAMENT_NAMES = (
    "BC Single Elim",
    "BC Double Elim",
    "BC Round Robin",
    "BC RR to DE",
    "BC Swiss",
    "BC Setup",
    "BC Player Pool",
)


class SeedBrowserCheckTournamentsCleanTests(TempMediaMixin, TestCase):
    def test_clean_removes_the_tournaments_their_matches_and_pool_players(
        self,
    ) -> None:
        _seed_tournaments()

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertFalse(Tournament.objects.exists())
        self.assertFalse(Match.objects.exists())
        self.assertFalse(GameRound.objects.exists())
        # The pool's generated Players sit on the global Free Agents Team.
        self.assertFalse(Player.objects.exists())
        self.assertFalse(Team.objects.exclude(name="Free Agents").exists())
        self.assertIn(f"Tournaments: {len(_TOURNAMENT_NAMES)}", output)
