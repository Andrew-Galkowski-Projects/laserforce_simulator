# UX-01 — User accounts and team ownership — SEAM CONTRACT

Status: **binding**. Three agents (Code / Tests / Docs) build against this document in parallel.
Every name, signature, field definition, migration filename, URL name, template path and DOM id below
is **locked**. If an agent believes a name is wrong, it stops and escalates — it does not rename.

Authorities, in order: `docs/adr/0038-accounts-and-uniform-manager-ownership.md`, `CONTEXT.md`
("Accounts and ownership" + the rewritten **Manager** entry), then this contract. `PLAN.md`'s UX-01
prose is **superseded** on two points (see §0.3).

---

## 0. Domain vocabulary (CONTEXT.md is the authority — quoted, not re-decided)

- **Account** — a registered human identity that logs in. The model is `accounts.User`.
- **Manager** — *the Account a row belongs to*. Persisted as a nullable `manager` FK on each
  Ownership root. Widened by ADR-0038 from the pre-UX-01 "implicit single local user".
- **Owner** — the **fictional boss** who judges and fires the Manager via the **Owner evaluation**
  (ADR-0026, `OwnerEvaluation`, `owner_mood.py`). **NEVER a login. NEVER renamed. NEVER touched by
  this slice.**
- **Ownership root** — a row that carries a `manager` FK directly. A row is a root *exactly when its
  parent FK is null*, except `Team` / `League` / `Tournament`, which have no parent FK and are
  therefore always roots.
- **Unmanaged row** — an Ownership root with `manager IS NULL`. Readable **and** writable by **any**
  authenticated Account, and **listed** to all of them.
- **League visibility** — a per-League `closed` / `open` marker, shipped **dormant**.

### 0.1 The single most dangerous naming hazard in this slice

| Name | What it is | Direction |
|---|---|---|
| `Team.manager` | **NEW.** FK `Team → accounts.User`. The Account that owns this Team. | Team → Account |
| `Team.managed_in_leagues` | **PRE-EXISTING.** Reverse accessor of `League.current_team`. The Leagues in which this Team is the career seat. | Team → Leagues |

They are near-homographs pointing in opposite directions at unrelated concepts. `Team.manager` is set
on **every** generated AI Team; it is **not** the career seat. The career seat is `League.current_team`.

### 0.2 The second hazard: Manager vs Owner

`manager` (the Account FK) and `OwnerEvaluation` / `owner_mood.py` / `Owner mood` / `Owner evaluation`
(the fiction) are **unrelated**. No agent may:

- rename anything containing `owner` / `Owner`;
- add `manager` to `OwnerEvaluation` (it derives via `league`);
- introduce the word "owner" for an Account in code, comments, templates, DOM ids or docs.

### 0.3 PLAN.md clauses this slice overrides

1. *"read-only access to others"* — **struck**. Cross-Account access is refused outright: another
   Account's row is neither listed nor readable. **404, never 403.**
2. *"send invitations to specific users"* / League joining — **deferred**. Only the `visibility`
   column + one create-form control ship, dormant.

---

## 1. New app: `accounts`

Created inside the nested Django project dir:
`laserforce_simulator/accounts/`. `CLAUDE.md`'s "three Django apps" becomes **four**.

### 1.1 File manifest (every file, exact path)

| Path (under `laserforce_simulator/`) | New? | Owner |
|---|---|---|
| `accounts/__init__.py` | new | Code |
| `accounts/apps.py` | new | Code |
| `accounts/models.py` | new | Code |
| `accounts/admin.py` | new | Code |
| `accounts/forms.py` | new | Code |
| `accounts/views.py` | new | Code |
| `accounts/urls.py` | new | Code |
| `accounts/permissions.py` | new | Code |
| `accounts/migrations/__init__.py` | new | Code |
| `accounts/migrations/0001_initial.py` | new | Code |
| `accounts/management/__init__.py` | new | Code |
| `accounts/management/commands/__init__.py` | new | Code |
| `accounts/management/commands/claim_unmanaged.py` | new | Code |
| `accounts/CLAUDE.md` | new | **Docs** |
| `accounts/tests/__init__.py` | new | **Tests** |
| `accounts/tests/test_user_model.py` | new | **Tests** |
| `accounts/tests/test_auth_views.py` | new | **Tests** |
| `accounts/tests/test_permissions.py` | new | **Tests** |
| `accounts/tests/test_claim_unmanaged.py` | new | **Tests** |

### 1.2 `accounts/apps.py`

```python
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
```

### 1.3 `accounts/models.py` — public names: `UserManager`, `User`

```python
from __future__ import annotations

from typing import Any

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager):
    """UX-01 — email-keyed manager for the custom `User`.

    `AbstractUser`'s default manager is keyed on `username`, which this model
    drops. Both creators take `email` as the first positional argument.
    """

    use_in_migrations = True

    def _create_user(self, email: str, password: "str | None", **extra_fields: Any) -> "User":
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(
        self, email: str, password: "str | None" = None, **extra_fields: Any
    ) -> "User":
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(
        self, email: str, password: "str | None" = None, **extra_fields: Any
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """UX-01 — the **Account**. Email-first; `username` is dropped entirely.

    NAMING: an Account becomes a row's **Manager** by way of the `manager` FK on
    each Ownership root. It is NEVER the **Owner** — that is the fictional boss
    of ADR-0026 (`OwnerEvaluation`, `owner_mood.py`), which has no login.
    """

    username = None  # type: ignore[assignment]
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()  # type: ignore[assignment]

    def __str__(self) -> str:
        return self.email
```

**Locked:** `username = None`; `email` unique; `USERNAME_FIELD = "email"`; `REQUIRED_FIELDS = []`.
No extra profile fields. No `related_name` collisions — `AbstractUser` brings `groups` /
`user_permissions` unchanged.

### 1.4 `accounts/admin.py` — public name: `UserAdmin`

Registered so an Account can be **removed** in Django Admin (the PLAN.md requirement). Because
`username` is gone, `django.contrib.auth.admin.UserAdmin`'s fieldsets cannot be reused verbatim.

```python
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """UX-01 — email-keyed admin. Account removal is the built-in delete action."""

    ordering = ("email",)
    list_display = ("email", "is_staff", "is_superuser", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
```

---

## 2. The `manager` FK — exactly five models

### 2.1 `on_delete` — **`models.SET_NULL`** (decision + justification)

> **`on_delete=models.SET_NULL`.** Deleting an Account in Admin must demote its rows to **Unmanaged**
> (still readable, still writable, still listed), never cascade a League's entire Season/Match/Event
> history out of existence — which is both the ADR-0038 "accounts are removable in Admin" requirement
> and the project's settled posture on every other user-facing FK (`League.current_team`,
> `Tournament.champion`, `Match.season`, `*.winner` are all `SET_NULL`).

`CASCADE` is explicitly rejected: one Admin delete would silently destroy simulation history.
`PROTECT` is explicitly rejected: it would make an Account undeletable, contradicting PLAN.md.

### 2.2 The field definition — **byte-identical on all five models**

Only `related_name` differs. Each declaration carries the ticket comment.

```python
    # UX-01 — the **Manager**: the Account this row belongs to (ADR-0038).
    # NULL = an **Unmanaged row**: readable AND writable by any authenticated
    # Account, and listed to all of them. SET_NULL so removing an Account in
    # Admin demotes its rows to Unmanaged rather than cascading history away.
    #
    # NAMING HAZARD: ``Team.manager`` (this FK, Team -> Account) is NOT
    # ``Team.managed_in_leagues`` (the reverse accessor of
    # ``League.current_team``, Team -> Leagues). Nor is the **Manager** the
    # **Owner** — that is the fictional boss of ADR-0026, never a login.
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="<see table>",
    )
```

| Model | File | `related_name` | Root when |
|---|---|---|---|
| `teams.Team` | `teams/models.py` | `"teams"` | always |
| `matches.League` | `matches/models.py` | `"leagues"` | always |
| `matches.Tournament` | `matches/models.py` | `"tournaments"` | always |
| `matches.Match` | `matches/models.py` | `"matches"` | `season_id IS NULL` |
| `matches.GameRound` | `matches/models.py` | `"game_rounds"` | `match_id IS NULL` |

