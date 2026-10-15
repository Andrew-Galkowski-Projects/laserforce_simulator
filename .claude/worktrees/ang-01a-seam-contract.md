# ANG-01a — Seam contract: REST API foundation (envelope, filters, Form-bound writes)

Binding for the code, tests and docs agents. Paths are relative to the Django root
`laserforce_simulator/` (where `manage.py` / `pytest.ini` live) unless they start with `docs/`.
Governing ADRs: **ADR-0039** (envelope + Form-bound writes + django-filter), **ADR-0038**
(ownership, 404 never 403, ArenaMap is shared reference data), **ADR-0004** (no data migration).

Hard limits: **no migration, no simulation change, no score re-baseline, no team DELETE.**
Serializers are **output-only**. Every API write binds an existing Django `Form`.

---

## 0. File ownership (every new / changed module)

| File | Status | Owns |
|---|---|---|
| `laserforce_simulator/api/__init__.py` | NEW (empty) | package marker |
| `laserforce_simulator/api/renderers.py` | NEW | `EnvelopeJSONRenderer` |
| `laserforce_simulator/api/pagination.py` | NEW | `EnvelopePagination` |
| `laserforce_simulator/api/exceptions.py` | NEW | `ErrorEntry`, `EnvelopeValidationError`, `envelope_exception_handler`, `errors_from_detail`, `form_errors_to_envelope`, `django_validation_error_to_envelope`, `validate_form_or_raise` |
| `laserforce_simulator/api/forms.py` | NEW | `plain_form_data`, `merged_form_data` |
| `laserforce_simulator/api/filters.py` | NEW | `AliasOrderingFilter` |
| `laserforce_simulator/api/viewsets.py` | NEW | `ListOnlyFilterMixin` |
| `laserforce_simulator/api/tests/__init__.py` + tests | NEW | project-level envelope tests (§9) |
| `laserforce_simulator/settings.py` | CHANGED | `INSTALLED_APPS`, `REST_FRAMEWORK` (§1) |
| `laserforce_simulator/api_urls.py` | CHANGED | registers `maps` (§3) |
| `requirements.txt` (Django root) | CHANGED | `django-filter>=25.1` |
| `teams/services.py` | NEW | generation + roster-import services, `player_form_initial`, `annotate_overall_rating`, `build_roster_template_csv`, moved helpers (§6) |
| `teams/views.py` | CHANGED | HTML views call services; moved helpers **re-exported** (§6.4) |
| `teams/filters.py` | NEW | `TeamFilterSet`, `PlayerFilterSet` |
| `teams/api_views.py` | CHANGED | `TeamViewSet`, `PlayerViewSet` (writes), `roster_import_errors_to_envelope` |
| `teams/serializers.py` | UNCHANGED | (reused as-is) |
| `matches/filters.py` | NEW | `MatchFilterSet`, `GameRoundFilterSet`, `GameEventFilterSet` |
| `matches/api_views.py` | CHANGED | filters/ordering; batch views raise instead of returning 400 `Response`s |
| `matches/tournament_views.py` | CHANGED (2 call sites only) | pass `manager=` to `_check_db_slot_collisions` / `_apply_roster` (§6.3) |
| `core/api_views.py` | NEW | `ArenaMapViewSet` |
| `core/serializers.py` | NEW | `ArenaMapSerializer` |
| `core/filters.py` | NEW | `ArenaMapFilterSet` |

The importable path of the new package is `laserforce_simulator.api.<module>` (same root as
`laserforce_simulator.settings`).

---

## 1. Settings

```python
INSTALLED_APPS = [..., "rest_framework", "django_filters"]   # appended after rest_framework

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],  # unchanged
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],              # unchanged
    "DEFAULT_RENDERER_CLASSES": [
        "laserforce_simulator.api.renderers.EnvelopeJSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "laserforce_simulator.api.pagination.EnvelopePagination",
    "PAGE_SIZE": 20,                                                                            # unchanged
    "EXCEPTION_HANDLER": "laserforce_simulator.api.exceptions.envelope_exception_handler",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "laserforce_simulator.api.filters.AliasOrderingFilter",
    ],
}
```

`requirements.txt`: add `django-filter>=25.1` (first release supporting Django 5.2).
`ORDERING_PARAM` stays DRF default `ordering`.

---

## 2. The envelope (locked wire format)

### 2.1 Shapes

| Case | Status | Body |
|---|---|---|
| Success, object | 200 / 201 | `{"data": {...}, "meta": {}}` |
| Success, non-paginated list / composite object | 200 / 201 | `{"data": <list or obj>, "meta": {}}` |
| Success, paginated list | 200 | `{"data": [...], "meta": {"page": int, "page_size": int, "count": int, "total_pages": int}}` |
| Error (any 4xx raised through DRF) | 4xx | `{"errors": [{"code": str, "message": str, "field": str \| null}, ...], "meta": {}}` |
| **204 No Content** (player DELETE) | 204 | **empty body** (`b""`), no envelope, no `Content-Type` requirement — EXEMPT |
| **CSV template** `GET /api/teams/import/template/` | 200 | raw `text/csv` — EXEMPT (Django `HttpResponse`, never rendered) |
| Unhandled exception | 500 | NOT enveloped (Django default) |

Key order in error entries is `code, message, field`. `errors` is never empty on an error response.

### 2.2 `meta` on paginated lists

`page` = `page.number` (1-based); `page_size` = the effective per-page size actually used
(after default/clamp); `count` = `paginator.count`; `total_pages` = `paginator.num_pages`
(Django semantics: an **empty** result is `count: 0, total_pages: 1, page: 1`).

### 2.3 Pagination rules (`EnvelopePagination`)

```python
class EnvelopePagination(PageNumberPagination):
    page_size = 20                    # mirrors PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = 100
    def get_paginated_response(self, data: list) -> Response: ...
    def get_paginated_response_schema(self, schema: dict) -> dict: ...  # optional, data/meta schema
```

- `?page=N` beyond range, `?page=0`, `?page=abc` → **404** envelope, `code: "not_found"` (DRF
  default "Invalid page."). `?page=last` stays DRF-legal (last page).
