# Changelog

All notable changes to `ambient-codex`.

## 1.0.2 - 2026-07-17

- Hardened completion recovery so reasoning-only responses never become
  deliverable code and do not silently trigger additional billed attempts.
- Added learned final-answer health to automatic model routing while preserving
  explicit model selection.
- Added versioned, validated Ambient-only artifact contracts with exact prompt
  hashes, recursive campaign bundles, per-output provenance, and deterministic
  pitch-deck rendering.
- Added atomic local plugin staging, synchronized cachebusted versions, verified
  activation, and rollback when an install fails.

## 1.0.1 - 2026-07-16

- Hardened broad multi-workstream builds with capacity-aware planning,
  automatic plan refinement, conservative HTML/CSS/SVG size estimates,
  dependency ordering, workstream-bounded generation batches, and bounded
  parallel waves for independent workstreams.
- Prevented silent clean results when `--max-files` omits planned artifacts or
  when a planned file cannot fit the safe per-artifact generation target.
- Removed nested reasoning-budget amplification from build recovery: multi-file
  calls and the two bounded compact retries never escalate, while a focused
  first attempt has at most one context-safe escalation. Terminal key, funds,
  rate, model, network, and service errors no longer multiply through batch
  sharding.
- Added incremental atomic writes for explicit headless `--apply --yes` builds,
  while preserving final reconciliation, backups, resume state, and path
  firewalls.
- Preserved a shared project brief through compact recovery prompts and added
  structured output for focused single-file generation.
- Derived artifact targets, output reserves, and batch sizing from live model
  metadata without Kimi/GLM/model-name special cases.
- Clarified Ambient-only authorship and text-only SVG/image provenance in the
  Codex skill.

## 1.0.0 - 2026-07-15

- Released the official Ambient integration for Codex.
- Added direct chat, model routing, audits, code generation, resumable builds,
  repository maps, delegation, and Ambient session mode.
- Added model-aware context management, output recovery, large-repository
  coverage tracking, quiet long-running operations, and no default elapsed-time
  cutoff for healthy work.
- Added isolated credential storage, secret scanning, privacy controls, native
  MCP tools, cross-platform tests, and public installation documentation.
