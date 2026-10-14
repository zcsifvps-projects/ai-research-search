#!/usr/bin/env bash
# Live security smoke test for a RUNNING ScholarPulse deployment (local or production).
#
#   bash scripts/smoke-test.sh <API_BASE_URL> [APP_URL]
#
# Examples:
#   bash scripts/smoke-test.sh http://localhost:8001 http://localhost:3000
#   bash scripts/smoke-test.sh https://<your-domain>/api https://<your-domain>
#
# Optional environment variables:
#   EXPECT_DOCS=1                  expect /docs and /openapi.json to be served (default 0 = must be hidden)
#   SMOKE_EMAIL / SMOKE_PASSWORD   a VERIFIED test account; enables the cookie-flag and logout-revocation checks
#   SKIP_RATE_TEST=1               skip the burst that deliberately trips the rate limiter (your IP is blocked ~1 minute)
#
# Exit code 0 = every check passed. Nothing here changes data; it registers no accounts.
set -u
API="${1:-}"; APP="${2:-}"
[ -n "$API" ] || { echo "usage: bash scripts/smoke-test.sh <API_BASE_URL> [APP_URL]"; exit 2; }
API="${API%/}"; APP="${APP%/}"
command -v curl >/dev/null || { echo "curl is required"; exit 2; }

