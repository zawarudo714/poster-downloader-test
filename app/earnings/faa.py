"""
Reading a FineArtAmerica account's ledger.

════════════════════════════════════════════════════════════════════════════
NO BROWSER
════════════════════════════════════════════════════════════════════════════
The control-panel pages are plain server-rendered HTML — the table is in the
response body, not built by JavaScript afterwards. So this is an HTTP session
and an HTML parser, not Selenium and not Playwright.

That matters more than it sounds. A headless browser would mean ~150MB in the
image, a browser process on a 2GB box, and a second thing that can hang. The
uploader on the Windows node needs a real browser because it fills forms and
uploads files; reading a table does not.

If FineArtAmerica ever puts this behind a JavaScript challenge, the fallback
is to run the same parsing against HTML fetched by the node's browser — the
parse functions below take a string, not a URL, precisely so that swap costs
nothing.

════════════════════════════════════════════════════════════════════════════
TWO PAGES, EACH FOR WHAT IT IS BEST AT
════════════════════════════════════════════════════════════════════════════
SALES  — the source for sales themselves. Its rows carry `artworkName` and
         `simpleProductDescription` as separate elements, so the artwork
         title needs no splitting at all, and it keeps real capitalisation
         ("Opeth - My Arms Your Hearse", not the ledger's shouting). Each
         row also contains its own Details panel, hidden rather than absent
         — so quantity, gross price, discount and buyer location cost no
         extra request.

BALANCE — the source for everything that is NOT a sale. Only the ledger
         lists payouts and refunds, and only it carries a running balance:

           · payouts as rows  → "what have I actually been paid"
           · refunds as rows  → an event we can read, rather than a sale
                                that silently vanishes and must be inferred
           · running balance  → a checksum on our own arithmetic

Reading the ledger for sales as well would mean splitting "ARTWORK - Product"
on " - " when both halves contain it. That is how a jigsaw puzzle ended up
with its size as the product and "Jigsaw Puzzle" glued to the artwork title.

════════════════════════════════════════════════════════════════════════════
STOP AT THE FIRST ROW WE ALREADY HAVE
════════════════════════════════════════════════════════════════════════════
Rows are newest-first and order ids only grow, so a daily read touches page
one and stops. An account being read for the FIRST time has nothing to stop
at, so it walks to the end — which IS the backfill. Same code path, no
special case, no "starting balance" to type in.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Optional

import requests

log = logging.getLogger("uvicorn.error")

BASE = "https://fineartamerica.com"
LOGIN_URL = f"{BASE}/loginpost.php"
LOGIN_PAGE = f"{BASE}/login.html"
BALANCE_URL = f"{BASE}/controlpanel/balance"
SALES_URL = f"{BASE}/controlpanel/sales"

TIMEOUT_S = 30
# Their pages are 20-25 rows each. A hard ceiling stops a parsing bug from
# walking forever if "have we seen this" never becomes true.
MAX_PAGES = 200

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


class FaaError(Exception):
    """Anything that stops a read. Carries text safe to show the admin."""


@dataclass
class LedgerRow:
    """One line of the balance ledger, exactly as read."""
    occurred_at: datetime
    entry_type: str                 # sale | payment | refund | other
    order_id: Optional[str]
    description: str
    credit: str                     # "0" when none
    debit: str
    balance_after: Optional[str]
    artwork_name: Optional[str] = None
    product: Optional[str] = None
    # Filled from the row's own hidden panel on the Sales page. Declared here
    # rather than attached dynamically so the shape of a row is readable in
    # one place.
    detail: Optional["SaleDetail"] = None

    @property
    def dedupe_key(self) -> str:
        """
        What makes this row unique for its account.

        Sales have an order id. Payouts do not, so they are keyed on when
        and how much — two payouts of the same amount in the same minute
        would collide, which cannot happen in practice and would be a
        harmless under-count if it did.
        """
        if self.order_id:
            return f"{self.entry_type}:{self.order_id}"
        return f"{self.entry_type}:{self.occurred_at:%Y%m%d%H%M}:{self.debit}"


@dataclass
class SaleDetail:
    """The extra fields the ledger does not carry, from the order panel."""
    website: Optional[str] = None
    product: Optional[str] = None
    quantity: Optional[int] = None
    gross_price: Optional[str] = None
    discount: Optional[str] = None
    net_price: Optional[str] = None
    buyer_location: Optional[str] = None


@dataclass
class ReadResult:
    rows: list[LedgerRow] = field(default_factory=list)
    pages_read: int = 0
    stopped_early: bool = False
    current_balance: Optional[str] = None


# ── Small parsing helpers ───────────────────────────────────────────────────

_MONEY = re.compile(r"-?\$?\s*([0-9][0-9,]*\.?[0-9]*)")


def money(text: Optional[str]) -> str:
    """
    "$4.50" -> "4.50".  "—" or blank -> "0".

    Returned as a STRING. Money is summed as Decimal later; parsing it to a
    float here would bake in error before it was ever stored.
    """
    if not text:
        return "0"
    m = _MONEY.search(text.replace(",", ""))
    return m.group(1) if m else "0"


def _clean(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def parse_datetime(date_text: str, time_text: str = "") -> datetime:
    """
    "08/17/2026" + "03:31 AM" -> datetime.

    Falls back to midnight when the time is missing or unreadable: the date
    is what payout eligibility depends on, and a wrong time is harmless
    where a wrong date is not.
    """
    date_text, time_text = _clean(date_text), _clean(time_text)
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(
                f"{date_text} {time_text}".strip() if "%I" in fmt else date_text, fmt)
        except ValueError:
            continue
    raise FaaError(f"Could not read the date {date_text!r}")


def classify(type_text: str) -> str:
    """Their Type column to ours. Unknown stays 'other' rather than guessing."""
    t = _clean(type_text).lower()
    if "sale" in t:
        return "sale"
    if "payment" in t or "payout" in t:
        return "payment"
    if "refund" in t or "return" in t or "credit" in t:
        return "refund"
    return "other"


# Product names FineArtAmerica actually prints on. Used to find where the
# artwork title ends, because BOTH halves contain " - ":
#
#   "BRAND NEW - THE DEVIL AND GOD... - Jigsaw Puzzle - 20\" x 28\""
#
# Splitting on the last " - " gives the puzzle's SIZE as the product and
# leaves "Jigsaw Puzzle" stuck on the end of the artwork title — which then
# fails to match any design we own. Splitting on the first one is worse:
# "Nirvana - In Utero" loses its own name.
#
# So the split happens at the first separator followed by something that
# looks like a product. An unknown product falls back to the last separator
# and is corrected later from the order's Details panel, which states the
# product exactly.
PRODUCT_WORDS = (
    "acrylic print", "art print", "bath towel", "beach sheet", "beach towel",
    "canvas print", "coffee mug", "duvet cover", "face mask", "fleece blanket",
    "framed print", "greeting card", "hand towel", "jigsaw puzzle", "metal print",
    "ornament", "phone case", "poster", "portable battery charger", "round beach towel",
    "shower curtain", "spiral notebook", "sticker", "t-shirt", "tapestry",
    "throw pillow", "tote bag", "weekender tote bag", "wood print", "yoga mat",
    "zip pouch", "coasters", "puzzle", "tank top", "sweatshirt", "long sleeve",
)


def split_description(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    "OPETH - MY ARMS YOUR HEARSE - Beach Towel" -> (artwork, product).

    A guess, and treated as one: the raw description is stored alongside, and
    the Details panel overwrites both fields for any sale we enrich.
    """
    text = _clean(text)
    if " - " not in text:
        return (text or None), None

    # First separator whose right-hand side begins with a known product.
    parts = text.split(" - ")
    for i in range(1, len(parts)):
        tail = " - ".join(parts[i:])
        low = tail.lower()
        if any(low.startswith(word) for word in PRODUCT_WORDS):
            return _clean(" - ".join(parts[:i])) or None, _clean(tail) or None

    artwork, product = text.rsplit(" - ", 1)
    return _clean(artwork) or None, _clean(product) or None


