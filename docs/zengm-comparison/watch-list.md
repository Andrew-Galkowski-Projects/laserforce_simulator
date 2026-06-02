# Watch List

`/l/1/watch_list` vs. `templates/leagues/watch_list.html` (`players_watch_list`).
See [README](README.md) for methodology / C-ids.

**ZenGM:** the **full player-stats table** filtered to watched players — Name ·
Position · Age · Team · MMR · Overall · Potential · Contract · GP · Min · K/G ·
D/G · A/G · CS · Gold — with **Rate (Per Game / Per 36 / Totals) + Regular/
Playoffs + page-size** toggles. Persisted in the league save.

**Ours (LG-06f):** the **full Player-Stats column set** filtered to watched
players (zero-filled for watched players with no Rounds in scope), with the
**Rate (Totals / Per Game / Per 10 min) + season (incl. Career) + page-size +
sortable-column** kit (no team filter — the watch list is a personal cross-team
set), plus a per-row **watch flag** and a "Remove All" button. The flag also
lives on 8 league screens (instant red/grey JS toggle). Stored in the **browser
session only**, now keyed **per-League** (`session["watch_lists"]`).

| Discrepancy | Type |
|---|---|
| ZenGM watch list = a *rich filtered stats view*; ours = the *Player-Stats column set filtered to watched players* | ✓ Shipped (LG-06f) |
| ZenGM persists watch list **in the save**; ours is **browser-session-local** (resets per browser, not per user) | ⚠ Gap → **UX-01** (acknowledged in template) |
| ZenGM rate / playoffs / page-size toggles | C2 / C3 / C4 |
| Explicit **Add form + Remove All** | extra (ours) |
