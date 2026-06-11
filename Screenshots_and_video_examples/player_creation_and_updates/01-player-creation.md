# How Players Are Created

This document traces how a brand-new player is generated, from an empty object
to a fully-rated prospect. Basketball is used as the worked example; per-sport
files are noted where relevant.

## Entry point: `player.generate`

`src/worker/core/player/generate.ts` builds the player object shell and is the
single entry point for new players. It is called when:

- Generating a **draft class** (`draft/genPlayersWithoutSaving.ts`).
- Creating a **random free agent** (`genRandomFreeAgent.ts`).
- Filling out a **new league** with random players.

`generate(tid, age, draftYear, newLeague, scoutingLevel, {bio...})` does the
following (`generate.ts:13-111`):

1. Calls `genRatings(...)` to produce the body height + the full ratings object
   (this is where almost all the interesting logic lives — see below).
2. Derives a **weight** from height + strength (`genWeight.ts`).
3. Computes the **displayed height** in inches, applying the league's
   `heightFactor` game attribute and a 0.92 multiplier for female leagues
   (`generate.ts:47-52`).
4. Assembles the player record: a minimum contract, birth year
   (`g.get("season") - age`), an empty `draft` block (ovr/pot filled in later),
   a generated face (`util/face.ts`), random **mood traits**
   (`genMoodTraits.ts`), and empty stats/awards/salary history.
5. Sets `value`, `valueNoPot`, etc. to 0 — these are filled in later by
   `updateValues` (which `develop` calls automatically).

Crucially, **`generate` does not set `ovr` or `pot`.** Those are 0 until
`player.develop(p, 0)` is run on the player (see the development doc). The two
steps are almost always paired: generate the shell, then develop it.

## Generating ratings: `genRatings.ts`

`genRatings.ts:11-157` is the shared wrapper. It:

1. Dispatches to the per-sport generator
   (`genRatings.basketball.ts` / `.football.ts` / `.baseball.ts` / `.hockey.ts`)
   via `bySport()`.
2. Applies a **draft-age adjustment** (see below).
3. Applies an extra **fuzz multiplier** for prospects who are further in the
   future (the further out a draft class, the less you can trust their ratings).
4. Computes the player's position with `pos(ratings)` (`pos.basketball.ts`).

### The per-sport generator (basketball example)

`genRatings.basketball.ts:51-172` is where a player's raw body and talent are
rolled. The steps:

**1. Height.** A height in inches is drawn from a custom empirical distribution
built from real NBA height frequencies, `heightDist()` in
`src/common/random.ts:195+`. It is offset by a random fraction of an inch, then
a wingspan adjustment of ±1 inch is added. `heightToRating()`
(`heightToRating.ts`) linearly maps inches to the 0–100 `hgt` rating; for
basketball, 5'6" → 0 and 7'9" → 100.

**2. Player "type".** Based on the height rating, the player is randomly
classified as a **point**, **wing**, or **big**
(`genRatings.basketball.ts:66-94`). Tall players are almost always bigs; short
players are almost always points; mid-height players are usually wings. This
type drives which ratings get boosted.

**3. Base raw ratings.** Every rookie starts from the same low baseline
(`genRatings.basketball.ts:97-112`) — e.g. `endu: 17`, `oiq: 22`, `tp: 32`.
Comment in code: *"Tall players are less talented, and all tend towards dumb and
can't shoot because they are rookies."* These low starting points are what later
development raises.

**4. Correlation factors.** Four Gaussian multipliers are rolled
(`genRatings.basketball.ts:116-119`), each bounded to [0.2, 1.2]:

- `factorAthleticism` — applied to `stre, spd, jmp, endu, dnk`
- `factorShooting` — applied to `ft, fg, tp`
- `factorSkill` — applied to `oiq, diq, drb, pss, reb`
- `factorIns` — applied to inside scoring (`ins`) and anything else

These are independent so that, for example, a player can be an elite athlete but
a poor shooter. They create realistic correlation *within* a skill group while
keeping groups independent.

