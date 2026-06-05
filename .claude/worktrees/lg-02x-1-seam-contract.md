# LG-02x-1 — Random Draw player-pool Tournament — SEAM CONTRACT

Single source of truth for the three parallel agents (code / tests / docs). Every
new model field, method name, signature, dict key, flag literal, DOM id, URL name,
and migration filename is pinned here. **No production code, tests, or docs in this
file — it is the contract only.**

Branch: `lg-02x-1-random-draw`. Builds on the shipped LG-02c RR→DE bracket
(`Tournament.format == "round_robin_double_elim"`), LG-02a/a-2 intake
(select-existing + `_generate_teams` + CSV `RosterImportForm`), and the SIM-09
`BatchSimulator.simulate_match` two-round entry point.

**Orthogonality principle (the spine of this feature):** `team_assembly` is a NEW
orthogonal field, NOT a new `format`. `format` stays `"round_robin_double_elim"`,
so every RR→DE path (`lock_and_build`, `_persist_elim_specs`,
`round_robin_standings`, `build_de_finals_if_rr_finished`, `play_next_node`,
`stage_progress`, the detail crosstable/cut-labels) is **untouched**. Pool intake,
the draw, the relaxed roster rule, and per-Round dynamic roles all key off
`team_assembly == "random_draw"`.

---

## 1. Model changes

### 1a. `Tournament` new fields (`matches/models.py`)

```python
TEAM_ASSEMBLY_CHOICES = (
    ("preset", "Preset teams"),
    ("random_draw", "Random draw player pool"),
)
team_assembly = models.CharField(
    max_length=16, choices=TEAM_ASSEMBLY_CHOICES, default="preset"
)

ROLE_ASSIGNMENT_CHOICES = (
    ("random", "Random per team per Round"),
    ("per_tier", "Per-tier bijection (both teams)"),
)
role_assignment_mode = models.CharField(
    max_length=16, choices=ROLE_ASSIGNMENT_CHOICES, default="random"
)
```

- `team_assembly` default `"preset"` ⇒ every existing Tournament is `preset`,
  byte-unchanged behaviour. Create-time only; meaningful for any `format`, but the
  pool/draw/per-Round-roles machinery fires only when `== "random_draw"`.
- `role_assignment_mode` default `"random"`. **Meaningful only when
  `team_assembly == "random_draw"`** (ignored for `preset`). Create-time only.
- Declare both **immediately after** the existing `wb_advancers` / `lb_advancers`
  / `swiss_rounds` block, before `created_at` / `champion`. No choices-less ints —
  both carry `choices`.

### 1b. `Team` new field (`teams/models.py`)

```python
is_draw_team = models.BooleanField(default=False)
```

- **NO FK to Tournament / Match** (avoids a `teams → matches` dependency
  inversion — the durable link lives on `TournamentPlayerEntry` instead). A drawn
  Team is identified by this flag; the Tournament owns the relationship via
  `TournamentParticipant` + `TournamentPlayerEntry`.
- Existing rows take `default=False` (no backfill — ADR-0004 disposable-data
  precedent).

### 1c. NEW model `TournamentPlayerEntry` (`matches/models.py`, after `BracketNode` / `SeriesMatch`)

The durable **pool registration AND draw result** — the source of truth for
`(player, tier, drawn_team)`. A drawn Team's `slot_*` FKs hold only the transient
per-Round role assignment; the (player, tier, team) truth lives here.

```python
class TournamentPlayerEntry(models.Model):
    tournament = models.ForeignKey(
        "matches.Tournament",
        on_delete=models.CASCADE,
        related_name="player_entries",
    )
    player = models.ForeignKey(
        "teams.Player",
        on_delete=models.CASCADE,
        related_name="tournament_entries",
    )
    tier = models.PositiveSmallIntegerField(null=True, blank=True)  # 1..6, null pre-draw
    drawn_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drawn_player_entries",
    )

    class Meta:
        ordering = ["tournament_id", "tier", "player_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "player"],
                name="uniq_tournament_player_entry",
            )
        ]
```

- `tournament` **CASCADE** (deleting a Tournament drops its pool).
- `player` **CASCADE** — a deleted Player drops its pool entries.
- `drawn_team` **SET_NULL**, nullable — a drawn Team deleted out of band leaves the
  entry's tier intact (defensive; the team can be re-derivable from
  `TournamentParticipant`).
