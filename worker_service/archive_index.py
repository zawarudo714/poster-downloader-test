"""
List the finished images on the storage box and post the paths home.

════════════════════════════════════════════════════════════════════════════
WHY THIS MACHINE
════════════════════════════════════════════════════════════════════════════
The storage box is mounted here as a drive letter. The database is on the
Linux server. Neither can see both, so the machine with the drive lists what
is on it and the machine with the database decides what it means.

THIS FILE HOLDS NO POLICY. It walks a folder it is told to walk and reports
what it found. Which poster a file belongs to, whether to attach it, and
what to do about one that matches nothing are all decided server-side —
where the posters are.

════════════════════════════════════════════════════════════════════════════
CHUNKED, AND THE REPLY IS THE STOP BUTTON
════════════════════════════════════════════════════════════════════════════
Roughly 4,900 files across 2,000 folders. Listing them costs seconds; the
server matching them is the slow part, so it goes out in chunks and each
reply says whether to carry on.

A node cannot hear a button — only an answer to a question it was already
asking. This is that question. A version that posted everything at the end
could not be stopped, and would lose the whole walk if the machine died.
"""

from __future__ import annotations

import os
from typing import Callable

from .uploader import UploadError


class ArchiveIndexStage:
    """Walk the processed tree and report it, a chunk at a time."""

    def __init__(self, client, config, log: Callable):
        self.client = client
        self.config = config
        self.log = log

    def run(self, job_id: int, payload: dict) -> dict:
        root = payload.get("root") or ""
        chunk_size = int(payload.get("chunk") or 200)
        emit = lambda text, **kw: self.log(text, job_id=job_id, **kw)

        if not root:
            raise UploadError("No archive folder was given.")

        # ── SAY WHAT IS WRONG, DO NOT INVENT A REASON ───────────────────
        #
        # A missing drive and an empty folder look identical from a count of
        # zero, and they need completely different actions: remount the
        # storage box, or accept that nothing has been processed yet.
        if not os.path.isdir(root):
            raise UploadError(
                f"{root} is not there. Either the storage box is not "
                f"mounted on this machine, or the folder in the pipeline "
                f"settings does not match where images are actually filed.")

        emit(f"Reading {root}")
        found: list[dict] = []
        for current, _dirs, files in os.walk(root):
            rel_dir = os.path.relpath(current, root).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""
            for name in files:
                if os.path.splitext(name)[1].lower() not in (
                        ".jpg", ".jpeg", ".png", ".webp"):
                    continue
                try:
                    size = os.path.getsize(os.path.join(current, name))
                except OSError:
                    size = None
                found.append({"path": f"{rel_dir}/{name}" if rel_dir else name,
                              "size": size})

        total = len(found)
        emit(f"{total:,} image(s) on the storage box. Sending them to the "
             f"server {chunk_size} at a time.")
        if not total:
            # Not an error. An empty archive is a real state — it is what a
            # fresh install looks like — and calling it a failure would send
            # the next reader hunting for a fault that is not there.
            self.client.post("/archive/done",
                             {"job_id": job_id, "total": 0, "partial": False})
            return {"total": 0, "linked": 0}

        linked = already = conflict = unmatched = 0
        cut_short = False

        for start in range(0, total, chunk_size):
            batch = found[start:start + chunk_size]
            reply = self.client.post("/archive/files", {
                "job_id": job_id, "files": batch,
            }) or {}

            # SAY WHAT IT ANSWERED. A reply read only for its stop flag is
            # how a chunk the server could not store produced a job that
            # reported "finished" over nothing having happened.
            if reply.get("error"):
                raise UploadError(f"The server could not store that batch: "
                                  f"{reply['error']}")
            linked += int(reply.get("linked") or 0)
            already += int(reply.get("already") or 0)
            conflict += int(reply.get("conflict") or 0)
            unmatched += int(reply.get("unmatched") or 0)

            done = min(start + chunk_size, total)
            emit(f"  {done:,} of {total:,} — {linked:,} newly linked, "
                 f"{already:,} already known, {unmatched:,} matched nothing",
                 progress=min(95, int(done / total * 95)))

            if reply.get("stop"):
                cut_short = True
                emit(f"Stopping — the dashboard says so. Everything sent so "
                     f"far is saved.", level="warn")
                break

        self.client.post("/archive/done", {
            "job_id": job_id, "total": total, "partial": cut_short,
        })
        return {"total": total, "linked": linked, "already": already,
                "conflict": conflict, "unmatched": unmatched,
                "cut_short": cut_short}
