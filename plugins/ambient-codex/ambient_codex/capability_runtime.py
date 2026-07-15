"""Observed token telemetry and adaptive capability persistence."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class CapabilityDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def _telemetry_enabled(deps=None):
    """AMBIENT_TELEMETRY=off|0|false|no keeps the static token constants."""
    os = deps.os
    return (os.environ.get("AMBIENT_TELEMETRY", "").strip().lower()
            not in ("off", "0", "false", "no"))


def observed_cpt(model, deps=None):
    """Per-model OBSERVED chars-per-token from the local usage ledger — a
    recent-weighted EWMA over records that carry both the input char count
    and a REAL (non-estimated) prompt_tokens figure — or None when there is
    no usable history (callers then fall back to EXACTLY the static
    CHARS_PER_TOKEN default, so a fresh install is byte-identical). Every
    sample and the result are clamped to [TELEMETRY_CPT_MIN,
    TELEMETRY_CPT_MAX] so a corrupt ledger can never skew budgets. The
    ledger is read ONCE per process (memoized) — model_profile sits on the
    hot path and must not re-hit the disk per call."""
    TELEMETRY_CPT_MIN = deps.TELEMETRY_CPT_MIN
    TELEMETRY_CPT_MAX = deps.TELEMETRY_CPT_MAX
    TELEMETRY_EWMA_ALPHA = deps.TELEMETRY_EWMA_ALPHA
    USAGE_PATH = deps.USAGE_PATH
    _telemetry_core = deps._telemetry_core
    _telemetry_enabled = deps._telemetry_enabled
    get_telemetry_cache = deps.get_telemetry_cache
    set_telemetry_cache = deps.set_telemetry_cache
    cache = get_telemetry_cache()
    value, cache = _telemetry_core.observed_cpt(
        model, _telemetry_enabled(), USAGE_PATH, cache,
        TELEMETRY_CPT_MIN, TELEMETRY_CPT_MAX, TELEMETRY_EWMA_ALPHA,
    )
    set_telemetry_cache(cache)
    return value


def _effective_cpt(model, deps=None):
    """The chars-per-token figure for SIZING `model`'s budget (how much content
    fits): the ledger-observed EWMA when real usage history exists (self-
    calibrating token math — smarter with use), else EXACTLY the static
    CHARS_PER_TOKEN default so a no-history run never shifts by a byte."""
    CHARS_PER_TOKEN = deps.CHARS_PER_TOKEN
    observed_cpt = deps.observed_cpt
    return observed_cpt(model) or CHARS_PER_TOKEN


def _cost_cpt(model, deps=None):
    """The chars-per-token figure for COST / SPEND-GATE math. Telemetry may only
    make the gate MORE conservative, never less: a higher observed cpt (fewer
    tokens per char) would LOWER the estimated input tokens and could let a run
    the static gate would block slip through, so the cost path clamps cpt to at
    most the static default (min(observed, default)). A lower observed cpt (a
    model that really uses MORE tokens per char) still tightens the gate. With
    no history this is exactly CHARS_PER_TOKEN — byte-identical to before."""
    CHARS_PER_TOKEN = deps.CHARS_PER_TOKEN
    observed_cpt = deps.observed_cpt
    return min(observed_cpt(model) or CHARS_PER_TOKEN, CHARS_PER_TOKEN)


def _read_caps_file(deps=None):
    """Fresh read of the capability store from disk (no memo). Missing/corrupt
    → {} (learning restarts clean, never an error)."""
    CAPABILITY_PATH = deps.CAPABILITY_PATH
    json = deps.json
    try:
        with open(CAPABILITY_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_caps(deps=None):
    """The learned capability store, read ONCE per process (memoized). Disabled
    telemetry yields an empty table (no history)."""
    _read_caps_file = deps._read_caps_file
    _telemetry_enabled = deps._telemetry_enabled
    get_cap_cache = deps.get_cap_cache
    set_cap_cache = deps.set_cap_cache
    cache = get_cap_cache()
    if cache is not None:
        return cache
    cache = _read_caps_file() if _telemetry_enabled() else {}
    set_cap_cache(cache)
    return cache


def _cap_recent(model, dim, deps=None):
    """The last-K outcomes for (model, dim) as a list of 0/1, or [] if none/
    malformed — the one place that tolerates a hand-edited or corrupt store.
    Guards EVERY layer (a `{"m": "bad"}` model entry must not crash the audit
    path that calls cap_state)."""
    CAP_RECENT_K = deps.CAP_RECENT_K
    _load_caps = deps._load_caps
    model_rec = _load_caps().get(model)
    if not isinstance(model_rec, dict):
        return []
    rec = model_rec.get(dim)
    if not isinstance(rec, dict):
        return []
    recent = rec.get("recent")
    if not isinstance(recent, list):
        return []
    return [1 if x else 0 for x in recent][-CAP_RECENT_K:]


def cap_state(model, dim, deps=None):
    """Learned state for (model, dimension): 'ok' | 'unreliable' | 'unknown'.
    'unknown' when learning is off or there is no history (the caller then
    tries the optimistic path). Hysteresis keyed on the MOST RECENT outcome so
    a stale success can't mask fresh failures: the latest attempt succeeded =>
    'ok' (recovered — models improve); the latest failed AND the last
    CAP_FAIL_THRESHOLD attempts all failed => 'unreliable'; otherwise
    'unknown'."""
    CAP_FAIL_THRESHOLD = deps.CAP_FAIL_THRESHOLD
    _cap_recent = deps._cap_recent
    _telemetry_enabled = deps._telemetry_enabled
    if not model or not _telemetry_enabled():
        return "unknown"
    recent = _cap_recent(model, dim)
    if not recent:
        return "unknown"
    if recent[-1] == 1:
        return "ok"
    if len(recent) >= CAP_FAIL_THRESHOLD and not any(recent[-CAP_FAIL_THRESHOLD:]):
        return "unreliable"
    return "unknown"


def _try_caps_lock(conf_dir, deps=None):
    """Best-effort NON-BLOCKING exclusive lock (POSIX flock) for the capability
    store, so a telemetry write never hangs. Returns an fd on success, else None
    (caller proceeds unlocked). Where fcntl is absent (Windows) we do NOT use an
    O_EXCL lock file — its unlink-on-release races with a concurrent holder
    (Codex round 4) — and simply run unlocked; capability learning is
    best-effort and a rare lost outcome self-heals."""
    fcntl = deps.fcntl
    os = deps.os
    if fcntl is None:
        return None
    lock_path = os.path.join(conf_dir, ".caps.lock")
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        try:
            os.close(fd)
        except (OSError, NameError, UnboundLocalError):
            pass
        return None


def _write_caps(table, deps=None):
    """Atomic rewrite (mkstemp + replace, 0600) — same discipline as the
    reservation/cache stores; a crash mid-write never leaves a torn file."""
    CAPABILITY_PATH = deps.CAPABILITY_PATH
    json = deps.json
    os = deps.os
    tempfile = deps.tempfile
    d = os.path.dirname(CAPABILITY_PATH)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".caps-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(table, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CAPABILITY_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_cap(model, dim, ok, deps=None):
    """Append one real outcome (ok: bool) for (model, dimension) and persist.
    Best-effort and fully swallowed on error — a QA/telemetry write must NEVER
    break a real result the user is waiting on. No-op when learning is off."""
    CAPABILITY_PATH = deps.CAPABILITY_PATH
    CAP_RECENT_K = deps.CAP_RECENT_K
    _read_caps_file = deps._read_caps_file
    _telemetry_enabled = deps._telemetry_enabled
    _try_caps_lock = deps._try_caps_lock
    _write_caps = deps._write_caps
    fcntl = deps.fcntl
    os = deps.os
    set_cap_cache = deps.set_cap_cache
    time = deps.time
    if not model or not _telemetry_enabled():
        return
    lock_fd = None
    try:
        conf_dir = os.path.dirname(CAPABILITY_PATH)
        os.makedirs(conf_dir, exist_ok=True)
        # NON-BLOCKING lock: a telemetry write must NEVER hang the real result a
        # caller is waiting on (Codex round 2: the blocking config lock could
        # stall record_cap). If a sibling holds it, we skip the lock and do a
        # best-effort read-modify-write — at worst one outcome races, never a
        # hang. Re-read fresh from disk so a held lock's writer isn't clobbered
        # when we DID get the lock.
        lock_fd = _try_caps_lock(conf_dir)
        table = _read_caps_file()
        model_rec = dict(table.get(model) if isinstance(table.get(model), dict) else {})
        dim_rec = model_rec.get(dim)
        dim_rec = dict(dim_rec) if isinstance(dim_rec, dict) else {}
        prior = dim_rec.get("recent")
        recent = [1 if x else 0 for x in prior] if isinstance(prior, list) else []
        recent.append(1 if ok else 0)
        dim_rec["recent"] = recent[-CAP_RECENT_K:]
        dim_rec["updated"] = time.time()
        model_rec[dim] = dim_rec
        table[model] = model_rec
        _write_caps(table)
        set_cap_cache(table)
    except Exception:
        pass  # never propagate a telemetry failure into the caller
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except OSError:
                pass
