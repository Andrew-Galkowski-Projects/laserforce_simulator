# ZenGM Finance System

These documents explain how team finances work in the ZenGM engine (Basketball
GM, Football GM, ZenGM Baseball, ZenGM Hockey): where a team's money comes from,
where it goes, how the four spending budgets translate into on-court advantages,
and how the salary cap, luxury tax, owner mood, and hype tie it all together.

1. **[Budget levels](./01-budget-levels.md)** — the unified 1–100 "level"
   abstraction behind scouting / coaching / health / facilities spending, how a
   level becomes a dollar amount and a gameplay effect, and ticket pricing.
2. **[Revenue and expenses](./02-revenue-and-expenses.md)** — the per-game
   engine that earns and spends money: attendance, ticket / TV / sponsor / merch
   revenue, the spending expenses, cash, profit, and hype.
3. **[Salary cap and taxes](./03-salary-cap-and-taxes.md)** — payroll, the three
   salary-cap modes, the luxury tax, the minimum-payroll penalty, the
   end-of-season assessment, and the finance-related league settings.
4. **[Owner mood and the season lifecycle](./04-owner-mood-and-lifecycle.md)** —
   how money feeds owner mood, inflation adjustment when the cap changes, the
   preseason AI budget reset, and the user-facing Team Finances page.

## Orientation

Almost all finance logic lives in the **worker** process. The pieces are spread
across a few locations rather than a single module:

| Location | Responsibility |
| --- | --- |
| `src/common/budgetLevels.ts` | The level↔amount↔effect math, shared by worker and UI. |
| `src/worker/core/finances/` | Payroll-time assessment: luxury tax, min payroll, the 3-year level average, default budget levels. |
| `src/worker/core/game/writeTeamStats.ts` | The per-game revenue/expense engine — runs once per simulated game. |
| `src/worker/core/game/attendance.ts` | Attendance and auto ticket pricing. |
| `src/worker/core/season/updateOwnerMood.ts` | Owner mood (money / wins / playoffs) at season end. |
| `src/worker/core/team/getPayroll.ts`, `getPayrolls.ts` | Current payroll from active + released contracts. |
| `src/worker/views/teamFinances.ts`, `leagueFinances.ts` | Data for the finance UI pages. |
| `src/ui/views/TeamFinances/` | The user-facing budget form. |

The engine is **multi-sport**. Rather than the `bySport()` dispatch-to-a-file
pattern used elsewhere, the finance code mostly uses inline `bySport({...})`
*coefficient tables* — the same formula with sport-specific constants (e.g.
football has ~28× the attendance and a different revenue model). **These
documents use basketball as the primary worked example**, and call out the
per-sport constants where they matter.

## Key concepts

| Term | Meaning |
| --- | --- |
| **Budget level** | An integer 1–100 the user/AI sets for each of the four spending categories. `34` (`DEFAULT_LEVEL`) is neutral (zero effect). |
| **`budget`** | A team's current settings: `{ ticketPrice, scouting, coaching, health, facilities }`. `ticketPrice` is dollars; the rest are levels. Stored on the `Team` object. |
| **`expenseLevels`** | Per-`teamSeason` running sum of the budget levels actually in effect each game, used to compute a true season-average level. |
| **`getLevelLastThree`** | The *effective* level used for gameplay effects: a games-played-weighted average of the level over the last three seasons. |
| **`levelToAmount`** | Converts a level into the dollars/season the team pays, scaled by the salary cap. |
| **`levelToEffect`** | Converts a level into a normalized −1.1…+1.1 effect that each gameplay effect function then rescales. |
| **Payroll** | Sum of all contract amounts (including released players still owed money). Compared against the cap and tax thresholds. |
| **`cash`** | A team's bank balance (thousands of dollars), carried across seasons. Adjusted by every game and by season-end taxes. |
| **`profit`** | A derived (not stored) per-season figure: total revenues − total expenses. |
| **`hype`** | 0–1 fan excitement, driven by winning relative to expectations; the dominant input to attendance. |
| **Owner mood** | Three 0–1 scores (money, wins, playoffs) for the user's team that determine whether you get fired. |

## How money flows (the 30-second version)

1. The user (or AI) sets four **budget levels** plus a **ticket price** on the
   Team Finances page.
2. Each simulated regular-season game, `writeTeamStats` credits the home team
   ticket/TV/sponsor/merch **revenue** (driven by attendance, which is driven by
   hype, population, ticket price, and facilities) and debits **expenses** (a
   per-game slice of payroll plus the four budgets). The net hits `cash`.
3. At season end, `assessPayrollMinLuxury` charges the **luxury tax** to high
   payrolls and a **minimum-payroll penalty** to low ones, redistributing half
   the tax to teams under the cap.
4. `updateOwnerMood` rolls season **profit** (vs. an expected profit) into the
   owner's **money** mood. Sustained losses — of money *or* games — get you
   fired.
