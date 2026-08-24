"""
Listing reconciliation, node side: fetch a list of addresses, report statuses.

════════════════════════════════════════════════════════════════════════════
THE SIMPLEST STAGE ON THIS MACHINE, AND IT SHOULD STAY THAT WAY
════════════════════════════════════════════════════════════════════════════
No browser, no login, no Chrome profile, no selectors, no wall. A HEAD
request per address and the status code back. That is the whole job.

It runs here rather than on the Linux server for one measured reason:
FineArtAmerica answers 403 to that machine for every page, public ones
included — headers make no difference, so it is the address being refused
and not the request. This machine gets a clean 200/404 with a plain HTTP
client. (Measured 2026-08-24; both are written up in the project brief.)

════════════════════════════════════════════════════════════════════════════
IT KNOWS NOTHING ABOUT THE MARKETPLACE
════════════════════════════════════════════════════════════════════════════
The addresses are built on the SERVER and handed over as a list. Nothing
here knows how FineArtAmerica spells a URL, what a title looks like after
they rewrite it, or which account a listing belongs to. That is what would
let the same job be pointed at another marketplace without touching this
file, and it is the same principle as the rest of the node: it holds no
configuration.

════════════════════════════════════════════════════════════════════════════
`requests`, NOT `urllib` — AND THIS IS NOT A PREFERENCE
════════════════════════════════════════════════════════════════════════════
This machine's Python has no root certificate store, so `urllib` cannot make
an HTTPS connection at all: it fails with CERTIFICATE_VERIFY_FAILED before
anything leaves the box. `requests` carries its own bundle via `certifi`,
which is why the rest of the agent has never hit it.

The symptom looks exactly like a network fault or a block and is neither.
Never "fix" it by turning verification off — that hides this failure and
this machine sends real marketplace passwords over the same library.
"""

from __future__ import annotations

import time
from typing import Optional

import requests

# Deliberately not a browser's. A bare client is what was measured working;
# a lone Chrome User-Agent is exactly what made TeePublic's store listing
# start returning 403, and a half-disguise is worse than none.
UA = "python-requests"

TIMEOUT_S = 30


class ListingCheckStage:
    """One instance per job, like every other stage."""

    def __init__(self, client, config: dict, log):
        self.client = client
        self.config = config
        self.log = log

    def run(self, job_id: int, payload: dict) -> dict:
        """
        Check every address in this chunk, reporting as we go.

        ════════════════════════════════════════════════════════════════════
        HEAD, NOT GET
        ════════════════════════════════════════════════════════════════════
        A live page is ~190KB and the not-found page ~128KB, and we want
        neither — only the status line. Across 4,811 listings that is the
        difference between about 900MB and nothing worth counting, and
        between three and a quarter hours and one. FineArtAmerica was
        measured returning the same 200/404 to HEAD as to GET.

        ════════════════════════════════════════════════════════════════════
        REPORTED IN SMALL BATCHES, AND THE REPLY IS THE STOP BUTTON
        ════════════════════════════════════════════════════════════════════
        Posting only at the end would lose everything checked so far if this
        machine died mid-chunk — and the server derives "what is left" from
        what has been recorded, so the same addresses would be checked again
        for ever.

        The reply also carries `stop`, which is the only way this can be
        halted: a node cannot hear a button, only an answer to a question it
        was already asking.
        """
        sweep_id = payload["sweep_id"]
        items = payload.get("items") or []
        gap = max(0.0, float(payload.get("gap_ms") or 300) / 1000.0)
        every = max(1, int(payload.get("report_every") or 25))

        self.client.job_log(job_id, [
            f"Checking {len(items)} listing address(es).",
            "HEAD requests only — no browser, no sign-in, nothing changed.",
        ], progress=2)

        session = requests.Session()
        session.headers.update({"User-Agent": UA})

        pending: list[dict] = []
        done = 0
        tally = {"live": 0, "gone": 0, "unknown": 0}
        cut_short = False

        try:
            for index, item in enumerate(items, start=1):
                status: Optional[int] = None
                try:
                    resp = session.head(item["url"], timeout=TIMEOUT_S,
                                        allow_redirects=True)
                    status = resp.status_code
                except requests.RequestException as e:
                    # A request that could not be made is NOT evidence the
                    # listing is gone. It goes back with no status and the
                    # server records it as "we could not look".
                    self.log(f"  ? {item['url']}: {type(e).__name__}")

                pending.append({"id": item["id"], "http": status})
                done += 1
                key = ("live" if status == 200 else
                       "gone" if status == 404 else "unknown")
                tally[key] += 1

                if len(pending) >= every or index == len(items):
                    stop = self._report(job_id, sweep_id, pending, done,
                                        len(items), tally)
                    pending = []
                    if stop:
                        cut_short = True
                        self.client.job_log(job_id, [
                            f"Stopping — the server says this sweep is over. "
                            f"{done} checked, {len(items) - done} not started."
                        ], progress=99)
                        break

                if index < len(items):
                    time.sleep(gap)
        except Exception as e:
            # Anything that escaped the per-address handling. Report the
            # chunk as failed so the sweep ENDS and says why, rather than
            # sitting at "checking" with the screen showing work in progress.
            detail = f"{type(e).__name__}: {e}"
            self.client.post("/listings/chunk-done",
                             {"sweep_id": sweep_id, "error": detail})
            raise

        self.client.post("/listings/chunk-done",
                         {"sweep_id": sweep_id, "partial": cut_short})
        return {"checked": done, "cut_short": cut_short, **tally}

    def _report(self, job_id: int, sweep_id: int, results: list[dict],
                done: int, total: int, tally: dict) -> bool:
        """
        Hand over what we have. Returns True when the server says stop.

        The answer is READ, not discarded. An earlier stage on this machine
        posted its results and looked at one key of the reply, so a batch the
        server could not store produced a job that fetched, stored nothing,
        and reported success — with the screen left empty and no line
        anywhere connecting the two.
        """
        try:
            reply = self.client.post("/listings/progress", {
                "sweep_id": sweep_id, "results": results,
            }) or {}
        except Exception as e:
            # Could not deliver. Not an instruction to stop — treating a
            # blip as a stop would abandon the chunk. The addresses in this
            # batch stay unrecorded and are simply checked again later.
            self.log(f"Could not report {len(results)} result(s): {e}",
                     level="error")
            return False

        stored = reply.get("stored")
        if stored != len(results):
            self.client.job_log(job_id, [
                f"WARN sent {len(results)} result(s) but the server stored "
                f"{stored} — those addresses will be checked again."
            ])

        self.client.job_log(job_id, [
            f"{done} of {total} · {tally['live']} still there, "
            f"{tally['gone']} not found"
            + (f", {tally['unknown']} could not be looked at"
               if tally["unknown"] else "")
        ], progress=min(98, int(done / max(1, total) * 95)))

        return bool(reply.get("stop"))
