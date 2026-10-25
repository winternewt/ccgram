import asyncio
from dataclasses import replace
from uuid import UUID

from ccgram.session_protocol.backend import Prompt
from ccgram.session_protocol.models import Action, ProtocolError
from ccgram.session_protocol.telegram import adapter_for


async def test_agent_text_uses_shared_message_action_and_deduplicates(service, backend):
    adapter = adapter_for({"session_service": service})

    first = await adapter.send_text(
        backend.target.id,
        "hello",
        action_id="telegram-message:123",
    )
    retry = await adapter.send_text(
        backend.target.id,
        "hello",
        action_id="telegram-message:123",
    )

    assert first == retry == (True, "Sent")
    assert backend.sent == [("text", "hello", True)]
    stored_id = service.store.db.execute("SELECT id FROM actions").fetchone()[0]
    assert UUID(stored_id)


async def test_missing_target_is_reported_without_sending(service, backend):
    adapter = adapter_for({"session_service": service})
    backend.disappear = True

    success, message = await adapter.send_text("@missing", "hello")

    assert not success and message == "gone"
    assert backend.sent == []


async def test_rejected_core_input_is_reported(service, backend):
    adapter = adapter_for({"session_service": service})
    attempts = []

    async def reject_input(target, text, *, enter, raw=None):
        attempts.append((text, enter, raw))
        return False

    backend.send_text = reject_input
    success, message = await adapter.send_text(
        backend.target.id,
        "hello",
        action_id="telegram-message:141",
    )

    assert not success and message == "Could not confirm delivery"
    assert attempts == [("hello", True, None)]
    assert (
        service.store.db.execute(
            "SELECT json_extract(result, '$.outcome') FROM actions"
        ).fetchone()[0]
        == "unknown"
    )


async def test_timed_out_core_input_is_recorded_as_unknown(service, backend):
    adapter = adapter_for({"session_service": service})

    async def timeout_input(target, text, *, enter, raw=None):
        raise TimeoutError

    backend.send_text = timeout_input
    success, message = await adapter.send_text(
        backend.target.id,
        "hello",
        action_id="telegram-message:142",
    )

    assert not success and message == "Could not confirm delivery"
    assert (
        service.store.db.execute(
            "SELECT json_extract(result, '$.outcome') FROM actions"
        ).fetchone()[0]
        == "unknown"
    )


async def test_shell_text_requires_explicit_terminal_intent(service, backend):
    backend.target = replace(backend.target, provider="shell")
    adapter = adapter_for({"session_service": service})

    success, message = await adapter.send_text(
        backend.target.id,
        "printf '%s' '$HOME'",
        terminal_input=True,
        action_id="telegram-message:124",
    )

    assert success and message == "Sent"
    assert backend.sent == [("text", "printf '%s' '$HOME'", True)]


async def test_numeric_terminal_choice_with_submit_false_is_paste_only(
    service, backend
):
    adapter = adapter_for({"session_service": service})

    success, message = await adapter.send_text(
        backend.target.id,
        "1",
        terminal_input=True,
        submit=False,
        action_id="telegram-callback:numeric-choice",
    )

    assert success and message == "Sent"
    assert backend.sent == [("text", "1", False)]


async def test_normal_text_is_rejected_for_shell_without_terminal_intent(
    service, backend
):
    backend.target = replace(backend.target, provider="shell")
    adapter = adapter_for({"session_service": service})

    success, message = await adapter.send_text(
        backend.target.id,
        "echo hello",
        action_id="telegram-message:139",
    )

    assert not success and "does not support this action" in message
    assert backend.sent == []


async def test_provider_exit_during_resolution_does_not_send_normal_text(
    service, backend
):
    adapter = adapter_for({"session_service": service})
    original_for_target = service.for_target

    async def provider_exits_during_resolution(window_id):
        result = await original_for_target(window_id)
        backend.target = replace(backend.target, provider="shell")
        return result

    service.for_target = provider_exits_during_resolution
    success, message = await adapter.send_text(
        backend.target.id,
        "normal agent text",
        action_id="telegram-message:140",
    )

    assert not success and "binding changed" in message
    assert backend.sent == []


