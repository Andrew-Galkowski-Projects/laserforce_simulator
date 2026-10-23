"""``seed_browser_check``: throwaway data for a browser check, and its cleanup."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from matches.models import GameRound, Match
from teams.models import Player, Team

_A = "browser-a@example.com"
_B = "browser-b@example.com"


def _seed(*args: str) -> str:
    out = StringIO()
    call_command("seed_browser_check", *args, stdout=out)
    return out.getvalue()


class SeedBrowserCheckTests(TestCase):
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
            GameRound.objects.filter(manager=a, match__isnull=True).count(), 1
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

        output = _seed("--clean")

        self.assertFalse(get_user_model().objects.filter(email__in=[_A, _B]).exists())
        self.assertFalse(Match.objects.exclude(manager=other).exists())
        self.assertFalse(GameRound.objects.exclude(manager=other).exists())
        self.assertFalse(Player.objects.exclude(team=kept).exists())
        self.assertEqual(list(Team.objects.all()), [kept])
        self.assertIn("removed", output)