- `?page_size>100` → clamped to 100 (no error). `?page_size` non-integer / `<=0` → default 20 (no error).
  (Both are DRF `PageNumberPagination` defaults — do not add custom validation.)
- `get_paginated_response` returns `Response(data)` (the bare page list) and sets the attribute
  `response.envelope_meta = {"page", "page_size", "count", "total_pages"}`. The renderer wraps it.

### 2.4 `EnvelopeJSONRenderer(JSONRenderer)`

```python
class EnvelopeJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type: str | None = None, renderer_context: dict | None = None) -> bytes: ...
```

Rules, in order, using `response = renderer_context["response"]`:
1. `response.status_code == 204` → return `b""`.
2. `status_code >= 400`: if `data` is already a dict whose keys are exactly `{"errors", "meta"}` → render
   as-is; otherwise (defensive: a hand-built error `Response`) render
   `{"errors": errors_from_detail(data), "meta": {}}`. New/changed code MUST raise, never hand-build.
3. Otherwise → render `{"data": data, "meta": getattr(response, "envelope_meta", {})}`.

Success bodies are **always** wrapped (no "already enveloped" sniffing for 2xx).
`BrowsableAPIRenderer` uses this renderer as its default, so the browsable page shows the envelope.

### 2.5 Exception handler

```python
class ErrorEntry(TypedDict):
    code: str
    message: str
    field: str | None

class EnvelopeValidationError(APIException):
    status_code = 400
    default_code = "invalid"
    default_detail = "Invalid input."
    def __init__(self, errors: list[ErrorEntry]) -> None: ...   # stores self.errors verbatim

def envelope_exception_handler(exc: Exception, context: dict) -> Response | None: ...
def errors_from_detail(detail: object, *, default_code: str = "invalid") -> list[ErrorEntry]: ...
```

`envelope_exception_handler`:
1. `response = rest_framework.views.exception_handler(exc, context)`; if `None` → return `None` (500 path).
   (This is what turns `Http404` → `NotFound` and Django `PermissionDenied` → `PermissionDenied`.)
2. If `isinstance(exc, EnvelopeValidationError)` → `errors = exc.errors`.
   Else if `isinstance(exc, ValidationError)` (DRF) → `errors = errors_from_detail(exc.detail)`.
   Else → `[{"code": exc.detail.code or exc.default_code, "message": str(exc.detail), "field": None}]`
   (for an `APIException` whose `detail` is a dict/list, use `errors_from_detail`).
3. `response.data = {"errors": errors, "meta": {}}`; keep `response.status_code` and every header DRF set
   (`Allow`, `WWW-Authenticate`, `Retry-After`). Return it.

`errors_from_detail` flattening (DRF `ErrorDetail` trees):
- `str`/`ErrorDetail` → one entry, `field: None`, `code = detail.code or default_code`.
- `list` → one entry per item, `field: None` (recursing for nested).
- `dict` → per key, one entry **per message**; `field = None` when key is `"non_field_errors"` or
  `"__all__"`, else the key. Nested dicts join with `"."` (`"a.b"`); list indices inside a field join as
  `"a.0"` (only reachable via serializer input, i.e. the batch POST).

### 2.6 Status / code mapping (locked)

| Situation | Status | `code` | `field` |
|---|---|---|---|
| Form / serializer validation | 400 | the Django/DRF error code (`required`, `invalid`, `invalid_choice`, `max_value`, `min_value`, `max_length`, `null`, `blank`, …); a Django error with `code=None` → `"invalid"` | field name, or `null` for non-field |
| Invalid filter value (typed filters, §4) | 400 | django-filter / Django form code (`invalid`, `invalid_choice`) | the query-param name |
| Malformed JSON | 400 | `parse_error` | null |
| Roster import parse / collision error | 400 | `roster_invalid` | `row:<n>` or `row:<n>:<column>` (§5.9) |
| Missing row **and cross-Account row** | 404 | `not_found` | null |
| Invalid page | 404 | `not_found` | null |
| Anonymous (SessionAuthentication) | **403** | `not_authenticated` | null |
| CSRF failure on a session write | 403 | `permission_denied` | null |
| Method not routed | 405 | `method_not_allowed` | null |
| Wrong content type (e.g. JSON to import) | 415 | `unsupported_media_type` | null |

Never 403 for cross-Account access (ADR-0038). Never 401 (no JWT until ANG-03).

---

## 3. Shared API helpers

### 3.1 Form-error conversion — `laserforce_simulator/api/exceptions.py`

```python
def form_errors_to_envelope(form: django.forms.BaseForm) -> list[ErrorEntry]: ...
def django_validation_error_to_envelope(exc: django.core.exceptions.ValidationError) -> list[ErrorEntry]: ...
def validate_form_or_raise(form: django.forms.BaseForm) -> None: ...
```

- `form_errors_to_envelope`: iterate `form.errors.as_data().items()` in order; for each
  `ValidationError e` and each `msg in e.messages` → `{"code": e.code or "invalid", "message": msg,
  "field": None if field == NON_FIELD_ERRORS else field}`. Messages are the same rendered strings
  `form.errors` shows (so HTML and API messages are byte-identical).
- `django_validation_error_to_envelope` (for `Model.full_clean()` failures): `error_dict` → per field
  as above (`NON_FIELD_ERRORS` → `null`); `error_list` → `field: null`.
- `validate_form_or_raise(form)`: `if not form.is_valid(): raise EnvelopeValidationError(form_errors_to_envelope(form))`.

**How views raise:** always `validate_form_or_raise(form)` (or
`raise EnvelopeValidationError(...)` for full_clean / roster errors). Views never build a 4xx `Response`.

### 3.2 Request-data helpers — `laserforce_simulator/api/forms.py`

```python
def plain_form_data(form_class: type[BaseForm], data: Mapping) -> dict[str, object]: ...
def merged_form_data(form_class: type[ModelForm], instance: Model, data: Mapping) -> dict[str, object]: ...
```

