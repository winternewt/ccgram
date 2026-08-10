## Summary

On the herdr backend, a pane can be observed under two different window ids,
and nothing reconciles them. Four user-visible failures follow from that one
property: agent replies are never delivered, a newly created agent is killed
while it waits at a trust prompt, a healthy session is reported as failed and
quarantined, and a new window loses the settings the creation flow just wrote.

**Environment:** ccgram 4.5.1, herdr 0.8.0 / socket protocol 19, Claude Code
2.1.226, Python 3.14.7, Ubuntu 22.04.5, `CCGRAM_MULTIPLEXER=herdr`, provider
Claude Code.

## The shared root cause

herdr publishes its two facts about a pane at different times: `agent` (the CLI
it classified) as soon as it detects the process, `agent_session` only once the
agent reports its own session id. ccgram derives a window id by hashing what is
published, so one pane yields two ids:

| moment | published | composite | id |
|---|---|---|---|
| pane exists, CLI detected | `agent: "claude"` | `{herdr, claude, terminal, <terminal_id>}` | **A** |
| agent reports its session | `agent_session` | `{herdr:claude, claude, id, <uuid>}` | **B** |

The ccgram `SessionStart` hook runs *inside that gap*. It resolves the pane to
**A** and writes `session_map.json` and `window_states` under **A**, while every
later observation — discovery, `list_windows`, the status poll — yields **B**,
which is what a topic binds.

Nothing converges them. `resolve_stale_ids` returns `False` on its first
statement for any backend with `ids_stable_across_restart=False`, and
`prune_session_map` early-returns for herdr, so `load_session_map` faithfully
recreates `window_states[A]` on every cycle. Because the hook always runs before
the agent has reported a session, this is deterministic on herdr rather than
racy.

**Verified live** by recomputing both digests with ccgram's own function against
a running pane: one pane, one Claude session, two ids, each holding exactly half
of what delivery needs. `herdr agent list` at that moment returned a complete
`agent_session`, i.e. **B** was current and **A** was what the hook had captured
seconds earlier.

---

## Failure 1 — a topic shows live status but never receives a reply

**Observed.** A Telegram topic bound to a herdr pane shows a live status bubble
— spinner, token counts, activity — and never receives a single agent reply.
Inbound messages reach the pane. Nothing errors, in Telegram or in the log at
default level. Restarting does not help; each restart adds another stale entry
to `state.json`.

**Expected.** Agent replies arrive in the bound topic.

**Why only replies.** The asymmetry is the diagnostic. Status resolves the live
pane directly and needs no agreement between records; outbound needs one side
only; inbound is the single path that requires two independently-written records
to agree on one key:

| path | resolves | needs both sides to agree? |
|---|---|---|
| status bubble | live pane, from the backend | no |
| outbound (user → agent) | topic → window id → `send()` | one side only |
| inbound (agent → user) | transcript → session id → **bound** window | **yes** |

Inbound routing asks whether the *bound* window carries the session id
(`session_resolver.py`). `window_states[B]` does not exist, so `get_session_id(B)`
is empty, no user matches, and the message is dropped at `message_routing.py`.
The session id lives under **A**, which nothing is bound to.

**Note on diagnostics.** That drop is logged at `debug` — `"No active users for
session %s"` — so at default level nothing surfaces at all. A transcript that
advanced, a live window and zero bound recipients is precisely this bug's
signature and would be worth a throttled `warning` naming both the session id and
the bound ids that failed to match.

## Failure 2 — creating a topic kills an agent that is waiting at a prompt

**Observed.** Creating a topic launched the agent, then closed the tab and
killed it a few seconds later. The user saw the agent's trust prompt in Telegram,
answered it, and got "This button has expired".

**Expected.** An agent waiting for input is a healthy agent, and its prompt is
answerable from Telegram.

**Mechanism.** Creation required herdr to report a session within 5s
(`_CREATED_SESSION_DISCOVERY_TIMEOUT_SECONDS`). An agent that stops for input
*before* it has a session never satisfies that — Claude's "do you trust the files
in this folder?" prompt is the common case, and it appears for every directory
not yet trusted. At the deadline creation rolled back, closing the tab while the
agent sat at the prompt, so by the time the user answered, the window was gone
and the callback's ownership check could not pass. The expired-button message is
a symptom here, not a separate defect.

## Failure 3 — a healthy session is reported as failed and quarantined

**Observed.** "❌ Session did not register with ccgram in time … the topic
remains quarantined" on a session that was in fact healthy. Nothing was killed,
and the topic was left bound to a window the flow believed it had failed to
create.

**Expected.** Creation completes, or fails cleanly having actually cleaned up.

**Mechanism.** Verbatim from the run (ids abbreviated):

```
17:57:51 Window created: 2 (id=…121eb4…) at /home/newton/bob provider=claude mode=yolo
17:57:52 Accepted bypass permissions prompt for window …121eb4…
17:57:53 Reconciled superseded window id …121eb4… -> …e187e5…
17:57:54 Session map: window_id …e187e5… updated sid=1c02cc23-…, cwd=/home/newton/bob
17:57:57 Timed out waiting for session_map entry: window_id=…121eb4…
```

The hook registered the session **three seconds before the timeout**, under the
new id. `launch_window` holds the id it minted in a local variable across
`wait_for_session_map_entry`; identity is superseded while that wait runs, so the
wait watches a key nothing will ever write again. The failure path then calls
`kill_window` on an id that no longer resolves, the kill returns `False`, and the
flow takes the branch that assumes the target is still alive.

The session was never in trouble — it appeared in Telegram later only because
the monitor's unbound-window discovery adopted it independently.

## Failure 4 — a new window loses its cwd, provider, mode and origin

**Observed.** One second after creation:

```
17:57:51 Window created: 2 (id=…121eb4…) … provider=claude mode=yolo
17:57:52 Removing stale window_state: …121eb4…
17:57:54 Corrected provider for …e187e5…: state= -> claude (session_map claimed claude; …)
```

`state=` is empty in that correction — the provider written one second earlier
was gone. The persisted state confirms the rest: no `origin`, no
`approval_mode`.

**Expected.** Choices the user made during creation survive creation.

**Mechanism.** `_remove_stale_window_states` drops any `window_state` that is
(a) absent from `session_map.json` and (b) not bound to a topic. A window being
created is *both*, by construction: its hook has not fired yet and the flow binds
afterwards. On tmux the window is exposed for well under a second; on herdr the
durable session id arrives later, widening the gap enough to hit reliably.

The losses are not cosmetic: `origin` marks a window as ccgram-created, which
governs whether closing the topic kills it; `approval_mode` drives the 🎲 YOLO
badge; `cwd` feeds recovery.

---

## Proposed fix

Reconcile identity rather than redefine it — a backend may declare that a window
it reports was previously known under a different id, and the core folds the
earlier state forward. tmux leaves the new field empty, so the tmux path is
unchanged throughout.

Making herdr identity always terminal-derived was tried first and rejected: it
works, and was verified live, but it discards the "a session keeps its identity
when it moves panes" guarantee that the existing tests assert, and breaks 20 of
them.

I have a branch with this implemented, four commits (one per failure above),
`6484 passed` with ruff/lazy-import gates clean. Happy to open it as a PR against
this issue.
