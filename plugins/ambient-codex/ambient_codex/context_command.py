"""Bounded stdin/file intake, code maps, density chunking, and reduce adapters."""

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .constants import CHUNK_CHARS


@dataclass(frozen=True)
class ContextDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def read_stdin_if_piped(deps=None):
    STDIN_WAIT_MAX_S = deps.STDIN_WAIT_MAX_S
    STDIN_WAIT_S = deps.STDIN_WAIT_S
    _intake_core = deps._intake_core
    _read_stdin_bounded = deps._read_stdin_bounded
    _stdin_read_and_decode = deps._stdin_read_and_decode
    os = deps.os
    select = deps.select
    sys = deps.sys
    try:
        if sys.stdin.isatty():
            return ""
    except (OSError, ValueError, AttributeError):
        pass
    wait_s = _intake_core.stdin_wait_seconds(
        os.environ, float(STDIN_WAIT_S), float(STDIN_WAIT_MAX_S)
    )
    # Guard against an IDE/CI wrapper that holds stdin open with NO data: wait
    # briefly for readability instead of blocking forever. A real pipe (git diff,
    # a file redirect) is readable immediately.
    no_data_msg = (
        f"ambient: stdin produced no data within {wait_s:g}s — proceeding "
        "without it (slow pipe? set AMBIENT_STDIN_WAIT=<seconds>)")
    ready = _intake_core.stdin_ready(sys.stdin, select.select, wait_s)
    if ready is None:
        # select() is unavailable here (Windows pipes, some IDE/CI wrappers) —
        # exactly the case the guard exists for. Do NOT fall through to an
        # UNBOUNDED blocking read: a held-open, dataless stdin would hang forever
        # (a real pipe finishes at EOF well within the wait). Bound it in a thread.
        return _read_stdin_bounded(wait_s, no_data_msg)
    if not ready:
        print(no_data_msg, file=sys.stderr)
        return ""
    return _stdin_read_and_decode()

def _stdin_read_and_decode(deps=None):
    """Read stdin fully (bounded by ABS_MAX_CHARS) and decode lossily — blocks
    until EOF, so call only when data is present or the read is time-bounded."""
    ABS_MAX_CHARS = deps.ABS_MAX_CHARS
    _argv_command = deps._argv_command
    _fail_exit = deps._fail_exit
    _intake_core = deps._intake_core
    sys = deps.sys
    data, warnings, error = _intake_core.read_stdin_text(
        sys.stdin, ABS_MAX_CHARS
    )
    for warning in warnings:
        print(f"ambient: {warning}", file=sys.stderr)
    if error is not None:
        _fail_exit(None, _argv_command(), "input", error)
    return data or ""

def _read_stdin_bounded(wait_s, no_data_msg, deps=None):
    """Read stdin without EVER hanging: run the blocking read in a daemon thread
    and give up after wait_s. A real pipe reaches EOF within the wait; a held-open
    dataless stdin (where select() is unavailable) would otherwise block forever."""
    _intake_core = deps._intake_core
    _stdin_read_and_decode = deps._stdin_read_and_decode
    sys = deps.sys
    threading = deps.threading
    data, timed_out = _intake_core.read_stdin_bounded(
        _stdin_read_and_decode, wait_s, threading.Thread
    )
    if timed_out:                   # still blocked on a dataless pipe — give up
        print(no_data_msg, file=sys.stderr)
        return ""
    return data

def warn_if_stdin_ignored(hint, deps=None):
    """One stderr note when piped data exists but this invocation will not read
    it — the user pays for a wrong-context answer otherwise, and silence was
    the bug. Zero-timeout peek; never blocks. EOF is
    'readable' with zero bytes (`< /dev/null` from any agent harness), so only
    warn when bytes are ACTUALLY waiting — a false note in front of --json
    output breaks machine consumers (live-battery finding)."""
    _intake_core = deps._intake_core
    fcntl = deps.fcntl
    select = deps.select
    sys = deps.sys
    if _intake_core.stdin_has_waiting_data(sys.stdin, select.select, fcntl):
        print(
            f"ambient: note — data is waiting on stdin but was NOT read "
            f"({hint})",
            file=sys.stderr,
        )

