# Revenue and expenses

This document covers the per-game money engine: how a team earns gate, TV,
sponsor, and merchandise revenue; how attendance and hype are computed; what it
pays out; and how those roll up into `cash` and `profit`. The core file is
`src/worker/core/game/writeTeamStats.ts`, which runs **once per simulated game**
for both teams, with attendance helpers in
`src/worker/core/game/attendance.ts`.

> **Units.** Everything in the finance code is in **thousands of dollars**
> internally. The UI divides by 1000 again to show millions. Watch for the `/
> 1000` conversions when reading the code.

## When does money move?

`writeTeamStats` is called after every game. It distinguishes:

- **All-Star games** (`team[0].id === -1`): no finances at all; returns the
  default stadium capacity and exits.
- **Regular-season games**: full revenue *and* expenses.
- **Playoff games**: attendance and ticket/TV/sponsor/merch revenue still
  accrue (with the playoff markup), but **the four budget expenses and payroll
  are not charged again** — players are paid over the regular season, so playoff
  games are pure upside. (`if (g.get("phase") !== PHASE.PLAYOFFS)` guards the
  expense block.)

## Attendance — the heart of revenue

Most revenue scales with attendance, so attendance is computed first, for the
home team only.

### Base attendance (`getBaseAttendance`)

```ts
baseAttendance = 10000 + (0.1 + 0.9*hype²) * pop * 1_000_000 * 0.01
```

- **`hype`** (0–1) dominates: it enters squared, so the difference between a
  bored fanbase and a hyped one is large.
- **`pop`** is the market population (millions).
- Sport multipliers follow: hockey `×1.05`, **football `×28`** (football plays
  far fewer games, so each draws a full stadium-sized crowd).
- Playoffs multiply by `PLAYOFF_ATTENDANCE_FACTOR = 1.5`.

### Actual attendance (`getActualAttendance`)

Base attendance is then adjusted by ticket price and facilities and capped at
the stadium:

```ts
relativeTicketPrice = adjustedTicketPrice * salaryCapFactor()
attendance  = baseAttendance * SPORT_FACTOR
attendance  = gauss(attendance, 1000)                       // per-game noise
attendance *= TICKET_PRICE_FACTOR / relativeTicketPrice²    // inverse-square in price
attendance *= facilitiesFactor(tid)                         // 1.0375 + facilitiesEffectAttendance(level)
attendance  = bound(attendance, 0, stadiumCapacity)
```

Key points:

- **Inverse-square price sensitivity.** Doubling the ticket price roughly
  quarters the (price-driven part of) attendance. `TICKET_PRICE_FACTOR = 45*50`.
- **`salaryCapFactor()`** normalizes the price against a 90000 baseline cap
  (`(90000/cap)^0.75` when the cap is small, else linear) so that high/low-cap
  leagues — including historical 1965-style leagues — aren't distorted.
- **`SPORT_FACTOR`** is a post-hoc per-sport scalar (`basketball 0.75`,
  `hockey 0.35`, `baseball 0.1`, `football 0.0575`) added when auto ticket prices
  were introduced, to keep overall finances roughly unchanged.
- **`facilitiesFactor`** uses `getLevelLastThree("facilities")` →
  `facilitiesEffectAttendance` (see [doc 01](./01-budget-levels.md)): nicer
  arenas pull in `±3.75%`.

### Auto ticket price (inverse)

