"""Focused code generation and interactive chat orchestration."""

import dataclasses
import os
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class GenerationDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def run_code(args, api_key, api_url, conf, deps):
    MIN_REASONING_CHUNK = deps.MIN_REASONING_CHUNK
    Session = deps.Session
    _best_of_chat = deps._best_of_chat
    _code_workflow = deps._code_workflow
    _fail_exit = deps._fail_exit
    _resolve_best_of = deps._resolve_best_of
    apply_output_budget = deps.apply_output_budget
    chat = deps.chat
    density_factor = deps.density_factor
    files_block = deps.files_block
    model_profile = deps.model_profile
    note_if_hidden = deps.note_if_hidden
    pack_chunks = deps.pack_chunks
    read_files = deps.read_files
    refuse_if_secrets = deps.refuse_if_secrets
    resolve_reduce_model = deps.resolve_reduce_model
    route_model = deps.route_model
    run_map_reduce = deps.run_map_reduce
    usage_exit = deps.usage_exit
    warn_if_stdin_ignored = deps.warn_if_stdin_ignored
    task = " ".join(args.task).strip()
    if not task:
        usage_exit('describe the task: '
                   'ambient-codex code "write a rate limiter" -f context.py')
    warn_if_stdin_ignored("`code` does not read stdin — pass context with -f FILE")
    refuse_if_secrets([("task", task)], args.allow_secrets)
    args = _code_workflow.clone_request(args)
    # 7a validated UP FRONT: an invalid --best-of must fail usage
    # (64) BEFORE the catalog fetch and BEFORE any billed task/context
    # distillation pass — zero spend on a doomed invocation. The 0→0.7
    # diversity bump _resolve_best_of applies is for the GENERATION samples
    # ONLY: the billed task-brief/context distillation passes
    # below keep the user's own temperature (extraction wants determinism,
    # not diversity), so it is restored here and re-applied at the
    # _best_of_chat call.
    pre_best_of_temp = getattr(args, "temperature", None)
    best_of_k = _resolve_best_of(args)
    best_of_temp = getattr(args, "temperature", None)
    args.temperature = pre_best_of_temp
    if getattr(args, "model", None):
        note_if_hidden(args.model, conf)
    session = Session(api_url=api_url, api_key=api_key, conf=conf)
    catalog = session.catalog()  # memoized: ONE fetch for the whole command
    ctx_len = 0  # cheap size probe (stat only) for routing + budget sizing
    for p in (args.context or []):
        try:
            ctx_len += os.path.getsize(p)
        except OSError:
            pass
    model = route_model(args, conf, "code", catalog,
                        input_chars=len(task) + ctx_len)
    reduce_model = resolve_reduce_model(args, conf, model, catalog=catalog)
    # cmd_code previously used raw model_budget with NO reasoning cap, so its
    # default reasoner (kimi-k2.7-code) marathoned on large -f context. The
    # profile fixes that: reasoning-awareness is intrinsic.
    profile = model_profile(catalog, model)
    single, chunk = profile.single_shot_chars, profile.chunk_chars
    if len(task) > single:
        # No-refusal invariant covers the task string too:
        # distill an oversized brief down to what generation needs.
        print(
            "ambient: task brief exceeds this model's window — distilling it first",
            file=sys.stderr,
        )
        apply_output_budget(args, profile, chunk)
        packed = pack_chunks([("task", task)],
                             max(MIN_REASONING_CHUNK,
                                 int(chunk / density_factor(task))))
        task, t_partial, t_reason = run_map_reduce(
            api_key, api_url, model,
            "From the given chunk of a long task brief, extract every "
            "requirement, constraint, and verbatim detail needed to do the work.",
            packed, args,
            "Merge these extracts into one concise, complete task brief, "
            "preserving verbatim requirements and constraints.",
            single, reduce_model=reduce_model, catalog=catalog,
            session=session,
        )
        if t_partial and not getattr(args, "allow_partial", False):
            _fail_exit(
                args, "code", "partial",
                f"task-brief distillation was incomplete ({t_reason}) — "
                "re-run, shorten the brief, or pass --allow-partial.",
                api_key=api_key,
            )
        if getattr(args, "_auto_budget", False):
            args.max_tokens = None  # re-derive below for the generation pass
    # Size the budget for the final generation input (task + context, capped at
    # single since oversized context is distilled below to <= single). A1.
    # (ctx_len was probed above, before routing.)
    apply_output_budget(args, profile, min(len(task) + ctx_len, single))
    context = ""
    if args.context:
        chunks = read_files(args.context)
        refuse_if_secrets(chunks, args.allow_secrets)
        context = files_block(chunks)
        if len(context) > single:
            # Distill oversized context down to what the task needs, then code.
            print(
                "ambient: context exceeds this model's window — distilling it first",
                file=sys.stderr,
            )
            packed = pack_chunks(chunks,
                                 max(MIN_REASONING_CHUNK,
                                     int(chunk / density_factor(context))))
            context, partial, reason = run_map_reduce(
                api_key, api_url, model,
                f"TASK: {task}\nFrom the given chunk of context files, extract "
                "verbatim every part (signatures, types, conventions, key logic) "
                "a senior engineer would need to do the task. Be selective.",
                packed, args,
                f"TASK: {task}\nMerge these context extracts into one concise, "
                "non-redundant context brief preserving verbatim signatures/types.",
                single, reduce_model=reduce_model, catalog=catalog,
                session=session,
            )
            if partial and not getattr(args, "allow_partial", False):
                _fail_exit(
                    args, "code", "partial",
                    f"context distillation was incomplete ({reason}) — "
                    "the generated code could miss cross-file details. Re-run, "
                    "narrow the context, or pass --allow-partial to proceed anyway.",
                    api_key=api_key,
                )
            clamped_context = _code_workflow.clamp_context(
                context, task=task, single_shot_chars=single)
            if clamped_context != context:
                # A partial distillation can fall back to raw concatenation far
                # above `single`; proceeding would 400 the paid-for generation
                # call. Clamp with an honest marker.
                context = clamped_context
    # 7a: --best-of applies to the final GENERATION call only (any brief/
    # context distillation above stays a single pass — sampling diversity
    # buys nothing on extraction). Validated at the TOP of cmd_code;
    # the diversity temperature applies HERE only.
    _code_workflow.dispatch_generation(
        api_key, api_url, model, task, context, args,
        best_of_k=best_of_k, best_of_temperature=best_of_temp,
        catalog=catalog, conf=conf, session=session,
        best_of_chat=_best_of_chat, chat=chat)


