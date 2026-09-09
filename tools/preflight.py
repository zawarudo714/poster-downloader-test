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
from pathlib import Path, PurePosixPath

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
    Names used but never bound IN SCOPE.

    `py_compile` does NOT catch this — a typo'd name compiles fine and
    explodes the first time that line runs.

    ════════════════════════════════════════════════════════════════════════
    WHY PER-FUNCTION AND NOT PER-FILE (v156, and it cost a broken screen)
    ════════════════════════════════════════════════════════════════════════
    The first version pooled every bound name FILE-WIDE, so a parameter of
    any function vouched for the same name in every OTHER function. v155
    shipped a route whose body read `full` while only its SIBLING route had
    a `full` parameter — a NameError on every request, the poster pane
    rendered empty, and this check was green. A check that looks at the
    wrong scope is coverage-shaped blindness.

    Scope model (deliberately simple, tuned to zero false positives on this
    codebase): a function may use its own bindings, its enclosing
    functions' bindings, module-level bindings, and builtins. Class bodies
    are treated as module-level. Wildcard imports would blind it — there
    are none here, and the sabotage test will notice if the model rots.
    """
    def bindings_of(node) -> set[str]:
        """Names BOUND directly inside `node`'s own scope (not nested defs)."""
        out: set[str] = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in (a.posonlyargs + a.args + a.kwonlyargs):
                out.add(arg.arg)
            if a.vararg: out.add(a.vararg.arg)
            if a.kwarg: out.add(a.kwarg.arg)
        stack = list(ast.iter_child_nodes(node))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(n.name)
                continue                      # nested scope binds elsewhere
            if isinstance(n, ast.ClassDef):
                out.add(n.name)
                continue
            if isinstance(n, ast.Lambda):
                continue
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                out.add(n.id)
            elif isinstance(n, ast.arg):
                out.add(n.arg)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for alias in n.names:
                    out.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                out.update(n.names)
            stack.extend(ast.iter_child_nodes(n))
        return out

    def loads_of(fn) -> set[str]:
        """Names LOADED in `fn`'s own scope (not nested defs/lambdas),
        including inside comprehensions (which bind their own targets)."""
        loads: set[str] = set()
        comp_bound: set[str] = set()
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                              ast.ClassDef)):
                continue
            if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp,
                              ast.GeneratorExp)):
                for comp in n.generators:
                    for t2 in ast.walk(comp.target):
                        if isinstance(t2, ast.Name):
                            comp_bound.add(t2.id)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                loads.add(n.id)
            stack.extend(ast.iter_child_nodes(n))
        return loads - comp_bound

    for path in py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError:
            continue                      # already reported above

        module_bound = bindings_of(tree) | {"__file__", "__name__", "__doc__"}

        # every function, with its chain of enclosing function scopes
        problems: list[str] = []

        def visit(node, enclosing: set[str]):
            for child in ast.walk(node):
                pass
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    own = bindings_of(child)
                    scope = module_bound | enclosing | own
                    unknown = sorted(
                        n for n in loads_of(child)
                        if n not in scope and n not in BUILTINS)
                    if unknown:
                        problems.append(
                            f"{child.name}() uses undefined name(s) {unknown}")
                    visit(child, enclosing | own)
                elif isinstance(child, ast.ClassDef):
                    visit(child, enclosing)
                else:
                    visit(child, enclosing)

        visit(tree, set())

        # module level itself
        top_unknown = sorted(
            n for n in loads_of(tree)
            if n not in module_bound and n not in BUILTINS)
        if top_unknown:
            problems.append(f"module level uses undefined name(s) {top_unknown}")

        for msg in problems:
            fail(f"{path.relative_to(ROOT)}: {msg}")


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


def _module_level_defs(tree):
    """
    Module-level functions in one file, as {name: (min_pos, max_pos, names,
    takes_kwargs)}. Methods are deliberately excluded, so `self` never has to
    be reasoned about, and so does anything decorated, because a decorator is
    free to change the signature it presents.
    """
    nested = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if inner is node:
                    continue
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nested.add(inner.name)
        elif isinstance(node, ast.ClassDef):
            for inner in ast.walk(node):
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nested.add(inner.name)

    out = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.decorator_list:
            continue
        a = node.args
        slots = [p.arg for p in (a.posonlyargs + a.args)]
        max_pos = None if a.vararg else len(slots)
        min_pos = len(slots) - len(a.defaults)
        allowed = set(slots) | {p.arg for p in a.kwonlyargs}
        required_kw = {p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults)
                       if d is None}
        out[node.name] = (min_pos, max_pos, slots, allowed, required_kw,
                          a.kwarg is not None, node.lineno)
    # A name that is also declared inside some other function or class is
    # ambiguous from here, so it is left alone.
    return {k: v for k, v in out.items() if k not in nested}


def check_call_arity() -> None:
    """
    A call into our own code with the WRONG NUMBER of arguments.

    `check_module_attributes` above proves the name exists. It says nothing
    about how the function is meant to be CALLED, and that gap shipped a 500:
    `_recall_targets` called `P.project_scope(db.query(...), project.id,
    default_project_id=...)`, but `project_scope` takes ONE positional
    argument and hands back a filter condition rather than a query. Every
    press of the count button on the recall panel raised TypeError (owner's
    find, 2026-09-09). Python compiled it, no name was undefined, the
    attribute really existed, and every other check stayed green.

    The question asked here is narrow on purpose, because a full call-graph
    analyser would have to be right about far too much to be right about
    anything:

      · only MODULE-LEVEL functions in files we own, so `self` never arises
      · nothing decorated, because a decorator may change the signature
      · nothing whose name is declared in more than one place we can see
      · calls using * or ** are skipped, since the count is not knowable

    What it catches is exactly the mistake above: too many arguments, too
    few, or a keyword the function does not have.
    """
    defs_by_stem: dict[str, dict] = {}
    ambiguous: dict[str, set] = {}
    trees: dict[Path, object] = {}

    for path in py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        trees[path] = tree
        found = _module_level_defs(tree)
        if path.stem in defs_by_stem:
            # Two files share a stem (there are two store_health.py). Any name
            # they both define cannot be resolved from a call site, so it is
            # dropped rather than guessed at.
            clash = set(defs_by_stem[path.stem]) & set(found)
            ambiguous.setdefault(path.stem, set()).update(clash)
            defs_by_stem[path.stem].update(found)
        else:
            defs_by_stem[path.stem] = dict(found)

    def check_one(path, lineno, label, sig, call):
        min_pos, max_pos, slots, allowed, required_kw, takes_kw, _ = sig
        if any(isinstance(a, ast.Starred) for a in call.args):
            return
        if any(k.arg is None for k in call.keywords):
            return
        n_pos = len(call.args)
        kw = [k.arg for k in call.keywords]

        if max_pos is not None and n_pos > max_pos:
            fail(f"{path.relative_to(ROOT)}:{lineno}: {label} takes "
                 f"{max_pos} positional argument(s), {n_pos} given")
            return
        if not takes_kw:
            for name in kw:
                if name not in allowed:
                    fail(f"{path.relative_to(ROOT)}:{lineno}: {label} has no "
                         f"argument named '{name}'")
                    return
        supplied = set(slots[:n_pos]) | set(kw)
        missing = [n for n in slots[:min_pos] if n not in supplied]
        missing += [n for n in sorted(required_kw) if n not in supplied]
        if missing:
            fail(f"{path.relative_to(ROOT)}:{lineno}: {label} is missing "
                 f"argument(s): {', '.join(missing)}")

    for path, tree in trees.items():
        alias_of: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name in defs_by_stem:
                        alias_of[a.asname or a.name] = a.name
            elif isinstance(node, ast.Import):
                for a in node.names:
                    stem = a.name.split(".")[-1]
                    if stem in defs_by_stem:
                        alias_of[a.asname or stem] = stem

        here = _module_level_defs(tree)
        # Any name bound by an assignment, a parameter, an import or a `for`
        # could be shadowing the module-level function of the same name, so
        # bare calls to it are left alone.
        shadowed = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                shadowed.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                shadowed |= {(a.asname or a.name).split(".")[0]
                             for a in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                shadowed |= {p.arg for p in
                             a.posonlyargs + a.args + a.kwonlyargs}
                if a.vararg:
                    shadowed.add(a.vararg.arg)
                if a.kwarg:
                    shadowed.add(a.kwarg.arg)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute):
                base = getattr(fn.value, "id", None)
                module = alias_of.get(base)
                if module is None or module == path.stem:
                    continue
                if fn.attr in ambiguous.get(module, set()):
                    continue
                sig = defs_by_stem[module].get(fn.attr)
                if sig:
                    check_one(path, node.lineno, f"{base}.{fn.attr}()",
                              sig, node)
            elif isinstance(fn, ast.Name):
                if fn.id in shadowed:
                    continue
                sig = here.get(fn.id)
                if sig:
                    check_one(path, node.lineno, f"{fn.id}()", sig, node)


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

    ════════════════════════════════════════════════════════════════════════
    ONE SCRIPT, SEVERAL PAGES
    ════════════════════════════════════════════════════════════════════════
    The check is per SCRIPT, against every template that loads it. Doing it
    per template-and-script PAIR reported three false failures at once:
    `admin.js` is loaded by the dashboard, the users page and the image
    browser, and its lightbox hooks live only in the last of those. Demanding
    each page carry every hook the shared script mentions is asking for
    something that was never true.
    """
    used_by: dict[Path, list[Path]] = {}
    for tpl in sorted(TPL.glob("*.html")):
        for js in _js_for_template(tpl):
            used_by.setdefault(js, []).append(tpl)

    base = (TPL / "base.html").read_text(encoding="utf-8", errors="ignore")

    for js, templates in sorted(used_by.items()):
        tpl_src = "\n".join(t.read_text(encoding="utf-8", errors="ignore")
                            for t in templates)
        where = ", ".join(t.name for t in templates)
        js_src = js.read_text(encoding="utf-8", errors="ignore")
        # ── EVERY WAY THIS CODEBASE ASKS FOR A HOOK ──────────────────
        #
        # It listed only `q` and `querySelector`, so a hook fetched with
        # `querySelectorAll` was never checked at all — which sabotage
        # found the day the jobs CANCEL button was added, because that
        # is exactly how it collects its buttons. A check with a hole in
        # it is most dangerous where the hole is: it reads as coverage.
        ASKERS = r"(?:q|querySelector|querySelectorAll|closest)"
        wanted = set(re.findall(
            ASKERS + r"""\(\s*['"]\[data-([a-z-]+)\]['"]""", js_src))

        # The QUERIES must be removed before searching this file, or the
        # check matches itself and can never fail. Found by sabotage:
        # renaming a hook to a typo left preflight green, because
        # `q('[data-run-pannel]')` obviously contains "data-run-pannel".
        built = re.sub(ASKERS + r"""\(\s*['"]\[data-[a-z-]+\]['"]""",
                       "", js_src)

        for hook in sorted(wanted):
            token = f"data-{hook}"
            if token in tpl_src or token in base or token in built:
                continue
            fail(f"{js.name} looks for [{token}] — not in {where}, "
                 f"base.html, or its own generated HTML")


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
    {js file: [(action, the endpoint IT calls and what it SENDS)]}.

    The endpoint is found by looking inside each action's own handler block,
    not by listing every endpoint in the file. A first version did the
    latter and produced "this button calls one of these ten addresses",
    which is not a map — it is the same information you started with,
    rearranged. Same rule as any figure on a screen: it has to answer the
    question you actually asked.

    ════════════════════════════════════════════════════════════════════════
    IT SHOWS THE REQUEST BODY, AND THAT IS WHY
    ════════════════════════════════════════════════════════════════════════
    The map used to print only the address. A bug lived in exactly the space
    it did not cover: three buttons on one screen sent `{auto, mode}` and a
    fourth sent `{}`, so the AUTOMATIC tickbox directly above it was read for
    three of them and silently dropped for the fourth. Unattended, that meant
    226 live listings switched off and then a run parked at a gate all night
    waiting for a person.

    Nothing was disconnected, so no check could fail. But side by side the
    odd one out is obvious:

        start              -> /start              {auto, mode}
        start-continue     -> /start              {auto, mode}
        start-missing      -> /start              {auto, mode}
        deactivate-missing -> /deactivate-missing {}

    A shared control read by SOME handlers and not others is its own class of
    defect — the same shape as a method with zero callers. This is what makes
    it visible, and it is a report rather than a failure because both columns
    have honest reasons to differ.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for js in sorted(JS.glob("*.js")):
        src = js.read_text(encoding="utf-8", errors="ignore")

        actions = set(re.findall(r"""data-action=["']([a-z-]+)""", src))
        actions |= set(re.findall(
            r"""(?:dataset\.action|\ba)\s*===\s*['"]([a-z-]+)['"]""", src))
        if not actions:
            continue

        rows = [(action, _endpoint_for(src, action))
                for action in sorted(actions)]
        out[js.name] = rows
    return out


