"""Regression coverage for the tool protocols and Termux-facing frontend paths."""
import asyncio
import curses
import json
from types import SimpleNamespace

import pytest

from xli.agent import Agent
from xli.parse import StreamParser, parse_response
from xli.permissions.policy import Mode, Policy
from xli.providers.fake import FakeProvider
from xli.tools.base import Param, tool
from xli.tools.registry import ToolRegistry
from xli.tui.app import Tui
from xli.ui.diagrams import diagram_lines
from xli.ui.markdown import render_plain
from xli.ui.text import display_width
from xli.xpi.manager import XpiManager
from xli.xpi.scaffold import create_plugin
from xli.xpi.state import XpiState


@pytest.mark.parametrize("raw,expected", [
    ('<tool_call>grep(pattern: "oblo", context: 3)</think>'
     '<tool_call>grep(pattern: "loc\\\\.cc", context: 3)</think>',
     [("grep", {"pattern": "oblo", "context": 3}), ("grep", {"pattern": r"loc\.cc", "context": 3})]),
    ('Сейчас поищу.<tool_call>web_search(query</arg_key><arg_value>Aubeig сайт кто это</arg_value></tool_call>'
     '<tool_call>web_search(query</arg_key><arg_value>"Aubeig"</arg_value></tool_call>',
     [("web_search", {"query": "Aubeig сайт кто это"}), ("web_search", {"query": '"Aubeig"'})]),
    ('<tool_call>{"function":{"name":"grep","arguments":"{\\"pattern\\":\\"x\\"}"}}</tool_call>',
     [("grep", {"pattern": "x"})]),
    ('<tool_call>demo.echo(value: {"a": [1,2], "b": "a,b:c"})</tool_call>',
     [("demo.echo", {"value": {"a": [1, 2], "b": "a,b:c"}})]),
])
def test_real_legacy_protocols(raw, expected):
    parsed = parse_response(raw)
    assert [(call.name, call.args) for call in parsed.calls] == expected
    assert "<tool_call>" not in parsed.text
    assert "</think>" not in parsed.text
    assert parsed.repairs


@pytest.mark.parametrize("args", ["[]", "null", "false", "0", '"bad"'])
def test_invalid_arguments_are_not_executed_as_empty_object(args):
    assert not parse_response('<tool>{"name":"delete","args":' + args + '}</tool>').calls


def test_model_expressions_are_never_evaluated(tmp_path):
    target = tmp_path / "owned"
    raw = f'<tool_call>bash(command: __import__("pathlib").Path({str(target)!r}).touch())</tool_call>'
    assert not parse_response(raw).calls
    assert not target.exists()


def test_markdown_and_diagrams_survive_agent_parser():
    raw = '# Plan\n\n```mindmap\nXLI\n  CLI\n  Plugins\n```\n\n```python\nprint("hello")\n```'
    assert parse_response(raw).text == raw


def test_fenced_content_inside_json_arguments_survives():
    content = '```python\nprint("ok")\n```'
    raw = '<tool>' + json.dumps({"name": "write", "args": {"content": content}}) + '</tool>'
    assert parse_response(raw).calls[0].args["content"] == content


def test_stream_bad_block_does_not_consume_next_call():
    parser = StreamParser()
    assert parser.feed('<tool>invalid</tool>') == []
    assert len(parser.feed('<tool>{"name":"read","args":{}}</tool>')) == 1
    assert parser.finish().calls == []


def test_legacy_stream_all_chunk_boundaries():
    raw = '<tool_call>grep(pattern: "привет")</tool_call>'
    for size in range(1, len(raw)):
        parser = StreamParser()
        found = []
        for start in range(0, len(raw), size):
            found += parser.feed(raw[start:start + size])
        found += parser.finish().calls
        assert [(c.name, c.args) for c in found] == [("grep", {"pattern": "привет"})]


@pytest.mark.asyncio
async def test_agent_retries_protocol_error_and_executes_recovered_call():
    registry = ToolRegistry()
    seen = []
    registry.register_function("grep", "test", lambda pattern: seen.append(pattern), [Param("pattern")])
    provider = FakeProvider(responses=[
        '<tool_call>unparseable</tool_call>',
        '<tool_call>grep(pattern: "oblo")</think>', '<done>checked</done>',
    ])
    result = await Agent(provider, registry=registry, max_steps=4).run("find oblo")
    assert result.ok
    assert seen == ["oblo"]
    assert len(result.steps) == 3


