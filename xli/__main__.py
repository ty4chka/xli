#!/usr/bin/env python3
"""
XLI v5.1 - Multi-Agent AI Coding Assistant — FIXED: multistep flag, better error handling
"""

import asyncio
import argparse
import sys
from pathlib import Path

xli_root = Path(__file__).parent.parent
if str(xli_root) not in sys.path:
    sys.path.insert(0, str(xli_root))

from xli.core.logger import StructuredLogger, print_banner, COLORS  # FIXED: added COLORS
from xli.core.config import get_config
from xli.core.plan_build import get_mode_switcher, Mode
from xli.core.context_scout import init_project
from xli.providers.unified import list_providers, create_provider

logger = StructuredLogger("xli.main")


def parse_args():
    parser = argparse.ArgumentParser(description="FIRE XLI PRO v5.1")
    parser.add_argument("--mode", choices=["tui", "nvim", "headless", "multistep"], 
                       default="auto", help="UI mode")
    parser.add_argument("--task", type=str, default="", help="Task to execute")
    parser.add_argument("--debug-file", type=str, help="Debug specific file")
    parser.add_argument("--skip-questions", action="store_true", 
                       help="Skip clarification questions")
    parser.add_argument("--notify", action="store_true", 
                       help="Send notification on completion")
    parser.add_argument("--output-format", choices=["text", "json", "vim"],
                       default="text", help="Output format")
    parser.add_argument("--mcp", type=str, help="Enable specific MCP servers")
    parser.add_argument("--provider", type=str, default="mistral",
                       help=f"LLM provider ({', '.join(list_providers())})")
    parser.add_argument("--model", type=str, help="Model name")
    parser.add_argument("--plan", action="store_true", help="Start in plan mode")
    parser.add_argument("--build", action="store_true", help="Start in build mode")
    parser.add_argument("--init", action="store_true", help="Initialize AGENTS.md for project")
    parser.add_argument("--team", type=str, default="default", help="Team name for inbox")
    parser.add_argument("--multistep", action="store_true", help="Use multi-step agent with sub-agents (headless mode)")
    parser.add_argument("--max-steps", type=int, default=12, help="Max steps for multi-step agent")
    return parser.parse_args()


async def main():
    args = parse_args()
    config = get_config()

    if args.init:
        print("Scanning project...")
        path = init_project()
        print(f"Created {path}")
        return

    print_banner()

    if args.provider:
        logger.log_structured("INFO", "main", f"Using provider: {args.provider}")
        try:
            provider = create_provider(args.provider, model=args.model or None)
            import xli.providers.base as base_module
            base_module._provider_instance = provider
        except Exception as e:
            logger.log_error("main", f"Failed to create provider {args.provider}", exc=e)
            print(f"Provider error: {e}")
            return

    mode_switcher = get_mode_switcher()
    if args.build:
        mode_switcher.switch(Mode.BUILD)
    elif args.plan:
        mode_switcher.switch(Mode.PLAN)

    print(f"  {mode_switcher.get_status_line()}")

    mode = args.mode
    if mode == "auto":
        mode = config.get_mode()

    logger.log_structured("INFO", "main", f"Starting {mode} mode")

    if mode == "multistep" or args.multistep:
        from xli.core.multistep import MultiStepAgent
        agent = MultiStepAgent(
            name="main",
            system_prompt="""You are an expert developer. Use tools to accomplish tasks.
When you need specialized help, delegate to sub-agents using the task tool.
Available sub-agents:
- @coder: writes code
- @tester: writes tests  
- @debugger: fixes bugs
- @optimizer: optimizes code
- @reviewer: reviews code

Be efficient. Use at most 2-3 sub-agents per task. Combine results.""",
            max_steps=args.max_steps
        )

        print(f"\n{COLORS['CYAN']}🤖 MULTI-STEP MODE (max {args.max_steps} steps){COLORS['RESET']}\n")

        try:
            result = await agent.run(args.task)
            print(f"\n{'='*60}")
            print("RESULT:")
            print(result)
            print(f"\n{'='*60}")
            print(agent.get_steps_summary())
        except Exception as e:
            logger.log_error("main", "Multi-step failed", exc=e)
            print(f"Fatal error: {e}")
        return

    if mode == "tui":
        try:
            from xli.ui.tui import XliTui
            app = XliTui()
            await app.run_async()
        except ImportError as e:
            logger.log_error("main", f"TUI not available: {e}")
            print(f"TUI not available: {e}")
            mode = "headless"

    if mode == "nvim":
        try:
            from xli.ui.nvim import create_nvim_ui
            ui = create_nvim_ui()
            if ui.is_available():
                print("Neovim UI ready.")
                while True:
                    await asyncio.sleep(1)
            else:
                mode = "headless"
        except ImportError as e:
            mode = "headless"

    if mode == "headless":
        from xli.ui.headless import run_headless
        await run_headless(args)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[XLI] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.log_error("main", "Fatal error", exc=e)
        print(f"Fatal error: {e}")
        sys.exit(1)
