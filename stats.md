# Player Stats by Screen — Laserforce mapping

This file maps the ZenGM (LoL GM) reference screenshots in
`Screenshots_and_video_examples/` onto the stats the **Laserforce simulator** actually
has. The screenshots are League-of-Legends GM pages (kills / towers / dragons / CS /
MMR / contracts); this document records, per screen, the **Laserforce-domain columns**
we want — the spec the LG-01z league-sidebar screens implement.

The LG-01z screens live in `matches/league_screens/` (views) +
`templates/leagues/*.html` (templates). Where a screen is already live, the column set
below is what it renders; where a LoL screen needs a model we don't have yet (finances,
draft, retirement), it is marked **DEFERRED** with the blocking model named.

---

## Our data (the source columns)

### Player rating attributes — `teams.models.Player` (0–100 each)
The 19 stored attributes plus the derived `overall_rating` (their unweighted mean).

| Group | Fields |
|-------|--------|
| Awareness | `player_awareness`, `game_awareness`, `resource_awareness` |
| Decision | `decision_making` |
| Physical | `positioning`, `stamina`, `speed`, `flexibility`, `adaptability` |
| Team | `communication`, `teamwork` |
| Role / skill | `Offensive_synergy`, `defensive_synergy`, `midfield_synergy`, `resupply_synergy`, `resupply_efficiency`, `accuracy`, `survival`, `special_usage` |
| Summary | `overall_rating` (`@property`, mean of the 19) |

### Player profile — `teams.models.Player`
`age`, `started_playing_age`, `total_games`, `home_site`, `height`, `preferred_roles`.

### Per-round performance — `matches.models.PlayerRoundState`
`points_scored`, `tags_made`, `times_tagged`, `shots_missed`, `final_lives`,
`resupplies_given`, `missiles_landed`, `specials_used`, `follow_up_shots`,
`reaction_shots`, `combo_resupply_count`, `missile_points`, `was_eliminated_at` (tick),
plus the derived `get_mvp` (MVP rating) and `get_accuracy` (% of shots that tagged).

### Derived season figures
- **Tag ratio** (our KDA) = `sum(tags_made) / max(sum(times_tagged), 1)`.
- **Survival (s)** = `mean(min(was_eliminated_at, 1800)) / 2` — ticks capped at the round
  length (`SURVIVED_SENTINEL`), `÷2` to seconds at the display boundary (TIME-01).

### Proxies not modelled yet — render `-`
**MMR**, **Rank tier**, **Potential** have no Laserforce equivalent today. Per the
decision to keep the columns, every screen below renders a literal `-` placeholder in
these slots until the follow-up task lands (see PLAN.md **STAT-PROXY-01**).

### Have no Laserforce analogue — dropped
`Weight`, `Country`, `Languages`, `Born`, `Region` (folded into **Home Site**),
`Contract / $` and `Asking For / Mood` (finances deferred), `Assists` (no assist stat —
support contribution is `resupplies_given`), the LoL objective stats `Towers /
Inhibitors / Dragon / Baron / CS / CS-20 / Jungle / River` (no creep/objective model —
the closest analogues are base captures, missiles, and nuke detonations, surfaced on
**Team Stats**), and `Gold` (folded into **Points**).

---

## LoL → Laserforce stat dictionary

| LoL stat | Laserforce equivalent |
|----------|----------------------|
| Position | Role (`preferred_roles`; per-round `PlayerRoundState.role`) |
| Region / Country | Home Site (`home_site`) |
| Age | `age` |
| Turned Pro | Started age (`started_playing_age`) |
| Height | `height` |
| Overall (Ovr) | `overall_rating` |
| Potential (Pot) | `-` (proxy, deferred) |
| MMR | `-` (proxy, deferred) |
| Rank (Challenger/Diamond/Master) | `-` (proxy, deferred — letter tiers) |
| K (kills) | Tags (`tags_made`) |
| D (deaths) | Tagged (`times_tagged`) |
| A (assists) | — (no analogue; nearest = `resupplies_given`) |
| KDA | Tag ratio (`tags_made / max(times_tagged, 1)`) |
| Gold | Points (`points_scored`) |
| Min (minutes) | Survival seconds (`was_eliminated_at ÷ 2`) |
| GP (games played) | Games (rounds the player appears in) |
| Towers / Inhibitors / Dragon / Baron | Base captures / missiles / nuke detonations (Team Stats) |
| CS / Jungle / Gold income | — (no analogue; Points is the catch-all) |

