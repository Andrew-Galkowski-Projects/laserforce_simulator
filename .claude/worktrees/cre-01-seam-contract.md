# CRE-01 seam contract — League templates chooser + Advanced screen + Difficulty

Single source of truth for the three parallel agents (code / tests / docs). Every
name below is locked. CRE-01 turns the single create-League form into a **template
chooser** (the new default at `create/`), relocates today's full form **verbatim** to
an **Advanced** screen at `create/advanced/`, and adds a transient **Difficulty**
dropdown shared by both. Creation logic is extracted into ONE shared helper so there
is no duplicated/new creation path.

Research anchors (verified, June 2026):
- `matches/league_views.py::league_create` — full body lives ~L969–1110, inside one
  `@transaction.atomic`. The CAR-01 manager pick is `sorted(created_teams, key=lambda
  t: t.name)[0]` at **L1021**, rename-if-`manager_team_name` (L1022–1025), then
  `league.current_team = manager_team` (L1026). CRE-01 **replaces** this alphabetical
  pick with the difficulty pick.
- `matches/league_views.py::_compute_team_overall(team) -> float` (**L120**) — mean
  `overall_rating` over a team's `active_players` (0.0 when none). `_seed_team_budgets_by_strength`
  (**L668**) ranks via `sorted(teams, key=lambda t: (-_compute_team_overall(t), t.id))`
  (**L684**). **This is the exact ranking the difficulty pick reuses** (FIN-03 cites it).
- `matches/forms.py::CreateLeagueForm` — 12 declared fields + `clean()`; fields today:
  `league_name`, `manager_team_name`, `season_name`, `start_date`, `num_teams`,
  `schedule_format` (disabled), `mean`, `std_dev`, `map_mode`, `map_pool`,
  `map_rotation` (hidden), `finance_enabled`, `challenge_fired_luxury_tax`, `phases`
  (hidden). `clean()` parses `phases` via `phase_composer.parse_phase_composition(...)`
  and stashes `cleaned_data["phase_specs"]`.
- `matches/league_urls.py` urlpatterns — first entry `path("create/",
  league_views.league_create, name="league_create")`, then `<int:league_id>/`.
- `matches/phase_composer.py` — confirmed: `member_night` is a **bare token**
  (`type_part == "member_night"`, no sub-config; a colon ⇒ malformed) and
  `round_robin:double_round_robin` parses (`double_round_robin ∈
  _VALID_SCHEDULE_FORMATS`). All 5 template wire strings parse (see §2).
- `templates/leagues/list.html` L8 — `<a id="league-create-link" ...
  href="/leagues/create/">` is a **raw string**, not a `{% url %}`. **No nav edit.**

---

## 1. Routing — `matches/league_urls.py`

```python
urlpatterns = [
    path("create/", league_views.league_create, name="league_create"),                       # NOW the chooser
    path("create/advanced/", league_views.league_create_advanced, name="league_create_advanced"),  # NEW — the relocated full form
    path("<int:league_id>/", league_views.league_dashboard, name="league_dashboard"),
    ...
]
```

- `league_create` URL name is **unchanged** but now points at the **chooser** view.
- `league_create_advanced` is **new**, at literal path `create/advanced/`. Insert it
  **immediately after** `create/` and **before** `<int:league_id>/` (both `create/*`
  paths are literal and non-overlapping, so order between the two `create*` lines is
  irrelevant; both MUST precede `<int:league_id>/`). Final order:
  `[create/, create/advanced/, <int:league_id>/, ...]`.
- `reverse("league_create") == "/leagues/create/"`;
  `reverse("league_create_advanced") == "/leagues/create/advanced/"`.
- Nav `league-create-link` (raw `/leagues/create/`) needs **no change**.

---

## 2. `LEAGUE_TEMPLATES` — NEW module `matches/league_templates.py`

A server-side constants bundle. NOT user-savable, NOT persisted, NO `League`
back-reference. Resolves to `CreateLeagueForm` field values so creation reuses the
existing path verbatim.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class LeagueTemplate:
    key: str
    label: str
    num_teams: int
    phases: str                          # phase_composer wire string
    finance_enabled: bool = False
    challenge_fired_luxury_tax: bool = False
    mean: int = 50
    std_dev: int = 15
    map_mode: str = "none"               # 3-zone fallback

