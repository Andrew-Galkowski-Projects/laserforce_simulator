# Season-end player development as a ZenGM age curve with a per-Season ratings history

**Status:** Accepted (LG-04, 2026-06-10)

## Context

PLAN.md **LG-04** ("Season-end stat updates") framed the end-of-season stat
update as driven by three factors — *new experience (games played this
season)*, *player age*, and *prior experience (historical games)* — with
default weights "fixed in code but overridable per season by the league
admin."

During the LG-04 grilling session (2026-06-10) the maintainer confirmed the
system is modeled faithfully on **ZenGM** (Basketball/Football/Hockey GM) and
supplied four reference docs
(`Screenshots_and_video_examples/player_creation_and_updates/`). The ZenGM
development engine (`developSeason`) **contradicts the PLAN framing**: ratings
change is driven **purely by an age curve** (a base change by age + random
noise + per-rating age modifiers + change limits, scaled by a team coaching
budget), run every **preseason** on *every active player regardless of minutes
played*. In ZenGM, in-game production never moves ratings — it only feeds a
separate `value` (the AI's trade score). This ADR records the decisions that
resolve the framing mismatch and the architectural questions ZenGM raises
against this codebase.

## Decisions

1. **Follow ZenGM faithfully; age drives development, games-played does not.**
   LG-04 implements the `developSeason` age curve (young trend up, peak
   mid-to-late 20s, older decline increasingly fast) with random noise and
   per-stat change limits, clamped to 0–100. The PLAN's "experience" framing is
   **discarded**. `Player.total_games` is kept but is **cosmetic** — ticked at
   each rollover (active Team player by their real regular-season appearance
   count; free-agent-pool player by a smaller random amount) and **never an
   input to the formula**.

2. **Full per-stat age curves.** Each of the 19 **Stats** gets its own
   `ageModifier(age)` + `changeLimits(age)`, mapped by analogy to a ZenGM
   archetype (athletic stats fade earliest, awareness/skill stats persist
   longest). With no laser-tag regression data these 19 curves are
   **invented-by-analogy, locked as tunable constants, calibration-deferred**
   to a later balance pass.

3. **Per-League season clock, not ZenGM's global clock.** ZenGM has one global
   season year; this app has *N* independent per-League `Season` chains rolled
   by each League's own `next_season`. Development is therefore **league-scoped**
   and fires at **`next_season`** (the preseason analogue), aging only the
   **developing set** — the rolling League's carried-forward Teams' players plus
   its `free_agent_pool` players. A Player not in a rolling League does not age.
   The global "everyone ages together on one year clock" is explicitly **not**
   adopted (it would double-age any Player visible to multiple rolling Leagues
   and has no key the schema can carry).

4. **Persist a per-Season ratings history (`PlayerSeasonRating`).** Rather than
   mutating live `Player` Stat fields with no audit trail, LG-04 ships a new
   `PlayerSeasonRating` model (FK `teams.Player` + FK `matches.Season`,
   `unique(player, season)`; the 19 Stat snapshot + `age` + `overall_rating` +
   a reserved nullable `potential` for LG-05). Lives in `matches/` (which
   already depends on `teams`) to avoid a cross-app FK inversion. A **baseline**
   row is written at `league_create` (as-generated Stats, no development); a
   **developed** row at each `next_season`. The live `Player` fields stay the
   Simulator's source of truth; the rating row is a read-only snapshot.

5. **Random, but no stored development seed.** The develop math is a Django-free
   pure module (`matches/development.py`, injected `random.Random`, guarded by a
   `TestNoDjangoImportsLeaked` subprocess check — the `draw.py` /
   `player_generator.py` precedent). Production builds a **fresh** RNG per
   rollover; **no seed is stored** — the `PlayerSeasonRating` row *is* the audit
   trail (matches ZenGM, which stores resulting ratings, not a dev seed).
   Development is outside the SIM-07/08 seed chain.

## Consequences

- **No Score Calibration re-baseline.** LG-04 mutates Stat *inputs* but changes
  no simulation *mechanic*, so the calibration targets are untouched.
- **First persisted mutation of `Player` stats in the league flow.** Every prior
  LG slice was read-only/derived; LG-04 writes. A migration ships (the
  `PlayerSeasonRating` model).
- **The coaching/scouting budget knob is deferred.** ZenGM scales development by
  a per-team coaching budget; this app has no per-(team, season) state. LG-04
  ships a fixed-in-code coaching-effect of **0** (pure age curve) and defers the
  per-team budget model to a combined slice designed *with* LG-05's scouting
  budget.
- **Retirement and replacement intake are deferred.** Players age and decline
  indefinitely (no cap, no removal); a retirement/regen lifecycle is its own
  later grill.
- **Free agents outside any rolling League never age.** An acceptable gap; a
  global free-agent-aging pass can be added later if wanted.
- **UI:** LG-04 fills the existing LG-06h `league-player-ratings-history-stub`
  with a minimal overall-rating-over-time trend + per-Season Stat table;
  Potential stays a `"—"` stub until LG-05.
