"""Model catalog presentation, default selection, and curation commands."""

import argparse
import difflib
import json
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class CatalogDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def model_badges(m):
    # Catalog fields are untrusted network data — a scalar (e.g. 42) would crash
    # the `"reasoning" in feats` membership test; coerce to a list first.
    feats = m.get("supported_features")
    feats = feats if isinstance(feats, (list, tuple)) else []
    ins = m.get("input_modalities")
    ins = ins if isinstance(ins, (list, tuple)) else []
    badges = []
    if "reasoning" in feats:
        badges.append("reason")
    if "image" in ins or "video" in ins:
        badges.append("vision")
    if "tools" in feats:
        badges.append("tools")
    if "structured_outputs" in feats or "json_mode" in feats:
        badges.append("json")
    return ",".join(badges)


def format_model_line(m, chat_default, code_default, note, hidden, deps):
    _as_bool = deps._as_bool
    _humanize_ctx = deps._humanize_ctx
    paint = deps.paint
    sanitize = deps.sanitize
    # Same vocabulary as `ambient-codex models`' section headers: a model is either
    # serving now (READY) or spins up on demand — never a bare mystery glyph.
    # Both labels render 9 columns wide so every row stays aligned.
    flag = (paint("READY", "32") + "    " if _as_bool(m.get("is_ready"))
            else paint("on-demand", "2"))
    marks = []
    if m["id"] == chat_default:
        marks.append("*chat")
    if m["id"] == code_default:
        marks.append("*code")
    if hidden:
        marks.append("hidden")
    mark = f"  {','.join(marks)}" if marks else ""
    _name = m.get("name")                       # untrusted: may be a non-str
    name = _name.strip() if isinstance(_name, str) else ""
    badges = model_badges(m)
    # id + name are catalog (network) data — sanitize before they hit the
    # terminal (A12); the raw values are still used for the comparisons above.
    disp_id = sanitize(m["id"])
    disp_name = sanitize(name)
    line = (
        f"{flag}  {disp_id:40} {_humanize_ctx(m.get('context_length')):>5}ctx  "
        f"{badges:<20}{mark}"
        + (f"   {disp_name}" if name and name.lower() not in m["id"].lower()
           else "")
    )
    if note:
        line += f"   · {sanitize(note[:40])}"
    return line


# A catalog id the API also lists under a primary (branded) id → the primary.
# The alias stays usable with an explicit -m; it is only collapsed from the
# storefront + the model COUNTS so all surfaces agree (models/doctor/curate).
CATALOG_ALIAS_OF = {"zai-org/GLM-5.1-FP8": "ambient/large"}


def dedupe_catalog(models):
    """Collapse a catalog row the API also lists under a second, more verbose
    id into the primary (branded) row, so the storefront never shows the same
    model twice. An explicit -m on either id still works."""
    seen_alias = {m["id"] for m in models}
    out = []
    for m in models:
        primary = CATALOG_ALIAS_OF.get(m["id"])
        if primary and primary in seen_alias:
            continue  # drop the alias if its primary is also listed
        out.append(m)
    return out


def dedupe_catalog_ids(ids):
    """Id-level dedup matching _dedupe_catalog, so a count taken from bare ids
    (doctor, curate) agrees with the deduped storefront count."""
    ids = list(ids)
    have = set(ids)
    return [i for i in ids
            if not (CATALOG_ALIAS_OF.get(i) and CATALOG_ALIAS_OF[i] in have)]


