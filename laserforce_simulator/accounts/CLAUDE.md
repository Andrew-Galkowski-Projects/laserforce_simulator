# accounts/

The fourth Django app, added by **UX-01**. It owns the **Account** (the row a human logs in as), the
Accounts and JWT token APIs (`/api/accounts/`, `/api/token/`), invites, and the **permission seam**
every other app calls to answer "may this Account see this row?".

Vocabulary, straight from GLOSSARY.md — get these three apart before editing anything here:

| Term | What it is |
|---|---|
| **Account** | A registered human identity that logs in. The model is `accounts.User`. |
| **Manager** | *The Account a row belongs to.* Persisted as a nullable `manager` FK on each **Ownership root**. Widened by [ADR-0038](../../docs/adr/0038-accounts-and-uniform-manager-ownership.md) from the pre-UX-01 "implicit single local user". |
| **Owner** | The **fictional boss** who judges and fires the Manager via the **Owner evaluation** ([ADR-0026](../../docs/adr/0026-manager-firing-owner-mood.md), `OwnerEvaluation`, `owner_mood.py`). **Never a login. Never renamed. Never touched by UX-01.** |

> The word "owner" is **not** used for an Account anywhere — not in code, comments or docs. `OwnerEvaluation` gained no `manager` column (it derives via `league`).

## Models (`accounts/models.py`)

**`User`** — the **Account**. An `AbstractUser` subclass, email-first:

- `username = None` — the field is dropped entirely.
- `email = models.EmailField("email address", unique=True)`.
- `USERNAME_FIELD = "email"`, `REQUIRED_FIELDS = []`, `__str__` returns the email.
- No profile fields **except** one: `self_insert` (**UX-02**), a nullable `OneToOneField` to
  `teams.Player` appended as the **last** field after `email` — see
  [Self-insert (UX-02)](#self-insert-ux-02) below. `groups` / `user_permissions` come from
  `AbstractUser` unchanged (no `related_name` collisions).

**`UserManager`** — a `BaseUserManager` with `use_in_migrations = True`. `AbstractUser`'s default
manager is keyed on `username`, which this model drops, so both creators take **`email` as the first
positional argument**: `create_user(email, password=None, **extra)` and
`create_superuser(email, password=None, **extra)`. Empty email raises `ValueError`; so does a
`create_superuser` call whose `is_staff` / `is_superuser` are forced to `False`.

**`accounts/admin.py::UserAdmin`** subclasses `django.contrib.auth.admin.UserAdmin` but **re-declares
`fieldsets` / `add_fieldsets` / `ordering`** — the parent's cannot be reused verbatim once `username`
is gone. Registering it is what satisfies the PLAN requirement that *admins can remove accounts*:
removal is the built-in Admin delete action, and `manager`'s `SET_NULL` is what makes that safe.

## The `manager` FK — five Ownership roots, one rule

An **Ownership root** is a row that carries a `manager` FK directly. **A row is a root exactly when
its parent FK is null.** Team / League / Tournament have no parent FK and are therefore *always*
roots. Every other row derives its Manager by traversing its non-null parent FK.

| Model | `related_name` | Root when |
|---|---|---|
| `teams.Team` | `teams` | always |
| `matches.League` | `leagues` | always |
| `matches.Tournament` | `tournaments` | always — **even when `season_phase` is set** (see below) |
| `matches.Match` | `matches` | `season_id IS NULL` (a *sandbox* Match) |
| `matches.GameRound` | `game_rounds` | `match_id IS NULL` (a *standalone* Round) |

The field definition is **byte-identical on all five** — only `related_name` differs:
`ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, ...)`,
appended as the **last** field of each model's field block.

**`SET_NULL` is load-bearing.** Deleting an Account in Admin must **demote** its rows to Unmanaged —
still readable, still writable, still listed — never cascade a League's whole Season/Match/Event
history out of existence. `CASCADE` was rejected for exactly that reason; `PROTECT` was rejected
because it would make an Account undeletable and contradict the PLAN requirement. It also matches the
project's settled posture on every other user-facing FK (`League.current_team`, `Tournament.champion`,
`Match.season`, `*.winner` are all `SET_NULL`).

