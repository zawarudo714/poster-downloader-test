"""
FineArtAmerica listing checker — a MEASURING tool, not a finished feature.

════════════════════════════════════════════════════════════════════════════
WHAT IT IS FOR
════════════════════════════════════════════════════════════════════════════
Before any reconciliation scanner gets built, three things have to be known
rather than assumed. Guessing any of them would put a large build on top of
an unexamined belief, which is how the last two features lost deploy cycles:

  1. Does the address rule actually work? Title + artist name should give the
     public address of a listing. Six examples say yes. Six is not many.

  2. What does FAA return for a listing that is NOT there? A real 404, or a
     200 carrying a "Page Not Found" page? The whole scanner is cheap if it
     is a status code and needs parsing if it is not.

  3. WHAT DOES AN INACTIVE LISTING LOOK LIKE — and this is the one that
     matters most. On TeePublic a switched-off design still has a working
     page. If FAA is the same, then "hidden" and "deleted" look identical
     from outside, and the scanner can only ever report "not reachable" and
     ask the owner which it was. If they differ, it can tell them apart on
     its own. Nothing else about the design can be settled until this is.

So this reports SIGNALS and does not pass judgement. It prints what came
back and lets a person decide what it means. A tool that announced
"MISSING" here would be inventing the very answer it exists to find.

════════════════════════════════════════════════════════════════════════════
IT USES THE REAL TITLE NORMALISER, NOT A COPY
════════════════════════════════════════════════════════════════════════════
FAA silently rewrites titles — folds accents, deletes most punctuation, caps
at 100 characters. `pipeline.clean_for_marketplace` already encodes exactly
what it does, measured character by character. This tool lifts that function
out of the shipped source and runs it, rather than keeping its own copy,
because two definitions of "what does the listing actually say" would drift
and this tool exists to tell the truth about that.

════════════════════════════════════════════════════════════════════════════
NO INSTALL, NO DEPENDENCIES
════════════════════════════════════════════════════════════════════════════
Standard library only, so it runs on the laptop by double-clicking
FAA_URL_CHECK.bat with nothing set up. Requests go out one at a time with a
gap, because a burst of them from one address is exactly how a site that was
merely indifferent becomes a site that is interested.
"""

from __future__ import annotations

import ast
import gzip
import io
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "https://fineartamerica.com/featured/"
ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "app" / "pipeline.py"

# Deliberately NOT a browser's. The TeePublic store listing returned 403 the
# moment a lone Chrome User-Agent was bolted on, while a bare client worked
# perfectly — a half-disguise is more suspicious than none. The GUI has a
# switch so the difference can be measured rather than argued about.
PLAIN_UA = "python-urllib/3"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ════════════════════════════════════════════════════════════════════════════
#  THE ADDRESS RULE
# ════════════════════════════════════════════════════════════════════════════

def load_cleaner():
    """
    Pull `clean_for_marketplace` out of pipeline.py and run it here.

    Importing the module would drag in SQLAlchemy and FastAPI, which this
    tool must not need. Parsing the file and executing the one pure function
    keeps a single definition of FAA's title behaviour with no copy to rot.
    """
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    ns = {"re": re}
    wanted = {"clean_for_marketplace"}
    picked = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "").startswith(("_FAA_", "_MARKETPLACE_",
                                                 "_DASH_", "MARKETPLACE_"))
                for t in node.targets):
            picked.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            picked.append(node)
    mod = ast.Module(picked, [])
    ast.fix_missing_locations(mod)
    exec(compile(mod, str(PIPELINE), "exec"), ns)
    return ns["clean_for_marketplace"]


def slug(text: str) -> str:
    """
    The address form of a title or an artist name.

    Measured from six real listings across two shops:

        "Alicia Keys - #B"      -> alicia-keys-b
        "The Killing - 2011 C"  -> the-killing-2011-c
        "White And Black"       -> white-and-black

    Every character that is not a letter or a digit becomes a gap, and runs
    of gaps collapse to one hyphen. The separating " - " and the "#" both
    simply disappear into that, which is why no special case is needed for
    either.
    """
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def listing_url(title: str, artist: str, cleaner=None) -> tuple[str, str]:
    """(the address, the title FAA would actually have stored)."""
    stored = cleaner(title) if cleaner else title
    return f"{BASE}{slug(stored)}-{slug(artist)}.html", stored


