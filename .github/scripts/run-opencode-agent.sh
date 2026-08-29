#!/usr/bin/env bash
# run-opencode-agent.sh — call OpenCode Zen as a REMOTE endpoint (agent service).
#
# IMPORTANT: we do NOT run OpenCode locally. The runner only POSTs the prompt to
# the Zen endpoint and captures the response. The agentic work (if any) is done by
# the Zen service; this script is a thin, replaceable HTTP client.
#
# SECURITY:
#   * OPENCODE_API_KEY (secret) is sent only as a Bearer token to the endpoint.
#   * OPENCODE_ZEN_ENDPOINT and OPENCODE_MODEL are repository VARIABLES (not secrets).
#   * Untrusted data (PR diffs, issue text, CI logs) is sent in the request BODY,
#     never into shell source or logs.
#   * --validate prints the planned request (host + auth redacted) WITHOUT calling.
#
# USAGE:
#   run-opencode-agent.sh --mode <read-only|dry-run|write-scoped> \
#                         --prompt-file <path> \
#                         [--context-dir <dir>] [--max-turns <n>] [--validate]

set -euo pipefail

MODE=""
PROMPT_FILE=""
CONTEXT_DIR="$(pwd)"
MAX_TURNS="20"
VALIDATE="false"

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --prompt-file) PROMPT_FILE="$2"; shift 2 ;;
    --context-dir) CONTEXT_DIR="$2"; shift 2 ;;
    --max-turns) MAX_TURNS="$2"; shift 2 ;;
    --validate) VALIDATE="true"; shift ;;
    *) echo "run-opencode-agent: unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$MODE" ] || [ -z "$PROMPT_FILE" ]; then
  echo "run-opencode-agent: --mode and --prompt-file are required" >&2; exit 2
fi
if [ ! -f "$PROMPT_FILE" ]; then
  echo "run-opencode-agent: prompt file not found: $PROMPT_FILE" >&2; exit 2
fi

# --- Required configuration (all from env; never printed) --------------------
if [ -z "${OPENCODE_API_KEY:-}" ]; then
  echo "run-opencode-agent: OPENCODE_API_KEY is not set (required)" >&2; exit 3
fi
if [ -z "${OPENCODE_ZEN_ENDPOINT:-}" ]; then
  echo "run-opencode-agent: OPENCODE_ZEN_ENDPOINT (repo variable) is not set" >&2; exit 5
fi
if [ -z "${OPENCODE_MODEL:-}" ]; then
  echo "run-opencode-agent: OPENCODE_MODEL (repo variable) is not set" >&2; exit 6
fi

if [ "$VALIDATE" = "true" ]; then
  echo "run-opencode-agent [validate]"
  echo "  mode:     $MODE"
  echo "  endpoint: ${OPENCODE_ZEN_ENDPOINT}"
  echo "  model:    ${OPENCODE_MODEL}"
  echo "  auth:     Bearer ${OPENCODE_API_KEY:0:4}*** (withheld)"
  echo "  prompt:   $PROMPT_FILE ($(wc -c < "$PROMPT_FILE") bytes)"
  echo "run-opencode-agent [validate]: OK (no HTTP call made)"
  exit 0
fi

# --- Build request body -------------------------------------------------------
# TODO(CONFIRM): confirm OpenCode Zen's exact request schema / path. The payload
# below assumes an OpenAI-compatible chat/completions shape. Adjust to Zen's real
# API. `mode`/`max_turns` are passed as custom fields (ignored if unsupported).
REQUEST_BODY="$(jq -n \
  --arg model "$OPENCODE_MODEL" \
  --arg mode "$MODE" \
  --argjson max_turns "$MAX_TURNS" \
  --rawfile content "$PROMPT_FILE" \
  '{
     model: $model,
     messages: [ { role: "user", content: $content } ],
     stream: false,
     metadata: { mode: $mode, max_turns: $max_turns }
   }')"

echo "run-opencode-agent: POST ${OPENCODE_ZEN_ENDPOINT} (mode=$MODE)" >&2

# Capture raw response; try to extract assistant text, fall back to raw.
RESP="$(curl -fsS -X POST "$OPENCODE_ZEN_ENDPOINT" \
  -H "Authorization: Bearer $OPENCODE_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$REQUEST_BODY")"

# TODO(CONFIRM): confirm the response field that holds the agent's text.
echo "$RESP" | jq -r '.choices[0].message.content // .text // .' 2>/dev/null || echo "$RESP"