`from django.conf import settings` must be added to `teams/models.py` (it is already imported in
`matches/models.py` — verify before adding a duplicate).

**Field placement:** append `manager` as the **last** field of each model's field block, immediately
before the model's `class Meta` / first method. Do not reorder existing fields.

### 2.3 `Tournament` is ALWAYS its own root — a resolved ambiguity

`Tournament.season_phase` may be non-null (a CONF-02 regional / CONF-04 Worlds bracket embedded in a
Season). **It is still a root and still carries `manager`.** The `season_phase` FK is *not* treated as
an ownership parent. The invariant is preserved instead by **propagation at creation**: every
embedded Tournament is stamped with its League's Manager at build time (§6.4). Consequence: a
Tournament's `manager` always equals its League's `manager` when embedded, so the flat predicate and a
traversal would agree.

### 2.4 `core.ArenaMap` is deliberately NOT an Ownership root

**No `manager` column. No queryset filtering. No permission check on any `/maps/` route.** All 14
`get_object_or_404(ArenaMap, ...)` sites in `core/views.py` are left **byte-for-byte unchanged**. Any
agent that adds a `manager` to `ArenaMap`, `MapZoneConfig`, `MapBaseConfig`, `SightLineConfig`,
`BaseSightLineConfig`, `MapCellRankingConfig` or `HeavyStrongSpotsConfig` has broken the contract.
Reason (ADR-0038): `is_default`, `Season.map_mode`, CONF-06 **Map pools** and `rotate_by_matchday`
reference maps across League boundaries.

`/maps/` routes are still behind the global login gate (§4) — authenticated, unfiltered.

### 2.5 `League.visibility` — the dormant column

```python
    # UX-01 — DORMANT this slice: nothing reads it. Ships as a column plus one
    # create-League control so the forward-compatible marker is authored from
    # day one; League membership / joining / invitations are deferred
    # (ADR-0038). Mirrors the LG-02-Part2c-3b ``SeasonPhase.tournament_mode``
    # dormant-column precedent.
    VISIBILITY_CHOICES = (
        ("closed", "Closed"),
        ("open", "Open"),
    )
    visibility = models.CharField(
        max_length=16,
        choices=VISIBILITY_CHOICES,
        default="closed",
    )
```

`VISIBILITY_CHOICES` is declared as a class-level constant on `League`, above the field block,
alongside the existing `MODE_CHOICES` / `STATE_CHOICES`.

**Dormancy is enforced:** the only permitted occurrences of the string `visibility` outside the model,
the migration, the form field, the template block and tests are **zero**. No `if league.visibility`,
no filtering, no context key, no read of any kind.

### 2.6 Migrations — exact filenames, exact dependencies

No `RunPython`, no backfill — ADR-0004 disposable-data posture, and ADR-0038 explicitly rejects a
backfill as vacuous (a custom user model means an empty user table on every existing database).

**`accounts/migrations/0001_initial.py`** — generated (`makemigrations accounts`). Must be the
`CreateModel` for `User` with `managers = [("objects", accounts.models.UserManager())]`.
Dependencies: `("auth", "0012_alter_user_first_name_max_length")`.

**`teams/migrations/0015_team_manager.py`**

```python
# UX-01 — Team.manager (the Account a Team belongs to, ADR-0038).
#
# Single AddField, NO RunPython / NO backfill (ADR-0004 disposable-data
# posture; ADR-0038 rejects a superuser backfill as vacuous — a custom user
# model means an empty user table on every existing database). Existing Teams
# stay manager=NULL, i.e. **Unmanaged rows**, until `manage.py claim_unmanaged`.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("teams", "0014_player_team_health_injury"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="teams",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
```

**`matches/migrations/0062_manager_ownership_and_league_visibility.py`** — **one** migration carrying
all five matches-app changes, in this operation order:

1. `AddField` `league.manager` (`related_name="leagues"`)
2. `AddField` `tournament.manager` (`related_name="tournaments"`)
3. `AddField` `match.manager` (`related_name="matches"`)
4. `AddField` `gameround.manager` (`related_name="game_rounds"`)
5. `AddField` `league.visibility` (`CharField(choices=[("closed","Closed"),("open","Open")], default="closed", max_length=16)`)

Dependencies:

```python
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("matches", "0061_conference_map_rotation"),
    ]
```

**Verified next numbers at contract time:** matches `0062`, teams `0015`, core **untouched** (`core`
stays at `0004_map05_cell_ranking_and_strong_spots`).

### 2.7 ⚠️ DEPLOYMENT HAZARD — `AUTH_USER_MODEL` swap on an existing database

Setting `AUTH_USER_MODEL` after `django.contrib.admin` / `auth` migrations have already been applied
against `auth.User` raises, on **existing** databases only:

```
InconsistentMigrationHistory: Migration admin.0001_initial is applied before
its dependency accounts.0001_initial
```

- **CI and the test suite are unaffected** — the test database is created fresh every run, so the
  suite passes.
- **The existing dev `db.sqlite3` and the Fly.io Postgres are affected.** The recovery path is a fresh
  database (ADR-0004: simulation data is disposable), not a data migration.
- The Code agent **must not** attempt a migration-history rewrite, a `RunPython` user copy, or a
  `--fake` shim. The **Docs** agent records this in `README.md` and in ADR-0038's Consequences.

---

## 3. `accounts/permissions.py` — the permission seam

The **entire** public surface. Names are final.

```python
"""UX-01 — Ownership resolution and the permission seam (ADR-0038).

A row is an **Ownership root** exactly when its parent FK is null; every other
row derives its **Manager** by traversing its non-null parent FK. Access is
granted when the resolved root's ``manager_id`` is the requesting Account, or
is NULL (an **Unmanaged row** — readable AND writable by any authenticated
Account). Cross-Account access raises **Http404, never 403**: another Account's
row must not be distinguishable from a nonexistent one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from django.db.models import Model, Q, QuerySet
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

_M = TypeVar("_M", bound=Model)

ROOT_MODELS: tuple[type[Model], ...]
"""The five Ownership roots, in contract order: Team, League, Tournament, Match, GameRound."""


def ownership_root(obj: Model) -> "Model | None": ...


def is_owned_by(obj: Model, user: "AbstractBaseUser | None") -> bool: ...


def get_owned_or_404(
    model: "type[_M] | QuerySet[_M]", request: HttpRequest, **lookup: object
) -> _M: ...


def owned_queryset(
    qs: QuerySet[_M], user: "AbstractBaseUser | None", *, path: str = ""
) -> QuerySet[_M]: ...


def owned_match_q(user: "AbstractBaseUser | None") -> Q: ...


def owned_game_round_q(user: "AbstractBaseUser | None") -> Q: ...


def stamp_manager(obj: _M, user: "AbstractBaseUser | None") -> _M: ...


def manager_or_none(request: HttpRequest) -> "AbstractBaseUser | None": ...
```

### 3.1 The ownership-root traversal table

Implemented as a module-private dict `_PARENT_FIELD: dict[type[Model], str]` mapping a model to the
**field name** of its ownership parent. Models absent from the dict and carrying `manager` are always
roots; models absent from the dict and *not* carrying `manager` have **no ownership axis**.

| Model | Carries `manager`? | `_PARENT_FIELD` entry | Root resolution |
|---|---|---|---|
| `teams.Team` | ✅ | *(absent)* | **self** |
| `matches.League` | ✅ | *(absent)* | **self** |
| `matches.Tournament` | ✅ | *(absent)* | **self** (even when `season_phase` is set — §2.3) |
| `matches.Match` | ✅ | `"season"` | **self** if `season_id is None`, else → `Season` |
| `matches.GameRound` | ✅ | `"match"` | **self** if `match_id is None`, else → `Match` |
| `teams.Player` | ❌ | `"team"` | → `Team` |
| `matches.Season` | ❌ | `"league"` | → `League` |
| `matches.Conference` | ❌ | `"season"` | → `Season` → `League` |
| `matches.SeasonPhase` | ❌ | `"season"` | → `Season` → `League` |
| `matches.PlayerSeasonRating` | ❌ | `"season"` | → `Season` → `League` |
| `matches.OwnerEvaluation` | ❌ | `"league"` | → `League` |
| `matches.TeamSeasonFinance` | ❌ | `"season"` | → `Season` → `League` |
| `matches.PlayerRoundState` | ❌ | `"game_round"` | → `GameRound` → (`Match` → `Season` → `League`) |
| `matches.GameEvent` | ❌ | `"game_round"` | → `GameRound` → (`Match` → `Season` → `League`) |
| `matches.TournamentParticipant` | ❌ | `"tournament"` | → `Tournament` |
| `matches.BracketNode` | ❌ | `"tournament"` | → `Tournament` |
| `matches.SeriesMatch` | ❌ | `"node"` | → `BracketNode` → `Tournament` |
| `matches.TournamentPlayerEntry` | ❌ | `"tournament"` | → `Tournament` |
| `core.ArenaMap` + all 6 map-config models | ❌ | *(absent)* | **no ownership axis** → `None` |

