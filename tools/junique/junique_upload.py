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
from tkinter import scrolledtext

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

import ledger as L

DESIGNS_URL = "https://www.juniqe.com/creator-suite/designs"
PROFILE = L.LOCAL / "chrome_profile"
SHOTS = L.LOCAL / "screenshots"
ARTBOARD = "#1 (2:3)"

# Which products stay on. Checked in order, first match wins; a product
# matching nothing stops the design, so a new Junique product is never
# switched on or off by guess. (DECIDED by the owner 2026-11-05.)
PRODUCT_RULES = [
    ("sticker sheet", False), ("phone case", False), ("doormat", False),
    ("beach towel (16:9)", False), ("fleece blanket (4:3)", False), ("cushion (3:2)", False),
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

    # small helpers
    def pause(self, k=1.0):
        time.sleep(self.delay * k)
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
            time.sleep(0.3)
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
            time.sleep(0.3)
        self.pause()
        self.click_text("button", "Create design", 30)

    def upload(self, path: Path):
        self.click_text("button", "Upload asset", 60)
        self.pause(0.5)
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
          window.__jqFile = null;""")
        mark = self.net.mark()
        self.click_text("button", "From this device", 20)
        end = time.time() + 10
        box = None
        while box is None and time.time() < end:
            box = self.d.execute_script(
                "return window.__jqFile || document.querySelector('input[type=file]');")
            time.sleep(0.2)
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
                    self.pause()
                    return
            time.sleep(0.5)
            if self.stop.is_set():
                raise StepError("stopped by you")
        raise StepError("the upload did not finish within 3 minutes")

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

    def toggle(self):
        boxes = [b for b in self.d.find_elements(By.CSS_SELECTOR, "input[type=checkbox][id^='id-']")
                 if b.is_displayed() or b.find_element(By.XPATH, "..").is_displayed()]
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
        self.find_visible("//*[normalize-space()='Choose an artboard for this product']", 10)
        options = [o for o in self.d.find_elements(By.XPATH, f"//*[normalize-space(text())='{ARTBOARD}']")
                   if o.is_displayed() and "ca-change-canvas__text" not in (o.get_attribute("class") or "")]
        if not options:
            raise StepError(f"{name}: artboard {ARTBOARD} not offered")
        self.js_click(options[-1])
        self.pause(0.6)
        rows = [r for r in self.d.find_elements(By.CSS_SELECTOR, "div.ca-change-canvas__text")
                if r.is_displayed()]
        if not rows or rows[0].text.strip() != ARTBOARD:
            raise StepError(f"{name}: the artboard did not change to {ARTBOARD}")

    READ_POSTER = """
      const st = (window.Konva ? Konva.stages : []).filter(s => {
          const c = s.container(); return c.closest('[class*="ca-preview-canvas"]') && c.offsetParent; });
      if (st.length !== 1) return {stages: st.length};
      const s = st[0], W = s.width(), H = s.height();
      const imgs = s.find('Image').map(i => i.getClientRect());
      // The frame drawn over the product overshoots the stage by at most a
      // few pixels on every side (8 measured); the poster is the other one.
      const frame = r => r.x <= 0 && r.y <= 0 && r.x >= -16 && r.y >= -16 &&
                         r.x + r.width >= W && r.x + r.width <= W + 16 &&
                         r.y + r.height >= H && r.y + r.height <= H + 16;
      const posters = imgs.filter(r => !frame(r) && r.y < -1);
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
        end = time.time() + 30
        names = None
        while not names and time.time() < end:
            names = self.product_names()
            time.sleep(0.5)
        if not names:
            raise StepError("the product list did not appear")
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
            box = self.toggle()
            if box.is_selected() != on:
                self.js_click(box)
                self.pause(0.6)
                if self.toggle().is_selected() != on:
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
        self.log(f"  {sum(1 for n in names if n and wanted(n))} products on, "
                 f"{sum(1 for n in names if n and not wanted(n))} off")

    def save_and_exit(self):
        self.click_text("button", "Save & exit", 20)
        end = time.time() + 60
        while time.time() < end:
            if re.search(r"/creator-suite/designs/\d+", self.d.current_url):
                return
            for b in self.d.find_elements(By.XPATH, "//button[normalize-space()='Save anyway']"):
                if b.is_displayed():
                    self.js_click(b)
            time.sleep(0.5)
        raise StepError("Save & exit did not return to the design page")

    def details(self, title):
        self.js_click(self.find_visible("//button[@aria-label='Rename']", 30, "the Rename button"))
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

    def publish(self, junique_id, auto: bool, wait_minutes: int):
        mark = self.net.mark()
        if auto:
            self.js_click(self.find_visible("//button[@data-cy='publish-button']", 10, "Publish"))
            limit = 90
        else:
            self.log("  READY: check it in Chrome, then click Publish yourself.")
            limit = wait_minutes * 60
        end = time.time() + limit
        while time.time() < end:
            for method, url, status in self.net.since(mark):
                if method == "PATCH" and url.endswith(f"/designs/{junique_id}/status"):
                    if status < 300:
                        return
                    raise StepError(f"Junique refused Publish ({status})")
            time.sleep(1)
            if self.stop.is_set():
                raise StepError("stopped by you before Publish")
        raise StepError("Publish was not confirmed in time")

    def one(self, d, auto, wait_minutes):
        path = Path(d["file"])
        if not path.exists():
            raise StepError(f"the file is not in the folder any more ({path})")
        self.new_design()
        jid = self.design_id()
        self.upload(path)
        self.products()
        self.save_and_exit()
        jid = self.design_id() or jid
        self.details(d["title"])
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
        tk.Button(top, text="Fetch", command=self.fetch).pack(side="left", padx=4)
        tk.Button(top, text="Open Chrome (sign in once)", command=self.open_chrome).pack(side="left", padx=4)
        row = tk.Frame(root, padx=10)
        row.pack(fill="x")
        self.auto = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Publish by itself (off: stops before Publish for you)",
                       variable=self.auto).pack(side="left")
        tk.Label(row, text="  Seconds between steps:").pack(side="left")
        self.delay = tk.Entry(row, width=4)
        self.delay.insert(0, "1.0")
        self.delay.pack(side="left")
        row2 = tk.Frame(root, padx=10, pady=6)
        row2.pack(fill="x")
        tk.Button(row2, text="Upload the fetched designs", bg="#2e7d32", fg="white",
                  command=self.upload).pack(side="left")
        tk.Button(row2, text="Stop", command=self.stop.set).pack(side="left", padx=6)
        self.status = tk.Label(row2, text="")
        self.status.pack(side="left", padx=8)
        self.out = scrolledtext.ScrolledText(root, height=26, font=("Consolas", 9))
        self.out.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.show_summary()

    def log(self, text):
        self.root.after(0, lambda: (self.out.insert("end", text + "\n"), self.out.see("end")))

    def show_summary(self):
        led = L.load()
        s = L.summary(led)
        self.status.config(text=f"Uploaded {s['uploaded']} · waiting {s['waiting']} · gave up "
                                f"{s['gave_up']} · in this batch {len(L.batch_todo(led))}")

    def background(self, fn):
        def run():
            try:
                fn()
            except Exception as e:             # noqa: BLE001 — shown, never swallowed
                self.log(f"STOPPED: {e}")
            self.root.after(0, self.show_summary)
        threading.Thread(target=run, daemon=True).start()

    def fetch(self):
        n = int(self.count.get() or 0)
        self.background(lambda: L.fetch(n, emit=self.log))

    def chrome(self):
        if self.driver is None:
            self.driver = start_chrome()
        return self.driver

    def open_chrome(self):
        def go():
            self.chrome().get(DESIGNS_URL)
            self.log("Chrome is open. Sign in to Junique there once; it remembers you.")
        self.background(go)

    def upload(self):
        self.stop.clear()
        auto = self.auto.get()
        delay = float(self.delay.get() or 1.0)

        def go():
            todo = L.batch_todo(L.load())
            if not todo:
                self.log("Nothing fetched to upload. Press Fetch first.")
                return
            jq = Junique(self.chrome(), self.log, self.stop, delay)
            done = failed = 0
            for n, d in enumerate(todo, 1):
                if self.stop.is_set():
                    break
                self.log(f"{n}/{len(todo)}  {d['title']}")
                jid = None
                try:
                    jid = jq.one(d, auto, wait_minutes=30)
                    L.mark_uploaded(d["saved_poster_id"], jid)
                    done += 1
                    self.log(f"  PUBLISHED (Junique design {jid})")
                except Exception as e:         # noqa: BLE001 — recorded per design
                    jid = jid or jq.design_id()
                    SHOTS.mkdir(parents=True, exist_ok=True)
                    shot = SHOTS / f"{time.strftime('%Y%m%d-%H%M%S')}_{d['saved_poster_id']}.png"
                    try:
                        jq.d.save_screenshot(str(shot))
                    except Exception:          # noqa: BLE001 — the screenshot is a bonus
                        shot = None
                    L.mark_failed(d["saved_poster_id"], str(e), jid)
                    failed += 1
                    self.log(f"  FAILED: {e}" + (f"  (draft {jid} left on Junique)" if jid else "")
                             + (f"  screenshot: {shot}" if shot else ""))
                self.root.after(0, self.show_summary)
            self.log(f"Done: {done} published, {failed} failed.")
        self.background(go)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
