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

from xli.ui.locale import t
from xli.ui.markdown import render_rows as md_rows
from xli.ui.summary import summarise_call
from xli.ui.text import display_width, json_dumps as _json_dumps, truncate as truncate_cells

# ---------------------------------------------------------------- styles
NORMAL = "normal"
DIM = "dim"
BOLD = "bold"
ACCENT = "accent"
GOOD = "good"
WARN = "warn"
BAD = "bad"
HEADING = "heading"
HEADING2 = "heading2"
HEADING3 = "heading3"
CODE = "code"
QUOTE = "quote"
LINK = "link"
ITALIC = "italic"
STRIKE = "strike"

Span = tuple[str, str]
Row = list[Span]

#: Which colour a permission mode is drawn in. The mode is the single most
#: consequential thing on screen -- it decides whether the agent may write -- so
#: it gets its own colour rather than sharing the accent.
MODE_STYLE = {"readonly": ACCENT, "confirm": WARN, "auto": BAD}

#: Braille spinner frames. Braille dots occupy one cell in every terminal that
#: can draw them at all, so the bar does not jitter as the animation runs.
SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

#: Left-edge marker per transcript event. Together they form a spine you can
#: scan without reading the text: a solid bar is something said, a branch is a
#: tool, a vertical is its result.
GUTTER = {
    "user": ("▌", ACCENT),
    "assistant": ("▌", GOOD),
    "tool_call": ("├", DIM),
    "tool_result": ("│", DIM),
    "repair": ("┊", WARN),
    "warning": ("┊", WARN),
    "error": ("▌", BAD),
    "step": ("·", DIM),
    "note": ("·", DIM),
    #: A `think` tool call: the agent talking to itself, worth showing but
    #: never to be mistaken for output or for speech to the user.
    "thought": ("∴", QUOTE),
}


def span(text: str, style: str = NORMAL) -> Span:
    return (text, style)


def spinner_frame(tick: int) -> str:
    """One braille frame, cycling. Negative and huge ticks both wrap safely."""
    return SPINNER[tick % len(SPINNER)]


# ------------------------------------------------------------------- chrome
def extend_rule(row: Row, width: int, *, style: str = DIM) -> Row:
    """Append box-drawing fill to `row` so it spans exactly `width`.

    Takes a row of spans rather than a string, because the alternative --
    flattening to text first -- silently discards every style in it. The header
    did exactly that and the permission-mode badge, the one thing on screen that
    says whether the agent may write, came out the same grey as the rule.
    """
    if width <= 0:
        return []
    used = _row_width(row)
    fill = width - used
    if fill <= 0:
        return _fit(row, width)
    return _fit(list(row) + [("─" * fill, style)], width)


def _rule(width: int, *, left: str = "", right: str = "", style: str = DIM) -> Row:
    """A horizontal rule with text at each end, filled with box-drawing."""
    if width <= 0:
        return []
    taken = display_width(left) + display_width(right)
    fill = width - taken
    if fill < 1:
        return _fit([(left + right, style)], width)
    if fill == 1:
        return _fit([(left, style), ("─", style), (right, style)], width)
    left_pad = " " if left else ""
    right_pad = " " if right else ""
    line = fill - display_width(left_pad) - display_width(right_pad)
    if line < 1:
        return _fit([(left + right, style)], width)
    return _fit(
        [(left, style), (left_pad, style), ("─" * line, style), (right_pad, style), (right, style)],
        width,
    )


def header_rows(
    width: int,
    *,
    model: str,
    provider: str,
    mode: str,
    session: str = "",
    kernel: str = "",
) -> list[Row]:
    """The two-line banner pinned to the top.

    Row one carries identity; row two carries state. The mode is drawn in its
    own colour and the rule underneath is what separates the chrome from the
    conversation.
    """
    mode_style = MODE_STYLE.get(mode, ACCENT)

    first: Row = [
        span(" XLI ", BOLD),
        span("─", DIM),
        span(f" {provider}/{model} ", DIM),
    ]
    if kernel:
        first += [span("─", DIM), span(f" kernel:{kernel} ", DIM)]

    second: Row = [span(f" {mode} ", mode_style)]
    if session:
        second += [span("─", DIM), span(f" {session[:18]} ", DIM)]

    return [_fit(first, width), extend_rule(second, width)]


def _justify(rows: list[Row], width: int) -> list[Row]:
    """Pad/truncate each row to exactly `width` columns."""
    out: list[Row] = []
    for row in rows:
        out.append(_fit(row, width))
    return out


