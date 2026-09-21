#!/usr/bin/env python3
"""
XLI TUI v4 — Textual UI, primary interface, full functionality
"""

import asyncio
from datetime import datetime
from typing import Optional

from textual.app import App
from textual.containers import Container, Horizontal, Vertical, Grid, ScrollableContainer
from textual.widgets import Header, Footer, Button, Static, Input, Collapsible, ProgressBar
from textual.reactive import reactive
from rich.text import Text
from rich.console import Console
from rich.panel import Panel
from pyfiglet import Figlet

from xli.ui.base import XliUI
# from xli.core.chain import XliCore  # imported lazily in run_chain
from xli.core.env import EnvironmentAdapter
from xli.core.logger import StructuredLogger
from xli.core.streaming import TuiStreamingHandler

logger = StructuredLogger("xli.ui.tui")

CSS = """
Screen { background: $surface; }
.results-grid { grid-size: 3; grid-columns: 1fr 1fr 1fr; grid-rows: auto; height: auto; margin: 1; }
.result-panel { border: solid $primary; padding: 1; margin: 1; background: $panel; overflow-y: auto; height: auto; }
.result-panel.coder { border: solid cyan; }
.result-panel.debugger { border: solid yellow; }
.result-panel.tester { border: solid magenta; }
.result-panel.optimizer { border: solid green; }
.result-panel.reviewer { border: solid blue; }
.progress-bar { margin-top: 1; }
.state-indicator { margin-top: 1; padding: 0 1; }
.result-content { margin-top: 1; }
.activity-log { border: solid $accent; height: 18; margin-top: 1; overflow-y: auto; background: $panel; }
#input-panel { border: solid $primary; margin-top: 1; padding: 1; background: $panel; }
#run-btn { width: 22; }
#task-input { width: 1fr; }
#status-bar { background: $panel; padding: 1; margin-top: 1; }
"""