LEAGUE_TEMPLATES: tuple[LeagueTemplate, ...] = (
    LeagueTemplate("4_team_quick",         "4-Team Quick",         4, "round_robin,tournament"),
    LeagueTemplate("8_team_classic",       "8-Team Classic",       8, "round_robin,tournament"),
    LeagueTemplate("8_team_career",        "8-Team Career",        8, "round_robin,tournament", finance_enabled=True),
    LeagueTemplate("8_team_double_rr",     "8-Team Double-RR",     8, "round_robin:double_round_robin,tournament"),
    LeagueTemplate("8_team_member_nights", "8-Team Member Nights", 8, "round_robin,member_night,tournament"),
)
LEAGUE_TEMPLATES_BY_KEY: dict[str, LeagueTemplate] = {t.key: t for t in LEAGUE_TEMPLATES}
```

The 5 wire strings are pinned against the real grammar:
- `"round_robin,tournament"` → RR phase + season-ending standings tournament (bare
  `tournament` ⇒ mode `standings`; preceding-RR guard satisfied). ✓ (rows 1/2/3)
- `"round_robin:double_round_robin,tournament"` → RR phase with `double_round_robin`
  schedule format + standings tournament. ✓ (row 4)
- `"round_robin,member_night,tournament"` → RR + bare `member_night` + standings
  tournament; `member_night` parses as a bare token, may sit anywhere, and the RR
  precedes the standings tournament. ✓ (row 5)

The chooser GET iterates `LEAGUE_TEMPLATES` (preserving order) to render `<option>`s
(`value=key`, text=`label`). The chooser POST resolves `LEAGUE_TEMPLATES_BY_KEY[key]`
(unknown/missing key ⇒ re-render chooser with an error, no creation).

---

## 3. `CreateLeagueForm.difficulty` — `matches/forms.py`

A **real, shared** field on `CreateLeagueForm` (consumed by BOTH the chooser-assembled
form and the Advanced form). **Transient** — consumed at create time only. NO
`League.difficulty` field, NO migration.

```python
DIFFICULTY_CHOICES = (("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard"))

difficulty = forms.ChoiceField(
    choices=DIFFICULTY_CHOICES,
    initial="medium",
    required=False,                       # absent/blank ⇒ coerced to "medium" at pick time
    widget=forms.Select(attrs={"id": "league-create-difficulty", "class": "form-select"}),
    label="Difficulty",
)
```

- **Position:** insert immediately **after `manager_team_name`** and **before
  `season_name`**. Field order becomes: `league_name`, `manager_team_name`,
  `difficulty`, `season_name`, `start_date`, `num_teams`, `schedule_format`, `mean`,
  `std_dev`, `map_mode`, `map_pool`, `map_rotation`, `finance_enabled`,
  `challenge_fired_luxury_tax`, `phases`.
- **`required=False` (locked):** existing Advanced POSTs that omit `difficulty` stay
  valid (default Medium), so migrating the existing full-field-set tests to
  `league_create_advanced` needs no `difficulty` key. The pick helper coerces a falsy
  difficulty to `"medium"`.
- **DOM id:** `league-create-difficulty`.
- **No `clean()` change** beyond the field declaration; `clean()`'s phase parsing is
  untouched.

---

## 4. Shared creation helper + ranking/pick helpers — `matches/league_views.py`

### 4a. Extracted creation helper (the ONLY creation path)

```python
@transaction.atomic
def _create_league_and_season(form: CreateLeagueForm) -> Season:
    """CRE-01 — the single League+Season creation body, shared by the chooser
    POST and the Advanced POST. Generates teams, creates League + draft Season,
    enrols teams, seeds the free-agent pool, materialises map_pool, runs the
    SeasonPhase create loop over cleaned_data["phase_specs"], writes baseline
    ratings, seeds FIN budgets — preserving today's order. The manager-team
    pick + rename live here (difficulty pick supersedes CAR-01 alphabetical).
    Returns the created draft Season; callers redirect to season_standings.
    """
