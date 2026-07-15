# Per-model tuning

Tuning in this CLI is **derived, not hand-coded**: `model_profile()` reads each
model's live catalog metadata (`context_length`, `max_output_length`, the
`reasoning` feature flag, structured-output capabilities) and derives its input
sizing, chunking, output budget, escalation ceiling, and JSON mode. Any model the
API adds is tuned automatically the moment it appears in the catalog — nothing to
update client-side.

Run bundled `control --all-models --json` or bundled `models` to see what the
catalog offers right now, and bundled `models --all` to include curated-out
entries. The derivation is covered
by catalog-driven invariants plus fuzz configs in CI, so a new model can never
land with an unsafe budget or chunk size.

## What the derivation does

- A hard output cap shrinks a model's single-shot pass to what reasoning + answer
  can actually fit, instead of marathoning toward an empty reply.
- A non-reasoning model spends no tokens thinking, so it gets a larger single-shot
  sized off its real context window.
- A model without structured-output support routes `--json` audits through the
  prompt-instruction + tolerant-parse path instead of a failing `response_format`.

## Selectability

Every catalog model is choosable: bundled `control model <id>` (sticky default),
`-m <id>` (one call), or the interactive bundled `control menu` picker. An
explicit `-m` always
works, even for a model the menus curate out.

An explicit `--max-tokens` request is first accepted through a CLI-wide safety
ceiling, then clamped to the selected model's live `max_output_length` and the
remaining context after the actual input is measured. A model such as Kimi
with a 262,144-token advertised output limit is therefore not rejected by a
stale client-side 200,000-token ceiling. The one-million-token parser guard is
only an abuse bound; every actual request still receives the selected model's
smaller catalog-derived output and context-safe cap.

The default single-shot input cap remains a cost/stability control, not a
provider limitation. For a long-context model, `AMBIENT_SINGLE_SHOT_MAX_CHARS`
can raise it; the live context-fit math still lowers the effective value when
reasoning plus the answer would not fit. Inputs beyond the effective value use
the bounded map/reduce path instead of being silently truncated.

## Defaults

The default model for every lane is `moonshotai/kimi-k2.7-code`. Any other model
stays fully selectable with bundled `control model <id>` / `-m <id>`.