Notes the agents must not re-derive:

- `TeamSeasonFinance.team` and `OwnerEvaluation.team_managed` are `SET_NULL` and are **not** the
  ownership parent; the `season` / `league` FK is.
- `PlayerSeasonRating.player` is **not** the ownership parent; `season` is (the rating is League data).
- `matches.standings.StandingsRow` is a **frozen dataclass, not a model**. It is never persisted, gets
  no `manager`, and appears nowhere in this table. *(This corrects the brief.)*
- `Match.season` is `SET_NULL`: deleting a Season silently demotes its Matches to **sandbox roots**
  with `manager IS NULL`, i.e. Unmanaged. This is accepted behaviour, not a bug.

### 3.2 `ownership_root` — locked algorithm

```python
_MAX_TRAVERSAL_DEPTH = 8  # deepest real chain is GameEvent -> ... -> League (4 hops)


def ownership_root(obj: Model) -> "Model | None":
    """Return the **Ownership root** of ``obj``, or ``None`` when it has no
    ownership axis (an ArenaMap or a map-config row).

    A row is its own root when it carries ``manager`` AND either has no
    ownership parent field or that parent FK is NULL.
    """
    current: "Model | None" = obj
    for _ in range(_MAX_TRAVERSAL_DEPTH):
        if current is None:
            return None
        parent_field = _PARENT_FIELD.get(type(current))
        if _has_manager(type(current)) and (
            parent_field is None or getattr(current, f"{parent_field}_id") is None
        ):
            return current
        if parent_field is None:
            return None
        current = getattr(current, parent_field)
    return None
```

`_has_manager(model)` is module-private; it tests for a concrete `manager` field on the model.
`_MAX_TRAVERSAL_DEPTH` is a private guard against an FK cycle and is **not** a test assertion target.

### 3.3 `get_owned_or_404` — the substitution target

```python
def get_owned_or_404(
    model: "type[_M] | QuerySet[_M]", request: HttpRequest, **lookup: object
) -> _M:
    """`get_object_or_404` plus an ownership gate.

    Resolves the row, walks to its **Ownership root**, and raises ``Http404``
    unless the root's ``manager_id`` is ``request.user.id`` OR is ``None`` (an
    **Unmanaged row**).

    Returns **the resolved row**, NOT its root.

    404 — never 403 — so another Account's row is indistinguishable from one
    that does not exist.
    """
    obj = get_object_or_404(model, **lookup)
    if not is_owned_by(obj, getattr(request, "user", None)):
        raise Http404("No %s matches the given query." % obj._meta.object_name)
    return obj
```

```python
def is_owned_by(obj: Model, user: "AbstractBaseUser | None") -> bool:
    """True when ``user`` may read AND write ``obj``.

    A row with no ownership axis (ArenaMap) is always True — shared reference
    data. An **Unmanaged row** (root ``manager`` NULL) is always True.
    """
    root = ownership_root(obj)
    if root is None:
        return True
    if root.manager_id is None:
        return True
    return user is not None and getattr(user, "is_authenticated", False) and root.manager_id == user.pk
```

**Locked semantics — do not "improve":**

- Returns the **row**, not the root.
- `manager IS NULL` ⇒ **allow** (read *and* write).
- No ownership axis ⇒ **allow**.
- Cross-Account ⇒ **`Http404`**.
- The signature is `(model, request, **lookup)` — `request` is the **second positional** argument, so
  every conversion is the mechanical edit in §5.1.

### 3.4 Queryset helpers

```python
def owned_queryset(
    qs: QuerySet[_M], user: "AbstractBaseUser | None", *, path: str = ""
) -> QuerySet[_M]:
    """Filter ``qs`` to rows the Account may see: its own plus **Unmanaged rows**.

    ``path`` is the ORM lookup prefix from the queryset's model to the root that
    carries ``manager`` — ``""`` (the default) for a root model itself,
    ``"team"`` for ``Player``, ``"season__league"`` for ``Season``-scoped rows.

    NOT VALID for ``Match`` or ``GameRound`` — their roots are conditional; use
    ``owned_match_q`` / ``owned_game_round_q`` instead.
    """
    prefix = f"{path}__" if path else ""
    return qs.filter(
        Q(**{f"{prefix}manager": user}) | Q(**{f"{prefix}manager__isnull": True})
    )
```

The two conditional roots get explicit, longhand `Q` builders — deliberately verbose so the branch
structure is auditable:

```python
def owned_match_q(user: "AbstractBaseUser | None") -> Q:
    """Predicate for `Match`: flat on a sandbox Match, traversed for a Season Match."""
    return (
        Q(season__isnull=True)
        & (Q(manager=user) | Q(manager__isnull=True))
    ) | (
        Q(season__isnull=False)
        & (Q(season__league__manager=user) | Q(season__league__manager__isnull=True))
    )


def owned_game_round_q(user: "AbstractBaseUser | None") -> Q:
    """Predicate for `GameRound`: flat when standalone, else through its Match."""
    return (
        Q(match__isnull=True)
        & (Q(manager=user) | Q(manager__isnull=True))
    ) | (
        Q(match__isnull=False)
        & Q(match__season__isnull=True)
        & (Q(match__manager=user) | Q(match__manager__isnull=True))
    ) | (
        Q(match__isnull=False)
        & Q(match__season__isnull=False)
        & (
            Q(match__season__league__manager=user)
            | Q(match__season__league__manager__isnull=True)
        )
    )
```

### 3.5 Stamping helpers

```python
def stamp_manager(obj: _M, user: "AbstractBaseUser | None") -> _M:
    """Set ``obj.manager`` to ``user`` and persist just that column.

    Used where the row is created by code that has no ``request`` — the
    `BatchSimulator` return values in the sandbox create views. An
    unauthenticated ``user`` leaves the row **Unmanaged**. Returns ``obj``.
    """
    obj.manager = user if (user is not None and getattr(user, "is_authenticated", False)) else None
    obj.save(update_fields=["manager"])
    return obj


def manager_or_none(request: HttpRequest) -> "AbstractBaseUser | None":
    """``request.user`` when authenticated, else ``None`` (an **Unmanaged row**)."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None
```

---

## 4. Settings and the global login gate

### 4.1 `laserforce_simulator/laserforce_simulator/settings.py` — every change

| Key | Change | Value |
|---|---|---|
| `INSTALLED_APPS` | **insert** `"accounts.apps.AccountsConfig",` immediately **before** `"teams.apps.TeamsConfig",` | — |
| `MIDDLEWARE` | **insert** one entry immediately **after** `"django.contrib.auth.middleware.AuthenticationMiddleware",` | `"django.contrib.auth.middleware.LoginRequiredMiddleware",` |
| `AUTH_USER_MODEL` | **new** | `"accounts.User"` |
| `LOGIN_URL` | **new** | `"login"` |
| `LOGIN_REDIRECT_URL` | **new** | `"landing"` |
| `LOGOUT_REDIRECT_URL` | **new** | `"login"` |
| `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` | **change** | `["rest_framework.permissions.IsAuthenticated"]` (was `AllowAny`) |

`AUTH_USER_MODEL` / `LOGIN_URL` / `LOGIN_REDIRECT_URL` / `LOGOUT_REDIRECT_URL` go in a new block
placed immediately **after** the existing `AUTH_PASSWORD_VALIDATORS` list, headed:

