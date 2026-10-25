"""One task-reentrant input lock per real target, shared by both adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from .models import ProtocolError


@dataclass
class _OwnedLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    owner: asyncio.Task | None = None


_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, _OwnedLock]] = (
    WeakKeyDictionary()
)


@asynccontextmanager
async def input_lock(target: str) -> AsyncIterator[None]:
    locks = _locks.setdefault(asyncio.get_running_loop(), {})
    entry = locks.setdefault(target.casefold(), _OwnedLock())
    task = asyncio.current_task()
    if entry.owner is task and task is not None:
        yield
        return
    try:
        await asyncio.wait_for(entry.lock.acquire(), timeout=10)
    except TimeoutError as exc:
        raise ProtocolError(423, "session_busy", "Session input is busy") from exc
    entry.owner = task
    try:
        yield
    finally:
        entry.owner = None
        entry.lock.release()
