"""
Junique upload window: puts the fetched designs on Junique by clicking, the
way the owner does it by hand.

WHY clicking: the owner asked for clicking automation like his Teepublic
tool, not API requests (DECIDED 2026-11-05). It drives a visible Chrome with
its own profile in tools/junique/local/chrome_profile; the owner signs in to
Junique there once, by hand. The tool never types a password.

    python tools/junique/junique_upload.py

Every step below was MEASURED from the owner's recording of 5 uploads and
his screenshots on 2026-11-05 (docs/facts/JUNIQUE.md). Where a page differs
from that, the tool stops on that design, saves a screenshot in
tools/junique/local/screenshots, records the failure in the ledger, and goes
on with the next one.

By default it stops before Publish: it fills everything, then waits for the
owner to click Publish himself. "Publish by itself" clicks it. Either way a
design is marked uploaded only when Junique answers the publish request
(PATCH /designs/<id>/status, 204).
"""
from __future__ import annotations

import json
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext, ttk

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

import psutil

import ledger as L

DESIGNS_URL = "https://www.juniqe.com/creator-suite/designs"
PROFILE = L.LOCAL / "chrome_profile"
SHOTS = L.LOCAL / "screenshots"
RUN_LOG = L.LOCAL / "run_log.txt"          # every window line, with the time
ARTBOARD = "#1 (2:3)"
# How often a check looks at the page again. Junique answers a click at
# once (the owner, 2026-11-05), so waiting is for the page, never a fixed time.
POLL = 0.1
# Above this share of the processor the editor misses clicks and Junique
# answered a save 202 and kept nothing (Mumbai, 2026-11-10: another project's
# tests held the processor at 100% and every step took 3-4 times as long).
BUSY_CPU = 85

# Which products stay on. Checked in order, first match wins; a product
# matching nothing stops the design, so a new Junique product is never
# switched on or off by guess. (DECIDED by the owner 2026-11-05.)
PRODUCT_RULES = [
    ("sticker sheet", False), ("phone case", False), ("doormat", False),
    ("beach towel (16:9)", False), ("fleece blanket (4:3)", False), ("cushion (3:2)", False),
    ("baby bib", False),              # the owner, 2026-11-05: the bib stays off
    ("tea towel", True), ("beach towel (5:4)", True),
    ("towel", False),
    ("poster", True), ("notebook", True), ("sticker", True),
    ("t-shirt", True), ("hoodie", True), ("sweatshirt", True), ("romper", True),
    ("baby", True), ("kids", True), ("tote bag", True),
    ("ceramic mug", True), ("enamel mug", True), ("shower curtain", True),
    ("fleece blanket (1:1)", True), ("cushion (1:1)", True),
    ("backlit film", True), ("pvc banner", True), ("wallpaper", True),
]


def wanted(name: str):
    n = name.lower().replace("’", "'")
    for pattern, on in PRODUCT_RULES:
        if pattern in n:
            return on
    return None


class StepError(Exception):
    pass


class EmptyDesign(StepError):
    """Junique kept nothing from the editor: the draft can be done again."""


# ── network: the only reliable "it worked" signals ─────────────────────────
class Net:
    """Reads Chrome's performance log for the requests that mean success."""

    def __init__(self, driver):
        self.d = driver
        self.methods: dict[str, tuple[str, str]] = {}
        self.seen: list[tuple[str, str, int]] = []   # (method, url, status)

    def drain(self):
        for entry in self.d.get_log("performance"):
            m = json.loads(entry["message"])["message"]
            p = m.get("params", {})
            if m.get("method") == "Network.requestWillBeSent":
                r = p.get("request", {})
                self.methods[p.get("requestId")] = (r.get("method", ""), r.get("url", ""))
            elif m.get("method") == "Network.responseReceived":
                method, _ = self.methods.get(p.get("requestId"), ("", ""))
                resp = p.get("response", {})
                self.seen.append((method, resp.get("url", ""), int(resp.get("status", 0))))

    def mark(self) -> int:
        self.drain()
        return len(self.seen)

    def since(self, mark: int):
        self.drain()
        return self.seen[mark:]


def short(e: Exception) -> str:
    """Selenium errors carry a long stack dump; the first line says it all."""
    text = str(e).strip()
    return text.split("\n")[0].removeprefix("Message: ")[:300] or type(e).__name__


def busy_cpu(secs=3.0):
    """The processor's use over a few seconds, and the program using most."""
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    total = psutil.cpu_percent(interval=secs)
    use = []
    for p in procs:
        try:
            if p.pid:                          # 0 is the idle process
                use.append((p.cpu_percent(None), p.info["name"]))
        except psutil.Error:
            pass
    return total, max(use, default=(0, "?"))[1]


