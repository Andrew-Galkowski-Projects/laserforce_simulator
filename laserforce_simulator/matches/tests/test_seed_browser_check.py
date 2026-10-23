"""``seed_browser_check``: throwaway data for a browser check, and its cleanup."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from rest_framework.test import APIClient

from core.map_test_fixtures import TempMediaMixin
from core.models import ArenaMap
from matches.models import GameRound, Match
from matches.sim_helpers.map_loader import load_map_context
from teams.models import Player, Team

_A = "browser-a@example.com"
_B = "browser-b@example.com"
_MAP_NAME = "Browser Check Arena"


def _seed(*args: str) -> str:
    out = StringIO()
    call_command("seed_browser_check", *args, stdout=out)
    return out.getvalue()


class SeedBrowserCheckTests(TempMediaMixin, TestCase):
    def test_seeds_two_accounts_and_account_a_data(self) -> None:
        output = _seed()

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
        self.assertIn(_A, output)
        self.assertIn("browser-check-pass", output)

    def test_refuses_to_seed_twice(self) -> None:
        _seed()
        with self.assertRaisesMessage(CommandError, "--clean"):
            _seed()

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


class SeedBrowserCheckMapTests(TempMediaMixin, TestCase):
    """The configured map and the full-fidelity Single Round played on it."""

    def _mapped_round(self) -> GameRound:
        a = get_user_model().objects.get(email=_A)
        return GameRound.objects.get(
            manager=a, match__isnull=True, arena_map__name=_MAP_NAME
        )

    def test_seeded_map_resolves_via_load_map_context(self) -> None:
        output = _seed()

        arena_map = ArenaMap.objects.get(name=_MAP_NAME)
        movement_ctx, zone_size = load_map_context(arena_map)
        self.assertIsNotNone(movement_ctx)
        self.assertEqual(zone_size, 20)
        self.assertIn(f"Map {arena_map.pk}", output)

    def test_mapped_round_is_full_fidelity_with_movement_events(self) -> None:
        output = _seed()

        game_round = self._mapped_round()
        self.assertIsNotNone(game_round.arena_map_id)
        self.assertEqual(game_round.fidelity, "full")
        self.assertTrue(game_round.events.filter(event_type="movement").exists())
        self.assertIn(f"mapped Single Round {game_round.pk}", output)

    def test_playback_api_returns_a_map_for_the_mapped_round(self) -> None:
        _seed()
        game_round = self._mapped_round()
        api = APIClient()
        api.force_login(game_round.manager)

        response = api.get(f"/api/rounds/{game_round.pk}/playback/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()["data"]["map"])

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
