"""
What every paid OpenAI answer cost — the Usage tab (v292).

════════════════════════════════════════════════════════════════════════════
ONE SOURCE: OPENAI'S OWN TOKEN COUNTS, ON THIS SITE'S OWN ANSWERS
════════════════════════════════════════════════════════════════════════════
Every answer OpenAI sends carries a `usage` block. Priced at OpenAI's rates,
those counts equalled the owner's usage dashboard (filtered to this site's
key) to the cent on 7 of 7 UTC days (MEASURED 2026-11-04). So the tab needs
no admin key, and other keys the owner tests with never mix in (DECIDED
2026-11-04: source 1 only).

Rows are written in three places, and only these:
  * gpt_images.generate / generate_step2, right after a live call returns;
  * ai_batches.collect, from the answer file it has just downloaded;
  * sweep() below, for a collected batch whose file was never copied — every
    batch from before v292, and any that a crash interrupted. The invariant
    is "every collected batch has usage_recorded_at", and Diagnostics watches
    it (check_usage_recorded).

A batch answer is keyed by (batch_id, custom_id), so copying a file twice
adds nothing. An answer that was billed but not filed (dropped, or failed to
file) is still recorded: OpenAI charged for it.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")

# OpenAI's BATCH prices for gpt-image-2.5-sunburst, dollars per million
# tokens. MEASURED 2026-11-04: these reproduce the usage dashboard to the
# cent. A live call costs double (LIVE_FACTOR). If OpenAI changes a price,
# change it here; rows already written keep the price they were written at.
BATCH_PRICE_PER_M = {"text_in": 2.50, "image_in": 4.00, "image_out": 15.00}
LIVE_FACTOR = 2.0

# How often sweep() may fetch one old answer file. A file of 400 pictures is
# about a gigabyte, so history is filled in slowly, one batch at a time.
SWEEP_EVERY_S = 120
_last_sweep: dict[int, float] = {}

# Settings whose change is marked on every chart, with the label shown.
MARKED_KEYS = {
    "openai_prompt": "painting prompt",
    "step2_prompt": "step 2 prompt",
    "step2_prompt_sky": "step 2 sky prompt",
    "openai_style_image": "style picture",
    "openai_model": "painting model",
    "step2_model": "step 2 model",
    "openai_quality": "painting quality",
    "step2_quality": "step 2 quality",
    "openai_size": "painting size",
    "step2_size": "step 2 size",
    "openai_batch_enabled": "batch on/off",
}


def cost(text_in: int, image_in: int, image_out: int, mode: str) -> float:
    p = BATCH_PRICE_PER_M
    usd = (text_in * p["text_in"] + image_in * p["image_in"]
           + image_out * p["image_out"]) / 1_000_000
    return round(usd * (LIVE_FACTOR if mode == "live" else 1.0), 6)


def tokens_of(usage: dict) -> tuple[int, int, int]:
    """(text in, image in, out) from an answer's `usage` block."""
    detail = usage.get("input_tokens_details") or {}
    text = int(detail.get("text_tokens") or 0)
    image = int(detail.get("image_tokens")
                or max(0, int(usage.get("input_tokens") or 0) - text))
    return text, image, int(usage.get("output_tokens") or 0)


def _kind(db: Session, stage: int, poster_id: Optional[int], at: datetime) -> str:
    """'repaint' when the design already had a picture of this stage before
    `at`. Read from the pictures themselves, so history and new rows follow
    one rule. Free Photopea edits (`variant`) are not pictures OpenAI made."""
    from .models import ProcessedImage as PI
    if not poster_id:
        return "first"
    q = db.query(PI.id).filter(PI.saved_poster_id == poster_id,
                               PI.variant.is_(None), PI.created_at < at)
    q = q.filter(PI.stage == 2) if stage == 2 else q.filter(PI.stage.is_(None))
    return "repaint" if q.first() is not None else "first"