def _fit(row: Row, width: int) -> Row:
    """Truncate a row to `width` cells, then pad with blanks.

    Measured in terminal cells, not characters: a wide glyph occupies two, so
    counting len() let the row overrun and the frame stepped out of alignment
    on every redraw.
    """
    result: Row = []
    used = 0
    for text, style in row:
        remaining = width - used
        if remaining <= 0:
            break
        if display_width(text) > remaining:
            result.append((truncate_cells(text, remaining), style))
            used = width
            break
        result.append((text, style))
        used += display_width(text)
    if used < width:
        result.append((" " * (width - used), NORMAL))
    return result


def _row_width(row: Row) -> int:
    return sum(display_width(text) for text, _ in row)


def truncate_left(text: str, width: int) -> str:
    """Keep the *end* of `text`, prefixed with an ellipsis.

    For the input line, where the newest characters are what the user is
    looking at, so scrolling has to drop from the left.
    """
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    budget = width - display_width("…")
    if budget <= 0:
        return "…"
    chars = list(text)
    out: list[str] = []
    used = 0
    for char in reversed(chars):
        from xli.ui.text import char_width

        step = char_width(char)
        if used + step > budget:
            break
        out.append(char)
        used += step
    return "…" + "".join(reversed(out))


# ---------------------------------------------------------------- transcript
#: Width of the left gutter, in cells. Every transcript row reserves it so the
#: markers line up in a column and the text starts at the same place each time.
GUTTER_WIDTH = 2


def _gutter(kind: str) -> Row:
    """The left-edge marker for an event kind."""
    mark, style = GUTTER.get(kind, (" ", DIM))
    return [span(f"{mark} ", style)]


def transcript_row(kind: str, payload: dict[str, Any], width: int) -> list[Row]:
    """Render one agent event into zero or more screen rows.

    Every row starts with a gutter marker, so the left edge reads as a spine:
    you can see who spoke, which tool ran and where it failed without reading a
    word. Assistant text goes through the markdown renderer; everything else is
    plain, because tool output and diagnostics should be shown verbatim.
    """
    gutter = _gutter(kind)

    if kind == "user":
        return _wrap(
            gutter + [span(t("tui_you"), ACCENT), span(str(payload.get("text", "")))], width
        )

    if kind == "assistant":
        text = str(payload.get("text", "")).strip()
        if not text:
            return []
        # The gutter is part of the indent, so the body wraps inside it. Every
        # row is padded back to `width`: the TUI paints into a fixed frame and
        # an unpadded row leaves the previous frame's characters on screen.
        body_width = max(20, width - GUTTER_WIDTH - 4)
        out: list[Row] = [_fit(gutter + [span("xli ", GOOD)], width)]
        for row in md_rows(text, body_width):
            out.append(_fit([span(" " * (GUTTER_WIDTH + 1), NORMAL)] + row, width))
        return out

    if kind == "tool_call":
        name = str(payload.get("name", ""))
        args = payload.get("args") or {}
        if name == "think":
            # The agent's own reasoning gets its own spine marker instead of
            # looking like an external tool poking at files.
            thought = str(args.get("thought", "")).strip()
            return _wrap(_gutter("thought") + [span(thought, ITALIC)], width)
        line = summarise_call(name, args)
        return _wrap(gutter + [span(f"{name} ", BOLD), span(line, DIM)], width)

    if kind == "tool_result":
        ok = bool(payload.get("ok"))
        mark = span(t("ok") + " ", GOOD) if ok else span(t("fail") + " ", BAD)
        summary = str(payload.get("summary", ""))
        return _wrap(gutter + [mark, span(summary, DIM)], width)

    if kind == "repair":
        return _wrap(gutter + [span(str(payload.get("detail", "")), WARN)], width)

    if kind == "warning":
        return _wrap(gutter + [span(str(payload.get("message", "")), WARN)], width)

    if kind == "error":
        return _wrap(gutter + [span(str(payload.get("message", "")), BAD)], width)

    if kind == "step":
        return _wrap(
            gutter
            + [
                span(
                    t(
                        "tui_step",
                        index=payload.get("index"),
                        max_steps=payload.get("max_steps"),
                    ),
                    DIM,
                )
            ],
            width,
        )

    if kind == "note":
        return _wrap(gutter + [span(str(payload.get("text", "")), DIM)], width)

    return []


