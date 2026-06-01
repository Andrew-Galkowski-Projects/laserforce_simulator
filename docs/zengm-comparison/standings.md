# Standings

`/l/1/standings` vs. `templates/seasons/standings.html` (`season_standings`).
See [README](README.md) for methodology and the C1–C10 cross-cutting ids.

**ZenGM columns:** Team · **W · L · Pct · GB · Blue (home) · Red (away) · Streak
· L5 · Towers · K · D · A**. Plus a secondary mini "conference" standings table,
a **season selector** (2020–2027), and playoff-line / clinched indicators.
(Populated example: `Team Unity (1)  14  4  .778  0  5-4  9-0  Lost 1  4-1  …`)

**Ours:** Rank · Team · **MP · W · L · T · Pts · RW · TS** (matches_played, wins,
losses, ties, league_points, round_wins, total_score). Season state badge,
draft-preview banner, breadcrumb, a "Champion:" line when completed.

| Discrepancy | Type |
|---|---|
| We have **Ties (T)** + **league points (Pts, 3/1/0)** + **round wins (RW)**; ZenGM is pure W/L with **Pct + Games Behind (GB)** | ▲ Layout (laser-tag rounds vs. LoL best-of) |
| ZenGM splits **home/away** (Blue/Red) and shows **Streak + Last-5 (L5)** form | ⚠ Gap → **LG-06g** (we have per-round side data but don't surface a side/form split) |
| ZenGM surfaces aggregate **K / D / A / Towers** on Standings | ⚠ Gap (those live on our Team Stats screen instead) |
| ZenGM **season selector** + playoff-clinch indicators | C1 ⚠ (LG-06d) / C3 = (LG-02) |
| Our **draft-preview** mode (rank teams by computed rating pre-start) | extra (ours) |
| Standings & Power Rankings **reset each regular season** in ZenGM; ours resets via a new Season object | ✓ analogue |
