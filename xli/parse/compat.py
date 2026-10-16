"""Literal-only recovery of explicitly tagged legacy model calls. Never eval."""
from __future__ import annotations

import ast
import json
import re

_START = re.compile(r"<tool_call>|<tool>")


def protocol_boundary(text: str, start: int, tokens: tuple[str, ...]) -> tuple[int, str]:
    """Find a protocol delimiter, not a delimiter written inside an argument.

    Track string escapes within literal containers. Prose before the JSON is
    allowed and apostrophes in that prose must not start a quoted string.
    """
    quote, escaped, depth = "", False, 0
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        for token in tokens:
            if text.startswith(token, index):
                return index, token
        if depth and char in "\"'":
            quote = char
        elif char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
    return -1, ""



def _literal(value: str):
    try:
        return json.loads(value)
    except ValueError:
        return ast.literal_eval(value)


def _arguments(source: str) -> dict:
    # Split only at top-level commas; strings/arrays/objects may contain commas.
    parts, start, depth, quote, escaped = [], 0, 0, "", False
    for i, char in enumerate(source):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(source[start:i])
            start = i + 1
    if quote or depth:
        raise ValueError("incomplete literal")
    parts.append(source[start:])
    args = {}
    for part in parts:
        if not part.strip():
            continue
        match = re.fullmatch(r"\s*([\w-]+)\s*[:=]\s*(.+)\s*", part, re.S)
        if not match or match[1] in args:
            raise ValueError("expected unique named literal arguments")
        args[match[1]] = _literal(match[2])
    return args


def decode_legacy(payload: str) -> dict:
    payload = payload.strip()
    if payload.startswith("{"):
        value = json.loads(payload)
        if isinstance(value, dict) and isinstance(value.get("function"), dict):
            value = value["function"]
        if not isinstance(value, dict):
            raise ValueError("expected object")
        args = value.get("args", value.get("arguments", {}))
        if isinstance(args, str):
            args = json.loads(args)
        name = value.get("name", value.get("tool"))
    else:
        match = re.fullmatch(r"([\w.-]+)\s*\((.*)", payload, re.S)
        if not match:
            raise ValueError("expected a named call")
        name, body = match.groups()
        if "</arg_key>" in body:
            # A known malformed XML dialect: query</arg_key><arg_value>…
            pairs = list(re.finditer(
                r"(?:<arg_key>)?([\w-]+)</arg_key>\s*<arg_value>(.*?)</arg_value>",
                body, re.S,
            ))
            remainder = re.sub(
                r"(?:<arg_key>)?[\w-]+</arg_key>\s*<arg_value>.*?</arg_value>",
                "", body, flags=re.S,
            ).strip()
            if not pairs or remainder not in ("", ")"):
                raise ValueError("incomplete XML arguments")
            args = {p[1]: p[2] for p in pairs}
            if len(args) != len(pairs):
                raise ValueError("duplicate argument")
        else:
            if not body.endswith(")"):
                raise ValueError("incomplete call")
            args = _arguments(body[:-1])
    if not isinstance(name, str) or not name.strip() or not isinstance(args, dict):
        raise ValueError("invalid name/arguments")
    # Also rejects Python-only values (sets, bytes, complex, etc.).
    json.dumps(args, allow_nan=False)
    return {"name": name.strip(), "args": args}


def normalize_legacy(text: str, *, final: bool = True) -> tuple[str, list[str]]:
    out, repairs, cursor = [], [], 0
    for opening in _START.finditer(text):
        if opening.start() < cursor:
            continue
        if opening.group() == "<tool>":
            end, token = protocol_boundary(text, opening.end(), ("</tool>",))
            after = end + len(token) if end >= 0 else len(text)
            out.append(text[cursor:after])
            cursor = after
            continue
        end, token = protocol_boundary(
            text, opening.end(), ("</tool_call>", "</think>", "<tool_call>", "<tool>"))
        if end < 0 and not final:
            break
        if end < 0:
            end = len(text)
        after = end if token in ("<tool_call>", "<tool>") else end + len(token)
        payload = text[opening.end():end]
        out.append(text[cursor:opening.start()])
        try:
            call = decode_legacy(payload)
            out.append('<tool>' + json.dumps(call, ensure_ascii=False) + '</tool>')
            repairs.append(f"normalised legacy tool call ({call['name']})")
        except (ValueError, SyntaxError, TypeError, RecursionError):
            # Leave a canonical invalid block, so parser and agent report a
            # failed protocol turn instead of claiming successful completion.
            out.append('<tool>invalid legacy call</tool>')
            repairs.append("skipped an unparseable <tool_call> block")
        cursor = after
    out.append(text[cursor:])
    return "".join(out), repairs
