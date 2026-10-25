"""Optional HTTP listener for the shared session protocol service."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from ..session_protocol.service import LocalSessionService
from ..session_protocol.store import ProtocolStore
from .server import start_server

_MAX_PORT = 65535


async def start_if_enabled(
    service: LocalSessionService, store: ProtocolStore
) -> Callable[[], Awaitable[None]] | None:
    """Start the local mobile API when CCGRAM_API_PORT is configured."""
    configured = os.getenv("CCGRAM_API_PORT")
    if not configured:
        return None
    port = int(configured)
    if not 1 <= port <= _MAX_PORT:
        raise ValueError("CCGRAM_API_PORT must be between 1 and 65535")
    hosts = frozenset(
        h.strip().lower()
        for h in os.getenv(
            "CCGRAM_API_HOSTS", f"127.0.0.1:{port},localhost:{port}"
        ).split(",")
        if h.strip()
    )
    origins = frozenset(
        o.strip() for o in os.getenv("CCGRAM_API_ORIGINS", "").split(",") if o.strip()
    )
    runner = await start_server(
        service, store, port=port, allowed_hosts=hosts, allowed_origins=origins
    )
    return runner.cleanup