- `tier` nullable: `null` after pool intake / before the draw; `1..6` after the
  draw (tier 1 = strongest band).
- `unique(tournament, player)` is the structural guarantee a Player cannot sit in
  two drawn Teams of the **same** Tournament; across **different** Tournaments a
  Player may belong to many drawn Teams (ownership/sharing rule §8 of the ask).
- `Meta.ordering = ["tournament_id", "tier", "player_id"]` so iteration is
  deterministic (tier-ascending, player-id tiebreak — matches the draw's
  tiebreak).

### 1d. `Team.roster_errors` relaxation (`teams/models.py`, ~line 192)

The "all players belong to this team" check is relaxed for draw teams ONLY:

```python
# Check that all players belong to team — RELAXED for draw teams
# (drawn Teams reference borrowed Players via slot FKs; ownership lives on
# TournamentPlayerEntry, and Player.team is never reassigned by the draw).
if not self.is_draw_team:
    for player, role, slot_name in filled:
        if player.team_id != self.pk:
            errors.append(f"{player.name} does not belong to this team")
```

- **Exact condition:** wrap the existing `for player, role, slot_name in filled:`
  belongs-to-team loop (the `player.team_id != self.pk` check) in
  `if not self.is_draw_team:`.
- **Keep unchanged** for draw teams: the all-6-slots-filled check, the
  duplicate-player check, and the role-distribution (Scout-only-twice) check. Only
  the ownership check is relaxed.

### 1e. Migrations

- **`matches/migrations/0040_tournament_random_draw.py`** — dep
  `0039_tournament_swiss`. Ops in pinned order:
  `AddField(Tournament.team_assembly)` → `AddField(Tournament.role_assignment_mode)`
  → `CreateModel(TournamentPlayerEntry)` (incl. the `UniqueConstraint` +
  `Meta.ordering`). **No `RunPython`, no backfill** (ADR-0004).
- **`teams/migrations/00XX_team_is_draw_team.py`** — next sequential `teams`
  migration number (Code agent: run `makemigrations teams` to resolve the exact
  prefix; single `AddField(Team.is_draw_team)`, no `RunPython`). The `matches`
  `0040` migration depends on this `teams` migration (cross-app dependency, since
  `TournamentPlayerEntry.drawn_team` and the draw both reference the new `Team`
  field — add the dependency tuple to `0040`).

---

## 2. Pure module — `matches/draw.py` (NEW)

Tier-balanced draw math + the two role-assignment-mode bijection builders.
**Pure Python, no Django / ORM / I/O / logging.** Frozen import allowlist:
`dataclasses`, `typing`, `random`, `collections` (NO `django.*`, NO `datetime`, NO
file I/O). Defended by `TestNoDjangoImportsLeaked` (subprocess fresh-import +
`sys.modules` walk — mirrors `matches/bracket.py` / `matches/standings.py` /
`matches/schedule_generator.py`).

> `random` is allowed in the allowlist because the role-assignment builders consume
> an injected `random.Random` (the per-Round role draw); the **draw computation
> itself consumes NO RNG** (deterministic straight-tiers + greedy balance).

### 2a. Constants / dataclass

```python
ROLE_SLOTS: tuple[str, ...] = (
    "commander", "heavy", "scout_1", "scout_2", "medic", "ammo"
)   # the 6 Team.slot_* suffixes, fixed order

@dataclass(frozen=True)
class DrawnTeamPlan:
    team_index: int            # 0-based, draw order (drawn team N)
    player_ids: tuple[int, ...]   # the 6 player ids assigned to this team, tier 1..6 order
    tiers: tuple[int, ...]        # parallel to player_ids: the tier (1..6) of each
```

### 2b. Draw computation