def record_live(db: Session, project, *, stage: int, gen, model: str,
                poster_id: Optional[int], purpose: str = "picture") -> None:
    """One live answer. Added to the caller's session, not committed: every
    caller commits straight after the call (the claim, or the test log)."""
    from .models import AIUsage
    if project is None:
        return
    now = datetime.utcnow()
    image_in, text_in, out = (int(gen.input_tokens or 0), int(gen.text_tokens or 0),
                              int(gen.output_tokens or 0))
    db.add(AIUsage(
        project_id=project.id, occurred_at=now, sent_at=now, stage=stage,
        mode="live", model=model or None, saved_poster_id=poster_id,
        kind="test" if purpose == "test" else _kind(db, stage, poster_id, now),
        text_in=text_in, image_in=image_in, image_out=out,
        cost_usd=cost(text_in, image_in, out, "live")))


def record_file(db: Session, project, batch, path: Path) -> int:
    """Copy the token counts out of a batch's answer file, then mark the
    batch recorded. Returns how many rows were added. Commits."""
    from .models import AIBatchItem, AIUsage
    items = {it.custom_id: it for it in
             db.query(AIBatchItem).filter(AIBatchItem.batch_id == batch.id).all()}
    have = {cid for (cid,) in db.query(AIUsage.custom_id)
            .filter(AIUsage.batch_id == batch.id).all()}
    added = 0
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            cid = line.get("custom_id") or ""
            body = (line.get("response") or {}).get("body") or {}
            usage = body.get("usage") if isinstance(body, dict) else None
            if not cid or cid in have or not usage:
                continue
            created = body.get("created")
            at = (datetime.utcfromtimestamp(int(created)) if created
                  else (batch.collected_at or datetime.utcnow()))
            text_in, image_in, out = tokens_of(usage)
            item = items.get(cid)
            poster_id = item.saved_poster_id if item is not None else None
            db.add(AIUsage(
                project_id=project.id, occurred_at=at, sent_at=batch.created_at,
                stage=int(batch.stage), mode="batch", model=batch.model,
                kind=_kind(db, int(batch.stage), poster_id, at),
                saved_poster_id=poster_id, batch_id=batch.id, custom_id=cid,
                text_in=text_in, image_in=image_in, image_out=out,
                cost_usd=cost(text_in, image_in, out, "batch")))
            have.add(cid)
            added += 1
    batch.usage_recorded_at = datetime.utcnow()
    db.commit()
    return added


def sweep(db: Session, project) -> bool:
    """Copy the token counts of ONE collected batch that has none yet.
    Returns True if a batch was settled. Never spends: downloading an answer
    file is free."""
    from . import gpt_images as G
    from .ai_batches import _tmp_dir
    from .models import AIBatch
    if time.monotonic() - _last_sweep.get(project.id, -1e9) < SWEEP_EVERY_S:
        return False
    _last_sweep[project.id] = time.monotonic()
    batch = (db.query(AIBatch)
               .filter(AIBatch.project_id == project.id,
                       AIBatch.collected_at.isnot(None),
                       AIBatch.usage_recorded_at.is_(None))
               .order_by(AIBatch.id.asc()).first())
    if batch is None:
        return False
    if not batch.output_file_id:
        # Failed validation, cancelled before any answer, or never sent:
        # OpenAI answered nothing, so it billed nothing.
        batch.usage_recorded_at = datetime.utcnow()
        batch.usage_note = "no answer file, so nothing was billed"
        db.commit()
        return True
    dest = _tmp_dir() / f"usage_{batch.id}.jsonl"
    try:
        G.file_download(G.api_key_for(db, project), batch.output_file_id, dest)
        n = record_file(db, project, batch, dest)
        log.info("Usage: batch %s copied, %s answers", batch.id, n)
    except G.PermanentFailure as e:
        db.rollback()
        batch.usage_recorded_at = datetime.utcnow()
        batch.usage_note = f"answer file could not be read: {e}"[:255]
        db.commit()
    except G.TransientFailure as e:
        db.rollback()
        log.info("Usage: batch %s not copied yet: %s", batch.id, e)
        return False
    finally:
        dest.unlink(missing_ok=True)
    return True