# ── The pages ───────────────────────────────────────────────────────────────

def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def parse_balance_page(html: str) -> tuple[list[LedgerRow], Optional[str], Optional[int]]:
    """
    Read one page of the balance ledger.

    Returns (rows, current balance, total row count). The total comes from
    `data-num-rows` on the container — knowing it up front means the walker
    stops because it is finished, not because a page happened to look empty.
    """
    soup = _soup(html)

    current_balance = None
    for tag in soup.find_all(string=re.compile(r"Current Balance")):
        current_balance = money(_clean(tag.parent.get_text() if tag.parent else str(tag)))
        break

    total_rows = None
    main = soup.find(id="mainDiv")
    if main and main.get("data-num-rows"):
        try:
            total_rows = int(main["data-num-rows"])
        except (TypeError, ValueError):
            total_rows = None

    rows: list[LedgerRow] = []
    for tr in soup.find_all("div", class_="tableRowDiv") or []:
        cells = tr.find_all("div", class_="tableElement")
        if len(cells) < 6:
            continue
        texts = [_clean(c.get_text(" ", strip=True)) for c in cells]
        rows.extend(_row_from_cells(texts) or [])

    # Their markup has used real <tr> as well as div-tables. Both are read
    # rather than assuming one, because a layout change should degrade to
    # "found nothing" and not to a silent half-read.
    if not rows:
        for tr in soup.find_all("tr"):
            texts = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
            if len(texts) >= 6:
                rows.extend(_row_from_cells(texts) or [])

    return rows, current_balance, total_rows


