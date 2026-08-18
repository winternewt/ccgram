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
