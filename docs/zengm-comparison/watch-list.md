# Watch List

`/l/1/watch_list` vs. `templates/leagues/watch_list.html` (`players_watch_list`).
See [README](README.md) for methodology / C-ids.

**ZenGM:** the **full player-stats table** filtered to watched players — Name ·
Position · Age · Team · MMR · Overall · Potential · Contract · GP · Min · K/G ·
D/G · A/G · CS · Gold — with **Rate (Per Game / Per 36 / Totals) + Regular/
Playoffs + page-size** toggles. Persisted in the league save.

**Ours:** minimal — Player · Team · **Overall** · Remove action; plus an "Add a
player" form and a "Remove All" button. Stored in the **browser session only**.

| Discrepancy | Type |
|---|---|
| ZenGM watch list = a *rich filtered stats view*; ours = a *bookmark list* (3 columns + add/remove) | ⚠ Gap → **LG-06f** (most material divergence here) |
| ZenGM persists watch list **in the save**; ours is **browser-session-local** (resets per browser, not per user) | ⚠ Gap → **UX-01** (acknowledged in template) |
| ZenGM rate / playoffs / page-size toggles | C2 / C3 / C4 |
| Explicit **Add form + Remove All** | extra (ours) |
