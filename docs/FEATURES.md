# Ambient Codex features

Ambient Codex routes work to Ambient while Codex remains responsible for local
tools, repository rules, safety checks, and final verification.

## Modes

- **Normal Codex** — use Ambient only when you ask.
- **Delegate** — route larger audits, code drafts, builds, and bulk reading to
  Ambient while Codex remains the normal chat experience.
- **Ambient session** — route conversation and primary generation through
  Ambient for the current thread. A new Codex thread starts in Normal Codex.

## Workflows

- **Ask** — short Ambient questions and second opinions.
- **Audit** — review files, staged changes, diffs, or repositories; optional
  multi-model consensus.
- **Code** — focused drafts using selected context files.
- **Build** — capacity-aware multi-file generation with plan refinement,
  dependency ordering, resumable manifests, incremental bounded writes, path
  validation, and local test review.
- **Map** — parallel summaries or classifications over many files.
- **Agent** — terminal-based Ambient work through the bundled opencode lane.

## Models

Chat/review and code/build can use separate defaults. Ambient Codex reads live
model metadata and derives context windows, output limits, reasoning budgets,
chunk sizes, and fallback behavior for the selected model. A concrete model is
never silently replaced unless fallback is enabled.

## Large work

Oversized inputs are split into bounded chunks and reduced from compact findings.
Large repositories use non-overlapping shards with coverage tracking. Partial or
unread coverage is reported instead of being presented as a clean result.

Broad builds validate the complete proposed file set before generation. Plans
that exceed file caps, contain dependency defects, or propose artifacts too
large for one safe answer are refined once and otherwise reported as coverage
gaps rather than silently truncated. Explicit headless `--apply --yes` runs
write accepted files as batches complete, so a late difficult artifact does not
withhold earlier successful work. After one coherence-anchor batch, independent
dependency-ready workstreams run in bounded parallel waves; batches from the
same workstream stay serialized.

Long builds run as one background process. Display-only heartbeats are hidden,
healthy progress has no elapsed-time cutoff, and real connection or no-progress
stalls remain detected. Build recovery reduces artifact units instead of nesting
large reasoning-budget escalation ladders inside the retry tree. Artifact size,
output budget, structured-output use, and reasoning reserve come from the live
selected-model profile rather than a Kimi/GLM/model-name rule table.

## Boundaries

Ambient cannot directly call Codex plugins, browser sessions, Sites, or private
connectors. Codex prepares the scoped brief and uses those tools around Ambient's
result. Ambient performs the primary model generation; Codex reviews and tests it.

Text-only Ambient models can produce SVG, HTML/CSS, canvas, and other code-native
visuals. They are not represented as raster image-generation models unless the
live model catalog explicitly advertises image output.

Selected prompts and files are sent to the configured Ambient endpoint. Keep
credentials and sensitive personal material out of routed input.
