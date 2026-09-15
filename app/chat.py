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
from .timeutil import fmt_local


# ─── Send / list ──────────────────────────────────────────────────────────

import uuid

# What an uploaded chat image may be, and how big. Kept small and explicit
# rather than trusting the filename — the content_type decides.
_CHAT_IMAGE_EXT = {
    "image/jpeg": "jpg", "image/pjpeg": "jpg",
    "image/png": "png", "image/gif": "gif", "image/webp": "webp",
}
_CHAT_IMAGE_MAX_BYTES = 12 * 1024 * 1024


def save_chat_image(upload) -> str:
    """
    Store an uploaded chat image under WORKSPACE_DIR/_chat and return its
    path relative to WORKSPACE_DIR. The name is a fresh UUID, so there is no
    collision and nothing about the uploader's filename reaches the disk.
    Raises ValueError with a plain-words reason the caller shows the user.
    """
    from .config import WORKSPACE_DIR
    ext = _CHAT_IMAGE_EXT.get((getattr(upload, "content_type", "") or "").lower())
    if not ext:
        raise ValueError("That file is not an image I can send. Use JPG, PNG, GIF or WebP.")
    data = upload.file.read()
    if not data:
        raise ValueError("That image was empty.")
    if len(data) > _CHAT_IMAGE_MAX_BYTES:
        raise ValueError("That image is too big. Keep it under 12 MB.")
    rel = f"_chat/{uuid.uuid4().hex}.{ext}"
    dest = WORKSPACE_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel


def send_message(
    db: Session,
    *,
    worker_id: int,
    sender: User,
    body: str,
    image_path: Optional[str] = None,
    image_url: Optional[str] = None,
) -> ChatMessage:
    """
    Append a message to the (admin, worker_id) thread. Sender can be either
    role; sender_role is captured denormalized for fast filtering.

    A message needs SOMETHING — text, an uploaded image, or a pasted link.
    An image-only message carries an empty body, which is why body is
    nullable-in-spirit ("") rather than required here.
    """
    body = (body or "").strip()
    if len(body) > 2000:
        body = body[:2000]
    image_url = (image_url or "").strip() or None
    if image_url and len(image_url) > 1024:
        raise ValueError("That image link is too long.")
    if not body and not image_path and not image_url:
        raise ValueError("Empty message.")

    msg = ChatMessage(
        worker_id   = worker_id,
        sender_id   = sender.id,
        sender_role = "admin" if sender.role == "admin" else "worker",
        body        = body,
        image_path  = image_path,
        image_url   = image_url,
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
        # An external link the browser loads directly, or a flag that this
        # message has an UPLOADED image the client fetches through the
        # role-scoped serve route (/admin/api/chat/image/{id} or
        # /api/chat/image/{id}). serialize stays role-agnostic; the client
        # knows its own role and builds that URL.
        "image_url":    msg.image_url or None,
        "image_upload": bool(msg.image_path),
        "created_at":  fmt_local(msg.created_at, "%Y-%m-%d %H:%M:%S"),
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
          .filter(User.role == "worker", User.is_active == 1, User.is_deleted == 0)
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
            "last_at":     fmt_local(last.created_at, "%Y-%m-%d %H:%M") if last else None,
            "last_sender": last.sender_role if last else None,
            "unread":      unread_count(db, worker_id=w.id, viewer_id=viewer_id),
        })
    # Sort: any thread with unread floats up; otherwise by last-message time desc.
    out.sort(key=lambda r: (-(r["unread"] or 0), -(1 if r["last_at"] else 0)))
    return out