# --------------------------------------------------------------------------
# `ambient chat` — native REPL. A readline loop over the SAME
# complete()/stream/redact/model_profile machinery as `ask`: rolling in-memory
# history trimmed to the model window, streamed replies, a cost/
# savings receipt after every turn, and per-turn cost gating + fleet
# reservation. Nothing is persisted; the key is never printed (redact).

CHAT_HELP = """commands:
  /model <id>   switch model for the session (explicit — printed; `auto`
                specs resolve via the live catalog like -m auto)
  /model        show the current model
  /clear        forget the conversation history
  /help         this help
  /exit         quit (Ctrl-D also quits; Ctrl-C only interrupts the
                current turn)"""



def run_chat(args, api_key, api_url, conf, deps):
    CHAT_HELP = deps.CHAT_HELP
    ChatError = deps.ChatError
    EXIT_USAGE = deps.EXIT_USAGE
    NetworkError = deps.NetworkError
    RequestSpec = deps.RequestSpec
    Session = deps.Session
    _StreamRedactor = deps._StreamRedactor
    _chat_input = deps._chat_input
    _fail_exit = deps._fail_exit
    _line_has_secret = deps._line_has_secret
    _stdin_is_tty = deps._stdin_is_tty
    _trim_chat_history = deps._trim_chat_history
    complete = deps.complete
    is_auto_model = deps.is_auto_model
    model_profile = deps.model_profile
    note_if_hidden = deps.note_if_hidden
    redact = deps.redact
    refuse_if_secrets = deps.refuse_if_secrets
    resolve_auto_model = deps.resolve_auto_model
    route_model = deps.route_model
    savings_note = deps.savings_note
    if not _stdin_is_tty():
        _fail_exit(
            args, "chat", "usage",
            "ambient-codex chat is an interactive REPL and needs a terminal — "
            "for piped/scripted use, call `ambient-codex ask` instead.",
            exit_code=EXIT_USAGE, api_key=api_key)
    # --system is prepended to every turn and sent to the network — scan it once
    # up front with the same tripwire the per-turn lines get.
    if getattr(args, "system", None):
        refuse_if_secrets([("system", args.system)], getattr(args, "allow_secrets", False))
    try:
        import readline  # noqa: F401 — stdlib line editing where available
    except ImportError:
        pass
    session = Session(api_url=api_url, api_key=api_key, conf=conf)
    catalog = session.catalog()  # memoized: ONE fetch for the whole command
    model = route_model(args, conf, "chat", catalog)
    explicit_max = getattr(args, "max_tokens", None) is not None
    # SACRED: True after an explicit CONCRETE /model choice —
    # those turns run with --fallback/AMBIENT_FALLBACK disabled so the
    # picked model can never be silently swapped; an `auto` spec is
    # explicit DELEGATION, so fallback stays available there.
    model_pinned = False
    history = []
    print(f"ambient-codex chat — {model} · /help for commands · /exit or Ctrl-D "
          "to quit", file=sys.stderr)
    while True:
        try:
            line = _chat_input("ambient> ")
        except EOFError:
            print("", file=sys.stderr)
            break
        except KeyboardInterrupt:
            print("\n(^C — /exit or Ctrl-D quits)", file=sys.stderr)
            continue
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, rest = line.partition(" ")
            if cmd in ("/exit", "/quit"):
                break
            if cmd == "/clear":
                history = []
                print("ambient: history cleared", file=sys.stderr)
                continue
            if cmd == "/help":
                print(CHAT_HELP, file=sys.stderr)
                continue
            if cmd == "/model":
                spec = rest.strip()
                if not spec:
                    print(f"ambient: current model: {model}", file=sys.stderr)
                    continue
                # SACRED: an explicit user switch. `auto` specs delegate via
                # the same resolver as `-m auto` (the pick is printed there);
                # a concrete id is taken as-is and printed — a bad id gets
                # the normal [model] diagnosis on the next turn.
                if is_auto_model(spec):
                    try:
                        model = resolve_auto_model(spec, catalog, conf, 0,
                                                   args)
                    except SystemExit:
                        continue  # diagnosis already printed; REPL survives
                    model_pinned = False  # delegation — fallback may apply
                else:
                    note_if_hidden(spec, conf, source="/model")
                    model = spec
                    model_pinned = True # concrete choice — SACRED
                    print(f"ambient: model → {model}", file=sys.stderr)
                continue
            print(f"ambient: unknown command {cmd} — /help", file=sys.stderr)
            continue
        # ---- one turn -----------------------------------------------------
        profile = model_profile(catalog, model)
        sys_len = len(getattr(args, "system", None) or "")
        if sys_len + len(line) > profile.single_shot_chars:
            # even with ALL history dropped this turn cannot fit the
            # model's window — refuse it cleanly BEFORE gating or billing
            # (a floored budget would silently overflow context). The REPL
            # survives; nothing was sent and history is unchanged.
            print(
                f"ambient: that message is too large for {model}'s context "
                f"window ({sys_len + len(line):,} chars > "
                f"{profile.single_shot_chars:,}) — nothing was sent. "
                "Shorten it, or use `ambient-codex ask -f FILE` for large inputs.",
                file=sys.stderr)
            continue
        # Never publish a credential typed into the REPL (Codex round 4: chat
        # had no tripwire). Warn and SKIP the turn — the REPL survives, unlike
        # the sys.exit refusal the one-shot commands use.
        if not getattr(args, "allow_secrets", False) and any(
                _line_has_secret(seg) for seg in line.splitlines()):
            print("ambient: that message looks like it contains a credential — "
                  "not sending it to the network. Redact it and try again.",
                  file=sys.stderr)
            continue
        # Rolling window: keep the system prompt + the new line, drop the
        # OLDEST history past the model's single-shot budget.
        budget = max(2_000, profile.single_shot_chars - sys_len - len(line))
        history = _trim_chat_history(history, budget)
        # H2: _trim_chat_history floors the budget at 2000 chars and always
        # keeps the most recent exchange, so the ASSEMBLED request (system
        # + trimmed history + latest) can still exceed the window on a
        # near-window line. Verify the REAL assembled size and drop more
        # history oldest-first — down to none. system+latest alone always
        # fits here (refused above), so a doomed over-window request is
        # never gated or billed.
        while history and sys_len + len(line) + sum(
                len(m.get("content") or "") for m in history) \
                > profile.single_shot_chars:
            history = history[1:]
        messages = ([{"role": "system", "content": args.system}]
                    if getattr(args, "system", None) else []) \
            + history + [{"role": "user", "content": line}]
        # SACRED a /model-pinned turn never falls back to another
        # model. The turn rides a frozen replaced spec: max_tokens
        # re-derived for THIS turn's size unless the user set it explicitly.
        turn_spec = dataclasses.replace(
            RequestSpec.from_args(args), _no_fallback=model_pinned,
            max_tokens=args.max_tokens if explicit_max else None)
        input_chars = sum(len(m.get("content") or "") for m in messages)
        turn_spec = turn_spec.with_output_budget(profile, input_chars)
        streamed = []
        _sr = _StreamRedactor(api_key)   # M43: per-turn boundary-safe redaction

        def on_delta(piece, _sr=_sr):
            out = _sr.feed(piece)
            if out:
                sys.stdout.write(out)
                sys.stdout.flush()
            streamed.append(piece)

        try:
            content, usage, body = complete(api_key, api_url, model,
                                            messages, turn_spec,
                                            on_delta=on_delta,
                                            session=session)
        except KeyboardInterrupt:
            # Ctrl-C ends the TURN, not the REPL. The half-streamed reply is
            # discarded (not appended to history) — an interrupted answer
            # must not silently poison later turns.
            print("\nambient: turn interrupted (^C) — reply discarded, "
                  "history unchanged", file=sys.stderr)
            continue
        except (ChatError, NetworkError) as err:
            if streamed:
                sys.stdout.write(_sr.flush())
                sys.stdout.write("\n")
                sys.stdout.flush()
            cat = getattr(err, "category", "network")
            diag = getattr(err, "diagnosis", None) or str(err)
            print(redact(f"ambient [{cat}]: {diag}", api_key),
                  file=sys.stderr)
            continue
        if streamed:                # M43: emit the buffered streaming tail
            sys.stdout.write(_sr.flush())
            sys.stdout.flush()
        clean_stream = bool(streamed) \
            and "".join(streamed).strip() == content
        if not clean_stream:
            if streamed:
                sys.stdout.write(
                    "\n[ambient: stream restarted — full reply below]\n")
            sys.stdout.write(redact(content, api_key))
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
        history = history + [{"role": "user", "content": line},
                             {"role": "assistant", "content": content}]
        if usage:
            served = body.get("_served_model", model) \
                if isinstance(body, dict) else model
            # per-turn receipt: cost + vs-frontier saving.
            print(redact(f"[ambient {served} | in={usage.get('prompt_tokens')} "
                         f"out={usage.get('completion_tokens')} tokens"
                         f"{savings_note(served, usage, catalog, conf)}]",
                         api_key), file=sys.stderr)


