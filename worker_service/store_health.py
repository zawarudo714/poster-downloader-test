"""
Marketplace listing health, on the node: scan, deactivate, reactivate.

════════════════════════════════════════════════════════════════════════════
WHAT EACH STAGE ACTUALLY DOES TO THE SITE
════════════════════════════════════════════════════════════════════════════
SCAN — no login, no account profile, nothing signed in.

    1. Walk the public store listing over plain HTTP:
           /user/<name>?page=1,2,3…   -> every /t-shirt/<id>-<slug> link
       The numeric ID comes free in that address. Nothing extra is fetched
       to obtain it, and it is the key everything downstream matches on.
    2. Fetch each design page over plain HTTP for its title and primary tag.
    3. Search the PUBLIC site in a real browser for that tag, and look for
       our design ID among the results.

    Only step 3 NEEDS a browser: TeePublic's search results are not in the
    raw HTML. Steps 1 and 2 are ordinary requests, which is why the bulk of
    a scan costs almost nothing — but they fall back to the browser when
    refused, because TeePublic answered the very first store page with HTTP
    403 on the first real run. Cheap must not mean fragile.

DEACTIVATE — signed in, using that account's own Chrome profile.

    Go to the design's own page and press the Deactivate button in its
    MANAGE bar. That control is:

        <form class="button_to" method="post"
              action="/designs/86734220/deactivate">
          <button type="submit">Deactivate</button>
          <input type="hidden" name="authenticity_token" value="…">
        </form>

    A form POST carrying a one-time token — which is why navigating to that
    address directly returns 404, and why the button has to be pressed on a
    freshly loaded page. It is also why the old tool's `a[href*='/deactivate']`
    finds nothing here: this is a <button>, not a link.

REACTIVATE — signed in, same profile.

    /designs/<id>/edit -> tick the terms box -> press publish.

════════════════════════════════════════════════════════════════════════════
EVERY MATCH IS BY NUMERIC ID
════════════════════════════════════════════════════════════════════════════
Never by title, never by URL string. The previous tool compared URLs, and a
design sitting on page one of the results was reported MISSING because the
store's copy of the link carried `?store_id=4129428` and the search result's
copy did not. An integer cannot differ by a query parameter, a relative
path, a renamed slug or a trailing slash.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import requests

from .uploader import (MarketplaceUploader, UploadError,
                       report_wall_result)

BASE = "https://www.teepublic.com"

# /t-shirt/86734220-tomb-raider  ->  86734220
DESIGN_ID = re.compile(r"/t-shirt/(\d+)-")

def new_session() -> requests.Session:
    """
    A plain session, with NO headers set.

    Deliberately bare, because that is what the owner's working scanner used
    and it read these pages successfully for months. An earlier version here
    set a Chrome User-Agent and nothing else, and TeePublic answered the very
    first store page with HTTP 403 — a request claiming to be Chrome while
    sending none of the other headers Chrome sends is a worse signature than
    an honest one.

    Rule: copy the request that is known to work before inventing one.
    """
    return requests.Session()


def fetch(url: str, session: requests.Session, driver=None) -> Optional[str]:
    """
    Page text, over plain HTTP — falling back to the browser if refused.

    Most of a scan is cheap precisely because these pages are ordinary HTML.
    But "cheap" must not mean "fragile": if the marketplace declines to talk
    to a plain client, the browser is already open a few lines away and it
    gets the same page. One page loaded slowly beats an account skipped.
    """
    try:
        reply = session.get(url, timeout=30)
        if reply.status_code == 200:
            return reply.text
        blocked = reply.status_code in (401, 403, 429)
    except requests.RequestException:
        blocked = True

    if not blocked or driver is None:
        return None
    try:
        driver.get(url)
        time.sleep(1.5)
        return driver.page_source or None
    except Exception:
        return None


def design_id_from(url: str) -> Optional[str]:
    """The numeric ID out of a design address, or None if it is not one."""
    match = DESIGN_ID.search(urlparse(url).path)
    return match.group(1) if match else None


def search_url(tag: str, page: int) -> str:
    """
    Page N of a public search, BUILT rather than followed.

    ════════════════════════════════════════════════════════════════════════
    WHY NOT FOLLOW THE "NEXT" LINK
    ════════════════════════════════════════════════════════════════════════
    Because there is not always one. TeePublic's pager shows numbers up to 7
    and then stops offering the next number — page 7 has no "8" on it, only
    an arrow, and the arrow is not always a plain link either. Following
    `a[rel="next"]` therefore works perfectly for six pages and then silently
    gives up, so EVERY design whose match sits beyond page 7 reads MISSING.

    The owner's own scanner hit exactly this and special-cased page 7 in
    place. That patched one instance of the problem; building the address
    removes the whole class of it, because the address is a plain pattern the
    site publishes and it works for page 8 and page 400 alike.

    Page 1 carries no `page=` parameter, matching what the site itself
    produces — copied rather than assumed.
    """
    query = quote_plus(tag)
    if page <= 1:
        return f"{BASE}/t-shirts?query={query}"
    return f"{BASE}/t-shirts?page={page}&query={query}"


# ════════════════════════════════════════════════════════════════════════════
#  READING THE STORE — plain HTTP, no browser
# ════════════════════════════════════════════════════════════════════════════

def list_designs(store_url: str, session: requests.Session,
                 emit: Callable[[str], None], driver=None) -> list[dict]:
    """
    Every design in a store, with its ID. Pages until a page has none.

    Public pages, so no login and no profile. Bounded at 200 pages so a
    listing that starts repeating itself cannot loop forever — a marketplace
    that ignores `?page=` and serves page 1 every time is a real failure mode
    and would otherwise run until someone noticed.
    """
    from bs4 import BeautifulSoup

    found: dict[str, dict] = {}
    for page in range(1, 201):
        html = fetch(f"{store_url}?page={page}", session, driver)
        if html is None:
            emit(f"  store page {page}: could not be read, stopping")
            break

        soup = BeautifulSoup(html, "html.parser")
        links = soup.select('a[href*="/t-shirt/"]')
        added = 0
        for link in links:
            href = (link.get("href") or "").strip()
            if not href:
                continue
            url = urljoin(BASE, href)
            did = design_id_from(url)
            if did and did not in found:
                found[did] = {"design_id": did, "url": url.split("?")[0]}
                added += 1

        if not links or not added:
            break
        emit(f"  store page {page}: {added} new ({len(found)} so far)")
    return list(found.values())


def design_details(url: str, session: requests.Session, driver=None) -> tuple:
    """
    (search tag, title, error). Plain HTTP — the design page is public.

    The PRIMARY TAG is what we search for, not the title: it is the term the
    marketplace itself files the design under, so it is the search a customer
    would realistically make.
    """
    from bs4 import BeautifulSoup

    html = fetch(url, session, driver)
    if html is None:
        return None, None, "page could not be read"

    soup = BeautifulSoup(html, "html.parser")
    strip = lambda t: re.sub(r"\s+T-Shirts?$", "", t, flags=re.I).strip()

    tag_el = soup.find("h2", class_="m-design-details__primary-tag")
    title_el = soup.find("h1", class_="h__h1--sm")
    tag = strip(tag_el.get_text(strip=True)) if tag_el else None
    title = strip(title_el.get_text(strip=True)) if title_el else None

    if not tag or not title:
        missing = " and ".join(
            x for x in (None if tag else "tag", None if title else "title") if x)
        return tag, title, f"page has no {missing}"
    return tag, title, None


# ════════════════════════════════════════════════════════════════════════════
#  THE SEARCH CHECK — the only part that needs a browser
# ════════════════════════════════════════════════════════════════════════════

def appears_in_search(uploader, *, tag: str, design_id: str, max_pages: int,
                      emit: Callable[[str], None], wall: dict) -> bool:
    """
    Does this design turn up when a customer searches its own primary tag?

    Matched on the ID found in each result's address. A visible design is
    normally on page one, so the common case is fast; a missing one is what
    pays the full page cost, which is why a scan takes longer the worse
    things are.

    ════════════════════════════════════════════════════════════════════════
    THE WALL IS CHECKED ONLY WHEN A PAGE LOOKS EMPTY
    ════════════════════════════════════════════════════════════════════════
    The interstitial can appear on a search page too. But asking "is this the
    wall?" before every page would cost the settling delay on every one of
    several thousand designs — hours added to a scan to catch something that
    normally happens once per browser.

    So the cheap test comes first: no results found. THAT is when the two
    explanations diverge, and telling them apart matters enormously — "no
    results" means the design is missing and gets deactivated, while "the
    wall" means we never looked. Getting that backwards would deactivate a
    healthy catalogue.

    The site's own logo settles it: every ordinary page has it, the wall has
    none.
    """
    from bs4 import BeautifulSoup
    from selenium.common.exceptions import WebDriverException

    driver = uploader.driver

    for page in range(1, max_pages + 1):
        url = search_url(tag, page)
        try:
            driver.get(url)
        except WebDriverException as e:
            emit(f"      search page {page} would not load: {e}")
            return False
        time.sleep(1.5)

        soup = BeautifulSoup(driver.page_source or "", "html.parser")
        results = soup.select('a[href*="/t-shirt/"]')

        # Empty AND no logo => we are looking at the wall, not at an empty
        # result set. Clear it and read the same page again before believing
        # anything about this design.
        if not results and wall.get("html_markers") \
                and not uploader.page_has_markup(wall["html_markers"]):
            emit("      the wall is in the way — clearing it")
            uploader.clear_wall(
                [], wall.get("paths") or [],
                wait_s=wall.get("wait_s", 5),
                attempts=wall.get("attempts", 3),
                on_result=wall.get("on_result"),
                html_markers=wall["html_markers"])
            driver.get(url)
            time.sleep(1.5)
            soup = BeautifulSoup(driver.page_source or "", "html.parser")
            results = soup.select('a[href*="/t-shirt/"]')

        for link in results:
            if design_id_from(urljoin(BASE, link.get("href") or "")) == design_id:
                return True

        # No results at all means we have run off the end of the list. Note
        # this is the ONLY stop condition: we do not follow a "next" link,
        # because there is not always one. See search_url.
        if not results:
            return False
    return False


# ════════════════════════════════════════════════════════════════════════════
#  THE STAGES
# ════════════════════════════════════════════════════════════════════════════

class StoreHealthStage:
    """Runs the three stages. One instance per job, like the other stages."""

    def __init__(self, client, config: dict, log):
        self.client = client
        self.config = config
        self.log = log

    # ── Stage 1: scan ───────────────────────────────────────────────────
    def scan(self, job_id: int, payload: dict) -> dict:
        """
        Check every design on every account.

        Accounts run in parallel threads, each holding ONE browser open for
        its whole account. The owner's original script launched and quit a
        whole Chrome per design — 1,881 designs meant 1,881 launches, which
        at 3-5 seconds each is over two hours of doing nothing but starting
        browsers.
        """
        accounts = payload.get("accounts") or []
        run_id = payload["run_id"]
        workers = max(1, int(payload.get("parallel") or 3))
        settings = payload.get("settings") or {}
        # Everything about the interstitial, decided by the server. The node
        # does not know which marketplaces have one.
        wall = {
            "html_markers": payload.get("wall_html_markers") or [],
            "paths":        payload.get("wall_paths") or [],
            "wait_s":       float(payload.get("wall_wait_s") or 5),
            "attempts":     int(payload.get("wall_max_attempts") or 3),
            "on_result":    lambda pid, ok: report_wall_result(
                                    self.client, pid, ok),
        }
        max_pages = int(payload.get("max_search_pages") or 25)
        delay = float(payload.get("delay_s") or 1)
        limit = int(payload.get("limit_per_account") or 0)

        self.client.job_log(
            job_id,
            [f"Scanning {len(accounts)} account(s), {workers} at a time.",
             "No sign-in needed — these are public pages."],
            progress=2)

        results = {"checked": 0, "missing": 0, "errors": 0}
        failures: list[str] = []
        lock = threading.Lock()
        queue = list(accounts)
        # Set when the SERVER says the run is no longer scanning — the admin
        # pressed stop. Shared across account threads so one of them hearing
        # it stops all of them, rather than each discovering it separately
        # one design later.
        stop = threading.Event()

        def emit(line: str) -> None:
            self.log(line)
            try:
                self.client.job_log(job_id, line)
            except Exception:
                pass

        def worker() -> None:
            while True:
                if stop.is_set():
                    return
                with lock:
                    if not queue:
                        return
                    account = queue.pop(0)
                try:
                    got = self._scan_account(job_id, run_id, account,
                                             max_pages, delay, emit, settings,
                                             wall, stop, limit)
                    with lock:
                        for key in results:
                            results[key] += got.get(key, 0)
                except Exception as e:
                    detail = f"{type(e).__name__}: {e}"
                    emit(f"✗ {account.get('name')}: {detail}")
                    with lock:
                        results["errors"] += 1
                        failures.append(f"{account.get('name')}: {detail}")

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(min(workers, len(queue)))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # ── AN ACCOUNT THAT BLEW UP IS NOT A FINISHED SCAN ───────────────
        #
        # This used to swallow the exception into a counter and then post
        # stage-done regardless. A missing library therefore produced a job
        # that logged one red line, reported "Job finished", and advanced the
        # run to "waiting for you" with ZERO designs checked — so the screen
        # would have said "nothing is missing" and been believed.
        #
        # A partial scan is worse than none, because the missing list it
        # shows is silently incomplete. So: any account failing fails the
        # whole stage, the run is marked failed with the real reason, and
        # the pipeline is released rather than held by a run going nowhere.
        if failures:
            self.client.post("/store/stage-done", {
                "run_id": run_id, "stage": "scan",
                "error": "; ".join(failures[:5]),
            })
            raise RuntimeError(
                f"{len(failures)} account(s) could not be scanned: "
                + "; ".join(failures[:5]))

        # ── SAY WHETHER IT ACTUALLY FINISHED ─────────────────────────────
        #
        # "The job ended" and "the scan finished" are different facts, and
        # the server cannot tell them apart from the outside. A stop or a
        # pause ends the job with everything reported and no failure — which
        # read as a clean finish, so the run advanced to the review gate off
        # a scan that had covered 199 designs of 1,543. On an automatic run,
        # resuming from there would have gone straight to DEACTIVATING.
        #
        # So the node says which one it was. It is the only thing that knows.
        self.client.post("/store/stage-done", {
            "run_id": run_id, "stage": "scan",
            "partial": bool(stop.is_set()),
        })
        return results

    def _scan_account(self, job_id: int, run_id: int, account: dict,
                      max_pages: int, delay: float,
                      emit: Callable[[str], None],
                      settings: dict, wall: dict,
                      stop: threading.Event, limit: int = 0) -> dict:
        """One account, one browser, held open for the whole account."""
        name = account.get("name")
        store_url = (account.get("store_url") or "").split("?")[0].rstrip("/")
        session = new_session()

        # A throwaway profile: scanning is public, so it must never touch the
        # account's real signed-in session. Nothing here can log us out.
        uploader = MarketplaceUploader(
            account={**account, "chrome_profile_dir": "",
                     "id": f"scan_{account['id']}"},
            settings=settings, config=self.config,
            client=self.client, log=self.log, job_id=None)

        counts = {"checked": 0, "missing": 0, "errors": 0}
        try:
            # Started BEFORE the listing is read, so it can stand in when
            # plain HTTP is refused — which is exactly what happened on the
            # first real run, with HTTP 403 on store page 1.
            uploader.start()

            # Meet the wall ONCE, up front, on a page we do not care about.
            # Dismissing it sets whatever the site remembers, so the rest of
            # this account's thousands of pages go straight through — and the
            # per-page check below stays a rare fallback rather than a
            # five-second tax on every design.
            if wall.get("html_markers"):
                try:
                    uploader.driver.get(store_url or BASE)
                    uploader.clear_wall(
                        [], wall.get("paths") or [],
                        wait_s=wall.get("wait_s", 5),
                        attempts=wall.get("attempts", 3),
                        on_result=wall.get("on_result"),
                        html_markers=wall["html_markers"])
                except Exception as e:
                    # Not fatal here. If the wall is genuinely blocking, the
                    # first search page will say so with the same check.
                    emit(f"  {name}: could not clear the wall up front ({e})")

            emit(f"→ {name}: reading the store listing")
            listed = list_designs(store_url, session, emit, uploader.driver)
            if not listed:
                raise RuntimeError(
                    f"No designs found at {store_url}. Check the store "
                    f"address on the TeePublic tab.")

            # ── THE SERVER DECIDES WHAT TO CHECK ─────────────────────────
            #
            # We post the whole listing; it reconciles that against the
            # catalogue — what is new, what came back, what the marketplace
            # no longer shows — and answers with the designs actually worth
            # checking. That is where a missing-only recheck and the owner's
            # exclusions are applied.
            #
            # None of that is decided here on purpose. The node holds no
            # policy, and "which designs matter" is policy.
            reply = self.client.post("/store/catalogue", {
                "run_id": run_id, "account_id": account["id"],
                "designs": listed,
            }) or {}
            changed = reply.get("changes") or {}
            if changed.get("added") or changed.get("removed") or changed.get("returned"):
                emit(f"  {name}: {changed.get('added', 0)} new, "
                     f"{changed.get('returned', 0)} back, "
                     f"{changed.get('removed', 0)} no longer listed")

            designs = reply.get("check") or []
            if reply.get("stop"):
                stop.set()
                emit(f"  {name}: stopped before checking anything.")
                return counts

            if limit:
                designs = designs[:limit]
                emit(f"  {name}: limited to the first {limit} for testing "
                     f"(scan_limit_per_account)")
            emit(f"→ {name}: {len(listed)} designs listed, "
                 f"{len(designs)} to check")

            for index, design in enumerate(designs, start=1):
                if stop.is_set():
                    break
                tag, title, error = design_details(design["url"], session,
                                                   uploader.driver)

                if error:
                    status, note = "error", error
                else:
                    visible = appears_in_search(
                        uploader, tag=tag, design_id=design["design_id"],
                        max_pages=max_pages, emit=emit, wall=wall)
                    status, note = ("visible" if visible else "missing"), None
                    if not visible:
                        counts["missing"] += 1

                if status == "error":
                    counts["errors"] += 1
                counts["checked"] += 1

                # Reported per DESIGN, not per account: a node that dies four
                # hours in keeps everything it already checked. The REPLY is
                # also how a stop reaches us — see the endpoint for why it
                # rides along here rather than on a poll of its own.
                verdict = self.client.post("/store/design", {
                    "run_id": run_id, "account_id": account["id"],
                    "design_id": design["design_id"], "url": design["url"],
                    "title": title, "search_tag": tag,
                    "status": status, "error": note,
                }) or {}
                if verdict.get("stop"):
                    stop.set()
                    emit(f"  {name}: stopped by the dashboard after "
                         f"{counts['checked']} design(s) — everything checked "
                         f"so far is saved.")
                    break

                if index % 10 == 0 or status != "visible":
                    emit(f"  {name} [{index}/{len(designs)}] "
                         f"{title or design['design_id']}: {status}")
                time.sleep(delay)
        finally:
            uploader.stop()

        emit(f"✓ {name}: {counts['checked']} checked, {counts['missing']} missing")
        return counts

    # ── Stages 3 and 5: deactivate / reactivate ─────────────────────────
    def act(self, job_id: int, payload: dict) -> dict:
        """
        Turn designs off, or back on. Signed in, one account per job.

        Both directions share this because they are the same shape: take a
        list of IDs we were given, do one thing to each, and report each one
        individually. The list is never re-derived from the marketplace —
        see the server-side docstring for why reading its inactive page
        would be dangerous.
        """
        run_id = payload["run_id"]
        action = payload["action"]            # deactivate | reactivate
        account = payload["account"]
        designs = payload.get("designs") or []
        html_markers = payload.get("wall_html_markers") or []
        paths = payload.get("wall_paths") or []
        signed_out = payload.get("signed_out_markers") or []
        wall_wait = float(payload.get("wall_wait_s") or 5)
        wall_tries = int(payload.get("wall_max_attempts") or 3)

        uploader = MarketplaceUploader(
            account=account, settings=payload.get("settings") or {},
            config=self.config, client=self.client, log=self.log, job_id=job_id)

        done = failed = 0
        try:
            # start() is the risky step and it sits OUTSIDE the per-design
            # handler, so a Chrome that will not launch throws past every
            # `_report` below. Without the outer catch further down, the run
            # would stay "deactivating" forever — holding Photoshop and
            # uploads — with the screen showing it politely in progress.
            uploader.start()
            for index, design in enumerate(designs, start=1):
                did = design["design_id"]
                label = design.get("title") or did
                uploader.emit(f"[{index}/{len(designs)}] {action} {label}",
                              progress=min(95, int(index / max(1, len(designs)) * 90)))
                try:
                    if action == "deactivate":
                        self._deactivate(uploader, design, html_markers,
                                         paths, signed_out, wall_wait,
                                         wall_tries)
                    else:
                        self._reactivate(uploader, did)
                    self._report(run_id, account["id"], did, action, None)
                    done += 1
                except Exception as e:
                    detail = f"{type(e).__name__}: {e}"
                    uploader.emit(f"  ✗ {label}: {detail}", level="error")
                    self._report(run_id, account["id"], did, action, detail)
                    failed += 1
        except Exception as e:
            # Anything that escaped the per-design handler — almost always
            # start(). Report the stage as failed so the run ENDS and the
            # pipeline is released, then re-raise so the job says so too.
            detail = f"{type(e).__name__}: {e}"
            self.client.post("/store/stage-done", {
                "run_id": run_id, "stage": action,
                "account_id": account["id"],
                "error": f"{account.get('name')}: {detail}",
            })
            raise
        finally:
            uploader.stop()

        self.client.post("/store/stage-done",
                         {"run_id": run_id, "stage": action,
                          "account_id": account["id"]})
        return {"done": done, "failed": failed, "account": account.get("name")}

    def _deactivate(self, uploader, design: dict, html_markers, paths,
                    signed_out, wall_wait, wall_tries) -> None:
        """
        Press Deactivate on the design's own page.

        The button lives in a form whose action carries the design ID, so the
        selector below is ID-verified: on a page showing several designs it
        could not press the wrong one. It is also a <button>, not a link —
        the old tool's `a[href*='/deactivate']` matches nothing here.
        """
        from selenium.webdriver.common.by import By

        did = design["design_id"]
        uploader.driver.get(design["url"])

        # Same wall that stands in front of the earnings page. Detected by
        # the absence of what we came for, exactly as it is there.
        # Detected by the site's own header logo, not by words on the
        # MANAGE bar. One marker covers every page these stages touch —
        # design pages, edit pages, search — and the wall carries none of it.
        if html_markers:
            uploader.clear_wall([], paths,
                                wait_s=wall_wait, attempts=wall_tries,
                                signed_out_markers=signed_out,
                                html_markers=html_markers)
        time.sleep(1)

        selector = f"form[action='/designs/{did}/deactivate'] button[type='submit']"
        try:
            button = uploader.driver.find_element(By.CSS_SELECTOR, selector)
        except Exception:
            raise UploadError(
                f"No Deactivate button for design {did}. Either the session "
                f"is not signed in as the owner, or the design is already "
                f"inactive.")
        uploader.js_click(button, what="deactivate")
        time.sleep(2)

    def _reactivate(self, uploader, design_id: str) -> None:
        """
        Republish from the design's edit page: accept terms, press publish.

        Unchanged from the owner's working tool except that the ID comes from
        our own record of what we deactivated, rather than from scraping the
        marketplace's inactive list.
        """
        from selenium.webdriver.common.by import By

        uploader.driver.get(f"{BASE}/designs/{design_id}/edit")
        time.sleep(2)

        try:
            box = uploader.driver.find_element(By.ID, "terms")
            if not box.is_selected():
                uploader.driver.execute_script(
                    "arguments[0].checked = true;"
                    "arguments[0].dispatchEvent(new Event('change'));", box)
                time.sleep(0.5)
        except Exception:
            pass          # already accepted on this design; not an error

        selector = ("button.publish-and-promote-button"
                    "[name='commit'][value='publish']")
        try:
            publish = uploader.driver.find_element(By.CSS_SELECTOR, selector)
        except Exception:
            raise UploadError(
                f"No publish button on the edit page for design {design_id}.")
        uploader.js_click(publish, what="publish")
        time.sleep(2)

    def _report(self, run_id: int, account_id: int, design_id: str,
                action: str, error: Optional[str]) -> None:
        """
        Say what happened to this one design, immediately.

        Per design rather than per batch: a stage that fell over halfway
        would otherwise leave no record of the twenty it had already turned
        off, and reactivation would then miss exactly those twenty.
        """
        try:
            self.client.post("/store/action", {
                "run_id": run_id, "account_id": account_id,
                "design_id": design_id, "action": action, "error": error,
            })
        except Exception as e:
            self.log(f"Could not report {action} of {design_id}: {e}",
                     level="error")
