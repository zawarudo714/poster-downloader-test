"""
Activity-log helper. Routes call `log()` after each meaningful action.
Kept tiny so we can call it from anywhere without dragging in route deps.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import ActivityLog, User


def log(
    db: Session,
    *,
    user: Optional[User],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
    commit: bool = False,
) -> ActivityLog:
    """
    Append one row to activity_log. Caller is responsible for db.commit() unless
    `commit=True` (handy for one-off operations outside the main request flow).
    """
    row = ActivityLog(
        user_id     = user.id if user else None,
        username    = user.username if user else None,
        action      = action,
        target_type = target_type,
        target_id   = target_id,
        details     = json.dumps(details, default=str) if details is not None else None,
    )
    db.add(row)
    if commit:
        db.commit()
    return row
