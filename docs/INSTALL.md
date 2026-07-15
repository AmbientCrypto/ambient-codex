# Install Ambient Codex

Ambient Codex is the official Ambient V1 integration for Codex.

## Requirements

- Codex
- Python 3.8 or newer
- An Ambient API key from [app.ambient.xyz](https://app.ambient.xyz)

## 1. Install the plugin

```bash
codex plugin marketplace add AmbientCrypto/ambient-codex
codex plugin add ambient-codex@ambient-codex
```

Reinstalling uses the same second command. Start a new Codex thread after every
install or update so Codex loads the current skill and MCP server.

## 2. Add the terminal launcher

The plugin is installed in a versioned Codex cache. This one-time command creates
a stable `ambient-codex` launcher that follows future plugin updates:

```bash
PLUGIN_DIR="$(codex mcp get ambient --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["transport"]["cwd"].rstrip("/."))')"
"$PLUGIN_DIR/bin/ambient" link
```

If `~/.local/bin` is not on `PATH`, the command prints the exact line to add.

## 3. Store your key

```bash
ambient-codex setup
```

Setup hides the key while you type, verifies it, and uses the OS keychain when
available. Never paste an API key into Codex chat.

## 4. Start

Open a new Codex thread and enter:

```text
$ambient
```

You can now choose a mode or model, ask a question, run an audit, or start a
build.

## Check or remove

```bash
ambient-codex doctor
ambient-codex control
ambient-codex setup --remove
ambient-codex link --remove
```

Uninstall the plugin with:

```bash
codex plugin remove ambient-codex@ambient-codex
```

See [PRIVACY.md](../plugins/ambient-codex/PRIVACY.md) for local state and purge
instructions.
