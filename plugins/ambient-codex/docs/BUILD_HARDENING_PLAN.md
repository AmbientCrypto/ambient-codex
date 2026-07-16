# Adaptive Build Hardening Plan

## Incident

A broad creative-services showcase requested 27 files across a portfolio hub,
pitch deck, example website, PR materials, campaign copy, brand guidelines, and
SVG artwork. Ambient planned the project successfully and generated 13 files,
but the build ran for roughly an hour without applying any output.

The saved state showed:

- 13 completed files retained in `.ambient-build.json`
- one repeatedly failed monolithic deck file
- repeated reasoning-only completions at escalating token budgets
- repeated incomplete-file recovery calls
- zero files visible in the target directory despite `--apply --yes`

This is a build-orchestration incident. The model demonstrated that it could
produce the individual artifact classes, but the lane packaged and retried the
program inefficiently.

## Root causes

### Planning contract

1. `--max-files` truncates a larger proposed plan but does not record the
   omitted files as a coverage gap.
2. The planner is not told `--max-files`, `--max-file-bytes`, or a realistic
   per-call artifact target before it proposes the architecture.
3. `est_lines * 40` materially underestimates HTML, CSS, SVG, and JSONL-escaped
   content.
4. A single large file is allowed into generation even when its estimate cannot
   fit the safe visible-answer budget.
5. Duplicate paths, unknown dependencies, dependency cycles, and incoherent
   workstream ordering are not represented in the plan contract.
6. The plan does not preserve a sufficiently detailed shared project brief for
   consistent names, design tokens, interfaces, routes, claims, and disclaimers.

### Generation and recovery

1. Completion-level reasoning budget escalation runs inside build-level
   split/retry recovery. One logical file attempt can therefore create several
   fully re-billed model requests before the build lane adapts.
2. The build call ceiling counts logical generation calls, not internal
   completion attempts.
3. Recovery prompts remove the full plan but also discard too much shared
   project context.
4. Single-file recovery remains prompt-only even when the model advertises
   structured output support.
5. Batch construction does not preserve workstream boundaries.
6. Large monolithic files are retried rather than rejected or decomposed during
   planning.
7. Independent workstreams run sequentially even after their dependencies are
   complete, making wall time the sum of every slow model call.

### Durability and user experience

1. Completed files are saved in resume state but not applied until the entire
   build finishes.
2. A late difficult file makes an otherwise useful `--apply --yes` build look
   empty.
3. Progress reports generation count but not whether completed files are already
   durable on disk.
4. A cancelled build preserves state but withholds successfully generated files
   that the user explicitly authorized the command to write.

### Skill routing and claims

1. The skill sends every multi-file request through one undifferentiated build
   invocation without a broad-program preflight contract.
2. It does not distinguish raster image generation from code-native SVG artwork
   when the serving catalog exposes text-only output.
3. It does not require exact option reuse between `--plan-only` and resume.
4. It tells Codex to wait through adaptive recovery but does not define when the
   CLI itself must fail fast because its plan is structurally unsafe.

## Target architecture

### 1. Capacity-aware planning

- Derive a conservative target artifact size from the selected model's current
  output budget, chars-per-token profile, and `--max-file-bytes`.
- Include the file-count cap, hard file-size cap, and target artifact size in
  the planning prompt.
- Ask for `est_bytes`, `workstream`, and `depends_on` metadata while preserving
  compatibility with older plan replies.
- Preserve a bounded `project_brief` containing cross-file decisions.
- Validate the complete proposed plan before truncation.
- Replan once when the proposal exceeds caps, contains oversized artifacts, or
  has dependency/path contract defects.
- Never report a capped or structurally incomplete plan as clean. If refinement
  still fails, return an explicit partial plan/coverage gap before generation.

### 2. Program-aware generation

- Topologically order files by declared dependencies.
- Keep batches inside one workstream.
- Size batches with conservative byte estimates rather than line count alone.
- Generate one coherence anchor first, then run dependency-ready workstreams in
  bounded parallel waves while serializing batches inside each workstream.
- Include the shared project brief in normal and recovery prompts.
- Use structured output for focused single-file calls when the serving model
  supports it.
- Disable completion-level escalation for multi-file generation and compact
  retries. Permit at most one context-safe escalation on a focused file's first
  attempt, retain two non-escalating compact retries inside the existing call
  ceiling, then let the build lane fail the artifact explicitly.
- Keep single-file recovery bounded and fail with an exact decomposition
  diagnosis when the planner did not produce a viable artifact unit.

### 3. Incremental durability

- For explicit headless `--apply --yes`, atomically apply each accepted batch
  immediately after saving resume state.
- Reconcile all completed records at finalization so interrupted or transient
  write failures remain recoverable.
- Preserve existing overwrite, backup, symlink, and path-firewall guarantees.
- Keep interactive builds manifest-first unless the user has already provided
  non-interactive write consent.

### 4. Honest observability

- Persist bounded build metrics: logical calls, recoveries, reasoning-only
  responses, truncations, generated files, workstream waves, maximum parallel
  calls, and incrementally applied files.
- Include planning omissions and oversized artifacts in JSON coverage details.
- Report generated and durable counts separately during long builds.
- Never claim clean completion while any planned artifact is omitted, oversized,
  failed, skipped, or unapplied.

### 5. Skill contract

- Route broad programs through the hardened build lane and preserve exact resume
  options.
- Treat Ambient-only authorship as a provenance constraint: Codex may formulate
  the acceptance brief and verify output, but must not author missing content.
- State that text-only serving models can create SVG/code-native artwork but not
  honestly claim raster image generation.
- Require review and deterministic rendering/testing after the Ambient envelope
  reports complete.

## Regression matrix

- plan exceeds `--max-files`
- plan contains a duplicate path
- plan contains an unknown dependency
- plan contains a dependency cycle
- one file estimate exceeds the safe artifact target
- one file estimate exceeds `--max-file-bytes`
- HTML/CSS/SVG estimates use conservative bytes-per-line fallback
- reasoning-only first generation
- truncated multi-file JSONL response
- wrong/rejected path returned by the model
- single-file structured-output fallback
- focused file omitted twice and recovered on the already-budgeted third call
- cancellation after one accepted batch
- resume after cancellation without re-generating completed files
- pre-existing destination without `--force`
- force overwrite with backup
- transient write failure followed by final reconciliation
- plan-only partial envelope exposes omissions
- headless apply makes completed files visible before total completion
- dependency-ready workstreams overlap while same-workstream batches serialize
- a failed dependency prevents downstream generation without deadlock
- project brief survives compact recovery
- text-only image request is described as SVG/code-native output

## Release gates

1. New regression tests fail against the organization release baseline.
2. Focused build tests pass.
3. Full unit suite passes.
4. Plugin validator passes.
5. Skill validator passes.
6. A bounded live build with multiple workstreams completes without reasoning
   escalation amplification, writes early completed files, and overlaps
   dependency-independent generation calls.
7. No push, tag, or release occurs without explicit approval.