# ════════════════════════════════════════════════════════════════════════════
#  FETCHING, AND WHAT CAME BACK
# ════════════════════════════════════════════════════════════════════════════

def visible_text(html: str) -> str:
    """Rough text of the page, with script and style thrown away."""
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html))


def fetch(url: str, *, browser_ua: bool, head_only: bool = False,
          timeout: int = 30) -> dict:
    """
    One request. Returns everything observed and decides nothing.

    A 404 is a RESULT here, not an error — measured 2026-08-24, FAA answers
    a missing listing with a real 404 rather than a 200 carrying an error
    page, so the status code is very possibly the whole check. It must come
    back as data rather than as an exception someone has to remember to
    catch.

    ════════════════════════════════════════════════════════════════════════
    HEAD, IF FAA HONOURS IT
    ════════════════════════════════════════════════════════════════════════
    A live listing is ~190KB and the Page Not Found page is ~128KB. Across
    4,811 uploaded images that is about 900MB and three and a quarter hours
    of downloading pages we only want the first line of. A HEAD request
    answers the same question for almost nothing.

    Whether FAA honours it is NOT assumed — that is what the toggle is for.
    Some servers return 200 to a HEAD for a page that would GET a 404, and
    believing that would report every missing listing as healthy. Run both
    and compare before trusting it.
    """
    started = time.time()
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA if browser_ua else PLAIN_UA,
        "Accept": "text/html",
    }, method="HEAD" if head_only else "GET")
    out = {"url": url, "status": None, "final_url": url, "error": "",
           "bytes": 0, "ms": 0, "page_title": "", "body": "",
           "headers": {"method": "HEAD" if head_only else "GET"}}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            out["status"] = resp.status
            out["final_url"] = resp.geturl()
            out["body"] = raw.decode("utf-8", "replace")
            out["bytes"] = len(raw)
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        out["status"] = e.code
        out["final_url"] = e.geturl()
        out["body"] = raw.decode("utf-8", "replace")
        out["bytes"] = len(raw)
    except Exception as e:                      # DNS, timeout, TLS
        out["error"] = f"{type(e).__name__}: {e}"

    out["ms"] = int((time.time() - started) * 1000)
    m = re.search(r"(?is)<title>(.*?)</title>", out["body"])
    out["page_title"] = (m.group(1).strip() if m else "")[:120]
    return out


def signals(result: dict, title: str, artist: str) -> dict:
    """
    What the page says about itself. Named signals, not a verdict.

    Both directions are recorded on purpose. The positive one — is the thing
    we came for actually here — is what a real check should eventually use,
    because it survives FAA redesigning its error page. The negative one is
    recorded only so the two can be COMPARED while the rule is still being
    worked out.
    """
    text = visible_text(result.get("body") or "")
    lower = text.lower()
    return {
        # Negative: the error page's own words, matched as VISIBLE TEXT
        # rather than markup, because class names change and sentences do not.
        "says_not_found": "page not found" in lower
                          or "the page that you requested can not be found" in lower,
        # Positive: what a real listing should carry.
        "has_title":  title.lower() in lower,
        "has_artist": artist.lower() in lower,
        # MEASURED WORTHLESS, 2026-08-24, and kept only to say so.
        #
        # The Page Not Found page carries FRAMED PRINTS / CANVAS PRINTS /
        # ART PRINTS too — "click one of the products below to start
        # shopping". So this is true on BOTH pages and proves nothing. It
        # looked like the most obviously positive marker of the four, which
        # is exactly why the signals are reported separately instead of
        # being blended into one confident verdict: a combined check would
        # have leaned on it and nobody would have noticed.
        "has_buy":    any(w in lower for w in ("add to cart", "select a print",
                                               "framed print", "canvas print")),
        "redirected": result.get("final_url", "") != result.get("url", ""),
    }