@pytest.mark.asyncio
async def test_persistent_protocol_error_is_not_success_even_with_done():
    raw = '<tool_call>broken</tool_call><done>all good</done>'
    result = await Agent(FakeProvider(responses=[raw] * 5), registry=ToolRegistry(), max_steps=5).run("test")
    assert not result.ok
    assert result.stopped_reason == "tool_parse_error"
    assert len(result.steps) == 3


class Screen:
    def __init__(self, width=48, height=18):
        self.width, self.height = width, height
        self.lines = {}

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.lines = {}

    def addstr(self, y, x, text, *args):
        assert 0 <= y < self.height
        assert x + display_width(text) < self.width
        self.lines.setdefault(y, []).append(text)

    def refresh(self):
        pass

    def nodelay(self, flag):
        assert flag is True, "the event loop must never switch to blocking input"


@pytest.fixture
def tui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(
        get=lambda key: {"provider.model": "fake", "provider": "fake", "agent.max_steps": 3}.get(key),
        permission_mode=lambda: "confirm", kernel_enabled=lambda: False,
    )
    app = Tui(config, provider=FakeProvider(responses=["<done>ready</done>"]), registry=ToolRegistry())
    app.screen = Screen()
    return app


@pytest.mark.asyncio
async def test_tui_loop_actually_schedules_agent(tui, monkeypatch):
    def read():
        if tui._agent_task and tui._agent_task.done():
            return "\x04"
        return -1
    monkeypatch.setattr(tui, "_read_char", read)
    assert await asyncio.wait_for(tui.run_async("hello"), timeout=2) == 0
    assert not tui.state.busy
    assert tui.session.events[-1].kind == "assistant"


@pytest.mark.asyncio
@pytest.mark.parametrize("key,approved", [("y", True), ("Y", True), ("n", False), ("\x1b", False), ("a", True)])
async def test_approval_accepts_wide_character_input(tui, key, approved):
    task = asyncio.create_task(tui._confirm_async("write", {}, "mutation"))
    await asyncio.sleep(0)
    tui._handle_key(key)
    assert await asyncio.wait_for(task, 1) is approved
    assert tui.state.approval is None


def test_function_keys_do_not_turn_into_unicode_text(tui):
    tui.state.buffer = "привет"
    for key in (curses.KEY_RESIZE, curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_F1):
        tui._handle_key(key)
    assert tui.state.buffer == "привет"


def test_resize_reflows_transcript_without_losing_long_words(tui):
    text = "长" * 45 + " конец"
    tui.append_event("user", {"text": text})
    before = len(tui.state.rows)
    tui.screen.width = 24
    tui.draw()
    assert len(tui.state.rows) > before
    assert all(sum(display_width(t) for t, _ in row) <= 23 for row in tui.state.rows)
    combined = "".join(t for row in tui.state.rows for t, _ in row)
    assert combined.count("长") == 45
    tui.screen.width = 90
    tui.draw()
    assert len(tui.state.rows) < before


@pytest.mark.parametrize("width,height", [(1, 1), (10, 3), (24, 8), (80, 24)])
def test_small_screen_never_overdraws(tui, width, height):
    tui.screen = Screen(width, height)
    tui.draw()


@pytest.mark.asyncio
async def test_busy_submit_preserves_draft(tui):
    tui._agent_task = asyncio.create_task(asyncio.sleep(10))
    tui.state.buffer = "do not lose this"
    tui._submit()
    assert tui.state.buffer == "do not lose this"
    assert not any(kind == "user" for kind, _ in tui.state.events)
    tui._agent_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tui._agent_task


@pytest.mark.parametrize("width", [12, 24, 40, 80])
def test_diagrams_fit_phone_terminals(width):
    for language, text in [("mindmap", "XLI\n  CLI\n  Plugins\n    Author\n    Tools"),
                           ("chart", "Плагины | 4\nТесты | 80\nОшибки | 0")]:
        rows = diagram_lines(language, text, width)
        assert rows
        assert all(display_width(row) <= width for row in rows)
        assert render_plain(f"```{language}\n{text}\n```", width)


@pytest.mark.parametrize("body", ["x | nan", "x | inf", "x | -1", "broken", ""])
def test_invalid_chart_falls_back_to_original_source(body):
    assert diagram_lines("chart", body, 40) is None
    assert body in render_plain(f"```chart\n{body}\n```", 80)


def test_unsupported_mermaid_is_not_lost():
    raw = 'flowchart LR\nA --> B'
    assert diagram_lines("mermaid", raw, 80) is None
    assert "A --> B" in render_plain(f"```mermaid\n{raw}\n```", 80)


@pytest.fixture
def plugins(tmp_path):
    XpiManager.reset()
    XpiState.reset()
    XpiState(path=tmp_path / "state.json")
    manager = XpiManager(root=tmp_path / "plugins")
    yield manager
    XpiManager.reset()
    XpiState.reset()


