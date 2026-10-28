"""
Swap the picture on a listing that is already live — the 417 (v266).

Asked for by the owner on 2026-10-28. Step 2 (the flatten) gives every
painting a cleaner, flatter look, including the 417 already for sale, and
FineArtAmerica lets an existing listing change its picture: Edit Image →
Change Image → Upload Image → Submit. The listing keeps its title, address,
keywords and favourites, so nothing is deleted and FineArtAmerica never
renumbers anything. MEASURED by the owner on a test account: Submit is
needed, and the new picture takes about a day to show on every product.

════════════════════════════════════════════════════════════════════════════
A JOB ON THIS MACHINE, NOT A SCRIPT BESIDE IT
════════════════════════════════════════════════════════════════════════════
It runs inside the worker program, as one more kind of job, for three
reasons that all come from how the uploader already works:

  * the browser is the account's own signed-in Chrome, and two programs
    cannot open one profile at once — a separate script would mean closing
    the worker program first;
  * the password arrives from the server with the job, exactly as for an
    upload or an earnings read, and never sits in a file;
  * it is one click on the Pipeline page, with its log in the Live Console
    and STOP working through the jobs list.

It is deliberately small, and it is deleted once the 417 are done.

════════════════════════════════════════════════════════════════════════════
THE PAGES, READ FROM THE OWNER'S SAVED COPIES (2026-10-28), NOT GUESSED
════════════════════════════════════════════════════════════════════════════
  · the public listing page carries an Edit link holding `artworkid=<n>`;
  · the edit page (updateartwork2026.html) has the title in
    input[name=artworkname], a "Change Image" anchor (a.imageDetailsButton)
    and a.buttonSubmit;
  · Change Image replaces the page body with input.uploadImageInput
    [name=uploadimage] and an "Upload Image" anchor inside #uploadImageDiv —
    and the details form is GONE until the upload finishes, so its title box
    coming back is the sign the upload is done.
Every selector is a dashboard setting (Pipeline → Upload → Page Selectors),
with an "||" fallback that finds the button by its words.

════════════════════════════════════════════════════════════════════════════
IT NEVER PUTS A PICTURE ON THE WRONG LISTING
════════════════════════════════════════════════════════════════════════════
Before anything is changed, the title in the edit form must be the title
the server expects for this listing. If it is not, that listing is skipped
and reported — never swapped "close enough".
"""

from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path

from .uploader import (MarketplaceUploader, UploadError, _drive_root,
                       looks_like_maintenance)


def _same_title(a: str, b: str) -> bool:
    """Case, spacing and accents do not count; every letter and digit does."""
    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s or "")
        return re.sub(r"[^a-z0-9]+", "", s.encode("ascii", "ignore").decode().lower())
    return norm(a) == norm(b) and norm(a) != ""


