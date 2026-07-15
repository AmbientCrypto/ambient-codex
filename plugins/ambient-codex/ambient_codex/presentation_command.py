"""Terminal sanitization, atomic config writes, curation, and lane defaults."""

import contextlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class PresentationDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def redact(text, api_key, deps=None):
    """Make model-derived text safe to print: strip the API key (endpoints
    echoing auth back must never land it in terminal output or transcripts)
    AND neutralize terminal-escape injection from untrusted model output.
    Keys shorter than 8 chars are not redacted: substring collisions would
    mangle ordinary text (real keys are far longer).
    ORDER MATTERS (M16): sanitize FIRST, then redact the key. A compromised
    endpoint could echo the key with an embedded escape (sk-abc<ESC>[0mdef) so
    the raw substring match misses it; stripping escapes first reassembles the
    contiguous key so the replace catches it. Keys are validated alphanumeric,
    so sanitizing can never corrupt a real key."""
    ANSI_RE = deps.ANSI_RE
    CTRL_RE = deps.CTRL_RE
    _KEY_PLACEHOLDER = deps._KEY_PLACEHOLDER
    os = deps.os
    if not text:
        return text
    if os.environ.get("AMBIENT_NO_SANITIZE") != "1":
        text = CTRL_RE.sub("", ANSI_RE.sub("", text))
    if api_key and len(api_key) >= 8:
        text = text.replace(api_key, _KEY_PLACEHOLDER)
    return text


def sanitize(text, deps=None):
    """Neutralize terminal-escape/control injection in untrusted NETWORK-derived
    strings (catalog model IDs, probe details) before printing. Same filter as
    redact() but with no api-key substitution — these are not secrets, just
    attacker-controllable if the endpoint is compromised/MITM'd. Coerces a
    non-str (e.g. a malformed catalog value) to str so the filter still runs."""
    redact = deps.redact
    if text is None or isinstance(text, str):
        return redact(text, "")
    return redact(str(text), "")


def _stderr_is_tty(deps=None):
    """True when stderr can take in-place \\r rewrites + escapes (B5)."""
    os = deps.os
    sys = deps.sys
    try:
        return sys.stderr.isatty() and os.environ.get("TERM", "") not in ("", "dumb")
    except Exception:  # noqa: BLE001
        return False


def _use_color(stream, deps=None):
    """Color only when it can't hurt: real TTY, TERM not dumb, NO_COLOR unset
    (https://no-color.org). Per-stream so `ambient-codex models | grep` gets clean
    bytes while stderr stays colored."""
    os = deps.os
    if os.environ.get("NO_COLOR") is not None \
            or os.environ.get("AMBIENT_NO_COLOR"):
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    try:
        return stream.isatty()
    except Exception:  # noqa: BLE001
        return False


def paint(s, code, stream=None, deps=None):
    """Minimal ANSI wrap (bold=1 dim=2 red=31 green=32 yellow=33) — NEVER used
    on --json/--raw output paths."""
    _use_color = deps._use_color
    sys = deps.sys
    stream = stream if stream is not None else sys.stdout
    return f"\033[{code}m{s}\033[0m" if _use_color(stream) else s


@contextlib.contextmanager
def _config_lock(conf_dir, deps=None):
    """Cross-platform exclusive lock for config writes (B1). POSIX uses flock;
    where fcntl is absent (Windows) it spins on an O_EXCL lock file and breaks a
    stale (>30s) lock so a crashed writer can't wedge the config forever."""
    _claim_state_dir = deps._claim_state_dir
    _config_store = deps._config_store
    fcntl = deps.fcntl
    sys = deps.sys
    time = deps.time
    with _config_store.config_lock(
        conf_dir,
        lambda: _claim_state_dir(conf_dir),
        fcntl,
        sys.exit,
        time.time,
        time.sleep,
    ):
        yield


def _private_dir(path, deps=None):
    """Create/heal a directory as owner-only. Cache entries quote the user's
    proprietary code and usage records their activity — the 0600 discipline of
    the env file applies to everything under ~/.config/ambient-codex (
    cache/usage were world-readable)."""
    _config_store = deps._config_store
    return _config_store.private_dir(path)


def save_config_values(updates, deps=None):
    """Atomically + concurrency-safely rewrite the env file (0600): replace
    managed keys once, drop duplicate managed-key lines, preserve the rest. A
    lock file serializes the read-modify-write-rename so two ambient processes
    (multiple sessions) can't lose updates or leave a torn file; the temp file
    has a unique name so concurrent writers never share one inode.
    `updates` may be a CALLABLE taking the freshly-parsed config dict and
    returning the updates dict — list-merge writers (curation) must compute
    their merge INSIDE the lock or two terminals lose each other's entries
."""
    CONFIG_PATH = deps.CONFIG_PATH
    _config_lock = deps._config_lock
    _config_store = deps._config_store
    sys = deps.sys
    return _config_store.save_config_values(
        CONFIG_PATH, updates, _config_lock, sys.exit
    )


