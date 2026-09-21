#!/usr/bin/env python3
"""
XLI TUI layout — pure functions, no terminal required.

Everything that decides *what goes on which line* lives here and returns plain
data: a screen is a list of rows, a row is a list of (text, style) spans. That
makes the whole interface unit-testable with no pty and no curses, which is the
only way to keep a full-screen app honest in CI.

`xli/tui/app.py` is the thin curses adapter that paints these rows. Nothing
there decides layout, so a rendering bug is always reproducible from a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------- styles
NORMAL = "normal"
DIM = "dim"
BOLD = "bold"
ACCENT = "accent"
GOOD = "good"
WARN = "warn"
BAD = "bad"

Span = tuple[str, str]
Row = list[Span]


def span(text: str, style: str = NORMAL) -> Span:
    return (text, style)


# ------------------------------------------------------------------- chrome
def header_rows(
    width: int,
    *,
    model: str,
    provider: str,
    mode: str,
    session: str = "",
    kernel: str = "",
) -> list[Row]:
    """The two-line banner pinned to the top."""
    title = span(f" XLI {mode} ", BOLD)
    left: Row = [title, span(f" {provider}/{model}", DIM)]
    if kernel:
        left.append(span(f"  kernel:{kernel}", DIM))

    right_text = f"session {session[:18]}" if session else ""
    # Both rows must be exactly `width` columns or the frame around the window
    # walks out of alignment on every redraw.
    second: Row = [span(right_text, DIM)] if right_text else []
    if right_text and width - len(right_text) > 0:
        second = [span(" " * (width - len(right_text)), NORMAL), span(right_text, DIM)]
    return [_fit(left, width), _fit(second, width)]


def _justify(rows: list[Row], width: int) -> list[Row]:
    """Pad/truncate each row to exactly `width` columns."""
    out: list[Row] = []
    for row in rows:
        out.append(_fit(row, width))
    return out


def _fit(row: Row, width: int) -> Row:
    """Truncate a row to `width`, then pad with blanks."""
    result: Row = []
    used = 0
    for text, style in row:
        remaining = width - used
        if remaining <= 0:
            break
        if len(text) > remaining:
            # Show an ellipsis rather than silently cutting mid-word.
            result.append((text[: max(0, remaining - 1)] + "…" if remaining > 1 else "…", style))
            used = width
            break
        result.append((text, style))
        used += len(text)
    if used < width:
        result.append((" " * (width - used), NORMAL))
    return result


def _row_width(row: Row) -> int:
    return sum(len(text) for text, _ in row)


# ---------------------------------------------------------------- transcript
def transcript_row(kind: str, payload: dict[str, Any], width: int) -> list[Row]:
    """Render one agent event into zero or more screen rows."""
    if kind == "user":
        return _wrap([span("you  ", ACCENT), span(str(payload.get("text", "")))], width)

    if kind == "assistant":
        text = str(payload.get("text", "")).strip()
        if not text:
            return []
        return _wrap([span("xli  ", GOOD), span(text)], width)

    if kind == "tool_call":
        name = str(payload.get("name", ""))
        args = _compact_args(payload.get("args") or {})
        return _wrap([span("  -> ", DIM), span(f"{name} ", BOLD), span(args, DIM)], width)

    if kind == "tool_result":
        ok = bool(payload.get("ok"))
        mark = span("ok ", GOOD) if ok else span("FAIL ", BAD)
        summary = str(payload.get("summary", ""))
        return _wrap([span("     ", DIM), mark, span(summary, DIM)], width)

    if kind == "repair":
        return _wrap([span("  ! ", WARN), span(str(payload.get("detail", "")), WARN)], width)

    if kind == "warning":
        return _wrap([span("  ! ", WARN), span(str(payload.get("message", "")), WARN)], width)

    if kind == "error":
        return _wrap([span("  x ", BAD), span(str(payload.get("message", "")), BAD)], width)

    if kind == "step":
        return [
            [span(f"-- step {payload.get('index')}/{payload.get('max_steps')}", DIM)]
        ]

    if kind == "note":
        return _wrap([span("  · ", DIM), span(str(payload.get("text", "")), DIM)], width)

    return []


def _compact_args(args: dict[str, Any], limit: int = 90) -> str:
    import json

    try:
        text = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(args)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _wrap(row: Row, width: int) -> list[Row]:
    """Word-wrap a row across `width`, honouring explicit newlines."""
    if width <= 0:
        return [[]]

    pieces: list[Span] = []
    for text, style in row:
        parts = text.split("\n")
        for index, part in enumerate(parts):
            if index:
                pieces.append(("\n", style))
            pieces.append((part, style))

    rows: list[Row] = []
    current: Row = []
    used = 0

    for text, style in pieces:
        if text == "\n":
            rows.append(current)
            current, used = [], 0
            continue
        for word in _split_words(text):
            word_len = len(word)
            if used + word_len > width and current:
                rows.append(current)
                current, used = [], 0
                # Do not carry a leading space onto the new line.
                if word == " ":
                    continue
            current.append((word, style))
            used += word_len

    rows.append(current)
    return [_fit(row, width) for row in rows]


def _split_words(text: str) -> list[str]:
    """Split into words keeping separators, so wrapping is lossless."""
    if not text:
        return []
    words: list[str] = []
    buffer = ""
    for char in text:
        if char == " ":
            if buffer:
                words.append(buffer)
                buffer = ""
            words.append(" ")
        else:
            buffer += char
    if buffer:
        words.append(buffer)
    return words


# ------------------------------------------------------------------ statusbar
def status_row(
    width: int,
    *,
    busy: bool = False,
    hint: str = "",
    counters: dict[str, Any] | None = None,
) -> Row:
    """The bottom bar: what is happening plus how to get out."""
    counters = counters or {}
    state = span(" working… ", BOLD) if busy else span(" ready ", BOLD)
    parts: Row = [state]

    bits = []
    for key in ("steps", "tools", "errors", "tokens"):
        if key in counters:
            bits.append(f"{key} {counters[key]}")
    if bits:
        parts.append(span("  " + " · ".join(bits), DIM))

    keys = hint or "^C quit · ^L redraw · Enter send · ↑ history"
    filler = width - _row_width(parts) - len(keys)
    if filler > 0:
        parts.append(span(" " * filler, DIM))
    parts.append(span(keys, DIM))
    return _fit(parts, width)


def input_row(width: int, prompt: str, text: str, *, cursor_visible: bool = True) -> Row:
    prefix = span(prompt, ACCENT)
    available = width - len(prompt)
    body = text if len(text) <= available else "…" + text[-(available - 1) :]
    row: Row = [prefix, span(body)]
    if cursor_visible and _row_width(row) < width:
        row.append(span(" ", BOLD))
    return _fit(row, width)


# --------------------------------------------------------------------- layout
@dataclass
class Layout:
    """Where each region starts, given a terminal size."""

    width: int
    height: int
    header_lines: int = 2
    input_lines: int = 1
    status_lines: int = 1

    @property
    def body_top(self) -> int:
        return self.header_lines

    @property
    def body_height(self) -> int:
        return max(
            1, self.height - self.header_lines - self.input_lines - self.status_lines
        )

    @property
    def body_bottom(self) -> int:
        return self.body_top + self.body_height

    @property
    def input_top(self) -> int:
        return self.body_bottom

    @property
    def status_top(self) -> int:
        return self.input_top + self.input_lines

    def is_usable(self) -> bool:
        return self.width >= 20 and self.height >= 8


def make_layout(width: int, height: int) -> Layout:
    return Layout(width=width, height=height)


def visible_window(rows: list[Row], height: int, scroll: int) -> tuple[list[Row], int]:
    """Slice `rows` to what fits, honouring a scroll offset from the bottom.

    scroll=0 means "pinned to the newest line", which is the normal state; a
    positive scroll walks backwards into history.
    """
    if not rows:
        return [], 0
    total = len(rows)
    start = max(0, total - height - max(0, scroll))
    end = min(total, start + height)
    return rows[start:end], total - end


def scroll_limit(total_rows: int, height: int) -> int:
    return max(0, total_rows - height)


# ------------------------------------------------------------------- approvals
def approval_rows(tool: str, args: dict[str, Any], reason: str, width: int) -> list[Row]:
    """The modal asking permission for a mutating tool call."""
    rows: list[Row] = []
    rows.append(_fit([span(f" approve {tool}? ", BOLD)], width))
    rows.append(_fit([span(f" {reason}", WARN)], width))
    for line in _wrap([span(f" {json_dumps(args)}", DIM)], width):
        rows.append(line)
    rows.append(_fit([span(" y allow · n refuse · a allow all ", ACCENT)], width))
    return rows


def json_dumps(value: Any, limit: int = 300) -> str:
    import json

    try:
        text = json.dumps(value, ensure_ascii=False, indent=None, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ------------------------------------------------------------------- help pane
HELP_TEXT = """\
XLI — keys
  Enter        send the line
  Up / Down    walk input history
  PgUp / PgDn  scroll the transcript
  Home / End   jump to top / bottom of the transcript
  ^L           redraw
  ^C           quit
  /help        list slash commands

slash commands
  /help        this pane
  /tools       list available tools
  /mode auto|confirm|readonly
  /model NAME  switch model
  /session     show the session id
  /clear       clear the transcript
  /quit        exit
"""


def help_rows(width: int) -> list[Row]:
    rows: list[Row] = []
    for line in HELP_TEXT.splitlines():
        rows.append(_fit([span(line, DIM if line.startswith(" ") else BOLD)], width))
    return rows
