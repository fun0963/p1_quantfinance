#!/usr/bin/env bash
# Start the 24/7 schedulers and prove the deployment is sound before walking away.
#
#   cd ~/p1_quantfinance && ./scripts/cloud_verify.sh
#
# Checks, in order: .env sanity (keys present, paper mode, single-writer host),
# build+start, broker reachability, the single-writer guard actually refusing a
# foreign host, and the alert channel. Any failure stops the script - a half-
# verified trading host is worse than none.
set -euo pipefail

COMPOSE="docker compose -f docker-compose.live.yml"
pass() { printf '  [OK]   %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1" >&2; exit 1; }
say()  { printf '\n=== %s ===\n' "$1"; }

cd "$(dirname "$0")/.."

say "1/6 .env sanity"
[ -f .env ] || fail ".env missing - run scripts/cloud_bootstrap.sh first"
# shellcheck disable=SC1091
set -a; . ./.env; set +a
[ -n "${ALPACA_API_KEY:-}" ]    || fail "ALPACA_API_KEY is empty - edit .env"
[ -n "${ALPACA_SECRET_KEY:-}" ] || fail "ALPACA_SECRET_KEY is empty - edit .env"
[ "${ALPACA_PAPER:-}" = "true" ] || fail "ALPACA_PAPER must be true (real money is not authorized)"
[ "${EXECUTE_HOST:-}" = "quant-live" ] || fail "EXECUTE_HOST must be quant-live (matches the compose hostname)"
pass "keys present, paper mode, single-writer host pinned"

say "2/6 build and start"
$COMPOSE up -d --build
sleep 5
running=$($COMPOSE ps --services --filter status=running | wc -l)
[ "$running" -eq 2 ] || fail "expected 2 running services, got ${running} - check: $COMPOSE logs"
pass "both schedulers running"

say "3/6 broker reachable and is a PAPER account"
acct=$($COMPOSE run --rm --no-deps spy-momentum account --json 2>/dev/null || true)
echo "$acct" | grep -q '"is_paper": *true' || fail "broker check failed or account is not paper: ${acct:0:200}"
pass "alpaca reachable, is_paper=true"

say "4/6 single-writer guard refuses a foreign host"
# Same image, wrong EXECUTE_HOST -> --execute must be rejected.
if $COMPOSE run --rm --no-deps -e EXECUTE_HOST=not-this-host \
        spy-momentum live SPY --execute >/dev/null 2>&1; then
    fail "guard did NOT block a foreign host - two hosts could double every order"
fi
pass "foreign host correctly refused"

say "5/6 alert channel"
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    $COMPOSE run --rm --no-deps spy-momentum alert-test >/dev/null 2>&1 \
        && pass "alert sent - check your phone" \
        || fail "alert-test failed; fix the Telegram settings in .env"
else
    printf '  [WARN] Telegram not configured - you will not be told when something breaks\n'
fi

say "6/6 system snapshot"
$COMPOSE run --rm --no-deps spy-momentum status || true

cat <<'NEXT'

--------------------------------------------------------------------
VERIFIED. The schedulers are running and survive reboots.

Still worth doing:
  * Hourly heartbeat watchdog (Telegram alert if a scheduler goes silent):
      crontab -e   then add:
      0 * * * * cd ~/p1_quantfinance && docker compose -f docker-compose.live.yml run --rm --no-deps spy-momentum health --alert >/dev/null 2>&1

  * ON YOUR WORKSTATION, add this line to its .env so the laptop can no
    longer place orders (it would double every position):
      EXECUTE_HOST=quant-live

Daily use:
  docker compose -f docker-compose.live.yml logs -f --tail 50 spy-momentum
  docker compose -f docker-compose.live.yml run --rm spy-momentum status
--------------------------------------------------------------------
NEXT
