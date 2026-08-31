#!/usr/bin/env bash
# upsert-marker-comment.sh — create or update a single bot-owned comment.
#
# A "marker" (an HTML comment such as <!-- ai-agent:pr-review -->) is embedded in
# the comment body so subsequent runs can find and REPLACE the same comment instead
# of spamming the thread. This makes the workflows idempotent.
#
# USAGE:
#   upsert-marker-comment.sh --repo OWNER/REPO --issue <n> \
#                            --marker "<!-- ai-agent:pr-review -->" \
#                            --body-file <path> [--max-chars 60000]
#
# The body is read from --body-file (never interpolated into the shell). The marker
# is always prepended so the comment remains identifiable.
set -euo pipefail

REPO=""
ISSUE=""
MARKER=""
BODY_FILE=""
MAX_CHARS="60000"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --issue) ISSUE="$2"; shift 2 ;;
    --marker) MARKER="$2"; shift 2 ;;
    --body-file) BODY_FILE="$2"; shift 2 ;;
    --max-chars) MAX_CHARS="$2"; shift 2 ;;
    *) echo "upsert-marker-comment: unknown arg: $1" >&2; exit 2 ;;
  esac
done

for v in REPO ISSUE MARKER BODY_FILE; do
  if [ -z "${!v}" ]; then echo "upsert-marker-comment: --${v,,} is required" >&2; exit 2; fi
done
if [ ! -f "$BODY_FILE" ]; then echo "upsert-marker-comment: body file not found: $BODY_FILE" >&2; exit 2; fi

# Truncate safely (preserve UTF-8) to MAX_CHARS.
TMP="$(mktemp)"
python3 - "$BODY_FILE" "$MAX_CHARS" "$TMP" <<'PY'
import sys
path, limit, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
text = open(path, encoding="utf-8").read()
if len(text) > int(limit):
    text = text[:int(limit)] + "\n\n… (truncated by size limit)"
open(out, "w", encoding="utf-8").write(text)
PY

FULL_BODY_FILE="$TMP.marker"
{ printf '%s\n\n' "$MARKER"; cat "$TMP"; } > "$FULL_BODY_FILE"

# Find an existing comment containing the marker.
EXISTING="$(gh api "repos/${REPO}/issues/${ISSUE}/comments?per_page=100" \
  --jq --arg m "$MARKER" '.[] | select(.body | contains($m)) | .id' | head -n1 || true)"

if [ -n "$EXISTING" ]; then
  echo "upsert-marker-comment: updating existing comment $EXISTING" >&2
  gh api -X PATCH "repos/${REPO}/issues/comments/${EXISTING}" \
    -f "body=@${FULL_BODY_FILE}" >/dev/null
else
  echo "upsert-marker-comment: creating new comment on #${ISSUE}" >&2
  gh api -X POST "repos/${REPO}/issues/${ISSUE}/comments" \
    -f "body=@${FULL_BODY_FILE}" >/dev/null
fi

rm -f "$TMP" "$FULL_BODY_FILE"
