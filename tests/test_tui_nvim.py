#!/usr/bin/env python3
"""Tests for the TUI layout engine and the Neovim plugin installer."""

from pathlib import Path


from xli.tui.widgets import (
    DIM,
    approval_rows,
    header_rows,
    help_rows,
    input_row,
    json_dumps,
    make_layout,
    scroll_limit,
    status_row,
    transcript_row,
    visible_window,
)
from xli.tui.widgets import _fit, _row_width, _wrap


# --------------------------------------------------------------- layout rules
class TestLayout:
    def test_regions_do_not_overlap(self):
        layout = make_layout(100, 40)
        assert layout.body_top == 2
        assert layout.body_bottom == layout.input_top
        assert layout.status_top == layout.input_top + 1
        assert layout.body_height == 40 - 2 - 1 - 1

    def test_minimum_height_still_has_a_body(self):
        layout = make_layout(80, 4)
        assert layout.body_height >= 1

    def test_usability_thresholds(self):
        assert make_layout(80, 24).is_usable()
        assert not make_layout(10, 24).is_usable()
        assert not make_layout(80, 5).is_usable()


class TestFitting:
    def test_pad_short_row(self):
        row = _fit([("hi", DIM)], 10)
        assert _row_width(row) == 10

    def test_truncate_long_row(self):
        row = _fit([("x" * 50, DIM)], 10)
        assert _row_width(row) == 10

    def test_truncation_shows_ellipsis(self):
        row = _fit([("abcdefghij", DIM)], 6)
        assert row[0][0].endswith("…")

    def test_zero_width_does_not_crash(self):
        assert _row_width(_fit([("abc", DIM)], 0)) == 0

    def test_exact_width_unchanged(self):
        row = _fit([("12345", DIM)], 5)
        assert row == [("12345", DIM)]


class TestWrapping:
    def test_long_line_wraps(self):
        rows = _wrap([("word " * 30, DIM)], 20)
        assert len(rows) > 1
        assert all(_row_width(r) == 20 for r in rows)

    def test_explicit_newline_splits(self):
        rows = _wrap([("one\ntwo", DIM)], 40)
        assert len(rows) == 2
        assert rows[0][0][0].startswith("one")
        assert rows[1][0][0].startswith("two")

    def test_wrapping_is_lossless(self):
        text = "the quick brown fox jumps over the lazy dog"
        rows = _wrap([(text, DIM)], 15)
        rebuilt = "".join(t for r in rows for t, _ in r).replace("\n", "")
        assert rebuilt.replace(" ", "") == text.replace(" ", "")

    def test_zero_width_returns_single_row(self):
        assert _wrap([("abc", DIM)], 0) == [[]]

    def test_empty_text(self):
        rows = _wrap([("", DIM)], 20)
        assert len(rows) == 1


# --------------------------------------------------------------------- header
class TestHeader:
    def test_two_rows_of_exact_width(self):
        rows = header_rows(60, model="gpt-4o", provider="openai", mode="confirm")
        assert len(rows) == 2
        assert all(_row_width(r) == 60 for r in rows)

    def test_contains_model_and_mode(self):
        rows = header_rows(80, model="mistral-large", provider="mistral", mode="auto")
        flat = "".join(t for r in rows for t, _ in r)
        assert "mistral" in flat and "auto" in flat

    def test_session_shown_when_present(self):
        rows = header_rows(80, model="m", provider="p", mode="auto", session="abc123")
        flat = "".join(t for r in rows for t, _ in r)
        assert "abc123" in flat

    def test_kernel_badge(self):
        rows = header_rows(80, model="m", provider="p", mode="auto", kernel="cython")
        flat = "".join(t for r in rows for t, _ in r)
        assert "kernel:cython" in flat

    def test_narrow_terminal_truncates_without_overflow(self):
        rows = header_rows(24, model="a-very-long-model-name", provider="openai", mode="confirm")
        assert all(_row_width(r) == 24 for r in rows)


