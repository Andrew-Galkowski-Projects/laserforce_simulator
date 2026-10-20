"""ANG-01f-5 -- the Player pool and the Draw of a Player-pool draw Tournament.

Shared by the HTML views (``matches.tournament_views.draw``) and the API
(``matches.tournament_api_views.draw``): both bind the same strict Forms and
call the same services. Every service checks :func:`require_pool_setup`
(sandbox, then setup, then ``team_assembly == "player_pool"``) itself, so a
caller cannot skip a guard.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404

from accounts.permissions import owned_queryset
from matches.draw import ROLE_SLOTS, compute_draw
from matches.models import Tournament, TournamentParticipant, TournamentPlayerEntry
from matches.tournament_services.errors import DrawNotReadyError, require_setup
from teams.constants import PLAYER_NAMES
from teams.forms import RosterImportForm
from teams.models import Player, Team, get_free_agents_team
from teams.player_generator import draw_preferred_roles, draw_stats
from teams.roster_importer import parse_roster_csv

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

NOT_PLAYER_POOL_MESSAGE = "This Tournament does not use a Player-pool draw."


def require_pool_setup(tournament: Tournament) -> None:
    """``require_setup`` (embedded first, then state), then the Player-pool rule.

    A preset Tournament raises a Django ``ValidationError`` (the API maps it to
    400 ``invalid`` with field null; the HTML flashes it).
    """
    require_setup(tournament)
    if tournament.team_assembly != "player_pool":
        raise ValidationError(NOT_PLAYER_POOL_MESSAGE)


# ---------------------------------------------------------------------------
# Pool add (existing Players)
# ---------------------------------------------------------------------------


class PoolAddPlayersForm(forms.Form):
    """``player_ids``: owned Players (own or Unmanaged Team) not already pooled."""

    player_ids = forms.ModelMultipleChoiceField(queryset=Player.objects.none())

    def __init__(
        self,
        data: Any = None,
        *,
        tournament: Tournament,
        user: "AbstractBaseUser | None",
    ) -> None:
        super().__init__(data)
        self.tournament = tournament
        # Leak fix: only Players the Account may see are valid choices, so
        # another Account's Player is ``invalid_choice``.
        self.fields["player_ids"].queryset = owned_queryset(
            Player.objects.all(), user, path="team"
        )

    def clean_player_ids(self) -> list[Player]:
        players = list(self.cleaned_data["player_ids"])
        pooled = set(
            self.tournament.player_entries.filter(player__in=players).values_list(
                "player_id", flat=True
            )
        )
        if pooled:
            raise ValidationError(
                "Player %(ids)s is already in the pool.",
                code="already_in_pool",
                params={"ids": ", ".join(str(i) for i in sorted(pooled))},
            )
        return players


@transaction.atomic
def add_pool_players(
    tournament: Tournament, form: PoolAddPlayersForm
) -> list[TournamentPlayerEntry]:
    """One pool entry (tier / drawn Team null) per Player in the valid ``form``."""
    require_pool_setup(tournament)
    return [
        TournamentPlayerEntry.objects.create(tournament=tournament, player=player)
        for player in form.cleaned_data["player_ids"]
    ]


def _pool_entry_or_404(tournament: Tournament, player_id: int) -> TournamentPlayerEntry:
    """The Tournament's pool entry for ``player_id``; ``Http404`` when absent."""
    try:
        return tournament.player_entries.get(player_id=player_id)
    except TournamentPlayerEntry.DoesNotExist:
        raise Http404("That Player is not in the pool.")


@transaction.atomic
def remove_pool_player(tournament: Tournament, player_id: int) -> None:
    """Drop ``player_id``'s pool entry; a Player not in the pool is ``Http404``."""
    require_pool_setup(tournament)
    _pool_entry_or_404(tournament, player_id).delete()


# ---------------------------------------------------------------------------
# Pool generate / import (new Players on the Free Agents Team)
# ---------------------------------------------------------------------------