def _compact_args(args: dict[str, Any], limit: int = 90) -> str:
    import json

    try:
        text = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(args)
    return text if display_width(text) <= limit else truncate_cells(text, limit)


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
            word_len = display_width(word)
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
    tick: int = 0,
    mode: str = "",
) -> Row:
    """The bottom bar: what is happening, how much has happened, how to get out.

    Three segments separated by `│`: state on the left, counters in the middle,
    keys on the right. The spinner animates rather than sitting still, because a
    static "working…" on a request that has hung is indistinguishable from one
    that is about to finish.
    """
    counters = counters or {}

    if busy:
        state: Row = [
            span(f" {spinner_frame(tick)} ", ACCENT),
            span(t("tui_working") + " ", BOLD),
        ]
    else:
        state = [span(" ● ", GOOD), span(t("tui_ready") + " ", BOLD)]
    if mode:
        state.append(span(f"[{mode}]", MODE_STYLE.get(mode, DIM)))

    bits = []
    for key in ("steps", "tools", "errors", "tokens"):
        if key in counters:
            bits.append(t(f"tui_{key}", n=counters[key]))
    middle: Row = [span(" │ ", DIM)] if bits else []
    if bits:
        middle.append(span(" · ".join(bits), DIM))

    keys = hint or t("tui_keys")

    used = _row_width(state) + _row_width(middle) + display_width(keys) + 2
    filler = width - used
    parts: Row = list(state) + list(middle)
    if filler > 0:
        parts.append(span(" " * filler, NORMAL))
        parts.append(span(keys, DIM))
    else:
        # Too narrow for everything: the keys matter more than the counters,
        # because they are how the user gets out.
        parts.append(span(" ", NORMAL))
        parts.append(span(truncate_cells(keys, max(0, width - _row_width(parts))), DIM))
    return _fit(parts, width)


def input_row(
    width: int,
    prompt: str,
    text: str,
    *,
    cursor_visible: bool = True,
    mode: str = "",
    hint: str = "",
) -> Row:
    """The composition line.

    The prompt takes the mode's colour, so a session that has been switched to
    readonly looks different at the point where the user is typing -- which is
    the moment the distinction actually matters. With an empty buffer the
    `hint` shows where to type and what the line is for, so the field never
    looks like dead space.
    """
    prefix = span(prompt, MODE_STYLE.get(mode, ACCENT))
    available = width - display_width(prompt)
    if text:
        body_span = span(text if display_width(text) <= available else truncate_left(text, available))
    elif hint:
        body_span = span(truncate_cells(hint, available), DIM)
    else:
        body_span = span("")
    row: Row = [prefix, body_span]
    if cursor_visible and _row_width(row) < width:
        row.append(span("▏", ACCENT))
    return _fit(row, width)


def separator_row(width: int, *, style: str = DIM) -> Row:
    """The rule between the conversation and the composition line."""
    if width <= 0:
        return []
    return _fit([("─" * width, style)], width)


# --------------------------------------------------------------------- layout
@dataclass
class Layout:
    """Where each region starts, given a terminal size."""

    width: int
    height: int
    header_lines: int = 2
    #: Two, not one: the rule above the composition line is part of the region.
    #: Painting both rows while reserving one made the status bar overwrite the
    #: line the user was typing on.
    input_lines: int = 2
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
    rows.append(_fit([span(t("tui_approve", tool=tool), BOLD)], width))
    rows.append(_fit([span(f" {reason}", WARN)], width))
    for line in _wrap([span(f" {summarise_call(tool, args)}", DIM)], width):
        rows.append(line)
    rows.append(_fit([span(t("tui_approve_keys"), ACCENT)], width))
    return rows


# json_dumps lives in xli.ui.text so xli.ui.summary can use it without
# importing this module back (summary → widgets closed an import cycle). The
# re-export keeps the historical widgets.json_dumps name working.
json_dumps = _json_dumps


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

HELP_TEXT_RU = """\
XLI — клавиши
  Enter        отправить строку
  Up / Down    история ввода
  PgUp / PgDn  листать транскрипт
  Home / End   в начало / в конец транскрипта
  ^L           перерисовать
  ^C           выход
  /help        список slash-команд

slash-команды
  /help        эта панель
  /tools       доступные инструменты
  /mode auto|confirm|readonly
  /model NAME  сменить модель
  /session     показать id сессии
  /clear       очистить транскрипт
  /quit        выход
"""


def help_rows(width: int) -> list[Row]:
    from xli.ui.locale import lang

    text = HELP_TEXT_RU if lang() == "ru" else HELP_TEXT
    rows: list[Row] = []
    for line in text.splitlines():
        rows.append(_fit([span(line, DIM if line.startswith(" ") else BOLD)], width))
    return rows
