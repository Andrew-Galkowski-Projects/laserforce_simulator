# CRE-02 — Tiered expected-finish team generation · SEAM CONTRACT

**Branch:** `cre-02-tiered-generation`
**PLAN.md task:** CRE-02 · Tiered expected-finish team generation (~line 412)
**Status of this document:** FROZEN. Code, Tests and Docs agents implement against
these names verbatim. Any name not listed here is internal implementation detail and
must not be asserted on.

---

## 1. What CRE-02 is

CRE-01 shipped the *selection-based* **League difficulty**: all N teams are generated
from one stat distribution and difficulty merely re-picks which of the N (equal) teams
becomes the manager's `current_team`. Because a team's mean active-roster
`overall_rating` averages 114 i.i.d. stat draws, its own SD is approximately 1.4 even at
`std_dev = 15`, so an 8-team league's best-to-worst gap is only about 4 points — Easy vs
Hard is close to a coin-flip among equals.

CRE-02 adds the *generation-based* complement: a create-time **League spread** choice
that draws each of the N competitive Teams from **its own tier mean** on a linear,
mean-preserving ramp. Delta = 0 (Even, default), 8 (Tiered) or 16 (Steep). The league
gains a real preseason favourite / wooden-spoon structure while its **average** talent
is unchanged at every spread.

### 1.1 The five locked decisions

These came out of the grill and are NOT reopenable by any agent.

1. **ONE ORDERING ONLY.** The tier a team was drawn from is a *generation input*. It is
   never persisted, never surfaced, never queried. After generation the League still has
   exactly one ordering: the measured strength rank computed by
   `matches/league_views.py::_rank_teams_by_strength` (mean active-roster
   `overall_rating` DESC, `team_id` ASC). There is **no** "projected finishing order"
   concept, **no** new model field, **no** migration.
2. **League spread is transient.** Even / Tiered / Steep maps to delta 0 / 8 / 16.
   **Even is the default** and MUST be byte-identical to pre-CRE-02 generation.
3. **Gradient is linear and MEAN-PRESERVING.**
   `tier_mean(i) = mean + delta - 2*delta*i/(n-1)` for 0-based `i` in `[0, n-1]`, then
   clamped to `[0, 100]`. Symmetric about `mean`, so the league average is unchanged,
   so there is **no simulated-scoring drift and NO Score Calibration (CAL-01)
   re-baseline obligation**. Degenerate: when `delta == 0` **or** `num_teams < 2`,
   return `[float(mean)] * num_teams` (this doubles as the `n - 1` divide-by-zero
   guard).
4. **Scope is the N competitive Teams only.** `_generate_free_agents` is untouched — the
   League free-agent pool is still drawn at the flat `mean`. Create-time only:
   `next_season` does **not** re-apply the spread (LG-04 development owns team strength
   thereafter).
5. **UI surface is the Advanced create form ONLY** (`/leagues/create/advanced/`). The
   one-click League template chooser (`/leagues/create/`) always creates an **Even**
   league. `LeagueTemplate` (`matches/league_templates.py`) gains **no** new field.

---

## 2. New and changed names — the complete seam

| Name | Owning module | Kind | Status |
|---|---|---|---|
| `LEAGUE_SPREAD_DELTAS` | `teams/player_generator.py` | module constant | NEW |
| `compute_tier_means` | `teams/player_generator.py` | pure function | NEW |
| `_build_player_kwargs` | `teams/views.py` | private helper | CHANGED (type hint only) |
| `_generate_teams` | `teams/views.py` | private helper | CHANGED (additive kwarg) |
| `CreateLeagueForm.LEAGUE_SPREAD_CHOICES` | `matches/forms.py` | class constant | NEW |
| `CreateLeagueForm.league_spread` | `matches/forms.py` | form field | NEW |
| `_template_to_form_data` | `matches/league_views.py` | private helper | CHANGED (one static key) |
| `_create_league_and_season` | `matches/league_views.py` | private writer | CHANGED (body only) |
| `templates/leagues/create_advanced.html` | template | template | CHANGED (one new block) |

---

## 3. `teams/player_generator.py` — the pure seam

