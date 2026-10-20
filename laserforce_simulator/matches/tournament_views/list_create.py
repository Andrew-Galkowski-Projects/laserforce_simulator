"""LG-02a — the Tournament index and the create form."""

import random

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from accounts.permissions import manager_or_none, owned_queryset
from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.models import Team
from teams.views import _generate_teams

from matches.bracket import default_seed_order
from matches.models import Tournament, TournamentParticipant
from matches.tournament_services.detail import format_label
from matches.tournament_services.seeding import team_mean_rating

# LG-02c (RR->DE) — the 6 locked (wb, lb) advancer shape combos, in select order.
# value string format "wb/lb" (e.g. "4/0"). lb is 0 or wb//2.
_RRDE_COMBOS: tuple[tuple[str, int, int], ...] = (
    ("4/0", 4, 0),
    ("4/2", 4, 2),
    ("8/0", 8, 0),
    ("8/4", 8, 4),
    ("16/0", 16, 0),
    ("16/8", 16, 8),
)
_RRDE_COMBO_BY_VALUE: dict[str, tuple[int, int]] = {
    value: (wb, lb) for value, wb, lb in _RRDE_COMBOS
}


def _parse_rrde_combo(raw: "str | None", tournament_format: str) -> tuple[int, int]:
    """Parse the ``rrde_combo`` select value into ``(wb_advancers,
    lb_advancers)``.

    A non-RRDE format ignores the combo and returns ``(0, 0)``. An RRDE format
    with an absent / invalid value forgiving-falls-back to the first combo
    ``(4, 0)``.
    """
    if tournament_format != "round_robin_double_elim":
        return (0, 0)
    return _RRDE_COMBO_BY_VALUE.get(raw or "", (4, 0))


def _parse_swiss_rounds(raw: "str | None") -> int:
    """LG-02c (Swiss) — parse the ``swiss_rounds`` POST field into a
    non-negative int (forgiving: absent/blank/invalid/negative -> 0 = auto).

    Clamping happens at lock; the view just coerces to a non-negative int, 0
    meaning auto.
    """
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else 0


def tournament_list(request: HttpRequest) -> HttpResponse:
    """List all Tournaments newest-first."""
    tournaments = list(
        owned_queryset(Tournament.objects.all(), request.user).order_by("-id")
    )
    # ANG-01f — the list shows the derived label ("Random Draw" for a
    # Player-pool draw played as RR->DE), not the raw format choice.
    for tournament in tournaments:
        tournament.format_label = format_label(tournament)
    return render(
        request,
        "matches/tournament_list.html",
        {"tournaments": tournaments},
    )


