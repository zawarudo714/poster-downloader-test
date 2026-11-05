"""
Copy the next N print files for Junique into a folder on this laptop.

    python tools/junique/fetch.py 20

Unfinished or failed designs come first, then new ones in approval order;
the 10 uploaded by hand (MANUAL_UPLOADS.csv) are never fetched. The rule and
the record live in ledger.py. The upload window has the same Fetch button.
"""
import sys

from ledger import fetch

if len(sys.argv) != 2 or not sys.argv[1].isdigit():
    print("Use: python tools/junique/fetch.py 20")
    sys.exit(2)
try:
    r = fetch(int(sys.argv[1]))
except RuntimeError as e:
    print(e)
    sys.exit(1)
print(f"Ledger: {r['uploaded']} uploaded, {r['waiting']} waiting, {r['gave_up']} gave up.")
