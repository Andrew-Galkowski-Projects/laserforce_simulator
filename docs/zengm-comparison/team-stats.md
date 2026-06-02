# Team Stats

`/l/1/team_stats` vs. `templates/leagues/team_stats.html` (`stats_team_stats`).
See [README](README.md) for methodology / C-ids.

**ZenGM:** season selector; grouped superCols; columns Team · GP · Won · Lost ·
Min · K · D · A · KDA · then many **objective** breakdowns (towers / inhibitors /
creep score / CS-20 / jungle / dragon / baron, each with "by shotcalling /
team-skill / champion" sub-splits) · Gold. Also a **Team vs. Opponent** view.
(Populated example: `EL5  18  14  4  …`.)

**Ours:** Team · **Avg PF · Avg PA · Avg Margin · Avg Survivors · Total Tags ·
Total Times Tagged · Base Captures · Missiles Fired · Missiles Hit · Nukes Fired ·
Nukes Landed · Cancelled Nukes**. Sortable. Event→column mapping in
`matches/team_stats_logic.py` (base_capture / missiled(+hit) / special-nuke-detonation
/ nuke_cancelled).

| Discrepancy | Type |
|---|---|
| LoL objective stats (towers/dragon/baron/CS) → **base captures / missiles / nukes** | = Intentional (`stats.md`: "objectives → Team Stats") |
| ZenGM's "by shotcalling / by skill" sub-attribution has no analogue | = Intentional (no such model) |
| Our **Avg points for/against/margin/survivors** are richer scoring detail than ZenGM's flat K/D/A | extra (ours) |
| ZenGM **Team-vs-Opponent toggle** — we show only own-team aggregates | ⚠ Gap |
| ~~ZenGM season selector~~ → `?season=` selector (each Season + **Career**) | C1 ✓ Delivered (LG-06d) |
| No **W/L/GP** columns on our Team Stats (they're on Standings) | ▲ Layout |
