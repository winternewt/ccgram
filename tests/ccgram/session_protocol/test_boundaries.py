import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MUTATIONS = {
    "send",
    "send_keys",
    "send_to_pane",
    "send_keys_to_pane",
    "create_window",
    "create_topic_target",
    "kill_window",
    "rename_window",
    "split_window",
    "send_to_window",
    "send_followup_to_window",
}


def backend_imports(tree):
    receivers = set()
    functions = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and "multiplexer" in (
            node.module or ""
        ).split("."):
            for imported in node.names:
                alias = imported.asname or imported.name
                if imported.name in {"multiplexer", "get_active_multiplexer"}:
                    receivers.add(alias)
                if imported.name in MUTATIONS:
                    functions.add(alias)
    return receivers, functions


def input_sites(source):
    tree = ast.parse(source)
    receivers, functions = backend_imports(tree)
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in functions:
            sites.append(node.func.id)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in MUTATIONS:
            receiver = node.func.value
            if isinstance(receiver, ast.Call):
                receiver = receiver.func
            if isinstance(receiver, ast.Name) and receiver.id in receivers:
                sites.append(node.func.attr)
    return sorted(sites)


def test_api_and_session_core_have_no_telegram_or_handler_imports():
    for directory in (ROOT / "src/ccgram/api", ROOT / "src/ccgram/session_protocol"):
        for path in directory.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    segments = (node.module or "").split(".")
                    assert "handlers" not in segments and "telegram" not in segments, (
                        path
                    )
                elif isinstance(node, ast.Import):
                    assert all(
                        "telegram" not in n.name.split(".")
                        and "handlers" not in n.name.split(".")
                        for n in node.names
                    ), path


def test_handlers_route_all_mutations_through_the_session_core():
    actual = {
        str(p.relative_to(ROOT)): sites
        for p in (ROOT / "src/ccgram/handlers").rglob("*.py")
        if (sites := input_sites(p.read_text()))
    }
    assert actual == {}


@pytest.mark.parametrize("method", sorted(MUTATIONS))
def test_input_audit_detects_direct_backend_mutations(method):
    assert input_sites(
        "from ccgram.multiplexer import multiplexer as backend\n"
        f"async def handler():\n    await backend.{method}('target')\n"
    ) == [method]


def test_input_audit_detects_imported_window_send_helper():
    assert input_sites(
        "from ccgram.multiplexer.window_ops import send_to_window as inject\n"
        "async def handler():\n    await inject('target', 'text')\n"
    ) == ["inject"]


def test_input_audit_allows_adapter_and_telegram_delivery():
    assert (
        input_sites(
            "async def handler(adapter, bot):\n"
            "    await adapter.send_keys('target', ['Enter'])\n"
            "    await bot.send_message(chat_id=1, text='done')\n"
        )
        == []
    )


def legacy_parser_sites(source):
    names = {"parse_transcript_entries", "parse_history_entry"}
    return sorted(
        node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        else node.attr
        for node in ast.walk(ast.parse(source))
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        )
        or (isinstance(node, ast.Attribute) and node.attr in names)
    )


def test_runtime_has_one_transcript_normalization_path():
    actual = {
        str(path.relative_to(ROOT)): sites
        for path in (ROOT / "src/ccgram").rglob("*.py")
        if (sites := legacy_parser_sites(path.read_text()))
    }
    assert actual == {}


@pytest.mark.parametrize("method", ["parse_transcript_entries", "parse_history_entry"])
def test_parser_audit_detects_legacy_definition_and_call(method):
    assert legacy_parser_sites(
        f"class Provider:\n    def {method}(self, entries):\n        return entries\n"
        f"provider.{method}([])\n"
    ) == [method, method]
