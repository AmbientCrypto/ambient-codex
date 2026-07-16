"""Immutable runtime constants shared by the Ambient Codex CLI facade.

This lowest-layer module deliberately imports only :mod:`re`. It must not read
the environment, filesystem, keychain, network, config, or mutable process
state at import time.
"""

import re


AMBIENT_CODEX_HOME_ENV = "AMBIENT_CODEX_HOME"
STATE_MARKER = ".ambient-codex"
KEYCHAIN_SERVICE = "ambient-codex"
KEYCHAIN_ACCOUNT = "api-key"
API_KEY_ENV = "AMBIENT_CODEX_API_KEY"  # pragma: allowlist secret
SHARED_API_KEY_ENV = "AMBIENT_API_KEY"  # pragma: allowlist secret
LAUNCHER_NAME = "ambient-codex"
DEFAULT_API_URL = "https://api.ambient.xyz"
KEY_CONSOLE_URL = "https://app.ambient.xyz"
SUPPORT_LINE = (
    "If this keeps happening, contact Ambient support with the "
    "[category] code shown above — support/community links at "
    "https://ambient.xyz"
)

# Exit-code contract: 0 clean, 1 diagnosed error, 2 partial result,
# 3 unconfigured, 64 usage error, and 130 interrupted.
EXIT_PARTIAL = 2
EXIT_UNCONFIGURED = 3
EXIT_USAGE = 64

# Untrusted model output must not be able to inject terminal controls.
ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|[PX^_][^\x1b]*(?:\x1b\\)?"
    r"|[@-Z\\-_=><~])"
)
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")

DEFAULT_MODEL = "moonshotai/kimi-k2.7-code"
DEFAULT_CODE_MODEL = "moonshotai/kimi-k2.7-code"
DEFAULT_TIMEOUT_S = 300
DEFAULT_MAX_TOKENS = 16_384
MAX_REQUESTED_TOKENS = 1_000_000
MIN_OUTPUT_TOKENS = 2_048
HEARTBEAT_S = 20
MAX_AUTO_BUDGET_TOKENS = 65_536
STREAM_LINE_MAX = 8 * 1_024 * 1_024

CHUNK_CHARS = 300_000
CHARS_PER_TOKEN = 3.2
INPUT_TOKEN_SAFETY = 1.75
REASONING_EXPANSION = 1.5
TELEMETRY_CPT_MIN = 1.0
TELEMETRY_CPT_MAX = 8.0
TELEMETRY_EWMA_ALPHA = 0.3
ANSWER_TOKENS_RESERVE = 6_000
OUTPUT_SAFETY = 1.15
CONTEXT_OVERHEAD_TOKENS = 2_500
REASONING_SINGLE_SHOT_CHARS = 32_000
SINGLE_SHOT_MAX_CHARS_DEFAULT = 120_000
REASONING_CHUNK_FACTOR = 0.85
MIN_REASONING_CHUNK = 8_000
NONREASONING_OUTPUT_BUDGET = 16_384
NONREASONING_CONTEXT_MARGIN = 0.85
FALLBACK_CONTEXT = 200_000
FALLBACK_MAX_OUTPUT = 16_384
ABS_MAX_CHARS = 20_000_000
MAX_PARALLEL_CHUNKS = 3
MAX_BUILD_FILES = 512
MAX_BUILD_FILE_BYTES = 5_000_000
BUILD_FAILURE_CIRCUIT_BREAKER = 3
REASONING_BUILD_BATCH_FILES = 3
DIRECT_BUILD_BATCH_FILES = 5

CODE_MAP_BUDGET_DEFAULT = 4_000
CODE_MAP_BUDGET_MAX = 40_000
CODE_MAP_SIGS_PER_FILE = 40
SIG_SCAN_LINE_MAX = 4_096
REPO_FILE_MAX_BYTES = ABS_MAX_CHARS
REPO_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out", "vendor",
    "__pycache__", "target", "venv", ".venv", ".tox", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "coverage", ".next", ".nuxt",
    "DerivedData", "Pods",
})
REPO_LOCKFILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "bun.lock", "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock",
    "Gemfile.lock", "composer.lock", "go.sum", "flake.lock",
    "packages.lock.json", "Package.resolved", "mix.lock",
})

__all__ = (
    "AMBIENT_CODEX_HOME_ENV",
    "STATE_MARKER",
    "KEYCHAIN_SERVICE",
    "KEYCHAIN_ACCOUNT",
    "API_KEY_ENV",
    "SHARED_API_KEY_ENV",
    "LAUNCHER_NAME",
    "DEFAULT_API_URL",
    "KEY_CONSOLE_URL",
    "SUPPORT_LINE",
    "EXIT_PARTIAL",
    "EXIT_UNCONFIGURED",
    "EXIT_USAGE",
    "ANSI_RE",
    "CTRL_RE",
    "DEFAULT_MODEL",
    "DEFAULT_CODE_MODEL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_MAX_TOKENS",
    "MAX_REQUESTED_TOKENS",
    "MIN_OUTPUT_TOKENS",
    "HEARTBEAT_S",
    "MAX_AUTO_BUDGET_TOKENS",
    "STREAM_LINE_MAX",
    "CHUNK_CHARS",
    "CHARS_PER_TOKEN",
    "INPUT_TOKEN_SAFETY",
    "REASONING_EXPANSION",
    "TELEMETRY_CPT_MIN",
    "TELEMETRY_CPT_MAX",
    "TELEMETRY_EWMA_ALPHA",
    "ANSWER_TOKENS_RESERVE",
    "OUTPUT_SAFETY",
    "CONTEXT_OVERHEAD_TOKENS",
    "REASONING_SINGLE_SHOT_CHARS",
    "SINGLE_SHOT_MAX_CHARS_DEFAULT",
    "REASONING_CHUNK_FACTOR",
    "MIN_REASONING_CHUNK",
    "NONREASONING_OUTPUT_BUDGET",
    "NONREASONING_CONTEXT_MARGIN",
    "FALLBACK_CONTEXT",
    "FALLBACK_MAX_OUTPUT",
    "ABS_MAX_CHARS",
    "MAX_PARALLEL_CHUNKS",
    "MAX_BUILD_FILES",
    "MAX_BUILD_FILE_BYTES",
    "BUILD_FAILURE_CIRCUIT_BREAKER",
    "REASONING_BUILD_BATCH_FILES",
    "DIRECT_BUILD_BATCH_FILES",
    "CODE_MAP_BUDGET_DEFAULT",
    "CODE_MAP_BUDGET_MAX",
    "CODE_MAP_SIGS_PER_FILE",
    "SIG_SCAN_LINE_MAX",
    "REPO_FILE_MAX_BYTES",
    "REPO_SKIP_DIRS",
    "REPO_LOCKFILES",
)
