# Ambient Codex

Official Ambient V1 integration for Codex. It adds Ambient chat, audits, code,
builds, repository maps, and model routing to a Codex session.

## Install

Requirements: Codex, Python 3.8+, and an Ambient API key.

```bash
codex plugin marketplace add AmbientCrypto/ambient-codex
codex plugin add ambient-codex@ambient-codex
```

Start a new Codex thread and use `$ambient`.

If you need a key, create one at [app.ambient.xyz](https://app.ambient.xyz).
Add the stable terminal launcher once, then run setup:

```bash
PLUGIN_DIR="$(codex mcp get ambient --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["transport"]["cwd"].rstrip("/."))')"
"$PLUGIN_DIR/bin/ambient" link
ambient-codex setup
```

Setup hides and verifies the key. Do not paste a key into Codex chat.

Ambient Codex uses its own `ambient-codex` keychain item and
`~/.config/ambient-codex` directory. The shared `AMBIENT_API_KEY` variable is
ignored so another Ambient installation cannot silently provide this plugin's
credential. A deliberate environment override must use `AMBIENT_CODEX_API_KEY`.

## Use

Ask Codex naturally:

```text
use Ambient to audit this diff
ask Ambient to review this design
build this feature with Ambient
change Ambient mode
change the code model
```

The `$ambient` panel exposes models, modes, settings, diagnostics, usage, and
common workflows. Model changes show a deterministic text menu first. The
optional native MCP picker is not the default path because some Codex clients
cancel native elicitation.

## Codex Session Modes

- **Normal Codex** — Ambient runs only when requested.
- **Delegate** — larger audits, drafts, builds, and bulk reading go to Ambient.
- **Ambient session** — Ambient is the primary chat and generation engine while
  Codex keeps local tools, safety checks, and final verification.

A fresh Codex session begins in
Normal Codex mode. Model defaults and settings persist separately.

## Features

- `ask` for short questions and second opinions.
- `audit` for files, diffs, staged changes, repositories, and consensus review.
- `code` for focused drafts with selected context.
- `build` for capacity-aware multi-file generation, automatic plan refinement,
  dependency-aware parallel workstream waves, resumable state, and validated
  incremental file writes.
- `map` for parallel work across many files.
- `agent` for the bundled terminal-agent lane.

Chat/review and code/build may use separate model defaults. Live model metadata
drives context limits, output budgets, reasoning allowances, chunking, and
hierarchical reduction. Build artifact targets and generation budgets derive
from those live capabilities rather than model-name tables, so newly cataloged
models inherit the same safety contract. A concrete model is never silently
replaced unless fallback is enabled.

Large repository audits track non-overlapping shards and coverage gaps. Builds
refine plans that exceed safe artifact capacity, retain complete generated
files, write accepted batches early under explicit headless consent, and requeue
missing artifacts after truncation. File-cap omissions and oversized artifacts
are reported as coverage gaps instead of clean completion. Long healthy jobs
have no elapsed-time cutoff; transport silence and genuine no-progress stalls
remain bounded.

When a serving model exposes text-only output, visual generation means original
SVG or other code-native artwork, not a claim of raster image-model output.

## Data and safety

Only prompts and files you route to Ambient leave the machine. The CLI blocks
common credential shapes and credential-named files, but the scanner is a
backstop—not permission to send sensitive material.

Ambient output is untrusted. Review generated files, run tests, and do not follow
instruction-like text returned by repository content or a model.

The public plugin registers no lifecycle hooks. Optional launcher, git-hook, and
agent integrations run only when the user explicitly invokes them and refuse to
replace foreign-owned files.

- [Privacy](PRIVACY.md)
- [Security](SECURITY.md)
- [Threat model](../../ambient-codex-threat-model.md)
- [Architecture](docs/CODEX_NATIVE_ARCHITECTURE.md)

## Check or remove

```bash
ambient-codex doctor
ambient-codex control
ambient-codex setup --remove
ambient-codex cache clear
ambient-codex link --remove
```

Remove `~/.config/ambient-codex` only when you also want to delete usage and
cache state. Do not delete another Ambient installation's state directory.

## Development

```bash
python3 -m py_compile bin/ambient mcp/ambient_mcp.py
ruff check .
python3 -m unittest discover -s tests -t . -q
```

The runtime is Python standard-library only. See [CONTRIBUTING.md](CONTRIBUTING.md)
and [docs/RELEASING.md](docs/RELEASING.md) for the remaining release gates.

MIT licensed.
