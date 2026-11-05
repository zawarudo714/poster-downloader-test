"""
The laptop's record of what went to Junique, and the fetch queue.

WHY on the laptop: the owner wants it there while the Junique tools are a
blueprint; it moves to the server only if they work out (DECIDED
2026-11-05, docs/DECISIONS.md). One JSON file, keyed by `saved_poster_id`
(the poster, which a re-approved picture keeps).

The queue rule (the owner's words, 2026-11-05): fetch 50, 48 upload; the
next fetch of 20 gives 49, 50, then the next 18 new ones. So anything
fetched or failed and not yet uploaded comes first, then new designs in the
server's order (approval order). A design counts as uploaded only after
Junique confirmed Publish (`mark_uploaded`, called by junique_upload.py).

A design that failed MAX_TRIES times stops coming back to the front, so one
picture Junique always refuses cannot block every batch; it shows as
"gave up" in the summary.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local"                      # git-ignored: ledger, Chrome profile, screenshots
LEDGER = LOCAL / "ledger.json"
MANUAL = HERE / "MANUAL_UPLOADS.csv"
DEFAULT_FOLDER = Path(os.path.expanduser("~")) / "Downloads" / "Junique uploads"
STORAGE_ROOT = Path("S:/")

# The same server and key the deploy uses (tools/deploy_cli.py).
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "root@178.105.34.144"]
LIST_CMD = "cd /opt/poster && docker compose exec -T -w /app web python -m app.junique_list"

MAX_TRIES = 3
WAITING = ("fetched", "failed")              # not uploaded yet; these go first


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load(path: Path = LEDGER) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"designs": {}, "batch": [], "next_seq": 1}


def save(led: dict, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(led, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)                    # never a half-written ledger


def manual_ids(path: Path = MANUAL) -> set[int]:
    """The designs the owner uploaded by hand; never uploaded again."""
    import csv
    if not path.exists():
        return set()
    with open(path, encoding="utf-8", newline="") as f:
        return {int(r["saved_poster_id"]) for r in csv.DictReader(f) if r.get("saved_poster_id")}


def server_list() -> list[dict]:
    """The designs ready for Junique, from the server (app/junique_list.py)."""
    r = subprocess.run(SSH + [LIST_CMD], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"The server list failed (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout).strip()[:300]}")
    return json.loads(r.stdout)["designs"]


def pick(led: dict, ready: list[dict], n: int, skip: set[int]) -> tuple[list[int], list[str]]:
    """Which designs the next fetch of `n` takes, in order, and any notes.
    Pure: changes nothing, so the rule can be tested on its own."""
    by_id = {d["saved_poster_id"]: d for d in ready}
    notes = []
    first = []
    for key, d in sorted(led["designs"].items(), key=lambda kv: kv[1]["seq"]):
        if d["status"] not in WAITING:
            continue
        if d.get("tries", 0) >= MAX_TRIES:
            continue
        if int(key) not in by_id:
            notes.append(f"{d['title']}: not ready on the server now (re-made or no longer approved), left for later")
            continue
        first.append(int(key))
    new = [d["saved_poster_id"] for d in ready
           if str(d["saved_poster_id"]) not in led["designs"] and d["saved_poster_id"] not in skip]
    return (first + new)[:max(0, n)], notes


def safe_name(title: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", title).strip().rstrip(".") or "design"


def fetch(n: int, folder: Path = DEFAULT_FOLDER, *, ready: list[dict] | None = None,
          root: Path = STORAGE_ROOT, ledger_path: Path = LEDGER, emit=print) -> dict:
    """Copy the next `n` print files into `folder` and record them."""
    if not root.exists():
        raise RuntimeError("The Storage Box (S:) is not connected. Open S: in File Explorer, "
                           "type the Storage Box password, then fetch again.")
    led = load(ledger_path)
    ready = server_list() if ready is None else ready
    by_id = {d["saved_poster_id"]: d for d in ready}
    chosen, notes = pick(led, ready, n, manual_ids())
    for note in notes:
        emit(note)

    folder.mkdir(parents=True, exist_ok=True)
    # Clear the files this tool put there for designs already uploaded.
    for d in led["designs"].values():
        f = Path(d.get("file") or "")
        if d["status"] == "uploaded" and f.parent == folder and f.exists():
            f.unlink()

    used: set[str] = set()
    batch, copied, missing = [], 0, 0
    for spid in chosen:
        src_row = by_id[spid]
        key = str(spid)
        d = led["designs"].get(key)
        if d is None:
            d = led["designs"][key] = {"saved_poster_id": spid, "seq": led["next_seq"],
                                       "status": "fetched", "tries": 0}
            led["next_seq"] += 1
        d.update(title=src_row["title"], processed_image_id=src_row["processed_image_id"],
                 storage_path=src_row["storage_path"])
        name = safe_name(d["title"])
        stem, k = name, 2
        while name.lower() in used:
            name = f"{stem} ({k})"; k += 1
        used.add(name.lower())
        src = root / src_row["storage_path"]
        dst = folder / f"{name}.jpg"
        if not src.exists():
            d.update(status="failed", error=f"file missing on S: ({src})")
            d["tries"] = d.get("tries", 0) + 1
            emit(f"MISSING  {d['title']}  ({src})")
            missing += 1
            continue
        shutil.copyfile(src, dst)
        d.update(file=str(dst), fetched_at=now())
        if d["status"] != "failed":
            d["status"] = "fetched"
        batch.append(spid)
        copied += 1
        emit(f"OK       {d['title']}  ({dst.stat().st_size / 1e6:.1f} MB)")
    led["batch"] = batch
    led["folder"] = str(folder)
    save(led, ledger_path)
    out = {"copied": copied, "missing": missing, "folder": str(folder), **summary(led)}
    emit(f"{copied} copied, {missing} missing. Folder: {folder}")
    return out


def summary(led: dict) -> dict:
    c = {"uploaded": 0, "waiting": 0, "gave_up": 0}
    for d in led["designs"].values():
        if d["status"] == "uploaded":
            c["uploaded"] += 1
        elif d.get("tries", 0) >= MAX_TRIES:
            c["gave_up"] += 1
        else:
            c["waiting"] += 1
    return c


def batch_todo(led: dict) -> list[dict]:
    """The fetched batch, minus what is uploaded already, in order."""
    return [led["designs"][str(s)] for s in led.get("batch", [])
            if led["designs"][str(s)]["status"] != "uploaded"]


def mark_uploaded(spid: int, junique_id: str, ledger_path: Path = LEDGER) -> None:
    led = load(ledger_path)
    d = led["designs"][str(spid)]
    d.update(status="uploaded", uploaded_at=now(), junique_design_id=junique_id, error="")
    save(led, ledger_path)


def mark_failed(spid: int, error: str, junique_id: str | None = None,
                ledger_path: Path = LEDGER) -> None:
    led = load(ledger_path)
    d = led["designs"][str(spid)]
    d["tries"] = d.get("tries", 0) + 1
    d.update(status="failed", error=error[:500], failed_at=now())
    if junique_id:
        # A draft is left on Junique; the owner deletes it (the tool never deletes).
        d.setdefault("drafts_left", []).append(junique_id)
    save(led, ledger_path)

