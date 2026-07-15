"""Pure bounded credential detection with no import-time external effects."""

import os
import re

from ambient_codex.constants import SIG_SCAN_LINE_MAX


SECRET_PATTERNS = [
    re.compile(r"(?i)(?:api[_-]?key|secret|passwd|password|token)['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9+/_.\-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_.\-]{20,}"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/]{20,}={0,2}"),  # HTTP Basic auth
    re.compile(r"(?:github_pat|gh[pousr])_[A-Za-z0-9_]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),  # GitLab personal access token
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"),  # Stripe secret/restricted key
    re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),  # SendGrid API key
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    # Unlabeled high-value creds the label pattern above misses. Both are
    # tightly anchored (fixed prefix + separators) so they stay linear on long
    # lines and carry a low false-positive rate.
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google API key
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,2000}\.eyJ[A-Za-z0-9_\-]{10,3000}"
               r"\.[A-Za-z0-9_\-]{6,2000}"),  # JWT (header.payload.signature), bounded
    # Azure / ADO.NET connection-string secrets: AccountKey=…==, SharedAccessKey=…,
    # SharedAccessSignature=sv=…&sig=… (a SAS token is a query string).
    re.compile(r"(?i)(?:account|shared[_-]?access|shared)[_-]?"
               r"(?:key|secret|signature)\s*=\s*['\"]?"
               r"(?=[A-Za-z0-9%+/=&?.\-]*[+/=%&])"   # a real key/SAS, not code
               r"[A-Za-z0-9%+/=&?.\-]{16,}"),
    re.compile(r"(?i)[?&]sig=[A-Za-z0-9%+/]{16,}"),  # a SAS signature parameter
    # A connection-string / URL password: 'Password=…' in an ADO.NET/JDBC  # pragma: allowlist secret
    # connection string or 'password=…' in a URL query — the #1 place a real DB
    # password is stored (Workflow leak). No spaces around '=' (code uses
    # 'password = …'), a preceding delimiter/quote, and a literal value (not an
    # interpolation/placeholder) keep it off normal source.
    re.compile(r"(?i)(?:^|[;&?\"'\s])(?:password|passwd|pwd)="
               r"(?![\s$%{<])[^;&\s\"'<>]{6,}"),
]
# SECRET_PATTERNS[0] is the LOOSE labeled pattern (keyword abutting '='); it can
# match a dotted code reference ('password = user.passwordHash'), so it runs
# AFTER the code-ref exclusion. Everything else is precise/anchored and runs
# first, so a real JWT/Azure key is caught even if it also looks code-ref-ish.
_LOOSE_LABEL_RE = SECRET_PATTERNS[0]
_PRECISE_SECRET_PATTERNS = SECRET_PATTERNS[1:]
# The loose labeled pattern also matches a labeled assignment whose UNQUOTED
# value is a code EXPRESSION — a function call ('password = getPasswordFromEnv()')
# or a dotted reference ('token = req.headers.authorization') — normal source
# that must NOT be refused (Codex round 12 structural false positive). A quoted
# value is a literal and is NOT cleared by this (no quote in the pattern).
_LOOSE_CODE_VALUE_RE = re.compile(
    r"(?i)(?:api[_-]?key|secret|passwd|password|token)\s*[:=]\s*"
    r"[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z0-9_.]*[A-Za-z0-9_](?:\s*\([^)\n]*\))?|\([^)\n]*\))"  # .attr[()] or ()
    r"\s*[;,)}\]]*\s*$")
# A labeled TYPE annotation ('token: CancellationToken') — the value is a
# PascalCase type name (upper, lower, letters), not a credential (Workflow false
# positive in a function signature). Not anchored to end-of-line so it clears one
# param among many. Value must START uppercase (a real token starts lower/mixed).
_LOOSE_TYPE_VALUE_RE = re.compile(
    r"(?i:api[_-]?key|secret|passwd|password|token)[ \t]*[:=][ \t]*"
    r"[A-Z][a-z][A-Za-z]{2,}(?![A-Za-z0-9])")
