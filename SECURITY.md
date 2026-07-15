# Security

Report vulnerabilities privately through a
[GitHub security advisory](https://github.com/AmbientCrypto/ambient-codex/security/advisories/new).
Do not open a public issue for a security problem.

Include the platform, Python version, Ambient Codex version, impact, and minimal
reproduction steps. Never include a real API key or private source code.

Ambient Codex sends only the prompts and files a user routes to the configured
Ambient endpoint. Model output is untrusted and must be reviewed before it is
applied or executed. See the [threat model](ambient-codex-threat-model.md) and
[plugin security boundary](plugins/ambient-codex/SECURITY.md).
