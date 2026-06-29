# DEL-01 — Delete League (full teardown) — SEAM CONTRACT

Guarded **Delete League** performing a FULL TEARDOWN of all data a career-mode
(`League.mode == "league"`) League owns, identified by PK/FK (never by name), in
one `@transaction.atomic` block. Design locked in
[ADR-0032](../../docs/adr/0032-delete-league-full-teardown.md) + the CONTEXT.md
Team/Player one-context ownership invariant (both already written — read them).

**Every name below was verified against the live code.** Verification deltas
from the prompt's assumptions are flagged inline with **✓VERIFIED** /
**⚠CORRECTED**. (Net: the prompt's assumptions were all correct — see the
"Corrections" section at the end; the only adjustment is the URL-insertion detail
+ two soft recommendations.)

---

## Locked names — quick index

| Thing | Locked value | Source (verified) |
|---|---|---|
| View module | `matches/league_views.py` | league routes resolve to `league_views.*` (`league_urls.py:11,14`) |
| View | `matches.league_views.league_delete(request, league_id) -> HttpResponse` | NEW |
| Teardown helper | `matches.league_views._teardown_league(league) -> None` | NEW |
| Career gate | `matches.league_views._is_career_league(league) -> bool` (`return league.mode == "league"`) | `league_views.py:3346` ✓VERIFIED |
| URL name | `league_delete` (bare, no `app_name`) | `league_urls.py` has no `app_name` |
| URL path | `path("<int:league_id>/delete/", league_views.league_delete, name="league_delete")` | NEW |
| Confirm template | `templates/leagues/league_confirm_delete.html` | templates dir = `laserforce_simulator/templates/` ✓VERIFIED |
| Success redirect | `redirect("league_list")` | `league_urls.py:161` |
| 400 on non-career | `HttpResponseBadRequest(<msg>)` | already imported `league_views.py:26` ✓VERIFIED |
| Atomic style | `with transaction.atomic():` **inline in the helper** | see decision below; `transaction` imported `league_views.py:20` |

---

## 1. URL — `matches/league_urls.py`

The file uses `from . import league_screens, league_views, views` (line 11) and
every route names its callable as `league_views.<fn>` (e.g.
`league_views.league_create`, `league_views.league_dashboard`,
`league_views.league_history`). **✓VERIFIED** — the prompt's `from . import
league_views` alias assumption is correct; routes reference `league_views.<fn>`
(NOT `views as ...`).

Add:

```python
path("<int:league_id>/delete/", league_views.league_delete, name="league_delete"),
```

**Insertion point (verified, with one correction):** the existing
`<int:league_id>/history/` entry is at `league_urls.py:31-35`; the `""`
(`league_list`) catch-all is the LAST entry at `league_urls.py:161`. **⚠CORRECTED
detail:** there are **many** specific routes *between* history and the catch-all
(the LG-01z live screens, playoffs, finances, the `coming_soon` placeholders).
Insert the `delete/` route **immediately after the `history/` entry**
(`league_urls.py:35`) for locality. Any position **before** the `""` catch-all is
correct — `<int:league_id>/delete/` is a distinct literal segment and is **not**
shadowed by the `<int:league_id>/` dashboard route (which only matches a
trailing-empty path), so first-match resolution is safe. Only the `""` catch-all
must stay last.

---

## 2. View — `matches.league_views.league_delete(request, league_id) -> HttpResponse`

Mirrors the **`teams/views.py:259 player_delete`** precedent **✓VERIFIED**: a
**single GET+POST view** — GET renders the confirm page, POST performs the
action, **NO `HttpResponseNotAllowed` 405 guard** (GET legitimately renders the
confirm page). Confirmed `player_delete` shape: `get_object_or_404` →
`if request.method == "POST": <delete> + messages.success(...) + redirect(...)`
→ else `render(<confirm template>)`.

```python
@transaction.atomic            # ⚠ SEE DECISION: decorator vs inline — recommend INLINE (drop this)
def league_delete(request, league_id):
    league = get_object_or_404(League, pk=league_id)

    # Career gate — reuse the verified helper (CAR-03 precedent). league_views.py:3346
    if not _is_career_league(league):
        return HttpResponseBadRequest(
            "Delete League is only available for career (league-mode) Leagues."
        )

    if request.method == "POST":
        league_name = league.name
        _teardown_league(league)                 # inline `with transaction.atomic():` lives HERE
        messages.success(request, f'League "{league_name}" deleted.')
        # last_league_id: do NOT pin the now-deleted id — see decision below.
        request.session.pop("last_league_id", None)
        return redirect("league_list")

    # GET — build delete_summary counts + render the confirm page.
    delete_summary = { ... }                     # see §4 for the pinned keys
    return render(
        request,
        "leagues/league_confirm_delete.html",
        {"league": league, "delete_summary": delete_summary},
    )
