# Player Ratings

`/l/1/player_ratings` vs. `templates/leagues/player_ratings.html`
(`stats_player_ratings`). See [README](README.md) for methodology / C-ids.

**ZenGM:** All-Teams filter · season selector · page-size. Columns: Name ·
Position · Team · Age · Region · **Rank (Challenger/Diamond/…) · MMR · Overall ·
Potential** · ~17 LoL rating attributes (Adaptability, Fortitude, Consistency,
Team Player, Leadership, Awareness, Laning, Team Fighting, Risk Taking,
Positioning, Skill Shots, Last Hitting, Summoner Spells, Stamina, Injury
Resistant) · Languages · Country.

**Ours:** Name · Team · Role · Overall · **the 19 Laserforce attributes** · Age ·
Site · Ht · GP · Start · MMR(`-`) · Rank(`-`) · Pot(`-`). Sortable; Prev/Next
pagination.

| Discrepancy | Type |
|---|---|
| LoL's 17 attributes → our **19 attributes** (awareness trio, synergies, accuracy, survival, …) | = Intentional (one-to-one domain remap) |
| Rank tier / MMR / Potential `-` proxies | = Intentional (STAT-PROXY-01) |
| **No team filter** | C5 ⚠ → **LG-06b** |
| **No season selector** | C1 ⚠ → **LG-06d** |
| **No page-size selector** (Prev/Next only) | C4 ⚠ → **LG-06a** |
