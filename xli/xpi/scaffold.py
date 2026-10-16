"""Create a reviewable, disabled-by-default plugin without executing its code."""
import json
from pathlib import Path

from xli.xpi.manager import XpiManager

SOURCE = '''from xli.xpi import XpiPlugin, Param, ToolResult, tool


class Plugin(XpiPlugin):
    def on_load(self, context):
        self.prefix = self.context.config.get("greeting", "Hello")

    def tools(self):
        @tool("hello", "Greet someone", [Param("name", required=True)])
        async def hello(name):
            # Return data; never print into the host's terminal.
            return ToolResult.success({"message": f"{self.prefix}, {name}!"})
        return [hello]

    def on_agent_end(self, context):
        count = self.context.get_state("runs", 0)
        self.context.set_state("runs", count + 1)

    def on_unload(self, context):
        # Release resources you acquired in on_load (files, clients, etc.).
        pass
'''


def create_plugin(root: Path, name: str, *, author: str = "", license: str = "MIT") -> Path:
    manifest = {
        "name": name, "version": "0.1.0", "api_version": 1,
        "author": author, "license": license, "description": "An XLI plugin",
        "homepage": "", "main": "plugin.py", "entry": "Plugin",
        "platform": "all", "enabled": False, "config": {"greeting": "Hello"},
    }
    # Validate before joining an untrusted name into a filesystem path.
    if not name or Path(name).name != name:
        raise ValueError("name must be a simple plugin identifier")
    directory = root / name
    XpiManager.validate_manifest(directory, manifest)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (directory / "plugin.py").write_text(SOURCE, encoding="utf-8")
    (directory / "README.md").write_text(
        f"# {name}\n\nAuthor: {author or '(set author in manifest.json)'}\n\n"
        f"License declaration: {license}. Add the corresponding LICENSE text before publishing.\n\n"
        f"Review plugin.py, then run `xli plugins enable {name}`.\n"
        f"The agent can call `{name}.hello` with `name` as a string.\n\n"
        "SDK reference: `xli guides read plugins`. Trusted Python, not sandboxed.\n",
        encoding="utf-8",
    )
    return directory
