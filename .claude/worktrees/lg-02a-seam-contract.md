# LG-02a — sandbox single-elimination Tournament — SEAM CONTRACT

Single source of truth for the 3 parallel build/test/docs agents. Every name,
field, signature, return shape, DOM id, and URL below is **locked**. Drift = a
failing test, not a judgement call.

**App:** `matches`. **Mode:** sandbox (standalone, decoupled from League/Season).
**Format:** single-elimination only (enum extensible, only `single_elimination`
ships). **Node = one existing `Match`** (2-round, `BatchSimulator.simulate_match`).
**Repo paths note:** the Django project is nested at
`laserforce_simulator/laserforce_simulator/`; the `matches` / `teams` / `core`
apps live under `laserforce_simulator/laserforce_simulator/matches/` etc. All
paths below are app-relative (`matches/…`) unless prefixed with `templates/` or
`laserforce_simulator/`.

---

## 0. Domain vocabulary (use these nouns verbatim; CONTEXT.md `### Tournaments`)

| Term | Meaning |
|---|---|
| **Tournament** | The standalone persisted bracket object (one `Tournament` row). |
| **Bracket** | The full tree of nodes for a Tournament (the in-memory structure built by `matches/bracket.py`). |
| **Bracket round** | One horizontal layer of the bracket (e.g. round 1, semis, final). **NEVER "round"** — collides with the 15-min game `GameRound`. |
| **Bracket node** | One node = one slot for a single `Match`. Holds two team slots, an optional played `Match`, an `is_bye` flag, and an advancement pointer. |
| **Bracket seed** | A participant's seed integer (1 = top seed). **NEVER "seed" alone in code comments** where RNG-seed confusion is possible — collides with RNG seed. |
| **Seeding** | The ordering of participants by Bracket seed (default talent order + manual reorder). |
| **Advancement** | Promoting a node's winner into its parent node's empty team slot. |
| **Bye** | A round-1 node a top Bracket seed skips when N is not a power of 2 (auto-advance). |

CONTEXT.md gains a `### Tournaments` subsection with the 8 terms above. No ADR
(decisions reversible: new models + a pure module + read-only-ish views).

---

## 1. Models (`matches/models.py`, appended after `Season`)

Migration: **`matches/migrations/0032_tournament.py`** (next sequential after the
latest existing `matches` migration — Code agent runs `makemigrations` and renames
to this exact filename; dependency is the latest `matches` migration at branch-cut
+ the latest `teams` migration). Operations in pinned order:
`CreateModel(Tournament)` → `CreateModel(TournamentParticipant)` →
`CreateModel(BracketNode)`. **No `RunPython`, no backfill** (ADR-0004 precedent).

### 1a. `Tournament`

```python
class Tournament(models.Model):
    FORMAT_CHOICES = (("single_elimination", "Single elimination"),)
    STATE_CHOICES = (
        ("setup", "Setup"),        # participants chosen, Seeding editable, bracket NOT built
        ("active", "Active"),      # bracket built + locked, nodes being played
        ("completed", "Completed"),# champion crowned
    )

    name = models.CharField(max_length=100)
    format = models.CharField(
        max_length=32, choices=FORMAT_CHOICES, default="single_elimination"
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="setup")
    created_at = models.DateTimeField(auto_now_add=True)
    # Stamped by advance logic when the final node resolves. SET_NULL — deleting
    # a Team must NOT cascade-delete the Tournament's history.
    champion = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tournaments_won",
    )

    def __str__(self) -> str:
        return self.name
```