This module is the existing LG-00 **pure** module: no Django imports, no ORM, no I/O,
no global RNG state, RNG injected by the caller. CRE-02 adds one constant and one
function. The existing `TestNoDjangoImportsLeaked` check in
`teams/tests/test_player_generator.py` continues to cover the module unchanged — the
new code must not import anything outside the frozen allowlist (`random` for the
`random.Random` type hint only, `typing`).

### 3.1 `LEAGUE_SPREAD_DELTAS`

```python
LEAGUE_SPREAD_DELTAS: dict[str, float] = {
    "even": 0.0,
    "tiered": 8.0,
    "steep": 16.0,
}
```

Exactly three keys, exactly these lowercase key strings, exactly these float values.
The keys are the same tokens the form's `league_spread` ChoiceField accepts, so a
`cleaned_data` value indexes this dict directly.

### 3.2 `compute_tier_means`

```python
def compute_tier_means(num_teams: int, mean: float, delta: float) -> list[float]:
    """CRE-02 — the linear, mean-preserving tier ramp.

    Returns a list of length ``num_teams``; entry ``i`` is the stat mean the
    ``i``-th generated Team's players are drawn from. Index 0 is the strongest
    tier, index ``num_teams - 1`` the weakest.

        tier_mean(i) = clamp(mean + delta - 2 * delta * i / (num_teams - 1), 0.0, 100.0)

    Degenerate cases — ``delta == 0`` or ``num_teams < 2`` — return
    ``[float(mean)] * num_teams`` (this is also the ``n - 1`` divide-by-zero guard).

    PURE: no RNG, no I/O, no Django. Deterministic given its three arguments.
    """
```

**Exact return shape:** `list[float]`, length `== num_teams`, every entry a `float` in
`[0.0, 100.0]`, monotonically **non-increasing** (strictly decreasing when `delta > 0`
and no clamping is in play; non-increasing once clamping flattens the head or tail).

**Behaviour table.**

| Input | Output |
|---|---|
| `num_teams == 0` | `[]` (falls into the `num_teams < 2` branch) |
| `num_teams == 1` | `[float(mean)]` |
| `delta == 0.0` (any `num_teams`) | `[float(mean)] * num_teams` |
| `delta > 0`, `num_teams >= 2` | the ramp, clamped |

**Clamping caveat — MANDATORY for the Tests agent.** Clamping is applied *after* the
ramp and therefore **breaks mean preservation at the extremes**. Mean preservation is
only asserted for un-clamped parameter sets (e.g. `mean=50`). At `mean=95, delta=16,
num_teams=8` the head clamps to `100.0` and the list mean is greater than 95; at
`mean=5, delta=16, num_teams=8` the tail clamps to `0.0` and the list mean is greater
than 5. Assert **bounds and monotonicity** in the clamped cases, never mean
preservation.

### 3.3 Worked example — the arithmetic Code and Tests MUST agree on

Computed from the reference implementation. Use `assertAlmostEqual(..., places=6)`;
do not pin the exact float repr.

`compute_tier_means(8, 50, 8)` — Tiered:

| i | value |
|---|---|
| 0 | 58.000000 |
| 1 | 55.714286 |
| 2 | 53.428571 |
| 3 | 51.142857 |
| 4 | 48.857143 |
| 5 | 46.571429 |
| 6 | 44.285714 |
| 7 | 42.000000 |

List mean = **50.0** (exactly, in IEEE double). Head minus tail gap = **16.0**.

`compute_tier_means(8, 50, 16)` — Steep:

| i | value |
|---|---|
| 0 | 66.000000 |
| 1 | 61.428571 |
| 2 | 56.857143 |
| 3 | 52.285714 |
| 4 | 47.714286 |
| 5 | 43.142857 |
| 6 | 38.571429 |
| 7 | 34.000000 |

List mean = **50.0** (exactly). Head minus tail gap = **32.0**.

Clamping reference sets (bounds + monotonicity only, no mean assertion):

- `compute_tier_means(8, 95, 16)` -> `[100.0, 100.0, 100.0, 97.285714, 92.714286, 88.142857, 83.571429, 79.0]`
- `compute_tier_means(8, 5, 16)` -> `[21.0, 16.428571, 11.857143, 7.285714, 2.714286, 0.0, 0.0, 0.0]`

Degenerate reference sets:

- `compute_tier_means(1, 50, 16)` -> `[50.0]`
- `compute_tier_means(8, 50, 0)` -> `[50.0] * 8`

