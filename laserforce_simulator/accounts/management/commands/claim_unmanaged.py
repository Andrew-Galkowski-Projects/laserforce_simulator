"""UX-01 — claim every **Unmanaged row** for one Account (ADR-0038).

    python manage.py claim_unmanaged --user manager@example.com

ADR-0038 rejected a ``RunPython`` backfill as vacuous — a custom user model
means an empty user table on every existing database, so the migration would
find no Account to stamp. This command is the explicit replacement, and it
keeps ADR-0004's no-backfill precedent intact.

Idempotent: a second run matches nothing and reports 0 for every model.
Uses ``.update()``, so no ``save()`` signals fire and ``auto_now`` columns are
left untouched.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from accounts.models import User
from accounts.permissions import ROOT_MODELS
from teams.models import FREE_AGENTS_TEAM_NAME, Team


class Command(BaseCommand):
    help = "Stamp every Unmanaged row (manager IS NULL) on all five Ownership roots to one Account."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--user",
            dest="user",
            required=True,
            help="Email address of the Account to claim the rows for.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        email = options["user"]
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f"No account with email {email!r}.")

        total = 0
        with transaction.atomic():
            for model in ROOT_MODELS:
                unmanaged = model.objects.filter(manager__isnull=True)
                if model is Team:
                    # The global "Free Agents" singleton is a cross-Account
                    # shared pool and must stay **Unmanaged**: claiming it would
                    # let this Account capture it permanently and 404 it for
                    # everyone else (the same reason ``_generate_free_agents``
                    # never stamps it). Per-League free-agent pool Teams are
                    # ordinary owned rows and ARE claimed.
                    unmanaged = unmanaged.exclude(name=FREE_AGENTS_TEAM_NAME)
                count = unmanaged.update(manager=user)
                total += count
                self.stdout.write(f"{model.__name__}: {count} claimed")

        self.stdout.write(
            self.style.SUCCESS(f"Total: {total} rows claimed by {user.email}")
        )