def read_files(paths, deps=None):
    """Returns [(path, content)] pairs. Oversized inputs are map-reduced later,
    not refused. Skips unreadable/non-regular/binary/empty files with a warning
    rather than aborting the batch or hanging on a device/FIFO."""
    ABS_MAX_CHARS = deps.ABS_MAX_CHARS
    _argv_command = deps._argv_command
    _fail_exit = deps._fail_exit
    _intake_core = deps._intake_core
    sys = deps.sys
    chunks, warnings, overflow_path = _intake_core.read_files(
        tuple(paths), ABS_MAX_CHARS
    )
    for warning in warnings:
        print(f"ambient: {warning}", file=sys.stderr)
    if overflow_path is not None:
        _fail_exit(
            None, _argv_command(), "input",
            f"input exceeds {ABS_MAX_CHARS:,} chars at {overflow_path} "
            "(too large for one request) — split the job into smaller pieces."
        )
    return list(chunks)

def files_block(chunks, deps=None):
    _map_reduce_core = deps._map_reduce_core
    return _map_reduce_core.files_block(chunks)

def _chunk_ranges(chunk_text, deps=None):
    """Extract 'path lines a-b' coverage labels from a packed chunk's block
    headers, so a failed chunk names exactly which file:lines went unreviewed
    (A8 coverage manifest)."""
    _map_reduce_core = deps._map_reduce_core
    return _map_reduce_core.chunk_ranges(chunk_text)

def code_map_budget(single_shot_chars=None, deps=None):
    """Per-model repo-map budget (5a): a 400k-window model deserves a far
    richer cross-file map than the old flat 4000 chars, capped so the map can
    never crowd real code out of a chunk. Safe default when the window is
    unknown (offline catalog / direct callers)."""
    CODE_MAP_BUDGET_DEFAULT = deps.CODE_MAP_BUDGET_DEFAULT
    CODE_MAP_BUDGET_MAX = deps.CODE_MAP_BUDGET_MAX
    _map_reduce_core = deps._map_reduce_core
    return _map_reduce_core.code_map_budget(
        single_shot_chars, CODE_MAP_BUDGET_DEFAULT, CODE_MAP_BUDGET_MAX)

def _sigs_python(raw, deps=None):
    ast = deps.ast
    sigs = []
    try:
        for n in ast.parse(raw).body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = ", ".join(a.arg for a in n.args.args)
                sigs.append(f"def {n.name}({params})")
            elif isinstance(n, ast.ClassDef):
                sigs.append(f"class {n.name}")
    except (SyntaxError, ValueError):
        pass
    return sigs

# Regex signature extractors for the top non-Python languages (5a). Cheap
# column-0 / brace-depth heuristic — NOT a parser: each entry is
# (max_brace_depth, compiled regex, template). Applied line-by-line; the
# brace depth is tracked naively (string/comment-blind), which is robust
# enough for a signatures-only map and costs nothing.
_C_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "return", "else", "do", "sizeof",
    "catch", "new", "throw", "case", "using", "typedef", "define",
})
_C_TYPE_SIGNATURE_MARKERS = ("class", "struct", "interface", "enum")
# Backtracking-safety contract for EVERY pattern below: no
# character class may mix word chars with \s (each whitespace run must have
# exactly ONE possible consumer), and every repeated group of "type tokens"
# is hard-bounded ({1,8} / {0,6}) so a failing match can't explore an
# exponential split space. The old C-family return-type pattern violated
# both ([\w:<>\[\],\.\s]+ then \s+ then [\s\*&]+) and hung build_code_map()
# — which runs on EVERY audit input — on a long run of spaces.
_C_MODS = (r"(?:public|private|protected|static|final|abstract|sealed|"
           r"partial|virtual|override|async|inline|extern|constexpr|"
           r"unsigned|signed|struct|const)")
