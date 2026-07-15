# Ambient Codex

Official Ambient V1 integration for Codex. Use Ambient models for chat, code,
builds, audits, and large-repository work without leaving Codex.

## Install

Requirements: Codex, Python 3.8+, and an Ambient API key.

```bash
codex plugin marketplace add AmbientCrypto/ambient-codex
codex plugin add ambient-codex@ambient-codex
```

Start a new Codex thread, then use `$ambient`:

```text
$ambient
```

Need a key? Create one at [app.ambient.xyz](https://app.ambient.xyz), then follow
the setup prompt. See the [installation guide](docs/INSTALL.md) for the one-time
terminal launcher and troubleshooting.

## Use

Ask naturally:

```text
use Ambient to audit this diff
ask Ambient to review this design
build this feature with Ambient
change Ambient mode
change the code model
```

Modes:

- **Normal Codex** — Ambient runs only when requested.
- **Delegate** — Codex sends larger coding and review work to Ambient.
- **Ambient session** — Ambient becomes the primary chat and generation engine
  for the current Codex thread.

See the concise [feature guide](docs/FEATURES.md) for models, builds, audits,
large repositories, and session behavior.

## Privacy and safety

Only prompts and files you route to Ambient leave your machine. Never send API
keys, credentials, or other sensitive material. Generated code is untrusted
until reviewed and tested.

- [Privacy](plugins/ambient-codex/PRIVACY.md)
- [Security](SECURITY.md)
- [Threat model](ambient-codex-threat-model.md)

## Develop

The plugin is in [`plugins/ambient-codex`](plugins/ambient-codex). Contributor
setup and release gates are in [CONTRIBUTING.md](CONTRIBUTING.md).

MIT licensed.
