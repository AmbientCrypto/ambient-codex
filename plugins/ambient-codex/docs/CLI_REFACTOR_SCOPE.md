# CLI Refactor Scope

Status: post-1.9 engineering plan; not a public-release blocker.

Execution state, decisions, bugs, verification, and the exact resume point live
in [CLI_REFACTOR_PROGRESS.md](CLI_REFACTOR_PROGRESS.md). Update that ledger at
every phase boundary and whenever scope, risk, or next action changes.

## Why Scope It

`bin/ambient` is intentionally easy to ship—one extensionless, stdlib-only
entrypoint—but it has grown beyond 13,000 lines. The test suite makes the current
runtime safe to release, while the file size raises long-term review, ownership,
merge-conflict, and change-isolation risk. The refactor should reduce those risks
without changing the public CLI, JSON envelopes, state, API behavior, or plugin
installation model.

## Goals

- Keep `bin/ambient` as the stable executable and compatibility facade.
- Preserve Python 3.8+, stdlib-only runtime, cross-platform behavior, and no-Node
  MCP startup.
- Move cohesive implementation behind explicit internal module boundaries.
- Keep most modules between 200 and 800 lines and functions below 50 lines where
  practical.
- Preserve immutable updates, explicit boundary validation, and existing exit
  codes/error categories.
- Make API, filesystem, spend, routing, and workflow logic independently testable.
- Maintain at least 80% runtime line coverage throughout extraction.

## Non-goals

- No command rename, flag redesign, state migration, JSON schema change, or new UI.
- No third-party runtime dependency or framework adoption.
- No direct Codex provider bridge until Ambient supports current Responses API
  tool payloads.
- No simultaneous MCP rewrite.
- No big-bang rewrite or behavior cleanup hidden inside mechanical extraction.

## Proposed Runtime Shape

```text
bin/ambient                    executable facade and parser bootstrap
ambient_codex/
  constants.py                version, limits, exit codes, defaults
  state.py                    namespaced config, keychain, cache, ledger
  transport.py                HTTPS, SSE, retries, response normalization
  models.py                   catalog, profiles, routing, token budgets
  safety.py                   secret scanning, bounded input, path validation
  spend.py                    pricing, reservations, gates, receipts
  map_reduce.py               chunking, fan-out, synthesis, partial contracts
  audit.py                    repo intake, findings, structured reduction
  generation.py               ask, code, and build orchestration
  integrations.py             hooks, launcher, opencode, Codex diagnostic
  cli.py                      argparse construction and command dispatch
```

The exact package location must work in all three delivery modes before any
extraction lands: source checkout, Codex's versioned plugin cache, and `pipx`
installation. A thin facade should re-export compatibility symbols temporarily
because many tests load and patch the extensionless script directly.

## Dependency Direction

```text
constants
  -> state, safety
  -> transport, models, spend
  -> map_reduce
  -> audit, generation
  -> integrations
  -> cli facade
```

- Lower layers must not import workflow or CLI modules.
- Network and filesystem effects enter through small injected callables or
  service objects so tests can replace them without patching module globals.
- Shared dataclasses and immutable request/result records should live at the
  lowest layer that owns them, not in a generic utilities bucket.
- MCP continues to execute the facade; it must not import private workflow
  modules directly.

## Phased Extraction

Each phase changes at most five production/test files before its own green
checkpoint commit.

### Phase 0 — Characterization and packaging spike

- Add import/install characterization tests for source, plugin-cache, `pipx`, and
  Windows path semantics.
- Record CLI help, version, exit-code, and representative JSON-envelope snapshots.
- Prove a minimal internal package is included in the committed archive and all
  install modes before moving behavior.
- No runtime behavior moves in this phase.

Exit gate: existing 1,128+ tests plus new packaging tests pass on CI.

### Phase 1 — Pure constants and records

- Extract constants, named tuples/dataclasses, and pure normalization helpers.
- Re-export moved names from `bin/ambient` to preserve test and caller behavior.
- Reject any extraction that introduces a circular import or import-time I/O.

