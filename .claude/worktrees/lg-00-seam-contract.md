# LG-00 Seam Contract — Player Generation Tools

**Status:** LOCKED. Three agents (Code / Tests / Docs) work against this in parallel.
Names below are frozen — do not rename, do not add fields. If reality contradicts
a name here, STOP and flag; do not silently drift.

Branch: `lg-00-player-generation` (already checked out).
All paths are relative to the repo's nested Django project root:
`laserforce_simulator/laserforce_simulator/` (where `manage.py` lives).

---

## 1. New public names (frozen)

| Kind | Name | Location |
|------|------|----------|
| Module | `teams/player_generator.py` (new, pure) | `teams/player_generator.py` |
| Pure tuple | `_STAT_FIELDS: tuple[str, ...]` (19 entries — see §2c) | `teams/player_generator.py` |
| Pure function | `draw_stats(rng, mean, std_dev) -> dict[str, int]` | `teams/player_generator.py` |
| Pure function | `draw_preferred_roles(rng) -> list[str]` | `teams/player_generator.py` |
| Pure function | `assign_slots(preferred_roles_per_player) -> dict[str, int | None]` | `teams/player_generator.py` |
| Form | `GenerateLeagueForm` | `teams/forms.py` |
| View | `generate_players(request)` | `teams/views.py` |
| URL name | `generate_players` | `teams/urls.py` |
| Constants tuple | `TEAM_NAMES: tuple[str, ...]` (new, ~30–50 themed names) | `teams/constants.py` |
| Manager class | `TeamManager(models.Manager)` w/ `regular()` | `teams/models.py` |
| Helper function | `get_free_agents_team() -> Team` | `teams/models.py` (module-level, near `Team`) |
| Template — form | `templates/teams/generate_players.html` (new) | `templates/teams/generate_players.html` |
| Template — confirm | `templates/teams/generate_players_done.html` (new) | `templates/teams/generate_players_done.html` |
| Template link | "Generate Players" anchor → `{% url 'generate_players' %}` | `templates/teams/team_list.html` |

No model field change, no migration, no ADR, no new dependency.
`TeamManager` is a manager-class swap on `Team.objects` — Django picks this up at
class load with no migration (managers are not schema).

The `Free Agents` Team has no `is_system` flag — it is identified by the **magic
name** `"Free Agents"` only. Auto-created on first generate run via
`get_free_agents_team()`.

---

## 2. The pure module seam (MOST IMPORTANT — view ↔ pure-Python boundary)

`teams/player_generator.py` is **pure Python**. The Tests agent will assert this
explicitly (see §7).

### 2a. Import allowlist (frozen)

The module may import **only** from:

- `random` (`Random` type-hint only — RNG is **injected** by the caller; the
  module itself MUST NOT seed a global `random.Random()`, MUST NOT call
  `random.seed`, MUST NOT call any module-level `random.*` function).
- `typing` (`Iterable`, `Mapping`, `Sequence`, etc. — for annotations only).
- `collections` (e.g. `defaultdict`) if needed by the algorithm.

The module must **NOT** import:

- `django.*` (no models, no ORM, no settings, no template engine).
- `teams.constants` (the view consumes `TEAM_NAMES` directly — see §6).
- `matches.sim_helpers.*` (role-name strings are hand-rolled locally as a 5-tuple
  to keep this module Django-free; do NOT import `role_constants`).
- any I/O module (no file I/O, no network).
- any simulator entry point.

This is the same "pure" discipline as RES-04's `cell_occupancy.py`, RV-03's
`pdf_report.py`, and HX-01's `career_stats.py`. The Tests agent pins it with a
defensive subprocess import check (see §7.1).

### 2b. Canonical role-name tuple (frozen, module-local)

Hand-rolled inside `player_generator.py`:

```python
_ROLE_NAMES: tuple[str, ...] = ("commander", "heavy", "scout", "medic", "ammo")
_SLOT_KEYS:  tuple[str, ...] = ("commander", "heavy", "scout_1", "scout_2", "medic", "ammo")
```

`_ROLE_NAMES` matches the lowercase role strings used by `PlayerRoundState.role`
and `Player.preferred_roles`. `_SLOT_KEYS` matches the `Team.slot_*` FK names —
note Scout has TWO slots, both bound to the `"scout"` role.

### 2c. `_STAT_FIELDS` tuple (frozen, exact order, 19 entries)

VERIFIED against `teams/models.py` (the 19 `IntegerField`s on `Player` with
`_STAT_VALIDATORS`, lines ~192–210). The order below is the canonical order the
contract pins — 3 awareness, 1 decision, 5 physical, 2 team, 8 role.

```python
_STAT_FIELDS: tuple[str, ...] = (
    # 3 awareness
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    # 1 decision
    "decision_making",
    # 5 physical
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    # 2 team
    "communication",
    "teamwork",
    # 8 role
    "Offensive_synergy",     # NOTE: capital O — matches the field name in models.py exactly
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)
```

VERIFIED: `Offensive_synergy` is intentionally capital-O — the existing field
name in `teams/models.py` line 203 uses that casing. Do NOT silently rename to
`offensive_synergy` here or in the view; the view will `Player(**stats)` so the
keys must match the field names byte-for-byte.

### 2d. `draw_stats(rng, mean, std_dev) -> dict[str, int]`

```python
def draw_stats(rng: random.Random, mean: float, std_dev: float) -> dict[str, int]:
    """Return one stat dict for a generated Player.

    PURE: receives the RNG as an argument; never reads global random state.
    Returns a dict keyed by every name in _STAT_FIELDS (19 keys).
    Each value is `max(0, min(100, round(rng.gauss(mean, std_dev))))`.
    """
```

- Returns a `dict[str, int]` with **exactly 19 keys**, one per `_STAT_FIELDS`
  entry. Key order in the returned dict mirrors `_STAT_FIELDS` (insertion-ordered
  dict, the Tests agent may assert on `list(d.keys())`).
