"""ANG-01f-4 -- Tournament setup writes shared by the HTML views and the API.

- :class:`SeedsForm` / :func:`apply_seeds` -- a full ``{team_id: seed}`` map
  that must be exactly 1..N over the current participants.
- :func:`lock_tournament` -- ``lock_and_build`` behind the setup guards; the
  model's format-rule ``ValidationError`` propagates.
- :func:`import_participants` -- a roster CSV whose brand-new Teams join the
  field, which is then re-seeded by talent.

Every write calls ``require_setup`` (embedded first, then state). Imports no
view module (``test_tournament_services_boundary``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms
from django.db import transaction

from matches.bracket import default_seed_order
from matches.models import Tournament, TournamentParticipant
from matches.tournament_services.errors import require_setup
from matches.tournament_services.seeding import team_mean_rating
from teams.forms import RosterImportForm
from teams.models import Team
from teams.roster_importer import parse_roster_csv
from teams.services import _apply_roster, _check_db_slot_collisions

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

# Added to every seed in the first phase of a two-phase seed write, so the
# unique (tournament, seed) constraint never sees two rows on one seed.
_SEED_OFFSET = 1000000


def _as_int(value: Any) -> int | None:
    """``value`` as an int, or ``None`` when it is not a whole number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


class SeedsForm(forms.Form):
    """``seeds``: a ``{team_id: seed}`` map over exactly the current participants.

    ``cleaned_data["seeds"]`` is a ``dict[int, int]``. The seeds must be the
    permutation 1..N, so a duplicate, a gap, a missing or extra team, or a
    non-integer is a form error on ``seeds``.
    """

    seeds = forms.JSONField()

    def __init__(self, data: Any = None, *, tournament: Tournament) -> None:
        super().__init__(data)
        self.tournament = tournament

    def clean_seeds(self) -> dict[int, int]:
        raw = self.cleaned_data["seeds"]
        if not isinstance(raw, dict):
            raise forms.ValidationError(
                "Seeds must map each participant's team id to its seed.",
                code="invalid",
            )
        seeds: dict[int, int] = {}
        for raw_team_id, raw_seed in raw.items():
            team_id = _as_int(raw_team_id)
            seed = _as_int(raw_seed)
            if team_id is None or seed is None:
                raise forms.ValidationError(
                    "Every team id and seed must be a whole number.", code="invalid"
                )
            seeds[team_id] = seed

        participant_ids = set(
            self.tournament.participants.values_list("team_id", flat=True)
        )
        missing = sorted(participant_ids - set(seeds))
        extra = sorted(set(seeds) - participant_ids)
        if missing:
            raise forms.ValidationError(
                f"Missing a seed for team(s) {', '.join(map(str, missing))}.",
                code="invalid",
            )
        if extra:
            raise forms.ValidationError(
                f"Team(s) {', '.join(map(str, extra))} are not participants.",
                code="invalid",
            )
        n = len(participant_ids)
        if sorted(seeds.values()) != list(range(1, n + 1)):
            raise forms.ValidationError(
                f"Seeds must be exactly 1 to {n}, each used once.", code="invalid"
            )
        return seeds


def _write_seeds(participants: list[TournamentParticipant], seed_by_team: dict) -> None:
    """Two-phase write: offset every seed, then write the final values."""
    for participant in participants:
        participant.seed = participant.seed + _SEED_OFFSET
        participant.save(update_fields=["seed"])
    for participant in participants:
        participant.seed = seed_by_team[participant.team_id]
        participant.save(update_fields=["seed"])


@transaction.atomic
def apply_seeds(tournament: Tournament, form: SeedsForm) -> None:
    """Persist a valid :class:`SeedsForm` (guards first)."""
    require_setup(tournament)
    if not form.is_valid():
        raise ValueError("apply_seeds needs a valid SeedsForm.")
    _write_seeds(list(tournament.participants.all()), form.cleaned_data["seeds"])


def lock_tournament(tournament: Tournament) -> Tournament:
    """setup -> active via ``lock_and_build`` (guards first).

    The model's format-rule ``django.core.exceptions.ValidationError`` (too
    few participants, RR->DE advancers above the field, an odd Swiss field)
    propagates with nothing written. Returns the refreshed Tournament.
    """
    require_setup(tournament)
    tournament.lock_and_build()
    tournament.refresh_from_db()
    return tournament


def import_participants(
    tournament: Tournament,
    form: RosterImportForm,
    *,
    user: AbstractBaseUser | None,
) -> list[Team]:
    """Import a roster CSV; its brand-new Teams join the field (guards first).

    Teams are created / appended ownership-scoped and stamped with ``user``
    (ANG-01a). Only the Teams the import CREATED become participants (appended
    Teams do not), then the whole field is re-seeded by talent. All-or-nothing:
    a ``RosterImportError`` propagates after its savepoint rolls back. Returns
    the new participant Teams in CSV order.
    """
    require_setup(tournament)
    if not form.is_valid():
        raise ValueError("import_participants needs a valid RosterImportForm.")

    with transaction.atomic():
        parsed = parse_roster_csv(form.cleaned_data["csv_file"])
        _check_db_slot_collisions(parsed, manager=user)
        created_teams, _appended, _count = _apply_roster(parsed, manager=user)

        existing_team_ids = set(
            tournament.participants.values_list("team_id", flat=True)
        )
        next_seed = len(existing_team_ids) + 1
        joined: list[Team] = []
        for team in created_teams:
            if team.id in existing_team_ids:
                continue
            TournamentParticipant.objects.create(
                tournament=tournament, team=team, seed=next_seed
            )
            joined.append(team)
            next_seed += 1

        # Re-seed the WHOLE field by talent.
        participants = list(tournament.participants.select_related("team"))
        seed_order = default_seed_order(
            [(p.team_id, team_mean_rating(p.team)) for p in participants]
        )
        _write_seeds(
            participants,
            {team_id: idx for idx, team_id in enumerate(seed_order, start=1)},
        )
    return joined
