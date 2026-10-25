# Fork record

The downstream seat's record for this fork (`docs/seats/DOWNSTREAM.md`): a verdict per upstream
release taken, and the patch table, which counts the fork's own commits on top of the upstream.

## Verdicts

| Release | How taken | Read in full | Not read | Verdict |
|---|---|---|---|---|
| v4.16.0 | replay (the last rebuild, 2026-10-12/13), before the seat existed | not recorded by the seat | not recorded by the seat | none: taken by hand before the gate was set. The next release's delta is read from v4.16.0 |
| v4.17.0, v4.18.0, v4.18.1 | merge `0717583` (with v4.18.2 to v4.18.4), 2026-10-25 | the delta `v4.16.0..v4.18.1` as one (277 files), by three Opus readers split by area, each finding checked by the seat: every hunk, tests, `.github/`, `scripts/`, the Makefile, `pyproject.toml` and the five new locked packages (hashes held against the package index) | about 550 lines of the deleted `transcript_parser.py`, scanned (deleted code cannot run); the archfit engine image (`ghcr.io/alexei-led/archfit`), which the fork's guard keeps from running here | red flag on `.github/workflows/archfit.yaml` (uploads a report and the fork's identity to the upstream's service on every push): the person chose to keep it with the job guarded to the upstream's repository (desk #128). New dependency `qrcode` and four dev packages: taken on the person's word (#133). The session core read and hashed every transcript whole each second (#131): the person chose to patch it in the merge, rows c57605d and 3154319. Otherwise clean: no new outbound host, the new local API binds 127.0.0.1 and is off unless `CCGRAM_API_PORT` is set, the two new Claude hooks only log |
| v4.18.2 | merge `0717583` | its delta from v4.18.1 (21 files), by an Opus reader, checked by the seat | the new archfit action commit and engine image (the guard keeps them from running here) | clean, no new flag: archfit pins and module map moved; a manual agterm smoke script nothing runs; `upgrade.py` restart through a callback (folded with 7c3bf9a); stricter Claude record filters (folded with 91adafb and 06908db) |
| v4.18.3 | merge `0717583` | its delta from v4.18.2 (8 files), by the seat | nothing | clean: logging of multiplexer outages throttled |
| v4.18.4 | merge `0717583` | its delta from v4.18.3 (19 files), tests included, by the seat | nothing | clean: the event-stream monitor's Telegram imports moved behind a callback; no new host, process, file write or dependency |

## Patch table

**Count: 18 open upstream-bound (all unfiled), 6 open local, 0 retired.** Rows: the fork's commits
past v4.16.0 touching `src/` or `tests/`, merges excluded, oldest first. Records and plans get no
row. A fate of "upstream-bound" means the change is not specific to this fork and could be filed;
none is filed until the person asks (the priming, "Fixes for the upstream"). Fates were first set by
the seat on 2026-10-17 from the commit messages; the person may move any.

Check against git: `git log --no-merges --oneline <last release merged>..main -- src tests` (now
`v4.18.4`) lists exactly the open rows, less each reverted pair and each closed row.

| Commit | Why it exists | Upstream issue / PR | Fate | Retired by |
|---|---|---|---|---|
| 3fd0d28 | `/clear` hook order let a session's end write the old session back into the session map, so content went undelivered | none | upstream-bound | |
| f57fc5f | a session that began while ccgram ran was seeded at EOF, losing its first messages | none | upstream-bound | |
| 91adafb | a compaction summary was relayed as a 20k-character user message in six parts | none | upstream-bound | |
| 13faa61 | the interactive handler re-captured the pane and re-detected a prompt the poll had already resolved | none | upstream-bound | |
| 80aa0bf | every exhausted rate-limit retry printed a 30-line traceback for a handled exception | none | upstream-bound | |
| d6e79c1 | Claude's "Worked for 5s" line was read as live status, so topics stayed active | none | upstream-bound | |
| 5fe9a34 | `/start` and the dashboard's New Session could not start a session | none | upstream-bound | |
| f7dc491 | status-bubble re-renders minted callback tokens until live keyboards were evicted | none | upstream-bound | |
| 63bffc8 | `CCGRAM_STATUS_MODE=herdr`: topic colours as herdr's sidebar draws them | none | local | |
| a5031da | `/reload_plugins` forwarded as Claude's `/reload-plugins` | none | upstream-bound | |
| 7ca263c | types `/reload-plugins` into the pane when Claude's answer asks for it | none | local | |
| 06908db | peer-session messages shown in topics without the trust notice | none | local | |
| 27a22d5 | a command result's `⎿` line was read as working status | none | upstream-bound | |
| 7c3bf9a | `/upgrade` reported a release when only dependencies moved | none | upstream-bound | |
| 99ae535 | journals a delivery watermark fence held over two minutes | none | local | |
| 1121274 | logs the Telegram error on silent send/edit failures | none | upstream-bound | |
| 7c142a0 | the reload auto-send fired again for old answers after a restart (fixes 7ca263c) | none | local | |
| 900057a | logs the branch behind every silent send failure | none | local | |
| fd1fb6e | telegramify-markdown >= 1.3 with pyromark 0.10 emptied any text with a `$` | none | upstream-bound | |
| d3b6c1e | a Live (N) button in `/sessions` to skip a topic's backlog | none | upstream-bound | |
| 078146e | `ALLOWED_USERS` and `CCGRAM_GROUP_ID` had no way to be found from ccgram itself: `scripts/discover_telegram_ids.py` | none | upstream-bound | |
| c57605d | the session core read and hashed every transcript whole each second: an unchanged one is no longer read, a grown one reads only its tail | none (issue drafted for the person) | upstream-bound | |
| 3154319 | a busy session's transcript was decoded and rebuilt whole on every change (about 7 s per change at 343 MB): only appended lines are parsed, and the store keeps no copy of the file (fixes c57605d's memo, which did) | none (issue drafted for the person) | upstream-bound | |
| 92675e6 | the parsed messages of a session left unchanged for five minutes were dropped, so its next line cost a whole re-parse (fixes 3154319) | none (with the issue for 3154319) | upstream-bound | |
