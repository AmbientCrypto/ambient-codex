# Releasing Ambient Codex

This repository publishes a Codex plugin from `plugins/ambient-codex` and a
local marketplace from `.agents/plugins/marketplace.json`.

## Version Bump

Update all version surfaces together:

- `.codex-plugin/plugin.json`
- `pyproject.toml`
- `bin/ambient` `__version__`
- `mcp/ambient_mcp.py` `SERVER_VERSION`
- `CHANGELOG.md`

## Gates

From `plugins/ambient-codex`:

```bash
python3 -m py_compile bin/ambient mcp/ambient_mcp.py
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" .
python3 "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py" skills/ambient
python3 -m unittest discover -s tests -t . -q
bash -n hooks/session-start.sh
```

Optional live batteries, run from the repository root:

```bash
cd ../..
bash tools/stress_test.sh
bash tools/model_matrix.sh
```

## Isolation Check

Before release, scan this repository's Codex-facing plugin surfaces for accidental
PATH-first or compatibility fallback routing:

```bash
python3 -m unittest tests.test_codex_native_isolation -q
```

Expected result is green. Ambient Codex should route through its bundled binary
or MCP server, not through another local Ambient install.

## Privacy And Secret Scan

```bash
python3 -m unittest tests.test_release_readiness tests.test_refactor_phase2_secrets -q
cd ../..
AMB_NO_LIVE=1 bash tools/stress_test.sh
```

The repository history must use GitHub noreply commit addresses. Public Markdown
must contain no personal home path, private email, phone number, security code,
or live credential. Credential-shaped values are allowed only as clearly fake
security fixtures covered by the tests above.

## Source Archive Hygiene

The release is source-only: no compiled executable, vendored dependency, default
hook, local state, key file, cache, or symlink belongs in the archive.

```bash
tmp="$(mktemp -d)"
git archive HEAD | tar -x -C "$tmp"
find "$tmp" -type l -print
find "$tmp" -type f -perm -111 -print
rm -rf "$tmp"
```

The executable list should contain only the documented Python/shell entrypoints.
CodeQL, the full immutable-action CI matrix, and branch protection must be green
before tagging.

## Install Verification

Public repository install:

```bash
codex plugin marketplace add AmbientCrypto/ambient-codex
codex plugin add ambient-codex@ambient-codex
```

Local checkout install, run from the repository root:

```bash
codex plugin marketplace add "$PWD"
codex plugin add ambient-codex@ambient-codex
```

The local marketplace file is:

```text
.agents/plugins/marketplace.json
```

Verify that Codex sees the plugin, the `$ambient` skill loads, `.mcp.json` starts
the MCP server with `python3 -u`, `codex mcp get ambient` shows the installed
cache version, and `ambient_self_test` succeeds. A public install must not
register default lifecycle hooks or require hook trust review.

## Uninstall Support Notes

Plugin uninstall removes plugin files only. User data is separate:

```bash
./bin/ambient control key remove
./bin/ambient cache clear
./bin/ambient link --remove
rm -rf ~/.config/ambient-codex
```