def _split_csv(raw, deps=None):
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def curation(conf, deps=None):
    """Model-curation state from env/config: (allow, hide, show,
    notes). allow/hide are lists of ids or fnmatch globs; show is a list of
    EXACT ids that override everything (so `curate show qwen/foo` can surface
    one model out of a hidden glob — without it, a glob hide
    was un-overridable); notes maps id -> label. Curation affects SURFACING
    and AUTOMATIC selection only — explicit -m/`use <id>` always works (model
    choice is SACRED). Parses defensively: junk config can never crash."""
    DEFAULT_MODEL_NOTES = deps.DEFAULT_MODEL_NOTES
    _split_csv = deps._split_csv
    json = deps.json
    os = deps.os
    sys = deps.sys
    allow = _split_csv(os.environ.get("AMBIENT_MODELS_ALLOW")
                       or conf.get("AMBIENT_MODELS_ALLOW"))
    hide = _split_csv(os.environ.get("AMBIENT_MODELS_HIDE")
                      or conf.get("AMBIENT_MODELS_HIDE"))
    show = _split_csv(os.environ.get("AMBIENT_MODELS_SHOW")
                      or conf.get("AMBIENT_MODELS_SHOW"))
    notes = dict(DEFAULT_MODEL_NOTES)
    raw_notes = conf.get("AMBIENT_MODEL_NOTES")
    if raw_notes:
        try:
            parsed = json.loads(raw_notes)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(k, str) and isinstance(v, str):
                        notes[k] = v  # "" = the user cleared a default note
        except json.JSONDecodeError:
            print("ambient: AMBIENT_MODEL_NOTES in config is malformed — "
                  "ignoring it (reset: ambient-codex curate reset)", file=sys.stderr)
    return allow, hide, show, notes


def is_hidden(model_id, allow, hide, show=(), deps=None):
    """True when curation removes this model from menus/automatic selection.
    Precedence: an exact SHOW entry always surfaces; then ALLOW (when
    non-empty) filters; HIDE applies within the result."""
    fnmatch = deps.fnmatch
    if model_id in show:
        return False
    if allow and not any(fnmatch.fnmatchcase(model_id, g) for g in allow):
        return True
    return any(fnmatch.fnmatchcase(model_id, g) for g in hide)


def note_if_hidden(model_id, conf, source="-m", deps=None):
    """One stderr line when an explicitly chosen model is curated out —
    explicit choice always wins, but silently ignoring the user's curation
    would look like a bug."""
    curation = deps.curation
    is_auto_model = deps.is_auto_model
    is_hidden = deps.is_hidden
    sys = deps.sys
    if is_auto_model(model_id):
        return  # a pseudo-spec, not a catalog id — nothing to note
    allow, hide, show, _ = curation(conf)
    if is_hidden(model_id, allow, hide, show):
        print(
            f"ambient: note — '{model_id}' is hidden by your model curation; "
            f"using it anyway (explicit {source} always wins). Surface it "
            f"with: ambient-codex curate show {model_id}",
            file=sys.stderr,
        )


def build_banner(deps=None):
    """The bare-`ambient` command showcase, with a live status footer built
    from purely local reads (zero network) so a configured user and a fresh
    install each see the right next step."""
    KEY_CONSOLE_URL = deps.KEY_CONSOLE_URL
    LAUNCHER_NAME = deps.LAUNCHER_NAME
    __version__ = deps.__version__
    argparse = deps.argparse
    read_config_file = deps.read_config_file
    resolve_key_and_backend = deps.resolve_key_and_backend
    resolve_model = deps.resolve_model
    conf = read_config_file()
    key, backend = resolve_key_and_backend(conf)
    ns = argparse.Namespace(model=None)
    if key:
        _mode = (conf.get('AMBIENT_DELEGATE') or 'off').lower()
        _mode_str = "TAKEOVER" if _mode == "takeover" else f"delegate {_mode}"
        status = (f"status: key configured ({backend}) · {_mode_str} · "
                  f"model {resolve_model(ns, conf, 'chat')}")
    else:
        status = (f"status: no API key yet — run: {LAUNCHER_NAME} setup   "
                  f"(keys at {KEY_CONSOLE_URL})")
    return f"""ambient-codex {__version__} — open frontier models in your terminal,
from the Ambient decentralized inference network (ambient.xyz).

Get started
  ambient-codex setup      store + verify your API key (one time)
  ambient-codex models     see which models are serving right now
  ambient-codex use        pick your sticky default model

Do work
  ambient-codex ask "question"   one-shot answer; big docs: cat doc.txt | ambient-codex ask "sum" -
  ambient-codex audit FILE...    adversarial code review; diffs: git diff | ambient-codex audit
  ambient-codex code "task"      generate code (-f app.py attaches context)
  ambient-codex build "task"     plan + generate a whole set of files (never executes)
  ambient-codex agent      interactive agentic terminal on Ambient (opencode)

Keep it healthy
  ambient-codex doctor     pinpoint key / funds / model-availability / network trouble
  ambient-codex usage      local token summary (--days N)
  ambient-codex mode on|off|takeover   delegate / full-takeover mode for Codex

{status}
more:   ambient-codex -h · use the $ambient skill inside Codex"""