---

## 4. `teams/views.py` — the generation call seam

### 4.1 `_build_player_kwargs` — type hint widened

Current (line ~766):

```python
def _build_player_kwargs(rng: random.Random, mean: int, std_dev: int) -> dict:
```

Final:

```python
def _build_player_kwargs(rng: random.Random, mean: float, std_dev: int) -> dict:
```

**This is the ONLY change to this function** — the body is untouched. The widening is
mandatory because `compute_tier_means` returns `list[float]` and `_generate_teams`
will pass `tier_means[i]` (a `float`) through to it; the downstream
`draw_stats(rng, mean: float, std_dev: float)` already types `mean` as `float`, so the
current `int` annotation is the only dishonest link in the chain. `std_dev` stays `int`
(nothing in CRE-02 makes it fractional). Passing an `int` where `float` is annotated is
valid under the PEP 484 numeric tower, so the three existing int-mean call sites need
no change.

### 4.2 `_generate_teams` — additive keyword-only parameter

Final signature (the new parameter is appended **last**, after `player_names_pool`):

```python
def _generate_teams(
    num_teams: int,
    players_per_team: int,
    *,
    rng: random.Random,
    mean: int,
    std_dev: int,
    team_names_pool: list[str],
    player_names_pool: list[str],
    tier_means: "list[float] | None" = None,
) -> list[Team]:
```

Return shape is unchanged: `list[Team]`, length `num_teams`, in creation order.

**Semantics.**

| `tier_means` | Behaviour |
|---|---|
| `None` (default) | **Byte-identical to pre-CRE-02.** Every Player of every Team is built with `_build_player_kwargs(rng, mean, std_dev)` exactly as today. Identical RNG consumption, identical outputs for a given seeded `rng`. |
| a `list[float]` of length `num_teams` | Team at 0-based creation index `i` draws **all** its players (including bench players 7+) with `_build_player_kwargs(rng, tier_means[i], std_dev)`. The `mean` parameter is then unused for those draws. |
| a list whose length `!= num_teams` | **Raise `ValueError`** before any DB write. |

**Index-to-team mapping is locked.** `_generate_teams` iterates
`for _team_idx in range(num_teams)`; `tier_means[0]` belongs to the **first** Team
created (the strongest tier) and `tier_means[num_teams - 1]` to the last (the weakest).
Nothing else about the loop changes — name popping, `Team.objects.create`,
`Player.objects.create`, `_assign_team_slots`, `team.save()` and the append to
`created_teams` all stay in their current order, so RNG consumption per player is
unchanged.

**Length-mismatch policy — RAISE (locked).** Silent tolerance would let a
`num_teams` / `tier_means` drift ship undetected and produce a mis-tiered league. Guard
at the top of the function, before the loop and before any ORM write:

```python
if tier_means is not None and len(tier_means) != num_teams:
    raise ValueError(
        f"tier_means has {len(tier_means)} entries but num_teams is {num_teams}"
    )
```

Tests may match on the substring `"tier_means"` in the exception message; the full
wording above is the locked text.

**Source compatibility.** The parameter is keyword-only with a default, so all three
existing call sites stay source-compatible and **must not be edited except for the one
league-create site named in section 6.2**:

| Call site | File:line (pre-change) | CRE-02 action |
|---|---|---|
| `generate_players` view | `teams/views.py:931` | **UNCHANGED** |
| Tournament team generation | `matches/tournament_views.py:201` | **UNCHANGED** |
| `_create_league_and_season` | `matches/league_views.py:1247` | passes `tier_means=` |

---

## 5. `matches/forms.py` — `CreateLeagueForm.league_spread`

Mirrors the CRE-01 `difficulty` field's style exactly (class-constant choices tuple,
`ChoiceField`, `initial`, `required=False`, `forms.Select` with an `id` plus a
`form-select` class, explicit `label`).

### 5.1 The class constant

Declared adjacent to the existing `DIFFICULTY_CHOICES` constant in the class body:

```python
    # CRE-02 — transient League spread selector. Consumed at create time only to
    # build the per-team tier-mean vector handed to ``_generate_teams``. No League
    # field, no migration. Advanced form only — the template chooser is always Even.
    LEAGUE_SPREAD_CHOICES = (
        ("even", "Even"),
        ("tiered", "Tiered"),
        ("steep", "Steep"),
    )
```