**`Tournament` is always its own root**, even for a CONF-02 regional or CONF-04 Worlds bracket
embedded in a Season: `season_phase` is *not* an ownership parent. The invariant "an embedded
Tournament's Manager equals its League's Manager" is held by **propagation at creation** (the three
`Season._build_*` sites stamp `manager=self.league.manager`), not by traversal — so the flat predicate
and a traversal would agree anyway.

**`core.ArenaMap` and the six map-config models are deliberately NOT roots.** No `manager` column, no
queryset filtering, no permission check on any `/api/maps/` route (they still require
`IsAuthenticated` — authenticated, unfiltered). Adding one breaks the contract; see
[`../core/CLAUDE.md`](../core/CLAUDE.md).

## The Unmanaged-row rule

An **Unmanaged row** is an Ownership root with `manager IS NULL`. It is **readable AND writable by
any authenticated Account, and listed to all of them.** NULL means *open*, not *frozen* and not
*hidden*.

That single decision is what lets the ~1237 pre-existing view-test calls pass behind one autouse login
fixture instead of a manager-stamping pass over 57 test files, and it keeps request-less writers
(`score_averages`, `game_analysis`, `BatchSimulator` in worker processes, test fixtures) working
unchanged. **It must be tightened the first time a second Account shares a deployment.**

Two consequences worth remembering: deleting an Account demotes its rows to Unmanaged rather than
destroying them, and because `Match.season` is `SET_NULL`, deleting a Season silently demotes its
Matches to Unmanaged *sandbox roots*. Both are accepted behaviour, not bugs.

## Permission seam (`accounts/permissions.py`)

The **entire** public surface. Nothing else in the module is importable API.

| Name | Contract |
|---|---|
| `ROOT_MODELS` | The five Ownership roots, in contract order: `Team`, `League`, `Tournament`, `Match`, `GameRound`. |
| `ownership_root(obj) -> Model \| None` | Walks to `obj`'s Ownership root. `None` when the row has **no ownership axis** (an `ArenaMap` or a map-config row). |
| `is_owned_by(obj, user) -> bool` | True when `user` may **read AND write** `obj`. No ownership axis ⇒ True. Root `manager` NULL ⇒ True. Otherwise `root.manager_id == user.pk` and the user is authenticated. |
| `get_owned_or_404(model, request, **lookup) -> _M` | `get_object_or_404` plus the gate. Returns **the resolved row, not its root**. |
| `owned_queryset(qs, user, *, path="") -> QuerySet` | Filters to `manager = user OR manager IS NULL`. `path` is the ORM lookup prefix from the queryset's model to the root carrying `manager` (`""` for a root, `"team"` for `Player`, `"season__league"` for Season-scoped rows). **Not valid for `Match` / `GameRound`.** |
| `owned_match_q(user) -> Q` | The `Match` predicate — flat on a sandbox Match, traversed through `season__league` for a Season Match. Deliberately longhand so the branch structure is auditable. |
| `owned_game_round_q(user) -> Q` | The `GameRound` predicate — flat when standalone, else through its `Match` (which itself splits on `season`). |
| `stamp_manager(obj, user) -> obj` | Sets `obj.manager` and persists **only that column** (`update_fields=["manager"]`). For rows created by code that has no `request` — the `BatchSimulator` return values in `matches.services.create_sandbox_match` / `create_sandbox_round`. An unauthenticated `user` leaves the row Unmanaged. |
| `manager_or_none(request) -> user \| None` | `request.user` when authenticated, else `None` (⇒ an Unmanaged row). |

**404, never 403.** `get_owned_or_404` raises `Http404` on a cross-Account hit so another Account's
row is indistinguishable from one that does not exist. Never substitute `PermissionDenied`.

**Locked semantics — do not "improve" these:** the row is returned, not the root; `manager IS NULL`
⇒ allow (read *and* write); no ownership axis ⇒ allow; cross-Account ⇒ `Http404`; `request` is the
**second positional** argument, which is what makes every call-site conversion the same mechanical
edit:

```python
thing = get_object_or_404(Model, pk=thing_id)      # before
thing = get_owned_or_404(Model, request, pk=thing_id)   # after
```

### The ownership-root traversal table

