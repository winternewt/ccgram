#!/usr/bin/env python3
"""Print the Telegram user and group IDs that ccgram's .env asks for.

Usage: uv run python scripts/discover_telegram_ids.py [--seconds N]

The Bot API cannot list a group's members (only its admins and a count), and
it keeps pending updates for 24 hours only, so the IDs are collected from live
traffic: start this, then have each person post anything in the group. Every
group the bot hears from is printed with its member count and admins, and every
person who posts, joins or presses a button is printed once.

People who posted before the script ran can still be found: forward one of
their messages to the bot (in a private chat or the group). The forward carries
the original sender's ID unless their "Forwarded Messages" privacy setting hides
it; then only their name arrives, and the script says so.

Reads TELEGRAM_BOT_TOKEN the way the ccgram CLIs do (cwd .env, then
$CCGRAM_DIR/.env). It long-polls getUpdates, so stop the bot first: Telegram
allows one poller per token and answers a second with 409 Conflict. The
updates it reads are confirmed, so the bot will not see them later.

HTTP goes through httpx, which honors HTTPS_PROXY / NO_PROXY from the
environment or the .env files.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ccgram.utils import load_ccgram_env

API_URL = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT_S = 50
HTTP_CONFLICT = 409
UPDATE_KINDS = (
    "message",
    "edited_message",
    "my_chat_member",
    "chat_member",
    "callback_query",
)
GROUP_TYPES = ("group", "supergroup")


@dataclass
class Person:
    user_id: int
    username: str | None
    name: str
    role: str
    chat_ids: set[int] = field(default_factory=set)

    def line(self) -> str:
        handle = f"@{self.username}" if self.username else "-"
        chats = ",".join(str(c) for c in sorted(self.chat_ids))
        return f"USER  {self.user_id}\t{handle}\t{self.name}\t{self.role}\tchat {chats}"


@dataclass
class Seen:
    groups: dict[int, str] = field(default_factory=dict)
    people: dict[int, Person] = field(default_factory=dict)
    hidden: set[str] = field(default_factory=set)

    def add_person(
        self, user: dict[str, Any] | None, chat_id: int | None, role: str = "member"
    ) -> Person | None:
        """Record a human user; return it only the first time it is seen."""
        if not user or user.get("is_bot"):
            return None
        known = self.people.get(user["id"])
        if known is not None:
            if chat_id is not None:
                known.chat_ids.add(chat_id)
            return None
        name = " ".join(filter(None, (user.get("first_name"), user.get("last_name"))))
        person = Person(user["id"], user.get("username"), name, role)
        if chat_id is not None:
            person.chat_ids.add(chat_id)
        self.people[person.user_id] = person
        return person


def _chat_of(obj: dict[str, Any]) -> dict[str, Any]:
    # callback_query carries its chat on the message the button belongs to.
    return obj.get("chat") or (obj.get("message") or {}).get("chat") or {}


def users_in_update(
    update: dict[str, Any],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Each (chat, users) an update names: the sender, joiners, a member change."""
    found: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for kind in UPDATE_KINDS:
        obj = update.get(kind)
        if not obj:
            continue
        candidates = [
            obj.get("from"),
            *obj.get("new_chat_members", []),
            (obj.get("new_chat_member") or {}).get("user"),
        ]
        found.append((_chat_of(obj), [user for user in candidates if user]))
    return found


def forward_in_update(
    update: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """(original sender, None) for a linked forward, (None, their name) for a
    forward whose sender hid the link, (None, None) for anything else."""
    for kind in ("message", "edited_message"):
        origin = (update.get(kind) or {}).get("forward_origin")
        if not origin:
            continue
        if origin.get("type") == "user":
            return origin["sender_user"], None
        if origin.get("type") == "hidden_user":
            return None, origin.get("sender_user_name") or "?"
    return None, None


class BotApi:
    def __init__(self, token: str) -> None:
        self._token = token
        self._client = httpx.Client(timeout=POLL_TIMEOUT_S + 20)

    def call(self, method: str, **params: Any) -> Any:
        resp = self._client.post(
            API_URL.format(token=self._token, method=method), json=params
        )
        body = resp.json()
        if resp.status_code == HTTP_CONFLICT:
            sys.exit(
                "getUpdates conflict: another poller (a running ccgram?) holds this token. Stop it first."
            )
        if not body.get("ok"):
            print(f"{method}: {body.get('description')}", file=sys.stderr)
            return None
        return body["result"]


def announce_group(api: BotApi, seen: Seen, chat: dict[str, Any]) -> None:
    chat_id = chat["id"]
    seen.groups[chat_id] = chat.get("title") or ""
    count = api.call("getChatMemberCount", chat_id=chat_id)
    print(f"GROUP {chat_id}\t{seen.groups[chat_id]}\tmembers={count}", flush=True)
    for admin in api.call("getChatAdministrators", chat_id=chat_id) or []:
        person = seen.add_person(admin["user"], chat_id, admin["status"])
        if person:
            print(person.line(), flush=True)


def listen(api: BotApi, seconds: float) -> Seen:
    seen = Seen()
    offset: int | None = None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        wait = max(1, min(POLL_TIMEOUT_S, int(deadline - time.monotonic())))
        updates = (
            api.call(
                "getUpdates",
                timeout=wait,
                offset=offset,
                allowed_updates=list(UPDATE_KINDS),
            )
            or []
        )
        for update in updates:
            offset = update["update_id"] + 1
            for chat, users in users_in_update(update):
                if chat.get("type") in GROUP_TYPES and chat["id"] not in seen.groups:
                    announce_group(api, seen, chat)
                for user in users:
                    person = seen.add_person(user, chat.get("id"))
                    if person:
                        print(person.line(), flush=True)
            report_forward(seen, update)
    return seen


def report_forward(seen: Seen, update: dict[str, Any]) -> None:
    user, hidden_name = forward_in_update(update)
    person = seen.add_person(user, None, "forwarded")
    if person:
        print(person.line(), flush=True)
    if hidden_name and hidden_name not in seen.hidden:
        seen.hidden.add(hidden_name)
        print(
            f"HIDDEN {hidden_name}: their privacy setting withholds the ID from"
            " forwards; have them post in the group, or ask @userinfobot",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--seconds", type=float, default=300, help="how long to listen (default 300)"
    )
    args = parser.parse_args()

    load_ccgram_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        sys.exit(
            "TELEGRAM_BOT_TOKEN is not set (looked in the environment, ./.env and $CCGRAM_DIR/.env)"
        )
    api = BotApi(token)
    me = api.call("getMe")
    if me is None:
        sys.exit("getMe failed: check the token")
    webhook = (api.call("getWebhookInfo") or {}).get("url")
    if webhook:
        sys.exit(
            f"bot @{me['username']} has a webhook set; getUpdates cannot run while it is"
        )
    print(
        f"listening {args.seconds:.0f}s as @{me['username']}: ask everyone to post in the group,"
        " or forward their messages to the bot",
        flush=True,
    )

    seen = listen(api, args.seconds)
    print(
        f"\n{len(seen.people)} people, {len(seen.groups)} group(s). For ~/.ccgram/.env:"
    )
    print("ALLOWED_USERS=" + ",".join(str(uid) for uid in sorted(seen.people)))
    for chat_id in sorted(seen.groups):
        print(f"CCGRAM_GROUP_ID={chat_id}  # {seen.groups[chat_id]}")


if __name__ == "__main__":
    main()