```

### Decisions (verified against precedent)

- **Career gate:** reuse `matches.league_views._is_career_league(league)` —
  **✓VERIFIED** exact name/signature/return: `def _is_career_league(league:
  League) -> bool: return league.mode == "league"` (`league_views.py:3346-3350`).
  On `not _is_career_league(league)` return `HttpResponseBadRequest(<message>)`
  — **✓VERIFIED** `HttpResponseBadRequest` is already imported
  (`league_views.py:22-30`, line 26), the CAR-03 precedent
  (`league_views.py:4021-4023`, `4065-4068`). Place the gate **after**
  `get_object_or_404` (so a bad id still 404s) and **before** any write.
- **GET → POST single view, no 405 guard** — mirrors `player_delete` verbatim
  (GET renders confirm; POST acts).
- **Atomic placement — RECOMMEND INLINE `with transaction.atomic():` INSIDE
  `_teardown_league`** (not the decorator on the view). Rationale: the whole
  teardown must be one atomic unit, but the view's GET branch + the
  career-gate/404 reads should not sit inside a transaction. The
  player-delete/league-views precedent decorates *write-only* views
  (`next_season` at `league_views.py:3878`, `reassign_team` at `4052` use
  `@transaction.atomic`), but `league_delete` is a **GET+POST** view, so wrapping
  the whole view would needlessly wrap the GET render. Put the atomic boundary in
  the helper. (Decorating the view also works and is harmless; inline-in-helper
  is the cleaner choice and is what this contract pins.)
- **`request.session["last_league_id"]`** — **✓VERIFIED** virtually every
  sibling league view sets it (`league_dashboard`, `league_history` at
  `league_views.py:2394`, the season views, the play views, etc.). The
  `core.context_processors.league_nav` processor defensively probes
  `League.objects.filter(pk=lid).exists()`, so a stale pin degrades gracefully —
  but a League **about to be deleted** should **NOT** be pinned. **Recommendation
  (locked):** on the POST teardown, do **not** write the deleted id; **clear it**
  with `request.session.pop("last_league_id", None)`. On the GET confirm render,
  setting it is harmless/sibling-consistent but **unnecessary** — recommend
  omitting it on GET too (nothing else needs it on a confirm page).

---

## 3. Teardown helper — `matches.league_views._teardown_league(league) -> None`

Ordered, all inside ONE `with transaction.atomic():`. **Candidate Team ids are
collected in step 2 BEFORE any delete** — critical, because
`TournamentPlayerEntry.tournament` is CASCADE (the drawn-team reverse accessor
vanishes once the Tournaments are deleted in step 4).

```python
def _teardown_league(league):
    with transaction.atomic():
        # 1 — embedded tournament ids (collect before the SeasonPhase rows cascade away)
        tournament_ids = list(
            SeasonPhase.objects.filter(
                season__league=league, tournament__isnull=False
            ).values_list("tournament_id", flat=True)
        )

        # 2 — candidate Team ids, by PK/FK only (NEVER by name). Collect NOW.
        candidate_team_ids = set()
        candidate_team_ids.update(
            Team.objects.filter(enrolled_seasons__league=league)
            .values_list("id", flat=True)              # (a) enrolled in this league's Seasons
        )
        if league.current_team_id is not None:
            candidate_team_ids.add(league.current_team_id)      # (b)
        if league.free_agent_pool_id is not None:
            candidate_team_ids.add(league.free_agent_pool_id)   # (c)
        candidate_team_ids.update(
            Team.objects.filter(
                drawn_player_entries__tournament_id__in=tournament_ids
            ).values_list("id", flat=True)             # (d) drawn teams of embedded tournaments
        )

        # 3 — delete league Matches (regular-season + embedded-tournament bracket)
        Match.objects.filter(
            Q(season__league=league)
            | Q(series_match__node__tournament_id__in=tournament_ids)
        ).distinct().delete()

        # 4 — delete embedded Tournaments (cascades participants / nodes / series / entries)
        Tournament.objects.filter(id__in=tournament_ids).delete()

        # 5 — delete the League (cascades Seasons + their dependents; SET_NULLs current/pool FKs)
        league.delete()

        # 6 — zero-reference guard, per candidate Team id (see exact queries below)
        for team_id in candidate_team_ids:
            _delete_team_if_orphaned(team_id)
