# matches: URLs

Every `/matches/` and `/api/` route in the app.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## URLs

```
/matches/                            → match list, create, detail
/matches/create/                     → create a full 2-round match
/matches/single-round/create/        → create a standalone game round (always detailed)
/matches/game-round/<id>/            → detailed round view
/matches/game-round/<id>/events/     → event timeline/filtering
/matches/game-round/<id>/events/data/ → JSON {events, playback_map} fetched by the events page (UXP-06)
/matches/game-round/<id>/missile-log/ → RES-03 missile usage log (URL name "missile_log")
/matches/game-round/<id>/heatmap/    → RES-04 per-round movement heatmap (URL name "movement_heatmap")
/matches/game-round/<id>/export/     → RV-03 round report PDF download (URL name "export_round_report")
/matches/compare/?round_a=&round_b= → RV-01 side-by-side round comparison (URL name "compare_rounds")
/matches/h2h/?team_a=&team_b=         → HX-03 head-to-head record (URL name "head_to_head")
/matches/h2h/player/?player_a=&player_b= → HX-04 player head-to-head record (URL name "player_head_to_head")
/matches/team/<id>/history/          → team win/loss history
/matches/simulate-batch/             → run N in-memory simulations (enqueues simulate_batch_task; URL name "simulate_batch")
/matches/simulate-batch/status/<job_id>/ → batch-simulate job status JSON (URL name "batch_simulate_status")
/matches/save-batch-games/           → enqueue save_games_task for avg/outlier seeds (URL name "save_batch_games")
/matches/save-batch-status/<job_id>/ → save-job status JSON (URL name "save_batch_status")

/api/matches/                        → MatchViewSet (list, create, detail; filters + ordering)
/api/rounds/                         → GameRoundViewSet (list, create, detail, actions; filters + ordering)
/api/rounds/<id>/events/             → paginated, filterable GameEvent list for that round ("gameround-events")
/api/rounds/<id>/missile-log/        → ANG-01b RES-03 missile log ("gameround-missile-log")
/api/rounds/<id>/heatmap/            → ANG-01b RES-04 heatmap data ("gameround-heatmap")
/api/rounds/<id>/report/             → ANG-01b RV-03 round report PDF, raw ("gameround-report")
/api/rounds/compare/?round_a=&round_b= → ANG-01b RV-01 comparison ("gameround-compare")
/api/simulate-batch/                 → API-03 REST POST: enqueue simulate_batch_task (URL name "api_simulate_batch")
/api/simulate-batch/save/<save_job_id>/ → ANG-01b save Job status (URL name "api_save_batch_status")
/api/simulate-batch/<job_id>/save/   → ANG-01b enqueue save_games_task (URL name "api_save_batch_games")
/api/simulate-batch/<job_id>/        → API-03 REST GET: poll job status (URL name "api_simulate_batch_status")
/api/leagues/                        → ANG-01d-1 LeagueViewSet list / create ("league-list")
/api/leagues/templates/              → ANG-01d-1 the 5 League templates ("league-templates")
/api/leagues/<id>/                   → ANG-01d-1 retrieve / DELETE ("league-detail")
/api/leagues/<id>/delete-summary/    → ANG-06-14 GET delete counts ("league-delete-summary")
/api/leagues/<id>/nav/               → ANG-06-14 GET League nav context ("league-nav")
/api/leagues/<id>/dashboard/         → ANG-01d-1 ("league-dashboard")
/api/leagues/<id>/history/           → ANG-01d-1 paginated history rows ("league-history")
/api/leagues/<id>/teams/<team_id>/schedule/ → ANG-01d-1 ("league-team-schedule")
/api/leagues/<id>/next-season/       → ANG-01d-1 POST rollover, started since ANG-06-14 ("league-next-season")
/api/leagues/<id>/new-team-candidates/ → ANG-01d-1 ("league-new-team-candidates")
/api/leagues/<id>/reassign-team/     → ANG-01d-1 POST ("league-reassign-team")
/api/seasons/                        → ANG-01d-1 SeasonViewSet list ("season-list")
/api/seasons/<id>/                   → ANG-01d-1 retrieve ("season-detail")
/api/seasons/<id>/dashboard/         → ANG-01d-1 ("season-dashboard")
/api/seasons/<id>/standings/         → ANG-01d-1 ?sort=&dir= ("season-standings")
/api/seasons/<id>/schedule/          → ANG-01d-1 ("season-schedule")
/api/seasons/<id>/awards/            → ANG-01d-1 ("season-awards")
/api/seasons/<id>/owner-evaluation/  → ANG-01d-1 ("season-owner-evaluation")
/api/seasons/<id>/conferences/       → ANG-01d-1 GET / PUT full replace ("season-conferences")
/api/leagues/<id>/watch-list/        → ANG-01e GET rows / DELETE clear ("league-watch-list")
/api/leagues/<id>/watch-list/<player_id>/ → ANG-01e PUT / DELETE one ("league-watch-player")
/api/leagues/<id>/player-stats/      → ANG-01e ("league-player-stats")
/api/leagues/<id>/player-ratings/    → ANG-01e ("league-player-ratings")
/api/leagues/<id>/free-agents/       → ANG-01e ("league-free-agents")
/api/leagues/<id>/leaders/           → ANG-01e four boards ("league-leaders")
/api/leagues/<id>/players/<player_id>/ → ANG-01e League player page ("league-league-player")
/api/leagues/<id>/teams/<team_id>/roster/ → ANG-01e ("league-team-roster")
/api/leagues/<id>/teams/<team_id>/history/ → ANG-01e Overall + Seasons ("league-team-history")
/api/leagues/<id>/teams/<team_id>/history/players/ → ANG-01e ("league-team-history-players")
/api/leagues/<id>/team-stats/        → ANG-01e ("league-team-stats")
/api/leagues/<id>/power-rankings/    → ANG-01e ("league-power-rankings")
/api/leagues/<id>/game-log/          → ANG-01e ("league-game-log")
/api/leagues/<id>/statistical-feats/ → ANG-01e ("league-statistical-feats")
/api/leagues/<id>/playoffs/          → ANG-01e ("league-playoffs")
/api/leagues/<id>/teams/<team_id>/finances/ → ANG-01e GET / PATCH ("league-team-finances")
/api/leagues/<id>/finances/          → ANG-01e ("league-finances")
/api/tournaments/                    → ANG-01f TournamentViewSet list / create ("tournament-list")
/api/tournaments/<id>/               → ANG-01f detail payload; PUT / PATCH / DELETE 405 ("tournament-detail")
/api/tournaments/<id>/seeds/         → ANG-01f PUT full {team_id: seed} map ("tournament-seeds")
/api/tournaments/<id>/lock/          → ANG-01f POST setup -> active ("tournament-lock")
/api/tournaments/<id>/import-participants/ → ANG-01f POST multipart roster CSV ("tournament-import-participants")
/api/tournaments/<id>/pool/          → ANG-01f GET paginated pool entries ("tournament-pool")
/api/tournaments/<id>/pool/players/  → ANG-01f POST {player_ids} ("tournament-pool-players")
/api/tournaments/<id>/pool/players/<player_id>/ → ANG-01f DELETE one, 204 ("tournament-pool-player")
/api/tournaments/<id>/pool/generate/ → ANG-01f POST {count, mean, std_dev} ("tournament-pool-generate")
/api/tournaments/<id>/pool/import/   → ANG-01f POST multipart roster CSV ("tournament-pool-import")
/api/tournaments/<id>/draw/          → ANG-01f POST run / re-roll the Draw ("tournament-draw")
/api/tournaments/<id>/draw/entries/<player_id>/ → ANG-01f PATCH {tier, drawn_team} ("tournament-draw-entry")
/api/tournaments/<id>/play-next/     → ANG-01f POST sync play ("tournament-play-next")
/api/tournaments/<id>/play-all/      → ANG-01f POST enqueue play-all Job ("tournament-play-all")
/api/tournaments/<id>/play-jobs/<job_id>/ → ANG-01f GET Job status ("tournament-play-job")
```

The sandbox HTML Tournament routes (`/tournaments/…`, `matches/tournament_urls.py`, bare URL names)
are listed in `tournaments-elimination.md` / `tournaments-rr-swiss-draw.md`; ANG-01f repointed them
at the `matches/tournament_views/` package with names unchanged.

The `simulate-batch/` extra paths in `api_urls.py` are ordered as above: the save-status path sits
before `<job_id>/save/` because `simulate-batch/save/save/` matches both patterns.
