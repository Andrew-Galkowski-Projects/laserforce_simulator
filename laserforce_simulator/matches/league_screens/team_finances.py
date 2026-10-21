"""FIN-01 -- Team Finances (league-context) screen.

Read-only-plus-budget-edit view rendering a Team's ``TeamSeasonFinance``
history + current budget levels + ticket price + cash + live luxury-tax /
min-payroll / payroll figures, inside the displayed Season of a League. The
displayed Team follows the LG-01g resolver chain (default
``league.current_team``, optional ``?team_id=`` override), exactly like
``team_roster`` / ``team_stats``.

ANG-01e T5: the budget-edit POST binds ``TeamFinancesForm`` and saves only
``league.current_team`` through ``update_team_finances`` (a posted ``team_id``
is ignored for writes). Invalid values re-render the page with the form errors
(200) instead of being clamped; a valid save redirects to the bare URL. The
edit form is shown only while the current Team is displayed. Finance OFF => the
edit form is hidden and a POST is inert.

Follows the shared LG-01z view contract: 405 on non-GET/POST,
``get_owned_or_404(League)``, session write, displayed-Season pick, sidebar
links with ``sidebar_active="finances_team"``, render
``leagues/team_finances.html``.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
)
from django.shortcuts import redirect, render

from accounts.permissions import get_owned_or_404
from laserforce_simulator.api.forms import merged_form_data
from matches import finance
from matches.forms import TeamFinancesForm
from matches.league_screen_services import (
    FinanceDisabled,
    TeamNotEditable,
    build_team_finances,
    enrolled_teams as _enrolled_teams,
    parse_team_id,
    resolve_league_team,
    update_team_finances,
)
from matches.league_views import _build_league_sidebar_links
from matches.models import League
from teams.models import Team


def _resolve_team(league: League, displayed_season, raw_team_id):
    """Pick the Team this screen renders -- ``?team_id=`` (must be enrolled)
    then ``league.current_team`` then the first enrolled Team, all gated on the
    displayed Season's enrolment (the shared ``resolve_league_team``).
    Returns ``(team, enrolled_teams)``."""
    team = resolve_league_team(league, displayed_season, parse_team_id(raw_team_id))
    return team, _enrolled_teams(displayed_season)


def team_finances(request: HttpRequest, league_id: int) -> HttpResponse:
    """FIN-01 -- Team Finances (league-context) page."""
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    league = get_owned_or_404(League, request, pk=league_id)
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

    # No Season => empty-state (the sidebar still renders).
    if displayed_season is None:
        context = {
            **base_context,
            "team": None,
            "enrolled_teams": [],
            "history": [],
        }
        return render(request, "leagues/team_finances.html", context)

    # A posted ``team_id`` only ever selected the Team to write; it is ignored.
    team, enrolled_teams = _resolve_team(
        league, displayed_season, request.GET.get("team_id")
    )

    form: TeamFinancesForm | None = None
    if request.method == "POST":
        editable_team = league.current_team
        if editable_team is None:
            return redirect(f"/leagues/{league.id}/team/finances/")
        # Absent fields keep the Team's current values (the FIN-01 form posts
        # every field; older clients may omit the FIN-04 ones).
        form = TeamFinancesForm(
            merged_form_data(TeamFinancesForm, editable_team, request.POST),
            instance=editable_team,
        )
        try:
            update_team_finances(league, editable_team, form)
        except (FinanceDisabled, TeamNotEditable):
            return redirect(f"/leagues/{league.id}/team/finances/")
        if not form.errors:
            messages.success(request, "Expense settings saved.")
            return redirect(f"/leagues/{league.id}/team/finances/")
        # Re-render the current Team with the errors; reload it so the
        # inputs show the saved values, not the rejected ones.
        team = Team.objects.get(pk=editable_team.pk)

    if team is None:
        context = {
            **base_context,
            "team": None,
            "enrolled_teams": enrolled_teams,
            "history": [],
        }
        return render(request, "leagues/team_finances.html", context)

    data = build_team_finances(displayed_season, team)
    roster_salaries = [
        {"name": r["player"].name, "salary": r["salary"], "player_id": r["player"].id}
        for r in data.roster_salaries
    ]
    availability = [
        {
            "name": r["player"].name,
            "games_remaining": r["games_remaining"],
            "player_id": r["player"].id,
        }
        for r in data.availability
    ]

    context = {
        **base_context,
        "team": team,
        "enrolled_teams": enrolled_teams,
        "editable": team.id == league.current_team_id,
        "form": form,
        "history": data.history,
        "live_payroll": data.live_payroll,
        "live_luxury_tax": data.live_luxury_tax,
        "live_min_payroll_penalty": data.live_min_payroll_penalty,
        "max_level": finance.MAX_LEVEL,
        "default_level": finance.DEFAULT_LEVEL,
        "roster_salaries": roster_salaries,
        "roster_salary_total": data.live_payroll,
        "budget_costs": data.budget_costs,
        "wins": data.wins,
        "losses": data.losses,
        "games": data.games,
        "current_hype": data.current_hype,
        "latest_finance": data.latest_finance,
        "injury_policy": team.injury_policy,
        "availability": availability,
    }
    return render(request, "leagues/team_finances.html", context)
