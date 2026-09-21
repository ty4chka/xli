#!/usr/bin/env python3
"""
XLI Utils — FileOps: atomic writes
"""

import os
import tempfile
from pathlib import Path
from typing import Optional

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.utils.file")


class FileOps:
    """Atomic file operations"""

    @staticmethod
    def atomic_write(path: str, content: str, mode: str = "w") -> bool:
        """Write file atomically using temp file + rename"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file in same directory
        fd, temp_path = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=f".{target.name}.tmp_"
        )

        try:
            with os.fdopen(fd, mode) as f:
                f.write(content)
                f.flush()
                os.fsync(fd)

            # Atomic rename
            os.replace(temp_path, str(target))
            logger.log_structured("INFO", "file", f"Atomic write: {path}")
            return True

        except Exception as e:
            logger.log_error("file", f"Atomic write failed: {path}", exc=e)
            try:
                os.unlink(temp_path)
            except:
                pass
            return False

    @staticmethod
    def read_lines(path: str, encoding: str = "utf-8") -> list:
        """Read file as lines"""
        try:
            return Path(path).read_text(encoding=encoding).splitlines()
        except Exception as e:
            logger.log_error("file", f"Read failed: {path}", exc=e)
            return []

    @staticmethod
    def ensure_dir(path: str) -> Path:
        """Ensure directory exists"""
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        return p
