# ZenGM Player System

These documents explain how players work in the ZenGM engine (Basketball GM,
Football GM, ZenGM Baseball, ZenGM Hockey):

1. **[Player creation](./01-player-creation.md)** — how a brand-new player's body
   and ratings are generated from scratch.
2. **[The potential (pot) rating](./02-potential-rating.md)** — what "potential"
   means, and how it is calculated/measured.
3. **[Player development](./03-player-development.md)** — how ratings rise and
   fall as a player ages over seasons.

## Orientation

All of the logic lives in the worker process, under
`src/worker/core/player/`. The engine is multi-sport: most files have a generic
entry point (e.g. `genRatings.ts`) that dispatches to a per-sport
implementation (`genRatings.basketball.ts`, `genRatings.football.ts`, etc.) via
the `bySport()` helper. **These documents use basketball as the primary worked
example**, since it is the default sport, but they point out the per-sport files
so you can find the equivalent logic for other sports.

Key concepts shared across all three documents:

| Term | Meaning |
| --- | --- |
| **Rating** | A single 0–100 attribute (e.g. `spd` speed, `tp` three-point). Stored per season. |
| **`ovr`** | Overall rating, 0–100, a weighted blend of the individual ratings. |
| **`pot`** | Potential — an estimate of the highest `ovr` the player will reach by age 29. |
| **`fuzz`** | Per-rating noise that hides a player's true ratings from the user (scouting uncertainty). |
| **`value`** | An internal worth score (not shown directly) used by the AI for trades, drafting, roster cuts. |
| **`scoutingLevel` / `coachingLevel`** | Team facility budget levels (1–100) that affect fuzz and development. |

A player object stores a **history** of ratings rows (`p.ratings[]`), one per
season. "Developing" a player almost always means operating on the *last* row
(`last(p.ratings)`), see `src/common/utils.ts`.
