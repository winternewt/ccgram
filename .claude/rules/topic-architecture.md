# Topic-Only Architecture

The Telegram adapter operates in Forum topics mode. Its topic routing remains separate from the optional mobile session list in `session_protocol/`. `GET /v1/sessions` includes eligible targets with no Telegram topic. No Telegram `active_sessions` store or non-topic routing is introduced.

## 1 Topic = 1 Window = 1 Session

Topic ID (Telegram) → Window ID (tmux `@id`) via `thread_bindings` in `state.json`.
Window ID → Session ID (Claude) via `session_map.json` (written by hook).

Window IDs (e.g. `@0`, `@12`) are unique within a tmux server session. Window names are display labels in `window_display_names`.

## Mapping 1: Topic → Window ID

In `session.py: SessionManager`:

```python
thread_bindings: dict[int, dict[int, str]]  # user_id → {thread_id → window_id}
window_display_names: dict[str, str]        # window_id → window_name
```

Storage: memory + `state.json`. Written when user creates a session via the directory browser in a topic. Purpose: route user messages to the correct tmux window.

## Mapping 2: Window ID → Session

In `session_map.json` (key format `"tmux_session:window_id"`):

```json
{
  "ccgram:@0": {
    "session_id": "uuid-xxx",
    "cwd": "/path",
    "window_name": "project",
    "provider_name": "claude",
    "transcript_path": "..."
  },
  "ccgram:@5": {
    "session_id": "uuid-yyy",
    "cwd": "/path",
    "window_name": "project-2",
    "provider_name": "codex",
    "transcript_path": "..."
  }
}
```

Written when Claude Code's `SessionStart` hook fires (always sets `provider_name: "claude"`; other providers have no hook). All hook events also append to `events.jsonl` for instant dispatch.

One window maps to one session; session_id changes after `/clear`. SessionMonitor reads session_map for which sessions to watch, and `events.jsonl` for instant event notifications (interactive UI, done detection, subagent status).

## Message Flows

Outbound (user → Claude):

```
User sends "hello" in topic (thread_id=42)
  → thread_bindings[user_id][42] → "@0"
  → TelegramSessionAdapter.send_text("@0", "hello", action_id=...)
  → ActionExecutor: verified binding, prompt checks, per-target input lock
  → MultiplexerSessionBackend → backend window
```

Inbound (Claude → user):

```
SessionMonitor reads new message (session_id = "uuid-xxx")
  → Iterate thread_bindings, find (user, thread) whose window_id maps to this session
  → Deliver to that thread_id
```

New topic flow: first message in unbound topic → directory browser → select directory → worktree picker (only if eligible git repo: in-work-tree, not bare, on named branch, no in-progress merge/rebase) → select provider → create window with chosen provider (rooted at worktree path when created) → bind topic → forward pending message.

Topic lifecycle: closing/deleting a topic auto-kills the tmux window and unbinds the thread. Stale bindings (window deleted externally) cleaned up by the status polling loop.

## Session Lifecycle

Startup cleanup: all tracked sessions not present in `session_map.json` are cleaned up.

Runtime change detection: each polling cycle checks for session_map changes — window's session_id changed (e.g., after `/clear`) → clean up old session; window deleted → clean up corresponding session.
