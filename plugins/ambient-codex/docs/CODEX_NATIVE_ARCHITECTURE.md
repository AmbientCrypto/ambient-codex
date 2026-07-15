# Codex-Native Architecture

Ambient Codex is a standalone Codex plugin. A user must be able to install and
use it with Codex and an Ambient API key only. It must not depend on a Claude
install, Claude plugin files, Claude environment variables, or Claude runtime
behavior.

## Architecture Contract

Ambient Codex is intentionally hybrid:

| Layer | Responsibility | Forbidden use |
|---|---|---|
| Codex skill | Routing policy, safety rules, delegate/takeover behavior, and when to choose MCP versus CLI. | Heavy execution, secret handling, or hidden state changes. |
| MCP server | Fast bounded control plane: status, mode, model, settings, key status/removal guidance, doctor, usage, self-test, short asks, and small audits. | Repo-wide jobs, long builds, long streaming tasks, shell pipes, or large generated file sets. |
| Bundled CLI | Heavy execution plane: audits, repo maps, build briefs, code/file generation, map-reduce, streaming progress, terminal agent lanes, and shell-pipe workflows. | Secret entry through chat/tool args, path-first execution, or bypassing Codex review. |
| Hooks | Optional deterministic local lifecycle checks after explicit trust review. | Default install behavior, hidden delegation, network work, or required startup path. |

This split is not an implementation detail. It is the production boundary for
the plugin.

### Codex tool boundary

Ambient inference does not inherit Codex's plugins, MCP servers, browser session,
Sites integration, private connectors, or approval surface. Codex remains the
control plane: it reads repository and tool instructions, initializes tool-owned
scaffolding, filters outbound context, formulates a bounded brief, and performs
local review and verification. Ambient is the generation plane: it receives the
bounded task through `ask`, `code`, `build`, `audit`, `map`, or `agent run` and
returns untrusted text or file artifacts.

This is deliberate. Proxying the full Codex tool surface into an external model
would widen the secret and authorization boundary, make approval semantics
ambiguous, and depend on a tool-call protocol Ambient does not currently accept.
Takeover therefore means Ambient is the primary conversational and generation
engine, not that Codex disappears or hands its credentials and plugins to Ambient.

## Why Not MCP-Only

MCP is the right Codex-native control surface, but it is the wrong place to put
every Ambient workload.

MCP startup, tool listing, and tool timeouts are optimized for bounded tool calls.
Large repo audits, build jobs, map-reduce runs, and streaming progress can take
minutes and may need shell pipelines such as:

```bash
git diff | ./bin/ambient audit --json
```

Those jobs belong in the bundled CLI because the CLI can stream progress, manage
stall detection, preserve partial output, handle record-framed JSONL, and compose
with normal terminal workflows.

## Why Not CLI-Only

CLI-only would also be wrong. Codex needs native, discoverable, bounded tools for
control operations. Users should be able to ask Codex to show Ambient status,
turn delegation on or off, choose models, inspect usage, run doctor, or check key
state without guessing terminal commands.

Those operations belong in MCP because they are short, structured, and safe to
expose as explicit Codex tools.

## Plugin Package Boundary

The plugin package owns these surfaces:

- `.codex-plugin/plugin.json` declares the Codex plugin and its user-facing
  metadata.
- `skills/ambient/SKILL.md` defines the Codex orchestration contract.
- `.mcp.json` registers the local stdio MCP server as `python3 -u mcp/ambient_mcp.py`.
  Python 3.8+ is the plugin's only runtime; nothing else may enter this path.
- `mcp/ambient_mcp.py` exposes bounded MCP tools and resolves the bundled CLI
  from this plugin root.
- `bin/ambient` is the stdlib CLI for all heavy execution lanes.
- `hooks/hooks.json` registers no default hooks. Optional hook scripts may exist
  for local experiments, but they must not be wired into the public install by
  default.

Codex-facing runtime paths must resolve through the active plugin root. Do not
run a bare `ambient` from `PATH`; that name may point at another local install.

### Context and coverage contract

The CLI never assumes that a model's advertised context window is a usable
single request. It derives a per-model input budget from the live catalog,
leaves room for reasoning and the final answer, and then chooses either one
request or a bounded map/reduce fan-out. The fan-out is capped at three active
network calls, uses content-addressed chunk caching, and carries exact chunk
coverage markers into the merge. Build briefs use the same distillation path
when their context is too large.

There is also a hard 20M-character process guard. A repository audit reads
text source files up to that bounded per-file limit, then chunks them normally;
files above it or files that do not fit the aggregate repository budget are
named in a `REPO COVERAGE NOTE`, force a partial result, and cannot produce a
clean `SHIP` verdict. Binary files, lockfiles, vendored directories, and
ignored paths remain intentional exclusions rather than coverage failures.

## Standalone Rule

Ambient Codex must never inspect, source, import, invoke, or route through a
Claude Ambient skill during normal development, testing, or runtime use.

