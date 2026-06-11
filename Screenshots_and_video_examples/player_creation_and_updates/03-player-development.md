# How Players Increase / Decrease in Skill Over Seasons

This document explains player **development**: how individual ratings change as a
player ages season to season, why young players improve and old players decline,
and how coaching and randomness factor in. Basketball is the worked example.

## When development happens

Development is driven by `player.develop()` (`develop.ts:107-239`). The main
real-game trigger is the **preseason phase**: for every active player,
`newPhasePreseason.ts:382-384` runs:

```ts
player.addRatingsRow(p, scoutingLevel);     // copy last season's ratings forward
await player.develop(p, 1, false, coachingLevels[p.tid]);  // age + develop 1 yr
```

`addRatingsRow` (`addRatingsRow.ts`) duplicates the previous season's ratings as
the new current row (averaging in fresh fuzz), so development always mutates the
*new* season's row while the history is preserved.

Other callers:

- `develop(p, 0)` — develops **zero** seasons but still recomputes
  `ovr`/`pot`/`skills`/`value`. Used right after `generate` and whenever ratings
  are edited (e.g. God Mode, re-signing fuzz changes,
  `newPhaseResignPlayers.ts:332`).
- `develop(p, years, newPlayer=true)` — used when creating players who need to be
  aged forward several seasons (new leagues, prospects staying "in college").
- `processScheduledEvents.ts` ages players when leagues jump multiple seasons.

## The `develop` driver

`develop(p, years, newPlayer, coachingLevel, skipPot)` (`develop.ts:107-239`):

1. Reads the current ratings row and the player's age
   (`ratings.season - p.born.year`).
2. Loops `years` times; each iteration may bump age (the age-increment rule is
   subtle — see comment at `develop.ts:133`) and calls
   `developSeason(ratings, age, srID, coachingLevel, false)` **unless the ratings
   are locked** (`develop.ts:132-141`).
3. After aging, recomputes:
   - `ovr` (`ovr()`),
   - `pot` (`monteCarloPot()`, unless `skipPot`),
   - position (`pos()`),
   - `skills` (`skills()`),
   - and for football, an updated `weight` (`develop.ts:144-224`).
4. For undrafted players, copies ovr/pot/skills into `p.draft`.

The single-season rating changes happen inside `developSeason`.

## `developSeason` — the core aging model (basketball)

`developSeason.basketball.ts:208-241` changes each rating for one season. There
are three ingredients: a **base change** (age-driven), a **per-rating age
modifier**, and **randomness**, all combined and then clamped.

### 1. Base change by age

`calcBaseChange(age, coachingLevel)` (`developSeason.basketball.ts:171-206`)
produces the overall direction of change. The age table:

| Age | Base change |
| --- | --- |
| ≤ 21 | **+2** |
| 22–25 | **+1** |
| 26–27 | 0 |
| 28–29 | −1 |
| 30–31 | −2 |
| 32–34 | −3 |
| 35–40 | −4 |
| 41–43 | −5 |
| 44+ | −6 |

So young players trend up, players peak in their mid-to-late 20s, and older
players decline — increasingly fast.

**Noise** is then added to the base change (`developSeason.basketball.ts:194-201`):

- age ≤ 23: `+ bound(realGauss(0, 5), -4, 20)` — huge upside, young players can
  break out
- age ≤ 25: `+ bound(realGauss(0, 5), -4, 10)`
- otherwise: `+ bound(realGauss(0, 3), -2, 4)` — much tighter

This is why a young prospect's development is volatile (some bust, some boom) but
veterans change predictably.

**Coaching** scales the magnitude (`developSeason.basketball.ts:203`):

```ts
val *= 1 + sign(val) * coachingEffect(coachingLevel);
```

`coachingEffect` (`budgetLevels.ts:66-69`) is `0.09 * levelToEffect(level)`,
ranging roughly ±0.09. Good coaching amplifies positive growth and *softens*
decline (because `sign(val)` flips the multiplier when `val` is negative). At the
default budget level (34) the effect is 0.

### 2. Per-rating age modifiers and change limits

A single base change isn't enough — different ratings develop on different
curves. Each rating has a `RatingFormula` with an `ageModifier(age)` and
`changeLimits(age)` (`developSeason.basketball.ts:10-169`):