async def test_interactive_terminal_submit_checks_fresh_prompt(service, backend):
    backend.current_prompt = Prompt("Allow this tool?\n1. Yes", "permission")
    adapter = adapter_for({"session_service": service})
    target, _ = await service.for_target(backend.target.id)
    prompt = await backend.prompt(target)
    assert prompt is not None
    expected = prompt.fingerprint
    backend.current_prompt = Prompt("A different prompt", "permission")

    success, message = await adapter.send_text(
        backend.target.id,
        "y",
        terminal_input=True,
        expected_prompt=expected,
        action_id="telegram-callback:125",
    )

    assert not success
    assert "prompt changed" in message
    assert backend.sent == []


async def test_interactive_terminal_submit_sends_literal_choice(service, backend):
    backend.current_prompt = Prompt("Allow this tool?\n1. Yes", "permission")
    adapter = adapter_for({"session_service": service})
    target, _ = await service.for_target(backend.target.id)
    prompt = await backend.prompt(target)
    assert prompt is not None
    expected = prompt.fingerprint

    success, _ = await adapter.send_text(
        backend.target.id,
        "y",
        terminal_input=True,
        expected_prompt=expected,
        action_id="telegram-callback:126",
    )

    assert success
    assert backend.sent == [("text", "y", True)]


async def test_same_action_id_cannot_retarget_a_different_prompt(service, backend):
    backend.current_prompt = Prompt("First prompt", "permission")
    adapter = adapter_for({"session_service": service})
    target, _ = await service.for_target(backend.target.id)
    first_prompt = await backend.prompt(target)
    assert first_prompt is not None
    first = await adapter.send_text(
        backend.target.id,
        "y",
        terminal_input=True,
        expected_prompt=first_prompt.fingerprint,
        action_id="telegram-callback:128",
    )
    backend.current_prompt = Prompt("Second prompt", "permission")
    target, _ = await service.for_target(backend.target.id)
    second_prompt = await backend.prompt(target)
    assert second_prompt is not None

    retry = await adapter.send_text(
        backend.target.id,
        "y",
        terminal_input=True,
        expected_prompt=second_prompt.fingerprint,
        action_id="telegram-callback:128",
    )

    assert first[0]
    assert not retry[0]
    assert "different action" in retry[1]
    assert backend.sent == [("text", "y", True)]


async def test_split_retry_returns_the_same_pane(service, backend):
    adapter = adapter_for({"session_service": service})
    created = []

    async def split(target):
        created.append(target.id)
        return "%2"

    backend.split = split

    first = await adapter.split(backend.target.id, action_id="telegram-message:127")
    retry = await adapter.split(backend.target.id, action_id="telegram-message:127")

    assert first == retry == (True, "%2")
    assert created == [backend.target.id]


async def test_split_uses_shared_action_lifecycle_and_publishes_update(
    service, backend
):
    adapter = adapter_for({"session_service": service})
    subscriber = service.hub.subscribe()

    async def unsupported_split(target):
        return None

    backend.split = unsupported_split

    success, message = await adapter.split(
        backend.target.id, action_id="telegram-message:130"
    )
    event = await subscriber.get()
    while event.type != "action.updated":
        event = await subscriber.get()
    service.hub.unsubscribe(subscriber)

    assert not success and message == "Split is not supported"
    assert event.type == "action.updated"
    assert event.data["outcome"] == "unknown"


async def test_reusing_action_id_for_another_pane_conflicts(service, backend):
    adapter = adapter_for({"session_service": service})

    async def select_pane(target, pane_id):
        return replace(target, pane_id=pane_id)

    backend.select_pane = select_pane

    first = await adapter.send_text(
        backend.target.id,
        "first",
        terminal_input=True,
        pane_id="%1",
        action_id="telegram-callback:131",
    )
    retry = await adapter.send_text(
        backend.target.id,
        "first",
        terminal_input=True,
        pane_id="%2",
        action_id="telegram-callback:131",
    )

    assert first[0]
    assert not retry[0] and "different action" in retry[1]
    assert backend.sent == [("text", "first", True)]


