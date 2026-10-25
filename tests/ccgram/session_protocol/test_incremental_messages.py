"""A transcript's messages built as it grows equal the messages built at once.

The session core keeps one builder per transcript and feeds it only the bytes
appended since the last observation, cut anywhere, even inside a line. Every
cut must give exactly what one pass over the transcript so far gives: the same
messages in the same order, revisions and tool results included.
"""

import json
import random

import pytest

from ccgram.session_protocol import messages as messages_module
from ccgram.session_protocol.messages import (
    MessageBuilder,
    TranscriptFeed,
    build_messages,
    read_entries,
)


def _line(data: dict) -> bytes:
    return (json.dumps(data) + "\n").encode()


def _assistant(uuid: str, *content: dict) -> bytes:
    return _line(
        {
            "type": "assistant",
            "uuid": uuid,
            "timestamp": "2026-10-25T00:00:00Z",
            "message": {"role": "assistant", "content": list(content)},
        }
    )


def _user(uuid: str, *content: dict) -> bytes:
    return _line(
        {
            "type": "user",
            "uuid": uuid,
            "timestamp": "2026-10-25T00:00:01Z",
            "message": {"role": "user", "content": list(content)},
        }
    )


def _call(call_id: str) -> dict:
    return {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"c": call_id}}


def _result(call_id: str, text: str, error: bool = False) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": text,
        "is_error": error,
    }


def _claude_transcript() -> bytes:
    return b"".join(
        [
            _user("u1", {"type": "text", "text": "hello"}),
            _assistant("a1", {"type": "text", "text": "thinking"}),
            # A record with the uuid of an earlier one replaces that message.
            _assistant("a1", {"type": "text", "text": "thinking"}, _call("t1")),
            _user("r1", _result("t1", "one")),
            # A result before its call still joins the call.
            _user("r2", _result("t2", "early")),
            _assistant("a2", _call("t2")),
            b"not json\n",
            _assistant("a3", _call("t3"), _call("t4")),
            _user("r3", _result("t3", "three", error=True)),
            _user("r4", _result("t3", "three again")),
            # A later call with the same id takes the result from the first one.
            _assistant("a4", _call("t4")),
            _user("r5", _result("t4", "four")),
            _user("u2", {"type": "text", "text": "bye"}),
        ]
    )


def _whole(data: bytes, provider: str = "claude", generation=2, source_range=None):
    return build_messages(
        read_entries(data, provider),
        "conv",
        provider,
        generation,
        source_range=source_range,
    )


def test_every_cut_of_a_growing_transcript_gives_the_whole_build():
    data = _claude_transcript()
    for seed in range(40):
        rng = random.Random(seed)
        feed = TranscriptFeed("conv", "claude", 2)
        position = 0
        while position < len(data):
            step = rng.randint(1, 120)
            back = rng.randint(0, min(position, 30))
            assert feed.feed(position - back, data[position - back : position + step])
            position += step
            seen = data[: feed.seen]
            assert feed.builder.messages() == _whole(seen)
            middle = len(seen) // 2
            assert feed.builder.messages((middle, None)) == _whole(
                seen, source_range=(middle, None)
            )


def test_batches_of_entries_give_the_one_batch_build():
    entries = read_entries(_claude_transcript(), "claude")
    for cut in range(len(entries) + 1):
        builder = MessageBuilder("conv", "claude", 2)
        builder.add(entries[:cut])
        builder.add(entries[cut:])
        assert builder.messages() == build_messages(entries, "conv", "claude", 2)


def test_the_build_joins_results_and_counts_revisions():
    messages = {m.id: m for m in _whole(_claude_transcript())}

    replaced = messages["conv/g2/a1"]
    assert (replaced.revision, replaced.parts[-1].output["text"]) == (3, "one")
    early = messages["conv/g2/a2"]
    assert early.parts[0].output["text"] == "early"
    moved_from = messages["conv/g2/a3"]
    assert moved_from.parts[0].output["text"] == "three again"
    assert moved_from.parts[0].status == "completed"
    assert moved_from.parts[1].output is None
    assert messages["conv/g2/a4"].parts[0].output["text"] == "four"


def test_a_gap_before_the_fed_bytes_asks_for_a_whole_read():
    data = _claude_transcript()
    feed = TranscriptFeed("conv", "claude", 2)
    assert feed.feed(0, data[:100])

    assert not feed.feed(200, data[200:])
    assert feed.feed(100, data[100:])
    assert feed.builder.messages() == _whole(data)


def test_an_unfinished_line_waits_for_its_end():
    line = _assistant("a1", {"type": "text", "text": "hi"})
    feed = TranscriptFeed("conv", "claude", 2)

    assert feed.feed(0, line[:-1])
    assert feed.builder.messages() == ()
    assert feed.feed(len(line) - 1, line[-1:])
    assert feed.builder.messages() == _whole(line)


def test_gemini_is_parsed_whole_on_each_change():
    def document(*texts: str) -> bytes:
        return json.dumps(
            {
                "messages": [
                    {"id": f"m{i}", "type": "gemini", "content": text}
                    for i, text in enumerate(texts)
                ]
            }
        ).encode()

    first, second = document("a"), document("a", "b")
    feed = TranscriptFeed("conv", "gemini", 2)
    assert feed.feed(0, first)
    assert feed.feed(len(first), b"")

    assert not feed.feed(len(first), second[len(first) :])
    assert feed.feed(0, second)
    assert feed.builder.messages() == _whole(second, "gemini")


@pytest.mark.parametrize("slice_bytes", [1, 64, 300])
def test_slices_smaller_than_a_line_give_the_whole_build(monkeypatch, slice_bytes):
    monkeypatch.setattr(messages_module, "_FEED_SLICE_BYTES", slice_bytes)
    data = _claude_transcript()
    feed = TranscriptFeed("conv", "claude", 2)

    assert feed.feed(0, data)

    assert feed.builder.messages() == _whole(data)
    assert feed.seen == len(data)


def test_a_line_that_is_not_jsonl_stops_parsing_as_one_pass_does(monkeypatch):
    monkeypatch.setattr(messages_module, "_FEED_SLICE_BYTES", 64)
    head = _user("u1", {"type": "text", "text": "hello"})
    data = head + b"stray\r" + _user("u2", {"type": "text", "text": "after"})
    feed = TranscriptFeed("conv", "claude", 2)

    assert feed.feed(0, data[:100])
    assert feed.feed(100, data[100:])

    assert feed.builder.messages() == _whole(data)
    assert [m.parts[0].text for m in feed.builder.messages()] == ["hello"]
