# Security Policy

## Reporting A Vulnerability

Please report vulnerabilities privately. Do not open a public issue for a
security problem.

- Preferred: open a GitHub security advisory on
  `github.com/AmbientCrypto/ambient-codex`.
- Include repro steps, platform, Python version, and `ambient-codex --version`.

## Threat Model

Ambient Codex is a local plugin and CLI wrapper around an external inference API.
The main security boundaries are:

- API key handling: keys are stored in the OS keychain item `ambient-codex` when
  available, or in owner-only `~/.config/ambient-codex/env` when file storage is
  explicitly used. Keys are never passed on argv and must not be pasted into chat.
- Install isolation: Ambient Codex owns only `~/.config/ambient-codex` and its
  `ambient-codex` keychain item for credentials, settings, cache, and usage. It
  does not import another Ambient install's key or state. The test suite proves
  this by running every command with the other install's directories at mode 000.
  User-invoked build, launcher, hook, and agent workflows can write to the
  explicitly selected build directory, native launcher/hook path, or opencode
  provider config as described below.
- `AMBIENT_API_KEY` is deliberately ignored because another Ambient install may use
  that shared name. Use `AMBIENT_CODEX_API_KEY` when overriding the key from the
  environment; `ambient-codex doctor` warns when the shared name is present and ignored.
- External input boundary: all web/API/MCP/model output is untrusted data.
- External output boundary: selected prompts, diffs, and files are sent to the
  configured inference endpoint.
- Launcher ownership: bundled `link` only manages launchers whose target path
  contains `/ambient-codex/`. Any other launcher is treated as foreign and left
  untouched.
- Lifecycle hooks: the public plugin registers no default command hooks. If a
  user opts into git audit hooks, ownership is checked by exact native
  `ambient-codex` markers before replacement or removal.
- Agent lane: `ambient-codex agent` adds or repairs only the namespaced
  `ambient-codex` provider in `~/.config/opencode/opencode.json`, preserves
  unrelated providers and restrictive file permissions, and never stores the
  literal key there. It exposes the key to the opencode subprocess environment
  and runs opencode with `--pure` by default so unrelated extensions are disabled.

Out of scope: the security of Ambient's hosted network and the opencode project.

## Good Practice

- Do not send secrets, credentials, PHI, private user data, or production dumps.
- Review all Ambient-generated code before applying or executing anything.
- Run tests/builds locally before accepting generated changes.
- Keep direct provider status honest: `ambient-codex codex` is diagnostic until it
  reports the Codex provider bridge working.
- Run the validation gates in `README.md` before release.