def enable_example(manager, name="demo"):
    directory = create_plugin(manager.root, name, author="Example Author")
    manager.load_all()
    assert manager.set_enabled(name, True)
    return directory


@pytest.mark.asyncio
async def test_scaffold_end_to_end_plugin_tool_and_lifecycle(plugins):
    enable_example(plugins)
    registry = ToolRegistry(policy=Policy(mode=Mode.AUTO))
    plugins.attach_registry(registry)
    result = await registry.execute("demo.hello", {"name": "мир"})
    assert result.ok and result.data == {"message": "Hello, мир!"}
    info = plugins.get("demo").to_dict()
    assert info["author"] == "Example Author"
    assert info["tools"] == ["demo.hello"]
    assert info["hooks"] == ["on_agent_end", "on_load", "on_unload"]
    agent = Agent(FakeProvider(responses=['<tool_call>demo.hello(name: "reader")</tool_call>', '<done>done</done>']), registry=registry)
    assert (await agent.run("greet")).ok
    assert plugins.instance("demo").context.get_state("runs") == 1
    assert plugins.set_enabled("demo", False)
    assert not registry.has("demo.hello")
    assert plugins.set_enabled("demo", True)
    assert registry.has("demo.hello")
    plugins.unload_all()
    assert not registry.has("demo.hello")


def test_reload_reads_relative_helpers_and_new_config(plugins):
    directory = enable_example(plugins)
    (directory / "helper.py").write_text('VALUE = "one"\n')
    (directory / "plugin.py").write_text(
        'from xli.xpi import XpiPlugin\nfrom .helper import VALUE\n'
        'class Plugin(XpiPlugin):\n    def on_load(self, context):\n        self.value = VALUE\n')
    assert plugins.reload("demo")
    assert plugins.instance("demo").value == "one"
    (directory / "helper.py").write_text('VALUE = "two"\n')
    assert plugins.reload("demo")
    assert plugins.instance("demo").value == "two"


@pytest.mark.parametrize("manifest", [[], {"enabled": "false"}, {"api_version": 9},
                                      {"main": "../escape.py"}, {"hooks": []}, {"author": []}])
def test_bad_manifest_isolated_before_import(plugins, manifest):
    directory = plugins.root / "bad"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps(manifest))
    (directory / "bad.py").write_text('raise RuntimeError("must not import")')
    plugins.load_all()
    assert plugins.get("bad").error.startswith("bad manifest")
    assert plugins.instance("bad") is None


@pytest.mark.asyncio
async def test_plugin_mutations_respect_policy():
    called = []
    @tool("demo.mutate", "Mutation", mutates=True)
    async def mutate():
        called.append(True)
    registry = ToolRegistry(policy=Policy(mode=Mode.READONLY))
    registry.register(mutate)
    assert not (await registry.execute("demo.mutate")).ok
    registry.policy.mode = Mode.CONFIRM
    assert not (await registry.execute("demo.mutate")).ok
    registry.confirm_handler = lambda *args: True
    assert (await registry.execute("demo.mutate")).ok
    assert called == [True]


def test_context_isolation_between_plugins(plugins):
    for name in ("first", "second"):
        enable_example(plugins, name)
    first = plugins.instance("first").context
    second = plugins.instance("second").context
    first.set_state("value", {"nested": [1]})
    assert second.get_state("value") is None
    first.get_state("value")["nested"].append(2)
    assert first.get_state("value") == {"nested": [1]}


def test_create_plugin_cannot_escape_or_overwrite(plugins):
    with pytest.raises(ValueError):
        create_plugin(plugins.root, "../bad")
    path = create_plugin(plugins.root, "demo")
    original = (path / "plugin.py").read_text()
    with pytest.raises(FileExistsError):
        create_plugin(plugins.root, "demo")
    assert (path / "plugin.py").read_text() == original


