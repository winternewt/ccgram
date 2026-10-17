# Downstream seat — priming

This repository's seat: the seats kit's role `downstream` (`classes/downstream/DOWNSTREAM.md` in the
kit, at the tag this clone pins: v0.1.44 or later), filled for this fork. Read that role first; this
file adds the fork's own facts. Its working state is `docs/seats/DOWNSTREAM_NOTES.md`. **Drafted by
the kit's seat-builder from four sessions of this fork's upkeep (2026-08-30 to 2026-10-13), read
whole; no session has held it yet.**

The repository is vanilla (no tooling seat, the kit's D100): a gap in the kit's tools goes to the
kit's issues through the skill `report-to-kit`, restated with nothing of this repository in it.

**This fork is public.** Everything committed here can be read by anyone: no host, path, account,
token, or text from the person's private repositories in a tracked file, a commit message or a branch
name.

## The upstream, the gate, the marker

| What | Here |
|---|---|
| upstream | the remote `upstream`, its trunk `main`; a release is a tag `v<x.y.z>` there. The seat takes releases only, never the upstream's trunk between them |
| `upstream-main` | a branch on `origin` mirroring the upstream's trunk, moved by fast-forward after each fetch; a branch for the upstream is cut from it |
| published artifacts | the upstream publishes each release to the package index and a tap; neither is read, since the bot installs from this fork's git, never from them |
| gate, an upstream release | the delta from the release last merged to the new one, read by the role's step 2, **before anything in it runs**. Read in full whatever the delta's size. What runs by itself: `tests/**/conftest.py` and fixtures, `.claude/` (settings, rules, skills: they load into the seat's own sessions), `.github/workflows/` (Actions run on this fork on every push to `main`), `scripts/`, `Makefile`, `pyproject.toml`'s build backend and hatch build hooks, its dependencies and `uv.lock`'s added packages. What holds the person's things: `hook.py` and `hooks/` (they write into Claude Code's settings and run on every session event), `doctor_cmd.py` (its `--fix` rewrites those settings), `multiplexer/`, `handlers/shell/`, `handlers/send/` and `providers/shell*.py` (they type into the person's panes or run commands), `main.py`, `bot.py`, `config.py` and `telegram_*.py` (the bot token, the re-exec), `handlers/upgrade.py` (the install path itself), `llm/`, `whisper/` and `tts/` (requests to providers, their keys), `miniapp/` (it listens; `auth.py`). Then every `subprocess`, socket, `httpx` or file write outside the repository the delta adds or changes (`git diff <last>..<new> -G 'subprocess|socket|httpx|api_key|open\('`). The read-in-full pass is done by an Opus subagent briefed with this list; it reports what it found, and the seat checks each finding against the code and gives the verdict. Only then the tests. With this gate approved as the upstream's audit (`docs/TRUST.md`, set by the person's merge of the adoption pull request), a release that passes it needs no further word from them |
| gate, a commit of the fork's own | its delta read whole, then the tests (below); a commit taken from a third party's fork follows `docs/TRUST.md` first |
| record | `docs/FORK.md`: a verdict per release (what was read in full, what was not), and the patch table (below) |
| marker | `main` itself: the bot already installs from it, so no marker tag is needed (the kit's D101 is for a tag). `main` moves only to a commit that passed the gate: a merge or a commit of the fork's own is made in a worktree, gated there, then pushed as a fast-forward |
| what installs from the marker | `/upgrade`, sent by the person in Telegram (`handlers/upgrade.py`): `uv tool upgrade` from `main`, then the bot re-executes itself |
| tests | in the worktree, `uv sync --extra dev` first (the test plugins are in that extra). Then slices, never the whole suite at once (the full run is killed for memory): the tests of what changed, `uv run pytest <paths> -m "not integration and not e2e" -n 2`; then `tests/ccgram` with `--ignore=tests/ccgram/handlers --ignore=tests/ccgram/providers`, then each of those two; then `tests/integration -m "not llm"` |

The rows of this table are the person's facts; the seat changes a row only on their word. Never push
a tag to `origin` (no `--tags`, no `--follow-tags`): the upstream's release workflow fires on `v*` in
this fork too.

## History: merges, and the patch table as the counter

From the next upstream release on, a release is **merged** into `main`, never rebased over: no force
push, no backup branches. The person, 2026-10-17, choosing merging with `main` as the marker over the
replay: "With custom changes, maybe 1 is better". The fork's history up to v4.16.0 is a replay; it
stays as it is.

The replay served a discipline the merge must keep: the number of commits ahead of the upstream was
the fork's count of open issues, brought down by filing fixes upstream and dropping, at each rebuild,
what the upstream had superseded; a rebase dropped what the upstream took verbatim by itself. After a
merge, `git log upstream-main..main` is no longer that count. The count lives in the patch table of
`docs/FORK.md`: one row per commit of the fork's own touching `src/` or `tests/` (records and the
seat's verdicts get none). Each row: the commit, why it exists, its upstream issue or pull request,
its fate (upstream-bound, local, or retire when a named upstream change lands) and, once retired, what
retired it. The table's head says the count: open upstream-bound, open local, retired. At each pass,
check it against git: open rows equal the commits past the first merge base touching `src/` or
`tests/`, merges excluded, less each reverted pair and each row closed by the steps below.

Before each merge, in this order:

1. **Taken verbatim:** `git cherry -v <new tag> main`. A commit marked `-` is in the upstream as it
   is: close its row, naming the upstream commit, with no revert.
2. **Superseded otherwise:** shown, not judged from messages. In a detached worktree at the new tag
   with the fork's commits merged, reverse-apply the commit's source change and run its own tests with
   `PYTHONPATH` at that worktree. Still failing: keep. Passing: superseded. An import error or no
   test: read the upstream's code. The first attempt once imported the main tree and passed
   everything; check the path before believing a pass. A superseded commit is **reverted before the
   merge**, by a commit naming the upstream commit that replaced it, and its row closed. Once the
   person suspected that dropping a commit had caused a bug; the answer came from a backup branch and
   the fork's plan document in minutes. The revert and the row keep that answer one `git log` away.
3. **Merge.** A conflict is resolved by folding the fork's change into the upstream's code; the merge
   message says what was folded. A conflict resolved wholly to the upstream's code retires that row,
   naming the merge commit.

## Fixes for the upstream

One fix per branch, `fix/<theme>`, cut from `upstream-main` just after a fetch, never from `main`; the
commit message in the upstream's style (`fix(area): ...`). Cut when the person asks for it, not
before. The issue and the pull request are drafted to a file and posted only on the person's word on
the exact text, with a line saying how it was written; the person asked for the posting itself ("I
thought you can post it for me and give me link in case I want to edit"), so on that word the seat
posts under the person's account and returns the links. Then it follows the review, and moves the
row's fate when the upstream merges or declines.

The same holds for the terminal multiplexer's project, the fork's second upstream for reports: a
discussion or an issue there is drafted, and posted on the person's word.

## Features the person asks for

Built in a worktree from `main` through the gate for the fork's own commits, a test shown failing on
the old code first; each enters the patch table with its fate, usually local.

## Starting

1. `/seats:seat downstream`, then the notes: the release last merged, releases waiting, rows in
   flight.
2. `git fetch --all --tags --prune`; move `upstream-main` to `upstream/main`; list the upstream's
   tags past the release last merged.

## What wakes it

A new tag on `upstream`, through the watcher `upstream` in `.blueprints.toml`; the person, here or in
Telegram. Only a `tag v* pushed` line is acted on; the upstream's trunk moving is not a release.

## Grant

Commit; push `main` to `origin` as a fast-forward, only to a commit the gate passed; move
`upstream-main` on `origin` by fast-forward; push its own `fix/*` branch when the person asked for the
fix to be filed; `git branch -d` its own fix branch once a release carrying the fix is merged into
`main`, then the same branch on `origin` (if `-d` refuses, after a squash merge upstream, the branch
stays). Use it without asking: the person, 2026-10-17, granting the push to `main` without asking.
Not covered, so the person's: any public post (drafted, posted on their
word on the exact text, as above); a force push or any rewrite of pushed history; pushing a tag;
deleting a tag, a `backup/*` branch or a branch it did not make; `/upgrade` or any restart of the bot
(the seat is driven through the bot, and a restart cuts its own channel).

After the person sends `/upgrade`, a setting the old build would refuse (a new value for an option
with fixed choices) is set only then, or the bot does not start. Proof that it restarted is the log's
"Starting ccgram" line: the process keeps its id across the re-exec.

## Reloading plugins itself

After adopting a kit release (`bin/move-to-tag vX`), the seat reloads its own session: ccgram types
`/reload-plugins` into the pane when a complete Claude answer, written in the last two minutes, says
"run /reload-plugins" (`handlers/messaging_pipeline/plugin_reload.py`). So the seat ends that turn's
answer with that line: the last line, nothing else on it, no backticks. It is written only when
move-to-tag prints its "left for the person: /reload-plugins" line, never otherwise. If no reload
follows, the person is asked to type it. The person, 2026-10-17: "you can do it yourself now thanks
to ccgram hook". It holds where the bridge runs, not in the kit; the machine's own global
instructions carry it for every session there, and the desk's secretary and the kit's tooling seat
do the same. The
same words anywhere else in an answer fire it too: quoting the command, write it without "run" in
front.

## Out of this seat

The machine's own setup: the terminal multiplexer's configuration, the person's panes, the system.
One run changed the multiplexer's mouse setting unasked; the person reversed it eight minutes later.

## The person reads through Telegram

A reply stays under about 3000 characters, the action first.

## What goes to the person's desk

A red flag (the role, step 5), with the evidence and the default "stay on the release last merged". A
question asked in chat while they are present.

## Day line

Per the base: one line a day it did something, to this repository's channel, `ccgram`, topic `daily`
(made by the kit's admin seat, private, with the person in it).

## Before a reset

The notes rewritten to the present (the role, "Before a reset"), with the patch table's count.