- `plain_form_data`: normalises `request.data` (JSON `dict` or `QueryDict`) to a plain `dict` restricted to
  keys present in `form_class.base_fields`. For a `QueryDict`: a `MultipleChoiceField` /
  `ModelMultipleChoiceField` key keeps the full list (`getlist`), every other key takes the last value.
  Unknown keys are dropped silently (not an error).
- `merged_form_data` (**PATCH semantics**): `{**model_to_dict(instance, fields=list(form_class.base_fields)),
  **plain_form_data(form_class, data)}`. FKs come out as pks, `preferred_roles` as a list.

Forms accept a plain dict: `CheckboxSelectMultiple` falls back to `data.get` (a list), `Select` with
`None` → empty → cleared slot.

### 3.3 Ordering — `laserforce_simulator/api/filters.py`

```python
class AliasOrderingFilter(OrderingFilter):
    def get_ordering(self, request: Request, queryset: QuerySet, view: APIView) -> list[str] | tuple[str, ...] | None: ...
```

- Public ordering terms are validated by DRF against `view.ordering_fields` (public names).
  **Invalid / unknown `?ordering=` terms are silently dropped (DRF default) — not a 400.**
  If nothing valid remains, `view.ordering` (ORM names) applies.
- Each requested term is then mapped through `getattr(view, "ordering_aliases", {})`
  (public → ORM expression), preserving a leading `-`.
- Then every name in `getattr(view, "ordering_tiebreak", ())` not already present is appended
  (stable pagination). `?ordering=a,-b` (comma list) is supported (DRF default).

### 3.4 `ListOnlyFilterMixin` — `laserforce_simulator/api/viewsets.py`

```python
class ListOnlyFilterMixin:
    def filter_queryset(self, queryset: QuerySet) -> QuerySet: ...
```

Applies the filter backends **only when `self.action == "list"`**; for every other action returns
`queryset` unchanged. Consequence: detail GET / PATCH / PUT / DELETE / extra actions resolve
`get_object()` against the owned queryset only (query params never 404 or 400 a detail lookup), and the
Free Agents team is reachable by id. Every viewset in this slice mixes it in first.

---

## 4. FilterSets (django-filter, explicit allowlists)

All use `django_filters.rest_framework.FilterSet`. Unknown query params are ignored.
**400 is guaranteed for typed filters** (Number/Choice/Date/Range) with an unparseable value.
`BooleanFilter` uses django-filter's `BooleanWidget`: accepts `true/false/1/0` (case-insensitive);
any other value is **ignored** (treated as absent) — tests must NOT assert 400 on booleans.

| Class (module) | Param | Filter | Semantics |
|---|---|---|---|
| `TeamFilterSet` (`teams/filters.py`), model `Team` | `name` | `CharFilter(lookup_expr="icontains")` | |
| | `include_free_agents` | `BooleanFilter(method="filter_include_free_agents")` (method returns qs unchanged) | see below |
| `PlayerFilterSet` (`teams/filters.py`), model `Player` | `team` | `NumberFilter(field_name="team")` | team id exact; foreign / nonexistent id → 200 empty (no existence leak) |
| | `name` | `CharFilter(lookup_expr="icontains")` | |
| `MatchFilterSet` (`matches/filters.py`), model `Match` | `team` | `NumberFilter(method="filter_team")` | `Q(team_red_id=v) \| Q(team_blue_id=v)` |
| | `season` | `NumberFilter(field_name="season")` | |
| | `match_type` | `ChoiceFilter(choices=Match.MATCH_TYPES)` | invalid → 400 `invalid_choice` |
| | `date_played_after`, `date_played_before` | `date_played = DateFromToRangeFilter()` | `YYYY-MM-DD`, inclusive by calendar day; invalid → 400 |
| `GameRoundFilterSet` (`matches/filters.py`), model `GameRound` | `match` | `NumberFilter(field_name="match")` | |
| | `arena_map` | `NumberFilter(field_name="arena_map")` | |
| `GameEventFilterSet` (`matches/filters.py`), model `GameEvent` | `event_type` | `ChoiceFilter(choices=GameEvent.EVENT_TYPES)` | invalid → 400 |
| | `actor` | `NumberFilter(field_name="actor")` | |
| | `target` | `NumberFilter(field_name="target")` | |
| | `timestamp_min`, `timestamp_max` | `timestamp = RangeFilter()` | raw ticks, inclusive |
| `ArenaMapFilterSet` (`core/filters.py`), model `ArenaMap` | `is_default` | `BooleanFilter()` | |
| | `has_confirmed_config` | `BooleanFilter(field_name="has_confirmed_config")` | filters the annotation (§5.12) |

`TeamFilterSet` free-agent rule: override
`filter_queryset(self, queryset: QuerySet[Team]) -> QuerySet[Team]` — after `super()`, unless
`self.form.cleaned_data.get("include_free_agents") is True`, apply
`queryset.filter(pk__in=Team.objects.regular().values("pk"))`. i.e. **default excludes every free-agent
pool Team** (global "Free Agents" + per-League pools, exactly `Team.objects.regular()` — matching the HTML
`team_list`); `?include_free_agents=true` includes them. List-only (§3.4).

Filter errors are raised by `DjangoFilterBackend` as DRF `ValidationError` → 400 envelope with `field` =
param name (the range filters report the base name, e.g. `date_played` / `timestamp`).

---

## 5. Endpoints

Router (`api_urls.py`, `DefaultRouter`): existing `teams/team`, `players/player`, `matches/match`,
`rounds/gameround` + **NEW** `router.register("maps", ArenaMapViewSet, basename="arenamap")`.
The two batch `path()`s are unchanged. All viewsets keep `@method_decorator(login_not_required, name="dispatch")`
and the settings-level `IsAuthenticated`. Ownership = `get_queryset()` built on the existing seam
(`owned_queryset` / `owned_match_q` / `owned_game_round_q`) + DRF `self.get_object()` → cross-Account = 404.

### 5.0 Route table

