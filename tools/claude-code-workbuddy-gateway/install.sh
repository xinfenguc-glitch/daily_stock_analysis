#!/bin/sh
# Install the Claude Code edition of the workbuddy-executor skill and register
# the codex-workbuddy-gateway MCP servers with Claude Code (user scope).
# Usage: ./install.sh /absolute/path/to/codex-workbuddy-gateway
set -eu

[ $# -eq 1 ] || { echo "usage: $0 /absolute/path/to/codex-workbuddy-gateway" >&2; exit 2; }
GATEWAY=$(CDPATH= cd -- "$1" && pwd)
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC="$HERE/skills/workbuddy-executor"
SKILLS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
DEST="$SKILLS/workbuddy-executor"

# Refuse README-only or unbuilt checkouts.
for f in package.json bin/agent-hub-mcp bin/workbuddy-gateway dist/platform/mcp.js dist/server.js scripts/agent-hub-call.mjs; do
  [ -e "$GATEWAY/$f" ] || { echo "error: $GATEWAY/$f missing - clone the full gateway and run 'npm ci && npm run build' first" >&2; exit 1; }
done
case "$GATEWAY" in *'|'*|*'&'*) echo "error: gateway path must not contain '|' or '&'" >&2; exit 1;; esac

# Stage with the gateway path filled in.
mkdir -p "$SKILLS"
STAGE=$(mktemp -d "$SKILLS/.workbuddy-executor-install-XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/references"
sed "s|__GATEWAY_DIR__|$GATEWAY|g" "$SRC/SKILL.md" > "$STAGE/SKILL.md"
cp "$SRC/references/setup.md" "$STAGE/references/setup.md"

if [ -d "$DEST" ] && diff -rq "$STAGE" "$DEST" >/dev/null 2>&1; then
  echo "skill: unchanged at $DEST"
else
  if [ -e "$DEST" ]; then
    BACKUP="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skill-backups/workbuddy-executor-$(date +%Y%m%d%H%M%S)"
    mkdir -p "$(dirname "$BACKUP")"
    mv "$DEST" "$BACKUP"
    echo "skill: previous version backed up to $BACKUP"
  fi
  mv "$STAGE" "$DEST"
  echo "skill: installed to $DEST"
fi

# Register MCP servers; never overwrite an existing registration.
command -v claude >/dev/null 2>&1 || { echo "warning: 'claude' CLI not found; register the MCP servers manually (see references/setup.md)" >&2; exit 0; }
register() {
  if claude mcp get "$1" >/dev/null 2>&1; then
    echo "mcp: '$1' already registered - left unchanged (check with: claude mcp get $1)"
  else
    claude mcp add --scope user "$1" -- "$2"
  fi
}
register agent-hub "$GATEWAY/bin/agent-hub-mcp"
register workbuddy "$GATEWAY/bin/workbuddy-gateway"
echo "done. Restart Claude Code and run /mcp to confirm both servers are connected."
