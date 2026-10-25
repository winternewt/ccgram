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
  Findings, each still to be checked against the code by the seat before the verdict:
  - **Red flag: `.github/workflows/archfit.yaml`** (new). Runs on push to `main` and on pull
    requests, `id-token: write`, an upstream maintainer's action pinned by SHA, uploads architecture
    data to a third-party service with the job's OIDC token. In this fork it would run on every push
    to `main`. To be removed (or disabled) in the merge, on the person's word.
  - **New runtime dependency `qrcode` 8.2**, imported only by the new `ccgram pair` command. Dev
    extra adds `jsonschema`, `referencing`, `jsonschema-specifications`, `rpds-py` (the last on a
    calendar version; check its provenance on the index). By the role, a new dependency is a red
    flag for the person.
  - **New local API server** (`src/ccgram/api/`): off unless `CCGRAM_API_PORT` is set (a non-integer
    value aborts startup), binds 127.0.0.1, Host/Origin allowlists, bearer device tokens from a
    120 s single-use pairing code, hashes only at rest. A paired device has full control: it reads
    every session's transcript and panes, pastes text and keys into panes (a shell command in
    effect), creates and kills sessions, uploads files. Note, not a flag while the port is unset.
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
  - Every handler's pane write (keys, text, kill, create, split, rename) now goes through the session
    core's adapter, with ownership checks kept before each call. Open check for the seat: that the
    core validates a `pane_id` taken from callback data as belonging to its window (the old code
    passed the window). `/split <command>` now also works on herdr.
  - The transcript monitor's event hub is the one the API server shares; only the Telegram consumer
    is durable. Several paths now read a whole transcript where they seeked (the slash-command
    failure probe on every forwarded command, the restart rebuild, the session summary).
  - The provisioning claim lost its 180 s bound for a CLI that never starts (a regression, not a
    flag).
  - No new outbound host in the Python, no eval or obfuscation, code matches the commit messages.
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
- Next, in order: check each finding; put the flags to the person
  (default: stay on `v4.16.0`); on their word, the superseded check, the merge with the folds and
  the workflow removed, tests in slices, the verdict rows in `docs/FORK.md` (one per release), push.

## Patch table

- In `docs/FORK.md`: 21 rows, 15 open upstream-bound (all unfiled), 6 open local, 0 retired.

## Seats kit

- The pin in `.claude/settings.local.json` names v0.1.89 (moved 2026-10-25), but on the old host the
  install record stayed at 0.1.87: the machine-wide marketplace record kept the old ref. Filed as the
  kit's #155. On the new host the install record is 0.1.89 and the watchdog runs it (checked
  2026-10-25); that clone has no `.claude/settings.local.json`, so no pin is set there yet.

## Carried from the old host's memory (it does not travel)

- This fork follows the upstream's code style, not the person's own standards: lazy imports
  annotated `# Lazy:` (linted), relative imports, `__all__` re-exports.
- A branch for the upstream carries exactly one fix; anything extra lands on our `main`. The person,
  2026-08-18: "Upstream demands pr branches to be 1-item focussed."
- The narrow test slices in the priming come from the old host's 1.9 GB of memory, where the full
  suite was killed and produced false failures; a larger host may run wider, but check first.
- `CCGRAM_STATUS_MODE=herdr` is the person's setting (row 63bffc8): yellow working, green finished.
