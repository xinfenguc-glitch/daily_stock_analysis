---
name: workbuddy-executor
description: Delegate execution work to the local WorkBuddy desktop app through the agent-hub MCP server (from codex-workbuddy-gateway), with Claude Code acting as planner and quality gate - writing the brief, dispatching, monitoring, verifying the real output, sending rework and recording acceptance. Use when the user asks to hand a task to WorkBuddy, have WorkBuddy build/write/produce something, check on or review a WorkBuddy run (hub-... id), or run a multi-step project with WorkBuddy executing and Claude doing QA. Do not use for questions about how the bridge works, or for work the user wants Claude to do directly.
allowed-tools: mcp__agent-hub__agent_hub_providers, mcp__agent-hub__agent_hub_models, mcp__agent-hub__agent_hub_workspaces, mcp__agent-hub__agent_hub_get, mcp__agent-hub__agent_hub_list, mcp__agent-hub__agent_hub_wait, mcp__agent-hub__agent_hub_events
---

# WorkBuddy Executor - Claude Code edition

Gateway install: `__GATEWAY_DIR__`

WorkBuddy does the work in a real desktop session bound to the project directory, using its own models, tools and skills. You are the project manager and QA: you write the brief, dispatch it, watch it cheaply, check the actual output yourself, send concrete rework, and record acceptance with evidence.

Ground rules:

- Never do delegated work yourself and present it as WorkBuddy's. If you take a task over, say so and name who did what.
- Never report a WorkBuddy result as done because WorkBuddy said so. `completed` means its turn ended and the report parsed. Acceptance comes from your own checks.
- Delegating does not widen what the user authorized. Pass the user's scope limits into every brief.
- Do not spawn Claude subagents to do work that is already dispatched to WorkBuddy.

## 1. Before the first dispatch in a session

1. If no `mcp__agent-hub__*` tools are loaded, read [references/setup.md](references/setup.md). As a stopgap, call the same tools through Bash (see section 9).
2. Call `agent_hub_providers` once. It shows whether the hub can reach WorkBuddy. A live hub does not prove WorkBuddy is logged in or has credits.
3. Pin down the absolute path of the business project. That path is the `workspace`. Never use the gateway directory or a guess.
4. If you are resuming, recover by id first (`agent_hub_get`, or `agent_hub_list` with a small limit). Never resubmit a goal that may already be running.

## 2. Write the brief

One `agent_hub_submit` call per work package:

```json
{
  "provider": "workbuddy",
  "workspace": "/absolute/project/dir",
  "task": "<brief, see below>",
  "output": "artifacts",
  "constraints": ["Only modify files under src/report/ and tests/report/", "Do not change config, .env, the gateway, or write memory"],
  "idempotencyKey": "<project>:<package-slug>:v1",
  "timeoutSeconds": 1800
}
```

`output` is `text`, `json` or `artifacts`. Leave out `workspace` only for a standalone job; the hub then assigns an isolated directory. Keep the `idempotencyKey` stable. Resubmitting with the same key and request returns the original run. Never change the key to get around a run whose state is unknown.

The `task` text must contain:

1. **Objective**: one or two sentences on the outcome, not the steps.
2. **Context and inputs**: which files or data to read, and any background WorkBuddy can't infer.
3. **Scope**: what may be changed, and what must not be touched.
4. **Deliverables**: exact absolute paths, or named files, sections and functions.
5. **Acceptance criteria**: checks that pass or fail, such as commands that must succeed, required sections, numeric checks, and "no changes outside X".
6. **Report**: status, a summary of at most 300 words, artifact paths, the commands run with their real results, and open problems. Long logs stay in files and are not pasted back.

Let WorkBuddy break the work down internally. Don't micro-manage individual tool steps.

## 3. Monitor cheaply

Save the returned `hub-...` id and tell the user. Then loop `agent_hub_wait({id, seconds: 45})`:

| State | What to do |
| --- | --- |
| `queued` / `dispatching` / `submitted` | Not started yet. Keep waiting. After about 4 waits with no change, call `agent_hub_providers` and `agent_hub_workspaces` once. Then ask the user to look at WorkBuddy: is it closed, logged out, or showing a permission prompt? Do not reinstall and do not resubmit. |
| `running` | WorkBuddy has picked it up. Long silence is normal. Use `agent_hub_events({id, after})` with a cursor if you need progress; don't re-read everything. |
| `completed` | Go to verification (section 4). |
| `blocked` / `failed` | Read the reason. Fix the input, scope or permission first, then rework. Never report success. |
| `cancel_requested` / `uncertain` | Execution may still be live. Check before doing anything else, and never auto-resubmit. |