When a team uses auto ticket pricing, the engine runs the attendance formula
*backwards* (`getActualAttendanceInverted`) to find the price that would exactly
fill the stadium at expected (non-playoff) base attendance.
`getAutoTicketPrice` / `getAutoTicketPriceByTid` wrap this and are used by the
preseason reset, the finance UI projection, and `writeTeamStats` itself for AI
teams (and user teams that haven't opted out).

## Revenue streams

With base attendance known, `writeTeamStats` computes (regular season only) up to
five revenue lines. The exact coefficients differ by sport; the structure for
**basketball / baseball / hockey** is:

| Stream | Formula (basketball-style) | Cap |
| --- | --- | --- |
| **Merchandise** | `salaryCapFactor2 * 4.5 * baseAttendance / 1000` | `≤ salaryCapFactor * 250` |
| **Sponsorship** | `salaryCapFactor2 * 15 * baseAttendance / 1000` | `≤ salaryCapFactor * 600` |
| **National TV** | `salaryCapFactor2 * 375` (flat — shared equally, not attendance-based) | — |
| **Local TV** | `salaryCapFactor2 * 15 * baseAttendance / 1000` | `≤ salaryCapFactor * 1200` |
| **Ticket (gate)** | `adjustedTicketPrice * attendance / 1000` | stadium-capped via attendance |

**Football** uses an explicitly different model (documented inline with target
annual figures: ~$170M national TV, ~$50M local TV, ~$75M ticket, ~$25M each
sponsor/merch), with TV/sponsor/merch divided across the small number of games.

`salaryCapFactor` here is `salaryCap / <frozen baseline cap>` (baseball 175000,
basketball 90000, football 200000, hockey 80000). `salaryCapFactor2` is the same
except hockey uses a legacy `cap/90000`. The "frozen" baselines are hard-coded so
that changing the league's cap doesn't silently rescale every coefficient.

### Global multipliers applied to all revenue

Two factors then scale every revenue line (and only revenue):

1. **`fudgeFactor`** — difficulty handicap, **user teams only**:
   `1 - 0.2*difficulty` (so +5% on easy, −5% on hard, −20% on insane). AI teams
   get `1`.
2. **`seasonLengthFactor`** — normalizes for league length so total annual
   revenue is stable whether you play 82 games or 30. Regular season:
   `defaultNumGames / numGames`. Playoffs: a ratio of default-vs-current playoff
   series lengths (each series counted as `ceil(games*3/4)`, i.e. the expected
   number of games in a best-of-N).

## Expenses

Regular-season expenses are a per-game slice of the season's commitments:

```ts
salaryPaid     = payroll / numGames
scoutingPaid   = levelToAmount(budget.scouting,   cap) / numGames
coachingPaid   = levelToAmount(budget.coaching,   cap) / numGames
healthPaid     = levelToAmount(budget.health,     cap) / numGames
facilitiesPaid = levelToAmount(budget.facilities, cap) / numGames
```

`payroll` is the team's full contract obligation (see
[doc 03](./03-salary-cap-and-taxes.md)); the four budgets convert level→dollars
via `levelToAmount` ([doc 01](./01-budget-levels.md)). Note that **payroll
expense is independent of `fudgeFactor` and `seasonLengthFactor`** — only
revenue gets those.

## Settling up each game

```ts
revenue  = merch + sponsor + nationalTv + localTv + ticket
expenses = salary + scouting + coaching + health + facilities
teamSeason.cash += revenue - expenses
```

Then `writeTeamStats` records the breakdown for the season-to-date:

- Each revenue line accumulates into `teamSeason.revenues.{merch, sponsor,
  nationalTv, localTv, ticket}` (and later `luxuryTaxShare`, see doc 03).
- Each expense line accumulates into `teamSeason.expenses.{salary, scouting,
  coaching, health, facilities}` (and later `luxuryTax`, `minTax`).
- The four `teamSeason.expenseLevels` accumulate the *levels* in effect (for
  `getLevelLastThree`).
- Home team only: `teamSeason.att += attendance` and `teamSeason.gpHome += 1`.

### `cash` vs `profit`

- **`cash`** is the persistent bank balance on `teamSeason` (starts at `10000`
  for a fresh season via `genSeasonRow`, and is *carried over* from the prior
  season). It can grow without bound or, in principle, go negative.
- **`profit`** is **not stored**. It's derived on demand in
  `src/worker/db/getCopies/teamsPlus.ts` as `(totalRevenues − totalExpenses) /
  1000` (millions) when a view requests the `profit` seasonAttr. Likewise
  `revenue`, `cash`, `salaryPaid`, and `payroll` seasonAttrs are computed there.

## Hype

Hype isn't money, but it's the dominant driver of attendance and is updated
inside `writeTeamStats` (regular season, after 5+ games played):

```ts
hype += 0.01*(winp - 0.55) + 0.015*(winp - winpOld)
hype  = bound(hype, 0, 1)
```

- `winp` is this season's winning percentage; `winpOld` is the average of the
  prior 0–2 seasons (defaulting to 0.5 for new teams).
- The `0.55` anchor means you must win *more than half* your games just to hold
  hype steady — mediocrity slowly bleeds fans.
- The second term rewards *improvement* relative to recent history: an
  overachieving small-market team can build hype fast.

Hype is carried across seasons in `genSeasonRow` (new teams start at a random
value). It feeds straight back into `getBaseAttendance` next game, closing the
loop: winning → hype → attendance → ticket/TV/sponsor/merch revenue → cash.
