# Owner mood and the season lifecycle

The first three documents covered *where money comes from and goes*. This one
covers the consequences — how finances feed the owner's mood (and your job
security) — plus the seasonal lifecycle events that reset or adjust budgets, and
the user-facing Team Finances page.

## Owner mood

`src/worker/core/season/updateOwnerMood.ts` runs after the playoffs end, **only
for the user's team** (it bails out under auto-play, spectator mode, or multi-
team mode, staying in sync with `genMessage.ts`). The owner judges you on three
axes, each accumulating toward 1.0:

```ts
deltas.wins     = winsFactor * 0.25*(won - numGames/2) / (numGames/2)
deltas.playoffs = depends on playoffRoundsWon vs numPlayoffRounds
deltas.money    = budget ? (profit - expectedProfit) / (100*salaryCapFactor) : 0
```

### The money axis

This is the finance hook. `profit` is the season's derived profit (see
[doc 02](./02-revenue-and-expenses.md)), compared against:

```ts
salaryCapFactor = salaryCap / <frozen baseline>     // bball 90000, etc.
expectedProfit  = 15 * salaryCapFactor
```

So owners expect roughly **$15M profit/season** (cap-scaled). Beat it and the
money mood rises; miss it (or lose money) and it falls. When the `budget`
setting is off, the money axis is forced to 0 — you're judged on wins alone.

### Wins and playoffs axes

- **Wins**: a `.500` record is neutral; the `winsFactor` (`baseball 2.2`, else
  `1`) compensates for baseball's higher variance so a 95-win baseball season is
  appropriately impressive.
- **Playoffs**: `−0.2` for missing entirely, a fraction scaling with rounds won,
  `+0.2` for winning the championship.

### Accumulation and firing

- Each axis is **capped at the top** (you can't bank money mood above 1) but
  **not at the bottom** — "you can't win the game by doing only one thing, but
  you can lose it by neglecting one thing." Neglecting finances *or* wins *or*
  playoffs can sink you even if the other two are maxed.
- Mood only updates once `season >= gracePeriodEnd` and not in God Mode.
- The accumulated `teamSeason.ownerMood` carries across seasons via
  `genSeasonRow` and is what the firing check reads. See
  `docs/firing_rules.md` for how the three scores combine into a firing
  decision.

## Seasonal lifecycle: who resets what

Budgets and ticket prices aren't static — several events touch them:

### Per game (`writeTeamStats`)
Accumulates revenue/expense/cash and, for AI teams (and user teams not opted
out), recomputes the auto ticket price from current hype/pop/stadium each home
game. See [doc 02](./02-revenue-and-expenses.md).

### Each preseason (`newPhasePreseason.ts`)
For every **non-user** team (and *all* teams under auto-play/spectator):

- `resetTicketPrice` → auto price from popRank.
- Each of the four budgets is re-rolled with **50% probability** from
  `defaultBudgetLevel(popRank)` — so AI spending drifts over time toward what
  their market can support, rather than staying fixed.
- `adjustForInflation`, `autoTicketPrice`, `keepRosterSorted` are forced on and
  `playThroughInjuries` reset to default.

User teams keep their settings untouched.

### When the salary cap changes (`setGameAttributes.ts`)
Because nearly every dollar figure is scaled by `salaryCap`, changing the cap
mid-league would distort budgets. So when `salaryCap` updates:

- **User teams** with `adjustForInflation` on have their ticket price rescaled
  by the ratio of `defaultTicketPrice` at the new vs. old cap (or re-auto-priced
  if on auto). Levels need no adjustment — `levelToAmount` already scales by the
  cap.
- **AI teams** simply get `resetTicketPrice` at the new cap.

### League load / upgrade (`createStream.ts`)
Old league files that stored raw dollar *amounts* per budget category are
converted to levels with `amountToLevel(amount, cap)`. `initialBudget` is
captured here too, for `getLevelLastThree` imputation.

## The Team Finances page

- **Worker view** `src/worker/views/teamFinances.ts` assembles: current payroll
  with live luxury-tax / min-payroll amounts, the multi-year contract table
  (with per-player cap %), per-season revenue/expense history (for the bar
  graphs), the three-year effective levels (`expenseLevelsLastThree` via
  `getLevelLastThree`), the auto ticket price, and other teams' ticket prices
  for comparison.
- **UI** `src/ui/views/TeamFinances/index.tsx` renders the editable budget form.
  For each of the four categories it shows the live gameplay effect (calling the
  same `coachingEffect` / `healthEffect` / `scoutingEffect*` /
  `facilitiesEffect*` functions from `budgetLevels.ts` the engine uses, so the
  preview is exact), plus the ticket-price field, an `autoTicketPrice` toggle,
  and an `adjustForInflation` toggle. Levels are `bound` to `[1, MAX_LEVEL]`.
- **Saving** calls the `updateBudget` worker API
  (`src/worker/api/index.ts`), which writes `t.budget`, `t.adjustForInflation`,
  and `t.autoTicketPrice`, re-deriving the ticket price if auto pricing was just
  switched on. It guards against `NaN` inputs and triggers a `teamFinances`
  realtime update. `getProjectedAttendance` (same file) powers the UI's
  "what attendance would this ticket price get me" preview by running the
  forward attendance formula with `randomize: false`.

## Putting it together

```
 user/AI sets budget levels + ticket price  ──┐
                                              │ levelToAmount / auto-price
 each game: writeTeamStats                    ▼
   revenue (attendance ← hype, pop, price, facilities)
   − expenses (payroll/N + 4 budgets/N)
   → cash,  and accumulates expenseLevels ───┐
                                              │ getLevelLastThree (3-yr avg)
 budgets buy gameplay edges ◄─────────────────┘
   scouting→fuzz, coaching→develop, health→injury, facilities→mood+attendance
                                              │
 season end:                                  ▼
   assessPayrollMinLuxury  (luxury tax / min-payroll penalty / redistribution)
   updateOwnerMood         (profit vs expectedProfit → money mood → firing)
                                              │
 preseason / cap change: reset AI budgets, re-price, inflation-adjust
```

The whole system is a feedback loop: winning builds hype, hype fills the arena,
gate money funds bigger budgets, budgets buy better players and development,
which produces more winning — while the salary cap, luxury tax, and owner's
profit expectations apply the brakes.