DEFAULT_GENERATE_MEAN = 50
DEFAULT_GENERATE_STD_DEV = 15
MAX_GENERATE_COUNT = 1000


class PoolGenerateForm(forms.Form):
    """Strict: ``count`` 1..1000; ``mean`` 0..100 and ``std_dev`` 1..40 when
    sent (omitted means 50 / 15). A non-number or out-of-range value is an
    error, never a default."""

    count = forms.IntegerField(min_value=1, max_value=MAX_GENERATE_COUNT)
    mean = forms.IntegerField(min_value=0, max_value=100, required=False)
    std_dev = forms.IntegerField(min_value=1, max_value=40, required=False)

    def clean_mean(self) -> int:
        mean = self.cleaned_data.get("mean")
        return DEFAULT_GENERATE_MEAN if mean is None else mean

    def clean_std_dev(self) -> int:
        std_dev = self.cleaned_data.get("std_dev")
        return DEFAULT_GENERATE_STD_DEV if std_dev is None else std_dev


def _free_agent_name(free_agents: Team, base_name: str) -> str:
    """``base_name``, suffixed ``#k`` until unique on the Free Agents Team."""
    name = base_name
    k = 2
    while Player.objects.filter(team=free_agents, name=name).exists():
        name = f"{base_name} #{k}"
        k += 1
    return name


@transaction.atomic
def generate_pool_players(
    tournament: Tournament, form: PoolGenerateForm
) -> list[TournamentPlayerEntry]:
    """``count`` fresh LG-00 Players on the Free Agents Team, each pooled."""
    require_pool_setup(tournament)
    count = form.cleaned_data["count"]
    mean = form.cleaned_data["mean"]
    std_dev = form.cleaned_data["std_dev"]

    rng = random.Random()
    free_agents = get_free_agents_team()
    name_pool = list(PLAYER_NAMES)
    rng.shuffle(name_pool)

    entries = []
    for i in range(count):
        base_name = name_pool.pop() if name_pool else f"Player {i + 1}"
        player = Player.objects.create(
            team=free_agents,
            name=_free_agent_name(free_agents, base_name),
            preferred_roles=draw_preferred_roles(rng),
            **draw_stats(rng, mean, std_dev),
        )
        entries.append(
            TournamentPlayerEntry.objects.create(tournament=tournament, player=player)
        )
    return entries


# ---------------------------------------------------------------------------
# The Draw
# ---------------------------------------------------------------------------


def _clear_previous_draw(tournament: Tournament) -> None:
    """Re-roll cleanup: drop the prior drawn Teams and their participants, and
    null every entry's tier / drawn Team."""
    prior_team_ids = list(
        tournament.player_entries.filter(drawn_team__isnull=False)
        .values_list("drawn_team_id", flat=True)
        .distinct()
    )
    if prior_team_ids:
        tournament.participants.filter(team_id__in=prior_team_ids).delete()
        tournament.player_entries.update(tier=None, drawn_team=None)
        Team.objects.filter(id__in=prior_team_ids, is_draw_team=True).delete()


@transaction.atomic
def run_draw(tournament: Tournament, *, user: "AbstractBaseUser | None") -> list[Team]:
    """Run the tier-balanced Draw over the pool and return the drawn Teams.

    Builds one drawn Team (``is_draw_team``, managed by ``user``) and
    participant per ``compute_draw`` plan, seeded in plan order, and fills each
    entry's tier / drawn Team. A re-run (re-roll) replaces the previous drawn
    Teams. A pool that is not a multiple of 6 with at least 24 Players raises
    ``DrawNotReadyError`` with no writes.
    """
    require_pool_setup(tournament)
    entries = list(tournament.player_entries.select_related("player"))
    n = len(entries)
    if n % 6 != 0 or n < 24:
        raise DrawNotReadyError(
            "The pool must be divisible by 6 and have at least 24 players "
            f"(>= 4 teams). It currently has {n}."
        )

    plans = compute_draw([(e.player_id, e.player.overall_rating) for e in entries])
    _clear_previous_draw(tournament)

    manager = user if user is not None and user.is_authenticated else None
    entry_by_player = {e.player_id: e for e in entries}
    teams = []
    for plan in plans:
        team = Team.objects.create(
            name=f"Draw Team {plan.team_index + 1}",
            is_draw_team=True,
            manager=manager,
        )
        # Initial valid assignment: tier order -> ROLE_SLOTS order (any valid
        # no-duplicate assignment satisfies the relaxed roster_errors).
        for slot, player_id in zip(ROLE_SLOTS, plan.player_ids):
            setattr(team, f"slot_{slot}_id", player_id)
        team.save()
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=plan.team_index + 1
        )
        for player_id, tier in zip(plan.player_ids, plan.tiers):
            entry = entry_by_player[player_id]
            entry.tier = tier
            entry.drawn_team = team
            entry.save(update_fields=["tier", "drawn_team"])
        teams.append(team)
    return teams


