#!/usr/bin/env bash
# test-docker.sh — Run the full test pyramid against the local Docker stack.
#
# Usage:
#   ./scripts/test-docker.sh              # full pyramid (unit + integration + e2e)
#   ./scripts/test-docker.sh --unit-only  # unit tests only, no Docker needed
#   ./scripts/test-docker.sh --keep-up    # don't tear down after (useful for debugging)

set -euo pipefail

UNIT_ONLY=false
KEEP_UP=false
BASE_URL="http://localhost:8000"

for arg in "$@"; do
  case $arg in
    --unit-only) UNIT_ONLY=true ;;
    --keep-up)   KEEP_UP=true ;;
  esac
done

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
pass() { echo -e "${GREEN}✔ $*${NC}"; }
fail() { echo -e "${RED}✖ $*${NC}"; }
info() { echo -e "${YELLOW}▶ $*${NC}"; }
header() { echo -e "\n${BOLD}════════════════════════════════════════${NC}"; echo -e "${BOLD}  $*${NC}"; echo -e "${BOLD}════════════════════════════════════════${NC}"; }

UNIT_EXIT=0
INTEGRATION_EXIT=0

# ── Layer 1: Unit tests (no Docker needed) ────────────────────────────────────
header "Layer 1 — Unit tests (ASGI client, no Docker)"
if python3 -m pytest tests/ --ignore=tests/integration -v --tb=short; then
  pass "Unit tests passed"
else
  UNIT_EXIT=$?
  fail "Unit tests FAILED"
fi

if $UNIT_ONLY; then
  echo ""
  info "Skipping integration layer (--unit-only)"
  exit $UNIT_EXIT
fi

# ── Start Docker stack ────────────────────────────────────────────────────────
header "Docker stack startup"
info "Building image..."
docker compose build --quiet

info "Starting services..."
docker compose up -d

# Wait for readiness (up to 60 s)
info "Waiting for service to be ready..."
READY=false
for i in $(seq 1 60); do
  if curl -sf "${BASE_URL}/api/mgmt/ready" > /dev/null 2>&1; then
    READY=true
    pass "Service is ready (${i}s)"
    break
  fi
  printf "."
  sleep 1
done
echo ""

if ! $READY; then
  fail "Service did not become ready after 60s"
  docker compose logs web --tail=50
  docker compose down
  exit 1
fi

# ── Layer 2+3: Integration + E2E ──────────────────────────────────────────────
header "Layer 2 — Integration tests (real HTTP, real PG + Redis)"
if python3 -m pytest tests/integration/ --ignore=tests/integration/test_e2e.py -v --tb=short; then
  pass "Integration tests passed"
else
  INTEGRATION_EXIT=$?
  fail "Integration tests FAILED"
fi

header "Layer 3 — E2E tests (full user flows)"
if python3 -m pytest tests/integration/test_e2e.py -v --tb=short; then
  pass "E2E tests passed"
else
  INTEGRATION_EXIT=$?
  fail "E2E tests FAILED"
fi

# ── Teardown ──────────────────────────────────────────────────────────────────
if $KEEP_UP; then
  info "Stack left running (--keep-up). Stop with: docker compose down"
else
  header "Teardown"
  docker compose down
  pass "Stack stopped"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
header "Results"
[ $UNIT_EXIT -eq 0 ]        && pass "Layer 1 — Unit:        PASSED" || fail "Layer 1 — Unit:        FAILED"
[ $INTEGRATION_EXIT -eq 0 ] && pass "Layer 2+3 — Integr/E2E: PASSED" || fail "Layer 2+3 — Integr/E2E: FAILED"

FINAL=$((UNIT_EXIT + INTEGRATION_EXIT))
[ $FINAL -eq 0 ] && pass "All layers green." || fail "One or more layers failed."
exit $FINAL
