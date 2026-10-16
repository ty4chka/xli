"""Readable session cards and transcript; raw event JSON remains untouched."""
from __future__ import annotations

from xli.ui.ansi import render_markdown_ansi, terminal_width
from xli.ui.text import split_cells


def _lines(text: str, width: int, prefix: str = "  ") -> list[str]:
    return [prefix + part for line in text.splitlines() or [""]
            for part in split_cells(line, max(1, width - len(prefix)))]


def session_list(sessions: list[dict], width: int | None = None) -> str:
    width = width or terminal_width()
    rows = ["", f"  Sessions · {len(sessions)}", ""]
    if not sessions:
        rows += _lines("No sessions yet. Start with: xli run <task>", width)
    for item in sessions:
        rows += _lines(str(item["id"]), width)
        rows += _lines(f"{item.get('modified', '')} · {item.get('events', 0)} events · {item.get('bytes', 0)} B", width, "    ")
        prompt = " ".join(str(item.get("first_prompt") or "(no prompt)").split())
        rows += _lines(prompt[:240], width, "    ")
        rows.append("")
    rows += _lines("xli session show <id>  ·  xli session show --latest", width)
    return "\n".join(rows) + "\n"


def session_transcript(session, width: int | None = None) -> str:
    from xli.parse import parse_response
    from xli.ui.summary import summarise_call

    width = width or terminal_width()
    rows = [""] + _lines(f"Session {session.session_id} · {len(session.events)} events", width)
    for event in session.events:
        data = event.data
        if event.kind in ("user", "assistant"):
            content = str(data.get("content", ""))
            calls = data.get("calls") or []
            parsed = None
            if event.kind == "assistant" and "<tool_call>" in content:
                parsed = parse_response(content)
                content = parsed.text
            if content.strip():
                rows += ["", "  you" if event.kind == "user" else "  xli", ""]
                if event.kind == "assistant":
                    rendered = render_markdown_ansi(content, max(1, width - 4))
                    rows += ["    " + line for line in rendered.splitlines()]
                else:
                    rows += _lines(content, width, "    ")
                rows.append("")
            for call in calls:
                rows += _lines(f"→ {call.get('name')} {summarise_call(call.get('name', ''), call.get('args') or {})}", width, "    ")
            if parsed is not None:
                rows += _lines("! Legacy text tool call — execution was not recorded:", width, "    ")
                for call in parsed.calls:
                    rows += _lines(f"{call.name} {summarise_call(call.name, call.args)}", width, "      ")
                if not parsed.calls:
                    rows += _lines(content or "Malformed tool protocol", width, "      ")
        elif event.kind == "tool":
            mark = "ok" if data.get("ok", True) else "FAIL"
            rows += _lines(f"[{mark}] {data.get('name', '')}", width, "    ")
    return "\n".join(rows) + "\n"