- Stop waiting once `timeoutSeconds` has passed, and report the task as blocked.
- A wait timing out does not cancel the run. A restart of the hub or of Claude does not authorize a resubmit.
- Between waits, do useful QA prep instead of idling. For example, turn the acceptance criteria into the exact commands you will run.
- Permission and login prompts inside WorkBuddy must be handled by the user in the WorkBuddy window. You cannot answer them.

## 4. Verify (the quality gate)

Check provenance first: `runtimeSource` must be `desktop_queue`, and `providerTaskId`, `attemptId` and `sessionId` must be present. Then check every acceptance criterion with your own tools:

- **Code**: run `git -C <workspace> status` and `git diff --stat`. Confirm every changed path is inside scope. Read the key hunks, then run the project's tests and linters yourself. Compare your results with WorkBuddy's claimed `COMMANDS_RUN` and `TEST_RESULT`; a mismatch is a finding.
- **Documents and files**: open them. Check the required sections and that the content is correct. "File exists and is non-empty" is never enough.
- **Data and analysis**: recompute a sample of the figures from the inputs.
- **Research and facts**: verify the key claims against primary sources. Check anything time-sensitive or high-risk more thoroughly.
- **Large outputs**: inspect the parts that matter, and don't redo the whole job.

Write evidence as concrete observations, for example "ran `pytest tests/report -q` → 37 passed" or "diff touches only src/report/render.py and tests/report/test_render.py (in scope)".

## 5. Rework

Only rework once the round has finished: `completed`, `blocked` or `failed`, and not holding capacity. Call `agent_hub_rework({id, feedback})` with one numbered list. For each item give the problem, where it is, what "fixed" looks like, and how you will re-check it. The original goal, scope and session carry over.

If the same issue survives 3 rounds, stop. Report the cause to the user: an unclear spec, a capability gap, the environment, or missing permission. Don't keep resending.

## 6. Accept

After the checks pass, call `agent_hub_accept({id, note, evidence: ["...", "..."]})`. It only records your review; it does not run checks. If the user wants to review the result themselves, leave it pending and say: "WorkBuddy has finished; waiting for your review."

## 7. Complex projects

1. Break the project into work packages. Each one has a single deliverable, verifiable acceptance criteria, and a dependency order. Show the plan to the user before dispatching.
2. The hub runs one task at a time by default (`AGENT_HUB_MAX_CONCURRENT=1`), and tasks in the same workspace always run in order. Dispatch the next dependent package only after the previous one is accepted.
3. Track each package with TaskCreate/TaskUpdate, recording its hub id, state, rework count and evidence.
4. After each accepted package, run an integration check: the full test suite or build yourself, not just that package's checks.
5. In the final report, list every package with its status, deliverable, evidence, who executed it, and any open issues. Don't claim token or credit savings you haven't measured.

## 8. Models and workspace bindings

- `agent_hub_models({sessionId?})` returns real model IDs. Pass `model` on `agent_hub_submit` or `agent_hub_rework` only with an ID from that list, never a display name.
- `agent_hub_workspaces({})` lists bindings. `agent_hub_bind_workspace({workspace, sessionId?, model?})` creates, reuses or switches a project's session or default model. The session must be idle with nothing queued.
- Changing a binding affects only future tasks. Runs already dispatched, and their rework, stay in their original session.

## 9. When the MCP tools are not loaded in this conversation

Call the same tools through the gateway's client script (JSON inline, or `@file.json`):

```sh
node "__GATEWAY_DIR__/scripts/agent-hub-call.mjs" agent_hub_providers '{}'
node "__GATEWAY_DIR__/scripts/agent-hub-call.mjs" agent_hub_wait '{"id":"hub-...","seconds":45}'
```

## 10. Legacy direct route

If the user already has `workbuddy` task ids that are not `hub-...` ids, resume them through the `workbuddy` MCP server: `workbuddy_wait`, `workbuddy_get_task` (`detail: "brief"`), `workbuddy_continue_task` and `workbuddy_accept_task`, all with the original taskId. Never convert them into new hub runs.

## Limits to state honestly

- macOS only. Upstream verified it on WorkBuddy 5.7.6. It relies on WorkBuddy's private desktop RPC, which a WorkBuddy update can break.
- Nothing runs while the Mac is off or the user is logged out.
- Credit balance and per-task cost are not exposed; report them as unknown, not as zero.
- There is no full tool trace from WorkBuddy. Report contents are self-reported until you verify them.
- Cancellation is cooperative and does not roll back actions already taken.