def _row_from_cells(texts: list[str]) -> list[LedgerRow]:
    """
    Build a row from one table row's cell text.

    Column order on the Balance page:
        Date(+time) · Type · Order ID · Description · Credit · Debit · Balance
    """
    try:
        raw_date, raw_type, raw_order, desc, credit, debit = texts[:6]
        balance = texts[6] if len(texts) > 6 else None
    except ValueError:
        return []

    if not re.match(r"\d{2}/\d{2}/\d{4}", raw_date):
        return []                              # header, or something else

    # "08/17/2026 03:31 AM" arrives as one cell once whitespace is collapsed.
    parts = raw_date.split()
    date_part = parts[0]
    time_part = " ".join(parts[1:3]) if len(parts) >= 3 else ""

    entry_type = classify(raw_type)
    order_id = None if _clean(raw_order) in ("", "—", "-") else _clean(raw_order)
    artwork, product = (split_description(desc) if entry_type in ("sale", "refund")
                        else (None, None))

    return [LedgerRow(
        occurred_at=parse_datetime(date_part, time_part),
        entry_type=entry_type,
        order_id=order_id,
        description=_clean(desc),
        credit=money(credit),
        debit=money(debit),
        balance_after=money(balance) if balance else None,
        artwork_name=artwork,
        product=product,
    )]


def parse_sales_page(html: str) -> tuple[list[LedgerRow], Optional[int]]:
    """
    Read one page of the Sales table.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS THE BETTER SOURCE FOR SALES
    ════════════════════════════════════════════════════════════════════════
    Three things this page gives that the balance ledger does not:

      · `artworkName` and `simpleProductDescription` are SEPARATE elements,
        so the artwork title needs no guessing at all. The ledger crams both
        into one string and leaves us splitting on " - ", which is exactly
        where the jigsaw-puzzle row went wrong.
      · The title keeps its real capitalisation — "Opeth - My Arms Your
        Hearse" rather than the ledger's shouted "OPETH - MY ARMS YOUR
        HEARSE". Matching against our stored titles is much happier with it.
      · The Details panel is ALREADY in the page, inside `tableBottomRowDiv`
        with `data-expanded="0"` merely hiding it. So quantity, gross price,
        discount and buyer location cost no extra request — a backfill of
        70 sales is 4 page loads, not 74.

    The ledger is still read, for payouts, refunds and the running balance.
    Each page is used for what it is actually best at.
    """
    soup = _soup(html)

    total_rows = None
    main = soup.find(id="mainDiv")
    if main and main.get("data-num-rows"):
        try:
            total_rows = int(main["data-num-rows"])
        except (TypeError, ValueError):
            total_rows = None

    rows: list[LedgerRow] = []
    for row_div in soup.find_all("div", class_="tableRowDiv"):
        def cell(name: str) -> str:
            el = row_div.find("div", class_=lambda c: c and name in c.split())
            return _clean(el.get_text(" ", strip=True)) if el else ""

        date_text = cell("tableElementDate")
        order_id = cell("tableElementOrderId")
        if not date_text or not order_id:
            continue

        artwork_el = row_div.find("p", class_="artworkName")
        product_el = row_div.find("p", class_="simpleProductDescription")

        # Extended price is the line total — quantity already applied — and
        # is what actually reaches you.
        extended = cell("tableElementExtendedPrice") or cell("tableElementPrice")

        qty = None
        raw_qty = cell("tableElementQuantity")
        if raw_qty:
            try:
                qty = int(re.sub(r"\D", "", raw_qty) or 0) or None
            except ValueError:
                qty = None

        row = LedgerRow(
            occurred_at=parse_datetime(date_text),
            entry_type="sale",
            order_id=order_id,
            description=_clean(cell("tableElementOrderDetails")),
            credit=money(extended),
            debit="0",
            balance_after=None,
            artwork_name=_clean(artwork_el.get_text()) if artwork_el else None,
            product=_clean(product_el.get_text()) if product_el else None,
        )

        # The hidden panel is right there in the same row.
        detail = parse_sale_details(str(row_div), order_id)
        if detail and detail.quantity is None:
            detail.quantity = qty
        row.detail = detail
        rows.append(row)

    return rows, total_rows


