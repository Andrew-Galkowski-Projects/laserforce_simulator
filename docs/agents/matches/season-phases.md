# matches: Season phases (Part2a-2c-2)

SeasonPhase foundation, the create-league phase composer, the RR -> playoff embed, and multi-round-robin seasons.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## LG-02-Part2a season phase foundation

Introduces the persisted **`SeasonPhase`** model and retrofits the **Season**
read-path so the per-Season schedule is sourced through a single **chokepoint on
`Season`** instead of reading `Season.schedule_format` / calling
`generate_schedule(...)` inline at every site. This is the **foundation slice**
of LG-02-Part2 — it generalises the previously-implicit "a Season *is* a single
**round-robin**" assumption into an **ordered list of typed phases**. The
**LG-02-Part2 grill (2026-06-04)** resolved that this phase model **IS** the
LG-06 phased-lifecycle model — off-season / regular / tournament are *phase
types*, not a parallel abstraction. **Zero user-visible change** in Part2a: a
one-phase `round_robin` Season is byte-identical to today's Season, and existing
phase-less Seasons keep playing via the implicit-single-phase fallback. The
composer UI (Part2b), per-phase format (Part2b), the heterogeneous multi-phase
play loop + `SeasonPhase → Tournament` embed (Part2c), and the `member_night`
phase are all **scoped out** (see below). **NEW
[ADR-0023](../../adr/0023-season-phase-composable-structure.md)** records
the ordered-typed-phase decision, the no-backfill defensive fallback, and the
forward one-directional `SeasonPhase → Tournament` FK; the **Season phase**
GLOSSARY.md glossary term carries the domain language (do not edit). Seam
contract:
[`.claude/worktrees/lg-02-part2a-seam-contract.md`](../../../.claude/worktrees/lg-02-part2a-seam-contract.md).

**NEW model `SeasonPhase` (LOCKED) — `matches/models.py`, declared immediately
after the `Season` class and before `Tournament`.** One ordered unit of a
Season's structure. Fields: **`season`** (`FK("matches.Season",
on_delete=CASCADE, related_name="phases")`); **`ordinal`**
(`PositiveSmallIntegerField()` — **1-based** ordering within the Season, no
default, set explicitly at create); **`phase_type`** (`CharField(max_length=16,
choices=PHASE_TYPE_CHOICES, default="round_robin")` — `max_length=16` headroom
for the 12-char `"member_night"`). The class attribute **`PHASE_TYPE_CHOICES`**
declares **all three** values now even though only `round_robin` has behaviour
this slice: `(("round_robin", "Round-robin"), ("tournament", "Tournament"),
("member_night", "Member night"))`. `Meta.ordering = ["ordinal"]` and a
**`UniqueConstraint(fields=["season", "ordinal"],
name="uniq_season_phase_ordinal")`** — the structural guarantee that two phases
of the *same* Season cannot share an ordinal (the same ordinal is fine across
*different* Seasons). `__str__` (locked shape): `f"{self.season} — phase
{self.ordinal} ({self.phase_type})"` (em-dash U+2014, matching the
`Season.__str__` convention). **NO FK to `Tournament`** and **NO per-phase
`schedule_format`** field this slice — the `round_robin` phase resolves fixtures
via the legacy `Season.schedule_format` (which **stays as-is**); both are
Part2b/Part2c.

**Migration `0041_season_phase` — `CreateModel`-only, NO `RunPython`.** Single
`CreateModel(SeasonPhase)` carrying the `UniqueConstraint` + `Meta.ordering`;
dependency `("matches", "0040_tournament_random_draw")`. **No `RunPython`, no
`RunSQL`, no backfill, no data migration** — the
[ADR-0004](../../adr/0004-simulation-data-is-disposable.md) disposable-data
precedent (same as the LG-01 `0029` and every prior `Season`/`Match` add).
Legacy phase-less Seasons rely on the in-memory fallback **forever** — they are
never backfilled with a row.

**Chokepoint on `Season` (LOCKED) — two read-only / pure-derivation methods (no
DB write, no RNG).** **`Season.ordered_phases() -> list[SeasonPhase]`** returns
the Season's phases in ordinal order: `list(self.phases.all())` when ≥ 1
persisted row exists (`Meta.ordering` guarantees order); when **zero** rows
exist it returns a **one-element list whose member is a real but UNSAVED
`SeasonPhase`** — the implicit fallback (see below). **`Season.scheduled_fixtures()
-> list[ScheduleFixture]`** returns the flat fixture list for the schedule: this
slice it is exactly the `round_robin` phase's list, sourced via
`generate_schedule(team_ids, self.schedule_format)` where `team_ids` follows the
existing, now-centralised rule — **draft** Season ⇒ `sorted(t.id for t in
season.teams.all())`; **active/completed** Season ⇒
`list(season.starting_team_ids_json or [])`. Exactly ONE `round_robin` phase
exists this slice (explicit or implicit), so the return is the single RR fixture
list — **NO cross-phase composition, NO matchday offsetting** (Part2c). Returns
**`[]`** when `len(team_ids) < 2` (mirrors the current per-site guard) — never
raises. `ScheduleFixture` is the existing frozen dataclass from
`matches/schedule_generator.py`, **consumed verbatim**. `scheduled_fixtures()`
is the **sole** `generate_schedule(...)` caller for the Season read-path after
this slice.

**Implicit-fallback representation (LOCKED).** The phase-less fallback is a
**real but UNSAVED `SeasonPhase` instance** — NOT a sentinel class, NOT a dict —
built as `SeasonPhase(season=self, ordinal=1, phase_type="round_robin")` with
**no `.save()`** (so `pk is None`; needs no DB row). Locked rationale: a real
instance keeps the downstream type uniform (`list[SeasonPhase]`), exposes
`.phase_type` / `.ordinal` / `.season` for Part2b/2c without a shim, and `pk is
None` is the unambiguous "implicit" marker a test asserts on. A private
`Season._implicit_phase()` builder is **Code-agent discretion** (only the
returned type + field values + `pk is None` are pinned).

