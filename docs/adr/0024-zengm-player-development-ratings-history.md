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

## Consequences — LG-05 (Player potential)

LG-05 (2026-06-10) fills the **reserved `potential` column** this ADR added,
via a **noise-free forward projection** of this ADR's age curve (Decision 2):
the per-stat curve is rolled forward from the player's current age to age 40
with zero noise (a `0.9` midpoint multiplier in place of the develop noise),
tracking the running-max overall as the ceiling, **floored at the current
overall** and capped at 100, plus a **scouting-noise band** off a fixed
`DEFAULT_SCOUTING_BUDGET = 50`. The gauss draw runs on a **separate
`random.Random()`** so LG-04's seeded develop output is byte-unchanged. The
**per-team scouting/coaching budget** this ADR deferred (Consequences) **remains
deferred to CAR-01**, which promotes the fixed constant to a per-team field.
Because `potential` is **read-only to the simulator** (never a sim input — no
Score Calibration re-baseline) and **reversible** (recomputed each rollover; a
nullable `FloatField` add — `teams/0012_player_potential.py`), LG-05 needed
**no new ADR**; this ADR stays Accepted/LG-04.

## Consequences — FIN-02 (coaching budget → development)

FIN-02 (2026-06-16) fulfils the **coaching/scouting budget knob** this ADR
deferred (Consequences, bullet 3) — the half about **coaching → development**
(scouting → potential remains deferred to FIN-03). The fixed-at-`0` coaching
effect is replaced by a real, ZenGM-faithful **Coaching effect** drawn from
FIN-01's per-Team coaching **Budget level**:

- **Mechanism (ZenGM-faithful, per `03-player-development.md`).** The per-player
  base age-change (`base_change(age) + base_change_noise(age, rng)`) is scaled
  once, directionally, **before** the per-stat loop:
  `effective *= 1 + sign(effective) * coaching_effect`. Good coaching amplifies
  positive growth and *softens* decline; the per-stat `age_modifier` /
  `change_limits` / `uniform(0.4, 1.4)` math is otherwise byte-unchanged.
  **No new RNG draw** — the pinned 1-gauss-then-19-uniform sequence is preserved.
- **Effect source.** A new pure `finance.coaching_effect(level) -> float`
  (`MAX_COACHING_EFFECT = 0.09`, reusing FIN-01's asymmetric facilities-band
  shape `(clamp(level,1,100) − 34) / (100 − 34)`) — `0.0` at the neutral level
  34, `+0.09` at level 100, `≈ −0.045` at level 1. The view passes the float into
  `development.develop_player_stats(..., coaching_effect=0.0)`; `development.py`
  keeps its frozen import allowlist (no `finance` import). **Default `0.0` ⇒
  multiplier exactly `1.0` ⇒ byte-identical to LG-04.**
- **Multi-Season smoothing (the `getLevelLastThree` / CONTEXT.md "Budget level"
  contract).** The level fed to the effect is a **games-weighted average over the
  last up-to-3 completed Seasons**, sourced from **new per-Season budget-level
  snapshots on `TeamSeasonFinance`** (all three of `coaching` / `scouting` /
  `facilities`, one migration — future-proofs FIN-03/FIN-04), with a fallback to
  the team's current `budget_coaching` when no snapshot history exists. The
  current/upcoming budget choice does **not** reach development until it has been
  a played, snapshotted Season — "a single splurge barely moves the needle."
- **Gating.** Active only when `League.finance_enabled` (the FIN-01 toggle) ON
  TOP of CAR-03's `_is_career_league`. Finance-OFF (and any Team left at the
  neutral level) ⇒ effect `0.0` ⇒ develop output **byte-identical to LG-04**.
- **Potential is untouched.** `_project_stat_noise_free` /
  `_project_peak_overall` gain **no** `coaching_effect` — LG-05's seeded
  potential output is byte-unchanged (coaching → potential is FIN-03's lane). A
  well-coached player may now develop *past* their projected ceiling; the
  `[current_overall, 100]` clamp keeps that safe.
- **No Score Calibration re-baseline.** Stat *inputs* shift for finance-ON
  Leagues but no simulation *mechanic* changes (the same posture as LG-04/LG-05).
  Reversible (a snapshot column + a multiplier) and unsurprising given this ADR +
  ADR-0027 (FIN-01) — hence a Consequences addendum, **not** a new ADR.
