"""Unit tests for the native-agent-status gap-fill in ``observe``.

``_native_agent_status`` synthesizes a busy ``StatusUpdate`` from a backend's
native agent state (herdr) when terminal scraping yielded nothing. It is gated
on ``capabilities.native_agent_status`` and only surfaces ``working`` /
``blocked``; ``idle`` / ``done`` / ``unknown`` return None so the existing
activity-based idle/done logic stays in control.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from ccgram.handlers.polling.polling_coordinator import _tick_bound_windows
from ccgram.handlers.polling.window_tick.observe import _native_agent_status
from ccgram.multiplexer import agent_status_cache
from ccgram.multiplexer.base import AgentStatus


@pytest.fixture(autouse=True)
def _clear_status_cache():
    # The push-status cache is a process-global; isolate every test so the
    # subprocess-fallback tests see a cold cache regardless of run order.
    agent_status_cache.reset()
    yield
    agent_status_cache.reset()


def _fake_mux(native: bool, status: AgentStatus | None) -> MagicMock:
    mux = MagicMock()
    mux.capabilities = SimpleNamespace(
        native_agent_status=native, supports_event_stream=True
    )
    mux.agent_status = AsyncMock(return_value=status)
    return mux


@pytest.mark.parametrize(
    ("before", "after", "expected_label"),
    [
        ("working", "idle", None),
        ("idle", "working", "working"),
        ("blocked", "done", None),
    ],
)
async def test_probe_only_backend_reads_each_status_transition(
    before: str, after: str, expected_label: str | None
) -> None:
    mux = _fake_mux(native=True, status=AgentStatus(before))
    mux.capabilities.supports_event_stream = False
    mux.agent_status.side_effect = [AgentStatus(before), AgentStatus(after)]
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        await _native_agent_status("agterm-window")
        status = await _native_agent_status("agterm-window")

    assert (status.display_label if status else None) == expected_label
    assert mux.agent_status.await_count == 2


async def test_returns_none_when_backend_lacks_native_status() -> None:
    mux = _fake_mux(native=False, status=AgentStatus(state="working"))
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        assert await _native_agent_status("w2:t1") is None
    mux.agent_status.assert_not_awaited()  # gated before the call


async def test_working_state_becomes_busy_status() -> None:
    mux = _fake_mux(
        native=True,
        status=AgentStatus(state="working", agent="codex", custom_status="indexing"),
    )
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        status = await _native_agent_status("w2:t1")
    assert status is not None
    assert status.raw_text == "indexing"  # custom_status preferred
    assert status.is_interactive is False


async def test_working_without_custom_status_uses_default_label() -> None:
    mux = _fake_mux(native=True, status=AgentStatus(state="working", agent="codex"))
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        status = await _native_agent_status("w2:t1")
    assert status is not None
    assert status.raw_text == "working"


async def test_blocked_state_surfaces_waiting() -> None:
    mux = _fake_mux(native=True, status=AgentStatus(state="blocked", agent="claude"))
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        status = await _native_agent_status("w2:t1")
    assert status is not None
    assert status.raw_text == "waiting for input"


@pytest.mark.parametrize("state", ["idle", "done", "unknown"])
async def test_idle_done_unknown_yield_none(state: str) -> None:
    mux = _fake_mux(native=True, status=AgentStatus(state=state, agent="claude"))
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        assert await _native_agent_status("w2:t1") is None


async def test_none_native_status_yields_none() -> None:
    mux = _fake_mux(native=True, status=None)
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        assert await _native_agent_status("w2:t1") is None


async def test_negative_cache_hit_skips_subsequent_subprocess() -> None:
    mux = _fake_mux(native=True, status=None)
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        assert await _native_agent_status("w2:t1") is None
        assert await _native_agent_status("w2:t1") is None

    mux.agent_status.assert_awaited_once_with("w2:t1")


async def test_cache_hit_skips_subprocess() -> None:
    # A warm push cache is read synchronously; the subprocess agent_status()
    # call is skipped (the per-tick subprocess the event stream replaces).
    mux = _fake_mux(native=True, status=AgentStatus(state="idle"))
    agent_status_cache.set_status(
        "w2:t1", AgentStatus(state="working", agent="codex", custom_status="linking")
    )
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        status = await _native_agent_status("w2:t1")
    assert status is not None
    assert status.raw_text == "linking"  # from the cache, not the subprocess
    mux.agent_status.assert_not_awaited()


async def test_cold_cache_falls_back_to_subprocess() -> None:
    mux = _fake_mux(native=True, status=AgentStatus(state="working", agent="codex"))
    with patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux):
        status = await _native_agent_status("w2:t1")
    assert status is not None
    assert status.raw_text == "working"
    mux.agent_status.assert_awaited_once()  # cold cache → one subprocess call


@pytest.mark.parametrize("event_stream", [True, False])
async def test_probe_error_fails_soft_logs_warning_and_recovers(
    event_stream: bool,
) -> None:
    mux = _fake_mux(native=True, status=None)
    mux.capabilities.supports_event_stream = event_stream
    mux.agent_status.side_effect = [
        RuntimeError("socket died"),
        RuntimeError("socket died"),
        AgentStatus(state="working", custom_status="recovered"),
    ]
    window_id = "w2:t1:event-stream" if event_stream else "w2:t1:probe-only"

    with (
        capture_logs() as logs,
        patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux),
    ):
        assert await _native_agent_status(window_id) is None
        assert await _native_agent_status(window_id) is None
        status = await _native_agent_status(window_id)

    assert status is not None
    assert status.raw_text == "recovered"
    assert mux.agent_status.await_count == 3
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1
    assert "socket died" in warnings[0]["event"]


@pytest.mark.parametrize("event_stream", [True, False])
async def test_probe_cancellation_propagates(event_stream: bool) -> None:
    mux = _fake_mux(native=True, status=None)
    mux.capabilities.supports_event_stream = event_stream
    mux.agent_status.side_effect = asyncio.CancelledError

    with (
        patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux),
        pytest.raises(asyncio.CancelledError),
    ):
        await _native_agent_status("w2:t1")


@pytest.mark.parametrize("event_stream", [True, False])
async def test_probe_failure_does_not_skip_later_window_tick(
    event_stream: bool,
) -> None:
    mux = _fake_mux(native=True, status=None)
    mux.capabilities.supports_event_stream = event_stream
    mux.agent_status.side_effect = [
        RuntimeError("probe failed"),
        AgentStatus(state="working"),
    ]
    window_ids = [
        f"probe-error-{event_stream}-first",
        f"probe-error-{event_stream}-second",
    ]
    visited: list[str] = []

    async def tick(_bot, _user_id, _thread_id, window_id, _window, **_kwargs):
        visited.append(window_id)
        await _native_agent_status(window_id)

    with (
        patch("ccgram.handlers.polling.window_tick.observe.tmux_manager", mux),
        patch("ccgram.handlers.polling.polling_coordinator.thread_router") as router,
        patch(
            "ccgram.handlers.polling.polling_coordinator.window_tick.tick_window",
            new_callable=AsyncMock,
        ) as tick_window,
    ):
        router.iter_thread_bindings_with_chat.return_value = [
            (1, -100, index, window_id) for index, window_id in enumerate(window_ids)
        ]
        tick_window.side_effect = tick
        await _tick_bound_windows(AsyncMock(), {}, adapter=MagicMock())

    assert visited == window_ids
    assert mux.agent_status.await_count == 2
