# teams: players tab

The sortable, paginated Players list (LG-00c). The SPA `PlayerList` screen at `/players` reads `GET /api/players/` (`ordering`, `page`, `page_size`); the ordering keys are documented in [`api.md`](api.md) **Filters + ordering**.
Moved verbatim from `laserforce_simulator/teams/CLAUDE.md`; the app index there points here.

## LG-00c sortable players tab

**Sort whitelist.** `teams.services.PLAYER_SORT_KEYS: dict[str, str]` (URL key -> ORM target; moved from `teams.views._SORT_KEYS` in ANG-08) is the 19 stat fields plus `name` / `team` / `overall_rating` / `potential`. `overall_rating` sorts the `overall_rating_db` annotation from `teams.services.annotate_overall_rating` (`(sum of 19 F-fields) / 19.0`). It feeds `PlayerViewSet.ordering_fields`, `matches.api_forms.PLAYER_RATING_SORT_KEYS` and `matches.league_screen_services`.

**Capital-O `Offensive_synergy` casing quirk.** URL key `offensive_synergy` (lowercase alias) maps to ORM target `Offensive_synergy` (capital-O, matching the existing `Player.Offensive_synergy` field byte-for-byte and the `_STAT_FIELDS` / `STAT_COLUMNS` precedent set by LG-00 / LG-00b).

**`preferred_roles` Python-side sentinel.** `"preferred_roles"` is NOT in `PLAYER_SORT_KEYS` (the field is a JSON list with no ORM target); a caller that accepts it sorts in memory by `(",".join(p.preferred_roles or []), p.name)` (League screens: `matches.league_screen_services`). The `/api/players/` ordering drops it.

The HTML tab's `_coerce_sort` / `_coerce_dir` param coercers went with it in ANG-08: the API validates `ordering` through `AliasOrderingFilter`, and the League screens through their param Forms.
