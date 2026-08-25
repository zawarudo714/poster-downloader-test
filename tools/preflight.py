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
                    # What it SENDS. The object literal after the address —
                    # `{}` is recorded as "{}" rather than blank, because
                    # "sends nothing" is the interesting answer, not a
                    # missing one.
                    sent = re.search(
                        re.escape(call.group(0)) + r"""[^,]*,\s*\{([^{}]*)\}""",
                        body)
                    if sent:
                        keys = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:",
                                          sent.group(1))
                        endpoint += "  {" + ", ".join(keys) + "}"
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


def check_store_logic() -> None:
    """
    The only BEHAVIOUR check in here, and it earns its place.

    Everything else above proves the wiring is connected. This runs the two
    decisions that stop a stage switching live listings — the stop signal and
    the skip-what-already-failed rule — against worked examples, using the
    shipped source rather than a copy. See tools/test_store_logic.py, and run
    it with --sabotage to confirm its checks can still go red.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import test_store_logic as T
        for label, ok in T.run_suite(T.SOURCE.read_text(encoding="utf-8")):
            if not ok:
                fail(f"listing-health rule broken: {label}")
    except SystemExit as e:                 # the functions were renamed away
        fail(f"store logic test could not run: {e}")
    finally:
        sys.path.pop(0)


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
    ("endpoints have buttons",    check_endpoints_have_buttons),
    ("nothing stuck behind hidden", check_hidden_ancestors),
    ("no queries inside loops",   check_queries_in_loops),
    ("zero is not treated as missing", check_falsy_zero_defaults),
    ("listing-health rules behave", check_store_logic),
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