```python
def compute_draw(
    pool: list[tuple[int, float]],
) -> list[DrawnTeamPlan]:
    """STRAIGHT TIERS + GREEDY BALANCE. Deterministic — consumes NO RNG.

    `pool` is a list of (player_id, overall_rating). Precondition (caller-validated):
    len(pool) % 6 == 0 and len(pool) >= 24 (>= 4 teams). Raises ValueError otherwise.

    Algorithm:
      1. Sort pool by overall_rating DESC, then player_id ASC (tiebreak).
      2. T = len(pool) // 6 teams. Form 6 contiguous tiers of T players each
         (tier 1 = the strongest band = first T, ..., tier 6 = weakest band).
      3. For each tier 1..6, in order: assign the strongest-remaining tier player
         to the currently-weakest team (lowest running total rating; team_index
         ASC tiebreak when totals are equal). One player per team per tier.
      4. Return one DrawnTeamPlan per team (team_index 0..T-1), player_ids/tiers
         ordered tier 1..6.

    Idempotent: same pool -> identical output (a re-roll is a no-op; admin
    hand-edits are the variation mechanism)."""
```

- Returns `len(pool) // 6` `DrawnTeamPlan`s.
- The "currently-weakest team" running total uses the players assigned so far
  across all already-processed tiers (greedy snake-style balance).

### 2c. Role-assignment bijection builders

```python
def build_random_role_assignment(
    tier_player_ids: list[int],
    rng: random.Random,
) -> dict[str, int]:
    """`random` mode, per TEAM independently. `tier_player_ids` is the team's 6
    player ids in tier order (index 0 = tier 1 .. index 5 = tier 6). Shuffle the
    6 ids into the 6 ROLE_SLOTS. Returns {slot_suffix: player_id} over all 6
    ROLE_SLOTS. Consumes the injected rng (one shuffle)."""

def build_per_tier_role_assignment(
    rng: random.Random,
) -> dict[int, str]:
    """`per_tier` mode. Draw ONE tier->slot bijection for the Round, applied to
    BOTH teams (so equal-tier players play the same role). Returns
    {tier (1..6): slot_suffix} — a permutation of ROLE_SLOTS keyed by tier.
    Consumes the injected rng (one shuffle). The caller applies it to each team's
    tier->player map to produce that team's {slot_suffix: player_id}."""
```

- `random` mode: each team gets its own `build_random_role_assignment` call (two
  independent shuffles per Round).
- `per_tier` mode: ONE `build_per_tier_role_assignment` call per Round; the closure
  applies the single `{tier: slot}` bijection to both teams' tier→player maps.
- Both consume a **fresh per-Round RNG** (see §3 — tournament sims are
  non-deterministic, so no SIM-07/08 seed-chain interaction).

---

## 3. Simulator seam — `BatchSimulator.simulate_match` (`matches/simulation/entrypoints.py`)

### 3a. New signature (additive, keyword-only)

```python
def simulate_match(
    self,
    team_red,
    team_blue,
    match_type: str = "friendly",
    *,
    arena_map=None,
    before_round_hook=None,   # NEW — Optional[Callable[[int, Team, Team], None]]
) -> Match:
```

- `before_round_hook` default `None` ⇒ **byte-unchanged** for every existing caller
  (preset tournaments, sandbox, season play). When `None`, no hook is invoked.
- Callable signature: **`before_round_hook(round_number: int, team_red, team_blue)
  -> None`**. `round_number` is `1` or `2`; `team_red` / `team_blue` are the Team
  objects **as passed into that round's internal `_simulate_and_flush_round` call**
  (i.e. round 2 receives the swapped order — see insertion points). The hook
  mutates the drawn Teams' `slot_*` FKs **in memory** before the round simulates.

### 3b. Two insertion points (exact)

`simulate_match` runs two rounds via `_simulate_and_flush_round` at the lines pinned
below (current `entrypoints.py`):

- **Before round 1** — insert immediately after `match = Match.objects.create(...)`
  (line ~643) and **before** the round-1 `self._simulate_and_flush_round(team_red,
  team_blue, match=match, round_number=1, ...)` call (line ~646):
  ```python
  if before_round_hook is not None:
      before_round_hook(1, team_red, team_blue)
  ```
- **Before round 2** — insert after the round-1 column copy block and **before** the
  round-2 `self._simulate_and_flush_round(team_blue, team_red, match=match,
  round_number=2, ...)` call (line ~664). **Pass the same argument order
  `simulate_match` uses for round 2 (`team_blue, team_red`)** so the hook sees the
  physical red/blue Teams for that round:
  ```python
  if before_round_hook is not None:
      before_round_hook(2, team_blue, team_red)
  ```

