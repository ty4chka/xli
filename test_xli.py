#!/usr/bin/env python3
"""
XLI v4.3 Comprehensive Test Suite
Tests: imports, tools, sub-agents, file creation, error handling, skills, MCP
"""

import sys
import asyncio
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path.home() / "xli"))

GREEN = "[92m"
RED = "[91m"
YELLOW = "[93m"
BLUE = "[94m"
RESET = "[0m"
BOLD = "[1m"

passed = 0
failed = 0
tests_run = 0

def test(name, func):
    global passed, failed, tests_run
    tests_run += 1
    try:
        result = func()
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        if result:
            print(GREEN + "PASS" + RESET + " " + name)
            passed += 1
        else:
            print(YELLOW + "WARN" + RESET + " " + name + " — returned False")
            failed += 1
    except Exception as e:
        print(RED + "FAIL" + RESET + " " + name + " — " + type(e).__name__ + ": " + str(e))
        failed += 1

def section(title):
    print()
    print(BLUE + BOLD + "=" * 60 + RESET)
    print(BLUE + BOLD + "  " + title + RESET)
    print(BLUE + BOLD + "=" * 60 + RESET)

# SECTION 1: Core Imports
section("1. CORE IMPORTS")

test("Import config", lambda: __import__("xli.core.config") is not None)
test("Import logger", lambda: __import__("xli.core.logger") is not None)
test("Import env", lambda: __import__("xli.core.env") is not None)
test("Import skills", lambda: __import__("xli.core.skills") is not None)
test("Import memory", lambda: __import__("xli.core.memory") is not None)

# SECTION 2: Providers
section("2. PROVIDERS")

def test_provider_imports():
    from xli.providers.base import AbstractProvider
    from xli.providers.mistral import MistralProvider
    return AbstractProvider is not None and MistralProvider is not None

test("Provider imports", test_provider_imports)

def test_mistral_resilience():
    from xli.providers.mistral import MistralProvider
    import inspect
    source = inspect.getsource(MistralProvider.chat)
    return "[ERROR:" in source and "max_retries" in source

test("Mistral 429 resilience", test_mistral_resilience)

# SECTION 3: Multi-Step Agent
section("3. MULTI-STEP AGENT")

def test_multistep_imports():
    from xli.core.multistep import MultiStepAgent, Tool, BashTool, FileWriteTool, TaskTool
    return all([MultiStepAgent, Tool, BashTool, FileWriteTool, TaskTool])

test("Multistep imports", test_multistep_imports)

def test_tool_registration():
    from xli.core.multistep import MultiStepAgent
    agent = MultiStepAgent("test", "test", max_steps=3)
    tools = list(agent.tools.keys())
    return all(t in tools for t in ["bash", "read", "write", "edit", "task"])

test("Tool registration", test_tool_registration)

async def test_file_write_tool():
    from xli.core.multistep import FileWriteTool
    tool = FileWriteTool()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.txt")
        await tool.execute(path=path, content="hello world")
        return os.path.exists(path) and "hello world" in Path(path).read_text()

test("FileWriteTool creates files", test_file_write_tool)

async def test_bash_tool_brace():
    from xli.core.multistep import BashTool
    tool = BashTool()
    with tempfile.TemporaryDirectory() as tmp:
        await tool.execute(command="mkdir -p " + tmp + "/dir/{a,b,c}")
        return (os.path.exists(tmp + "/dir/a") and 
                os.path.exists(tmp + "/dir/b") and 
                os.path.exists(tmp + "/dir/c"))

test("BashTool brace expansion", test_bash_tool_brace)

async def test_subagent_inherits():
    from xli.core.multistep import MultiStepAgent
    parent = MultiStepAgent("parent", "parent", max_steps=2)
    task_tool = parent.tools.get("task")
    if not task_tool:
        return False
    return len(task_tool.parent_tools) > 0 and "write" in task_tool.parent_tools