# ── Tracing an action to the address it calls ───────────────────────────────
#
# THE PREVIOUS VERSION KNEW ONE DISPATCH STYLE AND ONE URL STYLE, so 35 of 57
# controls read "?". Widening the regexes made it worse rather than better: it
# began reporting `reject -> /admin/revisions/:id/approve` and a truncated
# `/greenl`, because it scanned a fixed 900-character window forward from the
# action's name and reported whatever address it bumped into — the neighbouring
# handler's, or half a string sliced mid-token.
#
# A map that says a button calls the opposite endpoint is far worse than one
# that says "?". So this follows the CODE instead of scanning near it, and
# where it cannot be sure it says so.

_URL_PATTERNS = (
    r"""API\s*\+\s*['"](/[A-Za-z0-9/_-]+)""",          # API + '/foo'
    r"""`\$\{API\}(/[A-Za-z0-9/_${}.?=&-]+)`""",       # `${API}/foo`
    r"""[`'"](/(?:admin|api)/[A-Za-z0-9/_${}.?=&-]+)[`'"]""",   # '/admin/foo'
    r"""[`'"](/[A-Za-z0-9_-]+(?:/[A-Za-z0-9/_${}.?=&-]+)?)[`'"]""",  # '/foo/${id}'
)

_SKIP_PAYLOAD_KEYS = ("method", "headers", "credentials", "cache", "signal")


def _block_from(src: str, pos: int, limit: int = 4000) -> str:
    """
    The brace-matched block starting at the first '{' at or after pos.

    Brace counting rather than a fixed window, because a window cuts mid-token
    — that is where the truncated `/greenl` came from — and it reads straight
    into whatever handler happens to sit next in the file.
    """
    start = src.find("{", pos)
    if start < 0 or start - pos > 300:
        return src[pos:pos + 400]
    depth, i, end = 0, start, min(len(src), start + limit)
    while i < end:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    return src[start:end]


def _function_body(src: str, name: str) -> str:
    """The body of a named function, however this codebase declares it."""
    for pat in (r"\basync\s+function\s+" + re.escape(name) + r"\s*\(",
                r"\bfunction\s+" + re.escape(name) + r"\s*\(",
                r"\b(?:const|let|var)\s+" + re.escape(name)
                + r"\s*=\s*(?:async\s*)?(?:function\s*)?\("):
        m = re.search(pat, src)
        if m:
            return _block_from(src, m.end())
    return ""


def _url_in(body: str) -> Optional[re.Match]:
    for pat in _URL_PATTERNS:
        m = re.search(pat, body)
        if m:
            return m
    return None


def _describe(body: str, m: re.Match) -> str:
    """An address plus the keys it sends, from a matched URL."""
    endpoint = re.sub(r"\$\{[^}]*\}", ":id", m.group(1))
    endpoint = endpoint.split("?")[0]
    sent = re.search(re.escape(m.group(0)) + r"""[^;]{0,200}?\{([^{}]*)\}""", body)
    if sent is not None:
        keys = [k for k in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", sent.group(1))
                if k not in _SKIP_PAYLOAD_KEYS]
        endpoint += "  {" + ", ".join(keys) + "}"
    return endpoint


def _handler_bodies(src: str, action: str) -> list[str]:
    """
    Every block that runs when this action fires. All four styles used here:

      case 'pay': doPay(); break;            <- dispatch table
      if (a === 'pay') { ... }               <- delegated listener
      const payBtn = q('[data-action="pay"]'); payBtn.onclick = ...
      querySelectorAll('[data-action="pay"]').forEach(btn => { ... })
    """
    a = re.escape(action)
    out = []

    # case 'x': ... break;   /   if (a === 'x') { ... }
    for m in re.finditer(r"""case\s*['"]""" + a + r"""['"]\s*:""", src):
        nxt = re.search(r"""\bcase\s*['"]|\bdefault\s*:""", src[m.end():])
        out.append(src[m.end(): m.end() + (nxt.start() if nxt else 300)])
    for m in re.finditer(r"""(?:dataset\.action|\ba)\s*===\s*['"]""" + a + r"""['"]""", src):
        out.append(_block_from(src, m.end()))

    # forEach(btn => { ... }) bound straight off the selector
    for m in re.finditer(r"""querySelectorAll\([^)]*data-action=["']"""
                         + a + r"""["'][^)]*\)\s*\.forEach\s*\(""", src):
        out.append(_block_from(src, m.end()))

    # const xBtn = ...querySelector('[data-action="x"]')  ->  xBtn's listener
    for m in re.finditer(r"""(?:const|let|var)\s+(\w+)\s*=\s*[^;\n]*"""
                         r"""data-action=["']""" + a + r"""["']""", src):
        var = m.group(1)
        lis = re.search(re.escape(var)
                        + r"""\s*(?:\.addEventListener\([^,]+,|\.onclick\s*=)""",
                        src[m.end():])
        if lis:
            out.append(_block_from(src, m.end() + lis.end()))
    return out


def _endpoint_for(src: str, action: str) -> str:
    """
    The address one action calls, and what it sends with it.

    Follows the handler, and if the handler just calls a named function it
    follows that too — one hop, which is how most of admin_pipeline.js is
    written (`case 'titles-greenlight': greenlightSelected(); break;`).

    Returns "?" rather than a guess. An inventory that is wrong is worse than
    one that admits a gap, because the gap gets looked at and the wrong answer
    does not.
    """
    page_only = False
    for body in _handler_bodies(src, action):
        if not body.strip():
            continue
        m = _url_in(body)
        if m:
            return _describe(body, m)

        # The handler delegates. Follow the functions it names, once.
        for call in re.findall(r"\b([a-zA-Z_]\w{2,})\s*\(", body):
            if call in ("if", "for", "while", "switch", "return", "function",
                        "catch", "typeof", "parseInt", "parseFloat", "Number"):
                continue
            inner = _function_body(src, call)
            if inner:
                m = _url_in(inner)
                if m:
                    return _describe(inner, m)
        if re.search(r"\breload\(|loadDesigns\(|\bhidden\b|showSection\(", body):
            page_only = True
    return "(page only — no server call)" if page_only else "?"


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

def check_tool_calls_exist() -> None:
    """
    An app function that a TOOL calls by name, and that does not exist.

    ════════════════════════════════════════════════════════════════════════
    THE TOOLS ARE OUTSIDE EVERY OTHER CHECK
    ════════════════════════════════════════════════════════════════════════
    `tools/` scripts drive the app through strings — commands sent over SSH,
    little programs piped into a container. None of that is imported, so
    nothing above ever type-checks it, and a wrong name only shows up when
    the step runs.

    Caught for real while building the migration tool: its password check
    called `P.decrypt()` and `P.get_account_password()`, neither of which
    exists. The real name is `decrypt_secret`. That check's entire job is
    proving account passwords survive the move — it would have failed for
    the wrong reason, at the worst moment, and been believed.

    Same rule as never inventing a URL or a form field: if it is outside the
    file you are writing, look it up.
    """
    app_defined: set[str] = set()
    for path in (APP / "pipeline.py", APP / "listing_check.py"):
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        app_defined |= {n.name for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for tool in sorted((ROOT / "tools").glob("*.py")):
        src = tool.read_text(encoding="utf-8", errors="ignore")
        for name, line in _called_names(src, r"\bP\.([a-z_][a-z_0-9]*)\("):
            if name not in app_defined:
                fail(f"tools/{tool.name}:{line} calls P.{name}() — "
                     f"no such function in the app")


def _called_names(src: str, pattern: str) -> list[tuple[str, int]]:
    """
    Matches in code and in ORDINARY strings, but never in comments or
    docstrings.

    ════════════════════════════════════════════════════════════════════════
    BOTH HALVES WERE GOT WRONG, IN OPPOSITE DIRECTIONS
    ════════════════════════════════════════════════════════════════════════
    Searching the raw text flagged this very check's own docstring, which
    names the invented functions in order to explain them — the same
    mistake the falsy-zero check made with its comment.

    Blanking every string then silently broke the check completely: these
    tools drive the app by piping PYTHON SNIPPETS over SSH, so the calls
    that matter live inside triple-quoted strings. The sabotage stopped
    firing and the report went green.

    So: comments and docstrings out, every other string in. Docstrings are
    found through the syntax tree rather than by guessing at quote styles,
    because "the first string in a function" is a structural fact and
    pattern-matching quotes is not.
    """
    import io
    import tokenize

    lines = src.splitlines(keepends=True)

    def offset(row: int, col: int) -> int:
        return sum(len(l) for l in lines[:row - 1]) + col

    blanked = list(src)

    def blank(start: int, end: int) -> None:
        for i in range(start, min(end, len(blanked))):
            if blanked[i] != "\n":
                blanked[i] = " "

    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                blank(offset(*tok.start), offset(*tok.end))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass

    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None) or []
            first = body[0] if body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                blank(offset(first.lineno, first.col_offset),
                      offset(first.end_lineno, first.end_col_offset))
    except SyntaxError:
        pass

    cleaned = "".join(blanked)
    return [(m.group(1), cleaned[:m.start()].count("\n") + 1)
            for m in re.finditer(pattern, cleaned)]