def run_models(args, api_key, api_url, conf, deps):
    KEY_CONSOLE_URL = deps.KEY_CONSOLE_URL
    LAUNCHER_NAME = deps.LAUNCHER_NAME
    _as_bool = deps._as_bool
    _dedupe_catalog = deps._dedupe_catalog
    curation = deps.curation
    fetch_models = deps.fetch_models
    format_model_line = deps.format_model_line
    is_hidden = deps.is_hidden
    resolve_key_and_backend = deps.resolve_key_and_backend
    resolve_model = deps.resolve_model
    models = [m for m in _dedupe_catalog(fetch_models(api_url, api_key))
              if isinstance(m, dict) and m.get("id")]
    models.sort(key=lambda m: (not _as_bool(m.get("is_ready")), m.get("id") or ""))
    chat_default = resolve_model(argparse.Namespace(model=None), conf, "chat")
    code_default = resolve_model(argparse.Namespace(model=None), conf, "code")
    configured = bool(resolve_key_and_backend(conf)[0])
    allow, hide, show, notes = curation(conf)
    hidden_ids = {m["id"] for m in models
                  if is_hidden(m["id"], allow, hide, show)}
    show_all = getattr(args, "all", False)
    if hidden_ids and len(hidden_ids) == len(models) and not show_all:
        print("ambient: your curation hides every model — showing all "
              "(fix: ambient-codex curate reset)", file=sys.stderr)
        show_all = True
    visible = [m for m in models if show_all or m["id"] not in hidden_ids]
    if getattr(args, "json", False):
        out = [{
            "id": m["id"], "name": m.get("name"),
            "ready": _as_bool(m.get("is_ready")),
            "context_length": m.get("context_length"),
            "max_output_length": m.get("max_output_length"),
            "features": m.get("supported_features") or [],
            "is_chat_default": m["id"] == chat_default,
            "is_code_default": m["id"] == code_default,
            "hidden": m["id"] in hidden_ids,
            "note": notes.get(m["id"]) or None,
        } for m in visible]
        print(json.dumps({"schema_version": 1, "configured": configured,
                          "models": out}, indent=2))
        return
    serving = [m for m in visible if _as_bool(m.get("is_ready"))]
    on_demand = [m for m in visible if not _as_bool(m.get("is_ready"))]
    if serving:
        print(f"Serving now ({len(serving)}) — ready for instant use:")
        for m in serving:
            print(format_model_line(m, chat_default, code_default,
                                    note=notes.get(m["id"]),
                                    hidden=m["id"] in hidden_ids))
    if on_demand:
        if serving:
            print()
        print(f"Available on demand ({len(on_demand)}) — "
              "spin up as demand arrives:")
        for m in on_demand:
            print(format_model_line(m, chat_default, code_default,
                                    note=notes.get(m["id"]),
                                    hidden=m["id"] in hidden_ids))
    print(
        f"\nDefaults: chat/audit={chat_default}  code={code_default}"
        "   (change with: ambient-codex use)   badges: reason vision tools json",
        file=sys.stderr,
    )
    if hidden_ids and not show_all:
        print(
            f"+ {len(hidden_ids)} hidden by your curation — see everything: "
            f"{LAUNCHER_NAME} models --all",
            file=sys.stderr,
        )
    if not configured:
        print(
            f"No API key configured — browsing only. Get one at "
            f"{KEY_CONSOLE_URL}, then run: {LAUNCHER_NAME} setup",
            file=sys.stderr,
        )


