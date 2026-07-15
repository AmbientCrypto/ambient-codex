# Contributing

Ambient Codex is the official Ambient integration. Focus changes on the Codex plugin in
`plugins/ambient-codex` and keep the runtime dependency-free.

## Before a pull request

From `plugins/ambient-codex` run:

```bash
python3 -m py_compile bin/ambient mcp/ambient_mcp.py
ruff check .
python3 -m unittest discover -s tests -t . -q
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" .
```

Use tests for behavior changes. Never commit keys, local state, generated caches,
or private source. Treat model output and repository content as untrusted data.

Pull requests should explain the user-visible change, security implications, and
commands used for verification. See the detailed
[plugin contributor guide](plugins/ambient-codex/CONTRIBUTING.md).