# Kept OUT of SECRET_PATTERNS: this pattern backtracks quadratically on long
# lowercase-alnum runs (measured 129s on a 400k-char line — an effective hang
# on minified/hex blobs). line_has_secret gates it on
# the literal '://' and scans only a bounded window around each occurrence.
# Credentials embedded in a URL: scheme://[user]:password@host. The username may
# be EMPTY (redis://:password@host is common — Codex round 9).
# Password in a URL — but the credential must not be a template PLACEHOLDER
# ('{password}', '${password}'): an f-string / templated DSN is not a leak
# (Workflow false positive). '{', '}', '$' excluded from the password run.
CREDS_IN_URL_RE = re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@]*:[^@\s{}$]{6,}@")
# An ALL-CAPS env identifier assigned a value (AWS_SECRET_ACCESS_KEY=…,
# DB_PASSWORD=…, GITHUB_TOKEN=…). The keyword-abuts-'=' pattern above misses
# these (the token before '=' is a bare KEY/TOKEN component). We classify the
# IDENTIFIER, then apply the right value guard:
#   * STRONG (contains SECRET/TOKEN/PASSWORD/PASSWD/CREDENTIAL/APIKEY as a
#     component, anywhere): the name alone signals a secret → any 8+ non-space
#     value trips (catches punctuated/short passwords and 'PASSWORD=…' with no
#     prefix). A strong name is NEVER exempted by a trailing _PUBLIC_KEY
#     (AWS_SECRET_PUBLIC_KEY is still a secret).
#   * a plain *_KEY (no strong token): ambiguous — PRIMARY_KEY/FOREIGN_KEY are
#     schema columns, not secrets — so it trips ONLY on a high-entropy value
#     (20+ base64/hex run) and a genuine *_PUBLIC_KEY is exempt.
# Anchored + linear-time ('_' is a hard delimiter so the quantifiers can't
# overlap).
# Matches KEY[:=]VALUE in env / JSON / YAML shapes, any case, with optional
# matching quotes around the key ("AWS_SECRET_ACCESS_KEY": "…") and an optional
# opening quote on the value. Group 1 = key quote (or ''), 2 = identifier,
# 3 = value quote (or ''), 4 = value.
# The identifier may use '_' (env), '-' (kebab/K8s/YAML: mysql-root-password),
# or '.' (dotted properties: spring.datasource.password) as component separators
# (Codex round 18).
ENV_ASSIGN_RE = re.compile(
    r"(?:^|[\{,\s])(['\"]?)(?:export[ \t]+)?"
    r"([A-Za-z][A-Za-z0-9_.\-]*[A-Za-z0-9])\1?"
    r"[ \t]*[:=][ \t]*(['\"]?)([^\s'\"]*)")
# A credential keyword as a full component (db_password, mysql-root-password,
# spring.datasource.password, AWS_SECRET_ACCESS_KEY) — separators '_', '-', '.' —
# OR a camelCase token (DbPassword), but NOT buried in a longer lowercase word
# (Tokenizer must not read as TOKEN — Codex round 3).
_ENV_STRONG_COMPONENT_RE = re.compile(
    r"(?i)(^|[_.\-])(SECRET|TOKEN|PASSWORD|PASSWD|PWD|CREDENTIAL|CREDENTIALS|APIKEY)"
    r"([_.\-]|$)")
# PASSWORD/PASSWD may be glued into an env-var name with no separator
# ('PGPASSWORD', 'MYSQLPASSWORD') — still a credential (Workflow). Config-about
# names ('PASSWORD_POLICY') are still excluded by _ENV_CONFIG_SUFFIX_RE first.
_ENV_STRONG_SUBSTR_RE = re.compile(r"(?i)(?:PASSWORD|PASSWD)")
_ENV_STRONG_CAMEL_RE = re.compile(
    r"(?:Secret|Token|Password|Passwd|Credential|Credentials|ApiKey)(?![a-z])")


