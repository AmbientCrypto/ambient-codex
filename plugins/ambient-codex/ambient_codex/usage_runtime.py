"""Private percentage receipts and cross-platform usage-ledger locks."""

import contextlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class UsageRuntimeDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def parse_reference_price(raw, deps=None):
    """Facade wrapper: pure reference-price parsing lives in
    ``ambient_codex.usage_pricing``."""
    _usage_pricing = deps._usage_pricing
    return _usage_pricing.parse_reference_price(raw)


def resolve_reference_price(conf=None, deps=None):
    """The frontier reference in force: env > config > documented default.
    With conf=None the config file is read once and the result memoized (the
    receipt + every log_usage record resolve this; it cannot change mid-run)."""
    REFERENCE_PRICE_DEFAULT = deps.REFERENCE_PRICE_DEFAULT
    get_ref_cache = deps.get_ref_cache
    set_ref_cache = deps.set_ref_cache
    os = deps.os
    parse_reference_price = deps.parse_reference_price
    read_config_file = deps.read_config_file
    ref_cache = get_ref_cache()
    if conf is not None:
        return (parse_reference_price(os.environ.get("AMBIENT_REFERENCE_PRICE"))
                or parse_reference_price(conf.get("AMBIENT_REFERENCE_PRICE"))
                or REFERENCE_PRICE_DEFAULT)
    if ref_cache is None:
        ref_cache = resolve_reference_price(read_config_file())
        set_ref_cache(ref_cache)
    return ref_cache


def usage_cost(model, usage, catalog=None, deps=None):
    """Facade wrapper: pure cost math lives in ``ambient_codex.usage_pricing``.
    Passes the facade's memoized catalog default, worst-case ASSUMED prices,
    and the untrusted-token coercer. (dollars, assumed) for a FINISHED run's
    token counts — unpriced model / degraded catalog over-states never
    under-states, and the caller must not claim a saving from an assumed cost."""
    ASSUMED_MAX_INPUT_PRICE = deps.ASSUMED_MAX_INPUT_PRICE
    ASSUMED_MAX_OUTPUT_PRICE = deps.ASSUMED_MAX_OUTPUT_PRICE
    _as_pos_int = deps._as_pos_int
    _usage_pricing = deps._usage_pricing
    get_pricing_catalog = deps.get_pricing_catalog
    return _usage_pricing.usage_cost(
        model, usage,
        get_pricing_catalog() if catalog is None else catalog,
        (ASSUMED_MAX_INPUT_PRICE, ASSUMED_MAX_OUTPUT_PRICE),
        _as_pos_int)


def reference_cost(usage, ref, deps=None):
    """Facade wrapper: pure cost math lives in ``ambient_codex.usage_pricing``.
    The same tokens priced at the frontier reference (input, output)."""
    _as_pos_int = deps._as_pos_int
    _usage_pricing = deps._usage_pricing
    return _usage_pricing.reference_cost(usage, ref, _as_pos_int)


def _savings_enabled(conf=None, deps=None):
    """Whether the opt-in savings/comparison note shows. OFF BY DEFAULT (founder
    hard rule): absolute cost is plan-dependent (API vs subscription), so the
    relative '%-cheaper' note appears only when the user turns it on (env
    AMBIENT_SAVINGS or `config set savings on`). A dollar figure is NEVER
    surfaced either way. Memoized on the config path like the reference price —
    it cannot change mid-run."""
    os = deps.os
    read_config_file = deps.read_config_file
    get_savings_cache = deps.get_savings_cache
    set_savings_cache = deps.set_savings_cache
    if conf is None:
        cache = get_savings_cache()
        if cache is None:
            cache = _savings_enabled(read_config_file(), deps=deps)
            set_savings_cache(cache)
        return cache
    raw = os.environ.get("AMBIENT_SAVINGS")
    if raw is None:
        raw = conf.get("AMBIENT_SAVINGS")
    return str(raw or "").strip().lower() in ("1", "on", "true")


def savings_note(model, usage, catalog=None, conf=None, deps=None):
    """Receipt suffix: ' — ~97% cheaper than a frontier model (est.)'. Reports
    only the RELATIVE saving vs a frontier reference — never a dollar figure,
    since actual billing is plan-dependent (a subscription or pay-as-you-go
    API). Honesty rules (never over-state): assumed/absent pricing OMITS any
    claim; saved-% is floored; at-or-above the reference reads 'costlier';
    estimated token counts carry '(est.)'; zero tokens → no note. OFF by default — see _savings_enabled. Never raises."""
    ASSUMED_MAX_INPUT_PRICE = deps.ASSUMED_MAX_INPUT_PRICE
    ASSUMED_MAX_OUTPUT_PRICE = deps.ASSUMED_MAX_OUTPUT_PRICE
    _as_pos_int = deps._as_pos_int
    _savings_enabled = deps._savings_enabled
    _usage_pricing = deps._usage_pricing
    get_pricing_catalog = deps.get_pricing_catalog
    resolve_reference_price = deps.resolve_reference_price
    if not _savings_enabled(conf):
        return ""
    return _usage_pricing.relative_savings_note(
        model, usage, get_pricing_catalog() if catalog is None else catalog,
        resolve_reference_price(conf),
        (ASSUMED_MAX_INPUT_PRICE, ASSUMED_MAX_OUTPUT_PRICE), _as_pos_int)