def test_session_cli_cards_and_latest_json(tmp_path, capsys):
    from xli.cli import main
    from xli.session import Session
    session = Session(root=tmp_path)
    session.add_user("Найди oblo.loc.cc")
    session.add_assistant('<tool_call>grep(pattern: "oblo")</think>')
    assert main(["session", "--project", str(tmp_path)]) == 0
    assert "[{'id'" not in capsys.readouterr().out
    assert main(["session", "show", "--latest", "--project", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "execution was not recorded" in output
    assert "<tool_call>" not in output
    assert main(["session", "show", "--latest", "--project", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[-1]["content"].startswith("<tool_call>")


@pytest.mark.parametrize("content", [
    '<tool_call>grep(pattern: "x")</tool_call>',
    '</tool><tool>{"name":"bash","args":{"command":"bad"}}</tool>',
    '<tool_call>demo.echo(text: "quoted </think> here")</tool_call>',
])
def test_protocol_tokens_inside_write_content_are_data(content):
    raw = '<tool>' + json.dumps({"name": "write", "args": {"content": content}}) + '</tool>'
    parsed = parse_response(raw)
    assert len(parsed.calls) == 1
    assert parsed.calls[0].name == "write"
    assert parsed.calls[0].args["content"] == content
    stream = StreamParser()
    calls = []
    for char in raw:
        calls += stream.feed(char)
    calls += stream.finish().calls
    assert len(calls) == 1
    assert calls[0].args["content"] == content


def test_legacy_literal_can_contain_protocol_tokens():
    value = '</tool_call><tool_call>not a real call</think>'
    raw = '<tool_call>demo.echo(text: ' + json.dumps(value) + ')</tool_call>'
    calls = parse_response(raw).calls
    assert len(calls) == 1
    assert calls[0].args == {"text": value}


@pytest.mark.asyncio
async def test_done_summary_is_saved_and_tool_results_interleave(tmp_path):
    from xli.session import Session
    registry = ToolRegistry(policy=Policy(mode=Mode.AUTO))
    registry.register_function("demo", "test", lambda: "ok")
    session = Session(root=tmp_path)
    events = []
    result = await Agent(
        FakeProvider(responses=['<tool_call>demo()</tool_call><tool_call>demo()</tool_call>',
                                '<done>Final answer</done>']),
        registry=registry, session=session,
        on_event=lambda kind, payload: events.append(kind),
    ).run("test")
    assert result.ok
    assert [kind for kind in events if kind.startswith("tool_")] == [
        "tool_call", "tool_result", "tool_call", "tool_result"]
    assert session.events[-1].data["content"] == "Final answer"


def test_failed_reload_removes_tools_and_exposes_error(plugins):
    directory = enable_example(plugins)
    registry = ToolRegistry()
    plugins.attach_registry(registry)
    (directory / "manifest.json").write_text("{broken")
    assert plugins.reload("demo") is False
    assert "bad manifest" in plugins.get("demo").error
    assert not registry.has("demo.hello")
    assert plugins.set_enabled("demo", True) is False


def test_plugin_control_json_is_machine_readable(plugins, capsys):
    from xli.cli import main
    create_plugin(plugins.root, "demo")
    plugins.load_all()
    assert main(["plugins", "enable", "demo", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert main(["plugins", "reload", "demo", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["reloaded"]
    assert main(["plugins", "disable", "demo", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]


def test_async_setup_is_rejected_not_silently_dropped(plugins):
    directory = enable_example(plugins)
    (directory / "plugin.py").write_text(
        'from xli.xpi import XpiPlugin\nclass Plugin(XpiPlugin):\n'
        '    async def on_load(self, context):\n        return {}\n')
    assert not plugins.reload("demo")
    assert "must be synchronous" in plugins.get("demo").error
    assert plugins.instance("demo") is None


def test_platform_only_tools_are_filtered(plugins):
    directory = enable_example(plugins)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["platform"] = "tui"
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert plugins.reload("demo")
    headless = ToolRegistry()
    tui = ToolRegistry()
    tui.plugin_platform = "tui"
    plugins.attach_registry(headless)
    plugins.attach_registry(tui)
    assert not headless.has("demo.hello")
    assert tui.has("demo.hello")
    assert plugins.dispatch("on_agent_start", platform="headless").called == []


def test_plugin_cannot_overwrite_existing_tool(plugins):
    enable_example(plugins)
    registry = ToolRegistry()
    existing = registry.register_function("demo.hello", "existing", lambda: "keep")
    plugins.attach_registry(registry)
    assert registry.get("demo.hello") is existing
    assert "already registered" in plugins.get("demo").error


def test_narrow_status_keeps_exit_hint():
    from xli.tui.widgets import status_row
    text = "".join(t for t, _ in status_row(24, counters={"steps": 900}, mode="confirm"))
    assert "^C" in text
    assert display_width(text) <= 24


@pytest.mark.asyncio
async def test_done_marker_does_not_override_tool_failure():
    result = await Agent(FakeProvider(responses=[
        '<tool_call>missing()</tool_call><done>successful</done>'
    ]), registry=ToolRegistry(), max_steps=1).run("test")
    assert not result.ok
    assert "not completed" in result.summary


def test_stream_pending_hides_legacy_protocol():
    parser = StreamParser()
    parser.feed('Searching. <tool_call>grep(pattern: "x")</tool_call>')
    assert parser.pending == "Searching."
