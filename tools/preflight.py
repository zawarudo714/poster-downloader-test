"""
Mechanical checks that must pass before a deploy. Run: python tools/preflight.py

════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS
════════════════════════════════════════════════════════════════════════════
Every check below was previously done by hand, from memory, at the end of a
session. That works exactly as often as somebody remembers. Each one is here
because it caught something real:

  · `log_activity` called with the wrong argument names — a 500 on the first
    click of a button that had "passed review"
  · a missing dictionary key in a status message — a 500 on a page that
    compiled perfectly
  · a `data-` hook renamed in a template but not in the JS — a panel that
    silently stopped rendering, with no error anywhere
  · an orphan closing tag that reparented a modal into a hidden section, so
    the ADD ACCOUNT button on an unrelated tab stopped appearing
  · a settings key read but never declared in DEFAULTS — an instant 500,
    invisible to every static check there is

════════════════════════════════════════════════════════════════════════════
WHAT IT CANNOT DO
════════════════════════════════════════════════════════════════════════════
This proves the WIRING is intact. It cannot tell you the behaviour is right —
that is what the invariant checks in diagnostics.py are for, and they run
against the live database rather than the source.

Two different questions, and both are needed:

    preflight   — "is anything obviously disconnected?"   before deploy
    diagnostics — "is the live system in a sane state?"   after, and forever

════════════════════════════════════════════════════════════════════════════
IT IS ALSO THE BUTTON MAP
════════════════════════════════════════════════════════════════════════════
`--map` prints every control on every screen and what it calls. That is the
enumeration rule 1 of CLAUDE.md asks for, done mechanically instead of from
memory — and it is the fastest way to prove a UI overhaul did not quietly
disconnect anything.

Exit code is 0 when clean, 1 when anything failed. Warnings do not fail.
"""

from __future__ import annotations

import ast
import builtins
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
JS = APP / "static" / "js"
TPL = APP / "templates"
NODE = ROOT / "worker_service"

BUILTINS = set(dir(builtins))

# Tags whose open/close counts must match. Void elements are excluded because
# they legitimately never close.
PAIRED_TAGS = ("div", "section", "form", "table", "thead", "tbody", "tr",
               "td", "th", "select", "label", "button", "span", "p")

failures: list[str] = []
warnings: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def py_files() -> list[Path]:
    out = []
    for base in (APP, NODE, ROOT / "scripts"):
        if base.is_dir():
            out += [p for p in base.rglob("*.py") if "__pycache__" not in str(p)]
    return out


# ════════════════════════════════════════════════════════════════════════════
#  1. PYTHON
# ════════════════════════════════════════════════════════════════════════════

def check_python_compiles() -> None:
    for path in py_files():
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as e:
            fail(f"{path.relative_to(ROOT)}:{e.lineno} will not compile: {e.msg}")


def check_undefined_names() -> None:
    """
    Names used but never bound anywhere in the file.

    `py_compile` does NOT catch this — a typo'd function name compiles fine
    and explodes the first time that line runs, which for an admin page is
    the first time you click the button.
    """
    for path in py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError:
            continue                      # already reported above

        bound: set[str] = {"__file__", "__name__", "__doc__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.Global):
                bound.update(node.names)

        unknown = sorted({
            n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id not in bound and n.id not in BUILTINS
        })
        if unknown:
            fail(f"{path.relative_to(ROOT)}: undefined name(s) {unknown}")


def check_settings_keys_declared() -> None:
    """
    Every PIPELINE settings key must be in pipeline.DEFAULTS.

    `pipeline.get_setting` raises on an undeclared key on purpose, so a typo
    cannot silently resolve to None. The consequence is that an undeclared
    key is an instant 500 which passes every other check here: the code
    parses, the names are defined, the hooks all exist.

    ════════════════════════════════════════════════════════════════════════
    IT MUST KNOW WHICH get_setting IT IS LOOKING AT
    ════════════════════════════════════════════════════════════════════════
    `payments.py` has its OWN `get_setting(db, key, default)` — unrelated,
    with its own store, and its keys are correctly absent from DEFAULTS.
    Several admin routes import that one INSIDE a function.

    A plain text search therefore reports `week_start_day` as a fatal bug on
    a line that has worked in production for months. That is worse than no
    check: a report with a known-false line in it is a report nobody reads.
    So this resolves, per call site, which function the name came from.
    """
    src = (APP / "pipeline.py").read_text(encoding="utf-8")
    declared: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (isinstance(target, ast.Name) and target.id == "DEFAULTS"
                and isinstance(value, ast.Dict)):
            declared |= {k.value for k in value.keys
                         if isinstance(k, ast.Constant)}

    if not declared:
        fail("pipeline.DEFAULTS could not be read — this check is blind")
        return

    for path in py_files():
        if path.name == "payments.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        # Which function bodies pull the name in from payments. Any call
        # inside one of those is talking to the other store.
        from_payments: set[int] = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.ImportFrom)
                        and "payments" in (node.module or "")
                        and any(a.name in ("get_setting", "set_setting")
                                for a in node.names)):
                    from_payments.add(id(fn))
                    break

        # Map every call to the function that encloses it.
        enclosing: dict[int, int] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call):
                        enclosing.setdefault(id(node), id(fn))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            if name not in ("get_setting", "set_setting"):
                continue
            if enclosing.get(id(node)) in from_payments:
                continue                      # the other store, not ours
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                continue                      # key is computed; cannot judge
            key = node.args[1].value
            if isinstance(key, str) and key not in declared:
                fail(f"{path.relative_to(ROOT)}:{node.lineno}: settings key "
                     f"'{key}' is used but not declared in pipeline.DEFAULTS "
                     f"— instant 500")