```python
# --- UX-01: Accounts (ADR-0038) ---
```

The three redirect settings use **URL names**, not paths (Django resolves both; names survive a mount
change). `"landing"` is the existing root URL name.

### 4.2 The exemption list — `@login_not_required` on exactly these

Verified against Django 5.2 docs: `LoginRequiredMiddleware` must sit **after** `AuthenticationMiddleware`,
and `process_view` receives the **resolved** view — so **`include()` cannot be wrapped**. Two forms:

- **Function views** → the plain decorator.
- **Class-based views** (`LoginView`, `LogoutView`, `PasswordChangeView`, `PasswordChangeDoneView`,
  and the four DRF ViewSets) → either `login_not_required(SomeView.as_view(...))` at the URLconf, or
  `@method_decorator(login_not_required, name="dispatch")` on the class. Both work because
  `View.as_view()` copies `dispatch.__dict__` onto the returned callable.

| Surface | Where | Form |
|---|---|---|
| login | `accounts/urls.py` | `login_not_required(LoginView.as_view(...))` |
| logout | `accounts/urls.py` | `login_not_required(LogoutView.as_view())` |
| register | `accounts/views.py::register` | `@login_not_required` decorator |
| password change | `accounts/urls.py` | `login_not_required(PasswordChangeView.as_view(...))` |
| password change done | `accounts/urls.py` | `login_not_required(PasswordChangeDoneView.as_view(...))` |
| `TeamViewSet` | `teams/api_views.py` | `@method_decorator(login_not_required, name="dispatch")` |
| `PlayerViewSet` | `teams/api_views.py` | `@method_decorator(login_not_required, name="dispatch")` |
| `MatchViewSet` | `matches/api_views.py` | `@method_decorator(login_not_required, name="dispatch")` |
| `GameRoundViewSet` | `matches/api_views.py` | `@method_decorator(login_not_required, name="dispatch")` |
| `SimulateBatchAPIView` | `matches/api_views.py` | `@method_decorator(login_not_required, name="dispatch")` |
| `SimulateBatchStatusAPIView` | `matches/api_views.py` | `@method_decorator(login_not_required, name="dispatch")` |

**Nothing else is exempt.** Every other view in the project — including all of `core/views.py` and the
`/maps/` routes — is gated by the middleware with no per-view decorator.

Why the API is exempt from the *middleware* but not from auth: DRF's `IsAuthenticated` then returns a
**403 JSON** body instead of the middleware's HTML **302** to the login page, which is what an API
client needs. Logging out of the browser session still locks the API.

`password_change_done` is exempt alongside `password_change` purely so the redirect target cannot
bounce; both are only reachable post-login in practice.

---

## 5. Converting the `get_object_or_404` call sites

### 5.1 The mechanical edit

```python
# before
thing = get_object_or_404(Model, pk=thing_id)
# after
thing = get_owned_or_404(Model, request, pk=thing_id)
```

Insert `request` as the **second positional argument**; change the function name. Lookup kwargs,
variable names, and everything else stay byte-identical. Import in each touched module:

```python
from accounts.permissions import get_owned_or_404
```

Remove the now-unused `get_object_or_404` import **only if** no other site in that module still uses it.

### 5.2 Verified inventory — **97 total, 14 ArenaMap left alone, 83 converted**

| File | Total | Convert | Leave |
|---|---|---|---|
| `matches/league_views.py` | 28 | **28** | 0 |
| `matches/league_screens/*.py` (15 modules) | 17 | **17** | 0 |
| `matches/views.py` | 14 | **14** | 0 |
| `core/views.py` | 14 | 0 | **14 (all ArenaMap)** |
| `matches/tournament_views.py` | 13 | **13** | 0 |
| `teams/views.py` | 11 | **11** | 0 |
| **Total** | **97** | **83** | **14** |

There are **zero** `get_list_or_404` calls in the project.

**Per-file detail (line numbers at contract time; match on the call, not the line):**

- `teams/views.py` — 66 `Team`, 100 `Team`, 120 `Team`, 190 `Team`, 191 `Player`, 205 `Team`,
  234 `Team`, 235 `Player`, 261 `Team`, 262 `Player`, 605 `Player`.
  The three paired `Player, id=player_id, team=team` lookups keep **both** kwargs:
  `get_owned_or_404(Player, request, id=player_id, team=team)`.
- `matches/views.py` — 217 + 218 `GameRound`, 290 `Match`, 320 `GameRound`, 498 `Team`,
  1025 / 1064 `GameRound`, 1103 / 1207 `GameRound`, 1580 + 1581 `Team`,
  1902 + 1903 `Player`, 2111 `League`.
- `matches/league_views.py` — 28 sites: `Season` ×20 (238, 285, 657, 818, 3151, 3209, 3236, 3367,
  3389, 3420, 3487, 3533, 3556, 3609, 3652, 3684, 3843, 3877, 3990, 5128), `League` ×7 (3031, 3115,
  4210, 4489, 5089, 5206, 5250), `Team` ×1 (4211). 20 + 7 + 1 = 28.
- `matches/tournament_views.py` — 13 sites, **all** `Tournament, pk=tournament_id`
  (713, 725, 766, 781, 807, 868, 937, 971, 995, 1055, 1106, 1127, 1188). Line 937's bare call in
  `tournament_play_status` is converted too, return value still discarded.
- `matches/league_screens/` — 17 sites across 15 modules, all `League, pk=league_id` except
  `player_detail.py:80` (`Player, pk=player_id`). Two modules have two sites:
  `player_detail.py` (79, 80) and `watch_list.py` (66, 115).

### 5.3 Adjacent `.objects.get(` sites — **converted too**

These are the same seam with a different exception type. Wrap the ownership check explicitly:

| File | Line | Function | Change |
|---|---|---|---|
| `matches/views.py` | 237 | `compare_rounds` | `Team.objects.get(id=team_id)` → `get_owned_or_404(Team, request, id=team_id)` |
| `matches/league_views.py` | 5273 | `reassign_team` | `Team.objects.get(pk=team_id)` → `get_owned_or_404(Team, request, pk=team_id)` |

`matches/api_views.py:92` (`Team.objects.get(id=tid)` inside `SimulateBatchAPIView.post`, wrapped in
`try/except Team.DoesNotExist` → 400) is **left alone** — it must keep returning 400, not 404. The
viewset-level `IsAuthenticated` is the gate there.

`get_or_create` sites (`core/views.py:534`, `teams/views.py:1075`, `matches/league_views.py:4761`,
`4924`) are **out of scope** — they create derived or shared rows.

---

## 6. Stamping sites — where `manager` is set

Every site below sets the Manager **at creation** from `request.user`, via `manager_or_none(request)`.

### 6.1 Changed signatures

```python
# teams/views.py — gains ONE keyword-only parameter, appended last, default None.
def _generate_teams(
    num_teams: int,
    players_per_team: int,
    *,
    rng: random.Random,
    mean: int,
    std_dev: int,
    team_names_pool: list[str],
    player_names_pool: list[str],
    tier_means: "list[float] | None" = None,
    manager: "AbstractBaseUser | None" = None,
) -> list[Team]:
```

Body change: `Team.objects.create(name=team_name)` → `Team.objects.create(name=team_name, manager=manager)`.
Player creation is untouched (Players derive from their Team).

```python
# matches/league_views.py — gains ONE keyword-only parameter, appended last, default None.
@transaction.atomic
def _create_league_and_season(
    form: CreateLeagueForm, *, manager: "AbstractBaseUser | None" = None
) -> Season:
```

This helper currently takes **no `request`** — the parameter is how the Account reaches it.

Type import in both modules, under `TYPE_CHECKING`, to avoid importing `accounts` into `teams`:

```python
if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
```

### 6.2 `_generate_teams` callers — all three pass `manager`

| File | Line | Caller | Passes |
|---|---|---|---|
| `teams/views.py` | 948 | `generate_players` (view) | `manager=manager_or_none(request)` |
| `matches/league_views.py` | 1373 | `_create_league_and_season` (helper) | `manager=manager` (its own new param) |
| `matches/tournament_views.py` | 201 | `tournament_create` (view) | `manager=manager_or_none(request)` |

