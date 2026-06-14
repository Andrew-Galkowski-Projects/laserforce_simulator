# CAR-01 Seam Contract — Manager names their career team at create-League

**Status:** locked + shipped.
**Scope (from the grill):** the "manager" is the implicit single local user — NO
`Manager`/`User` model (both deferred to UX-01), NO new model field, NO migration.
Single-player `League.mode == "league"` IS career mode (NO new mode value). CAR-01 =
the manager names their own team at create-League time; that named team becomes ONE
OF THE N generated teams and is pointed to by the existing `League.current_team` FK.
Per-team scouting budget is DEFERRED. UI surface = the create-League form field ONLY.
No simulator change, no Score Calibration re-baseline, no ADR.

---

## 1. Form field — `matches/forms.py::CreateLeagueForm`

```python
manager_team_name = forms.CharField(
    max_length=100,
    required=False,
    label="Your team name",
    widget=forms.TextInput(
        attrs={
            "id": "league-create-manager-team-name",
            "class": "form-control",
            "autocomplete": "off",
        }
    ),
)
```

- **Position:** AFTER `league_name`, BEFORE `season_name`. Final order:
  `league_name → manager_team_name → season_name → start_date → num_teams →
  schedule_format → mean → std_dev → map_mode → map_pool → phases`.
- NO `clean()` change. NO uniqueness validation (team names are not globally unique).

## 2. View — `matches/league_views.py::league_create` (inside the existing `@transaction.atomic`)

Replaces the LG-01g alphabetical auto-pick line:

```python
manager_name = (cleaned.get("manager_team_name") or "").strip()
if manager_name:
    manager_team = sorted(created_teams, key=lambda t: t.name)[0]
    manager_team.name = manager_name
    manager_team.save(update_fields=["name"])
    league.current_team = manager_team
else:
    league.current_team = sorted(created_teams, key=lambda t: t.name)[0]
league.save(update_fields=["current_team", "free_agent_pool"])
```

- The renamed team is the alphabetical-first generated team → stays one of
  `created_teams`, so league size = `num_teams` (N-1 rivals + 1 manager team).
- Runs at the current `current_team` position — BEFORE `Season.objects.create` /
  `season.teams.add` / the phase loop / `_write_baseline_ratings`. Rename touches
  only `Team.name`, so it is order-independent w.r.t. baseline-ratings/potential.
- Blank path is byte-identical to today's LG-01g behaviour.
- `cleaned` is the view's existing `form.cleaned_data` alias.

## 3. Template — `templates/leagues/create.html`

New field row rendering `{{ form.manager_team_name }}`, between the
`league-create-league-name` row and the `league-create-season-name` row. DOM id
`league-create-manager-team-name`.

## 4. Test boundary — `matches/tests/test_league_create.py::TestCar01ManagerTeamName`

Reuses `_valid_payload(**overrides)`; no `mock.patch` on `_generate_teams`.
- Named: 302; `current_team` is the named team; named team enrolled in Season M2M;
  `season.teams.count() == num_teams`; whitespace stripped.
- Blank: `current_team == sorted(names)[0]` (LG-01g unchanged); count == num_teams.

## 5. NO-CHANGE list

No migration / new model field / `Manager`/`User` model / new `League.mode` value /
scouting budget / dashboard-sidebar change / `_generate_teams` change / simulator
change / re-baseline / ADR / CONTEXT.md change beyond the done **Current team** edit.