**Read-path retrofit — every Season-read call site routes through the
chokepoint.** Each was an inline `generate_schedule(team_ids,
…schedule_format)`; each becomes `… = season.scheduled_fixtures()` (or
`self.scheduled_fixtures()`), preserving byte-identical output: **`Season._is_finished`**
(`models.py:1015` — the `< 2`-team / empty-fixtures early-`False` guard becomes
`if not fixtures: return False`; behaviour-equivalence is load-bearing — a
phase-less Season MUST return today's result); **`play_season_task`**
(`tasks.py:190` — downstream `played_keys` build / `select_play_fixtures` /
per-fixture `simulate_scheduled_round` loop unchanged); **`season_schedule`**
(`league_views.py:371`); **`_build_dashboard_context`** (`league_views.py:646`);
**`league_history`** Play-Week preview (`league_views.py:1512`);
**`team_schedule`** (`league_views.py:1822`). `Season.complete_if_finished`
(`models.py:984`) is **unchanged** (routes through `_is_finished`). The pure
module **`matches/season_dashboard.py`** (`find_next_fixture` / `round_progress`
/ `find_next_matchday` / `select_play_fixtures` / `compute_leaders` /
`LeaderRow`) is **NOT edited** — it stays pure (frozen no-Django import
allowlist, defended by `TestNoDjangoImportsLeaked`); the **caller** builds the
fixtures via `scheduled_fixtures()` and passes the list in, exactly as today.
**Deliberately NOT touched:** `Tournament.lock_and_build` (`models.py:1233`) and
`tournament_views.py:435` both call `generate_schedule` for a **standalone
Tournament**, not a Season — the chokepoint is Season-only.

**Create-on-Season-create — one explicit `round_robin` phase per new Season.**
Both `@transaction.atomic`-decorated draft-creation views gain one
`SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")`
**inside the existing atomic block, after the `Season.objects.create(...)`** (so
a rollback drops the phase too): **`league_create`** (`league_views.py`, after
the create at `:527`) and **`next_season`** (after the create at `:1900`). No
other site writes a Season this slice. **Admin-created Seasons get the fallback
for free** (zero rows ⇒ implicit phase) — no admin-side creation hook.

**Admin (`matches/admin.py`).** `SeasonPhase` is added to the model import and
registered after the existing `SeasonAdmin` (no existing registration touched):
`@admin.register(SeasonPhase)` `SeasonPhaseAdmin(list_display = ("season",
"ordinal", "phase_type"))`. An optional `SeasonPhaseInline(TabularInline,
model=SeasonPhase, extra=0)` on `SeasonAdmin.inlines` is **Code-agent
discretion** (the standalone registration is the mandatory part).

**Determinism.** **No simulator change**, **no RNG** consumed by any new code —
the model methods are pure derivations over the ORM + the existing deterministic
`generate_schedule`, which is a pure function of the *set* of `team_ids`, so
routing it through the chokepoint changes nothing about which fixtures are
produced. **No SIM-07 / SIM-08 contract interaction, NO Score Calibration
re-baseline.** The `BatchSimulator` and `generate_schedule` are consumed
verbatim.

**Scope-out (LOCKED — DO NOT build here).** **Composer UI** (picking/ordering
phases at create-League time) → Part2b. **Per-phase `schedule_format`** → none;
the RR phase reads `Season.schedule_format`; Part2b. **Multi-phase play loop** /
cross-phase matchday offsetting / per-phase standings scoping → Part2c
(`scheduled_fixtures()` returns exactly ONE RR list this slice).
**`SeasonPhase → Tournament` FK** + tournament embed → Part2b/2c.
**`member_night` / `tournament` phase behaviour** → declared in the enum, inert
this slice (only `round_robin` resolves fixtures); `member_night` is deferred to
its own sandbox grill (see PLAN.md). **GLOSSARY.md** ("Season phase" term) and
**ADR-0023** are already written.

**Tests:** `matches/tests/test_season_phase.py` (NEW — model fields / locked
types + defaults / `PHASE_TYPE_CHOICES` declares all three; `Meta.ordering ==
["ordinal"]` (phases inserted 2,1,3 iterate 1,2,3); the
`uniq_season_phase_ordinal` constraint rejects a duplicate `(season, ordinal)`
but allows the same ordinal across different Seasons; `season.phases` reverse
accessor + CASCADE delete; `ordered_phases()` returns explicit phases in ordinal
order, and a phase-less Season returns a one-element list whose member is an
**unsaved** `SeasonPhase` with `pk is None`, `phase_type == "round_robin"`,
`ordinal == 1`, `season == self`; the **behaviour-equivalence guarantee** — a
Season with one explicit `round_robin` phase vs an otherwise-identical
phase-less Season return the **identical** `scheduled_fixtures()` list and
produce the **identical** `_is_finished()` / `complete_if_finished()` outcome
(same state flip, same `champion_team` id) over hand-built `Match`/`GameRound`
rows — asserting on schema-level outcomes, **not** simulated point totals;
`scheduled_fixtures()` returns `[]` for a `< 2`-team Season without raising).
`matches/tests/test_league_create.py` (extend — a successful `league_create`
POST creates the Season AND exactly one `SeasonPhase(ordinal=1,
phase_type="round_robin")`; the existing rollback test leaves **zero**
`SeasonPhase` rows). `matches/tests/test_league_next_season.py` (extend —
`next_season` creates the new draft Season AND its one `round_robin` phase).
`matches/tests/views_tests.py` (extend — read-path equivalence at the view
layer: the rendered schedule/dashboard for a phase-less Season is byte-identical
to one with an explicit RR phase). `matches/tests/test_league_play.py` (extend —
`play_season_task` over a phase-less Season plays the same fixtures it does
today, under the existing `CELERY_TASK_ALWAYS_EAGER` conftest). Tests assert on
persisted row shapes, the constraint, ordering, the fallback representation, and
behaviour-equivalence — **never** on exact simulated point totals.

**Locked names:** see the seam contract
[`.claude/worktrees/lg-02-part2a-seam-contract.md`](../../../.claude/worktrees/lg-02-part2a-seam-contract.md)
for the authoritative list — `SeasonPhase` (fields `season` /
`FK(Season, CASCADE, related_name="phases")`, `ordinal` /
`PositiveSmallIntegerField` 1-based, `phase_type` / `CharField(max_length=16,
default="round_robin")`), `PHASE_TYPE_CHOICES` (all three values),
`Meta.ordering = ["ordinal"]` + `uniq_season_phase_ordinal`, the `Season.phases`
reverse accessor, the chokepoint `Season.ordered_phases()` /
`Season.scheduled_fixtures()`, the unsaved-`SeasonPhase` implicit fallback
(optional `Season._implicit_phase()`), the two `SeasonPhase.objects.create(...)`
creation sites (`league_create` / `next_season`), `SeasonPhaseAdmin`
(+ optional `SeasonPhaseInline`), and the migration `0041_season_phase` (dep
`0040_tournament_random_draw`, `CreateModel`-only).

## LG-02-Part2b create-league phase composer

Builds on the Part2a foundation by giving the **create-League** surface a
vanilla-JS **"+" composer** that writes **multiple ordered `SeasonPhase` rows**
(versus Part2a's single auto-created `round_robin` phase), plus two **dormant**
`SeasonPhase` columns and a new **pure** parsing module. A Season's structure is
now author-composable as an ordered list of phase *types* (RR / Tournament) at
create time; the composer serializes the ordered rows into a hidden wire-format
field, the form's `clean()` parses it through the pure module, and **both**
`SeasonPhase`-creation sites (`league_create`, `next_season`) loop over the
parsed specs. **The read-path is UNCHANGED** — the Part2a chokepoint
`Season.scheduled_fixtures()` still calls `generate_schedule(team_ids,
Season.schedule_format)` and **ignores the phase rows**: it plays the first
`round_robin` phase and treats `tournament` phases as invisible. Part2b *writes*
more rows but *reads* none of them. **No simulator change, no RNG, no SIM-07 /
SIM-08 interaction, NO Score Calibration re-baseline, no read-path / chokepoint
change.** The forward `SeasonPhase → Tournament` FK column lands here but is
**ALWAYS NULL in Part2b** (the lazy build + hand-off is Part2c). The
[ADR-0023](../../adr/0023-season-phase-composable-structure.md)
ordered-typed-phase decision and the **Season phase** GLOSSARY.md glossary term
already carry the domain language (both already written — do not edit). Seam
contract:
[`.claude/worktrees/lg-02-part2b-seam-contract.md`](../../../.claude/worktrees/lg-02-part2b-seam-contract.md).

**Two NEW dormant `SeasonPhase` columns (LOCKED) — `matches/models.py`, appended
immediately after the existing `phase_type` field.** Everything else on
`SeasonPhase` is UNCHANGED (`PHASE_TYPE_CHOICES`, `season`, `ordinal`,
`phase_type`, `Meta.ordering = ["ordinal"]`, the `uniq_season_phase_ordinal`
constraint, `__str__`). **`schedule_format`**
(`CharField(max_length=32, null=True, blank=True)`) — **dormant**: nothing reads
it this slice. At create, a `round_robin` phase **copies `Season.schedule_format`**
(today's value `"single_round_robin"`); a `tournament` phase gets **`NULL`**.
**`tournament`** (`ForeignKey("matches.Tournament", null=True, blank=True,
on_delete=models.SET_NULL, related_name="season_phases")`) — the forward
one-directional `SeasonPhase → Tournament` link, **ALWAYS NULL in Part2b**. The
reverse accessor is `tournament.season_phases`; the ref is **same-app**
(`matches.Tournament`) so there is **no cross-app dependency**. Tournament stays
season-agnostic ([ADR-0019](../../adr/0019-tournament-bracket-model.md)
survives) — the FK points one way only.

**Migration `0042_seasonphase_format_tournament` — two `AddField`, NO
`RunPython`.** Dependency `("matches", "0041_season_phase")` (the latest matches
migration ⇒ this is `0042`). Two `AddField` ops in order (`schedule_format`,
then `tournament`). **No `RunPython`, no `RunSQL`, no backfill, no data
migration** — the
[ADR-0004](../../adr/0004-simulation-data-is-disposable.md) disposable-data
precedent (same posture as `0041`). The `tournament` FK references
`matches.Tournament` (same app) so there is **no cross-app migration
dependency**.

**NEW pure module `matches/phase_composer.py` (LOCKED) — Django-free composer
parser.** Mirrors the `matches/standings.py` / `matches/schedule_generator.py`
purity discipline: a **frozen import allowlist of `dataclasses` and `typing`
ONLY** — NO `django`, NO ORM, NO `random` / `datetime` / `json` / I/O /
logging — defended by **`TestNoDjangoImportsLeaked`** (subprocess fresh-import +
`sys.modules` walk). The **frozen dataclass** **`PhaseSpec`** has three fields:
`ordinal` (`int`, 1-based, contiguous `1..N` in composer order), `phase_type`
(`str`, `"round_robin"` | `"tournament"`), and `schedule_format`
(`Optional[str]` — the season format for an RR phase, `None` for a tournament
phase). The **function** **`parse_phase_composition(raw: str, *,
season_schedule_format: str) -> list[PhaseSpec]`** parses the composer's
serialized output. The **wire format (LOCKED) is comma-separated phase-type
tokens** — e.g. `"round_robin,tournament"` — parsed with `str.split(",")` and a
`str.strip()` per token (chosen over JSON to keep the allowlist minimal, no
`json` import; the template serializes the ordered rows into this exact form).
**Behaviour:** an **empty / blank `raw`** (`""` or whitespace-only after strip)
**short-circuits to exactly one** `PhaseSpec(ordinal=1,
phase_type="round_robin", schedule_format=season_schedule_format)` — the Part2a
default — **before any validation** (so it is never treated as "zero RR").
Otherwise: split on `,`, strip each token, assign **contiguous ordinals 1..N**
in composer order, set `schedule_format = season_schedule_format` for
`round_robin` specs and `None` for `tournament` specs. Valid phase types are
**`"round_robin"` and `"tournament"` only** — `"member_night"` is **NOT
selectable** in Part2b and is rejected as an unknown type. The module raises
**plain `ValueError`** (NOT `django.core.exceptions.ValidationError` — keep it
Django-free) with these **exact message strings**: zero `round_robin` in a
non-empty composition ⇒ `"composition must contain at least one round-robin
phase"`; an unknown phase type (incl. `"member_night"` or any non-RR /
non-tournament token) ⇒ `f"unknown phase type: {token!r}"`; malformed input
(an empty token between commas like `"round_robin,,tournament"`, or any token
empty after strip) ⇒ `"malformed phase composition"`. **Validation order**
within a non-empty `raw`: tokenise → reject malformed (empty token) → reject
unknown type per token → after building specs, reject zero `round_robin`.

**Form (`matches/forms.py`, `CreateLeagueForm`) — hidden `phases` field +
`clean()` seam.** A new hidden field **`phases`** (`forms.CharField(widget=
forms.HiddenInput(attrs={"id": "league-create-phases"}), required=False)`)
carries the serialized composition; the existing **disabled Season-level
`schedule_format` `ChoiceField` STAYS unchanged** (it is the live read-path
source, locked at `"single_round_robin"`). `clean()` (preserving all existing
LG-01j map-mode-vs-pool rules verbatim) calls the pure module
`parse_phase_composition(cleaned_data.get("phases", "") or "",
season_schedule_format=cleaned_data.get("schedule_format") or
"single_round_robin")` inside a `try`; on `ValueError` it re-raises as a
`forms.ValidationError(str(exc))` attached to the **`"phases"`** field, else it
stashes the result under **`cleaned_data["phase_specs"]`** (`list[PhaseSpec]`).
The `season_schedule_format` argument is the form's own `schedule_format` value
(the disabled field's locked `"single_round_robin"`).

**Views (`matches/league_views.py`) — both creation sites loop over the specs.**
In **`league_create`** (~line 553, inside the existing `@transaction.atomic`
block) the single `SeasonPhase.objects.create(season=season, ordinal=1,
phase_type="round_robin")` is **replaced by a loop** over
`form.cleaned_data["phase_specs"]`, each iteration creating a `SeasonPhase` with
`season=season`, `ordinal=spec.ordinal`, `phase_type=spec.phase_type`,
`schedule_format=spec.schedule_format`, and **`tournament=None`** (always). In
**`next_season`** (~line 1942, inside its existing `@transaction.atomic` block)
there is **no composer** — it **carries the previous Season's composition
forward** (mirroring the team-id / map-pool carry-forward): the single
`SeasonPhase.objects.create(...)` is replaced by a **copy loop** over the source
Season's `phases.all()` (the `latest_completed` carry-forward source Season;
`Meta.ordering=["ordinal"]` guarantees order), copying `ordinal`, `phase_type`,
and `schedule_format` **verbatim** while **resetting `tournament=None`**. Both
loops stay inside the existing atomic blocks so a rollback drops the phases too.

**Template (`templates/leagues/create.html`) — vanilla-JS composer (no
framework, inline `<script>`, per the LG-01d precedent).** A **"+ Add block"**
button clones a row template into the composer container; each row has a
phase-type `<select>` (`round_robin` / `tournament`) and, for a `round_robin`
row, a `schedule_format` `<select>` with the single option `single_round_robin`
(mirroring the disabled Season-level one); rows are removable (reorder optional).
On submit the JS serializes the ordered rows into the hidden
`#league-create-phases` input in the **wire format pinned above** (comma-joined
phase-type tokens in row order, e.g. `"round_robin,tournament"`). A **"member
nights coming soon"** note and a per-tournament-block **"build coming in a later
release"** flag communicate the deferred surfaces. **LOCKED DOM ids / class
substring:** `league-create-phases-composer` (outer composer container `<div>`);
`league-create-add-block` (the "+ Add block" button); `league-create-phases`
(the hidden input — also the form field's widget id above);
`league-create-phase-row-{i}` (per-row wrapper, `{i}` = 0-based row index
assigned by JS); `league-create-phase-type-{i}` (per-row phase-type `<select>`);
`league-create-phase-format-{i}` (per-row `schedule_format` `<select>` on RR
rows); `league-create-member-night-note` (the "member nights coming soon" note);
and the CSS-class **substring** `phase-tournament-pending` (the per-tournament
"build coming later" flag). All new ids are net-new and **do not collide** with
the existing create.html ids (`league-create-form`, `league-create-league-name`,
`league-create-season-name`, `league-create-start-date`,
`league-create-num-teams`, `league-create-schedule-format`,
`league-create-mean`, `league-create-std-dev`, `league-create-map-mode`,
`league-create-map-pool`, `league-create-submit`).

**Admin (`matches/admin.py`).** `SeasonPhaseAdmin.list_display` extends from
`("season", "ordinal", "phase_type")` to **`("season", "ordinal",
"phase_type", "schedule_format", "tournament")`** — no other admin change.

**Composer scope + validity rules.** The composer offers **`round_robin` and
`tournament` only** (no `member_night`), and the non-RR (`tournament`) phases are
**persisted but dormant** — the Part2a chokepoint plays the **first `round_robin`
phase** via `Season.schedule_format` and never reads the others. Validity (the
pure module enforces): **≥ 1 `round_robin`** phase required; **`tournament`
phases may sit anywhere** (no ordering constraint this slice); an **empty
composer ⇒ a single `round_robin`** (Part2a equivalence); **contiguous ordinals
1..N** in composer order.

**Determinism / read-path-unchanged note.** **No simulator change, no RNG**
consumed by any new code — the composer parser is a pure string→specs derivation
and the creation loops are plain ORM writes. The Season read-path / chokepoint is
**byte-identical to Part2a** (it still plays exactly the first RR phase), so
there is **NO Score Calibration re-baseline** and no SIM-07 / SIM-08 contract
interaction. The dormant columns are written-but-never-read this slice.

**Deferred to Part2c (DO NOT build here).** The **per-phase seeding-mode /
tournament-kind field** on `SeasonPhase` (season-ending Standings-seeded vs
mid-season strength-/un-seeded, with its compose-time validity rule) → Part2c.
**Wiring the chokepoint to `phase.schedule_format`** (this slice's dormant
column) + the first alternative regular-season format → Part2c (until then the
read-path reads `Season.schedule_format`). **Per-tournament-block configuration**
(format / `team_assembly` / seeding) + the lazy tournament **build / hand-off**
(populating the `tournament` FK) → Part2c. `member_night` stays inert (its own
sandbox grill — see PLAN.md / LG-07).

**Tests:** `matches/tests/test_phase_composer.py` (NEW — pure-unit +
`TestNoDjangoImportsLeaked`): empty `raw` → the single RR default; an RR spec's
`schedule_format` is copied from `season_schedule_format`; a tournament spec's
`schedule_format` is `None`; contiguous ordinals `1..N`; multi-phase order
preserved; a zero-RR composition raises `ValueError("composition must contain at
least one round-robin phase")`; an unknown `phase_type` raises `ValueError`; a
`member_night` token is rejected (unknown-type); a malformed `raw` (empty token)
raises `ValueError("malformed phase composition")`; the purity subprocess check.
`matches/tests/test_season_phase.py` (EXTEND — `schedule_format` nullable +
default-`None` for a tournament phase; the `tournament` FK nullable + `SET_NULL`
+ `related_name="season_phases"`). `matches/tests/test_league_create.py` (EXTEND
— the composer happy path persists multiple ordered `SeasonPhase` rows with
correct ordinals / types / `schedule_format` and `tournament=None`; an empty
composer ⇒ a single `round_robin` (Part2a equivalence); a no-RR composition is
rejected at the form layer leaving **zero** League / Season / phase rows created
— transaction atomicity; the existing single-phase tests still pass).
`matches/tests/test_league_next_season.py` (EXTEND — `next_season` copies the
previous Season's full phase composition forward — ordinals / types /
`schedule_format` — with `tournament` reset to `NULL`).

**Locked names:** see the seam contract
[`.claude/worktrees/lg-02-part2b-seam-contract.md`](../../../.claude/worktrees/lg-02-part2b-seam-contract.md)
for the authoritative list — **model fields** `SeasonPhase.schedule_format`
(`CharField(max_length=32, null=True, blank=True)`) / `SeasonPhase.tournament`
(`FK(matches.Tournament, null=True, blank=True, on_delete=SET_NULL,
related_name="season_phases")`); **migration**
`0042_seasonphase_format_tournament` (dep `0041_season_phase`, two `AddField`, no
`RunPython`); **pure module** `matches/phase_composer.py` — dataclass
`PhaseSpec(ordinal, phase_type, schedule_format)`, fn
`parse_phase_composition(raw, *, season_schedule_format) -> list[PhaseSpec]`;
**wire format** comma-separated phase-type tokens (`"round_robin,tournament"`,
`str.split(",")`); **`ValueError` strings** `"composition must contain at least
one round-robin phase"` / `f"unknown phase type: {token!r}"` / `"malformed phase
composition"`; **form** field `phases` (`HiddenInput`, id `league-create-phases`,
`required=False`) + `cleaned_data["phase_specs"]` (existing disabled
`schedule_format` field unchanged); **views** `league_create` (~553) spec loop /
`next_season` (~1942) carry-forward copy loop, both inside the existing
`@transaction.atomic`, `tournament=None` always; **template DOM ids**
`league-create-phases-composer`, `league-create-add-block`,
`league-create-phases`, `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}`,
`league-create-member-night-note`, class substring `phase-tournament-pending`;
**admin** `SeasonPhaseAdmin.list_display = ("season", "ordinal", "phase_type",
"schedule_format", "tournament")`; **read-path UNCHANGED, `tournament` FK always
NULL, no re-baseline.**

## LG-02-Part2c-1 RR → single-elimination playoff embed

The first slice of LG-02-Part2c — a **thin orchestration layer** that takes a
Season composed of an ordered `round_robin` phase then a `tournament` phase,
plays the regular season, **auto-builds** a standings-seeded single-elimination
playoff bracket the moment the RR phase completes (matchups visible **before**
any playoff click), then drains the bracket to crown the **Season champion**. It
replaces Part2b's "play the first `round_robin` phase only" read-path with a
**phase cursor** + two **lifecycle hooks** on `Season`, an **auto-build** that
wires an existing standalone `Tournament` (consumed VERBATIM) into a
`SeasonPhase`, two new play views + a Celery task that drain the already-shipped
tournament engine, dashboard + template wiring to surface the playoff button
group, and one compose-time guard. **No `Match.season_phase` FK, no Match
migration, no simulator change, no tournament engine change, no Score Calibration
re-baseline.** The [ADR-0023](../../adr/0023-season-phase-composable-structure.md)
ordered-typed-phase decision (extended with a "Part2c-1 consequences" addendum)
and the **Season phase** GLOSSARY.md glossary term carry the domain language. Seam
contract:
[`.claude/worktrees/lg-02-part2c-1-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-1-seam-contract.md).

**Phase cursor + derived completion (`matches/models.py`, on `Season`).**
**`Season.current_phase() -> SeasonPhase | None`** is a pure read-derivation (no
DB write, no RNG): it walks `ordered_phases()` (the Part2a chokepoint — ordinal
order, or the one-element implicit-`round_robin` fallback list with `pk is None`)
and returns the **first INCOMPLETE phase by ordinal**, or `None` once every phase
is complete. Completion is **DERIVED, not stored** — there is **no
`SeasonPhase.state` field**. The single derivation site is the private
**`Season._phase_complete(phase) -> bool`** (lives on `Season`, NOT `SeasonPhase`,
so it can reach `scheduled_fixtures()` / `_is_finished()` without a back-reference
dance): a `round_robin` phase is complete ⇔ the existing **`_is_finished()`**
all-fixtures-played check (REUSED verbatim — byte-identical to today for the
one-RR-phase case); a `tournament` phase is complete ⇔
**`phase.tournament_id is not None AND phase.tournament.state == "completed"`**.
A phase-less / single-RR-phase Season returns its (implicit or explicit) RR phase
while the RR is unfinished, then `None` once finished — exactly mirroring today's
"Season is done when all fixtures played". `member_night` and any future type are
inert this slice (the compose guard forbids composing one), so `_phase_complete`
returns `False` for them (unreachable). **NOTE on multi-RR:** this slice composes
exactly one RR phase, so `_is_finished()` (which covers the whole RR fixture list)
is the correct per-phase RR completion; per-phase RR fixture scoping for multi-RR
is **Part2c-2**.

**Auto-build on RR completion (`Season.activate_pending_tournament_phase() ->
None`, `@transaction.atomic`, idempotent).** The build hook. It is a **no-op**
when `current_phase()` is `None`, isn't a `tournament` phase, is already built
(`tournament_id is not None` — the idempotency guard), is the implicit fallback
(`pk is None`), or its **preceding** phase (`Season._preceding_phase(phase)` — the
phase one ordinal lower) isn't complete. Otherwise it builds: reads the preceding
phase's **final Standings** (`Season._final_standings_for_phase(phase)`, which
reuses `matches.standings.compute_standings` with the **exact** 8-key match dicts +
`(id, name)` enrolled tuples the old `_stamp_champion` assembled — `season_rounds`
omitted), creates a `Tournament(format="single_elimination",
team_assembly="preset", state="setup", name=f"{self.name} Playoffs")`, creates
**one `TournamentParticipant` per season team** with **`seed = StandingsRow.rank`**
(rank 1 → seed 1; `compute_standings` zero-fills every enrolled team so seeds are
dense `1..N`, satisfying `uniq_tournament_seed`), sets `phase.tournament` +
`phase.save(update_fields=["tournament"])`, then calls
**`tournament.lock_and_build()`** (the existing setup → active transition;
validates `>= 4` participants, builds + persists the `BracketNode` tree from the
seeds). The bracket is **visible immediately** — matchups exist before any playoff
click. A second call after a successful build hits the `tournament_id is not None`
guard (idempotent). A Season with `< 4` teams cannot reach a playoff
(`lock_and_build` raises `ValidationError`) — a degenerate config, not a happy
path.

**Rewritten completion (`Season.complete_if_finished() -> None`, REWRITTEN,
`@transaction.atomic`).** No-op on non-`active`; else it gates on the **FINAL
phase** (last ordinal, `ordered_phases()[-1]`) being `_phase_complete`, then
stamps the champion via **`Season._stamp_champion_for_final_phase(final_phase)`**
(which **replaces** the now-removed `_stamp_champion` — its standings-rank-1 logic
moves behind the RR branch). Champion = `final_phase.tournament.champion` when the
final phase is a tournament (with a defensive `None` guard that never blocks in
practice, since the engine stamps `state="completed"` + `champion` together), else
`compute_standings(...)[0]` of the final RR phase. **`_is_finished()` is
UNCHANGED** (still the RR all-fixtures-played check). **BYTE-IDENTICAL fallback:**
for a single-RR-phase Season (explicit or implicit `pk is None`), `final_phase` is
that RR phase, `_phase_complete(final_phase) == _is_finished()`, and the champion
is `compute_standings(...)[0]` — the same state flip + same `champion_team` id as
today.

**Post-round hook wiring (`matches/simulation/entrypoints.py`).**
`BatchSimulator.simulate_scheduled_round` already calls
`season.complete_if_finished()` after persistence (twice — once per Round branch).
Each of those two sites gains a **`season.activate_pending_tournament_phase()`
call IMMEDIATELY BEFORE** the existing `season.complete_if_finished()` call (the
build hook fires first, then the completion check). **Ordering is load-bearing:**
the build hook runs first so the moment the last RR fixture lands, the tournament
phase is built and becomes `current_phase()`; the completion check that follows
then sees the final phase is the (now-incomplete) tournament phase and does NOT
prematurely complete the Season. Both new calls are idempotent / no-op except at
the exact RR-completion boundary, so adding them to every persisted Round is safe
and cheap. **No simulator mechanics change, no RNG**; the
`simulate_scheduled_round` signature is unchanged.

**Celery task (`matches/tasks.py`) — `play_playoffs_task`.**
**`@shared_task(bind=True, name="matches.play_playoffs")`**, signature
`(self, season_id: int) -> dict`. Mirrors the `play_tournament_task` body pattern:
deferred imports, an inactive/unbuilt-guard early return of `{"completed": 0,
"total": 0}`, then **`while play_next_node(tournament) is not None`** drains the
bracket one Match at a time, emitting a stage-progress `update_state(state=
"PROGRESS", meta={"completed", "total"})` after each, with a
`finally: django.db.close_old_connections()`. After draining,
`season.complete_if_finished()` crowns the Season champion (the tournament final
phase is now complete; `tournament.champion` is set). **Return shape LOCKED:**
`{"completed": int, "total": int}` — **STAGE** counts from
`matches.bracket.stage_progress` (reused VERBATIM, fed by `_node_to_dict` over the
tournament's nodes), NOT node counts — matching the `play_tournament_task` shape
exactly so `_build_play_status_response` reads it unchanged. **NO outer
`@transaction.atomic`** — `play_next_node` is already `@transaction.atomic` per
Match (ADR-0016 precedent), so a mid-drain failure leaves every resolved node
committed and is resumable.

**Two play views (`matches/league_views.py`).**
**`play_single_round(request, season_id) -> HttpResponse`** (sync): POST-only
(`HttpResponseNotAllowed(["POST"])` first, 405 on GET — the LG-01d idiom), writes
`last_league_id` after the 404 guard, requires `current_phase()` to be a built
(`tournament_id is not None`) tournament phase else re-renders the dashboard with
`play_error` (the `_render_season_dashboard_error` 400-equivalent), plays **exactly
one** playoff Match via `play_next_node` (deferred import), then
`season.complete_if_finished()` (crowns the Season if the final node landed), then
**302 redirect** to `season_dashboard`.
**`play_playoffs(request, season_id) -> JsonResponse`** (async): POST-only (405
otherwise), same built-tournament guard but returns **409** JSON `{"error": ...}`
on mismatch (async endpoints return JSON, not a dashboard re-render — the
`tournament_play_all` precedent); happy path ⇒ `play_playoffs_task.delay(season_id)`
→ **202** JSON `{job_id, season_id}` (mirrors `play_two_months` / `play_until_end`).
**Polling REUSES** the LG-01d `play_status` view + `_build_play_status_response` +
`_celery_state_to_job_status` **verbatim** (same URL name, same 5-key JSON
`{status, completed, total, error, season_id}`) — the playoff task's stage-count
return is read unchanged from `async_result.info` / `.result`. **No change** to
`play_status` or `_build_play_status_response`.

**URL routes (`matches/season_urls.py`).** Two new bare-named entries —
`play_single_round` (`<int:season_id>/play-single-round/`) and `play_playoffs`
(`<int:season_id>/play-playoffs/`) — inserted **after** the LG-01d play routes and
**before** the `standings/` / `schedule/` entries (first-match resolution).
`play_status` is **reused** for the playoff job — no new status route.

**Compose-time guard (`matches/phase_composer.py`).** `parse_phase_composition`
gains ONE new rule: a `tournament` phase requires a **preceding** `round_robin`
phase. It fires **after** the existing zero-RR check (once all tokens are
known-valid and ≥ 1 RR exists, walk the specs in order and raise if any
`tournament` spec precedes the first `round_robin` spec). New `ValueError` string
(LOCKED, byte-equal): **`"a tournament phase requires a preceding round-robin
phase"`**. Pure `ValueError` (the module stays Django-free — frozen
`dataclasses` / `typing` allowlist unchanged, `TestNoDjangoImportsLeaked` still
passes); the form layer re-wraps as a `forms.ValidationError` attached to
`phases`. `member_night` remains rejected at the unknown-type step.

**Dashboard context + templates.** `_build_dashboard_context` (rendered by both
the League and Season dashboards) grows by **four playoff-cursor keys**, computed
from `displayed_season.current_phase()`: **`playoff_phase_active`** (`bool` — `True`
iff `current_phase()` is a tournament phase that is built **+ active**
(`tournament_id is not None AND tournament.state == "active"`));
**`playoff_tournament_id`** (`int | None` — `phase.tournament_id` when a tournament
phase is built, active **or** completed); **`playoff_completed`** (`bool` — `True`
iff a tournament phase exists, is built, and `tournament.state == "completed"`);
**`has_following_tournament_phase`** (`bool` — `True` iff the phase list contains a
`tournament` phase at an ordinal **after** the current RR phase). The existing
`action_button_state` / `action_button_label` keys are UNCHANGED in name; the
playoff buttons are a **separate group**, not a replacement for the action-button
slot. **NEW DOM ids** (both surfaces; they stack underneath the LG-01c/d
action-button + play-dropdown slot): the Play Single Round form / submit
(`{season,league}-dashboard-play-single-round-form` / `-submit`), the Play
Playoffs form / submit / progress
(`{season,league}-dashboard-play-playoffs-form` / `-submit` / `-progress`), and the
**View bracket** link (`{season,league}-dashboard-view-bracket-link`). Both playoff
buttons render **only when `playoff_phase_active`**; the View-bracket link renders
**whenever `playoff_tournament_id is not None`** (built tournament phase, active OR
completed) with `href = {% url 'tournament_detail' playoff_tournament_id %}` (the
existing standalone bracket page — **do NOT embed the bracket**, decision #9). The
forms POST to `play_single_round` / `play_playoffs`. *(RETIRED by NAV-01: the playoff Play forms `{season,league}-dashboard-play-single-round-form` / `-submit` / `-play-playoffs-form` / `-submit` / `-progress` are **DELETED** from the dashboards and relocate to the `Play ▾` topnav as `topbar-play-play-single-round` / `topbar-play-play-playoffs`; the `{season,league}-dashboard-view-bracket-link` read-only link is **KEPT** on the dashboard, and `playoff_phase_active` / `playoff_completed` / `has_following_tournament_phase` / `following_tournament_is_final` now come from `_build_play_controls_context`. See **NAV-01** above.)* **Conditional terminal-label
relabel:** the LG-01d terminal play-dropdown button labeled **"Until End of
Season"** is relabeled to **"Until Playoffs"** **iff `has_following_tournament_phase`**
— the form action / behaviour (`play_until_end`) and the button DOM id
(`{season,league}-dashboard-play-until-end`) are UNCHANGED, only the visible label
text swaps. The Play Playoffs form's **inline poll JS** intercepts submit,
fetch-POSTs, reads the 202 `{job_id, season_id}`, then polls `play_status` on a
1000 ms interval updating `-play-playoffs-progress` and reloading on
`status === "complete"` (mirrors the LG-01d `play_two_months` / `play_until_end`
inline JS verbatim — duplicated per template, no shared partial); Play Single Round
submits synchronously (server-side 302). **Poll UX (shared with the LG-01d play
dropdown):** the poll interval is **500 ms** and every progress block (the LG-01d
`-play-progress` and the Part2c-1 `-play-playoffs-progress`, on both dashboards)
carries a Bootstrap `spinner-border` (class `play-progress-spinner` /
`playoffs-progress-spinner`) shown alongside the live "Running… X / Y" counter so
the run reads as alive between per-round counter ticks; the spinner is hidden in
`clearPolling()` (on `complete` / `error`) and on an enqueue failure, and re-shown
by `showProgress()` on a retry.

**Tournament Matches stay `season=NULL` (decision #3).** The tournament engine
(`play_next_node`, `simulate_match(match_type="tournament")`) is consumed
VERBATIM — playoff Matches never get a `season` FK, so the playoff is invisible to
season-scoped history. There is **NO `Match.season_phase` FK, NO Match migration,
NO re-baseline, NO migration of any kind** this slice (the FK + season-linked
playoff Match history are deferred to **Part2c-2** alongside multi-RR). Because
`_final_standings_for_phase` filters `Match.objects.filter(season=self,
is_completed=True)`, tournament Matches never pollute the RR standings query.

**Determinism.** RR sims are unchanged (byte-identical). Tournament sims are
**non-deterministic** (`simulate_match` draws fresh per-round seeds), so tests
assert on `state` / `champion_team` id / bracket-node winners — **NEVER** on exact
simulated point totals. **No SIM-07 / SIM-08 interaction, NO Score Calibration
re-baseline** (extend ADR-0023, no new ADR).

**Scope-out (DEFERRED to Part2c-2 — DO NOT build here).** Multi-RR play loop +
`Match.season_phase` FK + cross-phase matchday offsetting; per-phase
`schedule_format` chokepoint wiring; the per-phase seeding-mode field + mid-season
tournaments; per-tournament-block config (format / top-N cut); non-single-elim
embeds (double-elim / RR / Swiss / RR→DE as a finals stage); season-linked playoff
Match history; weekly playoff pacing.

**Tests:** `matches/tests/test_phase_composer.py` (EXTEND — `"tournament,
round_robin"` raises the new `ValueError`; `"round_robin,tournament"` parses to 2
ordered specs; the zero-RR string still fires first; purity still passes).
`matches/tests/test_season_phase.py` / a new `matches/tests/test_season_playoff.py`
(cursor/completion derivation; auto-build seeds by standings rank + is idempotent;
`complete_if_finished` champion = tournament champion for a drained playoff and
`compute_standings(...)[0]` byte-identical for a single-RR-phase Season;
`play_playoffs_task` drains to champion under `CELERY_TASK_ALWAYS_EAGER`; the two
views' status codes (302 / dashboard-error / 405 and 202 / 409 / 405); the
dashboard context keys per cursor sub-state + the conditional terminal label) —
**never** on exact simulated point totals.

**Locked names (quick index):**
- **`Season` methods (`matches/models.py`):** `current_phase(self) ->
  SeasonPhase | None`; `_phase_complete(self, phase) -> bool`;
  `_preceding_phase(self, phase) -> SeasonPhase | None`;
  `activate_pending_tournament_phase(self) -> None` (`@transaction.atomic`,
  idempotent); `_final_standings_for_phase(self, phase) -> list[StandingsRow]`;
  `complete_if_finished(self) -> None` (REWRITTEN, `@transaction.atomic`);
  `_stamp_champion_for_final_phase(self, final_phase) -> None` (replaces the
  removed `_stamp_champion`).
- **Seed mapping:** `TournamentParticipant.seed = StandingsRow.rank` (rank 1 →
  seed 1). **Tournament create:** `format="single_elimination"`,
  `team_assembly="preset"`, `state="setup"`, `name=f"{season.name} Playoffs"`;
  then `tournament.lock_and_build()`.
- **Phase-completion rule:** RR ⇔ `_is_finished()`; tournament ⇔
  `phase.tournament_id is not None AND phase.tournament.state == "completed"` —
  in `Season._phase_complete`.
- **Hook wiring (`matches/simulation/entrypoints.py`):** `simulate_scheduled_round`
  calls `activate_pending_tournament_phase()` then `complete_if_finished()` after
  persistence in BOTH Round branches.
- **Celery task (`matches/tasks.py`):** `play_playoffs_task`,
  `@shared_task(bind=True, name="matches.play_playoffs")`, `(self, season_id:
  int) -> dict`, returns `{"completed": int, "total": int}` (stage counts from
  `matches.bracket.stage_progress`).
- **Views (`matches/league_views.py`):** `play_single_round(request, season_id)
  -> HttpResponse` (sync POST, 302 / dashboard-error / 405);
  `play_playoffs(request, season_id) -> JsonResponse` (async POST, 202 / 409 /
  405). **Reused:** `play_status` + `_build_play_status_response` +
  `_celery_state_to_job_status`.
- **URL names (`matches/season_urls.py`):** `play_single_round`
  (`<int:season_id>/play-single-round/`), `play_playoffs`
  (`<int:season_id>/play-playoffs/`), before `standings/` / `schedule/`.
- **Compose guard `ValueError` (`matches/phase_composer.py`):** `"a tournament
  phase requires a preceding round-robin phase"`, after the zero-RR check.
- **Dashboard context keys:** `playoff_phase_active`, `playoff_tournament_id`,
  `playoff_completed`, `has_following_tournament_phase`.
- **DOM ids (season / league):**
  `{season,league}-dashboard-play-single-round-form` / `-submit`;
  `{season,league}-dashboard-play-playoffs-form` / `-submit` / `-progress`;
  `{season,league}-dashboard-view-bracket-link`. Terminal label "Until Playoffs"
  iff `has_following_tournament_phase`. View-bracket link → `{% url
  'tournament_detail' playoff_tournament_id %}`.
- **ADR:** extend [ADR-0023](../../adr/0023-season-phase-composable-structure.md)
  (no new ADR). **No `Match.season_phase` FK, no Match migration, no
  simulator/engine change, no re-baseline.**

See the seam contract
[`.claude/worktrees/lg-02-part2c-1-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-1-seam-contract.md)
for the authoritative names + behaviours.

## LG-02-Part2c-2 multi-round-robin season

The next slice of LG-02-Part2c — a **thin orchestration layer** that generalises
the Part2c-1 single-RR-then-single-elim path into a **multi-round-robin** season.
It adds a `Match.season_phase` FK, a per-phase find-or-create key, per-phase RR
completion, a multi-RR play loop, and **cross-phase global-continuous matchday
offsetting**. The supported + tested composition is **one-or-more `round_robin`
phases then an OPTIONAL trailing `tournament`** (RR1→RR2, RR1→RR2→playoff). The
tournament engine (`simulate_match(match_type="tournament")` / `play_next_node`)
and the Part2c-1 auto-build / cursor / completion machinery are consumed
**VERBATIM**; legacy phase-less and single-RR seasons stay **byte-identical**.
**No simulator mechanics change, no tournament-engine change, no composer/form/
template change, no Score Calibration re-baseline, no SIM-07/08 interaction.** The
[ADR-0023](../../adr/0023-season-phase-composable-structure.md)
ordered-typed-phase decision (extended with a "Part2c-2 consequences" addendum)
and the **Matchday** / **Season phase** GLOSSARY.md glossary touch-ups carry the
domain language. Seam contract:
[`.claude/worktrees/lg-02-part2c-2-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-2-seam-contract.md).

**`Match.season_phase` FK + migration `0043` (`matches/models.py`).** A new
optional FK on `Match` mirrors the existing `Match.season` SET_NULL block:
`season_phase = models.ForeignKey("matches.SeasonPhase", null=True, blank=True,
on_delete=models.SET_NULL, related_name="matches")`, declared immediately after
`Match.season`. **RR Matches now carry BOTH `season=<season>` AND
`season_phase=<rr phase>`**; tournament/playoff Matches stay `season=NULL,
season_phase=NULL` (engine consumed verbatim) and legacy phase-less seasons
(implicit `pk is None` phase) keep `season_phase=NULL` — byte-identical. The
reverse accessor `SeasonPhase.matches` reuses the same `related_name` label
`Season.matches` uses, but the owning models differ (`Season` vs `SeasonPhase`)
so the two reverse accessors don't collide. Migration
`matches/migrations/0043_match_season_phase.py` (dep
`0042_seasonphase_format_tournament`) is a **single `AddField`** — **NO
`RunPython`, NO backfill, NO data migration** (the [ADR-0004](../../adr/0004-simulation-data-is-disposable.md)
disposable-data posture, same as `0029` / `0041` / `0042`). The FK is the
load-bearing reason this slice exists: it makes the find-or-create key
phase-aware so identical pairings in different RR phases are DISTINCT Matches.

**By-phase fixture seam + global-continuous matchday offset (`Season`, in
`matches/models.py`).** A NEW `Season.scheduled_fixtures_by_phase(self) ->
list[tuple[SeasonPhase, list[ScheduleFixture]]]` returns one `(phase, fixtures)`
tuple per `round_robin` phase in ordinal order (tournament phases contribute no
fixtures — they drain via the bracket, not `generate_schedule`). **Phase k's
fixtures are offset by the SUM of all prior RR phases' matchday spans** (a phase's
un-offset span is `max(f.matchday for f in <base>)`), so the whole season is one
monotonic 1..N matchday calendar and the `date = start_date + (matchday-1)*7`
derivation keeps working. Each offset fixture is a NEW `ScheduleFixture` with
`matchday = original + offset` (round_number / team ids unchanged); fixtures come
from `generate_schedule(team_ids, phase.schedule_format or self.schedule_format)`
(both currently resolve to `single_round_robin`, so output is byte-identical for
the single-RR case). `Season.scheduled_fixtures(self) -> list[ScheduleFixture]` is
**REWRITTEN to be the flat concatenation** of every phase's offset fixtures —
preserving its return type, its `[]`-on-`<2`-teams guard, and byte-identical
output for a single-RR (or phase-less) Season (one phase, offset 0) so every flat
caller (`_is_finished`, `season_schedule`, `_build_dashboard_context`,
`league_history`, `team_schedule`) is unchanged. A new private
`Season._scheduled_team_ids(self) -> list[int]` extracts the draft-vs-snapshot
`team_ids` rule (no behaviour change) so the by-phase seam and per-phase
completion read one source.

**Per-phase RR completion (`Season`, in `matches/models.py`).**
`Season._phase_complete(self, phase)` is **REWRITTEN** so the `round_robin` branch
is **per-phase EXCEPT the implicit fallback**: `phase.pk is None` ⇒ the
whole-season `_is_finished()` (byte-identical legacy path); a persisted RR phase ⇒
the NEW private `Season._rr_phase_complete(self, phase) -> bool`. The tournament
branch and trailing `return False` are unchanged. `_rr_phase_complete` is the
per-phase analogue of `_is_finished`: it reads THIS phase's offset fixtures (via
the NEW private `Season._fixtures_for_phase(self, phase) -> list[ScheduleFixture]`,
the matching entry from `scheduled_fixtures_by_phase()`) and scopes the
played-rounds query by **`GameRound.objects.filter(match__season_phase=phase)`**,
comparing against the same Side-agnostic `(frozenset({team ids}), round_number)`
key `_is_finished` uses (the offset only changes `matchday`, which the key does not
read). This makes the cursor finish RR1 before RR2 opens — and because
`current_phase()` already walks `ordered_phases()` calling `_phase_complete` per
phase, **no `current_phase` edit is needed**. `_final_standings_for_phase` stays
**whole-season** (`Match.objects.filter(season=self, is_completed=True)`) so
standings are **cumulative across all RR phases** (a trailing playoff seeds from
the cumulative leader; an RR-final-phase champion = cumulative leader).

**Phase-aware find-or-create key (`matches/simulation/entrypoints.py`).**
`simulate_scheduled_round` gains a **keyword-only `season_phase=None`** appended
after `arena_map=None` (`def simulate_scheduled_round(self, season, team_a,
team_b, round_number, *, arena_map=None, season_phase=None) -> GameRound`).
**Default `None` = legacy/sandbox unchanged.** The Side-agnostic find-or-create
key becomes **`(season, season_phase, frozenset({team_a_id, team_b_id}))`** — both
lookup queries filter `season=season, season_phase=season_phase`, and the Round-1
create stamps `season_phase=season_phase`. So identical pairings in DIFFERENT RR
phases become DISTINCT Matches; `season_phase=None` keeps the key
`(season, NULL, frozenset)`, byte-identical to today's `(season, frozenset)`. The
two post-round hooks (`activate_pending_tournament_phase()` then
`complete_if_finished()`) are **UNCHANGED** — `complete_if_finished` already gates
on the final phase, and `_phase_complete` now resolves per-phase RR, so the season
completes only when the truly-final phase finishes. **No new RNG draw** — only the
Match the round attaches to changes.

**Phase-aware pure helpers (`matches/season_dashboard.py`).** The module stays
**Django-free** (frozen `collections`/`dataclasses`/`typing` allowlist;
`TestNoDjangoImportsLeaked` must keep passing). `select_play_fixtures` and
`find_next_matchday` become phase-aware via **PLAIN INT phase-ids** — no Django,
no `SeasonPhase` import. Their `fixtures` arg is now a
`list[tuple[int | None, ScheduleFixture]]` (each `(phase_id, fixture)`) and
`played_keys` is `set[tuple[int | None, frozenset[int], int]]`; the per-fixture
key built inside the sweep becomes the 3-tuple `(phase_id, frozenset({team ids}),
round_number)`. Because the play loop feeds OFFSET fixtures, the existing "next
`max_matchdays` distinct matchdays" sweep selects a contiguous GLOBAL window that
naturally spans the RR1→RR2 boundary (`max_matchdays is None` ⇒ all unplayed
pairs). **`find_next_fixture` / `round_progress` / `compute_leaders` / `LeaderRow`
stay UNCHANGED on the FLAT 2-tuple key shape** `(frozenset, round_number)` — they
serve the dashboard's whole-season next/progress display over the flat
`scheduled_fixtures()` + flat `played_keys` (already global-continuous via the
offset), so no phase-aware variant is needed for them. The seam split:
play-loop helpers attribute each Round to its owning phase (3-tuple); dashboard
helpers need only whole-season next/progress (2-tuple); both shapes are plain-int
/ frozenset / dataclass, so the module stays Django-free.

**Play-loop wiring (`matches/tasks.py::play_season_task`,
`matches/league_views.py::play_week`).** Both sites are rewritten to iterate
**by phase**: build `by_phase = season.scheduled_fixtures_by_phase()`, a
`phase_by_id = {phase.id: phase for phase, _ in by_phase}` map, a flat
`fixtures = [(phase.id, fixture) for phase, pf in by_phase for fixture in pf]`
list, and a phase-aware `played_keys = {(gr.match.season_phase_id,
frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr
in GameRound.objects.filter(match__season=season).select_related("match")}`. The
per-fixture loop unpacks `(phase_id, fixture)` and passes
`season_phase=phase_by_id.get(phase_id)` into `simulate_scheduled_round`. For a
phase-less season `scheduled_fixtures_by_phase()` yields one tuple whose
`phase.id is None`, so `phase_by_id.get(None)` → `None` → `season_phase=None`
flows through, preserving legacy `season_phase=NULL` Matches. The
`_resolve_fixture_map` / `in_bulk` / progress / `close_old_connections`
machinery, the 405 guard, `last_league_id` write, and the success/error responses
are otherwise unchanged. **`play_two_months` / `play_until_end` are UNCHANGED** —
they enqueue `play_season_task`; the `max_matchdays=8 | None` window carries
through `select_play_fixtures` unchanged over the global-continuous matchdays.

**Scope-out (DEFERRED to Part2c-3 — DO NOT build here).** Per-phase
`schedule_format` wiring beyond the read + the first alternative regular-season
format; the per-phase seeding-mode field + mid-season tournaments; per-tournament-
block config (format / top-N cut); non-single-elim embeds (double-elim / RR /
Swiss / RR→DE as a finals stage); a season-linked playoff Match-history surface;
weekly playoff pacing. `_final_standings_for_phase` stays whole-season (cumulative
standings are the intended behaviour, not a deferral).

**Determinism.** RR sims are byte-identical per Round (no mechanics change).
Tournament sims stay **non-deterministic** — tests assert on `state` /
`champion_team` id / bracket-node winners and on Match counts / `season_phase_id`
attribution, **NEVER** on exact simulated point totals. **No SIM-07 / SIM-08
interaction, NO Score Calibration re-baseline** (extend ADR-0023, no new ADR).

**Locked names (quick index):**
- **Model + migration (`matches/models.py`):** `Match.season_phase =
  models.ForeignKey("matches.SeasonPhase", null=True, blank=True,
  on_delete=models.SET_NULL, related_name="matches")`; reverse accessor
  `SeasonPhase.matches`; migration
  `matches/migrations/0043_match_season_phase.py` (dep
  `0042_seasonphase_format_tournament`, single `AddField`, NO `RunPython`).
- **`Season` methods (`matches/models.py`):**
  `scheduled_fixtures_by_phase(self) -> list[tuple[SeasonPhase,
  list[ScheduleFixture]]]` (NEW — per-phase offset fixtures);
  `scheduled_fixtures(self) -> list[ScheduleFixture]` (REWRITTEN — flat
  concatenation of by-phase offset fixtures);
  `_phase_complete(self, phase) -> bool` (REWRITTEN — RR branch per-phase via
  `_rr_phase_complete`, `pk is None` ⇒ `_is_finished()`);
  `_rr_phase_complete(self, phase) -> bool` (NEW — per-phase RR, scoped
  `match__season_phase=phase`);
  `_fixtures_for_phase(self, phase) -> list[ScheduleFixture]` (NEW private — THIS
  phase's offset fixtures);
  `_scheduled_team_ids(self) -> list[int]` (NEW private — extracted
  draft-vs-snapshot rule). UNCHANGED: `current_phase`, `_preceding_phase`,
  `_final_standings_for_phase` (whole-season — cumulative standings),
  `activate_pending_tournament_phase`, `_stamp_champion_for_final_phase`,
  `complete_if_finished`, `_is_finished`, `ordered_phases`, `_implicit_phase`,
  `start_season`.
- **Matchday offset:** phase k offset = sum of prior RR phases' matchday spans;
  per-phase span = `max(f.matchday for f in <un-offset base>)`; result is one
  monotonic 1..N calendar; `date = start_date + (matchday-1)*7` unchanged.
- **Find-or-create key (`matches/simulation/entrypoints.py`):**
  `(season, season_phase, frozenset({team_a_id, team_b_id}))`; signature gains
  keyword-only `season_phase=None`
  (`simulate_scheduled_round(self, season, team_a, team_b, round_number, *,
  arena_map=None, season_phase=None) -> GameRound`); post-round hooks
  (`activate_pending_tournament_phase()` then `complete_if_finished()`) UNCHANGED.
- **Pure helpers (`matches/season_dashboard.py`) — phase-aware, Django-free:**
  `select_play_fixtures(fixtures, played_keys, max_matchdays)` and
  `find_next_matchday(fixtures, played_keys)` where `fixtures: list[tuple[int |
  None, ScheduleFixture]]`, `played_keys: set[tuple[int | None, frozenset[int],
  int]]`. UNCHANGED on the FLAT 2-tuple shape: `find_next_fixture`,
  `round_progress`, `compute_leaders`, `LeaderRow`. `TestNoDjangoImportsLeaked`
  must keep passing.
- **played_keys shape (play loop):** `(season_phase_id, frozenset({team_red_id,
  team_blue_id}), round_number)` from `match.season_phase_id` (plain `int |
  None`). FLAT dashboard `played_keys` stays the 2-tuple `(frozenset,
  round_number)`.
- **Play-loop sites:** `matches/tasks.py::play_season_task` and
  `matches/league_views.py::play_week` — both build `by_phase` / `phase_by_id` /
  flat `[(phase.id, fixture)]` / phase-aware `played_keys`, call
  `select_play_fixtures(...)`, pass `season_phase=phase_by_id.get(phase_id)`.
  `play_two_months` / `play_until_end` UNCHANGED.
- **Composer / form / template:** UNCHANGED (`parse_phase_composition` already
  permits multiple `round_robin` tokens, ≥1 RR, no cap; the Part2c-1
  tournament-must-follow-RR guard stays).
- **ADR:** extend [ADR-0023](../../adr/0023-season-phase-composable-structure.md)
  with a "Part2c-2 consequences" addendum (no new ADR). **No new GLOSSARY.md
  domain term, no simulator/engine change, no re-baseline.**

See the seam contract
[`.claude/worktrees/lg-02-part2c-2-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-2-seam-contract.md)
for the authoritative names + behaviours.