def print_welcome_panel(models, where, probe_detail, conf, funds_issue=False, deps=None):
    """Post-verification command showcase — the moment a stranger decides the
    tool is worth keeping. Called from setup success and first-use onboarding."""
    KEY_CONSOLE_URL = deps.KEY_CONSOLE_URL
    argparse = deps.argparse
    ready_model_ids = deps.ready_model_ids
    resolve_model = deps.resolve_model
    ready = len(ready_model_ids(models))
    ns = argparse.Namespace(model=None)
    chat_model = resolve_model(ns, conf, "chat")
    net = (f"Network:   {len(models)} models, {ready} serving right now "
           "(capacity scales with demand — `ambient-codex models`)")
    if not ready:
        net += ("\n           models spin up as demand arrives — your key is "
                "verified; check `ambient-codex models` in a few minutes")
    print(f"Key verified — {probe_detail}.")
    print(f"Stored in: {where}")
    print(net)
    print("\nWelcome to Ambient — open frontier models from your terminal.\n")
    if funds_issue:
        print(f"  NOTE: your account is OUT OF FUNDS — completions will fail until\n"
              f"  you top up at {KEY_CONSOLE_URL}, then run: ambient-codex doctor\n")
    else:
        print("  Try it now (30 seconds):\n"
              '    ambient-codex ask "Reply with exactly: AMBIENT-OK"\n')
    print("""  Everyday:
    git diff | ambient-codex audit              second-opinion review of your changes
    ambient-codex audit src/*.py --focus security   audit files (any size — auto-split)
    ambient-codex ask "question"                one-shot Q&A (pipe docs: cat doc | ambient-codex ask "…" -)
    ambient-codex code "write a ..."            code generation
    ambient-codex build "make a ..." --dir out  plan + generate a set of files (never executes)

  Manage:
    ambient-codex models         what's serving (READY) right now
    ambient-codex use            pick your sticky default model
    ambient-codex usage          local token summary
    ambient-codex doctor         one-command diagnosis when anything fails
    ambient-codex link           put `ambient` on your PATH
    ambient-codex setup --force  rotate/replace your key · --remove deletes it

  In Codex: use the $ambient skill or say "use Ambient" · ambient-codex mode on = delegate mode
            · ambient-codex mode takeover = Ambient handles substantive work (ambient-codex mode off)
""")
    print(f"Default model: {chat_model} — change any time: ambient-codex use")


def key_paste_problem(key, deps=None):
    """Local sanity check of a pasted key BEFORE any network call. Malformed
    pastes (smart quotes, embedded whitespace, shell fragments) must produce a
    human message — not a UnicodeEncodeError crash in urllib header
    encoding, and never a late, misleading 'Keychain write failed' (the
    keychain writer rejects quotes/backslashes). Returns None when plausible."""
    KEY_CONSOLE_URL = deps.KEY_CONSOLE_URL
    re = deps.re
    if not key:
        return "That was empty — paste the key value."
    if re.search(r"\s", key):
        return ("That does not look like an API key (it contains spaces or "
                "line breaks). Paste just the key value.")
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in key):
        return ("The paste contains characters that cannot appear in an "
                "Ambient key (smart quotes / unicode). Re-copy it from "
                f"{KEY_CONSOLE_URL}.")
    if len(key) < 16:
        return "That looks too short to be an Ambient key."
    if '"' in key or "\\" in key:
        return ("That does not look like a valid key (it contains quote or "
                "backslash characters). Re-copy it from the console.")
    return None


def model_map(conf, deps=None):
    """Parse AMBIENT_MODEL_MAP — the USER's per-phase routing config, e.g.
    "map=z-ai/glm-5.2,reduce=moonshotai/kimi-k2.7-code,chat=…" — into
    {phase: model_id}. Phases: chat, code, map (the bulk lane), reduce (the
    map-reduce synthesis step). Explicit -m / --reduce-model on the CLI always
    override it. Defensive: junk config can never crash."""
    _model_config = deps._model_config
    os = deps.os
    return _model_config.model_map(conf, os.environ)


def resolve_model(args, conf, kind="chat", phase=None, deps=None):
    """Resolution order: -m flag > AMBIENT_MODEL_MAP phase entry > env var >
    saved default > built-in. `phase` names the AMBIENT_MODEL_MAP slot to
    consult (default: same as `kind`); the map is the user's own explicit
    config, so consulting it never violates the sacred-model rule — and the
    -m flag still beats everything."""
    DEFAULT_CODE_MODEL = deps.DEFAULT_CODE_MODEL
    DEFAULT_MODEL = deps.DEFAULT_MODEL
    _model_config = deps._model_config
    os = deps.os
    return _model_config.resolve_model(
        args, conf, kind, phase, os.environ, DEFAULT_MODEL, DEFAULT_CODE_MODEL,
    )
