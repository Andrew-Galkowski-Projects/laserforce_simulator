# FIN-05 — Luxury-tax challenge-mode firing — SEAM CONTRACT

Branch: `fin-05-luxury-tax-firing`. An **optional per-League rule** (default off, set at
League creation, never edited mid-League) that fires the Manager **outright** whenever
their Current team pays the **luxury tax** in a completed Season — **independent of
cumulative owner mood**. Mirrors ZenGM's `challengeFiredLuxuryTax`. Extends the CAR-02 /
[ADR-0026](../../docs/adr/0026-manager-firing-owner-mood.md) owner-mood firing lifecycle
and reads the FIN-01 luxury-tax expense line (`TeamSeasonFinance.luxury_tax`).

**No simulator change → no Score Calibration re-baseline. No new ADR — a Consequences
addendum on ADR-0026. No new CONTEXT.md term.** Inert outside career mode and inert when
`finance_enabled` is OFF.

Verified against the live repo before writing — pinned facts:
`matches/owner_mood.py::decide_verdict` is the **keyword-only `seasons_in_tenure`** signature
(L121–145; the `past_grace and total <= FIRE_THRESHOLD ⇒ Verdict("fired", 0)` branch is L138–139).
`matches/league_views.py::_ensure_owner_evaluations` (L3446) already fetches
`tsf = TeamSeasonFinance.objects.filter(team_id=team_managed_id, season=season).first()`
for the FIN-01 money axis (L3535–3537), calls `decide_verdict(...)` (L3545–3553), and writes
the row via `OwnerEvaluation.objects.get_or_create(... defaults={...})` (L3555–3569). `next_season`
(L3671) routes `verdict == "fired"` + unreassigned → `new_team_picker` (L3714–3718); `owner_evaluation`
(L3724) renders with `evaluation` in context (L3765) and already passes `is_fired` / `reassigned`
(L3772–3773). `TeamSeasonFinance.luxury_tax` is the expense-line field (`models.py:1685`,
`FloatField(default=0.0)`). `League.finance_enabled` is `models.BooleanField(default=False)`
(`models.py:872`). `CreateLeagueForm` declares `finance_enabled` (`forms.py:225–230`) and threads
it into `League.objects.create(...)` via `league_create` (`league_views.py:925`). The latest
matches migration is `0052_teamseasonfinance_health_cost` ⇒ this slice is
`0053_fin05_luxury_tax_firing`.

---

## 0 · Locked names (quick index)

| Kind | Name |
|---|---|
| Pure-seam fn (CHANGED sig) | `matches/owner_mood.py::decide_verdict(totals, deltas, *, seasons_in_tenure, luxury_tax_paid: bool = False, challenge_fired_luxury_tax: bool = False) -> Verdict` |
| New branch (FIRST in past-grace block) | `if past_grace and challenge_fired_luxury_tax and luxury_tax_paid: return Verdict("fired", 0)` |
| Pure module invariant | Django-free; frozen import allowlist (`dataclasses`/`typing`/`collections`) UNCHANGED; `Verdict` dataclass UNCHANGED (no `fired_reason`) |
| Model field | `matches.models.OwnerEvaluation.fired_reason = CharField(max_length=16, choices=FIRED_REASON_CHOICES, default="")` |
| Model field | `matches.models.League.challenge_fired_luxury_tax = BooleanField(default=False)` |
| Migration | `matches/migrations/0053_fin05_luxury_tax_firing.py` (2× `AddField`, dep `("matches", "0052_teamseasonfinance_health_cost")`, no `RunPython`) |
| Writer (CHANGED) | `matches.league_views._ensure_owner_evaluations` |
| Form field | `matches.forms.CreateLeagueForm.challenge_fired_luxury_tax = forms.BooleanField(required=False, initial=False, ...)` (DOM id `league-create-challenge-luxury-tax`) |
| View (CHANGED) | `matches.league_views.league_create` (threads `challenge_fired_luxury_tax=`) |
| View (UNCHANGED) | `matches.league_views.next_season` (no code change) |
| Template (CHANGED) | `templates/leagues/create.html` (checkbox row) |
| Template (CHANGED) | `templates/seasons/owner_evaluation.html` (flavour element, DOM id `owner-evaluation-fired-reason`) |
| ADR | ADR-0026 Consequences addendum (no new ADR) |

