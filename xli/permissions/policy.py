#!/usr/bin/env python3
"""
XLI Permissions — the gate every tool call passes through.

Three operating modes, matching how much rope the user wants to give the agent:

  auto      run everything except what an explicit deny rule blocks
  confirm   ask before anything that mutates the machine (writes, shells)
  readonly  refuse mutations outright; reads are always allowed

The policy is a *pure decision function*. It never prints, never prompts, never
touches the network — it returns a Decision, and the frontend (CLI, TUI, nvim)
decides how to ask the human. That separation is what lets the same policy run
headless in CI and interactively in a terminal.

Deny patterns are matched with fnmatch against the concrete resource a tool
names (a path, a command string), so `--deny '/etc/*'` or
`--deny 'rm -rf *'` does what it looks like.
"""

from __future__ import annotations

import fnmatch
import os
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Mode(str, Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    READONLY = "readonly"

    @classmethod
    def parse(cls, value: Any) -> Mode:
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        aliases = {
            "auto": cls.AUTO,
            "yolo": cls.AUTO,
            "yes": cls.AUTO,
            "confirm": cls.CONFIRM,
            "ask": cls.CONFIRM,
            "readonly": cls.READONLY,
            "read-only": cls.READONLY,
            "ro": cls.READONLY,
        }
        if text not in aliases:
            raise ValueError(
                f"unknown permission mode {value!r}; expected one of auto|confirm|readonly"
            )
        return aliases[text]


@dataclass(slots=True)
class Decision:
    allowed: bool
    reason: str
    needs_confirmation: bool = False
    rule: str | None = None

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "needs_confirmation": self.needs_confirmation,
            "rule": self.rule,
        }


ALLOW = Decision(True, "allowed")


# Commands that are never safe to run unattended, whatever the mode says.
# These are prefix-matched on the first token of the parsed command.
HARD_DENY_COMMANDS: tuple[str, ...] = (
    "mkfs",
    "dd",
    ":(){",          # fork bomb
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "init",
)

# Anything that recursively deletes or rewrites history needs a human in the loop.
CONFIRM_COMMAND_PATTERNS: tuple[str, ...] = (
    "rm -rf *",
    "rm -fr *",
    "git push --force*",
    "git push -f *",
    "git reset --hard*",
    "git clean -fd*",
    "chmod -R 777 *",
    "sudo *",
    "curl * | sh*",
    "curl * | bash*",
    "wget * | sh*",
    "wget * | bash*",
    "npm publish*",
    "pip install --index-url *",
)

MUTATING_TOOLS: frozenset = frozenset({
    "write", "edit", "bash", "shell", "apply_patch", "delete", "git_commit", "mkdir",
})

READONLY_TOOLS: frozenset = frozenset({
    "read", "ls", "glob", "grep", "todo", "status", "diff", "git_status", "search",
})