def describe(result: dict, sig: dict) -> str:
    """
    One short phrase. Says UNCLEAR when it is unclear, which is the point.

    ════════════════════════════════════════════════════════════════════════
    "NOT THERE" AND "WE COULD NOT LOOK" ARE DIFFERENT ANSWERS
    ════════════════════════════════════════════════════════════════════════
    A 404 is evidence that the listing is gone. A 403, a 429, a 5xx or a
    timeout is evidence of nothing at all — we were blocked, or the site had
    a moment. Collapsing the second into the first would report a healthy
    listing as a copyright takedown and send the owner hunting one.

    Same shape as the TeePublic wall, where the header logo is the only
    thing separating "no search results" from "we never actually looked".
    Here the status code does that job, and it has to be honoured just as
    carefully.
    """
    status = result["status"]
    if result["error"]:
        return f"COULD NOT LOOK — {result['error'].split(':')[0]}"
    if status in (403, 429) or (status or 0) >= 500:
        return f"COULD NOT LOOK — HTTP {status}, not evidence of anything"
    if status == 404:
        return "GONE (HTTP 404)"
    if sig["says_not_found"]:
        return f"NOT FOUND page, but HTTP {status} — check this one by hand"
    if sig["has_title"] and sig["has_artist"]:
        return f"looks LIVE (HTTP {status})"
    if result["headers"].get("method") == "HEAD" and status == 200:
        return "HTTP 200 to a HEAD — no body to confirm it with"
    if status == 200:
        return "HTTP 200 but the title/artist are not on it — UNCLEAR"
    return f"HTTP {status} — UNCLEAR"


# ════════════════════════════════════════════════════════════════════════════
#  THE WINDOW
# ════════════════════════════════════════════════════════════════════════════

HELP = (
    "Paste one per line. Either a TITLE exactly as it was saved, or a full "
    "https:// address.\n"
    "Titles are turned into an address using the artist name below. "
    "Addresses are fetched as given.\n"
    "Include a few you KNOW are live, a few you know are gone, and — most "
    "importantly — one you have switched to INACTIVE by hand."
)