def check_activity_log_calls() -> None:
    """Every log_activity call must match audit.log's signature."""
    sig = {"db", "user", "action", "target_type", "target_id", "details",
           "commit"}
    for path in py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "log_activity"):
                extra = {k.arg for k in node.keywords if k.arg} - sig
                if extra:
                    fail(f"{path.relative_to(ROOT)}:{node.lineno}: "
                         f"log_activity got unexpected {sorted(extra)}")


# ════════════════════════════════════════════════════════════════════════════
#  2. JAVASCRIPT AND TEMPLATES
# ════════════════════════════════════════════════════════════════════════════

def check_module_attributes() -> None:
    """
    A typo'd call into one of our own modules — `SH.strandedd(...)`.

    The undefined-name check above only sees bare names. `SH.stranded` is an
    attribute on a module that IS defined, so a misspelt attribute sails
    through every static check and explodes the first time the line runs.
    That is the "500 on the first click of a button that passed review"
    failure, and it is the most common way a cross-module rename breaks
    something.

    Only OUR modules are resolved. Third-party attributes are not checkable
    without importing, and importing is not something a preflight should do.
    """
    ours: dict[str, set[str]] = {}
    for path in py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        names = {
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef))
        } | {
            t.id for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        } | {
            n.target.id for n in tree.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
        }
        # Anything the module IMPORTS is reachable through it too —
        # `P.AppSetting` works because pipeline.py imports AppSetting. Leaving
        # these out produced fifty false alarms on the first run.
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                names |= {(a.asname or a.name).split(".")[0] for a in n.names}
        # UNION across files that share a stem, rather than the last one
        # winning. There are two `store_health.py` — one on the server, one on
        # the node — and keying by stem alone made every server-side call look
        # like a typo. Being lenient here costs almost nothing: a genuine
        # misspelling exists in NEITHER file, which is what this is for.
        ours[path.stem] = ours.get(path.stem, set()) | names

    for path in py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        # alias -> module stem, for `from x import y as A` and `import x as A`
        alias_of: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name in ours:
                        alias_of[a.asname or a.name] = a.name
            elif isinstance(node, ast.Import):
                for a in node.names:
                    stem = a.name.split(".")[-1]
                    if stem in ours:
                        alias_of[a.asname or stem] = stem

        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            base = getattr(node.value, "id", None)
            if base not in alias_of:
                continue
            module = alias_of[base]
            if module == path.stem:
                continue
            if node.attr.startswith("_"):
                continue                     # private helpers, not worth it
            if node.attr not in ours[module]:
                fail(f"{path.relative_to(ROOT)}:{node.lineno}: "
                     f"{base}.{node.attr} does not exist in {module}.py")


def check_js_parses() -> None:
    if not JS.is_dir():
        return
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except Exception:
        warn("node is not installed here, so JS was not parsed")
        return
    for path in sorted(JS.glob("*.js")):
        r = subprocess.run(["node", "--check", str(path)], capture_output=True,
                           text=True)
        if r.returncode:
            fail(f"{path.relative_to(ROOT)} will not parse: "
                 f"{r.stderr.strip().splitlines()[0] if r.stderr else '?'}")