# An identifier that NAMES config ABOUT a secret rather than holding one: its
# trailing component is a non-secret modifier (SECRET_NAME, SECRET_PATH,
# PASSWORD_POLICY, TOKEN_EXPIRATION, …). KEY/TOKEN/SECRET/PASSWORD themselves are
# NOT in this list, so DB_PASSWORD / API_SECRET / ACCESS_TOKEN stay secrets.
_ENV_CONFIG_SUFFIX_RE = re.compile(
    r"(?i)[_.\-](NAME|PATH|POLICY|EXPIR\w*|ID|TTL|TIMEOUT|LENGTH|COUNT|URL|URI|FILE|"
    r"DIR|DIRECTORY|TYPE|ENABLED|DISABLED|REQUIRED|FORMAT|HEADER|PREFIX|SUFFIX|"
    r"ROTATION|LIFETIME|GRACE|ALGORITHM|ALGO|ISSUER|AUDIENCE|SCOPES?|LOCATION|"
    r"SOURCE|PROVIDER|METHOD|MODE|VERSION|LIMIT|MAX|MIN|ATTEMPTS|WINDOW|AGE|"
    r"DAYS|HOURS|MINUTES|SECONDS|PATTERN|VALIDATION|VALIDATOR|MANAGER|SERVICE|"
    r"STORE|VAULT|BACKEND|STRATEGY|ENV|ENVIRONMENT|LABEL|DESCRIPTION|TITLE|"
    r"ENDPOINT|HOST|HOSTNAME|PORT|DOMAIN|REGION|ADDRESS|USERNAME|OWNER)$")


# A key that NAMES a Kubernetes/Helm Secret resource rather than holding a value —
# 'existingSecret', 'secretName', 'secretKeyRef', 'secretRef' (Workflow false
# positive: its value is a resource NAME, not a credential).
_ENV_SECRET_REF_RE = re.compile(
    r"(?i)^(?:existing[_.\-]?secret|secret[_.\-]?(?:name|ref|key[_.\-]?ref)"
    r"|[a-z0-9]*[_.\-]?secretname)$")
# The same, as a whole-LINE key: 'existingSecret: my-app-secrets' names a k8s
# Secret RESOURCE, so its value is a name, not a credential (Workflow).
_ENV_SECRET_REF_LINE_RE = re.compile(
    r"(?i)^[\ \t\-]*['\"]?(?:existing[_.\-]?secret|secret[_.\-]?(?:name|ref|key[_.\-]?ref))"
    r"['\"]?[ \t]*[:=]")


def env_is_strong(ident):
    if _ENV_CONFIG_SUFFIX_RE.search(ident) or _ENV_SECRET_REF_RE.match(ident):
        return False  # config ABOUT a secret / a k8s Secret NAME, not a value
    return bool(_ENV_STRONG_COMPONENT_RE.search(ident)
                or _ENV_STRONG_CAMEL_RE.search(ident)
                or _ENV_STRONG_SUBSTR_RE.search(ident))
# An ambiguous *_KEY trips only when a SENSITIVE component precedes KEY (schema
# columns like PRIMARY_KEY/FOREIGN_KEY/CACHE_KEY are NOT secrets — Codex).
_ENV_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(^|[_.\-])(ACCESS|PRIVATE|ENCRYPT|ENCRYPTION|SIGN|SIGNING|AUTH|SESSION|"
    r"CLIENT|MASTER|ROOT|APP|SERVICE)[_.\-]KEY$")
_PUBLIC_KEY_IDENT_RE = re.compile(r"(?i)(^|[_.\-])PUBLIC[_.\-]KEY$")
# A high-entropy key value: 20+ base64/hex chars. Pure lowercase-hex (a git SHA
# / hash) and dashed UUIDs are excluded — they aren't credentials (Codex).
_ENV_HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9+/]{20,}")
# A password-shaped value: 8+ chars mixing at least one letter and one digit
# ('Pa55word123'). A pure word ('postgres') or pure number is NOT password-shaped.
_ENV_PASSWORDISH_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9]{8,}$")
# A ROOTED filesystem PATH value ('/home/me/keys/service-account.json',
# '~/.aws/creds', 'C:\\keys\\x') is a location, not a credential (Workflow false
# positive). Must start with a path root so a base64 secret containing '/'
# ('wJalr…/K7…/bPx…', an AWS secret key) is NOT mistaken for a path.
_ENV_PATH_VALUE_RE = re.compile(
    r"^(?:(?:~|\.{1,2})?[/\\]|[A-Za-z]:[\\/])[\w.\-]+(?:[/\\][\w.\-]+)*$")