_SIG_PATTERNS = {
    "js": [
        (0, re.compile(r"^(?:export\s+(?:default\s+)?)?(?:declare\s+)?"
                       r"(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
         "function {0}"),
        (0, re.compile(r"^(?:export\s+)?(?:const|let|var)\s+"
                       r"([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?:async\s+)?"
                       r"(?:\([^()\n]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=>"),
         "{0}()"),
        (0, re.compile(r"^(?:export\s+(?:default\s+)?)?(?:declare\s+)?"
                       r"(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
         "class {0}"),
        (0, re.compile(r"^(?:export\s+)?(?:declare\s+)?"
                       r"(interface|enum)\s+([A-Za-z_$][\w$]*)"),
         "{0} {1}"),
        (0, re.compile(r"^(?:export\s+)?(?:declare\s+)?"
                       r"type\s+([A-Za-z_$][\w$]*)\s*="),
         "type {0}"),
    ],
    "go": [
        (0, re.compile(r"^func\s+(?:\([^()]*\)\s*)?([A-Za-z_]\w*)"),
         "func {0}"),
        (0, re.compile(r"^type\s+([A-Za-z_]\w*)\s+(struct|interface)\b"),
         "type {0} {1}"),
    ],
    "rust": [
        (1, re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?"
                       r"(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
                       r'(?:extern\s+"[^"]*"\s+)?fn\s+([A-Za-z_]\w*)'),
         "fn {0}"),
        (0, re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?"
                       r"(struct|enum|trait|union)\s+([A-Za-z_]\w*)"),
         "{0} {1}"),
        (0, re.compile(r"^impl\b\s*(.{0,60}?)\s*\{?\s*$"), "impl {0}"),
        (0, re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)"),
         "mod {0}"),
    ],
    # Java/C#/C/C++: type declarations near the top plus return-type NAME(
    # signatures at brace depth 0 (C/C++ functions) or 1 (methods in a
    # top-level class). Keyword hits (if/for/while…) are filtered out.
    "c": [
        (1, re.compile(r"^\s*(?:" + _C_MODS + r"\s+)*"
                       r"(?:[\w:<>\[\],.]+(?:\s*[*&]+)?\s+){1,8}"
                       r"(?:[*&]+\s*)*([A-Za-z_]\w*)\s*\("),
         "{0}()"),
        (1, re.compile(r"^\s*(?:[A-Za-z_]\w*\s+){0,6}"
                       r"(class|struct|interface|enum)\s+([A-Za-z_]\w*)"),
         "{0} {1}"),
    ],
    "ruby": [
        (0, re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)"),
         "def {0}"),
        (0, re.compile(r"^(class|module)\s+([A-Z]\w*)"), "{0} {1}"),
    ],
}
_SIG_EXT = {
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js",
    ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".java": "c", ".cs": "c", ".c": "c", ".h": "c", ".cc": "c",
    ".cpp": "c", ".cxx": "c", ".hpp": "c", ".hh": "c", ".m": "c",
}


