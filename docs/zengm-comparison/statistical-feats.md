# Statistical Feats

`/l/1/player_feats` vs. `templates/leagues/statistical_feats.html`
(`stats_statistical_feats`). See [README](README.md) for methodology / C-ids.

**ZenGM:** a **sortable table, one row per single-game feat** — full box-score stat
line for that game plus **Opp · Result · Season**. Team + season + Regular/Playoffs
+ page-size filters. (Populated: `David 'Judge' Fontenot SUP YWG … PLG W 2020`.)

**Ours:** a **per-game feed (LG-06e)** — one sortable row per **(player, round)**
that achieved a feat, carrying that round's full box-score line plus **Opp ·
per-Round Result · Season**, deep-linking to the Round (`stat_feats.py`'s
`scan_feats -> (list[FeatRow], list[TeamFeatRecord])`). **Hybrid qualification:**
a row is included if it crosses a per-game threshold (triple-nuke, Medic shutout,
perfect-accuracy Heavy, high tags/points/MVP/resupplies/missiles) OR is a
season-best leader (tagged "season best"); a row stacks one `FeatBadge` per kind.
Comeback wins moved to a separate **Team feats** section. Sortable over every
column, paginated (10/25/50/100), team + season (incl. **Career**) filters;
default sort = most recent first.

| Discrepancy | Type |
|---|---|
| ZenGM = **a feed of every notable single game** (one row per game, full stat line); ours is now the same — **one row per (player, round) feat** with the round's box-score line + Opp / Result / Season, deep-linking to the Round; comeback wins split into a separate Team-feats section | ✓ Delivered (LG-06e) |
| ZenGM team / season / playoffs / page-size filters — season delivered (`?season=` selector, each Season + **Career**); team filter = C5 (LG-06b); page-size = C4 (LG-06a, now wired on this screen by LG-06e); playoffs = C3 (LG-02) | C1 ✓ (LG-06d); C5 ✓ (LG-06b); C4 ✓ (LG-06a/e); C3 ⚠ |
| ZenGM rows are **sortable**; ours is now sortable over every column (C6, LG-06c, expanded by LG-06e to the full box-score column set) | C6 ✓ Delivered (LG-06c/e) |
| Our laser-tag feats (nukes, medic shutouts, comebacks) are a deliberate domain remap | = Intentional |