# ----------------------------------------------------------------- transcript
class TestTranscriptRows:
    def test_user_event(self):
        rows = transcript_row("user", {"text": "fix it"}, 60)
        flat = "".join(t for r in rows for t, _ in r)
        assert "you" in flat and "fix it" in flat

    def test_assistant_event(self):
        rows = transcript_row("assistant", {"text": "on it"}, 60)
        assert "xli" in "".join(t for r in rows for t, _ in r)

    def test_empty_assistant_text_yields_nothing(self):
        assert transcript_row("assistant", {"text": "   "}, 60) == []

    def test_tool_call_shows_name_and_args(self):
        rows = transcript_row("tool_call", {"name": "read", "args": {"path": "a.py"}}, 60)
        flat = "".join(t for r in rows for t, _ in r)
        assert "read" in flat and "a.py" in flat

    def test_tool_result_ok_and_fail(self):
        ok = "".join(t for r in transcript_row("tool_result", {"ok": True, "summary": "fine"}, 60) for t, _ in r)
        bad = "".join(t for r in transcript_row("tool_result", {"ok": False, "summary": "boom"}, 60) for t, _ in r)
        assert "ok" in ok and "FAIL" in bad

    def test_long_args_are_truncated(self):
        rows = transcript_row("tool_call", {"name": "write", "args": {"content": "x" * 500}}, 60)
        flat = "".join(t for r in rows for t, _ in r)
        assert "…" in flat

    def test_unknown_event_yields_nothing(self):
        assert transcript_row("mystery", {}, 60) == []

    def test_step_event(self):
        rows = transcript_row("step", {"index": 2, "max_steps": 10}, 60)
        assert "2/10" in "".join(t for r in rows for t, _ in r)

    def test_multiline_assistant_wraps(self):
        rows = transcript_row("assistant", {"text": "line one\nline two"}, 60)
        assert len(rows) >= 2

    def test_every_row_is_exact_width(self):
        for kind, payload in [
            ("user", {"text": "hello there " * 20}),
            ("assistant", {"text": "response " * 20}),
            ("tool_call", {"name": "grep", "args": {"pattern": "x"}}),
            ("repair", {"detail": "closed a fence"}),
            ("error", {"message": "provider died"}),
        ]:
            for row in transcript_row(kind, payload, 50):
                assert _row_width(row) == 50, f"{kind} row overflows"


# ------------------------------------------------------------------ statusbar
class TestStatusBar:
    def test_exact_width(self):
        assert _row_width(status_row(70)) == 70

    def test_busy_state(self):
        flat = "".join(t for t, _ in status_row(70, busy=True))
        assert "working" in flat

    def test_counters_rendered(self):
        flat = "".join(t for t, _ in status_row(90, counters={"steps": 3, "tools": 5, "errors": 1}))
        assert "steps 3" in flat and "tools 5" in flat and "errors 1" in flat

    def test_custom_hint(self):
        flat = "".join(t for t, _ in status_row(90, hint="press y"))
        assert "press y" in flat

    def test_narrow_bar_still_fits(self):
        assert _row_width(status_row(30)) == 30


class TestInputRow:
    def test_exact_width(self):
        assert _row_width(input_row(40, " ❯ ", "hello")) == 40

    def test_long_input_scrolls_left(self):
        row = input_row(20, "> ", "z" * 100)
        flat = "".join(t for t, _ in row)
        assert flat.startswith("> ")
        assert "…" in flat

    def test_empty_input(self):
        assert _row_width(input_row(30, "> ", "")) == 30


# -------------------------------------------------------------------- scroll
class TestScrolling:
    def test_pinned_to_bottom_by_default(self):
        rows = [[(str(i), DIM)] for i in range(20)]
        window, hidden = visible_window(rows, 5, 0)
        assert [r[0][0] for r in window] == ["15", "16", "17", "18", "19"]
        assert hidden == 0

    def test_scrolling_back_reveals_older_rows(self):
        rows = [[(str(i), DIM)] for i in range(20)]
        window, _ = visible_window(rows, 5, 5)
        assert [r[0][0] for r in window] == ["10", "11", "12", "13", "14"]

    def test_scroll_clamped_at_top(self):
        rows = [[(str(i), DIM)] for i in range(20)]
        window, _ = visible_window(rows, 5, 9999)
        assert window[0][0][0] == "0"

    def test_empty_rows(self):
        assert visible_window([], 10, 0) == ([], 0)

    def test_fewer_rows_than_height(self):
        rows = [[("a", DIM)], [("b", DIM)]]
        window, hidden = visible_window(rows, 10, 0)
        assert len(window) == 2 and hidden == 0

    def test_scroll_limit(self):
        assert scroll_limit(20, 5) == 15
        assert scroll_limit(3, 10) == 0


# ------------------------------------------------------------------- approval
class TestApprovalPane:
    def test_shows_tool_and_reason(self):
        rows = approval_rows("write", {"path": "a.py"}, "mutation", 60)
        flat = "\n".join("".join(t for t, _ in r) for r in rows)
        assert "write" in flat and "mutation" in flat and "a.py" in flat

    def test_mentions_the_keys(self):
        rows = approval_rows("bash", {"command": "rm x"}, "confirm", 60)
        flat = "\n".join("".join(t for t, _ in r) for r in rows)
        assert "y" in flat and "n" in flat

    def test_every_row_exact_width(self):
        for row in approval_rows("bash", {"command": "x" * 200}, "r", 40):
            assert _row_width(row) == 40