BUILD_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "plan": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "purpose": {"type": "string"},
                    "est_lines": {"type": "integer"},
                },
                "required": ["path", "purpose", "est_lines"],
            },
        },
        "notes": {"type": "string"},
        "advisory_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["plan", "notes", "advisory_steps"],
}

BUILD_FILES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "required": ["files"],
}

BUILD_PLAN_PROMPT = """You are a senior software engineer planning a build.
Given a task (and optional context), design the complete set of files to create.
Output ONLY a JSON object matching the requested schema. Path rules: RELATIVE,
forward slashes, no '..' segments, no leading '/', never under .git, no
credential-style files (.env, id_rsa, *.pem, credentials.json). Keep the set
minimal but COMPLETE — a working result, not a sketch. est_lines = your honest
line-count estimate per file. advisory_steps = commands the USER may want to run
afterwards (install deps, run tests) — informational text only, they will NOT be
executed. notes = 1-3 sentences on the design."""

BUILD_JSON_INSTRUCTION_PLAN = (
    "\n\nOUTPUT FORMAT: return ONLY a JSON object (no prose, no markdown fences) "
    'matching: {"plan":[{"path":str,"purpose":str,"est_lines":int}],'
    '"notes":str,"advisory_steps":[str]}'
)

BUILD_GEN_PROMPT = """You are a senior software engineer generating COMPLETE files
for a planned build. Produce the FULL content of every requested file — production
quality, no placeholders, no TODOs, imports correct across files. Use exactly the
requested relative paths.

OUTPUT FORMAT — emit ONE file per line as a standalone JSON object:
{"path":"<relative/path>","content":"<full file content>"}
Rules: exactly one complete JSON object per line, newline-separated; NO array and
NO envelope wrapping them; NO prose, comments, or markdown fences; escape every
newline inside content as \\n so each file stays on ONE line. Emit the files in the
order requested. If your output is cut off by a length limit, do NOT try to
continue a partial object — a follow-up request will ask for the remaining files;
never split one file across replies."""

# Retained for back-compat: a non-compliant model that emits the old single-object
# form is still salvaged by _parse_file_records (which unwraps a {"files":[...]}).
BUILD_JSON_INSTRUCTION_FILES = (
    "\n\nOUTPUT FORMAT: one JSON object per line — "
    '{"path":str,"content":str} — no array, no fences, no prose.'
)



__all__ = ("GenerationDependencies", "run_chat", "run_code")