def check_page_context() -> None:
    """
    A page rendered without the context its LAYOUT needs.

    ════════════════════════════════════════════════════════════════════════
    JINJA FAILS SILENTLY, WHICH IS THE WHOLE PROBLEM
    ════════════════════════════════════════════════════════════════════════
    `base.html` chooses which navigation to draw with `{% if user.role ==
    'admin' %}`. An undefined `user` is falsy, not an error — so a route that
    forgets to pass it renders a page with NO NAVIGATION AT ALL. No
    exception, no warning, nothing in a log. Just a screen with no way off
    it, which is exactly how the Listing check tab shipped.

    Every other check here would pass it: the template parses, its tags
    balance, its hooks exist, its buttons have handlers. Nothing looks at
    whether the DATA the layout depends on was supplied.

    A failure, not a warning: there is no legitimate reason for an admin page
    to render without its navigation.
    """
    base = (TPL / "base.html")
    if not base.is_file():
        return
    base_src = base.read_text(encoding="utf-8", errors="ignore")

    # What the layout reads before anything page-specific runs. Derived from
    # base.html rather than hardcoded, so adding a new layout-level variable
    # extends this check automatically.
    required = {name for name in ("user", "active_tab")
                if re.search(r"\{[%{][^}]*\b" + name + r"\b", base_src)}
    if not required:
        return

    # template name -> the context keys each render site passes
    for path in ROOT.glob("app/routes/*.py"):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(
                r"""TemplateResponse\((.{0,400}?)\)\s*$""",
                src, re.M | re.S):
            call = m.group(1)
            tpl = re.search(r"""["']([a-z_0-9]+\.html)["']""", call)
            if not tpl:
                continue
            # Which template does it extend? Only base.html's needs apply.
            page = TPL / tpl.group(1)
            if not page.is_file():
                continue
            if 'extends "base.html"' not in page.read_text(
                    encoding="utf-8", errors="ignore"):
                continue
            given = set(re.findall(r"""["']([a-z_0-9]+)["']\s*:""", call))
            for name in sorted(required - given):
                fail(f"{path.relative_to(ROOT)}: renders {tpl.group(1)} "
                     f"without '{name}' — base.html needs it, and Jinja will "
                     f"silently render nothing rather than complain")


def check_queries_in_loops() -> None:
    """
    A database query inside a loop — one round trip per row.

    ════════════════════════════════════════════════════════════════════════
    FOUND BY LUCK, WHICH IS WHY IT IS NOW A CHECK
    ════════════════════════════════════════════════════════════════════════
    `listing_check.findings()` called `accounts(db)` inside its row loop.
    Invisible against the five rows on the test server; 2,000 queries against
    the real 4,811. It surfaced only because it was read aloud while
    answering an unrelated question — nothing would have caught it, and the
    symptom on production would have been "the page is slow", which sends
    you looking in the wrong place entirely.

    A WARNING, not a failure: a query inside a loop over three accounts is
    perfectly reasonable. The point is that it should be a decision rather
    than an accident, and the fix is almost always to hoist one lookup out.

    ════════════════════════════════════════════════════════════════════════
    TWO THINGS THE FIRST VERSION GOT WRONG, BOTH FOUND BY SABOTAGE
    ════════════════════════════════════════════════════════════════════════
    It scored ZERO against the very bug it was written for. The offending
    call was `accounts(db)` — a helper that queries — not `db.query`
    directly, and the check only knew the literal form. A check blind to its
    own motivating case is the exact failure this file exists to prevent, so
    it now resolves which FUNCTIONS in the codebase perform queries and
    counts a call to one of those as a query.

    And it warned 86 times, which is the same thing as warning never. Most
    were `for` bodies looping over a handful of accounts. It now looks only
    inside COMPREHENSIONS, which is where a per-row lookup actually hides in
    this codebase — building a response payload one row at a time.
    """
    # ── Which of our own functions perform a query ───────────────────────
    queriers: set[str] = set()
    trees: dict[Path, ast.Module] = {}
    for path in py_files():
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
    for tree in trees.values():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "query"):
                    queriers.add(fn.name)
                    break

    def is_query(call: ast.AST) -> str:
        if not isinstance(call, ast.Call):
            return ""
        f = call.func
        if (isinstance(f, ast.Attribute) and f.attr in ("query", "scalar")
                and isinstance(f.value, ast.Name)
                and f.value.id in ("db", "session")):
            return f"{f.value.id}.{f.attr}()"
        # A call to one of our own querying helpers counts too — that is
        # what the first version missed entirely.
        name = (f.id if isinstance(f, ast.Name)
                else f.attr if isinstance(f, ast.Attribute) else "")
        return f"{name}()" if name in queriers else ""

    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                                     ast.GeneratorExp)):
                continue
            # A comprehension's FIRST iterable is evaluated once, not per
            # item, so a query there is fine. Only the element expression
            # and any condition run repeatedly.
            inside = ([node.elt] if hasattr(node, "elt")
                      else [node.key, node.value])
            for gen in node.generators:
                inside += gen.ifs
            for sub in inside:
                for call in ast.walk(sub):
                    what = is_query(call)
                    if what:
                        warn(f"{path.relative_to(ROOT)}:{call.lineno} — "
                             f"{what} runs once per item in this "
                             f"comprehension; hoist it out if the list can "
                             f"be long")


