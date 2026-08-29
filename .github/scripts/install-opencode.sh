#!/usr/bin/env bash
# install-opencode.sh — install the OpenCode CLI for GitHub Actions.
#
# SECURITY NOTE: This is a REPLACEABLE, conservative default. The exact
# OpenCode Zen install method must be confirmed against official docs before
# enabling any write-capable workflow. See docs/ai-agent-workflows.md.
#
# Default strategy: download a pinned GitHub release tarball (pinned by
# OPencode_VERSION). If a binary is already on PATH it is reused.
set -euo pipefail

VERSION="${1:-${OPencode_VERSION:-}}"
if [ -z "$VERSION" ]; then
  echo "install-opencode: OPencode_VERSION is not set (pass as arg or env)" >&2
  exit 1
fi

if command -v opencode >/dev/null 2>&1; then
  echo "install-opencode: reusing existing opencode at $(command -v opencode)"
  opencode --version 2>/dev/null || true
  exit 0
fi

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) ARCH="amd64" ;;
  aarch64 | arm64) ARCH="arm64" ;;
esac

# TODO(CONFIRM): verify the exact release artifact name/path for OpenCode Zen.
# This URL pattern is a placeholder and MUST be validated.
URL="https://github.com/anomalyco/opencode/releases/download/v${VERSION}/opencode_${VERSION}_${OS}_${ARCH}.tar.gz"
echo "install-opencode: downloading ${URL}" >&2

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$URL" -o "$TMP/opencode.tgz"
tar -xzf "$TMP/opencode.tgz" -C "$TMP"
# Install the first 'opencode' executable found in the extracted tree.
BIN="$(find "$TMP" -type f -name opencode | head -n1)"
if [ -z "$BIN" ]; then
  echo "install-opencode: no 'opencode' binary found in release artifact" >&2
  exit 1
fi
install -m 0755 "$BIN" /usr/local/bin/opencode
echo "install-opencode: installed $(/usr/local/bin/opencode --version 2>/dev/null || echo '<unknown version>')"
