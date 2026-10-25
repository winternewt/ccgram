import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ccgram.hooks.adapters import ClaudeHookAdapter
from ccgram.providers.base import HookEvent
from ccgram.session_protocol.backend import Prompt
from ccgram.session_protocol.models import Action, ProtocolError
from ccgram.session_protocol import store as store_module
from ccgram.session_protocol.service import LocalSessionService


def append(path, data):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(data) + "\n")


@pytest.fixture
def full_reads(monkeypatch):
    """Observe every transcript with a full read, as before the observation cache.

    The race tests below change the file at the moment the store reads it; an
    unchanged file is not read at all once cached, so they pin the full read.
    """
    real = store_module._observe_transcript
    monkeypatch.setattr(
        store_module,
        "_observe_transcript",
        lambda path, row, _memo: real(path, row, None),
    )


async def test_delivery_correlates_after_transcript_was_published_during_send(
    service, backend, tmp_path, monkeypatch
):
    path = tmp_path / "transcript.jsonl"
    path.touch()
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    action = Action(
        str(uuid4()), session.binding_version, "message.send", {"text": "hello"}
    )

    async def send_text(target, text, *, enter, raw=None):
        append(
            path,
            {
                "type": "user",
                "uuid": "early",
                "timestamp": datetime.now(UTC).isoformat(),
                "message": {"content": text},
            },
        )
        await service.refresh()
        return True

    monkeypatch.setattr(backend, "send_text", send_text)
    result, status = await service.execute(session.id, action)
    assert status == 200 and result.outcome == "sent"
    await service.refresh()
    _, events = service.hub.replay(service.hub.epoch, 0)
    assert any(
        event.type == "action.updated"
        and event.data.get("actionId") == action.id
        and event.data.get("messageId") == "conv/early"
        for event in events
    )
    replacements = [event.data for event in events if event.type == "message.upsert"]
    assert len(replacements) == 2
    assert replacements[0]["actionId"] is None
    assert replacements[1]["actionId"] == action.id
    assert replacements[1]["revision"] > replacements[0]["revision"]
    history = await service.history(session.id, None, 50)
    assert history.messages[0].revision == replacements[1]["revision"]
    restarted = LocalSessionService(backend, service.store)
    after_restart = await restarted.history(session.id, None, 50)
    assert after_restart.messages[0] == history.messages[0]


async def test_history_joins_results_beyond_page_and_rejects_cross_conversation_cursor(
    service, backend, tmp_path
):
    path = tmp_path / "transcript.jsonl"
    path.touch()
    backend.target = replace(backend.target, transcript=path)
    append(
        path,
        {
            "type": "assistant",
            "uuid": "first",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "id": "tool",
                        "input": {"command": "test"},
                    }
                ]
            },
        },
    )
    append(
        path, {"type": "assistant", "uuid": "second", "message": {"content": "later"}}
    )
    append(
        path,
        {
            "type": "user",
            "uuid": "result",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool", "content": "passed"}
                ]
            },
        },
    )
    session = (await service.list_sessions())[0]
    newest = await service.history(session.id, None, 1)
    assert newest.prev_cursor
    older = await service.history(session.id, newest.prev_cursor, 1)
    assert older.messages[0].revision == 2
    assert older.messages[0].parts[0].output["text"] == "passed"
    backend.target = replace(backend.target, conversation_id="new-conversation")
    with pytest.raises(ProtocolError, match="cursor"):
        await service.history(session.id, newest.prev_cursor, 1)


async def test_history_source_range_keeps_tool_result_with_its_call(
    service, backend, tmp_path
):
    path = tmp_path / "transcript.jsonl"
    path.touch()
    backend.target = replace(backend.target, transcript=path)
    append(
        path,
        {
            "type": "assistant",
            "uuid": "call",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "id": "tool",
                        "input": {"command": "test"},
                    }
                ]
            },
        },
    )
    append(
        path, {"type": "assistant", "uuid": "middle", "message": {"content": "later"}}
    )
    result_offset = path.stat().st_size
    append(
        path,
        {
            "type": "user",
            "uuid": "result",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool", "content": "passed"}
                ]
            },
        },
    )
    session = (await service.list_sessions())[0]

    page = await service.history_by_source_range(session.id, result_offset)

    assert len(page.messages) == 1
    assert page.messages[0].id == "conv/call"
    assert page.messages[0].parts[0].output["text"] == "passed"


async def test_transcript_replacement_changes_generation_ids_and_binding(
    service, backend, tmp_path
):
    path = tmp_path / "transcript.jsonl"
    append(
        path, {"type": "assistant", "uuid": "same", "message": {"content": "before"}}
    )
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    first = await service.history(session.id, None, 50)
    replacement = tmp_path / "replacement"
    append(
        replacement,
        {"type": "assistant", "uuid": "same", "message": {"content": "after"}},
    )
    replacement.replace(path)
    current = await service.get_session(session.id)
    second = await service.history(session.id, None, 50)
    assert current.binding_version == session.binding_version + 1
    assert current.conversation.history_generation == 1
    assert first.messages[0].id != second.messages[0].id
    restarted = LocalSessionService(backend, service.store)
    assert (await restarted.history(session.id, None, 50)).messages[
        0
    ].id == second.messages[0].id


