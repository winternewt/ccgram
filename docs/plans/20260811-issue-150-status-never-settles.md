## Summary

A topic's status emoji and typing indicator can get stuck reporting "working" on
an agent that is idle or finished. Three distinct mechanisms produce that one
symptom; all three are in the status-polling and activity-tracking layer, and all
three are backend-independent (found on herdr, but nothing in the causes is
herdr-specific).

**Environment:** ccgram 4.5.1, herdr 0.8.0 / socket protocol 19, Claude Code
2.1.226, Python 3.14.7, Ubuntu 22.04.5, `CCGRAM_STATUS_MODE=user`.

---

## Failure 1 — a settled window falls back into the startup grace

**Observed.** The topic stays on the "active" emoji and the typing indicator runs
forever, on a window whose agent is idle at its prompt.

**Expected.** A finished agent's topic goes idle and the typing indicator clears.

**Ruling out the status sources first.** Replayed against the live capture, both
terminal parsers return nothing for that pane:

```
pyte status:     None
terminal status: None
```

and herdr's native agent status reads `idle`, which `_native_agent_status` maps
to `None` anyway — it only synthesizes a `StatusUpdate` for `working` and
`blocked`. So `resolved_status_text` is `None` and `is_recently_active` is False
(the transcript had not been written to in hours). The status did not come from
any status source.

**Mechanism.** `decide_tick`, given "no status, no activity, not a shell prompt,
has not seen status, no startup timer", returns `starting` — and
`_apply_starting_transition` paints the topic **active** and sends a typing
indicator, which is right for a window that is genuinely booting.

The trap is how a window *leaves* that state. Both terminal transitions called
`cancel_startup_timer`, which clears `startup_time` but leaves `has_seen_status`
False — precisely the input state above. Replayed against the real decision
kernel:

```
after idle/done, next tick -> starting
31s into grace              -> idle
```

A 30-second sawtooth: active for 30s, one tick of idle, active again. The
topic-emoji debounce (5s to active, 30s to idle) swallows the single idle tick,
so the topic reads permanently active and the typing indicator never clears.

`_apply_done_transition` did call `mark_seen_status` — but only
`if not supports_hook`. So hook-backed Claude, the provider whose completion
ccgram observes most reliably, was the *only* one that kept looping. A window
whose transcript ccgram ever saw go active escapes by luck, because
`is_recently_active` marks the flag as a side effect.

## Failure 2 — a restart repaints every topic slowly

*(found while verifying the previous one)*

**Observed.** After a restart, every bound topic reads active with a typing
indicator for about a minute before settling, however long its agent has been
idle.

**Expected.** A restart repaints topics to their real state promptly.

**Mechanism.** Two stacked delays, neither doing its job in this case:

- **30s startup grace.** Poll state is in-memory, so an inherited window has no
  `has_seen_status` and no startup timer — the same shape as one ccgram just
  launched. It enters the grace, which paints the topic active and types. But the
  window is not booting; ccgram simply has no state for it yet.
- **30s rename debounce.** The first observed state then waits out the idle
  debounce. That debounce damps flicker *between* observed states; on the first
  sighting after a restart there is no earlier state to flicker against, and the
  emoji on screen was painted by a process that no longer exists.

## Failure 3 — replayed transcript history counts as live activity

*(found while verifying the previous one)*

**Observed.** After a restart, a topic quiet since earlier repaints immediately,
but a topic whose agent wrote anything shortly before the restart still reads
active with a typing indicator. The asymmetry is the diagnostic.

**Expected.** Messages that arrived while ccgram was down are delivered; the
agent is not reported as working *now* on account of them.

**Ruling out the obvious suspects.** Measured against the live panes, not
assumed: both status parsers return `None` for the affected pane even while its
agent is actively working; the pyte buffer does not go stale across frames
(feeding a busy frame then a quiet one still yields `None`); and `events_offset`
is persisted in `monitor_state.json`, so hook events are not replayed at startup
either. That leaves `is_recently_active`.

**Mechanism.** `monitor_state.json` persists `last_byte_offset` per transcript;
the reader's mtime cache is in-memory. So the first poll after a restart passes
the mtime guard and reads from the persisted offset — replaying whatever the
previous run never consumed. `_process_session_file` then did:

```python
if new_entries:
    self._idle_tracker.record_activity(session_id)
```

Those entries are history, but the stamp is *now*. Any session that wrote
anything before the restart looked busy for the full 10s activity window — active
topic, typing indicator — and the 30s idle debounce held that state for a further
half-minute. A session whose offset was already current replayed nothing and
repainted correctly, which is exactly the asymmetry observed.

---

## Proposed fix

- A window that reaches `idle` or `done` is marked *settled*, not merely
  stopped — and `cancel_startup_timer` is removed rather than left as a trap that
  reproduces this at its next call site. The `supports_hook` gate on `done` goes
  too: a window that reached done has finished starting, whoever reported it.
- Windows inherited from a previous run are settled at startup, and their topics
  seeded for one debounce-free repaint. Busy windows are unaffected — status or
  transcript activity returns them to active on the next tick. The "first call
  starts the debounce" contract is left intact; the allowance is an explicit
  one-shot seeded only at startup, so the six tests pinning that contract still
  hold unchanged.
- The catch-up read no longer touches the activity clock. The discriminator is
  `session_id not in self._file_mtimes` — true only for a session that is in
  persisted state but has not been read in this process. Entries still flow on,
  so messages that arrived while ccgram was down are still delivered; only the
  "working right now" claim is dropped.

I have a branch with this implemented, three commits (one per failure above),
`6477 passed` with ruff/lazy-import gates clean. Happy to open it as a PR against
this issue.