class XliTui(App):
    """Primary Textual TUI for XLI — composition over inheritance"""
    """Primary Textual TUI for XLI"""
    
    CSS = CSS
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("c", "clear_log", "Clear"),
        ("f", "focus_input", "Focus"),
        ("m", "show_mcp", "MCP"),
        ("q", "skip_questions", "Skip questions"),
        ("s", "toggle_stream", "Streaming")
    ]
    
    def __init__(self):
        App.__init__(self)
        self._ui_adapter = XliUIAdapter()
        
        self.env = EnvironmentAdapter()
        self.core = None  # lazy init
        self.progress_bars = {}
        self.state_widgets = {}
        self.response_widgets = {}
        self.log_widget = None
        self.skip_questions = False
        self.streaming = True
    
    def compose(self):
        yield Header(show_clock=True)
        
        with Container():
            # Info bar
            with Horizontal():
                yield Static("FIRE XLI PRO v4")
                yield Static(datetime.now().strftime('%Y-%m-%d'))
                yield Static("PLAN → CODER → DEBUGGER → TESTER → OPTIMIZER → REVIEWER")
                yield Static(f"MCP: {self._count_mcp()}")
            
            # Results grid — 5 agents
            with Grid(classes="results-grid"):
                for agent_id, title, color in [
                    ("coder", "CODER", "cyan"),
                    ("debugger", "DEBUGGER", "yellow"),
                    ("tester", "TESTER", "magenta"),
                    ("optimizer", "OPTIMIZER", "green"),
                    ("reviewer", "REVIEWER", "blue")
                ]:
                    with Vertical(classes=f"result-panel {agent_id}"):
                        yield Static(f"[bold {color}]{title}[/bold {color}]")
                        self.progress_bars[agent_id] = ProgressBar(total=100, show_percentage=False)
                        yield self.progress_bars[agent_id]
                        self.state_widgets[agent_id] = Static("WAITING")
                        yield self.state_widgets[agent_id]
                        self.response_widgets[agent_id] = Static("Waiting...", classes="result-content")
                        yield self.response_widgets[agent_id]
            
            # Live log
            with Collapsible(title="LIVE LOG", collapsed=False):
                self.log_widget = ScrollableContainer(classes="activity-log")
                yield self.log_widget
            
            # Input panel
            with Horizontal(id="input-panel"):
                self.task_input = Input(placeholder="Enter task...", id="task-input")
                self.run_button = Button("RUN", variant="primary", id="run-btn")
                yield self.task_input
                yield self.run_button
            
            # Status bar
            self.status_bar = Static("Enter — run | q — skip | m — MCP | s — stream", id="status-bar")
            yield self.status_bar
        
        yield Footer()
    
    def _count_mcp(self) -> int:
        """Count available MCP servers"""
        try:
            from xli.mcp.registry import get_registry
            return len(get_registry().list_enabled())
        except:
            return 0
    
    def on_mount(self):
        self.set_focus(self.task_input)
        self._log("XLI PRO v4 started", "SYS")
        self._log(f"Mode: {self.env.name} | MCP: {self._count_mcp()}", "SYS")
        logger.log_structured("INFO", "tui", "TUI mounted")
    
    def _log(self, msg: str, agent: str = "SYS"):
        """Add log entry"""
        if not self.log_widget:
            return
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = Static(f"{timestamp} [{agent}] {msg}")
        self.log_widget.mount(entry)
        self.log_widget.scroll_end(animate=False)
        logger.log_structured("DEBUG", "tui", msg, {"agent": agent})
    
    def update_state(self, agent_key: str, state: str, progress: int):
        """Update agent state"""
        if agent_key in self.state_widgets:
            self.state_widgets[agent_key].update(state)
        if agent_key in self.progress_bars:
            self.progress_bars[agent_key].progress = progress
    
    def update_response(self, agent_key: str, response: str):
        """Update agent response"""
        if agent_key in self.response_widgets:
            preview = response[:300] if response else "No response"
            self.response_widgets[agent_key].update(preview)
    
    def clear_results(self):
        """Clear all results"""
        for key in ["coder", "debugger", "tester", "optimizer", "reviewer"]:
            self.update_response(key, "Waiting...")
            self.update_state(key, "WAITING", 0)
    
    async def run_chain(self, task: str):
        """Run agent chain"""
        self.clear_results()
        self.update_state("coder", "WRITING", 20)
        self._log(f"TASK: {task[:200]}", "SYS")
        
        logger.log_structured("INFO", "tui", "Running chain", {"task": task[:100]})
        
        try:
            if self.core is None:
                from xli.core.chain import XliCore
                self.core = XliCore(self.env)
            result = await self.core.run_chain(
                task, 
                skip_questions=self.skip_questions,
                stream=self.streaming
            )
            
            # Update all panels
            self.update_response("coder", result.get("coder", "")[:300])
            self.update_state("coder", "DONE", 100)
            
            if result.get("debugger"):
                self.update_response("debugger", result["debugger"][:300])
                self.update_state("debugger", "DONE", 100)
            else:
                self.update_state("debugger", "SKIPPED", 100)
            
            if result.get("tester"):
                self.update_response("tester", result["tester"][:300])
                self.update_state("tester", "DONE", 100)
            else:
                self.update_state("tester", "SKIPPED", 100)
            
            if result.get("optimizer"):
                self.update_response("optimizer", result["optimizer"][:300])
                self.update_state("optimizer", "DONE", 100)
            else:
                self.update_state("optimizer", "SKIPPED", 100)
            
            if result.get("reviewer"):
                self.update_response("reviewer", result["reviewer"][:300])
                self.update_state("reviewer", "DONE", 100)
            else:
                self.update_state("reviewer", "SKIPPED", 100)
            
            self._log("TASK COMPLETE", "SYS")
            logger.log_structured("INFO", "tui", "Chain complete")
            
        except Exception as e:
            logger.log_error("tui", "Chain failed", exc=e)
            self._log(f"ERROR: {e}", "SYS")
            self.update_state("coder", "ERROR", 0)
    
    async def on_button_pressed(self, event: Button.Pressed):
        """Handle button press"""
        if event.button.id == "run-btn":
            task = self.task_input.value.strip()
            if task:
                self.task_input.value = ""
                await self.run_chain(task)
                self.set_focus(self.task_input)
    
    def on_input_submitted(self, event: Input.Submitted):
        """Handle input submit"""
        if event.input.id == "task-input":
            task = event.value.strip()
            if task:
                self.task_input.value = ""
                asyncio.create_task(self.run_chain(task))
                self.set_focus(self.task_input)
    
    def action_clear_log(self):
        """Clear log"""
        if self.log_widget:
            self.log_widget.children.clear()
            self._log("Log cleared", "SYS")
    
    def action_focus_input(self):
        """Focus input"""
        self.set_focus(self.task_input)
    
    def action_show_mcp(self):
        """Show MCP servers"""
        try:
            from xli.mcp.registry import get_registry
            registry = get_registry()
            servers = registry.list_all()
            
            self._log("=== MCP SERVERS ===", "SYS")
            for s in servers:
                status = "OK" if s["enabled"] else "NO"
                self._log(f"{status} {s['name']}: {s['description'][:40]}", "MCP")
                
        except Exception as e:
            logger.log_error("tui", "MCP list failed", exc=e)
            self._log(f"MCP error: {e}", "SYS")
    
    def action_skip_questions(self):
        """Toggle skip questions"""
        self.skip_questions = not self.skip_questions
        self._log(f"Questions {'OFF' if self.skip_questions else 'ON'}", "SYS")
    
    def action_toggle_stream(self):
        """Toggle streaming"""
        self.streaming = not self.streaming
        self._log(f"Streaming {'ON' if self.streaming else 'OFF'}", "SYS")
    
    # XliUI implementations
    def display(self, text: str, title: Optional[str] = None):
        console = Console()
        if title:
            console.print(Panel(text, title=title))
        else:
            console.print(text)
    
    async def input(self, prompt: str, default: str = "") -> str:
        # Would need async input dialog
        return default
    
    def notify(self, msg: str, level: str = "info"):
        self._log(f"[{level}] {msg}", "NOTIFY")
    
    def open_buffer(self, content: list, name: str = "XLI", filetype: str = "markdown"):
        # Create new buffer in TUI
        pass
    
    def progress(self, percent: int, message: str = ""):
        self.status_bar.update(f"{message} {percent}%")
    
    def clear(self):
        self.clear_results()
    
    def choice(self, question: str, options: list, default=None) -> int:
        # Would need modal dialog
        return default or 0
    
    def confirm(self, question: str) -> bool:
        # Would need modal dialog
        return True



# ─── XliUI Adapter for TUI ───

class XliUIAdapter:
    """Adapter to provide XliUI interface without metaclass conflict"""

    def __init__(self):
        self.logger = StructuredLogger("xli.ui.adapter")

    def display(self, text: str, title: Optional[str] = None):
        console = Console()
        if title:
            console.print(Panel(text, title=title))
        else:
            console.print(text)

    async def input(self, prompt: str, default: str = "") -> str:
        return default

    def notify(self, msg: str, level: str = "info"):
        print(f"[{level}] {msg}")

    def open_buffer(self, content: list, name: str = "XLI", filetype: str = "markdown"):
        pass

    def progress(self, percent: int, message: str = ""):
        print(f"\r{message} {percent}%", end="", flush=True)

    def clear(self):
        print("\n" * 50)

    def choice(self, question: str, options: list, default=None) -> int:
        return default or 0

    def confirm(self, question: str) -> bool:
        return True