async def test_sent_message_gets_delivered_identity_and_survives_restart(
    service, backend, tmp_path
):
    path = tmp_path / "transcript.jsonl"
    path.touch()
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    action = Action(
        str(uuid4()), session.binding_version, "message.send", {"text": "hello   world"}
    )
    result, _ = await service.execute(session.id, action)
    assert result.outcome == "sent"
    append(
        path,
        {
            "uuid": "user-entry",
            "type": "user",
            "timestamp": datetime.now(UTC).isoformat(),
            "message": {"content": "hello world"},
        },
    )
    await service.refresh()
    messages = (await service.history(session.id, None, 50)).messages
    assert messages[0].action_id == action.id
    restarted = LocalSessionService(backend, service.store)
    assert (await restarted.history(session.id, None, 50)).messages[
        0
    ].action_id == action.id
    _, events = service.hub.replay(service.hub.epoch, 0)
    assert any(
        event.type == "action.updated"
        and event.data.get("messageId") == "conv/user-entry"
        for event in events
    )


async def test_hook_digest_matches_transcript_without_persisting_tool_input(
    service, backend, tmp_path
):
    path = tmp_path / "transcript.jsonl"
    path.touch()
    backend.target = replace(
        backend.target,
        transcript=path,
        conversation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    tool_input = {"command": "uv run pytest", "private": "not-in-hook-log"}
    append(
        path,
        {
            "uuid": "tool-entry",
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call",
                        "name": "Bash",
                        "input": tool_input,
                    }
                ]
            },
        },
    )
    session = (await service.list_sessions())[0]
    event = ClaudeHookAdapter().normalize(
        {
            "hook_event_name": "PermissionRequest",
            "session_id": backend.target.conversation_id,
            "tool_name": "Bash",
            "tool_input": tool_input,
        }
    )
    assert event is not None
    assert "not-in-hook-log" not in json.dumps(event.data)
    await service.observe_hook(
        HookEvent(
            event.canonical_event_name,
            "ccgram:@1",
            event.session_id,
            event.data,
            time.time(),
        ),
        "ccgram:",
    )
    backend.current_prompt = Prompt("uv run pytest\n1. Yes\n2. No", "PermissionPrompt")
    session = await service.get_session(session.id)
    assert session.open_requests[0].source == "hook"


async def test_resume_is_available_only_for_a_previous_agent_in_a_shell(
    service, backend
):
    backend.target = replace(backend.target, provider="shell", resume_provider="claude")
    session = (await service.list_sessions())[0]
    assert "session.resume" in session.capabilities
    result, status = await service.execute(
        session.id,
        Action(
            str(uuid4()),
            session.binding_version,
            "session.resume",
            {"conversationId": "conv"},
        ),
    )
    assert status == 200 and result.outcome == "sent"
    assert backend.sent == [("resume", "conv", True)]


@pytest.mark.parametrize("operation", ["history", "refresh", "action"])
@pytest.mark.parametrize("replacement", [True, False])
async def test_source_change_during_read_never_uses_old_generation(
    service, backend, tmp_path, monkeypatch, operation, replacement, full_reads
):
    from contextlib import contextmanager
    from pathlib import Path

    path = tmp_path / "transcript.jsonl"
    original = {"type": "assistant", "uuid": "same", "message": {"content": "old"}}
    changed = {**original, "message": {"content": "new"}}
    append(path, original)
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    initial = (await service.history(session.id, None, 50)).messages[0]
    real_open = Path.open
    reads = 0
    mutate_on = 2 if operation == "action" else 1

    @contextmanager
    def open_source(source, mode="r", *args, **kwargs):
        nonlocal reads
        with real_open(source, mode, *args, **kwargs) as stream:
            yield stream
        if source == path and mode == "rb":
            reads += 1
            if reads == mutate_on:
                destination = tmp_path / "replacement" if replacement else path
                with real_open(destination, "w", encoding="utf-8") as stream:
                    stream.write(json.dumps(changed) + "\n")
                if replacement:
                    destination.replace(path)

    monkeypatch.setattr(Path, "open", open_source)
    if operation == "action":
        result, _ = await service.execute(
            session.id,
            Action(
                str(uuid4()), session.binding_version, "message.send", {"text": "hi"}
            ),
        )
        assert result.outcome == "rejected"
        assert result.error["code"] == "binding_conflict"
        assert backend.sent == []
    elif operation == "refresh":
        await service.refresh()
        _, events = service.hub.replay(service.hub.epoch, 0)
        updates = [e.data for e in events if e.type == "message.upsert"]
        assert updates[-1]["id"] != initial.id
    history = await service.history(session.id, None, 50)
    assert history.history_generation == 1
    assert history.messages[0].id != initial.id
    assert history.messages[0].parts[0].text == "new"


