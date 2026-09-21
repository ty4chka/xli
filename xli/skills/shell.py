#!/usr/bin/env python3
"""
XLI Utils — SafeShell: dangerous check, timeout

This now delegates to xli.core.shell_safety / xli.core.exec_guard instead of
keeping its own (5th!) copy of a dangerous-command blocklist. The previous
version also had a real bug: it explicitly excluded "rm -rf ." from the
dangerous check, meaning "delete the current directory recursively" was
allowed through unblocked.
"""

import subprocess
from typing import Optional

from xli.core.logger import StructuredLogger
from xli.core.shell_safety import is_shell_command_safe
from xli.core.exec_guard import run_guarded_shell

logger = StructuredLogger("xli.utils.shell")


class SafeShell:
    """Safe shell execution with validation"""

    @staticmethod
    def is_dangerous(cmd: str) -> Optional[str]:
        """Check if command is dangerous. Returns reason or None."""
        safe, reason = is_shell_command_safe(cmd)
        return None if safe else reason

    @staticmethod
    def run(cmd: str, timeout: int = 30, cwd: Optional[str] = None,
            capture: bool = True) -> subprocess.CompletedProcess:
        """Run shell command safely"""
        danger = SafeShell.is_dangerous(cmd)
        if danger:
            logger.log_structured("WARN", "shell", f"BLOCKED: {danger}")
            raise ValueError(f"Dangerous command blocked: {danger}")

        logger.log_structured("INFO", "shell", f"Executing: {cmd[:80]}")

        return run_guarded_shell(cmd, timeout=timeout, cwd=cwd)
