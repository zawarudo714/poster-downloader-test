#!/usr/bin/env python3
"""
Turn an existing payment run into a PAY AHEAD run, after the fact.

    python scripts/convert_run_to_pay_ahead.py --run 6 --amount 650 --take-from-run 5
    python scripts/convert_run_to_pay_ahead.py --run 6 --amount 650 --take-from-run 5 --apply

WHY THIS EXISTS (owner, 2026-10-12)
    Money was sent for Sat 3 + Sun 4 Oct before those days were checked:
    650 KES, recorded INSIDE the 1725 KES run (#5). The run for those two
    days (#6) was then recorded at 0 KES with only the 116 pictures that
    were good at the time — so every later fix came back on payday as
    "unpaid", and he had to type custom amounts (Padua: 2499 on screen,
    2492 to send).

    PAY AHEAD runs exist now (payments.advance_poster_ids). This converts
    an old run into one: it adds every picture on its days that is not paid
    yet — flagged and not-yet-looked-at ones included, one per title —
    marks it as paid ahead, and moves the money that really paid for it
    from the other run (the TOTAL of money recorded never changes).

    Without --apply it only PRINTS what would change. Money records are
    changed only with --apply, after he has read the preview.
"""

import argparse
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.audit import log as log_activity          # noqa: E402
from app.db import SessionLocal                     # noqa: E402
from app.models import MasterTitle, PaymentRun, SavedPoster, User  # noqa: E402
from app.payments import (                          # noqa: E402
    advance_poster_ids, held_for_check, parse_decimal, per_day_breakdown,
    ready_to_paint_ids,
)


def _ids(run):
    try:
        return [i for i in json.loads(run.poster_ids_json or "[]") if isinstance(i, int)]
    except (TypeError, ValueError):
        return []


def _show(run, label):
    print(f"  {label}: run #{run.id} {run.period_start} -> {run.period_end} · "
          f"{run.poster_count} pictures · {run.amount_kes} KES · "
          f"{'PAID AHEAD' if run.is_advance else 'normal'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, required=True, help="the run to turn into a pay-ahead")
    ap.add_argument("--amount", required=True, help="KES that really paid for it")
    ap.add_argument("--take-from-run", type=int, default=0,
                    help="the run whose amount held that money (it is lowered by the same)")
    ap.add_argument("--apply", action="store_true", help="really change the records")
    a = ap.parse_args()

    db = SessionLocal()
    run = db.get(PaymentRun, a.run)
    if run is None:
        print(f"No payment run #{a.run}.")
        return 1
    other = db.get(PaymentRun, a.take_from_run) if a.take_from_run else None
    if a.take_from_run and other is None:
        print(f"No payment run #{a.take_from_run}.")
        return 1
    amount = parse_decimal(a.amount)
    moved = amount - parse_decimal(run.amount_kes or "0")

    add = advance_poster_ids(db, worker_id=run.worker_id,
                             start=run.period_start, end=run.period_end)
    add = [i for i in add if i not in set(_ids(run))]
    held = held_for_check(db, add)

    print("BEFORE")
    _show(run, "this run ")
    if other:
        _show(other, "other run")
    print(f"\nPICTURES TO ADD ({len(add)}) — not paid yet, now covered by the money sent ahead:")
    for pid in add:
        sp = db.get(SavedPoster, pid)
        mt = db.get(MasterTitle, sp.master_title_id) if sp else None
        state = {"flag": "flag still open", "look": "waiting for your look"}.get(
            held.get(pid), "ready")
        print(f"  {sp.original_save_date if sp else '?'} · "
              f"{(str(mt.external_id) + '. ' + mt.title) if mt else '?'} · {state}")

    print("\nAFTER")
    print(f"  this run : run #{run.id} · {len(_ids(run)) + len(add)} pictures · "
          f"{amount} KES · PAID AHEAD")
    if other:
        print(f"  other run: run #{other.id} · {other.poster_count} pictures · "
              f"{parse_decimal(other.amount_kes or '0') - moved} KES")
    total_before = parse_decimal(run.amount_kes or "0") + (parse_decimal(other.amount_kes or "0") if other else Decimal("0"))
    total_after = amount + ((parse_decimal(other.amount_kes or "0") - moved) if other else Decimal("0"))
    print(f"  money recorded across these runs: {total_before} KES before, {total_after} KES after")
    if other is None and moved != 0:
        print("  NOTE: no --take-from-run, so the total money recorded changes by "
              f"{moved} KES.")
    if other is not None and parse_decimal(other.amount_kes or "0") - moved < 0:
        print("Refusing: the other run would go below 0 KES.")
        return 1

    if not a.apply:
        print("\nNothing changed. Run again with --apply to do it.")
        return 0

    admin = db.query(User).filter_by(role="admin").order_by(User.id).first()
    ids = sorted(set(_ids(run)) | set(add))
    run.poster_ids_json = json.dumps(ids)
    run.poster_count = len(ids)
    run.by_day_json = json.dumps(per_day_breakdown(db, poster_ids=ids))
    run.is_advance = 1
    run.amount_kes = str(amount)
    run.note = ((run.note or "") + f" · Converted to PAY AHEAD on 2026-10-12: "
                f"{len(add)} pictures not checked at the time added, {amount} KES "
                f"recorded here" + (f" (moved from run #{other.id})" if other else "")).strip(" ·")
    if other:
        other.amount_kes = str(parse_decimal(other.amount_kes or "0") - moved)
        other.note = ((other.note or "") + f" · {moved} KES of this moved to run "
                      f"#{run.id}, the pay-ahead it really paid for.").strip(" ·")
    log_activity(db, user=admin, action="payment_run_pay_ahead",
                 target_type="payment_run", target_id=run.id,
                 details={"added": add, "amount": str(amount),
                          "moved_from_run": other.id if other else None,
                          "moved_kes": str(moved)})
    # What has passed its checks goes on to painting, exactly as a payment
    # would send it (flagged ones wait until they pass).
    try:
        from app import pipeline as P
        ready = ready_to_paint_ids(db, run.worker_id, add)
        if ready:
            P.greenlight_for_payment_run(db, run, by="pay-ahead repair", poster_ids=ready)
    except Exception as e:  # recording the money must not fail on painting
        print(f"(Could not send ready pictures to painting: {e})")
    db.commit()
    print("\nDone. The records above are now in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
