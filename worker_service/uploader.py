"""
Marketplace upload stage — Selenium, sequential, one tab.

════════════════════════════════════════════════════════════════════════════
WHY SEQUENTIAL (do not "optimise" this back)
════════════════════════════════════════════════════════════════════════════
The legacy tool opened one tab per image up-front (35-50 at a time) and then
walked them in phases. It lost 20-30% of every batch, and the loss rate grew
with batch size. Causes, all structural:

  * Memory pressure — dozens of live marketplace pages, tabs crash silently.
  * Session/page staleness — the page opened at tab 5 has expired by tab 40.
  * Stale element references — Selenium handles die when a tab re-renders.
  * Rate heuristics — many simultaneous opens looks exactly like a bot.

This module opens ONE tab and completes one image at a time. It's slower per
image and that is irrelevant: the node is unattended and only needs to land
100/day. Reliability is the whole point.

════════════════════════════════════════════════════════════════════════════
EVERYTHING IS CONFIGURATION
════════════════════════════════════════════════════════════════════════════
No CSS selector, URL, wait or template appears as a literal below — they all
arrive from the dashboard in the claim payload. When a marketplace changes its
form you edit a selector in the browser and re-run a single-image test. That
is the difference between a five-minute fix and a redeploy.

Selector strings are prefixed: `css:`, `xpath:` or `name:`.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .client import PipelineClient, PipelineError


# Phrases that mean "the site is challenging us, not rejecting the image".
# Seeing one of these must pause the whole account rather than burn attempts
# on every queued image.
BOT_MARKERS = (
    "cloudflare", "captcha", "checking your browser", "access denied",
    "are you human", "unusual traffic", "verify you are human",
    "rate limit", "too many requests",
)


def _drive_root(root: Path) -> Path:
    """
    Make a bare Windows drive letter mean the ROOT of that drive.

    `Path("S:") / "fineartamerica"` produces `S:fineartamerica`, which is not
    a mistake Windows rejects — it is a legal DRIVE-RELATIVE path, resolved
    against whatever the current directory happens to be on S:. So the
    uploader was quietly looking in the wrong place whenever that directory
    was not the root, and the path in the error message looked almost right,
    which is the worst kind of wrong.

    `storage_root` is stored as "S:" in the dashboard, so this is the normal
    case rather than an edge one.
    """
    text = str(root)
    if re.fullmatch(r"[A-Za-z]:", text):
        return Path(text + "\\")
    return root


def profile_path_for(account: dict, config: dict) -> str:
    """
    Where an account's Chrome profile lives. THE definition — nothing else
    works this out for itself.

    Module level rather than a method because the cleanup job has to compute
    the same path for an account that no longer exists, and a second copy of
    this rule is exactly how the launcher and the orphan-sweeper drifted
    apart and left the sweeper doing nothing for months.

    Keyed on the account ID: names get renamed and can be reused after a ban,
    IDs cannot.
    """
    configured = (account.get("chrome_profile_dir") or "").strip()
    if configured:
        return configured
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_",
                  account.get("name") or "").strip("_") or "account"
    return str(Path(config.get("temp_dir", "C:/faa/temp"))
               / "profiles" / f"{account.get('id', 0)}_{slug}")


def profiles_root(config: dict) -> Path:
    """The folder profiles live under. Nothing outside it is ever deleted."""
    return Path(config.get("temp_dir", "C:/faa/temp")) / "profiles"


def _clone_options(source: Options, profile_dir: str) -> Options:
    """
    The same Chrome options, pointed at a different profile.

    Rebuilt rather than mutated: Selenium's Options object accumulates
    arguments and has no way to remove one, so editing the original would
    leave TWO --user-data-dir flags and Chrome would silently use the first.
    """
    clone = Options()
    for arg in source.arguments:
        if arg.startswith("--user-data-dir="):
            continue
        clone.add_argument(arg)
    clone.add_argument(f"--user-data-dir={profile_dir}")
    for key, value in (source.experimental_options or {}).items():
        clone.add_experimental_option(key, value)
    return clone


def _tail(path: Path, lines: int = 25) -> str:
    """Last few lines of a log file, or '' if there isn't one."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    return "\n".join(text[-lines:])


def _startup_error(original: Exception, profile_dir: str,
                   driver_log: Path) -> "UploadError":
    """
    Turn chromedriver's stack dump into something that names the problem.

    Everything here is chosen to be readable on the DASHBOARD, because that
    is the only place the owner can see it — the node is an unattended box he
    does not normally have a session on.
    """
    hints: list[str] = []

    found = _find_chrome_binary()
    if found:
        hints.append(f"Chrome found at {found}")
    else:
        hints.append("Chrome was NOT found in any standard location — install it, "
                     "or set its path in the account settings")

    if " " in str(profile_dir):
        hints.append(f"profile path contains a space: {profile_dir}")

    if (Path(profile_dir) / "SingletonLock").exists():
        hints.append("a stale SingletonLock exists in the profile — a previous "
                     "Chrome is still holding it, or crashed while holding it")

    hints.append("the profile was cleared and rebuilt and it STILL failed, so "
                 "this is Chrome or chromedriver itself, not the saved session")
    hints.append("verify by hand with: "
                 f'& "{found or "chrome.exe"}" --headless=new --disable-gpu '
                 f'--no-sandbox --dump-dom https://example.com')

    tail = _tail(driver_log)
    detail = f"\n--- chromedriver log ---\n{tail}" if tail else ""

    return UploadError(
        "Could not start Chrome. " + "; ".join(hints) +
        f". Original: {original}{detail}",
        fatal=True,
    )


def _find_chrome_binary() -> Optional[str]:
    """
    Locate chrome.exe in the usual places.

    Used only to turn chromedriver's opaque "Chrome instance exited" into a
    message that names the actual problem.
    """
    import shutil as _shutil

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return _shutil.which("chrome") or _shutil.which("google-chrome")



