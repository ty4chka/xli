#!/usr/bin/env python3
"""
XLI ANSI — markdown as coloured terminal text for the line-based front ends.

The CLI and the REPL both print assistant answers into a plain stream, and
both stopped hand-rolling escape codes in favour of this one renderer: it
uses the same markdown parser as the curses TUI, so a heading or a fence
reads the same in all three.

Colour degrades to plain text when stdout is not a terminal or NO_COLOR is
set — a piped answer must stay machine-readable.
"""

from __future__ import annotations

import os
import sys

# Markdown span styles, mapped to the same vocabulary the TUI's curses palette
# uses, so one renderer drives both front ends.
ANSI_FOR_STYLE: dict[str, str | None] = {
    "normal": None,
    "dim": "2",
    "bold": "1",
    # Violet, not cyan: cyan reads as "info / link", violet is this app's own
    # accent. Heading levels step down the family so hierarchy is visible.
    "accent": "35",
    "good": "32",
    "warn": "33",
    "bad": "31",
    "heading": "1;95",
    "heading2": "1;35",
    "heading3": "1",
    "code": "7",
    "quote": "3;90",
    "link": "4;35",
    "italic": "3",
    "strike": "9",
}


def ansi_enabled() -> bool:
    """FORCE_COLOR wins over NO_COLOR; otherwise trust the tty."""
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def terminal_width(default: int = 80) -> int:
    """Usable width, from COLUMNS or the tty, never wider than the terminal."""
    raw = os.environ.get("COLUMNS")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    try:
        return max(20, os.get_terminal_size(sys.stdout.fileno()).columns)
    except (OSError, ValueError, AttributeError):
        return default


def render_markdown_ansi(
    markdown: str,
    width: int | None = None,
    *,
    enabled: bool | None = None,
) -> str:
    """Markdown as ANSI-coloured text.

    `enabled` lets a caller with its own colour decision (the CLI's STYLE)
    keep it; None means decide from the environment.
    """
    from xli.ui.markdown import render_rows

    if width is None:
        width = terminal_width()
    if enabled is None:
        enabled = ansi_enabled()

    rows = render_rows(markdown, width)
    if not enabled:
        return "\n".join("".join(text for text, _ in row).rstrip() for row in rows)

    out: list[str] = []
    for row in rows:
        parts: list[str] = []
        for text, style in row:
            code = ANSI_FOR_STYLE.get(style)
            parts.append(f"\033[{code}m{text}\033[0m" if code else text)
        out.append("".join(parts).rstrip())
    return "\n".join(out)