@dataclass
class Policy:
    """Decides whether a tool call may run.

    Attributes
    ----------
    mode:            the operating mode.
    deny:            fnmatch patterns for resources that are always refused.
    allow:           fnmatch patterns that skip the confirmation prompt in
                     `confirm` mode — an explicit user opt-in.
    root:            optional project root; paths escaping it need confirmation.
    """

    mode: Mode = Mode.CONFIRM
    deny: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    root: Path | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.mode = Mode.parse(self.mode)
        if self.root is not None:
            self.root = Path(self.root).resolve()

    # ------------------------------------------------------------- resources
    @staticmethod
    def resource_for(tool: str, args: dict[str, Any]) -> str:
        """The string deny/allow patterns are matched against for a given call."""
        for key in ("path", "file", "filename", "command", "cmd", "pattern", "url"):
            value = args.get(key)
            if value:
                return str(value)
        return tool

    # -------------------------------------------------------------- checking
    def check(self, tool: str, args: dict[str, Any] | None = None) -> Decision:
        args = args or {}
        resource = self.resource_for(tool, args)
        decision = self._check(tool, resource)
        self.history.append(
            {"tool": tool, "resource": resource, **decision.to_dict()}
        )
        return decision

    def _check(self, tool: str, resource: str) -> Decision:
        # 1. Explicit deny always wins, in every mode.
        for pattern in self.deny:
            if _matches(pattern, resource):
                return Decision(False, f"blocked by deny rule {pattern!r}", rule=pattern)

        # 2. Tool-level hard refusals.
        if self.mode is Mode.READONLY and tool in MUTATING_TOOLS:
            return Decision(False, f"tool {tool!r} is refused in readonly mode")

        if tool in ("bash", "shell"):
            hard = _dangerous_command(resource)
            if hard:
                return Decision(
                    False, f"refusing dangerous command matching {hard!r}", rule=hard
                )

        # 3. Reads are always allowed.
        if tool in READONLY_TOOLS:
            return ALLOW

        # 4. Auto mode lets everything else through.
        if self.mode is Mode.AUTO:
            return ALLOW

        # 5. Explicit allow patterns opt a resource out of prompting.
        for pattern in self.allow:
            if _matches(pattern, resource):
                return Decision(True, f"pre-approved by allow rule {pattern!r}", rule=pattern)

        # 6. Confirm mode: mutating tools and dangerous commands ask first.
        if tool in MUTATING_TOOLS:
            return Decision(
                True, "mutation requires confirmation", needs_confirmation=True
            )

        if tool in ("bash", "shell"):
            for pattern in CONFIRM_COMMAND_PATTERNS:
                if _matches(pattern, resource):
                    return Decision(
                        True,
                        f"command matches {pattern!r}",
                        needs_confirmation=True,
                        rule=pattern,
                    )

        return ALLOW

    # ------------------------------------------------------------- utilities
    def check_path_inside_root(self, path: str | os.PathLike) -> Decision:
        """Refuse writes that escape the project root by symlink or .. traversal."""
        if self.root is None:
            return ALLOW
        target = Path(path)
        resolved = (target if target.is_absolute() else self.root / target).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            return Decision(
                False, f"path {resolved} is outside project root {self.root}"
            )
        return ALLOW

    def confirm(self, tool: str, args: dict[str, Any] | None = None) -> Decision:
        """Mark a previously-needs_confirmation call as approved by the user."""
        decision = Decision(True, "approved by user")
        self.history.append(
            {
                "tool": tool,
                "resource": self.resource_for(tool, args or {}),
                **decision.to_dict(),
            }
        )
        return decision

    def summary(self) -> dict[str, Any]:
        total = len(self.history)
        return {
            "mode": self.mode.value,
            "decisions": total,
            "denied": sum(1 for h in self.history if not h["allowed"]),
            "confirmed": sum(1 for h in self.history if h.get("needs_confirmation")),
            "deny_rules": list(self.deny),
            "allow_rules": list(self.allow),
        }

    def reset_history(self) -> None:
        self.history.clear()


def _matches(pattern: str, resource: str) -> bool:
    """fnmatch on the resource, and on its basename for path-like patterns."""
    if fnmatch.fnmatch(resource, pattern):
        return True
    return bool(os.path.basename(resource)) and fnmatch.fnmatch(
        os.path.basename(resource), pattern
    )


def _dangerous_command(command: str) -> str | None:
    """Return the matched hard-deny token if the command is never safe to run."""
    stripped = command.strip()
    if not stripped:
        return None

    lowered = stripped.lower()
    for token in HARD_DENY_COMMANDS:
        if lowered.startswith(token):
            return token

    # Redirecting a device into a block device (`dd if=... of=/dev/sda`) is fatal
    # even when the command is embedded in a pipeline.
    try:
        words = shlex.split(lowered)
    except ValueError:
        words = lowered.split()

    if any(w.startswith("of=/dev/") for w in words):
        return "of=/dev/*"
    if "/dev/sd" in lowered or "/dev/nvme" in lowered:
        return "/dev/sd*"
    if ">/dev/sd" in lowered.replace(" ", ""):
        return ">/dev/sd*"
    return None


def policy_from_env(default: Mode = Mode.CONFIRM) -> Policy:
    """Build a Policy from XLI_* environment variables, for headless runs."""
    mode = Mode.parse(os.environ.get("XLI_PERMISSION_MODE", default.value))
    deny = _split(os.environ.get("XLI_DENY", ""))
    allow = _split(os.environ.get("XLI_ALLOW", ""))
    root = os.environ.get("XLI_ROOT")
    return Policy(mode=mode, deny=deny, allow=allow, root=Path(root) if root else None)


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(os.pathsep) if part.strip()]


DEFAULT_POLICY = Policy(mode=Mode.CONFIRM)