Exactly three tuples, in this order, with these exact value tokens and these exact
human labels. The value tokens are the `LEAGUE_SPREAD_DELTAS` keys.

### 5.2 The field

Declared immediately **after** the existing `difficulty` field in the class body (so
the two transient create-time selectors sit together):

```python
    # CRE-02 — transient League spread (consumed at create time only).
    # ``required=False`` so an Advanced POST that omits it stays valid (defaults
    # Even); the tier-vector build coerces a falsy/unknown value to "even".
    league_spread = forms.ChoiceField(
        choices=LEAGUE_SPREAD_CHOICES,
        initial="even",
        required=False,
        widget=forms.Select(
            attrs={"id": "league-create-league-spread", "class": "form-select"}
        ),
        label="League spread",
    )
```

**Locked attributes:** field name `league_spread`; widget DOM id
`league-create-league-spread`; widget class `form-select`; `initial="even"`;
`required=False`; label `"League spread"`.

`required=False` means an omitted `league_spread` yields
`cleaned_data["league_spread"] == ""` (Django's `ChoiceField` empty value), which the
create writer coerces to `"even"` — this is the exact CRE-01 `difficulty` precedent and
is why the view tests can assert "omitting the field still succeeds".

---

## 6. `matches/league_views.py`

### 6.1 `_template_to_form_data` — one static key, signature UNCHANGED

**The signature does NOT gain a parameter.** It stays:

```python
def _template_to_form_data(
    template: LeagueTemplate, *, league_name: str, difficulty: str
) -> "dict[str, object]":
```

Rationale: the chooser surface has no spread selector at all (locked decision 5), so
the value is a static constant, not caller data. Threading a parameter through would
imply the chooser can vary it.

The returned dict gains **exactly one** new key, placed adjacent to the existing
`"difficulty"` key:

```python
        "difficulty": difficulty,
        # CRE-02 — the chooser has no spread selector; templates are always Even.
        "league_spread": "even",
```

All other keys keep their current values and the `LeagueTemplate` dataclass gains
**no** field.

### 6.2 `_create_league_and_season` — the tier vector

**New import**, inserted between `from teams.models import Player, Team` (line ~37) and
`from teams.views import _coerce_dir, _generate_free_agents, _generate_teams`
(line ~38):

```python
from teams.player_generator import LEAGUE_SPREAD_DELTAS, compute_tier_means
```

Exactly this import — both names, from `teams.player_generator` (the pure module), NOT
re-exported through `teams.views`.

**Body change**, immediately before the existing `_generate_teams(...)` call at line
~1247:

```python
    # CRE-02 — transient League spread -> per-team tier-mean vector. A falsy or
    # unknown value coerces to "even" (delta = 0), and delta = 0 passes
    # ``tier_means=None`` so Even generation is byte-identical to pre-CRE-02.
    spread = cleaned.get("league_spread") or "even"
    delta = LEAGUE_SPREAD_DELTAS.get(spread, 0.0)
    tier_means = (
        compute_tier_means(cleaned["num_teams"], cleaned["mean"], delta)
        if delta
        else None
    )

    created_teams = _generate_teams(
        cleaned["num_teams"],
        6,
        rng=rng,
        mean=cleaned["mean"],
        std_dev=cleaned["std_dev"],
        team_names_pool=team_names_pool,
        player_names_pool=player_names_pool,
        tier_means=tier_means,
    )
```

**Coercion rules — locked, exact.**

| `cleaned.get("league_spread")` | `spread` | `delta` | `tier_means` |
|---|---|---|---|
| `"even"` | `"even"` | `0.0` | `None` |
| `"tiered"` | `"tiered"` | `8.0` | 8-entry ramp |
| `"steep"` | `"steep"` | `16.0` | delta-16 ramp |
| `""` (field omitted) | `"even"` | `0.0` | `None` |
| `None` (key absent) | `"even"` | `0.0` | `None` |
| any unknown string (e.g. `"insane"`) | that string | `0.0` (dict `.get` default) | `None` |

Two independent layers make an unknown value harmless: `or "even"` handles falsy, and
`LEAGUE_SPREAD_DELTAS.get(spread, 0.0)` handles unknown-but-truthy. The
`if delta else None` step is what guarantees the byte-identical Even path — Even never
constructs a list at all.

**Everything else in `_create_league_and_season` is unchanged**, in particular:

- `rng = random.Random()` stays **UNSEEDED** (this is why the view layer must not
  assert on strength — see section 8.3).
- the `_generate_free_agents(...)` call still passes the flat `mean=cleaned["mean"]`.
- `_pick_manager_team(created_teams, cleaned.get("difficulty") or "medium")` is
  untouched and still reads the measured strength rank.
- the manager rename, League/Season creation, `season.teams.add`, `map_pool.set`, the
  `SeasonPhase` loop, `_write_baseline_ratings` and the FIN budget seeding all keep
  their current order.

---

## 7. Template — `templates/leagues/create_advanced.html`

**File:** `laserforce_simulator/templates/leagues/create_advanced.html`
**This is the ONLY template CRE-02 touches.** `templates/leagues/create.html` (the
chooser) is **NOT** edited.

**Insertion point (locked).** Immediately **after** the closing `</div>` of the existing
`<div class="row g-2 mb-3">` block that holds `Stat mean` plus `Stat standard deviation`
(the row opens at line 81 and closes at line 92 in the pre-change file), and
immediately **before** the `{# LG-02-Part2b -- vanilla-JS phase composer. #}` comment
line (line 94 pre-change). The new block is a sibling `<div class="mb-3">`, matching
the surrounding blocks' shape.

**Exact block to insert:**

```html
        {# CRE-02 -- transient League spread; Advanced form only. #}
        <div class="mb-3">
            <label for="league-create-league-spread" class="form-label">League spread</label>
            {{ form.league_spread }}
            <div class="form-text">
                How unequal the generated league is. Even draws every team from the
                same stat distribution; Tiered and Steep ramp the teams from a
                strong projected favourite down to a weak projected wooden spoon,
                keeping the league's average talent the same.
            </div>
            {{ form.league_spread.errors }}
        </div>
```

The `for=` attribute must equal the widget's locked DOM id
`league-create-league-spread`. The help-text wording is guidance, not a locked string —
tests assert on the DOM id, not on the prose.

---

## 8. Test boundary

### 8.1 What Tests MAY assert on (the public seam)

- `teams.player_generator.LEAGUE_SPREAD_DELTAS` — its three keys and three float values.
- `teams.player_generator.compute_tier_means(num_teams, mean, delta)` — return type,
  length, per-index values (`assertAlmostEqual`, `places=6`), monotonicity, bounds,
  mean preservation (un-clamped inputs only), and the degenerate branches.
- `teams.views._generate_teams(..., tier_means=[...], rng=random.Random(42))` — the
  created Teams' mean active-roster `overall_rating`, and the `ValueError` on a
  length mismatch.
- `matches.forms.CreateLeagueForm` — the presence, `required`, `initial` and `choices`
  of `league_spread`; form validity with each token, with the field omitted, and with
  an unknown token.
- `matches.league_views._template_to_form_data(...)` — that the returned dict carries
  `"league_spread": "even"`.
- The Advanced POST at `reverse("league_create_advanced")` — HTTP status, that a
  `League` plus a draft `Season` plus N `Team`s were created, and that the rendered GET
  page contains the DOM id `league-create-league-spread`.

### 8.2 What Tests MUST NOT assert on (internal detail)

- The exact float repr of any tier mean (use `assertAlmostEqual`).
- Which specific Team object landed at which tier — nothing records the tier, and the
  contract explicitly forbids inferring one. Tier-0 vs tier-(N-1) DB assertions are made
  on the **return list order** of `_generate_teams` (`created_teams[0]` vs
  `created_teams[-1]`), which is creation order, not a persisted ranking.
- Any relationship between the tier ramp and `_rank_teams_by_strength` /
  `_pick_manager_team` output — RNG means the measured rank legitimately disagrees with
  the tier order for some teams.
- The number or order of `rng` calls inside `_build_player_kwargs`.
- The help-text prose in the template.
- Anything about `_generate_free_agents` behaviour changing (it does not).
- League-average preservation measured on generated *Teams* — the ramp preserves the
  mean of the *tier vector*, not the empirical mean of a finite random sample.

### 8.3 The view-layer flakiness rule — MANDATORY

`_create_league_and_season` builds `rng = random.Random()` **unseeded**. Therefore:

> **No view-layer test may assert anything about team strength, tier ordering, or the
> strong-vs-weak gap.** View tests assert form validity, HTTP status, object counts, and
> DOM ids only. All strength assertions live at the `_generate_teams` layer, where the
> test injects `rng=random.Random(42)`.

### 8.4 Determinism obligations

- `compute_tier_means` is pure and deterministic — no seeding needed.
- Every `_generate_teams` DB test injects `rng=random.Random(42)` (or another fixed
  seed) and asserts a **direction / magnitude** relationship, e.g. "the tier-0 team's
  mean active-roster `overall_rating` is meaningfully greater than the tier-(N-1)
  team's", never an exact point total.
- A `tier_means=None` test must show the None path is unchanged: two
  `_generate_teams(..., rng=random.Random(SEED))` runs — one with the kwarg omitted,
  one with `tier_means=None` — produce identical per-player stat sequences. (Use a
  fresh DB state / distinct name pools per run and compare the ordered stat values, not
  PKs.)

---

## 9. Test file placement (per CLAUDE.md conventions)

Verified against the existing layout: `teams/tests/test_player_generator.py` is the
pure-unit home for `player_generator` (plain `unittest.TestCase`, no Django imports,
already carries `TestNoDjangoImportsLeaked`); `teams/tests/test_generate_view.py` covers
the LG-00 *view*, not the `_generate_teams` helper directly;
`matches/tests/test_league_create.py` and `matches/tests/test_league_templates.py` cover
the league-create surfaces and are both large shared files.

| Layer | File | Status | Classes |
|---|---|---|---|
| PURE unit — `compute_tier_means`, `LEAGUE_SPREAD_DELTAS` | `laserforce_simulator/teams/tests/test_player_generator.py` | **APPEND ONLY** — add one new class at the end of the file; do not touch existing classes | `TestComputeTierMeans` |
| DB — `_generate_teams(tier_means=...)` | `laserforce_simulator/teams/tests/test_generate_teams_tiered.py` | **NEW FILE** | `TestGenerateTeamsTierMeans` |
| Form + view — Advanced POST, `_template_to_form_data` | `laserforce_simulator/matches/tests/test_league_spread.py` | **NEW FILE** | `TestCre02LeagueSpreadFormField`, `TestCre02AdvancedCreateWithSpread`, `TestCre02TemplateFormDataIsAlwaysEven` |

Two new files (rather than appending to `test_league_create.py` /
`test_generate_view.py`) keep the Tests agent off files the Code agent is not editing
and off files other in-flight work may touch.

### 9.1 Minimum test coverage

`TestComputeTierMeans` (pure, `unittest.TestCase`, no Django):

- returns a list of length `num_teams` for each of `LEAGUE_SPREAD_DELTAS`' three deltas
- monotonically non-increasing for `delta = 8` and `delta = 16`
- mean preserved (`assertAlmostEqual(sum(v)/len(v), mean, places=6)`) at `mean=50` for
  both non-zero deltas and for `num_teams` in `{2, 4, 8, 16}`
- the two section-3.3 worked-example vectors match index-by-index (`places=6`)
- clamping at `mean=95, delta=16, num_teams=8` — every entry `<= 100.0`, head entries
  equal `100.0`, list still non-increasing, **no mean assertion**
- clamping at `mean=5, delta=16, num_teams=8` — every entry `>= 0.0`, tail entries equal
  `0.0`, list still non-increasing, **no mean assertion**
- `num_teams == 1` -> `[float(mean)]`
- `num_teams == 0` -> `[]`
- `delta == 0` -> `[float(mean)] * num_teams`, for `num_teams` in `{1, 8}`
- `LEAGUE_SPREAD_DELTAS` has exactly the keys `{"even", "tiered", "steep"}` mapping to
  `0.0 / 8.0 / 16.0`

`TestGenerateTeamsTierMeans` (Django `TestCase`):

- `tier_means=[...]` of the right length creates `num_teams` Teams and the returned
  list length matches
- with `rng=random.Random(42)`, `num_teams=8`, `mean=50`, `std_dev=15`,
  `tier_means=compute_tier_means(8, 50, 16)`: `created_teams[0]`'s mean active-roster
  `overall_rating` is meaningfully greater than `created_teams[-1]`'s (assert a gap
  comfortably below the ~32-point expectation, e.g. `> 10`, so the test is robust)