@transaction.atomic
def tournament_create(request: HttpRequest) -> HttpResponse:
    """GET -> render create form. POST valid -> create Tournament + participants
    with default Seeding, redirect to detail. POST invalid -> re-render (200).
    """
    # Only Teams with a full valid roster (all 6 slots filled, no duplicate
    # player — ``Team.is_valid_roster``) may enter a Tournament; an incomplete
    # roster cannot field a 6v6 game. ``select_related`` the 6 slot FKs so
    # ``is_valid_roster`` touches no extra queries per Team.
    available_teams = [
        t
        for t in Team.objects.regular().select_related(
            "slot_commander",
            "slot_heavy",
            "slot_scout_1",
            "slot_scout_2",
            "slot_medic",
            "slot_ammo",
        )
        if t.is_valid_roster
    ]

    if request.method != "POST":
        return render(
            request,
            "matches/tournament_create.html",
            {"form": None, "available_teams": available_teams},
        )

    name = (request.POST.get("name") or "").strip()
    selected_ids = request.POST.getlist("teams")

    # LG-02x-1 — orthogonal team-assembly axis. Parse early so the preset-only
    # participant-selection validation can be skipped for player_pool (a
    # player_pool Tournament is created in setup with an EMPTY pool — the user
    # fills the pool + runs the draw on the detail page).
    team_assembly = request.POST.get("team_assembly")
    if team_assembly not in ("preset", "player_pool"):
        team_assembly = "preset"

    try:
        generate_count = int(request.POST.get("generate_count") or 0)
    except (TypeError, ValueError):
        generate_count = 0
    try:
        generate_ppt = int(request.POST.get("generate_ppt") or 6)
    except (TypeError, ValueError):
        generate_ppt = 6
    # A tournament Team must field a full 6-player roster, so generated teams
    # always get at least 6 players — a smaller request is clamped up rather
    # than producing an unplayable participant.
    generate_ppt = max(6, generate_ppt)

    errors = []
    if not name:
        errors.append("Tournament name is required.")

    teams: list[Team] = []
    if team_assembly == "preset":
        if selected_ids:
            selected = list(
                Team.objects.filter(id__in=selected_ids).select_related(
                    "slot_commander",
                    "slot_heavy",
                    "slot_scout_1",
                    "slot_scout_2",
                    "slot_medic",
                    "slot_ammo",
                )
            )
            # Reject any selected Team without a full valid roster (defends a
            # tampered/stale POST of an id the filtered select list never
            # offered).
            ineligible = [t for t in selected if not t.is_valid_roster]
            if ineligible:
                names = ", ".join(t.name for t in ineligible)
                errors.append(
                    "These teams don't have a full 6-player roster and can't "
                    f"enter a tournament: {names}. Complete their rosters first."
                )
            teams.extend(selected)

        if generate_count and generate_count > 0:
            rng = random.Random()
            team_names_pool = list(TEAM_NAMES)
            player_names_pool = list(PLAYER_NAMES)
            generated = _generate_teams(
                generate_count,
                generate_ppt if generate_ppt > 0 else 6,
                rng=rng,
                mean=50,
                std_dev=15,
                team_names_pool=team_names_pool,
                player_names_pool=player_names_pool,
                manager=manager_or_none(request),
            )
            teams.extend(generated)

        if len(teams) < 4:
            errors.append("A tournament requires at least 4 teams.")

    if errors:
        for err in errors:
            messages.error(request, err)
        return render(
            request,
            "matches/tournament_create.html",
            {"form": None, "available_teams": available_teams},
        )

    # LG-02b-2 — four per-depth Series length slots (final / semifinal /
    # quarterfinal / earlier rounds), each int-coerced with a forgiving fallback
    # to 1 then forced into {1,3,5}. No monotonicity — the four are independent.
    def _parse_series_length(field: str) -> int:
        try:
            value = int(request.POST.get(field) or 1)
        except (TypeError, ValueError):
            value = 1
        if value not in (1, 3, 5):
            value = 1
        return value

    final_series_length = _parse_series_length("final_series_length")
    semifinal_series_length = _parse_series_length("semifinal_series_length")
    quarterfinal_series_length = _parse_series_length("quarterfinal_series_length")
    earlier_series_length = _parse_series_length("earlier_series_length")

    # LG-02c — bracket format. Forgiving fallback (mirrors the series-length
    # parses): only the known formats are accepted, anything else (absent,
    # tampered) falls back to single-elimination.
    tournament_format = request.POST.get("format")
    if tournament_format not in (
        "single_elimination",
        "double_elimination",
        "round_robin",
        "round_robin_double_elim",
        "swiss",
    ):
        tournament_format = "single_elimination"

    # LG-02c (RR->DE) — the (wb, lb) advancer combo. A single rrde_combo select
    # enumerating the 6 locked shape combos ("4/0", "4/2", "8/0", "8/4",
    # "16/0", "16/8"). For an RRDE create an absent/invalid combo falls back to
    # the first combo (4, 0); for any non-RRDE create the combo is ignored and
    # both advancers persist 0.
    wb_advancers, lb_advancers = _parse_rrde_combo(
        request.POST.get("rrde_combo"), tournament_format
    )

    # LG-02c (Swiss) — the requested round count (0 = auto). Persisted for every
    # create (harmless — only read at lock when format == "swiss").
    swiss_rounds = _parse_swiss_rounds(request.POST.get("swiss_rounds"))

    # LG-02x-1 — role-assignment mode (meaningful only for player_pool).
    # Forgiving fallback to the default.
    role_assignment_mode = request.POST.get("role_assignment_mode")
    if role_assignment_mode not in ("random", "per_tier"):
        role_assignment_mode = "random"

    tournament = Tournament.objects.create(
        name=name,
        state="setup",
        format=tournament_format,
        final_series_length=final_series_length,
        semifinal_series_length=semifinal_series_length,
        quarterfinal_series_length=quarterfinal_series_length,
        earlier_series_length=earlier_series_length,
        wb_advancers=wb_advancers,
        lb_advancers=lb_advancers,
        swiss_rounds=swiss_rounds,
        team_assembly=team_assembly,
        role_assignment_mode=role_assignment_mode,
        # UX-01 — the **Manager**: the Account this Tournament belongs to.
        manager=manager_or_none(request),
    )

    # Default Seeding via mean active-player overall_rating. For player_pool the
    # pool is empty at create — participants are seeded by the draw, not here.
    if team_assembly == "preset":
        team_ratings = [(t.id, team_mean_rating(t)) for t in teams]
        seed_order = default_seed_order(team_ratings)
        team_by_id = {t.id: t for t in teams}
        for idx, team_id in enumerate(seed_order, start=1):
            TournamentParticipant.objects.create(
                tournament=tournament, team=team_by_id[team_id], seed=idx
            )

    return redirect("tournament_detail", tournament_id=tournament.id)
