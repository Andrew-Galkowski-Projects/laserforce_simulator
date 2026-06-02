# Game Log

`/l/1/game_log` vs. `templates/leagues/game_log.html` (`stats_game_log`).
See [README](README.md) for methodology / C-ids.

**ZenGM:** **single-team-centric** — team selector **+ season selector**; columns
**Opp · W/L · Score**; each row expands to a box score.

**Ours:** **league-wide match log** — team *filter* ("All teams" + each team);
columns **Matchday · Date · Red · Blue · Score · Winner**; rows link to the round
detail page.

| Discrepancy | Type |
|---|---|
| ZenGM is **one team's** results (Opp / W-L / Score); ours is a **league-wide** list of all rounds (Red vs. Blue) | ▲ Layout (fundamental orientation difference) |
| ~~ZenGM season selector; ours current-season only~~ → `?season=` selector (each Season + **Career**) | C1 ✓ Delivered (LG-06d) |
| ZenGM inline expandable **box score**; ours links out to round detail | ▲ Layout |
| Our **Matchday / Date / Winner** columns are extra context | extra (ours) |
| Not sortable either side; ours could add sort | C6 → **LG-06c** |
