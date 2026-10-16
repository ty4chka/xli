# Design references and authorship

XLI project author: **Aubeig** (see `pyproject.toml`). Plugin author metadata is
separate from the host application's authorship; declarations are not signatures.

For this terminal/plugin update, OpenCode was inspected as an architectural
reference, not vendored or translated:

- Repository: https://github.com/anomalyco/opencode
- License reported by the repository: MIT
- Inspected `dev` revision: `34d8ded0c58dd66808c1d60e081e8c66da12a896`
- Plugin setup/options/disposable registration documentation:
  https://github.com/anomalyco/opencode/blob/34d8ded0c58dd66808c1d60e081e8c66da12a896/packages/plugin/src/README.md
- Separate terminal frontend:
  https://github.com/anomalyco/opencode/tree/34d8ded0c58dd66808c1d60e081e8c66da12a896/packages/tui/src

Concepts used as reference: separating terminal presentation from the host,
explicit extension lifecycle/cleanup, plugin configuration and scoped tool
contributions. The implementation here is Python/curses using XLI's existing
registry and event pipeline. No OpenCode source code, fonts, logos, or other
assets were copied into this change. Future code reuse must preserve applicable
copyright and license notices; an MIT label alone is not attribution.

Maintained plugin authoring guide: `xli/guides/plugins.md`.
Terminal diagrams and usage: `xli/guides/visuals.md`.
