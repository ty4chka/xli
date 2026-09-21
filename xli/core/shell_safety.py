#!/usr/bin/env python3
"""
XLI Shell Safety — shared guard for every subprocess.run(..., shell=True) call.

Before this module existed, each caller (BashTool, chain.py, nvim.py) kept its
own copy-pasted regex blocklist. That meant:
  - three slightly different lists to keep in sync
  - trivially bypassable patterns (e.g. "curl x|bash" with no spaces skipped
    the "curl .* | bash" regex entirely)
  - no coverage for command substitution / chaining tricks

This is still a blocklist, not a real sandbox (true isolation needs containers
or seccomp, tracked as a follow-up). But it centralizes the rules, closes the
obvious bypasses, and gives one place to extend.
"""

import re

# Patterns are matched against a whitespace-normalized, lowercased command.
_BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/(?:\s|$)",       # rm -rf /
    r"rm\s+-rf\s+/\*",             # rm -rf /*
    r"rm\s+-rf\s+~",               # rm -rf ~
    r"rm\s+-rf\s+\.(?:\s|$)",      # rm -rf .  (wipes the cwd)
    r"rm\s+-rf\s+\./?\*",          # rm -rf ./*
    r"mkfs\.",
    r"dd\s+if=",
    r"chmod\s+-R?\s*777\s+/",
    r":\(\)\s*\{.*\};\s*:",        # fork bomb :(){ :|:& };:
    r"\bsudo\b",
    r"curl[^\n]*\|\s*(sudo\s+)?(ba)?sh\b",   # curl ... | bash  (spaces optional)
    r"wget[^\n]*\|\s*(sudo\s+)?(ba)?sh\b",
    r">\s*/dev/sd[a-z]",
    r"\bmv\s+/\S*\s+/dev/null",
    r"\becho\s+.*>\s*/etc/passwd",
]

_COMPILED = [re.compile(p) for p in _BLOCKED_PATTERNS]

# Structural red flags that warrant blocking regardless of specific command:
# command substitution / chaining combined with a remote-fetch tool.
_FETCH_TOOLS = ("curl", "wget", "nc ", "ncat")
_CHAIN_OPS = ("|", ";", "&&", "$(", "`")


def is_shell_command_safe(command: str) -> tuple[bool, str | None]:
    """Check a raw shell command string before it's passed to subprocess.run(shell=True).

    Returns (True, None) if the command looks acceptable, else (False, reason).
    This is a defense-in-depth heuristic, not a guarantee — treat any command
    from an LLM or untrusted source as something that could still do damage
    within the permissions of the running process.
    """
    if not command or not command.strip():
        return False, "empty command"

    normalized = " ".join(command.split()).lower()

    for pattern in _COMPILED:
        if pattern.search(normalized):
            return False, f"matched blocked pattern: {pattern.pattern}"

    if any(tool in normalized for tool in _FETCH_TOOLS) and any(
        op in command for op in _CHAIN_OPS
    ):
        return False, "remote fetch combined with command chaining/substitution"

    return True, None