def run_use(args, api_key, api_url, conf, deps):
    _as_bool = deps._as_bool
    curation = deps.curation
    fetch_models = deps.fetch_models
    format_model_line = deps.format_model_line
    is_auto_model = deps.is_auto_model
    is_hidden = deps.is_hidden
    resolve_model = deps.resolve_model
    save_config_values = deps.save_config_values
    models = [m for m in fetch_models(api_url, api_key)
              if isinstance(m, dict) and m.get("id")]
    models.sort(key=lambda m: (not _as_bool(m.get("is_ready")), m.get("id") or ""))
    known_ids = [m["id"] for m in models]
    allow, hide, show, notes = curation(conf)
    chosen = args.model_id
    if not chosen:
        if not sys.stdin.isatty():
            sys.exit("ambient: no TTY for interactive picker; use: ambient-codex use <model-id>")
        chat_default = resolve_model(argparse.Namespace(model=None), conf, "chat")
        code_default = resolve_model(argparse.Namespace(model=None), conf, "code")
        pick_from = [m for m in models
                     if getattr(args, "all", False)
                     or not is_hidden(m["id"], allow, hide, show)]
        if not pick_from:
            pick_from = models
        n_hidden = len(models) - len(pick_from)
        print("Pick a default model (READY = serving right now; "
              "others spin up on demand):\n")
        for i, m in enumerate(pick_from, 1):
            print(f"  {i:2}. {format_model_line(m, chat_default, code_default, note=notes.get(m['id']))}")
        if n_hidden:
            print(f"\n  (+{n_hidden} hidden by curation — "
                  "`ambient-codex use --all` picks from everything)")
        try:
            raw = input("\nModel number: ").strip()
            idx = int(raw)
            if not 1 <= idx <= len(pick_from):
                raise ValueError
        except (ValueError, EOFError, KeyboardInterrupt):
            sys.exit("\nambient: cancelled (no valid selection)")
        chosen = pick_from[idx - 1]["id"]
    elif is_auto_model(chosen):
        # `ambient use auto[:cheapest|:largest]` stores the LITERAL spec —
        # it re-resolves against the live catalog on every call (and prints
        # each pick). Skip the catalog-id validation: it is not a model id.
        chosen = chosen.strip().lower()
        print(
            f"note: '{chosen}' is a delegation, not a model — every call "
            "picks a serving model from the catalog and prints its choice.",
            file=sys.stderr,
        )
    elif chosen not in known_ids:
        # Explicit user text, so a UNIQUE substring match resolves (still the
        # user's choice — never a guess between candidates). Otherwise suggest.
        subs = [i for i in known_ids if chosen.lower() in i.lower()]
        if len(subs) == 1:
            print(f"ambient: matched '{chosen}' → {subs[0]}", file=sys.stderr)
            chosen = subs[0]
        else:
            sugg = subs or difflib.get_close_matches(chosen, known_ids,
                                                     n=3, cutoff=0.4)
            hint = f" Did you mean: {', '.join(sugg[:3])}?" if sugg else ""
            sys.exit(f"ambient: unknown model '{chosen}'.{hint} "
                     "Run 'ambient-codex models' for the list.")
    if not is_auto_model(chosen) and is_hidden(chosen, allow, hide, show):
        print(
            f"note: '{chosen}' is hidden from the model menu — saving anyway "
            f"(surface it with: ambient-codex curate show {chosen}).",
            file=sys.stderr,
        )
    picked = next((m for m in models if m["id"] == chosen), None)
    if picked and not _as_bool(picked.get("is_ready")):
        print(
            f"note: '{chosen}' isn't serving at this moment — saving anyway; "
            "Ambient brings models up as demand arrives (`ambient-codex models` "
            "shows what's serving now).",
            file=sys.stderr,
        )
    if notes.get(chosen):
        print(f"note for {chosen}: {notes[chosen]}", file=sys.stderr)
    updates = {}
    if args.chat or not args.code:
        updates["AMBIENT_MODEL"] = chosen
    if args.code or not args.chat:
        updates["AMBIENT_CODE_MODEL"] = chosen
    save_config_values(updates)
    scope = " and ".join(
        s for s, on in (("chat/audit", "AMBIENT_MODEL" in updates),
                        ("code/agent", "AMBIENT_CODE_MODEL" in updates)) if on
    )
    print(f"Default for {scope} set to: {chosen}")


