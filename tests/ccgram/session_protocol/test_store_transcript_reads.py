"""The session core observes every transcript each second; most have not changed.

An unchanged transcript must not be read or hashed again, and a transcript that
only grew must be read from where the last observation ended, while a rewrite
still moves the history generation as a full read does. The evidence of an
append is the last 4 KiB before the old end: a rewrite before them that also
grows the file under the same inode goes unseen, which Claude's append-only
JSONL never does. Nothing keeps the transcript itself in memory.
"""

import hashlib
import os

import pytest

from ccgram.session_protocol import store as store_module
from ccgram.session_protocol.store import ProtocolStore


@pytest.fixture
def sha256_calls(monkeypatch):
    calls: list[int] = []
    real = hashlib.sha256

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(store_module.hashlib, "sha256", counting)
    return calls


@pytest.fixture
def bytes_read(monkeypatch):
    """Total bytes the store reads from transcript files."""
    total = {"n": 0}
    real_open = store_module.Path.open

    def counting_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        real_read = handle.read

        def read(*read_args):
            data = real_read(*read_args)
            total["n"] += len(data)
            return data

        handle.read = read
        return handle

    monkeypatch.setattr(store_module.Path, "open", counting_open)
    return total


def _write(path, data: bytes) -> None:
    path.write_bytes(data)


def _append(path, data: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(data)


async def test_unchanged_transcript_is_not_read_again(
    tmp_path, sha256_calls, bytes_read
):
    store = ProtocolStore(tmp_path / "state.sqlite3")
    transcript = tmp_path / "t.jsonl"
    _write(transcript, b'{"a":1}\n' * 1000)
    first = await store.transcript_snapshot("k", transcript)
    sha256_calls.clear()
    bytes_read["n"] = 0

    second = await store.transcript_snapshot("k", transcript)

    assert sha256_calls == []
    assert bytes_read["n"] == 0
    assert (second.start, second.content) == (len(first.content), b"")
    assert second.record == first.record
    store.close()


async def test_grown_transcript_reads_only_its_tail(tmp_path, sha256_calls, bytes_read):
    store = ProtocolStore(tmp_path / "state.sqlite3")
    transcript = tmp_path / "t.jsonl"
    head = b'{"a":1}\n' * 10_000
    _write(transcript, head)
    first = await store.transcript_snapshot("k", transcript)
    tail = b'{"b":2}\n'
    _append(transcript, tail)
    expected = hashlib.sha256(head + tail).hexdigest()
    sha256_calls.clear()
    bytes_read["n"] = 0

    grown = await store.transcript_snapshot("k", transcript)

    assert (grown.start, grown.content) == (len(head), tail)
    assert grown.record[3] == expected
    assert grown.generation == first.generation
    assert sha256_calls == []
    assert bytes_read["n"] < len(head) // 4
    store.close()


async def test_rewrite_that_keeps_the_inode_moves_the_generation(tmp_path):
    store = ProtocolStore(tmp_path / "state.sqlite3")
    transcript = tmp_path / "t.jsonl"
    _write(transcript, b'{"a":1}\n' * 100)
    first = await store.transcript_snapshot("k", transcript)
    inode = transcript.stat().st_ino
    with transcript.open("r+b") as handle:
        handle.seek(len(b'{"a":1}\n') * 99)
        handle.write(b'{"z":9}\n{"c":3}\n')

    rewritten = await store.transcript_snapshot("k", transcript)

    assert transcript.stat().st_ino == inode
    assert rewritten.generation == first.generation + 1
    assert (rewritten.start, rewritten.content) == (0, transcript.read_bytes())
    store.close()


async def test_replaced_transcript_moves_the_generation(tmp_path):
    store = ProtocolStore(tmp_path / "state.sqlite3")
    transcript = tmp_path / "t.jsonl"
    _write(transcript, b'{"a":1}\n' * 100)
    first = await store.transcript_snapshot("k", transcript)
    replacement = tmp_path / "new.jsonl"
    _write(replacement, b'{"a":1}\n' * 100 + b'{"b":2}\n')
    os.replace(replacement, transcript)

    replaced = await store.transcript_snapshot("k", transcript)

    assert replaced.generation == first.generation + 1
    assert (replaced.start, replaced.content) == (0, transcript.read_bytes())
    store.close()


async def test_a_restarted_store_observes_with_a_full_read(tmp_path, sha256_calls):
    path = tmp_path / "state.sqlite3"
    transcript = tmp_path / "t.jsonl"
    _write(transcript, b'{"a":1}\n' * 100)
    store = ProtocolStore(path)
    first = await store.transcript_snapshot("k", transcript)
    store.close()
    restarted = ProtocolStore(path)
    sha256_calls.clear()

    again = await restarted.transcript_snapshot("k", transcript)

    assert sha256_calls
    assert again.record == first.record
    restarted.close()


async def test_unused_observations_are_forgotten(tmp_path, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(store_module.time, "monotonic", lambda: now["t"])
    store = ProtocolStore(tmp_path / "state.sqlite3")
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    _write(old, b"x\n")
    _write(new, b"y\n")
    await store.transcript_snapshot("old", old)
    now["t"] += store_module._TRANSCRIPT_MEMO_IDLE_SECONDS + 1

    await store.transcript_snapshot("new", new)

    assert set(store._transcript_memos) == {"new"}
    store.close()


async def test_the_observation_keeps_only_the_marker_not_the_file(tmp_path):
    store = ProtocolStore(tmp_path / "state.sqlite3")
    transcript = tmp_path / "t.jsonl"
    _write(transcript, b'{"a":1}\n' * 10_000)
    await store.transcript_snapshot("k", transcript)
    _append(transcript, b'{"b":2}\n')
    await store.transcript_snapshot("k", transcript)

    memo = store._transcript_memos["k"]

    assert (
        memo.marker == transcript.read_bytes()[-store_module._TRANSCRIPT_MARKER_BYTES :]
    )
    store.close()


async def test_a_full_observation_returns_the_whole_file(tmp_path):
    store = ProtocolStore(tmp_path / "state.sqlite3")
    transcript = tmp_path / "t.jsonl"
    _write(transcript, b'{"a":1}\n' * 100)
    first = await store.transcript_snapshot("k", transcript)
    _append(transcript, b'{"b":2}\n')

    whole = await store.transcript_snapshot("k", transcript, full=True)
    after = await store.transcript_snapshot("k", transcript)

    assert (whole.start, whole.content) == (0, transcript.read_bytes())
    assert whole.generation == first.generation
    assert (after.start, after.content) == (len(whole.content), b"")
    store.close()
