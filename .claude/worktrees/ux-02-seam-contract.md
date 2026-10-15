# UX-02 Seam Contract — User–player link ("Self-insert")

Branch: `ux-02-self-insert`. Domain terms: CONTEXT.md **Self-insert**, **Account**, **Manager**,
**Ownership root**, **Unmanaged row**. This contract is the single source of truth for the Code,
Tests and Docs agents. **Do not rename anything below.** Every name, path, URL name, DOM id, CSS
class, context key and flash string is locked.

**No simulation / RNG / BatchSimulator change. No re-baseline. No change to `PLAYER_ROW_KEYS`, to
`matches.views._player_row`, or to `accounts/permissions.py`.**

---

## 1. Model + migration

`accounts/models.py`, class `User`, appended as the **last** field after `email`:

```python
# UX-02 — the Account's Self-insert (CONTEXT.md). A vanity *link* to an
# existing Player: never moves, enrols or re-simulates it. SET_NULL so deleting
# the Player (or its Team, which cascades the Player) just leaves no Self-insert.
self_insert = models.OneToOneField(
    "teams.Player",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="self_insert_of",
)
```

- Reverse accessor: `player.self_insert_of` (raises `RelatedObjectDoesNotExist` when unlinked —
  production code never reads it; use the helpers in §2).
- **Migration:** `accounts/migrations/0002_user_self_insert.py` (the name `makemigrations accounts`
  generates for a single `AddField`). One `AddField`, no `RunPython`. Dependencies:
  `("accounts", "0001_initial")`, `("teams", "0015_team_manager")`.
- **No cycle:** `teams.0015_team_manager` depends on `swappable_dependency(AUTH_USER_MODEL)`, which
  resolves to `("accounts", "__first__")` = `0001_initial`, not `0002`. No `teams` / `matches` / `core`
  migration is generated (a reverse relation produces none). After the change,
  `python laserforce_simulator/manage.py makemigrations --check --dry-run` must report no changes.
- `accounts/admin.py` is **not** changed (the field is not added to `UserAdmin.fieldsets`).

---

## 2. Domain helpers — new module `accounts/self_insert.py`

**Why a new module, not `permissions.py`:** `accounts/CLAUDE.md` declares the `permissions.py` table
"the **entire** public surface"; the Self-insert is a domain feature *built on* that seam, not part of
it, so it gets its own module and leaves the locked seam untouched.

Public surface (exactly four functions; nothing else is importable API):

```python
def get_self_insert(user: "AbstractBaseUser | AnonymousUser | None") -> "Player | None": ...
def is_self_insert_eligible(player: Player, user: "AbstractBaseUser | AnonymousUser | None") -> bool: ...
def set_self_insert(user: "User", player: Player) -> None: ...
def clear_self_insert(user: "User") -> None: ...
```

### `get_self_insert(user)` — the drift guard (read-only)

1. `user is None` or not `getattr(user, "is_authenticated", False)` ⇒ `None`.
2. `getattr(user, "self_insert_id", None) is None` ⇒ `None` **with zero queries** (the column is
   already on the loaded `User` row — this is what keeps every existing page render's query count
   unchanged for an Account with no Self-insert).
3. Otherwise exactly **one** query:
   `Player.objects.select_related("team").filter(pk=user.self_insert_id, team__manager_id=user.pk).first()`.
   Returns that Player, or `None` when the link has **drifted** (the Player's Team is now Unmanaged,
   or managed by another Account, e.g. a Player moved to the global "Free Agents" Team).
- **Never writes.** A drifted link stays in the column; it silently reappears if ownership returns.
- Does not read or populate the `user.self_insert` related-object cache.

### `is_self_insert_eligible(player, user)` — STRICT

`True` iff `user` is authenticated **and** `player.team.manager_id is not None` **and**
`player.team.manager_id == user.pk`. An **Unmanaged** Team's Players are **not** eligible (they are
readable/writable per the Unmanaged-row rule, but not linkable until `claim_unmanaged` stamps them).
The global "Free Agents" singleton is never stamped, so its Players are never eligible.