def wait_until_quiet(log, stop, measure=None, pause=27):
    """Wait before a design while the computer is too busy for the editor."""
    measure, said = measure or busy_cpu, False
    while not stop.is_set():
        total, top = measure()
        if total < BUSY_CPU:
            if said:
                log(f"  the computer is calmer now ({total:.0f}%); carrying on")
            return
        if not said:
            log(f"  the computer is busy: the processor is at {total:.0f}% (most of it {top}). "
                "Junique misses clicks and loses saves when the computer is this slow, "
                "so the uploader waits until it calms down. Close heavy programs to go on sooner.")
            said = True
        stop.wait(pause)


# ── the browser ────────────────────────────────────────────────────────────
def start_chrome():
    PROFILE.mkdir(parents=True, exist_ok=True)
    o = webdriver.ChromeOptions()
    o.add_argument(f"--user-data-dir={PROFILE}")
    o.add_argument("--profile-directory=Default")
    o.add_argument("--window-size=1400,950")
    o.add_experimental_option("detach", True)
    o.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=o)


class Junique:
    def __init__(self, driver, log, stop: threading.Event, delay: float):
        self.d, self.log, self.stop, self.delay = driver, log, stop, delay
        self.net = Net(driver)
        self.last_save = "not seen"
        self.busy = lambda text: None          # the window's busy line, set by App

    # small helpers
    def pause(self, k=1.0):
        time.sleep(self.delay * k)
        if self.stop.is_set():
            raise StepError("stopped by you")

    def wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(POLL)
            if self.stop.is_set():
                raise StepError("stopped by you")

    def js_click(self, el):
        self.d.execute_script("arguments[0].scrollIntoView({block:'center'});arguments[0].click();", el)

    def find_visible(self, xpath, timeout=20, what=None):
        end = time.time() + timeout
        while time.time() < end:
            for el in self.d.find_elements(By.XPATH, xpath):
                try:
                    if el.is_displayed():
                        return el
                except Exception:          # noqa: BLE001 — element went away; look again
                    pass
            time.sleep(POLL)
            if self.stop.is_set():
                raise StepError("stopped by you")
        raise StepError(f"did not find {what or xpath} within {timeout} s")

    def click_text(self, tag, text, timeout=20):
        el = self.find_visible(f"//{tag}[normalize-space()={json.dumps(text)}]", timeout, f"'{text}'")
        self.js_click(el)
        return el

    def design_id(self):
        m = re.search(r"/creator-suite/designs/(\d+)", self.d.current_url) or \
            re.search(r"designId=(\d+)", self.d.current_url)
        return m.group(1) if m else None

    # the steps
    def new_design(self):
        self.d.get(DESIGNS_URL)
        btn = self.find_visible("//button[@data-cy='add-new-design-button']", 30, "'Add new design'"
                                " (are you signed in to Junique in this Chrome?)")
        self.js_click(btn)
        end = time.time() + 30
        while not re.search(r"/creator-suite/designs/\d+", self.d.current_url):
            if time.time() > end:
                raise StepError("Junique did not open a new design page")
            time.sleep(POLL)
        self.click_text("button", "Create design", 30)

    def upload(self, path: Path):
        self.click_text("button", "Upload asset", 60)
        # The file box is made by script; keep its dialog from opening and
        # hand Selenium the input instead.
        self.d.execute_script("""
          if (!window.__jqPatched) {
            const orig = HTMLInputElement.prototype.click;
            HTMLInputElement.prototype.click = function () {
              if (this.type === 'file') {
                window.__jqFile = this;
                if (!this.isConnected) { this.style.display = 'none'; document.body.appendChild(this); }
                return;
              }
              return orig.call(this);
            };
            window.__jqPatched = true;
          }
          window.__jqFile = null;
          // wait_placed reads finished downloads here; a full buffer drops new ones.
          performance.setResourceTimingBufferSize(5000);
          performance.clearResourceTimings();""")
        mark = self.net.mark()
        self.click_text("button", "From this device", 20)
        end = time.time() + 10
        box = None
        while box is None and time.time() < end:
            box = self.d.execute_script(
                "return window.__jqFile || document.querySelector('input[type=file]');")
            time.sleep(POLL)
        if box is None:
            raise StepError("the file box did not appear after 'From this device'")
        box.send_keys(str(path))
        self.log("  uploading the file…")
        end = time.time() + 180
        while time.time() < end:
            for method, url, status in self.net.since(mark):
                if "/web-api/image-ng" in url and status >= 400:
                    self.dismiss_understood()
                    raise StepError("Junique's quality check refused the file")
                if "/web-api/collage-designer-assets/" in url and method == "POST" and status < 300:
                    self.wait_placed(mark)
                    return
            time.sleep(0.2)
            if self.stop.is_set():
                raise StepError("stopped by you")
        raise StepError("the upload did not finish within 3 minutes")

    PICTURE_HOST = "jq-prod-de-image.s3"

    def wait_placed(self, mark, timeout=30) -> bool:
        """The accepted file is only in the design once the editor has
        downloaded it back and drawn it. Going to Products before that left an
        empty frame and no product switch (2026-11-05, drafts 705201 and
        705211). The owner reached Products 1.2 s or more after this download
        (MEASURED in the 2026-11-05 recording). The owner says the clicks
        can go on while the picture still loads, so a slow picture is noted,
        never a failure: the product switch check decides. No extra pause
        after it: the owner says the clicks can go on (2026-11-05)."""
        self.log("  waiting for the picture to show on the design…")
        end = time.time() + timeout
        while time.time() < end:
            asked = any(self.PICTURE_HOST in url and method == "GET" and status < 300
                        for method, url, status in self.net.since(mark))
            # A resource timing entry exists only once the download has finished.
            if asked and self.d.execute_script(
                    "return performance.getEntriesByType('resource')"
                    ".some(e => e.name.includes(arguments[0]));", self.PICTURE_HOST):
                return True
            time.sleep(POLL)
            if self.stop.is_set():
                raise StepError("stopped by you")
        self.log(f"  note: the picture was still loading after {timeout} s; going on")
        return False

    def dismiss_understood(self):
        try:
            self.click_text("*", "Understood", 5)
        except StepError:
            pass

    def product_names(self):
        return self.d.execute_script("""
          const leaf = [...document.querySelectorAll('div')].find(
              el => el.textContent.trim() === 'Poster' && el.parentElement && el.offsetParent);
          if (!leaf) return null;
          const list = leaf.parentElement.parentElement;
          return [...list.children].map(c => c.children[1]
              ? c.children[1].textContent.replace(/\\s+/g, ' ').trim() : null);""")

    def card(self, index):
        return self.d.execute_script("""
          const leaf = [...document.querySelectorAll('div')].find(
              el => el.textContent.trim() === 'Poster' && el.parentElement && el.offsetParent);
          return leaf.parentElement.parentElement.children[arguments[0]].children[1];""", index)

    def toggle(self, index, timeout=10, none_ok=False):
        """The product's own switch, on its card. Every product with an
        artboard shows one, so the whole page can hold many (MEASURED
        2026-11-05: Poster's showed while Notebook was chosen).
        With none_ok, a card marked "--disabled" (no artboard, MEASURED
        2026-11-05 on draft 705231) answers None at once instead of waiting
        out the timeout for a switch that never comes."""
        end = time.time() + timeout
        while True:
            card = self.card(index)
            boxes = [b for b in card.find_elements(
                         By.CSS_SELECTOR, "input[type=checkbox][id^='id-']")
                     if b.is_displayed() or b.find_element(By.XPATH, "..").is_displayed()]
            if len(boxes) == 1 or time.time() > end:
                break
            if none_ok and not boxes and "--disabled" in (card.get_dom_attribute("class") or ""):
                return None
            time.sleep(POLL)
        if not boxes and none_ok:
            return None
        if not boxes:
            raise StepError("no on/off switch for the product, even with its artboard set")
        if len(boxes) != 1:
            raise StepError(f"expected one on/off switch for the product, found {len(boxes)}")
        return boxes[0]

    def check_artboard(self, name):
        rows = [r for r in self.d.find_elements(By.CSS_SELECTOR, "div.ca-change-canvas__text")
                if r.is_displayed()]
        if not rows:
            raise StepError(f"{name}: no artboard row")
        if rows[0].text.strip() == ARTBOARD:
            return
        change = [c for c in self.d.find_elements(By.CSS_SELECTOR, "div.ca-change-canvas__change")
                  if c.is_displayed()][0]
        self.js_click(change)
        self.choose_artboard(name)
        rows = [r for r in self.d.find_elements(By.CSS_SELECTOR, "div.ca-change-canvas__text")
                if r.is_displayed()]
        if not rows or rows[0].text.strip() != ARTBOARD:
            raise StepError(f"{name}: the artboard did not change to {ARTBOARD}")

    def choose_artboard(self, name):
        """In the open "Choose an artboard for this product" box, pick ours."""
        self.find_visible("//*[normalize-space()='Choose an artboard for this product']", 10,
                          "the 'Choose an artboard' box")
        # The tile (picture and label) takes the click; the label alone does
        # nothing (MEASURED 2026-11-05 on draft 705231).
        tiles = [t for t in self.d.find_elements(By.CSS_SELECTOR, ".ca-preview-page-thumb-tooltip__page-thumb")
                 if t.is_displayed() and t.text.strip() == ARTBOARD]
        if not tiles:
            raise StepError(f"{name}: artboard {ARTBOARD} not offered")
        # A pick closes the box. A tile that is already the chosen one
        # ("--active") does nothing when clicked, and Escape does not close
        # the box either; "Change" does (MEASURED 2026-11-05 on draft 705270,
        # where Notebook had its artboard by the time the box opened).
        if "--active" not in (tiles[0].get_dom_attribute("class") or ""):
            self.js_click(tiles[0])
        end, closed_by_change = time.time() + 5, False
        while any(h.is_displayed() for h in self.d.find_elements(
                By.CSS_SELECTOR, ".ca-preview-page-thumb-tooltip__headline")):
            if time.time() > end:
                raise StepError(f"{name}: choosing artboard {ARTBOARD} did nothing")
            if not closed_by_change and time.time() > end - 4 and self.artboard_text() == ARTBOARD:
                self.js_click([c for c in self.d.find_elements(By.CSS_SELECTOR, "div.ca-change-canvas__change")
                               if c.is_displayed()][0])
                closed_by_change = True
            time.sleep(POLL)
        self.pause(0.6)

    def artboard_text(self):
        rows = [r for r in self.d.find_elements(By.CSS_SELECTOR, "div.ca-change-canvas__text")
                if r.is_displayed()]
        return rows[0].text.strip() if rows else None

    # The "+" of the "Front" side under the preview (MEASURED 2026-11-05 on
    # draft 705231: a script click on it opens the artboard box).
    PLUS = """
      const page = [...document.querySelectorAll('.ca-preview-product-pages__product-page')].find(p =>
          p.offsetParent && (p.querySelector('.product-page__page-label') || {}).textContent === 'Front');
      return page ? page.querySelector('.ca-change-canvas__add-canvas') : null;"""

    def add_artboard(self, name):
        """On the tool's designs the products open with no artboard, so no
        switch shows (drafts 705201, 705211, 705221). The owner's fix: "+"
        beside "Front", then the picture "#1 (2:3)" (2026-11-05)."""
        plus = self.d.execute_script(self.PLUS)
        if plus is None:
            raise StepError(f"{name}: no artboard, and no '+' beside 'Front' to add one")
        # A click while the list is still scrolling to the product's tab can
        # be lost (MEASURED 2026-11-05: Sticker opened on the second click).
        for _ in range(3):
            self.js_click(plus)
            end = time.time() + 3
            while time.time() < end and not any(h.is_displayed() for h in self.d.find_elements(
                    By.CSS_SELECTOR, ".ca-preview-page-thumb-tooltip__headline")):
                time.sleep(POLL)
            if time.time() < end:
                break
            plus = self.d.execute_script(self.PLUS) or plus
        self.choose_artboard(name)
        self.log(f"  {name}: artboard {ARTBOARD} added")

    READ_POSTER = """
      const st = (window.Konva ? Konva.stages : []).filter(s => {
          const c = s.container(); return c.closest('[class*="ca-preview-canvas"]') && c.offsetParent; });
      if (st.length !== 1) return {stages: st.length};
      const s = st[0], W = s.width(), H = s.height();
      const imgs = s.find('Image').map(i => i.getClientRect());
      // Frames and product photos stick out on the left: Shower Curtain frame
      // x -8, Notebook frame x -24, T-Shirt photo x -101. The poster never
      // does (x 0, 0, 8) when it needs a drag down (MEASURED 2026-11-05).
      // A top a few pixels above the edge is rounding (Notebook: y -2).
      const posters = imgs.filter(r => r.x >= -1 && r.y < -4);
      return {stages: 1, W, H, posters, el: s.container()};"""

    def snap_top(self, name):
        """Shower Curtain and the like open with the poster pushed up past
        the top edge (MEASURED: y = -146). Drag it down until its top meets
        the edge. The editor moves it with its own mouse handling, so this
        is a real mouse drag, checked by reading Konva again."""
        for attempt in range(3):
            r = self.d.execute_script(self.READ_POSTER)
            if r.get("stages") != 1:
                raise StepError(f"{name}: expected one preview, found {r.get('stages')}")
            posters = r["posters"]            # only pictures pushed above the top edge
            if not posters:
                if attempt:
                    self.log(f"  {name}: poster moved to the top edge")
                return
            if len(posters) > 1:
                raise StepError(f"{name}: {len(posters)} pictures above the edge, expected one")
            top = posters[0]["y"]
            ActionChains(self.d).move_to_element(r["el"]).click_and_hold() \
                .move_by_offset(0, 10).move_by_offset(0, round(-top) - 10).pause(0.2) \
                .release().perform()
            self.pause(0.8)
        raise StepError(f"{name}: the poster would not move to the top edge")

    def products(self):
        self.click_text("span", "2. Products", 30)
        names = self.wait_names()
        unknown = [n for n in names if n and wanted(n) is None]
        if unknown:
            raise StepError("new product(s) on Junique, not in the on/off list: "
                            + ", ".join(unknown) + " (tell Claude whether each should be on)")
        colour_done = False
        for i, name in enumerate(names):
            if not name:
                continue
            on = wanted(name)
            self.js_click(self.card(i))
            self.pause(0.7)
            box = self.toggle(i, timeout=4, none_ok=True)
            if box is None:
                if not on:
                    continue                  # no artboard: the product has no picture, so it is off
                self.add_artboard(name)
                box = self.toggle(i)
            if box.is_selected() != on:
                self.js_click(box)
                self.pause(0.6)
                if self.toggle(i).is_selected() != on:
                    raise StepError(f"{name}: the switch did not turn {'on' if on else 'off'}")
            if not on:
                continue
            self.check_artboard(name)
            if not colour_done:
                # The colour row (dark for apparel and notebook backs) is one
                # setting for the design; it shows on the first such product.
                swatch = [li for li in self.d.find_elements(By.XPATH, "//li[@title='Black']")
                          if li.is_displayed()]
                if swatch:
                    self.js_click(swatch[0])
                    colour_done = True
                    self.pause(0.5)
            self.snap_top(name)
        if not colour_done:
            self.log("  note: no Black colour was offered on any product")
        self.check_switches(names)
        self.log(f"  {sum(1 for n in names if n and wanted(n))} products on, "
                 f"{sum(1 for n in names if n and not wanted(n))} off")

    def check_switches(self, names):
        """Read every product's switch once more, without clicking. With the
        short waits a "+" clicked before the preview moved on could give the
        previous product an artboard (and maybe switch it on); this catches
        it before Save instead of publishing a product the owner keeps off."""
        wrong = []
        for i, name in enumerate(names):
            if not name:
                continue
            box = self.toggle(i, timeout=0, none_ok=True)
            is_on = bool(box and box.is_selected())
            if is_on != wanted(name):
                wrong.append(f"{name} is {'on' if is_on else 'off'}")
        if wrong:
            raise StepError("products in the wrong state: " + ", ".join(wrong))

    def wait_names(self, timeout=30):
        end = time.time() + timeout
        names = None
        while not names and time.time() < end:
            names = self.product_names()
            time.sleep(POLL)
        if not names:
            raise StepError("the product list did not appear")
        return names

    def save_and_exit(self, settle=10, every=10, total=180, reload_at=90, settle_reload=15):
        """Save & exit sometimes does nothing: no request, no message, no box
        (Hong Kong 706496, New York 706509, Los Angeles 707154; 2026-11-06/07).
        The same click minutes later worked at once (MEASURED on 706509 and
        707154). Why the editor ignores it is not known (LEAD: it is still busy
        with something). Hong Kong showed "Something went wrong" and only a
        reload helped (MEASURED). So: click, watch 10 s, click again, for up to
        3 minutes; reload once halfway; log what Junique sent after each click
        so the next stop gives evidence, not a guess."""
        self.wait(settle)
        start, clicks, reloaded = time.time(), 0, False
        first = self.net_mark()
        while True:
            mark = self.net_mark()
            self.click_text("button", "Save & exit", 20)
            clicks += 1
            if self.saved_and_left(every):
                if clicks > 1:
                    self.log(f"  Save & exit worked on click {clicks}, {int(time.time() - start)} s after the first")
                self.last_save = self.save_requests(first)
                self.log(f"  saved ({self.last_save})")
                return
            self.log(f"  Save & exit click {clicks} did nothing in {every} s ({self.save_evidence(mark)})")
            spent = time.time() - start
            if spent >= total:
                break
            if not reloaded and spent >= reload_at:
                reloaded = True
                self.log("  reloading the editor, then clicking Save & exit again")
                self.d.refresh()
                self.check_switches(self.wait_names())
                self.wait(settle_reload)
        raise StepError(f"Save & exit was clicked {clicks} times over {int(time.time() - start)} s"
                        f"{' (with one reload)' if reloaded else ''} and Junique never saved")

    def saved_and_left(self, timeout):
        end, anyway_at = time.time() + timeout, 0.0
        while time.time() < end:
            if re.search(r"/creator-suite/designs/\d+", self.d.current_url):
                return True
            for b in self.d.find_elements(By.XPATH, "//button[normalize-space()='Save anyway']"):
                # Cortina, draft 706464 (2026-11-06): Junique said "Something
                # went wrong", the button went away mid-check and the stale
                # read stopped the run, though the save went through.
                # Philadelphia, draft 707197 (2026-11-07): the box stays up for
                # about 1.5 s while Junique saves; clicking it every 0.1 s may
                # start more than one save (LEAD: the design page then loaded
                # 3 times and the draft came back empty). Once per 10 s.
                try:
                    if b.is_displayed() and time.time() - anyway_at >= 10:
                        self.js_click(b)
                        anyway_at = time.time()
                        end = max(end, anyway_at + 60)     # the save itself takes a few s
                except Exception:          # noqa: BLE001 — element went away; look again
                    pass
            time.sleep(POLL)
            if self.stop.is_set():
                raise StepError("stopped by you")
        return False

    def net_mark(self):
        try:
            return self.net.mark()
        except Exception:          # noqa: BLE001 — the evidence is a bonus
            return None

    def save_requests(self, mark):
        """The editor's saves Junique answered: the PUT to
        collage-designer-design carries the picture and the products."""
        try:
            if mark is None:
                return "requests not readable"
            puts = [st for m, u, st in self.net.since(mark) if m == "PUT" and "collage-designer-design" in u]
        except Exception:          # noqa: BLE001 — the evidence is a bonus
            return "requests not readable"
        return f"{len(puts)} save request(s), answered {', '.join(map(str, puts)) or 'nothing'}"

    def save_evidence(self, mark):
        """What Junique did after a Save & exit click that had no effect."""
        try:
            sent = [f"{m} {u.split('juniqe.com')[-1][:70]} {st}" for m, u, st in self.net.since(mark)
                    if "juniqe.com" in u and (m != "GET" or st >= 400)] if mark is not None else None
        except Exception:          # noqa: BLE001
            sent = None
        wrong = False
        for el in self.d.find_elements(By.XPATH, "//*[contains(text(), 'went wrong')]"):
            try:
                wrong = wrong or el.is_displayed()
            except Exception:      # noqa: BLE001 — element went away
                pass
        said = "requests: " + (", ".join(sent) or "none") if sent is not None else "requests not readable"
        return said + ("; 'Something went wrong' is showing" if wrong else "; no error showing")

    def details(self, title, first=10, after_reload=30):
        # London (706551, 2026-11-06) came back from Save & exit showing
        # "Start with your design" and stayed so; after a reload the page
        # showed its 31 products and Rename (MEASURED). Draft 705258 was
        # likely the same. So reload once before calling it empty.
        rename = "//button[@aria-label='Rename']"
        try:
            found = self.find_visible(rename, first, "the Rename button")
        except StepError:
            if self.stop.is_set():
                raise
            self.log("  the saved design looks empty; reloading the page to look again")
            self.d.refresh()
            found = self.find_visible(f"{rename} | //*[normalize-space()='Start with your design']",
                                      after_reload, "the Rename button")
            if found.tag_name.lower() != "button":
                self.wait(5)            # the empty view may still give way to the design
                found = next(iter(b for b in self.d.find_elements(By.XPATH, rename) if b.is_displayed()), None)
                if found is None:
                    raise EmptyDesign("the saved design is empty on Junique ('Start with your design'), "
                                    "even after a reload: the picture and products were not kept")
        self.js_click(found)
        box = self.find_visible("//input[@placeholder='Name']", 10, "the Name box")
        box.send_keys(Keys.CONTROL, "a")
        box.send_keys(Keys.DELETE)
        box.send_keys(title[:60])
        self.click_text("button", "Save", 10)
        self.find_visible("//*[contains(normalize-space(), 'Successfully saved!')]", 15,
                          "'Successfully saved!'")
        terms = self.d.find_element(By.ID, "v2-termsAndConditions")
        if not terms.is_selected():
            self.js_click(terms)
            self.pause(0.5)
        if not self.d.find_element(By.ID, "v2-termsAndConditions").is_selected():
            raise StepError("the rights box did not tick")

    def publish(self, junique_id, auto: bool, wait_minutes: int, enable_wait=30, confirm_wait=20):
        mark = self.net.mark()
        if auto:
            # Singapore (707145, 2026-11-07): Publish carries `disabled` until
            # Junique has taken in the name and the rights box; a click then
            # does nothing and nothing says so. Wait for it, click again if
            # no confirmation comes.
            for _ in range(3):
                self.js_click(self.enabled_publish(enable_wait))
                if self.confirmed(junique_id, mark, confirm_wait):
                    return
                self.log("  Publish was not confirmed; clicking it again")
            raise StepError("Publish was clicked 3 times but Junique did not confirm it")
        self.log("  READY: check it in Chrome, then click Publish yourself.")
        self.busy(f"WAITING FOR YOU: check the design in Chrome, then click Publish "
                  f"(up to {wait_minutes} min)")
        if not self.confirmed(junique_id, mark, wait_minutes * 60):
            raise StepError("Publish was not confirmed in time")

    def enabled_publish(self, timeout):
        end = time.time() + timeout
        while True:
            btn = self.find_visible("//button[@data-cy='publish-button']", 10, "Publish")
            try:
                if btn.get_dom_attribute("disabled") is None:
                    return btn
            except Exception:              # noqa: BLE001 — redrawn; look again
                pass
            if time.time() > end:
                raise StepError(f"Publish stayed greyed out for {timeout} s")
            self.wait(POLL)

    def confirmed(self, junique_id, mark, limit):
        """True once Junique's PATCH .../status answers; False at the time limit."""
        end = time.time() + limit
        while time.time() < end:
            for method, url, status in self.net.since(mark):
                if method == "PATCH" and url.endswith(f"/designs/{junique_id}/status"):
                    if status < 300:
                        return True
                    raise StepError(f"Junique refused Publish ({status})")
            time.sleep(min(1, limit))
            if self.stop.is_set():
                raise StepError("stopped by you before Publish")
        return False

    def one(self, d, auto, wait_minutes):
        path = Path(d["file"])
        if not path.exists():
            raise StepError(f"the file is not in the folder any more ({path})")
        t = [time.time()]
        lap = lambda: t.append(time.time()) or round(t[-1] - t[-2])
        self.new_design()
        jid = self.design_id()
        self.upload(path)
        took = f"new design and upload {lap()} s"
        try:
            self.products()
            took += f", products {lap()} s"
        except StepError as e:
            if auto or self.stop.is_set():
                raise
            # The draft already holds a Junique slot; finishing it by hand
            # costs nothing, failing it wastes the slot.
            self.log(f"  PRODUCTS NOT DONE: {short(e)}")
            self.log(f"  FINISH BY HAND in Chrome: set the products, Save & exit, name it "
                     f"\"{d['title'][:60]}\", tick the rights box, then click Publish.")
            self.busy(f"WAITING FOR YOU: finish \"{d['title']}\" by hand and click Publish "
                      f"(up to {wait_minutes} min)")
            self.publish(jid, False, wait_minutes)
            return jid
        self.save_and_exit()
        jid = self.design_id() or jid
        try:
            self.details(d["title"])
        except EmptyDesign as e:
            # Mumbai (709832, 2026-11-10): the save was answered 202, not 200,
            # and Junique kept nothing. Doing the same draft again does not
            # work: its editor then opens on an empty 3:4 board and the
            # picture only reaches the asset list (MEASURED). So it fails.
            raise EmptyDesign(f"{e}. Junique answered the save: {self.last_save}") from None
        self.log(f"  took: {took}, save and name {lap()} s (total {round(t[-1] - t[0])} s)")
        self.publish(jid, auto, wait_minutes)
        return jid


