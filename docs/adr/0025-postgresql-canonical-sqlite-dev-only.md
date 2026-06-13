# PostgreSQL is the canonical database; SQLite is a guarded dev-only convenience

**Status:** Accepted (INFRA-01, 2026-06-13)

## Context

PLAN.md **INFRA-01** ("Migrate from SQLite to PostgreSQL") was motivated by
recurring `OperationalError: database is locked` errors during long "Play Until
End of Season" runs. SQLite is a single-writer database, and the app now has
genuinely concurrent writers: the Celery "Play …" tasks (ADR-0013) run a loop of
per-Round write transactions (ADR-0016) while the dashboard JS polls
`play_status`. A WAL + busy-timeout mitigation (`core/db_pragmas.py` +
`DATABASES["default"]["OPTIONS"]`) bought headroom, but the durable fix is a
concurrent-writer database.

By the time INFRA-01 was grilled (2026-06-13), most of the migration had
**already shipped incidentally** via the Docker / CI / Fly.io deployment work:
`psycopg2-binary` was in `requirements.txt`; `docker-compose.yml` ran
`postgres:16` and pointed the app's `DATABASE_URL` at it; CI ran the full pytest
suite *and* the docker smoke job against `postgres:16`; and the Fly.io deploy ran
off that same Postgres-backed image. `settings.py` already read `DATABASE_URL`
via `dj_database_url`, defaulting to SQLite only when the env var is unset.

The open question INFRA-01 actually resolved was therefore **not** "how do we get
onto Postgres" but **"what is the canonical database, and what do we do with the
SQLite codepath that is still the local default?"** A future reader seeing a
SQLite default in `settings.py`, a SQLite-only PRAGMA hook in `db_pragmas.py`,
*and* Postgres everywhere in compose/CI/deploy would reasonably wonder which is
the real database and why both exist. This ADR records that decision.

## Decisions

1. **PostgreSQL is the canonical database.** Production (Fly.io), CI (both the
   test job and the docker smoke job), and the Docker Compose stack all run
   `postgres:16`. The lock fix *is* Postgres's MVCC: concurrent writers never
   take a single-writer lock, so the `database is locked` class of error is
   structurally eliminated in every environment that matters.

2. **SQLite stays as a guarded dev-only convenience, not removed.** When
   `DATABASE_URL` is unset, `settings.py` falls back to a local `db.sqlite3` so a
   contributor can `runserver` and `pytest` with zero daemon setup. The SQLite
   write-contention hardening — the `OPTIONS` `timeout`/`transaction_mode` block
   and the `core/db_pragmas.py` WAL `connection_created` hook — **stays**, guarded
   on `ENGINE == sqlite3` / `connection.vendor == "sqlite"` so it is a silent
   no-op on Postgres. It is kept (not deleted) because SQLite remains a supported
   local path that still benefits from the WAL mitigation.

3. **No row migration; dev/test data is disposable (ADR-0004).** Moving to
   Postgres is a fresh `migrate` on an empty database, not a `dumpdata`/`loaddata`
   transfer. Existing local SQLite data is throwaway. Only real production data
   (none exists yet) would ever need a transfer plan.

4. **Parity is verified, not assumed — but the headline acceptance is a Postgres
   property, not an automated test.** The "Play Until End of Season with no lock
   errors" criterion cannot be reproduced in the test suite: the suite forces
   `CELERY_TASK_ALWAYS_EAGER` (synchronous, in-process, single writer), so there
   is no concurrent writer to lock. Lock-freedom on Postgres is an MVCC property,
   documented here and confirmed by a one-off manual compose smoke. What is
   **automated** instead is (a) a backend-independent unit guard that the SQLite
   PRAGMA receiver early-returns on a non-sqlite connection (PRAGMAs never run on
   Postgres), (b) a JSONField round-trip guard (dict in → dict out, valid on
   either backend, covering the SQLite-text vs Postgres-`jsonb` boundary), and
   (c) the existing full suite, which CI already runs green on Postgres.

## Consequences

- **Two databases are supported on purpose.** Postgres is canonical; SQLite is a
  documented local convenience. This is a deliberate dual-DB stance, not an
  unfinished migration — hence the SQLite hardening stays in the tree as a
  guarded fallback rather than being ripped out.
- **The SQLite-specific assumption audit came back clean.** No raw SQL,
  `.extra()`, or `.raw()` (only the guarded PRAGMA); **zero** case-insensitive
  lookups (`icontains`/`iexact`) that would rely on SQLite's case-insensitive
  `LIKE`; JSONField round-trips through `jsonb` without app code depending on key
  order. The only residual behavioural delta is text `order_by("name")` collation
  (SQLite BINARY vs Postgres locale), which is cosmetic ordering, not correctness.
- **No simulator-mechanic change, no Score Calibration re-baseline.** This is an
  infrastructure decision; no simulation input or mechanic moves.
- **No new domain term.** Database engine choice is infrastructure, not
  ubiquitous language — CONTEXT.md is untouched.
- **CI cost.** Running the suite against a real Postgres service (vs in-memory
  SQLite) is marginally slower but buys prod parity; accepted.