def run_curate(args, deps):
    LAUNCHER_NAME = deps.LAUNCHER_NAME
    _dedupe_catalog_ids = deps._dedupe_catalog_ids
    _split_csv = deps._split_csv
    curation = deps.curation
    is_hidden = deps.is_hidden
    read_config_file = deps.read_config_file
    resolve_api_url = deps.resolve_api_url
    resolve_key_and_backend = deps.resolve_key_and_backend
    resolve_model = deps.resolve_model
    safe_catalog = deps.safe_catalog
    save_config_values = deps.save_config_values
    usage_exit = deps.usage_exit
    """Model-curation surface: pick which models the plugin shows.
    Works pre-key (the catalog endpoint is unauthenticated); all writes go
    through the atomic + locked config writer. Curation shapes menus and
    automatic selection only — explicit -m / `use <id>` always works."""
    conf = read_config_file()
    verb = args.verb or "status"
    ids = list(args.ids or [])

    def _catalog_ids():
        key, _ = resolve_key_and_backend(conf)
        cat = safe_catalog(resolve_api_url(conf), key or "none")
        return [m.get("id") for m in cat
                if isinstance(m, dict) and m.get("id")]

    def _warn_unknown(entries):
        known = _catalog_ids()
        if not known:
            return  # offline — skip the nicety
        for e in entries:
            if any(ch in e for ch in "*?[") or e in known:
                continue
            print(f"warning: '{e}' is not in today's catalog — saved anyway "
                  "(the catalog changes as models scale up and down)",
                  file=sys.stderr)

    def _print_status():
        conf2 = read_config_file()
        allow2, hide2, show2, notes2 = curation(conf2)
        print(f"curation: allow={','.join(allow2) if allow2 else '(everything)'}"
              f"  hide={','.join(hide2) if hide2 else '(nothing)'}"
              + (f"  show-overrides={','.join(show2)}" if show2 else ""))
        user_notes = {k: v for k, v in notes2.items() if v}
        if user_notes:
            print("notes:")
            for k, v in sorted(user_notes.items()):
                print(f"  {k}: {v}")
        known = _dedupe_catalog_ids(_catalog_ids())  # match the storefront count
        if known:
            vis = [i for i in known if not is_hidden(i, allow2, hide2, show2)]
            print(f"{len(vis)} surfaced / {len(known) - len(vis)} hidden "
                  f"of {len(known)} catalog models")

    if verb == "status":
        _print_status()
        return
    if verb == "reset":
        save_config_values({"AMBIENT_MODELS_ALLOW": None,
                            "AMBIENT_MODELS_HIDE": None,
                            "AMBIENT_MODELS_SHOW": None,
                            "AMBIENT_MODEL_NOTES": None})
        print("curation reset — every catalog model is surfaced again")
        return
    # Every merge below is computed INSIDE the config lock from the freshest
    # file state (save_config_values callable form) — two terminals
    # editing curation concurrently can't lose each other's entries.
    if verb == "note":
        if not ids:
            usage_exit('usage: ambient-codex curate note <model-id> ["text"]')
        target, text = ids[0], " ".join(ids[1:]).replace("\n", " ")[:120]

        def _merge_note(fresh):
            try:
                stored = json.loads(fresh.get("AMBIENT_MODEL_NOTES") or "{}")
                if not isinstance(stored, dict):
                    stored = {}
            except json.JSONDecodeError:
                stored = {}
            stored[target] = text  # "" clears (and suppresses a built-in note)
            return {"AMBIENT_MODEL_NOTES": json.dumps(stored)}

        save_config_values(_merge_note)
        print(f"note for {target}: {text or '(cleared)'}")
        return
    if not ids:
        usage_exit(f'usage: ambient-codex curate {verb} <model-id-or-glob> ...')
    ns = argparse.Namespace(model=None)
    defaults = {resolve_model(ns, conf, "chat"), resolve_model(ns, conf, "code")}
    if verb == "hide":
        _warn_unknown(ids)

        def _merge_hide(fresh):
            hide_f = _split_csv(fresh.get("AMBIENT_MODELS_HIDE"))
            show_f = _split_csv(fresh.get("AMBIENT_MODELS_SHOW"))
            return {
                "AMBIENT_MODELS_HIDE":
                    ",".join(dict.fromkeys(hide_f + ids)),
                "AMBIENT_MODELS_SHOW":
                    ",".join(s for s in show_f if s not in ids) or None,
            }

        save_config_values(_merge_hide)
        for e in ids:
            if e in defaults:
                print(f"note: '{e}' is a current lane default — it keeps "
                      "working; pick a new default with: ambient-codex use",
                      file=sys.stderr)
        print(f"hidden: {', '.join(ids)}")
    elif verb == "show":

        def _merge_show(fresh):
            allow_f = _split_csv(fresh.get("AMBIENT_MODELS_ALLOW"))
            hide_f = [h for h in _split_csv(fresh.get("AMBIENT_MODELS_HIDE"))
                      if h not in ids]
            show_f = _split_csv(fresh.get("AMBIENT_MODELS_SHOW"))
            # Exact entries leave HIDE; anything STILL hidden (a glob, or
            # strict-allow mode) gets an explicit show-override.
            still = [i for i in ids if is_hidden(i, allow_f, hide_f, show_f)]
            return {
                "AMBIENT_MODELS_HIDE": ",".join(hide_f) or None,
                "AMBIENT_MODELS_SHOW":
                    ",".join(dict.fromkeys(show_f + still)) or None,
            }

        save_config_values(_merge_show)
        print(f"surfaced: {', '.join(ids)}")
    elif verb == "only":
        _warn_unknown(ids)
        save_config_values({"AMBIENT_MODELS_ALLOW": ",".join(ids),
                            "AMBIENT_MODELS_HIDE": None,
                            "AMBIENT_MODELS_SHOW": None})
        print(f"menu restricted to: {', '.join(ids)} "
              "(new catalog models stay hidden until added — "
              f"{LAUNCHER_NAME} curate reset undoes this)")
    _print_status()
