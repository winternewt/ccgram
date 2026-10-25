"""Small ccgram/1 conformance client; device tokens come only from the environment."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--url", required=True, help="Server origin, without /v1")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("meta")
    commands.add_parser("sessions")
    history = commands.add_parser("history")
    history.add_argument("session")
    history.add_argument("--before")
    action = commands.add_parser("action")
    action.add_argument("session")
    action.add_argument(
        "body", type=Path, help="Saved action JSON; reuse it for retries"
    )
    stream = commands.add_parser("stream")
    stream.add_argument("--epoch")
    stream.add_argument("--seq", type=int)
    stream.add_argument("--count", type=int, default=1)
    return result


async def run(args: argparse.Namespace, token: str) -> int:
    base = args.url.rstrip("/") + "/v1"
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(
        headers=headers, timeout=aiohttp.ClientTimeout(total=30)
    ) as client:
        if args.command == "stream":
            async with client.ws_connect(base + "/stream") as ws:
                data = {"protocol": "ccgram/1"}
                if args.epoch is not None:
                    data["resume"] = {"epoch": args.epoch, "seq": args.seq}
                await ws.send_json({"type": "hello", "data": data})
                await ws.send_json({"type": "subscribe", "data": {"sessionIds": "*"}})
                for _ in range(args.count):
                    print(json.dumps(await ws.receive_json(), ensure_ascii=False))
            return 0
        path = "/" + args.command
        params = {}
        body = None
        if args.command in {"action", "history"}:
            path = f"/sessions/{quote(args.session, safe='')}/" + (
                "actions" if args.command == "action" else "history"
            )
        if args.command == "history" and args.before:
            params["before"] = args.before
        if args.command == "action":
            body = json.loads(args.body.read_text(encoding="utf-8"))
        async with client.request(
            "POST" if body is not None else "GET", base + path, json=body, params=params
        ) as response:
            print(json.dumps(await response.json(), ensure_ascii=False, indent=2))
            return int(response.status >= HTTPStatus.BAD_REQUEST)


def main() -> int:
    cli = parser()
    args = cli.parse_args()
    parsed = urlsplit(args.url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or (
            parsed.scheme == "http"
            and parsed.hostname not in {"127.0.0.1", "localhost"}
        )
    ):
        cli.error("use HTTPS, or HTTP on localhost for a local conformance run")
    if (
        parsed.username
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        cli.error("url must be an origin without credentials or a path")
    if args.command == "stream" and (
        (args.epoch is None) != (args.seq is None) or args.count < 1
    ):
        cli.error("resume needs both --epoch and --seq; --count must be positive")
    token = os.environ.get("CCGRAM_DEVICE_TOKEN")
    if not token:
        cli.error("set CCGRAM_DEVICE_TOKEN to a paired device token")
    return asyncio.run(run(args, token))


if __name__ == "__main__":
    raise SystemExit(main())