# ── the window ─────────────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        root.title("Junique uploader")
        root.geometry("760x560")
        self.stop = threading.Event()
        self.driver = None
        top = tk.Frame(root, padx=10, pady=8)
        top.pack(fill="x")
        tk.Label(top, text="How many:").pack(side="left")
        self.count = tk.Entry(top, width=5)
        self.count.insert(0, "5")
        self.count.pack(side="left", padx=4)
        self.b_fetch = tk.Button(top, text="Fetch", command=self.fetch)
        self.b_fetch.pack(side="left", padx=4)
        self.b_chrome = tk.Button(top, text="Open Chrome (sign in once)", command=self.open_chrome)
        self.b_chrome.pack(side="left", padx=4)
        row = tk.Frame(root, padx=10)
        row.pack(fill="x")
        self.auto = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Publish by itself (off: stops before Publish for you)",
                       variable=self.auto).pack(side="left")
        tk.Label(row, text="  Seconds between steps:").pack(side="left")
        self.delay = tk.Entry(row, width=4)
        self.delay.insert(0, "0.3")          # 1.0 until 2026-11-05; raise it if clicks get lost
        self.delay.pack(side="left")
        row2 = tk.Frame(root, padx=10, pady=6)
        row2.pack(fill="x")
        self.b_upload = tk.Button(row2, text="Upload the fetched designs", bg="#2e7d32", fg="white",
                                  command=self.upload)
        self.b_upload.pack(side="left")
        self.b_stop = tk.Button(row2, text="Stop", command=self.press_stop, state="disabled")
        self.b_stop.pack(side="left", padx=6)
        self.status = tk.Label(row2, text="")
        self.status.pack(side="left", padx=8)
        # The busy line: what is running now, a moving bar and the seconds,
        # so a slow step (the server list takes ~10 s) never looks like a
        # click that did nothing (the owner, 2026-11-05).
        row3 = tk.Frame(root, padx=10)
        row3.pack(fill="x")
        self.bar = ttk.Progressbar(row3, mode="indeterminate", length=160)
        self.bar.pack(side="left")
        self.busy = tk.Label(row3, text="Ready.", fg="#555", anchor="w")
        self.busy.pack(side="left", padx=8, fill="x", expand=True)
        self.bar_secs = tk.Label(row3, text="", fg="#555")
        self.bar_secs.pack(side="right")
        self.job = None
        self.out = scrolledtext.ScrolledText(root, height=26, font=("Consolas", 9))
        self.out.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.show_summary()

    def log(self, text):
        self.root.after(0, lambda: (self.out.insert("end", text + "\n"), self.out.see("end")))
        # Philadelphia (707197, 2026-11-07) failed and only this window held
        # what happened; a file keeps it for whoever investigates.
        try:
            with open(RUN_LOG, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def show_summary(self):
        led = L.load()
        s = L.summary(led)
        self.status.config(text=f"Uploaded {s['uploaded']} · waiting {s['waiting']} · gave up "
                                f"{s['gave_up']} · in this batch {len(L.batch_todo(led))}")

    def set_busy(self, text):
        """Show what is running now (safe from any thread)."""
        self.root.after(0, lambda: self.job and self.busy.config(
            text=f"{self.job[0]}: {text}"))

    def _tick(self):
        if self.job:
            secs = int(time.time() - self.job[1])
            self.bar_secs.config(text=f"{secs} s")
            self.root.after(500, self._tick)

    def background(self, what, fn, stoppable=False):
        """Run one job at a time. While it runs every action button is greyed
        out, so a second click cannot start it twice."""
        if self.job:
            return
        self.job = (what, time.time())
        for b in (self.b_fetch, self.b_chrome, self.b_upload):
            b.config(state="disabled")
        self.b_stop.config(state="normal" if stoppable else "disabled")
        self.busy.config(text=f"{what}: starting…", fg="#b26a00")
        self.bar.start(12)
        self._tick()
        self.log(f"── {what} ──")

        def done():
            self.job = None
            self.bar.stop()
            self.bar_secs.config(text="")
            for b in (self.b_fetch, self.b_chrome, self.b_upload):
                b.config(state="normal")
            self.b_stop.config(state="disabled", text="Stop")
            self.busy.config(text="Ready.", fg="#555")
            self.show_summary()

        def run():
            try:
                fn()
            except Exception as e:             # noqa: BLE001 — shown, never swallowed
                self.log(f"STOPPED: {short(e)}")
            self.root.after(0, done)
        threading.Thread(target=run, daemon=True).start()

    def press_stop(self):
        self.stop.set()
        self.b_stop.config(state="disabled", text="Stopping…")
        self.set_busy("stopping after the current step…")

    def fetch(self):
        n = int(self.count.get() or 0)

        def emit(line):
            self.log(line)
            self.set_busy(line.strip())
        self.background(f"Fetching {n}", lambda: L.fetch(n, emit=emit))

    def alive(self) -> bool:
        if self.driver is None:
            return False
        try:
            self.driver.window_handles         # raises once the window is closed
            return True
        except Exception:                      # noqa: BLE001 — any error means gone
            return False

    def chrome(self):
        """The tool's Chrome, opened again if the owner closed it."""
        if not self.alive():
            if self.driver is not None:
                self.log("Chrome was closed; opening it again (you stay signed in).")
                try:
                    self.driver.quit()
                except Exception:              # noqa: BLE001 — it is already gone
                    pass
            self.set_busy("opening Chrome…")
            self.driver = start_chrome()
        return self.driver

    def open_chrome(self):
        def go():
            d = self.chrome()
            self.set_busy("loading the Junique designs page…")
            d.get(DESIGNS_URL)
            self.log("Chrome is open. Sign in to Junique there once; it remembers you. "
                     "Leave this Chrome open while uploading.")
        self.background("Opening Chrome", go)

    def upload(self):
        self.stop.clear()
        auto = self.auto.get()
        delay = float(self.delay.get() or 0.3)

        def go():
            todo = L.batch_todo(L.load())
            if not todo:
                self.log("Nothing fetched to upload. Press Fetch first.")
                return
            jq = Junique(self.chrome(), self.log, self.stop, delay)
            jq.busy = self.set_busy
            done = failed = 0
            for n, d in enumerate(todo, 1):
                if self.stop.is_set():
                    self.log("Stopped by you.")
                    break
                wait_until_quiet(self.log, self.stop)
                if self.stop.is_set():
                    self.log("Stopped by you.")
                    break
                self.log(f"{n}/{len(todo)}  {d['title']}")
                self.set_busy(f"design {n} of {len(todo)}: {d['title']}"
                              + ("" if auto else " (waits for your Publish)"))
                jid = None
                try:
                    jid = jq.one(d, auto, wait_minutes=30)
                    L.mark_uploaded(d["saved_poster_id"], jid)
                    done += 1
                    self.log(f"  PUBLISHED (Junique design {jid})")
                except Exception as e:         # noqa: BLE001 — recorded per design
                    if not self.alive():
                        # Chrome was closed: not the design's fault, so no failure is counted.
                        self.log("  Chrome was closed, so the upload stopped. This design is not "
                                 "counted as failed; press Upload again to carry on.")
                        break
                    jid = jid or jq.design_id()
                    if self.stop.is_set():
                        # Stop is the owner's choice, not a refusal: no try is counted.
                        self.log("  Stopped by you; not counted as failed."
                                 + (f" Draft {jid} is left on Junique." if jid else ""))
                        break
                    SHOTS.mkdir(parents=True, exist_ok=True)
                    shot = SHOTS / f"{time.strftime('%Y%m%d-%H%M%S')}_{d['saved_poster_id']}.png"
                    try:
                        jq.d.save_screenshot(str(shot))
                    except Exception:          # noqa: BLE001 — the screenshot is a bonus
                        shot = None
                    L.mark_failed(d["saved_poster_id"], str(e), jid)
                    failed += 1
                    self.log(f"  FAILED: {short(e)}" + (f"  (draft {jid} left on Junique)" if jid else "")
                             + (f"  screenshot: {shot}" if shot else ""))
                    # Every new design uses one of Junique's 20 slots, even a
                    # failed one, so a failure stops the run (2026-11-05: two
                    # slots went on failures in one run).
                    self.log("  The run stopped here so no more Junique slots are used. "
                             "Tell Claude what the log says before pressing Upload again.")
                    self.root.after(0, self.show_summary)
                    break
                self.root.after(0, self.show_summary)
            self.log(f"Done: {done} published, {failed} failed.")
        self.background("Uploading", go, stoppable=True)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