Exit gate: byte-identical dry-run plans and JSON fixtures.

### Phase 2 — State, safety, and spend boundaries

- Extract namespaced state/keychain/config handling.
- Extract secret scanning, bounded readers, and path validation.
- Extract pricing, fleet reservations, and cost gates.
- Keep filesystem/keychain calls behind explicit adapters.

Exit gate: state-isolation, secret-tripwire, path, spend, and concurrent-write
tests pass unchanged; no permissions or file-format drift.

### Phase 3 — Transport, models, and map/reduce

- Extract HTTP/SSE transport and error normalization.
- Extract live-catalog model profiles, context/output budgets, and routing.
- Extract chunking, fan-out, retries, synthesis, and partial-result contracts.
- Preserve one-fetch catalog memoization and no blind POST retries.

Exit gate: live model matrix, truncation recovery, fallback, consensus, and
reasoning-budget tests remain green.

### Phase 4 — Audit and generation workflows

- Extract audit intake/findings/reduction.
- Extract ask/code/build planning, resume identity, and safe apply.
- Move one workflow at a time; do not combine extraction with prompt changes.
- Keep command handlers as thin orchestration functions.

Exit gate: large-repo dry runs, planted-bug audits, resumable builds, apply
idempotency, coverage-gap, and partial-result tests pass.

### Phase 5 — Integrations and facade reduction

- Extract launcher/hook ownership, opencode provider/agent, and Codex diagnostics.
- Reduce `bin/ambient` to parser/bootstrap/compatibility exports, targeting fewer
  than 800 lines.
- Remove temporary re-exports only after tests import public package interfaces.
- Update architecture and contributor documentation.

Exit gate: installed MCP, no-Node, agent `--pure`, takeover, archive, and full
cross-platform CI all pass.

## Compatibility Contracts

The refactor is incomplete unless all of these remain stable:

- Executables: bundled `bin/ambient` and the `ambient-codex` launcher.
- Python support: 3.8 through the newest CI version.
- Commands, flags, help text where documented, and exit codes `0/1/2/3/64/130`.
- JSON/JSONL schema version, status, partial, coverage-gap, and finish semantics.
- State/keychain names, ownership marker, permissions, and purge behavior.
- Model choice, explicit fallback rules, context/output clamps, and spend gates.
- MCP's 14 tool names, schemas, framing, timeouts, and source/cache root behavior.
- Build path firewall, resume validation, atomic writes, and non-executable output.
- No Claude runtime/config/key dependency and no Node dependency.

## Principal Risks

| Risk | Control |
|---|---|
| Direct tests patch facade globals that extracted code no longer reads | Introduce dependency injection first; retain compatibility adapters until tests migrate deliberately |
| Internal package omitted from plugin cache or `pipx` artifact | Phase 0 install-mode tests and clean archive smoke before extraction |
| Circular imports recreate a monolith across files | Enforce the dependency direction above and add an import-cycle check |
| Error wording/exit/partial behavior drifts | Snapshot boundary contracts and move one workflow per commit |
| Windows path/keychain/subprocess regressions | Require the existing Windows CI matrix on every phase |
| Live API behavior changes during extraction | Keep live model matrix as a release gate; do not mix prompt/API redesign with movement |
| Coverage looks high while new modules are omitted | Update CI `--include` to cover the full internal package before the first extraction |

## Definition of Done

- `bin/ambient` is below 800 lines and contains no workflow implementation.
- No production module exceeds 800 lines without a documented exception.
- Full tests, 80%+ coverage, lint, validators, archive, installed MCP, takeover,
  agent, stress, and all-model matrix pass.
- Source checkout, Codex cache, `pipx`, macOS, Linux, and Windows behavior match.
- Public contracts and security/privacy boundaries are unchanged or explicitly
  versioned and documented.
- Every phase is independently revertible; no big-bang merge is required.
