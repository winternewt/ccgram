# RCA — Herdr window identity and status that would not settle

**Date:** 2026-08-11
**Branch:** `fix/herdr-identity-and-status` (base: upstream `main` at v4.5.1)
**Environment:** ccgram 4.5.1, herdr 0.8.0 / socket protocol 19, Claude Code
2.1.226, Python 3.14.7, Ubuntu 22.04.5, `CCGRAM_MULTIPLEXER=herdr`,
`CCGRAM_STATUS_MODE=user`.

Seven defects, each reproduced before it was fixed, one commit each. §1 is
**not** one of them — it is upstream's v4.5.1 fix, included because §2 and §3
only make sense against it. A further hypothesis was investigated,
implemented, and then **discarded** when measurement refuted it; the closing
section records it so it is not re-attempted.

| § | Symptom | Root cause |
|---|---------|------------|
| 1 | A herdr tab running an agent never becomes a Telegram topic | `_parse_live_record` drops any record without an `agent_session`, and herdr publishes `agent` first — *fixed upstream in 4.5.1* |
| 2 | Topic shows live spinners and token counts but never receives a reply | One pane is observed under two ids; the hook writes state under one, the topic binds the other |
| 3 | Creating a topic kills an agent that stopped at a trust prompt | Creation demanded a published session within 5s and rolled back at the deadline |
| 4 | "Session did not register in time … topic remains quarantined" on a healthy session | The wait is pinned to the id creation minted; the hook writes under the id that superseded it |
| 5 | A new window loses its cwd, provider, YOLO mode and origin one second after creation | The stale-state sweep cannot tell a window mid-creation from an abandoned one |
| 6 | Topic stays green with a typing indicator forever after the agent finishes | `idle`/`done` cleared the startup *timestamp* without recording that startup happened — a 30s sawtooth |
| 7 | After a restart every topic reads green + typing for about a minute | An inherited window has no in-memory poll state, so it looks like one ccgram just launched; the first rename then waits out the debounce too |
| 8 | After a restart, a topic whose agent wrote anything beforehand *still* reads green + typing | The byte offset is persisted but the mtime cache is not, so the first poll replays unread history — and stamped it as activity *now* |

§1–§5 all follow from one property of the backend: **on herdr, window identity
firms up over time.** §6 and §7 are backend-independent and pre-existing; herdr
only made §6 visible, because it produced a window whose transcript ccgram
never saw go active.

## Upstream issues and branches

Upstream requires an issue before a PR, and agreement on the approach in that
issue before the implementation is submitted (`CONTRIBUTING.md`). The defects
are therefore filed grouped by shared root cause, not one issue per symptom.