`ownership_root` is a **bounded loop** (private `_MAX_TRAVERSAL_DEPTH`, a guard against an FK cycle —
the deepest real chain is `GameEvent → GameRound → Match → Season → League`, 4 hops) over a
module-private `_PARENT_FIELD: dict[type[Model], str]` mapping a model to the **field name** of its
ownership parent. A model absent from the dict that carries `manager` is always a root; a model absent
from the dict that does *not* carry `manager` has **no ownership axis**.

| Model | Carries `manager`? | `_PARENT_FIELD` | Root resolution |
|---|---|---|---|
| `teams.Team` | yes | *(absent)* | self |
| `matches.League` | yes | *(absent)* | self |
| `matches.Tournament` | yes | *(absent)* | self (even with `season_phase` set) |
| `matches.Match` | yes | `season` | self if `season_id is None`, else → `Season` |
| `matches.GameRound` | yes | `match` | self if `match_id is None`, else → `Match` |
| `teams.Player` | no | `team` | → Team |
| `matches.Season` | no | `league` | → League |
| `matches.Conference` | no | `season` | → Season → League |
| `matches.SeasonPhase` | no | `season` | → Season → League |
| `matches.PlayerSeasonRating` | no | `season` | → Season → League |
| `matches.OwnerEvaluation` | no | `league` | → League |
| `matches.TeamSeasonFinance` | no | `season` | → Season → League |
| `matches.PlayerRoundState` | no | `game_round` | → GameRound → (Match → Season → League) |
| `matches.GameEvent` | no | `game_round` | → GameRound → (Match → Season → League) |
| `matches.TournamentParticipant` | no | `tournament` | → Tournament |
| `matches.BracketNode` | no | `tournament` | → Tournament |
| `matches.SeriesMatch` | no | `node` | → BracketNode → Tournament |
| `matches.TournamentPlayerEntry` | no | `tournament` | → Tournament |
| `core.ArenaMap` + all 6 map-config models | no | *(absent)* | **no ownership axis** → `None` |

Do **not** re-derive these — they are deliberate and non-obvious:

- `TeamSeasonFinance.team` and `OwnerEvaluation.team_managed` are `SET_NULL` and are **not** the
  ownership parent; `season` / `league` is.
- `PlayerSeasonRating.player` is **not** the ownership parent; `season` is (a rating is League data).
- `matches.standings.StandingsRow` is a **frozen dataclass, not a model** — never persisted, no
  `manager`, and it appears nowhere in this table.

`_PARENT_FIELD`, `_MAX_TRAVERSAL_DEPTH` and `_has_manager` are **private**: tests assert traversal
*behaviour* through `ownership_root`, never the table itself.

## The global login gate

`LoginRequiredMiddleware` (Django 5.1+) sits immediately **after**
`django.contrib.auth.middleware.AuthenticationMiddleware` and gates the whole project — chosen over
~220 per-view decorators. Settings added by UX-01, in a `# --- UX-01: Accounts (ADR-0038) ---` block
after `AUTH_PASSWORD_VALIDATORS`: `AUTH_USER_MODEL = "accounts.User"` and
`LOGIN_URL = "admin:login"` (ANG-08, ADR-0047: the Admin login is the only Django login page left,
so the gate sends anonymous visitors there; there is no `LOGIN_REDIRECT_URL` /
`LOGOUT_REDIRECT_URL`), plus
`REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = ["rest_framework.permissions.IsAuthenticated"]`
(was `AllowAny`).

**The exemptions are guarded by `accounts/tests/test_login_exempt_surfaces.py`**, which walks the
live URLconf. Adding an exemption means adding it to that test's allowlist, a deliberate security
decision. There are two kinds:

- **HTML views** are exempt only through an explicit `login_not_required`. The exemptions are the
  Admin login and, since ANG-10 (ADR-0049), the SPA shell: `HTML_EXEMPT_VIEWS ==
  {"django.contrib.admin.sites.login", "core.spa.spa_index", "core.spa.app_redirect"}` (the SPA's
  `index.html` and the `/app/` redirect carry no data; the SPA signs in through the API). Every
  other HTML route (the rest of `/admin/`, `/media/`) stays gated and 302s to
  `/admin/login/?next=...`.
