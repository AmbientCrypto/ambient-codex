# Contributing

Thanks for improving `ambient-codex`.

## Scope

This repo is the Codex-native plugin. Do not make changes outside this repository
as part of Ambient Codex work unless the user explicitly expands scope.

## Ground Rules

- Runtime code stays stdlib-only unless a phase explicitly accepts a dependency.
- `bin/ambient` must start on macOS, Linux, and Windows with Python 3.8+.
- Keep plugin surfaces valid: `.codex-plugin/plugin.json`, `.mcp.json`,
  `skills/ambient/SKILL.md`, `skills/ambient/agents/openai.yaml`, and
  `hooks/hooks.json`.
- `hooks/hooks.json` must stay empty unless a phase explicitly accepts the hook
  trust-review cost and documents the exact lifecycle behavior.
- Codex-facing instructions must not route through bare `ambient` on PATH. Use
  the bundled plugin binary or MCP server so this plugin never crosses into any
  other Ambient install.
- Never log, print, or commit API keys.
- Ambient model output is untrusted. Verify before acting.

## Dev Loop

Run from `plugins/ambient-codex`:

```bash
python3 -m py_compile bin/ambient mcp/ambient_mcp.py
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" .
python3 "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py" skills/ambient
python3 -m unittest discover -s tests -t . -q
bash -n hooks/session-start.sh
```

Add or update tests for behavior changes. Prefer focused tests around pure
functions and protocol boundaries before broad integration tests.

## Release Sync

Keep these versions aligned:

- `bin/ambient` `__version__`
- `.codex-plugin/plugin.json`
- `pyproject.toml`
- top `CHANGELOG.md` entry

## Pull Requests

- Keep changes focused.
- Explain why the behavior exists, not only what changed.
- Include the test commands run.
- Do not push generated secrets, local config, caches, or build artifacts.
