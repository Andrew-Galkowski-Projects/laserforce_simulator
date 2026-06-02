# Power Rankings

`/l/1/power_rankings` vs. `templates/leagues/power_rankings.html`
(`league_power_rankings`). See [README](README.md) for methodology / C-ids.

**ZenGM columns:** **Overall (rank) · Performance · Talent (based on MMR) · Talent
(based on OVR) · Team · Team League · Games Won · Games Lost · Last Five Games ·
Towers Destroyed Differential**. Compact headers `O / P / T-MMR / T-OVR / … / L5 /
Diff`. Pre-season the **Performance (P)** rank is `-`; once games exist it
**diverges from the talent rank** (observed: eLite5 talent #1 but performance #2).

**Ours:** Rank · Team · **Mean Ovr · Win% · Avg Score Diff · Power score** (power
= sum of three min-max-normalized components). Sortable.

| Discrepancy | Type |
|---|---|
| ZenGM separates **Performance rank** vs. two **Talent ranks** (MMR-based, OVR-based); ours collapses to one **Power score** + raw components | ▲ Layout |
| ZenGM "Talent based on MMR" — we have no MMR; our talent proxy is mean overall | = Intentional (STAT-PROXY-01) |
| ZenGM shows **W / L / Last-5** inline | ⚠ Gap (no form/L5) |
| Our **Avg Score Differential** ≈ ZenGM's **Tower Differential** | ✓ analogue |
| ZenGM has no season selector here; ours now adds a `?season=` selector (each Season + **Career**) for consistency with the other stats screens | C1 ✓ Delivered (LG-06d; extra of ours) |
