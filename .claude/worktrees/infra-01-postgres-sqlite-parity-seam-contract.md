# INFRA-01 — PostgreSQL/SQLite Parity Hardening — Seam Contract

**Type:** TESTS + DOCS ONLY. **No production code change. No new production symbol. No migration.**

## No-op declaration (Code agent)

- INFRA-01 introduces **zero new production names** and changes **zero production lines**.
- `laserforce_simulator/settings.py` stays **byte-unchanged**.
- `laserforce_simulator/core/db_pragmas.py` stays **byte-unchanged**.
- **No model field added/changed → no `makemigrations`, no migration file.**
- Postgres is already canonical (`docker-compose.yml`, CI `ci.yml`, Fly deploy, `psycopg2-binary` in `requirements.txt`). SQLite remains the guarded dev-only default when `DATABASE_URL` is unset. ADR-0025 is already written.
- The Code agent's job is to confirm the no-op. The only artifacts that land are two new tests in `core/tests.py` (Tests agent) and any doc touch (Docs agent).

## Existing symbols the Tests assert against (verbatim — do not change)

### 1. `core.db_pragmas.set_sqlite_pragmas`
File: `laserforce_simulator/core/db_pragmas.py`

```python
@receiver(connection_created)
def set_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
```

- Signature: `set_sqlite_pragmas(sender, connection, **kwargs)`.
- Guard: early-returns when `connection.vendor != "sqlite"`. Only after the guard is `connection.cursor()` touched.

### 2. `core.models.MapZoneConfig`
File: `laserforce_simulator/core/models.py`

Required construction fields (an `ArenaMap` FK plus `zone_size`; `zone_data` is the payload under test):

```python
class MapZoneConfig(models.Model):
    arena_map = models.ForeignKey(ArenaMap, on_delete=models.CASCADE, related_name="zone_configs")
    zone_size = models.IntegerField()           # required (no default)
    zone_data = models.JSONField()              # required (no default) — field under test
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

`ArenaMap` (parent FK target) requires `name` + `image`; build it house-style with an in-memory PNG via `SimpleUploadedFile` (see `core/tests.py` `_make_map`, e.g. `ArenaMap.objects.create(name=..., image=SimpleUploadedFile("x.png", <png bytes>, content_type="image/png"), img_width=20, img_height=15)`).

## The two tests (both land in `core/tests.py`)

### Test A — PRAGMA receiver vendor-guard (backend-agnostic, no DB)
- **Asserts:** calling `set_sqlite_pragmas(sender=<any>, connection=<fake vendor="postgresql">, **kwargs)` **never calls** `connection.cursor()`.
- **Mechanism:** fake connection is a `MagicMock` (or equivalent) with `connection.vendor = "postgresql"`; after the call assert `fake_connection.cursor.assert_not_called()`.
- Runs identically on any backend — it does not hit the database.

### Test B — JSONField round-trip parity (DB; passes on whichever backend the suite runs)
- **Asserts:** a `MapZoneConfig` saved with the nested `zone_data` fixture below, then `refresh_from_db()`, has `reloaded.zone_data` **deep-equal** to the input payload (`assertEqual`).
- Covers SQLite-text vs Postgres-jsonb serialization parity. Passes on SQLite locally and Postgres in CI.

#### `zone_data` fixture payload (exact shape to round-trip)
A nested dict containing: a 2D `zones` int array, a `wall_meta` dict, and an `elevation` 2D float array.

```python
{
    "zones": [[1, 0, 1], [4, 1, 5]],
    "wall_meta": {"0,1": {"facing": "N", "height": 2.0}},
    "elevation": [[0.0, 1.5, 0.0], [2.25, 0.0, 1.0]],
}
```

## Test boundary

- **Asserted (public behavior):** (A) the receiver's externally observable contract — it does **not** open a cursor when `vendor != "sqlite"`; (B) the ORM JSON round-trip — what goes into `zone_data` deep-equals what comes back after `refresh_from_db()`.
- **NOT asserted (internal):** the exact PRAGMA SQL strings (`"PRAGMA journal_mode=WAL;"`, `"PRAGMA synchronous=NORMAL;"`) are implementation detail and are **not** assertion targets. Test A asserts cursor-not-called, not which statements would run. No assertion on which DB backend is active, on `DATABASES` config, or on `settings.py`/`db_pragmas.py` source bytes.
