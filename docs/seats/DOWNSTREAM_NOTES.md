# Downstream seat — notes

State as of 2026-10-25, written at a host move: the seat stood down on its old host and was taken
on the person's workstation the same day. Nothing in flight lived only on the old host; what was there
is reproducible from the steps below. First start there: fetched, `upstream-main` already at
`upstream/main` (`36a6efa`), no tag past `v4.18.1`.

## Upstream

- **Now (2026-10-31): `main` is on `v4.21.0`** (merge `f745579`: clean by the gate, Claude and Codex
  command menus mirrored like Pi's; a semantic conflict with `13faa61` fixed in the merge). Before
  it, 2026-10-28: `v4.20.0` (merge `b8f2437`, with v4.19.0) plus the fork's own commits. The delta `v4.18.4..v4.20.0` (65 files) was read in full by an Opus reader; the seat
  checked its findings: nothing hidden, no new dependency. v4.19.0 made YOLO the default launch mode
  (mode picker gone) and turned `CCGRAM_AUTOSTART_TRUST` on by default (folder trust written into
  `~/.claude.json` and Codex's `config.toml`, trust screens answered by the bot). The person, asked
  in chat: "Keep old safety"; `232fe26` sets both defaults back. Suite 8915 passed. One tmux
  integration test (`test_missing_target_returns_empty_result[list_panes-...]`) errored once in the
  parallel run and passed alone: it touches the live tmux server. The entries below are the
  history of the v4.18.4 merge.
- **`main` CI red since 2026-10-31, cause open.** `tests/integration/test_session_protocol.py::
  test_real_tmux_list_history_send_respond_resume_and_replay` times out (no Claude session listed in
  8 s) on `ac00aef` (twice) and on `71d0805`, which passed on 2026-10-28; so not v4.21.0. The
  fork's runs now land on the `ubuntu-26.04` image with the system CPython 3.14.4; upstream's latest
  runs are on `ubuntu-24.04` with 3.14.10, so upstream is not known to pass on 26.04 either. Ruled
  out locally: serial run as CI runs it, a clean HOME and environment, CPython 3.14.4 (all pass).
  The fork's lock differs from upstream's only by the `pyromark` cap. Not tried: tmux as shipped on
  26.04 (local is 3.5a). CI has no `workflow_dispatch`; a debug run needs a PR (the person's word).
  Put to the person on desk #169 (pin CI to ubuntu-24.04, debug by PR, or leave it; default: leave).
- Last release taken: `v4.16.0`, by replay. `main` is still on it (plus the fork's own commits, the
  last `5b01e00`).
- **Waiting: v4.17.0, v4.18.0, v4.18.1, v4.18.2, v4.18.3, v4.18.4**, read as one delta from `v4.16.0` (277 files, about 13.6k
  lines outside tests). `upstream-main` on `origin` is at `36a6efa` (= `v4.18.1`).
- **Gate, read-in-full pass: done** by three Opus readers split by area (2026-10-25), every hunk of
  the delta read but about 550 lines of the deleted `transcript_parser.py`'s tail, scanned (deleted
  code cannot run). Unchanged between the tags: `main.py`, `bot.py`, `config.py`, `telegram_*.py`,
  `llm/`, `whisper/`, `tts/`, `miniapp/`, `handlers/send/`, `handlers/upgrade.py`, the Makefile.
  Findings, checked against the code by the seat (2026-10-25) unless marked open:
  - **Red flag, checked: `.github/workflows/archfit.yaml`** (new, with `.archfit-app.yaml` and
    archfit `CODEOWNERS` entries; `.archfit.yaml` itself predates `v4.16.0`). Actions are enabled on
    this fork with all actions allowed, so after the merge it runs on every push to `main` and every
    pull request. The action (`alexei-led/archfit-action@7efc29b`, read whole: `action.yml` and its
    four scripts) checks out the full history without a persisted credential, pulls the engine image
    `ghcr.io/alexei-led/archfit` by digest and runs it over the checkout (`.git` read-only, all
    capabilities dropped, no token in its environment, but **default container networking**). Because
    `.archfit.yaml` exists, report mode measures, then mints a GitHub OIDC token for audience
    `https://app.coupling.dev` and POSTs the engine's architecture report there, with an envelope
    naming this repository, the commit, the run id and the image digest. That token is an identity
    claim for that audience only, not a GitHub credential. **Not read: the engine image itself**
    (code that runs with network access over the checkout). The fork is public, so the source is no
    secret; what leaves is derived data plus the fork's identity, to a service the person has not
    chosen. The App is very likely not installed for this fork, so the upload would also be refused and
    the job would go red on each push. Options for the person: delete the workflow and
    `.archfit-app.yaml` in the merge (a modify/delete conflict whenever the upstream edits them), or
    keep them and guard the job with `if: github.repository == 'alexei-led/ccgram'` (one line, merges
    cleanly). The `CODEOWNERS` lines name a user with no access here: GitHub ignores them, a note.
  - **New runtime dependency `qrcode` 8.2**, imported only by the new `ccgram pair` command (lazy),
    which prints a local JSON payload (the URL the person passes, a single-use code, the store's
    fingerprint) and draws it as a terminal QR; no network path, its only locked dependency is
    `colorama`. Dev extra adds `jsonschema` 4.26.0, `referencing` 0.37.0,
    `jsonschema-specifications` 2025.9.1, `rpds-py` 2026.9.1. Checked: every hash in `uv.lock` for
    the five is on PyPI for that version, from the PyPI registry; `rpds-py` moved to calendar versions
    at 2026.5.1 under the same maintainer and is published by trusted publishing from
    `crate-py/rpds`'s own CI. By the role, a new dependency is still the person's call: clean.
  - **New local API server** (`src/ccgram/api/`): off unless `CCGRAM_API_PORT` is set (a non-integer
    value aborts startup), binds 127.0.0.1, Host/Origin allowlists, bearer device tokens from a
    120 s single-use pairing code, hashes only at rest. A paired device has full control: it reads
    every session's transcript and panes, pastes text and keys into panes (a shell command in
    effect), creates and kills sessions, uploads files. Note, not a flag while the port is unset.
    Checked: the bind is the literal `127.0.0.1` with no override (`CCGRAM_API_HOSTS` and
    `CCGRAM_API_ORIGINS` widen only the allowlists); the machine's hostname, read by the session core,
    goes only into the snapshots this local API serves. The new `scripts/session_protocol_client.py`
    is a manual conformance client: it talks only to the `--url` given (HTTPS, or HTTP on localhost)
    with a token from the environment, and nothing runs it.
  - **Checked by the seat, a blocker for adoption as is:** the session core's `refresh()` runs every
    second (`session_protocol/runtime.py` `poll`) and, for every target, `_source_version` calls
    `store.transcript_snapshot`, whose `_transcript_record` reads the **whole** transcript into memory
    and SHA-256s it, with no stat shortcut; only the re-parse is gated on the stat stamp, so an active
    session (stamp changing every second) is also re-parsed whole every second. The old host had a
    190 MB live transcript: that is a 190 MB read, hash and allocation per second per such session.
    Candidate for an upstream issue (stat first, read only the appended tail), on the person's word.
  - **Always on, even with the API off:** the new session core starts at every launch and polls
    each target every second, as above; it writes
    `protocol.sqlite3` (actions table never pruned) and an uploads folder in ccgram's state
    directory. Note for memory and CPU on a small host.
  - **Two new synchronous Claude hooks, `PermissionRequest` and `PreToolUse`**, installed only by
    `ccgram hook --install` or `doctor --fix` (doctor will report them missing). They log the tool
    name and a hash of its input, give no decision, and cost one Python start before every tool
    call. A Pi `hooks.json` writer and `PI_CODING_AGENT_DIR` are new, used only on a Pi install.
    Checked: both append to the local events log through the existing writer and print no decision.
  - Every handler's pane write (keys, text, kill, create, split, rename) now goes through the session
    core's adapter, with ownership checks kept before each call. Checked: a `pane_id` from callback
    data reaches the multiplexer only through `backend.select_pane`, which looks it up among
    `list_panes` of the bound window and refuses one not there (`actions.py` validation, before any
    write). `/split <command>` now also works on herdr.
  - The transcript monitor's event hub is the one the API server shares; only the Telegram consumer
    is durable. Several paths now read a whole transcript where they seeked (the slash-command
    failure probe on every forwarded command, the restart rebuild, the session summary).
  - The provisioning claim lost its 180 s bound for a CLI that never starts (a regression, not a
    flag).
  - No new outbound host in the Python, no eval or obfuscation, code matches the commit messages.
    Checked: the delta's added lines under `src/` and `scripts/` carry no URL literal and no new HTTP
    client use beyond the loopback API and the conformance client; no new process spawn
    (`worktrees.py` is the old `handlers/topics/worktree.py` moved, one `except` widened).
- **v4.18.2** (tagged 2026-10-25, `2a19f3f`; `upstream-main` on `origin` moved to it): its delta from
  `v4.18.1` (21 files, about 1.4k lines) read in full by an Opus reader, checked by the seat. No new
  host, dependency, lock change, secret read, conftest, Makefile or `.claude/` change. Verdicts:
  - archfit: the action pin moves to `cadf35c`, the engine to v3.2.0 with a new image digest, and
    `.archfit.yaml`'s module map (the data uploaded) grows; endpoint, audience and triggers unchanged.
    The new action commit and image were not read: either option on desk #128 keeps the job from
    running on this fork, so neither needs reading. No new flag.
  - `scripts/smoke_agent_relay.py` (new): a manual harness that starts a real agent CLI through
    agterm; it refuses unless `AGTERM_ENABLED=1`, and nothing in the Makefile or CI runs it.
  - `handlers/upgrade.py`: still `uv tool upgrade ccgram`, the restart still `os.execv`, now through
    a callback in `bot_data`. Conflicts with the fork's 7c3bf9a (the dependency-only summary): keep
    both.
  - `session_protocol/messages.py` filters harder: Claude's `_claude()` now drops `isMeta`,
    `isSidechain` and **`isCompactSummary`** records before parsing, so the fold of 91adafb must turn a
    compaction summary into its notice **before** that drop; check whether a `<cross-session-message>`
    entry is `isMeta` for the fold of 06908db. Codex drops `system` role messages and user text
    opening with its injected context tags. The summarizer sends less transcript text, never more.
  - The whole-transcript read each second (#131) is untouched: the store is not in the delta.
- **v4.18.3** (tagged 2026-10-25, `bfe7bb7`; `upstream-main` moved to it): its delta from `v4.18.2`
  (8 files, 177 lines) read in full by the seat: logging only. A multiplexer outage is logged at its
  start and end (session core's `poll`) or throttled (status poll, monitor reconciliation, agterm
  listing) instead of once per second. No process, socket, file write or dependency. Clean.
- **v4.18.4** (tagged 2026-10-25, `0d92483`; `upstream-main` moved to it): its delta from `v4.18.3`
  (19 files, about 700 lines) read in full by the seat, tests included. A refactor: the event-stream
  monitor no longer imports Telegram or handlers (bootstrap hands it a death callback, the new
  `handlers/polling/window_death.py`), and the shutdown banner's reason comes from a getter `main`
  puts in `bot_data`. `.claude/rules/architecture.md` (loads into the seat's sessions) is prose
  matching the code; the Makefile adds one test to `arch-guard`; `.archfit.yaml` gains module rules
  only. The one subprocess (`test_import_no_cycles`, a clean-interpreter import probe) was there
  before. No new host, process, file write or dependency. Clean.
- When the desk answers, the merge takes the newest release then (now `v4.18.4`).
- **Trial merge** (a detached worktree at `v4.18.1`, `git merge --no-commit main`): no fork commit
  taken verbatim (`git cherry` shows none). 7 conflicts:
  - `transcript_parser.py` and its test deleted upstream (the parsing moved, rewritten, to
    `session_protocol/messages.py`). Fold 91adafb (compaction notice) and 06908db (peer messages)
    into `_claude()` there: an `isCompactSummary` entry becomes a `notice` part; render
    `<cross-session-message>` before the system-tag filter drops it.
  - `providers/claude.py`: drop the fork's `parse_history_entry` (upstream removed history parsing
    from providers); the compaction moves as above.
  - `hooks/adapters.py`: keep upstream's `PermissionRequest`/`PreToolUse` branch, fold f57fc5f's
    field table, making sure it carries upstream's events.
  - `session_monitor.py`, `transcript_reader.py`: upstream rewrote the reader around batches,
    watermarks and tool-delivery fences. Port f57fc5f (a fresh session read from offset 0),
    99ae535 (fence diagnostic) and 7c142a0 (reload auto-send only for fresh answers, which reads a
    message's timestamp) onto the new shape; check whether upstream's own backlog skip supersedes
    d3b6c1e.
  - `handlers/sessions_dashboard.py`: one hunk, take upstream's signature (it adds an adapter).
  - `send_telegram_to_window` and its follow-up variant now require an `adapter` keyword and lost
    `raw=`: the fork's `messaging_pipeline/plugin_reload.py` calls it without one and would raise
    `TypeError` on the next auto-reload. `set_message_callback` is now legacy (delivery goes through
    `register_delivery_consumer`); check the fields 7c142a0's freshness test reads still reach it.
- **Decided by the person in chat, 2026-10-25:** "guard it and merge to follow the upstream tip"
  (archfit, #128: keep the workflow, guard the job with
  `if: github.repository == 'alexei-led/ccgram'`); the 1 Hz whole-transcript read (#131): patch it
  in the merge (an unchanged file is not read, a grown one reads only its tail and extends the hash),
  and draft an upstream issue for their word; the new dependencies (#133): take them. "The upstream
  tip" is taken as the newest release, `v4.18.4`, by the priming's rule (releases only).
- **Merged, branch `merge-v4.18.4`** in the worktree `../ccgram-merge` (a sibling of this clone):
  the merge `0717583` (folds in its message; superseded check: none of the 21 rows taken verbatim or
  superseded, no reverts), `c57605d` (the observation cache) and `3154319` (only appended lines are
  parsed; the store keeps the last 4 KiB, not the file). On the 343 MB transcript a grown tick takes
  about 2 ms instead of about 7 s; `92675e6` keeps an idle session's parsed messages. Unit suite
  8298 passed, integration 375 passed. One upstream test is order-dependent: it failed in 3 of 14
  randomly ordered runs at the head and in none of 6 before these commits, though neither it nor the
  state it depends on is touched by them, and no changed test module leaves that state when run
  before it:
  `test_session_monitor.py::TestNewWindowDetection::test_callback_error_does_not_crash` resets
  `monitor._last_session_map`, which nothing reads now; reconcile reads the global
  `session_lifecycle` map, which an earlier test can leave holding `@1`. Unchanged from `v4.18.4`.
- The incremental build was put to the person on the desk (#141), default: build it, then push
  `main`. Built under that default while they were away (2026-10-25).
- Upstream issue for the 1 Hz read: drafted outside the repository, to put to the person with its
  exact text; posted only on their word.
- Then: push `main` by fast-forward to the branch head; remove the worktrees and the merge branch.

## Patch table

- In `docs/FORK.md`: 27 rows, 20 open upstream-bound (all unfiled), 7 open local, 0 retired;
  checked against git 2026-10-31 (`git log --no-merges v4.21.0..main -- src tests`: 27).

## The bot's logs

- The bot runs on another host; its service logs come from that host's sysadmin seat, by direct
  message with a window and what is sought (it replies with an excerpt, newest 200 lines at most).
  `wake_from` carries `*/sysadmin` so its replies wake this seat.
- Seen there 2026-10-25: "Complete assistant message has no routed topic; dropped", 146 times
  from 02:39Z to 13:40Z: no topic was opened for any session after the reboot. Root cause (traced by
  the sysadmin seat, confirmed by this seat in the fork and unchanged in `v4.18.4`):
  `topic_orchestration.collect_target_chats` finds chats only through bindings and
  `group_chat_ids`, which are keyed per topic and popped on unbind; the last two topics were deleted
  by the dead-session cleanup before the reboot, so the set was empty, `CCGRAM_GROUP_ID` was unset
  there, and the empty case logged at debug only. `/sync` never records its chat, so its Fix does
  nothing; startup's "adopted N" counts attempts. No upstream issue. Workaround, the person's:
  `CCGRAM_GROUP_ID` in the bot's env file and a restart; in place by 2026-10-27 (the person, on desk
  #138: "groupid is in"). Code fix: the person chose it on desk #138 (2026-10-27, "A: 1"); landed as
  `c7341a4` (the router keeps the last forum group, `/sync` records its group, the empty case warns
  at most every 10 minutes). It reaches the bot at the person's `/upgrade`. Upstream issue drafted;
  it and the transcript-read draft wait on the person's word on desk #162 (both texts inline there;
  default: nothing posted). The desk question still told the person
  to set the value after they had: they asked for a kit report, filed as kit #198 (a seat keeping an
  open desk question current).

## Seats kit

- The pin in `.claude/settings.local.json` names v0.1.89 (moved 2026-10-25), but on the old host the
  install record stayed at 0.1.87: the machine-wide marketplace record kept the old ref. Filed as the
  kit's #155. On the new host the pin and the install record both moved to v0.1.91 by
  `bin/move-to-tag.py` (2026-10-25), then to v0.1.92 and v0.1.93 the same day, to v0.1.95 on
  2026-10-26, to v0.1.96, v0.1.97 and v0.1.98 on 2026-10-27 and to v0.1.99 and v0.1.100 on 2026-10-31; the watchdog runs it. With v0.1.93, `[repo] forge_gate_unseated = true` (bc029d6): the fork is public, so every
  session's forge writes stay checked.

## Carried from the old host's memory (it does not travel)

- The bot-driven plugin reload (ending an answer with a line the live bot typed into the pane)
  belonged to the old host, where the bot drove the seat's session. Here the person types the
  reload; the priming's "Reloading plugins" says so (2026-10-25).

- This fork follows the upstream's code style, not the person's own standards: lazy imports
  annotated `# Lazy:` (linted), relative imports, `__all__` re-exports.
- A branch for the upstream carries exactly one fix; anything extra lands on our `main`. The person,
  2026-08-18: "Upstream demands pr branches to be 1-item focussed."
- The narrow test slices in the priming come from the old host's 1.9 GB of memory, where the full
  suite was killed and produced false failures; a larger host may run wider, but check first.
- `CCGRAM_STATUS_MODE=herdr` is the person's setting (row 63bffc8): yellow working, green finished.
