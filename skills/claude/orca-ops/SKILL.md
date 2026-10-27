---
name: orca-ops
description: Operate the Orca IDE from any agent on this machine (crossroads) — list/split/close terminal panes, run a command in a shell pane, hand a prompt to an agent in another pane, read a pane, spawn a worktree with an agent. Use whenever a task says Orca, pane, split, "tell the other agent", handoff, or worktree.
---

# Orca operations (crossroads, local only)

Remote machines (dreammaker) are not covered yet: there is no Orca on dreammaker.

## CLI name

Always run `orca-ide` for every command below. Never run bare `orca`: outside an Orca terminal it can be the GNOME screen reader and starts speech on the user's machine. Add `--json` and read the JSON; do not parse the human output.

Anything not listed here: `orca-ide skills get orca-cli` (full guide, about 26 KB — read it only when this page is not enough), or `orca-ide <command> --help`.

## Rules

1. Look before you act: `orca-ide terminal list --json` first. Use `title`, `agentIdentity` (claude, pi, codex...) and `worktreePath` to pick the pane. The `preview` text in that list can be stale or wrong; the real screen is `terminal read`.
2. Address a pane by one handle only (`term_...`). Handles change after an Orca restart (`terminal_handle_stale`): list again and use the new one. Never send to both.
3. Never type into your own pane (`$ORCA_TERMINAL_HANDLE`).
4. Never start an agent inside a pane that is meant for shell commands. To start an agent, use `agent launch` or `worktree create --agent` (below).
5. Do not close panes or worktrees you did not create. `terminal close --all`, `worktree rm` and similar need the user's go-ahead.
6. Silence is not failure: do not resend a prompt because nothing came back. Wait, then read.
7. A program needs a few seconds to start. After launching something in a pane, do not judge it from the first `terminal read`. Wait 5–10 seconds, or check `orca-ide terminal show --terminal <handle> --json` and look at `agentIdentity` (it becomes `opencode`, `codex`, ... once the agent is up), then read the screen. Do not report "not started" from a read taken right after the send.

## Send safely: use the script

`~/.agents/skills/orca-ops/scripts/orca-send.sh` reads the live screen and refuses (exit 2, prints `REFUSED: ...`) instead of guessing.

```bash
orca-send.sh shell <handle> '<command>'   # plain shell pane; refuses if an agent runs there or no prompt is showing
orca-send.sh agent <handle> '<prompt>'    # pane running an agent; refuses if no agent is detected
```

If it refuses, read the pane (`terminal read`) and decide; do not work around it with a raw `terminal send`.

## Recipes

Split a pane (new shell next to an existing one; `vertical` = top/bottom, `horizontal` = left/right):
```bash
orca-ide terminal split --terminal <handle> --direction horizontal --json   # result.split.handle is the new pane
```

Split and run a command in one step (from `--help`, not yet run here; the new pane is a fresh shell, so this is also the way to start a CLI such as opencode in its own pane):
```bash
orca-ide terminal split --terminal <handle> --direction vertical --command 'OPENCODE_DISABLE_CLAUDE_CODE=1 opencode --standalone' --json
```
opencode must always start with `--standalone` here: the background service is off on purpose, and starting it resumes old sessions.

Run something in the new shell, then read it:
```bash
orca-send.sh shell <new-handle> 'make test'
orca-ide terminal wait --terminal <new-handle> --for exit --timeout-ms 60000 --json   # only if the command exits the shell; otherwise just read after a pause
orca-ide terminal read --terminal <new-handle> --json    # screen text is in result.terminal.tail
```

Ask an agent that is already running in another pane, and get its answer back:
```bash
orca-ide terminal list --json                                  # find the pane (agentIdentity set)
orca-ask.sh <handle> '<question or task, with file paths>' [timeout_seconds]   # prints the complete answer
```
`orca-ask.sh` is the way to get a reply. A screen read cannot return it: `terminal read` shows only the visible rows of the pane (about 15–50 lines), so a long answer is cut off and the top is lost. The script tells the agent to write its answer to a file, waits for the agent to go idle, and prints the file. Exit codes: 2 refused, 3 agent went idle without writing the file (read the pane to see why), 4 timeout. Do not retry a timed-out ask by resending; read the pane first.

To send without needing an answer, use `orca-send.sh agent <handle> '<prompt>'`. The receipt may stop at `input_accepted`: the text arrived, not that the turn started. If the agent shows a permission or trust menu, the send is refused: show the user, do not answer the menu yourself.

Is the other agent ready? Three checks, in order:
1. `orca-ide terminal show --terminal <handle> --json` : `agentIdentity` is set (the agent process is up) and `connected`/`writable` are true.
2. `orca-ide terminal wait --terminal <handle> --for tui-idle --timeout-ms 30000 --json` : returns `satisfied: true` when the agent is idle (instantly if it already is; it blocks while the agent is working; checked 2026-10-27 with opencode).
3. Idle is not "has finished my task": right after a send, the agent may not have started its turn yet and still looks idle. That is why `orca-ask.sh` judges completion by the answer file, not by idle alone.

Start a new agent in the current worktree:
```bash
orca-ide agent launch --agent pi --prompt '<task>' --json
```

Spawn a worktree (new checkout) with an agent. Independent work: always `--no-parent`, and do not pass `--base-branch` unless the user asked.
```bash
orca-ide worktree create --name <short-name> --no-parent --agent pi --prompt '<task brief>' --json
```
Report the new worktree id and the agent handle, then stop; do not wait for the agent to finish unless asked. List worktrees with `orca-ide worktree ps --json`.

## Not verified on this machine

Checked 2026-10-27: `terminal list/show/read/split/close`, `terminal wait --for tui-idle` (against opencode), `orca-send.sh` (shell accepted, agent/own-pane/busy-shell refused, agent mode accepted), `orca-ask.sh` (opencode, answer returned; own-pane refused), and Pi splitting its own pane to start opencode and send it a prompt. Not run: `split --command` outside that one case, `agent launch`, `worktree create` — the command forms come from `orca-ide <command> --help`.