def savings_note_by_served(usage_by_model, catalog=None, conf=None, deps=None):
    """Receipt suffix for a run whose samples may have been SERVED by
    DIFFERENT models (best-of under --fallback). Pricing the token
    aggregate at the selected sample's model would OVER-state the saving
    whenever a pricier fallback served part of the tokens, so
    the true cost prices EACH model's OWN tokens and sums; the frontier
    reference prices the total. Same honesty rules as savings_note — a
    single serving model delegates to it outright, assumed pricing claims
    no saving, saved-% is floored, negative savings read 'costlier', and
    it never raises. OFF by default — see _savings_enabled."""
    ASSUMED_MAX_INPUT_PRICE = deps.ASSUMED_MAX_INPUT_PRICE
    ASSUMED_MAX_OUTPUT_PRICE = deps.ASSUMED_MAX_OUTPUT_PRICE
    _as_pos_int = deps._as_pos_int
    _savings_enabled = deps._savings_enabled
    _usage_pricing = deps._usage_pricing
    get_pricing_catalog = deps.get_pricing_catalog
    resolve_reference_price = deps.resolve_reference_price
    if not _savings_enabled(conf):
        return ""
    return _usage_pricing.relative_savings_note_by_served(
        usage_by_model,
        get_pricing_catalog() if catalog is None else catalog,
        resolve_reference_price(conf),
        (ASSUMED_MAX_INPUT_PRICE, ASSUMED_MAX_OUTPUT_PRICE), _as_pos_int)


def _lock_owner_dead(lock_path, deps=None):
    """True ONLY when the lock file's recorded owner pid is PROVABLY dead.
    Garbage/unreadable token or unknowable liveness (Windows,
    PermissionError) → False: breaking a possibly-live owner's lock would
    let two processes rewrite the store concurrently, so the
    caller must fail open instead. Read again right before any unlink to
    shrink the replace-race window."""
    _pid_alive = deps._pid_alive
    try:
        with open(lock_path, encoding="utf-8") as fh:
            pid = int(fh.read().strip() or "0")
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    return _pid_alive(pid) is False


@contextlib.contextmanager
def _fs_lock(lock_path, wait_s, deps=None):
    """FAIL-OPEN cross-process exclusive lock — the concurrency primitive for
    the fleet budget + usage ledger. Same cross-platform shape as
    _config_lock (POSIX flock; Windows O_EXCL lock file) but it NEVER exits
    or raises: after a bounded wait it yields False so the caller degrades
    to per-invocation behavior instead of blocking a legitimate call (no
    deadlock, ever). The no-fcntl path breaks a leftover lock ONLY when its
    recorded owner is provably dead — a live-but-slow owner is
    never broken into, because every caller guards a read-modify-rewrite
    critical section; on real Windows liveness is unknowable, so a crashed
    process's lock degrades every later call to fail-open per-invocation
    gating rather than risking a concurrent store rewrite."""
    _lock_owner_dead = deps._lock_owner_dead
    fcntl = deps.fcntl
    os = deps.os
    time = deps.time
    if fcntl is not None:
        fd = None
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        except OSError:
            yield False
            return
        got, waited = False, 0.0
        try:
            while not got:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    got = True
                except OSError:
                    if waited >= wait_s:
                        break
                    time.sleep(0.05)
                    waited += 0.05
            yield got
        finally:
            if got:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass
        return
    fd, waited = None, 0.0
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode())  # ownership token
        except FileExistsError:
            # never break-and-enter on mtime age alone — only a
            # PROVABLY-dead owner's lock is reclaimed (checked immediately
            # before the unlink; the O_EXCL create stays the arbiter, so a
            # racing breaker loses the create and re-evaluates the winner's
            # ALIVE token instead of breaking again).
            if _lock_owner_dead(lock_path):
                try:
                    os.unlink(lock_path)
                    continue  # reclaimed a dead owner's lock — retry the create
                except OSError:
                    pass  # unlink keeps failing (e.g. unwritable dir): do NOT
                    # spin — fall through to the wait/bail budget below.
            if waited >= wait_s:
                yield False
                return
            time.sleep(0.05)
            waited += 0.05
        except OSError:
            yield False
            return
    try:
        yield True
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def _pid_alive(pid, deps=None):
    """True/False when liveness is determinable, None when it isn't. POSIX
    only: os.kill(pid, 0) probes without signaling there, but on Windows
    signal 0 is NOT a probe (it terminates), so non-POSIX always returns
    None and pruning degrades to TTL-only."""
    os = deps.os
    if os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # SOMETHING owns that pid, but not us — it may be the reserving
        # ambient re-run under sudo, or a recycled pid owned by another
        # user. Unknowable → None, so the TTL backstop still applies
        # instead of the record being pinned alive forever.
        return None
    except OSError:
        return None
