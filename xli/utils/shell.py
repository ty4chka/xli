#!/usr/bin/env python3
"""
XLI Utils — SafeShell: dangerous check, timeout
"""

import subprocess

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.utils.shell")

# Dangerous commands blacklist
DANGEROUS_PATTERNS = [
    "rm -rf /", "mkfs", "dd if=/dev/zero", ">:(){ :|:& };:",
    "chmod 777 /", "mv /* /dev/null", "curl .*| sh", "wget .*| sh",
    "sudo rm -rf", "rm -rf ~", "del /f /s /q", "format c:",
    "shutdown", "reboot", "poweroff", "halt",
]


class SafeShell:
    """Safe shell execution with validation"""

    @staticmethod
    def is_dangerous(cmd: str) -> str | None:
        """Check if command is dangerous. Returns reason or None."""
        cmd_lower = cmd.lower().strip()

        for pattern in DANGEROUS_PATTERNS:
            if pattern.lower() in cmd_lower:
                return f"Dangerous pattern detected: {pattern}"

        # Check for rm -rf without specific path
        if "rm -rf" in cmd_lower and "rm -rf ." not in cmd_lower:
            parts = cmd_lower.split()
            if "rm" in parts and "-rf" in parts:
                idx = parts.index("-rf") if "-rf" in parts else parts.index("rm") + 1
                if idx + 1 < len(parts):
                    target = parts[idx + 1]
                    if target in ("/", "/*", "~", "~/*", "."):
                        return f"Dangerous rm -rf target: {target}"

        return None

    @staticmethod
    def run(cmd: str, timeout: int = 30, cwd: str | None = None,
            capture: bool = True) -> subprocess.CompletedProcess:
        """Run shell command safely"""
        danger = SafeShell.is_dangerous(cmd)
        if danger:
            logger.log_structured("WARN", "shell", f"BLOCKED: {danger}")
            raise ValueError(f"Dangerous command blocked: {danger}")

        logger.log_structured("INFO", "shell", f"Executing: {cmd[:80]}")

        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=capture,
            text=True,
            timeout=timeout,
            cwd=cwd
        )

        return result
