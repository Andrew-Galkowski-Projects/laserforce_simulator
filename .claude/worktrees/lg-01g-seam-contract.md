# LG-01g — Per-Team Schedule View · Seam Contract

Locked artifact for the three parallel agents (code / tests / docs). LG-01g
ships the **per-Team Schedule page** at
`GET /leagues/<int:league_id>/team_schedule/<int:team_id>/`, the
**new `League.current_team` FK** (single-column migration
`0030_league_current_team.py`), the **LG-01b `league_create` auto-set**
that populates `current_team` to the alphabetically-first Team at League
create time, and the **LG-01f sidebar wiring flip** that turns the
TEAM > Schedule entry from disabled placeholder into a live link
targeting the picked Team's schedule page. **No simulator touch, no RNG,
no `_flush_to_db` touch, no SIM-07 / SIM-08 interaction, no Score
Calibration re-baseline, no new pure module, no API, no JS framework,
no `messages.*`, no Celery, no admin change, no ADR.**

This contract mirrors the structure of
[`.claude/worktrees/lg-01f-seam-contract.md`](lg-01f-seam-contract.md)
(sidebar partial / context processor / DOM-id discipline / 14-entry
helper signature precedent),
[`.claude/worktrees/lg-01b-seam-contract.md`](lg-01b-seam-contract.md)
(the `@transaction.atomic` view body LG-01g's `current_team` auto-set
lands inside),
[`.claude/worktrees/lg-01c-seam-contract.md`](lg-01c-seam-contract.md)
(the `displayed_season` resolution chain pattern), and
[`.claude/worktrees/lg-01e-seam-contract.md`](lg-01e-seam-contract.md)
(POST view shape / session-write rule / single-decorator precedent —
LG-01g's GET view is the read-only twin of this shape).

This contract **rescopes** the PLAN.md LG-01g literal URL (`/games/`)
to a per-Team Schedule view at `/team_schedule/<team_id>/`. The rescope
is intentional and final; the Docs agent annotates PLAN.md with this
note (§9). The PLAN entry at lines 738–743 is superseded by this
contract; no other PLAN line is touched.

---

## 0. Overview & Locked Names (front-loaded — copy-paste source for Step 7 agents)

Every public name LG-01g introduces or pins, in one place. Every name
below is final; any drift in Step 7 is rework.

| Kind | Name | Notes |
|---|---|---|
| URL path | `/leagues/<int:league_id>/team_schedule/<int:team_id>/` | Inserted AFTER LG-01f `<int:league_id>/history/` and BEFORE LG-01a `""` in `matches/league_urls.py` |
| URL name | `team_schedule` | Bare name, no `app_name`. `reverse("team_schedule", kwargs={"league_id": league.id, "team_id": team.id})` |
| URL file edit | `matches/league_urls.py` | Single-line insert; final order `[create/, <int:league_id>/, <int:league_id>/next-season/, <int:league_id>/history/, <int:league_id>/team_schedule/<int:team_id>/, ""]` |
| View | `matches.views.team_schedule` | `(request: HttpRequest, league_id: int, team_id: int) -> HttpResponse`, undecorated (read-only), GET-only via `HttpResponseNotAllowed(["GET"])` as the first line of the body |
| Helper | `matches.views._resolve_current_team_for_sidebar` | Module-level flat helper, `(league: League, displayed_season: Season \| None) -> Team \| None`, encapsulates the order-(a)-(b)-(c) Team-pick chain so the view + the sidebar helper share one source of truth |
| Helper | `matches.views._render_fixture_sides` | Module-level flat helper, `(fixture: ScheduleFixture, teams_by_id: dict[int, Team]) -> tuple[Team, Team]`, returns `(red_team, blue_team)` accounting for the Round-2 per-Match colour swap |
| Helper | `matches.views._build_team_schedule_rows` | Module-level flat helper, `(displayed_season: Season, team: Team, fixtures: list[ScheduleFixture], played_game_rounds: Iterable[GameRound], teams_by_id: dict[int, Team]) -> dict[str, list[dict]]`, returns `{"upcoming": list[dict], "completed": list[dict]}` |
| Helper MODIFIED | `matches.views._build_league_sidebar_links` | LG-01f helper signature **MUST NOT change**. The internal body flips the `"schedule_team"` entry from always-disabled to LIVE-when-Season+Team-resolved by reading `league.current_team_id` |
| Model field (NEW) | `matches.models.League.current_team` | `ForeignKey("teams.Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_in_leagues")` |
| Migration (NEW) | `matches/migrations/0030_league_current_team.py` | Single `AddField` on `League`; dep `("matches", "0029_league_season_match_fk")` |
| Reverse accessor | `team.managed_in_leagues` | `Manager[League]` — the Leagues this Team is the `current_team` for |
| View edit (LG-01b) | `matches.views.league_create` | INSIDE the existing `@transaction.atomic` body: after `_generate_teams(...)` returns and after `League.objects.create(...)`, BEFORE `Season.objects.create(...)`, set `league.current_team = sorted(created_teams, key=lambda t: t.name)[0]` and `league.save(update_fields=["current_team"])` |
| Template (NEW) | `templates/leagues/team_schedule.html` | Extends `base.html`; `{% block title %}{{ team.name }} — Schedule{% endblock %}` (em-dash U+2014, locked exact format) |
| Template MODIFIED | (none) | LG-01f sidebar partial is consumed unchanged; the schedule_team entry's LIVE/disabled state is computed view-side in `_build_league_sidebar_links` |
| `sidebar_active` literal | `"schedule_team"` | Extends LG-01f enum; only the Team Schedule view sets this |
| Session key | `request.session["last_league_id"]` | Integer; written by `team_schedule` after the 405 / 404 guards, before render — extends the LG-01f session-write site list |
| Context key (team schedule view) | `league` | The resolved `League` object |
| Context key (team schedule view) | `displayed_season` | Resolved `Season` (active or latest completed) — guaranteed non-`None` by the rule-3 404 |
| Context key (team schedule view) | `team` | The resolved picked `Team` object |
| Context key (team schedule view) | `upcoming_rows` | `list[dict]` — 7-key Upcoming row dicts (§5) |
| Context key (team schedule view) | `completed_rows` | `list[dict]` — 11-key Completed row dicts (§5) |
| Context key (team schedule view) | `team_picker_options` | `QuerySet[Team]` (or list) — `displayed_season.teams.order_by("name")` |
| Context key (team schedule view) | `sidebar_links` | `list[dict]` — the 14 entries from `_build_league_sidebar_links(league, displayed_season, "schedule_team")` |
| Context key (team schedule view) | `sidebar_active` | Literal `"schedule_team"` |
| Context key (team schedule view) | `current_team` | `league.current_team` (`Team | None`) — used for an optional "Your team" badge on the picker; if the badge is dropped, the key stays in context for forward-compat |
| DOM id | `team-schedule-header` | Page header wrapper (always present); contains the picked team name + Season name |
| DOM id | `team-schedule-team-picker-form` | The `<form method="get">` wrapping the dropdown |
| DOM id | `team-schedule-team-picker` | The `<select>` element |
| DOM id | `team-schedule-team-picker-apply` | The `<noscript>` Apply submit button; present only inside the `<noscript>` block |
| DOM id | `team-schedule-upcoming-section` | The Upcoming column wrapper (always present) |
| DOM id | `team-schedule-upcoming-list` | The `<ul>` / `<table>` of Upcoming rows; only when `upcoming_rows` non-empty |
| DOM id | `team-schedule-upcoming-empty` | Notice when `upcoming_rows` empty; substring `"No upcoming games"` |
| DOM id | `team-schedule-completed-section` | The Completed column wrapper (always present) |
| DOM id | `team-schedule-completed-list` | The `<ul>` / `<table>` of Completed rows; only when `completed_rows` non-empty |
| DOM id | `team-schedule-completed-empty` | Notice when `completed_rows` empty; substring `"No completed games"` |
| DOM id (per Upcoming row) | `team-schedule-upcoming-row-{matchday}-{round_number}` | The `(matchday, round_number)` pair is the unique Upcoming fixture key |
| DOM id (per Completed row) | `team-schedule-completed-row-{game_round_id}` | `game_round_id` is the persisted unique key |
| Preserved DOM id (LG-01f) | `league-sidebar` | Outer sidebar wrapper — untouched |
| Preserved DOM id (LG-01f) | `sidebar-team-schedule_team` | Sidebar entry id — preserved; inner element flips from `<span class="…disabled…">` to `<a href="…">` on pages where the entry is LIVE |
| CSS class substring | `"active"` | Applied to the sidebar entry whose `key` matches `sidebar_active` — on the Team Schedule page the `schedule_team` entry's class contains `"active"` |
| CSS class substring | `"disabled"` | No longer applies to `schedule_team` on pages where the entry is LIVE; still applies on the league dashboard / season dashboard when the fallback chain returns `None` (no Team resolvable) |
| Literal | `"(R)"` | Red-side prefix glyph in row VS strings — view-side concatenation |
| Literal | `"(B)"` | Blue-side prefix glyph in row VS strings |
| Literal | `"W"` | Outcome string (Win), single-char, uppercase |
| Literal | `"L"` | Outcome string (Loss), single-char, uppercase |
| Literal | `"T"` | Outcome string (Tie), single-char, uppercase |
| Literal | `"No upcoming games"` | Upcoming empty-state notice substring |
| Literal | `"No completed games"` | Completed empty-state notice substring |
| Literal | `"No Season in this League."` | 404 message body when the `displayed_season` chain returns `None` |
| Literal | `{team_name} — Schedule` | Page title format (em-dash U+2014, NOT hyphen) |
| Test file (NEW) | `matches/tests/test_lg01g_team_schedule.py` | Django `TestCase` for the view, helpers, DOM ids, sidebar wiring |
| Test file EXTENDED | `matches/tests/test_lg01_models.py` | Append `TestLeagueCurrentTeamField` |
| Test file EXTENDED | `matches/tests/test_league_create.py` | Append `TestLg01gCurrentTeamAutoSet` |
| Test file EXTENDED | `matches/tests/test_league_sidebar.py` | Append `TestLg01gScheduleTeamEntryLive` |
| Round detail URL (linked from Completed row) | `/matches/game-round/{game_round_id}/` | Existing Round detail route; Code agent resolves via `reverse(...)` of the existing URL name (do NOT add a new URL); literal path is pinned for tests |

---

## 1. URL + View Signature

### 1a. URL

A single new path entry in `matches/league_urls.py`. **No new URL include
file.** The LG-01a-mounted file is already routed by
`laserforce_simulator/urls.py`.

**Insertion point:** the new `path(...)` line is inserted **AFTER** the
LG-01f `path("<int:league_id>/history/", views.league_history, name="league_history")`
entry and **BEFORE** the LG-01a `path("", views.league_list, name="league_list")`
entry. Django URL resolution is first-match — every typed
`<int:league_id>/...` pattern is more specific than the empty `""`
pattern.

**Final `urlpatterns` order** (LG-01b top + LG-01c dashboard + LG-01e
next-season + LG-01f history + LG-01g addition + LG-01a tail):

1. `path("create/", views.league_create, name="league_create")` *(LG-01b)*
2. `path("<int:league_id>/", views.league_dashboard, name="league_dashboard")` *(LG-01c)*
3. `path("<int:league_id>/next-season/", views.next_season, name="next_season")` *(LG-01e)*
4. `path("<int:league_id>/history/", views.league_history, name="league_history")` *(LG-01f)*
5. `path("<int:league_id>/team_schedule/<int:team_id>/", views.team_schedule, name="team_schedule")` *(NEW — LG-01g)*
6. `path("", views.league_list, name="league_list")` *(LG-01a)*

- **URL name** is `team_schedule` (bare, **no `app_name`**) — mirrors
  every LG-01x bare-name precedent.
- **HTTP methods:** GET only. Non-GET ⇒ **405** via
  `HttpResponseNotAllowed(["GET"])` as the **first** line of the view
  body (LG-01c / LG-01d / LG-01e / LG-01f locked pattern). NOT
  `@require_GET`.

### 1b. View signature

```python
def team_schedule(request: HttpRequest, league_id: int, team_id: int) -> HttpResponse:
    """LG-01g — Per-Team Schedule page.

    Two-column read-only view of a single Team's per-Round schedule
    inside the displayed Season of ``league_id``. The Upcoming column
    enumerates unplayed (fixture, round_number) pairs that involve the
    Team; the Completed column enumerates persisted GameRounds for
    Matches involving the Team. A dropdown above the columns navigates
    to a different Team's view inside the same League.
    """
```

- **No decorator.** Read-only; no `@transaction.atomic`. No
  `@require_GET` — the explicit `HttpResponseNotAllowed(["GET"])` guard
  is the locked pattern.
- **405 guard:** first line of the body, before any ORM hit.
- **404 guards (in order):**
  1. `league = get_object_or_404(League, pk=league_id)`
  2. `team = get_object_or_404(Team, pk=team_id)` — defensive
     standalone 404; the contract does NOT require the Team to be in
     `displayed_season.teams` (tests for the cross-Season case land in
     LG-02; LG-01g's dropdown only offers in-Season Teams, but a
     hand-typed URL targeting an out-of-Season Team is a defensive 200
     with `upcoming_rows=[]` + `completed_rows=[]` — see §3 step 7
     defensive note).
  3. **`displayed_season is None` ⇒ `Http404("No Season in this League.")`** — when
     `league.active_season is None` AND no completed Season exists.

---

## 2. Model Change + Migration

### 2a. `League.current_team` field

```python
# matches/models.py — inside class League(models.Model):
current_team = models.ForeignKey(
    "teams.Team",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="managed_in_leagues",
)
```

- **String reference** `"teams.Team"` (NOT a direct import) — mirrors
  the LG-01 / LG-01a precedent on cross-app FKs in `matches/models.py`
  (avoids a circular import).
- **`null=True, blank=True`** — pre-LG-01g Leagues have `current_team=None`;
  the LG-01b auto-set populates it on new Leagues only. No backfill.
- **`on_delete=models.SET_NULL`** — Team deletion (admin or test
  teardown) nulls the FK on every League pointing at the Team; does
  not cascade-delete the League. The LG-01g view's fallback chain
  handles `current_team is None` cleanly (§7 rule 10).
- **`related_name="managed_in_leagues"`** — the reverse accessor on
  Team. Plural because a single Team could be the current_team of
  multiple Leagues (no uniqueness constraint at the DB level — a Team
  may participate in multiple Leagues, and the user could plausibly
  manage that Team in more than one). CAR-01 may add a uniqueness
  constraint later; LG-01g intentionally leaves it open.

### 2b. Migration `0030_league_current_team.py`

```python
# matches/migrations/0030_league_current_team.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("teams", "<latest teams migration>"),   # Code agent resolves via `python manage.py makemigrations --dry-run`
        ("matches", "0029_league_season_match_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="league",
            name="current_team",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="managed_in_leagues",
                to="teams.team",
            ),
        ),
    ]
```

- **Single `AddField` operation** — no other migration changes.
- **`dependencies`:** `("matches", "0029_league_season_match_fk")` is
  verified at LG-01g grilling time as the latest LG-01 migration in
  the repo. The Code agent verifies via `python manage.py makemigrations --check --dry-run`
  before landing the file; if a newer migration has merged since
  grilling (e.g. 0030 was taken by another worktree), renumber to the
  next available integer AND update the dep accordingly. The contract
  pins the **field shape** and the **single-op shape**, not the exact
  integer (renumber-safe).
- **`teams` migration dep:** points to the latest migration in the
  `teams` app at land time so the FK target exists in the dep graph;
  Code agent resolves the literal name via `manage.py makemigrations`.

---

## 3. View Body Algorithm (step by step)

The view body is a pinned 9-step sequence. **No steps reordered, no
steps added, no steps omitted.**

1. **405 guard:** `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`.
   First line of body, before any ORM hit.

2. **404 guard (League):** `league = get_object_or_404(League, pk=league_id)`.

3. **404 guard (Team):** `team = get_object_or_404(Team, pk=team_id)`.
   Defensive — the dropdown only offers in-Season Teams, but a
   hand-typed URL with a deleted-Team id 404s cleanly.

4. **Resolve `displayed_season`:**
   ```python
   displayed_season = (
       league.active_season
       or league.seasons.filter(state="completed").order_by("-id").first()
   )
   if displayed_season is None:
       raise Http404("No Season in this League.")
   ```
   - `league.active_season` is the LG-01 `@property` (NOT a re-implemented
     query). LG-01c / LG-01f locked precedent.
   - The 404 fires when a League exists but has zero Seasons of any
     state. (Should rarely happen — LG-01b auto-creates a Season at
     League create time — but a manually-deleted Season can produce
     this state.)

5. **Compute fixtures + `teams_by_id`:**
   ```python
   team_ids = displayed_season.starting_team_ids_json or sorted(
       t.id for t in displayed_season.teams.all()
   )
   fixtures = generate_schedule(team_ids, displayed_season.schedule_format)
   teams_by_id = Team.objects.in_bulk(team_ids)
   ```
   - `starting_team_ids_json or sorted(...)` fallback handles the
     unactivated `draft` Season case (the snapshot is `None` until
     `Season.start_season()` lands). Mirrors LG-01c / LG-01f
     precedent byte-for-byte.
   - `generate_schedule(team_ids, schedule_format)` is the LG-01
     pure-module entry point in `matches/schedule_generator.py`.
     Returns `list[ScheduleFixture]` sorted by `(matchday, team_a_id)`.
   - `Team.objects.in_bulk(team_ids)` is a single `IN`-query — used
     for both the row-rendering name lookups AND the picker dropdown
     (the dropdown overlaps `displayed_season.teams.order_by("name")`
     — the QuerySet, NOT this lookup — so it can carry CSS hooks like
     "is the current_team").

6. **Fetch played GameRounds for this Team in this Season:**
   ```python
   played_game_rounds = list(
       GameRound.objects
       .filter(match__season=displayed_season)
       .filter(match__team_red=team) | GameRound.objects
       .filter(match__season=displayed_season)
       .filter(match__team_blue=team)
       # equivalent unified form via Q:
       # .filter(match__season=displayed_season, **)
       .select_related("match", "match__team_red", "match__team_blue")
       .order_by("id")
   )
   ```
   Recommended canonical form (Code agent chooses; the contract pins
   the resulting set and the `select_related`, not the precise QuerySet
   syntax):
   ```python
   from django.db.models import Q
   played_game_rounds = list(
       GameRound.objects.filter(
           match__season=displayed_season,
       ).filter(
           Q(match__team_red=team) | Q(match__team_blue=team)
       ).select_related(
           "match", "match__team_red", "match__team_blue",
       ).order_by("id")
   )
   ```
   - `select_related` eliminates per-row JOIN queries when the row
     builder accesses `gr.match.team_red_id` / `gr.match.team_blue_id`
     / per-Round point fields.
   - `order_by("id")` matches the Completed-rows sort rule from §5
     (chronological insertion order).

7. **Build the rows via the helper:**
   ```python
   rows = _build_team_schedule_rows(
       displayed_season=displayed_season,
       team=team,
       fixtures=fixtures,
       played_game_rounds=played_game_rounds,
       teams_by_id=teams_by_id,
   )
   upcoming_rows = rows["upcoming"]
   completed_rows = rows["completed"]
   ```
   - Defensive: when `team.id not in team_ids` (hand-typed URL for an
     out-of-Season Team), the helper naturally returns
     `{"upcoming": [], "completed": []}` because the `team.id in {fixture.team_a_id, fixture.team_b_id}`
     filter matches nothing and the `played_game_rounds` queryset is
     empty (no Matches in this Season for this Team). The view
     does NOT 404 in that case — it renders the empty-state notices.

8. **Session write:** `request.session["last_league_id"] = league.id`.
   - Writes the int (not str) so the `league_nav` context processor
     can `reverse(...)` cleanly. Must fire AFTER the 405 / 404 guards
     (so a stale 404 doesn't pin a deleted League id into the session)
     and BEFORE the final template render. LG-01f locked rule.

9. **Build the sidebar + render:**
   ```python
   sidebar_links = _build_league_sidebar_links(league, displayed_season, "schedule_team")
   context = {
       "league": league,
       "displayed_season": displayed_season,
       "team": team,
       "upcoming_rows": upcoming_rows,
       "completed_rows": completed_rows,
       "team_picker_options": displayed_season.teams.order_by("name"),
       "sidebar_links": sidebar_links,
       "sidebar_active": "schedule_team",
       "current_team": league.current_team,
   }
   return render(request, "leagues/team_schedule.html", context)
   ```
   - All **9 context keys MUST be present in every render path** —
     including the defensive empty-state render.

---

## 4. Helper Function Signatures

All three helpers are module-level flat functions in
`matches/views.py`, private (`_`-prefix), no DB hits beyond what the
caller has already pre-fetched. LG-01c / LG-01f / RV-01 precedent for
view-glue surfaces.

### 4a. `_resolve_current_team_for_sidebar`

```python
def _resolve_current_team_for_sidebar(
    league: League,
    displayed_season: Season | None,
) -> Team | None:
    """LG-01g — Pick the Team that the TEAM > Schedule sidebar entry
    targets when LIVE.

    Resolution chain (in order):
        (a) ``league.current_team`` if that Team is enrolled in
            ``displayed_season`` (defensive — admin may have removed the
            Team from the Season's M2M between the auto-set and this
            render).
        (b) The alphabetically-first Team in ``displayed_season.teams``.
        (c) ``None`` — no Team in Season; the sidebar entry stays
            disabled.

    Returns ``None`` when ``displayed_season is None`` (the league
    dashboard case at LG-01f) — the entry stays disabled.
    """
```

- **Read `league.current_team_id`** (the underlying FK column) before
  accessing `league.current_team` to avoid an extra SELECT when the
  Team is already cached or unused. LG-01e `season.league_id`
  precedent. (Code agent: `if league.current_team_id and league.current_team_id in displayed_season.teams.values_list("id", flat=True): ...`)
- **The enrolled-in-Season check** uses
  `displayed_season.teams.values_list("id", flat=True)` — a single
  `SELECT id`-only query. **NOT** `displayed_season.teams.filter(pk=league.current_team_id).exists()`
  (which also works, but the `.values_list("id", flat=True)` form is
  reusable if the sidebar helper has already fetched the id list for
  the dropdown options — Code agent picks the form that minimises
  query count when integrated into `_build_league_sidebar_links`).
- **Branch (b) tiebreaker:** `displayed_season.teams.order_by("name").first()`.
  Stable, lowercase-insensitive (default Django ORM ordering uses the
  database collation; for SQLite test DBs this is case-insensitive by
  default).

### 4b. `_render_fixture_sides`

```python
def _render_fixture_sides(
    fixture: ScheduleFixture,
    teams_by_id: dict[int, Team],
) -> tuple[Team, Team]:
    """LG-01g — Resolve a fixture's per-Round Side assignment.

    For ``fixture.round_number == 1``: returns
    ``(teams_by_id[fixture.team_a_id], teams_by_id[fixture.team_b_id])``.

    For ``fixture.round_number == 2``: returns
    ``(teams_by_id[fixture.team_b_id], teams_by_id[fixture.team_a_id])``
    — the per-Match colour swap, view-side flip. This mirrors the
    arg-reversal that ``simulate_scheduled_round`` performs when
    persisting the second Round of a Match: the lower-id Team plays
    Red in Round 1 and Blue in Round 2 (and vice versa).

    Raises:
        KeyError: if a team id is missing from ``teams_by_id``. The
            schedule never produces fixture ids outside
            ``starting_team_ids_json`` (the lookup is built from that
            same set), so a ``KeyError`` is a real bug — the helper
            does not swallow it.
    """
```

- **Pure function** — no DB hits, no Django imports beyond the
  `ScheduleFixture` dataclass type hint.
- **Returns a tuple of `Team` objects**, NOT names — the caller (the
  row-builder) reads `.id` and `.name` off each Team.

### 4c. `_build_team_schedule_rows`

```python
def _build_team_schedule_rows(
    displayed_season: Season,
    team: Team,
    fixtures: list[ScheduleFixture],
    played_game_rounds: Iterable[GameRound],
    teams_by_id: dict[int, Team],
) -> dict[str, list[dict]]:
    """LG-01g — Walk fixtures + played GameRounds, sort into
    Upcoming / Completed columns from the picked Team's perspective.

    Algorithm (pinned):
        1. Build ``played_keys = {(frozenset({gr.match.team_red_id,
           gr.match.team_blue_id}), gr.round_number)
           for gr in played_game_rounds}`` — Side-agnostic, mirrors
           the LG-01 / LG-01c ``find_next_fixture`` precedent.
        2. Build ``fixture_by_key: dict[(frozenset, int), ScheduleFixture]``
           from the full ``fixtures`` list — for the Completed-row
           ``matchday`` recovery (§5b note).
        3. Filter ``fixtures`` to ones where
           ``team.id in {fixture.team_a_id, fixture.team_b_id}``.
        4. For each filtered fixture:
            - If ``(frozenset({fixture.team_a_id, fixture.team_b_id}),
              fixture.round_number) in played_keys`` ⇒ skip (the
              played-rounds loop will produce the Completed row for
              this fixture instead).
            - Else ⇒ append an Upcoming row dict (§5a, 7 keys),
              computed via ``_render_fixture_sides(fixture,
              teams_by_id)``.
        5. For each GameRound in ``played_game_rounds`` (already
           ordered by id asc in the view's queryset):
            - Look up the matching fixture via
              ``fixture_by_key.get((frozenset({gr.match.team_red_id,
              gr.match.team_blue_id}), gr.round_number))``;
              ``matchday = fixture.matchday`` if found, else
              ``matchday = 0`` (defensive — sandbox-Match conversion
              edge case; do not crash).
            - Append a Completed row dict (§5b, 11 keys); the Side
              read comes from the persisted ``gr.match.team_red`` /
              ``gr.match.team_blue`` directly (NOT recomputed via
              ``_render_fixture_sides`` — the GameRound already
              records the actual physical Sides for that Round).
        6. Sort Upcoming by ``(matchday, round_number)`` asc.
           Completed retains the view's queryset order (id asc =
           chronological).

    Returns:
        ``{"upcoming": list[dict], "completed": list[dict]}``.
    """
```

- **No DB hits** — the helper consumes the pre-fetched `played_game_rounds`
  list and `teams_by_id` lookup. The view's `select_related` on the
  queryset ensures `gr.match.team_red` / `gr.match.team_blue` access
  is JOIN-free.
- **`Iterable[GameRound]` parameter type** — the helper accepts a
  list, queryset, or generator. The view passes a list for clarity;
  tests may pass a list directly without `.objects` wrapping.

---

## 5. Context Keys + Row Dict Shapes

### 5a. Upcoming row — frozen 7-key dict shape

```python
{
    "matchday":        int,
    "round_number":    int,                  # 1 or 2
    "date":            datetime.date,        # displayed_season.start_date + timedelta(days=(matchday - 1) * 7)
    "red_team_id":     int,
    "red_team_name":   str,
    "blue_team_id":    int,
    "blue_team_name":  str,
}
```

- **`date` derivation** is byte-for-byte the LG-01 `season_schedule`
  precedent: `displayed_season.start_date + timedelta(days=(matchday - 1) * 7)`.
  Imports `timedelta` from `datetime` (already imported by LG-01b /
  LG-01e at the top of `views.py` — defensive check first, no
  duplicate import).
- **Side assignment** comes from `_render_fixture_sides(fixture, teams_by_id)`:
  - Round 1: `red = teams_by_id[fixture.team_a_id]`, `blue = teams_by_id[fixture.team_b_id]`.
  - Round 2: `red = teams_by_id[fixture.team_b_id]`, `blue = teams_by_id[fixture.team_a_id]`
    (the per-Match colour swap).
- **No `outcome` key on Upcoming rows** — the game hasn't been played.
- **No `game_round_id` key on Upcoming rows** — no persisted GameRound
  yet.

### 5b. Completed row — frozen 11-key dict shape

```python
{
    "matchday":         int,                # see "matchday lookup" below
    "round_number":     int,                # game_round.round_number (1 or 2)
    "date":             datetime.date,      # same formula as Upcoming, derived from matchday
    "red_team_id":      int,                # game_round.match.team_red_id
    "red_team_name":    str,                # game_round.match.team_red.name
    "blue_team_id":     int,                # game_round.match.team_blue_id
    "blue_team_name":   str,                # game_round.match.team_blue.name
    "game_round_id":    int,                # game_round.id
    "red_score":        int,                # per-Round, NOT per-Match total
    "blue_score":       int,                # per-Round, NOT per-Match total
    "outcome":          str,                # "W" | "L" | "T" from picked Team's per-Round perspective
}
```

- **`red_score` / `blue_score` are PER-ROUND points** — read off the
  Match's per-Round columns based on `game_round.round_number`:

  ```python
  if game_round.round_number == 1:
      red_score = game_round.match.red_round1_points
      blue_score = game_round.match.blue_round1_points
  else:  # round_number == 2
      red_score = game_round.match.red_round2_points
      blue_score = game_round.match.blue_round2_points
  ```

  Verified field names against `matches/models.py` Match class:
  `red_round1_points`, `blue_round1_points`, `red_round2_points`,
  `blue_round2_points` (all `IntegerField(default=0)`). Per-Round
  semantics is the LG-01g contract — NOT the rolled-up
  `Match.red_total_points` / `Match.blue_total_points` (which roll up
  both Rounds plus `red_bonus_points` / `blue_bonus_points`).

- **`outcome` derivation** (per-Round, from picked Team's perspective):
  ```python
  picked_is_red = (team.id == game_round.match.team_red_id)
  picked_per_round = red_score if picked_is_red else blue_score
  other_per_round = blue_score if picked_is_red else red_score
  if picked_per_round > other_per_round:
      outcome = "W"
  elif picked_per_round < other_per_round:
      outcome = "L"
  else:
      outcome = "T"
  ```

  - **NOT** derived from `Match.winner` / `Match.red_rounds_won` /
    `Match.blue_rounds_won` — the picked Team can have a Match that
    rolled up as a win (e.g. 1-1 tie on rounds + Red wins on total
    points + Red was the picked Team) yet have *lost* this individual
    Round. The contract pins per-Round outcome; the Tests agent
    pins this with `test_outcome_is_per_round_not_per_match_winner`.

- **`matchday` lookup for Completed rows:** a `GameRound` does not
  store `matchday` directly. The helper recovers it via the
  `fixture_by_key` lookup built from the full `fixtures` list (§4c
  step 2). When no fixture matches (defensive — sandbox-Match
  conversion edge case, where a Match was created outside the
  schedule and later re-pointed at the Season), set `matchday = 0`
  and continue. **Do not crash.** The contract documents this
  fallback; the Tests agent does NOT pin a test for it (it's a
  defensive arm, not a behavioural contract).

### 5c. View context — 9 frozen keys

| Key | Type | Source |
|---|---|---|
| `league` | `League` | `get_object_or_404(League, pk=league_id)` |
| `displayed_season` | `Season` | resolution chain (rule 3 / §3 step 4); guaranteed non-`None` |
| `team` | `Team` | `get_object_or_404(Team, pk=team_id)` |
| `upcoming_rows` | `list[dict]` | `rows["upcoming"]` from `_build_team_schedule_rows` |
| `completed_rows` | `list[dict]` | `rows["completed"]` from `_build_team_schedule_rows` |
| `team_picker_options` | `QuerySet[Team]` | `displayed_season.teams.order_by("name")` |
| `sidebar_links` | `list[dict]` | `_build_league_sidebar_links(league, displayed_season, "schedule_team")` |
| `sidebar_active` | `str` | literal `"schedule_team"` |
| `current_team` | `Team \| None` | `league.current_team` |

- **All 9 keys MUST be present in every render path.** Tests assert
  this with a single `test_view_ships_nine_frozen_context_keys` test.

---

## 6. Template + DOM Ids

### 6a. NEW template `templates/leagues/team_schedule.html`

- **Path:** `laserforce_simulator/templates/leagues/team_schedule.html`.
- **Extends:** `extends "base.html"`.
- **Includes:** `{% include "_partials/league_sidebar.html" %}` — the
  LG-01f partial, consumed unchanged.
- **Block title:** `{% block title %}{{ team.name }} — Schedule{% endblock %}`
  — em-dash U+2014 (locked exact format, NOT hyphen, NOT en-dash).

### 6b. Page structure (Code agent's discretion on exact markup; only the locked DOM ids + empty-state substrings + (R)/(B) glyph format are pinned)

```django
{% extends "base.html" %}
{% block title %}{{ team.name }} — Schedule{% endblock %}
{% block content %}
  <div class="d-flex">
    {% include "_partials/league_sidebar.html" %}
    <main>
      <header id="team-schedule-header">
        <h1>{{ team.name }} — Schedule</h1>
        <p>{{ displayed_season.name }}</p>
        <form id="team-schedule-team-picker-form" method="get">
          <select id="team-schedule-team-picker"
                  name="picked_team_id"
                  onchange="window.location.href = '{% url 'team_schedule' league_id=league.id team_id=0 %}'.replace('/0/', '/' + this.value + '/');">
            {% for option in team_picker_options %}
              <option value="{{ option.id }}" {% if option.id == team.id %}selected{% endif %}>{{ option.name }}</option>
            {% endfor %}
          </select>
          <noscript>
            <button type="submit" id="team-schedule-team-picker-apply">Apply</button>
          </noscript>
        </form>
      </header>

      <section id="team-schedule-upcoming-section">
        <h2>Upcoming Games</h2>
        {% if upcoming_rows %}
          <table id="team-schedule-upcoming-list">
            <thead><tr><th>Matchday</th><th>Date</th><th>Match</th></tr></thead>
            <tbody>
              {% for row in upcoming_rows %}
                <tr id="team-schedule-upcoming-row-{{ row.matchday }}-{{ row.round_number }}">
                  <td>{{ row.matchday }} · R{{ row.round_number }}</td>
                  <td>{{ row.date|date:"Y-m-d" }}</td>
                  <td>(R) {{ row.red_team_name }} VS (B) {{ row.blue_team_name }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div id="team-schedule-upcoming-empty">No upcoming games</div>
        {% endif %}
      </section>

      <section id="team-schedule-completed-section">
        <h2>Completed Games</h2>
        {% if completed_rows %}
          <table id="team-schedule-completed-list">
            <thead><tr><th>Matchday</th><th>Date</th><th>Match</th><th>Score</th><th>Result</th><th></th></tr></thead>
            <tbody>
              {% for row in completed_rows %}
                <tr id="team-schedule-completed-row-{{ row.game_round_id }}">
                  <td>{{ row.matchday }} · R{{ row.round_number }}</td>
                  <td>{{ row.date|date:"Y-m-d" }}</td>
                  <td>(R) {{ row.red_team_name }} VS (B) {{ row.blue_team_name }}</td>
                  <td>{{ row.red_score }} — {{ row.blue_score }}</td>
                  <td>{{ row.outcome }}</td>
                  <td><a href="/matches/game-round/{{ row.game_round_id }}/">View Round detail</a></td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div id="team-schedule-completed-empty">No completed games</div>
        {% endif %}
      </section>
    </main>
  </div>
{% endblock %}
```

- **Inline JS** in the `<select onchange="…">` attribute is the only
  scripting LG-01g adds. Mirrors LG-00c's per-page-selector inline
  JS precedent. The `<noscript>` Apply button is the no-JS fallback;
  on submit it sends `?picked_team_id=N` to the current URL, but
  since the URL has the `team_id` in the path component, the
  no-JS submit does NOT navigate to a new team — it re-renders the
  same page. (Pinned acceptable degradation for LG-01g: the
  `<noscript>` Apply button exists for accessibility (form
  submission feels right with a button) but the actual no-JS
  navigation flow would require a tiny redirect-view we explicitly
  scoped out. CAR-01 may revisit.)
- **The Round detail link** uses the literal path
  `/matches/game-round/{game_round_id}/` in the template for test
  assertion clarity. The Code agent MAY swap this to a `{% url ... %}`
  call if the existing URL name is stable (verify via
  `python manage.py show_urls | findstr game-round` or by reading
  `matches/match_urls.py` / `matches/urls.py`). Tests assert the
  literal path substring `"/matches/game-round/"` followed by the
  `game_round_id` is present in the rendered row.
- **Side prefix glyphs:** literal `"(R)"` and `"(B)"` in the row
  rendering. Concatenation pattern `(R) {red_team_name} VS (B) {blue_team_name}`
  — locked. Tests substring-match.

### 6c. Locked DOM ids — full inventory

| DOM id | Always present? | Notes |
|---|---|---|
| `team-schedule-header` | always | Page header wrapper |
| `team-schedule-team-picker-form` | always | The `<form>` |
| `team-schedule-team-picker` | always | The `<select>` |
| `team-schedule-team-picker-apply` | always (inside `<noscript>`) | The fallback submit button |
| `team-schedule-upcoming-section` | always | Upcoming column wrapper |
| `team-schedule-upcoming-list` | conditional | `<table>` only when `upcoming_rows` non-empty |
| `team-schedule-upcoming-empty` | conditional | `<div>` only when `upcoming_rows` empty; substring `"No upcoming games"` |
| `team-schedule-completed-section` | always | Completed column wrapper |
| `team-schedule-completed-list` | conditional | `<table>` only when `completed_rows` non-empty |
| `team-schedule-completed-empty` | conditional | `<div>` only when `completed_rows` empty; substring `"No completed games"` |
| `team-schedule-upcoming-row-{matchday}-{round_number}` | per Upcoming row | The `(matchday, round_number)` pair is the unique Upcoming fixture key |
| `team-schedule-completed-row-{game_round_id}` | per Completed row | `game_round_id` is the persisted unique key |

### 6d. Preserved LG-01f DOM ids + CSS class substrings

- **`league-sidebar`** — outer sidebar wrapper, untouched.
- **`sidebar-team-schedule_team`** — sidebar entry id, preserved.
  Inner element flips from `<span class="…disabled…">` to
  `<a href="…">` when the entry is LIVE (i.e. when the helper's
  resolution chain produces a non-`None` target Team).
- **CSS class substring `"active"`** — applied to the entry whose
  `key` matches `sidebar_active`. On the Team Schedule page the
  `schedule_team` entry's class contains `"active"`.
- **CSS class substring `"disabled"`** — no longer applies to
  `schedule_team` on pages where the entry is LIVE. Still applies on
  the league dashboard when `displayed_season is None` (no Season in
  League at all) OR when `displayed_season.teams.exists()` is False.

---

## 7. Sidebar Wiring

### 7a. `_build_league_sidebar_links` signature is **UNCHANGED**

The LG-01f helper signature is locked and MUST NOT change at LG-01g:

```python
def _build_league_sidebar_links(
    league: League,
    displayed_season: Season | None,
    sidebar_active: str | None,
) -> list[dict]:
    ...
```

The body of the helper changes to compute the `schedule_team` entry's
`url` / `disabled` fields via the new internal helper
`_resolve_current_team_for_sidebar(league, displayed_season)` (§4a).

### 7b. `schedule_team` entry — new behaviour

The §0 LG-01f-locked entry was:

```python
{"key": "schedule_team", "label": "Schedule", "section": "team", "url": None, "disabled": True, "active": False}
```

LG-01g replaces the `url` / `disabled` computation with:

```python
picked = _resolve_current_team_for_sidebar(league, displayed_season)
if picked is None:
    schedule_team_url = None       # disabled (entry stays disabled per LG-01f shape)
else:
    schedule_team_url = reverse(
        "team_schedule",
        kwargs={"league_id": league.id, "team_id": picked.id},
    )
{
    "key": "schedule_team",
    "label": "Schedule",
    "section": "team",
    "url": schedule_team_url,
    "disabled": (schedule_team_url is None),
    "active": (sidebar_active == "schedule_team"),
}
```

- **The `url` is `None`** (entry stays disabled) when:
  1. `displayed_season is None` (no Season in League), OR
  2. `displayed_season.teams.exists()` is False (Season has no teams), OR
  3. Both branches (a) and (b) of `_resolve_current_team_for_sidebar`
     return `None` — branch (b) returns `None` only when
     `displayed_season.teams.exists()` is False (same as case 2 above).
- **`active=True`** is set only when `sidebar_active == "schedule_team"`
  — i.e. on the Team Schedule page itself. Every other page that
  sets `sidebar_active` (dashboard / standings / schedule / history)
  will see `active=False` on this entry.
- **All 13 other entries are unchanged** from LG-01f. The 14-entry
  count stays at 14.

### 7c. Pages that render the sidebar — `schedule_team` entry state

| Page | `displayed_season` | Sidebar `schedule_team` state |
|---|---|---|
| League dashboard (LG-01c) | resolved per LG-01c chain (active > latest completed > None) | LIVE when Season has teams AND a Team resolvable; disabled otherwise |
| League history (LG-01f) | resolved per LG-01f chain (same) | Same as League dashboard |
| Season dashboard (LG-01c) | the Season itself | LIVE when Season has teams AND a Team resolvable; disabled otherwise |
| Season standings (LG-01) | the Season itself | Same |
| Season schedule (LG-01) | the Season itself | Same |
| **Team Schedule (LG-01g, NEW)** | resolved per LG-01g chain (active > latest completed; 404 if None) | LIVE + `active=True` (the entry's URL targets the picked Team, NOT the Team-pick fallback) |

- **On the Team Schedule page itself** the sidebar's `schedule_team`
  URL is the same URL the user is already on (the picked Team's
  schedule page). This is acceptable — the `active=True` class makes
  the redundancy visually obvious; clicking is idempotent.

### 7d. Defensive degradation cases

| Case | `_resolve_current_team_for_sidebar` returns | Sidebar `schedule_team` state |
|---|---|---|
| `league.current_team` set, Team enrolled in `displayed_season` | branch (a): `league.current_team` | LIVE → `current_team`'s page |
| `league.current_team` set, Team NOT enrolled in `displayed_season` (admin removed) | branch (b): alphabetically-first in-Season Team | LIVE → fallback Team's page |
| `league.current_team = None`, Season has teams | branch (b): alphabetically-first in-Season Team | LIVE → fallback Team's page |
| `league.current_team = None`, Season has no teams | branch (c): `None` | disabled |
| `displayed_season = None` | helper guard: `None` | disabled |

---

## 8. LG-01b Auto-Set

### 8a. Insertion point in `league_create`

The LG-01b `league_create` view body (per
[`.claude/worktrees/lg-01b-seam-contract.md`](lg-01b-seam-contract.md)
§3) has a 6-step pinned skeleton. The LG-01g auto-set lands **after
step 4** (the `_generate_teams(...)` call returns `created_teams`) and
**inside step 5** (the `League.objects.create(...)` + `Season.objects.create(...)`
pair). Specifically:

```python
# ... step 4: created_teams = _generate_teams(...) ...

# step 5a: create League first (no current_team yet — defaults to None)
league = League.objects.create(
    name=cleaned["league_name"],
    mode="league",
    state="active",
)

# === LG-01g auto-set: NEW lines inserted between 5a and 5b ===
league.current_team = sorted(created_teams, key=lambda t: t.name)[0]
league.save(update_fields=["current_team"])
# === end LG-01g insert ===

# step 5b: create the Season (must come AFTER the auto-set so any
# reverse-lookup on League sees the FK populated; intra-transaction
# semantics make this technically equivalent either way, but the
# pinned order keeps the auto-set adjacent to the create that produced
# created_teams)
season = Season.objects.create(
    league=league,
    name=cleaned["season_name"],
    start_date=cleaned["start_date"],
    state="draft",
    schedule_format=cleaned["schedule_format"],
)

# step 6: enroll teams + redirect (unchanged)
season.teams.add(*created_teams)
return redirect("season_standings", season_id=season.id)
```

### 8b. Behaviour

- **`sorted(created_teams, key=lambda t: t.name)[0]`** — picks the
  alphabetically-first Team by `name`. `created_teams` is the
  `list[Team]` returned by `_generate_teams`; ordering is RNG-driven
  inside the generator, so the sort is necessary for deterministic
  auto-set.
- **`save(update_fields=["current_team"])`** — single-column UPDATE,
  avoids touching other fields (mode/state/name) that haven't
  changed. Defensive against any future LG-01b field additions.
- **Inside the `@transaction.atomic` body** — the auto-set is part
  of the create transaction; a later failure (e.g. `Season.objects.create`
  raises) rolls back the `current_team` write atomically with the
  League create.

### 8c. LG-01e `next_season` is **NOT touched**

The LG-01e `next_season` view creates a new draft Season in an
existing League. **No edit to `next_season`** at LG-01g land time:

- `League.current_team` carries forward by reference (it's a FK to a
  Team row; the row persists across Seasons).
- If the carried-forward Team is no longer enrolled in the new draft
  Season's M2M (per the LG-01e snapshot-copy behaviour, the new
  Season's teams come from the previous Season's
  `starting_team_ids_json` — so any Team that was in the previous
  Season is also in the new one — but admin actions between Seasons
  could remove a Team), the LG-01g view's fallback chain handles it
  per §7d row 2 (branch (b) takes over).

---

## 9. Test Boundary

### 9a. NEW file `matches/tests/test_lg01g_team_schedule.py`

Django `TestCase` (or `TransactionTestCase` if the Tests agent finds
session-write assertions require it). The Tests agent writes failing
tests against this list **before** the Code agent lands implementation.

**Locked test classes and methods:**

- **`TestTeamScheduleRouting`**
  - `test_get_returns_200_with_valid_ids`
  - `test_get_returns_405_on_post`
  - `test_get_returns_404_on_missing_league`
  - `test_get_returns_404_on_missing_team`
  - `test_get_returns_404_when_no_season_in_league`

- **`TestTeamScheduleSeasonResolution`**
  - `test_displayed_season_is_active_when_one_exists`
  - `test_displayed_season_falls_back_to_latest_completed`
  - `test_displayed_season_is_none_returns_404`

- **`TestTeamScheduleRowGranularity`**
  - `test_upcoming_row_per_unplayed_fixture`
  - `test_completed_row_per_persisted_game_round`
  - `test_partial_match_round1_in_completed_round2_in_upcoming`

- **`TestTeamScheduleSideAnnotation`**
  - `test_round1_upcoming_renders_team_a_red_team_b_blue`
  - `test_round2_upcoming_renders_team_b_red_team_a_blue_per_match_colour_swap`
  - `test_completed_row_reads_persisted_game_round_team_red_blue`

- **`TestTeamScheduleOutcome`**
  - `test_outcome_W_when_picked_team_side_per_round_points_higher`
  - `test_outcome_L_when_picked_team_side_per_round_points_lower`
  - `test_outcome_T_on_equal_per_round_points`
  - `test_outcome_is_per_round_not_per_match_winner`

- **`TestTeamScheduleSorting`**
  - `test_completed_rows_sorted_by_game_round_id_asc`
  - `test_upcoming_rows_sorted_by_matchday_then_round_number_asc`

- **`TestTeamScheduleDropdown`**
  - `test_team_picker_lists_displayed_season_enrolled_teams_alphabetical`
  - `test_team_picker_select_has_locked_dom_id`
  - `test_team_picker_form_navigates_to_new_team_id_url`

- **`TestTeamScheduleEmptyStates`**
  - `test_no_upcoming_games_renders_notice_with_locked_substring`
  - `test_no_completed_games_renders_notice_with_locked_substring`

- **`TestTeamScheduleContextKeys`**
  - `test_view_ships_nine_frozen_context_keys`
  - `test_sidebar_active_equals_schedule_team`

- **`TestTeamScheduleSidebarWiring`**
  - `test_schedule_team_entry_is_live_on_team_schedule_page`
  - `test_schedule_team_entry_active_true_on_team_schedule_page`
  - `test_schedule_team_entry_disabled_when_no_season_in_league`

- **`TestTeamScheduleSessionWrite`**
  - `test_session_last_league_id_written_after_guards`

- **`TestTeamScheduleDomIds`** — one test per locked DOM id from §6c,
  asserting presence on a happy-path render. At minimum:
  - `test_team_schedule_header_id_present`
  - `test_team_schedule_team_picker_form_id_present`
  - `test_team_schedule_team_picker_id_present`
  - `test_team_schedule_team_picker_apply_id_present`
  - `test_team_schedule_upcoming_section_id_present`
  - `test_team_schedule_upcoming_list_id_present_when_rows`
  - `test_team_schedule_upcoming_empty_id_present_when_no_rows`
  - `test_team_schedule_completed_section_id_present`
  - `test_team_schedule_completed_list_id_present_when_rows`
  - `test_team_schedule_completed_empty_id_present_when_no_rows`
  - `test_team_schedule_upcoming_row_id_format_matchday_round`
  - `test_team_schedule_completed_row_id_format_game_round_id`

### 9b. EXTENDED file `matches/tests/test_lg01_models.py`

Append `TestLeagueCurrentTeamField`:

- `test_current_team_is_nullable` — new `League()` with no `current_team` saves clean; `instance.current_team is None`.
- `test_current_team_default_is_None` — `League.objects.create(name="X")` ⇒ `current_team is None`.
- `test_current_team_set_null_on_team_delete` — create League with `current_team=team`, delete `team`, re-fetch League, assert `current_team is None`.
- `test_related_name_managed_in_leagues` — assert `team.managed_in_leagues.all()` returns the Leagues this Team is the `current_team` for.
- `test_migration_0030_exists` — assert the migration file `matches/migrations/0030_league_current_team.py` exists OR (more robust) assert the field is in the model's `_meta.get_fields()` set under the name `current_team` with the expected on-delete behaviour.

### 9c. EXTENDED file `matches/tests/test_league_create.py`

Append `TestLg01gCurrentTeamAutoSet`:

- `test_league_create_populates_current_team_to_first_alphabetical_team` — POST the form with 4 teams, fetch the created League, assert `league.current_team is not None` AND `league.current_team.name == sorted(team.name for team in league.seasons.first().teams.all())[0]`.
- `test_league_create_current_team_is_in_created_teams` — POST, fetch League, assert `league.current_team` is one of the teams enrolled in the Season's M2M (defence against the auto-set accidentally pointing at a pre-existing Team from a different League).

### 9d. EXTENDED file `matches/tests/test_league_sidebar.py`

Append `TestLg01gScheduleTeamEntryLive`:

- `test_schedule_team_entry_url_resolves_via_current_team_when_in_season` — set `league.current_team = team_in_season`; build sidebar; assert `schedule_team` entry's `url` reverses to `team_schedule(league_id=…, team_id=team_in_season.id)`.
- `test_schedule_team_entry_url_falls_back_to_first_alphabetical_when_current_team_none` — set `league.current_team = None`, Season has teams "B" / "A" / "C"; build sidebar; assert URL targets Team "A".
- `test_schedule_team_entry_url_falls_back_when_current_team_not_in_displayed_season` — set `league.current_team = team_X` where `team_X NOT IN displayed_season.teams.all()`; build sidebar; assert URL targets alphabetically-first in-Season Team (NOT `team_X`).
- `test_schedule_team_entry_disabled_when_displayed_season_is_none` — call helper with `displayed_season=None`; assert `schedule_team` entry has `url=None` AND `disabled=True`.
- `test_schedule_team_entry_disabled_when_displayed_season_has_no_teams` — create Season with no enrolled teams; call helper; assert `schedule_team` entry has `url=None` AND `disabled=True`.

### 9e. Test infrastructure rules (locked)

- Tests MUST NOT touch `simulate_scheduled_round` / `simulate_match` /
  `save_games` or any simulator entry point. Per-Round point fields
  on the Match (`red_round1_points`, etc.) are set directly via
  `Match.objects.create(...)` or `match.<field> = N; match.save()`.
- Tests MUST NOT `mock.patch` the ORM beyond `@override_settings` /
  `TestCase` machinery. LG-01e precedent.
- Tests MAY hand-construct `Match` + `GameRound` + `Team` rows
  directly (LG-01-style fixture pattern). The recommended fixture
  shape per test class is a `setUp` that creates one League + one
  Season (M2M-populated with 4 Teams) + 0-or-more partially-played
  Matches (each Match has both a Round-1 and a Round-2 GameRound,
  or only Round-1 for partial-match tests).

---

## 10. Out of Scope (paste verbatim into the contract — never relitigate)

- **No model change beyond the single `League.current_team` FK
  addition** (no other field, no other model).
- **No migration beyond `0030_league_current_team.py`** (a single
  `AddField`).
- **No new pure module** (`matches/team_schedule.py` is NOT created —
  inline helpers only).
- **No new file in `matches/sim_helpers/`** or anywhere else under
  `matches/`.
- **No edit to** `matches/standings.py` / `matches/schedule_generator.py`
  / `matches/season_dashboard.py` / `matches/tasks.py` /
  `matches/simulation.py` / `matches/season_urls.py` /
  `matches/admin.py` / `matches/forms.py`.
- **No edit to** `teams/models.py` / `teams/views.py` / `teams/forms.py`
  / `teams/admin.py` / `teams/constants.py` / `teams/player_generator.py`.
- **No edit to existing templates** beyond the new
  `templates/leagues/team_schedule.html` file, the
  `_build_league_sidebar_links` schedule_team-entry flip (NO template
  edit — the partial reads `entry["url"]` / `entry["disabled"]` /
  `entry["active"]` and renders accordingly), and the LG-01b view
  edit (no template edit). The 5 LG-01f-modified templates
  (`base.html`, `leagues/dashboard.html`, `seasons/dashboard.html`,
  `seasons/standings.html`, `seasons/schedule.html`) are **untouched**.
- **No new ADR.** Decisions are reversible (one nullable FK + a new
  read-only view + a sidebar flip).
- **No new CONTEXT.md term beyond "Current team"** (the "Team
  schedule" entry was added at grilling time; the Docs agent does
  NOT re-add it).
- **No JS framework / htmx / Alpine / Stimulus** — only inline JS for
  the dropdown's `onchange` navigation (LG-00c precedent) and the
  `<noscript>` Apply fallback button.
- **No API / DRF endpoint** (`/api/leagues/<id>/team_schedule/<tid>/`
  deferred — LG-01g is UI-only).
- **No `django.contrib.messages` flash.**
- **No new dependency** (no `requirements.txt` edit).
- **No backfill** for pre-LG-01g Leagues (`current_team` defaults to
  `None`; existing test fixtures stay valid).
- **No CAR-01 manager-role plumbing** beyond the `current_team` FK
  (CAR-01 may rename / repoint later).
- **No top-nav refactor** (LG-01h scope).
- **No `League.archived` toggle UI** (admin-only, deferred).
- **No simulator touch / no RNG / no Score Calibration re-baseline.**
- **No `_flush_to_db` touch / no SIM-07 / SIM-08 contract interaction.**

---

## 11. Locked Names Index

See §0 — every public name LG-01g introduces or pins is enumerated
there. The §0 table is the **single source of truth**; if a name in
the body of this contract conflicts with §0, §0 wins.

### 11a. Cross-app imports for the view (final list)

The Code agent adds (defensively, checking existing imports — no
duplicates) to the top of `matches/views.py`:

- `from django.shortcuts import get_object_or_404, redirect, render` — every name already imported per LG-01b / LG-01c / LG-01e / LG-01f precedent. Defensive check; no duplicate.
- `from django.http import HttpResponseNotAllowed, Http404` — `HttpResponseNotAllowed` already imported (LG-01c / LG-01d / LG-01e / LG-01f). `Http404` may or may not be — defensive check; add to the existing `from django.http import …` line if not present.
- `from django.urls import reverse` — already imported (LG-01f for sidebar URL building). Defensive check; no duplicate.
- `from django.db.models import Q` — for the OR-filter on `played_game_rounds`. Defensive check; add if not present.
- `from teams.models import Team` — already imported per LG-01b / LG-01c precedent. Defensive check; no duplicate.
- `from datetime import timedelta` — already imported per LG-01b / LG-01 standings/schedule precedent. Defensive check; no duplicate.
- `from .models import League, Season, GameRound, Match` — `League` / `Season` already imported (LG-01 / LG-01b). `GameRound` / `Match` may or may not be — defensive check; add individual names if not present.
- `from .schedule_generator import generate_schedule` — LG-01c precedent. Defensive check; no duplicate.

**NO new top-level imports** beyond those listed above. **NO
`from .season_dashboard import …`** — LG-01g consumes none of those
helpers.

### 11b. PLAN.md edit (Docs agent reference)

The Docs agent:

1. Marks LG-01g as `- completed` in PLAN.md.
2. Appends a dense implementation note in the LG-01 house style
   (mirror LG-01f's note dense-paragraph shape).
3. The note MUST explicitly mention:
   - The **rescope** from the PLAN literal `/games/` to
     `/leagues/<league_id>/team_schedule/<team_id>/`.
   - The new `League.current_team` FK (single-column migration
     `0030_league_current_team.py`).
   - The LG-01b auto-set hook (`league_create` populates
     `current_team` to the alphabetically-first generated Team).
   - The LG-01f sidebar wiring flip (TEAM > Schedule entry now LIVE
     with a fallback chain).
   - The two-column page shape (Upcoming Games + Completed Games) with
     per-Round granularity and the per-Match colour swap on Round 2.
   - Out of scope: no API endpoint, no JS framework, no simulator
     touch, no ADR.

### 11c. CONTEXT.md edit (Docs agent reference)

The Docs agent adds **one** new term to CONTEXT.md (the "Team
schedule" entry was added at grilling time; do NOT re-add):

- **Current team** — under `### League and seasons`, adjacent to
  **League** / **Season**. Definition: the Team within a League that
  the user manages (the one they can edit players on). Persisted as
  `League.current_team` `FK(Team, null=True, on_delete=SET_NULL,
  related_name="managed_in_leagues")`. Set by LG-01b at League create
  time to the first alphabetical Team; SET_NULL on Team delete; LG-01g
  uses it as the default Team-picker target in the TEAM > Schedule
  sidebar entry. CAR-01 (PLAN.md, deferred) will replace the auto-set
  with manager-driven assignment.

Lock the substring `"Current team"` and the paragraph framing above.

---

End of contract. Three parallel agents (code / tests / docs) operate
against this artifact; the Tests agent writes failing tests against
the locked test list (§9) BEFORE the Code agent lands implementation.
