# LG-02-Part2c-3b seam contract — per-phase `tournament_mode` field (dormant)

Thin, **fully dormant** slice: add `SeasonPhase.tournament_mode`, thread it through
the compose/creation/carry-forward seam (always `"standings"` this slice), and
document the validity rule. **No composer picker, no wire-format mode token, no
guard relaxation, no build branch** — all deferred to Part2c-3c. No simulator /
RNG change → **no Score Calibration re-baseline**. Extends
[ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md) with a
"Part2c-3b consequences" addendum (no new ADR). CONTEXT.md **Season phase** entry
was updated at grill time (the `tournament_mode` vocabulary + the stale
Part2c-2 → Part2c-3b fix).

## Model (`matches/models.py`, `SeasonPhase`)

Add a class attribute + field, declared **immediately after the `tournament` FK**
(the last Part2b field):

```python
TOURNAMENT_MODE_CHOICES = (
    ("standings", "Season-ending: from Standings"),
    ("strength", "Mid-season: by team strength"),
    ("unseeded", "Mid-season: random seed"),
    ("random_draw", "Mid-season: drawn pool -> RR->DE"),
)
tournament_mode = models.CharField(
    max_length=16,
    choices=TOURNAMENT_MODE_CHOICES,
    default="standings",
)
```

- All 4 choices declared now (the `member_night` precedent — declared-but-inert;
  only `standings` has build behaviour this slice).
- `max_length=16` (matches `phase_type`; longest value `"random_draw"` is 11).
- **No constraint, no `db_index`.** Meaningful only for `tournament` phases;
  `round_robin` phases carry the inert default.
- **`unseeded` != `random_draw`**: `unseeded` randomly seeds the season's existing
  **preset** teams; `random_draw` builds fresh balanced teams from a **player
  pool** (reuses LG-02x-1 `team_assembly="random_draw"` + `format="round_robin_double_elim"`).
- **Only `standings` requires a preceding `round_robin` phase** (the existing
  blanket compose guard already enforces this — see Validity rule below).

## Migration

`matches/migrations/0045_seasonphase_tournament_mode.py`, dependency
`("matches", "0044_match_leg")`, **single `AddField`**, **NO `RunPython` / NO
backfill** (ADR-0004 disposable-data posture; existing standings-playoff phases
inherit `default="standings"` correctly).

## Pure module (`matches/phase_composer.py`)

`PhaseSpec` gains **one trailing field with a default**:

```python
@dataclass(frozen=True)
class PhaseSpec:
    ordinal: int
    phase_type: str
    schedule_format: Optional[str]
    tournament_mode: str = "standings"   # NEW (trailing, defaulted)
```

- Appended **LAST** with a default ⇒ every existing keyword construction stays
  equality-identical when defaulted (the c-3a `ScheduleFixture.leg` precedent).
- **Wire format UNCHANGED.** `tournament_mode` is **NOT parsed from the wire** this
  slice — `parse_phase_composition` leaves every spec at the `"standings"` default.
  A `tournament:<x>` token still raises `"malformed phase composition"` (the
  existing "a tournament token takes no format" rule), **reserving the `:` syntax
  for the c-3c picker.**
- Frozen import allowlist (`dataclasses`, `typing`) + `TestNoDjangoImportsLeaked`
  unchanged.

## Creation / carry-forward (`matches/league_views.py`)

Both existing `SeasonPhase.objects.create(...)` loops gain one kwarg:

- `league_create` spec loop (~558): add `tournament_mode=spec.tournament_mode`.
- `next_season` carry-forward loop (~2112): add `tournament_mode=src.tournament_mode`
  (verbatim carry — forward-compatible for c-3c, mirrors the `schedule_format`
  carry).

## Admin (`matches/admin.py`)

`SeasonPhaseAdmin.list_display` appends `"tournament_mode"` →
`("season", "ordinal", "phase_type", "schedule_format", "tournament", "tournament_mode")`.

## Validity rule

**Unchanged.** The `standings` mode's "needs a preceding `round_robin` phase"
requirement is **already enforced for every `tournament` block** by the existing
blanket `parse_phase_composition` preceding-RR guard. c-3b adds no new parser rule
and does **not** relax the guard (relaxation for mid-season modes = c-3c).

## UNCHANGED (explicitly out of scope → c-3c)

- `Season.activate_pending_tournament_phase` (still hardcodes standings-seeding;
  the default already matches, so byte-identical).
- The compose guard / wire format / composer template.
- Read-path, simulator, RNG, `Match` model. **No re-baseline.**

## Tests

- `matches/tests/test_season_phase.py` (EXTEND): field exists, `default="standings"`,
  `max_length==16`, all 4 choices declared; a `tournament` phase created via
  `league_create` carries `tournament_mode="standings"`.
- `matches/tests/test_phase_composer.py` (EXTEND): `PhaseSpec.tournament_mode`
  defaults `"standings"`; `parse_phase_composition` output specs (RR + tournament)
  all carry `"standings"`; a `tournament:strength` token still raises
  `"malformed phase composition"`; `TestNoDjangoImportsLeaked` still passes.
- `matches/tests/test_league_create.py` (EXTEND): a composed
  `round_robin,tournament` persists the tournament phase with
  `tournament_mode="standings"`.
- `matches/tests/test_league_next_season.py` (EXTEND): **hand-set a source phase's
  `tournament_mode="strength"` via ORM, assert the carry-forward preserves it** —
  the load-bearing forward-compat guard for c-3c.