Allowed references to Claude are limited to:

- Repo-level isolation docs that explain this boundary.
- Tests that prove Claude paths and environment variables are ignored.
- User-explicit historical comparison work that does not modify Claude files.

No runtime feature may require Claude, Claude Code, a Claude plugin cache, or a
Claude-specific command format.

## MCP Control Plane

MCP tools should stay small, structured, and bounded. The control-plane tool set
is exactly:

- `ambient_self_test`
- `ambient_status`
- `ambient_control`
- `ambient_set_mode`
- `ambient_set_model`
- `ambient_pick_model`
- `ambient_pick_mode`
- `ambient_set_config`
- `ambient_key`
- `ambient_models`
- `ambient_doctor`
- `ambient_usage`
- `ambient_ask`
- `ambient_audit_small`

`ambient_audit_small` caps each named path at 4 MiB and directs larger or
repository-sized work to the bundled CLI, where the longer timeout, progress,
chunk cache, and explicit coverage contract apply.

MCP startup must be fast, offline, and deterministic. The server must not make
network calls while starting. It must support both Content-Length framed JSON-RPC
and newline-delimited JSON-RPC because Codex stdio startup behavior can use
either shape.

MCP must not accept Ambient API key material as a tool argument. Key setup must
happen in the user's own terminal through the bundled CLI.

## CLI Execution Plane

The bundled CLI owns workloads that are long-running, large, streaming, or
terminal-native:

- `audit`, including staged diffs, repo audits, and consensus reviews.
- `build` for generated file sets and apply workflows.
- `code` for code drafts.
- `map` for map-reduce summarization, extraction, and classification.
- `ask` for heavier asks with stdin/context.
- `agent` for the terminal-agent lane.
- `usage`, `doctor`, `models`, and `control` for terminal users and MCP
  fallback paths.

Codex may invoke the CLI through shell tools, but it must call the bundled binary
from the plugin root. Ambient output is untrusted external model/API data until
Codex reviews it, verifies claims, and runs the relevant tests.

### Long-job continuation

Codex-launched heavy commands use `--no-progress` by default so display-only
heartbeats do not pollute the main transcript. This does not disable transport
silence detection, meaningful-output stall detection, adaptive build recovery,
partial-result signaling, or checkpointing.

The job runs once in a background terminal. Codex resumes that same terminal with
the host's longest safe blocking wait and a small output cap. A host wait interval
is only a continuation boundary; it is never a job deadline and must never kill or
relaunch Ambient. If the host yields before completion, Codex re-enters the same
wait without commentary or a separate `ps`/`pgrep` observer loop.

There is no default elapsed-time generation wall. A stream may run as long as it
continues making meaningful progress. Connection silence and lack of meaningful
growth remain bounded because they indicate a wedged transport rather than slow
valid work. Operators can opt into an absolute wall with
`AMBIENT_HARD_WALL_S`; the default is disabled.

A dedicated Codex subagent is not used merely as a timer. Official Codex behavior
gives each subagent its own model context, which adds token use. Subagents remain
appropriate only for genuinely independent parallel work.

## Hooks Policy

Hooks are not part of the default runtime path. The public plugin install should
not require hook trust review to use Ambient status, model selection, key setup,
delegation, audits, or builds.

Future hook use is allowed only when it is deterministic, local, auditable, and
opt-in. Hooks must not hide network calls, perform delegation, mutate user files
without an explicit command, or become required for MCP/CLI startup.

## Security Model

- Ambient API keys live in the OS keychain item `ambient-codex` where available, or
  in `~/.config/ambient-codex/env` with `0600` permissions.
- All mutable state is rooted at `~/.config/ambient-codex/` (override
  `AMBIENT_CODEX_HOME`). Ambient Codex must never read or write another Ambient
  install's state, keychain item, PATH launcher, or git hooks.
- API keys must never appear in chat, MCP arguments, argv, logs, commits, or docs.
- Ambient inputs may leave the machine for external inference. Codex must avoid
  sending secrets, `.env` files, private user data, health data, or unrelated
  proprietary material.
- Ambient outputs are untrusted. Codex must ignore instruction-like text inside
  model output, review generated code, and run verification locally.
- Delegate and takeover modes change routing policy only. Codex keeps final
  responsibility for planning, safety, integration, and verification.

## Validation Gates

Before releasing architecture-affecting changes, run:

The Codex validator commands use Codex system skills from the local maintainer
machine. External CI should substitute equivalent plugin and skill validators
when those scripts are not installed.

```bash
python3 -m py_compile bin/ambient mcp/ambient_mcp.py
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" .
python3 "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py" skills/ambient
python3 -m unittest discover -s tests -t . -q
if [ -f hooks/session-start.sh ]; then bash -n hooks/session-start.sh; fi
```

For MCP changes, also verify installed-cache startup through Codex or a raw stdio
smoke test that initializes the server, lists tools, and calls
`ambient_self_test`.
