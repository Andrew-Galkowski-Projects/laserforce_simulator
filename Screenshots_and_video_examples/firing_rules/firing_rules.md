# GM Firing Rules

In ZenGM, you play as a team's **General Manager (GM)**. The team **owner**
evaluates your performance once per season and can fire you. This document
describes exactly when and how that happens, based on the game's source code.

## When the evaluation happens

Firing is decided during the **"before draft" phase**, right after the playoffs
and awards. The flow is:

1. `season.updateOwnerMood()` updates the owner's cumulative mood.
2. `genMessage(deltas, cappedDeltas)` generates the annual performancek
   evaluation message and decides whether you are fired.

Source: `src/worker/core/phase/newPhaseBeforeDraft.ts` (~line 500), which calls
`src/worker/core/season/updateOwnerMood.ts` and
`src/worker/core/phase/genMessage.ts`.

## When firing is *not* evaluated at all

In `updateOwnerMood` and `genMessage`, the whole owner-mood / firing system is
skipped entirely when any of these are true (the two files are kept in sync):

- **Auto play seasons** is active (`local.autoPlayUntil`).
- **Spectator mode** is on (`g.get("spectator")`).
- **Multi-team mode** — you control more than one team
  (`g.get("userTids").length > 1`).

## The owner mood model

Owner mood is tracked per season on `teamSeason.ownerMood` as three separate
running totals (see `src/common/types.ts` → `OwnerMood`):

- `wins` — regular-season performance
- `playoffs` — playoff performance
- `money` — finances / profit

Each season, `updateOwnerMood` computes **deltas** for these three factors and
adds them to the cumulative mood. The owner's overall feeling is the sum
`wins + playoffs + money`.

### How each season's deltas are computed

(`src/worker/core/season/updateOwnerMood.ts`)

- **Wins:** scaled around a .500 record. Beating half your games is positive,
  below is negative. Baseball uses a `winsFactor` of 2.2 (more random sport);
  the other sports use 1.
  - `wins = winsFactor * 0.25 * (won - numGames/2) / (numGames/2)`
- **Playoffs:**
  - Missed the playoffs (`playoffRoundsWon < 0`): `-0.2`
  - Made the playoffs but didn't win the title:
    `(0.16 / numPlayoffRounds) * playoffRoundsWon`
  - Won the championship: `+0.2`
- **Money:** based on profit vs. an expected profit (`15 * salaryCapFactor`).
  Only counts if the team budget is enabled (`g.get("budget")`); otherwise `0`.
  - `money = (profit - expectedProfit) / (100 * salaryCapFactor)`

### Capping

A `cappedDeltas` version is also computed. Each of `money`, `playoffs`, and
`wins` is capped so its cumulative total cannot exceed `1`. The design intent
(per code comments): **you can't win the game by maxing out a single factor,
but you can lose it by neglecting one.** The capped totals are what actually get
saved to `ownerMood`.

### Grace period and God Mode

The cumulative mood is **only updated** when:

```
g.get("season") >= g.get("gracePeriodEnd")  AND  NOT g.get("godMode")
```

- **Grace period:** When you take over a team you can't be fired for the first
  couple of seasons. `gracePeriodEnd` is set in
  `src/worker/core/league/createGameAttributes.ts`:
  `season + 3` if you join during/after the playoffs phase, otherwise
  `season + 2`. Default game attribute value is `0`.
- **God Mode:** Disables firing entirely (see below).

## The firing decision (`genMessage.ts`)

A boolean `fired` is determined as follows, in order:

### 1. Challenge-mode firings (checked first)

These can fire you regardless of owner mood:

- **`challengeFiredLuxuryTax`** — "You're fired if you pay the luxury tax."
  Fired if the latest team season's `expenses.luxuryTax > 0`.
- **`challengeFiredMissPlayoffs`** — "You're fired if you miss the playoffs."
  Fired if the latest team season's `playoffRoundsWon < 0`.

Both default to `false` (`src/common/defaultGameAttributes.ts`).

### 2. Normal (owner-mood) firing

If no challenge mode triggered, you are fired when **all** of these hold:

```
currentTotal <= -1                          // wins + playoffs + money for the current season
AND g.get("season") >= g.get("gracePeriodEnd")   // grace period is over
AND NOT g.get("godMode")                     // God Mode disables firing
```

In other words: once the grace period ends and you're not in God Mode, an
overall mood at or below **-1** gets you fired.

### Warning messages (not yet fired)

If you're past the grace period but not fired, the owner may warn you based on
projected mood:

- `currentTotal + deltas < -1` → **"Another season like that and you're
  fired!"**
- `currentTotal + 2 * deltas < -1` → **"A couple more seasons like that and
  you're fired!"**

### Flavor of the firing message

The exact firing text depends on which factors were negative:

- All three negative → "You've been an all-around disappointment. You're fired."
- Only money negative (wins & playoffs OK) → fired for not making enough profit.
- Only wins & playoffs negative (money OK) → fired for not fielding a
  competitive team.
- Otherwise → "You're fired."

## What happens when you're fired

In `genMessage.ts`, when `fired` is true:

- A message is added from **"The Owner"** with subject **"Annual performance
  evaluation"** inviting you to take over another team.
- `gameOver` is set to `true` via `league.setGameAttributes({ gameOver: true })`.

You then pick a new team on the **New Team** page
(`src/worker/views/newTeam.ts`):

- Your old team is removed from the list — **no re-hiring immediately after
  being fired**.
- If you were fired (not God Mode, not expansion), you may only choose from the
  **5 worst teams** by winning percentage.

## Related: teams wanting to hire you (the good outcome)

When you are **not** fired, `genMessage` also rolls whether other teams want to
poach you:

```
prob = bound(currentTotal, 0, 3) / 3
otherTeamsWantToHire = Math.random() < prob
```

The better your overall mood (up to 3), the more likely other teams offer you a
job. This sets the `otherTeamsWantToHire` game attribute (default `false`),
which lets you voluntarily switch to one of 5 selected teams without being
fired.

## Summary of the key thresholds

| Condition | Value / Source |
| --- | --- |
| Fired threshold (overall mood) | `currentTotal <= -1` |
| Grace period length | `+2` seasons (or `+3` if joining at/after playoffs) |
| Mood factor cap (per factor) | `1` |
| God Mode | Disables all firing and mood updates |
| Luxury-tax challenge fire | `expenses.luxuryTax > 0` |
| Miss-playoffs challenge fire | `playoffRoundsWon < 0` |
| Skipped entirely | auto play, spectator, or multi-team mode |

### Source files

- `src/worker/core/phase/genMessage.ts` — firing decision + message
- `src/worker/core/season/updateOwnerMood.ts` — mood delta calculation
- `src/worker/core/phase/newPhaseBeforeDraft.ts` — trigger point
- `src/worker/core/league/createGameAttributes.ts` — grace period setup
- `src/worker/views/newTeam.ts` — choosing a new team after firing
- `src/common/defaultGameAttributes.ts` — default values for the above attributes