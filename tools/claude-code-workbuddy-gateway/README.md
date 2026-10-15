# claude-code-workbuddy-gateway

The Claude Code edition of the `workbuddy-executor` skill from [dudu555-djiao/codex-workbuddy-gateway](https://github.com/dudu555-djiao/codex-workbuddy-gateway) v0.7.0.

Claude Code plans the work, writes the brief and does QA. The WorkBuddy desktop app on your Mac executes the tasks.

> This folder is a standalone developer tool kept in this repository. It is not part of the stock-analysis application: nothing under `src/`, `api/` or `apps/` imports it, the Docker image does not copy it, and it is not a repository skill under `.claude/skills/`. Install it into your user-level Claude Code config with `install.sh`.

```
Claude Code ─MCP→ agent-hub (127.0.0.1:18492) → task queue → persistent service ─CDP wake→ WorkBuddy session
     ↑                                                                                         │ does the work
     └── verify real files / run tests ← report + artifacts ← claim/report helper ←────────────┘
          rework ↺  accept (with evidence)
```

## What this port changes

The gateway runtime is reused **unmodified**. Its MCP servers are plain stdio servers and work with any MCP client. Only the Codex-specific layer is replaced:

| Codex original | Claude Code edition |
| --- | --- |
| `npm run skill:install` → `~/.codex/skills/` | `./install.sh <gateway>` → `~/.claude/skills/` (honours `CLAUDE_CONFIG_DIR`) |
| `codex mcp add workbuddy -- …` | `claude mcp add --scope user agent-hub` and `workbuddy` (an existing registration is never overwritten) |
| `agents/openai.yaml` (Codex UI metadata) | Removed. Claude Code reads the SKILL.md frontmatter, and `allowed-tools` pre-approves the read-only hub tools (get, wait, events and so on) |
| `$workbuddy-executor` | `/workbuddy-executor`, or triggered automatically from the description |
| Rules for a supervisor | Same contract, plus an explicit QA gate: git-diff scope check, rerunning tests, comparing claimed results with your own, a 3-round rework cap, a plan for decomposing complex projects, and package tracking with TaskCreate |

Left alone on purpose: the `[CODEX_TASK]` and `[CODEX_QUEUE_WAKE …]` strings inside the gateway. They are protocol markers, matched by a regex in the code injected into WorkBuddy. Renaming them would break the wake path and gain nothing.

## Install (on the Mac that runs WorkBuddy)

See [skills/workbuddy-executor/references/setup.md](skills/workbuddy-executor/references/setup.md). In short:

```sh
git clone https://github.com/dudu555-djiao/codex-workbuddy-gateway ~/tools/codex-workbuddy-gateway
cd ~/tools/codex-workbuddy-gateway && cp -n .env.example .env   # set WORKBUDDY_BACKEND=desktop-queue
npm ci && npm run build && npm test && ./bin/agent-hub install
cd /path/to/daily_stock_analysis/tools/claude-code-workbuddy-gateway && ./install.sh ~/tools/codex-workbuddy-gateway
# restart Claude Code → /mcp → both servers connected
```

## Verified so far (Linux container, no WorkBuddy)

- Upstream build with `tsc` passed; upstream tests: 166/166 passed (mocked desktop).
- MCP handshake from a client identifying as `claude-code` listed all 14 `workbuddy_*` and 12 `agent_hub_*` tools.
- `install.sh`: a fresh install, an idempotent re-run, backup of a modified skill, and rejection of a non-gateway directory all passed. `claude mcp list` (Claude Code 2.1.300) reported `agent-hub` and `workbuddy` as **√ Connected**.

**Not verified:** a real task executed by WorkBuddy. That needs macOS with WorkBuddy logged in; run the acceptance steps in `setup.md`.

## Notes

- **License:** the upstream repo has no LICENSE file, which by default means all rights reserved. This port contains none of upstream's code or text. It calls the gateway's public MCP interface and asks you to clone the gateway yourself. Before publishing a renamed fork of the gateway itself, get the author's permission.
- **Security:** the persistent service opens a loopback Chrome DevTools port into WorkBuddy (18491 by default). Any process running as your user can drive WorkBuddy through it. Use it only on a machine you trust.