def _sigs_regex(text, lang, deps=None):
    """Line-scan signature extraction with naive brace-depth tracking. Ruby
    has no braces — its patterns simply match at any point (def is expected
    indented inside class/module bodies)."""
    CODE_MAP_SIGS_PER_FILE = deps.CODE_MAP_SIGS_PER_FILE
    SIG_SCAN_LINE_MAX = deps.SIG_SCAN_LINE_MAX
    _C_KEYWORDS = deps._C_KEYWORDS
    _C_TYPE_SIGNATURE_MARKERS = deps._C_TYPE_SIGNATURE_MARKERS
    _SIG_PATTERNS = deps._SIG_PATTERNS
    re = deps.re
    sigs, depth = [], 0
    braces = lang != "ruby"
    for line in text.splitlines():
        if len(sigs) >= CODE_MAP_SIGS_PER_FILE:
            sigs.append("…")
            break
        if len(line) > SIG_SCAN_LINE_MAX:
            # A minified/pathological line carries no useful column-0
            # signature — and it is exactly where regex backtracking blows
            # up. Brace depth is still tracked (linear count).
            if braces:
                depth = max(0, depth + line.count("{") - line.count("}"))
            continue
        if (lang == "c" and "(" not in line
                and not any(marker in line
                            for marker in _C_TYPE_SIGNATURE_MARKERS)):
            # Both C-family patterns require either a parameter list or one of
            # the declaration words above. Avoid running regexes over long runs
            # of whitespace that cannot possibly become a signature.
            if braces:
                depth = max(0, depth + line.count("{") - line.count("}"))
            continue
        first = re.match(r"\s*([A-Za-z_]\w*)", line)
        if lang == "c" and first and first.group(1) in _C_KEYWORDS:
            pass  # `return foo(x);` / `if (…)` — a statement, never a signature
        else:
            for max_depth, rx, template in _SIG_PATTERNS[lang]:
                if braces and depth > max_depth:
                    continue
                m = rx.match(line)
                if m:
                    name = m.group(m.lastindex or 1)
                    if lang == "c" and name in _C_KEYWORDS:
                        continue
                    sigs.append(template.format(*m.groups()))
                    break
        if braces:
            depth = max(0, depth + line.count("{") - line.count("}"))
    return sigs

def _file_signatures(path, text, deps=None):
    """Top-level signatures for one file — ast for Python (exact), regex
    heuristics for the other top languages (5a), [] when the language is
    unknown. Gutter prefixes ('  42| ') are stripped first."""
    _SIG_EXT = deps._SIG_EXT
    _sigs_python = deps._sigs_python
    _sigs_regex = deps._sigs_regex
    os = deps.os
    re = deps.re
    raw = re.sub(r"^ *\d+\| ", "", text, flags=re.M)
    if path.endswith(".py"):
        return _sigs_python(raw)
    lang = _SIG_EXT.get(os.path.splitext(path)[1].lower())
    if lang is None:
        return []
    return _sigs_regex(raw, lang)

