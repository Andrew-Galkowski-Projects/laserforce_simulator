# League Leaders

`/l/1/leaders` vs. `templates/leagues/league_leaders.html` (`stats_league_leaders`).
See [README](README.md) for methodology / C-ids.

**ZenGM:** 4 top-10 boards — **Kills · Assists · Creep Score · KDA** (per game),
with a **season selector**. (Populated: `1. FluffyKnight, EGA  6.6` …)

**Ours:** 4 boards — **Average Tags · Average Score · Fewest Times Tagged · Tag
Ratio**, top entries each (`league_leaders_logic.compute_leaderboards`).

| Discrepancy | Type |
|---|---|
| Kills→Tags, KDA→Tag Ratio, CS→(dropped), Assists→(no analogue) — we substituted **Avg Score** and **Fewest Times Tagged** | = Intentional (`stats.md` board mapping) |
| ZenGM **season selector** | C1 ⚠ (LG-06d) |
| ZenGM boards are sortable / clickable into a fuller page; ours are static top-N | C6 ⚠ → **LG-06c** |
