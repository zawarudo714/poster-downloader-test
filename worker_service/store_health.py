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


def fetch(url: str, session: requests.Session, driver=None,
          html_markers: Optional[list] = None) -> Optional[str]:
    """
    Page text, over plain HTTP — falling back to the browser if refused.

    Most of a scan is cheap precisely because these pages are ordinary HTML.
    But "cheap" must not mean "fragile": if the marketplace declines to talk
    to a plain client, the browser is already open a few lines away and it
    gets the same page. One page loaded slowly beats an account skipped.

    ════════════════════════════════════════════════════════════════════════
    THE BROWSER FALLBACK CAN LAND ON THE WALL, AND MUST SAY SO
    ════════════════════════════════════════════════════════════════════════
    It has no mouse paths and no way to clear anything — it is one page load
    with nobody watching. What it CAN do is refuse to pass the wall's HTML
    off as the page we asked for: if the site's own header logo is not in
    what came back, the honest answer is "we could not look".

    None already means exactly that to both callers, so a store listing
    reads as an account we could not read rather than an account that has
    lost all its designs.
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
        page = driver.page_source or None
    except Exception:
        return None
    return page if page_is_theirs(page, html_markers) else None


def page_is_theirs(html: Optional[str], html_markers: Optional[list]) -> bool:
    """
    Is this the marketplace's own page, or something standing in front of it?

    A NAMED CALL rather than an inline condition, on purpose: preflight
    checks that every function navigating the browser here also consults the
    wall, and it can only see a call. An inline `any(m in html ...)` reads as
    a guard to a person and as nothing at all to the check — which is the
    same trap as a test that matches its own search term.

    A page with no markers configured is accepted: an unmeasured site should
    not have every read refused.
    """
    if not html:
        return False
    if not html_markers:
        return True
    return any(m in html for m in html_markers)


def _blocked(error: Exception) -> bool:
    """
    Was this something in the WAY, rather than something wrong with the
    design we were acting on?

    ════════════════════════════════════════════════════════════════════════
    THE MOST EXPENSIVE DISTINCTION IN THIS FILE
    ════════════════════════════════════════════════════════════════════════
    Get it wrong in one direction and one bad ten minutes writes permanent
    errors against dozens of healthy designs — 79 of them on 25 Aug, in four
    minutes, while a wall sat in front of every page. Those designs then
    leave the work list, and on the switch-back-ON side that means live
    listings stay hidden and earn nothing.

    Get it wrong in the other and a design that genuinely will not switch is
    retried at the front of every sweep for ever, telling nobody.

    Three things mean "in the way", and they are all properties of the
    ACCOUNT'S BROWSER right now, not of any design:

      · transient      — the wall, a maintenance page, a timeout
      · pause_minutes  — systemic: signed out, credentials refused
      · fatal          — the whole account is finished for this turn

    ONE definition, because the retry loop and the reporting line must agree
    about it. Two copies of this question drifting apart is precisely how a
    design gets retried and blamed at the same time.
    """
    return bool(getattr(error, "transient", False)
                or getattr(error, "pause_minutes", 0)
                or getattr(error, "fatal", False))


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
                 emit: Callable[[str], None], driver=None,
                 html_markers: Optional[list] = None) -> list[dict]:
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
        html = fetch(f"{store_url}?page={page}", session, driver, html_markers)
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


def design_details(url: str, session: requests.Session, driver=None,
                   html_markers: Optional[list] = None) -> tuple:
    """
    (search tag, title, error). Plain HTTP — the design page is public.

    The PRIMARY TAG is what we search for, not the title: it is the term the
    marketplace itself files the design under, so it is the search a customer
    would realistically make.
    """
    from bs4 import BeautifulSoup

    html = fetch(url, session, driver, html_markers)
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
        # Failures the far side will probably not still be having in half an
        # hour — the wall, a maintenance page, a timeout. Tracked apart from
        # the rest so the server can wait and come back rather than throwing
        # away the night.
        transient: list[str] = []
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
                        if getattr(e, "transient", False):
                            transient.append(account.get("name"))

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
                # ALL of them being the far side's problem is very different
                # from one account having a real fault. The first is worth
                # waiting out; the second is not.
                "transient": bool(transient) and len(transient) == len(failures),
                "transient_accounts": transient,
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
            listed = list_designs(store_url, session, emit, uploader.driver,
                                  wall.get("html_markers") or None)
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
                tag, title, error = design_details(
                    design["url"], session, uploader.driver,
                    wall.get("html_markers") or None)

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
        # Set when the server tells us to stop mid-account. Reported at the
        # end so a cut-short account is never mistaken for a finished one.
        cut_short = False
        # Consecutive wall failures. After a run of them it is plainly the
        # account and not the design: the first real attempt cost 97 minutes
        # and 98 failures before one path happened to land. Stopping early
        # and reporting it as transient lets the whole stage wait and come
        # back, instead of grinding through 178 designs at a minute each.
        wall_streak = 0
        give_up_after = int(payload.get("wall_give_up_after") or 5)
        # Immediate second attempts at ONE design after a blocked one. Small
        # on purpose: this covers the wall arriving between two page loads,
        # which clearing it and going again fixes in seconds. A wall that is
        # properly in the way is not solved by trying harder here — that is
        # what the run-level wait is for, and it comes back in half an hour
        # rather than hammering the site now.
        per_design_retries = int(payload.get("design_retries") or 1)
        store_url = account.get("store_url") or account.get("profile_url") or ""
        # TeePublic's own count of switched-off designs, read at both ends of
        # this account's turn. The only figure here that does not come from
        # our own records — see `_read_inactive_count`.
        counted = payload.get("count_check", True)
        try:
            # start() is the risky step and it sits OUTSIDE the per-design
            # handler, so a Chrome that will not launch throws past every
            # `_report` below. Without the outer catch further down, the run
            # would stay "deactivating" forever — holding Photoshop and
            # uploads — with the screen showing it politely in progress.
            uploader.start()

            # ── MEET THE WALL ONCE, NOT ONCE PER DESIGN ──────────────────
            #
            # It is a per-BROWSER thing: the log shows 98 designs failing,
            # one getting through, then 100+ sailing past untouched. Clearing
            # it up front costs five seconds; clearing it per design cost an
            # hour and a half.
            if html_markers:
                try:
                    uploader.driver.get(BASE)
                    uploader.clear_wall([], paths, wait_s=wall_wait,
                                        attempts=wall_tries,
                                        signed_out_markers=signed_out,
                                        html_markers=html_markers)
                except Exception as e:
                    uploader.emit(f"Could not clear the wall up front ({e}) — "
                                  f"carrying on; each design will check.",
                                  level="warn")

            # The marketplace's own figure BEFORE we touch anything. The
            # server compares it with the one taken afterwards; nothing is
            # decided here, because "do these numbers agree" is policy.
            before_count = (
                self._read_inactive_count(uploader, store_url, html_markers,
                                          paths, signed_out, wall_wait,
                                          wall_tries) if counted else None)
            if before_count is not None:
                uploader.emit(f"TeePublic says {before_count} design(s) are "
                              f"switched off before we start.")

            for index, design in enumerate(designs, start=1):
                did = design["design_id"]
                label = design.get("title") or did
                uploader.emit(f"[{index}/{len(designs)}] {action} {label}",
                              progress=min(95, int(index / max(1, len(designs)) * 90)))

                # ── ONE DESIGN, WITH A SECOND GO IF WE WERE BLOCKED ──────
                #
                # A design that failed because something was in the WAY has
                # told us nothing about itself, so giving up on it after one
                # look is throwing work away. A design that failed on a page
                # that was plainly ours has told us everything, and trying
                # again just costs a page load. Only the first is retried,
                # and the difference comes from the header-logo test rather
                # than from guessing.
                error = None
                for attempt in range(per_design_retries + 1):
                    try:
                        if action == "deactivate":
                            self._deactivate(uploader, design, html_markers,
                                             paths, signed_out, wall_wait,
                                             wall_tries)
                        else:
                            self._reactivate(uploader, did, html_markers,
                                             paths, signed_out, wall_wait,
                                             wall_tries)
                        error = None
                        break
                    except Exception as e:
                        error = e
                        # A FATAL error is about the whole account — three
                        # mouse paths already failed, or the session is
                        # signed out. Replaying them two seconds later is
                        # one chance taken twice, which is the mistake the
                        # spaced run-level wait exists to correct.
                        if getattr(e, "fatal", False):
                            break
                        if not _blocked(e):
                            break            # ours, and genuinely broken
                        if attempt < per_design_retries:
                            uploader.emit(
                                f"  blocked on {label} — clearing the way and "
                                f"trying once more", level="warn")

                if error is None:
                    stop = self._report(run_id, account["id"], did, action,
                                        None)
                    done += 1
                    wall_streak = 0
                else:
                    detail = f"{type(error).__name__}: {error}"
                    blocked = _blocked(error)
                    uploader.emit(f"  ✗ {label}: {detail}", level="error")
                    # A FAILURE THAT IS NOT THE DESIGN'S FAULT MUST NOT BE
                    # WRITTEN AGAINST IT. A wall, a signed-out session, three
                    # spent mouse paths — all facts about this browser for
                    # the next few minutes and none of them about the design.
                    # Recording one on the row takes the design out of the
                    # work list, which on the switch-back-ON side leaves a
                    # live listing hidden while the screen blames a design
                    # that is perfectly healthy.
                    stop = self._report(run_id, account["id"], did, action,
                                        detail, transient=blocked)
                    failed += 1

                    # Whatever ends the account, the server hears it from
                    # the outer handler as a stage failure rather than as a
                    # job that quietly stopped — so the run either waits and
                    # comes back or ends saying why.
                    if getattr(error, "fatal", False):
                        raise error

                    if blocked:
                        wall_streak += 1
                        if wall_streak >= give_up_after:
                            raise UploadError(
                                f"Gave up on {account.get('name')} after "
                                f"{wall_streak} designs in a row blocked by "
                                f"the wall. {done} were done first.",
                                transient=True, fatal=True)
                    else:
                        wall_streak = 0

                # ── THE SERVER SAID STOP ─────────────────────────────────
                #
                # Checked after BOTH outcomes, because a run being paused
                # has nothing to do with whether this particular design
                # worked. Reported as `partial` so the server knows the
                # account was cut short and does not read a clean ending as
                # a finished account — the same mistake that once let a
                # paused scan advance to a mass deactivation.
                if stop:
                    cut_short = True
                    uploader.emit(
                        f"Stopping — the server says this run is no longer "
                        f"switching designs. {done} done, "
                        f"{len(designs) - index} not started.", level="warn")
                    break

            # ── AND WHAT DOES TEEPUBLIC SAY NOW ──────────────────────────
            #
            # Read here, INSIDE the try, while the signed-in browser is
            # still open — it is the last thing this account's turn does.
            # Posted rather than judged: whether the numbers agree is the
            # server's question, and it is the only one that knows what we
            # believed we were doing.
            if counted:
                after_count = self._read_inactive_count(
                    uploader, store_url, html_markers, paths, signed_out,
                    wall_wait, wall_tries)
                verdict = self.client.post("/store/inactive-count", {
                    "run_id": run_id, "account_id": account["id"],
                    "stage": action,
                    "before": before_count, "after": after_count,
                    "switched": done, "cut_short": cut_short,
                }) or {}
                # SAY WHAT IT ANSWERED. A reply read and discarded is how a
                # page the server could not use produced a job that reported
                # "finished" over nothing having been stored.
                if verdict.get("note"):
                    uploader.emit(verdict["note"],
                                  level="error" if verdict.get("mismatch")
                                  else "ok")
        except Exception as e:
            # Anything that escaped the per-design handler — almost always
            # start(). Report the stage as failed so the run ENDS and the
            # pipeline is released, then re-raise so the job says so too.
            detail = f"{type(e).__name__}: {e}"
            self.client.post("/store/stage-done", {
                "run_id": run_id, "stage": action,
                "account_id": account["id"],
                "error": f"{account.get('name')}: {detail}",
                # The wall passes; a rejected password does not. Only the
                # first is worth sleeping on.
                "transient": bool(getattr(e, "transient", False)),
            })
            raise
        finally:
            uploader.stop()

        self.client.post("/store/stage-done",
                         {"run_id": run_id, "stage": action,
                          "account_id": account["id"],
                          "partial": cut_short})
        return {"done": done, "failed": failed, "cut_short": cut_short,
                "account": account.get("name")}

    # ── ONE WAY OF ASKING "AM I EVEN ON THE RIGHT PAGE" ─────────────────
    #
    # BOTH switching directions go through these two helpers, and that is the
    # entire point of them existing. The wall check used to live only in
    # `_deactivate`. On 25 Aug the wall turned up partway through a
    # reactivation and switching-ON, which had never been taught about it,
    # loaded 79 walls in a row, found no publish button on any of them, and
    # wrote 79 design-shaped errors in four minutes — three seconds each,
    # against twenty for real work.
    #
    # The give-up guard could not save it either: that counts failures marked
    # "this was the wall", and the mark is set inside the check that was
    # never called. A live guard that cannot fire.
    #
    # The general shape, and it is already in CLAUDE.md rule 8: when a
    # mechanism has several instances, changing one is not changing it.

    def _open_page(self, uploader, url: str, html_markers, paths,
                   signed_out, wall_wait, wall_tries) -> None:
        """Go to a page and be sure it is the page, not the wall in front of it."""
        uploader.driver.get(url)
        if html_markers:
            uploader.clear_wall([], paths,
                                wait_s=wall_wait, attempts=wall_tries,
                                signed_out_markers=signed_out,
                                html_markers=html_markers)
        time.sleep(1)

    def _missing_control(self, uploader, html_markers, what: str,
                         message: str) -> UploadError:
        """
        Turn "the button is not here" into the RIGHT complaint.

        A missing control has two completely different meanings and the
        report has to say which:

          · the page is ours and the button is genuinely absent — the
            design's problem. Retrying will not help; it needs a person.
          · the page is not ours at all — the far side's problem. Nothing
            about the design is known, and treating it as the design's
            failure is how one wall became 79 permanent-looking errors.

        Told apart by the site's own header logo, exactly as the wall is.
        This is the same positive test used everywhere else here, so the two
        can never disagree about what a good page looks like.
        """
        if html_markers and not uploader.page_has_markup(html_markers):
            return UploadError(
                f"No {what} — and this is not a TeePublic page at all, so "
                f"the design tells us nothing. The wall is most likely back.",
                transient=True)
        return UploadError(message)

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
        self._open_page(uploader, design["url"], html_markers, paths,
                        signed_out, wall_wait, wall_tries)

        selector = f"form[action='/designs/{did}/deactivate'] button[type='submit']"
        try:
            button = uploader.driver.find_element(By.CSS_SELECTOR, selector)
        except Exception:
            raise self._missing_control(
                uploader, html_markers, f"Deactivate button for design {did}",
                f"No Deactivate button for design {did}. Either the session "
                f"is not signed in as the owner, or the design is already "
                f"inactive.")
        uploader.real_click(button, what="deactivate")
        time.sleep(2)

    def _reactivate(self, uploader, design_id: str, html_markers, paths,
                    signed_out, wall_wait, wall_tries) -> None:
        """
        Republish from the design's edit page: accept terms, press publish.

        The ID comes from our own record of what we deactivated, never from
        scraping the marketplace's inactive list — one real account holds 379
        designs the owner turned off himself and nothing on that page tells
        them apart from ours.

        THE BUTTON IS PRESSED FOR REAL, not with JavaScript, and that is not
        a style choice — see `uploader.real_click`. A JavaScript click goes
        through anything drawn over the button and reports success, which is
        how one design was recorded as republished while it stayed off.
        """
        from selenium.webdriver.common.by import By

        self._open_page(uploader, f"{BASE}/designs/{design_id}/edit",
                        html_markers, paths, signed_out, wall_wait, wall_tries)

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
            raise self._missing_control(
                uploader, html_markers,
                f"publish button on the edit page for design {design_id}",
                f"No publish button on the edit page for design {design_id}.")
        uploader.real_click(publish, what="publish")
        time.sleep(2)

    # ── THE MARKETPLACE'S OWN COUNT, AS A CHECKSUM ──────────────────────

    def _read_inactive_count(self, uploader, store_url: str, html_markers,
                             paths, signed_out, wall_wait,
                             wall_tries) -> Optional[int]:
        """
        How many designs does TeePublic itself say are switched off?

        ════════════════════════════════════════════════════════════════════
        WHY BOTHER WHEN WE ALREADY WROTE DOWN WHAT WE DID
        ════════════════════════════════════════════════════════════════════
        Because what we wrote down is the thing that can be wrong. Every
        other check here compares our records against our records. This is
        the only number in the system that comes from outside them, so it is
        the only one that can catch us believing a design is live when it is
        sitting on the inactive tab.

        Same idea as FineArtAmerica's Balance page, whose printed total is
        used to prove our arithmetic did not miss any rows.

        ════════════════════════════════════════════════════════════════════
        MEASURED 25 AUG: THIS NEEDS TO BE SIGNED IN
        ════════════════════════════════════════════════════════════════════
        Signed out, the same address answers "You do not have permission to
        edit this store" and shows no counts at all. So it cannot be a cheap
        public fetch — it rides along inside the switching job, which is
        already signed in as this exact account with its own Chrome profile.
        It costs one page load at each end of an hour of work.

        Found by the LINK'S OWN ADDRESS, `/inactive`, not by a class name.
        The classes on that page are randomised (`tOHY4`, `qrvwN4`) and
        would break on the site's next deploy while blaming us.

        Returns None when the number is not there. NOT zero — "we could not
        look" and "nothing is switched off" are opposite answers, and
        collapsing them would report a healthy account as evidence of a bug.
        """
        from selenium.webdriver.common.by import By

        if not store_url:
            return None
        try:
            self._open_page(uploader, store_url, html_markers, paths,
                            signed_out, wall_wait, wall_tries)
            links = uploader.driver.find_elements(
                By.CSS_SELECTOR, "a[href*='/inactive']")
            for link in links:
                found = re.search(r"(\d[\d,]*)", link.text or "")
                if found:
                    return int(found.group(1).replace(",", ""))
        except Exception as e:
            self.log(f"Could not read the inactive count at {store_url}: {e}",
                     level="warn")
        return None

    def _report(self, run_id: int, account_id: int, design_id: str,
                action: str, error: Optional[str], *,
                transient: bool = False) -> bool:
        """
        Say what happened to this one design, immediately.

        Per design rather than per batch: a stage that fell over halfway
        would otherwise leave no record of the twenty it had already turned
        off, and reactivation would then miss exactly those twenty.

        RETURNS TRUE WHEN THE SERVER SAYS STOP. The reply used to be thrown
        away, which meant a node could not be stopped at all once an account
        was under way: PAUSE and STOP THIS RUN changed the screen while
        designs kept switching off for the rest of the hour. A node cannot
        hear a button — only an answer to a question it was already asking,
        and this is that question.
        """
        try:
            reply = self.client.post("/store/action", {
                "run_id": run_id, "account_id": account_id,
                "design_id": design_id, "action": action, "error": error,
                # WHOSE fault it was. A wall says nothing about the design,
                # so the server records the reason without holding it
                # against the row — otherwise one bad ten minutes leaves a
                # live listing switched off and blames a healthy design.
                "transient": transient,
            })
            return bool((reply or {}).get("stop"))
        except Exception as e:
            # A report we could not deliver is not an instruction to stop.
            # Treating a blip as a stop would abandon the account and leave
            # its designs half switched.
            self.log(f"Could not report {action} of {design_id}: {e}",
                     level="error")
            return False
