"""
Behaviour test of the Junique fetch queue, with the owner's own example:
fetch 50, 48 upload, and the next fetch of 20 gives 49, 50, then 18 new.
Uses a throw-away ledger, Storage Box and folder; never touches S: or the
real ledger. Run from the repo root:

    python tools/junique/test_ledger.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as L  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="jq_ledger_test_"))
root, folder, led_path = tmp / "S", tmp / "out", tmp / "ledger.json"
ready = []
for i in range(1, 121):
    rel = f"p/{i}.jpg"
    (root / "p").mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(b"jpg")
    ready.append({"saved_poster_id": i, "processed_image_id": 1000 + i,
                  "title": f"Place {i}", "storage_path": rel, "approved_at": None})
manual = {3}                       # uploaded by hand: never fetched
L.manual_ids = lambda path=None: manual

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
quiet = lambda s: None
run = lambda n: L.fetch(n, folder, ready=ready, root=root, ledger_path=led_path, emit=quiet)

run(50)
led = L.load(led_path)
first = led["batch"]
ok(len(first) == 50 and 3 not in first and first[:3] == [1, 2, 4],
   "the first 50 are in order and skip the one uploaded by hand")
ok(len(list(folder.glob("*.jpg"))) == 50, "50 files are in the folder")

for spid in first[:48]:
    L.mark_uploaded(spid, f"j{spid}", ledger_path=led_path)
L.mark_failed(first[49], "quality check refused", ledger_path=led_path)   # 50th failed, 49th never tried

run(20)
led = L.load(led_path)
ok(led["batch"][:2] == first[48:50] and led["batch"][2:] == list(range(52, 70)),
   f"the next 20 are the 2 unfinished, then 18 new ({led['batch'][:4]}...)")
ok(len(list(folder.glob("*.jpg"))) == 20, "files of uploaded designs left the folder")
ok(led["designs"][str(first[49])]["status"] == "failed", "a failed design keeps its failure until it uploads")

for _ in range(L.MAX_TRIES - 1):
    L.mark_failed(first[49], "refused again", ledger_path=led_path)
picked, _ = L.pick(L.load(led_path), ready, 5, manual)
ok(first[49] not in picked, "a design that failed 3 times stops blocking the front")

L.mark_uploaded(first[48], "jx", ledger_path=led_path)
ready_now = [d for d in ready if d["saved_poster_id"] != 52]     # re-made: not ready now
picked, notes = L.pick(L.load(led_path), ready_now, 3, manual)
ok(52 not in picked and any("Place 52" in n for n in notes),
   "an unfinished design that is no longer ready is held back and named")

print("ALL PASS")
