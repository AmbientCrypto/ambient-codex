# Ambient Artifact Contract

Use this contract when Ambient must create a deck, document, website, image set,
or campaign bundle whose provenance must remain independently verifiable.

## Workflow

1. Ask Ambient to author one JSON specification conforming to
   `assets/artifact-spec-v1.schema.json`. A mixed campaign uses a
   `campaign_bundle` with complete nested artifact specifications; each nested
   deck, document, site, or image set is validated by its own rules.
2. Require `creative_author: Ambient`, `creative_authorship: ambient-only`, the
   exact model id, a SHA-256 of the exact generation-brief bytes, deterministic
   renderer metadata, and the permitted Codex roles. Compute the hash without
   newline or encoding normalization:

   ```bash
   python3 scripts/artifact_contract.py hash-prompt exact-brief.txt
   ```
3. Validate before rendering:

   ```bash
   python3 scripts/artifact_contract.py validate artifact.json --json
   ```

4. Render deterministically with the applicable Codex artifact tool. Rendering
   may translate coordinates, fonts, colors, local assets, and content exactly;
   it must not add creative copy, images, claims, or design decisions.
   For a deck, initialize an artifact-tool workspace and run the bundled
   renderer after validation:

   ```bash
   node scripts/render_deck.mjs \
     --spec artifact.json --root . \
     --workspace /absolute/artifact-tool-workspace \
     --preview-dir /absolute/preview-directory
   ```
5. Run artifact-specific visual and structural checks.
6. Hash the specification and every declared output:

   ```bash
   python3 scripts/artifact_contract.py manifest artifact.json \
     --root . --output output/ambient-provenance.json
   ```

## Boundary

Ambient owns all creative content in the specification. Codex may orchestrate,
validate, deterministically render, and verify. Do not ask a model to write a
renderer adapter for PowerPoint, PDF, or another tool-specific API when a
deterministic renderer already exists. Keep model output declarative.

All paths must be relative POSIX paths inside the artifact root. Asset elements
must reference local files; HTTP, data, and file URLs are rejected. Rendered
outputs must exist as regular, non-symlink files before a provenance manifest
can be written.

`rendering.engine`, `engine_version`, `font_policy`, and `asset_policy` record
the mechanical renderer boundary. Choose explicit policies such as
`embed-or-fail` and `local-only`; never allow an implicit font, remote asset, or
format substitution to change the design silently.

## Typed Content

- `deck` declares slide size, slides, speaker notes, and bounded elements.
- `document` declares page size, pages, and bounded elements.
- `site` declares an entrypoint and typed source files whose paths exactly
  match the declared outputs.
- `image_set` declares typed code-native source files whose paths exactly
  match the declared outputs. Raster generation is allowed only when the
  selected Ambient model exposes the corresponding output modality and the
  generated file is tracked as an output.
- `campaign_bundle` contains complete nested artifact specifications. The
  bundle output list must exactly equal the union of child outputs, with one
  owner per path.

## Model Failure And Fallback

An exact-model partial or reasoning-only result remains incomplete. Resume it
only with the same model, brief bytes, options, and build cache. Do not change
models inside that resumable build.

If the user explicitly authorizes fallback after the exact model cannot return
a final result, close the failed attempt and start a new build generation. Do
not reuse the incompatible cache. Declare every actual model in
`provenance.models` and add `generation_records` mapping one model and exact
prompt hash to every output it authored. Records must cover all outputs exactly
once. A fallback is never silent, and Codex still may not fill creative gaps in
an Ambient-only artifact.

## Deck Requirements

Deck specifications must declare positive finite slide dimensions, unique slide
ids, non-empty speaker notes, and elements with finite geometry inside the slide
bounds. Supported element types are `text`, `asset`, `shape`, and `line`.

The validator intentionally fails closed. Treat any violation or missing output
as an incomplete artifact, not as permission for Codex to invent a replacement.
