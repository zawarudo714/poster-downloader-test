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
    for path in (APP / "pipeline.py", APP / "listing_check.py",
                 APP / "earnings" / "store_health.py"):
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


# ── A GUARD IS ONLY A GUARD IF EVERY PATH CALLS IT ──────────────────────
#
# (file, what the risky thing looks like, what protects it, plain words).
#
# Add a row whenever a protective call has to accompany a risky one. The
# point is not the individual rule — it is that "somebody remembered" stops
# being the mechanism.
GUARDED: list[tuple[str, str, tuple[str, ...], str]] = [
    ("worker_service/store_health.py", "driver.get(",
     ("clear_wall(", "_open_page(", "page_is_theirs("),
     "navigates the browser without consulting the interstitial wall"),
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
    ("tool calls exist in the app", check_tool_calls_exist),
    ("pages get what the layout needs", check_page_context),
    ("no queries inside loops",   check_queries_in_loops),
    ("zero is not treated as missing", check_falsy_zero_defaults),
    ("listing-health rules behave", check_store_logic),
    ("guards are called on every path", check_guards_are_called),
    ("state changes are logged", check_state_changes_are_logged),
    ("every document is linked", check_no_orphan_documents),
    ("local imports come before use", check_local_imports_not_used_earlier),
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