- the same call is deterministic: two runs at the same seed produce the same ordered
  per-team mean `overall_rating` values
- `tier_means=None` (and the kwarg omitted entirely) produce identical generation at the
  same seed — the byte-identical-Even pin
- a length mismatch (`num_teams=4`, `tier_means` of length 3 and of length 5) raises
  `ValueError` and creates **zero** Teams and **zero** Players
- every generated Player's stats stay in `[0, 100]` under `tier_means` (the `draw_stats`
  clamp still applies at extreme tier means)

`TestCre02LeagueSpreadFormField` (Django `TestCase`):

- `CreateLeagueForm()` has a `league_spread` field
- it is `required=False` and `initial == "even"`
- its choices are exactly `(("even", "Even"), ("tiered", "Tiered"), ("steep", "Steep"))`
- a full valid payload with `league_spread` set to each of the three tokens is valid
- a full valid payload **omitting** `league_spread` is valid and yields
  `cleaned_data["league_spread"] == ""`
- `GET /leagues/create/advanced/` renders the DOM id `league-create-league-spread`

`TestCre02AdvancedCreateWithSpread` (Django `TestCase`):

- POST to `reverse("league_create_advanced")` with `league_spread="tiered"` redirects
  and creates one `League`, one draft `Season`, and `num_teams` enrolled Teams
