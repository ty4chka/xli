#!/usr/bin/env python3
"""Markdown rendering for terminals.

Two front ends need this and neither should own it: the TUI paints styled spans
into curses, and the CLI prints ANSI to stdout. So this module produces neither
— it produces the same (text, style) spans the TUI already uses, and a separate
function in `xli/cli.py` turns those into ANSI. One parser, two painters.

It is deliberately a small hand-written parser rather than a dependency. The
alternative is `rich`, which is an optional extra under `[tui]`; making the CLI
depend on it would mean `xli run` stopped working on a bare install, which is
the exact failure `_http.py` was written to fix.

Supported: headings, fenced code (with a language tag), bullet/numbered lists,
block quotes, horizontal rules, tables, and inline bold/italic/code/links.
Anything unrecognised is passed through as plain text, so a malformed document
degrades to readable prose instead of raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

Span = tuple[str, str]

# Style names. These are the TUI's vocabulary; the ANSI painter maps them.
NORMAL = "normal"
DIM = "dim"
BOLD = "bold"
ACCENT = "accent"
GOOD = "good"
WARN = "warn"
BAD = "bad"
CODE = "code"
QUOTE = "quote"
HEADING = "heading"
LINK = "link"


@dataclass
class Block:
    """One markdown block, already split from its neighbours."""

    kind: str                      # paragraph, heading, code, list, quote, rule, table
    text: str = ""
    level: int = 0                 # heading level, or list indent
    language: str = ""             # code fence language
    items: list[str] = field(default_factory=list)
    ordered: bool = False
    rows: list[list[str]] = field(default_factory=list)


_FENCE = re.compile(r"^(\s*)(```+|~~~+)\s*([A-Za-z0-9_+#.-]*)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RULE = re.compile(r"^\s{0,3}([-*_])\s*(?:\1\s*){2,}$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")


def parse(markdown: str) -> list[Block]:
    """Split markdown into blocks. Never raises on malformed input."""
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        # ---- fenced code ------------------------------------------------
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(2)
            language = fence.group(3)
            body: list[str] = []
            index += 1
            while index < len(lines):
                closing = _FENCE.match(lines[index])
                if closing and closing.group(2)[0] == marker[0]:
                    index += 1
                    break
                body.append(lines[index])
                index += 1
            blocks.append(Block(kind="code", text="\n".join(body), language=language))
            continue

        # ---- heading ----------------------------------------------------
        heading = _HEADING.match(line)
        if heading:
            blocks.append(
                Block(kind="heading", text=heading.group(2).strip(), level=len(heading.group(1)))
            )
            index += 1
            continue

        # ---- horizontal rule --------------------------------------------
        if _RULE.match(line):
            blocks.append(Block(kind="rule"))
            index += 1
            continue

        # ---- table ------------------------------------------------------
        if _TABLE_ROW.match(line) and index + 1 < len(lines) and _TABLE_SEP.match(lines[index + 1]):
            rows = [_split_row(line)]
            index += 2
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                rows.append(_split_row(lines[index]))
                index += 1
            blocks.append(Block(kind="table", rows=rows))
            continue

        # ---- block quote -------------------------------------------------
        quote = _QUOTE.match(line)
        if quote:
            body = [quote.group(1)]
            index += 1
            while index < len(lines):
                more = _QUOTE.match(lines[index])
                if not more:
                    break
                body.append(more.group(1))
                index += 1
            blocks.append(Block(kind="quote", text="\n".join(body)))
            continue

        # ---- list ---------------------------------------------------------
        bullet = _BULLET.match(line)
        ordered = _ORDERED.match(line)
        if bullet or ordered:
            match = bullet or ordered
            items = [match.group(3)]
            indent = len(match.group(1))
            is_ordered = bool(ordered)
            index += 1
            while index < len(lines):
                more = _BULLET.match(lines[index]) or _ORDERED.match(lines[index])
                if not more:
                    # A continuation line belongs to the previous item.
                    if lines[index].strip() and lines[index].startswith(" " * (indent + 1)):
                        items[-1] += " " + lines[index].strip()
                        index += 1
                        continue
                    break
                if bool(_ORDERED.match(lines[index])) != is_ordered:
                    break
                items.append(more.group(3))
                index += 1
            blocks.append(
                Block(kind="list", items=items, ordered=is_ordered, level=indent)
            )
            continue

        # ---- blank ---------------------------------------------------------
        if not line.strip():
            index += 1
            continue

        # ---- paragraph ------------------------------------------------------
        body = [line]
        index += 1
        while index < len(lines):
            nxt = lines[index]
            if (
                not nxt.strip()
                or _FENCE.match(nxt)
                or _HEADING.match(nxt)
                or _RULE.match(nxt)
                or _BULLET.match(nxt)
                or _ORDERED.match(nxt)
                or _QUOTE.match(nxt)
                or _TABLE_ROW.match(nxt)
            ):
                break
            body.append(nxt)
            index += 1
        blocks.append(Block(kind="paragraph", text=" ".join(s.strip() for s in body)))

    return blocks


def _split_row(line: str) -> list[str]:
    inner = line.strip().strip("|")
    return [cell.strip() for cell in inner.split("|")]


# ---------------------------------------------------------------- inline spans
_INLINE = re.compile(
    r"""
      (`+)(?P<code>.+?)\1                      # `code`
    | \*\*\*(?P<bi>.+?)\*\*\*                  # ***bold italic***
    | \*\*(?P<bold>.+?)\*\*                    # **bold**
    | (?P<italic>\*(?!\s)(?:[^*]|\*\*)+?\*)     # *italic*
    | __(?P<ubold>.+?)__                       # __bold__
    | \[(?P<link>[^\]]*)\]\((?P<url>[^)]*)\)   # [text](url)
    """,
    re.VERBOSE,
)


def inline_spans(text: str, base: str = NORMAL) -> list[Span]:
    """Turn inline markdown into styled spans, leaving prose untouched."""
    if not text:
        return []
    out: list[Span] = []
    position = 0

    for match in _INLINE.finditer(text):
        if match.start() > position:
            out.append((text[position : match.start()], base))

        if match.group("code") is not None:
            out.append((match.group("code"), CODE))
        elif match.group("bi") is not None:
            out.append((match.group("bi"), BOLD))
        elif match.group("bold") is not None:
            out.append((match.group("bold"), BOLD))
        elif match.group("ubold") is not None:
            out.append((match.group("ubold"), BOLD))
        elif match.group("italic") is not None:
            out.append((match.group("italic")[1:-1], ACCENT))
        elif match.group("link") is not None:
            label = match.group("link") or match.group("url")
            out.append((label, LINK))
        position = match.end()

    if position < len(text):
        out.append((text[position:], base))
    return [span for span in out if span[0]]


def strip_inline(text: str) -> str:
    """Remove inline markup, keeping the words. For width-sensitive contexts."""
    return "".join(part for part, _ in inline_spans(text))


# ------------------------------------------------------------------- rendering
def render_rows(markdown: str, width: int, *, indent: int = 0) -> list[list[Span]]:
    """Render markdown to rows of spans, wrapped to `width`.

    Returns the same row shape the TUI paints, so the two stay in step.
    """
    from xli.ui.text import display_width

    rows: list[list[Span]] = []
    gutter = " " * indent
    body_width = max(10, width - indent)

    for block in parse(markdown):
        if block.kind == "heading":
            prefix = "▌" if block.level <= 2 else "▐"
            rows.append(_wrap_spans(
                [(f"{gutter}{prefix} ", ACCENT)]
                + [(t, HEADING) for t, _ in inline_spans(block.text)],
                body_width,
            ))

        elif block.kind == "code":
            if block.language:
                rows.append([(f"{gutter}┌─ {block.language} ", DIM), ("─" * 3, DIM)])
            else:
                rows.append([(f"{gutter}┌", DIM), ("─" * 3, DIM)])
            for line in (block.text.split("\n") if block.text else [""]):
                rows.append([(f"{gutter}│ ", DIM), (line, CODE)])
            rows.append([(f"{gutter}└", DIM), ("─" * 3, DIM)])

        elif block.kind == "list":
            for number, item in enumerate(block.items, start=1):
                marker = f"{number}." if block.ordered else "•"
                rows.append(_wrap_spans(
                    [(f"{gutter}  {marker} ", ACCENT)] + inline_spans(item),
                    body_width,
                    hang=len(marker) + 3,
                ))

        elif block.kind == "quote":
            for line in block.text.split("\n"):
                rows.append(_wrap_spans(
                    [(f"{gutter}▎ ", DIM)] + inline_spans(line, QUOTE), body_width
                ))

        elif block.kind == "rule":
            rows.append([(gutter + "─" * min(body_width, 40), DIM)])

        elif block.kind == "table":
            rows.extend(_render_table(block.rows, body_width, gutter))

        else:  # paragraph
            rows.append(_wrap_spans(gutter_spans(gutter) + inline_spans(block.text), body_width))

    return rows


def gutter_spans(gutter: str) -> list[Span]:
    return [(gutter, NORMAL)] if gutter else []


def _render_table(rows: list[list[str]], width: int, gutter: str) -> list[list[Span]]:
    from xli.ui.text import display_width, truncate

    if not rows:
        return []
    columns = max(len(row) for row in rows)
    rows = [row + [""] * (columns - len(row)) for row in rows]

    # Give every column its widest cell, then shrink to fit if needed.
    widths = [
        max(display_width(strip_inline(row[i])) for row in rows) for i in range(columns)
    ]
    overhead = columns * 3 + 1
    available = max(columns, width - len(gutter) - overhead)
    if sum(widths) > available:
        scale = available / sum(widths)
        widths = [max(3, int(w * scale)) for w in widths]

    out: list[list[Span]] = []
    for position, row in enumerate(rows):
        cells: list[Span] = [(f"{gutter}│ ", DIM)]
        for index, cell in enumerate(row):
            text = truncate(strip_inline(cell), widths[index])
            cells.append((text.ljust(widths[index]), BOLD if position == 0 else NORMAL))
            cells.append((" │ ", DIM))
        out.append(cells)
        if position == 0:
            line = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
            out.append([(gutter + line, DIM)])
    return out


def _wrap_spans(spans: list[Span], width: int, *, hang: int = 0) -> list[Span]:
    """Join spans into a single row; the caller's painter handles the wrap."""
    return list(spans)


def wrap_row(row: list[Span], width: int, *, hang: int = 0) -> list[list[Span]]:
    """Word-wrap a span row to `width` cells, preserving each span's style."""
    from xli.ui.text import char_width, display_width

    if width <= 0:
        return [[]]

    out: list[list[Span]] = []
    current: list[Span] = []
    used = 0
    continuation = " " * hang

    def flush() -> None:
        nonlocal current, used
        out.append(current)
        current, used = [], 0

    for text, style in row:
        for line in text.split("\n"):
            if line != text.split("\n")[0]:
                flush()
                if hang:
                    current.append((continuation, NORMAL))
                    used = hang
            for word in _words(line):
                word_width = display_width(word)
                if word == " " and not current:
                    continue
                if used + word_width > width and current:
                    flush()
                    if hang:
                        current.append((continuation, NORMAL))
                        used = hang
                    if word == " ":
                        continue
                # A single word wider than the line has to be split by cells.
                if word_width > width - (hang if not current else 0):
                    for char in word:
                        step = char_width(char)
                        if step == 0:
                            current.append((char, style))
                            continue
                        if used + step > width and current:
                            flush()
                            if hang:
                                current.append((continuation, NORMAL))
                                used = hang
                        current.append((char, style))
                        used += step
                    continue
                current.append((word, style))
                used += word_width

    out.append(current)
    return out


def _words(text: str) -> list[str]:
    """Split on spaces, keeping them, so wrapping is lossless."""
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


def render_plain(markdown: str, width: int = 80) -> str:
    """Markdown as plain text, for logs and non-tty output."""
    from xli.ui.text import truncate

    lines: list[str] = []
    for row in render_rows(markdown, width):
        text = "".join(part for part, _ in row)
        lines.append(truncate(text, width))
    return "\n".join(lines)
