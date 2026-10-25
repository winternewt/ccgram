# Plan: Ephemeral tool-progress message (OpenClaw "progress mode")

## Goal

Replicate OpenClaw's Telegram `streaming.mode: progress` behavior: while the agent
works, show a single rolling "tools/progress" message; when the turn completes,
**delete** that message, leaving only the final reply. Result: live visibility
during work, clean thread after.

Source of truth — OpenClaw docs (`docs.openclaw.ai/channels/telegram`):

> "progress mode keeps one editable status draft for tool progress, clears it at
> completion, and sends the final answer as a normal message."

(Perplexity's `showProgressMessage` / `deleteProgressMessageOnComplete` config keys
were hallucinated — ignore them. The real axis is `streaming.mode`.)

## Decisions (confirmed with user)

1. **Tool-deletion only.** ccgram sends the reply one-shot when complete
   (`message_routing.py:95` enqueues only `is_complete` messages; partial chunks
   are discarded). Live-streaming the reply body is out of scope — would require
   capturing discarded partial transcript text + a new draft + throttling.
2. **Single rolling FIFO.** One tools message; drop oldest entries on overflow
   instead of overflowing to a new message. Deleted on completion.
3. **Claude + Pi.** Falls out for free — both emit `content_type` `tool_use`/
   `tool_result` (`pi_format.py` translates Pi's `toolCall`). Codex/Gemini/Shell
   emit no tool stream, so they're naturally excluded; no per-provider branching.

## What already exists (reuse, don't rebuild)

- `tool_batch.py` — batches `tool_use`/`tool_result` into one message, edited in
  place (currently via `DraftStream`). ~80% of OpenClaw's machinery.
- `delete_message` / `edit_message_text` / `send_message` — confirmed documented
  PTB methods (Context7, v22.5). The ephemeral path uses only these; deletion is
  `client.delete_message` on the tracked `telegram_msg_id` (the deletion lever).
- Flush is already triggered at turn end on the same per-user FIFO queue:
  - `flush_if_active` runs before the final text ContentTask (`message_queue.py:297`).
  - `_flush_batch_for_task` runs on the Stop-driven `StatusUpdateTask`
    (`message_queue.py:334`).
    So a flush that _deletes_ instead of _finalizes_ lands the deletion exactly when
    the reply appears, with no new trigger wiring.

## The gap

Today `flush_batch` calls `draft.finalize()` → tool message kept permanently
(`tool_batch.py:506`). And overflow at 9 entries / 2800 chars flushes to a **new**
message (`_handle_tool_use_event`, `tool_batch.py:459-467`). Two behavior changes
needed, both gated on a new mode.

## Implementation

### 1. Add the third batch mode — `window_state_store.py`

- `BATCH_MODES = frozenset({"batched", "verbose", "ephemeral"})`.
- `ephemeral` = batched display + FIFO-rolling + delete-on-complete.
- Decide default via global config (see §5). Per-window value still in `WindowState.batch_mode`.

### 2. Batching eligibility — `tool_batch.py` + `window_query.py`

- `is_batch_eligible` currently checks `get_batch_mode(window_id) == "batched"`
  (`tool_batch.py:82`). Add `ephemeral` to the truthy set. Add a helper, e.g.
  `is_ephemeral_tools(window_id)` reading `get_batch_mode == "ephemeral"`.

### 3. FIFO-drop-oldest on overflow — `tool_batch.py`

- In `_add_tool_use_entry` (line 376), when mode is ephemeral and the new entry
  would overflow: pop oldest entries from the front until it fits, recompute
  `total_length`, append. Never return overflow (no flush-to-new-message).
- `_handle_tool_use_event` (line 437): skip the overflow→flush→new-batch branch
  when ephemeral.
- **Edge:** dropping a `tool_use` whose `tool_result` hasn't arrived → the later
  result won't match. In `_handle_tool_result` (line 352), the no-match path
  currently flushes + delivers the result as content (line 372). In ephemeral
  mode, **drop the orphan result silently** instead — never emit a permanent
  content message for it.

### 4. Delete instead of finalize on flush — `tool_batch.py`

**Confirmed-API constraint (Context7 / python-telegram-bot v22.5):** documented
methods are `send_message`, `edit_message_text`, `delete_message`. `send_message_draft`
exists but is documented only for "streaming partial text messages" — there is **no
documented method to cancel or delete an un-finalized draft.** So the ephemeral
tools message must NOT use the draft path: a draft we intend to delete has no
documented teardown.

- For ephemeral mode, send the rolling tools message via the plain confirmed path
  — `send_message` on first entry, `edit_message_text` on each update (reuse
  `safe_send` / `edit_with_fallback` in `message_sender.py`), tracking
  `telegram_msg_id`. No `DraftStream`.
- `flush_batch`: when ephemeral, call `client.delete_message(chat_id, telegram_msg_id)`.
  No prior message → nothing to delete.
- Net effect: one normal message, edited in place during the turn, deleted on
  completion — every call a documented method. Drops the draft/legacy branch and
  the `_DRAFT_UNAVAILABLE` interplay from this path entirely.

### 5. User control — global default + per-window toggle

- Global default: add a config flag (e.g. `config.ephemeral_tools`, env
  `CCGRAM_EPHEMERAL_TOOLS`, default off) feeding `DEFAULT_BATCH_MODE` resolution —
  mirror the `hide_tool_calls` global-default + per-window-override precedent
  (`window_query.is_tool_calls_hidden`).
- Per-window: extend the `/verbose` command (`topic_commands.py:24`,
  `session.cycle_batch_mode` at `session.py:675`) from a 2-way toggle to a 3-way
  cycle `batched → ephemeral → verbose → batched`, and update its help text.
  **Decided:** 3-way `/verbose` cycle (no new command surface). The verbose_command
  reply (`topic_commands.py:52-61`) currently has a two-branch if/else — extend to
  three branches with a clear ephemeral message.

## Edge cases to handle / test

- **Tools-only turn (no trailing text).** Stop's `StatusUpdateTask` flushes →
  deletes the tool message; the completion status bubble remains as the only trace.
  Acceptable per "clean thread" goal — assert the tool message is gone and the
  status/completion text is present.
- **StopFailure / no Stop.** Batch persists until next flush trigger (unchanged
  from today). Verify a failed turn doesn't silently erase the only error trail —
  if no reply follows, the deletion shouldn't outrun the error surface.
- **Orphan tool_result after FIFO drop** — silently dropped (§3), no content leak.
- **Ordering race** (Stop in tick N, text in tick N+1): the Status-driven flush
  deletes the tool message, status bubble bridges the gap, reply lands next tick.
  Pre-existing status behavior — do NOT expand scope to fix status bubble timing.

## Verification

- Unit: `_add_tool_use_entry` FIFO eviction (entry-count and char-budget overflow);
  orphan `tool_result` dropped, not delivered.
- Unit: `flush_batch` in ephemeral mode calls `delete_message` on the tracked
  msg_id (not `finalize`/`edit`). Use `FakeTelegramClient` to assert delete recorded.
- Unit: ephemeral path sends via `send_message` + `edit_message_text` only — no
  draft API touched (assert against `FakeTelegramClient`).
- Integration: simulate a Claude turn (tool_use ×N → tool_result ×N → final text)
  in ephemeral mode; assert exactly one final reply message remains, no tool
  message. Repeat with a Pi-shaped transcript.
- Integration: tools-only turn → tool message deleted on Stop, completion status
  present.
- Full gate: ruff + pyright + pytest.

## Out of scope

- Live-streaming the reply body (decision 1).
- Synthesized "progress" for Codex/Gemini/Shell (no tool stream).
- Changing status-bubble timing/lifecycle.
