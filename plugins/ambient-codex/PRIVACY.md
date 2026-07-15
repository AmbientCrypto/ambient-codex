# Privacy

`ambient-codex` is local-first. It sends only the prompts, code, diffs, or files
you explicitly route to the configured Ambient-compatible inference endpoint.

## What Leaves Your Machine

- Prompt and file content passed to `ambient-codex ask`, `ambient-codex audit`,
  `ambient-codex map`, `ambient-codex code`, `ambient-codex build`, or
  `ambient-codex agent`.
- Requests go to `https://api.ambient.xyz` by default, or to the endpoint the
  user explicitly configured and trusted.

No analytics, crash reporting, or background telemetry is sent by this plugin.

## What Stays Local

- API key: OS keychain item `ambient-codex` when available, or
  `~/.config/ambient-codex/env` with owner-only permissions when file storage is
  explicitly used.
- Usage ledger: `~/.config/ambient-codex/usage.jsonl`.
- Chunk cache: `~/.config/ambient-codex/cache/`.
- All Ambient Codex state lives under `~/.config/ambient-codex/`
  (override: `AMBIENT_CODEX_HOME`), separate from any other Ambient install. Ambient
  Codex never reads or writes another install's config directory or keychain item.
- Build resume state: `<build-dir>/.ambient-build.json`.
- Codex plugin files: under the local plugin install/cache.
- Agent provider metadata: `ambient-codex agent` adds or repairs the namespaced
  `ambient-codex` provider in `~/.config/opencode/opencode.json`. It stores an
  environment-variable reference, never the literal Ambient key, and preserves
  other providers. Agent sessions use opencode's `--pure` mode by default.
- Optional launcher and git-hook commands write only to the path or repository
  the user explicitly selects and refuse to replace foreign-owned entries.

## User Responsibility

Auditing or building against code publishes that selected content to the network
you configured. The CLI refuses obvious credential-looking content and `.env`
files, but the tripwire is a backstop. Do not send secrets, credentials, private
user data, health data, or unrelated proprietary material.

## Agent Boundary

`ambient-codex agent` launches opencode as a separate tool and passes the Ambient key
to that subprocess through the environment. `--pure` disables unrelated opencode
extensions by default, but opencode still reads the provider configuration above
and has its own behavior. This privacy statement covers the Ambient Codex plugin
and CLI, not opencode itself.

## Purge Commands

```bash
ambient-codex setup --remove
ambient-codex cache clear
rm -rf ~/.config/ambient-codex
```

Only remove `~/.config/ambient-codex`. Another Ambient install may own
`~/.config/ambient`; deleting that could remove its key and usage history.

Delete `.ambient-build.json` files from build directories when you no longer
need resume state.

The opencode provider entry contains no literal key and is not removed by the
commands above. Remove the `ambient-codex` provider from
`~/.config/opencode/opencode.json` manually if you also want to purge that
non-secret integration metadata.