---

## Screens

| # | LoL screen | File | Laserforce screen | Status |
|---|-----------|------|-------------------|--------|
| 1 | Player Ratings | `league_player_stats.png` | Player Ratings (`/stats/player-ratings/`) | **LIVE** |
| 2 | Player Stats | `league_player_statistic_feats.png` | Player Stats (`/stats/player-stats/`) | **LIVE** |
| 3 | Player Detail | `player_detail.png` | Player career page (`/players/<id>/stats/`, HX-01) | LIVE (existing) |
| 4 | League Leaders | `leauge_stat_leaders.png` | League Leaders (`/stats/league-leaders/`) | **LIVE** |
| 5 | Statistical Feats | `league_player_single_game_statistical_feats.png` | Statistical Feats (`/stats/statistical-feats/`) | **LIVE** |
| 6 | Roster | `league_roster_view.png` | Team Roster (`/team/roster/`) | **LIVE** |
| 7 | Free Agents | `league_free_agent_view.png` | Free Agents (`/players/free-agents/`) | **LIVE** |
| 8 | Trading Block | `league_trade_block_view.png` | — | **DEFERRED** (no finances / cap model) |
| 9 | Trade | `league_trade_view.png` | — | **DEFERRED** (no finances / cap model) |
| 10 | Future Prospects | `league_prospects_view.png` | — | **DEFERRED** (no draft model) |
| 11 | Hall of Fame | `league_hall_of_fame.png` | — | **DEFERRED** (no retirement / career-arc model) |
| 12 | Dashboard (Starting Lineup) | `league_dashboard_view.png` | League / Season dashboard (LG-01c) | LIVE (existing) |

Live LG-01z screens with no single LoL row above (LoL team-level / extra screens):
**Power Rankings** (`league_power_rankings_view.png`), **Game Log**
(`league_game_log.png`), **Team Stats**, **Team History**, **Watch List**.

---

### 1. Player Ratings — `/stats/player-ratings/` · LIVE
Sortable, paginated table of every Player on a Team enrolled in the displayed Season,
showing rating attributes (NOT performance — that is Player Stats).

| Group | Columns |
|-------|---------|
| Identity | Name (→ career page), Team, Role (`preferred_roles`) |
| Summary | Overall, Potential `-` |
| Proxies | MMR `-`, Rank `-` |
| Bio | Age, Home Site, Height, Games, Started age |
| Attributes (19) | Player Aware, Game Aware, Resource Aware, Decision, Positioning, Stamina, Speed, Flexibility, Adaptability, Communication, Teamwork, Offensive Syn, Defensive Syn, Midfield Syn, Resupply Syn, Resupply Eff, Accuracy, Survival, Special Usage |

Name / Team / Role / Overall + the 19 attributes are sortable (LG-00c `_SORT_KEYS`
whitelist). Bio + the three proxies render as fixed (non-sortable) columns.

### 2. Player Stats — `/stats/player-stats/` · LIVE
Sortable, paginated per-player **performance** aggregated over the displayed Season's
completed Rounds. Counts are summed across rounds; MVP / Acc% / Survival are per-round
means; Tag ratio is sum/sum.

| Column | Source |
|--------|--------|
| Name (→ career page) | `player.name` |
| Team | per-round team |
| GP | rounds played |
| Points | Σ `points_scored` |
| MVP | mean `get_mvp` |
| Tags | Σ `tags_made` |
| Tagged | Σ `times_tagged` |
| Tag Ratio | Σtags / max(Σtagged, 1) |
| Acc% | mean `get_accuracy` |
| Survival | mean survival seconds |
| Lives | Σ `final_lives` |
| Resup | Σ `resupplies_given` |
| Missiles | Σ `missiles_landed` |
| Specials | Σ `specials_used` |
| Follow-up | Σ `follow_up_shots` |
| Reaction | Σ `reaction_shots` |
| Combo Resup | Σ `combo_resupply_count` |