| Issue | Covers | Branch (code only, no docs) |
|---|---|---|
| [alexei-led/ccgram#149](https://github.com/alexei-led/ccgram/issues/149) | §2–§5 — one pane observed under two window ids | `fix/herdr-window-identity` (4 commits) |
| [alexei-led/ccgram#150](https://github.com/alexei-led/ccgram/issues/150) | §6–§8 — status that never settles | `fix/status-never-settles` (3 commits) |

Both branch from upstream `main` at v4.5.1 and carry no documentation: this RCA
and the `CHANGELOG` entry live on this fork's `main` only, since upstream
manages its own changelog. `.claude/rules/architecture.md` *is* carried, because
it documents the seams the code introduces.

PRs are deliberately not opened yet — upstream's process is to settle the
approach on the issue first.

---

## 1. A detected agent is invisible until it publishes a session

*Upstream's fix, released in v4.5.1 — not part of this branch. It is the
ground §2 and §3 stand on: it is what mints a target for a pane whose agent
has not reported a session, and that target is what §2 reconciles and §3
binds.*

**Observed.** A new herdr tab running Claude never becomes a Telegram topic.
The pane is live and visible in herdr; Telegram shows nothing, indefinitely.

**Expected.** A tab running an agent becomes a topic. A bare shell tab does
not — that is the distinction the discovery filter exists to draw.

**Root cause.** herdr publishes its two facts about a pane at different times:
`agent` (the CLI it classified) as soon as it detects the process,
`agent_session` only once the agent reports its own session id.
`_parse_live_record` returned `None` whenever `agent_session` was absent, so a
sessionless agent was dropped from the `agent.list` snapshot outright — no
target, no `WindowRef`, nothing for discovery to admit. The gate was never
`is_agent_topic_window`; the record did not survive parsing.

The gap is not brief. An agent that stops for input *before* it has a session
stays there until answered, and Claude's "do you trust the files in this
folder?" prompt appears for every directory not yet trusted.

**Fix.** A record with `agent` but no `agent_session` falls back to a
terminal-derived composite, so the pane gets an opaque target from the moment
herdr classifies it. That target is provisional by construction — the
session-derived one supersedes it once it arrives (§2). A record with no
`agent` at all still yields nothing, so a bare shell tab still does not become
a topic.

---

## 2. One pane, two identities, and nothing reconciles them

**Observed.** A topic bound to a herdr pane shows a live status bubble —
spinner, token counts, activity — and never receives a single agent reply.
Inbound messages reach the pane. Nothing errors, in Telegram or in the log at
default level. Restarting does not help; each restart adds another stale entry
to `state.json`.

**Expected.** Agent replies arrive in the bound topic.

**Root cause.** The asymmetry is the whole clue. Status and delivery are
different code paths with different identity requirements:

| path | what it resolves | needs both sides to agree? |
|---|---|---|
| status bubble | live pane, directly from the backend | no |
| outbound (you → agent) | topic → window id → `send()` | one side only |
| inbound (agent → you) | transcript → session id → **bound** window | **yes** |

Only inbound requires two independently-written records to agree on one key,
and only inbound was broken.

ccgram derives a herdr window id by hashing what herdr publishes about the
pane. Because the two facts arrive at different times, one pane yields two:

| moment | published | composite | id |
|---|---|---|---|
| pane exists, CLI detected | `agent: "claude"` | `{herdr, claude, terminal, <terminal_id>}` | **A** |
| agent reports its session | `agent_session` | `{herdr:claude, claude, id, <uuid>}` | **B** |

The ccgram `SessionStart` hook runs *inside that gap*. It resolves the pane to
**A** and writes `session_map.json` and `window_states` under **A**, while
every later observation — discovery, `list_windows`, the status poll — yields
**B**, which is what the topic binds. Inbound routing then asks whether the
*bound* window carries the session id (`session_resolver.py`); `window_states[B]`
does not exist, so no user matches and the message is dropped at
`message_routing.py`.

Nothing reconciled them. `resolve_stale_ids` returns `False` on its first
statement for any backend with `ids_stable_across_restart=False`, and
`prune_session_map` early-returns for herdr, so `load_session_map` faithfully
recreates `window_states[A]` on every cycle. **The two ids can never converge
on their own**, and because the hook always runs before the agent has reported
a session, the failure is deterministic rather than racy.

**Proof.** Verified live against a running pane by recomputing both digests
with ccgram's own function: one pane, one Claude session, two ids, each holding
exactly half of what delivery needs. `herdr agent list` at that moment returned
a complete `agent_session`, i.e. **B** was current and **A** was what the hook
had captured seconds earlier.

**Fix.** Reconcile, rather than redefine identity:

- `WindowRef.alias_window_ids` lists superseded identities. tmux leaves it
  empty, so nothing about the tmux path changes.
- The herdr adapter publishes **A** as an alias whenever it reports **B** —
  precisely the id a hook running before publication would have written.
- `window_resolver.migrate_window_aliases()` folds `window_states`, thread and
  chat bindings, read offsets and display names onto the current id, filling
  only gaps so the canonical entry never loses what it resolved itself.
- `SessionMapSync.rename_session_map_entry()` re-keys the hook-written entry
  under the same `fcntl` lock the hook uses. Without this half,
  `load_session_map` recreates `window_states[A]` next cycle and the migration
  never sticks.
- `SessionManager.reconcile_window_aliases()` runs from the monitor loop every
  cycle, deliberately not startup-only: these ids are minted whenever an agent
  session starts, long after bootstrap.

**Rejected alternative.** Making identity always terminal-derived. It works —
it was verified live — but it discards the "a session keeps its identity when
it moves panes" guarantee that upstream tests assert, and breaks 20 tests.

---

## 3. Creation kills an agent that is waiting at a prompt

**Observed.** Creating a topic launched the agent, then closed the tab and
killed it a few seconds later. The user saw the agent's trust prompt in
Telegram, answered it, and got "This button has expired".

**Expected.** An agent waiting for input is a healthy agent. The prompt is
answerable from Telegram.

**Root cause.** Creation required herdr to report a session within 5s
(`_CREATED_SESSION_DISCOVERY_TIMEOUT_SECONDS`). An agent that stops for input
before it has a session never satisfies that. At the deadline creation rolled
back, closing the tab while the agent sat at the prompt — so by the time the
user answered, the window was gone and the callback's ownership check could
not pass. The expired-button message was a symptom, not a second defect.

**Fix.** A pane that has not been classified is not a failed creation. herdr
publishes the pane, with its terminal id, from the moment it exists, and that
terminal id is exactly what a later sessionless record hashes — so the target
minted from it is the one the agent will answer to. Creation falls back to that
provisional target instead of rolling back, and the pane keeps running; when
the session arrives, the session-derived target declares the provisional one as
its alias and §2's migration folds the state forward.

- `_pane_locator`/`_provisional_record` mint the terminal-derived target from
  `pane list` when `agent.list` is still silent.
- `guard_session_target` re-resolves a provisional target against its pane, so
  actions work while the prompt is up; a closed pane drops it and fails exactly
  as any dead window does.
- `_agent_list_snapshot` forgets a provisional target as soon as herdr reports
  it, by target id or by alias.

Rollback still happens when the pane itself is gone.

---

## 4. Creation waits on an id nothing will ever write

**Observed.** "❌ Session did not register with ccgram in time … the topic
remains quarantined" on a session that was in fact healthy. Nothing was killed,
and the topic was left bound to a window the flow believed it had failed to
create; the button the user then tapped resolved to a dead token.

**Expected.** Creation completes, or fails cleanly having actually cleaned up.

**Root cause.** Verbatim from the run (ids abbreviated):

```
17:57:51 Window created: 2 (id=…121eb4…) at /home/newton/bob provider=claude mode=yolo
17:57:52 Accepted bypass permissions prompt for window …121eb4…
17:57:53 Reconciled superseded window id …121eb4… -> …e187e5…
17:57:54 Session map: window_id …e187e5… updated sid=1c02cc23-…, cwd=/home/newton/bob
17:57:57 Timed out waiting for session_map entry: window_id=…121eb4…
```

The hook registered the session **three seconds before the timeout**, under
`…e187e5…`. The wait was pinned to `…121eb4…`.

`launch_window` creates the window, persists origin/cwd/provider/mode, binds
the topic, then calls `wait_for_session_map_entry(A)`. Between the bind and the
wait, §2's reconciliation runs on the monitor cycle and renames `A` → `B`
everywhere that matters. That is the previous fix working exactly as designed;
what it cannot do is reach into the local variable of a coroutine that is
mid-`await`. So the wait expires on a key nothing will ever write again — and
the failure path compounds it by calling `kill_window(A)`, which no longer
resolves, so the kill returns `False` and the flow takes the branch that
assumes the target is still alive.

The session itself was never in trouble. It appeared in Telegram later only
because the monitor's unbound-window discovery adopted it independently.

**Fix.**

- `window_resolver` records every supersession it performs in a bounded
  in-memory ledger and exposes `resolve_window_alias`, which walks the chain
  (identity can be superseded more than once) and returns its input unchanged
  when nothing moved — safe to call on any backend.
- The redirect is recorded *before* the reference check: the identity moved
  whether or not any state needed migrating.
- `window_query.resolve_window_alias` is the handler-facing read (the query
  layer is the approved read path).
- `wait_for_session_map_entry` takes an optional `resolve_window_id`,
  **re-applied on every poll**, so the key it watches follows the window.
  Callers on stable-id backends omit it; tmux behaviour is byte-identical.
- `launch_window` passes the resolver and re-points itself via
  `_follow_supersession`, which carries the creation guard to the new id so the
  monitor cannot adopt the window into a second topic during the handover. The
  failure path re-points before killing, so cleanup addresses a window that
  exists.

---

## 5. The stale sweep eats a window that is being created

**Observed.** A new window lost the cwd, provider, YOLO mode and ccgram origin
the creation flow had just written, one second after creation:

```
17:57:51 Window created: 2 (id=…121eb4…) … provider=claude mode=yolo
17:57:52 Removing stale window_state: …121eb4…
17:57:54 Corrected provider for …e187e5…: state= -> claude (session_map claimed claude; …)
```

`state=` is empty in that correction — the provider written one second earlier
was gone. The persisted state confirms the rest: no `origin`, no
`approval_mode`.

**Expected.** Choices the user made during creation survive creation.

**Root cause.** `_remove_stale_window_states` drops any `window_state` that is
(a) absent from `session_map.json` and (b) not bound to a topic. A window being
created is *both*, by construction: its hook has not fired yet and the flow
binds afterwards. On tmux the window is exposed for well under a second; on
herdr the durable session id arrives later, widening the gap enough to hit
reliably.

The losses are not cosmetic. `origin` marks a window as ccgram-created, which
governs whether closing the topic kills it; `approval_mode` drives the 🎲 YOLO
badge; `cwd` feeds recovery.

**Fix.**

- `session_map` gains a `register_in_flight_window_predicate` seam (mirrors
  `register_approval_callback`: single registration, test reset), wired in
  `bootstrap.wire_runtime_callbacks` to
  `topic_orchestration.is_pending_creation`. The pending set stays with the
  flow that owns it — a core → handlers import would invert the dependency.
  Unwired (`doctor`, `status`, unit tests) means nothing is being created, so
  nothing is protected.
- The sweep skips windows a creation flow owns.
- Belt and braces for the case where the hook-built state already exists when
  reconciliation runs: `_adopt_creation_choices` carries `origin` and
  `approval_mode` from the superseded entry when the live one still holds the
  class default. The "fill only what's empty" rule that governs the other
  migrated fields cannot express this, because these two defaults are
  meaningful values (`manual_discovered`, `normal`) rather than blanks. The
  defaults are read off the state class, never restated.

---

## 6. A settled window falls back into the startup grace

**Observed.** Topic stays 🟢 and the typing indicator runs forever, on a window
whose agent is idle at its prompt. herdr's own status for that pane reads
`idle`.

**Expected.** A finished agent's topic goes idle and the typing indicator
clears.

**Ruling out the obvious suspects.** Replayed against the live capture, both
terminal parsers return nothing for that pane:

```
pyte status:     None
terminal status: None
```

and herdr's native agent status reads `idle`, which `_native_agent_status` maps
to `None` anyway — it only synthesizes a status for `working` and `blocked`. So
`resolved_status_text` is `None` and `is_recently_active` is False (the
transcript had not been written to in hours). **The status did not come from
any status source.** See §8 for the hypothesis this replaced.

**Root cause.** `decide_tick`, given "no status, no activity, not a shell
prompt, has not seen status, no startup timer", returns `starting`. And
`_apply_starting_transition` paints the topic **active** and sends a typing
indicator — reasonable for a window that is genuinely booting.

The trap is how a window *leaves* that state. Both terminal transitions called
`cancel_startup_timer`, which clears `startup_time` but leaves
`has_seen_status` False — precisely the input state above. Replayed against the
real kernel:

```
after idle/done, next tick -> starting
31s into grace              -> idle
```

A 30-second sawtooth: green for 30s, one tick of yellow, green again. The
topic-emoji debounce (5s to active, 30s to idle) swallows most of the yellow,
so the topic reads permanently green and the typing indicator never clears.

`_apply_done_transition` did call `mark_seen_status` — but only
`if not supports_hook`. So hook-backed Claude, the provider whose completion
ccgram observes most reliably, was the *only* one that kept looping. A window
whose transcript ccgram ever saw go active escapes by luck, because
`is_recently_active` marks the flag as a side effect; this window never got
that far.

**Fix.** `_transition_to_idle` and `_apply_done_transition` mark the window
settled instead of merely stopping its clock; the `supports_hook` gate on
`done` is removed (a window that reached done has finished starting, whoever
reported it); and `cancel_startup_timer` is deleted rather than left as a trap
that reproduces the bug at its next call site.

---

## 7. A restart repaints every topic slowly

*(found while verifying §6)*

**Observed.** With §6 fixed the restarted bot settled correctly, but it took
about a minute, during which every topic read green with a typing indicator —
however long its agent had been idle.

**Expected.** A restart repaints topics to their real state promptly.

**Root cause.** Two stacked delays, neither doing its job in this case:

- **30s startup grace.** Poll state is in-memory, so an inherited window has no
  `has_seen_status` and no startup timer: the same shape as one ccgram just
  launched. It enters the grace, which paints the topic active and types. But
  the window is not booting — ccgram simply has no state for it yet.
- **30s rename debounce.** The first observed state then waits out the idle
  debounce. That debounce damps flicker *between* observed states; on the first
  sighting after a restart there is no earlier state to flicker against, and
  the emoji on screen was painted by a process that is gone.

**Fix.** `start_status_polling` settles already-bound windows
(`mark_preexisting`) and seeds their topics for one debounce-free repaint
(`mark_awaiting_first_paint`). Busy windows are unaffected — status or
transcript activity returns them to active on the next tick. The "first call
starts the debounce" contract is deliberately left intact: six tests pin it, so
the allowance is an explicit one-shot seeded only at startup rather than a
rewrite of the debounce rule.

---

## 8. Replayed history counts as activity

*(found while verifying §7)*

**Observed.** With §7 fixed, a topic quiet since earlier repainted immediately
on restart — but the topic of the session that had been working right before
the restart stayed green with a typing indicator. The asymmetry is the whole
clue.

**Expected.** Messages that arrived while ccgram was down are delivered; the
agent is not reported as working *now* on account of them.

**Ruling out the obvious suspects.** Measured against the live panes, not
assumed:

- Both status parsers return `None` for the green pane **even while its agent
  is actively working**, so status text was never the source.
- The pyte buffer does not go stale: feeding a busy frame and then a quiet one
  into the same buffer still yields `None`.
- `events_offset` is persisted in `monitor_state.json`, so hook events are not
  replayed at startup either.

That leaves `is_recently_active`.

**Root cause.** `monitor_state.json` persists `last_byte_offset` per
transcript; the reader's mtime cache is in-memory. So the first poll after a
restart passes the mtime guard and reads from the persisted offset — replaying
whatever the previous run never consumed. `_process_session_file` then did:

```python
if new_entries:
    self._idle_tracker.record_activity(session_id)
```

Those entries are history, but the stamp is *now*. Any session that wrote
anything before the restart therefore looked busy for the full 10s activity
window — active topic, typing indicator — and the 30s idle debounce held that
colour for a further half-minute. A session whose offset was already current
replayed nothing, and repainted correctly; hence the asymmetry.

**Fix.** The catch-up read no longer touches the activity clock. The
discriminator is `session_id not in self._file_mtimes` — true only for a
session that is in persisted state but has not been read in this process.
Entries still flow on, so messages that arrived while ccgram was down are still
delivered; only the "working right now" claim is dropped. The next poll, with
genuinely new bytes, records activity as usual.

---

## Discarded hypothesis — "herdr's agent status is unreliable for Claude"

Between §6's symptom and §6's actual cause, one intermediate fix was written
and is **deliberately not on this branch**. It is recorded here so it is not
re-attempted.

**The hypothesis.** `_resolve_status` falls back to the backend's native agent
status when every scraper comes up empty. The reasoning was: herdr's Claude
integration only calls `pane.report_agent_session` and never reports run state,
so herdr's `agent_status` must be a terminal heuristic that can sit at
`working` indefinitely — which would resurrect a busy status on every tick and
keep the typing indicator firing. The change made `_resolve_status` return
`None` for any provider with `supports_hook`, restricting the gap-fill to
hookless providers (Codex, Gemini).

**Why it is wrong.** Measured against the live socket:

```
w9:p1  agent: claude  agent_status: "working"   (a Claude session actively working)
wB:p2  agent: claude  agent_status: "idle"      (a Claude session idle at its prompt)
```

herdr's `agent_status` is accurate for Claude panes — it reports `idle` for an
idle agent. The premise was false. And the mechanism could not have produced
the symptom in any case: `_native_agent_status` only synthesizes a
`StatusUpdate` for `working` and `blocked`, mapping `idle`/`done`/`unknown` to
`None`, so the gap-fill can never pin a finished window to active unless herdr
genuinely claims it is working.

The symptom persisted after this change; §6 is the real cause.

**Why dropping it is a net gain, not a neutral revert.** Both terminal parsers
return `None` for a herdr Claude pane *even while its agent works* (measured in
§8). Without the gap-fill, ccgram's only "is it working" signal for Claude on
herdr is transcript activity. With it, herdr's accurate `agent_status` fills
exactly the gap the fallback was written for. Excluding hook-backed providers
discards a good signal to fix a bug that was somewhere else.

---

## What this does *not* fix

- **The 5-second registration budget.** It is now spent watching the right key
  (§4), and the waiting-at-a-prompt case is handled (§3), but an agent that
  legitimately takes longer to publish a session still trips it.
- **The quarantine message itself.** When the wait genuinely fails *and* the
  kill genuinely fails, the user still gets a message that reads like a ccgram
  fault rather than an instruction.
- **The three-way "This button has expired".** `resolve_callback_data` returns
  `None` for unknown token, expired token, and failed ownership alike. Fixing
  §2–§4 stops the ownership branch dominating in normal use, but the message is
  still worth disambiguating.
- **The silent inbound drop.** "No active users for session %s" is logged at
  `debug`, so at default level a transcript that advanced with zero bound
  recipients surfaces nothing. That is precisely this bug's signature and
  deserves a throttled `warning` naming both the session id and the bound ids
  that failed to match.
- **`HERDR_SOCKET_PATH` is effectively required.** Unset, CLI calls still work
  and `ccgram doctor` passes, but `open_socket_stream("")` fails in a reconnect
  loop and the push-event stream is silently dead.