def value_looks_nonsecret(val):
    """True if `val` is clearly NOT a literal credential: a filesystem path, or a
    PascalCase TYPE name ('CancellationToken', 'OAuth2Token'). A type name has ≤1
    digit, so a random token ('Xk9mQz7Lp2w', 'Secret123') with 2+ digits is NOT
    excluded and stays flagged (Workflow false positives on TS type annotations)."""
    if _ENV_PATH_VALUE_RE.match(val):
        return True
    return (val[:1].isupper() and val.isalnum() and any(c.islower() for c in val)
            and sum(c.isdigit() for c in val) <= 1)
# A value that is a pure variable interpolation ($VAR / ${VAR} / %VAR% / #{...}
# / $(...)) is a passthrough reference, not a literal secret (Codex round 17:
# 'DB_PASSWORD=${DB_PASSWORD}' is weekly deployment config).
_ENV_INTERP_RE = re.compile(
    r"^(?:\$[A-Za-z_][A-Za-z0-9_]*"          # $VAR
    r"|\$\{\{[^}\n]*\}\}"                      # ${{ secrets.X }} (GitHub Actions)
    r"|\{\{[^}\n]*\}\}"                        # {{ var }} (Jinja/Ansible/Helm)
    r"|\$\{[^}\n]*\}"                          # ${VAR}, ${VAR:-default}, ${VAR/…}
    r"|%[A-Za-z_][A-Za-z0-9_]*%"              # %VAR%
    r"|#\{[^}\n]*\}|\$\([^)\n]*\))$")         # #{...} / $(...)
# Secret-ish punctuation in a value marks it a literal credential rather than a
# code reference — deliberately EXCLUDES '/' (paths) and code brackets.
_SECRET_VALUE_HINT_RE = re.compile(r"[@#$%^&*!=~]")
_CODE_VALUE_RE = re.compile(r"[()\[\]{}]")
# A whole line that is just `[keyword…] name = obj.attr.reference[;]` is a code
# expression, not a credential — e.g. `password = user.password_hash` or
# `const password = user.passwordHash;` (Codex R4/R6 false positives). Allows a
# declaration keyword prefix, camelCase attribute segments, and a trailing ';'.
# Safe against JWT/base64 secrets because those are caught by the PRECISE
# patterns before this filter runs (see line_has_secret).
_CODE_REF_ASSIGN_RE = re.compile(
    r"^\s*(?:(?:const|let|var|final|val|public|private|protected|static|"
    r"readonly|self\.|this\.)\s*)*"
    r"(?=[\w.]*[a-z])[A-Za-z_][\w.]*\s*[:=]\s*"       # LHS must have a lowercase
    r"[a-z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+\s*;?\s*$")
# An assignment (any case) whose VALUE is a code EXPRESSION — a function call
# ('DB_PASSWORD = os.getenv("DB_PASSWORD")', the #1 secret-plumbing pattern) or a
# dotted reference ('X = process.env.SECRET') — is CODE, never a credential. A
# BARE identifier value ('DB_PASSWORD=aQ7pR2xL9mZ4kT8v') and a real bracketed  # pragma: allowlist secret
# password ('DB_PASSWORD=p(ass)w0rd!') are NOT cleared — they lack the clean
# call/dotted-ref shape (Codex round 16).
# The tail a code value may carry before end-of-line: a TS 'as Type' cast, a
# non-null '!', an '|| default' / '?? default' fallback, a ';'/',' terminator,
# and/or a trailing '#'/'//' comment (Workflow false positives — env reads with
# a comment / fallback / trailing comma were wrongly flagged).
_CODE_TAIL = (r"(?:[ \t]*as[ \t]+[\w<>\[\].]+)?(?:[ \t]*!)?"
              r"(?:[ \t]*(?:\|\||\?\?)[ \t]*[^\n]*?)?[ \t]*[;,]?[ \t]*"
              r"(?:(?://|\#)[^\n]*)?$")