### `set_self_insert(user, player)`

Precondition (caller-checked, not re-checked): `is_self_insert_eligible(player, user)`.
Inside one `transaction.atomic()`:

1. **Stale-link takeover:** `User.objects.filter(self_insert=player).exclude(pk=user.pk).update(self_insert=None)`
   — another Account may hold a drifted link to this Player; the strictly-owning Account wins. This
   runs *before* step 2 so the OneToOne unique constraint never fires.
2. `user.self_insert = player; user.save(update_fields=["self_insert"])`.

Linking a second Player simply **replaces** the first (step 2 overwrites). Idempotent when `player`
is already the link. No other column, row, roster, slot or enrolment is touched.

### `clear_self_insert(user)`

`user.self_insert = None; user.save(update_fields=["self_insert"])`. Clears the raw column even when
the link had drifted.

---

## 3. Views + URLs (link / unlink)

Module: `accounts/views.py` (alongside `register`). Routes appended to `accounts/urls.py` (mounted at
`/accounts/`, no `app_name`). Both are **POST-only** via `@require_POST` (GET/PUT/etc. ⇒ **405**).
Neither carries `@login_not_required` — the global `LoginRequiredMiddleware` 302s an anonymous request
to `LOGIN_URL` (the eleven exempt surfaces stay eleven).

| URL name | Path | View function |
|---|---|---|
| `self_insert_link` | `/accounts/self-insert/<int:player_id>/link/` | `accounts.views.self_insert_link(request, player_id: int) -> HttpResponse` |
| `self_insert_unlink` | `/accounts/self-insert/unlink/` | `accounts.views.self_insert_unlink(request) -> HttpResponse` |

### Redirect target (both views)

Private helper `accounts.views._safe_next(request, fallback: str) -> str`: returns `request.POST["next"]`
when present and `url_has_allowed_host_and_scheme(next, allowed_hosts={request.get_host()},
require_https=request.is_secure())`; otherwise `fallback`. Both buttons post
`next={{ request.get_full_path }}`, so the normal round-trip lands back on the page clicked from.

### `self_insert_link`

1. `player = get_owned_or_404(Player.objects.select_related("team"), request, pk=player_id)` —
   nonexistent ⇒ **404**; another Account's Team ⇒ **404** (never 403, matching the seam).
2. If `not is_self_insert_eligible(player, request.user)` (only reachable now for an **Unmanaged**
   Team): `messages.error(request, "Only a Player on a Team you manage can be your Self-insert.")`;
   redirect (302) to `_safe_next(request, reverse("player_detail", args=[player.team_id, player.id]))`.
   **No write.**
3. Else `set_self_insert(request.user, player)`;
   `messages.success(request, f'"{player.name}" is now your Self-insert.')`; redirect (302) to
   `_safe_next(request, reverse("player_detail", args=[player.team_id, player.id]))`.

### `self_insert_unlink`

1. If `request.user.self_insert_id is None`: `messages.info(request, "You have no Self-insert to unlink.")`
   (no write).
2. Else `clear_self_insert(request.user)`; `messages.success(request, "Self-insert unlinked.")`.
3. Redirect (302) to `_safe_next(request, reverse("landing"))`.

---

## 4. Context processor

`accounts/context_processors.py` (new file), function `self_insert(request: HttpRequest) -> dict[str, Any]`,
registered **last** in `TEMPLATES[0]["OPTIONS"]["context_processors"]` as
`"accounts.context_processors.self_insert"` (after `core.context_processors.watch_list`).

Returns exactly two keys:

| Key | Type | Value |
|---|---|---|
| `self_insert_player` | `Player \| None` | `get_self_insert(getattr(request, "user", None))` |
| `self_insert_player_id` | `int \| None` | `self_insert_player.id` or `None` |

- Anonymous / no `user` attribute (a bare `RequestFactory()` request) ⇒ both `None`, no exception,
  no query.