def read_sales(
    session: requests.Session,
    *,
    is_known: Callable[[str], bool],
    max_pages: int = MAX_PAGES,
) -> ReadResult:
    """
    Walk the Sales table newest-first, stopping at the first known order.

    Same incremental rule as the ledger: a daily read touches one page, and
    an account read for the first time walks to the end, which IS the
    backfill.
    """
    result = ReadResult()
    seen_total: Optional[int] = None

    for page in range(1, max_pages + 1):
        url = SALES_URL if page == 1 else f"{SALES_URL}?page={page}"
        try:
            resp = session.get(url, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            raise FaaError(f"Could not read sales page {page}: {e}")
        if resp.status_code != 200:
            raise FaaError(f"Sales page {page} returned HTTP {resp.status_code}")

        rows, total = parse_sales_page(resp.text)
        result.pages_read = page
        if page == 1:
            seen_total = total
        if not rows:
            break

        for row in rows:
            if is_known(row.dedupe_key):
                result.stopped_early = True
                return result
            result.rows.append(row)

        if seen_total is not None and len(result.rows) >= seen_total:
            break

    return result


def parse_sale_details(html: str, order_id: str) -> Optional[SaleDetail]:
    """
    Pull the label/value pairs out of an order's Details panel.

    Read BY LABEL, never by position — a reordered or added row would
    otherwise shift every value silently, which is the worst way for a
    scraper to fail.
    """
    soup = _soup(html)
    pairs: dict[str, str] = {}
    for div in soup.find_all("div", class_="additionalDetailDiv"):
        label = div.find("p", class_="additionalDetailLabel")
        value = div.find("p", class_="additionalDetailValue")
        if not label or not value:
            continue
        pairs[_clean(label.get_text()).rstrip(":").lower()] = _clean(value.get_text())

    if not pairs:
        return None
    if pairs.get("order id") and order_id and pairs["order id"] != order_id:
        return None                            # a different order's panel

    qty = None
    if pairs.get("quantity"):
        try:
            qty = int(re.sub(r"\D", "", pairs["quantity"]) or 0) or None
        except ValueError:
            qty = None

    return SaleDetail(
        website=pairs.get("website"),
        product=pairs.get("product"),
        quantity=qty,
        gross_price=money(pairs.get("price")),
        discount=money(pairs.get("discount")),
        net_price=money(pairs.get("net price")),
        buyer_location=pairs.get("buyer's location"),
    )


# ── Session ─────────────────────────────────────────────────────────────────

def login(email: str, password: str) -> requests.Session:
    """
    Sign in and return a session carrying the cookies.

    The login form is the same one the uploader drives on the node. Posting
    it directly is enough here because nothing afterwards needs JavaScript.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    try:
        session.get(LOGIN_PAGE, timeout=TIMEOUT_S)      # sets the initial cookies
        resp = session.post(
            LOGIN_URL,
            data={"email": email, "password": password, "rememberme": "1"},
            timeout=TIMEOUT_S,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        raise FaaError(f"Could not reach FineArtAmerica: {e}")

    if resp.status_code >= 400:
        raise FaaError(f"FineArtAmerica returned HTTP {resp.status_code} on login.")

    # Proven by fetching a page that only exists behind a login, rather than
    # by trusting the login response — which returns 200 for a bad password.
    check = session.get(BALANCE_URL, timeout=TIMEOUT_S)
    if "Current Balance" not in check.text:
        raise FaaError(
            "Signed in but the balance page was not returned — the email or "
            "password is probably wrong, or the account is locked.")
    return session


def read_ledger(
    session: requests.Session,
    *,
    is_known: Callable[[str], bool],
    max_pages: int = MAX_PAGES,
) -> ReadResult:
    """
    Walk the ledger newest-first, stopping at the first row already stored.

    `is_known(dedupe_key)` is supplied by the caller so this function never
    touches the database — which is what makes it testable against a saved
    HTML file.
    """
    result = ReadResult()
    seen_total: Optional[int] = None

    for page in range(1, max_pages + 1):
        url = BALANCE_URL if page == 1 else f"{BALANCE_URL}?page={page}"
        try:
            resp = session.get(url, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            raise FaaError(f"Could not read page {page}: {e}")
        if resp.status_code != 200:
            raise FaaError(f"Page {page} returned HTTP {resp.status_code}")

        rows, balance, total = parse_balance_page(resp.text)
        result.pages_read = page
        if page == 1:
            result.current_balance = balance
            seen_total = total

        if not rows:
            break

        for row in rows:
            if is_known(row.dedupe_key):
                result.stopped_early = True
                return result
            result.rows.append(row)

        # Finished when we have as many rows as the page said exist.
        if seen_total is not None and len(result.rows) >= seen_total:
            break

    return result


def fetch_sale_detail(session: requests.Session, order_id: str) -> Optional[SaleDetail]:
    """
    The Details panel for one order.

    Called only for rows we have not seen before, so a normal night is zero
    to a handful of requests. The first read of an account is one per sale,
    which is the price of a backfill and is paid once.
    """
    try:
        resp = session.get(f"{SALES_URL}?orderid={order_id}", timeout=TIMEOUT_S)
    except requests.RequestException as e:
        log.warning("Could not read details for order %s: %s", order_id, e)
        return None
    if resp.status_code != 200:
        return None
    return parse_sale_details(resp.text, order_id)
