# Roster

`/l/1/roster` vs. `templates/leagues/team_roster.html` (`team_roster`).
See [README](README.md) for methodology / C-ids.

**ZenGM:** one table; columns **(select) · Name · Position · Age · Region · Years
With Team · MMR · Overall · Potential · Contract · GP · Min/G · KDA · Gold(k) ·
Release · First-Choice Pick · First-Choice Ban · Trade For · Languages · Country**.
Controls: team picker **+ season selector + champion pick/ban dropdowns**; starters
separated from bench by a divider; **drag to reorder**, inline **Release / edit**.

**Ours:** **two** tables — *Starting Six* (Role · Name · Age · Home Site · Height ·
Games · Started · MMR · Rank · Overall · Potential) and *Bench* (same, minus Role).
Team-picker dropdown only. Read-only.

| Discrepancy | Type |
|---|---|
| MMR / Rank / Potential present but rendered `-` | = Intentional (STAT-PROXY-01) |
| Contract / Release / pick / ban / Trade-For dropped | = Intentional (finances/roster mgmt deferred, C9) |
| Region → **Home Site**; Country / Languages dropped | = Intentional (`stats.md` dictionary) |
| ZenGM shows per-game performance (Min/G, KDA, Gold) on the roster; ours shows none (performance lives on Player Stats) | ▲ Layout |
| **Years With Team** (tenure) column | ⚠ Gap (minor) |
| No **season selector** on our roster | C1 ⚠ (LG-06d) |
| Single sortable table vs. our fixed two-table Starting/Bench split | ▲ Layout |
