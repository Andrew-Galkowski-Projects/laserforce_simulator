"""FIN-01 — Team Finances (league-context) screen.

Read-only-plus-budget-edit view rendering the managed Team's
``TeamSeasonFinance`` history + current budget levels + ticket price + cash +
live luxury-tax / min-payroll / payroll figures, inside the displayed Season of
a League. Keyed on ``current_team`` (the LG-01g resolver chain — default
``league.current_team``, optional ``?team_id=`` override), exactly like
``team_roster`` / ``team_stats``.

A budget-edit POST branch writes ``Team.budget_scouting / coaching / facilities``
+ ``ticket_price`` (budget levels clamped ``[1, MAX_LEVEL]``) then redirects to
the bare URL (the LG-01z ``watch_list_toggle`` POST-then-redirect precedent);
finance OFF ⇒ the edit form is hidden/inert.

Follows the shared LG-01z view contract: 405 GET-guard on non-GET/POST,
``get_object_or_404(League)``, session write, displayed-Season pick, sidebar
links with ``sidebar_active="finances_team"``, render
``leagues/team_finances.html``. Finance-disabled League ⇒ a "Finances are
disabled for this League" notice in place of the body.
"""

from __future__ import annotations

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
)
from django.shortcuts import get_object_or_404, redirect, render

from matches import finance
from matches.league_views import (
    _build_league_sidebar_links,
    _resolve_current_team_for_sidebar,
    _team_wins_games_for_season,
)
from matches.models import League, TeamSeasonFinance
from teams.models import Team


def _resolve_team(league: League, displayed_season, raw_team_id):
    """Pick the Team this screen renders — ``?team_id=`` (must be enrolled)
    then ``league.current_team`` then the LG-01g resolver, all gated on the
    displayed Season's enrolment. Returns ``(team, enrolled_teams)``."""
    enrolled_teams = list(displayed_season.teams.order_by("name"))
    enrolled_ids = {t.id for t in enrolled_teams}

    team: Team | None = None
    if raw_team_id is not None:
        try:
            requested_id = int(raw_team_id)
        except (TypeError, ValueError):
            requested_id = None
        if requested_id is not None and requested_id in enrolled_ids:
            team = next((t for t in enrolled_teams if t.id == requested_id), None)

    if team is None:
        current_team_id = league.current_team_id
        if current_team_id is not None and current_team_id in enrolled_ids:
            team = next((t for t in enrolled_teams if t.id == current_team_id), None)
    if team is None:
        resolved = _resolve_current_team_for_sidebar(league, displayed_season)
        if resolved is not None and resolved.id in enrolled_ids:
            team = next((t for t in enrolled_teams if t.id == resolved.id), None)

    return team, enrolled_teams


def _coerce_level(raw, default: int) -> int:
    """Clamp a posted budget level to ``[1, finance.MAX_LEVEL]``; invalid →
    the team's current value (``default``)."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(finance.MAX_LEVEL, value))


def _coerce_price(raw, default: float) -> float:
    """Clamp a posted ticket price to ``>= 0``; invalid → current value."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(0.0, value)


