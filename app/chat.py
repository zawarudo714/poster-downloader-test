"""
Chat helper — admin ↔ worker direct messaging (one thread per worker).

Design constraints:
- Polling-based, not WebSocket (works fine within the existing /api/state poll).
- Conversation is implicit between (admin, worker) pairs. We identify the
  conversation by `worker_id`; the sender is whoever typed the message.
- Workers see exactly one thread (with admins). Admins see a list of all
  worker threads with unread counts.
- Read state is per-(worker_thread, viewer): admin's view of a thread can
  be unread while the worker's view is read, and vice versa.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from .models import ChatMessage, ChatReadState, User


# ─── Send / list ──────────────────────────────────────────────────────────

def send_message(
    db: Session,
    *,
    worker_id: int,
    sender: User,
    body: str,
) -> ChatMessage:
    """
    Append a message to the (admin, worker_id) thread. Sender can be either
    role; sender_role is captured denormalized for fast filtering.
    """
    body = (body or "").strip()
    if not body:
        raise ValueError("Empty message.")
    if len(body) > 2000:
        body = body[:2000]

    msg = ChatMessage(
        worker_id   = worker_id,
        sender_id   = sender.id,
        sender_role = "admin" if sender.role == "admin" else "worker",
        body        = body,
    )
    db.add(msg)
    db.flush()
    return msg


def list_messages(
    db: Session,
    *,
    worker_id: int,
    limit: int = 200,
    after_id: Optional[int] = None,
):
    """
    Newest-last list, capped at `limit`. If `after_id` is given, only return
    messages with id > after_id (used by the polling delta path).
    """
    q = db.query(ChatMessage).filter(ChatMessage.worker_id == worker_id)
    if after_id:
        q = q.filter(ChatMessage.id > after_id)
    rows = q.order_by(ChatMessage.id.asc()).limit(limit).all()
    return rows


def serialize_message(msg: ChatMessage) -> dict:
    return {
        "id":          msg.id,
        "worker_id":   msg.worker_id,
        "sender_id":   msg.sender_id,
        "sender_role": msg.sender_role,
        "body":        msg.body,
        "created_at":  msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at_iso": msg.created_at.isoformat() + "Z",
    }


# ─── Read state ───────────────────────────────────────────────────────────

def mark_read(db: Session, *, worker_id: int, viewer_id: int) -> None:
    """Bump the viewer's last-read pointer to NOW for this worker thread."""
    row = (
        db.query(ChatReadState)
          .filter_by(worker_id=worker_id, viewer_id=viewer_id)
          .first()
    )
    if row is None:
        row = ChatReadState(worker_id=worker_id, viewer_id=viewer_id, last_read_at=datetime.utcnow())
        db.add(row)
    else:
        row.last_read_at = datetime.utcnow()


def unread_count(db: Session, *, worker_id: int, viewer_id: int) -> int:
    """
    How many messages in this thread are newer than the viewer's last_read_at,
    AND were not sent by the viewer themselves (your own messages aren't unread).
    """
    state = (
        db.query(ChatReadState)
          .filter_by(worker_id=worker_id, viewer_id=viewer_id)
          .first()
    )
    cutoff = state.last_read_at if state else datetime(1970, 1, 1)
    return (
        db.query(func.count(ChatMessage.id))
          .filter(
              ChatMessage.worker_id == worker_id,
              ChatMessage.created_at > cutoff,
              ChatMessage.sender_id != viewer_id,
          )
          .scalar()
        or 0
    )


def admin_thread_summaries(db: Session, *, viewer_id: int) -> list[dict]:
    """
    For the admin chat page: list every worker thread with last-message
    preview + unread count for the admin viewer. Also includes workers
    with zero messages so admin can start a conversation.
    """
    # All workers, in alpha order. Admin can chat with any of them.
    workers = (
        db.query(User)
          .filter(User.role == "worker", User.is_active == 1)
          .order_by(User.username.asc())
          .all()
    )
    out = []
    for w in workers:
        last = (
            db.query(ChatMessage)
              .filter(ChatMessage.worker_id == w.id)
              .order_by(ChatMessage.id.desc())
              .first()
        )
        out.append({
            "worker_id":   w.id,
            "username":    w.username,
            "last_body":   (last.body[:80] + ("…" if len(last.body) > 80 else "")) if last else "",
            "last_at":     last.created_at.strftime("%Y-%m-%d %H:%M") if last else None,
            "last_sender": last.sender_role if last else None,
            "unread":      unread_count(db, worker_id=w.id, viewer_id=viewer_id),
        })
    # Sort: any thread with unread floats up; otherwise by last-message time desc.
    out.sort(key=lambda r: (-(r["unread"] or 0), -(1 if r["last_at"] else 0)))
    return out