def unrecorded(db: Session, project, *, older_than_h: int = 6) -> int:
    """Collected batches still without token counts after `older_than_h`
    hours — the sweep should have reached them by then."""
    from .models import AIBatch
    cut = datetime.utcnow() - timedelta(hours=older_than_h)
    return (db.query(AIBatch)
              .filter(AIBatch.project_id == project.id,
                      AIBatch.collected_at.isnot(None),
                      AIBatch.collected_at < cut,
                      AIBatch.usage_recorded_at.is_(None)).count())


# ════════════════════════════════════════════════════════════════════════════
#  THE NUMBERS FOR THE PAGE
# ════════════════════════════════════════════════════════════════════════════

def _changes(db: Session, project, since: datetime) -> list[dict]:
    """Setting changes worth a marker, oldest first: {at, key, label}.
    Read from the activity log, which every settings save writes."""
    from .models import ActivityLog
    rows = (db.query(ActivityLog)
              .filter(ActivityLog.action.in_(("pipeline_setting", "pipeline_settings",
                                              "setting_reset")),
                      ActivityLog.created_at >= since)
              .order_by(ActivityLog.created_at.asc()).all())
    out = []
    for r in rows:
        try:
            d = json.loads(r.details or "{}")
        except ValueError:
            continue
        if d.get("project") not in (project.slug, None):
            continue
        if r.action == "pipeline_settings":
            # A group save lists every key on the form; only `changed`
            # (written since v292) says which ones really moved.
            keys = d.get("changed") or []
        else:
            keys = [d.get("key")]
        for k in keys:
            if k in MARKED_KEYS:
                out.append({"at": r.created_at, "key": k, "label": MARKED_KEYS[k]})
    return out


def _approved_by_day(db: Session, project, start: datetime) -> dict[date, int]:
    """Designs approved per UTC day: one per poster per day."""
    from .models import ProcessedImage as PI
    rows = (db.query(PI.saved_poster_id, PI.reviewed_at)
              .filter(PI.project_id == project.id, PI.review_status == "approved",
                      PI.reviewed_at >= start).all())
    seen: dict[date, set] = {}
    for pid, at in rows:
        seen.setdefault(at.date(), set()).add(pid)
    return {d: len(s) for d, s in seen.items()}


