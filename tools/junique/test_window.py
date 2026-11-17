"""Window check: buttons grey out while a job runs, and Chrome reopens after
the owner closed it. Opens a real Chrome with a throw-away profile, never the
owner's. Run from the repo root:  python tools/junique/test_window.py"""
import sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import psutil
import tkinter as tk
import junique_upload as J
import ledger as L

J.PROFILE = Path(tempfile.mkdtemp(prefix="jq_profile_"))
J.RUN_LOG = Path(tempfile.mkdtemp(prefix="jq_log_")) / "run_log.txt"
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m) or (c or (_ for _ in ()).throw(SystemExit(1)))

# The tests must not flash windows on the owner's screen (he said so on
# 2026-11-10): Chrome runs headless and the test window sits off screen.
_options = J.webdriver.ChromeOptions
def _quiet_options():
    o = _options()
    o.add_argument("--headless=new")
    return o
J.webdriver.ChromeOptions = _quiet_options
# The run reads the processor before each design; the test computer may be
# busy, so the run sees a calm reading (5e4 checks the real one).
real_busy_cpu, cpu_reads = J.busy_cpu, []
J.busy_cpu = lambda secs=3.0: (cpu_reads.append(secs), (10.0, "test"))[1]

root = tk.Tk()
root.geometry("+-4000+-4000")
app = J.App(root)
root.geometry("+-4000+-4000")
pump = time.sleep                      # the window runs in mainloop, as in real use
result = []

