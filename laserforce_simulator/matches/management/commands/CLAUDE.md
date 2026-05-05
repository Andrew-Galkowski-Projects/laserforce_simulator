# matches/management/commands

Django management commands for offline simulation analysis. Run from the `laserforce_simulator/` directory (where `manage.py` lives).

## score_averages.py

Batch-simulates N rounds using `BatchSimulator` (pure in-memory, no DB writes) and prints aggregate stats per role.

```bash
python manage.py score_averages --rounds 50
python manage.py score_averages --rounds 100 --team-red "Team A" --team-blue "Team B" --seed 42
```

Defaults to the first two teams in the DB with active rosters. Use `--seed` for reproducible runs.

### Output sections

1. **Score summary** — avg score vs calibration target, avg tags made, avg times tagged. Colour-coded: green = within 500 of target, yellow = 500–1000 over, red = >1000 over.
2. **Missile breakdown** — avg missile points, % of total score, avg missiles hit per round.
3. **Reset-window tags** — avg times tagged while in the 4–7 s respawn window (informational; helps tune survival stats).
4. **Uptime breakdown** — avg seconds in each state (active / reset-window / dead / not-targetable) and percentages. Useful for diagnosing whether a role is dying too fast or spending too much time in transit.
5. **Follow-up & reaction shots** — avg follow-up shots per round, follow-ups as % of total tags, same for reaction shots. Use this to check whether follow-up eligibility logic is working correctly (e.g. heavy should always show 0.0% follow-up).

### Calibration targets

Real-world average scores used to evaluate simulation accuracy:

| Role | Target |
|------|--------|
| Commander | 9,952 |
| Heavy | 6,482 |
| Scout | 5,102 |
| Ammo | 3,242 |
| Medic | 2,282 |

---

## game_analysis.py

Analyses events from a completed (DB-persisted) `GameRound` and prints per-player uptime breakdowns reconstructed from `player_downed` and resupply events.

```bash
python manage.py game_analysis --round-id <game_round_pk>
```

Useful for verifying that the `ResourceBasedSimulator`'s event log produces uptime numbers consistent with what `BatchSimulator` accumulates directly.