async def test_stale_pane_membership_prevents_telegram_input(service, backend):
    adapter = adapter_for({"session_service": service})

    async def missing_pane(target, pane_id):
        raise ProtocolError(409, "binding_conflict", "Selected pane no longer exists")

    backend.select_pane = missing_pane
    success, message = await adapter.send_text(
        backend.target.id,
        "never send",
        terminal_input=True,
        pane_id="%gone",
        action_id="telegram-callback:132",
    )

    assert not success and "no longer exists" in message
    assert backend.sent == []


async def test_public_action_data_cannot_forge_execution_context(service, backend):
    _, snapshot = await service.for_target(backend.target.id)
    action = Action(
        "00000000-0000-4000-8000-000000000138",
        snapshot.binding_version,
        "terminal.paste",
        {
            "text": "literal",
            "_executionContext": {"paneId": "%2", "expectedPrompt": None},
        },
    )
    selected = []

    async def select_pane(target, pane_id):
        selected.append(pane_id)
        return replace(target, pane_id=pane_id)

    backend.select_pane = select_pane
    result, _ = await service.execute(snapshot.id, action)
    assert result.outcome == "sent"

    try:
        await service.execute(snapshot.id, action, pane_id="%2")
    except ProtocolError as error:
        assert "different action" in error.message
    else:
        raise AssertionError("trusted pane context reused a public action result")

    assert backend.sent == [("text", "literal", False)]
    assert selected == []


async def test_telegram_submit_holds_input_lock_against_mobile_key(service, backend):
    adapter = adapter_for({"session_service": service})
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def submit(target, text, *, enter, raw=None):
        entered.set()
        await release.wait()
        calls.append(("submit", text, enter))
        return True

    async def send_key(target, key):
        calls.append(("key", key))
        return True

    backend.send_text = submit
    backend.send_key = send_key
    target, snapshot = await service.for_target(backend.target.id)
    telegram = asyncio.create_task(
        adapter.send_text(
            backend.target.id,
            "hello",
            terminal_input=True,
            action_id="telegram-message:133",
        )
    )
    await entered.wait()
    mobile = asyncio.create_task(
        service.execute(
            snapshot.id,
            Action(
                "00000000-0000-4000-8000-000000000134",
                snapshot.binding_version,
                "terminal.key",
                {"keys": ["Enter"]},
            ),
        )
    )
    await asyncio.sleep(0)
    assert calls == []
    release.set()
    assert (await telegram)[0]
    assert (await mobile)[0].outcome == "sent"
    assert calls == [("submit", "hello", True), ("key", "Enter")]


async def test_pi_followup_is_serialized_and_rejected_for_other_providers(
    service, backend
):
    adapter = adapter_for({"session_service": service})
    backend.target = replace(backend.target, provider="pi")
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def followup(target, text):
        calls.append(("paste", text))
        entered.set()
        await release.wait()
        calls.append(("enter", "M-Enter"))
        return True

    async def send_text(target, text, *, enter, raw=None):
        calls.append(("message", text, enter))
        return True

    backend.send_followup = followup
    backend.send_text = send_text
    target, snapshot = await service.for_target(backend.target.id)
    followup_task = asyncio.create_task(
        adapter.send_followup(
            backend.target.id, "next", action_id="telegram-message:135"
        )
    )
    await entered.wait()
    mobile = asyncio.create_task(
        service.execute(
            snapshot.id,
            Action(
                "00000000-0000-4000-8000-000000000136",
                snapshot.binding_version,
                "message.send",
                {"text": "later"},
            ),
        )
    )
    await asyncio.sleep(0)
    assert calls == [("paste", "next")]
    release.set()
    assert (await followup_task)[0]
    assert (await mobile)[0].outcome == "sent"
    assert calls == [
        ("paste", "next"),
        ("enter", "M-Enter"),
        ("message", "later", True),
    ]

    backend.target = replace(backend.target, provider="claude")
    rejected = await adapter.send_followup(
        backend.target.id, "unsupported", action_id="telegram-message:137"
    )
    assert not rejected[0] and "supported for Pi" in rejected[1]