- the same with `league_spread="steep"`
- the same with `league_spread` **omitted** still succeeds (the Even fallback)
- the same with an unknown `league_spread` value: the form rejects it (`ChoiceField`
  validation) and nothing is created. Assert whichever path the test actually
  exercises — do not assert a strength outcome either way.
- **NO strength assertions anywhere in this class** (section 8.3)

`TestCre02TemplateFormDataIsAlwaysEven` (Django `TestCase`):

- `_template_to_form_data(t, league_name="X", difficulty="medium")["league_spread"] == "even"`
  for **every** entry in `LEAGUE_TEMPLATES`
- the resulting dict still produces a valid `CreateLeagueForm` for every template
- a chooser POST (`reverse("league_create")` with a valid `template` key) still creates
  a League plus a draft Season

---

## 10. Explicitly NOT changed

The Code agent must produce a **zero diff** for every item below. The Tests agent must
not write a test that would require changing any of them.

| Item | Location | Why untouched |
|---|---|---|
| `_pick_manager_team` | `matches/league_views.py:884` | CRE-01's difficulty pick reads the measured rank; the tier ramp is invisible to it (decision 1) |
| `_rank_teams_by_strength` | `matches/league_views.py:873` | the single measured ordering — CRE-02 adds no second ordering (decision 1) |
| `_seed_team_budgets_by_strength` | `matches/league_views.py` | FIN-03 seeding reads the same measured rank; unaffected |
| `_generate_free_agents` | `teams/views.py:847` | the pool stays flat at `mean` (decision 4) |
| `_assign_team_slots` | `teams/views.py` | slot assignment is tier-independent |
| `draw_stats`, `draw_preferred_roles`, `assign_slots` | `teams/player_generator.py` | LG-00 surface frozen; CRE-02 only **adds** to the module |
| `LeagueTemplate` dataclass plus `LEAGUE_TEMPLATES` | `matches/league_templates.py` | no new field, no new template row (decision 5) |
| `templates/leagues/create.html` (the chooser) | templates | chooser has no spread selector (decision 5) |
| `_template_to_form_data` **signature** | `matches/league_views.py:1199` | static value only (section 6.1) |
| `next_season` / `_develop_league_for_new_season` | `matches/league_views.py` | create-time only; LG-04 owns strength thereafter (decision 4) |
| Any model, any field, any migration | `teams/models.py`, `matches/models.py`, `*/migrations/` | the tier is a generation input, never persisted (decision 1) |
| The simulator, `BatchSimulator`, all sim RNG | `matches/sim_helpers/`, `matches/simulation.py` | no simulation behaviour change |
| Score Calibration / CAL-01 baselines | wherever they live | the ramp is mean-preserving, so no scoring drift, so **no re-baseline** |
| `_generate_teams` call site in `generate_players` | `teams/views.py:931` | keyword-only default keeps it source-compatible |
| `_generate_teams` call site in tournament creation | `matches/tournament_views.py:201` | same |
| `CONTEXT.md` | repo root | the **League spread** term and the **League difficulty** amendment were already written during the grill (already present as a working-tree change) — **the Docs agent must NOT re-add or re-edit them** |
| REST API, serializers, admin | `teams/api_views.py`, `teams/serializers.py`, `*/admin.py` | nothing new is exposed |
| ADR | `docs/adr/` | no new ADR — the decision is reversible (transient form field plus pure function plus additive kwarg) |

