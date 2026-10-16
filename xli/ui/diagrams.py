"""Dependency-free terminal diagrams. Unsupported syntax falls back to source.

These are deliberately small, documented formats, not a Mermaid interpreter.
No HTML, shell, browser, network, or graph-layout dependency is involved.
"""
from __future__ import annotations

import math
import re

from xli.ui.text import display_width, split_cells, truncate


def mindmap(source: str, width: int) -> list[str]:
    entries = []
    for line in source.expandtabs(2).splitlines():
        if not line.strip() or line.strip() == "mindmap":
            continue
        depth = len(line) - len(line.lstrip())
        label = re.sub(r"^[-*+]\s+", "", line.strip())
        if label.startswith("root((") and label.endswith("))"):
            label = label[6:-2]
        entries.append((depth, label))
    if not entries or len(entries) > 200:
        raise ValueError("mindmap needs 1–200 nodes")
    rows, stack = [], []
    for i, (depth, label) in enumerate(entries):
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if len(stack) > 12:
            raise ValueError("mindmap too deep")
        following = next((d for d, _ in entries[i + 1:] if d <= depth), -1)
        last = following != depth
        prefix = "".join("   " if is_last else "│  " for _, is_last in stack[1:])
        prefix += ("└─ " if last else "├─ ") if stack else "◈ "
        budget = max(1, width - display_width(prefix))
        for n, part in enumerate(split_cells(label, budget)):
            rows.append(truncate((prefix if n == 0 else " " * display_width(prefix)) + part, width))
        stack.append((depth, last))
    return rows


def chart(source: str, width: int) -> list[str]:
    values = []
    for line in source.splitlines():
        if not line.strip():
            continue
        label, separator, raw = line.rpartition("|")
        if not separator or not label.strip():
            raise ValueError("expected label | non-negative number")
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError("chart values must be finite and non-negative")
        values.append((label.strip(), value))
    if not values or len(values) > 200:
        raise ValueError("chart needs 1–200 values")
    peak = max(v for _, v in values) or 1
    label_width = min(max(display_width(k) for k, _ in values), max(1, width // 3), 24)
    numbers = [f"{v:g}" for _, v in values]
    bar_width = max(0, width - label_width - max(map(len, numbers)) - 4)
    rows = []
    for (label, value), number in zip(values, numbers):
        label = truncate(label, label_width)
        label += " " * max(0, label_width - display_width(label))
        bar = "█" * round(value / peak * bar_width)
        rows.append(truncate(f"{label}  {bar.ljust(bar_width)}  {number}", width))
    return rows


def diagram_lines(language: str, source: str, width: int) -> list[str] | None:
    """None means render the original fenced code, never silently discard it."""
    if width < 12:
        return None
    try:
        if language in ("mindmap", "tree"):
            return mindmap(source, width)
        if language == "chart":
            return chart(source, width)
        if language == "mermaid" and source.lstrip().startswith("mindmap\n"):
            return mindmap(source, width)
    except (ValueError, OverflowError):
        return None
    return None
