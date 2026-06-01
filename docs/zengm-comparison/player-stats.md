# Player Stats

`/l/1/player_stats` vs. `templates/leagues/player_stats.html` (`stats_player_stats`).
See [README](README.md) for methodology / C-ids.

**ZenGM:** All-Teams filter · **Career Totals / Season** (selector now lists
2020–2027 + Career) · **Per Game / Per 36 / Totals** · **Regular / Playoffs** ·
page-size. Grouped superCols; many LoL columns (K/D/A/KDA + objective + CS + gold).

**Ours:** Name · Team · GP · Points · MVP · Tags · Tagged · Tag Ratio · Acc% ·
Survival(s) · Lives · Resup · Missiles · Specials · Follow-up · Reaction · Combo
Resup (`season_player_stats` — counts summed, MVP/Acc/Survival meaned, Tag Ratio
sum/sum). Sortable; Prev/Next pagination.

| Discrepancy | Type |
|---|---|
| LoL K/D/A/CS/objectives → our **Tags / Tagged / Tag Ratio / Acc% / Survival / Missiles / Specials / Follow-up / Reaction / Combo** | = Intentional (dictionary) |
| **No team filter** | C5 ⚠ → **LG-06b** |
| **No Career-vs-Season**, **no Per-Game/Per-36/Totals**, **no Regular/Playoffs** toggles | C7/C2 ⚠ → **LG-06d** (Playoffs = C3, LG-02) |
| **No page-size selector** | C4 ⚠ → **LG-06a** |
| Our **MVP** column is an extra not in ZenGM | extra (ours) |