```

### Step-by-step verified FK facts

**Step 1 — embedded tournament ids.**
`SeasonPhase.season` → `FK(Season, on_delete=CASCADE, related_name="phases")`
(`models.py:1838-1842`). `SeasonPhase.tournament` →
`FK("matches.Tournament", null=True, on_delete=SET_NULL,
related_name="season_phases")` (`models.py:1855-1861`). **✓VERIFIED.** Filter on
`season__league=league` (the `Season.league` FK is CASCADE, `related_name=
"seasons"`, `models.py:949-953`). Collect ids before the cascade in step 5 nulls
nothing useful (the *phase rows* are deleted by cascade; the Tournament rows are
NOT cascaded — that's why step 4 deletes them explicitly).

**Step 2 — candidate Team ids by PK/FK (verified reverse accessors).**
- (a) `Team.enrolled_seasons` — **✓VERIFIED**: `Season.teams =
  ManyToManyField("teams.Team", related_name="enrolled_seasons")`
  (`models.py:956-959`). Query: `Team.objects.filter(
  enrolled_seasons__league=league)`.
- (b) `league.current_team_id` — **✓VERIFIED**: `League.current_team =
  FK("teams.Team", null=True, on_delete=SET_NULL,
  related_name="managed_in_leagues")` (`models.py:881-887`).
- (c) `league.free_agent_pool_id` — **✓VERIFIED**: `League.free_agent_pool =
  FK("teams.Team", null=True, on_delete=SET_NULL,
  related_name="free_agent_pool_for")` (`models.py:893-899`).
- (d) `Team.drawn_player_entries` — **✓VERIFIED**: `TournamentPlayerEntry.
  drawn_team = FK("teams.Team", on_delete=SET_NULL, null=True,
  related_name="drawn_player_entries")` (`models.py:2818-2824`).
  `TournamentPlayerEntry.tournament` is CASCADE (`models.py:2805-2809`), so this
  reverse set is destroyed by step 4 — collect ids in step 2 first. Query:
  `Team.objects.filter(drawn_player_entries__tournament_id__in=tournament_ids)`.

**Step 3 — delete league Matches.**
`Match.season` → `FK("matches.Season", null=True, on_delete=SET_NULL,
related_name="matches")` (`models.py:59-65`) — so a season-scoped Match would
*survive orphaned* without an explicit delete (the whole point of DEL-01).
`SeriesMatch.match` → `FK("matches.Match", null=True, on_delete=SET_NULL,
related_name="series_match")` (`models.py:2767-2773`) — **✓VERIFIED the reverse
accessor is `series_match`** (singular, NOT `series_matches`). `SeriesMatch.node`
→ `FK("matches.BracketNode", on_delete=CASCADE,
related_name="series_matches")` (`models.py:2762-2766`); `BracketNode.tournament`
→ `FK(Tournament, on_delete=CASCADE, related_name="nodes")`
(`models.py:2655-2657`). So `series_match__node__tournament_id` is the verified
Match→SeriesMatch→BracketNode→Tournament chain. **✓VERIFIED cascade-clean
deletes:** `GameRound.match` → CASCADE (`models.py:162-168`);
`GameEvent.game_round` → CASCADE (`models.py:769-771`);
`PlayerRoundState.game_round` → CASCADE (`models.py:334-336`). Deleting the Match
rows cleanly cascades GameRounds → GameEvents/PlayerRoundStates. `.distinct()` is
required (the `series_match` to-many join can duplicate Match rows).

**Step 4 — delete embedded Tournaments.** **✓VERIFIED CASCADE children:**
`TournamentParticipant.tournament` → CASCADE (`models.py:2630-2632`);
`BracketNode.tournament` → CASCADE (`models.py:2655-2657`) → `SeriesMatch.node` →
CASCADE; `TournamentPlayerEntry.tournament` → CASCADE (`models.py:2805-2809`).
`Tournament.champion` is SET_NULL on a Team (`related_name="tournaments_won"`) —
irrelevant here. So `Tournament.objects.filter(id__in=tournament_ids).delete()`
cleanly removes participants / nodes / series rows / pool entries.

**Step 5 — `league.delete()`.** **✓VERIFIED on_deletes:**
- `Season.league` → CASCADE (`models.py:949-953`) → cascades each Season and its
  dependents:
  - `SeasonPhase.season` → CASCADE (`models.py:1838-1842`)
  - `PlayerSeasonRating.season` → CASCADE, `related_name="player_ratings"`
    (`models.py:1594-1598`)
  - `TeamSeasonFinance.season` → CASCADE, `related_name="team_finances"`
    (`models.py:1740-1744`)
  - `OwnerEvaluation.season` → CASCADE, `related_name="owner_evaluations"`
    (`models.py:1682-1684`)
  - `Season.teams` M2M join rows — auto-removed when the Season is deleted.
- `OwnerEvaluation.league` → CASCADE, `related_name="owner_evaluations"`
  (`models.py:1679-1681`) — removed directly by the League delete.
- `League.current_team` / `League.free_agent_pool` are **SET_NULL FKs declared ON
  the League** — deleting the League just drops those columns with the row; the
  referenced Teams are **not** cascaded (which is exactly why step 6 must delete
  them under the guard).

**Step 6 — zero-reference guard.** A candidate Team is deleted **only if**, after
steps 3–5, it has zero remaining references. Deleting a Team cascades its Players:
**✓VERIFIED** `Player.team = FK(Team, on_delete=CASCADE, related_name="players")`
(`teams/models.py:249`). **✓VERIFIED reverse accessors:** `Team.red_matches` /
`Team.blue_matches` from `Match.team_red`/`team_blue` (both `on_delete=CASCADE`,
`related_name="red_matches"`/`"blue_matches"`, `models.py:20-25`).

Exact existence checks (delete iff **all five** are False):

```python
def _delete_team_if_orphaned(team_id):
    team = Team.objects.filter(pk=team_id).first()
    if team is None:
        return
    still_referenced = (
        team.red_matches.exists()                 # Match.team_red  (CASCADE)
        or team.blue_matches.exists()             # Match.team_blue (CASCADE)
        or team.enrolled_seasons.exists()         # Season.teams M2M (surviving Season)
        or team.managed_in_leagues.exists()       # League.current_team (surviving League)
        or team.free_agent_pool_for.exists()      # League.free_agent_pool (surviving League)
    )
    if not still_referenced:
        team.delete()                             # cascades Team.players