- The hook fires **once per round**, so the 2 Rounds of one Match get **independent**
  role assignments (re-draw every Round). `_simulate_and_flush_round` reads
  `team.active_roster` off the (now-mutated) `slot_*` FKs, so the in-memory rewrite
  takes effect for that round's sim. **No change to `_simulate_and_flush_round`
  itself.**

---

## 4. Engine seam — `play_next_node` (`matches/tournament_engine.py`)

`play_next_node` currently calls (line ~41):
```python
match = BatchSimulator().simulate_match(
    node.team_a, node.team_b, match_type="tournament"
)
```

### 4a. Draw branch

Replace that single call with a `team_assembly`-keyed branch:

```python
if tournament.team_assembly == "random_draw":
    hook = _build_role_hook(tournament)   # NEW module-level helper, this file
    match = BatchSimulator().simulate_match(
        node.team_a, node.team_b, match_type="tournament",
        before_round_hook=hook,
    )
else:
    match = BatchSimulator().simulate_match(
        node.team_a, node.team_b, match_type="tournament"
    )
```

- The `else` branch is **byte-identical** to today (preset path unchanged). Every
  other line of `play_next_node` (the per-Match-atomic body, clinch, advance, RR /
  Swiss / RR→DE guards, crown) is **untouched**.

### 4b. `_build_role_hook(tournament) -> Callable` (NEW, `matches/tournament_engine.py`)

Builds the closure passed as `before_round_hook`. Responsibilities:

- Reads `tournament.role_assignment_mode` (`"random"` or `"per_tier"`).
- The closure `(round_number, team_red, team_blue)`:
  1. For each of the two drawn Teams, load its `TournamentPlayerEntry` rows
     (`tournament.player_entries.filter(drawn_team=team)`), build the team's
     tier→player_id map (tier 1..6).
  2. Draw a **fresh per-Round RNG** — `rng = random.Random()` (default OS entropy,
     fresh every call; tournament sims are non-deterministic).
  3. **`random` mode:** call `build_random_role_assignment(team_tier_ids, rng)`
     **per team independently** (so each team shuffles its own 6 tier-players into
     the 6 role slots).
     **`per_tier` mode:** call `build_per_tier_role_assignment(rng)` **once** and
     apply that single `{tier: slot}` bijection to BOTH teams' tier→player maps
     (equal-tier players play the same role both sides).
  4. Rewrite **both** drawn Teams' `slot_*` FKs **in memory** from the resulting
     `{slot_suffix: player_id}` maps (set `team.slot_commander_id`,
     `team.slot_heavy_id`, `team.slot_scout_1_id`, `team.slot_scout_2_id`,
     `team.slot_medic_id`, `team.slot_ammo_id`). **In-memory only — no `.save()`**
     (the per-Round assignment is transient; the durable truth is the
     `TournamentPlayerEntry` tier + `drawn_team`).
- Roles re-draw **every Round** (the hook is called once per round by
  `simulate_match`, so each of the 2 Rounds gets an independent assignment).
- **Non-determinism note:** the role draw consumes a fresh `random.Random()` (NOT
  the SIM-07 seed chain). No SIM-07/SIM-08 interaction, no Score Calibration
  re-baseline.

---

## 5. Views / URLs (`matches/tournament_views.py`, `matches/tournament_urls.py`)

All new URL names are **bare** (no `app_name`, mounted at `/tournaments/`).

### 5a. CHANGED — `tournament_create` (existing view)

- Read a new POST field **`team_assembly`** (forgiving-fallback: only `"preset"` /
  `"random_draw"`, else `"preset"`) and **`role_assignment_mode`** (only `"random"`
  / `"per_tier"`, else `"random"`); stamp both via
  `Tournament.objects.create(..., team_assembly=..., role_assignment_mode=...)`.
- The `format` select is **unchanged** — a `random_draw` Tournament uses
  `format="round_robin_double_elim"` (the wb/lb combo `rrde_combo` select is reused
  verbatim, validated by the existing LG-02c RR→DE lock-time check).
- The existing select-existing-teams and `_generate_teams` participant paths stay
  for `preset`. For `random_draw`, participants are NOT chosen at create time — the
  Tournament is created in `setup` with an **empty** pool, and the user fills the
  pool + runs the draw on the detail page (pool intake views below).