def report(db: Session, project, *, start: date, end: date, stage: str = "all",
           kind: str = "all", mode: str = "all", prompt: str = "all",
           today: Optional[date] = None) -> dict:
    """Everything the Usage tab draws, in UTC days. Filters apply to every
    number; the date range applies to the charts, not to the tiles."""
    from .models import AIUsage
    today = today or datetime.utcnow().date()
    month0 = today.replace(day=1)
    floor = min(start, month0, today - timedelta(days=13))
    t0 = datetime.combine(floor, datetime.min.time())

    changes = _changes(db, project, datetime(2000, 1, 1))
    prompt_changes = [c["at"] for c in changes if c["key"] == "openai_prompt"]
    # Prompt N runs from change N-1 to change N. A request carries the
    # prompt in force when it was SENT, so a batch sent before a change and
    # answered after it still counts under the old prompt.
    edges = [None] + prompt_changes + [None]
    versions = [{"id": str(i), "from": edges[i], "to": edges[i + 1]}
                for i in range(len(edges) - 1)]

    def version_of(at: Optional[datetime]) -> str:
        if at is None:
            return "0"
        n = sum(1 for c in prompt_changes if c <= at)
        return str(n)

    rows = (db.query(AIUsage)
              .filter(AIUsage.project_id == project.id, AIUsage.occurred_at >= t0)
              .all())
    keep = []
    for r in rows:
        if stage != "all" and str(r.stage) != stage:
            continue
        if kind != "all" and r.kind != kind:
            continue
        if mode != "all" and r.mode != mode:
            continue
        if prompt != "all" and version_of(r.sent_at or r.occurred_at) != prompt:
            continue
        keep.append(r)

    def series_key(r) -> str:
        if r.kind == "test":
            return "test"
        return f"s{r.stage}_{r.kind}"

    approved = _approved_by_day(db, project, t0)
    days: dict[date, dict] = {}
    d = floor
    while d <= max(end, today):
        days[d] = {"spend": {"s1_first": 0.0, "s1_repaint": 0.0, "s2_first": 0.0,
                             "s2_repaint": 0.0, "test": 0.0},
                   "paintings": 0, "painting_repaints": 0, "step2": 0,
                   "step2_repaints": 0, "text_in_s1": 0, "answers": 0}
        d += timedelta(days=1)
    for r in keep:
        day = days.get(r.occurred_at.date())
        if day is None:
            continue
        day["spend"][series_key(r)] += r.cost_usd
        day["answers"] += 1
        if r.kind == "test":
            continue
        if r.stage == 1:
            day["paintings"] += 1
            day["text_in_s1"] += r.text_in
            day["painting_repaints"] += r.kind == "repaint"
        else:
            day["step2"] += 1
            day["step2_repaints"] += r.kind == "repaint"

    def total(d0: date, d1: date) -> dict:
        """Sums over [d0, d1], both inclusive."""
        spend = n_paint = n_rep = n_s2 = n_s2rep = n_appr = 0
        x = d0
        while x <= d1:
            v = days.get(x)
            if v:
                spend += sum(v["spend"].values())
                n_paint += v["paintings"]; n_rep += v["painting_repaints"]
                n_s2 += v["step2"]; n_s2rep += v["step2_repaints"]
            n_appr += approved.get(x, 0)
            x += timedelta(days=1)
        return {"spend": round(spend, 4), "approved": n_appr,
                "per_approved": round(spend / n_appr, 4) if n_appr else None,
                "repaint_rate": round(n_rep / n_paint, 4) if n_paint else None,
                "step2_repaint_rate": round(n_s2rep / n_s2, 4) if n_s2 else None}

    last7 = total(today - timedelta(days=6), today)
    prev7 = total(today - timedelta(days=13), today - timedelta(days=7))
    yday = total(today - timedelta(days=1), today - timedelta(days=1))
    mtd = total(month0, today)
    days_in_month = ((month0 + timedelta(days=32)).replace(day=1) - month0).days
    # The month so far, carried on at the last 7 days' pace — not at the
    # month's own average, which the first days of a month swing wildly.
    forecast = round(mtd["spend"] + last7["spend"] / 7 * (days_in_month - today.day), 2)

    chart = []
    x = start
    while x <= end:
        v = days.get(x)
        if v is not None:
            n = v["paintings"]
            spend = sum(v["spend"].values())
            appr = approved.get(x, 0)
            chart.append({
                "day": x.isoformat(),
                "spend": {k: round(s, 4) for k, s in v["spend"].items()},
                "total": round(spend, 4),
                "paintings": n, "painting_repaints": v["painting_repaints"],
                "step2": v["step2"], "step2_repaints": v["step2_repaints"],
                "repaint_rate": round(v["painting_repaints"] / n, 4) if n else None,
                "step2_repaint_rate": (round(v["step2_repaints"] / v["step2"], 4)
                                       if v["step2"] else None),
                "approved": appr,
                "per_approved": round(spend / appr, 4) if appr else None,
                "text_per_painting": round(v["text_in_s1"] / n) if n else None,
                "answers": v["answers"],
            })
        x += timedelta(days=1)

    def iso(t):
        return t.isoformat() + "Z" if t else None

    return {
        "today": today.isoformat(),
        "tiles": {"today": total(today, today), "yesterday": yday,
                  "last7": last7, "prev7": prev7, "mtd": mtd, "forecast": forecast},
        "days": chart,
        "markers": [{"at": iso(c["at"]), "day": c["at"].date().isoformat(),
                     "label": c["label"]} for c in changes
                    if start <= c["at"].date() <= end],
        "prompt_versions": [{"id": v["id"], "from": iso(v["from"]), "to": iso(v["to"])}
                            for v in versions],
        "prices": {"batch_per_m": BATCH_PRICE_PER_M, "live_factor": LIVE_FACTOR},
        "unrecorded_batches": unrecorded(db, project, older_than_h=0),
    }