- Wrap the lookup in `try/except DatabaseError` ⇒ both `None` (mirrors `core.context_processors`'
  broken-transaction defence).

---

## 5. Template tag — the "You" badge

New package `accounts/templatetags/` (`__init__.py` + `self_insert_tags.py`). Library name:
**`self_insert_tags`** → templates use `{% load self_insert_tags %}`. Not registered as a builtin:
**every template (and every included partial) that uses the tag loads it explicitly** — `{% load %}`
is not inherited from `base.html`.

```python
YOU_BADGE_HTML = '<span class="badge text-bg-primary ms-1 you-badge" title="Your Self-insert">You</span>'

@register.simple_tag(takes_context=True)
def you_badge(context: Context, player_id: object) -> str: ...
```

- Returns `mark_safe(YOU_BADGE_HTML)` iff `context.get("self_insert_player_id")` is not `None` and
  `int(player_id) == int(that value)`.
- Returns `""` when: the context key is missing (e.g. `render_to_string` without a request) or
  `None`; `player_id` is `None` / `""` / not int-coercible (`TypeError` / `ValueError` swallowed);
  the ids differ. Never raises.
- `YOU_BADGE_HTML` is a module constant and the **exact** rendered string (tests may assert it
  verbatim). The CSS hook is the class `you-badge`; no project stylesheet is added (Bootstrap 5.3
  `text-bg-primary` supplies the styling).
- Usage: `{{ player.name }}{% you_badge player.id %}` — the tag goes **immediately after** the name
  expression, inside the same element (inside the `<a>` is NOT required; place it after the closing
  `</a>` when the name is a link, so the badge is not part of the link text).

---

## 6. Buttons on both player detail pages

Same DOM ids on both pages (each page renders at most one of the two forms). Both forms are
`method="post"`, carry `{% csrf_token %}` and `<input type="hidden" name="next" value="{{ request.get_full_path }}">`.

| Condition (evaluated in template) | Renders |
|---|---|
| `self_insert_player_id == player.id` | `<form id="self-insert-unlink-form" action="{% url 'self_insert_unlink' %}">` with `<button type="submit" id="self-insert-unlink-button" class="btn btn-sm btn-outline-secondary">Unlink</button>` |
| else if `<team>.manager_id` and `<team>.manager_id == user.id` | `<form id="self-insert-link-form" action="{% url 'self_insert_link' player.id %}">` with `<button type="submit" id="self-insert-link-button" class="btn btn-sm btn-outline-primary">This is me</button>` |
| else (Unmanaged or — unreachable after 404 — foreign Team) | nothing |

- **Sandbox** `templates/teams/player_detail.html` (view `teams.views.player_detail`, URL
  `player_detail`, `/teams/<team_id>/player/<player_id>/`): `<team>` is `team`. Place the form in the
  identity card directly under `<p class="text-muted mb-3">{{ team.name }}</p>`. **No view change.**
- **League** `templates/leagues/player_detail.html` (view
  `matches.league_screens.player_detail.player_detail`, URL `league_player_detail`,
  `/leagues/<league_id>/players/<player_id>/`): `<team>` is `player.team`. Place the form inside
  `#league-player-header`, after the "Career stats (global)" link. **No view change** (frozen LG-06h
  context keys untouched; `player.team` lazy-loads one row).

---

## 7. Nav — "My player"

`templates/_partials/topnav_auth.html`, authenticated branch, a new `<li>` immediately **after** the
`account-signed-in-as` item:

```django
{% if self_insert_player %}
<li><a class="dropdown-item" id="account-my-player-link" href="{% url 'player_detail' self_insert_player.team_id self_insert_player.id %}">My player</a></li>
{% endif %}
```

**Link rule: always the sandbox `player_detail` page.** Every Player has a Team, `get_self_insert`
guarantees that Team is strictly owned (so the link can never 404), and that page hosts the Unlink
button. The League page needs a `league_id` that a Player does not carry. Hidden (no `<li>` at all)
whenever `self_insert_player` is `None` — unlinked, drifted, or anonymous.