### 6.3 `_generate_free_agents` — **NO `manager` parameter** *(deviation from the brief, §11)*

Verified: `_generate_free_agents` creates **no Team**. It bulk-creates `Player` rows onto a Team it is
handed (`team=pool_team`), or — when `team is None` — onto the **shared global singleton** returned by
`teams.models.get_free_agents_team()` (the magic-named `"Free Agents"` LG-00 sandbox pool).

That singleton must stay **Unmanaged**: it is a cross-Account shared pool, and stamping it would let
the first Account to hit `generate_players` with `num_teams == 0` capture it permanently and 404 it for
everyone else. Its Players are derived rows on an Unmanaged Team, so they stay reachable by all.

**Locked:** `_generate_free_agents` keeps its current signature verbatim. `get_free_agents_team()`
keeps its current signature verbatim. Neither is stamped.

### 6.4 The full stamping table

| # | File | Function | Row created | Stamp |
|---|---|---|---|---|
| 1 | `teams/views.py:87` | `team_create` | `Team` (via `TeamForm`) | `team = form.save(commit=False)` → `team.manager = manager_or_none(request)` → `team.save()` |
| 2 | `teams/views.py:839` | `_generate_teams` | `Team` | `manager=manager` (§6.1) |
| 3 | `matches/league_views.py:1384` | `_create_league_and_season` | `League` | `manager=manager` in `League.objects.create(...)` |
| 4 | `matches/league_views.py:1396` | `_create_league_and_season` | `Team` (the League's free-agent pool Team) | `manager=manager` in `Team.objects.create(...)` |
| 5 | `matches/league_views.py:3577` | `member_night_setup` (view) | `Team` (`team_a`, `is_draw_team=True`) | `manager=manager_or_none(request)` |
| 6 | `matches/league_views.py:3581` | `member_night_setup` (view) | `Team` (`team_b`, `is_draw_team=True`) | `manager=manager_or_none(request)` |
| 7 | `matches/tournament_views.py:273` | `tournament_create` (view) | `Tournament` | `manager=manager_or_none(request)` |
| 8 | `matches/tournament_views.py:1160` | `tournament_draw` (view) | `Team` (`is_draw_team=True`) | `manager=manager_or_none(request)` |
| 9 | `matches/views.py:400` | `create_match` (view) | `Match` (returned by `BatchSimulator.simulate_match`) | `stamp_manager(match, request.user)` immediately after the `try/except` |
| 10 | `matches/views.py:471` | `create_single_round` (view) | `GameRound` (returned by `simulate_single_round_detailed`) | `stamp_manager(game_round, request.user)` immediately after the `try/except` |
| 11 | `matches/models.py:1535` | `Season._build_tournament_for_phase` | `Tournament` (embedded) | `manager=self.league.manager` |
| 12 | `matches/models.py:1624` | `Season._build_last_chance_tournament` | `Tournament` (embedded) | `manager=self.league.manager` |
| 13 | `matches/models.py:2052` | `Season.build_pending_worlds_bracket` | `Tournament` (embedded) | `manager=self.league.manager` |

Sites 9 and 10 are `stamp_manager` (post-hoc) because the sandbox create views **never construct the
row themselves** — they delegate to `BatchSimulator` and receive the persisted object. Do **not**
thread a `manager` parameter through the simulator.

Sites 11–13 are the §2.3 propagation: an embedded Tournament inherits its League's Manager. `self` is a
`Season`, and `Season.league` is non-null.

### 6.5 Explicitly OUT OF SCOPE — creation sites that stay Unmanaged

| File | Line | Function | Row | Why |
|---|---|---|---|---|
| `matches/simulation/entrypoints.py` | 671 | `BatchSimulator.simulate_match` | `Match` | Runs in worker processes with no request. Sandbox callers stamp post-hoc (§6.4 #9); batch/API callers leave the row Unmanaged, which is fully accessible per §0. |
| `matches/simulation/entrypoints.py` | 928 | `BatchSimulator.simulate_scheduled_round` | `Match` | Season Match — derives from `season.league`. |
| `matches/simulation/persistence.py` | 349 | `flush_to_db` | `GameRound` | Derives from its `Match`. |
| `matches/league_views.py` | 3585 | `member_night_setup` | `Match` | Has `season=season` → derived, not a root. |
| `matches/league_views.py` | 1423, 4978 | `_create_league_and_season`, `_run_season_rollover` | `Season` | Derived (→ League). |
| `matches/league_views.py` | 1445, 5035 | ditto | `SeasonPhase` | Derived (→ Season → League). |
| `matches/league_views.py` | 722, 1501 | `manage_conferences`, `_create_league_and_season` | `Conference` | Derived (→ Season → League). |
| `matches/models.py` | 1985 | `Season._ensure_worlds_phase` | `SeasonPhase` | Derived. |
| `matches/tournament_engine.py` | 166 | `play_specific_node` | `SeriesMatch` | Derived (→ BracketNode → Tournament). |
| `teams/views.py` | 1075 | `_apply_roster` | `Team` (`get_or_create`) | Roster-import merge onto possibly-existing Teams; deferred. Rows stay Unmanaged. |
| `teams/views.py` | 896 | `_generate_free_agents` | `Player` | Derived (→ Team). §6.3. |

---

## 7. Root list querysets

All four root list views filter to `manager=user OR manager IS NULL`.

| View | File | URL name | Before → After |
|---|---|---|---|
| `team_list` | `teams/views.py:54` | `team_list` | `Team.objects.regular().prefetch_related("players")` → `owned_queryset(Team.objects.regular().prefetch_related("players"), request.user)` |
| `league_list` | `matches/league_views.py:934` | `league_list` | Both `League.objects.filter(state=...)` calls wrapped: `list(owned_queryset(League.objects.filter(state="active"), request.user).order_by("-id"))` and the same for `"archived"` |
| `tournament_list` | `matches/tournament_views.py:105` | `tournament_list` | `Tournament.objects.order_by("-id")` → `owned_queryset(Tournament.objects.all(), request.user).order_by("-id")` |
| `match_list` | `matches/views.py:265` | `match_list` | `matches` → `Match.objects.filter(owned_match_q(request.user)).select_related(...).order_by("-date_played")`; `detailed_rounds` → `GameRound.objects.filter(match__isnull=True).filter(owned_game_round_q(request.user)).select_related(...).order_by("-date_played")` |

`player_list` (`teams/views.py:1242`, url name `player_list`) is **also** scoped, since Players derive
from Teams: `owned_queryset(Player.objects.select_related("team"), request.user, path="team")` applied
**before** the existing `.annotate(...)` / `.order_by(...)` / pagination chain.

`map_list` (`core/views.py`, url name `map_list`) is **NOT** filtered — §2.4.

---

## 8. DRF

### 8.1 Settings

`REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = ["rest_framework.permissions.IsAuthenticated"]`.
`DEFAULT_AUTHENTICATION_CLASSES` (SessionAuthentication), pagination and `PAGE_SIZE` are unchanged.

### 8.2 Viewsets — decorator + manager-scoped queryset

All four gain `@method_decorator(login_not_required, name="dispatch")` (§4.2) and a `get_queryset`
override. The class-level `queryset` attribute **stays** on every viewset — the DRF router needs it for
basename/model introspection.

```python
# teams/api_views.py
class TeamViewSet(ReadOnlyModelViewSet):
    queryset = Team.objects.prefetch_related("players").order_by("name")

    def get_queryset(self):
        return owned_queryset(
            Team.objects.prefetch_related("players").order_by("name"), self.request.user
        )


class PlayerViewSet(ReadOnlyModelViewSet):
    queryset = Player.objects.select_related("team").order_by("team__name", "name")
    serializer_class = PlayerSerializer

    def get_queryset(self):
        return owned_queryset(
            Player.objects.select_related("team").order_by("team__name", "name"),
            self.request.user,
            path="team",
        )
```

```python
# matches/api_views.py
class MatchViewSet(ReadOnlyModelViewSet):
    queryset = Match.objects.select_related("team_red", "team_blue", "winner").order_by("-date_played")
    serializer_class = MatchSerializer

    def get_queryset(self):
        return (
            Match.objects.filter(owned_match_q(self.request.user))
            .select_related("team_red", "team_blue", "winner")
            .order_by("-date_played")
        )
```

`GameRoundViewSet.get_queryset()` keeps its existing `self.action == "retrieve"` prefetch branch and
its `@action(detail=True, url_path="events")` method unchanged; only the base queryset is wrapped:

```python
        qs = (
            GameRound.objects.filter(owned_game_round_q(self.request.user))
            .select_related("match", "team_red", "team_blue", "winner")
            .order_by("-date_played")
        )
```

`TeamViewSet.get_serializer_class()` is unchanged.

Router registration in `laserforce_simulator/api_urls.py` is **unchanged** — no basenames move, no
URL names change (`team-list`, `team-detail`, `player-list`, `player-detail`, `match-list`,
`match-detail`, `gameround-list`, `gameround-detail`, `gameround-events`, `api_simulate_batch`,
`api_simulate_batch_status`).

---

## 9. Auth surfaces

House style, verified against the codebase: **Bootstrap 5.3 from CDN, no project stylesheet**;
templates live **only** under `laserforce_simulator/templates/` (no app-level template dirs);
**no URL namespaces anywhere** — every name is flat; every form field declares its DOM id explicitly
in the widget `attrs`; labels are hand-written with `for=` **hardcoded to that id**.

### 9.1 URLs — `accounts/urls.py`, mounted at `/accounts/`

Root URLconf gains **one** line in `laserforce_simulator/laserforce_simulator/urls.py`, placed
immediately **after** the `path("admin/", ...)` entry:

```python
    path("accounts/", include("accounts.urls")),
```

| URL name | Path | View |
|---|---|---|
| `login` | `/accounts/login/` | `django.contrib.auth.views.LoginView` |
| `logout` | `/accounts/logout/` | `django.contrib.auth.views.LogoutView` |
| `register` | `/accounts/register/` | `accounts.views.register` |
| `password_change` | `/accounts/password-change/` | `django.contrib.auth.views.PasswordChangeView` |
| `password_change_done` | `/accounts/password-change/done/` | `django.contrib.auth.views.PasswordChangeDoneView` |

**No `app_name`** — matching the project-wide convention. These five names are Django's own defaults,
so `LOGIN_URL`, `PasswordChangeView.success_url` and `{% url %}` all resolve with no extra config.

**NO password reset.** `password_reset`, `password_reset_done`, `password_reset_confirm`,
`password_reset_complete` are **not** defined, not routed, not templated, not linked. Deferred with
OAuth (no mail provider). Recovery is `manage.py changepassword`.

### 9.2 Forms — `accounts/forms.py`

Three classes. Names locked: **`EmailAuthenticationForm`**, **`RegisterForm`**,
**`StyledPasswordChangeForm`**.

```python
class EmailAuthenticationForm(AuthenticationForm):
    """UX-01 — `AuthenticationForm` with the house DOM ids and an email input.

    Django keeps the field NAME ``username`` even when ``USERNAME_FIELD`` is
    ``email``; only the widget and label change here. The POST key is
    ``username`` — tests must post ``{"username": <email>, "password": ...}``.
    """
```

- `username` field: label `"Email"`, widget `forms.EmailInput(attrs={"id": "login-email", "class": "form-control", "autocomplete": "email", "autofocus": True})`
- `password` field: widget `forms.PasswordInput(attrs={"id": "login-password", "class": "form-control", "autocomplete": "current-password"})`

> ⚠️ **Locked and non-obvious:** the login POST key is **`username`**, carrying an email value.

```python
class RegisterForm(UserCreationForm):
    """UX-01 — open self-registration: email + password + confirm."""

    class Meta:
        model = User
        fields = ("email",)
```

- `email`: widget `forms.EmailInput(attrs={"id": "register-email", "class": "form-control", "autocomplete": "email"})`, label `"Email"`
- `password1`: `forms.PasswordInput(attrs={"id": "register-password1", "class": "form-control", "autocomplete": "new-password"})`, label `"Password"`
- `password2`: `forms.PasswordInput(attrs={"id": "register-password2", "class": "form-control", "autocomplete": "new-password"})`, label `"Confirm password"`

`UserCreationForm` supplies password matching and runs `AUTH_PASSWORD_VALIDATORS`; email uniqueness
comes from the model's `unique=True`.

```python
class StyledPasswordChangeForm(PasswordChangeForm):
    """UX-01 — `PasswordChangeForm` with the house DOM ids."""
```

- `old_password`: id `password-change-old-password`, `autocomplete="current-password"`
- `new_password1`: id `password-change-new-password1`, `autocomplete="new-password"`
- `new_password2`: id `password-change-new-password2`, `autocomplete="new-password"`

All three classes use `class="form-control"` on every widget.

### 9.3 Views — `accounts/views.py`

One function view; the other three surfaces are Django's CBVs wired in `accounts/urls.py`.

```python
@login_not_required
def register(request: HttpRequest) -> HttpResponse:
    """UX-01 — open self-registration. On success, log the new Account in and
    redirect to ``LOGIN_REDIRECT_URL``.
    """
```

- GET → render `accounts/register.html` with `{"form": RegisterForm()}`
- POST valid → `user = form.save()`, then `django.contrib.auth.login(request, user)`, then
  `redirect("landing")`
- POST invalid → re-render `accounts/register.html` with the bound form, **status 200**

CBV wiring in `accounts/urls.py`:

- `LoginView.as_view(template_name="accounts/login.html", authentication_form=EmailAuthenticationForm, redirect_authenticated_user=True)`
- `LogoutView.as_view()` — **POST only** in Django 5.x (GET logout was removed); the nav control is a form, not a link
- `PasswordChangeView.as_view(template_name="accounts/password_change.html", form_class=StyledPasswordChangeForm, success_url=reverse_lazy("password_change_done"))`
- `PasswordChangeDoneView.as_view(template_name="accounts/password_change_done.html")`

### 9.4 Templates — four new files

| Path (under `laserforce_simulator/templates/`) | Rendered by |
|---|---|
| `accounts/login.html` | `LoginView` |
| `accounts/register.html` | `accounts.views.register` |
| `accounts/password_change.html` | `PasswordChangeView` |
| `accounts/password_change_done.html` | `PasswordChangeDoneView` |
| `_partials/topnav_auth.html` | included by `base.html` |

All extend `base.html`, use `{% block title %}` + `{% block content %}`, and follow the
`leagues/create_advanced.html` block shape verbatim:

```html
        <div class="mb-3">
            <label for="<hardcoded-widget-id>" class="form-label">Human Label</label>
            {{ form.<field> }}
            <div class="form-text">…optional help copy…</div>
            {{ form.<field>.errors }}
        </div>
```

`<h1>` headings carry **no id** (house rule). Each form ends with
`<button type="submit" id="<prefix>-submit" class="btn btn-primary">…</button>`.

### 9.5 Locked DOM ids

**`accounts/login.html`**

| id | Element |
|---|---|
| `login-form` | `<form method="post">` |
| `login-email` | email input (widget attr) |
| `login-password` | password input (widget attr) |
| `login-submit` | submit button, text `Sign in` |
| `login-errors` | `<div class="alert alert-danger">` wrapping `{{ form.non_field_errors }}` |
| `login-register-link` | `<a href="{% url 'register' %}">Create an account</a>` |

**`accounts/register.html`**

| id | Element |
|---|---|
| `register-form` | `<form method="post">` |
| `register-email` | email input |
| `register-password1` | password input |
| `register-password2` | confirm input |
| `register-submit` | submit button, text `Create account` |
| `register-errors` | non-field error container |
| `register-login-link` | `<a href="{% url 'login' %}">Already have an account? Sign in</a>` |

**`accounts/password_change.html`**

| id | Element |
|---|---|
| `password-change-form` | `<form method="post">` |
| `password-change-old-password` | input |
| `password-change-new-password1` | input |
| `password-change-new-password2` | input |
| `password-change-submit` | submit button, text `Change password` |
| `password-change-errors` | non-field error container |

**`accounts/password_change_done.html`**

| id | Element |
|---|---|
| `password-change-done-notice` | `<div class="alert alert-success">Your password has been changed.</div>` |
| `password-change-done-home-link` | `<a href="{% url 'landing' %}">Back to home</a>` |

### 9.6 The top-nav auth control

New partial `templates/_partials/topnav_auth.html`, included from `base.html` **once**, placed
immediately **after** the closing `{% endif %}` of the `app_mode` branch block and **before** the
closing `</div>` of `<div class="navbar-nav ms-auto">` — so it renders in all three modes and sits at
the far right.

`base.html` gains exactly **one** line:

```html
                    {% include "_partials/topnav_auth.html" %}
```

Markup follows the `topnav_tools_help.html` dropdown shape (`<div class="nav-item dropdown">` →
`<a class="nav-link dropdown-toggle" id="…-nav-link" … >Label ▾</a>` → `<ul class="dropdown-menu"
aria-labelledby="…-nav-link">` → `<li><a class="dropdown-item" id="…">`).

| id | Branch | Element |
|---|---|---|
| `account-nav-link` | authenticated | dropdown toggle, text `{{ user.email }} ▾` |
| `account-signed-in-as` | authenticated | `<li><span class="dropdown-item-text" id="account-signed-in-as">Signed in as {{ user.email }}</span></li>` |
| `account-password-change-link` | authenticated | `<a class="dropdown-item" href="{% url 'password_change' %}">Change password</a>` |
| `account-sign-out-form` | authenticated | `<form method="post" action="{% url 'logout' %}">` with `{% csrf_token %}` |
| `account-sign-out-button` | authenticated | `<button type="submit" class="dropdown-item">Sign out</button>` |
| `account-sign-in-link` | anonymous | `<a class="nav-link" href="{% url 'login' %}">Sign in</a>` |
| `account-register-link` | anonymous | `<a class="nav-link" href="{% url 'register' %}">Register</a>` |

Branch on `{% if user.is_authenticated %}` … `{% else %}` … `{% endif %}`.
`django.contrib.auth.context_processors.auth` is **already** in `TEMPLATES.OPTIONS.context_processors`
— no settings change is needed for `{{ user }}`.

> ⚠️ **Sign-out is a POST form, not a link.** Django 5.x `LogoutView` rejects GET. Any agent writing
> `<a href="{% url 'logout' %}">` has broken the contract.

---

## 10. Management command — `claim_unmanaged`

**Module path:** `laserforce_simulator/accounts/management/commands/claim_unmanaged.py`
**Invocation:** `python laserforce_simulator/manage.py claim_unmanaged --user <email>`

```python
class Command(BaseCommand):
    help = "Stamp every Unmanaged row (manager IS NULL) on all five Ownership roots to one Account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            dest="user",
            required=True,
            help="Email address of the Account to claim the rows for.",
        )
```

**Behaviour, locked:**

- Resolves `User.objects.get(email=<value>)`; on `User.DoesNotExist` raises
  `CommandError(f"No account with email {email!r}.")`.
- Iterates the five root models **in this exact order**: `Team`, `League`, `Tournament`, `Match`,
  `GameRound`.
- For each: `count = Model.objects.filter(manager__isnull=True).update(manager=user)`.
- Wrapped in a single `transaction.atomic()`.
- **Idempotent** — a second run matches nothing and reports `0` for every model.
- Uses `.update()`, so no `save()` signals fire and `auto_now` columns are untouched.

**Output shape, locked** (six lines, written to `self.stdout`):

```
Team: 12 claimed
League: 3 claimed
Tournament: 1 claimed
Match: 40 claimed
GameRound: 85 claimed
Total: 141 rows claimed by manager@example.com
```

The final line is wrapped in `self.style.SUCCESS(...)`; the five per-model lines are unstyled. A
second run prints the same six lines with every number `0`.

---

## 11. Tests seam

### 11.1 The autouse login fixture — `laserforce_simulator/conftest.py`

The root `conftest.py` **already exists** and defines a Celery `pytest_configure`. The fixture is
**appended**; the existing docstring, `os.environ.setdefault` and `pytest_configure` are preserved
verbatim.

Two public names, locked:

```python
SHARED_MANAGER_EMAIL = "test-manager@example.com"


def get_shared_manager():
    """Return (creating on first call) the shared test **Account**.

    Idempotent within a test's transaction. Used by the autouse fixture and
    available to any test that needs the Account object itself.
    """


@pytest.fixture(autouse=True)
def force_login_shared_manager(request):
    """UX-01 — log every DB-backed test in as the shared Account.

    `LoginRequiredMiddleware` gates every view, so ~1237 pre-existing view-test
    calls would otherwise 302. Rows those tests create stay **Unmanaged**
    (manager NULL), which is readable AND writable by any Account — so no
    per-test manager stamping is needed.
    """
```

**Locked behaviour of the fixture:**

1. If the test has **no database access**, do nothing and yield immediately. Detected as: no
   `django_db` marker (`request.node.get_closest_marker("django_db")`) **and** `request.instance` is
   not a `django.test.TestCase` subclass instance. Never call `request.getfixturevalue("db")`
   unconditionally — that would force a database on pure unit tests.
2. Django `TestCase` subclasses: reach the client via **`request.instance.client`** and call
   `client.force_login(get_shared_manager())`.
3. Plain-pytest tests: `request.getfixturevalue("client").force_login(get_shared_manager())`.
4. `force_login` (not `client.login`) — no password round-trip, no hasher cost.

### 11.2 ⚠️ The fixture CANNOT reach locally-instantiated clients

Verified: **73 call sites across exactly 5 files** build their own `Client()` / `APIClient()` inside
the test body rather than using `self.client` or the pytest `client` fixture. The autouse fixture has
no handle on those objects, so they will 302.

| File | Local client instantiations |
|---|---|
| `matches/tests/views_tests.py` | 42 |
| `matches/tests/test_batch_views.py` | 15 |
| `matches/tests/test_heatmap.py` | 10 |
| `matches/tests/test_missile_log.py` | 4 |
| `matches/tests/test_playback_map.py` | 2 |

The **Tests** agent owns the fix: after each local instantiation, add
`client.force_login(get_shared_manager())` (or, for the `Client().get(url)` one-liners in
`test_heatmap.py`, hoist to a named local first). No production code changes for this.

`matches/tests/test_apis.py` assigns `self.client = APIClient()` in `setUp`, which **overwrites** the
`TestCase` client *after* the fixture ran — it belongs to the same bucket and needs the same treatment.

### 11.3 Test boundary

**Tests MAY assert against (public seam):**

- Every URL name, path, template path and DOM id in §9.
- HTTP status codes and redirect targets; **404, never 403**, for a row owned by another Account.
- Anonymous access to a gated view → 302 to `LOGIN_URL`; anonymous API access → **403 JSON**.
- `accounts.User`: `USERNAME_FIELD`, `REQUIRED_FIELDS`, `username is None`, email uniqueness,
  `UserManager.create_user` / `create_superuser` behaviour and their `ValueError`s.
- Presence, nullability and `SET_NULL` behaviour of `manager` on all five roots; the `related_name`s.
- `League.visibility` default `"closed"`, its choices, the `league-create-visibility` control's
  presence, and — a **dormancy** assertion — that no view context or rendered page varies with it.
- Public functions in `accounts/permissions.py`: `ownership_root`, `is_owned_by`, `get_owned_or_404`,
  `owned_queryset`, `owned_match_q`, `owned_game_round_q`, `stamp_manager`, `manager_or_none`,
  `ROOT_MODELS`.
- **Unmanaged-row semantics**: a `manager IS NULL` root is listed, readable **and** writable by any
  authenticated Account.
- Every stamping site in §6.4 — assert the created row's `manager_id`.
- Changed signatures: `_generate_teams(..., manager=...)` and
  `_create_league_and_season(form, manager=...)` accept the kwarg and stamp correctly; both still work
  with the kwarg **omitted** (defaults `None` ⇒ Unmanaged).
- `claim_unmanaged`: the six output lines, the counts, `CommandError` on an unknown email, and
  idempotency across two runs.

**Tests MUST NOT assert against (internal):**

- The `_PARENT_FIELD` dict literal, `_MAX_TRAVERSAL_DEPTH`, `_has_manager` — assert traversal
  *behaviour* through `ownership_root`, never the table itself.
- The `Q`-tree shape returned by `owned_match_q` / `owned_game_round_q` — assert the **rows** returned.
- Query counts, SQL text, or `str(queryset.query)`.
- Whether a stamping site uses `Model.objects.create(manager=…)` or `stamp_manager(...)` — assert the
  persisted `manager_id`.
- The order of `AddField` operations inside a migration.
- `UserAdmin` fieldset contents.
- Bootstrap class names on auth templates (ids are locked; classes are not).

### 11.4 Determinism

No RNG, parallelism or ordering behaviour changes in this slice, so no serial-vs-parallel determinism
test is required. `pytest.ini`'s `-n auto --dist worksteal` is unchanged. The autouse fixture must be
worker-safe: `get_shared_manager()` uses `get_or_create` keyed on `SHARED_MANAGER_EMAIL`, inside the
test's own transaction, so parallel workers never collide.

---

## 12. File ownership — no overlap

Any file appearing in two columns is a contract bug; escalate rather than both editing.

### 12.1 CODE agent

```
laserforce_simulator/accounts/__init__.py
laserforce_simulator/accounts/apps.py
laserforce_simulator/accounts/models.py
laserforce_simulator/accounts/admin.py
laserforce_simulator/accounts/forms.py
laserforce_simulator/accounts/views.py
laserforce_simulator/accounts/urls.py
laserforce_simulator/accounts/permissions.py
laserforce_simulator/accounts/migrations/__init__.py
laserforce_simulator/accounts/migrations/0001_initial.py
laserforce_simulator/accounts/management/__init__.py
laserforce_simulator/accounts/management/commands/__init__.py
laserforce_simulator/accounts/management/commands/claim_unmanaged.py
laserforce_simulator/laserforce_simulator/settings.py
laserforce_simulator/laserforce_simulator/urls.py
laserforce_simulator/teams/models.py
laserforce_simulator/teams/views.py
laserforce_simulator/teams/api_views.py
laserforce_simulator/teams/migrations/0015_team_manager.py
laserforce_simulator/matches/models.py
laserforce_simulator/matches/forms.py
laserforce_simulator/matches/views.py
laserforce_simulator/matches/api_views.py
laserforce_simulator/matches/league_views.py
laserforce_simulator/matches/tournament_views.py
laserforce_simulator/matches/league_screens/*.py        (15 modules)
laserforce_simulator/matches/migrations/0062_manager_ownership_and_league_visibility.py
laserforce_simulator/templates/base.html
laserforce_simulator/templates/_partials/topnav_auth.html
laserforce_simulator/templates/accounts/login.html
laserforce_simulator/templates/accounts/register.html
laserforce_simulator/templates/accounts/password_change.html
laserforce_simulator/templates/accounts/password_change_done.html
laserforce_simulator/templates/leagues/create_advanced.html
```

**Code must NOT touch:** `core/views.py`, `core/urls.py`, `core/models.py`, any `core/migrations/*`,
`laserforce_simulator/api_urls.py`, `matches/simulation/*`, `matches/tournament_engine.py`,
`matches/owner_mood.py`, `matches/standings.py`, or any test file.

### 12.2 TESTS agent

```
laserforce_simulator/conftest.py
laserforce_simulator/accounts/tests/__init__.py
laserforce_simulator/accounts/tests/test_user_model.py
laserforce_simulator/accounts/tests/test_auth_views.py
laserforce_simulator/accounts/tests/test_permissions.py
laserforce_simulator/accounts/tests/test_claim_unmanaged.py
laserforce_simulator/matches/tests/test_ownership.py            (new)
laserforce_simulator/matches/tests/test_league_create.py         (add league-create-visibility to the locked-id tuple)
laserforce_simulator/matches/tests/views_tests.py                (login the 42 local clients)
laserforce_simulator/matches/tests/test_batch_views.py           (login the 15 local clients)
laserforce_simulator/matches/tests/test_heatmap.py               (login the 10 local clients)
laserforce_simulator/matches/tests/test_missile_log.py           (login the 4 local clients)
laserforce_simulator/matches/tests/test_playback_map.py          (login the 2 local clients)
laserforce_simulator/matches/tests/test_apis.py                  (login the setUp APIClient)
laserforce_simulator/matches/tests/test_topnav.py                (add the topnav auth-control ids)
laserforce_simulator/teams/tests/test_ownership.py               (new)
+ any other existing test file that fails solely because of the login gate
```

Tests owns **every** file under a `tests/` directory or matching `test_*.py` / `*_tests.py`, plus
`conftest.py`. It must not edit production modules; if a test cannot pass without a production change,
it escalates rather than editing.

### 12.3 DOCS agent

```
CLAUDE.md                                                (three Django apps -> four; accounts app guide link)
README.md                                                (the §2.7 AUTH_USER_MODEL deployment hazard; claim_unmanaged)
PLAN.md                                                  (mark UX-01 done; strike the two §0.3 clauses)
docs/adr/0038-accounts-and-uniform-manager-ownership.md  (Consequences: the §2.7 migration hazard)
laserforce_simulator/accounts/CLAUDE.md                  (new — app guide)
laserforce_simulator/teams/CLAUDE.md                     (Team.manager vs Team.managed_in_leagues)
laserforce_simulator/matches/CLAUDE.md                   (the four matches-app roots; the permission seam)
laserforce_simulator/core/CLAUDE.md                      (ArenaMap is deliberately NOT owned)
```

`CONTEXT.md` is **already updated** on this branch and is **frozen** — no agent edits it.

---

## 13. `League.visibility` — the dormant surface, in full

Three touch points and no more.

**1. Model** — `matches/models.py`, §2.5.

**2. Form** — `matches/forms.py::CreateLeagueForm`, declared **last** in the class body, after
`challenge_fired_luxury_tax` / `number_of_conferences` and before `phases`:

```python
    # UX-01 — DORMANT: authored at create time, read by nothing this slice.
    # The forward-compatible marker for who may join a League once League
    # membership exists (ADR-0038). ``required=False`` so a POST that omits it
    # stays valid; the create path coerces a falsy value to "closed".
    visibility = forms.ChoiceField(
        choices=League.VISIBILITY_CHOICES,
        initial="closed",
        required=False,
        widget=forms.Select(
            attrs={"id": "league-create-visibility", "class": "form-select"}
        ),
        label="League visibility",
    )
```

**3. Template** — `templates/leagues/create_advanced.html` **only** (not `create.html`), inserted as a
new `<div class="mb-3">` block immediately **after** the `league-create-number-of-conferences` block
and before the `league-create-schedule-format` block:

```html
        {# UX-01 -- dormant visibility marker; nothing reads it yet. #}
        <div class="mb-3">
            <label for="league-create-visibility" class="form-label">League visibility</label>
            {{ form.visibility }}
            <div class="form-text">
                Closed leagues are yours alone. Open is reserved for a future
                release where other managers can join — it has no effect yet.
            </div>
            {{ form.visibility.errors }}
        </div>
```

**Wiring:** `_create_league_and_season` passes `visibility=cleaned.get("visibility") or "closed"` into
`League.objects.create(...)`. `_template_to_form_data` adds `"visibility": "closed"` alongside its
existing `"schedule_format": "single_round_robin"` entry.

**Nothing else.** No read, no branch, no context key, no sidebar entry, no admin column.

---

## 14. Out of scope — do not implement

- Password **reset** (any of the four views/templates/URLs) — no mail provider.
- Google / OAuth social login.
- League membership, joining, invitations, or any **read** of `League.visibility`.
- Cross-Account read-only sharing, a `Team.is_public` / `ArenaMap.is_public` flag, or any sharing UI.
- A `manager` FK on `ArenaMap` or any `core` model.
- Renaming **Owner** / `OwnerEvaluation` / `owner_mood.py` / Owner-mood DOM ids.
- Per-object permissions, Django `Group`s, roles, or an object-level permission backend.
- A `RunPython` backfill of existing rows, or any migration-history rewrite for §2.7.
- Threading `manager` through `BatchSimulator` or `matches/simulation/*`.
- Stamping the global `"Free Agents"` singleton Team (§6.3).
- User profile screens, avatars, display names, or account-deletion self-service (Admin only).
