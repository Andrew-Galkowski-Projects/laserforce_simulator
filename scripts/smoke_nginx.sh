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

fail() {
  echo "FAIL: $1"
  failures=$((failures + 1))
}

pass() {
  echo "ok:   $1"
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
for i in $(seq 1 30); do
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
if grep -q '<app-root' "$TMP/app.html" && grep -q '<base href="/app/">' "$TMP/app.html"; then
  pass "/app/ serves index.html with base href /app/"
else
  fail "/app/ serves index.html with base href /app/"
fi
if grep -iq '^cache-control:.*no-cache' "$TMP/app.h"; then
  pass "index.html is Cache-Control: no-cache"
else
  fail "index.html is Cache-Control: no-cache"
fi

# 2. A deep link falls back to index.html.
curl -s -o "$TMP/deep.html" "$BASE/app/teams/123"
if grep -q '<app-root' "$TMP/deep.html"; then
  pass "/app/teams/123 falls back to index.html"
else
  fail "/app/teams/123 falls back to index.html"
fi

# 3. Hashed bundles are cached for good.
main_js="$(grep -o 'main-[A-Za-z0-9]*\.js' "$TMP/app.html" | head -1)"
if [ -z "$main_js" ]; then
  fail "index.html names a main-*.js bundle"
else
  code="$(curl -s -D "$TMP/js.h" -o /dev/null -w '%{http_code}' "$BASE/app/$main_js")"
  if [ "$code" = "200" ] && grep -iq '^cache-control:.*immutable' "$TMP/js.h"; then
    pass "$main_js is 200 and immutable"
  else
    fail "$main_js is 200 and immutable (got $code)"
  fi
fi

# 4. /api/ reaches Django: an anonymous call gets the ADR-0039 error envelope.
code="$(curl -s -o "$TMP/me.json" -w '%{http_code}' "$BASE/api/accounts/me/")"
if [ "$code" = "401" ] && grep -q '"errors"' "$TMP/me.json"; then
  pass "/api/accounts/me/ is a 401 envelope from Django"
else
  fail "/api/accounts/me/ is a 401 envelope from Django (got $code)"
fi

# 5. Everything outside /app/ is Django's: anonymous / redirects to its sign-in form.
curl -s -L -o "$TMP/root.html" "$BASE/"
if grep -q 'csrfmiddlewaretoken' "$TMP/root.html"; then
  pass "/ is Django's page"
else
  fail "/ is Django's page"
fi

# 6. A map-sized upload is not cut off by nginx's body limit.
head -c 2097152 /dev/zero >"$TMP/big.png"
code="$(curl -s -o /dev/null -w '%{http_code}' -F "name=smoke" -F "image=@$TMP/big.png" "$BASE/api/maps/")"
if [ "$code" != "413" ] && [ "$code" != "000" ]; then
  pass "2 MB POST /api/maps/ is not 413 (got $code)"
else
  fail "2 MB POST /api/maps/ is not 413 (got $code)"
fi

# 7 + 8. Django's CSRF middleware sees what nginx forwards. A sign-in POST with bad
# credentials re-renders the form (200) when CSRF passes, and is a 403 when it fails.
curl -s -c "$TMP/jar" -o "$TMP/login.html" "$BASE/accounts/login/"
token="$(grep -o 'name="csrfmiddlewaretoken" value="[^"]*"' "$TMP/login.html" | head -1 | sed 's/.*value="//; s/"$//')"
login_post() {
  curl -s -b "$TMP/jar" -o /dev/null -w '%{http_code}' "$@"     --data-urlencode "csrfmiddlewaretoken=$token"     --data-urlencode "username=smoke@example.com"     --data-urlencode "password=not-the-password"     "$BASE/accounts/login/"
}
if [ -z "$token" ]; then
  fail "/accounts/login/ carries a CSRF token"
else
  # 7. Host keeps the port, so the browser's Origin matches Django's host.
  code="$(login_post -H "Origin: $BASE")"
  if [ "$code" = "200" ]; then
    pass "Host keeps the port: Origin $BASE passes CSRF"
  else
    fail "Host keeps the port: Origin $BASE passes CSRF (got $code)"
  fi
  # 8. An upstream X-Forwarded-Proto: https is kept, so Django treats the request as
  # secure and demands a Referer (absent here) on a POST without Origin.
  code="$(login_post -H "X-Forwarded-Proto: https")"
  if [ "$code" = "403" ]; then
    pass "X-Forwarded-Proto https is passed to Django"
  else
    fail "X-Forwarded-Proto https is passed to Django (got $code)"
  fi
fi

if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed"
  docker logs "$CONTAINER"
  exit 1
fi
echo "nginx smoke: all checks passed"