_ENV_CODE_VALUE_RE = re.compile(
    # A value that CALLS or SUBSCRIPTS is code, any spacing — a function call
    # ('X = os.getenv("K")') or a subscript ('X = os.environ["K"]', the canonical
    # Django read). The lookahead requires a '(' or '[' so a plain dotted '.env'
    # value is not swept in here.
    r"[:=]\s*(?=[A-Za-z_][\w.\[\]()\"' ]*[\[(])"
    # a chain of .attr / [subscript] / (call) — the call allows ONE nested level
    # so 'os.environ.get("K", token_urlsafe(32))' is recognized as code (Workflow):
    r"[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]\n]*\]|\((?:[^()\n]|\([^()\n]*\))*\))+"
    + _CODE_TAIL +
    # A DOTTED-reference value is code only with a SPACE AFTER the operator (code
    # / JSON style: 'X = process.env.Y', '"apiKey": process.env.Y') — NOT '.env'
    # 'KEY=prod.db.password' with no space, which is a real secret (round 13/16).
    # The char BEFORE the ':' may be a space or a closing quote (JSON key):
    r"|[\"'\t ][ \t]*[:=][ \t]+[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)+" + _CODE_TAIL)
# A value that is a shell command substitution ('$(openssl rand -hex 32)') or a
# backtick command — code that PRODUCES a secret at runtime, not a literal
# (Workflow false positive; ENV_ASSIGN_RE's value stops at the first space).
_ENV_CMDSUB_RE = re.compile(r"[:=][ \t]*(?:\$\([^\n]*\)|`[^\n]*`)[ \t]*;?[ \t]*"
                            r"(?:(?://|\#)[^\n]*)?$")


def env_assignment_is_secret(line):
    """True if `line` assigns a real credential to a credential-named key, in
    env / JSON / YAML shape, any case (see ENV_ASSIGN_RE). A code reference
    (`password = get_input()`, `foreign_key = other.id`) is NOT a secret. ALL
    assignments are checked — on the whole line AND on each whitespace token, so
    a prose prefix ('here is my key: AWS_SECRET_ACCESS_KEY=…') whose first match
    greedily swallows the real assignment is still caught (Codex R5).

    DOCUMENTED BACKSTOP BOUNDARY: a bare lowercase unquoted assignment to a
    plain DICTIONARY-WORD value (`password: postgres`) is intentionally NOT
    flagged — it can't be told apart from a config KEY (`password_policy:
    enabled`) or a code variable (`password = default_pw`) without
    false-positiving on the tool's primary job (auditing code). The tripwire is
    a backstop for the COMMON high-signal shapes (uppercase env vars, quoted
    JSON/YAML values, punctuated/high-entropy secrets, vendor tokens, auth
    headers, URL creds); the docs tell users never to send secrets."""
    for segment in [line, *line.split()]:
        for m in ENV_ASSIGN_RE.finditer(segment):
            ident, val = m.group(2), m.group(4)
            quoted = bool(m.group(1)) or bool(m.group(3))
            if len(val) < 8:
                continue
            if " " in val.strip():
                continue  # a message/sentence value ('Passwords must match'), not a secret
            if _ENV_INTERP_RE.match(val):
                continue  # a variable passthrough ($VAR/${VAR}), not a literal
            if value_looks_nonsecret(val):
                continue  # a type name / filesystem path, not a credential
            if env_is_strong(ident):
                # An ALL-CAPS env var or a quoted key/value is a literal secret
                # regardless of punctuation (a real password CAN contain '(').
                if ident.isupper() or quoted:
                    return True
                # A lowercase name: secret-ish punctuation ('!','@',…) marks a
                # literal secret even with brackets (db_password: p(ass)w0rd! —
                # Codex R5); ELSE brackets mean a code expression; else a
                # high-entropy base64 token (with a digit/upper) is a secret.
                if _SECRET_VALUE_HINT_RE.search(val):
                    return True
                if _CODE_VALUE_RE.search(val):
                    continue
                # A password-shaped value — 8+ chars mixing letters AND digits
                # ('Pa55word123', 'Welcome2024') — is a real credential even for a
                # lowercase dotted key ('spring.datasource.password=…', Workflow).
                # A pure dictionary word ('postgres') has no digit and stays the
                # documented not-flagged case.
                if _ENV_PASSWORDISH_RE.match(val):
                    return True
                if _ENV_HIGH_ENTROPY_RE.match(val) and re.search(r"[A-Z0-9]", val):
                    return True
                continue
            if _ENV_SENSITIVE_KEY_RE.search(ident) \
                    and not _PUBLIC_KEY_IDENT_RE.search(ident) \
                    and not _CODE_VALUE_RE.search(val):
                # A sensitive *_KEY with a 20+ base64/hex value (SESSION_KEY /
                # ACCESS_KEY can be raw hex); CACHE_KEY etc. are already filtered
                # out by the sensitive-name gate.
                if _ENV_HIGH_ENTROPY_RE.match(val):
                    return True
    return False