---

## 8. Badge coverage table (Code agent executes; Tests agent spot-checks)

Scope rule: every **server-rendered visible** Player-name site gets `{% you_badge <id> %}`. Each
template listed adds `{% load self_insert_tags %}` (after its `{% extends %}` line, or at the top of a
partial). Line numbers are as of `f816957`.

**Excluded by rule (do NOT badge):** `{% block title %}` contents (no HTML in `<title>`);
`<option>` text (no markup allowed in options); the avatar initial `{{ player.name|first|upper }}`;
`|escapejs` / `json_script` / any JS-built view (`game_round_events.html` playback, `movement_heatmap.html`
roster JSON, `play_week_live.html`, charts); free-text descriptions (`missile_log.html` `row.description`);
form widgets (`team_slots_form.html`); the PLAY-01 live-dashboard fragments re-rendered by
`matches.league_views._render_live_play_panels` via `render_to_string` with no request — the tag
renders `""` there, so the badge disappears from a JS-patched leaders card until reload (accepted).
Team / League / Season / map / conference names are not Player names.

| # | Template | Render site(s) (line: expression) | Player-id expression | View change? |
|---|---|---|---|---|
| 1 | `teams/player_detail.html` | 16: `<h3>{{ player.name }}</h3>` (exclude 3 title, 22 initial) | `player.id` | No |
| 2 | `teams/team_detail.html` | 50: active roster `{{ player.name }}`; 80: bench `{{ player.name }}` | `player.id` | No |
| 3 | `teams/player_list.html` (`/players/`) | 58: `{{ player.name }}` | `player.id` | No |
| 4 | `teams/player_career_stats.html` | 10: `<h1>{{ player.name }}` (exclude 5 title) | `player.id` | No |
| 5 | `teams/player_delete.html` | 9: `remove {{ player.name }} from` | `player.id` | No |
| 6 | `leagues/player_detail.html` | 24: `<h1>{{ player.name }}</h1>`; 197: empty-state alert `{{ player.name }} has no recorded Rounds` (exclude 15 title) | `player.id` | No |
| 7 | `leagues/free_agents.html` | 68: `{{ player.name }}` | `player.id` | No |
| 8 | `leagues/player_ratings.html` | 83: `{{ player.name }}` | `player.id` | No |
| 9 | `leagues/player_stats.html` | 110: `{{ row.player_name }}` | `row.player_id` | No |
| 10 | `leagues/league_leaders.html` | 57, 96, 135, 174: `{{ row.player_name }}` (four boards) | `row.player_id` | No |
| 11 | `leagues/statistical_feats.html` | 119: `{{ row.player_name }}` | `row.player_id` | No |
| 12 | `leagues/team_history.html` | 163: `{{ p.name }}` | `p.player_id` | No |
| 13 | `leagues/team_roster.html` | 57: starting `{{ player.name }}`; 95: bench `{{ player.name }}` | `player.id` | No |
| 14 | `leagues/watch_list.html` | 107: `{{ row.player_name }}` | `row.player_id` | No |
| 15 | `leagues/history.html` | 48: `in_progress_row.season_mvp.player_name`; 49: `in_progress_row.finals_mvp.player_name`; 65: `row.season_mvp.player_name`; 66: `row.finals_mvp.player_name` — badge inside each `{% if %}` branch | `in_progress_row.season_mvp.player_id` / `in_progress_row.finals_mvp.player_id` / `row.season_mvp.player_id` / `row.finals_mvp.player_id` (`AwardWinner.player_id`) | No |
| 16 | `leagues/team_finances.html` | 207: `{{ r.name }}` (roster salaries); 236: `{{ entry.name }}` (availability) | `r.player_id`; `entry.player_id` | **Yes** — `matches/league_screens/team_finances.py::team_finances`: add `"player_id": p.id` to the `roster_salaries` dict and to the `availability` dict comprehensions (keys `name`, `salary` / `games_remaining` unchanged) |
| 17 | `_partials/dashboard_leaders_snippet.html` (included by `leagues/dashboard.html` and `seasons/dashboard.html`) | 10: `{{ row.player_name }}` | `row.player_id` (`LeaderRow.player_id`) | No |
| 18 | `seasons/awards.html` | 43, 56, 69, 82, 95, 108, 121: `{{ w.player_name }}` (seven award rows, incl. the `kd_rows` loop) | `w.player_id` (`AwardWinner.player_id`) | No |
| 19 | `matches/game_round_detail.html` (Round box score) | 95, 187: scoreboard `{{ perf.name }}` (red, blue); 238, 255: resources list `<strong>{{ perf.name }}:</strong>` (red, blue) — badge after the name, before the colon | `perf.player_id` | **Yes** — `matches/views.py::game_round_detail`: build `red_players = [{**_player_row(s), "player_id": s.player_id} for s in red_states]` (same for blue). This is a view-local enrichment: `_player_row`, `PLAYER_ROW_KEYS` (still 28) and the `export_round_report` PDF are **unchanged** |
| 20 | `matches/compare_rounds.html` | 80: `{{ row.name }}` (deltas table) | `row.player_id` | No |
| 21 | `matches/head_to_head.html` | 129: `{{ top_impactful.team_a.name }}`; 144: `{{ top_impactful.team_b.name }}` | `top_impactful.team_a.player_id`; `top_impactful.team_b.player_id` | No |
| 22 | `matches/player_head_to_head.html` | 77: `<h2>{{ player_a.name }} vs {{ player_b.name }}</h2>` (exclude 24, 33 options) | `player_a.id`; `player_b.id` | No |
| 23 | `matches/team_history.html` (sandbox `team_match_history`) | 127: `{{ row.player.name }}` | `row.player.id` | No |
| 24 | `matches/tournament_detail.html` | 136: pool table `{{ entry.player.name }}`; 181: drawn-team list `{{ entry.player.name }}` (exclude 60 option) | `entry.player_id` | No |

