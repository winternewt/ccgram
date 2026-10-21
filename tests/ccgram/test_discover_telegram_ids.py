from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "discover_telegram_ids.py"
)

GROUP = {"id": -1001000000001, "title": "team", "type": "supergroup"}
PRIVATE = {"id": 501, "type": "private", "first_name": "Ann"}
ANN = {
    "id": 501,
    "is_bot": False,
    "first_name": "Ann",
    "last_name": "Lee",
    "username": "ann",
}
BOB = {"id": 502, "is_bot": False, "first_name": "Bob"}
BOT = {"id": 900, "is_bot": True, "first_name": "the bot", "username": "the_bot"}


def _load_module():
    spec = importlib.util.spec_from_file_location("discover_telegram_ids", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["discover_telegram_ids"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _ids(found) -> list[tuple[int, list[int]]]:
    return [(chat["id"], [u["id"] for u in users]) for chat, users in found]


def test_message_names_its_sender_and_chat(mod):
    update = {
        "update_id": 1,
        "message": {"message_id": 7, "chat": GROUP, "from": ANN, "text": "hi"},
    }
    assert _ids(mod.users_in_update(update)) == [(GROUP["id"], [ANN["id"]])]


def test_join_message_names_sender_and_every_joiner(mod):
    update = {
        "update_id": 2,
        "message": {
            "message_id": 8,
            "chat": GROUP,
            "from": ANN,
            "new_chat_members": [BOB, BOT],
        },
    }
    assert _ids(mod.users_in_update(update)) == [
        (GROUP["id"], [ANN["id"], BOB["id"], BOT["id"]])
    ]


def test_member_change_names_actor_and_subject(mod):
    update = {
        "update_id": 3,
        "chat_member": {
            "chat": GROUP,
            "from": ANN,
            "date": 0,
            "old_chat_member": {"status": "left", "user": BOB},
            "new_chat_member": {"status": "member", "user": BOB},
        },
    }
    assert _ids(mod.users_in_update(update)) == [(GROUP["id"], [ANN["id"], BOB["id"]])]


def test_callback_query_takes_its_chat_from_the_button_message(mod):
    update = {
        "update_id": 4,
        "callback_query": {
            "id": "q",
            "from": BOB,
            "message": {"message_id": 9, "chat": GROUP},
            "data": "x",
        },
    }
    assert _ids(mod.users_in_update(update)) == [(GROUP["id"], [BOB["id"]])]


def test_update_without_known_kind_names_nobody(mod):
    assert mod.users_in_update({"update_id": 5, "poll": {"id": "p"}}) == []


def test_seen_skips_bots_and_reports_each_person_once(mod):
    seen = mod.Seen()
    first = seen.add_person(ANN, GROUP["id"], "creator")
    again = seen.add_person(ANN, PRIVATE["id"])
    bot = seen.add_person(BOT, GROUP["id"])

    assert first is not None and again is None and bot is None
    assert set(seen.people) == {ANN["id"]}
    person = seen.people[ANN["id"]]
    # A repeat sighting still records the extra chat, and keeps the first role.
    assert person.chat_ids == {GROUP["id"], PRIVATE["id"]}
    assert (person.name, person.role) == ("Ann Lee", "creator")
    # Chats print in numeric order, so a group (negative id) comes first.
    assert (
        person.line()
        == f"USER  501\t@ann\tAnn Lee\tcreator\tchat {GROUP['id']},{PRIVATE['id']}"
    )


def test_line_marks_a_missing_username(mod):
    seen = mod.Seen()
    person = seen.add_person(BOB, None)
    assert person is not None
    assert person.chat_ids == set()
    assert person.line() == "USER  502\t-\tBob\tmember\tchat "