def check_falsy_zero_defaults() -> None:
    """
    `Number(x) || 300` — a legitimate ZERO silently becomes the default.

    ════════════════════════════════════════════════════════════════════════
    ALSO FOUND BY LUCK
    ════════════════════════════════════════════════════════════════════════
    The listing-check screen estimated how long a sweep would take from the
    configured pause between requests. Setting that pause to zero is a
    perfectly good thing to do — and zero is falsy, so it fell through to
    300 and the screen quoted the same time whether or not the pause was
    turned off. Caught only because the estimator happened to be exercised
    with a zero while testing something else.

    The shape is general: any numeric setting where 0, "" or false is a
    REAL value the owner might choose. `||` cannot tell those apart from
    "missing". Use an explicit check instead.

    ════════════════════════════════════════════════════════════════════════
    `|| 0` IS FINE, AND THE FIRST VERSION DID NOT KNOW THAT
    ════════════════════════════════════════════════════════════════════════
    It flagged thirteen lines, every one of them `parseFloat(x) || 0`, where
    the fallback IS zero so nothing can be lost. It also flagged its own
    explanatory comment. Thirteen false lines is a report nobody reads —
    same lesson as the settings check reporting `week_start_day`.

    Only a NON-ZERO fallback can silently replace a real zero, so that is
    the only thing worth saying.
    """
    pattern = re.compile(
        r"(?:Number|parseInt|parseFloat)\([^;]*?\)\s*\|\|\s*([0-9]*\.?[0-9]+)")
    for js in sorted(JS.glob("*.js")):
        src = js.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(src.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue          # a comment about the bug is not the bug
            m = pattern.search(line)
            if m and float(m.group(1)) != 0:
                warn(f"{js.name}:{i} — falls back to {m.group(1)}, so a real "
                     f"ZERO here is silently replaced: {stripped[:60]}")


# ── A GUARD IS ONLY A GUARD IF EVERY PATH CALLS IT ──────────────────────
#
# (file, what the risky thing looks like, what protects it, plain words).
#
# Add a row whenever a protective call has to accompany a risky one. The
# point is not the individual rule — it is that "somebody remembered" stops
# being the mechanism.
GUARDED: list[tuple[str, str, tuple[str, ...], str]] = [
    # ── external_id REPEATS ACROSS PROJECTS ─────────────────────────────
    #
    # It is the `0` column from a project's OWN sheet, so every project
    # starts again at 1: the movie list and MUSIK both hold an external_id
    # 2, `The Dark Knight` and `Radiohead`. Looking one up without saying
    # which project returns whichever row the query reaches first.
    #
    # Measured 2026-08-25: the upload-history import did exactly this and
    # matched 0 of 4,865 images, while reporting a page of plausible
    # findings about the wrong project's titles. Nothing looked broken —
    # the numbers were confident and completely wrong.
    ("app/**/*.py", ".external_id ==",
     ("project_scope(", "scope_titles(", "_title_scope("),
     "looks a title up by its sheet number without scoping to a project, "
     "and that number repeats in every project"),
    ("scripts/**/*.py", ".external_id ==",
     ("project_scope(", "scope_titles(", "_titles_by_ext_query("),
     "looks a title up by its sheet number without scoping to a project, "
     "and that number repeats in every project"),
    # ── HANDING OUT A TITLE IS A PERMISSION QUESTION ────────────────────
    #
    # Every worker route that gives a worker a title scopes it — except
    # `go_to_title`, which claimed an unclaimed title by id with no check at
    # all, so a worker assigned only to MUSIK could take a movie title.
    # Nothing on the screen offers such an id, which is exactly why it went
    # unnoticed: it was unreachable by clicking and wide open to anything
    # else.
    ("app/routes/worker.py", "claimed_by_id   = user.id",
     ("_may_touch(", "_scope_to_project(", "_worker_project("),
     "claims a title for a worker without checking the title is in a "
     "project that worker is allowed to work in"),
]


def check_local_imports_not_used_earlier() -> None:
    """
    A name imported INSIDE a function, but used earlier in that same function.

    ════════════════════════════════════════════════════════════════════════
    WHY
    ════════════════════════════════════════════════════════════════════════
    `import x` anywhere in a function makes `x` a LOCAL for the whole
    function, so a use above that line raises UnboundLocalError at runtime —
    even when the module imports the same name at the top and every other
    function uses it happily.

    Nothing static catches it. `py_compile` is happy. The undefined-name
    check is happy, because the name IS bound — just not yet. It only fails
    when that line actually runs.

    `MEASURED 2026-08-27`: `save_image()` resolved the project three times,
    each behind its own `from ..pipeline import resolve_project`. Adding a
    fourth use ABOVE them shipped as v130 and broke every paste-a-URL save
    with a 500. Preflight was green. The owner found it by saving a poster.

    That is the shape this whole file exists to prevent: a change that
    passes every check and fails on the first real click.

    Reported as a WARNING rather than a failure, because a local import
    below a use of the same name is legal wherever the earlier use is a
    different binding — but it is worth a look every time.
    """
    for path in sorted((ROOT / "app").rglob("*.py")) + \
                sorted((ROOT / "scripts").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Names this function imports locally, and where.
            imported: dict[str, int] = {}
            for node in ast.walk(fn):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        name = alias.asname or alias.name.split(".")[0]
                        imported.setdefault(name, node.lineno)
            if not imported:
                continue
            # Any LOAD of that name strictly above its import line.
            for node in ast.walk(fn):
                if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                        and node.id in imported
                        and node.lineno < imported[node.id]):
                    warn(f"{rel}:{node.lineno} — {fn.name}() uses "
                         f"'{node.id}' before importing it locally on line "
                         f"{imported[node.id]}. A local import makes the name "
                         f"local for the WHOLE function, so this raises "
                         f"UnboundLocalError when it runs.")
                    break


def check_state_changes_are_logged() -> None:
    """
    An admin endpoint that changes something must say so in the activity log.

    ════════════════════════════════════════════════════════════════════════
    WHY
    ════════════════════════════════════════════════════════════════════════
    `CLAUDE.md`: "ActivityLog for every state change — actor, target,
    timestamp, JSON detail." The owner cannot read the database, so the
    activity log is the only way he can answer "why did this happen" after
    the fact.

    `MEASURED 2026-08-27`: `api_skip_failures` permanently excluded items
    from the pipeline and logged nothing, while `api_retry_failures` — its
    neighbour on the same screen, doing the REVERSIBLE version of the same
    thing — logged properly. `api_update_node` could switch a node off while
    ban, delete and update ACCOUNT all logged. Both found by comparing
    siblings, not by reading either one alone.

    Reported as a WARNING. Some mutating endpoints genuinely do not need an
    entry — a test that spends nothing, a read-through cache write — and a
    hard failure would train people to add a log line to silence it rather
    than to think. The list is short enough to read.

    ALLOW lists the ones deliberately left alone, WITH a reason, so the
    warning stays short enough that a new one stands out.
    """
    ALLOW = {
        "api_test": "a diagnostic that changes nothing lasting",
        "api_test_gpt_process": "a single test generation; its spend is metered separately",
        "api_trigger_run": "the run it starts records itself",
        "api_cancel_job": "the job carries its own cancelled state and reason",
        "master_upload": "records itself as an ImportJob row with started_by",
        "chat_admin_mark_read": "read-state bookkeeping, not a state change worth auditing",
        "api_review_remember": (
            "an autosave of a slider position, fired every few hundred "
            "milliseconds while the owner works through hundreds of posters. "
            "Logging each one would bury the entries that matter under "
            "thousands of 'moved a slider' rows, which is how a log stops "
            "being read. The moment that IS auditable — releasing the poster "
            "with that placement settled — is logged by the approve endpoint."),
    }
    MUTATING = ("post", "put", "delete", "patch")

    # `admin.py` as well as `*_admin.py`. The first version's glob quietly
    # excluded the largest admin file — reviewed 2026-08-27, one deploy after
    # the check shipped. Nothing real was hiding there, but a check whose
    # coverage claim is wrong is the exact thing this file exists to prevent.
    targets = sorted((ROOT / "app" / "routes").glob("*_admin.py"))
    if (ROOT / "app" / "routes" / "admin.py").exists():
        targets.append(ROOT / "app" / "routes" / "admin.py")
    for path in targets:
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(isinstance(d, ast.Call)
                       and getattr(d.func, "attr", "") in MUTATING
                       for d in fn.decorator_list):
                continue
            called = _calls_made_in(fn)
            if "log_activity" in called:
                continue
            if not (called & {"commit", "set_setting", "clear_setting"}):
                continue                      # nothing persisted
            if fn.name in ALLOW:
                continue
            warn(f"{rel}:{fn.lineno} — {fn.name}() changes state and writes "
                 f"no activity log. If that is deliberate, add it to ALLOW "
                 f"in check_state_changes_are_logged with the reason.")


def check_no_orphan_documents() -> None:
    """
    Every .md in the repo must be named in CLAUDE.md's document index.

    ════════════════════════════════════════════════════════════════════════
    WHY
    ════════════════════════════════════════════════════════════════════════
    `AUDIT.md` — a complete control-by-control walk of every screen, per
    role, per project — sat in the repo from 2026-08-17 referenced by
    nothing. On 2026-08-27 a session redid most of that work from scratch,
    missed findings the file already had, and only discovered it existed by
    listing the directory for an unrelated reason.

    The finding was worth less than the pointer to it. Writing a document is
    cheap; making the next session KNOW it exists is the part that fails,
    and it fails silently because an unread file looks exactly like a file
    with nothing in it.

    This is the cheapest rung that does not depend on anyone remembering.

    Deliberately checks NAMING, not content: a document can be stale and
    still be worth reading. Staleness is what the provenance tags are for.
    """
    index = (ROOT / "CLAUDE.md")
    if not index.exists():
        fail("CLAUDE.md is missing — nothing can point at the other documents")
        return
    text = index.read_text(encoding="utf-8", errors="ignore")

    skip_dirs = {".venv", "node_modules", ".git", "__pycache__"}
    docs = sorted(
        p for p in ROOT.rglob("*.md")
        if not any(part in skip_dirs for part in p.parts)
        and p.name != "CLAUDE.md"
    )
    if not docs:
        fail("no .md files found at all — this check is looking in the wrong place")
        return

    for p in docs:
        rel = p.relative_to(ROOT).as_posix()
        # Named by full path or by filename — both are unambiguous enough to
        # find, and demanding one exact form would be a rule about
        # formatting rather than about being reachable.
        if rel not in text and p.name not in text:
            fail(f"{rel} is not named in CLAUDE.md — a document nothing "
                 f"points at is a document nobody reads. Add it to the "
                 f"index, or delete it if it is finished with.")

    # ── AND THE OTHER DIRECTION ─────────────────────────────────────────
    #
    # The check above finds a document nobody points at. It cannot find a
    # POINTER TO A DOCUMENT THAT IS GONE, and those are the more dangerous
    # half: an orphan file is merely unread, whereas a dangling reference
    # sends the next session looking for guidance that no longer exists —
    # and, worse, implies the thing it described is still true.
    #
    # Found on 2026-09-01 by deleting two documents during the strip-down.
    # Every check stayed green while CLAUDE.md went on listing both in its
    # index table. The hole was at the edge of the pattern, which is where
    # they usually are.
    present = {p.name for p in docs} | {"CLAUDE.md"}
    for m in re.finditer(r"`([\w./-]+\.md)`", text):
        named = m.group(1)
        if PurePosixPath(named).name not in present:
            fail(f"CLAUDE.md points at {named}, which does not exist. A "
                 f"reference to a deleted document is worse than no "
                 f"reference: it sends the next session hunting for advice "
                 f"that is gone, and implies it still applies.")


def _calls_made_in(node: ast.AST) -> set[str]:
    """
    Every function name actually INVOKED inside a node.

    Both `foo(...)` and `mod.foo(...)`, because a guard is often reached
    through a module — `earnings_service.pause_reading(...)`. Comments and
    docstrings cannot appear here, which is the entire point: see the note in
    check_guards_are_called().
    """
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def check_guards_are_called() -> None:
    """
    Every function doing the risky thing must also do the protective thing.

    ════════════════════════════════════════════════════════════════════════
    THE BUG THIS EXISTS FOR
    ════════════════════════════════════════════════════════════════════════
    Switching a design OFF checked for TeePublic's interstitial wall on every
    page. Switching one back ON never had. On 25 Aug the wall appeared partway
    through a reactivation: 79 designs in a row loaded a wall, found no publish
    button, and were written down as broken designs — three seconds each,
    against twenty for real work. The give-up guard could not save it either,
    because that counts failures marked "this was the wall" and the mark is set
    inside the check that was never called.

    NOTHING WOULD HAVE CAUGHT IT. The code compiled, every name was defined,
    no hook was missing, no endpoint was orphaned, and the stage reported
    "Job finished". The owner found it by reading a log.

    So this asks the mechanical version of the question: which functions do
    the dangerous thing, and do they all do the safe thing? It is the same
    shape as `--map` printing the request body beside each endpoint — the odd
    one out is only obvious when its peers are listed next to it.

    ════════════════════════════════════════════════════════════════════════
    EVERY GUARD MUST BE A CALL — THE FIRST VERSION MATCHED ITSELF
    ════════════════════════════════════════════════════════════════════════
    It accepted the bare word `html_markers` as evidence of protection. That
    word is also the name of a PARAMETER, so putting the original bug back
    on purpose left the check green: the sabotaged function still mentioned
    it while doing nothing with it.

    Every entry therefore ends in `(` and names something that is actually
    invoked. The same failure as the hook check that searched the JS for the
    hook name and found its own query. A check that cannot go red is worse
    than no check, because it is counted as coverage.
    """
    for pattern, risky, guards, what in GUARDED:
        # A rule usually belongs to a KIND of code rather than to one file —
        # the project-scoping one applies to anything that touches titles.
        # A glob keeps it from being a list of files somebody has to
        # remember to extend, which is the same failure as a guard nobody
        # remembers to call.
        paths = ([ROOT / pattern] if "*" not in pattern
                 else sorted(ROOT.glob(pattern)))
        if not paths or not any(p.exists() for p in paths):
            fail(f"{pattern} matches nothing — this guard check is now blind")
            continue
        for path in paths:
            rel = path.relative_to(ROOT).as_posix()
            src = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue                # check_python_compiles owns that
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                body = ast.get_source_segment(src, node) or ""
                if risky not in body:
                    continue
                # PROSE MUST NOT SATISFY A GUARD.
                #
                # This used to substring-match the raw source, so a comment
                # reading "# see _may_touch()" counted as calling it. Found
                # 2026-08-27 by sabotage: the call was deleted, the comment
                # ABOUT the call stayed, and the check went right on passing.
                #
                # That is the same failure the docstring above describes and
                # believed it had fixed — ending an entry in "(" stops a bare
                # word matching a parameter name, but does nothing about a
                # sentence that mentions the function. Every entry here was
                # exposed to it, not just the new one.
                #
                # So the guards are looked for among the calls this function
                # ACTUALLY makes, taken from the syntax tree, where a comment
                # does not exist.
                called = _calls_made_in(node)
                if not any(g.rstrip("(") in called for g in guards):
                    fail(f"{rel}:{node.lineno} — {node.name}() {what} "
                         f"(expected one of: {', '.join(guards)})")


def check_endpoints_have_buttons() -> None:
    """
    An admin endpoint nothing on the site ever calls.

    ════════════════════════════════════════════════════════════════════════
    THE REVERSE OF "buttons have handlers", AND IT CAUGHT A REAL ONE
    ════════════════════════════════════════════════════════════════════════
    `/admin/pipeline/api/jobs/{id}/cancel` had worked since jobs existed and
    NOTHING had ever called it — so on the day a stopped sweep left two
    accounts' worth of switching queued, the only way to reach it was the
    browser's developer console.

    Zero callers of a working endpoint is a defect, not a style question. It
    is the same shape as `open_work_tab`, which was moved out of one function
    and never added to the other: the method existed, compiled, had a
    docstring, and nothing called it, so every upload ran in the wrong tab
    for months.

    A WARNING rather than a failure, because some endpoints legitimately
    have no button — the node's own API, and anything called by a script.

    ════════════════════════════════════════════════════════════════════════
    IT MUST LOOK AT TEMPLATES TOO, NOT JUST JAVASCRIPT
    ════════════════════════════════════════════════════════════════════════
    The first version searched only the JS and immediately reported
    `reset_password` as uncalled — it is a plain `<form method="post">` in
    admin_users.html and has worked for months. One known-false line is
    enough to make the whole report something people skim past, which is the
    same reason the settings check has to know which `get_setting` it is
    looking at.
    """
    posts: dict[str, Path] = {}
    for path in ROOT.glob("app/routes/*admin*.py"):
        src = path.read_text(encoding="utf-8")
        prefix_m = re.search(r'APIRouter\(prefix="([^"]+)"', src)
        prefix = prefix_m.group(1) if prefix_m else ""
        for m in re.finditer(r'@router\.post\("([^"]+)"\)', src):
            posts[prefix + m.group(1)] = path

    callers = "\n".join(
        p.read_text(encoding="utf-8")
        for folder, pattern in ((JS, "*.js"), (TPL, "*.html"))
        if folder.is_dir() for p in folder.glob(pattern))

    for route, path in sorted(posts.items()):
        # Callers build URLs from a base constant or a Jinja expression, so
        # the full path almost never appears literally. Match the END of it.
        #
        # ── A ONE-WORD TAIL IS NOT ENOUGH, AND THAT WAS FOUND BY SABOTAGE
        #
        # The first version matched only the final segment. Deleting the
        # jobs CANCEL button changed nothing, because the word "cancel" also
        # appears in `account-cancel` and `node-cancel` — two modal close
        # buttons that have nothing to do with it. The check could not go
        # red, which is the failure this whole file exists to prevent.
        #
        # So a route with a path parameter is matched across it, which is
        # specific enough to be about that one endpoint.
        segments = [s for s in route.split("/") if s]
        params = [i for i, s in enumerate(segments) if s.startswith("{")]
        if params and params[0] > 0:
            pattern = (re.escape(segments[params[0] - 1]) + "/"
                       # A path parameter is written in a dozen ways by the
                       # callers — `${id}`, `{{ u.id }}`, `' + id + '`. What
                       # they all share is no slash, no quote and no line
                       # break, so that is what is matched rather than any
                       # one syntax.
                       + "/".join(r"[^'\"`/\n]{1,40}?" if s.startswith("{")
                                  else re.escape(s)
                                  for s in segments[params[0]:]))
            shown = "/".join(segments[params[0] - 1:])
        else:
            pattern = re.escape(segments[-1]) if segments else ""
            shown = segments[-1] if segments else ""
        if pattern and not re.search(pattern, callers):
            warn(f"{path.relative_to(ROOT)}: POST {route} — no button, form "
                 f"or fetch anywhere calls it (nothing matches '{shown}')")


# ════════════════════════════════════════════════════════════════════════════
# Settings the owner can reach
# ════════════════════════════════════════════════════════════════════════════
#
# Keys that are DELIBERATELY not a box on the settings forms. Each one needs a
# reason, and the reason has to be one of three things — anything else means
# the owner cannot change a value he is expected to change.
#
#   WRITTEN BY THE APP   nobody types it; a human editing it would be a bug
#   ITS OWN PANEL        edited somewhere better than a one-line box
#   NOT A SETTING        a credential or a structure with its own screen
#
NOT_ON_THE_SETTINGS_FORMS = {
    # Written by the app itself. A human editing one of these would be a bug.
    "earnings_last_run_at":          "written by the nightly earnings read",
    "earnings_last_run_day":         "written by the nightly earnings read",
    "earnings_daily_run_started_at": "written by the nightly earnings read",
    "run_mode":        "set by the PAUSE NEW WORK and RESUME buttons",
    "run_mode_reason": "set by the PAUSE NEW WORK and RESUME buttons",
    # Their own panels, which are better than a one-line box.
    "process_script":     "the JSX editor",
    "openai_prompt":      "the PROMPT panel, with its own save and reset",
    "openai_style_image": "the STYLE REFERENCE panel, which uploads a file",
    "signature_image":    "the SIGNATURE panel, which uploads a file",
    "selectors":          "the selectors grid",
    "timings":            "the timings grid",
}

# ════════════════════════════════════════════════════════════════════════════
# Settings with nowhere to type them — the backlog, found 2026-09-03
# ════════════════════════════════════════════════════════════════════════════
#
# These have no box on any screen and no reason to be exempt. They are WARNED
# about rather than failed, so this check can be switched on today without
# stopping every deploy until eighteen screens have been built.
#
# THE SHAPE OF THE LIST IS THE POINT. Anything NEW fails immediately; only
# what was already broken on the day the check was written is tolerated, and
# it is tolerated by NAME, so the debt cannot grow quietly. Delete a line
# whenever you give that setting a box.
#
# The owner's own notes name several of these as things that must be on the
# dashboard — the pay rate, the allowed hosts, the image cap, the source
# search URL. The rule was written down and then broken for months, because a
# rule about something ABSENT has nothing to trip over. Clearing this list is
# a job for the QoL stage or the Mega Audit.
SETTINGS_WITH_NO_BOX_YET = {
    "pay_rate_kes", "soft_limit_per_title",
    "allowed_image_hosts", "review_min_width_px",
    "earnings_sales_url", "earnings_balance_url", "earnings_retry_window_hours",
    "listing_check_alarm_ratio", "listing_check_max_attempts",
    "listing_check_min_sample",
    "upload_pause_after_failures",
}


def check_settings_are_reachable() -> None:
    """
    Every pipeline setting must be editable from the dashboard.

    ════════════════════════════════════════════════════════════════════════
    THE DEFECT THIS EXISTS FOR
    ════════════════════════════════════════════════════════════════════════
    `brave_query_normal` and `brave_query_deep` sat in DEFAULTS with no box
    anywhere on the Pipeline page, from the day the Brave search was built
    until 2026-09-03. They are the words the worker's SEARCH button sends —
    the single thing the owner most needs to experiment with — and changing
    one meant a code edit and a deploy.

    Nothing could have found it. The code parses, the key is declared, the
    setting resolves, every page renders. The standing rule "anything he
    might want to tweak belongs in the dashboard" was written down and then
    quietly broken, because a rule about something that is ABSENT has nothing
    to trip over. He would have discovered it by looking for the box.

    So it is mechanical now: the code default and the form field are compared
    directly, and a new key with nowhere to type it fails before deploy.

    An exception has to be WRITTEN DOWN with a reason, in the table above.
    That is the point — not to force every key onto a form, but to make
    leaving one off a decision somebody made rather than a thing that
    happened.
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

    # The keys the settings forms offer. Read out of SETTINGS_GROUPS in the
    # JavaScript, which is where the metadata actually lives — the template
    # only holds empty containers, so looking there would find nothing and
    # report every key as missing.
    # EVERY admin screen, not just the Pipeline page. Settings are edited in
    # several places on purpose — the listing-check numbers live on the
    # Listing check tab, the store ones on the TeePublic tab — and a check
    # that only read admin_pipeline.js would report all of those as missing.
    # A report with known-false lines in it is a report nobody reads.
    on_forms: set[str] = set()
    for path in list((ROOT / "app" / "static" / "js").glob("*.js")) + \
            list((APP / "templates").glob("*.html")):
        text = path.read_text(encoding="utf-8")
        # Two ways a key reaches a form. The generated forms list it as the
        # first entry of a field row; a hand-written input carries it as a
        # data-setting attribute. Matched as a CALL-shaped pattern in both
        # cases rather than as a bare word, so a key merely mentioned in help
        # text or a comment does not count as a box.
        on_forms |= set(re.findall(r"\[\s*'([a-z0-9_]+)'\s*,\s*'(?:text|number|"
                                   r"password|select|bool|textarea)'", text))
        on_forms |= set(re.findall(r'data-set(?:ting)?="([a-z0-9_]+)"', text))
        on_forms |= set(re.findall(r"data-set(?:ting)?='([a-z0-9_]+)'", text))
        # A control wired by hand posts the key in its own save call, e.g.
        # `settings: { greenlight_mode: ... }`. That IS a box on a screen.
        on_forms |= set(re.findall(r"settings:\s*\{\s*([a-z0-9_]+)\s*:", text))

    if not on_forms:
        fail("no settings fields could be read out of the admin screens — "
             "this check is blind")
        return

    missing = declared - on_forms - set(NOT_ON_THE_SETTINGS_FORMS)

    for key in sorted(missing - SETTINGS_WITH_NO_BOX_YET):
        fail(f"pipeline setting '{key}' has no box on any admin screen. Add "
             f"it to SETTINGS_GROUPS in admin_pipeline.js, or to "
             f"NOT_ON_THE_SETTINGS_FORMS in this file with the reason why "
             f"nobody should type it")

    still_missing = sorted(missing & SETTINGS_WITH_NO_BOX_YET)
    if still_missing:
        warn(f"{len(still_missing)} settings still have no box anywhere: "
             + ", ".join(still_missing)
             + " — the known backlog from 2026-09-03, in "
               "SETTINGS_WITH_NO_BOX_YET")

    # A backlog entry for something that HAS been given a box is a line
    # claiming to excuse a defect that is fixed. Left in place it would go on
    # tolerating that key if the box were ever removed again.
    for key in sorted(SETTINGS_WITH_NO_BOX_YET - missing):
        warn(f"'{key}' now has a box — delete it from "
             f"SETTINGS_WITH_NO_BOX_YET so a future removal is caught")

    # The exception list must not rot. A key removed from DEFAULTS leaves an
    # entry here claiming to excuse something that no longer exists, and the
    # next person reads that as coverage.
    for key in sorted(set(NOT_ON_THE_SETTINGS_FORMS) - declared):
        warn(f"NOT_ON_THE_SETTINGS_FORMS still lists '{key}', which is no "
             f"longer a pipeline setting — remove the line")


def check_no_filter_on_fixed_element_ancestors() -> None:
    """
    The top bar must never carry a CSS filter.

    ════════════════════════════════════════════════════════════════════════
    THE DEFECT THIS EXISTS FOR (2026-09-06, v149)
    ════════════════════════════════════════════════════════════════════════
    `backdrop-filter: blur(...)` was put on `.topbar` for a frosted-glass
    look. A filter (or transform) on an element quietly makes it the
    CONTAINING BLOCK for every `position: fixed` descendant — and the
    sidebar nav lives INSIDE the top bar — so the whole rail was squeezed
    into the bar's 48 pixels and shipped as a broken scrollbox. Nothing
    else could have caught it: the CSS is valid, every hook exists, and
    the failure is purely geometric.

    Narrow on purpose: it checks the selectors that ANCESTOR the fixed
    sidebar (.topbar, .topbar-inner), not all of CSS. Add a selector here
    if another fixed element ever gains a styled ancestor.
    """
    import re as _re
    css = (APP / "static" / "css" / "style.css").read_text(encoding="utf-8")
    # Rule blocks whose selector list targets the bar itself.
    for m in _re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        if not _re.search(r"\.topbar(-inner)?\s*(,|$)", sel.strip()):
            continue
        if _re.search(r"(?<!-)\b(backdrop-)?filter\s*:", body) or \
           _re.search(r"\btransform\s*:", body):
            fail(".topbar carries a filter/transform — that traps the "
                 "fixed sidebar inside the 48px bar (see v149). Remove it.")


def _plated_image_classes() -> set[str]:
    """
    Every class name on, or above, a picture that sits on a colour plate.

    A PLATE is any element whose `background-color` is set from JavaScript —
    found from the code, never from a list here, because a list is one more
    thing somebody has to remember to extend. The markup is read as MARKUP,
    with a real parser and a real ancestor stack: the poster's own selector
    is `.review-img img`, which does not mention the plate at all, so
    matching selector TEXT against the plate's class proves nothing. (The
    first version of this check did exactly that, and stayed green with the
    original bug put back.)

    Fragments live in two places — Jinja templates, and the template literals
    inside the review script — so both are parsed. `${...}` holes are blanked
    first, which leaves the literal class names intact.
    """
    from html.parser import HTMLParser

    plates: set[str] = set()
    js_files = sorted((APP / "static" / "js").glob("*.js"))
    for js in js_files:
        text = js.read_text(encoding="utf-8")
        if not re.search(r"\.style\.backgroundColor\s*=", text):
            continue
        for hook in re.findall(r"\[data-([a-z0-9-]*canvas[a-z0-9-]*)\]", text):
            plates.add("data-" + hook)
    if not plates:
        return set()

    found: set[str] = set()

    class Walk(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, list[str], bool]] = []

        def handle_starttag(self, tag, attrs):
            names = {k for k, _v in attrs}
            classes = []
            for k, v in attrs:
                if k == "class" and v:
                    classes = v.split()
            on_plate = bool(names & plates) or any(
                s[2] for s in self.stack)
            if tag == "img" and any(s[2] for s in self.stack):
                for _t, cls, _p in self.stack:
                    found.update(cls)
                found.update(classes)
            if tag not in ("img", "br", "input", "hr", "meta", "link"):
                self.stack.append((tag, classes, on_plate))

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    return

    def walk(fragment: str) -> None:
        w = Walk()
        try:
            w.feed(fragment)
        except Exception:      # noqa: BLE001 — a tolerant read, never fatal
            pass

    hole = re.compile(r"\$\{[^{}]*\}")
    for tpl in sorted((APP / "templates").glob("*.html")):
        walk(tpl.read_text(encoding="utf-8"))
    for js in js_files:
        text = js.read_text(encoding="utf-8")
        for lit in re.findall(r"`([^`]*)`", text):
            if "<" in lit:
                walk(hole.sub(" ", lit))
    return found


def _plate_own_classes() -> set[str]:
    """
    The class names of the plate elements themselves.

    `_plated_image_classes()` returns everything on or above a plated picture,
    which is deliberately broad. This is the narrow companion: only the
    element actually carrying the plate hook, so a rule can be flagged for
    painting the PLATE without also flagging every card and cell around it.
    """
    from html.parser import HTMLParser

    plates: set[str] = set()
    js_files = sorted((APP / "static" / "js").glob("*.js"))
    for js in js_files:
        text = js.read_text(encoding="utf-8")
        if not re.search(r"\.style\.backgroundColor\s*=", text):
            continue
        for hook in re.findall(r"\[data-([a-z0-9-]*canvas[a-z0-9-]*)\]", text):
            plates.add("data-" + hook)
    if not plates:
        return set()

    found: set[str] = set()

    class Walk(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if not ({k for k, _v in attrs} & plates):
                return
            for k, v in attrs:
                if k == "class" and v:
                    found.update(v.split())

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

    def walk(fragment: str) -> None:
        w = Walk()
        try:
            w.feed(fragment)
        except Exception:      # noqa: BLE001 — a tolerant read, never fatal
            pass

    hole = re.compile(r"\$\{[^{}]*\}")
    for tpl in sorted((APP / "templates").glob("*.html")):
        walk(tpl.read_text(encoding="utf-8"))
    for js in js_files:
        text = js.read_text(encoding="utf-8")
        for lit in re.findall(r"`([^`]*)`", text):
            if "<" in lit:
                walk(hole.sub(" ", lit))
    return found


def check_no_background_on_composited_img() -> None:
    """
    A picture that sits on a chosen colour must not paint its own.

    ════════════════════════════════════════════════════════════════════════
    THE DEFECT THIS EXISTS FOR (2026-09-09)
    ════════════════════════════════════════════════════════════════════════
    The Approve Artwork screen shows a TRANSPARENT poster on a coloured
    plate, and the browser composites the two — which is the same arithmetic
    the server does when flattening, so the preview IS the finished poster.
    `.review-img img` also carried `background: #111`. A background on the
    picture paints on top of the plate, behind the see-through pixels, so
    the sky came out near-black whatever colour was picked. The zoom overlay
    had no such rule, which is why the owner reported that the colour
    preview "only works when I zoom in".

    Nothing could have found it. The CSS is valid, every hook exists, the
    handler fires, and the colour really is applied — to an element you
    cannot see. This environment cannot render a page, so only a person
    looking at the screen would ever have noticed.

    ════════════════════════════════════════════════════════════════════════
    THE RULE, STATED GENERALLY, AND WHY IT GOT WIDER ON 2026-09-09
    ════════════════════════════════════════════════════════════════════════
    The composite has exactly ONE background, and JavaScript owns it. Any
    background written in CSS is a second one, and the two cannot both be
    right.

    The first version only asked about the PICTURE, because at the time the
    colour lived on the plate and the picture was the thing wrongly painting
    over it. Then the colour MOVED onto the picture — the plate is wider
    than the artwork on a card and taller in the zoom, so a colour painted
    there showed as bars that are not in the finished file (owner,
    2026-09-09). At that moment this check went green for the wrong reason:
    its question was still "does the picture paint over the plate?", and the
    answer had stopped meaning anything.

    So it now asks the question that survives the colour moving: does ANY
    CSS rule give a background to the picture, or to the plate itself? Either
    one competes with the JavaScript, and which of the two is currently the
    carrier no longer has to be known.

    A rule is flagged only when every class it names sits on that path, so
    `.review-img-source img` — an opaque photograph outside any plate — is
    left alone.
    """
    plated = _plated_image_classes()
    plate_own = _plate_own_classes()
    if not plated:
        return

    css = (APP / "static" / "css" / "style.css").read_text(encoding="utf-8")
    # COMMENTS OUT FIRST. A rule's "selector" is everything back to the
    # previous brace, which includes the paragraph of prose above it — and
    # this file's prose is full of file names, so `.py` and `.js` were being
    # read as class names and every rule looked like it named something
    # unknown. That is what kept the first version of this check green with
    # the original bug put back.
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        # Anchored to the START of a declaration. Unanchored, `transition:
        # background-color .08s` reads as a background being set, which it is
        # not — and a check that fires on the innocent case gets switched off.
        if not re.search(r"(?:^|[{;])\s*background(-color)?\s*:", body):
            continue
        if re.search(r"background(-color)?\s*:\s*(none|transparent|inherit)",
                     body):
            continue
        for part in sel.split(","):
            part = part.strip()
            names = set(re.findall(r"\.([A-Za-z0-9_-]+)", part))
            if not names:
                continue
            # `img`, but also `img[data-poster-img]` and `img:hover`. The
            # first version stopped at a bare `img`, so the day the poster
            # rules were narrowed to `img[data-poster-img]` this check would
            # have gone quietly blind to the very bug it was written for.
            # Found by sabotage, not by reading it (2026-09-09).
            ends_in_img = re.search(
                r"(^|[\s>+~])img(\[[^\]]*\]|::?[a-zA-Z-]+(\([^)]*\))?)*\s*$",
                part)
            if ends_in_img and names <= plated:
                fail(f"CSS rule '{part}' gives a background to a picture "
                     f"that sits on a colour plate. The composite already "
                     f"has one background and JavaScript owns it, so this "
                     f"one competes with the colour being chosen and the "
                     f"preview goes wrong (see v163). Remove the background.")
            elif not ends_in_img and names <= plate_own:
                fail(f"CSS rule '{part}' gives a background to the colour "
                     f"plate itself. The plate is bigger than the artwork, "
                     f"so a colour there shows as bars beside the picture "
                     f"that are not in the finished file (see v169). The "
                     f"colour belongs on the picture. Remove the background.")


def check_overlay_sits_in_its_measuring_layer() -> None:
    """
    A mark placed by PERCENTAGE must live inside the box it is a percentage of.

    ════════════════════════════════════════════════════════════════════════
    THE DEFECT THIS EXISTS FOR (2026-09-09)
    ════════════════════════════════════════════════════════════════════════
    The signature is positioned `left: 91%`, `width: 16.8%`. A percentage in
    CSS resolves against the nearest positioned ANCESTOR, and the mark was a
    child of the colour plate. The plate is wider than the artwork on a card
    and taller than it in the zoom, while the server measures from the
    picture — `W, H = img.size` in app/signature.py. So the mark sat out on
    the coloured bar OUTSIDE the artwork, and came out a different size in
    the two views. The owner reported all three symptoms at once, because
    they are one mistake.

    `.sig-layer` now sits exactly over the poster's rendered box, and the
    mark goes inside it. This check asserts the nesting at every place the
    mark is built.

    ════════════════════════════════════════════════════════════════════════
    WHAT THIS CANNOT DO, SAID PLAINLY
    ════════════════════════════════════════════════════════════════════════
    It proves the mark is in the right BOX. It cannot prove that box is the
    right SIZE, because that is a fact about a rendered page and nothing here
    renders. Only somebody looking at the screen can confirm the layer really
    covers the picture. The nesting is the half that can be mechanised, and
    the half that was actually got wrong.
    """
    def offenders(text: str, sites: list[int], where: str) -> None:
        for i in sites:
            before = text[:i]
            plate = max(before.rfind("data-canvas"),
                        before.rfind("data-zoom-canvas"))
            layer = before.rfind("data-sig-layer")
            if plate == -1:
                continue          # not inside a plate at all; nothing to say
            if layer < plate:
                line = text.count("\n", 0, i) + 1
                fail(f"{where}:{line}: the signature mark is built directly "
                     f"inside the colour plate. Its percentages would be "
                     f"measured against the plate, which is bigger than the "
                     f"artwork, so it lands outside the picture (see v169). "
                     f"Put it inside the [data-sig-layer] element.")

    seen = 0
    for tpl in sorted((APP / "templates").glob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        sites = [m.start() for m in re.finditer(r'class="sig-mark"', text)]
        seen += len(sites)
        offenders(text, sites, str(tpl.relative_to(ROOT)))

    for js in sorted((APP / "static" / "js").glob("*.js")):
        text = js.read_text(encoding="utf-8")
        # The CALL SITE, not the definition. On the card the mark's markup
        # comes back from sigMarkHtml(), so where that result is dropped into
        # the page is what decides its positioning context.
        sites = [m.start() for m in re.finditer(r"\$\{\s*sigMarkHtml\s*\(", text)]
        seen += len(sites)
        offenders(text, sites, str(js.relative_to(ROOT)))

    # A check that found nothing to look at is a check reporting on nothing.
    # The mark was renamed once already; if it is renamed again this says so
    # instead of going quietly green.
    if not seen:
        fail("no signature mark could be found in any template or script — "
             "this check is blind, and was passing on an empty set")


def check_click_targets_do_not_swallow_controls() -> None:
    """
    A whole-region click target must not contain sliders or buttons.

    ════════════════════════════════════════════════════════════════════════
    THE DEFECT THIS EXISTS FOR (2026-09-09)
    ════════════════════════════════════════════════════════════════════════
    `data-zoom-open` sat on the whole Approve Artwork card, so clicking
    anywhere on it opened the full-screen overlay. The controls live on that
    card, so the handler carried an exception list: not `.review-color`, not
    `.review-versions`. The signature bar was added later and nobody extended
    the list, so every nudge of a slider threw the overlay open in the
    owner's face.

    That is the shape this catches, and it is bigger than one attribute: **a
    click target covering a REGION will swallow every control that region
    ever grows.** The exception list is the tell — it has to be remembered,
    and the thing that breaks it is a control added months later by somebody
    who never read the handler.

    So the rule is structural rather than behavioural: put the target on the
    thing that was actually meant to be clicked. Then there is nothing to
    exclude and nothing to remember.
    """
    from html.parser import HTMLParser

    # The attributes that make a whole element clickable, read from the code
    # rather than listed here — anything the scripts reach for with `closest`
    # and then act on. Kept to the zoom family on purpose: `data-action` marks
    # the buttons themselves, which are supposed to be clickable.
    hooks = {"data-zoom-open"}

    bad: list[tuple[str, int, str]] = []

    # A void element holds nothing, so it can never swallow a control — and
    # putting the target on one (an <img>) is exactly the fix this rule wants.
    VOID = {"img", "br", "input", "hr", "meta", "link", "source", "area"}
    CONTROLS = {"input", "button", "select", "textarea"}

    class Walk(HTMLParser):
        def __init__(self, where):
            super().__init__(convert_charrefs=True)
            self.where = where
            self.depth = 0          # >0 while inside a click target

        def handle_starttag(self, tag, attrs):
            names = {k for k, _v in attrs}
            if self.depth and tag in CONTROLS:
                bad.append((self.where, self.getpos()[0], tag))
            if tag in VOID:
                return              # opens no region, closes no region
            if self.depth:
                self.depth += 1
            elif names & hooks:
                self.depth = 1

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            if tag in VOID:
                return
            if self.depth:
                self.depth -= 1

    def walk(fragment: str, where: str) -> None:
        w = Walk(where)
        try:
            w.feed(fragment)
        except Exception:      # noqa: BLE001 — a tolerant read, never fatal
            pass

    seen = 0
    hole = re.compile(r"\$\{[^{}]*\}")
    for tpl in sorted((APP / "templates").glob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        seen += sum(text.count(h) for h in hooks)
        walk(text, str(tpl.relative_to(ROOT)))
    for js in sorted((APP / "static" / "js").glob("*.js")):
        text = js.read_text(encoding="utf-8")
        seen += sum(text.count(h) for h in hooks)
        for lit in re.findall(r"`([^`]*)`", text):
            if "<" in lit:
                walk(hole.sub(" ", lit), str(js.relative_to(ROOT)))

    for where, line, tag in bad:
        fail(f"{where}:{line}: a click target contains a <{tag}>. Clicking "
             f"that control will also fire the target's own handler, and an "
             f"exception list in the handler is something somebody has to "
             f"remember to extend (see v171). Put the target on the element "
             f"that was meant to be clicked instead.")

    # A renamed hook must say so rather than pass on an empty set.
    if not seen:
        fail("no click-target hooks were found in any template or script — "
             "this check is blind, and was passing on nothing")


def check_colour_names_have_rules() -> None:
    """
    A colour chosen in one file must be DEFINED in another. Both directions.

    ════════════════════════════════════════════════════════════════════════
    THE SHAPE THIS CATCHES (2026-09-09, v165)
    ════════════════════════════════════════════════════════════════════════
    Two things now pick a colour by NAME rather than by value. The sidebar
    marks each group `data-nav-tint="money"`, and the status strip asks for
    `chip('warn', ...)`. In both cases the name is looked up in the
    stylesheet, and in both cases a name with no rule behind it fails
    SILENTLY — the nav tint drops the whole declaration (an undefined
    variable inside `rgba()` is invalid CSS, so the border simply is not
    drawn), and the strip's chip quietly falls back to grey. A red "machine
    OFFLINE" chip rendering grey is the version of this that costs money.

    Nothing else could find it: the template is valid, the JavaScript parses,
    every hook exists, and this environment cannot render a page. So the
    only honest check is to read the names out of the source that CHOOSES
    them and confirm the source that DEFINES them has each one.

    Generalises past colours: whenever one file names something another file
    must provide — a CSS class, an icon key, a settings key — the two lists
    can be compared mechanically, and a name that means nothing is exactly
    the kind of mistake nobody notices by reading.
    """
    css = (APP / "static" / "css" / "style.css").read_text(encoding="utf-8")

    # ── The sidebar tints ────────────────────────────────────────────────
    wanted = set()
    for tpl in sorted((APP / "templates").glob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        wanted.update(re.findall(r'data-nav-tint="([a-z0-9_-]+)"', text))
    defined = set(re.findall(r'\[data-nav-tint="([a-z0-9_-]+)"\]', css))
    for name in sorted(wanted - defined):
        fail(f"the sidebar uses data-nav-tint=\"{name}\" but style.css has no "
             f"[data-nav-tint=\"{name}\"] rule — the whole group would draw "
             f"with no colour at all")
    for name in sorted(defined - wanted):
        warn(f"style.css defines the sidebar tint '{name}' and nothing uses "
             f"it — delete it rather than leaving a colour nobody can reach")

    # ── The status strip's chip tones ────────────────────────────────────
    js = (APP / "static" / "js" / "pulse.js")
    if not js.is_file():
        return
    text = js.read_text(encoding="utf-8")
    tones = set(re.findall(r"chip\(\s*'([a-z0-9_-]+)'", text))
    have = set(re.findall(r"\.pulse-([a-z0-9_-]+)\s*\{[^}]*--chip-tint", css))
    for tone in sorted(tones - have):
        fail(f"the status strip asks for chip('{tone}', ...) but style.css "
             f"has no .pulse-{tone} rule setting --chip-tint — that chip "
             f"would render grey whatever it is trying to say")

    # ── AND EVERY ADMIN LINK MUST LIVE IN A COLOURED BAND ────────────────
    #
    # The sidebar's colour only works if it covers everything. A link added
    # later and left outside a band renders as a plain grey row in the
    # middle of coloured blocks — it does not break, it just looks like a
    # mistake, and nobody would think to check for it.
    #
    # Scoped to `/admin/` sub-pages on purpose: the "All Projects" exit link
    # and the whole worker menu are deliberately outside the bands, and a
    # rule that has to list its own exceptions is one somebody must remember
    # to extend. The href says which is which.
    from html.parser import HTMLParser

    base = (APP / "templates" / "base.html")
    if not base.is_file():
        return
    markup = base.read_text(encoding="utf-8")
    markup = re.sub(r"\{[%#].*?[%#]\}", " ", markup, flags=re.S)
    markup = re.sub(r"\{\{.*?\}\}", "X", markup, flags=re.S)

    stray: list[str] = []

    class Walk(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.depth: list[bool] = []          # is this ancestor a band?

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            cls = a.get("class", "") or ""
            if tag == "a" and "navlink" in cls:
                href = a.get("href", "")
                if href.startswith("/admin/") and not any(self.depth):
                    stray.append(href)
                return
            if tag in ("div", "nav", "span", "section", "button"):
                banded = ("nav-band" in cls or "nav-group-menu" in cls)
                self.depth.append(banded)

        def handle_endtag(self, tag):
            if tag in ("div", "nav", "span", "section", "button") and self.depth:
                self.depth.pop()

    w = Walk()
    try:
        w.feed(markup)
    except Exception:                     # noqa: BLE001 — a tolerant read
        return
    for href in sorted(set(stray)):
        fail(f"the admin link {href} is not inside a coloured band — it "
             f"would draw as a plain grey row between coloured ones. Put it "
             f"in a <div class=\"nav-band\" data-nav-tint=\"...\"> in "
             f"base.html")


def _js_wrappers(text: str):
    """Each top-level `(function () { … })()` block, as (start_line, body)."""
    import re as _re
    text = _re.sub(r"/\*.*?\*/", " ", text, flags=_re.S)
    # LINE COMMENTS TOO. Leaving them in made a sentence reading "only try
    # every 4th tick (~12s)" look like a call to `tick()`. Prose must not
    # satisfy a check, and it must not TRIGGER one either. The lookbehind
    # keeps `https://` from being mistaken for a comment.
    text = _re.sub(r"(?<![:\w])//[^\n]*", " ", text)
    starts = [m.start() for m in
              _re.finditer(r"^\(function \(\) \{", text, _re.M)]
    bounds = starts + [len(text)]
    return [(text[:b].count("\n") + 1, text[b:bounds[i + 1]])
            for i, b in enumerate(starts)]


def _js_private_names(body: str) -> set:
    """
    Helpers DECLARED inside one wrapper, so reachable only from inside it.

    Only the three shapes this codebase actually uses to declare a helper.
    Being narrow here makes the check quieter, never louder: a declaration
    this misses simply is not considered, and no false alarm can result.
    """
    import re as _re
    # `async ` must be optional. Leaving it out was a hole exactly at the
    # edge of the pattern: `async function load()` was not seen as a
    # declaration, so every file with one was accused of borrowing somebody
    # else's `load`. Enumerate the variants — this codebase writes helpers
    # both ways.
    return (set(_re.findall(r"^\s{2}(?:async\s+)?function\s+"
                            r"([A-Za-z_$][\w$]*)\s*\(", body, _re.M))
            | set(_re.findall(r"^\s{2}(?:const|let)\s+([A-Za-z_$][\w$]*)\s*="
                              r"\s*(?:function|\(|[A-Za-z_$][\w$]*\s*=>)",
                              body, _re.M)))


def check_js_helpers_are_in_scope() -> None:
    """
    A private helper must not be called from outside the wrapper that owns it.

    ════════════════════════════════════════════════════════════════════════
    THE TWO DEFECTS THIS EXISTS FOR, BOTH FOUND ON 2026-09-09
    ════════════════════════════════════════════════════════════════════════
    · `admin_pipeline.js` holds two wrappers. The big one declares a shortcut
      called `q`; the small GPT panel declares `$`. The signature UPLOAD
      button's handler was written in the second and called `q(...)`, which
      does not exist there. The owner pressed UPLOAD and nothing happened.
    · `toast` was declared inside `admin_pipeline.js` while `admin_review_
      images.js` called it too. Those two files never load on the same page,
      so on Approve Artwork the eyedropper's message threw every time — and
      so did the one line that reports a failed pixel read.

    Neither could be seen. Both files PARSE: `q(...)` is perfectly good
    JavaScript that happens to have no `q`. "Buttons have handlers" passed,
    because a handler really was written. And an error thrown inside an
    `async` handler becomes a rejected promise nobody awaits, so the page
    does nothing at all and says nothing at all.

    ════════════════════════════════════════════════════════════════════════
    HOW IT ASKS THE QUESTION, AND WHY IT IS NOT A SCOPE ANALYSER
    ════════════════════════════════════════════════════════════════════════
    The first version tried to list every name each wrapper could see and
    compare that with every call. That needs a real JavaScript lexer:
    template literals nest, regex literals hold quotes, and my hand-rolled
    string-blanker silently ate real code — it reported `jobTone` as
    undefined while `function jobTone` sat forty lines below, because the
    blanker had wiped the declaration. A check that has to be right about
    lexing to be right about anything is the wrong check.

    So this one never asks "what can be seen here". It asks the far narrower
    question that both defects answer YES to: **is this name somebody's
    private helper, being called from outside?** A name only qualifies if it
    is DECLARED inside some wrapper, which means no list of browser globals
    is needed and no CDN library can trip it — `Chart` and `URL` are declared
    nowhere, so they are never candidates.
    """
    import re as _re

    # NAMES THE BROWSER ALSO PROVIDES. One file declaring its own `open` or
    # `close` says nothing about another file calling `window.open`, so a
    # collision on one of these is meaningless and must not be reported.
    also_global = {"open", "close", "load", "find", "focus", "blur", "print",
                   "stop", "scroll", "name", "status", "alert", "confirm"}

    files = sorted((APP / "static" / "js").glob("*.js"))
    owners: dict = {}                      # name -> [(file, wrapper line), ...]
    wrappers: dict = {}                    # file -> [(line, body), ...]

    for js in files:
        wrappers[js.name] = _js_wrappers(js.read_text(encoding="utf-8"))
        for line, body in wrappers[js.name]:
            for name in _js_private_names(body):
                # EVERY owner, not just the first one found. Several files
                # legitimately declare their own `q`, and blaming the
                # alphabetically-first one sends the reader to a file that
                # has nothing to do with it — a correct finding wearing a
                # wrong address is a finding that gets doubted.
                owners.setdefault(name, []).append((js.name, line))

    for js in files:
        for line, body in wrappers[js.name]:
            # PERMISSIVE about what counts as "this wrapper has its own",
            # STRICT about what counts as somebody's private helper. A
            # declaration at any depth — `const run = async () => {` four
            # spaces in — means this wrapper is not borrowing anything, and
            # both directions can only make the check quieter.
            mine = _js_private_names(body) | set(_re.findall(
                r"\b(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", body))
            for name, places in owners.items():
                if name in mine or name in also_global:
                    continue
                if any(f == js.name and ln == line for f, ln in places):
                    continue
                # Prefer an owner in THIS file: that is the one the reader
                # can act on, and it is the commoner mistake by far.
                same = [(f, ln) for f, ln in places if f == js.name]
                owner_file, owner_line = (same or places)[0]
                # A call, not a mention: the name followed by an opening
                # bracket, and not preceded by a dot or another word.
                if not _re.search(r"(?<![.\w$])" + _re.escape(name) + r"\s*\(",
                                  body):
                    continue
                where = (f"another wrapper in the same file (line {owner_line})"
                         if owner_file == js.name
                         else f"{owner_file} (line {owner_line})")
                fail(f"{js.name}: the wrapper at line {line} calls {name}(), "
                     f"but {name} is declared privately in {where}. Nothing "
                     f"can reach it from here, so the control that runs it "
                     f"does nothing at all — the error is swallowed inside "
                     f"an async handler. Move it to its own file and put it "
                     f"on `window`, the way toast.js does.")


# ── A MAGIC WORD MEANING "WE DO NOT KNOW" IS NOT AN ABSENT VALUE ───────────
# Every one of these strings is TRUTHY, so a screen guarding with
# `year ? draw(year) : nothing` draws it. On 2026-09-09 the owner reported
# "(N/A)" beside travel titles that have no year at all. The trail ran back to
# ONE column default — `year = Column(String(16), nullable=False,
# default="N/A")` — plus four other places that typed the same two letters:
# the folder-name builder, which baked `(N/A)` into permanent folder PATHS on
# disk, a dead parser, and the local seed data.
#
# The shape generalises past years. Whenever "unknown" is spelt as a WORD
# rather than as NULL, every emptiness test downstream silently passes, and
# nothing anywhere is broken enough to notice. The honest way to say a value
# is absent is to have no value.
# The list is deliberately SHORT. It holds only strings that are jargon for
# "null" and are never a sentence a person would choose to write. "unknown",
# "-" and "?" are left out on purpose: those are ordinary English and ordinary
# typography, so flagging them would fire on every healthy log line, and a
# check that fires on the normal case is a keystroke rather than a guard.
ABSENT_WORDS = {"N/A", "n/a", "N/a", "NA", "TBD"}

# Where saying one of these words is the POINT rather than a stored value:
# a screen may legitimately print a marketplace's own wording back out, as
# long as nothing writes it into the database.
ABSENT_WORD_EXEMPT_FILES = {
    "app/earnings/faa.py",       # prints FAA's own wording back to the screen
}


def _absent_word_columns() -> None:
    """models.py: a NOT NULL column may not default to a magic word."""
    src = (APP / "models.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Column"):
            continue
        default = nullable = None
        for kw in node.keywords:
            if kw.arg == "default":
                default = kw.value
            elif kw.arg == "nullable":
                nullable = kw.value
        if not isinstance(default, ast.Constant):
            continue
        if not isinstance(default.value, str):
            continue
        if default.value not in ABSENT_WORDS:
            continue
        not_null = isinstance(nullable, ast.Constant) and nullable.value is False
        where = f"models.py line {node.lineno}"
        if not_null:
            fail(f"{where}: a NOT NULL column defaults to {default.value!r}, "
                 f"which is a word meaning 'unknown'. That word is TRUTHY, so "
                 f"every `value ? ... : ...` guard downstream passes and the "
                 f"word gets drawn on the screen. Make the column nullable "
                 f"and let absence be NULL.")
        else:
            fail(f"{where}: column defaults to {default.value!r}. Absence "
                 f"should be NULL, never a word.")


def _absent_word_literals() -> None:
    """Nothing anywhere may hand a magic 'unknown' word to code as a value."""
    for path in py_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in ABSENT_WORD_EXEMPT_FILES or rel.startswith("tools/"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue                      # check_python_compiles reports this
        # A fallback used INSIDE a message is a different thing: it is text
        # for a person to read, written at the display end, which is exactly
        # where an absence is supposed to be turned into words. Only a value
        # travelling onward as data is the defect.
        in_message = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for sub in ast.walk(node):
                    in_message.add(id(sub))

        for node in ast.walk(tree):
            if id(node) in in_message:
                continue
            # `x or "N/A"` — the exact shape that put (N/A) into folder paths.
            if (isinstance(node, ast.BoolOp)
                    and isinstance(node.op, ast.Or)
                    and isinstance(node.values[-1], ast.Constant)
                    and node.values[-1].value in ABSENT_WORDS):
                fail(f"{rel} line {node.lineno}: falls back to "
                     f"{node.values[-1].value!r} when a value is missing. "
                     f"Pass nothing instead, and let the screen decide how to "
                     f"show an absence.")
            # `year="N/A"` handed to anything at all.
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (isinstance(kw.value, ast.Constant)
                            and kw.value.value in ABSENT_WORDS):
                        fail(f"{rel} line {node.lineno}: passes "
                             f"{kw.arg}={kw.value.value!r}. That is a word "
                             f"meaning 'unknown'; use None.")


def check_no_magic_absent_value() -> None:
    _absent_word_columns()
    _absent_word_literals()


# ── THE RESET MUST HAVE AN OPINION ON EVERY TABLE ──────────────────────────
# `scripts/reset_workflow.py` wipes the work and keeps the configuration.
# Five tables added after it was written (earnings rows, sweeps, snapshots,
# aliases, the search cache) were in NEITHER list, so a "reset to zero"
# would have opened with the test shop's money still on the Earnings tab
# (found 2026-09-09). The defect is general: a wipe script and a schema
# grow independently, and nothing asks the new table which side it is on.
# So: every model class in models.py must appear in reset_workflow.py —
# either wiped in its `tables` list or named in KEPT_ON_PURPOSE with a
# reason. A table in neither fails the deploy and forces the decision.

def check_reset_covers_every_table() -> None:
    models_src = (APP / "models.py").read_text(encoding="utf-8")
    model_names = {n.name for n in ast.parse(models_src).body
                   if isinstance(n, ast.ClassDef)
                   and any(getattr(b, "id", "") == "Base" for b in n.bases)}

    reset = ROOT / "scripts" / "reset_workflow.py"
    if not reset.is_file():
        fail("scripts/reset_workflow.py is missing — the reset path is gone.")
        return
    src = reset.read_text(encoding="utf-8")
    tree = ast.parse(src)

    kept, wiped = set(), set()
    for node in ast.walk(tree):
        # KEPT_ON_PURPOSE = ("User", ...)
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "KEPT_ON_PURPOSE" for t in node.targets)):
            kept = {e.value for e in ast.walk(node.value)
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        # tables = [("label", Model), ...]  — take every bare Name in it
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "tables" for t in node.targets)):
            wiped |= {e.id for e in ast.walk(node.value) if isinstance(e, ast.Name)}
    # MasterTitle is wiped/reset by its own dedicated block, not the list.
    wiped.add("MasterTitle")

    if not kept:
        fail("reset_workflow.py has no KEPT_ON_PURPOSE list — the check "
             "cannot tell a kept table from a forgotten one.")
        return
    for name in sorted(model_names - kept - wiped):
        fail(f"models.py defines {name} but reset_workflow.py neither wipes "
             f"it nor names it in KEPT_ON_PURPOSE. Decide which side of the "
             f"reset it is on — a table in neither list survives every "
             f"'reset to zero' unnoticed.")
    for name in sorted((kept | wiped) - model_names):
        if name in ("MasterTitle",): continue
        warn(f"reset_workflow.py mentions {name}, which is not a model in "
             f"models.py — probably a rename it missed.")


# ── A SETTING NOBODY READS IS A CONTROL THAT PROTECTS NOTHING ──────────────
# `brave_daily_query_cap` had a box describing it as a safety net, and no
# code consulted it. `allowed_download_hosts` was declared beside the real
# `allowed_image_hosts` and read by nothing (found 2026-09-09). Both looked
# exactly like protection. A key is DEAD if its name appears nowhere in the
# repo outside its own DEFAULTS line — no reader, no box, no help text.
# (`check_settings_are_reachable` asks the opposite question: a key that is
# read but cannot be edited. Both directions are one grep each.)

def check_every_default_is_read() -> None:
    src = (APP / "pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys: list[str] = []
    for node in tree.body:
        val = None
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "DEFAULTS":
            val = node.value
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEFAULTS" for t in node.targets):
            val = node.value
        if isinstance(val, ast.Dict):
            keys = [k.value for k in val.keys if isinstance(k, ast.Constant)]
    if not keys:
        fail("could not find the DEFAULTS dict in app/pipeline.py")
        return

    blob = ""
    for base in (APP, NODE, ROOT / "scripts"):
        if not base.is_dir(): continue
        for q in base.rglob("*.py"):
            if "__pycache__" in str(q): continue
            blob += q.read_text(encoding="utf-8", errors="replace")
    for q in list(JS.glob("*.js")) + list(TPL.glob("*.html")):
        blob += q.read_text(encoding="utf-8", errors="replace")

    for k in keys:
        # its DEFAULTS line is one occurrence; anything else is a reader,
        # a box, or per-project override plumbing — all count as "alive".
        if blob.count(f'"{k}"') + blob.count(f"'{k}'") <= 1:
            fail(f"the setting {k!r} is declared in DEFAULTS and referenced "
                 f"by NOTHING else in the repo — no code reads it and no "
                 f"screen offers it. A control nobody consults reads as "
                 f"protection and is not. Wire it up or delete it.")


CHECKS = [
    ("python compiles",           check_python_compiles),
    ("no undefined names",        check_undefined_names),
    ("settings keys declared",    check_settings_keys_declared),
    ("settings reachable on the dashboard", check_settings_are_reachable),
    ("activity log calls valid",  check_activity_log_calls),
    ("cross-module calls exist",  check_module_attributes),
    ("calls pass the right arguments", check_call_arity),
    ("javascript parses",         check_js_parses),
    ("template tags balance",     check_template_tags_balance),
    ("page hooks exist",          check_hooks_exist),
    ("buttons have handlers",     check_actions_are_handled),
    ("endpoints have buttons",    check_endpoints_have_buttons),
    ("nothing stuck behind hidden", check_hidden_ancestors),
    ("tool calls exist in the app", check_tool_calls_exist),
    ("pages get what the layout needs", check_page_context),
    ("no queries inside loops",   check_queries_in_loops),
    ("zero is not treated as missing", check_falsy_zero_defaults),
    ("guards are called on every path", check_guards_are_called),
    ("state changes are logged", check_state_changes_are_logged),
    ("every document is linked", check_no_orphan_documents),
    ("local imports come before use", check_local_imports_not_used_earlier),
    ("the top bar carries no filter", check_no_filter_on_fixed_element_ancestors),
    ("no picture paints over its colour plate", check_no_background_on_composited_img),
    ("every colour name has a rule", check_colour_names_have_rules),
    ("click targets do not swallow controls",
     check_click_targets_do_not_swallow_controls),
    ("overlays sit in the box they are measured against",
     check_overlay_sits_in_its_measuring_layer),
    ("javascript helpers are in scope", check_js_helpers_are_in_scope),
    ("absence is NULL, never a magic word", check_no_magic_absent_value),
    ("the reset has an opinion on every table", check_reset_covers_every_table),
    ("every setting is read by something", check_every_default_is_read),
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
