# Downstream seat — notes

State as of 2026-10-25, written at a host move: the seat stood down on its old host and was taken
on the person's workstation the same day. Nothing in flight lived only on the old host; what was there
is reproducible from the steps below. First start there: fetched, `upstream-main` already at
`upstream/main` (`36a6efa`), no tag past `v4.18.1`.

## Upstream

- Last release taken: `v4.16.0`, by replay. `main` is still on it (plus the fork's own commits, the
  last `5b01e00`).
- **Waiting: v4.17.0, v4.18.0, v4.18.1**, read as one delta from `v4.16.0` (277 files, about 13.6k
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
- Next, in order: findings checked (2026-10-25); the flags are with the person (archfit: delete or
  guard; the new dependencies; the whole-transcript read each second), default: stay on `v4.16.0`; on their word, the superseded check, the merge with the folds and
  the workflow removed, tests in slices, the verdict rows in `docs/FORK.md` (one per release), push.

## Patch table

- In `docs/FORK.md`: 21 rows, 15 open upstream-bound (all unfiled), 6 open local, 0 retired.

## The bot's logs

- The bot runs on another host; its service logs come from that host's sysadmin seat, by direct
  message with a window and what is sought (it replies with an excerpt, newest 200 lines at most).
  `wake_from` carries `*/sysadmin` so its replies wake this seat.
- Seen there 2026-10-25: "Complete assistant message has no routed topic; dropped" for two sessions.
  That warning is upstream's (#288, `message_routing._handle_unroutable_message`), made for a
  tracked session whose topic binding lags (after a restart or an adoption) or is absent. Not
  looked into yet; first ask whether those sessions had a topic at that time.

## Seats kit

- The pin in `.claude/settings.local.json` names v0.1.89 (moved 2026-10-25), but on the old host the
  install record stayed at 0.1.87: the machine-wide marketplace record kept the old ref. Filed as the
  kit's #155. On the new host the pin and the install record both moved to v0.1.90 by
  `bin/move-to-tag.py` (2026-10-25), and the watchdog runs it.

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
