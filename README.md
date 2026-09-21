# XLI

An autonomous coding agent with a JSON-RPC kernel, an optional Cython-compiled
core, and three frontends: a CLI, a full-screen TUI, and a Neovim plugin.

```
xli fix the failing tests in ./api and show me what changed
```

---

## Why the shape it has

Everything is built around one decision: **the agent is a server, the interfaces
are clients.**

```
  ┌──────────┐  ┌──────────┐  ┌──────────────┐
  │   CLI    │  │   TUI    │  │ Neovim (Lua) │      ← presentation only
  └────┬─────┘  └────┬─────┘  └──────┬───────┘
       │             │               │
       │      JSON-RPC 2.0 / ndjson  │
       └─────────────┼───────────────┘
                     ▼
            ┌─────────────────┐
            │   xli kernel    │   agent loop · tools · permissions
            │  (serve/stdio)  │   sessions · config · Cython core
            └─────────────────┘
```

That is what makes a Go, Rust, or web frontend possible later without rewriting
the agent: they speak the same bytes the Neovim plugin already speaks. Run
`xli serve` and talk to it.

---

## Install

```bash
pip install -e .
xli doctor          # what is present, what is missing
```

Python 3.10+. `httpx` for the providers, `Cython` + a C compiler only if you
want the compiled kernel.

---

## Commands

| Command | What it does |
|---|---|
| `xli <task>` | run one task and exit |
| `xli repl` | interactive line interface |
| `xli tui` | full-screen interface |
| `xli serve` | JSON-RPC kernel (`--unix` for a socket, stdio by default) |
| `xli config` | `list` · `get` · `set` · `path` |
| `xli kernel` | `preflight` · `build` · `status` · `clean` |
| `xli tools` | `list` · `schema` · `prompt` · `enable` · `disable` |
| `xli session` | `list` · `show` · `delete` |
| `xli plugins` | `list` · `enable` · `disable` · `reload` · `state` · `dispatch` |
| `xli skills` | list skill definitions |
| `xli mcp` | list MCP servers |
| `xli nvim` | install the Neovim plugin |
| `xli doctor` | environment report |

Exit codes are meaningful: `0` success, `1` the task failed, `2` bad usage,
`3` the environment is broken. Every listing command takes `--json`.

---

## Permissions

The agent never decides for itself whether it may touch your machine.

| Mode | Behaviour |
|---|---|
| `auto` | everything runs except what an explicit deny rule blocks |
| `confirm` | mutations ask first (the default) |
| `readonly` | mutations refused outright |

```bash
xli config set permissions.mode auto
xli config set permissions.deny '/etc/*,*.env'
xli run --deny 'rm -rf *' "clean up build output"
```

Some commands are refused in every mode — `mkfs`, `dd of=/dev/...`, `shutdown`,
fork bombs. Others (`sudo`, `curl … | bash`, `git push --force`) always ask.

The policy is a pure decision function: it returns a verdict and the frontend
decides how to ask a human. The same policy runs headless in CI and
interactively in a terminal.

---

## The Cython kernel

`xli/core` can be compiled to C extensions. It is optional and it is safe:

```bash
xli kernel preflight   # is the toolchain actually there?
xli kernel build       # compile xli/core into xli/_ckernel/
xli kernel status      # what is compiled, what is stale
xli kernel clean       # throw it all away
```

A compiled module is used **only** when its recorded source hash still matches
the `.py` file *and* it was built for this interpreter. Edit a file and the
stale `.so` is ignored; switch Python versions and the whole kernel is ignored.
"Built" can never mean "running yesterday's logic".

`preflight` exists because the usual failure is a missing `Python.h`, and the
gcc message for that is useless unless you already know what it means:

```
$ xli kernel preflight
  [     ok] cython: Cython 3.3.0
  [     ok] compiler: /usr/bin/cc
  [MISSING] python-headers: Python.h not found in /usr/include/python3.11
            fix: sudo apt-get install -y python3.11-dev
  [     ok] setuptools: setuptools 66.1.1
```

Without the kernel everything still works, just in pure Python.

---

## Configuration

Dotted keys, layered: defaults → `~/.xli/config.json` → `./.xli/config.json` →
`XLI_*` environment.

