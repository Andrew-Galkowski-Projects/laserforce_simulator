# Free Agents

`/l/1/free_agents` vs. `templates/leagues/free_agents.html` (`players_free_agents`).
See [README](README.md) for methodology / C-ids.

**ZenGM:** Name · Position · Age · Region · MMR · Overall · Potential · Min/G ·
K/G · D/G · A/G · KDA · CS · **Asking for · Mood · Negotiate** · Country ·
Languages. **Page-size selector** (10/25/50/100).

**Ours:** Name · Team · Roles · Overall · **all 19 rating attributes** · Age · Site
· Ht · GP · Start · MMR(`-`) · Rank(`-`) · Pot(`-`). Sortable on Name/Team/Role/
Overall + the 19 attributes; bio + proxies fixed. Prev/Next pagination.

| Discrepancy | Type |
|---|---|
| Asking-for / Mood / **Negotiate** action dropped | = Intentional (finances deferred, C9) |
| MMR / Rank / Potential `-` | = Intentional (STAT-PROXY-01) |
| ZenGM shows **per-game performance** (K/D/A/CS); ours shows the **19 rating attributes** instead | ▲ Layout (free agents have no season performance in our model) |
| **No page-size selector** (Prev/Next only) | C4 ⚠ → **LG-06a** |
| Our 30+ column attribute table is very wide vs. ZenGM's ~13 | ▲ Layout |