---

## 1 · Pure decider `matches/owner_mood.py::decide_verdict` (CHANGED signature)

**Signature CHANGES to** (two keyword-only bools, ZenGM-shaped, both default `False`):

```python
def decide_verdict(
    totals: MoodTotals,
    deltas: MoodDeltas,
    *,
    seasons_in_tenure: int,
    luxury_tax_paid: bool = False,
    challenge_fired_luxury_tax: bool = False,
) -> Verdict:
```

**New branch placement (LOCKED).** The branch is placed **FIRST inside the `past_grace`
block — BEFORE the existing mood `total <= FIRE_THRESHOLD` check**:

```python
    total = totals.wins + totals.playoffs + totals.money
    delta = deltas.wins + deltas.playoffs + deltas.money
    past_grace = seasons_in_tenure > GRACE_PERIOD_SEASONS

    # FIN-05 — luxury-tax challenge fire, checked FIRST inside the same past-grace gate.
    if past_grace and challenge_fired_luxury_tax and luxury_tax_paid:
        return Verdict("fired", 0)

    if past_grace and total <= FIRE_THRESHOLD:          # mood fire — BYTE-UNCHANGED
        return Verdict("fired", 0)
    if past_grace and total + delta < FIRE_THRESHOLD:    # hot-seat 1 — BYTE-UNCHANGED
        return Verdict("hot_seat", 1)
    if past_grace and total + 2 * delta < FIRE_THRESHOLD:  # hot-seat 2 — BYTE-UNCHANGED
        return Verdict("hot_seat", 2)
    return Verdict("retained", 0)
```