- **Every DRF view is exempt automatically.** DRF 3.15+ `APIView.as_view()` sets
  `login_required = False` itself, and the API views also carry
  `@method_decorator(login_not_required, name="dispatch")` (redundant under DRF 3.15+). The API is
  gated instead by the settings-level `IsAuthenticated`, which returns a **401 JSON** body
  (`WWW-Authenticate: Bearer`, ADR-0041) rather than the middleware's HTML **302** to the login page.
  Logging out of the browser session still locks the API. Only the `ANONYMOUS_API_VIEWS` allowlist
  (`RegisterAPIView` and the three JWT token views, each `permission_classes = [AllowAny]`) is
  anonymous-allowed.
- **DESK-01:** the guard walks the Desktop App's URLconf (`desktop_app.urls`) too. Its allowlist is
  `ANONYMOUS_API_VIEWS` plus `desktop_app.launch_code.DesktopLoginView`, the Launch code sign-in
  (see [`../desktop_app/CLAUDE.md`](../desktop_app/CLAUDE.md)).

`process_view` receives the **resolved** view, so an `include()` cannot be wrapped. Both CBV forms
work because `View.as_view()` copies `dispatch.__dict__` onto the returned callable.

All four ANG-01a viewsets have a manager-scoped `get_queryset()` and still **keep** their
class-level `queryset` attribute, because the DRF router needs it for basename introspection.

**API ownership after ANG-01a** ([ADR-0039](../../docs/adr/0039-rest-api-envelope-and-form-bound-writes.md)).
The anonymous response is enveloped — `{"errors": [{"code": "not_authenticated", ...}], "meta": {}}` —
and is a **401** with `WWW-Authenticate: Bearer` since ANG-03 (ADR-0041; it was 403 before). The Team / Player viewsets gained writes (create,
PATCH, slots PUT, add player, generate, roster import, player DELETE); every one resolves its target
through the same owned `get_queryset()` + `get_object()`, so a write against another Account's row is
**404 and leaves the row unchanged**, and every create path stamps `manager_or_none(request)`.
`ArenaMapViewSet` is authenticated but **not** owned (ArenaMap is shared reference data). The
API-03 batch POST's team lookup is still not ownership-scoped (ANG-01b follow-up).

## Invites (`accounts.models.Invite`, `accounts/admin.py::InviteAdmin`)

Registration is **invite-only**. An `Invite` row: `code` (random `secrets.token_urlsafe(16)`,
unique, not editable), optional `email` (only that address may redeem it), `note`, `max_uses`
(default **1**, at least 1), `use_count` (not editable), optional `expires_at` (blank = never) and
`created_at`. Properties: `uses_left`, `is_expired` (`expires_at <= now`) and `is_redeemable`
(uses left and not expired). `Invite.clean` refuses an email lock with `max_uses > 1`: that
address can register only once, so the extra uses could never be spent.

Each sign-up through an invite writes an **`InviteRedemption`** (`invite` FK `CASCADE`,
`related_name="redemptions"`; `user` `OneToOneField(User, SET_NULL)`, `related_name=
"invite_redemption"`; `redeemed_at`). `use_count` only ever grows, so deleting the Account leaves
the redemption with `user = NULL` and never gives the use back. Migration `0004` copied each
used 0003 invite into one redemption plus `use_count = 1` before dropping `used_by` / `used_at`.

Staff create invites in Django admin (`/admin/accounts/invite/`). The add page asks for `email`,
`note`, `max_uses` and `expires_at`; after saving, a success message shows the absolute sign-up
link (`/register?invite=<code>`, the SPA route, ANG-08). The changelist shows *Uses* (`3 / 10`), *Status*
(Active / Used up / Expired) and a *Sign-up link* column while the invite is redeemable; the
change page lists its redemptions in a read-only inline. Tests: `accounts/tests/test_invites.py`.

## Forms (`accounts/forms.py`)

**There is NO password reset** — deferred with OAuth for want of a mail provider. **Recovery is
`manage.py changepassword`.**

Two classes, both bound by the Accounts API below:

- **`RegisterForm(UserCreationForm)`** — `Meta.model = User`, `fields = ("email",)`.
  **Invite-only** self-registration: email + password + confirm + `invite` (a code). Password
  matching and `AUTH_PASSWORD_VALIDATORS` come from `UserCreationForm`; email uniqueness from the
  model's `unique=True`. `clean_invite` returns the `Invite` row, or `INVALID_INVITE_MESSAGE` for a
  missing / unknown / used-up code and `EXPIRED_INVITE_MESSAGE` past `expires_at`; `clean` adds `WRONG_EMAIL_INVITE_MESSAGE` on `invite` when an
  email-locked invite meets another address (case-insensitive). `save` is atomic: it spends a
  use with a conditional `UPDATE ... SET use_count = use_count + 1 WHERE use_count < max_uses`
  (racing sign-ups for the last use cannot both win; the loser's Account rolls back), then writes
  the `InviteRedemption`.
- **`StyledPasswordChangeForm(PasswordChangeForm)`** — `PasswordChangeForm` with the house labels;
  only the validation is load-bearing.

## Accounts API (ANG-01g, `accounts/api_views.py`)

Four plain `APIView`s under `/api/accounts/`, routed as explicit `path()`s in
`laserforce_simulator/api_urls.py` and following
[ADR-0039](../../docs/adr/0039-rest-api-envelope-and-form-bound-writes.md) (envelope, Form-bound
writes, errors always raised). The output shape is **`MeSerializer`** (`accounts/serializers.py`):
`{id, email, date_joined, is_staff, self_insert, desktop, desktop_notice}`, where `self_insert` is
`PlayerSerializer` output of `get_self_insert(user)` or `null` — the raw column is never
serialized, so a drifted link reads `null`. **DESK-01:** `desktop` is `settings.DESKTOP_APP`
(`false` Hosted; the SPA hides Sign out and Change password when it is set) and `desktop_notice`
the Desktop App's reset notice (`desktop_app.notice`), always `null` Hosted.

| Method | Path | URL name | Behaviour |
|---|---|---|---|
| GET | `accounts/me/` | `api_accounts_me` | 200 `me` |
| POST | `accounts/register/` | `api_accounts_register` | Binds `RegisterForm` (so `invite` is required: 400 on `field: "invite"` when missing / invalid); 201 `me` of the new Account. **Does not log in** (the client then calls `POST /api/token/`); an authenticated caller keeps its own session. |
| POST | `accounts/me/password/` | `api_accounts_password` | Binds `StyledPasswordChangeForm(request.user, ...)`; 204, then `update_session_auth_hash` so the session survives. |
| PUT | `accounts/me/self-insert/` | `api_accounts_self_insert` | `{"player": <int>}`, bound through **`SelfInsertParamsForm`** (`accounts/api_forms.py`); 200 `me`. An integer string (`"5"`) is accepted; a missing `player` ⇒ 400 `required`, and `null` / a bool / a fractional float / a list / a non-numeric string ⇒ 400 `invalid`, both on `field: "player"`; a non-object body ⇒ 400 `parse_error`; missing or cross-Account Player ⇒ 404 (`get_owned_or_404`); Unmanaged Team ⇒ 400 `not_eligible` with `NOT_ELIGIBLE_MESSAGE`, nothing written. |
| DELETE | `accounts/me/self-insert/` | `api_accounts_self_insert` | 204, idempotent; clears a drifted link too. |

`NOT_ELIGIBLE_MESSAGE` lives in `accounts/self_insert.py`; the API `not_eligible` error imports it.

Anonymous calls to everything except register are an enveloped 401 `not_authenticated`.
Tests: `accounts/tests/test_api_accounts.py`.

**Angular screens (ANG-06 item 7).** The SPA's `/register`, `/account/password` and the Shell
account menu (My player / Unlink Self-insert) call these endpoints through `AuthService`; see
[`frontend/CLAUDE.md`](../../frontend/CLAUDE.md).

**CSRF.** DRF's `SessionAuthentication` enforces CSRF only for an *authenticated* session. An
anonymous register POST therefore needs no token, but a logged-in browser caller (the ANG-04 client)
must send the CSRF token on every unsafe method -- **including register**. `APIClient` does not
enforce CSRF, so the tests do not cover this. A JWT (`Authorization: Bearer`) caller needs no CSRF token.

## JWT token endpoints (ANG-03, `accounts/token_api_views.py`)

`POST /api/token/` (login, `{"email", "password"}`), `/api/token/refresh/` and `/api/token/logout/`.
The access token (15 min) comes back in the body; the refresh token (7 days, rotated and blacklisted
on every refresh) lives only in the httpOnly `refresh_token` cookie scoped to `/api/token/`. No CSRF,
no session. Full contract: `docs/agents/matches/api.md` **ANG-03**; decision:
[ADR-0041](../../docs/adr/0041-jwt-api-auth-with-httponly-refresh-cookie.md). Tests:
`accounts/tests/test_api_token.py`. The three views are in the login-exemption guard's
`ANONYMOUS_API_VIEWS`. The cookie is `Secure` when `settings.REFRESH_COOKIE_SECURE` (default
`True`; the Desktop App sets `False`) and `DEBUG` is off; `set_refresh_cookie(response, refresh)`
is public so the Launch code view sets the same cookie (DESK-01). A password change does **not** revoke outstanding refresh tokens (deferred).

## Stamping — where `manager` is set

Every site sets the Manager **at creation** from `request.user` via `manager_or_none(request)`.
Sites: the API Team create; `teams.services._generate_teams` and
`matches.league_services.create_league_and_season`, which each take **one keyword-only `manager`
parameter** (an omitted kwarg leaves the row Unmanaged); the League's free-agent pool Team; both
`league_play_services.setup_member_night` draw Teams; `tournament_services.create.create_tournament`'s
Tournament and `tournament_services.draw.run_draw`'s drawn Team; the post-hoc `stamp_manager(...)`
in `matches.services.create_sandbox_match` / `create_sandbox_round` on the `Match` / `GameRound`
**returned by** `BatchSimulator`; and the three `Season._build_*` embedded-Tournament sites
(`manager=self.league.manager`).

**ANG-01a additions.** The roster-import helpers `_check_db_slot_collisions` / `_apply_roster`
(now in `teams/services.py`) take a **required keyword `manager`**: Teams named in a CSV resolve only
among the Account's own + Unmanaged Teams, and a miss creates a Team **stamped** with the importer —
this closed a leak where importing appended to (and echoed player names from) another Account's
same-named Team. Callers: the API `POST /api/teams/import/` and the Tournament participant import.
The API `POST /api/teams/` and `POST /api/teams/generate/` stamp the same way.

`manager` is deliberately **not** threaded through `BatchSimulator` or `matches/simulation/*` — those
run in worker processes with no request. Rows they create stay Unmanaged, which is fully accessible.

The global `"Free Agents"` singleton Team (`teams.models.get_free_agents_team()`) is **never
stamped**: it is a cross-Account shared pool, and stamping it would let the first Account to run
`generate_players` with `num_teams == 0` capture it permanently and 404 it for everyone else.
`_generate_free_agents` and `get_free_agents_team()` keep their signatures verbatim.

## Management command — `claim_unmanaged`

```bash
python laserforce_simulator/manage.py claim_unmanaged --user <email>
```

Stamps **every Unmanaged row on all five roots** to one Account, inside a single
`transaction.atomic()`, iterating `Team` → `League` → `Tournament` → `Match` → `GameRound` and
running `Model.objects.filter(manager__isnull=True).update(manager=user)` per model. Because it uses
`.update()`, **no `save()` signals fire and no `auto_now` column is touched**. Unknown email raises
`CommandError`. Output is six lines — five unstyled `<Model>: N claimed` lines plus a
`self.style.SUCCESS` total. **Idempotent:** a second run matches nothing and prints `0` for every
model.

This command exists *instead of* a `RunPython` backfill. A backfill to "the first superuser" is not
merely skipped but **vacuous** — a custom user model means a new, empty user table on every existing
database, so it would find nobody and stamp nothing. See
[ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md).

## Self-insert (UX-02)

An Account may link **one** existing `Player` as its **Self-insert** (GLOSSARY.md) — a vanity record
of the human behind the Account. It is a **link, not a copy**: linking never moves, enrols,
re-rosters or re-simulates the Player, and the Player does **not** appear on any simulated Team
because of it. **No simulation / RNG / `BatchSimulator` change, no re-baseline**, and
`accounts/permissions.py` is untouched.

### The field

```python
self_insert = models.OneToOneField(
    "teams.Player", null=True, blank=True, on_delete=models.SET_NULL,
    related_name="self_insert_of",
)
```

- Stored on **`User`**, not on `Player` — the Account owns the choice; `Player` gains no column.
- `SET_NULL`: deleting the Player (or its Team, which cascades the Player) just leaves the Account
  with no Self-insert.
- OneToOne ⇒ a Player is at most one Account's Self-insert (DB unique constraint).
- Reverse accessor `player.self_insert_of` raises `RelatedObjectDoesNotExist` when unlinked —
  **production code never reads it**; use the helpers below.
- `accounts/admin.py` is **not** changed (the field is not in `UserAdmin.fieldsets`).

### Domain helpers (`accounts/self_insert.py`)

A **separate module, deliberately not `permissions.py`**: the permission seam table above is the
*entire* public surface of that module, and the Self-insert is a domain feature built *on* the seam,
not part of it. Exactly four public functions:

| Name | Contract |
|---|---|
| `get_self_insert(user) -> Player \| None` | **The drift guard — read-only.** `None` for `None` / anonymous. `None` with **zero queries** when `user.self_insert_id is None` (the column is already on the loaded `User` row, so an unlinked Account costs nothing extra). Otherwise **one** query: the Player with `select_related("team")`, filtered `team__manager_id=user.pk`. Returns `None` when the link has **drifted** (the Player's Team is now Unmanaged, or another Account's — e.g. the Player moved to the global "Free Agents" Team). **Never writes**: a drifted link stays in the column and silently reappears if ownership returns. Does not touch the `user.self_insert` related-object cache. |
| `is_self_insert_eligible(player, user) -> bool` | **Strict.** True iff `user` is authenticated **and** `player.team.manager_id` is not `None` **and** equals `user.pk`. An **Unmanaged** Team's Players are **not** eligible — readable/writable under the Unmanaged-row rule, but not linkable until `claim_unmanaged` stamps them. The "Free Agents" singleton is never stamped, so its Players are never eligible. |
| `set_self_insert(user, player) -> None` | Precondition (caller-checked): eligible. One `transaction.atomic()`: (1) **stale-link takeover** — clear any *other* Account's link to this Player (a drifted link elsewhere; the strictly-owning Account wins), run first so the OneToOne unique constraint never fires; (2) set and `save(update_fields=["self_insert"])`. Linking a second Player **replaces** the first; idempotent on the current link. Touches nothing else. |
| `clear_self_insert(user) -> None` | Sets `None` and saves `update_fields=["self_insert"]`. Clears the raw column even when the link had drifted. |

### Surfaces

Link and unlink go through `PUT` / `DELETE /api/accounts/me/self-insert/` (see
[Accounts API](#accounts-api-ang-01g-accountsapi_viewspy)); `MeSerializer.self_insert` is the read.

## Migrations

Six files; only `0004_invite_max_uses_expiry` has a `RunPython`:

- `accounts/migrations/0001_initial.py` — `CreateModel` for `User` with
  `managers = [("objects", accounts.models.UserManager())]`; dep
  `auth.0012_alter_user_first_name_max_length`.
- `teams/migrations/0015_team_manager.py` — one `AddField`; deps
  `swappable_dependency(AUTH_USER_MODEL)` + `teams.0014_player_team_health_injury`.
- `matches/migrations/0062_manager_ownership_and_league_visibility.py` — **one** migration carrying
  all five matches-app changes in order (`league.manager`, `tournament.manager`, `match.manager`,
  `gameround.manager`, `league.visibility`); deps `swappable_dependency(AUTH_USER_MODEL)` +
  `matches.0061_conference_map_rotation`.
- `accounts/migrations/0002_user_self_insert.py` (**UX-02**) — one `AddField` (`user.self_insert`);
  deps `accounts.0001_initial` + `teams.0015_team_manager`. **No cycle:** `teams.0015` depends on
  `swappable_dependency(AUTH_USER_MODEL)`, which resolves to `accounts.__first__` = `0001`, not
  `0002`. The reverse relation generates **no** `teams` / `matches` / `core` migration.

- `accounts/migrations/0003_invite.py` — one `CreateModel` (`Invite`); dep `accounts.0002_user_self_insert`.
- `accounts/migrations/0004_invite_max_uses_expiry.py` — adds `max_uses` / `use_count` / `expires_at`,
  creates `InviteRedemption`, **one `RunPython`** copying used invites into redemptions, then drops
  `used_by` / `used_at`. Reversing it restores the columns empty (the copy's reverse is a no-op).

`core` is untouched and stays at `0004_map05_cell_ranking_and_strong_spots`.

> ### ⚠️ `AUTH_USER_MODEL` deployment hazard
>
> Setting `AUTH_USER_MODEL` after `auth` / `admin` migrations have already been applied against
> `auth.User` raises, **on existing databases only**:
> `InconsistentMigrationHistory: Migration admin.0001_initial is applied before its dependency
> accounts.0001_initial`.
>
> **CI and the test suite are unaffected** — the test database is created fresh every run, so
> `pytest` goes green while the dev `db.sqlite3` and the Fly.io Postgres both break. Never read a
> green suite as proof the deploy is healthy.
>
> The approved recovery is a **fresh database** (ADR-0004 — simulation data is disposable): delete
> `db.sqlite3` locally / re-provision the Fly.io Postgres, then `migrate` and `createsuperuser`.
> Explicitly **not** a data migration, a migration-history rewrite, or a `--fake` shim.

## Tests

`accounts/tests/` — `test_user_model.py` (`USERNAME_FIELD`, `REQUIRED_FIELDS`, `username is None`,
email uniqueness, both `UserManager` creators and their `ValueError`s), `test_auth_views.py`
(anonymous API → 401 JSON envelope, never a 302), `test_permissions.py` (every
public name in the seam; traversal *behaviour*, never `_PARENT_FIELD`; **404 never 403**; the
Unmanaged read/write/list rule) and `test_claim_unmanaged.py` (the six output lines, the counts,
`CommandError`, idempotency), and `test_self_insert.py` (**UX-02** — the field, `SET_NULL` on Player
and Team delete, OneToOne uniqueness; the four `self_insert.py` functions incl. zero queries when
unlinked and drift ⇒ `None` with the column unchanged). The gate's own 302 to the Admin login is
pinned in `core/tests.py` `ServeMediaTests`.

> **Fixture warning (UX-02):** most fixtures create **Unmanaged** Teams. A test expecting a
> successful link must first set `team.manager = get_shared_manager()` — otherwise `get_self_insert`
> returns `None` by design.

Ownership coverage for the roots themselves lives in `matches/tests/test_ownership.py` and
`teams/tests/test_ownership.py`.

The root `conftest.py` carries an **autouse login fixture** (`SHARED_MANAGER_EMAIL`,
`get_shared_manager()`, `force_login_shared_manager`) that logs every DB-backed test in as one shared
Account via `force_login`. It **does nothing** for a test with no database access, and it **cannot
reach clients a test instantiates itself** — 73 such `Client()` / `APIClient()` sites across 5 files
call `force_login(get_shared_manager())` explicitly. `get_shared_manager()` uses `get_or_create` inside
the test's own transaction, so `pytest -n auto --dist worksteal` workers never collide.

## See also

- [ADR-0038](../../docs/adr/0038-accounts-and-uniform-manager-ownership.md) — the decision record.
- Seam contract:
  [`.claude/worktrees/ux-01-seam-contract.md`](../../.claude/worktrees/ux-01-seam-contract.md).
- GLOSSARY.md **Account** / **Ownership root** / **Unmanaged row** / **League visibility** terms and
  the rewritten **Manager** entry.
- [`../teams/CLAUDE.md`](../teams/CLAUDE.md) — `Team.manager` and the `Team.managed_in_leagues`
  naming hazard.
- [`docs/agents/matches/accounts-ownership.md`](../../docs/agents/matches/accounts-ownership.md) **UX-01** — the four matches-app roots and the
  dormant `League.visibility`.
- [`../core/CLAUDE.md`](../core/CLAUDE.md) — why `ArenaMap` is deliberately not owned.
- PLAN.md **UX-01**.
- **UX-02** Self-insert: seam contract
  [`.claude/worktrees/ux-02-seam-contract.md`](../../.claude/worktrees/ux-02-seam-contract.md),
  GLOSSARY.md **Self-insert**, PLAN.md **UX-02**.