- NEW create-form DOM ids: `tournament-create-team-assembly` (the
  `<select name="team_assembly">`), `tournament-create-role-assignment-mode` (the
  `<select name="role_assignment_mode">`). Both shown client-side via the existing
  `tournamentCreateToggle` JS (role-assignment-mode shown only when
  `team_assembly == "random_draw"`; behaviour pinned, exact JS at Code agent's
  discretion). Every existing create-form id is unchanged.

### 5b. NEW — pool intake (three sources), draw, re-roll, hand-edit

Mirror the LG-02a/a-2 Team-intake at **Player** granularity. All POST,
`@transaction.atomic`, setup-only (reject with `messages.error` + redirect once
`tournament.is_locked`). All create `TournamentPlayerEntry` rows (tier `null`,
`drawn_team` `null` until the draw). Generated/CSV Players are created on the Free
Agents Team via `teams.models.get_free_agents_team()`.

| URL name | Path | Method | View fn | What it does |
|---|---|---|---|---|
| `tournament_pool_add_existing` | `<id>/pool/add-existing/` | POST | `tournament_pool_add_existing` | Add selected existing Players (multi-select of `Player.objects.all()`) as pool entries. |
| `tournament_pool_generate` | `<id>/pool/generate/` | POST | `tournament_pool_generate` | Generate N fresh Players via the LG-00 pure generator (`draw_stats` / `draw_preferred_roles`), created on the Free Agents Team, added as pool entries. |
| `tournament_pool_import` | `<id>/pool/import/` | POST | `tournament_pool_import` | CSV import via the LG-00b `RosterImportForm` + `parse_roster_csv`; **each CSV row = one pool Player** (team-grouping IGNORED — see §5c). Players created on the Free Agents Team, added as pool entries. |
| `tournament_pool_remove` | `<id>/pool/remove/` | POST | `tournament_pool_remove` | Remove a pool entry (by `player_id` / entry id) while in setup. |
| `tournament_draw` | `<id>/draw/` | POST | `tournament_draw` | Run the draw: validate pool size, build drawn Teams + participants, persist (see §5d). Re-runnable (re-roll) while setup. |
| `tournament_draw_edit` | `<id>/draw/edit/` | POST | `tournament_draw_edit` | Admin hand-edit of a drawn entry's `tier` / `drawn_team` (the variation mechanism; the draw itself is deterministic). |

- Insert these path entries into `matches/tournament_urls.py` **before** the
  existing `<int:tournament_id>/` detail catch route is irrelevant (detail uses a
  trailing-segment match), but place them adjacent to the other
  `<int:tournament_id>/...` routes (after `import-participants/`).
- The existing `tournament_lock` view (URL `tournament_lock`) is **reused
  unchanged** to reach `active`: it calls `tournament.lock_and_build()`, which
  takes the existing RR→DE branch over the drawn Teams (now `TournamentParticipant`
  rows). **No `lock_and_build` change** — the draw must have produced participants +
  drawn Teams before lock; lock validates `>= 4` participants as today.

### 5c. CSV-for-a-player-pool reconciliation

The LG-00b / LG-02a-2 CSV path (`parse_roster_csv`) groups rows by team
(`ParsedRoster.by_team`). For a **player pool** we need players, not team-grouped
rosters. `tournament_pool_import` therefore:

- Calls `parse_roster_csv(decoded_text)` (reuse — header validation, per-row
  coercion, bundled `RosterImportError`).
- **Ignores `by_team` grouping** — iterates `parsed.rows` (the flat CSV-order list)
  and treats **each `ParsedRow` as one pool Player**: creates a Player on the Free
  Agents Team from `row.profile` + `row.stats` + `row.preferred_roles`
  (`Player.objects.create(team=get_free_agents_team(), name=row.name,
  preferred_roles=row.preferred_roles, **row.profile, **row.stats)`), then a
  `TournamentPlayerEntry`. The CSV `role` column (slot intent) and the CSV `team`
  column are **not used** for the pool (slots are assigned per-Round by the draw;
  team membership is the pool, not the CSV team). Does **NOT** call
  `_check_db_slot_collisions` / `_apply_roster` (those are roster-slot helpers
  irrelevant to a flat player pool).
- Error branch: `transaction.set_rollback(True)` + re-render the detail page (HTTP
  200) with the bound form + `exc.errors` (mirrors `tournament_import_participants`).

### 5d. `tournament_draw` persistence (exact)

