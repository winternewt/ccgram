# Fork record

The downstream seat's record for this fork (`docs/seats/DOWNSTREAM.md`): a verdict per upstream
release taken, and the patch table, which counts the fork's own commits on top of the upstream.

## Verdicts

| Release | How taken | Read in full | Not read | Verdict |
|---|---|---|---|---|
| v4.16.0 | replay (the last rebuild, 2026-10-12/13), before the seat existed | not recorded by the seat | not recorded by the seat | none: taken by hand before the gate was set. The next release's delta is read from v4.16.0 |

## Patch table

**Count: 14 open upstream-bound (all unfiled), 6 open local, 0 retired.** Rows: the fork's commits
past v4.16.0 touching `src/` or `tests/`, merges excluded, oldest first. Records and plans get no
row. A fate of "upstream-bound" means the change is not specific to this fork and could be filed;
none is filed until the person asks (the priming, "Fixes for the upstream"). Fates were first set by
the seat on 2026-10-17 from the commit messages; the person may move any.

Check against git: `git log --no-merges --oneline v4.16.0..main -- src tests` (until the first merge
of an upstream release; then from that merge's base).

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