```

Given the one-context invariant this guard passes for every candidate; it exists
so the single unenforced edge (a sandbox tournament that "selected existing" a
league Team, or a deliberately shared Team) can never CASCADE a foreign Match or
foreign career row — anything still referenced is **left behind** (safe over
complete, ADR-0032).

---

## 4. Confirm template — `templates/leagues/league_confirm_delete.html`

**✓VERIFIED** templates dir = `laserforce_simulator/templates/` (glob:
`templates/leagues/list.html`, `templates/leagues/dashboard.html`, …).

- Extends `base.html`, `{% block content %}`.
- **Shell — RECOMMEND the `d-flex` + `{% include "_partials/league_sidebar.html"
  %}` shell**, matching the closest sibling **action** page `new_team.html` and
  every league-context screen (`dashboard.html`, `player_detail.html`,
  `team_*`/`stats_*`/`free_agents.html`, etc. — **✓VERIFIED** all use the shell;
  `list.html` and `create.html` are the only `templates/leagues/*` that do NOT,
  and both are top-level non-league-scoped pages). **Soft caveat:** the sidebar
  links point at routes that 404 the instant the POST succeeds — purely cosmetic
  on a GET confirm page. A focused card *without* the shell is also acceptable;
  this contract pins the shell for consistency with `new_team.html`.
- **DOM ids (LOCKED):**
  - `league-delete-confirm` — root container
  - `league-delete-summary` — the counts block (renders `delete_summary`)
  - `league-delete-form` — the POST `<form method="post">` with `{% csrf_token %}`
  - `league-delete-submit` — the destructive submit button
  - `league-delete-cancel` — link back to the league dashboard
    (`{% url 'league_dashboard' league.id %}`)
- **Context keys (LOCKED):** `league`, `delete_summary`.
- **`delete_summary` — pinned dict keys (5):** `seasons`, `matches`,
  `tournaments`, `teams`, `players` — each an int count the view computes on GET
  (e.g. `seasons = league.seasons.count()`; `tournaments =
  len(tournament_ids)`; `matches` = the count of the step-3 queryset; `teams` =
  `len(candidate_team_ids)`; `players` = `Player.objects.filter(
  team_id__in=candidate_team_ids).count()`). The exact count expressions are the
  Code agent's to write; only the **5 keys + their meaning** are locked.
- `{% csrf_token %}` is mandatory inside `league-delete-form`.

---

## 5. Entry points (rendered ONLY when `league.mode == "league"`)

- **`templates/leagues/dashboard.html`** — context var for the league is
  **`league`** (**✓VERIFIED**: `{% block title %}{{ league.name }} — League`,
  `<h1>{{ league.name }}</h1>`, existing `{% url 'league_history' league.id %}`
  links). Add a link with DOM id **`league-dashboard-delete-link`** →
  `{% url 'league_delete' league.id %}`, gated `{% if league.mode == "league"
  %}`. Natural home: the header `<div>` beside the existing
  `league-dashboard-past-evaluations-link` (`dashboard.html` header block).
- **`templates/leagues/list.html`** — loop var is **`league`**, iterating
  **`active_leagues`** and **`archived_leagues`** (**✓VERIFIED**: `{% for league
  in active_leagues %}`, per-row `<a href="/leagues/{{ league.id }}/">`, state
  cell `<span class="state-badge …">`). Add a per-row control with DOM id
  **`league-list-delete-link-{{ league.id }}`** →
  `{% url 'league_delete' league.id %}`, gated `{% if league.mode == "league" %}`
  (so archived/non-career rows are unaffected). Mirror the existing per-row
  `/leagues/<id>/` link cell.

---

## 6. Tests — `matches/tests/test_league_delete.py` (Django `TestCase`)

Assert on **row counts / existence / status codes / redirect targets** — never on
simulated point totals. No mocks beyond standard `TestCase`. Reuse the
League/Season/Team/Match fixture idiom from the sibling files
`matches/tests/test_league_next_season.py` / `test_league_create.py` /
`test_league_dashboard.py`.

Pinned class/method intent:
- **Full-teardown happy path** — a populated career League with Seasons, Matches,
  GameRounds/Events/PlayerRoundStates, an embedded playoff Tournament (nodes +
  series + bracket Matches), generated Teams + Players + a free-agent pool: after
  POST, **zero** of those rows remain; `/teams/`, `/players/`, `/tournaments/`,
  and the sandbox match list are unaffected.
- **Cross-context SAFETY (the load-bearing test):** a candidate Team also
  enrolled in **another** League's Season SURVIVES; a candidate Team that played
  a **sandbox `season=NULL`** Match SURVIVES (the zero-reference guard leaves it,
  with its Players, untouched).
- **Embedded-tournament + playoff-Match teardown** — the embedded Tournament's
  bracket Matches (reachable via `series_match__node__tournament`) and the
  Tournament rows are gone; no orphan bracket in `/tournaments/`.
- **Mode gate** — a non-`league` (sandbox / multiplayer) League POST → **400**,
  nothing deleted.
- **GET renders summary** — GET → 200, `delete_summary` counts present, confirm
  DOM ids present.
- **POST redirects to `league_list`** — 302 to `reverse("league_list")`.
- **Both entry points** — render the delete link for a league-mode League
  (dashboard `league-dashboard-delete-link`; list
  `league-list-delete-link-{id}`) and are ABSENT for a non-`league` League.

---

## Corrections made against the prompt's assumptions

Everything the prompt assumed checked out; the only adjustments:

1. **URL insertion location** — the prompt said "AFTER `history/` and BEFORE the
   `""` catch-all," implying they're adjacent. They are **not**: many specific
   routes (live screens, playoffs, finances, `coming_soon`) sit between them.
   Corrected guidance: insert right after the `history/` entry
   (`league_urls.py:35`); any position before the `""` catch-all is correct
   (`delete/` cannot be shadowed by `<int:league_id>/`).
2. **Atomic style** — chose `with transaction.atomic():` **inline in
   `_teardown_league`** over the `@transaction.atomic` view decorator, because
   `league_delete` is a GET+POST view (the player-delete/`next_season`/
   `reassign_team` decorated precedents are write-only). Both work; inline is
   pinned.
3. **`last_league_id`** — recommend **clearing** it on POST teardown
   (`request.session.pop("last_league_id", None)`) and **not** writing it on GET,
   rather than pinning a soon-deleted id (the `league_nav` processor's defensive
   `exists()` probe means a stale pin is non-fatal, but clearing is cleaner).
4. **Confirm-template shell** — soft recommendation to USE the `d-flex` +
   `_partials/league_sidebar.html` shell (matching `new_team.html` and every
   league-screen page; `list.html`/`create.html` are the only non-shell
   `leagues/*` templates), with the dead-link cosmetic caveat noted.

All verified names that exactly matched the prompt (no change): view module
`matches/league_views.py`; `_is_career_league(league: League) -> bool` returning
`league.mode == "league"`; `HttpResponseBadRequest` already imported; routes
reference `league_views.<fn>`; templates dir `laserforce_simulator/templates/`;
`Season.teams` related_name `enrolled_seasons`; `TournamentPlayerEntry.drawn_team`
related_name `drawn_player_entries`; `SeriesMatch.match` related_name
`series_match`; `SeriesMatch.node` → `BracketNode`, `BracketNode.tournament`;
`Match.season` SET_NULL; `Match.team_red`/`team_blue` related_names
`red_matches`/`blue_matches` (CASCADE); `Player.team` CASCADE; `GameRound.match`,
`GameEvent.game_round`, `PlayerRoundState.game_round` all CASCADE;
`Tournament`/`TournamentParticipant`/`BracketNode`/`SeriesMatch`/
`TournamentPlayerEntry` cascade chain; `Season`→`SeasonPhase`/`PlayerSeasonRating`/
`TeamSeasonFinance`/`OwnerEvaluation` all CASCADE; `League.current_team`
(`managed_in_leagues`) / `League.free_agent_pool` (`free_agent_pool_for`) SET_NULL;
`player_delete` GET+POST-no-405 precedent (`teams/views.py:259`).

---

ADR-0032 + the CONTEXT.md Team/Player ownership invariant are already written.
