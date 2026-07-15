# Ambient Codex Agent Instructions

This repository is a standalone Codex-native fork of the Ambient plugin work.

## Isolation

- Treat any Claude Ambient install as out of scope for normal development,
  testing, and runtime behavior.
- Do not inspect, source, invoke, import, or route through the Claude Ambient
  skill tree unless the user explicitly asks for a historical comparison.
- Do not write, format, reset, clean, or commit anything in the Claude skill tree.
- All implementation belongs under this repository unless the user explicitly changes scope.

## Quality Bar

- Prefer architecture fixes over compatibility shims that hide bad boundaries.
- Keep the plugin installable through `.codex-plugin/plugin.json`, `skills/`, `hooks/hooks.json`, and `.mcp.json`.
- Use `${PLUGIN_ROOT}` and `${PLUGIN_DATA}` in Codex plugin files.
- Do not add Claude compatibility fallbacks. Ambient Codex is a native rebuild.
- Keep runtime code stdlib-only unless a phase explicitly accepts a dependency after evaluation.

## Validation Gates

Run these from `plugins/ambient-codex` before marking implementation phases complete:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" .
python3 -m unittest discover -s tests -t . -q
```

For skill changes, also run the Codex skill validator from
`$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py` when available.