PASS=0; FAIL=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
check(){ if [ "$2" = "1" ]; then ok "$1"; else bad "$1"; fi; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@"; }
hdrs() { curl -s -D - -o /dev/null --max-time 20 "$@" | tr -d '\r'; }
has()  { printf '%s' "$1" | grep -qi "$2" && echo 1 || echo 0; }
JSON='Content-Type: application/json'

echo "API: $API"
echo "== Availability and information exposure"
body=$(curl -s --max-time 20 "$API/health")
check "/health returns exactly {\"status\":\"healthy\"} (no configuration leaked)" "$([ "$(printf '%s' "$body" | tr -d ' \n')" = '{"status":"healthy"}' ] && echo 1 || echo 0)"
if [ "${EXPECT_DOCS:-0}" = "1" ]; then
  check "/docs is served (EXPECT_DOCS=1)"        "$([ "$(code "$API/docs")" = 200 ] && echo 1 || echo 0)"
else
  check "/docs is hidden in production"          "$([ "$(code "$API/docs")" = 404 ] && echo 1 || echo 0)"
  check "/openapi.json is hidden in production"  "$([ "$(code "$API/openapi.json")" = 404 ] && echo 1 || echo 0)"
fi
err=$(curl -s --max-time 20 -X POST "$API/ask" -H "$JSON" -d '{"question":"x","results":[],"session_id":"short"}')
check "validation errors do not echo server paths or secrets" "$([ "$(has "$err" '/home/\|\.env\|traceback\|api_key')" = 0 ] && echo 1 || echo 0)"

echo "== Every protected route refuses anonymous callers"
for pair in "GET /auth/me" "GET /bookmarks" "POST /bookmarks" "DELETE /bookmarks/1" "POST /history/sync" \
            "POST /auth/change-password" "DELETE /auth/delete-account" "POST /auth/logout-all"; do
  m=${pair%% *}; p=${pair#* }
  c=$(code -X "$m" "$API$p" -H "$JSON" -d '{}')
  check "$m $p -> 401 or 422 without a session (got $c)" "$([ "$c" = 401 ] || [ "$c" = 422 ] && echo 1 || echo 0)"
done
c=$(code -X POST "$API/history/sync" -H "$JSON" -d '{"session_id":"aaaaaaaaaaaaaaaa"}')
check "POST /history/sync -> 401 without a session (got $c)" "$([ "$c" = 401 ] && echo 1 || echo 0)"
c=$(code -X POST "$API/ask" -H "$JSON" -d '{"question":"x","results":[],"session_id":"short"}')
check "weak guest session ids are rejected with 422 (got $c)" "$([ "$c" = 422 ] && echo 1 || echo 0)"
hist=$(curl -s --max-time 20 "$API/history/anonymous")
check "guessable guest session ids read nothing" "$([ "$(printf '%s' "$hist" | tr -d ' \n')" = '{"history":[]}' ] && echo 1 || echo 0)"

echo "== Registration does not reveal which emails exist or sign anyone in"
h=$(hdrs -X POST "$API/auth/register" -H "$JSON" -d '{"email":"smoke-nonexistent-check@invalid.test","password":"not-a-real-password-1"}')
check "register sets no cookie" "$([ "$(has "$h" '^set-cookie')" = 0 ] && echo 1 || echo 0)"
echo "  NOTE  that call may have queued a verification email to smoke-nonexistent-check@invalid.test (harmless)"

echo "== Response headers"
h=$(hdrs "$API/health")
check "API: X-Content-Type-Options: nosniff"  "$(has "$h" '^x-content-type-options: nosniff')"
check "API: X-Frame-Options set"              "$(has "$h" '^x-frame-options')"
check "API: Content-Security-Policy set"      "$(has "$h" '^content-security-policy')"
check "API: responses are not cacheable"      "$(has "$h" '^cache-control: no-store')"
case "$API" in https://*) check "API: Strict-Transport-Security set (HTTPS)" "$(has "$h" '^strict-transport-security')";; esac

echo "== CORS (only the real frontend may call the API with credentials)"
if [ -n "$APP" ]; then
  h=$(hdrs -X OPTIONS "$API/auth/login" -H "Origin: $APP" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type")
  check "frontend origin is allowed, with credentials" "$([ "$(has "$h" "^access-control-allow-origin: $APP\$")" = 1 ] && [ "$(has "$h" '^access-control-allow-credentials: true')" = 1 ] && echo 1 || echo 0)"
fi
h=$(hdrs -X OPTIONS "$API/auth/login" -H "Origin: https://evil.invalid" -H "Access-Control-Request-Method: POST")
check "a foreign origin is NOT allowed" "$([ "$(has "$h" '^access-control-allow-origin: https://evil.invalid')" = 0 ] && echo 1 || echo 0)"
check "wildcard origin is never returned" "$([ "$(has "$h" '^access-control-allow-origin: \*')" = 0 ] && echo 1 || echo 0)"

if [ -n "${SMOKE_EMAIL:-}" ] && [ -n "${SMOKE_PASSWORD:-}" ]; then
  echo "== Session cookie and logout revocation (test account)"
  jar=$(mktemp); trap 'rm -f "$jar"' EXIT
  h=$(curl -s -D - -o /dev/null -c "$jar" --max-time 20 -X POST "$API/auth/login" -H "$JSON" \
        -d "{\"email\":\"$SMOKE_EMAIL\",\"password\":\"$SMOKE_PASSWORD\"}")
  sc=$(printf '%s' "$h" | grep -i '^set-cookie: access_token')
  check "login sets the access_token cookie" "$([ -n "$sc" ] && echo 1 || echo 0)"
  check "cookie is HttpOnly"   "$(has "$sc" 'httponly')"
  check "cookie has SameSite"  "$(has "$sc" 'samesite=')"
  case "$API" in https://*) check "cookie is Secure (HTTPS)" "$(has "$sc" 'secure')";; esac
  token=$(awk '$6=="access_token"{print $7}' "$jar")
  check "session works before logout" "$([ "$(code -b "$jar" "$API/auth/me")" = 200 ] && echo 1 || echo 0)"
  curl -s -o /dev/null -b "$jar" -X POST "$API/auth/logout" --max-time 20
  check "a copied token is dead after logout" "$([ "$(code -H "Cookie: access_token=$token" "$API/auth/me")" = 401 ] && echo 1 || echo 0)"
fi

if [ "${SKIP_RATE_TEST:-0}" != "1" ]; then
  echo "== Rate limiting (sends 14 bad logins; your IP may be blocked for about a minute)"
  got429=0
  for i in $(seq 1 14); do
    c=$(code -X POST "$API/auth/login" -H "$JSON" -d "{\"email\":\"rate$i-$RANDOM@invalid.test\",\"password\":\"wrong-password-1\"}")
    [ "$c" = 429 ] && got429=1
  done
  check "burst of bad logins is throttled (429 seen)" "$got429"
fi

if [ -n "$APP" ]; then
  echo "== Frontend: $APP"
  h=$(hdrs "$APP/")
  check "frontend answers 200"                   "$(has "$h" '^HTTP/[0-9.]* 200')"
  check "frontend: X-Content-Type-Options"       "$(has "$h" '^x-content-type-options: nosniff')"
  check "frontend: X-Frame-Options"              "$(has "$h" '^x-frame-options')"
  check "frontend: does not advertise X-Powered-By" "$([ "$(has "$h" '^x-powered-by')" = 0 ] && echo 1 || echo 0)"
  if [ "$(has "$h" '^content-security-policy')" = 1 ] || [ "$(has "$h" '^content-security-policy-report-only')" = 1 ]; then
    check "frontend: Content-Security-Policy present" 1
  else
    echo "  INFO  no Content-Security-Policy header (expected in dev; in production check CSP_MODE)"
  fi
  html=$(curl -s --max-time 20 "$APP/")
  leaked=0
  for js in $(printf '%s' "$html" | grep -o '/_next/static/[^"]*\.js' | sort -u | head -40); do
    if curl -s --max-time 20 "$APP$js" | grep -qE 'localhost:(8000|8001)|127\.0\.0\.1:8000'; then leaked=1; fi
  done
  case "$APP" in http://localhost*) echo "  INFO  skipping the 'no localhost baked into the build' check (running locally)";;
    *) check "frontend build has no localhost API URL baked in (NEXT_PUBLIC_* build args were set)" "$([ "$leaked" = 0 ] && echo 1 || echo 0)";; esac
fi

echo; echo "Result: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