**5. Final per-rating roll.** For each rating
(`genRatings.basketball.ts:121-142`):

```
rating = limitRating( correlationFactor * typeFactor * realGauss(baseValue, 3) )
```

- `typeFactor` comes from `typeFactors[type]` (`genRatings.basketball.ts:11-45`),
  e.g. a `point` gets `spd × 1.65`, a `big` gets `ins × 1.6` but `tp × 0.8`.
- `realGauss(base, 3)` adds normal noise around the baseline.
- `limitRating()` floors the result and clamps it to [0, 100]
  (`limitRating.ts`).

The output is a full ratings object plus `fuzz` (see below), `ovr: 0`, `pot: 0`,
`season`, and an empty `skills` array (`genRatings.basketball.ts:144-166`).

### Draft-age adjustment

Real leagues let users change the draft age. `genRatings.ts:30-129` simulates
the "extra years of development" an older prospect would have had, without
actually running the (slow) develop loop and without making old players terrible.

- It compares the configured draft age to the sport's default
  (basketball default = 19, `genRatings.ts:23-28`), capped at age 30.
- It computes `scale = round(3 * sign(ageDiff) * |ageDiff|^exponent)` (exponent
  0.8 for basketball, `genRatings.ts:34-43`).
- Most ratings get `+scale`; a set of "slow-developing" ratings
  (`spd, jmp, drb, pss, reb` for basketball) get only `+scale/2`
  (`genRatings.ts:114-128`).

So a league set to draft 22-year-olds produces stronger, more polished
prospects than one drafting teenagers.

## Fuzz: scouting uncertainty

Fuzz is the noise added to a player's *displayed* ratings so the user cannot see
true values exactly. It is generated in `genFuzz.ts`:

- The amount depends on the team's **scouting level** (a facilities budget).
  Better scouting → smaller fuzz. `scoutingEffectCutoff` (clamp, 1–8) and
  `scoutingEffectStddev` (1–3) come from `src/common/budgetLevels.ts:70-81`.
- Fuzz is a Gaussian draw clamped to ±cutoff.

`genRatings.ts:131-146` then **multiplies fuzz** for prospects who are further in
the future: ×√2 one year out, ×2 two-or-more years out (offsets shift if the
phase is past re-signing). This is why distant draft classes have very uncertain
ratings.

When a player's ratings are shown or used for ordering,
`fuzzRating(rating, fuzz)` (`fuzzRating.ts`) applies the noise — but fuzz is
forced to 0 in God Mode and in multi-team mode.

## Special / scrub adjustments in draft classes

`draft/genPlayersWithoutSaving.ts` post-processes a freshly generated class:

- **Special players** (`bonus.ts`): on average one prospect per class gets a
  random `+0..10` boost to *every* rating, creating occasional superstars
  (`genPlayersWithoutSaving.ts:171-185`).
- **Scrubs**: if the user increased the number of draft rounds, the excess
  players are nerfed with a negative `bonus()` so the extra picks are not as
  good as a normal class (`genPlayersWithoutSaving.ts:187-224`).
- Players who declare at 18 or younger have their college blanked
  (`genPlayersWithoutSaving.ts:226-232`).
- Prospects are sorted into draft years by `pot` plus a large random fudge
  factor, so the most promising tend to declare first
  (`genPlayersWithoutSaving.ts:143-169`).

## Summary flow

```
generate(tid, age, draftYear, ...)
  └─ genRatings(season, scoutingLevel)
        ├─ genRatings.<sport>()   → height, raw ratings, fuzz
        ├─ draft-age adjustment   → +scale to ratings
        ├─ fuzz × distance factor
        └─ pos(ratings)           → position
  └─ genWeight, face, mood traits, contract, bio
        → player object with ovr=0, pot=0

then:  player.develop(p, 0)   → fills in ovr, pot, skills, value
```

See **[02-potential-rating.md](./02-potential-rating.md)** for how `pot` is
computed, and **[03-player-development.md](./03-player-development.md)** for the
`develop` step.
