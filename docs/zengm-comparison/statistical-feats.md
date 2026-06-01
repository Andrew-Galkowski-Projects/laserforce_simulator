# Statistical Feats

`/l/1/player_feats` vs. `templates/leagues/statistical_feats.html`
(`stats_statistical_feats`). See [README](README.md) for methodology / C-ids.

**ZenGM:** a **sortable table, one row per single-game feat** — full box-score stat
line for that game plus **Opp · Result · Season**. Team + season + Regular/Playoffs
+ page-size filters. (Populated: `David 'Judge' Fontenot SUP YWG … PLG W 2020`.)

**Ours:** a **list-group of ~9 feat categories** (triple-nuke games, Medic shutouts,
perfect-accuracy Heavy, single-game MVP/score leaders, tag streaks, resupply/
missile leaders, comeback wins — `stat_feats.py`). Each row: label · player name ·
value badge · "View round" link. No filters.

| Discrepancy | Type |
|---|---|
| ZenGM = **a feed of every notable single game** (one row per game, full stat line); ours = **a fixed set of category "best" entries** | ⚠ Gap → **LG-06e** (most material divergence here — different concept) |
| ZenGM team / season / playoffs / page-size filters | C5/C1/C3/C4 ⚠ |
| ZenGM rows are **sortable**; ours is a static list | C6 ⚠ → **LG-06c** |
| Our laser-tag feats (nukes, medic shutouts, comebacks) are a deliberate domain remap | = Intentional |
