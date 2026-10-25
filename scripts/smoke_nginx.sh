#!/usr/bin/env bash
# ANG-07 smoke test for the nginx front door (ADR-0046): builds frontend/Dockerfile,
# runs it in front of an already-running Django, and asserts on response bodies.
#
#   bash scripts/smoke_nginx.sh
#
# Env:
#   DJANGO_UPSTREAM  host:port nginx proxies to      (default 127.0.0.1:8000)
#   NGINX_PORT       port nginx listens on           (default 8080)
#   NETWORK          "host" (CI) or "bridge"; bridge publishes NGINX_PORT, for
#                    Docker Desktop with DJANGO_UPSTREAM=host.docker.internal:<port>
#   IMAGE            image tag to build              (default laserforce-frontend)
#   SKIP_BUILD       set to 1 to reuse an existing IMAGE
#
# No `set -e`: every check runs and failures are counted, so one report lists them all.
set -u

DJANGO_UPSTREAM="${DJANGO_UPSTREAM:-127.0.0.1:8000}"
NGINX_PORT="${NGINX_PORT:-8080}"
NETWORK="${NETWORK:-host}"
IMAGE="${IMAGE:-laserforce-frontend}"
CONTAINER="smoke-nginx"
BASE="http://localhost:${NGINX_PORT}"
TMP="$(mktemp -d)"
failures=0

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

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

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  docker build -t "$IMAGE" frontend || { echo "FAIL: docker build"; exit 1; }
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
if [ "$NETWORK" = "host" ]; then
  net_args=(--network host)
else
  net_args=(-p "${NGINX_PORT}:${NGINX_PORT}")
fi
docker run -d --name "$CONTAINER" "${net_args[@]}" \
  -e DJANGO_UPSTREAM="$DJANGO_UPSTREAM" \
  -e NGINX_PORT="$NGINX_PORT" \
  "$IMAGE" >/dev/null || { echo "FAIL: docker run"; exit 1; }

ready=false
for _ in $(seq 1 30); do
  if curl -s -o /dev/null "$BASE/app/"; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != "true" ]; then
  echo "FAIL: nginx did not answer on $BASE after 30s"
  docker logs "$CONTAINER"
  exit 1
fi

# 1. The SPA shell at /app/, built with the /app/ base href, never cached.
curl -s -D "$TMP/app.h" -o "$TMP/app.html" "$BASE/app/"
check "/app/ serves the SPA shell" has '<app-root' "$TMP/app.html"
check "index.html has base href /app/" has '<base href="/app/">' "$TMP/app.html"
check "index.html is Cache-Control: no-cache" has '^cache-control:.*no-cache' "$TMP/app.h"

# 2. A deep link falls back to index.html.
curl -s -o "$TMP/deep.html" "$BASE/app/teams/123"
check "/app/teams/123 falls back to index.html" has '<app-root' "$TMP/deep.html"

# 3. Hashed bundles are cached for good.
main_js="$(grep -o 'main-[A-Za-z0-9]*\.js' "$TMP/app.html" | head -1)"
code="$(curl -s -D "$TMP/js.h" -o /dev/null -w '%{http_code}' "$BASE/app/${main_js:-missing.js}")"
check "index.html names a main-*.js bundle" [ -n "$main_js" ]
check "the main bundle is 200 (got $code)" [ "$code" = "200" ]
check "the main bundle is immutable" has '^cache-control:.*immutable' "$TMP/js.h"

# 4. /api/ reaches Django: an anonymous call gets the ADR-0039 error envelope.
code="$(curl -s -o "$TMP/me.json" -w '%{http_code}' "$BASE/api/accounts/me/")"
check "/api/accounts/me/ is 401 (got $code)" [ "$code" = "401" ]
check "/api/accounts/me/ is the error envelope" has '"errors"' "$TMP/me.json"

# 5. Everything outside /app/ is Django's: anonymous / redirects to its sign-in form.
curl -s -L -o "$TMP/root.html" "$BASE/"
check "/ is Django's sign-in form" has 'csrfmiddlewaretoken' "$TMP/root.html"

# 6. A map-sized upload is not cut off by nginx's body limit.
head -c 2097152 /dev/zero >"$TMP/big.png"
code="$(curl -s -o /dev/null -w '%{http_code}' -F "name=smoke" -F "image=@$TMP/big.png" "$BASE/api/maps/")"
check "2 MB POST /api/maps/ reaches Django (got $code)" [ "$code" != "413" -a "$code" != "000" ]

# 7 + 8. Django's CSRF middleware sees what nginx forwards. A sign-in POST with bad
# credentials re-renders the form (200) when CSRF passes, and is a 403 when it fails.
curl -s -c "$TMP/jar" -o "$TMP/login.html" "$BASE/accounts/login/"
token="$(grep -o 'name="csrfmiddlewaretoken" value="[^"]*"' "$TMP/login.html" | head -1 | sed 's/.*value="//; s/"$//')"
login_post() {
  curl -s -b "$TMP/jar" -o /dev/null -w '%{http_code}' "$@" \
    --data-urlencode "csrfmiddlewaretoken=$token" \
    --data-urlencode "username=smoke@example.com" \
    --data-urlencode "password=not-the-password" \
    "$BASE/accounts/login/"
}
check "/accounts/login/ carries a CSRF token" [ -n "$token" ]
# 7. Host keeps the port, so the browser's Origin matches Django's host.
code="$(login_post -H "Origin: $BASE")"
check "Host keeps the port: Origin $BASE passes CSRF (got $code)" [ "$code" = "200" ]
# 8. An upstream X-Forwarded-Proto: https is kept, so Django treats the request as
# secure and demands a Referer (absent here) on a POST without Origin.
code="$(login_post -H "X-Forwarded-Proto: https")"
check "X-Forwarded-Proto https is passed to Django (got $code)" [ "$code" = "403" ]

# 9. The upstream name is resolved per request, not once at start: a config whose
# upstream doesn't resolve yet still loads (so a recreated app's new IP is picked up).
unresolved_config_loads() {
  docker run --rm -e DJANGO_UPSTREAM=lf-smoke-unresolvable:8000 "$IMAGE" nginx -t -q >/dev/null 2>&1
}
check "a config with an unresolvable upstream loads" unresolved_config_loads

if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed"
  docker logs "$CONTAINER"
  exit 1
fi
echo "nginx smoke: all checks passed"
