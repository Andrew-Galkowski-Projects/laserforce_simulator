# Budget levels

A team spends money in four "facility" categories — **scouting**, **coaching**,
**health**, and **facilities** — plus it sets a **ticket price**. The four
categories share a single abstraction: a **level** from 1 to 100. This document
explains that abstraction (all of it lives in `src/common/budgetLevels.ts` so
the worker and the UI agree), how a level becomes both a dollar cost and a
gameplay effect, and the separate ticket-price mechanism.

> **History note.** Levels are relatively new. Old league files stored raw
> dollar *amounts* per category; on load they are converted with `amountToLevel`
> (see `createStream.ts`). The level system was introduced so the *effect* of
> spending stays constant even when the salary cap (and therefore the dollar
> amounts) changes.

## The three conversions

`budgetLevels.ts` exposes three families of functions. Think of a level as
sitting in the middle, with money on one side and gameplay on the other.

```
       levelToAmount(level, cap)            levelToEffect(level)
$ cost  <───────────────────────  LEVEL 1..100  ───────────────────────>  normalized effect
       amountToLevel(amount, cap)                                          (rescaled per category)
```

Three constants anchor the scale:

| Constant | Value | Meaning |
| --- | --- | --- |
| `MAX_LEVEL` | `100` | Top of the range. |
| `DEFAULT_LEVEL` | `34` | The neutral level — **effect is exactly 0 here**, not at 50. |
| `BUDGET_LEVEL_SCALE` | `1.1` | The asymptote magnitude for `levelToEffect`; >1 because the `tanh` upper branch never reaches 1 and no team realistically sits at level 1. |

### Level → dollars (`levelToAmount`)

```ts
levelToAmount(level, salaryCap) =
  round( (salaryCap/90000)*1345
       + (900*(salaryCap/90000)*(round(level)-1)) / (2*DEFAULT_LEVEL - 1) ) * 10
```

Everything is scaled by `salaryCap/90000` (90000 being the frozen basketball
baseline cap) so a given level costs proportionally the same regardless of the
league's cap. The denominator uses `2*DEFAULT_LEVEL - 1` rather than `MAX_LEVEL`
deliberately: it keeps `DEFAULT_LEVEL` in the middle of the dollar range and
leaves headroom at the top. `amountToLevel` is the algebraic inverse, used only
for upgrading old league files, and is `bound` to `[1, MAX_LEVEL]`.

### Level → effect (`levelToEffect`)

```ts
x = (3*(round(level)-1)) / (MAX_LEVEL-1) - 1     // maps 1..100 → -1..+2
levelToEffect = x < 0 ? SCALE * x                // linear below DEFAULT_LEVEL
                      : SCALE * tanh(x)           // saturating above
```

At `DEFAULT_LEVEL` (34), `x = 0` and the effect is 0. Below default the penalty
grows linearly; above default the benefit saturates via `tanh` (diminishing
returns on lavish spending). The output range is roughly `[-1.1, +1.1]`.

Each gameplay system then rescales that normalized effect into its own units.
**All of these scaling functions live in `budgetLevels.ts`** so the UI can show
the player the exact effect their slider will have:

| Function | Output range | Used by |
| --- | --- | --- |
| `facilitiesEffectMood(level)` | `−2.2 … +2.2` (`2 × effect`) | `player/moodComponents.ts` — happier players on better facilities |
| `facilitiesEffectAttendance(level)` | `−0.0375 … +0.0375` | `game/attendance.ts` — better facilities draw more fans |
| `healthEffect(level)` | `+0.13 … −0.13` (`−0.12 × effect`; note the sign flip) | `player/injury.ts` — better health shortens injuries |
| `coachingEffect(level)` | `−0.10 … +0.10` (`0.09 × effect`) | `player/develop.ts` — better coaching speeds development |
| `scoutingEffectCutoff(level)` | `1 … 8` integer | `player/genFuzz.ts` — better scouting reduces ratings uncertainty |
| `scoutingEffectStddev(level)` | `1 … 3` | `player/genFuzz.ts` — same |

So the four budgets buy four distinct edges: scouting buys *information*
(less rating fuzz), coaching buys *development*, health buys *availability*
(fewer/shorter injuries), and facilities buy *mood + attendance* (player
happiness and gate revenue).

## The effective level: `getLevelLastThree`

Spending does **not** take effect instantly. The level used for every gameplay
effect above is the **games-played-weighted average over the last three
seasons**, computed by `src/worker/core/finances/getLevelLastThree.ts`. This is
why dumping money into facilities for one season barely moves the needle — and
why neglected facilities keep hurting you for years.

How it works (only when the `budget` setting is on; otherwise it returns
`DEFAULT_LEVEL`):

- Each game, `writeTeamStats` adds the current `t.budget[key]` into
  `teamSeason.expenseLevels[key]`. So `expenseLevels` is a *sum of levels over
  games played*, not an average.
- `getLevelLastThree` pulls up to the last three `teamSeason` rows, sums their
  `expenseLevels[key]`, divides by total games played → a true per-game average.
- **Imputation** fills gaps so new or partial seasons don't skew the result:
  - A season with games played but zero `expenseLevels` (e.g. an imported
    real-players history row) is imputed at `t.initialBudget[key]`.
  - A missing season, or a not-yet-started season in the current year, is
    imputed at `t.initialBudget[key]` for a full `numGames`.
  - A genuinely empty future row (0 GP, 0 expense, before the season ran) is
    ignored.

`initialBudget` is set once at team creation and serves as the stand-in "what
this team has historically spent" value for these imputations.

## Default budget level for AI / new teams (`defaultBudgetLevel`)

`src/worker/core/finances/defaultBudgetLevel.ts` picks a level for a team from
its **population rank** (`popRank`): big-market teams spend more. It scales
`popRank` into the `±BUDGET_LEVEL_SCALE` effect space, adds Gaussian noise
(`gauss(0.95, 0.2)` — mean slightly under 1 so it doesn't constantly hit the
bounds), clamps to `±0.98·SCALE`, and runs it back through `effectToLevel`
(the inverse of `levelToEffect`, also defined in this file). This is called when
generating new teams and — importantly — every preseason for AI teams (see
[doc 04](./04-owner-mood-and-lifecycle.md)).

## Ticket price (the fifth budget item)

`budget.ticketPrice` is different from the four levels: it's a raw dollar amount,
and it directly drives attendance (and therefore gate revenue) via an inverse-
square relationship — see [doc 02](./02-revenue-and-expenses.md). Two modes:

- **Auto ticket price** (`t.autoTicketPrice`, default true, and always true for
  AI teams): the engine solves for the price that exactly fills the stadium at
  expected attendance, via `getActualAttendanceInverted` /
  `getAutoTicketPrice` in `attendance.ts`. `resetTicketPrice.ts` and the
  preseason both reapply this.
- **Manual**: the user sets a fixed dollar price on the Team Finances page; only
  inflation adjustment (when the cap changes) touches it thereafter.

In the playoffs the price is automatically marked up by
`getAdjustedTicketPrice` (`src/common/getAdjustedTicketPrice.ts`):
`√PLAYOFF_ATTENDANCE_FACTOR × price`, where `PLAYOFF_ATTENDANCE_FACTOR = 1.5`,
matching the playoff attendance bump so the stadium doesn't overflow.