def steps():

    # 1. A slow fetch greys out the buttons and shows the busy line at once.
    release = threading.Event()
    L.fetch = lambda n, emit=print: (emit("Asking the server…"), release.wait(10))
    root.after(0, app.fetch)
    pump(0.6)
    states = [b.cget("state") for b in (app.b_fetch, app.b_chrome, app.b_upload)]
    ok(states == ["disabled"] * 3, f"buttons are greyed out while fetching ({states})")
    ok("Fetching" in app.busy.cget("text") and "Asking the server" in app.busy.cget("text"),
       f"the busy line says what runs ({app.busy.cget('text')!r})")
    root.after(0, app.fetch)                                    # a second click does nothing
    release.set()
    pump(1.0)
    ok(app.b_fetch.cget("state") == "normal" and app.busy.cget("text") == "Ready.",
       "buttons come back when the fetch ends")
    ok(app.out.get("1.0", "end").count("── Fetching") == 1, "the double click started one fetch only")

    # 2. Chrome closed by hand is opened again.
    d1 = app.chrome()
    ok(app.alive(), "Chrome opened")
    pid = d1.service.process.pid
    kids = [p for p in psutil.Process(pid).children(recursive=True) if p.name().lower() == "chrome.exe"]
    for p in kids:
        p.kill()                                   # like closing the window
    time.sleep(2)
    ok(not app.alive(), "a closed Chrome is noticed")
    d2 = app.chrome()
    ok(d2 is not d1 and app.alive(), "Chrome opens again by itself")
    d2.get("about:blank")
    ok(d2.current_url == "about:blank", "the new Chrome works")
    d2.quit()

    # 3. A failed design stops the run: every new design uses a Junique slot.
    calls, failed = [], []
    class FailingJunique:
        def __init__(self, *a):
            self.d, self.busy = Fake(), None
        def one(self, d, auto, wait_minutes):
            calls.append(d["saved_poster_id"])
            raise J.StepError("no on/off switch for the product")
        def design_id(self):
            return "999"
    class Fake:
        def save_screenshot(self, p): pass
    real = J.Junique, app.chrome, app.alive, L.batch_todo, L.load, L.mark_failed
    J.Junique, app.chrome, app.alive = FailingJunique, (lambda: Fake()), (lambda: True)
    L.load = lambda: {"designs": {}, "batch": []}
    L.batch_todo = lambda led: [{"saved_poster_id": i, "title": f"T{i}"} for i in (1, 2, 3)]
    L.mark_failed = lambda spid, err, jid=None: failed.append((spid, jid))
    J.SHOTS = Path(tempfile.mkdtemp(prefix="jq_shots_"))
    root.after(0, app.upload)
    pump(1.5)
    J.Junique, app.chrome, app.alive, L.batch_todo, L.load, L.mark_failed = real
    ok(calls == [1], f"the run stops after the first failed design ({calls})")
    ok(len(cpu_reads) == 1, f"the run read the processor before the design ({len(cpu_reads)} reads)")
    ok(failed == [(1, "999")], f"the failure and its draft are recorded ({failed})")
    ok("no more Junique slots are used" in app.out.get("1.0", "end"), "the log says why it stopped")

    # 4. The tool waits a little for the picture, and goes on if it is slow.
    class Page:
        done = False
        def get_log(self, kind): return []
        def execute_script(self, js, *a): return self.done
    class Net:
        seen = []
        def since(self, mark): return self.seen
    page, stop = Page(), threading.Event()
    jq = J.Junique(page, lambda t: None, stop, 0.01)
    jq.net = Net()
    pic = ("GET", "https://jq-prod-de-image.s3-eu-central-1.amazonaws.com/2026/11/05/x", 200)
    for seen, done, want in (([], False, False), ([pic], False, False), ([pic], True, True)):
        jq.net.seen, page.done = seen, done
        got = jq.wait_placed(0, timeout=1)        # never raises: the owner says clicks can go on
        ok(got == want, f"picture asked={bool(seen)} downloaded={done}: seen on the design={got}")

    # 5. The "+" of the Front side is found (page shaped like Junique's, read 2026-11-05).
    d3 = J.start_chrome()
    page = lambda label, extra="": (
        '<div class="ca-preview-product-pages__product-page"><div><div class="product-page__page-label">'
        + label + '</div></div><div class="ca-change-canvas"><div class="ca-change-canvas__add-canvas" id="'
        + label + extra + '">+</div></div></div>')
    d3.get("data:text/html," + "<div>" + page("Front") + page("Back") + "</div>")
    plus = d3.execute_script(J.Junique.PLUS)
    ok(plus is not None and plus.get_attribute("id") == "Front",
       "the '+' of the Front side is found, not the Back one")
    d3.execute_script("document.getElementById('Front').remove()")
    ok(d3.execute_script(J.Junique.PLUS) is None, "no Front '+' means none is clicked")

    # 5a. The owner's on/off list (2026-11-05): the bib stays off, other baby clothes on.
    got = {n: J.wanted(n) for n in ("Baby Bib", "Baby T-Shirt", "Short-Sleeve Baby Romper", "Sticker Sheet")}
    ok(got == {"Baby Bib": False, "Baby T-Shirt": True, "Short-Sleeve Baby Romper": True,
               "Sticker Sheet": False}, f"the on/off list ({got})")

    # 5b. Only a poster pushed above the edge is dragged; frames and product
    # photos are not (the four previews measured on draft 705231, 2026-11-05).
    layouts = {"Shower Curtain": (375, 417, [[0, -146, 375, 563], [-8, -8, 391, 433]], 1),
               "Notebook": (276.05, 382.82, [[0, -2, 276, 414], [-24, -14, 314, 412]], 0),
               "T-Shirt": (141.8, 189.1, [[8, 0, 126, 189], [-101, -90, 344, 412]], 0),
               "Poster": (375, 417, [[0, 0, 375, 417]], 0)}
    d3.get("data:text/html,<div class='ca-preview-canvas'><div id='c'>x</div></div>")
    for name, (W, H, rects, want) in layouts.items():
        d3.execute_script("""const [W, H, rects] = arguments, c = document.getElementById('c');
            window.Konva = {stages: [{container: () => c, width: () => W, height: () => H,
                find: () => rects.map(([x, y, width, height]) => ({getClientRect: () => ({x, y, width, height})}))}]};""",
                          W, H, rects)
        got = len(d3.execute_script(J.Junique.READ_POSTER)["posters"])
        ok(got == want, f"{name}: {got} picture(s) to drag down (want {want})")

    # 5c. A card with no artboard is read at once, and the last check names a
    # product left in the wrong state (product list shaped like draft 705231's).
    def product(name, state):              # state: "on", "off", or "none" (no artboard)
        box = "" if state == "none" else f'<input type="checkbox" id="id-{len(name)}"{" checked" if state == "on" else ""}>'
        cls = "ca-customization-product" + (" ca-customization-product--disabled" if state == "none" else "")
        return f'<div><div>{name}</div><div class="{cls}">{name}{box}</div></div>'
    def shop(**states):
        d3.get("data:text/html,<div>" + "".join(product(n.replace("_", " "), s) for n, s in states.items()) + "</div>")
    jq3 = J.Junique(d3, lambda t: None, threading.Event(), 0.01)
    shop(Poster="on", Sticker_Sheet="none", Notebook="none")
    t0 = time.time()
    none = jq3.toggle(2, timeout=4, none_ok=True)
    ok(none is None and time.time() - t0 < 1, f"no artboard is seen at once ({time.time() - t0:.1f} s, not 4 s)")
    names = jq3.product_names()
    def raised(states):
        shop(**states)
        try:
            jq3.check_switches(jq3.product_names())
        except J.StepError as e:
            return str(e)
        return ""
    ok(names == ["Poster", "Sticker Sheet", "Notebook"] and
       raised(dict(Poster="on", Sticker_Sheet="none", Notebook="on")) == "", "the right states pass the last check")
    msg = raised(dict(Poster="on", Sticker_Sheet="on", Notebook="on"))
    ok("Sticker Sheet is on" in msg, f"an off product switched on is caught ({msg})")
    msg = raised(dict(Poster="on", Sticker_Sheet="off", Notebook="none"))
    ok("Notebook is off" in msg and "Sticker" not in msg, f"an on product left without artboard is caught ({msg})")

    # 5d. The artboard box: a pick closes it; a tile already chosen does not,
    # so "Change" closes it (as on draft 705270, 2026-11-05).
    def box(active):
        d3.get("data:text/html,<div id='pop'><div class='ca-preview-page-thumb-tooltip__headline'>"
               "Choose an artboard for this product</div><div id='tile' class='ca-preview-page-thumb-tooltip__page-thumb"
               + (" ca-preview-page-thumb--active" if active else "") + "'>%231 (2:3)</div></div>"
               "<div class='ca-change-canvas__text'>%231 (2:3)</div><div class='ca-change-canvas__change'>Change</div>"
               "<script>window.changes = 0; const pop = document.getElementById('pop');"
               "document.getElementById('tile').onclick = e => { if (!e.target.className.includes('--active')) pop.remove(); };"
               "document.querySelector('.ca-change-canvas__change').onclick = () => { window.changes++; pop.remove(); };</script>")
        jq3.choose_artboard("Notebook")
        return d3.execute_script("return [!!document.getElementById('pop'), window.changes]")
    ok(box(False) == [False, 0], "a new pick closes the box by itself")
    ok(box(True) == [False, 1], "an already chosen artboard: 'Change' closes the box")

    # 5e. A save that kept nothing is named as such, not as a missing Rename button.
    d3.get("data:text/html,<h2>Design</h2><div>Start with your design</div>")
    try:
        jq3.details("T", first=0.5, after_reload=0.5); msg = ""
    except J.StepError as e:
        msg = str(e)
    ok("saved design is empty" in msg and "even after a reload" in msg, f"an empty saved design is named ({msg})")
    d3.quit()

    # 5e2. A design page stuck on "Start with your design" that shows the
    # design after a reload is used (London, draft 706551, 2026-11-06).
    class Btn:
        tag_name = "button"
        def is_displayed(self): return True
    class StaleView:
        reloaded, clicked = 0, []
        def refresh(self): self.reloaded += 1
        def find_elements(self, by, xp):
            return [Btn()] if self.reloaded and "Rename" in xp else []
        def execute_script(self, js, *a): self.clicked.append(a)
    view, logged = StaleView(), []
    jq7 = J.Junique(view, logged.append, threading.Event(), 0.01)
    try:
        jq7.details("T", first=0.3, after_reload=0.3); msg = ""
    except J.StepError as e:
        msg = str(e)
    ok(view.reloaded == 1 and view.clicked and "Name box" in msg,
       f"a stale empty view is reloaded and Rename is clicked ({view.reloaded} reload, then: {msg})")

    # 5e4. A design waits while the computer is too busy, and says so
    # (Mumbai, 2026-11-10: another project's tests held the processor at 100%).
    lines, seen = [], iter([(97, "node.exe"), (96, "node.exe"), (30, "x")])
    calls = []
    def measure():
        calls.append(1)
        return next(seen)
    J.wait_until_quiet(lines.append, threading.Event(), measure, pause=0)
    ok(len(calls) == 3 and len(lines) == 2 and "97%" in lines[0] and "node.exe" in lines[0]
       and "calmer" in lines[1], f"a busy computer is waited out and named ({len(calls)} reads, {lines})")
    lines.clear()
    J.wait_until_quiet(lines.append, threading.Event(), lambda: (20, "x"), pause=0)
    ok(lines == [], "a quiet computer is not mentioned")
    total, top = real_busy_cpu(0.5)
    ok(0 <= total <= 100 and isinstance(top, str), f"the real processor reading works ({total:.0f}%, {top})")

    # 5e3. Every window line also lands in the run log with its time
    # (Philadelphia, draft 707197, 2026-11-07: only the window held the story).
    app.log("log line for the file 707197")
    text = J.RUN_LOG.read_text(encoding="utf-8") if J.RUN_LOG.exists() else ""
    ok("log line for the file 707197" in text and text[:2] == "20", f"window lines are kept in {J.RUN_LOG.name}")

    # 5f. A "Save anyway" button that vanishes mid-check does not stop the run
    # (Cortina, draft 706464, 2026-11-06: the save had gone through).
    from selenium.common.exceptions import StaleElementReferenceException
    class Gone:
        def is_displayed(self): raise StaleElementReferenceException("stale element reference")
    class SavePage:
        looks = 0
        @property
        def current_url(self):
            return "https://www.juniqe.com/creator-suite/designs/1" if self.looks > 2 else "https://x/editor"
        def find_elements(self, by, xp):
            self.looks += 1
            return [Gone()]
    jq5 = J.Junique(SavePage(), lambda t: None, threading.Event(), 0.01)
    jq5.click_text = lambda *a: None
    try:
        jq5.save_and_exit(settle=0, every=1); msg = ""
    except Exception as e:          # noqa: BLE001
        msg = type(e).__name__
    ok(msg == "", f"a vanished 'Save anyway' button is skipped ({msg or 'no error'})")

    # 5f2. "Save anyway" is clicked once while the save runs, not every 0.1 s
    # (Philadelphia, draft 707197, 2026-11-07: LEAD, repeated clicks).
    class Btn:
        def is_displayed(self): return True
    class SavingPage:
        def __init__(self): self.t0, self.clicks = time.time(), 0
        current_url = property(lambda s: "https://www.juniqe.com/creator-suite/designs/3"
                               if time.time() - s.t0 > 1.5 else "https://x/editor")
        def find_elements(self, by, xp): return [Btn()] if "Save anyway" in xp else []
        def execute_script(self, js, el): self.clicks += 1
    sp = SavingPage()
    jq9 = J.Junique(sp, lambda t: None, threading.Event(), 0.01)
    jq9.click_text = lambda *a: None
    jq9.save_and_exit(settle=0, every=5)
    ok(sp.clicks == 1, f"'Save anyway' is clicked once while Junique saves ({sp.clicks} clicks)")
    class FakeNet:
        def mark(self): return 0
        def since(self, m): return [("PUT", "https://www.juniqe.com/x/shop-proxy/collage-designer-design/3?safe=false", 200),
                                    ("GET", "https://www.juniqe.com/creator-suite/designs/3", 200)]
    jq9.net = FakeNet()
    said = jq9.save_requests(0)
    ok(said == "1 save request(s), answered 200", f"the save requests are reported ({said})")

    # 5g. An editor that ignores Save & exit until reloaded (Hong Kong, draft
    # 706496, 2026-11-06): the tool keeps clicking, reloads once halfway,
    # checks the switches, saves. 5i. One that ignores the first clicks and
    # then answers (Los Angeles, draft 707154, 2026-11-07): no reload needed.
    class Frozen:
        def __init__(self, heals_after_reload, ignore):
            self.heals, self.ignore, self.reloaded, self.saved = heals_after_reload, ignore, 0, False
        current_url = property(lambda s: "https://www.juniqe.com/creator-suite/designs/2" if s.saved else "https://x/editor")
        def find_elements(self, by, xp): return []
        def refresh(self): self.reloaded += 1
    def frozen_run(heals, ignore=10**6):
        page, seen = Frozen(heals, ignore), []
        jq6 = J.Junique(page, seen.append, threading.Event(), 0.01)
        clicks = []
        def click(*a):
            clicks.append(round(time.time() - t0, 2))
            page.saved = (page.heals and page.reloaded > 0) or len(clicks) > page.ignore
        jq6.click_text = click
        jq6.wait_names = lambda: ["Poster"]
        jq6.check_switches = lambda names: seen.append(f"checked {names}")
        t0 = time.time()
        try:
            jq6.save_and_exit(settle=0.3, every=0.2, total=1.5, reload_at=0.5, settle_reload=0.4); msg = ""
        except J.StepError as e:
            msg = str(e)
        return page.reloaded, msg, seen, clicks
    reloaded, msg, seen, clicks = frozen_run(True)
    ok(reloaded == 1 and msg == "" and "checked ['Poster']" in seen,
       f"a frozen editor is reloaded, checked and saved ({reloaded} reload, {msg or 'saved'})")
    ok(clicks[0] >= 0.3 and clicks[-1] - clicks[-2] >= 0.2 + 0.4,
       f"Save & exit waits for the editor first, and again after the reload (clicks at {clicks} s)")
    reloaded, msg, seen, clicks = frozen_run(False)
    ok(reloaded == 1 and "never saved" in msg and len(clicks) >= 4,
       f"one reload only, clicks until the time is up, then fails ({reloaded}, {len(clicks)} clicks, {msg})")
    ok(any("did nothing" in line and "requests not readable" in line for line in seen),
       "each click that did nothing is logged with what Junique sent")
    reloaded, msg, seen, clicks = frozen_run(False, ignore=2)
    ok(reloaded == 0 and msg == "" and len(clicks) == 3,
       f"an editor that ignores the first 2 clicks is clicked again, not reloaded ({len(clicks)} clicks, "
       f"{reloaded} reloads, {msg or 'saved'})")

    # 5h. "Publish by itself" waits for Publish to lose `disabled` and clicks
    # again if Junique does not confirm (Singapore, draft 707145, 2026-11-07).
    class PubBtn:
        def __init__(self, page): self.page = page
        def is_displayed(self): return True
        def get_dom_attribute(self, name):
            return "" if time.time() < self.page.t0 + self.page.grey else None
    class PubPage:
        def __init__(self, grey, confirm_on):
            self.t0, self.grey, self.confirm_on, self.clicks, self.tries = time.time(), grey, confirm_on, [], 0
        def find_elements(self, by, xp): return [PubBtn(self)]
        def execute_script(self, js, el):
            self.tries += 1
            if el.get_dom_attribute("disabled") is None:      # a disabled button ignores clicks
                self.clicks.append(round(time.time() - self.t0, 1))
    class PubNet:
        def __init__(self, page): self.page = page
        def mark(self): return 0
        def since(self, m):
            ok_ = len(self.page.clicks) >= self.page.confirm_on
            return [("PATCH", "https://x/designs/9/status", 204)] if ok_ else []
    def pub_run(grey, confirm_on):
        page = PubPage(grey, confirm_on)
        jq8 = J.Junique(page, lambda t: None, threading.Event(), 0.01)
        jq8.net = PubNet(page)
        try:
            jq8.publish("9", True, 1, enable_wait=2, confirm_wait=0.3); msg = ""
        except J.StepError as e:
            msg = str(e)
        return page.clicks, msg, page.tries
    clicks, msg, tries = pub_run(0.5, 1)
    ok(msg == "" and tries == 1 and clicks[0] >= 0.5, f"Publish is clicked once, when no longer greyed out ({tries} click(s) at {clicks}, {msg or 'published'})")
    clicks, msg, _ = pub_run(0, 2)
    ok(msg == "" and len(clicks) == 2, f"an unconfirmed Publish is clicked again ({clicks}, {msg or 'published'})")
    clicks, msg, _ = pub_run(0, 9)
    ok(len(clicks) == 3 and "3 times" in msg, f"three unconfirmed clicks fail the design ({clicks}, {msg})")

    # 6. When the products step fails, the draft is handed to the owner, not failed.
    published = []
    class Steps(J.Junique):
        def __init__(self): self.log, self.busy, self.stop = (lambda t: None), (lambda t: None), threading.Event()
        def new_design(self): pass
        def design_id(self): return "777"
        def upload(self, path): pass
        def products(self): raise J.StepError("Poster: no artboard, and no '+' beside 'Front' to add one")
        def publish(self, jid, auto, wait): published.append(jid)
        def save_and_exit(self): raise AssertionError("should not save")
    here = Path(__file__)
    s = Steps()
    ok(s.one({"file": str(here), "title": "T"}, False, 1) == "777" and published == ["777"],
       "a products failure waits for the owner's Publish in the same draft")
    try:
        s.one({"file": str(here), "title": "T"}, True, 1)
        raised = False
    except J.StepError:
        raised = True
    ok(raised and published == ["777"], "with 'Publish by itself' ticked it still fails (nobody watches)")

    # 6b. A draft Junique kept nothing of fails and says what Junique
    # answered the save; it is not done again in the same draft, since that
    # editor opens on an empty 3:4 board (Mumbai, 709832, 2026-11-10).
    calls = []
    class Empty(J.Junique):
        def __init__(self):
            self.log, self.busy, self.stop = calls.append, (lambda t: None), threading.Event()
            self.last_save = "1 save request(s), answered 202"
        def new_design(self): calls.append("new")
        def design_id(self): return "888"
        def upload(self, path): calls.append("upload")
        def products(self): calls.append("products")
        def save_and_exit(self): calls.append("save")
        def details(self, title): raise J.EmptyDesign("the saved design is empty on Junique")
        def publish(self, jid, auto, wait): calls.append(f"publish {jid}")
    try:
        Empty().one({"file": str(here), "title": "T"}, True, 1); msg = ""
    except J.EmptyDesign as e:
        msg = str(e)
    ok(calls.count("upload") == 1 and "answered 202" in msg and "publish 888" not in calls,
       f"an empty draft fails once, saying what Junique answered ({calls.count('upload')} upload, {msg})")

    result.append("ok")

def run():
    try:
        steps()
    finally:
        root.after(0, root.destroy)

threading.Thread(target=run, daemon=True).start()
root.mainloop()
print("ALL PASS" if result else "NOT FINISHED")
