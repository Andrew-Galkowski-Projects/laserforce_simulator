"""Seed throwaway data for a browser check, and remove it again.

Two Accounts: A owns four valid Teams, a simulated sandbox Match, a Single
Round and an unfinished Match (round 1 only, like a League Match mid-Season);
B owns nothing, for cross-Account checks. ``--clean`` deletes both Accounts and
every row they own. For a worktree's disposable database (ADR-0004).
"""

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from matches.models import GameRound, Match
from matches.services import create_sandbox_match, create_sandbox_round
from teams.forms import GenerateLeagueForm
from teams.models import Player, Team
from teams.services import run_player_generation

EMAIL_A = "browser-a@example.com"
EMAIL_B = "browser-b@example.com"
PASSWORD = "browser-check-pass"


class Command(BaseCommand):
    help = "Seed (or --clean) two throwaway Accounts and Account A's Teams and Matches."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--clean", action="store_true", help="delete the seeded data instead"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["clean"]:
            self._clean()
        else:
            self._seed()

    def _seed(self) -> None:
        user_model = get_user_model()
        if user_model.objects.filter(email__in=[EMAIL_A, EMAIL_B]).exists():
            raise CommandError("Already seeded: run seed_browser_check --clean first.")

        a = user_model.objects.create_user(email=EMAIL_A, password=PASSWORD)
        user_model.objects.create_user(email=EMAIL_B, password=PASSWORD)

        form = GenerateLeagueForm(
            {"num_teams": "4", "players_per_team": "6", "mean": 50, "std_dev": 15}
        )
        if not form.is_valid():
            raise CommandError(f"Team generation form rejected: {form.errors}")
        run_player_generation(form.cleaned_data, manager=a)
        red, blue, third, fourth = Team.objects.filter(manager=a).order_by("id")[:4]

        match = create_sandbox_match(red, blue, "friendly", arena_map=None, user=a)
        game_round = create_sandbox_round(third, fourth, arena_map=None, user=a)
        unfinished = Match.objects.create(
            team_red=red,
            team_blue=fourth,
            match_type="friendly",
            red_round1_points=41000,
            blue_round1_points=39000,
            manager=a,
        )
        # save() derives a winner and completion; force the mid-Season state back.
        Match.objects.filter(pk=unfinished.pk).update(winner=None, is_completed=False)

        self.stdout.write(f"Accounts {EMAIL_A} (data) and {EMAIL_B} (empty)")
        self.stdout.write(f"Password: {PASSWORD}")
        self.stdout.write(
            f"Match {match.pk}, Single Round {game_round.pk}, "
            f"unfinished Match {unfinished.pk}"
        )

    @transaction.atomic
    def _clean(self) -> None:
        users = get_user_model().objects.filter(email__in=[EMAIL_A, EMAIL_B])
        counts = {
            "Single Rounds": GameRound.objects.filter(manager__in=users).delete()[0],
            "Matches": Match.objects.filter(manager__in=users).delete()[0],
            "Players": Player.objects.filter(team__manager__in=users).delete()[0],
            "Teams": Team.objects.filter(manager__in=users).delete()[0],
            "Accounts": users.delete()[0],
        }
        self.stdout.write(
            "removed " + ", ".join(f"{name}: {n}" for name, n in counts.items())
        )