# FineArtAmerica serves this at a NORMAL artwork URL, with a normal 200 — the
# body is one sentence and nothing else. Captured 2026-08-13. Because it is a
# page rather than an HTTP error, a status-code check sails straight past it
# and the uploader would report every image as failed.
MAINTENANCE_MARKERS = (
    "we're undergoing maintenance",
    "we are undergoing maintenance",
    "undergoing maintenance and will be back",
)


def looks_like_maintenance(page_source: str) -> bool:
    """
    Is the marketplace down for maintenance rather than broken?

    Worth distinguishing because the response is completely different: a
    failed upload should be retried and eventually surfaced, whereas
    maintenance means STOP — every account, not just this one — and come back
    later. Retrying through a maintenance window burns the daily quota on
    requests that cannot succeed.
    """
    lowered = (page_source or "").lower()
    return any(m in lowered for m in MAINTENANCE_MARKERS)


class UploadError(RuntimeError):
    """
    An upload failure.

    `pause_minutes` > 0 marks it systemic (bot check, bad credentials, form
    structure changed) so the server parks the account. `fatal` aborts the
    remaining batch on this node.
    """

    def __init__(self, message: str, *, pause_minutes: int = 0,
                 pause_reason: Optional[str] = None, fatal: bool = False,
                 pause_immediate: bool = True):
        super().__init__(message)
        self.pause_minutes = pause_minutes
        self.pause_reason = pause_reason or message
        self.fatal = fatal
        # False means "this MIGHT be systemic". The server then waits for a
        # run of them before parking the account, instead of stopping on the
        # first. Used for a missing form field, because FineArtAmerica serves
        # two versions of its upload form and one miss usually means we got
        # the other page rather than that the form has changed.
        self.pause_immediate = pause_immediate


def parse_selector(raw: str) -> tuple[str, str]:
    """
    Turn a configured selector string into a Selenium (By, value) pair.

    Prefixes keep the dashboard honest about intent and let a single text
    field express any lookup strategy. Unprefixed values are treated as CSS,
    which is what an admin pasting from devtools will produce.
    """
    value = (raw or "").strip()
    if value.startswith("css:"):
        return By.CSS_SELECTOR, value[4:].strip()
    if value.startswith("xpath:"):
        return By.XPATH, value[6:].strip()
    if value.startswith("name:"):
        return By.NAME, value[5:].strip()
    if value.startswith("id:"):
        return By.ID, value[3:].strip()
    return By.CSS_SELECTOR, value


