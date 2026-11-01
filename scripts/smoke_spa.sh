#!/usr/bin/env bash
# Smoke test for the app image serving the SPA itself (ANG-10, ADR-0049): run it
# against an already-running container of the root Dockerfile's image, and it
# asserts on response bodies and headers. The SPA is at /; /api/, /admin/,
# /static/ and /media/ are Django's own.
#
#   bash scripts/smoke_spa.sh
#
# Env:
#   BASE  where the app answers  (default http://localhost:8000)
#
# No `set -e`: every check runs and failures are counted, so one report lists them all.
set -u

BASE="${BASE:-http://localhost:8000}"
TMP="$(mktemp -d)"
failures=0
trap 'rm -rf "$TMP"' EXIT

# check NAME COMMAND...: runs COMMAND, prints ok / FAIL with NAME, counts failures.
check() {
  local name="$1"
  shift
  if "$@"; then
    echo "ok:   $name"
  else
    echo "FAIL: $name"
    failures=$((failures + 1))
  fi
}

# has PATTERN FILE: FILE contains PATTERN (a header match ignores case).
has() {
  grep -iq -- "$1" "$2"
}

# lacks PATTERN FILE: FILE does not contain PATTERN.
lacks() {
  ! grep -iq -- "$1" "$2"
}

# 1. The SPA shell at /, built with the default base href, never cached.
curl -s -D "$TMP/root.h" -o "$TMP/root.html" "$BASE/"
check "/ serves the SPA shell" has '<app-root' "$TMP/root.html"
check "index.html has base href /" has '<base href="/">' "$TMP/root.html"
check "index.html is Cache-Control: no-cache" has '^cache-control:.*no-cache' "$TMP/root.h"

# 2. A deep link falls back to index.html.
curl -s -o "$TMP/deep.html" "$BASE/teams/123"
check "/teams/123 falls back to index.html" has '<app-root' "$TMP/deep.html"

# 3. Hashed bundles are cached for good, and served compressed.
main_js="$(grep -o 'main-[A-Za-z0-9_-]*\.js' "$TMP/root.html" | head -1)"
code="$(curl -s -D "$TMP/js.h" -o /dev/null -w '%{http_code}' "$BASE/${main_js:-missing.js}")"
check "index.html names a main-*.js bundle" [ -n "$main_js" ]
check "the main bundle is 200 (got $code)" [ "$code" = "200" ]
check "the main bundle is immutable" has '^cache-control:.*immutable' "$TMP/js.h"
curl -s -D "$TMP/gz.h" -o /dev/null -H 'Accept-Encoding: gzip' "$BASE/${main_js:-missing.js}"
check "the main bundle is served gzip" has '^content-encoding: gzip' "$TMP/gz.h"
curl -s -D "$TMP/br.h" -o /dev/null -H 'Accept-Encoding: br' "$BASE/${main_js:-missing.js}"
check "the main bundle is served brotli" has '^content-encoding: br' "$TMP/br.h"

# 4. The fonts live under /fonts/, not Django's /media/ (outputPath.media).
styles_css="$(grep -o 'styles-[A-Za-z0-9_-]*\.css' "$TMP/root.html" | head -1)"
curl -s -o "$TMP/styles.css" "$BASE/${styles_css:-missing.css}"
font="$(grep -o 'fonts/[A-Za-z0-9_.-]*\.woff2' "$TMP/styles.css" | head -1)"
code="$(curl -s -D "$TMP/font.h" -o /dev/null -w '%{http_code}' "$BASE/${font:-fonts/missing.woff2}")"
check "styles-*.css names a font under fonts/" [ -n "$font" ]
check "the font is 200 (got $code)" [ "$code" = "200" ]
check "the font is immutable" has '^cache-control:.*immutable' "$TMP/font.h"

# 5. /api/ reaches Django: an anonymous call gets the ADR-0039 error envelope.
code="$(curl -s -o "$TMP/me.json" -w '%{http_code}' "$BASE/api/accounts/me/")"
check "/api/accounts/me/ is 401 (got $code)" [ "$code" = "401" ]
check "/api/accounts/me/ is the error envelope" has '"errors"' "$TMP/me.json"

# 6. An unknown API path is Django's 404, never the SPA shell.
code="$(curl -s -o "$TMP/nope.html" -w '%{http_code}' "$BASE/api/no-such-route/")"
check "/api/no-such-route/ is 404 (got $code)" [ "$code" = "404" ]
check "/api/no-such-route/ is not the SPA shell" lacks '<app-root' "$TMP/nope.html"

# 7. An ADR-0046 /app/ link is a 301 to the same path without the prefix.
code="$(curl -s -D "$TMP/app.h" -o /dev/null -w '%{http_code}' "$BASE/app/teams/123")"
check "/app/teams/123 is a 301 (got $code)" [ "$code" = "301" ]
check "/app/teams/123 redirects to /teams/123" has '^location: /teams/123\s*$' "$TMP/app.h"

# 8. Admin renders with its own /static/ files, and Django's CSRF middleware sees
# the request as sent. An Admin login POST with bad credentials re-renders the
# form (200) when CSRF passes, and is a 403 when it fails. Admin's form names the
# email field `username`.
curl -s -c "$TMP/jar" -o "$TMP/login.html" "$BASE/admin/login/"
admin_css="$(grep -o '/static/admin/css/[a-z_]*\.css' "$TMP/login.html" | head -1)"
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE${admin_css:-/static/missing.css}")"
check "/admin/login/ links an Admin stylesheet" [ -n "$admin_css" ]
check "the Admin stylesheet is 200 (got $code)" [ "$code" = "200" ]
token="$(grep -o 'name="csrfmiddlewaretoken" value="[^"]*"' "$TMP/login.html" | head -1 | sed 's/.*value="//; s/"$//')"
login_post() {
  curl -s -b "$TMP/jar" -o /dev/null -w '%{http_code}' "$@" \
    --data-urlencode "csrfmiddlewaretoken=$token" \
    --data-urlencode "username=smoke@example.com" \
    --data-urlencode "password=not-the-password" \
    "$BASE/admin/login/"
}
check "/admin/login/ carries a CSRF token" [ -n "$token" ]
code="$(login_post -H "Origin: $BASE")"
check "Origin $BASE passes CSRF (got $code)" [ "$code" = "200" ]
# A TLS proxy's X-Forwarded-Proto: https (Funnel, Fly) makes the request secure,
# so Django demands a Referer (absent here) on a POST without Origin.
code="$(login_post -H "X-Forwarded-Proto: https")"
check "X-Forwarded-Proto https reaches Django (got $code)" [ "$code" = "403" ]

if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "SPA smoke: all checks passed"