| Method + path | View / action | Route name | Success |
|---|---|---|---|
| GET `/api/teams/` | `TeamViewSet.list` | `team-list` | 200 paginated `TeamListSerializer` |
| POST `/api/teams/` | `TeamViewSet.create` | `team-list` | 201 `TeamSerializer` |
| GET `/api/teams/<pk>/` | `TeamViewSet.retrieve` | `team-detail` | 200 `TeamSerializer` |
| PATCH `/api/teams/<pk>/` | `TeamViewSet.partial_update` | `team-detail` | 200 `TeamSerializer` |
| PUT `/api/teams/<pk>/slots/` | `TeamViewSet.slots` | `team-slots` | 200 `TeamSerializer` |
| POST `/api/teams/<pk>/players/` | `TeamViewSet.add_player` | `team-players` | 201 `PlayerSerializer` |
| POST `/api/teams/generate/` | `TeamViewSet.generate` | `team-generate` | 201 `{created_teams, free_agent_count}` |
| POST `/api/teams/import/` | `TeamViewSet.import_roster` | `team-import` | 201 `{created_teams, appended_teams, player_count, row_count}` |
| GET `/api/teams/import/template/` | `TeamViewSet.import_template` | `team-import-template` | 200 raw CSV |
| GET `/api/players/` | `PlayerViewSet.list` | `player-list` | 200 paginated `PlayerSerializer` |
| GET `/api/players/<pk>/` | `PlayerViewSet.retrieve` | `player-detail` | 200 `PlayerSerializer` |
| PATCH `/api/players/<pk>/` | `PlayerViewSet.partial_update` | `player-detail` | 200 `PlayerSerializer` |
| DELETE `/api/players/<pk>/` | `PlayerViewSet.destroy` | `player-detail` | 204 empty |
| GET `/api/matches/`, `/api/matches/<pk>/` | `MatchViewSet` | `match-list` / `match-detail` | 200 |
| GET `/api/rounds/`, `/api/rounds/<pk>/` | `GameRoundViewSet` | `gameround-list` / `gameround-detail` | 200 |
| GET `/api/rounds/<pk>/events/` | `GameRoundViewSet.events` | `gameround-events` | 200 paginated |
| GET `/api/maps/`, `/api/maps/<pk>/` | `ArenaMapViewSet` | `arenamap-list` / `arenamap-detail` | 200 |
| POST `/api/simulate-batch/` | `SimulateBatchAPIView` | `api_simulate_batch` | 200 (unchanged status) |
| GET `/api/simulate-batch/<job_id>/` | `SimulateBatchStatusAPIView` | `api_simulate_batch_status` | 200 |

**405s that must hold:** PUT/DELETE `/api/teams/<pk>/`; GET `/api/teams/<pk>/slots/` and
`/api/teams/<pk>/players/`; POST/PUT `/api/players/` & PUT `/api/players/<pk>/`; any write on
matches/rounds/maps/events; GET `/api/teams/generate/`, `/api/teams/import/`; POST `/api/teams/import/template/`.
(`DELETE /api/teams/<pk>/` → 405 — no team delete.)

Routing note: DRF registers `detail=False` extra actions before the detail route, so `teams/generate/`,
`teams/import/`, `teams/import/template/` never resolve as `pk`. Do **not** set `lookup_value_regex`
(a URL-resolver 404 would be an un-enveloped Django 404); non-numeric pks reach DRF `get_object()` → 404 envelope.

### 5.1 `TeamViewSet`

```python
@method_decorator(login_not_required, name="dispatch")
class TeamViewSet(ListOnlyFilterMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Team.objects.prefetch_related("players")          # router introspection
    filterset_class = TeamFilterSet
    ordering_fields = ("name", "created_date", "wins", "losses", "id")
    ordering = ("name",)
    ordering_tiebreak = ("id",)
    def get_queryset(self) -> QuerySet[Team]: ...       # owned_queryset(Team.objects.prefetch_related("players"), self.request.user)
    def get_serializer_class(self) -> type[Serializer]: ...  # list -> TeamListSerializer; else TeamSerializer
    def create(self, request: Request) -> Response: ...
    def partial_update(self, request: Request, pk: str | None = None) -> Response: ...
    @action(detail=True, methods=["put"], url_path="slots", url_name="slots")
    def slots(self, request: Request, pk: str | None = None) -> Response: ...
    @action(detail=True, methods=["post"], url_path="players", url_name="players")
    def add_player(self, request: Request, pk: str | None = None) -> Response: ...
    @action(detail=False, methods=["post"], url_path="generate", url_name="generate")
    def generate(self, request: Request) -> Response: ...
    @action(detail=False, methods=["post"], url_path="import", url_name="import",
            parser_classes=[MultiPartParser, FormParser])
    def import_roster(self, request: Request) -> Response: ...
    @action(detail=False, methods=["get"], url_path="import/template", url_name="import-template")
    def import_template(self, request: Request) -> HttpResponse: ...
```

No `update` / `destroy` methods (→ 405). Response serializers always get `context={"request": request}`
(via `self.get_serializer`).

### 5.2 POST `/api/teams/` — create

Request JSON/form: `{"name": str}`. Bind `TeamForm(data=plain_form_data(TeamForm, request.data))`;
`validate_form_or_raise`; `team = form.save(commit=False)`; `team.manager = manager_or_none(request)`;
`team.save()`. → **201** `{"data": TeamSerializer(team), "meta": {}}`.
Missing name → 400 `[{"code": "required", "field": "name", ...}]`.

### 5.3 PATCH `/api/teams/<pk>/`

`team = self.get_object()` (owned → foreign = 404). `TeamForm(data=merged_form_data(TeamForm, team, request.data), instance=team)`;
validate; save. → 200 `TeamSerializer`. Empty body `{}` = no-op 200. Manager never changes.

### 5.4 PUT `/api/teams/<pk>/slots/` — full replace

