# The API authenticates with JWT: an in-memory access token and a rotating httpOnly refresh cookie

**Status:** Accepted (ANG-03, 2026-10-20). Refines ADR-0039's note that the API answers anonymous
requests with 403 "until ANG-03".

The Angular client (ANG-04 onwards) needs to authenticate against `/api/` without relying on the
Django session that the HTML views use. `djangorestframework-simplejwt` issues a short-lived
**access token** (15 minutes) and a longer **refresh token** (7 days). They are kept in different
places on purpose:

- **The access token lives only in the client's memory.** `POST /api/token/` returns it in the
  response body, and the client sends it as `Authorization: Bearer`. It is never put in
  localStorage, where any injected script could read it.
- **The refresh token lives only in an httpOnly cookie.** It is never in a response body, so no
  script can read it. The cookie is `SameSite=Lax`, `Secure` outside DEBUG, and scoped to
  `Path=/api/token/`, so it is sent only to the login, refresh and logout endpoints. That lets a page
  reload restore the login without asking for the password again.
- **Refresh tokens rotate.** Every `POST /api/token/refresh/` sets a new cookie and blacklists the
  old token (`token_blacklist` app). `POST /api/token/logout/` blacklists the token and deletes the
  cookie. The server has to do this because JavaScript cannot clear an httpOnly cookie.

`JWTAuthentication` is listed **first** in `DEFAULT_AUTHENTICATION_CLASSES` and
`SessionAuthentication` stays **second**. DRF takes `WWW-Authenticate` from the first class, so an
anonymous `/api/` request now gets **401** (`WWW-Authenticate: Bearer`) instead of 403. The client's
interceptor treats that 401 as the signal to refresh. Keeping the session class means the browsable
API, the HTML-side session and session-based API tests all keep working.

**The token endpoints do not require a CSRF token.** Refresh and logout are authenticated by a
cookie, so in principle a request to them can be forged. We accept that:

- `SameSite=Lax` keeps the cookie off cross-site POSTs.
- CORS (ADR-0039 / ANG-02) stops any unlisted origin from reading the new access token.
- A forced refresh only rotates the cookie in the victim's own browser.
- A forced logout is a nuisance, not a breach.

Requiring a CSRF token would make the client bootstrap a CSRF cookie first, which costs more than
it protects.

**The token endpoints validate input with simplejwt's serializers, not a Django `Form`.** They are
exempt from ADR-0039's "writes bind the existing Django Form" rule: logging in, refreshing and
logging out are an auth exchange, not a domain write, and no HTML form carries those fields.

## Considered options

- **Session auth only, with Angular same-origin and CSRF.** Rejected: it ties the client to Django's
  cookie and CSRF dance, and gives no 401 signal to refresh on.
- **Both tokens returned in the body and stored in localStorage.** Rejected: any XSS could steal a
  7-day credential.
- **Drop `SessionAuthentication` from the API.** Rejected for now: the browsable API and the HTML
  site still depend on it. It can go once ANG-08 removes the template views.
- **Require CSRF on the token endpoints.** Rejected for the reasons above.

## Consequences

- Every existing test asserting an anonymous 403 on `/api/` becomes a 401. Real permission 403s are
  unchanged, and cross-Account rows stay 404.
- A password change does not revoke outstanding refresh tokens. That is deferred (PLAN.md Deferred
  Items); the 15-minute access lifetime bounds the exposure of a leaked access token.
- `token_blacklist` brings third-party migrations and a growing `OutstandingToken` table.
  `flushexpiredtokens` can prune it; scheduling that is not part of ANG-03.