def build_code_map(labeled, budget=None, deps=None):
    """A compact repo map — file list + top-level signatures — prepended to
    EVERY chunk so a caller in one chunk and its callee in another can still be
    cross-checked (A7). Budgeted so it can't push a chunk past context; the
    budget scales with the map model (code_map_budget). When the map exceeds
    its budget, whole files are dropped and an EXPLICIT '(+N files omitted)'
    marker is appended — the model must know coverage is partial (5a), never
    a silent mid-line truncation."""
    CODE_MAP_BUDGET_DEFAULT = deps.CODE_MAP_BUDGET_DEFAULT
    _file_signatures = deps._file_signatures
    if budget is None:
        budget = CODE_MAP_BUDGET_DEFAULT
    header = "REPO MAP (signatures only — full bodies may live in another chunk):"
    entry_cap = max(200, budget // 4)
    entries = []
    for label, text in labeled:
        path = label.split(" [")[0]
        sigs = _file_signatures(path, text)
        entry = f"  {path}: " + (", ".join(sigs) if sigs
                                 else f"{len(text):,} chars")
        if len(entry) > entry_cap:
            entry = entry[:entry_cap] + " …"
        entries.append(entry)
    lines, used, shown = [header], len(header), 0
    marker_reserve = 60  # room for the omission marker, guaranteed to fit
    for entry in entries:
        if used + 1 + len(entry) > budget - marker_reserve:
            break
        lines.append(entry)
        used += 1 + len(entry)
        shown += 1
    if shown < len(entries):
        lines.append(f"  (+{len(entries) - shown} files omitted from the "
                     "repo map)")
    return "\n".join(lines)

def _py_break_lines(text, deps=None):
    """1-based line numbers that start a TOP-LEVEL def/class in a (possibly
    gutter-prefixed) Python source — preferred chunk boundaries so a function is
    never cut mid-body (A6). Empty set on non-.py or parse failure → the caller
    falls back to the plain line split. Line count is preserved 1:1."""
    ast = deps.ast
    re = deps.re
    raw = re.sub(r"^ *\d+\| ", "", text, flags=re.M)  # strip any gutters
    try:
        tree = ast.parse(raw)
    except (SyntaxError, ValueError):
        return set()
    return {n.lineno for n in getattr(tree, "body", [])
            if hasattr(n, "lineno")}

def density_factor(text, deps=None):
    """How much char-based sizing must SHRINK for token-dense text. All budgets
    assume CHARS_PER_TOKEN=3.2 (code/English); CJK runs ~1-1.5 chars/token, so
    a 120k-char Chinese doc is 2-3x the assumed tokens and would 400 on context
    at 'correct' char sizing. Sampled, linear blend:
    pure-ASCII → 1.0, heavily-non-ASCII → ~2.6."""
    _chunking = deps._chunking
    return _chunking.density_factor(text)


def _chunk_break_lines(label, text, deps=None):
    """Return preferred split points for a labeled Python source item."""
    _py_break_lines = deps._py_break_lines
    return (_py_break_lines(text)
            if label.endswith(".py") or ".py " in label else set())

def pack_chunks(labeled_chunks, chunk_chars=CHUNK_CHARS, deps=None):
    """Facade seam for extracted size-safe chunk packing."""
    _chunk_break_lines = deps._chunk_break_lines
    _chunking = deps._chunking
    return _chunking.pack_chunks(
        labeled_chunks, chunk_chars, break_lines=_chunk_break_lines)

def _resolve_parallel(args, deps=None):
    """Fan-out width: `--parallel` flag > AMBIENT_MAX_PARALLEL env > the
    MAX_PARALLEL_CHUNKS default. Clamped to 1-16 — bounded so a
    typo'd '1000' must not stampede the network. A bad value falls back to
    the next source instead of crashing a paid run."""
    MAX_PARALLEL_CHUNKS = deps.MAX_PARALLEL_CHUNKS
    _map_reduce_core = deps._map_reduce_core
    os = deps.os
    return _map_reduce_core.resolve_parallel(
        (getattr(args, "parallel", None), os.environ.get("AMBIENT_MAX_PARALLEL")),
        MAX_PARALLEL_CHUNKS)

def _reduce_response_format(rf, reduce_profile, deps=None):
    """Re-gate a response_format request to the REDUCE model's capabilities
    a strict json_schema the map model supports can 400 on a reduce
    model that lacks structured_outputs — downgrade per its own features."""
    _map_reduce_core = deps._map_reduce_core
    response_format_for = deps.response_format_for
    return _map_reduce_core.reduce_response_format(
        rf, reduce_profile, response_format_for=response_format_for)

# L13: a distinctive sentinel for the per-chunk index — collision-safe, unlike
# the old literal "{i}" which corrupted a user system prompt / code map that
# happened to contain "{i}". Always substituted before the note is sent/hashed.
_CHUNK_IDX_TOKEN = "AMBIENT_CHUNK_INDEX"


def _map_note(map_system, code_map, n_chunks, deps=None):
    """The per-chunk system prompt run_map_reduce sends (chunk index patched
    in per call). Factored out so the best-of audit miss-plan can
    precompute the SAME salted cache keys the live fan-out will use — key
    parity by construction, never by copy-paste."""
    _CHUNK_IDX_TOKEN = deps._CHUNK_IDX_TOKEN
    _map_reduce_core = deps._map_reduce_core
    return _map_reduce_core.map_note(
        map_system, code_map, n_chunks, _CHUNK_IDX_TOKEN)