class ListingSwapStage:
    def __init__(self, client, config: dict, log):
        self.client = client
        self.config = config
        self.log = log

    def run(self, job_id: int, payload: dict) -> dict:
        items = payload.get("items") or []
        account = payload["account"]
        settings = payload["settings"]
        storage_root = Path(self.config.get("storage_root_override")
                            or settings["storage_root"])
        self.client.job_log(job_id, [
            f"Swapping the picture on {len(items)} live listing(s) for "
            f"'{account['name']}'.",
            "Each listing keeps its title, address and keywords — only the "
            "picture changes.",
        ], progress=2)

        up = MarketplaceUploader(account=account, settings=settings,
                                 config=self.config, client=self.client,
                                 log=self.log, job_id=job_id)
        done = failed = 0
        stopped = False
        try:
            try:
                up.start()
                up.login()
                up.open_work_tab()
            except Exception as e:                       # noqa: BLE001
                detail = f"{type(e).__name__}: {e}"
                up.capture_evidence("swap_startup")
                # Reported for every item, because nothing was changed and the
                # server must know none of them were swapped.
                for item in items:
                    self._report(job_id, item, ok=False,
                                 error=f"could not open the marketplace — {detail}")
                raise

            for index, item in enumerate(items, start=1):
                progress = 5 + int(index / max(1, len(items)) * 93)
                up.emit(f"[{index}/{len(items)}] {item['expected_title']}",
                        progress=progress, note=f"{done} swapped, {failed} failed")
                try:
                    image = _drive_root(storage_root) / item["storage_path"]
                    if not image.is_file():
                        raise UploadError(f"The new print file is not on the "
                                          f"storage drive: {image}")
                    artwork_id = self._swap_one(up, item, image)
                    reply = self._report(job_id, item, ok=True,
                                         artwork_id=artwork_id)
                    done += 1
                    up.emit(f"  ✓ swapped (artwork {artwork_id})", level="ok")
                except UploadError as e:
                    failed += 1
                    shot = up.capture_evidence(f"swap_{item['tracking_id']}")
                    up.emit(f"  ✗ {e}", level="error")
                    reply = self._report(job_id, item, ok=False, error=str(e),
                                         screenshot=shot)
                    if e.fatal:
                        up.emit("Stopping — the problem affects the whole account.",
                                level="error")
                        break
                except Exception as e:                   # noqa: BLE001
                    failed += 1
                    shot = up.capture_evidence(f"swap_{item['tracking_id']}")
                    up.emit(f"  ✗ unexpected: {e}", level="error")
                    reply = self._report(job_id, item, ok=False,
                                         error=f"{type(e).__name__}: {e}",
                                         screenshot=shot)
                # The REPLY is the stop button — a machine cannot hear one.
                if reply.get("stop"):
                    stopped = True
                    up.emit("Stopping — the job was cancelled on the site.",
                            level="warn")
                    break
                time.sleep(float(account["timings"].get("between_images", 0.5)))
        finally:
            up.stop()
        return {"swapped": done, "failed": failed, "cut_short": stopped}

    def _swap_one(self, up: MarketplaceUploader, item: dict, image: Path) -> str:
        wait = up.t("page_load_wait", 2)

        # 1 — the public listing page, which holds the Edit link.
        up.driver.get(item["listing_url"])
        time.sleep(wait)
        if looks_like_maintenance(up.driver.page_source):
            raise UploadError("FineArtAmerica is showing its maintenance page.",
                              pause_minutes=30, fatal=True,
                              pause_reason="Marketplace is in maintenance")
        try:
            link = up.find("swap_edit_link", timeout=20)
        except UploadError:
            raise UploadError(
                f"No Edit link on {item['listing_url']} — the listing may not "
                f"exist at that address, or this account is not signed in.")
        href = (link.get_attribute("href") or "").strip()
        m = re.search(r"artworkid=(\d+)", href)
        if not m:
            raise UploadError(f"The Edit link carries no artwork number: {href!r}")
        artwork_id = m.group(1)

        # 2 — the edit page, and the check that it is the right listing.
        up.driver.get(href)
        time.sleep(wait)
        marker = (up.selectors.get("still_on_form_marker") or "updateartwork").lower()
        if marker not in (up.driver.current_url or "").lower():
            raise UploadError(f"Did not reach the edit page (on "
                              f"{up.driver.current_url}).")
        shown = up.find("title_field").get_attribute("value") or ""
        if not _same_title(shown, item["expected_title"]):
            raise UploadError(
                f"Not swapped: this listing is titled {shown!r}, but the picture "
                f"is for {item['expected_title']!r}.")

        # 3 — Change Image → the file → Upload Image.
        up.js_click(up.find("swap_change_image", clickable=True), what="Change Image")
        time.sleep(wait)
        up.emit(f"  → sending the new picture ({image.stat().st_size / 1024:.0f} KB)")
        up.find("swap_file_input").send_keys(str(image.resolve()))
        time.sleep(wait)
        up.js_click(up.find("swap_upload_button", clickable=True), what="Upload Image")
        time.sleep(up.t("upload_wait", 5))

        # 4 — the details form comes back when the upload is done. Same check
        #     again, then Submit.
        shown = up.find("title_field").get_attribute("value") or ""
        if not _same_title(shown, item["expected_title"]):
            raise UploadError(
                f"After the upload the form shows {shown!r} — not submitting.")
        up.js_click(up.find("submit_button", clickable=True), what="Submit")

        # 5 — it has saved when the page leaves the edit form.
        deadline = time.time() + max(30.0, up.t("element_timeout", 60))
        while time.time() < deadline:
            time.sleep(1.0)
            if marker not in (up.driver.current_url or "").lower():
                return artwork_id
        raise UploadError(f"Submit did not complete — still on the edit page "
                          f"({up.driver.current_url}).")

    def _report(self, job_id: int, item: dict, *, ok: bool, artwork_id: str = "",
                error: str = "", screenshot=None) -> dict:
        try:
            return self.client.post("/swap/report", {
                "job_id": job_id, "tracking_id": item["tracking_id"],
                "processed_id": item["processed_id"], "ok": ok,
                "artwork_id": artwork_id, "error": error[:2000],
                "screenshot": screenshot,
            }) or {}
        except Exception as e:                           # noqa: BLE001
            # The swap itself may have happened; say so in the log, loudly,
            # so the row can be put right with the record command.
            self.log(f"Could not report swap of tracking {item['tracking_id']}: {e}",
                     level="error")
            return {}