class MarketplaceUploader:
    """
    Drives one browser session for one account.

    Built per batch and disposed afterwards so a wedged browser can never
    poison the next run.
    """

    def __init__(self, *, account: dict, settings: dict, config: dict,
                 client: PipelineClient, log: Callable[..., None],
                 job_id: Optional[int] = None):
        self.account = account
        self.settings = settings
        self.config = config
        self.client = client
        self.log = log
        self.job_id = job_id

        # ── The profile folder, worked out ONCE ─────────────────────────
        #
        # This used to be derived in start() and read raw in _kill_orphans(),
        # so when an account had no `chrome_profile_dir` set — the normal case
        # — the launcher used a sensible default while the orphan sweeper saw
        # a blank string and gave up. The one piece of code whose whole job is
        # to clear a stuck profile had never run for any account.
        #
        # Keyed on the account ID, not its name. A rename used to silently
        # create a second profile and orphan the first, and two accounts that
        # ever shared a name would have shared one folder — including a new
        # account reusing a banned one's name, which would have inherited the
        # banned account's cookies.
        self.profile_dir = self._profile_path()

        self.selectors: dict[str, str] = account["selectors"]
        self.timings: dict[str, float] = account["timings"]
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
        # The login tab collects Chrome dialogs; work happens in a second
        # tab. See open_work_tab().
        self.login_handle: Optional[str] = None
        self.work_handle: Optional[str] = None

    # ── Helpers ────────────────────────────────────────────────────────────

    def t(self, key: str, default: float = 1.0) -> float:
        try:
            return float(self.timings.get(key, default))
        except (TypeError, ValueError):
            return default

    def sel(self, key: str) -> str:
        value = self.selectors.get(key)
        if not value:
            # A missing selector is a configuration problem, not a transient
            # one — pause so the queue isn't wasted while it's unfixed.
            raise UploadError(
                f"Selector '{key}' is not configured. Set it under "
                f"Pipeline → Upload → Page Selectors.",
                pause_minutes=60,
                pause_reason=f"Missing selector: {key}",
            )
        return value

    def emit(self, message: str, *, level: str = "info",
             progress: Optional[int] = None, note: Optional[str] = None) -> None:
        """Log locally and to the dashboard's Live Console in one call."""
        self.log(message, level=level)
        if self.job_id:
            self.client.job_log(self.job_id, message, level=level,
                                progress=progress, note=note)

    

    def js_click(self, element, *, what: str = "element") -> None:
        """
        Click by dispatching the event on the element itself.

        A normal Selenium click aims at screen coordinates, so anything drawn
        on top — a promo bar, a cookie notice, a browser dialog — intercepts it.
        Worse, the click is often reported as successful while nothing happens,
        which is how a failure surfaces several steps later as a missing
        element on a page you never left.

        FineArtAmerica has a promotional bar and a "design inspiration" banner
        on every page, so this is not hypothetical. Dispatching directly on the
        element ignores all of it, and also ignores scroll position.
        """
        try:
            self.driver.execute_script("arguments[0].click();", element)
        except WebDriverException:
            # Fall back to a real click; better a coordinate click than none.
            try:
                element.click()
            except WebDriverException as e:
                raise UploadError(f"Could not click {what}: {e}")

    def open_work_tab(self) -> None:
        """
        Move to a fresh tab for the actual work, leaving the login tab behind.

        The legacy uploader opened N+1 tabs and deliberately never used the
        first one, because the login tab reliably ends up with a Chrome dialog
        over it. That tool uploaded thousands of images successfully, so the
        pattern is worth keeping even though we now also suppress the dialogs
        at source — belt and braces, and it costs one tab.
        """
        try:
            self.login_handle = self.driver.current_window_handle
            self.driver.switch_to.new_window("tab")
            self.work_handle = self.driver.current_window_handle
            self.emit("Opened a clean tab for uploading (login tab left behind)")
        except WebDriverException as e:
            # Not fatal — carry on in the login tab rather than abandoning the
            # run over a tab we only wanted for hygiene.
            self.emit(f"Could not open a separate work tab ({e}); "
                      f"continuing in the login tab", level="warn")

    def find(self, key: str, *, clickable: bool = False, timeout: Optional[float] = None):
        by, value = parse_selector(self.sel(key))
        wait = WebDriverWait(self.driver, timeout or self.t("element_timeout", 30))
        condition = (EC.element_to_be_clickable((by, value)) if clickable
                     else EC.presence_of_element_located((by, value)))
        try:
            return wait.until(condition)
        except TimeoutException:
            # The single most useful fact when an element is missing is which
            # page we were actually looking at — nine times out of ten the
            # selector is fine and a previous step failed to navigate. Report
            # that inline rather than making the operator open the screenshot.
            try:
                current = self.driver.current_url or "(unknown)"
                title = (self.driver.title or "").strip()[:80]
            except WebDriverException:
                current, title = "(unavailable)", ""

            # Is the element there but in an iframe? A very common cause, and
            # invisible from the error alone.
            frame_hint = ""
            try:
                frames = self.driver.find_elements(By.TAG_NAME, "iframe")
                if frames:
                    frame_hint = (f" The page contains {len(frames)} iframe(s) — "
                                  f"the element may be inside one.")
            except WebDriverException:
                pass

            # ── What IS on the page ──────────────────────────────────────
            #
            # When a marketplace changes its form, the question is always
            # "what is the field called now", and the answer was only
            # obtainable by opening the saved HTML and reading it. Listing
            # the field names here turns a selector break into a
            # copy-and-paste fix: read the name, put it in Page Selectors,
            # re-test. FineArtAmerica has more than one version of the
            # upload form live at once (updateartwork.html and
            # updateartwork2025.html), so this is not a rare event.
            fields = ""
            try:
                names = []
                for tag in ("input", "select", "textarea"):
                    for el in self.driver.find_elements(By.TAG_NAME, tag):
                        name = (el.get_attribute("name")
                                or el.get_attribute("id") or "").strip()
                        if name and name not in names:
                            names.append(name)
                if names:
                    fields = ("\n  fields on the page : "
                              + ", ".join(names[:40])
                              + (" …" if len(names) > 40 else ""))
            except WebDriverException:
                pass

            raise UploadError(
                f"Element '{key}' not found within "
                f"{timeout or self.t('element_timeout', 30):.0f}s.\n"
                f"  selector : {self.sel(key)}\n"
                f"  page now : {current}\n"
                f"  title    : {title}{frame_hint}{fields}\n"
                f"  If the page above isn't the one you expected, an earlier "
                f"step failed to navigate and the selector is fine. Otherwise "
                f"update it under Pipeline → Upload → Page Selectors and re-test.",
                pause_minutes=45,
                pause_reason=f"Selector '{key}' no longer matches",
                pause_immediate=False,
            )

    # ── Bot detection ──────────────────────────────────────────────────────

    def check_for_bot_wall(self, context: str) -> None:
        """
        Look for a challenge page.

        Checked after navigation and login because continuing past one wastes
        the batch and makes the account look worse. Raising with a long pause
        gives the site time to cool off.
        """
        try:
            page = (self.driver.page_source or "").lower()
        except WebDriverException:
            return
        hits = [marker for marker in BOT_MARKERS if marker in page]
        if not hits:
            return
        shot = self.capture_evidence(f"botwall_{context}")
        raise UploadError(
            f"Bot-protection page detected during {context} "
            f"(matched: {', '.join(hits)}). Account paused to cool off.",
            pause_minutes=180,
            pause_reason=f"Bot wall during {context}: {', '.join(hits)}",
            fatal=True,
        )

    def capture_evidence(self, label: str) -> Optional[str]:
        """
        Push a screenshot and the page HTML to the server.

        Stored server-side, not on this disposable node, so it's still
        available in the dashboard when you sit down to work out what broke.
        The screenshot is what the Failures list shows.
        """
        if self.driver is None:
            return None
        path = None
        try:
            png = self.driver.get_screenshot_as_png()
            path = self.client.upload_artifact(
                kind="screenshot", name=f"{label}.png", data=png)
        except Exception:
            pass
        try:
            html = (self.driver.page_source or "").encode("utf-8", "replace")
            self.client.upload_artifact(
                kind="pagesource", name=f"{label}.html", data=html)
        except Exception:
            pass
        return path

    # ── Browser lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        """
        Launch Chrome with the account's persistent profile.

        The profile carries the marketplace session cookie, which is why most
        runs skip the login form entirely. It's disposable: wipe it and the
        next run logs in again.
        """
        profile_dir = self.profile_dir
        slug = Path(profile_dir).name

        # Anything still holding this profile is cleared BEFORE launching, not
        # only when a run ends. The orphan is left behind by the run that
        # crashed; sweeping on the way out never helps the run that inherits
        # the mess.
        self._kill_orphans()
        Path(profile_dir).mkdir(parents=True, exist_ok=True)

        # Fail with something actionable rather than chromedriver's stack dump.
        if " " in str(profile_dir):
            self.emit(
                f"Chrome profile path contains a space ({profile_dir}) — "
                f"chromedriver often fails on these. Set a space-free path in "
                f"the account's 'Chrome profile dir'.",
                level="warn",
            )

        options = Options()
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
        # Unattended box: no visible window, and the flags below are what make
        # headless Chrome behave on a bare Windows VPS.
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Suppress every Chrome-level dialog that can steal focus from the page.
        #
        # These are BROWSER dialogs, not page elements — Selenium can neither
        # see nor dismiss them, and while one is up, clicks on the page beneath
        # can be silently swallowed. The "your password was found in a data
        # breach" prompt is the one that bites here, and it is controlled
        # separately from the password manager itself, so disabling the manager
        # alone (as an earlier version did) was not enough.
        options.add_experimental_option("prefs", {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            "profile.password_manager_leak_detection": False,
            "profile.default_content_setting_values.notifications": 2,
            "autofill.profile_enabled": False,
            "autofill.credit_card_enabled": False,
        })
        options.add_argument("--disable-features=PasswordLeakDetection,AutofillServerCommunication")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-infobars")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        # ── chromedriver's own log, which is where the real reason lives ──
        #
        # "Chrome instance exited. Examine ChromeDriver verbose log to
        # determine the cause" is the most common failure on this box, and
        # until now that log was never written — so the one line that says
        # WHY did not exist anywhere. It is written to the node's temp dir
        # and its tail is attached to the error we send to the server,
        # because an error only readable on an unattended VPS is an error
        # nobody will ever read.
        driver_log = Path(self.config.get("temp_dir", "C:/faa/temp")) / "chromedriver.log"
        try:
            driver_log.parent.mkdir(parents=True, exist_ok=True)
            service = ChromeService(log_output=str(driver_log),
                                    service_args=["--verbose"])
        except Exception:
            service = None            # older Selenium: carry on without it

        def _launch(opts, svc):
            return (webdriver.Chrome(options=opts, service=svc) if svc
                    else webdriver.Chrome(options=opts))

        try:
            self.driver = _launch(options, service)
        except WebDriverException as first_error:
            # ── Clear the profile and try the SAME path again ─────────────
            #
            # There is deliberately no second folder any more. A separate
            # "_fallback" profile meant two directories per account, an orphan
            # whenever a name changed, and a saved session that was never the
            # one actually in use. The profile is disposable by design, so the
            # honest recovery is to throw this one away and rebuild it here.
            #
            # The chromedriver log is reported on THIS failure, not only when
            # the retry also fails. Chromedriver's own advice is "examine the
            # verbose log" and until now a successful retry threw that log
            # away — which is why the same failure repeated for days with no
            # evidence of its cause.
            tail = _tail(driver_log, 12)
            self.emit(
                f"Chrome would not start on {profile_dir} "
                f"({first_error.__class__.__name__}) — clearing that profile "
                f"and trying again."
                + (f"\n--- chromedriver said ---\n{tail}" if tail else ""),
                level="warn",
            )

            import shutil
            shutil.rmtree(profile_dir, ignore_errors=True)
            leftovers = list(Path(profile_dir).glob("*")) if Path(profile_dir).exists() else []
            if leftovers:
                # Half-deleted profiles are self-perpetuating: Chrome refuses
                # the remains, we delete what we can, and the same wreckage is
                # there next run. Say so rather than failing the same way
                # every night in silence.
                self.emit(
                    f"Could not fully clear {profile_dir} — {len(leftovers)} "
                    f"item(s) are locked. Something still has it open; the "
                    f"next run will hit the same wall. Delete the folder by "
                    f"hand, or reboot the machine.",
                    level="error",
                )
            Path(profile_dir).mkdir(parents=True, exist_ok=True)

            try:
                self.driver = _launch(_clone_options(options, profile_dir), service)
                self.emit("Started on a rebuilt profile — you will see one "
                          "extra sign-in this run, then it is back to normal.",
                          level="warn")
            except WebDriverException:
                raise _startup_error(first_error, profile_dir, driver_log)

        self.driver.set_page_load_timeout(90)
        self.wait = WebDriverWait(self.driver, self.t("element_timeout", 30))

        try:
            caps = self.driver.capabilities
            self.emit(
                f"Chrome {caps.get('browserVersion', '?')} / "
                f"chromedriver "
                f"{caps.get('chrome', {}).get('chromedriverVersion', '?').split(' ')[0]}"
            )
        except Exception:
            pass


    def stop(self) -> None:
        """
        Tear the browser down.

        Also sweeps orphaned processes for this profile: in headless mode a
        crashed run leaves invisible chrome.exe holding the profile lock,
        which then breaks the *next* run with "user data directory already in
        use".
        """
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self._kill_orphans()

    # Process names to sweep, covering both platforms. The upload stage is
    # deliberately OS-portable — only the Photoshop stage genuinely requires
    # Windows — so this must not assume .exe. Running uploads on the Linux
    # server while Photoshop runs elsewhere is a supported deployment.
    _BROWSER_PROCESS_NAMES = (
        "chrome.exe", "chromedriver.exe",          # Windows
        "chrome", "chromedriver",                  # Linux
        "google-chrome", "google-chrome-stable",   # Linux packaging variants
    )

    def _profile_path(self) -> str:
        """This account's profile folder. See profile_path_for()."""
        return profile_path_for(self.account, self.config)

    def _kill_orphans(self) -> None:
        profile_dir = self.profile_dir
        if not profile_dir:
            return
        try:
            import psutil
        except ImportError:
            return

        key = os.path.normpath(profile_dir).lower()
        killed = 0
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name not in self._BROWSER_PROCESS_NAMES:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                # Only this profile's processes — never the operator's own browser.
                if key in cmdline:
                    proc.kill()
                    killed += 1
            except Exception:
                continue
        if killed:
            self.emit(f"Cleaned up {killed} orphaned Chrome process(es)")

    # ── Login ──────────────────────────────────────────────────────────────

    def login(self) -> None:
        """
        Authenticate, reusing the saved session when possible.

        Credential entry is conditional on the login form actually being
        present: on most runs the profile cookie is still valid and we skip
        straight through, which is both faster and less conspicuous.
        """
        self.emit("Phase: login", progress=5)
        step = "opening login page"
        try:
            self.driver.get(self.sel("login_url"))
            time.sleep(self.t("page_load_wait", 2))
            self.check_for_bot_wall("login")

            step = "clicking the artist-login link"
            try:
                self.js_click(self.find("artist_login_link", clickable=True, timeout=10),
                              what="artist login link")
                time.sleep(self.t("page_load_wait", 2))
            except UploadError:
                # Not fatal — an existing session often lands past this link.
                self.emit("Login link not present (likely already signed in)")

            # Only fill the form if it's actually there.
            by, value = parse_selector(self.sel("username_field"))
            form_present = True
            try:
                self.driver.find_element(by, value)
            except NoSuchElementException:
                form_present = False

            if form_present:
                step = "entering credentials"
                self.emit("Login form present — entering credentials")
                field = self.find("username_field")
                field.clear()
                field.send_keys(self.account["email"])

                by_p, value_p = parse_selector(self.sel("password_field"))
                password = self.driver.find_element(by_p, value_p)
                password.clear()
                password.send_keys(self.account["password"])

                step = "submitting the login form"
                self.js_click(self.find("login_submit", clickable=True),
                              what="login submit")
                time.sleep(self.t("login_wait", 2))
                self.check_for_bot_wall("login submit")

                # Still on a login form means the credentials were rejected.
                try:
                    self.driver.find_element(by, value)
                    shot = self.capture_evidence("login_rejected")
                    raise UploadError(
                        "Still on the login form after submitting — credentials "
                        "look wrong or the account is locked.",
                        pause_minutes=240,
                        pause_reason="Login rejected — check the stored password",
                        fatal=True,
                    )
                except NoSuchElementException:
                    pass
            else:
                self.emit("Reusing saved session — no credential entry needed")

            step = "navigating to the profile page"
            profile_url = self.account.get("profile_url") or self.selectors.get("control_panel_url")
            if profile_url:
                self.driver.get(profile_url)
                time.sleep(self.t("popup_delay", 2))

            step = "dismissing any popup"
            try:
                by_c, value_c = parse_selector(self.sel("popup_close"))
                self.js_click(self.driver.find_element(by_c, value_c), what="popup close")
                time.sleep(1)
                self.emit("Dismissed an interstitial popup")
            except (NoSuchElementException, UploadError):
                pass

            self.emit("Login OK", level="ok", progress=10)

            # Everything from here happens in a clean tab.
            self.open_work_tab()

        except UploadError:
            raise
        except WebDriverException as e:
            self.capture_evidence("login_error")
            raise UploadError(f"Login failed while {step}: {e}", pause_minutes=30)

    # ── Single image ───────────────────────────────────────────────────────

    def upload_one(self, item: dict, image_path: Path) -> dict[str, Any]:
        """
        Take one image through the full form, logging each phase separately.

        Per-phase logging is the point: when something breaks you can see it
        was the file input rather than the title field, which tells you exactly
        which selector to fix.
        """
        if not image_path.is_file():
            raise UploadError(f"Image not readable at {image_path}. "
                              f"Check the storage mount matches storage_root.")

        # Maintenance is checked FIRST, before anything is filled in. The page
        # looks like a normal 200 and every selector on it is missing, so
        # without this the run reports a cascade of "field not found" failures
        # and burns the account's daily quota discovering the site is down.
        #
        # It pauses rather than fails: this is not a problem with the image.
        if looks_like_maintenance(self.driver.page_source):
            raise UploadError(
                "FineArtAmerica is showing its maintenance page.",
                # Long enough that the node stops hammering a site that is
                # deliberately offline; short enough to resume by itself.
                pause_minutes=30,
                pause_reason="Marketplace is in maintenance — nothing to fix, "
                             "it will resume when the site is back",
                fatal=True,
            )

        timings: dict[str, int] = {}

        def phase(name: str) -> Callable[[], None]:
            started = time.time()

            def done() -> None:
                timings[name] = int((time.time() - started) * 1000)
            return done

        # 1 — open a fresh upload form
        #
        # Prefer navigating straight to the upload URL. Clicking a button on the
        # profile page is what the legacy tool did, and it silently stopped
        # navigating after a site redesign — the click "succeeded", the page
        # never changed, and the run failed several steps later looking for a
        # file input that was never going to be there.
        mark = phase("open_form")
        upload_url = (self.selectors.get("upload_url") or "").strip()

        if upload_url:
            # Manual override, for the day the flow below stops working.
            self.emit(f"  → opening upload form: {upload_url}")
            self.driver.get(upload_url)
            time.sleep(self.t("page_load_wait", 2))
        else:
            # Read the address out of the Upload Image link and go there,
            # rather than clicking it.
            #
            # The link carries a per-session id
            # (…/updateartwork.html?newartwork=true&sessionid=…), so the URL
            # cannot be hardcoded — it has to come from the live page. Reading
            # it also removes a click, and a click that silently fails to
            # navigate is precisely what stranded earlier runs on the profile
            # page with no error.
            profile_url = self.account.get("profile_url")
            if profile_url:
                self.driver.get(profile_url)
                time.sleep(self.t("page_load_wait", 2))

            link = self.find("upload_button")
            href = (link.get_attribute("href") or "").strip()

            if href and not href.lower().startswith("javascript:"):
                self.emit(f"  → opening upload form: {href}")
                self.driver.get(href)
            else:
                # A JavaScript link has no address to follow; dispatch the
                # click on the element instead of at its coordinates.
                self.emit("  → opening upload form (script link — clicking)")
                self.js_click(link, what="upload button")
            time.sleep(self.t("page_load_wait", 2))

        self.check_for_bot_wall("upload form")

        # Confirm we actually landed on the form before going further. Failing
        # here names the real problem instead of surfacing it three steps later
        # as a missing element.
        try:
            landed = self.driver.current_url or ""
        except WebDriverException:
            landed = ""
        marker = (self.selectors.get("still_on_form_marker") or "updateartwork").lower()
        if marker and marker not in landed.lower():
            shot = self.capture_evidence("not_on_upload_form")
            raise UploadError(
                f"Did not reach the upload form.\n"
                f"  expected a URL containing : {marker}\n"
                f"  actually on               : {landed}\n"
                f"  If you were redirected to a login or profile page, the "
                f"session may have expired. If the upload page has simply moved, "
                f"update 'upload_url' under Pipeline → Upload → Page Selectors.",
                pause_minutes=30,
                pause_reason="Cannot reach the upload form",
            )
        mark()

        # 2 — hand the file to the input
        mark = phase("send_file")
        self.emit(f"  → sending file ({image_path.stat().st_size / 1024:.0f} KB)")
        self.find("file_input").send_keys(str(image_path.resolve()))
        time.sleep(self.t("page_load_wait", 2))
        mark()

        # 3 — confirm the upload and wait for the site to ingest it
        mark = phase("confirm_upload")
        self.emit("  → confirming upload")
        self.js_click(self.find("upload_confirm", clickable=True), what="upload confirm")
        time.sleep(self.t("upload_wait", 5))
        self.check_for_bot_wall("image upload")
        mark()

        # 4 — fill the listing form
        mark = phase("fill_form")
        self.emit(f"  → filling form: {item['remote_title']!r}")
        delay = self.t("form_input_delay", 0.4)

        title_field = self.find("title_field")
        title_field.click()
        time.sleep(delay)
        # Select-all + delete rather than clear(): some forms re-populate on
        # clear() and end up with concatenated text.
        title_field.send_keys(Keys.CONTROL + "a")
        title_field.send_keys(Keys.DELETE)
        time.sleep(delay)
        title_field.send_keys(item["remote_title"])
        time.sleep(delay)

        if item.get("keywords"):
            by_k, value_k = parse_selector(self.sel("keywords_field"))
            keywords = self.driver.find_element(by_k, value_k)
            keywords.click()
            time.sleep(delay)
            # Append rather than replace — the site pre-fills keywords from
            # the title and those are worth keeping.
            keywords.send_keys(Keys.CONTROL + Keys.END)
            time.sleep(delay)
            keywords.send_keys(item["keywords"])
            time.sleep(delay)

        if item.get("description"):
            by_d, value_d = parse_selector(self.sel("description_field"))
            description = self.driver.find_element(by_d, value_d)
            description.click()
            time.sleep(delay)
            description.send_keys(Keys.CONTROL + "a")
            description.send_keys(Keys.DELETE)
            time.sleep(delay)
            description.send_keys(item["description"])
            time.sleep(delay)
        mark()

        # 5 — submit and verify
        mark = phase("submit")
        self.emit("  → submitting")
        self.js_click(self.find("submit_button", clickable=True), what="submit")
        time.sleep(self.t("submit_wait", 2.5))

        # A silent submit failure leaves us on the form. Detecting it here is
        # what stops the legacy tool's habit of recording phantom successes.
        marker = self.selectors.get("still_on_form_marker")
        current_url = self.driver.current_url or ""
        if marker and marker.lower() in current_url.lower():
            shot = self.capture_evidence(f"submit_failed_{item['tracking_id']}")
            raise UploadError(
                f"Submit did not complete — still on the form ({current_url}). "
                f"The site may have rejected the image or a required field.",
                pause_minutes=0,
            )
        mark()

        # FAA rejects an unrenderable title with an HTML error page, not an
        # HTTP error, so a "successful" submit can still have listed nothing.
        # Validation before dispatch should mean we never see this — treat it
        # as a real failure rather than reporting a listing that isn't there.
        if "only a-z in your artwork title" in (self.driver.page_source or "").lower():
            raise UploadError(
                f"FineArtAmerica rejected the title {item['remote_title']!r}: "
                f"it contains no characters the site accepts.")

        self.emit(f"  ✓ live: {item['remote_title']}", level="ok")
        return {"timings": timings, "final_url": current_url}