@pytest.mark.parametrize("replacement", [True, False])
async def test_source_change_between_cached_observations_moves_the_generation(
    service, backend, tmp_path, replacement
):
    path = tmp_path / "transcript.jsonl"
    original = {"type": "assistant", "uuid": "same", "message": {"content": "old"}}
    append(path, original)
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    initial = (await service.history(session.id, None, 50)).messages[0]
    await service.refresh()
    changed = json.dumps({**original, "message": {"content": "new"}}) + "\n"
    if replacement:
        (tmp_path / "replacement").write_text(changed, encoding="utf-8")
        (tmp_path / "replacement").replace(path)
    else:
        path.write_text(changed, encoding="utf-8")

    await service.refresh()

    history = await service.history(session.id, None, 50)
    assert history.history_generation == 1
    assert history.messages[0].id != initial.id
    assert history.messages[0].parts[0].text == "new"


async def test_appended_entries_reach_history_without_a_new_generation(
    service, backend, tmp_path
):
    path = tmp_path / "transcript.jsonl"
    append(path, {"type": "assistant", "uuid": "a", "message": {"content": "one"}})
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    await service.refresh()
    append(path, {"type": "assistant", "uuid": "b", "message": {"content": "two"}})

    await service.refresh()

    history = await service.history(session.id, None, 50)
    assert history.history_generation == 0
    assert [m.parts[0].text for m in history.messages] == ["one", "two"]


@pytest.fixture
def whole_reads(monkeypatch):
    """Count the store's whole-file reads of a transcript."""
    real = store_module._transcript_record
    reads: list[int] = []

    def counting(path, row):
        snapshot = real(path, row)
        reads.append(len(snapshot.content))
        return snapshot

    monkeypatch.setattr(store_module, "_transcript_record", counting)
    return reads


async def test_a_growing_transcript_is_parsed_from_where_it_was_left(
    service, backend, tmp_path, whole_reads
):
    path = tmp_path / "transcript.jsonl"
    append(path, {"type": "assistant", "uuid": "a", "message": {"content": "one"}})
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    await service.history(session.id, None, 50)
    for index in range(5):
        append(
            path,
            {"type": "assistant", "uuid": f"n{index}", "message": {"content": "more"}},
        )
        await service.refresh()

    history = await service.history(session.id, None, 50)

    assert len(whole_reads) == 1
    assert [m.parts[0].text for m in history.messages] == ["one"] + ["more"] * 5


async def test_bytes_no_parse_saw_are_filled_by_one_whole_read(
    service, backend, tmp_path, whole_reads
):
    path = tmp_path / "transcript.jsonl"
    append(path, {"type": "assistant", "uuid": "a", "message": {"content": "one"}})
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    append(path, {"type": "assistant", "uuid": "b", "message": {"content": "two"}})
    # An observation whose appended bytes no message read takes.
    await service._source_version(service._targets[session.id])
    append(path, {"type": "assistant", "uuid": "c", "message": {"content": "three"}})

    history = await service.history(session.id, None, 50)

    assert len(whole_reads) == 2
    assert [m.parts[0].text for m in history.messages] == ["one", "two", "three"]


@pytest.mark.parametrize("replacement", [True, False])
async def test_action_rechecks_source_after_message_parsing(
    service, backend, tmp_path, monkeypatch, replacement
):
    from pathlib import Path

    path = tmp_path / "transcript.jsonl"
    append(path, {"type": "assistant", "uuid": "old", "message": {"content": "old"}})
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    real_stat = Path.stat
    observations = 0

    def stat_and_rotate(source, *args, **kwargs):
        nonlocal observations
        if source == path:
            observations += 1
            if observations == 4:
                import os

                previous = real_stat(path)
                destination = tmp_path / "new-transcript" if replacement else path
                with destination.open("w", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "type": "assistant",
                                "uuid": "new",
                                "message": {"content": "new"},
                            }
                        )
                        + "\n"
                    )
                if replacement:
                    destination.replace(path)
                else:
                    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        return real_stat(source, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_and_rotate)
    result, _ = await service.execute(
        session.id,
        Action(str(uuid4()), session.binding_version, "message.send", {"text": "hi"}),
    )
    assert result.outcome == "rejected"
    assert result.error["code"] == "binding_conflict"
    assert backend.sent == []


async def test_unstable_source_fails_closed(
    service, backend, tmp_path, monkeypatch, full_reads
):
    from contextlib import contextmanager
    from pathlib import Path

    path = tmp_path / "transcript.jsonl"
    path.write_text("{}\n")
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    real_open = Path.open

    @contextmanager
    def unstable_open(source, mode="r", *args, **kwargs):
        with real_open(source, mode, *args, **kwargs) as stream:
            yield stream
        if source == path and mode == "rb":
            with real_open(path, "ab") as stream:
                stream.write(b"{}\n")

    monkeypatch.setattr(Path, "open", unstable_open)
    result, _ = await service.execute(
        session.id,
        Action(str(uuid4()), session.binding_version, "message.send", {"text": "hi"}),
    )
    assert result.outcome == "rejected"
    assert backend.sent == []
    snapshot = await service.get_session(session.id)
    assert snapshot.status.state == "unknown"
    assert "message.send" not in snapshot.capabilities
