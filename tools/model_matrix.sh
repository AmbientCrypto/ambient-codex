#!/usr/bin/env bash
# ambient-codex model × command matrix. Complements tools/stress_test.sh:
#   1. EVERY catalog model gets exercised — READY models must complete real
#      work; non-serving models must fail with the clean [model] diagnosis (never a
#      traceback, never a hang).
#   2. Command surfaces the battery doesn't reach live: use/mode/usage/curate
#      cycles, cache clear, link idempotency, trust-url refusal, plan-only
#      builds, consensus --json, git --staged/--diff audits, ask -s, code -f.
# Runs under a sandbox HOME; spends a small amount on READY models.
#
# Usage: bash tools/model_matrix.sh
set -u
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AMB="${AMB:-$REPO_ROOT/plugins/ambient-codex/bin/ambient}"
WORK="${AMBIENT_MATRIX_WORK:-$(mktemp -d)}"
mkdir -p "$WORK"
if [ -z "${AMBIENT_MATRIX_WORK:-}" ]; then
  trap 'rm -rf "$WORK"' EXIT
else
  echo "matrix artifacts retained at: $WORK"
fi
PASS=0; FAIL=0; SKIP=0
pass() { echo "  PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL  $1  -- $2"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP  $1  -- $2"; SKIP=$((SKIP+1)); }

REAL_HOME="$HOME"
SBHOME="$WORK/home"; mkdir -p "$SBHOME/.config/ambient-codex"
KEY="$(security find-generic-password -s ambient-codex -a api-key -w 2>/dev/null || true)"
[ -z "$KEY" ] && [ -f "$REAL_HOME/.config/ambient-codex/env" ] && \
  KEY="$(sed -n 's/^AMBIENT_API_KEY=//p' "$REAL_HOME/.config/ambient-codex/env" | head -1)"
if [ -z "$KEY" ]; then echo "no API key — matrix needs live access"; exit 1; fi
export HOME="$SBHOME" AMBIENT_NO_ONBOARD=1
LOG="$WORK/all.log"
run() { OUT="$WORK/out.$RANDOM"; "$@" >"$OUT" 2>&1; RC=$?; cat "$OUT" >> "$LOG"; return 0; }
wk() { AMBIENT_CODEX_API_KEY="$KEY" "$@"; }

echo "=== ambient model × command matrix ($("$AMB" --version)) ==="

# ---- 1. EVERY MODEL --------------------------------------------------------
run wk "$AMB" models --json
python3 - "$OUT" > "$WORK/ids.txt" <<'PY'
import json,sys
d=json.loads(open(sys.argv[1]).read()[open(sys.argv[1]).read().index("{"):])
for m in d["models"]:
    print(("READY " if m["ready"] else "idle  ") + m["id"])
PY
N_MODELS=$(wc -l < "$WORK/ids.txt" | tr -d ' ')
echo "--- per-model behavior ($N_MODELS catalog models) ---"
while read -r state mid; do
  run wk "$AMB" ask "Reply with exactly: MDL-OK" -m "$mid" --yes --timeout 90
  if grep -q "Traceback\|\[internal\]" "$OUT"; then
    fail "model $mid" "internal error/traceback"
  elif [ "$state" = "READY" ]; then
    grep -q "MDL-OK" "$OUT" && pass "READY $mid completes an ask" \
      || fail "READY $mid" "no MDL-OK (rc=$RC): $(tail -c 120 "$OUT")"
  else
    if [ $RC -eq 1 ] && grep -qiE "\[model\]|no workers|READY now" "$OUT"; then
      pass "idle  $mid fails clean ([model] diagnosis)"
    else
      fail "cold  $mid" "rc=$RC: $(tail -c 120 "$OUT")"
    fi
  fi
done < "$WORK/ids.txt"

echo "--- READY models must do real WORK (planted-bug audit + codegen) ---"
cat > "$WORK/bug.py" <<'PY'
def divide(a, b):
    return a / b


def always_crashes():
    return 1 / 0
PY
# Process substitution (NOT `grep | while`): keep the loop in the CURRENT shell
# so pass()/fail() update the summary counters + exit code. A pipe would run the
# body in a subshell, silently dropping every real-WORK result from the tally.
while read -r _ mid; do
  run wk "$AMB" audit "$WORK/bug.py" -m "$mid" --json --yes --timeout 240
  if python3 -c "
import json,sys
raw=open('$OUT').read(); d=json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
assert d['verdict'] and isinstance(d['findings'],list) and d['findings']
assert any('zero' in (f.get('title','')+f.get('defect','')).lower() or 'division' in (f.get('title','')+f.get('defect','')).lower() for f in d['findings'])
" 2>/dev/null; then pass "audit on $mid finds the planted bug"
  else fail "audit on $mid" "$(head -c 150 "$OUT")"; fi
  run wk "$AMB" code "Output only Python code: def add(a,b) returning a+b" -m "$mid" --json --yes --timeout 240
  if python3 -c "
import json
raw=open('$OUT').read(); d=json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
assert d['status']=='ok' and 'def add' in d['content']
" 2>/dev/null; then pass "codegen on $mid returns working envelope"
  else fail "codegen on $mid" "$(head -c 150 "$OUT")"; fi
done < <(grep '^READY' "$WORK/ids.txt")

# ---- 2. COMMANDS the battery doesn't reach live ----------------------------
echo "--- use / mode / curate / cache / link / trust-url cycles (sandbox) ---"
run wk "$AMB" use moonshotai/kimi-k2.7-code --chat
grep -q "chat/audit set to" "$OUT" && pass "use --chat persists" || fail "use --chat" "$(head -c 100 "$OUT")"
run wk "$AMB" use k2.7 --code   # unique-substring resolution
grep -q "matched 'k2.7'" "$OUT" && pass "use resolves unique substring" || fail "use substring" "$(head -c 120 "$OUT")"
run wk "$AMB" use kimi --code < /dev/null   # ambiguous → refuse + suggest
[ $RC -ne 0 ] && grep -q "Did you mean" "$OUT" && pass "use refuses ambiguous substring with suggestions" || fail "use ambiguous" "rc=$RC"
run "$AMB" mode on;  grep -q "ON" "$OUT" && pass "mode on" || fail "mode on" "?"
run "$AMB" mode;     grep -q "delegate=on" "$OUT" && pass "mode status reflects on" || fail "mode status" "?"
run "$AMB" mode takeover
grep -qi "takeover" "$OUT" && grep -q "ON" "$OUT" && pass "mode takeover" || fail "mode takeover" "?"
run "$AMB" control --json
python3 -c "
import json; raw=open('$OUT').read()
d=json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
assert d['mode']=='takeover' and any(o['state']=='takeover' and o['current'] for o in d['mode_options'])" 2>/dev/null \
  && pass "control --json reflects takeover" || fail "control takeover" "$(head -c 120 "$OUT")"
run "$AMB" mode off; grep -q "OFF" "$OUT" && pass "mode off" || fail "mode off" "?"
run wk "$AMB" curate only moonshotai/kimi-k2.7-code
run wk "$AMB" curate status
grep -q "1 surfaced" "$OUT" && pass "curate only restricts menu" || fail "curate only" "$(head -c 120 "$OUT")"
run wk "$AMB" curate note z-ai/glm-5.2 "matrix test note"
run wk "$AMB" curate status
grep -q "matrix test note" "$OUT" && pass "curate note persists" || fail "curate note" "?"
run wk "$AMB" curate reset
grep -q "reset" "$OUT" && pass "curate reset" || fail "curate reset" "?"
run "$AMB" cache; grep -q "cache:" "$OUT" && pass "cache status" || fail "cache" "?"
run "$AMB" cache clear; grep -q "removed" "$OUT" && pass "cache clear" || fail "cache clear" "?"
run "$AMB" link --dir "$WORK/bin"; run "$AMB" link --dir "$WORK/bin"
if [ -f "$WORK/bin/ambient-codex" ] && [ -x "$WORK/bin/ambient-codex" ] \
   && [ ! -L "$WORK/bin/ambient-codex" ] \
   && grep -q "Stable user-facing launcher" "$WORK/bin/ambient-codex"; then
  pass "stable launcher install is idempotent"
else
  fail "link idempotent" "stable launcher contract not preserved"
fi
run "$AMB" trust-url https://gateway.example < /dev/null
[ $RC -ne 0 ] && grep -qi "interactive-only" "$OUT" && pass "trust-url refuses non-TTY" || fail "trust-url gate" "rc=$RC"
run wk "$AMB" usage --json
python3 -c "
import json; raw=open('$OUT').read()
d=json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
forbidden={'cost','price','total_est_cost','reference_price','frontier_cost','saved','saved_pct'}
assert 'models' in d and not (forbidden & set(d))" 2>/dev/null \
  && pass "usage --json parses without money fields" || skip "usage --json" "no usage yet in sandbox"

echo "--- setup validation lanes (safe: bogus key only; no store/delete) ---"
run bash -c "echo not-a-plausible | \"$AMB\" setup --key-stdin"
[ $RC -ne 0 ] && grep -qi "too short\|does not look" "$OUT" && pass "setup pre-validates stdin key" || fail "setup prevalidation" "rc=$RC"
run bash -c "echo sk-bogus-but-plausible-key-000000 | \"$AMB\" setup --key-stdin"
[ $RC -ne 0 ] && grep -qi "rejected\|Nothing was saved" "$OUT" && ! grep -q Traceback "$OUT" \
  && pass "setup live-rejects a bogus key cleanly" || fail "setup bogus key" "rc=$RC"

echo "--- git lanes: --staged and --diff in a real repo (live) ---"
REPO="$WORK/repo"; mkdir -p "$REPO"; cd "$REPO" || exit
git init -q; git config user.email m@x; git config user.name m
printf 'def f(a, b):\n    return a + b\n' > app.py; git add app.py; git commit -qm base
printf 'def f(a, b):\n    return a / b  # changed\n' > app.py; git add app.py
run wk "$AMB" audit --staged --json --yes --timeout 240
python3 -c "
import json; raw=open('$OUT').read()
d=json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
assert d['kind']=='audit' and 'verdict' in d" 2>/dev/null \
  && pass "audit --staged returns a verdict" || fail "audit --staged" "$(head -c 150 "$OUT")"
run wk "$AMB" audit --diff no-such-ref --yes
[ $RC -ne 0 ] && grep -qi "git diff failed" "$OUT" && pass "audit --diff bad ref fails honestly" || fail "diff bad ref" "rc=$RC"
cd - >/dev/null || exit

echo "--- ask -s / code -f / build --plan-only / consensus --json (live) ---"
run wk "$AMB" ask "What is 2+2? Reply with just the number." -s "You are terse." --yes
grep -q "4" "$OUT" && pass "ask with system prompt" || fail "ask -s" "$(tail -c 80 "$OUT")"
printf 'GREETING = "hello"\n' > "$WORK/ctx.py"
run wk "$AMB" code "Output only Python: print GREETING from the context module" -f "$WORK/ctx.py" --yes
grep -q "GREETING" "$OUT" && pass "code -f uses context" || fail "code -f" "$(tail -c 100 "$OUT")"
run wk "$AMB" build "a single-file python fizzbuzz module" --dir "$WORK/po" --plan-only --json --yes
python3 -c "
import json; raw=open('$OUT').read()
d=json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
assert d['kind']=='build' and d['plan'] and d['written'] is False" 2>/dev/null \
  && pass "build --plan-only returns a plan, writes nothing" || fail "plan-only" "$(head -c 150 "$OUT")"
[ ! -e "$WORK/po/fizzbuzz.py" ] && pass "plan-only left the target dir untouched" || fail "plan-only wrote" "file exists"
run wk "$AMB" audit "$WORK/bug.py" --consensus moonshotai/kimi-k2.7-code,z-ai/glm-5.2 --json --yes --allow-partial --timeout 300
python3 -c "
import json; raw=open('$OUT').read()
d=json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
assert d['kind']=='consensus' and isinstance(d['findings'],list)
assert all('corroboration' in f for f in d['findings'])" 2>/dev/null \
  && pass "consensus --json envelope with corroboration" || fail "consensus --json" "$(head -c 150 "$OUT")"

if grep -qF "$KEY" "$LOG"; then fail "KEY-LEAK tripwire" "key appeared in output"; else pass "key-leak tripwire clean"; fi
SUMMARY="=== MATRIX SUMMARY: $PASS passed, $FAIL failed, $SKIP skipped ==="
echo "$SUMMARY"
if [ -n "${AMBIENT_MATRIX_WORK:-}" ]; then
  printf '%s\n' "$SUMMARY" > "$WORK/summary.txt"
fi
[ "$FAIL" -eq 0 ]