class TestHelp:
    def test_all_rows_exact_width(self):
        for row in help_rows(70):
            assert _row_width(row) == 70

    def test_documents_the_slash_commands(self):
        flat = "\n".join("".join(t for t, _ in r) for r in help_rows(70))
        for command in ("/help", "/tools", "/mode", "/quit"):
            assert command in flat


class TestJsonDumps:
    def test_compact(self):
        assert json_dumps({"a": 1}) == '{"a":1}'

    def test_truncated(self):
        assert json_dumps({"a": "x" * 500}, limit=20).endswith("...")

    def test_unserialisable_falls_back_to_str(self):
        assert "object" in json_dumps(object())


# --------------------------------------------------------------- nvim install
class TestNvimInstall:
    def test_plugin_files_present(self):
        from xli.nvim.install import plugin_files

        files = plugin_files()
        assert "plugin/xli.lua" in files
        assert "lua/xli/init.lua" in files
        assert "lua/xli/rpc.lua" in files
        assert "lua/xli/ui.lua" in files

    def test_install_copies_everything(self, tmp_path):
        from xli.nvim.install import install_plugin, plugin_files

        result = install_plugin(target=tmp_path)
        assert result["ok"] is True
        assert result["target"] == str(tmp_path)
        assert sorted(result["copied"]) == sorted(plugin_files())
        for rel in plugin_files():
            assert (tmp_path / rel).is_file()

    def test_install_writes_setup_snippet(self, tmp_path):
        from xli.nvim.install import install_plugin

        result = install_plugin(target=tmp_path)
        snippet = Path(result["snippet"])
        assert snippet.exists()
        assert "require('xli').setup(" in snippet.read_text()

    def test_refuses_to_overwrite_without_force(self, tmp_path):
        from xli.nvim.install import install_plugin

        install_plugin(target=tmp_path)
        second = install_plugin(target=tmp_path)
        assert second["ok"] is False
        assert "overwritten" in second["error"]
        assert second["blocked"]

    def test_force_allows_overwrite(self, tmp_path):
        from xli.nvim.install import install_plugin

        install_plugin(target=tmp_path)
        (tmp_path / "plugin" / "xli.lua").write_text("-- modified")
        result = install_plugin(target=tmp_path, force=True)
        assert result["ok"] is True
        assert (tmp_path / "plugin" / "xli.lua").read_text() != "-- modified"

    def test_existing_snippet_not_duplicated(self, tmp_path):
        from xli.nvim.install import install_plugin

        first = install_plugin(target=tmp_path)
        install_plugin(target=tmp_path, force=True)
        after = Path(first["snippet"]).read_text()
        assert after.count("require('xli').setup(") == 1

    def test_user_init_lua_is_never_touched(self, tmp_path):
        from xli.nvim.install import install_plugin

        (tmp_path / "init.lua").write_text("-- my own config\n")
        install_plugin(target=tmp_path)
        assert (tmp_path / "init.lua").read_text() == "-- my own config\n"

    def test_snippet_optional(self, tmp_path):
        from xli.nvim.install import install_plugin

        result = install_plugin(target=tmp_path, write_snippet=False)
        assert result["snippet"] is None

    def test_uninstall_removes_only_plugin_files(self, tmp_path):
        from xli.nvim.install import install_plugin, uninstall_plugin

        install_plugin(target=tmp_path)
        (tmp_path / "init.lua").write_text("-- mine\n")
        (tmp_path / "plugin" / "other.lua").write_text("-- theirs\n")

        result = uninstall_plugin(target=tmp_path)
        assert result["ok"] is True
        assert not (tmp_path / "lua" / "xli").exists()
        assert not (tmp_path / "plugin" / "xli.lua").exists()
        assert (tmp_path / "init.lua").exists()
        assert (tmp_path / "plugin" / "other.lua").exists()

    def test_default_target_uses_xdg(self, monkeypatch, tmp_path):
        from xli.nvim.install import nvim_config_dir

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert nvim_config_dir() == tmp_path / "nvim"

    def test_missing_source_reported(self, monkeypatch, tmp_path):
        import xli.nvim.install as installer

        monkeypatch.setattr(installer, "PLUGIN_SOURCE", tmp_path / "nope")
        result = installer.install_plugin(target=tmp_path / "out")
        assert result["ok"] is False
        assert "not found" in result["error"]