def check_template_tags_balance() -> None:
    """
    Open and close counts must match.

    Browsers repair broken markup silently by closing containers early, which
    reparents everything below the break. The symptom then appears somewhere
    unrelated to the edit — a modal ending up inside a hidden panel, a button
    that responds to clicks by doing nothing.
    """
    for path in sorted(TPL.glob("*.html")):
        src = path.read_text(encoding="utf-8", errors="ignore")
        # Comments hold example markup; counting it produces false alarms.
        body = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
        for tag in PAIRED_TAGS:
            opens = len(re.findall(rf"<{tag}[\s>]", body))
            closes = len(re.findall(rf"</{tag}>", body))
            if opens != closes:
                fail(f"{path.relative_to(ROOT)}: <{tag}> {opens} open vs "
                     f"{closes} close — markup will be silently reparented")


def _js_for_template(tpl: Path) -> list[Path]:
    """The scripts a template loads. Its hooks may live in any of them."""
    src = tpl.read_text(encoding="utf-8", errors="ignore")
    return [JS / m for m in re.findall(r"/static/js/([a-z_0-9]+\.js)", src)
            if (JS / m).is_file()]


def check_hooks_exist() -> None:
    """
    Every `[data-x]` the JS looks for must exist somewhere it can find it.

    A renamed hook is the classic UI-overhaul failure: no error, no console
    message, just a panel that silently stops rendering. Hooks the JS creates
    in its own generated HTML count — that is where half of them live.
    """
    for tpl in sorted(TPL.glob("*.html")):
        scripts = _js_for_template(tpl)
        if not scripts:
            continue
        tpl_src = tpl.read_text(encoding="utf-8", errors="ignore")
        base = (TPL / "base.html").read_text(encoding="utf-8", errors="ignore")

        for js in scripts:
            js_src = js.read_text(encoding="utf-8", errors="ignore")
            wanted = set(re.findall(r"""q\(\s*['"]\[data-([a-z-]+)\]['"]""", js_src))
            wanted |= set(re.findall(
                r"""querySelector\(\s*['"]\[data-([a-z-]+)\]['"]""", js_src))

            # The QUERIES must be removed before searching this file, or the
            # check matches itself and can never fail. Found by sabotage:
            # renaming a hook to a typo left preflight green, because
            # `q('[data-run-pannel]')` obviously contains "data-run-pannel".
            built = re.sub(r"""(?:q|querySelector)\(\s*['"]\[data-[a-z-]+\]['"]\)""",
                           "", js_src)

            for hook in sorted(wanted):
                token = f"data-{hook}"
                if token in tpl_src or token in base or token in built:
                    continue
                fail(f"{js.name} looks for [{token}] — not in "
                     f"{tpl.name}, base.html, or its own generated HTML")


def check_hidden_ancestors() -> None:
    """
    A control inside a `hidden` element that nothing ever unhides.

    Unhiding a child of a hidden parent does nothing, which produces the
    perfect silent failure: a button that responds to every click by doing
    absolutely nothing.
    """
    for tpl in sorted(TPL.glob("*.html")):
        src = tpl.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"<(section|div)[^>]*\bdata-([a-z-]+)[^>]*\bhidden\b",
                             src):
            hook = m.group(2)
            unhidden = any(
                f"data-{hook}" in js.read_text(encoding="utf-8", errors="ignore")
                for js in _js_for_template(tpl))
            if not unhidden:
                warn(f"{tpl.name}: <{m.group(1)} data-{hook}> is hidden and no "
                     f"script ever shows it")


# ════════════════════════════════════════════════════════════════════════════
#  3. THE BUTTON MAP — every control, and what it calls
# ════════════════════════════════════════════════════════════════════════════

