# Salary cap and taxes

This document covers payroll, the three salary-cap modes, the luxury tax, the
minimum-payroll penalty, the end-of-season assessment that charges them, and the
finance-related league settings. The assessment code lives in
`src/worker/core/finances/`.

## Payroll

`src/worker/core/team/getPayroll.ts` returns a team's total payroll in thousands
of dollars. Two important properties:

- It sums contracts of **active players** *and* **released players still owed
  money** (`releasedPlayers`). Cutting a player does not erase their cap hit.
- With an optional `season` argument it counts only contracts that extend through
  that season (`contract.exp >= season`); otherwise it counts everything
  currently on the books. It can also take a pre-fetched `ContractInfo[]`
  instead of a tid.

`getPayrolls.ts` runs it for every non-disabled team, returning
`{ [tid]: payroll }`.

> Payroll is the cap/tax input. The *contract* system (free agency, negotiation,
> contract length/amount limits) is upstream of finance and is not covered here;
> this document is about what happens to a team **given** its payroll.

## Salary-cap modes (`salaryCapType`)

A single setting, `g.get("salaryCapType")`, switches between three regimes:

| Mode | Meaning | Luxury tax? |
| --- | --- | --- |
| `"soft"` | NBA-style: you can exceed the cap via exceptions, but pay luxury tax above the luxury line. (Default.) | Yes |
| `"hard"` | You may never exceed the cap. | **No** — `getLuxuryTaxAmount` returns 0 under a hard cap (you can't get high enough to owe it). |
| `"none"` | No cap at all. The luxury line still functions as a tax threshold. | Yes |

This setting also changes which threshold is used as the luxury-tax
*redistribution* cutoff (see below).

## The thresholds (league settings)

These are game attributes, defaulted in `src/common/defaultGameAttributes.ts`
(values shown are the basketball defaults; presets like the hard-cap or
no-cap leagues override them):

| Attribute | Default | Role |
| --- | --- | --- |
| `salaryCap` | `150000` | The cap. Also the universal scaling baseline for revenue & `levelToAmount`. |
| `minPayroll` | `95000` | Below this, you pay the minimum-payroll penalty. |
| `luxuryPayroll` | `168000` | Above this, you pay luxury tax. |
| `luxuryTax` | `1.5` | Tax rate: $1.50 owed per $1 over the luxury line. |
| `minContract` / `maxContract` | `1200` / `50000` | Per-player contract bounds (contract system). |
| `minContractLength` / `maxContractLength` | `1` / `5` | Contract length bounds. |
| `budget` | `true` | Master switch — when off, budgets have no gameplay effect and `getLevelLastThree` returns `DEFAULT_LEVEL`. |

## The two penalties

Both are pure functions of payroll (in `src/worker/core/finances/`):

### Luxury tax (`getLuxuryTaxAmount`)

```ts
if (payroll > luxuryPayroll && salaryCapType !== "hard")
    return luxuryTax * (payroll - luxuryPayroll)
return 0
```

A flat marginal rate on the amount over the luxury line. Disabled under a hard
cap.

### Minimum-payroll penalty (`getMinPayrollAmount`)

```ts
if (payroll < minPayroll) return minPayroll - payroll
return 0
```

If you spend too little on players, you're charged the shortfall — you pay the
money either way, so there's no benefit to tanking payroll below the floor.

## The end-of-season assessment (`assessPayrollMinLuxury`)

Called once after the regular season. For every team's current-season
`teamSeason` row it:

1. **Records final payroll**: `teamSeason.payrollEndOfSeason = payroll`.
2. **Charges the min-payroll penalty** if applicable:
   `expenses.minTax`, subtracts from `cash`, and logs a `minPayroll` event
   (notification only for the user's team).
3. **Charges luxury tax** if applicable: `expenses.luxuryTax`, subtracts from
   `cash`, logs a `luxuryTax` event, and adds it to a league-wide `collectedTax`
   pool.
4. **Redistributes half the tax pool**. Teams whose payroll is at or below the
   **payroll cutoff** split `collectedTax * 0.5` evenly:
   - cutoff = `salaryCap` normally, but `luxuryPayroll` when
     `salaryCapType === "none"` (no cap means the cap value is meaningless as a
     cutoff).
   - Each qualifying team gets `revenues.luxuryTaxShare = distribute`, added to
     `cash`, and a `luxuryTaxDist` event. Teams above the cutoff get
     `luxuryTaxShare = 0`.
5. Persists every modified `teamSeason`.

The other half of the collected tax simply leaves the economy (the league keeps
it) — this is a money sink that mildly penalizes high-spending leagues overall.

`luxuryTax` and `minTax` are the two expense lines added *only* here (they're not
touched per-game in `writeTeamStats`), and `luxuryTaxShare` is the one revenue
line added only here. All three feed the `profit` / `revenue` rollups in
`teamsPlus.ts`.

## Where the user sees this

`src/worker/views/teamFinances.ts` computes the live `luxuryTaxAmount` and
`minPayrollAmount` from current payroll for display, plus the full contract table
and per-season revenue/expense history (`formatRevenueExpenses`).
`src/worker/views/leagueFinances.ts` shows the league-wide table of
`revenue / profit / cash / payrollOrSalaryPaid` per team.
