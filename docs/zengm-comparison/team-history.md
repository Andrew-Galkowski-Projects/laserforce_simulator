# Team History

`/l/1/team_history` vs. `templates/leagues/team_history.html` (`team_history`).
See [README](README.md) for methodology / C-ids.

**ZenGM (players table):** Name · Position · GP · Min/G · K/G · D/G · A/G · KDA ·
Creep Score · **Last Season with Team**. Team picker + **page-size selector**.
(ZenGM's season-by-season record + retired numbers live in a separate header block.)

**Ours:** richer single page — **Overall** card (round W-L-T, playoff appearances,
championships) · **Seasons** table (Year · Record W-L-T · Final rank) · **Players**
table (Name · Games · Points · Tags · Times tagged · Missiles · Resupplies ·
Specials · Last season) with **green = still on team / blue = now elsewhere**
colour coding. Team picker only.

| Discrepancy | Type |
|---|---|
| We combine **season-by-season record + championships + player rollup** on one page; ZenGM's page is just the player table | ▲ Layout (ours arguably richer) |
| Player table uses **laser-tag career stats** vs. LoL K/D/A/CS | = Intentional (dictionary) |
| Green/blue current-vs-former colouring is **ours only** | extra (ours) |
| ZenGM has a **page-size selector**; ours has **no pagination at all** (could blow up for long franchises) | ⚠ Gap → **LG-06a** |
| Player table is **not sortable** | C6 ⚠ → **LG-06c** |