def button_map() -> dict[str, list[tuple[str, str]]]:
    """
    {js file: [(action, the endpoint IT calls)]} — read from the source.

    The endpoint is found by looking inside each action's own handler block,
    not by listing every endpoint in the file. A first version did the
    latter and produced "this button calls one of these ten addresses",
    which is not a map — it is the same information you started with,
    rearranged. Same rule as any figure on a screen: it has to answer the
    question you actually asked.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for js in sorted(JS.glob("*.js")):
        src = js.read_text(encoding="utf-8", errors="ignore")

        actions = set(re.findall(r"""data-action=["']([a-z-]+)""", src))
        actions |= set(re.findall(
            r"""(?:dataset\.action|\ba)\s*===\s*['"]([a-z-]+)['"]""", src))
        if not actions:
            continue

        rows = []
        for action in sorted(actions):
            endpoint = "?"
            # The handler for this action, and roughly its body.
            m = re.search(
                r"""(?:dataset\.action|\ba)\s*===\s*['"]"""
                + re.escape(action) + r"""['"]""", src)
            if m:
                body = src[m.end():m.end() + 600]
                # Stop at the next action so we do not read its endpoint.
                nxt = re.search(r"""(?:dataset\.action|\ba)\s*===\s*['"]""",
                                body)
                if nxt:
                    body = body[:nxt.start()]
                call = re.search(r"""API\s*\+\s*['"](/[a-z-]+)""", body) \
                    or re.search(r"""['"](/admin/[a-z/_-]+)['"]""", body)
                if call:
                    endpoint = call.group(1)
                elif re.search(r"\breload\(|loadDesigns\(|\bhidden\b", body):
                    endpoint = "(page only — no server call)"
            rows.append((action, endpoint))
        out[js.name] = rows
    return out


def check_actions_are_handled() -> None:
    """
    Every button that exists must have code that reacts to it.

    A `data-action` nobody handles is a button that does nothing when
    pressed — and after a restyle, that is the single likeliest thing to be
    true and the hardest to notice by eye.
    """
    for tpl in sorted(TPL.glob("*.html")):
        scripts = _js_for_template(tpl)
        if not scripts:
            continue
        blob = "\n".join(js.read_text(encoding="utf-8", errors="ignore")
                         for js in scripts)
        src = tpl.read_text(encoding="utf-8", errors="ignore")
        for action in sorted(set(re.findall(r'data-action="([a-z-]+)"', src))):
            if f"'{action}'" not in blob and f'"{action}"' not in blob:
                fail(f"{tpl.name}: button '{action}' has no handler — "
                     f"pressing it does nothing")


# ════════════════════════════════════════════════════════════════════════════

CHECKS = [
    ("python compiles",           check_python_compiles),
    ("no undefined names",        check_undefined_names),
    ("settings keys declared",    check_settings_keys_declared),
    ("activity log calls valid",  check_activity_log_calls),
    ("cross-module calls exist",  check_module_attributes),
    ("javascript parses",         check_js_parses),
    ("template tags balance",     check_template_tags_balance),
    ("page hooks exist",          check_hooks_exist),
    ("buttons have handlers",     check_actions_are_handled),
    ("nothing stuck behind hidden", check_hidden_ancestors),
]


def main() -> int:
    if "--map" in sys.argv:
        print("BUTTON MAP — every control and what it calls\n")
        total = resolved = 0
        for js, rows in button_map().items():
            print(f"{js}")
            for action, endpoint in rows:
                total += 1
                resolved += endpoint != "?"
                print(f"    {action:<24} -> {endpoint}")
            print()
        # Said plainly rather than left as a column of question marks. The
        # HANDLER check covers all of them; only this endpoint column is
        # best-effort, because the older screens dispatch their actions in
        # shapes this cannot follow.
        print(f"{total} controls · {resolved} traced to an endpoint · "
              f"{total - resolved} shown as '?'")
        if total != resolved:
            print("A '?' means the endpoint could not be traced from the "
                  "source, NOT that the button is broken —\n'buttons have "
                  "handlers' in the main run proves every one of them is "
                  "wired to something.")
        return 0

    print("PREFLIGHT\n")
    for label, fn in CHECKS:
        before = len(failures), len(warnings)
        try:
            fn()
        except Exception as e:                    # a broken check is a failure
            fail(f"check '{label}' crashed: {type(e).__name__}: {e}")
        f = len(failures) - before[0]
        w = len(warnings) - before[1]
        mark = "FAIL" if f else ("warn" if w else " ok ")
        extra = f"  ({f} problem(s))" if f else (f"  ({w} warning(s))" if w else "")
        print(f"  [{mark}] {label}{extra}")

    if warnings:
        print("\nWARNINGS — worth a look, not blocking:")
        for w in warnings:
            print(f"  · {w}")

    if failures:
        print(f"\n{len(failures)} PROBLEM(S) — do not deploy:\n")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    print("\nAll clear. The wiring is intact.")
    print("Note: this proves nothing is disconnected, not that behaviour is")
    print("right — that is what the Diagnostics page checks, against the")
    print("live database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