**Justification of each field:** `format` enum present-but-single per locked
decision 2 (extensible). `state` is the 3-state machine (decision: setup is the
seeding-editable window — the bracket is only built on the setup→active
transition, mirroring `Season.start_season`'s draft→active M2M lock). `champion`
SET_NULL mirrors `Match.winner` / `Season.champion_team` precedent.

**State machine (methods on `Tournament`):**

```python
@transaction.atomic
def lock_and_build(self) -> None:
    """setup -> active. Validates participant count (>= 4), builds the
    BracketNode tree from the current Seeding via matches.bracket.build_bracket,
    persists every node, flips state='active'. Raises django.core.exceptions
    .ValidationError on count < 4 or state != 'setup'."""

@property
def is_locked(self) -> bool:
    """True iff state != 'setup' (Seeding can no longer be edited)."""

def find_next_playable_node(self) -> "BracketNode | None":
    """Delegates to matches.bracket.find_next_node over this Tournament's nodes.
    Returns the lowest (bracket_round, position) node with both team slots filled,
    is_bye=False, and match_id IS NULL. None when nothing is ready (or completed)."""
```

`lock_and_build` raises `ValidationError` (imported `from
django.core.exceptions import ValidationError` — already imported in
`models.py`). The `>= 4` minimum is the locked decision-5 floor (arbitrary N ≥ 4).

### 1b. `TournamentParticipant`

```python
class TournamentParticipant(models.Model):
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="participants"
    )
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="+")
    # 1-based Bracket seed. Lower int = stronger seed. Unique per Tournament.
    seed = models.PositiveIntegerField()

    class Meta:
        ordering = ["seed"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "seed"], name="uniq_tournament_seed"
            ),
            models.UniqueConstraint(
                fields=["tournament", "team"], name="uniq_tournament_team"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tournament.name} #{self.seed} {self.team.name}"
```

**Justification:** `team` CASCADE + `related_name="+"` (no reverse accessor
needed — participants are always reached via the Tournament). `seed` is
`PositiveIntegerField` (1-based, lower=stronger). Two `UniqueConstraint`s pin: no
duplicate seed and no duplicate team within one Tournament. `Meta.ordering` by
`seed` makes `tournament.participants.all()` Seeding-ordered by default. The field
is named **`seed`** on the model (the column); domain comments use **"Bracket
seed"** to disambiguate from RNG seed.

### 1c. `BracketNode`

```python
class BracketNode(models.Model):
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="nodes"
    )
    # 1-based Bracket round (1 = first round played; max = final).
    bracket_round = models.PositiveIntegerField()
    # 0-based position within the Bracket round, top-to-bottom in the tree.
    position = models.PositiveIntegerField()

    # The two team slots. Either may be NULL pre-Advancement (a later-round node
    # whose feeder nodes have not resolved yet). SET_NULL on Team delete.
    team_a = models.ForeignKey(
        "teams.Team", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    team_b = models.ForeignKey(
        "teams.Team", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    # The Bracket seed integers parked alongside each slot, so the tie-break can
    # break on "higher Bracket seed" without re-querying participants and so a
    # bye node can carry its single team's seed forward. NULL when the slot is empty.
    seed_a = models.PositiveIntegerField(null=True, blank=True)
    seed_b = models.PositiveIntegerField(null=True, blank=True)

    # The played Match for this node (NULL until played; a bye node stays NULL).
    match = models.ForeignKey(
        "matches.Match", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bracket_node",
    )
    # Advancement pointer: the parent node this node's winner feeds into (NULL for
    # the final node). slot tells the parent which side to fill.
    advances_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="feeders",
    )
    advances_to_slot = models.CharField(
        max_length=1, null=True, blank=True,
        choices=(("a", "team_a"), ("b", "team_b")),
    )
    # A round-1 node a top Bracket seed skips (auto-advanced; never played).
    is_bye = models.BooleanField(default=False)
    # The Team that won (or auto-advanced through) this node. NULL until resolved.
    winner = models.ForeignKey(
        "teams.Team", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["bracket_round", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "bracket_round", "position"],
                name="uniq_tournament_round_position",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tournament.name} R{self.bracket_round}/{self.position}"
```

**Justification per field:** `bracket_round` (1-based) + `position` (0-based) are
the tree coordinates; the `UniqueConstraint` pins one node per coordinate.
`team_a`/`team_b` nullable SET_NULL — empty until feeders resolve; a bye node has
exactly one filled slot (the surviving seed) and one NULL slot. `seed_a`/`seed_b`
carry the Bracket seed alongside each slot so the **tie-break** ("higher Bracket
seed advances") and bye carry-forward need no participant re-query. `match`
SET_NULL nullable — NULL until the node is played; a bye node stays NULL forever.
`advances_to` (self-FK) + `advances_to_slot` are the **Advancement pointer**: when
this node resolves, the winner fills `advances_to.team_<slot>`. `is_bye` flags the
auto-advance node. `winner` caches the resolved Team (a bye node's `winner` is set
at build time; a played node's `winner` is set at advance time).

---

## 2. Pure module `matches/bracket.py`

**Frozen import allowlist (the ONLY modules this file may import):**
`dataclasses`, `typing`, `math`, `collections`. **NO Django, NO ORM, NO
`random`, NO `datetime`, NO I/O, NO logging.** Enforced by a
`TestNoDjangoImportsLeaked` subprocess fresh-import + `sys.modules` walk
(mirrors `matches/standings.py` / `matches/schedule_generator.py` precedent).

The pure module owns bracket **STRUCTURE + Seeding + bye placement + tie-break
math**. Django objects are converted to plain ints/dicts at the **view/model
boundary** — the module never sees a `Team`, `Match`, or `BracketNode` ORM
instance.

### 2a. Dataclasses (frozen)

```python
@dataclass(frozen=True)
class BracketNodeSpec:
    """One node in the built bracket, as plain data (pre-ORM)."""
    bracket_round: int          # 1-based
    position: int               # 0-based within the round
    team_a_id: int | None       # participant team id, or None (empty/feeder slot)
    team_b_id: int | None
    seed_a: int | None          # Bracket seed parked with team_a_id
    seed_b: int | None
    is_bye: bool
    advances_to: tuple[int, int] | None   # (bracket_round, position) of parent, None for final
    advances_to_slot: str | None          # "a" | "b" | None
    winner_id: int | None       # pre-resolved for a bye node, else None


@dataclass(frozen=True)
class ParticipantSpec:
    """A participant as plain data crossing the view->pure-module boundary."""
    team_id: int
    seed: int                   # 1-based Bracket seed
```

### 2b. Public functions (every signature LOCKED)

```python
def default_seed_order(team_ratings: list[tuple[int, float]]) -> list[int]:
    """Default Seeding: team ids sorted by mean active-player overall_rating DESC,
    then team_id ASC as a deterministic tiebreak.

    team_ratings: list of (team_id, mean_overall_rating). The view builds this
    from Team.active_players + Player.overall_rating (the SAME talent ranking the
    LG-01c draft-preview standings use). Returns team ids best-first (index 0 is
    Bracket seed 1)."""


def build_bracket(participants: list[ParticipantSpec]) -> list[BracketNodeSpec]:
    """Build the full single-elimination bracket node-spec list for N >= 4
    participants (arbitrary N, byes for non-powers-of-2).

    Standard 1vN, 2v(N-1), ... seed pairing. The bracket size is the next power
    of two >= N (2 ** ceil(log2(N))). The top (size - N) Bracket seeds receive a
    Bye in round 1 (is_bye=True nodes pre-resolved to that seed's team_id, with
    winner_id set). advances_to / advances_to_slot wire every node to its parent;
    the final node has advances_to=None.

    Returns BracketNodeSpec list ordered by (bracket_round, position). Raises
    ValueError on len(participants) < 4 or duplicate seeds/team_ids."""


def find_next_node(
    nodes: list[dict], 
) -> dict | None:
    """Return the lowest (bracket_round, position) node dict that is PLAYABLE:
    both team slots filled (team_a_id and team_b_id not None), is_bye False, and
    no recorded winner_id / match_id yet. None when nothing is ready.

    nodes: list of plain dicts (the view flattens BracketNode rows to dicts via
    _node_to_dict — see seam below). The pure function never touches the ORM."""


def advance_winner(
    nodes: list[dict], node_position: tuple[int, int], winner_id: int, winner_seed: int,
) -> list[dict]:
    """Given the flattened node dicts and the (bracket_round, position) of a node
    that just resolved to winner_id (Bracket seed winner_seed), return the list of
    PARENT mutations needed: a list of dicts
    {"bracket_round": int, "position": int, "slot": "a"|"b", "team_id": int, "seed": int}.

    Empty list when the resolved node is the final (advances_to is None). The view
    applies these mutations to the ORM. Pure: computes the target slot from the
    resolved node's advances_to / advances_to_slot fields carried in `nodes`."""


def resolve_bye_chain(nodes: list[dict]) -> list[dict]:
    """Cascade byes at build time: for every is_bye node, return the parent-slot
    mutations (same shape as advance_winner's output) that promote the bye team
    into the next round, recursively if a later-round node ends up with one filled
    slot and the other slot's feeder is also a bye. Used by build/persist so a top
    seed's bye is reflected in round 2 immediately. Empty list when no byes."""


def break_tie(
    seed_a: int, best_round_score_a: int, seed_b: int, best_round_score_b: int,
) -> int:
    """Deterministic tie-break for a node whose Match has winner_id IS NULL.
    Returns the Bracket seed (seed_a or seed_b) of the team that advances:
      1. Higher best single-Round score advances (best_round_score_* = the team's
         max of its two GameRound point totals in this Match).
      2. If still equal, the higher Bracket seed (LOWER seed int) advances.
    No re-sim. Pure integer comparison."""
```

**`TestNoDjangoImportsLeaked` obligation:** the Tests agent adds a class that
spawns `python -c "import matches.bracket"` in a subprocess (Django configured via
`DJANGO_SETTINGS_MODULE` is NOT needed because the module imports no Django) and
asserts no `sys.modules` key starts with `"django"`. Mirror the exact subprocess
idiom from `matches/tests/test_standings.py::TestNoDjangoImportsLeaked`.

---

## 3. View functions (`matches/tournament_views.py`, NEW file)

All views import `from .bracket import build_bracket, find_next_node,
advance_winner, default_seed_order, break_tie, resolve_bye_chain`,
`from .simulation.entrypoints import BatchSimulator`,
`from teams.views import _generate_teams` (the LG-01b cross-app seam — DO NOT
change `_generate_teams`'s signature), `from teams.models import Team`,
`from teams.constants import TEAM_NAMES, PLAYER_NAMES`.

`app_mode` is `"sandbox"` for every `/tournaments/*` path (the LG-01k path-prefix
processor maps anything not under `/leagues/` or `/seasons/` to sandbox — no
processor change needed; `/tournaments/` falls into the sandbox bucket
automatically).

| View | HTTP | Guard | Behaviour / redirect | Context keys |
|---|---|---|---|---|
| `tournament_list(request)` | GET | — | List all Tournaments newest-first (`Tournament.objects.order_by("-id")`). | `tournaments` |
| `tournament_create(request)` | GET / POST | POST creates | GET → render create form (team-source picker). POST valid → create `Tournament(state="setup")` + `TournamentParticipant` rows (default Seeding via `default_seed_order`), `@transaction.atomic`, redirect to `tournament_detail`. POST invalid → re-render form (200). | `form`, `available_teams` |
| `tournament_detail(request, tournament_id)` | GET | `HttpResponseNotAllowed(["GET"])` on non-GET | `get_object_or_404(Tournament)`. Renders the bracket tree + (in `setup`) the Seeding-edit form + play controls. | see §6 |
| `tournament_reseed(request, tournament_id)` | POST | `HttpResponseNotAllowed(["POST"])`; reject if `tournament.is_locked` (302 back w/ message) | Persist a manually reordered Seeding (new `seed` ints from POST), `@transaction.atomic`. Redirect to `tournament_detail`. | — |
| `tournament_lock(request, tournament_id)` | POST | `HttpResponseNotAllowed(["POST"])` | Calls `tournament.lock_and_build()` (setup→active, builds + persists nodes). On `ValidationError` → redirect back w/ `messages.error`. Redirect to `tournament_detail`. | — |
| `tournament_play_next(request, tournament_id)` | POST | `HttpResponseNotAllowed(["POST"])`; reject if state != `"active"` | Find next playable node (`tournament.find_next_playable_node()`), sim ONE Match via `BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")`, attach to node, resolve winner (incl. tie-break), Advance, stamp champion if final. `@transaction.atomic`. Redirect to `tournament_detail`. | — |

**Guard idiom** (locked, mirrors `movement_heatmap` / `export_round_report`):
non-allowed method → `return HttpResponseNotAllowed([...])` as the FIRST line of
the body.

**`@transaction.atomic`** wraps `tournament_create`, `tournament_reseed`,
`tournament_lock`, `tournament_play_next` (each is a single all-or-nothing write).

**Play-next winner resolution (in the view, the ORM side of the seam):**
1. `match = BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")`.
2. `node.match = match`.
3. `winner_team = match.winner` (the `Match.calculate_winner` result, persisted by `Match.save`).
4. If `winner_team is None` (a true tie — rounds tied AND total points tied):
   compute `best_a = max(match.red_round1_points, match.red_round2_points)` and
   `best_b = max(match.blue_round1_points, match.blue_round2_points)` **keyed to
   which slot each team occupies** (team_a is the team passed as `team_red`, so
   `red_*` is team_a's, `blue_*` is team_b's — `simulate_match` stores
   team-position-keyed columns); call
   `winning_seed = break_tie(node.seed_a, best_a, node.seed_b, best_b)` and map
   that seed back to `node.team_a`/`node.team_b`.
5. Set `node.winner`, save node. Compute parent mutations via
   `advance_winner(flattened_nodes, (node.bracket_round, node.position),
   winner_team.id, winner_seed)`; apply them to the parent `BracketNode` rows.
6. If `node.advances_to_id is None` (final): `tournament.champion = winner_team`,
   `tournament.state = "completed"`, save.

---

## 4. URLs (`matches/tournament_urls.py`, NEW file)

No `app_name` (bare URL names, mirrors `season_urls.py` / `league_urls.py`).

```python
from django.urls import path
from . import tournament_views as views

urlpatterns = [
    path("", views.tournament_list, name="tournament_list"),
    path("create/", views.tournament_create, name="tournament_create"),
    path("<int:tournament_id>/", views.tournament_detail, name="tournament_detail"),
    path("<int:tournament_id>/reseed/", views.tournament_reseed, name="tournament_reseed"),
    path("<int:tournament_id>/lock/", views.tournament_lock, name="tournament_lock"),
    path("<int:tournament_id>/play-next/", views.tournament_play_next, name="tournament_play_next"),
]
```

**Order matters:** `create/` BEFORE `<int:tournament_id>/` (the literal must not
be shadowed by the int converter — though `<int:>` only matches digits, the
explicit ordering pin guards future regex drift). `""` (`tournament_list`) sits
first; the int-converter routes never match the empty path.

**Mount point** in `laserforce_simulator/laserforce_simulator/urls.py` — insert
**after** the `path("matches/", include("matches.urls"))` line and before
`path("seasons/", …)`:

```python
path("tournaments/", include("matches.tournament_urls")),
```

Reverse via bare names: `reverse("tournament_detail", args=[t.id])`.

---

## 5. base.html sandbox nav entry (LG-01k mode-based topnav)

**The prompt's "`app_mode == "sandbox"` block" is the `{% elif app_mode ==
"sandbox" %}` branch of `templates/base.html` (currently lines 56–63).** Insert a
new flat nav anchor in that branch, placed AFTER the existing `Maps` link and
BEFORE the `{% include "_partials/topnav_tools_help.html" %}` line:

```html
<a class="nav-link" id="tournaments-nav-link" href="{% url 'tournament_list' %}">Tournaments</a>
```

- **DOM id:** `tournaments-nav-link` (locked).
- **Label:** `Tournaments` (locked).
- **Block:** the `{% elif app_mode == "sandbox" %}` branch ONLY (NOT the league
  branch, NOT the start `{% else %}` branch). `/tournaments/*` resolves to
  `app_mode == "sandbox"` via the existing path-prefix processor — no processor
  edit.

---

## 6. Templates

All under `laserforce_simulator/templates/`. Each extends `base.html`.

### 6a. `templates/matches/tournament_list.html`
- `{% block title %}Tournaments{% endblock %}`.
- DOM ids: `tournament-list-table` (the `<table>`, rendered only when
  `tournaments` non-empty), `tournament-list-empty` (notice when empty,
  contains substring `No tournaments yet`), `tournament-create-link` (always —
  the "Create Tournament" button → `{% url 'tournament_create' %}`).
- Each row: name link → `{% url 'tournament_detail' t.id %}`, state badge in an
  element whose `class` contains `state-badge`.

### 6b. `templates/matches/tournament_create.html`
- `{% block title %}Create Tournament{% endblock %}`.
- One `<form method="post" id="tournament-create-form">` with `{% csrf_token %}`.
- **Team-source (select + generate) form** — DOM ids:
  - `tournament-create-name` (`<input>` for the Tournament name).
  - `tournament-create-team-select` (a multi-`<select>` of existing Teams —
    `Team.objects.regular()`, the LG-00 manager that excludes Free Agents pool
    teams).
  - `tournament-create-generate-count` (`<input type="number">` — how many NEW
    teams to generate via `_generate_teams`).
  - `tournament-create-generate-ppt` (`<input type="number">` — players per team
    for generated teams; defaults to `6`).
  - `tournament-create-submit` (submit button).
- Empty state for the select: if `available_teams` is empty render
  `tournament-create-no-teams-notice` (substring `No teams available`).

### 6c. `templates/matches/tournament_detail.html`
- `{% block title %}{{ tournament.name }} — Tournament{% endblock %}` (em-dash
  U+2014).
- **Bracket tree** (locked decision 9 — tree on the detail page): outer
  container DOM id `tournament-bracket` containing one column per Bracket round
  with DOM id `tournament-bracket-round-{n}` (1-based) and one node element per
  Bracket node with DOM id `tournament-node-{bracket_round}-{position}`. A bye
  node carries the CSS-class substring `bye-node`; a played node links to its
  Match via `{% url ... %}` for the match detail (Code agent picks the match URL
  name — `match_detail` if it exists, else the node renders the score inline).
- **Seeding-edit form** (only when `tournament.state == "setup"`): DOM id
  `tournament-seeding-form` — a `<form method="post"
  action="{% url 'tournament_reseed' tournament.id %}">` with one ordered input
  per participant (DOM id `tournament-seed-input-{team_id}`) carrying the new
  Bracket seed int, plus a submit button `tournament-seeding-submit`. Rendered
  ONLY in `setup`; absent once locked.
- **Play controls:**
  - `tournament-lock-form` — a `<form method="post" action="{% url
    'tournament_lock' tournament.id %}">` with a submit button
    `tournament-lock-submit` (label "Lock & Build Bracket"); rendered ONLY when
    `state == "setup"`.
  - `tournament-play-next-form` — a `<form method="post" action="{% url
    'tournament_play_next' tournament.id %}">` with submit button
    `tournament-play-next-submit` (label "Play Next Match"); rendered ONLY when
    `state == "active"`.
  - `tournament-champion-banner` (only when `state == "completed"`, contains the
    champion team name and the substring `Champion`).
- **Empty state:** if a Tournament somehow has zero participants, render
  `tournament-detail-empty` (substring `No participants`).

**`tournament_detail` context keys (frozen):** `tournament`, `participants`
(Seeding-ordered list), `rounds` (a list of `{"bracket_round": int, "nodes":
list[node_view_dict]}` grouped for the tree), `next_node` (the
`find_next_playable_node()` result or `None`), `is_locked` (bool),
`can_play` (`state == "active" and next_node is not None`).

`node_view_dict` keys: `bracket_round`, `position`, `team_a` (`Team | None`),
`team_b` (`Team | None`), `seed_a`, `seed_b`, `is_bye`, `match` (`Match | None`),
`winner` (`Team | None`).

---

## 7. Admin (`matches/admin.py`, appended after `SeasonAdmin`)

```python
from .models import Tournament, TournamentParticipant, BracketNode

class TournamentParticipantInline(admin.TabularInline):
    model = TournamentParticipant
    extra = 0

class BracketNodeInline(admin.TabularInline):
    model = BracketNode
    extra = 0

@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ("name", "format", "state", "champion", "created_at")
    inlines = (TournamentParticipantInline, BracketNodeInline)

@admin.register(TournamentParticipant)
class TournamentParticipantAdmin(admin.ModelAdmin):
    list_display = ("tournament", "seed", "team")

@admin.register(BracketNode)
class BracketNodeAdmin(admin.ModelAdmin):
    list_display = ("tournament", "bracket_round", "position", "team_a", "team_b", "is_bye", "winner")
```

No existing registration modified.

---

## 8. Seam boundaries (what crosses, what's internal)

**View → pure module (`matches/bracket.py`):** plain ints/floats/dicts ONLY.
- `default_seed_order` receives `list[tuple[int, float]]` `(team_id,
  mean_overall_rating)`; the view builds the rating from `Team.active_players`
  + `Player.overall_rating` (`mean(p.overall_rating for p in
  team.active_players) if team.active_players else 0.0` — the LG-01c
  draft-preview formula verbatim).
- `build_bracket` receives `list[ParticipantSpec]` (`team_id`, `seed`).
- `find_next_node` / `advance_winner` / `resolve_bye_chain` receive **flattened
  node dicts** built by a private view/model helper `_node_to_dict(node:
  BracketNode) -> dict` with keys: `bracket_round`, `position`, `team_a_id`,
  `team_b_id`, `seed_a`, `seed_b`, `is_bye`, `match_id`, `winner_id`,
  `advances_to` (tuple `(bracket_round, position)` of `advances_to`, or `None`),
  `advances_to_slot`.
- `break_tie` receives four ints.

**Pure module → view:** `BracketNodeSpec` / `ParticipantSpec` dataclasses,
`list[int]` (seed order), and parent-mutation dicts
(`{"bracket_round", "position", "slot", "team_id", "seed"}`). The view applies
mutations to the ORM. **The pure module NEVER imports or returns a Django
object.**

**What the Tests agent asserts against:**
- Pure-module **outputs** (`build_bracket` node-spec lists for N=4/5/8/16;
  `find_next_node` ordering; `advance_winner` / `resolve_bye_chain` mutation
  lists; `break_tie` truth table; `default_seed_order` ordering + tiebreak;
  `TestNoDjangoImportsLeaked`).
- View **DOM ids + HTTP status** (200/302/404/405) and **state transitions**
  (setup→active→completed) and **champion stamping**.
- Model **constraints** (unique seed, unique team, unique round/position),
  `lock_and_build` `ValidationError` on N<4, tie-break end-to-end via a
  deterministic seeded sim.

**What is internal (NOT asserted across the seam):** the exact A* / circle-method
internals; the `_node_to_dict` flattening helper shape beyond its documented keys;
the in-memory parent-mutation application order.

---

## 9. Test files (project `matches/tests/test_*.py` convention)

- **`matches/tests/test_bracket.py`** — pure-unit (no DB, no Django). Classes:
  `TestDefaultSeedOrder`, `TestBuildBracketPowerOfTwo` (N=4, 8, 16),
  `TestBuildBracketWithByes` (N=5, 6, 12 — byes for top seeds, standard
  1vN pairing), `TestFindNextNode`, `TestAdvanceWinner`, `TestResolveByeChain`,
  `TestBreakTie` (higher best-round-score wins; tie → lower seed int wins),
  `TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk).
- **`matches/tests/test_tournament_models.py`** — Django `TestCase`. Classes:
  `TestTournamentModel` (state defaults, `is_locked`, `__str__`),
  `TestTournamentLockAndBuild` (setup→active, `ValidationError` on N<4, nodes
  persisted, byes pre-resolved), `TestTournamentParticipantConstraints` (unique
  seed, unique team), `TestBracketNodeConstraints` (unique round/position),
  `TestFindNextPlayableNode`.
- **`matches/tests/test_tournament_views.py`** — Django `TestCase`. Classes:
  `TestTournamentList`, `TestTournamentCreate` (GET form, POST select-existing,
  POST generate-new via real `_generate_teams` — NO `mock.patch` on it so
  signature drift fails loudly), `TestTournamentDetail` (DOM ids, setup vs active
  vs completed rendering), `TestTournamentReseed` (persists, rejected once
  locked), `TestTournamentLock` (transition + ValidationError path),
  `TestTournamentPlayNext` (sims one Match, advances winner, tie-break path with a
  forced-tie fixture, champion stamped on final). Use small brackets (N=4) with
  `BatchSimulator.ROUND_TICKS` patched small for speed; assert on schema-level
  outcomes + DOM, NOT exact point totals.

---

## 10. Scope-out (LOCKED — all DEFERRED, do NOT build)

- **CSV import of participants** → LG-02a-2.
- **Async "play-all" / Celery task** → LG-02a-2. LG-02a ships ONLY the
  synchronous game-by-game `tournament_play_next` POST.
- **Series / best-of-N nodes** → deferred (a node is exactly one 2-round `Match`).
- **Double-elimination / round-robin / Swiss** → never (format enum extensible,
  only `single_elimination` ships).
- **In-League / in-Season embedding** → Tournament is standalone, `season`-less,
  decoupled.
- **Batch-N tournament simulation** → out of scope.
- **No `simulate_scheduled_round` / `simulate_match` change** — the existing
  `BatchSimulator.simulate_match(team_red, team_blue, match_type=..., *,
  arena_map=None)` is consumed verbatim.
- **No arena-map config per Tournament** (every node sims with `arena_map=None`,
  3-zone fallback — a per-Tournament map config is a future task).
- **No backfill, no `RunPython`** (ADR-0004 precedent — new models only).
- **No CONTEXT.md term beyond the 8 `### Tournaments` entries; no ADR.**

---

## 11. Locked-names index (quick reference)

Models: `matches.models.Tournament` / `TournamentParticipant` / `BracketNode`;
related names `participants` / `nodes` / `feeders` / `tournaments_won` /
`bracket_node`; `Tournament` methods `lock_and_build` / `is_locked` /
`find_next_playable_node`; `Tournament.champion` FK; `TournamentParticipant.seed`;
`BracketNode` fields `bracket_round` / `position` / `team_a` / `team_b` /
`seed_a` / `seed_b` / `match` / `advances_to` / `advances_to_slot` / `is_bye` /
`winner`; constraints `uniq_tournament_seed` / `uniq_tournament_team` /
`uniq_tournament_round_position`.

Pure module: `matches/bracket.py`; dataclasses `BracketNodeSpec` /
`ParticipantSpec`; functions `default_seed_order` / `build_bracket` /
`find_next_node` / `advance_winner` / `resolve_bye_chain` / `break_tie`;
format enum value `"single_elimination"`; state enum values `"setup"` /
`"active"` / `"completed"`.

Views: `matches/tournament_views.py`; `tournament_list` / `tournament_create` /
`tournament_detail` / `tournament_reseed` / `tournament_lock` /
`tournament_play_next`; helper `_node_to_dict`; cross-app import
`from teams.views import _generate_teams`.

URLs: `matches/tournament_urls.py`; names `tournament_list` /
`tournament_create` / `tournament_detail` / `tournament_reseed` /
`tournament_lock` / `tournament_play_next`; mount
`path("tournaments/", include("matches.tournament_urls"))`.

Templates: `templates/matches/tournament_list.html` /
`tournament_create.html` / `tournament_detail.html`. DOM ids:
`tournament-list-table` / `tournament-list-empty` / `tournament-create-link` /
`tournament-create-form` / `tournament-create-name` /
`tournament-create-team-select` / `tournament-create-generate-count` /
`tournament-create-generate-ppt` / `tournament-create-submit` /
`tournament-create-no-teams-notice` / `tournament-bracket` /
`tournament-bracket-round-{n}` / `tournament-node-{bracket_round}-{position}` /
`tournament-seeding-form` / `tournament-seed-input-{team_id}` /
`tournament-seeding-submit` / `tournament-lock-form` /
`tournament-lock-submit` / `tournament-play-next-form` /
`tournament-play-next-submit` / `tournament-champion-banner` /
`tournament-detail-empty`. CSS-class substrings `state-badge` / `bye-node`.

base.html: DOM id `tournaments-nav-link`, label `Tournaments`, in the
`{% elif app_mode == "sandbox" %}` branch.

Admin: `TournamentAdmin` / `TournamentParticipantAdmin` / `BracketNodeAdmin` +
inlines `TournamentParticipantInline` / `BracketNodeInline`.

Migration: `matches/migrations/0032_tournament.py`.

Test files: `matches/tests/test_bracket.py` /
`matches/tests/test_tournament_models.py` /
`matches/tests/test_tournament_views.py`.
