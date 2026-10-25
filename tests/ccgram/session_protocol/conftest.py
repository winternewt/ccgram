from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ccgram.session_protocol.backend import Prompt, Target
from ccgram.session_protocol.models import ProtocolError, Status
from ccgram.session_protocol.service import LocalSessionService
from ccgram.session_protocol.store import ProtocolStore


class FakeBackend:
    def __init__(self, transcript: Path | None = None) -> None:
        self.target = Target(
            "@1",
            "project",
            "/workspace",
            "claude",
            "conv",
            transcript,
            "%1",
            "claude",
            24,
            80,
            123,
            "normal",
            Status("idle", "", "native", "2026-10-20T10:00:00Z"),
            host="test-host",
            foreground_argv=("claude",),
        )
        self.current_prompt: Prompt | None = None
        self.sent: list[tuple[str, str, bool]] = []
        self.fail = False
        self.disappear = False
        self.clear_on_input = False
        self.unavailable = False

    async def targets(self) -> list[Target]:
        if self.unavailable:
            raise ProtocolError(503, "backend_unavailable", "offline")
        return [] if self.disappear else [self.target]

    async def inspect(self, target_id: str) -> Target:
        if self.disappear:
            raise ProtocolError(404, "not_found", "gone")
        return self.target

    async def prompt(self, target: Target) -> Prompt | None:
        return self.current_prompt

    async def terminal(self, target: Target, pane: str) -> dict[str, Any]:
        if pane != "p0":
            raise ProtocolError(404, "not_found", "pane")
        return {"pane": pane, "rows": 24, "cols": 80, "ansi": "hello", "hash": "h1"}

    async def send_text(
        self, target: Target, text: str, *, enter: bool, raw: bool | None = None
    ) -> bool:
        self.sent.append(("text", text, enter))
        if self.clear_on_input:
            self.current_prompt = None
        if self.fail:
            raise OSError("lost acknowledgement")
        return True

    async def send_key(self, target: Target, key: str) -> bool:
        self.sent.append(("key", key, False))
        if self.clear_on_input:
            self.current_prompt = None
        return not self.fail

    async def close(self, target: Target) -> bool:
        self.disappear = True
        return True

    async def rename(self, target: Target, title: str) -> bool:
        self.target = replace(self.target, title=title)
        return True

    async def commands(self, target: Target) -> list[dict[str, str]]:
        return [{"name": "/help", "description": "Help", "source": "builtin"}]

    async def resume(self, target: Target, conversation_id: str) -> bool:
        self.sent.append(("resume", conversation_id, True))
        return True

    async def create(self, data: dict[str, Any]) -> Target:
        return self.target


@pytest.fixture
def store(tmp_path):
    instance = ProtocolStore(tmp_path / "protocol.sqlite3")
    yield instance
    instance.close()


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def service(backend, store):
    return LocalSessionService(backend, store)
