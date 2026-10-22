# Coding Standards

Read by the **Standards** reviewer in `/code-review`, not by the implementing agent. Only judgement calls belong here. A rule a machine can check (formatting, an import shape, a banned call) should become a check in `/verify`, a pre-commit hook or CI, not a line in this file. `black` formatting and `mypy` errors are already enforced by `/verify`, so don't report them.

## Python and Django

- **Type hints on every public function signature.** Flag a public function that has none.
- **One job per function.** Flag a function over ~25 lines that does two things and splits cleanly.
- **Django idioms.** Prefer `get_object_or_404`, model methods over logic in views, and a `services.py` builder shared by the HTML view and the API (see `teams/services.py`, `matches/services.py`, `matches/league_services.py`, `core/services.py`). Flag raw SQL unless a comment justifies it.
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

## Angular frontend

- **Parity with the Django form it replaces.** An Angular form that ports a Django
  form carries each widget's constraints (`min`, `max`, `maxlength`, `required`) on the
  matching control. Flag a control missing one (ANG-06b shipped the Player profile
  inputs without their bounds).

## Tests

- Each new public function has a happy-path test and an edge-case or failure test.
- A bug fix has a regression test that fails without the fix.
- Tests go through public seams with real in-memory objects or Django `TestCase`. Flag a test that only checks that a mock returned what it was told to return.
- Simulation tests use a fixed seed or injected deterministic stats. Flag an exact point-total assertion on an unseeded run.
- Test placement follows the root `CLAUDE.md` ("Test placement").

## Naming

- Use the terms defined in `GLOSSARY.md`. Flag a synonym the glossary lists under its "avoid" words.