# Credential-named files refused regardless of content (the
# old check missed .envrc/.netrc/.npmrc/.pgpass/id_rsa/*.pem/credentials.json).
SECRET_NAMES_RE = re.compile(
    r"(^|\.)env(\.|$)|^\.envrc$|^\.netrc$|^\.npmrc$|^\.pgpass$"
    r"|^id_(rsa|ed25519|ecdsa|dsa)(\.pub)?$|^credentials(\.json)?$"
    r"|\.(pem|p12|pfx)$", re.I)


def line_has_secret(line):
    # Bound the regex scan for the label/precise patterns to a real secret's
    # length — a credential is short, and scanning an unbounded minified/blob
    # line risks super-linear backtracking (Workflow: a 90k 'eyJ'-run took 2.2s).
    # The URL window below stays on the FULL line (it is already linear via fixed
    # windows and must catch a cred after many benign URLs).
    scan = line if len(line) <= SIG_SCAN_LINE_MAX else line[:SIG_SCAN_LINE_MAX]
    # Precise/anchored patterns first — a real JWT/Azure/AWS key is a secret
    # even if the line also reads like a code reference.
    if any(p.search(scan) for p in _PRECISE_SECRET_PATTERNS):
        return True
    if (_CODE_REF_ASSIGN_RE.match(scan) or _ENV_CODE_VALUE_RE.search(scan)
            or _ENV_CMDSUB_RE.search(scan) or _ENV_SECRET_REF_LINE_RE.match(scan)):
        # code (`x=a.b.c`, `getenv(...)`, `$(cmd)`) or a k8s Secret NAME reference
        # ('existingSecret: my-secret') — a reference, not a credential value.
        return False
    if (_LOOSE_LABEL_RE.search(scan) and not _LOOSE_CODE_VALUE_RE.search(scan)
            and not _LOOSE_TYPE_VALUE_RE.search(scan)):
        return True  # labeled secret, but not a code call/reference/type-annotation value
    if env_assignment_is_secret(scan):
        return True
    if "://" not in line:
        return False
    # Overlapping fixed windows (4k window / 2k step): total work stays linear
    # in the line length with NO occurrence cap (a cap let a
    # credential after 20 benign URLs through), and a cred URL spanning up to
    # ~2k chars can never straddle past both windows that contain it.
    step, win = 2048, 4096
    for start in range(0, len(line), step):
        seg = line[start:start + win]
        if "://" in seg and CREDS_IN_URL_RE.search(seg):
            return True
    return False



def secret_hits(labeled_chunks, limit=20):
    """Return bounded credential locations without including matched content."""
    hits = ()
    for label, text in labeled_chunks:
        if len(hits) >= limit:
            break
        base = os.path.basename(label)
        if SECRET_NAMES_RE.search(base):
            hits = (*hits, f"{label} (credential-named file — never send these)")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            first = re.match(r"^[ \t]*(\d+)\| ?", line)
            display_line = int(first.group(1)) if first else lineno
            scan_line = line
            while True:
                stripped = re.sub(r"^[ \t]*\d+\| ?", "", scan_line, count=1)
                if stripped == scan_line:
                    break
                scan_line = stripped
            if line_has_secret(scan_line):
                hits = (*hits, f"{label}:{display_line}")
                if len(hits) >= limit:
                    break
    return hits


__all__ = (
    "SECRET_NAMES_RE",
    "env_is_strong",
    "value_looks_nonsecret",
    "env_assignment_is_secret",
    "line_has_secret",
    "secret_hits",
)