- Each value is an `int` in the closed range `[0, 100]`.
- Formula per stat: `max(0, min(100, round(rng.gauss(mean, std_dev))))`.
- Each stat is an **independent** draw from `rng.gauss(mean, std_dev)` —
  draws are made in `_STAT_FIELDS` order to keep RNG consumption deterministic
  given a seeded `random.Random`.
- `mean` and `std_dev` are passed through verbatim. The pure module does NOT
  range-check them — that is the form's job.

### 2e. `draw_preferred_roles(rng) -> list[str]`

```python
def draw_preferred_roles(rng: random.Random) -> list[str]:
    """Return a list of 1–3 unique role names drawn from _ROLE_NAMES.

    Count distribution: 70% / 20% / 10% for length 1 / 2 / 3.
    Roles within a single draw are uniform without replacement.
    """
```

- Implementation pin (so the test agent can assert RNG-consumption parity):
  1. Choose the count `n` via `rng.choices([1, 2, 3], weights=[70, 20, 10], k=1)[0]`.
  2. Choose `n` roles via `rng.sample(_ROLE_NAMES, n)`.
- Returns `list[str]` of length 1, 2, or 3.
- All values are members of `_ROLE_NAMES`.
- No duplicates within a single draw.
- Same seeded `random.Random` ⇒ identical output across runs.

### 2f. `assign_slots(preferred_roles_per_player) -> dict[str, int | None]`

```python
def assign_slots(
    preferred_roles_per_player: Sequence[Sequence[str]],
) -> dict[str, int | None]:
    """Greedy bipartite match of 6 players → 6 slot keys.

    Input: a length-6 sequence where each element is a player's preferred_roles
    list (a sequence of role names from _ROLE_NAMES). The view trims to
    players[:6] BEFORE calling.

    Output: a dict keyed by _SLOT_KEYS (length 6), value is the player INDEX
    (0–5) assigned to that slot, or None if no preferring player was available
    when the slot was processed. The view back-fills None entries with leftover
    players (by ascending player index) in a subsequent step.
    """
```

- **Input length is exactly 6.** The view trims (`players[:6]`) before calling.
  The pure function does NOT have to defensively handle other lengths — but
  must not crash if given a short list (return `None` for the unreachable
  slots and stop). The contract: **callers pass length 6**; behaviour on other
  lengths is undefined except "do not raise".
- **Output shape: `dict[str, int | None]`.** Keys are `_SLOT_KEYS` in that
  exact order (insertion-ordered dict). Values are player indices in
  `[0, 5]`, or `None`.
- **Algorithm — greedy bipartite, canonical-slot-first:**
  1. Iterate `_SLOT_KEYS` in order: `commander`, `heavy`, `scout_1`,
     `scout_2`, `medic`, `ammo`.
  2. For each slot, the slot's **role** is the slot key with any trailing
     `"_1"`/`"_2"` stripped (so `scout_1` and `scout_2` both want `"scout"`).
  3. Pick the lowest-index **unassigned** player whose `preferred_roles`
     contains that role.
  4. If no such player exists, the slot's value is `None` and the loop
     continues to the next slot. (The view back-fills `None`-valued slots
     later with leftover players.)
- **Deterministic tie-break:** when two unassigned players both prefer the
  current slot's role, the lower player-index wins. Pinned by Tests case
  `test_assign_slots_deterministic_tiebreak`.
- **Pure:** no `random` calls inside `assign_slots`. The function is
  deterministic given its input — the RNG enters at the level above, when
  the view shuffles player order before assembling
  `preferred_roles_per_player`.

---

## 3. View contract (`generate_players` in `teams/views.py`)

```python
@transaction.atomic
def generate_players(request):
    """LG-00 player/team generation surface.

    GET  -> render the form.
    POST -> validate the form; on success, resolve random_* markers, generate
            players (and Teams if num_teams >= 1) inside a single transaction,
            redirect to the confirmation page. On invalid form, re-render
            with errors (status 200).
    """
```

Behavioural pins:

- `request.method == "GET"` → render `templates/teams/generate_players.html`
  with `{"form": GenerateLeagueForm()}`. Status 200.
- `request.method == "POST"` → instantiate `GenerateLeagueForm(request.POST)`.
  If `form.is_valid()` is False, re-render the form template with the bound
  form (status 200, errors displayed).
