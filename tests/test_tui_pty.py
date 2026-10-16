"""Exercise actual curses, UTF-8 input, approvals and terminal restoration.

No model API, GUI, terminal-emulator package, or running development server.
"""
import os
import select
import struct
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX pseudo-terminal required")

CHILD = r'''
from pathlib import Path
from types import SimpleNamespace
from xli.permissions.policy import Mode, Policy
from xli.providers.fake import FakeProvider
from xli.tools.base import Param, tool
from xli.tools.registry import ToolRegistry
from xli.tui.app import run_tui
from xli.ui.locale import set_lang
set_lang("en")
policy = Policy(mode=Mode.CONFIRM)
registry = ToolRegistry(policy=policy)
@tool("write", "Write test marker", [Param("content", required=True)], mutates=True)
async def write(content):
    Path("confirmed.txt").write_text(content)
    return {"written": True}
registry.register(write)
provider = FakeProvider(responses=[
    '<tool_call>write(content: "привет")</tool_call>',
    '## Verified\n\n```mindmap\nXLI\n  Tools\n  Plugins\n```\n<done>PTY_COMPLETE</done>',
])
config = SimpleNamespace(
    get=lambda key: {"provider.model": "fake", "provider": "fake", "agent.max_steps": 3}.get(key),
    permission_mode=lambda: "confirm", kernel_enabled=lambda: False,
)
raise SystemExit(run_tui(config, registry=registry, policy=policy, provider=provider))
'''


@pytest.mark.parametrize("width", [40, 80])
def test_real_terminal_task_and_approval(tmp_path, width):
    import fcntl
    import pty
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, width, 0, 0))
    before = termios.tcgetattr(slave)
    env = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8",
               XLI_CONFIG_DIR=str(tmp_path / "config"))
    process = subprocess.Popen([sys.executable, "-c", CHILD], cwd=tmp_path,
                               stdin=slave, stdout=slave, stderr=slave, env=env)
    received = bytearray()

    def until(marker):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if marker in received:
                return
            if process.poll() is not None:
                break
            if select.select([master], [], [], 0.1)[0]:
                received.extend(os.read(master, 65536))
        raise AssertionError(f"Missing {marker!r}; exit={process.poll()}; output={bytes(received)!r}")

    try:
        until(b"XLI")
        os.write(master, "Проверь запись\r".encode())
        until(b"mutation requires confirmation")
        assert not (tmp_path / "confirmed.txt").exists(), "must await approval"
        os.write(master, b"y")
        until(b"PTY_COMPLETE")
        assert (tmp_path / "confirmed.txt").read_text() == "привет"
        assert b"<tool_call>write" not in received
        os.write(master, b"\x04")
        assert process.wait(timeout=5) == 0
        after = termios.tcgetattr(slave)
        assert after == before, "curses must restore the terminal on exit"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)
        os.close(slave)