---

## 11. File ownership — three parallel agents, zero collisions

| Agent | Files it may write | Notes |
|---|---|---|
| **Code** | `laserforce_simulator/teams/player_generator.py` (sections 3.1 + 3.2) | new constant plus new function only |
| | `laserforce_simulator/teams/views.py` (sections 4.1 + 4.2) | one type hint, one kwarg, one guard, one call-arg swap inside the player loop |
| | `laserforce_simulator/matches/forms.py` (section 5) | one class constant plus one field |
| | `laserforce_simulator/matches/league_views.py` (section 6) | one import, one dict key, one 8-line block, one kwarg |
| | `laserforce_simulator/templates/leagues/create_advanced.html` (section 7) | one inserted block |
| **Tests** | `laserforce_simulator/teams/tests/test_player_generator.py` | **APPEND ONE CLASS AT END ONLY** |
| | `laserforce_simulator/teams/tests/test_generate_teams_tiered.py` | new file |
| | `laserforce_simulator/matches/tests/test_league_spread.py` | new file |
| **Docs** | `laserforce_simulator/teams/CLAUDE.md` | add a `## CRE-02 tiered generation` note after the LG-00 generation section describing `LEAGUE_SPREAD_DELTAS`, `compute_tier_means`, and the `_generate_teams` kwarg |
| | `laserforce_simulator/matches/CLAUDE.md` | add a `## CRE-02 league spread` subsection after the existing `## CRE-01 league templates + difficulty` section (line ~1465) |
| | `PLAN.md` | mark CRE-02 done, replace the "Open questions for its own grill" paragraph with the resolved decisions plus a link to this contract |