- On a valid POST:
  1. Resolve `random_*` and `"Random (...)"` markers:
     - `num_teams == "random_2_10"` → `random.randint(2, 10)`.
     - `players_per_team == "random_team"` → `random.randint(6, 8)`.
     - `players_per_team == "random_pool"` → `random.randint(12, 100)`.
     - Otherwise both are parsed as `int(...)`.
  2. Build a local `rng = random.Random()` (no explicit seed — production
     uses fresh entropy). Pass this `rng` into every `draw_stats` and
     `draw_preferred_roles` call. (The pure module does NOT touch
     `random.randint` — that step is the view's, for resolving form markers.)
  3. **Pre-build name pools:**
     - `team_names_pool = list(TEAM_NAMES); random.shuffle(team_names_pool)`.
     - `player_names_pool = list(PLAYER_NAMES); random.shuffle(player_names_pool)`.
     Pop from these pools as Teams/Players are created. On exhaustion, fall
     back to `f"{TEAM_NAMES[-1]} #{n}"` / `f"{PLAYER_NAMES[-1]} #{n}"`.
  4. **Team-name collision check:** after popping a candidate team name,
     check `Team.objects.filter(name=candidate).exists()`; on collision,
     append `f" #{k}"` with `k` incrementing from 2 until a free name is
     found.
  5. **Player-name collision check (Free Agents Team only):** for the
     `num_teams == 0` branch, after popping a candidate player name, check
     `Player.objects.filter(team=free_agents_team, name=candidate).exists()`;
     on collision, append `f" #{k}"` with `k` incrementing from 2 until
     clear. For regular teams, no per-team name uniqueness check is
     performed — Player names are not unique-constrained per Team in the
     current schema.
  6. **`num_teams >= 1` branch:**
     - For each Team to create:
       - Pop a name + collision-check; `team = Team.objects.create(name=...)`.
       - Pop `players_per_team` Player profiles. For each player, call
         `_random_player_profile()` (existing helper in `teams/models.py`),
         then OVERRIDE `profile["name"]` with the popped+deduped name from
         the Player name pool. Build stats with `draw_stats(rng, mean, std_dev)`
         and preferred_roles with `draw_preferred_roles(rng)`. Create the
         Player via `Player.objects.create(team=team, name=..., **profile_minus_name,
         **stats, preferred_roles=...)`.
       - **Slot fill:** take the first 6 created players' `preferred_roles`
         as `preferred_roles_per_player`; call `assign_slots(...)`. For each
         `(slot_key, player_index)` entry, set the matching FK on the Team
         (e.g. `slot_key == "scout_1"` → `team.slot_scout_1 = players[player_index]`).
         For `None`-valued slot_keys, **back-fill** with leftover players
         (those whose index was never assigned), in ascending player-index
         order. After back-fill, all 6 slots are filled.
       - Players 7+ (`players_per_team > 6`) are bench: created on the Team
         but never assigned to a `slot_*` FK.
       - `team.save()` once after all slot FKs are set.
  7. **`num_teams == 0` branch:**
     - `free_agents = get_free_agents_team()`.
     - Pop `players_per_team` Player profiles (with per-team name
       collision-check against `free_agents`). For each, `Player.objects.create(
       team=free_agents, ...)` with stats and preferred_roles as above.
     - Do NOT touch any `slot_*` FK on the Free Agents Team — it stays an
       unfilled roster on purpose.
  8. Redirect (302) to the confirmation page, passing the created team
     IDs and free-agent count via the session OR by re-rendering directly
     instead of redirecting. **Pinned: re-render `generate_players_done.html`
     directly** (no redirect, no session round-trip — the confirmation
     page is the POST response, status 200). Context:
     ```python
     {
         "created_teams": list[Team],      # may be empty if num_teams == 0
         "free_agent_count": int,          # 0 if num_teams >= 1
     }
     ```
- The entire generation runs inside `@transaction.atomic` (decorator on
  the view). If any ORM write raises, all writes roll back.

Imports needed in `teams/views.py`:

```python
import random
from django.db import transaction
from django.shortcuts import render, get_object_or_404
from teams.constants import TEAM_NAMES, PLAYER_NAMES
from teams.forms import GenerateLeagueForm
from teams.models import Team, Player, get_free_agents_team, _random_player_profile
from teams.player_generator import draw_stats, draw_preferred_roles, assign_slots
```

`_random_player_profile` is the existing private helper at `teams/models.py:11`.
The view calls it for `age` / `started_playing_age` / `total_games` / `home_site`
and discards the `name` it returns. Its signature is **NOT** refactored for
LG-00.

---

## 4. Form contract (`GenerateLeagueForm` in `teams/forms.py`)

New form (or new module `teams/forms.py` if one does not exist; check before
creating).

```python
class GenerateLeagueForm(forms.Form):
    NUM_TEAMS_CHOICES = (
        ("0",  "0 (free-agent pool)"),
        ("2",  "2"), ("3", "3"), ("4", "4"), ("5", "5"), ("6", "6"),
        ("7",  "7"), ("8", "8"), ("9", "9"), ("10", "10"), ("11", "11"),
        ("12", "12"), ("13", "13"), ("14", "14"), ("15", "15"), ("16", "16"),
        ("17", "17"), ("18", "18"), ("19", "19"), ("20", "20"),
        ("random_2_10", "Random (2–10)"),
    )
    PLAYERS_PER_TEAM_CHOICES = (
        # team mode (num_teams >= 1)
        ("6", "6"), ("7", "7"), ("8", "8"), ("9", "9"),
        ("random_team", "Random (6–8)"),
        # pool mode (num_teams == 0) — 12..100 inclusive
        *(str(n) for n in range(12, 101)),  # rendered as (n, n) pairs in real code
        ("random_pool", "Random (12–100)"),
    )

    num_teams = forms.CharField(
        widget=forms.Select(choices=NUM_TEAMS_CHOICES),
    )
    players_per_team = forms.CharField(
        widget=forms.Select(choices=PLAYERS_PER_TEAM_CHOICES),
    )
    mean = forms.IntegerField(
        min_value=0, max_value=100, initial=50,
    )
    std_dev = forms.IntegerField(
        min_value=1, max_value=40, initial=15,
    )

    def clean(self):
        cleaned = super().clean()
        nt = cleaned.get("num_teams")
        ppt = cleaned.get("players_per_team")
        if nt == "0":
            if ppt not in {"random_pool"} and not (ppt and ppt.isdigit() and 12 <= int(ppt) <= 100):
                raise forms.ValidationError(
                    "Players per team must be 12–100 when generating a free-agent pool"
                )
        else:
            if ppt not in {"6", "7", "8", "9", "random_team"}:
                raise forms.ValidationError(
                    "Players per team must be 6–9 when generating teams"
                )
        return cleaned
```

Pins:

- The two dropdowns are **`CharField` + `Select` widget** with string choices.
  No JS-driven dependent toggle for v1 — the wide superset of choices is shown
  always, and `clean()` enforces the cross-field rule.
- `num_teams` choices: exactly the 22 entries above — `"0"`, `"2"`..`"20"`,
  `"random_2_10"`. **Do NOT include `"1"`** (the spec lists 0, 2..20, random).
- `players_per_team` choices: the union of `"6"`..`"9"`, `"12"`..`"100"`,
  `"random_team"`, `"random_pool"` — 98 choices total. Pinned with the exact
  string values; the view parses them with `int(...)` or matches the
  `random_*` markers.
- `mean`: `IntegerField`, `min_value=0`, `max_value=100`, `initial=50`.
- `std_dev`: `IntegerField`, `min_value=1`, `max_value=40`, `initial=15`.
- `clean()` raises `forms.ValidationError` for the two cross-field violations
  with the **exact wording** above (Tests will substring-match).

---

## 5. URL contract (`teams/urls.py`)

Add the new entry **BEFORE** any catch-all / `re_path` in `teams/urls.py`:

```python
path("generate/", views.generate_players, name="generate_players"),
```

Full URL (mounted at `/teams/`): `/teams/generate/`.

Tests reverse via `reverse("generate_players")` — no `app_name:` prefix
(consistent with HX-01's pattern; existing `teams/urls.py` does not use
`app_name`).

---

## 6. Constants contract (`teams/constants.py`)

Append a new top-level tuple `TEAM_NAMES` of 30–50 themed laser-tag team names.
Same shape and casing as the existing `PLAYER_NAMES` constant — a module-level
`tuple[str, ...]` of plain strings.

```python
TEAM_NAMES: tuple[str, ...] = (
    "Red Phoenix",
    "Blue Vipers",
    "Neutron Storm",
    # ... 30-50 entries total ...
)
```

The exact name list is the Code agent's choice (themed laser-tag flavour). The
contract pins:

- Type: `tuple[str, ...]` at module scope.
- Length: between 30 and 50 inclusive.
- Each entry is a non-empty `str` with no surrounding whitespace.
- All entries are unique (`len(set(TEAM_NAMES)) == len(TEAM_NAMES)`).

`TEAM_NAMES` is consumed **only by the view** (`teams/views.py`). The pure
module (`teams/player_generator.py`) does NOT import it (see §2a).

---

## 7. Manager + helper contract (`teams/models.py`)

### 7a. New `TeamManager`

```python
class TeamManager(models.Manager):
    def regular(self):
        """All Teams except the reserved Free Agents Team."""
        return self.exclude(name="Free Agents")


class Team(models.Model):
    # ... existing fields ...
    objects = TeamManager()
    # ... existing methods ...
```

- `TeamManager` subclasses `models.Manager`.
- Single public method `regular()` returning a queryset that excludes the
  Team whose `name == "Free Agents"`.
- `Team.objects = TeamManager()` assigned on the class.
- `Team.objects.all()` is **unchanged** — it continues to include the Free
  Agents Team. Tests pin this distinction.
- No migration: managers are not schema. Django picks this up at class load.

### 7b. New `get_free_agents_team()` helper

Module-level function, defined adjacent to the `Team` class (NOT a `Team`
classmethod — keeps the call-site grep `get_free_agents_team()` short and
obvious; mirrors the existing `_random_player_profile` module-level helper):

```python
def get_free_agents_team() -> "Team":
    """Return the singleton Free Agents Team, creating it on first call.

    The Free Agents Team is identified by the magic name "Free Agents".
    Idempotent: subsequent calls return the same Team row.
    """
    team, _created = Team.objects.get_or_create(name="Free Agents")
    return team
```

- Uses `get_or_create(name="Free Agents")`.
- Returns the `Team` instance (not the `(team, created)` tuple — callers do
  not need the `created` flag).
- Idempotent: the second call must return the same `pk`.
- The Free Agents Team has no `slot_*` FKs filled — `is_valid_roster` returns
  `False`, **by design**.

### 7c. Team-list view migration

`teams/views.py::team_list` (the existing list view) must switch from
`Team.objects.all()` to `Team.objects.regular()`. The Code agent edits this
one call site as part of LG-00.

**Out of scope:** other call sites of `Team.objects.all()` (admin, REST API,
`simulate_match`, etc.) are NOT migrated in LG-00. The Free Agents Team has no
filled roster, so any code that iterates rosters or runs simulations against
it already fails the `is_valid_roster` gate.

---

## 8. Template contracts

### 8a. `templates/teams/generate_players.html` (new — form page)

Wireframe (Code agent fills in styling; the contract pins DOM ids and copy):

```django
{% extends "base.html" %}

{% block title %}Generate Players{% endblock %}

{% block content %}
<div class="container mt-4">
    <h1>Generate Players</h1>
    <p>
        Create randomised Teams and/or Players for testing or league bootstrap.
    </p>

    <form method="post" id="generate-players-form">
        {% csrf_token %}

        <div class="mb-3">
            <label for="generate-players-num-teams">Number of teams</label>
            {{ form.num_teams }}   {# rendered as <select id="generate-players-num-teams" ...> by Code agent #}
            {{ form.num_teams.errors }}
        </div>

        <div class="mb-3">
            <label for="generate-players-per-team">Players per team</label>
            {{ form.players_per_team }}
            {{ form.players_per_team.errors }}
        </div>

        <div class="mb-3">
            <label for="generate-players-mean">Stat mean</label>
            {{ form.mean }}
            {{ form.mean.errors }}
        </div>

        <div class="mb-3">
            <label for="generate-players-std-dev">Stat standard deviation</label>
            {{ form.std_dev }}
            {{ form.std_dev.errors }}
        </div>

        {% if form.non_field_errors %}
            <div class="alert alert-danger">{{ form.non_field_errors }}</div>
        {% endif %}

        <button type="submit" id="generate-players-submit" class="btn btn-primary">
            Generate
        </button>
    </form>
</div>
{% endblock %}
```

The Code agent ensures the form widgets render with the locked DOM ids on the
underlying `<select>`/`<input>` (e.g. by setting `widget.attrs["id"] = "..."`
in the Form class). Locked DOM ids:

| Element                                     | Locked id                       |
|---------------------------------------------|---------------------------------|
| `<form>`                                    | `generate-players-form`         |
| `num_teams` `<select>`                      | `generate-players-num-teams`    |
| `players_per_team` `<select>`               | `generate-players-per-team`     |
| `mean` `<input>`                            | `generate-players-mean`         |
| `std_dev` `<input>`                         | `generate-players-std-dev`      |
| Submit `<button>`                           | `generate-players-submit`       |

### 8b. `templates/teams/generate_players_done.html` (new — confirmation page)

Wireframe:

```django
{% extends "base.html" %}

{% block title %}Generation Complete{% endblock %}

{% block content %}
<div class="container mt-4">
    <h1>Generation complete</h1>

    {% if created_teams %}
        <h2>Created teams</h2>
        <ul id="generate-confirm-teams-list">
            {% for team in created_teams %}
                <li><a href="{% url 'team_detail' team.id %}">{{ team.name }}</a></li>
            {% endfor %}
        </ul>
    {% endif %}

    {% if free_agent_count %}
        <div class="alert alert-info" id="generate-confirm-free-agents-notice">
            <strong>Created
                <span id="generate-confirm-free-agent-count">{{ free_agent_count }}</span>
                free-agent players.</strong>
            They will be visible on the Players tab once it ships (LG-00c).
        </div>
    {% endif %}

    <a href="{% url 'team_list' %}">Back to Teams</a>
</div>
{% endblock %}
```

Locked DOM ids and copy substrings:

| Element / data                              | Locked value                                       |
|---------------------------------------------|----------------------------------------------------|
| Created-teams `<ul>` id                     | `generate-confirm-teams-list`                      |
| Free-agents notice `<div>` id               | `generate-confirm-free-agents-notice`              |
| Free-agent count `<span>` id                | `generate-confirm-free-agent-count`                |
| Free-agents notice copy (substring)         | `"Created"` … `"free-agent players"`               |
| Free-agents notice deferred-feature mention | `"once it ships (LG-00c)"`                         |
| URL name used for team links                | `team_detail` (existing — already in `teams/urls.py`) |
| URL name used for back link                 | `team_list` (existing)                             |

The free-agents notice block is rendered **only** when `free_agent_count > 0`.
The created-teams `<ul>` is rendered **only** when `created_teams` is
non-empty. When both are empty the page only shows the heading and the back
link — though this is unreachable under the form's `clean()` constraints.

### 8c. `templates/teams/team_list.html` (existing — entry-point edit)

Add an anchor in the page header, sibling to the existing "New Team" link:

```django
<a href="{% url 'generate_players' %}" id="generate-players-link">Generate Players</a>
```

The exact location within the header is the Code agent's call. Tests pin via
substring `"Generate Players"` and DOM id `generate-players-link`.

---

## 9. CONTEXT.md additions (frozen prose)

Two new domain terms appended to `CONTEXT.md` (Docs agent owns the edit).

### 9a. Free Agents Team

> **Free Agents Team**: the reserved system **Team** named `"Free Agents"`,
> identified by magic name (no `is_system` field). Holds generated players
> who were not assigned to a regular Team via the **LG-00 generation**
> flow's `num_teams = 0` branch. Filtered from the Teams list via the new
> `Team.objects.regular()` manager method; visible via the Players tab
> (LG-00c, deferred). Has no slot FKs filled — `is_valid_roster` returns
> False, by design (the Free Agents Team is a player container, not a
> competitive roster). Auto-created on first use via
> `Team.objects.get_or_create(name="Free Agents")`.
>
> _Avoid_: treating the Free Agents Team as a playable Team (it can't pass
> `is_valid_roster`); creating a second Free Agents Team (auto-created by
> name; reused).

### 9b. LG-00 generation

> **LG-00 generation**: the bulk player-creation flow at
> `GET /teams/generate/` (`POST` to the same URL). Two output modes —
> `num_teams ≥ 1` creates new Teams with auto-filled rosters + optional
> bench; `num_teams = 0` creates a flat pool of players on the **Free
> Agents Team**. Stat values are randomised by Gaussian draw (mean /
> std-dev user-configurable). Distinct from a roster CSV import (LG-00b)
> and from the per-player edit form.

---

## 10. PLAN.md additions (frozen prose)

The Docs agent:

1. Marks **LG-00** `- completed` with a dense implementation note in the
   house style (mirror the density of RV-03 / HX-01 / HX-02 notes — pure
   module + view + form + manager + helper + 2 templates + CONTEXT.md
   terms + scope-outs).
2. Inserts a new task **LG-00c · Sortable Players tab** AFTER the
   `LG-00b` entry, with body:
   > A new `/players/` index page listing every Player (including the Free
   > Agents pool), sortable by any of the 19 stats + `overall_rating` +
   > `team` + `preferred_roles`. Server-side sort via `?sort=&dir=asc|desc`
   > query params with HX-02 forgiving-fallback validation. Adds a
   > 'Players' nav link in `base.html`. Visible immediately after LG-00
   > lands so the generated free-agent pool is browsable.

---

## 11. Test boundary (frozen — Tests agent reads this section)

All LG-00 tests live in **three new files** under the existing
`teams/tests/` package:

| File | Kind |
|------|------|
| `teams/tests/test_player_generator.py` | Pure-unit (no DB, no Django imports in the assertion paths beyond stdlib) |
| `teams/tests/test_generate_view.py` | Django `TestCase` — form + view + DB writes |
| `teams/tests/test_team_list_filters_free_agents.py` | Django `TestCase` — manager + list view |

`teams/tests/` is confirmed a package in this repo (existing test files live
under it per the HX-01 / HX-02 precedent). If it does not exist as a package
in this branch yet, the Tests agent creates `__init__.py`.

### 11.1. `teams/tests/test_player_generator.py` (pure-unit)

Class suggestions and required cases:

#### `TestDrawStats`

1. **`test_output_has_19_keys_in_canonical_order`** — `list(draw_stats(rng, 50, 15).keys())`
   equals the `_STAT_FIELDS` tuple as a list (in order).
2. **`test_all_values_int_in_0_100`** — every value is an `int`, `0 <= v <= 100`.
3. **`test_keys_are_real_player_fields`** — every key in the returned dict
   is a real attribute on a `Player` instance (imports `Player` from the
   models layer is permitted in this **one** assertion since it's a
   contract-shape check; alternatively the test hard-codes the expected
   tuple, which is the cleaner choice — pinned: hard-code the expected
   tuple in the test file to keep the file Django-import-free).
4. **`test_clamp_at_0_and_100_triggers_with_extreme_std_dev`** — with
   `mean=50, std_dev=40` over 5000 draws (each producing 19 values), the
   set of all values contains both `0` and `100`. Seeded `random.Random(42)`.
5. **`test_same_seed_produces_identical_output`** — two calls with
   independently-constructed `random.Random(123)` produce equal dicts.

#### `TestDrawPreferredRoles`

1. **`test_output_length_is_1_2_or_3`** — over 1000 seeded draws, every
   output has length in `{1, 2, 3}`.
2. **`test_all_values_are_valid_roles`** — every output is a subset of
   `{"commander", "heavy", "scout", "medic", "ammo"}`.
3. **`test_no_duplicates_within_a_single_draw`** —
   `len(set(out)) == len(out)` for every draw.
4. **`test_count_distribution_approximates_70_20_10`** — over N=10_000
   seeded draws, the fraction with `len==1` is within ±0.03 of 0.70, the
   fraction with `len==2` is within ±0.03 of 0.20, the fraction with
   `len==3` is within ±0.02 of 0.10.
5. **`test_same_seed_produces_identical_output`** — same as
   `TestDrawStats`.

#### `TestAssignSlots`

The Tests agent assembles input lists of length 6 (each element is a list
of role names). Slot-key order asserted: `("commander", "heavy", "scout_1",
"scout_2", "medic", "ammo")`.

1. **`test_full_match_each_player_prefers_their_slot_role`** — Input:
   players preferring `[commander], [heavy], [scout], [scout], [medic],
   [ammo]`. Expected: `{"commander": 0, "heavy": 1, "scout_1": 2,
   "scout_2": 3, "medic": 4, "ammo": 5}`.
2. **`test_partial_match_unmatched_slots_are_None`** — Input: only 4 of
   the 6 players prefer roles that align with slots; the other 2 slots
   end up `None`-valued. Assert the matched slots map to the correct
   player indices and the remaining 2 slot keys map to `None`.
3. **`test_over_prefer_scout_third_scout_preferer_displaced`** — Input:
   three players prefer Scout (`["scout"]`), three prefer other roles.
   Both `scout_1` and `scout_2` go to the first two Scout-preferers
   (lowest indices); the third Scout-preferer is NOT assigned to a slot
   (slot may still be `None` for some other slot the third player did
   not prefer). Pin: among the three Scout-preferers, the two with the
   lowest indices fill the Scout slots.
4. **`test_no_player_prefers_commander_slot_is_None`** — Input: every
   player's preferred_roles excludes `"commander"`. Expected:
   `result["commander"] is None`.
5. **`test_assign_slots_deterministic_tiebreak`** — Input: player 0 and
   player 1 both prefer `"heavy"`. Expected: `result["heavy"] == 0`
   (lower index wins). Run twice in the same test — must produce the
   same result.
6. **`test_assign_slots_output_keys_are_slot_key_tuple_in_order`** —
   `list(result.keys()) == ["commander", "heavy", "scout_1", "scout_2",
   "medic", "ammo"]`.

#### `TestNoDjangoImportsLeaked`

1. **`test_no_django_imports_leaked`** — Fresh subprocess (or `sys.modules.pop`
   + `importlib.import_module`) import of `teams.player_generator`. After
   import, scan `sys.modules` for any module whose name starts with
   `"django"`. Assert the count is zero (or, if a `django` was imported
   transitively by stdlib's own ancestry, assert `teams.player_generator`
   itself has no `django`/`models`/`teams.constants` attributes). The
   contract: **the module imports cleanly with Django uninstalled** —
   mirror RES-04's `test_no_django_imports_leaked` exactly.

### 11.2. `teams/tests/test_generate_view.py` (Django `TestCase`)

Class suggestions:

#### `TestGenerateGet`

1. **`test_get_200`** — `GET reverse("generate_players")` → 200.
2. **`test_form_fields_present`** — Response body contains DOM ids
   `generate-players-num-teams`, `generate-players-per-team`,
   `generate-players-mean`, `generate-players-std-dev`,
   `generate-players-submit`, `generate-players-form`.

#### `TestGeneratePostHappyPathTeams`

1. **`test_post_3_teams_6_players_creates_18_players_3_teams`** — POST
   `num_teams="3"`, `players_per_team="6"`, `mean="50"`, `std_dev="15"`.
   Assert `Team.objects.regular().count() == 3` and `Player.objects.count() == 18`
   (assuming a clean test DB).
2. **`test_post_3_teams_all_rosters_valid`** — Same POST. Each newly
   created Team has `is_valid_roster` True.
3. **`test_post_response_is_confirmation_page_with_team_links`** —
   Response is 200, body contains `generate-confirm-teams-list` and 3
   `<a href="/teams/<id>/">` anchors (one per created team).

#### `TestGeneratePostHappyPathBenchPlayers`

1. **`test_post_2_teams_8_players_each_creates_16_players_with_bench`** —
   POST `num_teams="2"`, `players_per_team="8"`. Assert
   `Player.objects.count() == 16`; for each new Team, `len(team.active_players) == 6`
   and `len(team.bench_players) == 2` (using the existing properties).

#### `TestGeneratePostHappyPathFreeAgents`

1. **`test_post_0_teams_20_pool_creates_20_free_agents`** — POST
   `num_teams="0"`, `players_per_team="20"`. Assert no new regular Teams
   (`Team.objects.regular().count()` unchanged), and the Free Agents
   Team exists with exactly 20 players (`free_agents.players.count() == 20`).
2. **`test_post_0_teams_response_contains_free_agents_notice`** — Same
   POST. Response body contains DOM id `generate-confirm-free-agents-notice`
   and `generate-confirm-free-agent-count`, and the substring `"20"`
   inside the count span.

#### `TestGeneratePostRandomResolutions`

1. **`test_random_2_10_resolves_in_range`** — Loop 10 times: POST
   `num_teams="random_2_10"`, `players_per_team="random_team"`. After
   each POST, count `Team.objects.regular()` created in that run — must
   be in `[2, 10]`. Each new Team's `Player.objects.filter(team=team).count()`
   must be in `[6, 8]`. Reset the DB between iterations
   (transactional `TestCase` already handles this per-test; for in-test
   loops, use explicit `Team.objects.regular().delete()`).
2. **`test_random_pool_resolves_in_range`** — Loop 10 times: POST
   `num_teams="0"`, `players_per_team="random_pool"`. After each POST,
   the Free Agents Team's player count is in `[12, 100]`.

#### `TestGeneratePostCrossFieldValidation`

1. **`test_num_teams_0_with_players_per_team_8_is_invalid`** — POST
   `num_teams="0"`, `players_per_team="8"`. Response 200 with form errors;
   body contains the substring `"Players per team must be 12–100"`.
   Assert NO Teams or Players were created.
2. **`test_num_teams_5_with_players_per_team_50_is_invalid`** — POST
   `num_teams="5"`, `players_per_team="50"`. Response 200 with form
   errors; body contains the substring `"Players per team must be 6–9"`.
   Assert NO Teams or Players were created.

#### `TestGeneratePostNameCollisions`

1. **`test_pre_existing_team_name_gets_hash_suffix`** — Pre-create
   `Team.objects.create(name=TEAM_NAMES[0])`. POST a generate run that
   would draw `TEAM_NAMES[0]` from the pool (use a seeded `random.shuffle`
   stub, OR loop the POST until a Team with that name's `" #2"` suffix
   appears, OR pre-create Teams for ALL names except one to force the
   collision). Assert no `IntegrityError` and at least one Team has a
   name ending in `" #2"`.
2. **`test_pre_existing_free_agent_player_name_gets_hash_suffix`** —
   Pre-create `Player.objects.create(team=get_free_agents_team(),
   name=PLAYER_NAMES[0], ...)`. POST `num_teams=0, players_per_team=N`
   where `N` is large enough to force the collision. Assert no
   `IntegrityError` and at least one new Free Agents player has a name
   ending in `" #2"`.

#### `TestGeneratePostTransactionAtomic`

1. **`test_pure_module_raises_mid_generation_rolls_back`** — Monkey-patch
   `teams.views.draw_stats` (or `draw_preferred_roles`) to raise
   `RuntimeError("boom")` on the Nth call (N chosen so it fires
   mid-loop, e.g. after the first Team is created but before the
   second). POST a 2-Team, 6-player run. Assert the response is a 5xx
   (the exception propagates) and `Team.objects.regular().count() == 0`
   and `Player.objects.count() == 0` afterward — i.e. the `@transaction.atomic`
   rolled back all writes including the first Team.

#### `TestFreeAgentsTeamAutoCreated`

1. **`test_free_agents_team_created_on_first_pool_post`** — Before:
   `Team.objects.filter(name="Free Agents").count() == 0`. POST
   `num_teams=0, players_per_team=12`. After: exactly one row with
   `name="Free Agents"`.
2. **`test_free_agents_team_reused_on_second_pool_post`** — POST two
   `num_teams=0` runs in sequence. Assert `Team.objects.filter(name="Free Agents").count() == 1`
   (still exactly one), and the row's `pk` is unchanged across the two
   runs.

### 11.3. `teams/tests/test_team_list_filters_free_agents.py` (Django `TestCase`)

#### `TestObjectsRegularManagerMethod`

1. **`test_regular_excludes_free_agents_team`** — Create the Free Agents
   Team via `get_free_agents_team()`. Create one normal Team. Assert
   `set(Team.objects.regular().values_list("name", flat=True))` does
   NOT contain `"Free Agents"`.
2. **`test_objects_all_still_includes_free_agents`** — Same setup.
   `Team.objects.all()` DOES include the Free Agents Team.

#### `TestTeamListExcludesFreeAgents`

1. **`test_team_list_html_does_not_show_free_agents`** — Create the Free
   Agents Team. GET `reverse("team_list")` (or `/teams/`). Response 200;
   body does NOT contain the substring `"Free Agents"`.
2. **`test_team_list_still_shows_regular_teams`** — Create the Free
   Agents Team AND a regular team named `"Red Phoenix"`. GET `/teams/`.
   Body contains `"Red Phoenix"`.

### 11.4. Files the Tests agent edits

| File | Action |
|------|--------|
| `teams/tests/test_player_generator.py` | NEW |
| `teams/tests/test_generate_view.py` | NEW |
| `teams/tests/test_team_list_filters_free_agents.py` | NEW |
| `teams/tests/__init__.py` | TOUCH if not already a package |

No existing test files are touched. The Tests agent does NOT run the full
pytest suite — only the new files (scoped run).

---

## 12. File ownership (who edits what)

| File | Code | Tests | Docs |
|------|:----:|:-----:|:----:|
| `teams/player_generator.py` (new pure module) | OWN | — | — |
| `teams/forms.py` (`GenerateLeagueForm`) | OWN | — | — |
| `teams/views.py` (`generate_players`; `team_list` switch to `.regular()`) | OWN | — | — |
| `teams/urls.py` (route) | OWN | — | — |
| `teams/constants.py` (`TEAM_NAMES` tuple) | OWN | — | — |
| `teams/models.py` (`TeamManager`, `get_free_agents_team`) | OWN | — | — |
| `templates/teams/generate_players.html` (new) | OWN | — | — |
| `templates/teams/generate_players_done.html` (new) | OWN | — | — |
| `templates/teams/team_list.html` (entry-point link) | OWN | — | — |
| `teams/tests/test_player_generator.py` (new) | — | OWN | — |
| `teams/tests/test_generate_view.py` (new) | — | OWN | — |
| `teams/tests/test_team_list_filters_free_agents.py` (new) | — | OWN | — |
| `CONTEXT.md` (Free Agents Team, LG-00 generation) | — | — | OWN |
| `PLAN.md` (mark LG-00 done; add LG-00c) | — | — | OWN |
| `teams/CLAUDE.md` (LG-00 subsection) | — | — | OWN |

Tests agent: the pure-unit file imports **only** `teams.player_generator` and
stdlib. The view/DB test cases use Django's `TestCase` + the test client.

---

## 13. Determinism / scope notes

- **No simulation behaviour change.** LG-00 creates Players and Teams; it does
  not run the simulator and does not consume any simulator RNG. No
  Score Calibration re-baseline.
- **No global RNG seeding.** The view constructs `rng = random.Random()` per
  POST (fresh entropy). Tests that need determinism construct their own
  `random.Random(seed)` and pass it directly to the pure functions.
- **`@transaction.atomic` covers the entire POST handler** so partial
  generation never persists. Pinned by Tests case
  `test_pure_module_raises_mid_generation_rolls_back`.
- **No is_simulated / round-history coupling** — Player and Team rows have
  no such flag; the Free Agents Team is the only marker of "generated, not
  league-real" players, and only by convention.

---

## 14. Out of scope (do NOT add)

- No per-stat or per-role bell-curve presets (single `mean`/`std_dev` for all
  19 stats).
- No `is_system` Team field (Free Agents identified by magic name).
- No Players tab (`/players/`) — that is **LG-00c**, deferred to after this
  task lands.
- No preview-before-commit UI (POST writes immediately).
- No seed input field on the form.
- No CSV import (`LG-00b`, separate task).
- No Season / Tournament linkage (`LG-01+`).
- No simulation behaviour change, no Score Calibration re-baseline.
- No JS-driven dependent dropdown toggle for v1 (the wide superset is
  rendered always; `clean()` enforces the cross-field rule — ugly but
  functional).
- No refactor of `_random_player_profile()` to take a `name` parameter — the
  view discards the name the helper returns.
- No migration. No model field changes. No ADR.

---

## 15. Locked names — quick-reference block

| Slot                                        | Name                                                                 |
|---------------------------------------------|----------------------------------------------------------------------|
| URL pattern                                 | `path("generate/", views.generate_players, name="generate_players")` |
| Full URL                                    | `/teams/generate/`                                                   |
| View (Django)                               | `teams.views.generate_players`                                       |
| Form                                        | `teams.forms.GenerateLeagueForm`                                     |
| Pure module                                 | `teams/player_generator.py`                                          |
| Pure function — stats                       | `draw_stats(rng, mean, std_dev) -> dict[str, int]`                   |
| Pure function — preferred roles             | `draw_preferred_roles(rng) -> list[str]`                             |
| Pure function — slot assignment             | `assign_slots(preferred_roles_per_player) -> dict[str, int | None]`  |
| Module-level stat-name tuple                | `_STAT_FIELDS` (19 entries, order pinned in §2c)                     |
| Module-level role-name tuple                | `_ROLE_NAMES` (5 entries: commander, heavy, scout, medic, ammo)      |
| Module-level slot-key tuple                 | `_SLOT_KEYS` (6: commander, heavy, scout_1, scout_2, medic, ammo)    |
| Manager class                               | `teams.models.TeamManager` (assigned to `Team.objects`)              |
| Manager method                              | `Team.objects.regular()` — excludes `name="Free Agents"`             |
| Free Agents helper                          | `teams.models.get_free_agents_team() -> Team` (module-level)         |
| Free Agents magic name                      | `"Free Agents"` (Team.name)                                          |
| Constants tuple                             | `teams.constants.TEAM_NAMES: tuple[str, ...]` (30–50 entries)        |
| Templates                                   | `templates/teams/generate_players.html` (form), `templates/teams/generate_players_done.html` (confirm) |
| DOM id — form                               | `generate-players-form`                                              |
| DOM id — num_teams                          | `generate-players-num-teams`                                         |
| DOM id — players_per_team                   | `generate-players-per-team`                                          |
| DOM id — mean                               | `generate-players-mean`                                              |
| DOM id — std_dev                            | `generate-players-std-dev`                                           |
| DOM id — submit                             | `generate-players-submit`                                            |
| DOM id — entry-point link (team_list)       | `generate-players-link`                                              |
| DOM id — confirm teams list                 | `generate-confirm-teams-list`                                        |
| DOM id — confirm free-agents notice         | `generate-confirm-free-agents-notice`                                |
| DOM id — confirm free-agent count           | `generate-confirm-free-agent-count`                                  |
| Test files (new)                            | `teams/tests/test_player_generator.py`, `teams/tests/test_generate_view.py`, `teams/tests/test_team_list_filters_free_agents.py` |
| CONTEXT.md terms added                      | **Free Agents Team**, **LG-00 generation**                           |
| PLAN.md new task inserted                   | **LG-00c · Sortable Players tab** (after LG-00b)                     |
| Scope-out reminders                         | no `is_system` field; no Players tab (LG-00c); no preview; no seed field; no CSV import (LG-00b); no Season/Tournament link; no sim behaviour change; no Score Calibration re-baseline; no JS-driven dependent dropdown for v1; no `_random_player_profile` refactor |
