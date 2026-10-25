import json
import stat
from uuid import uuid4

import pytest

from ccgram.session_protocol.models import Action, ActionResult, ProtocolError
from ccgram.session_protocol.store import ProtocolStore


def test_ids_and_binding_versions_survive_restart(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = ProtocolStore(path)
    sid, version = store.bind("TARGET", "claude/conv/pane")
    assert sid.startswith("s_") and len(sid) == 28
    assert version == 1
    assert store.bind("target", "claude/conv/pane") == (sid, 1)
    store.close()
    with_store = ProtocolStore(path)
    assert with_store.bind("target", "codex/conv2/pane") == (sid, 2)
    assert with_store.bind("new-target", "codex/conv2/pane", ("target",)) == (sid, 2)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with_store.close()


def test_ambiguous_aliases_do_not_steal_an_identity(store):
    first = store.bind("a", "binding")[0]
    second = store.bind("b", "binding")[0]
    third = store.bind("c", "binding", ("a", "b"))[0]
    assert third not in {first, second}


def test_pairing_is_atomic_single_use_and_stores_only_hashes(store):
    code = store.make_pairing_code()
    paired = store.pair(code, "iPhone")
    assert store.authenticate(paired["deviceToken"]) == paired["deviceId"]
    with pytest.raises(ProtocolError, match="already used"):
        store.pair(code, "attacker")
    dump = "\n".join(store.db.iterdump())
    assert code not in dump and paired["deviceToken"] not in dump
    assert "deviceToken" not in json.dumps(store.devices())
    store.revoke(paired["deviceId"])
    with pytest.raises(ProtocolError) as error:
        store.authenticate(paired["deviceToken"])
    assert error.value.status == 401


def test_pairing_expires_at_boundary(tmp_path):
    now = [1000.0]
    store = ProtocolStore(tmp_path / "db", clock=lambda: now[0])
    code = store.make_pairing_code()
    now[0] += 120
    with pytest.raises(ProtocolError):
        store.pair(code, "phone")
    store.close()


def test_crash_reservation_is_unknown_after_restart(tmp_path):
    path = tmp_path / "db"
    action = Action(str(uuid4()), 1, "message.send", {"text": "hi"})
    store = ProtocolStore(path)
    fingerprint = store.action_fingerprint("s_test", action)
    store.reserve(action.id, fingerprint)
    store.close()
    restarted = ProtocolStore(path)
    assert restarted.action(action.id, fingerprint) == (
        ActionResult(action.id, "unknown"),
        200,
    )
    with pytest.raises(ProtocolError, match="different action"):
        restarted.action(action.id, "changed body")
    restarted.close()