def team_finances(request: HttpRequest, league_id: int) -> HttpResponse:
    """FIN-01 — Team Finances (league-context) page."""
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    league = get_object_or_404(League, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )

    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="finances_team"
    )

    base_context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "finances_team",
        "finance_enabled": league.finance_enabled,
    }

    # No Season ⇒ empty-state (the sidebar still renders).
    if displayed_season is None:
        context = {
            **base_context,
            "team": None,
            "enrolled_teams": [],
            "history": [],
        }
        return render(request, "leagues/team_finances.html", context)

    raw_team_id = request.POST.get("team_id") or request.GET.get("team_id")
    team, enrolled_teams = _resolve_team(league, displayed_season, raw_team_id)

    # Budget-edit POST — write Team budgets + ticket price, then redirect to
    # the bare URL (POST-then-redirect). Inert when finance is OFF or no Team.
    if request.method == "POST":
        if league.finance_enabled and team is not None:
            team.budget_scouting = _coerce_level(
                request.POST.get("budget_scouting"), team.budget_scouting
            )
            team.budget_coaching = _coerce_level(
                request.POST.get("budget_coaching"), team.budget_coaching
            )
            team.budget_facilities = _coerce_level(
                request.POST.get("budget_facilities"), team.budget_facilities
            )
            # FIN-04 — the fourth budget + the injury-resolution policy toggle.
            team.budget_health = _coerce_level(
                request.POST.get("budget_health"), team.budget_health
            )
            posted_policy = request.POST.get("injury_policy")
            if posted_policy in ("auto_sub", "play_hurt"):
                team.injury_policy = posted_policy
            team.ticket_price = _coerce_price(
                request.POST.get("ticket_price"), team.ticket_price
            )
            team.save(
                update_fields=[
                    "budget_scouting",
                    "budget_coaching",
                    "budget_facilities",
                    "budget_health",
                    "injury_policy",
                    "ticket_price",
                ]
            )
        # POST-then-redirect to the bare URL (the LG-01z watch_list_toggle
        # precedent); the bare URL resolves back to current_team, the one
        # editable Team.
        return redirect(f"/leagues/{league.id}/team/finances/")

    if team is None:
        context = {
            **base_context,
            "team": None,
            "enrolled_teams": enrolled_teams,
            "history": [],
        }
        return render(request, "leagues/team_finances.html", context)

    # Per-Season finance history (newest-first) for this Team.
    history = list(
        TeamSeasonFinance.objects.filter(team=team)
        .select_related("season")
        .order_by("-season_id")
    )

    # Live figures from the current roster (independent of any persisted row).
    active = list(team.active_players)
    roster_salaries = [
        {"name": p.name, "salary": finance.salary_for_overall(p.overall_rating)}
        for p in active
    ]
    live_payroll = sum(r["salary"] for r in roster_salaries)
    live_luxury_tax = finance.luxury_tax(live_payroll)
    live_min_payroll_penalty = finance.min_payroll_penalty(live_payroll)

    # Per-level dollar costs of the three budgets (ZenGM Expense Settings).
    budget_costs = {
        "scouting": finance.level_to_amount(team.budget_scouting),
        "coaching": finance.level_to_amount(team.budget_coaching),
        "facilities": finance.level_to_amount(team.budget_facilities),
        # FIN-04 — the fourth budget cost line.
        "health": finance.level_to_amount(team.budget_health),
    }

    # FIN-04 — currently-unavailable players (newest-first by games remaining).
    availability = [
        {"name": p.name, "games_remaining": p.games_unavailable}
        for p in sorted(
            team.players.filter(games_unavailable__gt=0),
            key=lambda p: p.games_unavailable,
            reverse=True,
        )
    ]

    # Headline metrics: this-Season record + the latest finance snapshot tiles.
    wins, games = _team_wins_games_for_season(displayed_season, team.id)
    losses = games - wins
    latest_finance = history[0] if history else None  # history is newest-first
    current_hype = latest_finance.hype if latest_finance else 0.0

    context = {
        **base_context,
        "team": team,
        "enrolled_teams": enrolled_teams,
        "history": history,
        "live_payroll": live_payroll,
        "live_luxury_tax": live_luxury_tax,
        "live_min_payroll_penalty": live_min_payroll_penalty,
        "max_level": finance.MAX_LEVEL,
        "default_level": finance.DEFAULT_LEVEL,
        "roster_salaries": roster_salaries,
        "roster_salary_total": live_payroll,
        "budget_costs": budget_costs,
        "wins": wins,
        "losses": losses,
        "games": games,
        "current_hype": current_hype,
        "latest_finance": latest_finance,
        # FIN-04 — health budget + injury policy + availability display.
        "injury_policy": team.injury_policy,
        "availability": availability,
    }
    return render(request, "leagues/team_finances.html", context)
