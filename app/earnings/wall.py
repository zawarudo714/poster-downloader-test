"""
The recorded mouse paths that get past a marketplace's interstitial wall.

════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS AT ALL
════════════════════════════════════════════════════════════════════════════
TeePublic puts a full-page wall in front of the account page. Its "No Thanks"
control is an <input type="checkbox"> sealed inside a CLOSED shadow root —
which means Selenium cannot find it, and JavaScript running on the page
cannot reach it either. There is no selector to write. Waiting does not help.

A real click does not need a selector: it aims at a POSITION and the browser
hit-tests whatever is underneath, sealed or not. So the owner records himself
moving to it, and we replay that.

════════════════════════════════════════════════════════════════════════════
THE ROTATION IS STRICTLY SEQUENTIAL
════════════════════════════════════════════════════════════════════════════
One counter, advanced on every use, shared across every account. Not "each
account owns a path" — that would tie a bad recording permanently to one
account and make the failure look like an account problem.

Failure counts are recorded but NEVER used to choose. Picking "the path that
works best" would quietly converge on one path, which is the opposite of the
point, and would hide a recording that had started failing.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import WallPath


def paths_for(db: Session, marketplace: str, *, enabled_only: bool = True) -> list[WallPath]:
    q = db.query(WallPath).filter(WallPath.marketplace == marketplace.lower())
    if enabled_only:
        q = q.filter(WallPath.is_enabled == 1)
    return q.order_by(WallPath.id).all()


def next_paths(db: Session, marketplace: str, count: int) -> list[WallPath]:
    """
    The next `count` recordings in the rotation, and move the counter on.

    Handed out as a LIST rather than one at a time on purpose: the node needs
    its retries decided before it starts, so that losing contact with the
    server mid-wall does not leave it with nothing to try. It also means the
    counter advances once per read instead of once per attempt, which keeps
    the rotation readable in the log.
    """
    from ..pipeline import get_setting, set_setting

    available = paths_for(db, marketplace)
    if not available:
        return []

    cursor = int(get_setting(db, "wall_path_cursor") or 0)
    chosen: list[WallPath] = []
    for step in range(min(count, len(available))):
        chosen.append(available[(cursor + step) % len(available)])
    set_setting(db, "wall_path_cursor",
                (cursor + len(chosen)) % len(available), by="wall")
    return chosen


def payload_for(paths: list[WallPath]) -> list[dict]:
    """What the node needs to replay them. Points are already page-relative."""
    out = []
    for p in paths:
        try:
            points = json.loads(p.points_json or "[]")
        except (TypeError, ValueError):
            continue
        if not points:
            continue
        out.append({
            "id": p.id,
            "label": p.label or f"path {p.id}",
            "points": points,
            "duration_ms": p.duration_ms or 0,
            "page_width": p.page_width,
            "page_height": p.page_height,
        })
    return out


def record_outcome(db: Session, path_id: int, *, worked: bool) -> None:
    """
    Count what happened. Reporting only — this never influences the rotation.

    It answers the one question the admin will actually ask when some reads
    get through and others do not: is it ONE bad recording, or has the wall
    changed? A per-path failure count separates those at a glance; a single
    "the wall failed" counter cannot.
    """
    path = db.query(WallPath).filter_by(id=path_id).first()
    if path is None:
        return
    path.use_count = (path.use_count or 0) + 1
    if not worked:
        path.fail_count = (path.fail_count or 0) + 1
    path.last_used_at = datetime.utcnow()


def save_path(db: Session, *, marketplace: str, points: list,
              page_width: Optional[int] = None,
              page_height: Optional[int] = None,
              label: Optional[str] = None,
              created_by: str = "recorder") -> WallPath:
    """
    Store one recording.

    Validated here rather than in the recorder, because the recorder is a
    convenience and this is the gate. A path with two points is a jump, not a
    movement, and would be worse than useless — it would look deliberate in
    the log while behaving like the thing we were avoiding.
    """
    cleaned = []
    for point in points or []:
        try:
            x, y, ms = int(point[0]), int(point[1]), int(point[2])
        except (TypeError, ValueError, IndexError):
            continue
        cleaned.append([x, y, ms])

    if len(cleaned) < 5:
        raise ValueError(
            f"That recording has only {len(cleaned)} usable points. Move the "
            f"mouse to the checkbox rather than jumping to it.")

    row = WallPath(
        marketplace=marketplace.lower(),
        label=label or None,
        points_json=json.dumps(cleaned),
        duration_ms=cleaned[-1][2],
        page_width=page_width,
        page_height=page_height,
        created_by=created_by,
    )
    db.add(row)
    db.flush()          # autoflush is off; the caller wants the id
    if not row.label:
        row.label = f"path {row.id}"
    return row


def overview(db: Session, marketplace: str) -> list[dict]:
    """One line per recording, for the dashboard."""
    return [
        {
            "id": p.id,
            "label": p.label or f"path {p.id}",
            "points": len(json.loads(p.points_json or "[]")),
            "duration_ms": p.duration_ms or 0,
            "used": p.use_count or 0,
            "failed": p.fail_count or 0,
            "enabled": bool(p.is_enabled),
            "last_used": p.last_used_at.isoformat() if p.last_used_at else None,
            "recorded_at": p.created_at.isoformat() if p.created_at else None,
            "page_size": (f"{p.page_width}x{p.page_height}"
                          if p.page_width and p.page_height else None),
        }
        for p in paths_for(db, marketplace, enabled_only=False)
    ]
