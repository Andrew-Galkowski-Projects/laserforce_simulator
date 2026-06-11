# The Potential (`pot`) Rating

This document explains what "potential" means in ZenGM and exactly how it is
calculated. Understanding `pot` requires first understanding `ovr`, so we cover
both.

## What `pot` represents

> **`pot` is an estimate of the highest `ovr` rating a player is expected to
> reach by the time he turns 29.**

It is on the same 0–100 scale as `ovr`. A 19-year-old with `ovr 50` and
`pot 75` is projected to peak around 75 overall if he develops normally. Once a
player is 29 or older, **`pot` is simply equal to `ovr`** — there is no more
projected growth (`develop.ts:38-39`, `potEstimator.ts:234-235`).

`pot` is recalculated every time a player develops (each preseason, on draft
generation, etc.) and stored on the current ratings row.

## Step 1: the overall rating (`ovr`)

`pot` is built on top of `ovr`, so here is how `ovr` is computed.

For basketball, `ovr.basketball.ts:10-58` is a **fixed linear formula** — a
weighted sum of the individual ratings, each centered on its league-average
value:

```
r = 0.159*(hgt-47.5) + 0.0777*(stre-50.2) + 0.123*(spd-50.8)
  + 0.051*(jmp-48.7) + 0.0632*(endu-39.9) + 0.0126*(ins-42.4)
  + 0.0286*(dnk-49.5) + 0.0202*(ft-47.0) + 0.0726*(tp-47.1)
  + 0.133*(oiq-46.8) + 0.159*(diq-46.7) + 0.059*(drb-54.8)
  + 0.062*(pss-51.3) + 0.01*(fg-47.0) + 0.01*(reb-51.4) + 48.5
```

The coefficients were derived by regression (`analysis/player-ovr-basketball`).
Note the most heavily weighted ratings are **height, defensive IQ, and offensive
IQ** (0.159, 0.159, 0.133) — these matter most for overall quality.

A piecewise **fudge factor** is then added (`ovr.basketball.ts:35-46`) to keep
the `ovr` scale consistent with the pre-2018 ratings rescaling, and the result is
rounded and clamped to [0, 100].

Other sports compute `ovr` differently and **per position**: football, hockey,
and baseball produce an `ovrs` map (one `ovr` per position) and pick the best one
(`develop.ts:159-197`, with formulas in `ovr.<sport>.ts`).

## Step 2: projecting the peak — `monteCarloPot`

The conceptual definition of `pot` is: *simulate this player aging up to 29 many
times, and take a high percentile of his best `ovr` along the way.* This is
implemented in `monteCarloPot` (`develop.ts:25-93`).

### The "true" Monte Carlo method

When the full simulation is used (`develop.ts:73-92`):

1. Make `NUM_SIMULATIONS = 20` copies of the player's ratings.
2. For each copy, repeatedly call `developSeason()` to age the player one year at
   a time up to age 29, recomputing `ovr` after each season and tracking the
   **maximum** `ovr` reached.
3. Collect the 20 max-ovr results and return the **75th percentile**
   (`maxOvrs.sort()[floor(0.75 * 20)]`, `develop.ts:92`).

Using the 75th percentile (rather than the mean or max) means `pot` reflects a
*good-but-not-best-case* development outcome. Development is random (see the
development doc), so this answers "how good could this player realistically get?"

If the player is already ≥29, it short-circuits and returns current `ovr`
(`develop.ts:38-39`).

### The fast method — the **pot estimator** (regression)

Running 20 aging simulations per player is expensive, especially for sports that
must compute `pot` for *every position*. So in most cases ZenGM uses a
precomputed regression model instead, `potEstimator.ts`.

`monteCarloPot` chooses the estimator when (`develop.ts:42-71`):

- The sport is **baseball, football, or hockey** (always — too many positions to
  simulate), **or**
- Basketball with a very large number of teams
  (`numActiveTeams >= TOO_MANY_TEAMS_TOO_SLOW`), or when the caller explicitly
  passes `usePotEstimator`.

**How the estimator works** (`potEstimator.ts`): it is a linear regression that
predicts `pot` from the player's current `ovr` and `age`. The coefficients were
fit by running the real `monteCarloPot` many times and regressing the results —
see the `analysis/pot-estimator-*` folders.

For **basketball** (`potEstimator.ts:233-241`):

```
pot = 72.314 - 2.3306 * age + 0.8331 * ovr + randInt(-2, 2)
```

(and `pot = ovr` if age ≥ 29). The `randInt(-2, 2)` adds a little noise so the
estimate isn't perfectly deterministic.

For **football / hockey / baseball** (`potEstimator.ts:18-230`), there is a
separate set of coefficients **per position**, with an `age × ovr` interaction
term:

```
pot = intercept + age*age_coeff + ovr*ovr_coeff
    + interaction*(age*ovr) + randInt(-2, 2)
```

For example, a football QB uses
`intercept 47.34, age -1.785, ovr 2.121, interaction -0.0424`
(`potEstimator.ts:148-153`). Some positions are capped (e.g. football K/P at 75,
hockey G at 90, `potEstimator.ts:5-10`).

### Final clamp

Whichever method is used, the result is forced to be **at least the current
`ovr`** (a player's potential can never be below his present ability,
`develop.ts:66-70`, `potEstimator.ts:215-219`) and clamped to [0, 100].

## Where `pot` is written

`pot` is set inside `develop` after ratings are updated
(`develop.ts:144-198`):

- **Basketball**: `ratings.pot = await monteCarloPot({ratings, age, srID})`
  (`develop.ts:150`).
- **Other sports**: a `pots` map is computed, one entry per position, and the
  player's main-position value becomes `ratings.pot`
  (`develop.ts:174-195`).

For undrafted players, the value is also copied into `p.draft.pot` so the draft
prospect's potential is preserved (`develop.ts:226-234`).

`pot` can be skipped (`skipPot` flag) for speed in tests/debug
(`develop.ts:127`).

## How `pot` is used

- **Displayed** to the user (fuzzed via `fuzzRating`) so they can scout
  prospects.
- **Draft ordering**: prospects declare for the draft and are sorted largely by
  `pot` (`genPlayersWithoutSaving.ts:157-162`).
- **Player `value`**: the internal worth score blends current ability and `pot`,
  weighted by age, in `valueCombineOvrPot.ts` — younger players lean heavily on
  `pot` (70% at age ≤19), older players almost entirely on current ability
  (`value.ts:127-162`, `valueCombineOvrPot.ts`). See the development doc for more
  on `value`.

## Summary

```
pot = projected peak ovr by age 29

monteCarloPot(ratings, age):
  if age >= 29:                return ovr
  if estimator applies:        pot = regression(ovr, age[, pos]) + noise
  else (basketball, normal):   simulate aging ×20, take 75th-pctile max ovr
  return clamp( max(pot, ovr), 0, 100 )
```
