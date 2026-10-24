"""``seed_browser_check``: throwaway data for a browser check, and its cleanup.

Seeding costs seconds (the map's sight-line compute, the League's Seasons), so
the read-only tests share one seed per class (``setUpTestData``, with a
class-scoped ``MEDIA_ROOT``; PERF-02). Tests that run ``--clean`` (its on-commit
hook deletes map files, which a rollback does not restore) or need an unseeded
database live in the ``*CleanTests`` classes and seed per test.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from rest_framework.test import APIClient

from core.map_test_fixtures import ClassTempMediaMixin, TempMediaMixin
from core.models import ArenaMap
from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.sim_helpers.map_loader import load_map_context
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
        # line still counts them (teardown_league's orphan sweep would not).
        self.assertIn("Teams: 4", output)

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