def main() -> int:
    import tkinter as tk
    from tkinter import ttk, messagebox

    try:
        cleaner = load_cleaner()
    except Exception as e:
        cleaner = None
        load_error = str(e)
    else:
        load_error = ""

    root = tk.Tk()
    root.title("FineArtAmerica listing check")
    root.geometry("1180x760")

    top = ttk.Frame(root, padding=10)
    top.pack(fill="x")

    ttk.Label(top, text="Artist name (exactly as on the Edit Image page):"
              ).grid(row=0, column=0, sticky="w")
    artist_var = tk.StringVar(value="Golden Reel")
    ttk.Entry(top, textvariable=artist_var, width=32).grid(row=0, column=1,
                                                           sticky="w", padx=6)

    ua_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(top, text="send a browser User-Agent", variable=ua_var
                    ).grid(row=0, column=2, padx=14)

    head_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(top, text="HEAD only (no page body)", variable=head_var
                    ).grid(row=0, column=5, padx=14)

    ttk.Label(top, text="seconds between requests:").grid(row=0, column=3)
    gap_var = tk.StringVar(value="1.0")
    ttk.Entry(top, textvariable=gap_var, width=6).grid(row=0, column=4)

    ttk.Label(root, text=HELP, padding=(10, 0), justify="left",
              foreground="#555").pack(fill="x")

    box = tk.Text(root, height=8, font=("Consolas", 10))
    box.pack(fill="x", padx=10, pady=6)
    box.insert("1.0", "\n".join([
        "Brother Bear - 2003 A",
        "The Killing - 2011 C",
        "Patriot Games - 1992 A",
        "https://fineartamerica.com/featured/the-killing-2011-c-golden-rdeel.html",
    ]))

    bar = ttk.Frame(root, padding=(10, 0))
    bar.pack(fill="x")
    status = tk.StringVar(value=("ready" if not load_error else
                                 f"title normaliser unavailable: {load_error}"))
    run_btn = ttk.Button(bar, text="CHECK")
    run_btn.pack(side="left")
    save_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(bar, text="save each page to tools/faa_pages/",
                    variable=save_var).pack(side="left", padx=12)
    ttk.Button(bar, text="COPY REPORT", command=lambda: copy_report()
               ).pack(side="left")
    ttk.Label(bar, textvariable=status).pack(side="left", padx=12)

    cols = ("input", "url", "status", "verdict", "notfound", "title?",
            "artist?", "buy?", "ms")
    tree = ttk.Treeview(root, columns=cols, show="headings", height=18)
    widths = (210, 330, 55, 210, 70, 55, 60, 50, 55)
    for c, w in zip(cols, widths):
        tree.heading(c, text=c.upper())
        tree.column(c, width=w, anchor="w")
    tree.pack(fill="both", expand=True, padx=10, pady=8)

    report_lines: list[str] = []

    def copy_report():
        if not report_lines:
            messagebox.showinfo("Nothing yet", "Run a check first.")
            return
        root.clipboard_clear()
        root.clipboard_append("\n".join(report_lines))
        status.set("report copied — paste it into the chat")

    def work():
        run_btn.config(state="disabled")
        tree.delete(*tree.get_children())
        report_lines.clear()
        artist = artist_var.get().strip()
        try:
            gap = max(0.0, float(gap_var.get()))
        except ValueError:
            gap = 1.0

        lines = [l.strip() for l in box.get("1.0", "end").splitlines() if l.strip()]
        report_lines.append(
            f"FAA LISTING CHECK  {datetime.now():%Y-%m-%d %H:%M}  "
            f"artist={artist!r}  browser_ua={ua_var.get()}  "
            f"method={'HEAD' if head_var.get() else 'GET'}  n={len(lines)}")
        report_lines.append("")

        pages = ROOT / "tools" / "faa_pages"
        if save_var.get():
            pages.mkdir(parents=True, exist_ok=True)

        for i, line in enumerate(lines, 1):
            if line.lower().startswith("http"):
                url, stored = line, ""
            else:
                url, stored = listing_url(line, artist, cleaner)
            status.set(f"{i} of {len(lines)} — {url}")
            root.update_idletasks()

            res = fetch(url, browser_ua=ua_var.get(),
                        head_only=head_var.get())
            sig = signals(res, stored or line, artist)
            verdict = describe(res, sig)

            tree.insert("", "end", values=(
                line[:40], url.replace(BASE, "…/"), res["status"] or "-",
                verdict,
                "yes" if sig["says_not_found"] else "no",
                "yes" if sig["has_title"] else "no",
                "yes" if sig["has_artist"] else "no",
                "yes" if sig["has_buy"] else "no",
                res["ms"]))

            report_lines.append(f"[{i}] {line}")
            if stored and stored != line:
                report_lines.append(f"     FAA would store the title as: {stored!r}")
            report_lines.append(f"     {url}")
            report_lines.append(
                f"     HTTP {res['status']}  {res['bytes']}b  {res['ms']}ms"
                + (f"  ERROR {res['error']}" if res["error"] else ""))
            if sig["redirected"]:
                report_lines.append(f"     REDIRECTED TO {res['final_url']}")
            report_lines.append(f"     <title> {res['page_title']!r}")
            report_lines.append(
                "     says-not-found=%s  has-title=%s  has-artist=%s  has-buy=%s"
                % (sig["says_not_found"], sig["has_title"],
                   sig["has_artist"], sig["has_buy"]))
            report_lines.append(f"     => {verdict}")
            report_lines.append("")

            if save_var.get() and res["body"]:
                name = re.sub(r"[^a-z0-9]+", "_", url.lower())[-80:] + ".html"
                (pages / name).write_text(res["body"], encoding="utf-8")

            if i < len(lines):
                time.sleep(gap)

        report_lines.append(
            "MEASURED so far — do not re-derive these:\n"
            "  · a missing listing returns a REAL HTTP 404, not a 200 error "
            "page (2026-08-24)\n"
            "  · a plain client is fine from the laptop; no browser "
            "User-Agent needed for public pages\n"
            "  · <title> reads '{stored title} {medium} by {artist} - Fine "
            "Art America' — one line carries everything\n"
            "  · the buy panel appears on the NOT FOUND page too, so it "
            "proves nothing\n"
            "\n"
            "STILL UNKNOWN — the checks above cannot answer these on their "
            "own:\n"
            "  · what an INACTIVE (hidden, not deleted) listing returns. "
            "THIS ONE DECIDES THE DESIGN.\n"
            "  · whether the same result comes back from the Linux server, "
            "which FAA challenges as a bot for signed-in pages\n"
            "  · whether an edited title leaves the old address redirecting\n"
            "  · whether HEAD is honoured — run the same list both ways and "
            "compare the statuses")
        status.set(f"done — {len(lines)} checked")
        run_btn.config(state="normal")

    run_btn.config(command=lambda: threading.Thread(target=work,
                                                    daemon=True).start())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