`team = self.get_object()`. Data = `{slot: request.data.get(slot) for slot in TeamSlotForm.base_fields}`
(**omitted slot → `None` → cleared**; explicit `null` → cleared). Bind
`TeamSlotForm(data=..., instance=team)` (**`instance` must be a kwarg** — the form reads `kwargs["instance"]`
to build the own-players queryset). A player id not on this team (incl. another Account's player) →
400 `code: "invalid_choice"`, `field: "slot_<x>"` — the message never names the foreign player.
→ 200 `TeamSerializer` (re-fetched / refreshed).

### 5.5 POST `/api/teams/<pk>/players/` — add player

`team = self.get_object()`. Data = `{**player_form_initial(), **plain_form_data(PlayerForm, request.data)}`
(omitted fields — including `name` — filled from the **same initial the HTML `player_add` GET uses**, §6.1).
Bind `PlayerForm(data=...)`; validate; `player = form.save(commit=False)`; `player.team = team`;
`player.full_clean()` — on Django `ValidationError` raise
`EnvelopeValidationError(django_validation_error_to_envelope(e))`; `player.save()`.
→ **201** `PlayerSerializer(player)`. Out-of-range stat → 400 `max_value`/`min_value` on that field.

### 5.6 POST `/api/teams/generate/`

Bind `GenerateLeagueForm(data=plain_form_data(GenerateLeagueForm, request.data))` — no defaults merged
(the form's `initial` is HTML-only; omitted `num_teams` → 400 `required`). Cross-field `clean()` error →
`field: null`. Then `result = run_player_generation(form.cleaned_data, manager=manager_or_none(request))`.
→ **201**:

```json
{"data": {"created_teams": [<TeamListSerializer>...], "free_agent_count": 0}, "meta": {}}
```

Free-agent mode (`num_teams="0"`): `created_teams: []`, `free_agent_count: N` (players land on the global
unmanaged Free Agents Team — unchanged LG-00 behaviour). Status is 201 in both modes.

### 5.7 POST `/api/teams/import/` — multipart

`parser_classes = [MultiPartParser, FormParser]` (JSON → 415). Bind
`RosterImportForm(data=request.data, files=request.FILES)`; validate (missing file → 400 `required`,
`field: "csv_file"`; oversize / non-UTF-8 → 400 `invalid`, `field: "csv_file"`). Then
`result = run_roster_import(form.cleaned_data["csv_file"], manager=manager_or_none(request))`;
`RosterImportError` → `raise EnvelopeValidationError(roster_import_errors_to_envelope(exc.errors))`.
→ **201**:

```json
{"data": {"created_teams": [<TeamListSerializer>...], "appended_teams": [<TeamListSerializer>...],
          "player_count": 12, "row_count": 12}, "meta": {}}
```

### 5.8 GET `/api/teams/import/template/`

Returns `HttpResponse(build_roster_template_csv(), content_type="text/csv")` with
`Content-Disposition: attachment; filename="roster_template.csv"` — **body and both headers byte-identical
to the HTML `import_roster_template`**. Not enveloped. Anonymous → 403 envelope (permission runs first).

### 5.9 Roster error representation (locked) — `teams/api_views.py`

```python
def roster_import_errors_to_envelope(errors: list[RowError]) -> list[ErrorEntry]: ...
```

One entry per `RowError`, in parser order:
`{"code": "roster_invalid", "message": e.message, "field": f"row:{e.row_num}:{e.field}" if e.field else f"row:{e.row_num}"}`.
`row_num` is the parser's 1-based data-row index; `row:0` / `row:0:<column>` = file-level (header) errors.
Status 400. DB-collision errors use the same mapping (`field` = `row:<n>:role`).

### 5.10 `PlayerViewSet`

```python
@method_decorator(login_not_required, name="dispatch")
class PlayerViewSet(ListOnlyFilterMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                    mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = Player.objects.select_related("team")
    serializer_class = PlayerSerializer
    filterset_class = PlayerFilterSet
    ordering_fields = tuple(_SORT_KEYS)          # imported from teams.views
    ordering_aliases = _SORT_KEYS                # public -> ORM (offensive_synergy -> Offensive_synergy,
                                                 #   team -> team__name, overall_rating -> overall_rating_db)
    ordering = ("team__name", "name")
    ordering_tiebreak = ("name", "id")
    def get_queryset(self) -> QuerySet[Player]: ...
        # annotate_overall_rating(owned_queryset(Player.objects.select_related("team"), self.request.user, path="team"))
    def partial_update(self, request: Request, pk: str | None = None) -> Response: ...
```

- Ordering keys = exactly the 23 keys of LG-00c `_SORT_KEYS` (`name`, `team`, `overall_rating`, the 19
  stats with the lowercase `offensive_synergy` alias, `potential`). `preferred_roles` is NOT orderable
  (it is not a `_SORT_KEYS` key; `?ordering=preferred_roles` is dropped). The raw ORM name
  `Offensive_synergy` is NOT a valid public term.
- `PlayerSerializer` output unchanged (no `overall_rating` field added this slice).
- PATCH: `player = self.get_object()`; `PlayerForm(data=merged_form_data(PlayerForm, player, request.data), instance=player)`;
  validate; `save(commit=False)`; `full_clean()` (→ envelope on error); `save()`. → 200. `team` is not a
  PlayerForm field → cannot be changed (silently ignored).
- DELETE: DRF `destroy` → `player.delete()` → **204 empty body**. Foreign → 404. Slot FKs SET_NULL (model).
- No `create` (POST `/api/players/` → 405) and no `update` (PUT → 405).

### 5.11 `MatchViewSet`, `GameRoundViewSet` (read-only, filters added)

| Viewset | `filterset_class` | `ordering_fields` | `ordering` | `ordering_tiebreak` |
|---|---|---|---|---|
| `MatchViewSet(ListOnlyFilterMixin, ReadOnlyModelViewSet)` | `MatchFilterSet` | `("date_played", "id")` | `("-date_played",)` | `("-id",)` |
| `GameRoundViewSet(ListOnlyFilterMixin, ReadOnlyModelViewSet)` | `GameRoundFilterSet` | `("date_played", "round_number", "id")` | `("-date_played",)` | `("-id",)` |

`get_queryset` drops its own `.order_by(...)` (ordering comes from the backend / `ordering`); ownership
predicates unchanged. Serializers unchanged.

`events` action (`@action(detail=True, url_path="events")`, route `gameround-events`):
`game_round = self.get_object()` (owned → 404); `qs = game_round.events.select_related("actor", "target").order_by("timestamp", "id")`;
`fs = GameEventFilterSet(request.query_params, queryset=qs, request=request)`;
`if not fs.is_valid(): raise django_filters.utils.translate_validation(fs.errors)`; paginate `fs.qs` with
`GameEventSerializer`. Ordering is fixed (`timestamp, id`; no `?ordering`). Always paginated.

### 5.12 NEW `ArenaMapViewSet` — `core/api_views.py`

```python
@method_decorator(login_not_required, name="dispatch")
class ArenaMapViewSet(ListOnlyFilterMixin, ReadOnlyModelViewSet):
    queryset = ArenaMap.objects.all()
    serializer_class = ArenaMapSerializer
    filterset_class = ArenaMapFilterSet
    ordering_fields = ("name", "created_at", "id")
    ordering = ("name",)
    ordering_tiebreak = ("id",)
    def get_queryset(self) -> QuerySet[ArenaMap]: ...
        # ArenaMap.objects.annotate(has_confirmed_config=Exists(
        #     MapZoneConfig.objects.filter(arena_map=OuterRef("pk"), confirmed=True)))
```

**Not ownership-filtered** (ADR-0038 shared reference data): every authenticated Account sees every map.

`core/serializers.py`:

```python
class ArenaMapSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(read_only=True, use_url=True)        # absolute URL (request in context) or null
    has_confirmed_config = serializers.BooleanField(read_only=True)     # from the annotation
    class Meta:
        model = ArenaMap
        fields = ["id", "name", "image", "is_default", "img_width", "img_height", "created_at", "has_confirmed_config"]
```

`has_confirmed_config` is true iff a `MapZoneConfig` with `confirmed=True` exists (same truth as
`latest_confirmed_config() is not None`).

### 5.13 Batch endpoints (API-03) — enveloped, logic unchanged

- Success `data` is **byte-identical** to today's bare JSON: POST keys `_BATCH_POST_KEYS`
  (`job_id, team_red_id, team_red_name, team_blue_id, team_blue_name, arena_map_id, n`), status keys
  `_BATCH_STATUS_KEYS` = the UI `batch_simulate_status` JSON. POST status stays **200**.
- The three hand-built `Response({"detail": msg}, status=400)` become `raise ValidationError(msg)` (DRF) —
  same messages, same 400 → envelope `{"code": "invalid", "message": msg, "field": null}`.
  Serializer failures → per-field entries (e.g. `field: "team_blue"`, `code: "required"`).
- `SimulateBatchRequestSerializer` stays (ADR-0039 leaves it). The team lookup there is NOT ownership-scoped
  today; leave it — out of scope (note in PR as follow-up).

---

## 6. Services refactor — `teams/services.py`

### 6.1 Small helpers

```python
def player_form_initial() -> dict[str, object]: ...
def annotate_overall_rating(qs: QuerySet[Player]) -> QuerySet[Player]: ...
def build_roster_template_csv() -> str: ...
```

- `player_form_initial()` = `{**{n: f.initial for n, f in PlayerForm.base_fields.items() if f.initial is not None},
  **_random_player_profile()}` — i.e. the 19 stat defaults (50, from the model) plus one
  `_random_player_profile()` call (name, age, started_playing_age, total_games, home_site, height).
  HTML `player_add` GET changes to `PlayerForm(initial=player_form_initial())` — rendered output unchanged
  (stats already rendered 50; same single RNG draw).
- `annotate_overall_rating(qs)` = `qs.annotate(overall_rating_db=(<sum of the 19 stats>) / 19.0)` — the exact
  expression lifted from `teams.views.player_list`; `player_list` calls it (byte-identical HTML).
- `build_roster_template_csv()` = header `ALL_COLUMNS` + `_ROSTER_TEMPLATE_ROWS` via `csv.writer` (moved from
  `import_roster_template`, which now returns `HttpResponse(build_roster_template_csv(), ...)` unchanged).
  `_ROSTER_TEMPLATE_ROWS` moves to services (re-exported from views).

### 6.2 Generation

```python
@dataclasses.dataclass(frozen=True)
class GenerationResult:
    created_teams: list[Team]
    free_agent_count: int

@transaction.atomic
def run_player_generation(cleaned_data: dict, *, manager: "AbstractBaseUser | None") -> GenerationResult: ...
```

Body = the post-validation half of today's `generate_players` POST, **verbatim and in the same RNG order**:
`_resolve_count_marker(num_teams, "random_2_10", 2, 10)` → `players_per_team` marker resolution
(`random_team` 6–8, `random_pool` 12–100, else `int`) → `rng = random.Random()` → shuffle team-name pool →
shuffle player-name pool → `_generate_teams(..., manager=manager)` (teams mode) or `_generate_free_agents(...)`
(pool mode, `num_teams == 0`). HTML `generate_players` keeps its `@transaction.atomic`, form handling and
template render, calling `run_player_generation(form.cleaned_data, manager=manager_or_none(request))` and
passing `result.created_teams` / `result.free_agent_count` into the same context keys.

The generation helpers **move to `teams/services.py`** (to avoid a views↔services import cycle):
`_pop_unique_name`, `_team_name_exists`, `_player_name_exists_on_team`, `_resolve_count_marker`,
`_build_player_kwargs`, `_assign_team_slots`, `_generate_teams`, `_generate_free_agents`, plus the imports
they use (`draw_stats`, `draw_preferred_roles`, `assign_slots`, `TEAM_NAMES`, `PLAYER_NAMES`). Signatures unchanged.

### 6.3 Roster import (+ ownership fix)

```python
@dataclasses.dataclass(frozen=True)
class RosterImportResult:
    created_teams: list[Team]
    appended_teams: list[Team]
    player_count: int
    row_count: int

def resolve_import_teams(names: Iterable[str], *, manager: "AbstractBaseUser | None") -> dict[str, Team]: ...
def _check_db_slot_collisions(parsed: ParsedRoster, *, manager: "AbstractBaseUser | None") -> None: ...
def _apply_roster(parsed: ParsedRoster, *, manager: "AbstractBaseUser | None") -> tuple[list[Team], list[Team], int]: ...
def run_roster_import(csv_text: str, *, manager: "AbstractBaseUser | None") -> RosterImportResult: ...
```

- `resolve_import_teams`: `owned_queryset(Team.objects.filter(name__in=list(names)), manager).order_by("id")`
  → `{t.name: t}` (highest id wins for a duplicated name — `Team.name` is not DB-unique). Scope = the
  Account's **own + Unmanaged** teams (owned_queryset semantics; Unmanaged is writable by any Account, so
  appending to an Unmanaged team — including the global Free Agents team — stays legal).
  `manager=None` → Unmanaged only.
- `_check_db_slot_collisions`: identical logic/messages, but `teams_by_name = resolve_import_teams(parsed.by_team, manager=manager)`.
  A name matching **only a foreign Account's Team** is absent → no collision check → **no foreign player
  name can appear in any error message**.
- `_apply_roster`: replaces `Team.objects.get_or_create(name=...)` with the same resolver: hit → appended;
  miss → `Team.objects.create(name=team_name, manager=manager)` → created (**stamped**). Never
  `get_or_create` (avoids `MultipleObjectsReturned` on duplicate names). Player creation / slot fill unchanged.
- `run_roster_import`: `with transaction.atomic(): parsed = parse_roster_csv(csv_text); _check_db_slot_collisions(parsed, manager=manager); created, appended, n = _apply_roster(parsed, manager=manager)`;
  returns `RosterImportResult(created, appended, n, len(parsed.rows))`. `RosterImportError` propagates **after**
  the atomic block rolls back (no partial writes).
- `manager` is a **required keyword** on both private helpers (no default) so no caller silently keeps the leak.
- HTML `import_roster`: keeps decorator / form / templates / context keys; its try-block becomes
  `result = run_roster_import(form.cleaned_data["csv_file"], manager=manager_or_none(request))`, the
  `except RosterImportError` branch is unchanged (incl. `transaction.set_rollback(True)`), success context
  uses `result.*`. Behaviour byte-identical except the ownership fix.
- `matches/tournament_views.py` (line ~833–834, the tournament roster import): pass
  `manager=manager_or_none(request)` to both `_check_db_slot_collisions` and `_apply_roster`. (Same leak, same
  fix; tournament-created teams are now stamped. Flag in PR.)

### 6.4 Re-exports (compat)

`teams/views.py` imports from `teams.services` every moved name (`_pop_unique_name`, `_team_name_exists`,
`_player_name_exists_on_team`, `_resolve_count_marker`, `_build_player_kwargs`, `_assign_team_slots`,
`_generate_teams`, `_generate_free_agents`, `_check_db_slot_collisions`, `_apply_roster`, `_ROSTER_TEMPLATE_ROWS`)
so `from teams.views import _generate_teams, _apply_roster, ...` in `matches/league_views.py`,
`matches/tournament_views.py`, `teams/tests/test_generate_teams_tiered.py`, `teams/tests/test_ownership.py`
keeps working. `_SORT_KEYS`, `_coerce_*` stay in `teams/views.py`.

---

## 7. What is NOT changing

Models, migrations, simulation, scores; `teams/serializers.py`, `matches/serializers.py`; HTML templates and
HTML URL names; HTML list pages' forgiving-fallback validation; `SimulateBatchRequestSerializer`;
`_team_name_exists` stays global (generation name-dedupe, not a leak in messages — out of scope).

---

## 8. Test boundary

**Asserted (public surface):** envelope shapes and `meta` keys/values; status codes and error `code`/`field`
values from §2.6; 204 empty body; CSV exemption byte-equality; ownership 404s on every read **and write**
(team PATCH / slots PUT / add player / player PATCH / player DELETE on another Account's row → 404 and the
row is unchanged); filter results and 400s for typed filters; ordering incl. aliases; route names via
`reverse()`; manager stamping on create/generate/import; HTML generate/import/player_add/player_list/template
behaviour unchanged; roster-import leak regression; service dataclass fields.

**Internal (do NOT assert):** the `envelope_meta` attribute name, renderer branch internals, helper call
order, `ListOnlyFilterMixin` internals, exact Django/DRF message strings other than ones the project owns
(roster messages, batch messages, `GenerateLeagueForm.clean` messages), random names/profile values.

Seed RNG (`random.seed(42)`) when asserting generation counts; never assert exact random names.

### 9. New test files (CLAUDE.md placement)

| File | Covers |
|---|---|
| `laserforce_simulator/api/tests/__init__.py` | package |
| `laserforce_simulator/api/tests/test_envelope.py` | renderer (object / list / paginated / 204 / 4xx passthrough / defensive normalisation), `EnvelopePagination` meta (incl. empty list `total_pages: 1`, `page_size` clamp to 100, invalid page 404 `not_found`), exception-handler mapping table §2.6 (404, 403 `not_authenticated`, 405 `method_not_allowed` + `Allow` header, 400 `parse_error`), `AliasOrderingFilter` (alias map, `-` prefix, invalid dropped, tiebreak) |
| `laserforce_simulator/api/tests/test_form_errors.py` | `form_errors_to_envelope` (field + `__all__`→null, one entry per message, `code=None`→`invalid`), `django_validation_error_to_envelope` (dict + list), `validate_form_or_raise`, `plain_form_data` (QueryDict multi-value, unknown keys dropped), `merged_form_data` |
| `teams/tests/test_api_writes.py` | §5.2–5.7 + 5.10 writes: success shapes/status, form errors, PATCH merge (only sent fields change), slots full-replace clears omitted, foreign player in slot → 400 `invalid_choice` without name, add-player defaults (omitted name filled, stats 50), generate both modes + cross-field `field: null`, import happy path / form errors / `row:<n>:<field>` errors / atomic rollback / 415 on JSON, template byte-equality vs HTML, player DELETE 204 empty, every 405 in §5.0, cross-Account 404 on every write, manager stamping |
| `teams/tests/test_api_filters.py` | `TeamFilterSet` (`name`, free-agent default exclusion incl. per-League pool, `include_free_agents=true`, Free Agents detail still reachable by id), `PlayerFilterSet` (`team`, `name`, `team=abc` → 400, foreign team id → empty), player ordering (all 23 keys accepted, `offensive_synergy` works, `Offensive_synergy` dropped, `overall_rating` desc correct), set(`ordering_fields`) == set(`_SORT_KEYS`) |
| `teams/tests/test_services.py` | `run_player_generation` (`GenerationResult` fields, manager stamped, free-agent mode), `run_roster_import` (`RosterImportResult` fields, rollback on error, created teams stamped), `resolve_import_teams` scope, `player_form_initial` keys, `annotate_overall_rating`, `build_roster_template_csv` |
| `teams/tests/test_roster_import_ownership.py` | **leak regression (HTML + API + service):** Account B owns team "Shared" with a filled commander slot named "SecretPlayer"; Account A imports a CSV with team "Shared"/commander → succeeds, creates a NEW "Shared" team managed by A, B's team untouched, and no response/error ever contains "SecretPlayer"; collision against A's own / an Unmanaged team still 400s with the existing message; duplicate same-name owned teams don't raise `MultipleObjectsReturned` |
| `matches/tests/test_api_filters.py` | `MatchFilterSet` (`team` either side, `season`, `match_type` + invalid → 400, `date_played_after/_before` + invalid date → 400), `GameRoundFilterSet` (`match`, `arena_map`), events filters (`event_type` + invalid → 400, `actor`, `target`, `timestamp_min/_max`), events stay `timestamp` ordered & paginated, match/round ordering |
| `core/test_maps_api.py` | (`core` has a single `core/tests.py`, so a sibling `test_*.py`) maps list/detail envelope, fields exactly §5.12, `image` absolute URL, `has_confirmed_config` true/false, filters `is_default` / `has_confirmed_config`, visible to two different Accounts (not ownership-filtered), anonymous 403, writes 405 |
| `accounts/tests/test_auth_views.py` (extend) | add `/api/maps/` to `TestApiIsUnauthorisedNotRedirected.API_PATHS`; assert anonymous body `errors[0].code == "not_authenticated"` |

### 10. Blast radius — existing tests that MUST be updated (envelope / now-allowed writes)

| File | Class | Change |
|---|---|---|
| `teams/tests/test_apis.py` | `TestTeamAPI` | `test_list_is_paginated` → assert `data`/`meta` with `count`; detail tests read `body["data"]`; `test_write_methods_not_allowed` → only PUT/DELETE detail stay 405 (POST/PATCH now valid — move positive cases to `test_api_writes.py`) |
| `teams/tests/test_apis.py` | `TestPlayerAPI` | paginated + detail reads via `["data"]`; `test_write_methods_not_allowed` (POST list 405) unchanged |
| `matches/tests/test_apis.py` | `TestMatchAPI` | `count/results` → `meta.count` / `data`; detail via `["data"]`; 405s unchanged |
| `matches/tests/test_apis.py` | `TestGameRoundAPI` | same; events tests `data["results"]` → `body["data"]`, `data["count"]` → `body["meta"]["count"]` |
| `matches/tests/test_batch_views.py` | `TestSimulateBatchAPIPost` | `test_post_returns_locked_json_shape` reads `body["data"]` (keys still `_BATCH_POST_KEYS`); 400 tests may additionally assert `errors` |
| `matches/tests/test_batch_views.py` | `TestSimulateBatchAPIStatusEager` | `post_resp.json()["data"]["job_id"]`; status body via `["data"]` (keys still `_BATCH_STATUS_KEYS`) |
| `matches/tests/test_batch_views.py` | `TestSimulateBatchAPIStatusUnknownJobId` | body via `["data"]` |
| `matches/tests/test_batch_views.py` | `TestAPIInheritsAllowAnyPermissions` | status-only — no change expected (verify) |
| `matches/tests/test_ownership.py` | `TestApiQuerysetScoping` | `.json()["results"]` → `.json()["data"]` (2 sites); 404 / 403 assertions unchanged |
| `matches/tests/test_simulation_ticks.py` | `TestAPIReturnsRawTicks` | events: `data["count"]`→`["meta"]["count"]`, `data["results"]`→`["data"]`; round detail `data["player_states"]` → `["data"]["player_states"]` |
| `accounts/tests/test_auth_views.py` | `TestApiIsUnauthorisedNotRedirected` | statuses/content-type unchanged — no edit required beyond §9 additions |
| `teams/tests/test_generate_view.py` | `test_pure_module_raises_mid_generation_rolls_back` | `mock.patch.object(teams.views, "draw_stats", ...)` → patch `teams.services` (where `_build_player_kwargs` now resolves `draw_stats`) |
| `teams/tests/test_roster_import_view.py` | all classes | expected unchanged — must stay green (byte-identical HTML); only re-check if any test relied on importing into a **foreign** team by name |
| `matches/tests/*tournament*` roster-import tests | — | must stay green; any test importing into an existing team owned by a different Account than the logged-in one would now create a new team (flag if hit) |

Unaffected (verified by grep): `teams/tests/test_serializers.py` (serializer-direct), HTML view tests that
don't hit `/api/`.

---

## 11. Verification gates for the orchestrator

1. `python laserforce_simulator/manage.py makemigrations --check --dry-run` → no changes.
2. Full `pytest` (parallel) green; report exact counts.
3. `python -m black laserforce_simulator`.
4. Spot-check: `reverse("team-import-template") == "/api/teams/import/template/"`, `reverse("arenamap-list") == "/api/maps/"`,
   `reverse("gameround-events", args=[1]) == "/api/rounds/1/events/"`.
5. Grep: no `Response(` with `status=4xx` left in `teams/api_views.py`, `matches/api_views.py`, `core/api_views.py`.