- **Respects the Grace period (LOCKED decision #1).** The luxury-tax fire is gated by the
  **SAME `past_grace = seasons_in_tenure > GRACE_PERIOD_SEASONS`** condition as mood firing —
  **no luxury-tax firing during grace**. It is the FIRST check *inside* the past-grace block,
  not a check that bypasses grace.
- **Byte-unchanged guarantee.** The mood `total <= FIRE_THRESHOLD` (`<=`) check and **both**
  hot-seat projections (`< FIRE_THRESHOLD`, strict; level-1-wins-when-both-hold ordering) are
  **byte-for-byte identical** to today, only shifted down by the inserted branch.
- **Default-False blast radius.** Both new params default `False` ⇒ **ZERO blast radius** on
  every existing caller / test of `decide_verdict` (a caller passing neither bool gets exactly
  today's behaviour: the FIN-05 branch is unreachable when `challenge_fired_luxury_tax` is `False`).
- **Django-free invariant.** The module stays Django-free — the frozen
  `dataclasses` / `typing` / `collections` import allowlist **holds** (the two new params are
  plain bools, no new import), so `TestNoDjangoImportsLeaked` still passes.
- **`Verdict` dataclass UNCHANGED.** No `fired_reason` on the pure seam — the `frozen Verdict`
  stays `(outcome, hot_seat_level)`. The reason is persisted by the writer onto the row
  (§3), NOT carried through the pure decider.

---

## 2 · Model changes (`matches/models.py`)

### 2.1 `OwnerEvaluation.fired_reason` (NEW field)

`fired_reason` is **persisted on the immutable `OwnerEvaluation` row** (decision #3) — NOT
re-derived. CONTEXT.md forbids recomputing a past evaluation from current state, so the
firing reason is stamped at write time and read back verbatim.

Append to the `OwnerEvaluation` model (after `hot_seat_level`, before `created_at`):

```python
    FIRED_REASON_CHOICES = (
        ("", ""),
        ("owner_mood", "Owner mood"),
        ("luxury_tax", "Luxury tax"),
    )
    ...
    fired_reason = models.CharField(
        max_length=16, choices=FIRED_REASON_CHOICES, default=""
    )
```

- **Legacy / pre-FIN-05 fired rows default `""`** → render as the mood-firing message (the
  template treats `""` and `"owner_mood"` identically — §6).
- The `Meta` (ordering + `uniq_league_season_owner_evaluation`) is UNCHANGED. The model stays
  immutable / `get_or_create`-written.

### 2.2 `League.challenge_fired_luxury_tax` (NEW field)

The per-League toggle (decision #4) — **create-time only, no mid-League edit surface**.
Append to the `League` model (next to `finance_enabled`):

```python
    challenge_fired_luxury_tax = models.BooleanField(default=False)
```

### 2.3 Migration `matches/migrations/0053_fin05_luxury_tax_firing.py`

- **Exactly 2× `AddField`**: `League.challenge_fired_luxury_tax` and
  `OwnerEvaluation.fired_reason`.
- **NO `RunPython` / `RunSQL` / backfill** (ADR-0004 disposable-data posture; the
  `0049`/`0050`/`0052` precedent). Existing fired rows take the `default=""` ⇒ render as the
  mood message.
- Dependency `("matches", "0052_teamseasonfinance_health_cost")` (the latest matches migration).

---

## 3 · Writer `matches.league_views._ensure_owner_evaluations` (CHANGED)

**Reuse the EXISTING per-Season `tsf` lookup** (decision #7) — the writer already fetches it for
the FIN-01 money axis at `league_views.py:3535`:

```python
        tsf = TeamSeasonFinance.objects.filter(
            team_id=team_managed_id, season=season
        ).first()
```

This `tsf` fetch must be available to the FIN-05 path. Today it sits *inside* the
`if league.finance_enabled and team_managed_id is not None:` block (L3534). FIN-05 needs
`luxury_tax_paid` whenever `challenge_fired_luxury_tax` is on — which only matters when
finance is on (no `TeamSeasonFinance` row ⇒ no luxury tax). So compute the bool from the
existing `tsf`:

```python
        luxury_tax_paid = tsf is not None and tsf.luxury_tax > 0
```

**Derivation rule:** `luxury_tax_paid = (tsf is not None) and (tsf.luxury_tax > 0)`. When the
managed team didn't pay the luxury tax that Season (`tsf.luxury_tax == 0.0`, the default), or
there's no `tsf` row (finance OFF, or no enrolled-team finance), `luxury_tax_paid` is `False`.

**Pass BOTH kwargs into `decide_verdict`:**

```python
        verdict = owner_mood.decide_verdict(
            owner_mood.MoodTotals(wins=wins_total, playoffs=playoffs_total, money=money_total),
            owner_mood.MoodDeltas(wins=wins_delta, playoffs=playoffs_delta, money=money_delta),
            seasons_in_tenure=seasons_in_tenure,
            luxury_tax_paid=luxury_tax_paid,
            challenge_fired_luxury_tax=league.challenge_fired_luxury_tax,
        )
```

**`fired_reason` reconstruction rule (LOCKED).** Compute the reason from the SAME inputs the
decider used, and store it in the `get_or_create(... defaults={...})` block:

```python
        fired_reason = (
            "luxury_tax"
            if (
                verdict.outcome == "fired"
                and league.challenge_fired_luxury_tax
                and luxury_tax_paid
            )
            else ("owner_mood" if verdict.outcome == "fired" else "")
        )
```

Add `"fired_reason": fired_reason` to the `defaults={...}` dict of the existing
`OwnerEvaluation.objects.get_or_create(...)` call (L3555–3569). The idempotent re-read branch
(the `existing is not None` early-`continue` at L3479–3494) **threads nothing new** — the eval
screen reads `evaluation.fired_reason` off the row directly (§6).

**Mood-recorded-normally invariant (decision #6).** On a challenge fire the writer **still
computes + stores the `wins`/`playoffs`/`money` deltas and cap-chains the cumulative totals
exactly as today** — the challenge ONLY changes `verdict.outcome` → `"fired"` (via the pure
decider) and `fired_reason` → `"luxury_tax"`. The tenure cumulative accrues up to the firing
and resets at the next tenure like any firing. Nothing about the factor math, the running
totals, or the tenure derivation changes.

**Silently inert (decision #5).** When `finance_enabled` is OFF there is no `TeamSeasonFinance`
row ⇒ `luxury_tax_paid` is `False` ⇒ the FIN-05 branch never fires. Outside career mode the
writer already early-returns via `if not _is_career_league(league): return` (L3462–3463), so no
row is written at all. **No cross-field form validation** (a `challenge_fired_luxury_tax`
toggle on a non-finance League is harmless — it just never has anything to fire on).

---

## 4 · `next_season` gate — UNCHANGED

`matches.league_views.next_season` already routes `verdict == "fired"` + unreassigned →
`new_team_picker` (L3714–3718). A **challenge fire produces `verdict == "fired"`** and routes
**identically** — there is **NO code change** in `next_season`. (Confirmed: the gate reads
`evaluation.verdict == "fired"` and `league.current_team_id == evaluation.team_managed_id`,
neither of which depends on the firing *reason*.)

---

## 5 · Create form + `league_create` + create.html

### 5.1 Form field (`matches/forms.py::CreateLeagueForm`)

Add (alongside `finance_enabled`, decision #10):

```python
    challenge_fired_luxury_tax = forms.BooleanField(
        required=False,
        initial=False,
        label="Fire on luxury tax",
        widget=forms.CheckboxInput(
            attrs={"id": "league-create-challenge-luxury-tax"}
        ),
    )
```

DOM id **`league-create-challenge-luxury-tax`**. No `clean()` change — no cross-field rule.

### 5.2 View threading (`matches/league_views.py::league_create`)

In the existing `League.objects.create(...)` call (L920–926), add:

```python
        challenge_fired_luxury_tax=form.cleaned_data["challenge_fired_luxury_tax"],
```

### 5.3 Template (`templates/leagues/create.html`)

Render a checkbox row **near the `finance_enabled` row** (the existing `form-check` block at
L107–116), mirroring its markup:

```html
        <div class="mb-3 form-check">
            {{ form.challenge_fired_luxury_tax }}
            <label for="league-create-challenge-luxury-tax" class="form-check-label">Fire on luxury tax</label>
            <div class="form-text">
                When on, the owner fires the manager outright any season the
                team pays the luxury tax — independent of cumulative mood.
                Only takes effect with team finances enabled.
            </div>
            {{ form.challenge_fired_luxury_tax.errors }}
        </div>
```

---

## 6 · Eval screen flavour element (`templates/seasons/owner_evaluation.html`)

Render a **distinct flavour element** (DOM id **`owner-evaluation-fired-reason`**, decision #11)
keyed on `evaluation.fired_reason`. The `owner_evaluation` view already passes `evaluation` in
context (L3765) — **read `evaluation.fired_reason` directly in the template; no new context key
is needed** (confirmed: the view builds `is_fired` / `reassigned` already, and `fired_reason` is
a plain field on the `evaluation` model instance).

Rendering rule (the existing CTA/verdict markup is unchanged; add this near the verdict badge):

```html
        {% if evaluation.verdict == "fired" %}
            {% if evaluation.fired_reason == "luxury_tax" %}
                <div id="owner-evaluation-fired-reason" class="alert alert-danger">
                    Fired for paying the luxury tax.
                </div>
            {% else %}
                <div id="owner-evaluation-fired-reason" class="alert alert-danger">
                    Fired by the owner — mood fell too low.
                </div>
            {% endif %}
        {% endif %}
```

- `evaluation.fired_reason == "luxury_tax"` ⇒ the distinct luxury-tax message.
- `"owner_mood"` **AND** legacy `""` ⇒ the mood-firing message (legacy `""` rows render as the
  mood message — the `{% else %}` catches both).
- Only rendered when `verdict == "fired"` (a retained / hot-seat eval shows no fired-reason
  element).

---

## 7 · Test boundary

Assertion discipline: assert **schema-level outcomes / verdicts / row fields / DOM ids** —
**NEVER exact simulated point totals** (build any standings/finance inputs from hand-constructed
fixtures).

### 7.1 Pure-unit — `matches/tests/test_owner_mood.py` (EXTEND)
Over `decide_verdict` ONLY (no DB):
- **Challenge precedence:** `challenge_fired_luxury_tax=True, luxury_tax_paid=True`,
  `seasons_in_tenure` past grace, with mood **above** the mood-fire threshold (would otherwise
  be `retained`) ⇒ `Verdict("fired", 0)`.
- **Grace suppression:** the SAME bools but `seasons_in_tenure <= GRACE_PERIOD_SEASONS` ⇒ NOT
  fired by the luxury rule (returns whatever the grace-gated mood path returns — `retained`).
- **Both-bools-required:** `challenge_fired_luxury_tax=True, luxury_tax_paid=False` ⇒ no luxury
  fire; `challenge_fired_luxury_tax=False, luxury_tax_paid=True` ⇒ no luxury fire (falls through
  to the mood path).
- **Default-off byte-identical:** calling `decide_verdict(totals, deltas,
  seasons_in_tenure=...)` with neither new bool yields the SAME `Verdict` as the pre-FIN-05
  decider across the mood / hot-seat / retained matrix.
- `TestNoDjangoImportsLeaked` still passes (no new import).

### 7.2 Model — `matches/tests/test_owner_evaluation_model.py` (EXTEND)
- `fired_reason` default `""`, the three `FIRED_REASON_CHOICES`.
- `League.challenge_fired_luxury_tax` default `False`.

### 7.3 Writer — `matches/tests/test_owner_evaluations_writer.py` (EXTEND, `TestCase`)
- **`fired_reason` values:** a challenge fire (finance ON, toggle ON, managed team's
  `TeamSeasonFinance.luxury_tax > 0`, past grace) writes `verdict="fired"` +
  `fired_reason="luxury_tax"`; a mood fire (no luxury tax, mood `<= -1` past grace) writes
  `fired_reason="owner_mood"`; a retained/hot-seat row writes `fired_reason=""`.
- **Mood recorded normally:** a challenge-fire row still carries the computed
  `wins_delta`/`playoffs_delta`/`money_delta` + cap-chained `*_total` (NOT zeroed); the next
  tenure resets cumulative as for any firing.
- **Finance-OFF inert:** toggle ON but `finance_enabled` OFF ⇒ no `TeamSeasonFinance` row ⇒
  `luxury_tax_paid` False ⇒ never a luxury fire; rows byte-identical to a no-FIN-05 run.
- **Non-career inert:** `mode="multiplayer"` ⇒ writer early-returns, zero rows (CAR-03).

### 7.4 `next_season` — `matches/tests/test_league_next_season.py` (EXTEND)
- **Challenge-fired → picker:** a challenge-fired-and-unreassigned Manager hitting `next_season`
  is redirected to `new_team_picker` and **no new Season is created** (same route as a mood fire).

### 7.5 Create form — `matches/tests/test_league_create.py` (EXTEND)
- A checked `challenge_fired_luxury_tax` POST persists `League.challenge_fired_luxury_tax=True`;
  the DOM id `league-create-challenge-luxury-tax` renders; default unchecked ⇒ `False`.

### 7.6 Eval view — `matches/tests/test_owner_evaluation_view.py` (EXTEND)
- A `fired_reason="luxury_tax"` eval renders `owner-evaluation-fired-reason` with the
  luxury-tax flavour; `"owner_mood"` and legacy `""` render the mood message; a non-fired eval
  renders NO fired-reason element.

**What is internal (not asserted):** the exact `tsf` query placement / `luxury_tax_paid`
local-variable name; the exact wording of the flavour strings beyond a stable substring
("luxury tax"); the migration's auto-generated field boilerplate.

---

## 8 · Scope-out (LOCKED — do NOT build)
No mid-League edit surface for `challenge_fired_luxury_tax` (create-time only). No cross-field
form validation (a toggle on a non-finance League is silently inert). No `Verdict.fired_reason`
on the pure seam (the reason is persisted on the row, never carried through `decide_verdict`).
No re-derivation of `fired_reason` from current state (CONTEXT.md forbids recomputing a past
evaluation — it is stamped at write time). No `next_season` code change. No new ADR (ADR-0026
Consequences addendum). No new CONTEXT.md term. No simulator change → no Score Calibration
re-baseline. No backfill / `RunPython`.

---

### Naming/placement choices the existing code forced (one line)
The writer ALREADY fetches `tsf = TeamSeasonFinance.objects.filter(team_id=team_managed_id,
season=season).first()` for the FIN-01 money axis, so FIN-05 reuses that exact lookup
(`luxury_tax_paid = tsf is not None and tsf.luxury_tax > 0`) rather than re-querying; the new
`decide_verdict` branch lands FIRST inside the existing `past_grace` block (so it respects the
SAME grace gate as mood firing while taking precedence over it); and `fired_reason` is persisted
on the immutable row + read straight off `evaluation` in the template (no new context key, no
recompute), because CONTEXT.md forbids recomputing a past evaluation from current state.
