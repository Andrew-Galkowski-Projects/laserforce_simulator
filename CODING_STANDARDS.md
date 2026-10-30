# Coding Standards

Read by the **Standards** reviewer in `/code-review`, not by the implementing agent. Only judgement calls belong here. A rule a machine can check (formatting, an import shape, a banned call) should become a check in `/verify`, a pre-commit hook or CI, not a line in this file. `black` formatting and `mypy` errors are already enforced by `/verify`, so don't report them.

## Python and Django

- **Type hints on every public function signature.** Flag a public function that has none.
- **One job per function.** Flag a function over ~25 lines that does two things and splits cleanly.
- **Django idioms.** Prefer `get_object_or_404`, model methods over logic in views, and a `services.py` builder shared by the HTML view and the API (see `teams/services.py`, `matches/services.py`, `matches/league_services.py`, `core/services.py`). A service returns model objects or dataclasses, and the API view turns them into refs (`history_seasons` returns Seasons, `_history_item` builds the refs); a service that builds the response dict is a finding. Flag raw SQL unless a comment justifies it.
- **Querysets.** Flag a queryset evaluated inside a loop (N+1). Suggest `select_related` / `prefetch_related`. Flag a large list built where a slice or generator would do. Flag a queryset whose iteration order decides RNG draws, saved order or output order but has no explicit `order_by`: PostgreSQL row order moves after an UPDATE, while SQLite's id order hides it (CI-01's rollover develop bug).
- **Dead code.** Unused imports, unreachable branches, commented-out blocks.
- **Pythonic idioms.** Comprehensions for simple transforms, `any()` / `all()`, `zip()`, `enumerate()`. A `dataclass` or `NamedTuple` instead of a dict with fixed keys.

## Simulation engine

- **The tick loop is hot.** Flag O(n^2) work, repeated dict lookups that could be hoisted, and values computed more than once per tick.
- **Determinism.** Randomness goes through the seeded RNG the simulator is given. Flag any bare `random.*` call, set iteration that decides an outcome, or ordering that depends on dict or DB order (DB order: see **Querysets**). A change to RNG, parallelism or ordering needs a serial-vs-parallel determinism test.
- **Tick math.** Check off-by-one errors at tick boundaries and conversions between seconds and ticks (`sim_helpers/time_constants.py`).
- **Formula constants.** A constant in a formula (MVP weights, score values) must match its documented value in the app's `CLAUDE.md`.

## REST API (ADR-0039)

- Writes bind the existing Django `Form`. Serializers are output-only. When no HTML
  Form exists (the HTML route takes the value from its URL), the write binds a small
  params Form in `<app>/api_forms.py` the same way, as `ZoneSizeParamsForm` and
  `SelfInsertParamsForm` do. Hand-rolled body validation is a finding.
- Views raise errors; they never build a 4xx `Response` by hand.
- Filters use an explicit `django-filter` allowlist.
- A row owned by another Account returns 404. An anonymous request returns 401
  with `WWW-Authenticate: Bearer` (ADR-0041).
- A list of related ids or rows in an API output declares its order (`order_by`, or
  a sort in the serializer). A model without `Meta.ordering` (e.g. `GameRound`) never
  supplies one, so `round_ids` came back in an undefined order until ANG-06d.

## Angular frontend

- **Parity with the Django form and template it replaces.** An Angular form that ports
  a Django form carries each widget's constraints (`min`, `max`, `step`, `maxlength`,
  `required`) on the matching control, from the Form's fields and the template's
  `<input>` attributes both. Flag a control missing one (ANG-06b shipped the Player
  profile inputs without their bounds); check the template before flagging an extra one
  (ANG-06-17's Ticket price `step="0.5"` lives only in the template).
- **Wrapping field text.** A `mat-form-field` with a `mat-hint` or a server error
  (`applyApiErrors`) sets `subscriptSizing="dynamic"`, so a wrapped line pushes the
  next field down instead of overlapping it (ANG-06 item 7's invite hint and password
  rules). A grid or flex row of such fields sets `align-items: start`, so one field's long
  hint leaves its neighbour's outline alone (ANG-06-14's Conferences hint stretched "Number
  of teams"). Its row gap is at least 1rem, so a two-line hint clears the next row's
  outlined label (ANG-06-17's Expense Settings had 0.25rem).
- **Equal tiles share their rows.** A row of equal metric tiles is a `grid` with
  `repeat(auto-fit, minmax(…))`, its minimum small enough that every tile fits one row
  in the page area at 1280px (~1010px, ~810px beside the League nav). A flex row with a wide basis leaves the last tile alone
  on a full-width row (ANG-06-17's Cash tile at 1280px, `flex: 1 1 8rem`).
- **Name pickers get room.** A `mat-select` whose options are entity names ("Player
  (Team)", "#id - Red vs Blue") takes at least half the form's row, and a full row
  when the form is narrow, so the chosen label stays whole. Flag one that shares a
  row with short filters. The flex recipe is `.h2h-picker__wide` in
  `frontend/src/app/shared/head-to-head/h2h-page.scss` (ANG-06-9's H2H and Compare
  pickers clipped names at 1280px and 768px).

- **Names in a scrolling table stay whole.** In a table that scrolls sideways, a
  header or cell holding a name wraps only between words and sets a `min-width`
  (a crosstable heading, a Standings Team). Flag `overflow-wrap: anywhere` on one:
  the table then shrinks instead of scrolling, and at 768px names broke one
  letter per line (ANG-06-13b's crosstable and Standings). It suits free text in
  a card or alert.
- **A state shows in shape or text, not colour alone.** A state a user must tell
  apart (on / off, current / former) differs by glyph, label or text as well as
  colour, as Sides keep their R / B badge. Flag a toggle or row mark that changes
  only a colour token: specs read text, so only a screenshot catches it (ANG-06-16's
  Watch-flag star read the same grey-blue on and off, and Team History's on-team
  mark was a tint alone).

## Tests

- Each new public function has a happy-path test and an edge-case or failure test.
- A bug fix has a regression test that fails without the fix.
- A filter or guard that ANDs conditions gets one fixture per condition that fails only that condition (ANG-06-14's `simulation_ready` had a red-only map and no blue-only one, so dropping the red-base check survived).
- Tests go through public seams with real in-memory objects or Django `TestCase`. Flag a test that only checks that a mock returned what it was told to return.
- Simulation tests use a fixed seed or injected deterministic stats. Flag an exact point-total assertion on an unseeded run.
- Test placement follows the root `CLAUDE.md` ("Test placement").
- Teardown deletes only a path the same code created and kept in a variable. Flag a delete (`rmtree`, `unlink`, `delete`) whose target is read back from settings or the environment: if setup failed, that value can still be the real directory (TEST-01's first `pytest_unconfigure` would have removed the real `media/`).

## Naming

- Use the terms defined in `GLOSSARY.md`. Flag a synonym the glossary lists under its "avoid" words.