Templates surveyed with **no** Player-name site: `enhanced_match_detail.html` (match detail — no
server-rendered player names), `match_detail.html`, `match_list.html`, `game_log.html`,
`playoffs.html`, `power_rankings.html`, `team_stats.html`, `team_schedule.html`, `schedule.html`,
`standings.html`, `owner_evaluation.html`, `manage_conferences.html`, `new_team.html`,
`league_finances.html`, `tournament_list.html`, `_tournament_round.html`, `batch_simulate.html`,
setup pages, `generate_players*.html`, `roster_import*.html`, `role_benchmarks.html`, `landing.html`,
`maps/*`, `accounts/*`.

---

## 9. Test boundary

Tests assert **only** against these public names: the `User.self_insert` field (+ `self_insert_of`
reverse, `SET_NULL` on Player/Team delete, OneToOne uniqueness); the four functions in
`accounts/self_insert.py`; URL names `self_insert_link` / `self_insert_unlink`, their status codes,
redirects and the four flash strings (§3); the context keys `self_insert_player` /
`self_insert_player_id`; the `you_badge` tag output (`YOU_BADGE_HTML` exactly, or `""`); DOM ids
`account-my-player-link`, `self-insert-link-form`, `self-insert-link-button`,
`self-insert-unlink-form`, `self-insert-unlink-button`; and presence of the substring `you-badge` on
pages. **Internal (do not assert):** `_safe_next`, query shapes beyond "zero queries when unlinked",
template line placement, Bootstrap classes other than `you-badge`.

**Fixture warning:** most existing fixtures create **Unmanaged** Teams. A test that expects a badge
or a successful link must first set `team.manager = get_shared_manager()` (root `conftest.py`;
the autouse fixture already logs that Account in) — otherwise `get_self_insert` returns `None` by design.

