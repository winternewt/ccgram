# RCA — A topic receives the whole conversation again, at random

**Date:** 2026-08-18
**Branch:** `fix/transcript-replay-on-append` (base: upstream `main` at v4.6.0)
**Environment:** ccgram 0.1.dev653 (upstream v4.6.0 + our fixes), herdr backend,
Claude Code 2.1.226, Python 3.14.7, Ubuntu 22.04.5, ext4.

**Observed.** Long-running topics periodically fill with their own history:
instead of the latest turns, the entire conversation is re-sent, in order, as
hundreds of messages. It takes minutes to drain, arrives with no log line
explaining it, and nothing the user does triggers it.

**Expected.** A transcript is read from the offset already consumed. History is
delivered once.

**Root cause.** `TranscriptReader._read_session_entries` stats the transcript
before and after each read and treats *any* ctime difference between the two as
the file having been rewritten under the read:

```python
rewritten_in_place = before.st_ctime_ns != after.st_ctime_ns
```

ctime moves for an append as well as for a rewrite. The poll reads *because*
the agent is writing, so the agent writing its next line during the read is the
ordinary case, not the exception. When it lands inside that window the read is
declared unstable: `last_byte_offset` is reset to 0 and the loop retries, and
the retry reads from the top of the file. Every entry above the offset is
parsed and delivered again. On the 2.3 MB transcript measured here that is the
whole session.

Failing the retry as well is no better: `_read_session_entries` returns `None`
with the offset already at 0, so the next poll replays the file anyway.

The sibling check in `_prepare_observed_generation` compares ctime the same way
but guards it with `st.st_size <= previous_size` — an append cannot satisfy it.
Only the in-read check is unguarded, which is why the replay is intermittent:
it needs a write to land inside the read, not merely between two polls.

Introduced upstream in `c50cb29` (*fix: detect transcript rewrites during
reads*, 2026-08-16, in v4.6.0), carried on our side by `b50d399`. Backend-
independent — nothing here is herdr- or tmux-specific.

**Fix.** Guard the comparison with the size, as the sibling check does:

```python
rewritten_in_place = (
    before.st_ctime_ns != after.st_ctime_ns
    and after.st_size <= before.st_size
)
```

A file that grew was appended to. A rewrite that also grew changes the bytes
before the offset the read resumed from, which the marker check beside this one
already catches, so the pair still covers the case the original commit was
written for.

**Reproduction.** `test_append_during_read_is_not_a_rewrite` wraps
`_read_new_lines` and appends one line between the read and the after-stat —
the race, made deterministic. Against the unfixed reader the topic receives the
line above the offset as well; against the fixed one it receives only what was
unread when the read started, and the line written during it stays for the next
poll.

**Also observed, not fixed here.**
`test_replacement_between_stat_and_open_retries_from_zero` (upstream, v4.6.0)
passes alone and fails roughly one run in three when any of several earlier
tests in the file runs first — it delivers `[]`. It fails the same way on
unmodified v4.6.0, so it predates this change. All the tests in that file drive
`window_id="@1"`, and provider resolution caches per window id, so the likely
culprit is cached provider state crossing test boundaries. Worth a separate
look before it masks a real regression.

---

## Second cause, same symptom — a stamped transcript read as a rewritten one

**Branch:** `fix/transcript-replay-on-touch` (base: upstream `main` at v4.6.0)

The append race above was not the whole of it: the topic kept filling after the
first fix shipped, and after a `/clear`.

`_prepare_observed_generation` runs before each read and resets the offset to 0
when the transcript's ctime moved and its size did not grow. ctime moves for a
metadata-only touch as much as for a rewrite, and Claude Code stamps transcript
times: `b2ae3694…jsonl` in `/home/newton/bob` carries mtime 03:32:45.135 MSK
against a last entry written at 02:38:14 MSK — 54 minutes earlier — and the
fractional part is whole milliseconds, which is a `utimes` call, not a write.
The bytes were untouched: the entry beginning at byte 2338921, the offset
ccgram had consumed at the time, is still an entry timestamped seconds after
that offset was recorded. The file is append-only, and all 4.5 MB of it went to
the topic again.

**Fix.** The prefix digest computed two lines above answers the same question
from the bytes — it hashes the file up to the size last committed and compares
it with the digest of what was read. Where it has a baseline it has already
decided, and ctime can only overrule it wrongly, so ctime now applies only when
there is no baseline (a session tracked but not yet committed).

**Reproduction.** `test_metadata_touch_is_not_a_rewrite` stamps a fully consumed
transcript with later times and asserts nothing is delivered. It sleeps 50 ms
before the touch: the kernel takes file times from a coarse clock, so a touch
in the same tick as the read does not move ctime at all and the defect does not
reproduce. That is also the reason the neighbouring upstream test
(`test_replacement_between_stat_and_open_retries_from_zero`, noted above) is
intermittent — it depends on two timestamps taken microseconds apart differing.

**Still open.** Both fixes make ccgram stop *mistaking* a stable file for a
rewritten one. Neither changes what happens on a real rewrite: the offset goes
to 0 and everything above it is delivered a second time. Claude entries carry a
`uuid`, so a rewrite could instead be resynchronised by finding the last
delivered entry in the new file and resuming after it, falling back to 0 only
when it is genuinely a different file. Worth doing before the next transcript
format change makes rewrites routine.
