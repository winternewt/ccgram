import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call
from uuid import uuid4

import pytest

from ccgram.multiplexer.tmux import TmuxManager
from ccgram.multiplexer.vim_state import notify_vim_insert_seen, reset_vim_state
from ccgram.session_protocol.backend import Prompt
from ccgram.session_protocol.events import EventHub
from ccgram.session_protocol.models import Action
from ccgram.session_protocol.requests import PendingHook, RequestStore


def test_hook_cannot_return_after_an_intervening_prompt(monkeypatch):
    requests = RequestStore(EventHub())
    now = time.time()
    hook = PendingHook("conv", "permission", "Bash", {"command": "git status"}, now)
    first_prompt = Prompt("git status\n1. Yes\n2. No", "permission")
    requests.observe_hook("s", hook)
    first = requests.reconcile("s", "conv", 1, first_prompt)
    assert first is not None and first.request.source == "hook"
    requests.reconcile(
        "s", "conv", 1, Prompt("different command\n1. Yes\n2. No", "permission")
    )
    monkeypatch.setattr("ccgram.session_protocol.requests.time.time", lambda: now + 31)
    returning = requests.reconcile("s", "conv", 1, first_prompt)
    assert returning is not None and returning.request.source == "screen"
    assert not returning.option_keys


@pytest.mark.parametrize("replacement", ["different", "closed", "same_screen"])
@pytest.mark.parametrize("reconciled", [False, True])
async def test_response_rejects_replacement_during_pending_capture(
    service, backend, replacement, reconciled
):
    session = (await service.list_sessions())[0]
    first_prompt = Prompt("first command\n1. Yes\n2. No", "permission")
    backend.current_prompt = first_prompt
    service.requests.observe_hook(
        session.id,
        PendingHook(
            "conv", "permission", "Bash", {"command": "first command"}, time.time()
        ),
    )
    snapshot = await service.get_session(session.id)
    request = snapshot.open_requests[0]
    intent = Action(
        str(uuid4()),
        snapshot.binding_version,
        "request.respond",
        {"requestId": request.id, "optionId": "1"},
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original_prompt = backend.prompt
    calls = 0

    async def delayed_prompt(target):
        nonlocal calls
        calls += 1
        captured = await original_prompt(target)
        if calls == 3:
            entered.set()
            await release.wait()
        return captured

    backend.prompt = delayed_prompt
    backend.clear_on_input = True
    pending = asyncio.create_task(service.execute(session.id, intent))
    await entered.wait()
    command = "second command" if replacement == "different" else "first command"
    backend.current_prompt = (
        None
        if replacement == "closed"
        else Prompt(f"{command}\n1. Yes\n2. No", "permission")
    )
    service.requests.observe_hook(
        session.id,
        PendingHook(
            "conv",
            "permission",
            "Bash",
            {"command": command, "newInput": True},
            time.time(),
        ),
    )
    if reconciled:
        await service.refresh()
    release.set()
    result, status = await pending
    assert status == 409 and result.outcome == "rejected"
    assert backend.sent == []


async def test_raw_pinned_shell_launch_bypasses_stale_vim_state(monkeypatch):
    manager = TmuxManager.__new__(TmuxManager)
    pane = MagicMock()
    pane.pane_id = "%1"
    pane.capture_pane.return_value = ["⌘0⌘"]
    window = MagicMock()
    window.window_id, window.panes, window.active_pane = "@1", [pane], pane
    session = MagicMock()
    session.windows.__iter__.side_effect = lambda: iter([window])
    session.windows.get.return_value = window
    manager.get_session = MagicMock(return_value=session)
    monkeypatch.setattr("ccgram.multiplexer.tmux.asyncio.sleep", AsyncMock())
    reset_vim_state()
    notify_vim_insert_seen("@1")
    try:
        assert await manager.send_to_pane("%1", "claude", window_id="@1")
        assert pane.send_keys.call_args_list == [
            call("claude", enter=True, literal=True)
        ]
    finally:
        reset_vim_state()


@pytest.mark.parametrize(
    "kind,structured",
    [("request.respond", True), ("message.send", True), ("message.send", False)],
)
@pytest.mark.parametrize("reconciled", [False, True])
@pytest.mark.parametrize("retired", [False, True])
async def test_completion_preserves_a_request_opened_during_confirmation(
    service, backend, kind, structured, reconciled, retired
):
    session = (await service.list_sessions())[0]
    backend.current_prompt = Prompt("first command\n1. Yes\n2. No", "permission")
    if structured:
        service.requests.observe_hook(
            session.id,
            PendingHook(
                "conv", "permission", "Bash", {"command": "first command"}, time.time()
            ),
        )
    snapshot = await service.get_session(session.id)
    data = (
        {"requestId": snapshot.open_requests[0].id, "optionId": "1"}
        if kind == "request.respond"
        else {"text": "must not reach second prompt", "dismissRequest": True}
    )
    intent = Action(str(uuid4()), snapshot.binding_version, kind, data)
    original_prompt = backend.prompt
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    confirmation_call = 4 if kind == "request.respond" else 3

    async def delayed_confirmation(target):
        nonlocal calls
        calls += 1
        captured = await original_prompt(target)
        if calls == confirmation_call:
            assert captured is None
            entered.set()
            await release.wait()
        return captured

    backend.prompt = delayed_confirmation
    backend.clear_on_input = True
    pending = asyncio.create_task(service.execute(session.id, intent))
    await entered.wait()
    if retired:
        await service.refresh()
        assert service.requests.get(session.id) is None
    backend.current_prompt = Prompt("second command\n1. Yes\n2. No", "permission")
    service.requests.observe_hook(
        session.id,
        PendingHook(
            "conv", "permission", "Bash", {"command": "second command"}, time.time()
        ),
    )
    if reconciled:
        await service.refresh()
    release.set()
    result, _ = await pending
    await service.refresh()
    second = service.requests.get(session.id)
    assert second is not None and second.request.id != snapshot.open_requests[0].id
    assert second.request.source == "hook"
    assert second.request.tool is not None
    assert second.request.tool["input"] == {"command": "second command"}
    if kind == "request.respond":
        assert result.outcome == "sent"
        assert backend.sent == [("text", "1", False)]
    else:
        assert result.outcome == "unknown"
        assert backend.sent == [("key", "Escape", False)]


@pytest.mark.parametrize("replaced", [False, True])
async def test_screen_dismissal_with_unchanged_unmatched_hook_forwards_text(
    service, backend, replaced
):
    session = (await service.list_sessions())[0]
    backend.current_prompt = Prompt("unrecognized menu\n1. Yes\n2. No", "permission")
    service.requests.observe_hook(
        session.id,
        PendingHook(
            "conv", "permission", "Bash", {"command": "git status"}, time.time()
        ),
    )
    snapshot = await service.get_session(session.id)
    assert snapshot.open_requests[0].source == "screen"
    if replaced:
        original_id = snapshot.open_requests[0].id
        service.requests.observe_hook(
            session.id,
            PendingHook(
                "conv", "permission", "Bash", {"command": "git diff"}, time.time()
            ),
        )
        snapshot = await service.get_session(session.id)
        current = snapshot.open_requests[0]
        assert current.id != original_id and current.source == "screen"
        assert not current.options and current.response_schema is None
    backend.clear_on_input = True
    intent = Action(
        str(uuid4()),
        snapshot.binding_version,
        "message.send",
        {"text": "continue", "dismissRequest": True},
    )
    result, status = await service.execute(session.id, intent)
    assert status == 200 and result.outcome == "sent"
    assert backend.sent == [("key", "Escape", False), ("text", "continue", True)]


@pytest.mark.parametrize("new_hook", [False, True])
@pytest.mark.parametrize("retirement", ["absent_prompt", "explicit_close"])
@pytest.mark.parametrize("intervening_poll", [False, True])
def test_request_retirement_preserves_only_new_unconsumed_hook(
    new_hook, retirement, intervening_poll
):
    requests = RequestStore(EventHub())
    prompt = Prompt("git status\n1. Yes\n2. No", "permission")
    first_hook = PendingHook(
        "conv", "permission", "Bash", {"command": "git status"}, time.time()
    )
    requests.observe_hook("s", first_hook)
    first = requests.reconcile("s", "conv", 1, prompt)
    assert first is not None and first.request.source == "hook"
    if new_hook:
        requests.observe_hook(
            "s",
            PendingHook(
                "conv",
                "permission",
                "Bash",
                {"command": "git diff"},
                time.time(),
            ),
        )
    if intervening_poll:
        intermediate = requests.reconcile("s", "conv", 1, prompt)
        assert intermediate is not None
        assert intermediate.request.source == ("screen" if new_hook else "hook")
    if retirement == "absent_prompt":
        assert requests.reconcile("s", "conv", 1, None) is None
    else:
        requests.close("s", "turn_ended")
    next_prompt = (
        Prompt("git diff\n1. Yes\n2. No", "permission") if new_hook else prompt
    )
    second = requests.reconcile("s", "conv", 1, next_prompt)
    assert second is not None and second.request.id != first.request.id
    expected_source = "hook" if new_hook and retirement == "absent_prompt" else "screen"
    assert second.request.source == expected_source
    assert bool(second.option_keys) == (expected_source == "hook")


@pytest.mark.parametrize("retirement", ["answered", "changed_prompt", "replaced"])
def test_consumed_hook_cannot_replay_after_another_hook_binds(retirement):
    requests = RequestStore(EventHub())
    first_hook = PendingHook(
        "conv", "permission", "Bash", {"command": "first"}, time.time()
    )
    first_prompt = Prompt("first\n1. Yes\n2. No", "permission")
    requests.observe_hook("s", first_hook)
    first = requests.reconcile("s", "conv", 1, first_prompt)
    assert first is not None and first.request.source == "hook"
    if retirement == "answered":
        requests.close("s", "answered")
    elif retirement == "changed_prompt":
        requests.reconcile("s", "conv", 1, Prompt("other menu", "permission"))
    requests.observe_hook(
        "s",
        PendingHook("conv", "permission", "Bash", {"command": "second"}, time.time()),
    )
    second = requests.reconcile(
        "s", "conv", 1, Prompt("second\n1. Yes\n2. No", "permission")
    )
    assert second is not None and second.request.source == "hook"
    requests.observe_hook("s", first_hook)
    replayed = requests.reconcile("s", "conv", 1, first_prompt)
    assert replayed is not None and replayed.request.source == "screen"
    assert not replayed.option_keys


def test_explicit_invalidation_consumes_unbound_pending_evidence():
    requests = RequestStore(EventHub())
    hook = PendingHook("conv", "permission", "Bash", {"command": "first"}, time.time())
    requests.observe_hook("s", hook)
    requests.close("s", "turn_ended")
    requests.observe_hook("s", hook)
    reopened = requests.reconcile(
        "s", "conv", 1, Prompt("first\n1. Yes\n2. No", "permission")
    )
    assert reopened is not None and reopened.request.source == "screen"


@pytest.mark.parametrize("elapsed", [1, 31])
def test_fresh_hook_with_same_input_is_independent_of_consumed_hook(
    monkeypatch, elapsed
):
    requests = RequestStore(EventHub())
    now = time.time()
    hook = PendingHook("conv", "permission", "Bash", {"command": "first"}, now)
    prompt = Prompt("first\n1. Yes\n2. No", "permission")
    requests.observe_hook("s", hook)
    requests.reconcile("s", "conv", 1, prompt)
    requests.close("s", "answered")
    monkeypatch.setattr(
        "ccgram.session_protocol.requests.time.time", lambda: now + elapsed
    )
    requests.observe_hook("s", hook)
    old = requests.reconcile("s", "conv", 1, prompt)
    assert old is not None and old.request.source == "screen"
    fresh_hook = PendingHook(
        "conv", "permission", "Bash", {"command": "first"}, now + elapsed
    )
    requests.observe_hook("s", fresh_hook)
    fresh = requests.reconcile("s", "conv", 1, prompt)
    assert fresh is not None and fresh.request.source == "hook"


@pytest.mark.parametrize("elapsed", [1, 31])
def test_replayed_hook_does_not_displace_newer_controls(monkeypatch, elapsed):
    requests = RequestStore(EventHub())
    now = time.time()
    first_hook = PendingHook("conv", "permission", "Bash", {"command": "first"}, now)
    requests.observe_hook("s", first_hook)
    requests.reconcile("s", "conv", 1, Prompt("first\n1. Yes\n2. No", "permission"))
    requests.close("s", "answered")
    monkeypatch.setattr(
        "ccgram.session_protocol.requests.time.time", lambda: now + elapsed
    )
    second_hook = PendingHook(
        "conv", "permission", "Bash", {"command": "second"}, now + elapsed
    )
    second_prompt = Prompt("second\n1. Yes\n2. No", "permission")
    requests.observe_hook("s", second_hook)
    second = requests.reconcile("s", "conv", 1, second_prompt)
    assert second is not None and second.request.source == "hook"
    requests.observe_hook("s", first_hook)
    assert requests.is_current("s", second)
    assert requests.reconcile("s", "conv", 1, second_prompt) is second


def test_retired_binding_never_reopens_after_tombstone_expiry(monkeypatch):
    requests = RequestStore(EventHub())
    now = time.time()
    first_hook = PendingHook("conv", "permission", "Bash", {"command": "first"}, now)
    prompt = Prompt("first\n1. Yes\n2. No", "permission")
    requests.observe_hook("s", first_hook)
    requests.reconcile("s", "conv", 1, prompt)
    requests.observe_hook(
        "s", PendingHook("conv", "permission", "Bash", {"command": "other"}, now)
    )
    requests.reconcile("s", "conv", 1, prompt)
    requests.observe_hook("s", first_hook)
    requests.reconcile("s", "conv", 1, prompt)
    monkeypatch.setattr("ccgram.session_protocol.requests.time.time", lambda: now + 31)
    reopened = requests.reconcile("s", "conv", 1, prompt)
    assert reopened is not None and reopened.request.source == "screen"
    assert not reopened.option_keys


def test_continuously_active_hook_keeps_controls_after_matching_ttl(monkeypatch):
    requests = RequestStore(EventHub())
    now = time.time()
    hook = PendingHook("conv", "permission", "Bash", {"command": "first"}, now)
    prompt = Prompt("first\n1. Yes\n2. No", "permission")
    requests.observe_hook("s", hook)
    opened = requests.reconcile("s", "conv", 1, prompt)
    assert opened is not None and opened.request.source == "hook"
    monkeypatch.setattr("ccgram.session_protocol.requests.time.time", lambda: now + 31)
    requests.observe_hook("s", hook)
    assert requests.reconcile("s", "conv", 1, prompt) is opened


@pytest.mark.parametrize("bound", [False, True])
def test_superseded_hook_cannot_restore_old_request_before_reconciliation(bound):
    requests = RequestStore(EventHub())
    now = time.time()
    first_hook = PendingHook(
        "conv", "permission", "Edit", {"file_path": "/tmp/file", "content": "old"}, now
    )
    second_hook = PendingHook(
        "conv", "permission", "Edit", {"file_path": "/tmp/file", "content": "new"}, now
    )
    prompt = Prompt("/tmp/file\n1. Yes\n2. No", "permission")
    requests.observe_hook("s", first_hook)
    first = requests.reconcile("s", "conv", 1, prompt) if bound else None
    requests.observe_hook("s", second_hook)
    requests.observe_hook("s", first_hook)
    if first is not None:
        assert not requests.is_current("s", first)
    second = requests.reconcile("s", "conv", 1, prompt)
    assert second is not None and second.request.source == "hook"
    assert second.request.tool is not None
    assert second.request.tool["input"]["content"] == "new"
    requests.observe_hook("s", second_hook)
    assert requests.is_current("s", second)


def test_older_unseen_hook_cannot_replace_newer_pending_evidence():
    requests = RequestStore(EventHub())
    now = time.time()
    newer = PendingHook("conv", "permission", "Bash", {"command": "newer"}, now)
    older = PendingHook("conv", "permission", "Bash", {"command": "older"}, now - 1)
    requests.observe_hook("s", newer)
    requests.observe_hook("s", older)
    opened = requests.reconcile(
        "s", "conv", 1, Prompt("newer\n1. Yes\n2. No", "permission")
    )
    assert opened is not None and opened.request.source == "hook"