```bash
xli config set provider openai
xli config set provider.model gpt-4o-mini
xli config set agent.max_steps 40
xli config list
```

Values are validated on write, so a typo fails immediately:

```
$ xli config set permissions.mode autos
permissions.mode: 'autos' is not one of auto, confirm, readonly
```

In the environment, a double underscore is the section separator
(`XLI_PERMISSIONS__MODE=auto`), because a single underscore cannot be told apart
from part of a key name.

---

## Neovim

With a plugin manager, point at the shipped plugin directory:

```lua
{ dir = "/path/to/xli/xli/nvim/plugin_root",
  config = function() require('xli').setup() end }
```

Without one:

```bash
xli nvim install
```

Then:

| Command | |
|---|---|
| `:Xli <task>` | run a task in a floating window |
| `:XliSelection` | send the visual selection |
| `:XliDiagnostics` | send this line's diagnostics |
| `:XliTools` `:XliStatus` `:XliKernel` `:XliClose` | |

The plugin is pure Lua — no Python at runtime. It spawns `xli serve --unix` on
demand and talks JSON-RPC to it, so a Neovim crash does not lose the agent's
work.

---

## Internal plugins (XPI)

XPI is xli's in-process plugin system — distinct from MCP servers (separate
processes) and skills (markdown guidance). Drop a package into
`~/.xli/xpi/<name>/` and the agent calls into it as it works.

```
~/.xli/xpi/audit-trail/
  manifest.json   {"name":"audit-trail","version":"1.0.0","main":"plugin.py"}
  plugin.py       class AuditTrail(XpiPlugin): ...
```

```python
from xli.xpi.base import XpiPlugin

class AuditTrail(XpiPlugin):
    def on_tool_call(self, context):      # {name, args}
        ...
    def on_agent_end(self, context):      # {ok, summary, steps, stopped_reason}
        ...
```

Hooks: `on_load`, `on_unload`, `on_agent_start`, `on_tool_call`,
`on_tool_result`, `on_agent_end`, `on_tui_mount`, `on_nvim_attach`,
`on_headless_start`. Set `"platform": "nvim"` in the manifest to restrict a
plugin to one frontend.

```bash
xli plugins list
xli plugins disable audit-trail
xli plugins reload audit-trail      # hot reload
xli plugins state                   # state shared across frontends
```

A plugin that raises is caught, logged and reported — it will not stop the
agent, and the other plugins still run. `xli.xpi.state.XpiState` gives plugins
one persisted key/value store shared by the TUI, Neovim and headless runs.

---

## The wire protocol

Newline-delimited JSON-RPC 2.0. One object per line, UTF-8.

```jsonc
// request
{"jsonrpc":"2.0","id":1,"method":"agent.run","params":{"task":"fix the tests"}}
// notification, streamed while it works
{"jsonrpc":"2.0","method":"agent.tool_call","params":{"name":"read","args":{...}}}
// response
{"jsonrpc":"2.0","id":1,"result":{"ok":true,"stopped_reason":"done",...}}
```

Start with `hello` and you are told whether your protocol version matches.
`rpc.methods` lists everything (`agent.*`, `tools.*`, `config.*`, `kernel.*`, `session.*`, `plugins.*`, `doctor`). See `xli/kernel/methods.py` for the map and
`xli/kernel/protocol.py` for the error codes.

---

## Development

```bash
pytest -q            # 395 tests
ruff check xli tests
```

The TUI is testable because layout is pure: `xli/tui/widgets.py` returns rows
of `(text, style)` spans and knows nothing about curses, which is the thin
adapter in `xli/tui/app.py`.

## Layout

```
xli/kernel/     JSON-RPC protocol, server, transports, method map
xli/agent.py    the model/tool loop
xli/tools/      tool registry and the built-in tools
xli/permissions/ policy engine
xli/parse/      model output -> tool calls, with repair
xli/session/    append-only conversation history
xli/manager/    config and the Cython build
xli/accel.py    import hook for the compiled kernel
xli/tui/        full-screen interface
xli/xpi/        internal plugin system (in-process, lifecycle hooks)
xli/nvim/       plugin installer + the Lua plugin it ships
xli/cli.py      command line
```