# ---------------------------------------------------------------------------
# Hand edit of one draw entry
# ---------------------------------------------------------------------------


class DrawEntryForm(forms.Form):
    """A partial edit of one draw entry: ``tier`` 1..6 and / or ``drawn_team``,
    which must be one of THIS Tournament's drawn Teams. At least one is sent."""

    tier = forms.IntegerField(min_value=1, max_value=6, required=False)
    drawn_team = forms.ModelChoiceField(queryset=Team.objects.none(), required=False)

    def __init__(self, data: Any = None, *, tournament: Tournament) -> None:
        super().__init__(data)
        self.tournament = tournament
        # Leak fix: only this Tournament's drawn Teams are valid choices.
        self.fields["drawn_team"].queryset = Team.objects.filter(
            is_draw_team=True,
            id__in=tournament.participants.values("team_id"),
        )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        if (
            not self.errors
            and cleaned.get("tier") is None
            and cleaned.get("drawn_team") is None
        ):
            raise ValidationError("Send a tier or a drawn Team.", code="required")
        return cleaned


@transaction.atomic
def edit_draw_entry(
    tournament: Tournament, player_id: int, form: DrawEntryForm
) -> TournamentPlayerEntry:
    """Apply the valid ``form``'s sent fields to ``player_id``'s entry.

    A Player not in the pool is ``Http404``. Only the drawn Team recorded on the
    entry changes; the drawn Teams' slot FKs are untouched (as before ANG-01f).
    """
    require_pool_setup(tournament)
    entry = _pool_entry_or_404(tournament, player_id)
    update_fields = []
    if form.cleaned_data.get("tier") is not None:
        entry.tier = form.cleaned_data["tier"]
        update_fields.append("tier")
    if form.cleaned_data.get("drawn_team") is not None:
        entry.drawn_team = form.cleaned_data["drawn_team"]
        update_fields.append("drawn_team")
    entry.save(update_fields=update_fields)
    return entry


@transaction.atomic
def import_pool_players(
    tournament: Tournament,
    form: RosterImportForm,
    *,
    user: "AbstractBaseUser | None",
) -> list[TournamentPlayerEntry]:
    """One pool Player on the Free Agents Team per row of the valid ``form``'s CSV.

    The CSV ``team`` / ``role`` grouping is ignored (the Draw assigns Teams).
    Raises ``RosterImportError`` (from ``parse_roster_csv``) before any write.
    Known gaps, logged in ``issues.md``: the Players land on the global Free
    Agents Team (so ``user`` does not own them), and the DB slot-collision
    check of a Team import is skipped.
    """
    require_pool_setup(tournament)
    parsed = parse_roster_csv(form.cleaned_data["csv_file"])
    free_agents = get_free_agents_team()
    entries = []
    for row in parsed.rows:
        player = Player.objects.create(
            team=free_agents,
            name=_free_agent_name(free_agents, row.name),
            preferred_roles=row.preferred_roles,
            **row.profile,
            **row.stats,
        )
        entries.append(
            TournamentPlayerEntry.objects.create(tournament=tournament, player=player)
        )
    return entries