test("Sub-agent inherits tools", test_subagent_inherits)

# SECTION 4: Chain
section("4. CHAIN")

def test_chain_imports():
    from xli.core.chain import XliCore, ChainResult
    return XliCore is not None and ChainResult is not None

test("Chain imports", test_chain_imports)

def test_chain_agents():
    from xli.core.chain import XliCore
    from xli.core.env import EnvironmentAdapter
    core = XliCore(EnvironmentAdapter())
    expected = ["PLANNER", "CODER", "DEBUGGER", "TESTER", "OPTIMIZER", "REVIEWER"]
    return all(a in core.agents for a in expected)

test("Chain agents present", test_chain_agents)

def test_chain_error_routing():
    from xli.core.chain import XliCore
    import inspect
    source = inspect.getsource(XliCore)
    return "_has_errors" in source and "errors" in source

test("Chain error routing", test_chain_error_routing)

# SECTION 5: UI
section("5. UI")

def test_ui_imports():
    try:
        from xli.ui.base import XliUI
        from xli.ui.headless import HeadlessUI
        return True
    except ImportError:
        return False

test("UI imports", test_ui_imports)

def test_headless_multistep():
    from xli.ui.headless import HeadlessUI
    import inspect
    source = inspect.getsource(HeadlessUI)
    return "multistep" in source and "run_multistep" in source

test("Headless multistep support", test_headless_multistep)

# SECTION 6: Skills
section("6. SKILLS")

def test_skills_manager():
    from xli.core.skills import get_skills_manager
    sm = get_skills_manager()
    return sm is not None

test("Skills manager", test_skills_manager)

def test_skills_injection():
    from xli.core.multistep import MultiStepAgent
    agent = MultiStepAgent("test", "test", max_steps=2)
    prompt = agent._build_system_prompt()
    return "SKILLS" in prompt.upper() or "skills" in prompt.lower()

test("Skills injection", test_skills_injection)

# SECTION 7: MCP
section("7. MCP")

def test_mcp_imports():
    try:
        from xli.mcp.client import MCPClient
        from xli.mcp.registry import get_registry
        return True
    except ImportError:
        return False

test("MCP imports", test_mcp_imports)

def test_mcp_in_chain():
    from xli.core.chain import XliAgent
    import inspect
    source = inspect.getsource(XliAgent._get_mcp_tools_prompt)
    return "shell_helper" in source or "lsp" in source

test("MCP tools in chain", test_mcp_in_chain)

# SECTION 8: Integration
section("8. INTEGRATION")

async def test_e2e_file_creation():
    from xli.core.multistep import MultiStepAgent
    with tempfile.TemporaryDirectory() as tmp:
        agent = MultiStepAgent("test", "test", max_steps=4)
        task = "Create a file at " + tmp + "/hello.txt with content 'Hello XLI'"
        await agent.run(task)
        return os.path.exists(tmp + "/hello.txt")

test("End-to-end file creation", test_e2e_file_creation)

async def test_error_recovery():
    from xli.core.multistep import MultiStepAgent
    agent = MultiStepAgent("test", "test", max_steps=3)
    task = "Read file /nonexistent/path/xyz.txt then create /tmp/test_recovery.txt with 'recovered'"
    await agent.run(task)
    return len(agent.steps) > 0

test("Error recovery", test_error_recovery)

# SUMMARY
print()
print(BLUE + BOLD + "=" * 60 + RESET)
print(BOLD + "  RESULTS: " + str(passed) + " passed, " + str(failed) + " failed, " + str(tests_run) + " total" + RESET)
print(BLUE + BOLD + "=" * 60 + RESET)

if failed == 0:
    print()
    print(GREEN + BOLD + "ALL TESTS PASSED!" + RESET)
else:
    print()
    print(RED + BOLD + str(failed) + " test(s) failed" + RESET)

sys.exit(0 if failed == 0 else 1)
