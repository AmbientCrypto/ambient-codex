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
- **Build** — multi-file generation with resumable manifests, bounded writes,
  path validation, and local test review.
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

Long builds run as one background process. Display-only heartbeats are hidden,
healthy progress has no elapsed-time cutoff, and real connection or no-progress
stalls remain detected.

## Boundaries

Ambient cannot directly call Codex plugins, browser sessions, Sites, or private
connectors. Codex prepares the scoped brief and uses those tools around Ambient's
result. Ambient performs the primary model generation; Codex reviews and tests it.

Selected prompts and files are sent to the configured Ambient endpoint. Keep
credentials and sensitive personal material out of routed input.