class UploadStage:
    """
    Drives the upload stage: claim → login once → upload each image → report.

    Reporting happens per image, immediately, so a crash at image 30 of 40
    keeps the first 29 recorded as uploaded.
    """

    def __init__(self, client: PipelineClient, config: dict,
                 log: Callable[..., None]):
        self.client = client
        self.config = config
        self.log = log
        self.temp_dir = Path(config.get("temp_dir") or "C:/faa/temp")

    def _resolve_image(self, item: dict, storage_root: Path) -> Path:
        """
        Locate the processed image.

        Reads straight off the mounted storage box normally — no transfer at
        all. Falls back to an HTTP fetch if the mount is missing, so a
        misconfigured drive letter makes uploads slow rather than impossible.
        """
        candidate = _drive_root(storage_root) / item["storage_path"]

        # ── Any failure to LOOK at the file means "not available" ─────────
        #
        # `is_file()` does not merely return False on a network drive that
        # cannot be reached: it RAISES. An unmapped or unauthenticated S:
        # gives WinError 1326 ("user name or password is incorrect"), which
        # escaped this method, escaped the per-item handler as an unexpected
        # error, and reported every image as failed — while the HTTP fallback
        # sitting three lines below was never reached.
        #
        # The fallback only counts if nothing can jump over it. So the test
        # is wrapped, and the reason is carried into the message: "the mount
        # is broken" and "the file genuinely is not there" need different
        # fixes and must not read the same.
        reason = ""
        try:
            if candidate.is_file():
                return candidate
        except OSError as e:
            reason = f" ({e.strerror or e})"

        self.log(
            f"Not readable on the storage mount ({candidate}){reason} — "
            f"fetching over HTTP instead",
            level="warn",
        )
        fallback = self.temp_dir / "upload_cache" / item["filename"]
        self.client.download_processed(item["tracking_id"], fallback)
        return fallback

    def run_batch(self, *, job_id: Optional[int] = None,
                  account_id: Optional[int] = None,
                  project_id: Optional[int] = None) -> dict:
        claim = self.client.claim_upload_batch(
            account_id=account_id, project_id=project_id)

        account = claim.get("account")
        items = claim.get("items") or []
        if not account or not items:
            self.log("Nothing to upload (no work, or every account is at its daily cap)")
            return {"claimed": 0, "uploaded": 0, "failed": 0}

        settings = claim["settings"]
        quota = claim.get("quota") or {}
        storage_root = Path(self.config.get("storage_root_override")
                            or settings["storage_root"])

        self.log(
            f"Claimed {len(items)} image(s) for '{account['name']}' "
            f"({account['target_site']}) · quota {quota.get('used')}/{quota.get('limit')}"
        )
        if job_id:
            self.client.job_log(
                job_id,
                f"Uploading {len(items)} image(s) to {account['name']} "
                f"· {quota.get('remaining')} left of today's {quota.get('limit')}",
                progress=2,
            )

        uploader = MarketplaceUploader(
            account=account, settings=settings, config=self.config,
            client=self.client, log=self.log, job_id=job_id,
        )

        uploaded = failed = 0
        gap = float(account["timings"].get("between_images", 3))

        try:
            # ── Getting as far as a logged-in browser ────────────────────
            #
            # This is reported per ITEM even though it is one account-wide
            # problem, because the items are already CLAIMED by the time we
            # get here. If this raises and we simply let it propagate, every
            # one of them is left sitting at 'uploading' with nothing said
            # about why — the error goes to this node's local console, which
            # on an unattended VPS nobody ever reads.
            #
            # The visible result was a pipeline that looked alive and moved
            # nothing: the stale-claim reaper would release the batch after
            # the timeout, the next cycle would claim it again, fail here
            # again, and strand it again, forever. Chrome failing to launch
            # and a wrong marketplace password both look exactly like that
            # from the dashboard.
            #
            # Paused rather than retried, because nothing about the next
            # attempt would be different.
            try:
                uploader.start()
                uploader.login()
            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
                shot = uploader.capture_evidence("fail_startup")
                uploader.emit(f"Could not start an upload session — {detail}",
                              level="error")
                for item in items:
                    self.client.report_upload_failure(
                        tracking_id=item["tracking_id"],
                        error=f"Upload session could not start — {detail}",
                        screenshot=shot,
                        pause_minutes=30,
                        pause_reason="Could not open or log in to the marketplace",
                    )
                return {"claimed": len(items), "uploaded": 0,
                        "failed": len(items), "account": account["name"]}

            for index, item in enumerate(items, start=1):
                progress = 10 + int(index / len(items) * 88)
                uploader.emit(
                    f"[{index}/{len(items)}] {item['remote_title']}",
                    progress=progress, note=f"{uploaded} up, {failed} failed",
                )
                try:
                    image_path = self._resolve_image(item, storage_root)
                    uploader.upload_one(item, image_path)
                    # Report immediately — never batch the bookkeeping.
                    self.client.report_uploaded(tracking_id=item["tracking_id"])
                    uploaded += 1

                except UploadError as e:
                    failed += 1
                    shot = uploader.capture_evidence(f"fail_{item['tracking_id']}")
                    uploader.emit(f"  ✗ {e}", level="error")
                    self.client.report_upload_failure(
                        tracking_id=item["tracking_id"], error=str(e),
                        screenshot=shot, pause_minutes=e.pause_minutes,
                        pause_reason=e.pause_reason,
                        pause_immediate=e.pause_immediate,
                    )
                    if e.fatal:
                        uploader.emit(
                            "Stopping this run — the problem affects the whole account.",
                            level="error",
                        )
                        break

                except Exception as e:
                    failed += 1
                    shot = uploader.capture_evidence(f"fail_{item['tracking_id']}")
                    uploader.emit(f"  ✗ unexpected: {e}", level="error")
                    self.client.report_upload_failure(
                        tracking_id=item["tracking_id"],
                        error=f"{type(e).__name__}: {e}", screenshot=shot,
                    )

                # A human-ish gap between images. Cheap insurance against rate
                # heuristics, and irrelevant to an unattended box.
                if index < len(items) and gap > 0:
                    time.sleep(gap)

        finally:
            uploader.stop()

        summary = {"claimed": len(items), "uploaded": uploaded, "failed": failed,
                   "account": account["name"]}
        self.log(f"Upload run done — {uploaded} uploaded, {failed} failed")
        return summary

    # ── Test hook ──────────────────────────────────────────────────────────

    def remove_profile(self, job_id: int, payload: dict) -> dict:
        """
        Delete one deleted account's Chrome profile folder.

        ════════════════════════════════════════════════════════════════════
        NARROW ON PURPOSE
        ════════════════════════════════════════════════════════════════════
        This names ONE folder, decided by the server at the moment you press
        delete. It never lists the directory and never decides for itself what
        looks unused.

        The wider design — "ask which accounts are alive, delete anything
        else" — was rejected deliberately. It would tidy up more, but a server
        that ever answered with a short list would wipe the sessions of live
        accounts: a hundred fresh logins at once, and a real chance of the
        marketplace starting to challenge us again, silently, overnight. The
        worst this version can do is leave one folder behind, which is exactly
        where things already stood.

        Two guards, because an instruction should survive a sanity check at
        the end that carries it out:

          * the path must sit INSIDE the profiles folder, unless the account
            had an explicit profile directory set — in which case that exact
            path, and nothing above it, is what gets removed
          * a missing folder is a SUCCESS, not an error. Deleting something
            already gone is the outcome we wanted.
        """
        import shutil

        # Worked out HERE, with the same function that launches Chrome, from
        # the identity the server sent. One rule, one place: the server has no
        # business knowing this machine's folder layout.
        target = Path(profile_path_for(
            {"id": payload.get("account_id"),
             "name": payload.get("name"),
             "chrome_profile_dir": payload.get("chrome_profile_dir")},
            self.config,
        ))
        root = profiles_root(self.config).resolve()
        explicit = bool((payload.get("chrome_profile_dir") or "").strip())

        try:
            resolved = target.resolve()
        except OSError:
            resolved = target

        # ── Never delete a CONTAINER, however we were told to ────────────
        #
        # This guard applies even when the account named its own profile
        # folder. An earlier version trusted an explicit path completely, and
        # a test pointed one at the profiles folder itself: it cheerfully
        # deleted every account's session in one go. An instruction that
        # would destroy other accounts' work is refused no matter who wrote
        # it.
        scratch = Path(self.config.get("temp_dir", "C:/faa/temp")).resolve()
        forbidden = {root, scratch, resolved.anchor and Path(resolved.anchor)}
        if resolved in forbidden or resolved in root.parents or resolved in scratch.parents:
            raise ValueError(
                f"Refusing to delete {resolved}: that is a folder holding "
                f"other things, not one account's profile.")

        if not explicit:
            # A derived path must additionally sit INSIDE the profiles folder.
            if root not in resolved.parents:
                raise ValueError(
                    f"Refusing to delete {resolved}: it is not inside {root}.")

        if not resolved.exists():
            self.log(f"Profile {resolved} was already gone — nothing to do.")
            return {"removed": False, "path": str(resolved), "note": "already gone"}

        size = 0
        try:
            size = sum(f.stat().st_size for f in resolved.rglob("*") if f.is_file())
        except OSError:
            pass

        shutil.rmtree(resolved, ignore_errors=True)
        if resolved.exists():
            # Reported rather than silently half-done: a locked profile that
            # keeps coming back is worth knowing about.
            raise RuntimeError(
                f"Could not fully delete {resolved} — something still has it "
                f"open. Delete it by hand, or reboot the machine.")

        self.log(f"Deleted profile {resolved} ({size // 1024} KB)", level="ok")
        return {"removed": True, "path": str(resolved), "bytes": size}

    def read_earnings(self, job_id: int, payload: dict) -> dict:
        """
        Fetch an account's ledger pages in the SAME browser the uploader uses.

        ════════════════════════════════════════════════════════════════════
        WHY THIS RUNS HERE AND NOT ON THE SERVER
        ════════════════════════════════════════════════════════════════════
        The server tried to read these pages with a plain HTTP client and got
        "Verify Visitor — Are you human?". This node does not, because it
        arrives in a real Chrome carrying the account's own profile — the
        same profile that already cleared that challenge once and holds the
        cookie proving it. Uploading and reading revenue are two things one
        logged-in browser can do; they were never two problems.

        ════════════════════════════════════════════════════════════════════
        THE SERVER DECIDES WHEN TO STOP
        ════════════════════════════════════════════════════════════════════
        "Stop at the first row we already have" needs the database, so this
        loop asks after every page instead of guessing a page count. That
        also means a page is STORED the moment it is read: if this node dies
        halfway through a first-time backfill, everything up to that point is
        already saved and tomorrow simply carries on.

        This node parses nothing. It fetches HTML and posts it back.
        """
        account = payload["account"]
        settings = payload["settings"]
        pages = payload.get("pages") or []
        max_pages = int(payload.get("max_pages") or 25)

        uploader = MarketplaceUploader(
            account=account, settings=settings, config=self.config,
            client=self.client, log=self.log, job_id=job_id,
        )

        fetched = 0
        try:
            # Same three exits as run_batch (rule: claimed work must always
            # end in a reported state). A failure here is reported against
            # the JOB, which is what the dashboard is watching.
            uploader.start()
            uploader.login()

            for spec in pages:
                kind = spec.get("kind") or "page"
                url = spec.get("url")
                for page_no in range(1, max_pages + 1):
                    uploader.emit(f"{kind}: page {page_no}",
                                  progress=min(95, 5 + fetched * 4))
                    uploader.driver.get(url)
                    html = uploader.driver.page_source
                    fetched += 1

                    verdict = self.client.post(
                        "/earnings/page",
                        {"job_id": job_id, "account_id": account["id"],
                         "kind": kind, "page": page_no, "url": url,
                         "html": html},
                    )
                    if not verdict.get("more"):
                        break
                    url = verdict.get("next_url")
                    if not url:
                        break
        finally:
            uploader.stop()

        return {"pages_fetched": fetched, "account": account["name"]}

    def test_upload(self, job_id: int, payload: dict) -> dict:
        """
        Walk exactly one image through the whole flow, phase by phase.

        Nothing is marked uploaded: this exists to tell you *where* the flow
        breaks after a selector or timing change, in about a minute, without
        consuming a queue item or a slot in the daily quota.
        """
        account = payload["account"]
        settings = payload["settings"]
        storage_root = Path(self.config.get("storage_root_override")
                            or settings["storage_root"])

        item = {
            "tracking_id":  payload["tracking_id"],
            "remote_title": payload["remote_title"],
            "keywords":     payload.get("keywords", ""),
            "description":  payload.get("description", ""),
            "storage_path": payload["storage_path"],
            "filename":     payload["filename"],
        }

        self.client.job_log(job_id, [
            f"Account: {account['name']} ({account['target_site']})",
            f"Listing title: {item['remote_title']}",
            f"Keywords: {item['keywords']}",
            f"Description: {(item['description'] or '')[:120]}"
            + ("…" if len(item.get("description") or "") > 120 else ""),
            f"Image: {item['storage_path']}",
            "This is a dry run — nothing will be marked uploaded.",
        ], progress=3)

        uploader = MarketplaceUploader(
            account=account, settings=settings, config=self.config,
            client=self.client, log=self.log, job_id=job_id,
        )

        try:
            uploader.start()
            uploader.login()
            image_path = self._resolve_image(item, storage_root)
            self.client.job_log(
                job_id, f"Image resolved to {image_path}", progress=20)
            result = uploader.upload_one(item, image_path)
            self.client.job_log(
                job_id, "All phases completed successfully.",
                level="ok", progress=100)
            return {"phases_ms": result["timings"], "final_url": result["final_url"],
                    "dry_run": True}
        finally:
            uploader.stop()
