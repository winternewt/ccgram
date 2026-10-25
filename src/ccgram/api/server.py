"""Loopback-only aiohttp edge; all session behavior belongs to the service."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any

from aiohttp import WSMsgType, web

from .. import __version__
from ..session_protocol.actions import parse_action
from ..session_protocol.events import ResyncRequiredError, Subscription
from ..session_protocol.models import PROTOCOL, ProtocolError, wire
from ..session_protocol.service import LocalSessionService
from ..session_protocol.store import ProtocolStore
from ..session_protocol.uploads import MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)
_MAX_BODY = 131072
_MIN_PAIR_CODE = 32
_MAX_PAIR_CODE = 128
_MAX_DEVICE_NAME = 100
_MAX_SUBSCRIPTIONS = 1000
_MIN_TERMINAL_INTERVAL = 500
_MAX_TERMINAL_INTERVAL = 30000
_DEVICE = web.RequestKey("ccgram_device", str)


class _RateLimiter:
    def __init__(self, count: int, interval: float = 60) -> None:
        self.count = count
        self.interval = interval
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        attempts = self._attempts[key]
        while attempts and attempts[0] <= now - self.interval:
            attempts.popleft()
        if len(attempts) >= self.count:
            raise ProtocolError(
                429,
                "rate_limited",
                "Too many requests",
                {
                    "retryAfterMs": max(
                        1, int((attempts[0] + self.interval - now) * 1000)
                    )
                },
            )
        attempts.append(now)


class ProtocolAPI:
    def __init__(self, service: LocalSessionService, store: ProtocolStore) -> None:
        self.service = service
        self.store = store
        self.pair_limit = _RateLimiter(5)
        self.action_limit = _RateLimiter(120)
        self.sockets: set[web.WebSocketResponse] = set()

    async def body(self, request: web.Request) -> dict[str, Any]:
        try:
            if request.content_type != "application/json":
                raise ValueError
            if (
                request.content_length is not None
                and request.content_length > _MAX_BODY
            ):
                raise ValueError
            raw = bytearray()
            async for chunk in request.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > _MAX_BODY:
                    raise ValueError
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError
            return body
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError(
                400, "invalid_request", "Expected a JSON object"
            ) from exc

    def response(self, value: Any, *, status: int = 200) -> web.Response:
        return web.json_response(
            wire(value),
            status=status,
            headers={
                "X-CCGram-Epoch": self.service.hub.epoch,
                "X-CCGram-Seq": str(self.service.hub.seq),
                "Cache-Control": "no-store",
            },
        )

    async def meta(self, _request: web.Request) -> web.Response:
        return self.response(
            {
                "protocol": PROTOCOL,
                "server": __version__,
                "minClient": "1.0.0",
                "capabilities": ["terminal.view", "session.create", "requests.hook"],
            }
        )

    async def sessions(self, _request: web.Request) -> web.Response:
        return self.response(await self.service.list_sessions())

    async def session(self, request: web.Request) -> web.Response:
        return self.response(await self.service.get_session(request.match_info["id"]))

    async def history(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "50"))
        except ValueError as exc:
            raise ProtocolError(
                400, "invalid_request", "limit must be an integer"
            ) from exc
        return self.response(
            await self.service.history(
                request.match_info["id"],
                request.query.get("before"),
                limit,
                conversation=request.query.get("conversation"),
            )
        )

    async def action(self, request: web.Request) -> web.Response:
        self.action_limit.check(request[_DEVICE])
        action = parse_action(await self.body(request))
        result, status = await self.service.execute(request.match_info["id"], action)
        if status != HTTPStatus.OK:
            return self.response({"error": result.error}, status=status)
        return self.response(result)

    async def create(self, request: web.Request) -> web.Response:
        self.action_limit.check(request[_DEVICE])
        data = await self.body(request)
        if any(
            not isinstance(data.get(key), str) or not data[key]
            for key in ("directory", "provider")
        ):
            raise ProtocolError(
                400, "invalid_request", "directory and provider are required"
            )
        if "worktree" in data and type(data["worktree"]) is not bool:
            raise ProtocolError(400, "invalid_request", "worktree must be boolean")
        if "workspaceId" in data and not isinstance(data["workspaceId"], str):
            raise ProtocolError(400, "invalid_request", "workspaceId must be a string")
        if data.get("mode", "normal") != "normal":
            raise ProtocolError(
                422,
                "unsupported_action",
                "Remote clients cannot raise the approval mode",
            )
        try:
            snapshot = await self.service.create_session(data)
        except LookupError as exc:
            raise ProtocolError(400, "invalid_request", "Unknown provider") from exc
        return self.response(snapshot, status=201)

    async def terminal(self, request: web.Request) -> web.Response:
        return self.response(
            await self.service.terminal(
                request.match_info["id"], request.query.get("pane", "p0")
            )
        )

    async def commands(self, request: web.Request) -> web.Response:
        return self.response(await self.service.commands(request.match_info["id"]))

    async def pair(self, request: web.Request) -> web.Response:
        self.pair_limit.check("pair")
        data = await self.body(request)
        code, name = data.get("code"), data.get("name")
        if (
            not isinstance(code, str)
            or not _MIN_PAIR_CODE <= len(code) <= _MAX_PAIR_CODE
            or not isinstance(name, str)
            or not 1 <= len(name) <= _MAX_DEVICE_NAME
        ):
            raise ProtocolError(
                400, "invalid_request", "A pairing code and device name are required"
            )
        return self.response(self.store.pair(code, name), status=201)

    async def devices(self, _request: web.Request) -> web.Response:
        return self.response(self.store.devices())

    async def revoke(self, request: web.Request) -> web.Response:
        self.store.revoke(request.match_info["id"])
        return web.Response(status=204)

    async def upload(self, request: web.Request) -> web.Response:
        self.action_limit.check(request[_DEVICE])
        content = await request.read()
        reference = self.service.uploads.save(
            request.headers.get("X-Filename", "upload"), request.content_type, content
        )
        return self.response(reference, status=201)

    async def download(self, request: web.Request) -> web.StreamResponse:
        path, metadata = self.service.uploads.get(request.match_info["id"])
        response = web.FileResponse(path)
        response.content_type = metadata["mime"]
        response.headers["Content-Disposition"] = "attachment"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    async def unsupported(self, _request: web.Request) -> web.Response:
        raise ProtocolError(
            422, "unsupported_action", "This optional capability is not enabled"
        )

    async def stream(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=_MAX_BODY)
        await ws.prepare(request)
        self.sockets.add(ws)
        subscriber = self.service.hub.subscribe()
        sender: asyncio.Task | None = None
        terminal: asyncio.Task | None = None
        token = request.headers["Authorization"].removeprefix("Bearer ")
        try:
            boundary = await self._handshake(ws)
            sender = asyncio.create_task(
                self._send_events(ws, subscriber, token, boundary)
            )
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    break
                self.store.authenticate(token)
                terminal = await self._client_frame(
                    ws, subscriber, terminal, json.loads(message.data)
                )
        except (ProtocolError, ValueError, TimeoutError, TypeError) as exc:
            reason = exc.code if isinstance(exc, ProtocolError) else "invalid_request"
            await ws.close(code=1008, message=reason.encode())
        finally:
            self.service.hub.unsubscribe(subscriber)
            self.sockets.discard(ws)
            for task in (sender, terminal):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(t for t in (sender, terminal) if t is not None),
                return_exceptions=True,
            )
        return ws

    async def _handshake(self, ws: web.WebSocketResponse) -> int:
        async with asyncio.timeout(10):
            hello = await ws.receive_json()
        if (
            not isinstance(hello, dict)
            or hello.get("type") != "hello"
            or not isinstance(hello.get("data"), dict)
            or hello["data"].get("protocol") != PROTOCOL
        ):
            raise ProtocolError(
                400, "invalid_request", "First frame must be hello for ccgram/1"
            )
        resume = hello["data"].get("resume")
        resumed, replay = False, []
        if resume is not None:
            if (
                not isinstance(resume, dict)
                or not isinstance(resume.get("epoch"), str)
                or type(resume.get("seq")) is not int
            ):
                raise ProtocolError(400, "invalid_request", "Invalid resume cursor")
            resumed, replay = self.service.hub.replay(resume["epoch"], resume["seq"])
        # Subscribe and read replay without an await between them; the queue
        # already holds events published since this socket was prepared.
        boundary = self.service.hub.seq
        await ws.send_json(
            {
                "v": 1,
                "epoch": self.service.hub.epoch,
                "class": "control",
                "type": "welcome",
                "data": {
                    "epoch": self.service.hub.epoch,
                    "seq": boundary,
                    "resumed": resumed,
                },
            }
        )
        for event in replay:
            await ws.send_json(event.body())
        return boundary

    async def _client_frame(
        self,
        ws: web.WebSocketResponse,
        subscriber: Subscription,
        terminal: asyncio.Task | None,
        frame: Any,
    ) -> asyncio.Task | None:
        if not isinstance(frame, dict) or not isinstance(frame.get("data"), dict):
            raise ProtocolError(400, "invalid_request", "Invalid client frame")
        kind, data = frame.get("type"), frame["data"]
        if kind == "subscribe":
            ids = data.get("sessionIds")
            if ids == "*":
                subscriber.session_ids = None
            elif (
                isinstance(ids, list)
                and len(ids) <= _MAX_SUBSCRIPTIONS
                and all(isinstance(sid, str) for sid in ids)
            ):
                subscriber.session_ids = set(ids)
            else:
                raise ProtocolError(
                    400, "invalid_request", "sessionIds must be '*' or a list"
                )
            return terminal
        if kind not in {"terminal.watch", "terminal.unwatch"}:
            raise ProtocolError(400, "invalid_request", "Unknown client frame")
        if terminal is not None:
            terminal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await terminal
        if kind == "terminal.unwatch":
            return None
        return self._watch_terminal(ws, data)

    def _watch_terminal(
        self, ws: web.WebSocketResponse, data: dict[str, Any]
    ) -> asyncio.Task:
        interval = data.get("intervalMs", 1000)
        if (
            type(interval) is not int
            or not _MIN_TERMINAL_INTERVAL <= interval <= _MAX_TERMINAL_INTERVAL
            or not isinstance(data.get("sessionId"), str)
            or not isinstance(data.get("pane", "p0"), str)
        ):
            raise ProtocolError(400, "invalid_request", "Invalid terminal watch")
        return asyncio.create_task(
            self._terminal_frames(
                ws, data["sessionId"], data.get("pane", "p0"), interval
            )
        )

    async def _send_events(
        self,
        ws: web.WebSocketResponse,
        subscriber: Subscription,
        token: str,
        boundary: int,
    ) -> None:
        try:
            while not ws.closed:
                self.store.authenticate(token)
                try:
                    event = await asyncio.wait_for(subscriber.get(), timeout=1)
                except TimeoutError:
                    continue
                if event.seq is None or event.seq > boundary:
                    await ws.send_json(event.body())
        except ResyncRequiredError:
            await ws.close(code=4009, message=b"resync_required")
        except ProtocolError:
            await ws.close(code=4401, message=b"unauthorized")
        except ConnectionError, RuntimeError:
            await ws.close()

    async def _terminal_frames(
        self, ws: web.WebSocketResponse, session_id: str, pane: str, interval: int
    ) -> None:
        previous = None
        try:
            while not ws.closed:
                data = await self.service.terminal(session_id, pane)
                stamp = (data["hash"], data["rows"], data["cols"])
                if stamp != previous:
                    previous = stamp
                    await ws.send_json(
                        {
                            "v": 1,
                            "epoch": self.service.hub.epoch,
                            "class": "ephemeral",
                            "type": "terminal.frame",
                            "sessionId": session_id,
                            "data": data,
                        }
                    )
                await asyncio.sleep(interval / 1000)
        except ProtocolError:
            await ws.close(code=1011, message=b"backend_unavailable")
        except ConnectionError:
            return

    async def shutdown(self, _app: web.Application) -> None:
        await asyncio.gather(
            *(
                ws.close(code=1001, message=b"server_shutdown")
                for ws in list(self.sockets)
            )
        )


def build_app(
    service: LocalSessionService,
    store: ProtocolStore,
    *,
    allowed_hosts: frozenset[str],
    allowed_origins: frozenset[str] = frozenset(),
) -> web.Application:
    """Build an app with exact Host authorities and exact browser Origins.

    Missing Origin is allowed for native clients. No CORS or cookie auth.
    """
    api = ProtocolAPI(service, store)

    @web.middleware
    async def security(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        try:
            if request.headers.get("Host", "").lower() not in allowed_hosts:
                raise ProtocolError(400, "invalid_request", "Host is not allowed")
            origin = request.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                raise ProtocolError(401, "unauthorized", "Origin is not allowed")
            if not (request.method == "POST" and request.path == "/v1/devices/pair"):
                auth = request.headers.get("Authorization", "")
                if not auth.startswith("Bearer ") or not auth.removeprefix("Bearer "):
                    raise ProtocolError(401, "unauthorized", "Bearer token required")
                request[_DEVICE] = store.authenticate(auth.removeprefix("Bearer "))
            return await handler(request)
        except ProtocolError as exc:
            return api.response(exc.body(), status=exc.status)
        except web.HTTPException as exc:
            return api.response(
                {
                    "error": {
                        "code": "not_found"
                        if exc.status == HTTPStatus.NOT_FOUND
                        else "invalid_request",
                        "message": exc.reason,
                        "details": {},
                    }
                },
                status=exc.status,
            )

    app = web.Application(middlewares=[security], client_max_size=MAX_UPLOAD_BYTES + 1)
    app.add_routes(
        [
            web.get("/v1/meta", api.meta),
            web.get("/v1/sessions", api.sessions),
            web.post("/v1/sessions", api.create),
            web.get("/v1/sessions/{id}", api.session),
            web.get("/v1/sessions/{id}/history", api.history),
            web.post("/v1/sessions/{id}/actions", api.action),
            web.get("/v1/sessions/{id}/terminal", api.terminal),
            web.get("/v1/sessions/{id}/commands", api.commands),
            web.post("/v1/uploads", api.upload),
            web.get("/v1/uploads/{id}", api.download),
            web.post("/v1/devices/pair", api.pair),
            web.get("/v1/devices", api.devices),
            web.delete("/v1/devices/{id}", api.revoke),
            web.put("/v1/devices/{id}/push", api.unsupported),
            web.get("/v1/stream", api.stream),
        ]
    )
    app.on_shutdown.append(api.shutdown)
    return app


async def start_server(
    service: LocalSessionService,
    store: ProtocolStore,
    *,
    port: int,
    allowed_hosts: frozenset[str],
    allowed_origins: frozenset[str] = frozenset(),
) -> web.AppRunner:
    """The bind address is intentionally not configurable (PROTO-R07)."""
    app = build_app(
        service, store, allowed_hosts=allowed_hosts, allowed_origins=allowed_origins
    )
    runner = web.AppRunner(app, access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
    except BaseException:
        await runner.cleanup()
        raise
    return runner
