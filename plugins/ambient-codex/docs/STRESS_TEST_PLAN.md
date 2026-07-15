# Ambient Codex QA Batteries

Run these after changes to `bin/ambient`, `mcp/ambient_mcp.py`, hooks, or plugin
metadata.

## Hermetic Gates

```bash
python3 -m py_compile bin/ambient mcp/ambient_mcp.py
python3 -m unittest discover -s tests -t . -q
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" .
python3 "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py" skills/ambient
bash -n hooks/session-start.sh
```

## Live Batteries

These may spend Ambient credit.

```bash
cd ../..
bash tools/stress_test.sh
AMB_NO_LIVE=1 bash tools/stress_test.sh
bash tools/model_matrix.sh
```

## Coverage Expectations

- CLI help, exit codes, setup prevalidation, key redaction, config parsing.
- Model catalog handling, curation, fallback, and sacred model behavior.
- Token sizing, chunking, map-reduce, partial coverage reporting.
- Audit, ask, code, build, map, consensus, best-of, cache, and usage surfaces.
- MCP initialize, tool listing, tool calls, validation, command boundaries, and
  redaction.
- Session hook reminders and launcher self-heal isolation.
- Windows launcher shim ownership.

## Live Assertions

READY models should complete bounded ask/audit/code checks. Non-serving models
should fail with a classified model diagnosis, not a traceback or hang. Live logs
must pass the key-leak tripwire.