```

- Body = today's `league_create` body (L988–1108) **verbatim**, with ONE change: the
  CAR-01 manager pick at L1021–1026 is replaced by the difficulty pick (§4c).
- `@transaction.atomic` lives on the helper (so each view stays free of decorator
  duplication; today's `@transaction.atomic` on `league_create` moves here).
- Step order preserved: `_generate_teams` → `League.objects.create` → **manager pick +
  rename + `current_team`** → free-agent `pool_team` + `_generate_free_agents` →
  `Season.objects.create` → `season.teams.add(*created_teams)` → `season.map_pool.set`
  → `SeasonPhase` create loop over `form.cleaned_data["phase_specs"]` →
  `_write_baseline_ratings` → `_seed_team_budgets_by_strength` (finance ON only).

### 4b. Ranking helper (extracted, shared)

```python
def _rank_teams_by_strength(teams: "Iterable[Team]") -> list[Team]:
    """Teams sorted strongest→weakest: mean active-roster overall_rating DESC,
    team_id ASC tiebreak. The same ranking _seed_team_budgets_by_strength uses."""
    return sorted(teams, key=lambda t: (-_compute_team_overall(t), t.id))
```

- `_seed_team_budgets_by_strength` (L668) is refactored to call
  `_rank_teams_by_strength(teams)` instead of inlining the `sorted(...)` (one ranking,
  one place). Behaviour unchanged.

### 4c. Difficulty manager pick (supersedes CAR-01)

```python
def _pick_manager_team(created_teams: list[Team], difficulty: str) -> Team:
    """CRE-01 — pick the manager's current_team by difficulty. Rank teams by
    strength (_rank_teams_by_strength), then index:
        easy → 0 (strongest), medium → N//2, hard → N-1 (weakest).
    A falsy/unknown difficulty defaults to "medium"."""
    ranked = _rank_teams_by_strength(created_teams)
    n = len(ranked)
    idx = {"easy": 0, "medium": n // 2, "hard": n - 1}.get(difficulty or "medium", n // 2)
    return ranked[idx]
```

- Inside `_create_league_and_season`, replacing L1021–1026:
  ```python
  manager_team = _pick_manager_team(created_teams, cleaned.get("difficulty") or "medium")
  manager_name = (cleaned.get("manager_team_name") or "").strip()
  if manager_name:
      manager_team.name = manager_name
      manager_team.save(update_fields=["name"])
  league.current_team = manager_team
  league.save(update_fields=["current_team", "free_agent_pool"])
  ```
- **Difficulty picks, name renames — they compose.** Difficulty selects WHICH of the
  N teams is the manager's; `manager_team_name` (if non-blank) renames that chosen team.
- **The CAR-01 alphabetical-first auto-pick is removed.** No `sorted(..., key=...name)`
  remains.

### 4d. Chooser → form-data assembly

```python
def _template_to_form_data(template: LeagueTemplate, *, league_name: str, difficulty: str) -> dict:
    """Merge a template row ∪ league_name ∪ difficulty ∪ static defaults into a
    dict CreateLeagueForm(data=...) accepts (so the chooser reuses the Advanced
    validation + phase_composer parse verbatim)."""
    return {
        "league_name": league_name,
        "manager_team_name": "",
        "difficulty": difficulty,
        "season_name": "Season 1",
        "start_date": timezone.localdate().isoformat(),
        "num_teams": str(template.num_teams),
        "schedule_format": "single_round_robin",
        "mean": str(template.mean),
        "std_dev": str(template.std_dev),
        "map_mode": template.map_mode,          # "none"
        "map_rotation": "",
        "phases": template.phases,
        "finance_enabled": template.finance_enabled,
        "challenge_fired_luxury_tax": template.challenge_fired_luxury_tax,
    }
```
- `map_pool` is omitted (empty multi-select). `schedule_format` is disabled (Django
  serves its initial regardless). Boolean keys pass python `True`/`False`
  (`CheckboxInput.value_from_datadict` accepts bools).

### 4e. The two views

```python
def league_create(request) -> HttpResponse:
    """CRE-01 chooser (the new default at create/). GET renders the template
    chooser. POST resolves the chosen LeagueTemplate, builds CreateLeagueForm(
    data=_template_to_form_data(...)), validates, and on valid calls
    _create_league_and_season(form) → redirect season_standings; invalid (or
    bad template key) ⇒ re-render the chooser."""

def league_create_advanced(request) -> HttpResponse:
    """CRE-01 Advanced — today's full-form league_create VERBATIM (form +
    template + flow), only the action/back-link/heading adjusted. GET renders
    the full form; POST validates CreateLeagueForm(request.POST) and on valid
    calls _create_league_and_season(form) → redirect season_standings."""
```
- Both views, on valid, do `season = _create_league_and_season(form); return
  redirect("season_standings", season_id=season.id)`.
- `_create_league_and_season` is the **only** writer; neither view re-implements
  creation.

---

## 5. Templates

| File | Status | Rendered by | Notes |
|---|---|---|---|
| `templates/leagues/create.html` | **NEW (chooser)** | `league_create` | Template `<select>` + league name + difficulty + submit + link to Advanced. Replaces the old file's content. |
| `templates/leagues/create_advanced.html` | **RELOCATED (old `create.html` verbatim)** | `league_create_advanced` | The current full form, moved here unchanged except: form `action="{% url 'league_create_advanced' %}"`, a back-link to the chooser, heading, and the NEW `{{ form.difficulty }}` row. |

Old → new path: the existing full-form content at `templates/leagues/create.html`
moves to `templates/leagues/create_advanced.html`; `create.html` becomes the new
chooser.

### DOM ids

**Chooser `create.html`:**
- `league-create-template` — the template `<select>` (`name="template"`, options
  keyed on `LeagueTemplate.key`, text=`label`).
- `league-create-league-name` — the league-name `<input>`.
- `league-create-difficulty` — the difficulty `<select>` (rendered via
  `{{ form.difficulty }}`; widget id already pinned in §3).
- `league-create-submit` — the chooser submit button.
- `league-create-advanced-link` — `<a href="{% url 'league_create_advanced' %}">`
  ("Advanced…").

**Advanced `create_advanced.html`** (all existing ids preserved verbatim — separate
page, no real collision with the chooser):
- `league-create-form`, `league-create-league-name`, `league-create-manager-team-name`,
  `league-create-season-name`, `league-create-start-date`, `league-create-num-teams`,
  `league-create-schedule-format`, `league-create-mean`, `league-create-std-dev`,
  `league-create-phases-composer`, `league-create-add-block`,
  `league-create-member-night-note`, `league-create-map-mode`,
  `league-create-map-pool`, `league-create-finance-enabled`,
  `league-create-challenge-luxury-tax`, `league-create-submit`.
- **NEW** `league-create-difficulty` — `{{ form.difficulty }}` row (shared field).
- **NEW** `league-create-use-template-link` — `<a href="{% url 'league_create' %}">`
  back to the chooser.

(`league-create-league-name` / `-difficulty` / `-submit` appear on both pages by
design — tests fetch one page at a time.)

---

## 6. Manager-pick rule (summary)

- Ranking source: `_rank_teams_by_strength` (mean active-roster `overall_rating` DESC,
  `team_id` ASC) — the **same** ranking `_seed_team_budgets_by_strength` (FIN-03) uses,
  via `_compute_team_overall`.
- Index map: `{easy: 0, medium: N//2, hard: N-1}` over the ranked (strongest-first)
  list, `N = num_teams`.
- Composition: difficulty PICKS the team; non-blank `manager_team_name` RENAMES the
  picked team; both then set `league.current_team`.
- This **supersedes** CAR-01's `sorted(created_teams, key=name)[0]`.

---

## 7. Test boundary

**Assert against (public surface):**
- **Templates table → valid form data:** every `LEAGUE_TEMPLATES` row, fed through
  `_template_to_form_data(... )`, produces a `CreateLeagueForm` that `is_valid()`; each
  row's `phases` parses without error (5 distinct compositions).
- **Each template end-to-end:** a chooser POST per template key creates the League +
  draft Season with the right `num_teams`, the right number of `SeasonPhase` rows
  (phase shape matches `phases`), `finance_enabled` honored (row 3 ON; others OFF),
  and `_8_team_member_nights` yields a `member_night` phase. **No `_generate_teams`
  mock** — exercise the real generator so signature drift surfaces.
- **Difficulty pick:** with deterministic injected per-team stats (so strength order is
  known), `difficulty="easy"` ⇒ `current_team` is the strongest, `medium` ⇒ the
  `N//2`-th, `hard` ⇒ the weakest (by `_rank_teams_by_strength` order). Verify on both
  the chooser path and the Advanced path.
- **Advanced relocation:** `league_create_advanced` GET → 200 + renders the full-form
  ids; POST of the full field set creates League/Season exactly as the pre-CRE-01
  `league_create` did (byte-equivalent League/Season/phase/team shape).
- **Difficulty + rename compose:** non-blank `manager_team_name` renames the
  difficulty-picked team; `current_team.name` == the chosen name AND the team is the
  difficulty-ranked one.
- **`TestCar01ManagerTeamName` update:** the two blank-name fallback tests
  (`test_blank_name_falls_back_to_alphabetical_first`,
  `test_empty_string_name_falls_back_to_alphabetical_first`) **break** under
  supersession — blank name now yields the difficulty-picked team (default Medium =
  rank `N//2` by strength), NOT the alphabetical-first team. Rewrite them to assert the
  Medium-difficulty strength pick. Named-team tests
  (`test_current_team_is_the_named_team`, `test_named_team_is_one_of_the_n_not_an_extra`,
  `test_whitespace_name_is_stripped`, `test_named_team_name_is_stored`) still pass
  (rename applies to whichever team difficulty picked). Migrate any
  `reverse("league_create")` full-field-set POST in the existing suite that exercises
  the full form to `reverse("league_create_advanced")` (the chooser POST no longer
  accepts the raw full field set); `_valid_payload` may add `difficulty` for the new
  tests but does **not** need it for the migrated Advanced POSTs (field is
  `required=False`, defaults Medium).

**Stays internal (not a test target):** `_template_to_form_data`'s exact dict shape,
`_create_league_and_season`'s internal step ordering, the `_rank_teams_by_strength`
extraction mechanics, the `_pick_manager_team` index arithmetic helper.

---

## 8. Scope-out (explicit)

- **No model change, no migration.** `difficulty` is transient (consumed at create);
  there is NO `League.difficulty` field. The created League carries **no template
  back-reference** and **no difficulty back-reference**.
- **No simulator touch, no RNG-into-the-sim change, no Score Calibration re-baseline.**
- **No Score Calibration** interaction of any kind.
- **No nav edit** (`league-create-link` raw `/leagues/create/` keeps hitting the
  chooser).
- Templates are **not** persisted, **not** user-savable.
- The Advanced form + template + flow are relocated **verbatim** (only
  action/back-link/heading/difficulty-row adjusted).

---

## 9. Locked names index

| Kind | Name | Location |
|---|---|---|
| URL name (chooser) | `league_create` → `create/` | `matches/league_urls.py` |
| URL name (advanced) | `league_create_advanced` → `create/advanced/` | `matches/league_urls.py` |
| View | `league_create(request) -> HttpResponse` (chooser) | `matches/league_views.py` |
| View | `league_create_advanced(request) -> HttpResponse` | `matches/league_views.py` |
| Helper | `_create_league_and_season(form: CreateLeagueForm) -> Season` (`@transaction.atomic`) | `matches/league_views.py` |
| Helper | `_rank_teams_by_strength(teams) -> list[Team]` | `matches/league_views.py` |
| Helper | `_pick_manager_team(created_teams, difficulty) -> Team` | `matches/league_views.py` |
| Helper | `_template_to_form_data(template, *, league_name, difficulty) -> dict` | `matches/league_views.py` |
| Module | `matches/league_templates.py` | NEW |
| Dataclass | `LeagueTemplate(key, label, num_teams, phases, finance_enabled=False, challenge_fired_luxury_tax=False, mean=50, std_dev=15, map_mode="none")` | `matches/league_templates.py` |
| Table | `LEAGUE_TEMPLATES` (5 rows) + `LEAGUE_TEMPLATES_BY_KEY` | `matches/league_templates.py` |
| Form field | `CreateLeagueForm.difficulty` (`ChoiceField`, `DIFFICULTY_CHOICES`, `initial="medium"`, `required=False`, widget id `league-create-difficulty`) | `matches/forms.py` |
| Const | `DIFFICULTY_CHOICES = (("easy","Easy"),("medium","Medium"),("hard","Hard"))` | `matches/forms.py` |
| Pick map | `{easy:0, medium:N//2, hard:N-1}` | `_pick_manager_team` |
| Template (new chooser) | `templates/leagues/create.html` | DOM: `league-create-template` / `-league-name` / `-difficulty` / `-submit` / `-advanced-link` |
| Template (relocated) | `templates/leagues/create_advanced.html` | all existing create.html ids + `league-create-difficulty` + `league-create-use-template-link` |