1. Validate pool: `N = tournament.player_entries.count()`; require `N % 6 == 0` and
   `N >= 24` (≥ 4 teams) — else `messages.error` + redirect (no writes).
2. Build the `(player_id, overall_rating)` pool list from the pool entries
   (`entry.player.overall_rating`), call `compute_draw(pool)` (pure).
3. **Re-roll cleanup:** if drawn Teams already exist (re-roll), delete the prior
   drawn Teams (`is_draw_team=True` for this tournament) + their
   `TournamentParticipant` rows, and null the entries' `tier` / `drawn_team`
   (idempotent — `compute_draw` is deterministic so a re-roll reproduces the same
   split; admin hand-edits are the variation).
4. For each `DrawnTeamPlan`:
   - Create `team = Team.objects.create(name="<Draw Team N>", is_draw_team=True)`
     (naming at Code agent's discretion; pinned: `is_draw_team=True`).
   - Set the 6 `slot_*` FKs from the plan's tier-ordered player_ids using an
     **initial valid assignment** (e.g. tier order → `ROLE_SLOTS` order; any valid
     no-duplicate assignment satisfies the relaxed `roster_errors`); `team.save()`.
   - Create `TournamentParticipant(tournament=tournament, team=team, seed=N+1)`
     (seed = 1-based draw order / RR seed; mirrors LG-02a participant seeding).
   - Fill each member entry's `tier` + `drawn_team` (`drawn_team=team`).
5. **Does NOT reassign `Player.team`** — drawn Teams reference borrowed Players via
   slot FKs only; `PlayerRoundState` references the real Player so career stats stay
   unified.

### 5e. `_detail_context` additions

`_detail_context(tournament)` keeps its existing 14 keys verbatim and adds (for the
`random_draw` setup surface; empty/defaulted for `preset`):

- `team_assembly` (`str`) — `tournament.team_assembly`.
- `role_assignment_mode` (`str`) — `tournament.role_assignment_mode`.
- `pool_entries` (`list[TournamentPlayerEntry]`, `select_related("player",
  "drawn_team")`, ordered tier-then-player-id) — `[]` for preset.
- `pool_size` (`int`) — `len(pool_entries)`.
- `is_drawn` (`bool`) — `tournament.player_entries.filter(drawn_team__isnull=False).exists()`.
- `pool_import_form` (`RosterImportForm()`) and `pool_import_row_errors`
  (`list[RowError]`, default `[]`) — the player-pool CSV intake form + errors
  (parallel to the existing `import_form` / `import_row_errors`).

### 5f. Template (`templates/matches/tournament_detail.html`) — new surface

Rendered only when `team_assembly == "random_draw"`. Locked DOM ids:

- Pool intake (setup): `tournament-pool-section` (wrapper);
  `tournament-pool-add-existing-form` / `-select` / `-submit`;
  `tournament-pool-generate-form` / `-count` / `-mean` / `-std-dev` / `-submit`;
  `tournament-pool-import-form` / `-file` / `-submit` / `-template-link` / `-errors`
  + per-row `tournament-pool-import-error-{row_num}-{field|"row"}`;
  `tournament-pool-table` (lists pool entries; one row
  `tournament-pool-entry-{player_id}` each) with per-entry remove control
  (`tournament-pool-remove-{player_id}`); `tournament-pool-size`
  (renders `pool_size`); `tournament-pool-invalid-notice` (shown when
  `pool_size % 6 != 0 or pool_size < 24`, with the substring `divisible by 6` /
  `at least 24`).
- Draw: `tournament-draw-form` / `-submit` (the "Draw teams" button, enabled only
  when pool size valid + setup); `tournament-draw-reroll-submit` (re-roll, same
  endpoint, shown once drawn); `tournament-draw-table` (the drawn-teams + tiers
  display, one section `tournament-draw-team-{team_id}` per drawn team, one row per
  member tagged with its `tier`); the hand-edit control
  `tournament-draw-edit-form` / per-entry `tournament-draw-edit-{player_id}`.
- Reused verbatim: the lock control (`tournament-lock-form` / `-submit`), play
  controls (`tournament-play-next-*` / `tournament-play-all-*`), and the
  champion banner (`tournament-champion-banner`). Once drawn + locked, the
  Tournament renders the existing RR→DE crosstable / cut-labels / DE-finals
  surfaces over the drawn Teams **unchanged**.

### 5g. Admin

`TournamentPlayerEntry` registered (`matches/admin.py`, after the existing
tournament admins) — `list_display = ("tournament", "player", "tier",
"drawn_team")`. The two new `Tournament` fields and `Team.is_draw_team` auto-surface
on the existing change forms; the existing inlines are reused. No existing
registration touched.

---

## 6. Owning module per name

| New name | File |
|---|---|
| `Tournament.team_assembly`, `Tournament.role_assignment_mode`, `TEAM_ASSEMBLY_CHOICES`, `ROLE_ASSIGNMENT_CHOICES` | `matches/models.py` |
| `TournamentPlayerEntry` model | `matches/models.py` |
| `Team.is_draw_team`; `roster_errors` relaxation | `teams/models.py` |
| `compute_draw`, `DrawnTeamPlan`, `ROLE_SLOTS`, `build_random_role_assignment`, `build_per_tier_role_assignment` | `matches/draw.py` (NEW pure module) |
| `simulate_match(before_round_hook=...)` seam + 2 insertion points | `matches/simulation/entrypoints.py` |
| `play_next_node` draw branch, `_build_role_hook` | `matches/tournament_engine.py` |
| `tournament_create` (changed), `tournament_pool_add_existing`, `tournament_pool_generate`, `tournament_pool_import`, `tournament_pool_remove`, `tournament_draw`, `tournament_draw_edit`, `_detail_context` additions | `matches/tournament_views.py` |
| 7 new URL names | `matches/tournament_urls.py` |
| pool/draw DOM ids + new surface | `templates/matches/tournament_detail.html` |
| `TournamentPlayerEntryAdmin` | `matches/admin.py` |
| migration `0040_tournament_random_draw` | `matches/migrations/` |
| migration `00XX_team_is_draw_team` | `teams/migrations/` |
| reused: `_generate_teams`, `get_free_agents_team`, `RosterImportForm`, `parse_roster_csv`, `RosterImportError`, `draw_stats`, `draw_preferred_roles` | (existing — `teams/views.py`, `teams/models.py`, `teams/forms.py`, `teams/roster_importer.py`, `teams/player_generator.py`) |

---

## 7. Test boundary

**Tests agent asserts against (public seam):**

- **Pure `matches/draw.py`** (`test_draw.py`, no DB): `compute_draw` — straight-tier
  formation, greedy-balance assignment to the weakest team, deterministic /
  idempotent (same pool → identical output, byte-equal across calls),
  rating-DESC + player-id-ASC sort, `ValueError` on `N % 6 != 0` and `N < 24`, N=24
  / 30 / 48 worked cases (team count = N/6, every team exactly 6, one player per
  tier per team); `build_random_role_assignment` (a permutation of all 6
  `ROLE_SLOTS`, consumes one rng shuffle, no duplicate slot/player);
  `build_per_tier_role_assignment` (a `{tier: slot}` bijection over `ROLE_SLOTS`,
  one rng shuffle); `TestNoDjangoImportsLeaked`.
- **Model rows / constraints** (`test_tournament_models.py` extend):
  `team_assembly` / `role_assignment_mode` choices + defaults;
  `TournamentPlayerEntry` create / `unique(tournament, player)` rejection /
  CASCADE on tournament delete / SET_NULL on team delete / `Meta.ordering`;
  `Team.is_draw_team` default + persistence.
- **Relaxed roster rule** (`teams/tests/test_models.py` extend): a draw team with
  borrowed Players (`player.team_id != team.pk`) has **no** "does not belong"
  error, but a draw team with a **duplicate** player OR a 3rd non-Scout role
  **still** errors; a non-draw team with a foreign player **still** errors.
- **Hook behaviour** (`test_simulation_view_paths.py` extend): `simulate_match`
  with `before_round_hook=None` is byte-unchanged (preset path); a hook is invoked
  once per round with `(round_number, team_red, team_blue)` and the round-2 call
  receives the swapped `(team_blue, team_red)` order; a hook that rewrites
  `slot_*` FKs changes the roster the round simulates against.
- **Engine draw branch** (`test_tournament_engine.py` extend): a `random_draw`
  Tournament routes `play_next_node` through the hook path (the hook is built +
  passed); a `preset` Tournament's `simulate_match` call is unchanged (no hook).
- **View DOM / status** (`test_tournament_views.py` extend): create-form
  `team_assembly` / `role_assignment_mode` selects + POST persistence + fallback;
  pool intake (existing / generate / CSV) creates `TournamentPlayerEntry` rows on
  the Free Agents Team; CSV error branch re-renders 200 with `exc.errors` and zero
  writes; `tournament_draw` validates `N % 6` / `N >= 24`, builds drawn Teams +
  participants + fills tier/drawn_team, re-roll is idempotent, hand-edit mutates a
  single entry; lock reached via the existing `tournament_lock` over drawn Teams;
  the new `_detail_context` keys + pool/draw DOM ids render.
- **Engine tasks** (`test_tournament_tasks.py` extend): `play_tournament_task`
  drains a `random_draw` RR→DE Tournament to a champion under
  `CELERY_TASK_ALWAYS_EAGER` (non-deterministic — assert champion stamped +
  `state="completed"`, never exact point totals).

**Internal (NOT asserted):** the exact in-memory FK rewrite mechanics inside the
closure, the drawn-Team naming string, the initial slot assignment chosen at draw
time (any valid no-duplicate assignment passes), per-Round RNG draw values, exact
Bootstrap CSS classes.

---

## 8. Locked literals

- `team_assembly` choices: **`"preset"`** (default) / **`"random_draw"`**.
- `role_assignment_mode` choices: **`"random"`** (default) / **`"per_tier"`**.
- `Team.is_draw_team` default **`False`**.
- `format` for a Random Draw Tournament stays **`"round_robin_double_elim"`** (NOT
  a new format value).
- **N-divisibility rule:** pool size `N` must satisfy `N % 6 == 0` AND `N >= 24`
  (≥ 4 teams); `tournament_draw` rejects otherwise.
- **Draw rule:** STRAIGHT TIERS + GREEDY BALANCE — sort by `overall_rating` DESC
  (player_id ASC tiebreak); 6 contiguous tiers of `T = N/6` (tier 1 = strongest);
  within each tier strongest-remaining → currently-weakest team (team_index ASC
  tiebreak). **Deterministic, consumes no RNG; re-roll idempotent; admin hand-edit
  is the variation mechanism.**
- **6 role slots** (fixed order): `commander, heavy, scout_1, scout_2, medic, ammo`
  (`ROLE_SLOTS`).
- **Per-Round role timing:** roles re-draw **every Round** (the 2 Rounds of one
  Match get independent assignments); `random` = each team shuffles its 6
  tier-players into the 6 slots independently; `per_tier` = one tier→slot bijection
  per Round applied to BOTH teams.
- **Non-determinism:** tournament sims draw fresh per-round seeds; the role draw
  consumes a fresh `random.Random()`. **No SIM-07 / SIM-08 interaction, NO Score
  Calibration re-baseline.**
- Migration filenames: **`matches/migrations/0040_tournament_random_draw.py`**
  (dep `0039_tournament_swiss` + the new `teams` migration) and
  **`teams/migrations/00XX_team_is_draw_team.py`** (Code agent resolves the exact
  prefix via `makemigrations teams`).

---

## 9. Scope-out (LOCKED — DEFERRED, do NOT build here)

- **Duos / Trios + a `TournamentSubGroup` model** — deferred to **LG-02x-2** (this
  contract is single-Player pool only).
- **No SIM-07 / SIM-08 interaction** — tournament sims are non-deterministic; the
  role draw uses fresh per-round RNG, not the seed chain.
- **No Score Calibration re-baseline** — no simulation *mechanics* change (the hook
  only swaps which Player occupies each role slot before a normal round).
- **No `Player.team` reassignment** by the draw — drawn Teams reference borrowed
  Players via slot FKs only.
- **No new `format` value** — `team_assembly` is orthogonal; `format` stays
  `"round_robin_double_elim"` and every RR→DE path is untouched.
- **No `simulate_match` / `_simulate_and_flush_round` mechanics change** beyond the
  additive `before_round_hook` kwarg + the two hook-invocation lines.
- **ADR-0022** records the tier-balanced draw + per-Round dynamic roles + relaxed
  draw-team roster ownership — **written separately by the Docs agent** (do NOT
  write it here; this contract only notes its existence).