**Nobody writes `CONTEXT.md`** — the grill already did (see section 10).
**Nobody writes `docs/adr/`** — no ADR for CRE-02.
**Nobody writes a migration** — there is no model change to migrate.

After the agents return: run `python -m black laserforce_simulator` from the repo root,
then the full `pytest -n auto` suite, and report exact pass/fail counts.

---

## 12. Quick reference — the whole seam in one block

```python
# teams/player_generator.py
LEAGUE_SPREAD_DELTAS: dict[str, float] = {"even": 0.0, "tiered": 8.0, "steep": 16.0}
def compute_tier_means(num_teams: int, mean: float, delta: float) -> list[float]: ...

# teams/views.py
def _build_player_kwargs(rng: random.Random, mean: float, std_dev: int) -> dict: ...
def _generate_teams(
    num_teams: int, players_per_team: int, *, rng: random.Random,
    mean: int, std_dev: int, team_names_pool: list[str],
    player_names_pool: list[str], tier_means: "list[float] | None" = None,
) -> list[Team]: ...

# matches/forms.py — CreateLeagueForm
LEAGUE_SPREAD_CHOICES = (("even", "Even"), ("tiered", "Tiered"), ("steep", "Steep"))
league_spread = forms.ChoiceField(
    choices=LEAGUE_SPREAD_CHOICES, initial="even", required=False,
    widget=forms.Select(
        attrs={"id": "league-create-league-spread", "class": "form-select"}
    ),
    label="League spread",
)

# matches/league_views.py
from teams.player_generator import LEAGUE_SPREAD_DELTAS, compute_tier_means
# _template_to_form_data(...) -> {..., "league_spread": "even", ...}  # signature unchanged
# _create_league_and_season(form):
spread = cleaned.get("league_spread") or "even"
delta = LEAGUE_SPREAD_DELTAS.get(spread, 0.0)
tier_means = (
    compute_tier_means(cleaned["num_teams"], cleaned["mean"], delta) if delta else None
)
created_teams = _generate_teams(..., tier_means=tier_means)
```
