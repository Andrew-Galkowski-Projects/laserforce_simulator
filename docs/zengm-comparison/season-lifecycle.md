# Season lifecycle & dynamic behaviour (the Play button)

How the reference product's screens change as a season is played, observed by
playing the LOL GM league from 2020 through 2027 and walking a full year-cycle
with the **Play** button. See [README](README.md) for methodology.

The Play menu is **phase-sensitive** — its options change at every phase — and
the app **auto-navigates** to the screen relevant to each transition.

## Observed phase cycle (one year)

| Phase (nav label) | Play-menu options | Auto-navigates to | What changes |
|---|---|---|---|
| **Preseason** | *Until regular season* | — | New year's rosters set (post free-agency/draft); standings / leaders / per-season stats empty for the new year |
| **Regular season** | *One week · One week (live) · Two months · Until playoffs* | — | Standings, Power Rankings, League Leaders, Player/Team Stats, Game Log fill as games sim; season selectors gain the new year |
| **Playoffs** | *One day · One day (live) · One week · One month · Through playoffs* | **Playoffs** bracket | Bracket seeded from final standings, fills round-by-round (Quarterfinals→Semifinals→Finals + 3rd-place) |
| **Before draft / Draft** | *(blocked by "Read new message")* | **History** (season summary) | Season-end **awards inbox message**; **League History** gains a row — Champion, Runner-up, **Finals MVP**, **season MVP**, "Nth title"; the **Draft** turns Prospects into drafted rookies on rosters |
| **Re-signing** | *Continue re-signing players · Until free agency* | **Negotiation** | Expiring contracts re-signed team by team |
| **Free agency** | *One day · One week · Until preseason* | — | ~30-day countdown; unsigned players sign elsewhere |
| → next **Preseason** | … | — | Retirements feed **Hall of Fame** inductions (e.g. 2026 MVP "County" retired 2027 → inducted with Peak MMR/OVR + best-season + career-stat blocks) |

## Dynamic behaviours worth noting

- **Phase-sensitive Play menu** — different verbs per phase; the button shows
  **Stop** while simming, becomes **Read new message** when a blocking inbox
  message exists, and is **disabled entirely when your team is invalid** (too few
  players — this was the silent popup that initially blocked the playthrough).
- **Auto-navigation on transition** — Playoffs→bracket, season-end→History,
  re-signing→Negotiation. The app pushes you to the contextually relevant page.
- **Resets each regular season** — Standings and Power Rankings zero out; Power
  Rankings' **Performance** rank is `-` until games exist, then **diverges from
  the talent rank** (e.g. talent #1 / performance #2).
- **Played-not-authored history** — League History, Hall of Fame, and Awards
  (MVP / Finals MVP) are all produced by simming, then frozen into history.
- **Season selector everywhere** accrues one entry per year, plus a **Career
  Totals** option on Player Stats.

## How our model compares

| Aspect | ZenGM | Ours (LG-01) | Type / PLAN |
|---|---|---|---|
| Lifecycle | preseason → regular → playoffs → draft → re-sign → free agency → (loop) | **draft preview → active → completed**; "Start Next Season" clones teams into a new draft | ⚠ Gap — no playoffs/draft/offseason (**LG-02** playoffs/tournaments) |
| Play surface | phase-sensitive menu | **Play One Week / Two Months / Until End of Season** + Start Season / Start Next Season (LG-01d) — analogous to ZenGM's *regular-season* menu only, **not** phase-aware | ▲ Layout — relabel/extend at **LG-02** |
| Auto-navigation on phase change | yes | no | ⚠ Gap (minor) |
| Awards (MVP / Finals MVP) on History | yes | **none** — History shows champion / runner-up / top-3 only | ⚠ Gap → **LG-03** (compute awards) + surface on League History (LG-01f) |
| Playoffs / Hall of Fame / Draft / Free agency screens | live | deferred (no model) | = Intentional (**LG-02**, finances/draft/retirement deferred) |
| Standings reset per season | yes (same league) | yes (new Season object) | ✓ analogue |

These are **structural / roadmap-level** divergences — bigger than the
column-level gaps in the per-page docs — and most are already on the deferred
list. The two cheap, in-domain additions surfaced by the playthrough are
**season-MVP / Finals-MVP awards on League History** (**LG-03**) and a
**phase-aware Play menu** if/when a playoff stage lands (**LG-02**).