### 3. Player Detail — player career page (`/players/<id>/stats/`) · LIVE (HX-01)
The existing career page is our Player-Detail card: career totals (games, avg points,
tag ratio, avg survival, avg accuracy, avg SP earned), per-role benchmark table, and a
rolling-mean points trend. The 19 rating attributes for one player are also shown on the
player edit form. No new screen — the LoL Player Detail maps onto this.

### 4. League Leaders — `/stats/league-leaders/` · LIVE
Four top-10 leaderboards over all players in the Season's completed Rounds. LoL's
Kills / Assists / Creep Score / KDA boards map to:

| Board | Stat |
|-------|------|
| Average tags | mean `tags_made` (desc) |
| Average score | mean `points_scored` (desc) |
| Fewest times tagged | mean `times_tagged` (asc) |
| Tag ratio | sum/sum (desc) |

### 5. Statistical Feats — `/stats/statistical-feats/` · LIVE
One row per notable single-round / single-match feat, each deep-linking to its Round.
LoL's "single-game statistical feats" map to our nine detected feats: triple-nuke
games, Medic shutouts, perfect-accuracy Heavy rounds, single-game MVP / score leaders,
tag streaks, resupply / missile leaders, and comeback wins.

### 6. Team Roster — `/team/roster/` · LIVE
A selected enrolled Team's Starting Six (`active_roster`) + Bench (`bench_players`),
team-picker dropdown. Per LoL roster columns (Position / Age / Region / MMR / Ovr / Pot /
Contract / GP / KDA / …) mapped to:

| Column | Source |
|--------|--------|
| Role (starting only) | slot role |
| Name (→ career page) | `player.name` |
| Age | `age` |
| Home Site | `home_site` |
| Height | `height` |
| Games | `total_games` |
| Started | `started_playing_age` |
| MMR `-` / Rank `-` / Potential `-` | proxies (deferred) |
| Overall | `overall_rating` |

Contract / Release / Pick / Ban columns are dropped (finances deferred); per-game
performance lives on Player Stats.

### 7. Free Agents — `/players/free-agents/` · LIVE
Sortable, paginated list of Players on the League's free-agent pool team (on no
competitive roster). Same identity / bio / proxy / attribute column set as **Player
Ratings** (§1). LoL's Asking-For / Mood (contract negotiation) and the per-game K/D/A/CS
columns are dropped — free agents have no Season performance, and finances are deferred.

### 8. Trading Block — DEFERRED
LoL screen lists tradeable assets + incoming offers (Name, Position, Age, Ovr, Pot,
Contract, Min, K, D, A, CS). Blocked on the **finances / trade model** (no contracts,
no cap, no trade machinery). Stays a "coming soon" stub until that lands.

### 9. Trade — DEFERRED
Two-team trade builder. Blocked on the same **finances / trade model** as §8.

### 10. Future Prospects — DEFERRED
Draft-eligible prospects (Draft / Current / Career stat blocks). Blocked on the
**draft / prospect model** (no draft class, no scouting, no prospect generation).

### 11. Hall of Fame — DEFERRED
Inducted retired players (Started / Retired / Peak MMR / Peak Overall / best & career
seasons). Blocked on the **retirement / career-arc model** (no retirement, no
peak-tracking, and depends on the MMR proxy from STAT-PROXY-01).

### 12. Dashboard (Starting Lineup) — League / Season dashboard · LIVE (LG-01c)
LoL team home page (Starting Lineup + Team Leaders + League Leaders + Finances +
Upcoming/Completed Games) maps onto our League / Season dashboards: standings snippet,
next fixture, round count, and three leader snippets. Finances panel is deferred.