- **Shooting ratings** (`ins, ft, fg, tp`, plus `dnk`,
  `developSeason.basketball.ts:15-33`): `ageModifier` is 0 until 27, then
  positive (+0.5 → +2). This *reverses* the age-related decline from
  `calcBaseChange`, modeling how shooters keep their touch into their 30s. Change
  limits `[-3, 13]`.
- **IQ ratings** (`oiq, diq`, `developSeason.basketball.ts:34-68`): big positive
  modifier when young (+4 at ≤21, +3 at ≤23), since basketball IQ grows with
  experience; like shooting it also resists late decline. Young players can gain
  a lot (limit up to `[-3, 7 + 5*(24-age)]` → up to +32 at age 19).
- **Speed / jump** (`developSeason.basketball.ts:74-117`): increasingly *negative*
  modifiers with age (athleticism fades), limits `[-12, 2]` — they can crash but
  barely improve.
- **Endurance** (`developSeason.basketball.ts:118-139`): a young random boost
  `uniform(0, 9)`, flat in the prime, declining after 30.
- **Strength** (`developSeason.basketball.ts:70-73`): no age modifier and
  unbounded limits — it just follows the base change and noise.
- **Ball skills** (`drb, pss, reb`): use the shooting age curve but tight limits
  `[-2, 5]`.

### 3. Combining it all per rating

For each rating (`developSeason.basketball.ts:228-240`):

```ts
ratings[key] = limitRating(
  ratings[key] +
    bound(
      (baseChange + ageModifier) * uniform(0.4, 1.4),   // direction + extra randomness
      changeLimits[0],
      changeLimits[1],
    )
);
```

So the per-rating delta = `(baseChange + per-rating ageModifier)` multiplied by a
random `0.4–1.4` factor, clamped to the rating's limits, then added and floored
to [0, 100].

### Height growth

Players ≤21 have a tiny chance to grow taller each season (≈1% +1", plus a rarer
extra inch), `developSeason.basketball.ts:213-224`.

## Real-player determinism (historical leagues)

For leagues using real players, `developSeason.ts:30-72` can blend the simulated
ratings toward the player's *actual* historical ratings at that age. The blend
weight is `realPlayerDeterminism²` (a game attribute): at 1.0 the player follows
his real career exactly; at 0 he develops randomly like a fictional player. This
runs *after* the random `developSeason.<sport>` step.

## Other sports

The structure is identical (a `develop` driver + per-sport `developSeason`), but
the per-rating curves differ: see `developSeason.football.ts`,
`developSeason.baseball.ts`, and `developSeason.hockey.ts`. Football also updates
weight and computes ratings per position.

## How development feeds `value`

After ratings change, `develop` calls `updateValues` (via the player module),
which uses `value.ts` to recompute the player's internal worth. Key points
(`value.ts:28-163`):

- For basketball, recent in-game performance (PER over the last ~2 seasons,
  weighted by minutes) is blended with `ovr` to get a "current" ability number —
  so production, not just ratings, affects perceived value.
- Current ability and `pot` are blended by age via
  `valueCombineOvrPot.ts`: at age ≤19 it's 70% potential / 30% current; this
  flips steadily until, past ~28, value is almost entirely current ability with a
  small decline multiplier.
- This `value` is what the AI uses for trades, free-agent signings, draft picks,
  and roster cuts — so a young player who develops well becomes more valuable both
  through rising `ovr` *and* through still-high `pot`.

## Summary

```
each preseason, per player:
  addRatingsRow()                       # carry ratings forward + new fuzz
  develop(p, 1, coachingLevel):
    developSeason(ratings, age, coaching):
      baseChange   = ageTable(age) + noise(age)        # young ↑, old ↓
      baseChange  *= 1 ± coachingEffect
      for each rating:
        delta = (baseChange + ratingAgeModifier(age)) * uniform(0.4,1.4)
        rating += clamp(delta, ratingChangeLimits)
    [optional] blend toward real historical ratings
    recompute ovr, pot, position, skills, weight
  updateValues()                        # recompute internal value (ovr+pot+stats)
```

Net effect: ratings rise quickly and unpredictably through the early 20s, peak
around 26–27, and decline (athleticism first, shooting/IQ last) into the 30s,
with coaching and randomness shaping each individual career.
