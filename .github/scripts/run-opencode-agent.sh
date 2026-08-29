#!/usr/bin/env bash
# run-opencode-agent.sh — isolated, safe wrapper around the OpenCode CLI (Zen).
#
# This script is the ONLY place that shells out to `opencode`. It is intentionally
# easy to replace and is kept isolated so the exact CLI surface can be confirmed
# without touching workflow files.
#
# SECURITY:
#   * The OpenCode Zen credential is taken ONLY from the OPENCODE_API_KEY
#     environment variable (injected from a GitHub Actions secret). It is never
#     echoed, logged, or written to any artifact/comment.
#   * Untrusted data (PR diffs, issue text, CI logs) is passed to the model via a
#     PROMPT FILE on disk, never interpolated into shell source.
#   * --validate mode prints the planned invocation and exits 0 WITHOUT calling
#     the model and WITHOUT exposing the key.
#
# USAGE:
#   run-opencode-agent.sh --mode <read-only|dry-run|write-scoped> \
#                         --prompt-file <path> \
#                         [--context-dir <dir>] \
#                         [--max-turns <n>] \
#                         [--validate]
#
# MODES:
#   read-only     -> agent must not modify the repository (review / diagnose)
#   dry-run       -> agent may propose a patch but must NOT push/commit
#   write-scoped  -> agent may edit the working tree; the WORKFLOW does the commit/push

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
  echo "run-opencode-agent: --mode and --prompt-file are required" >&2
  exit 2
fi
if [ ! -f "$PROMPT_FILE" ]; then
  echo "run-opencode-agent: prompt file not found: $PROMPT_FILE" >&2
  exit 2
fi

# --- Credential mapping -------------------------------------------------------
# OpenCode Zen is assumed to read its key from OPENCODE_API_KEY. If the installed
# CLI expects a different variable, set it here (single mapping point).
OC_ZEN_TOKEN_VAR="OPENCODE_API_KEY"
if [ -z "${!OC_ZEN_TOKEN_VAR:-}" ]; then
  echo "run-opencode-agent: ${OC_ZEN_TOKEN_VAR} is not set (required to call OpenCode Zen)" >&2
  exit 3
fi
export "$OC_ZEN_TOKEN_VAR"

# --- Binary presence ----------------------------------------------------------
if ! command -v opencode >/dev/null 2>&1; then
  if [ "$VALIDATE" = "true" ]; then
    echo "run-opencode-agent [validate]: opencode binary NOT found on PATH" >&2
  else
    echo "run-opencode-agent: 'opencode' binary not found; install it first" >&2
    exit 4
  fi
fi

# --- Build the OpenCode invocation -------------------------------------------
# TODO(CONFIRM): confirm the exact `opencode` subcommand and flags for headless
# agent runs. The template below is a conservative placeholder. Adjust OC_RUN_ARGS
# (do NOT change the rest of this script).
OC_RUN_ARGS=(run --prompt-file "$PROMPT_FILE")
case "$MODE" in
  read-only)
    # TODO(CONFIRM): use the OpenCode flag that forbids file writes / shell exec.
    OC_RUN_ARGS+=(--mode ask)
    ;;
  dry-run | write-scoped)
    # TODO(CONFIRM): use the OpenCode flag that allows edits but never pushes.
    OC_RUN_ARGS+=(--mode build)
    ;;
  *)
    echo "run-opencode-agent: invalid mode: $MODE" >&2
    exit 2
    ;;
esac
OC_RUN_ARGS+=(--max-turns "$MAX_TURNS")
OC_RUN_ARGS+=(--cwd "$CONTEXT_DIR")

if [ "$VALIDATE" = "true" ]; then
  echo "run-opencode-agent [validate]"
  echo "  mode:        $MODE"
  echo "  prompt-file: $PROMPT_FILE"
  echo "  context-dir: $CONTEXT_DIR"
  echo "  max-turns:   $MAX_TURNS"
  echo "  token-var:   $OC_ZEN_TOKEN_VAR (value withheld)"
  echo "  command:     opencode ${OC_RUN_ARGS[*]}"
  echo "run-opencode-agent [validate]: OK (no model call made)"
  exit 0
fi

echo "run-opencode-agent: invoking opencode (mode=$MODE)" >&2
# Output goes to stdout; the workflow captures and truncates it.
exec opencode "${OC_RUN_ARGS[@]}"