| File (new) | Covers |
|---|---|
| `accounts/tests/test_self_insert.py` | Field + migration effect (link, SET_NULL on Player delete and on Team delete, OneToOne `IntegrityError` on a raw double-link); `get_self_insert`: anonymous / `None` ⇒ None, unlinked ⇒ None with `assertNumQueries(0)`, owned ⇒ Player, drift (Team set Unmanaged, Team set to other Account) ⇒ None and column unchanged; `is_self_insert_eligible` (owned / Unmanaged / foreign); `set_self_insert` replace + stale other-Account link cleared; `clear_self_insert`. Views: GET ⇒ 405 on both; anonymous POST ⇒ 302 to login; link owned ⇒ 302 + success message + column set; link Unmanaged ⇒ 302 + error message + no write; link other-Account Player ⇒ 404; nonexistent ⇒ 404; second link replaces first; `next` honoured, off-host `next` ignored (fallback used); unlink ⇒ clears + success; unlink when none ⇒ info message. Context processor: `RequestFactory()` request with no `user` ⇒ both None; anonymous ⇒ None; linked ⇒ id + Player. Template tag: match ⇒ exact `YOU_BADGE_HTML`; mismatch / missing context key / `None` / `"abc"` ⇒ `""`; str id `"5"` matches int 5. Nav: `account-my-player-link` present with `player_detail` href when linked, absent when unlinked and when drifted. |
| `teams/tests/test_self_insert_badges.py` | Sandbox pages: `player_detail` (badge present; Link button when owned and not linked; Unlink button when linked; neither when Unmanaged), `team_detail` roster (badge exactly once, only on the linked row), `player_list` `/players/`, `player_career_stats`. Negative: an unlinked Account ⇒ no `you-badge` on any of them. |
| `matches/tests/test_self_insert_badges.py` | `league_player_detail` (badge + both button states), League `team_roster`, `stats_league_leaders`, `game_round_detail` box score (badge present; `PLAYER_ROW_KEYS` still 28), `season_awards`, `team_finances` (view now supplies `player_id`), `compare_rounds`. |

Representative-page minimum (≥ 8): sandbox player detail, team detail roster, `/players/` list,
League team roster, League leaders, Round detail box score, Season awards, League player page.

Full-suite gate: `pytest -n auto` from the nested project dir, exact pass/fail counts reported.

---

## 10. File ownership (three parallel agents)

**Code agent** — production `.py`, templates, migration, settings:
- `accounts/models.py`, `accounts/migrations/0002_user_self_insert.py` (generate via `makemigrations accounts`, then verify deps)
- `accounts/self_insert.py` (new), `accounts/views.py`, `accounts/urls.py`
- `accounts/context_processors.py` (new), `accounts/templatetags/__init__.py` + `self_insert_tags.py` (new)
- `laserforce_simulator/settings.py` (one line in `context_processors`)
- `matches/views.py` (`game_round_detail` only), `matches/league_screens/team_finances.py` (`team_finances` only)
- Templates: `_partials/topnav_auth.html`, the two player detail pages (§6), and every template in §8.
- Run `python -m black laserforce_simulator` and `makemigrations --check --dry-run`.

**Tests agent** — test files only: the three new files in §9. May not edit production code,
templates, `conftest.py` or existing tests.

**Docs agent** — `laserforce_simulator/accounts/CLAUDE.md` (new "Self-insert (UX-02)" section:
field, `accounts/self_insert.py` surface, the two URLs, context processor, tag, Migrations list gains
`0002_user_self_insert`, Tests list gains `test_self_insert.py`; the "No extra profile fields" line is
amended), `laserforce_simulator/teams/CLAUDE.md` (Player `self_insert_of` reverse accessor; badges on
sandbox pages), `laserforce_simulator/matches/CLAUDE.md` (badges on league/round pages; the
`game_round_detail` `player_id` enrichment is NOT a scoreboard key; `team_finances` row dicts gain
`player_id`), root `CLAUDE.md` (no new commands — touch only if an index line needs it), `PLAN.md`
(mark UX-02 done), `README.md` (one feature line), `IMPLEMENTED_FEATURES.md` if present. **CONTEXT.md
is not touched** (the Self-insert entry is already written).
