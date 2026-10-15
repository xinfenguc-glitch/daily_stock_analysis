# Setup: Claude Code → WorkBuddy

Read this only for first-time setup, when the `mcp__agent-hub__*` tools are missing, or when the connection fails.

## Requirements

- macOS, with the WorkBuddy desktop app installed, logged in, and with model credit available.
- Node.js 22.13 or newer (24 LTS recommended) and npm.
- Claude Code (CLI or desktop app) running **on the same Mac**. A cloud session at claude.ai/code cannot reach WorkBuddy, because the bridge talks to WorkBuddy over loopback only.

## 1. Gateway (upstream project, unmodified)

```sh
git clone https://github.com/dudu555-djiao/codex-workbuddy-gateway ~/tools/codex-workbuddy-gateway
cd ~/tools/codex-workbuddy-gateway
cp -n .env.example .env          # keeps an existing .env
# in .env:  WORKBUDDY_BACKEND=desktop-queue
npm ci && npm run build && npm test
./bin/agent-hub install           # local hub service on 127.0.0.1:18492
./bin/agent-hub status
```

Keep the directory in a stable location, because the MCP registration stores its absolute path.

If WorkBuddy or Claude Code cannot find `node` because it comes from nvm and only exists in an interactive shell, run `command -v node` and put the result in the gateway's `.env` as `WORKBUDDY_NODE_BIN='/abs/path/to/node'`. Never commit `.env`.

Skip the upstream `npm run skill:install`. It installs the Codex version of this skill into `~/.codex`.

## 2. This skill and the MCP registrations

```sh
./install.sh ~/tools/codex-workbuddy-gateway
```

This copies the skill to `~/.claude/skills/workbuddy-executor`, backing up any version that differs, and fills in the gateway path. It then registers two user-scope MCP servers, skipping any that already exist:

- `agent-hub` → `<gateway>/bin/agent-hub-mcp`: the main route for new tasks.
- `workbuddy` → `<gateway>/bin/workbuddy-gateway`: the legacy direct route, for older task ids.

To do the same by hand:

```sh
claude mcp add --scope user agent-hub -- /abs/gateway/bin/agent-hub-mcp
claude mcp add --scope user workbuddy -- /abs/gateway/bin/workbuddy-gateway
```

Restart Claude Code, then run `/mcp`. Both servers should show as connected.

## 3. First real run (acceptance)

A connected MCP server, a live hub or a `queued` run does **not** prove that WorkBuddy executes anything. Prove it end to end with a small task:

1. Ask: "Use /workbuddy-executor to have WorkBuddy create `checklist.md` with 5 items in `/tmp/wb-smoke`."
2. Watch the run reach `running`, then `completed`, and check that `runtimeSource` is `desktop_queue`.
3. Read the file yourself, then send one rework (for example, "add a 6th item about X"). Confirm the change lands in the same session.
4. Accept the run with evidence.
5. Optional: quit WorkBuddy normally, dispatch another small task, and confirm the service relaunches it and the task gets picked up.

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Tools missing in Claude | The MCP isn't registered or Claude hasn't restarted. Run `claude mcp get agent-hub` and restart. In the meantime, use `scripts/agent-hub-call.mjs`. |
| Every call errors with UNAUTHORIZED or connection refused | The hub isn't running. Run `./bin/agent-hub status`, then `./bin/agent-hub install`. |
| Run stuck in `queued` | WorkBuddy is closed, logged out, or showing a permission dialog. Check `./bin/workbuddy-service status`. The user has to clear dialogs in WorkBuddy. |
| `workspace` rejected | The path must be an existing absolute directory, and it must match the bound session's directory. |
| Model error | Use an ID from `agent_hub_models`, not a display name. The session must be idle to switch models. |
| Broke after a WorkBuddy update | The private desktop RPC changed. Check the upstream repo for a newer release. |

## Security notes

- Everything listens on loopback only. The hub requires a bearer token stored in a private local file.
- The persistent service launches WorkBuddy with a loopback Chrome DevTools port (18491 by default). Any process running as your user can drive WorkBuddy through that port while it is open. Run the setup only on a machine you trust.
- The service does not read WorkBuddy login credentials and does not modify `app.asar`.
