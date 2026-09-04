# Accounts are email-first and isolated; ownership is a uniform `manager` FK on five roots

UX-01 introduces real logins, and the obvious word for the new relationship — "owner" — was already
taken: this project's **Owner** is the fictional boss who judges and fires the **Manager** via the
**Owner evaluation** ([ADR-0026](0026-manager-firing-owner-mood.md), `OwnerEvaluation`,
`owner_mood.py`). Rather than rename that career-mode vocabulary, we promoted the term CONTEXT.md had
already reserved for exactly this moment ("the Manager is not a persisted entity — there is no
`Manager`/`User` row until UX-01"). We decided:

1. **A custom `accounts.User`** (`AbstractUser`, `username = None`, unique `email` as
   `USERNAME_FIELD`) in a new fourth Django app, with `AUTH_USER_MODEL` set before any user FK
   exists. Today is the last cheap moment for this swap.
2. **A nullable `manager` FK on exactly five ownership roots** — `Team`, `League`, `Tournament`, a
   sandbox `Match` (`season IS NULL`) and a standalone `GameRound` (`match IS NULL`). The rule is *a
   row is a root exactly when its parent FK is null*; every other row derives its Manager by
   traversing its non-null parent FK. The permission check is therefore one flat predicate on a root, or one
   traversal, and never a rule about *which* Team is "really" yours.
3. **`ArenaMap` is deliberately not owned.** It is shared reference data.
4. **Global `LoginRequiredMiddleware`** (Django 5.1+) with `@login_not_required` on the four auth
   surfaces, rather than ~220 per-view decorators.
5. **Cross-account access is refused outright.** An Account sees only its own rows plus **Unmanaged
   rows** (`manager IS NULL`), which stay readable and writable by any authenticated Account.

## Considered options

- **Renaming the fictional Owner** (to "Club owner"/"Boss") so "owner" could mean the login. Rejected:
  it would churn ADR-0026, the `OwnerEvaluation` model, `owner_mood.py`, CONTEXT.md and the
  career-screen DOM ids, to buy a word we did not need.
- **A separate generic `created_by` custody axis alongside a career-seat `League.manager`.** More
  precise — a League's 20 generated AI Teams are custodied, not *managed*, in the career sense — but
  it adds two concepts in one slice. Instead, **Manager widened to "the Account this row belongs
  to"** and the career seat stays expressed as the pre-existing `League.current_team`, which
  CONTEXT.md already defines as how the Manager is identified.
- **`League.manager` only, with permission inherited by traversal.** Rejected: sandbox Teams,
  sandbox Tournaments and sandbox Matches have no League to inherit from, so a second axis would be
  needed anyway, and every check would become a join.
- **Owning `ArenaMap` too.** Rejected: `is_default`, `Season.map_mode`, the CONF-06 per-Conference
  **Map pools** and `rotate_by_matchday` all reference maps across League boundaries, so a private
  map silently breaks another Manager's rotation. A per-map `is_public` flag was the correct end
  state and was deferred rather than invented here.
- **Anonymous read-only browsing** (PLAN's original wording, and much the smaller retrofit — every
  pure-read view would have needed no change). Rejected on the maintainer's call: content is private
  until an explicit sharing feature exists.
- **A `RunPython` backfill of existing rows to the first superuser.** Rejected as *vacuous*: a custom
  user model means a new, empty user table on every database including the existing dev and Fly.io
  ones, so the migration would find no superuser and stamp nothing. Replaced by an explicit
  `claim_unmanaged` management command, which also keeps
  [ADR-0004](0004-simulation-data-is-disposable.md)'s no-backfill precedent intact.

## Consequences

- `manager IS NULL` is an **open** row (readable *and* writable by any authenticated Account), not a
  frozen one. This is what lets the existing ~1237 view-test calls pass behind a single autouse login
  fixture instead of a manager-stamping pass over 57 test files, and it keeps `score_averages` /
  `game_analysis` working. It must be tightened when a second Account first shares a deployment.
- **Manager** now means two things at once — the Account a row belongs to, and (for a League) the
  human running its **Current team**. `Team.manager` is set on *every* generated AI Team, so it is
  not the career seat; `League.current_team` is.
- Naming hazard: `Team.manager` (the new Account FK) versus `Team.managed_in_leagues` (the
  pre-existing reverse accessor of `League.current_team`) — similar words, opposite directions.
- Password **reset** is deferred with OAuth (it needs a mail provider the deploy does not have);
  only login / logout / register / password-change ship. Recovery is `manage.py changepassword`.
- League membership, invitations and joining are deferred; `League.visibility` ships **dormant** on
  the LG-02-Part2b / LG-02-Part2c-3b dormant-column precedent.
- CLAUDE.md's "three Django apps" becomes four.
- **⚠️ The `AUTH_USER_MODEL` swap breaks every database that already exists — and CI cannot see it.**
  Because `django.contrib.admin` / `auth` migrations have already been applied against `auth.User` on
  those databases, pointing `AUTH_USER_MODEL` at `accounts.User` makes the next `migrate` fail with
  `InconsistentMigrationHistory: Migration admin.0001_initial is applied before its dependency
  accounts.0001_initial`. The failure is **invisible to CI and to the test suite**, which build a
  fresh test database on every run: `pytest` goes green while the dev `db.sqlite3` and the Fly.io
  Postgres are both unusable. A passing suite is therefore not evidence that the deployment
  migrated. The accepted recovery is a **fresh database** — delete `db.sqlite3` locally,
  re-provision the Fly.io Postgres, then `migrate` and `createsuperuser` — which is the
  [ADR-0004](0004-simulation-data-is-disposable.md) disposable-data posture applied to the one
  migration that cannot be written as a no-op. A migration-history rewrite, a `RunPython` copy of the
  old `auth_user` rows, and a `--fake` shim were all rejected: they buy nothing (the rows carry no
  simulation data) at the cost of a hand-edited `django_migrations` table on a live deploy. This is
  also why "today is the last cheap moment for this swap" above is a hard claim rather than a soft
  one — the cost is already non-zero, and it only grows. Written up in `README.md` (prominently, at
  the head of *Getting Started*), the repo-root `CLAUDE.md` *Database* section, and
  `laserforce_simulator/accounts/CLAUDE.md`.
