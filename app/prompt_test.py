"""
PROMPT TEST MODE — trying a new painting prompt on a handful of repaints.

════════════════════════════════════════════════════════════════════════════
WHAT THE OWNER ASKED FOR (2026-09-29)
════════════════════════════════════════════════════════════════════════════
He is tuning the painting prompt to cut down reruns and Photoshop work. A
RERUN normally starts painting the moment he saves, with the MAIN prompt —
so every attempt to test a wording spent money on pictures he had not
chosen, with a prompt he was not testing. What he wanted instead:

  · a switch. While it is on, every repaint waits in a PILE and costs
    nothing;
  · a prompt typed on the test screen itself, under a name he gives it
    ("prompt v12"), never touching the main prompt on the Settings page;
  · TEST THE PROMPT ON N — the N oldest in the pile are painted with that
    prompt and become a ROUND, which he judges on its own: KEEP or RERUN
    only, no Photoshop and no unusable, because the question is only
    "did this prompt get it right";
  · a score per round (kept against rerun), with the prompt's name beside
    it, so "is it getting better" is a number rather than a memory;
  · TRY THIS ROUND AGAIN — repaint just the ones he reran, with whatever
    prompt is in the box now, for a like-for-like comparison;
  · DELETE ALL TEST DATA once he has his best prompt. Test records only,
    never a painting: a kept picture is real work on its way to the
    marketplace.

Switching the mode OFF puts repaints back to how they always worked. The
pile does NOT empty itself when it goes off — that would be exactly the
burst of spending the mode exists to prevent — so the button stays until
the pile is empty.

════════════════════════════════════════════════════════════════════════════
THE TWO MARKS, AND WHY EVERYTHING ELSE IS DERIVED
════════════════════════════════════════════════════════════════════════════
A picture carries at most one of `SavedPoster.rerun_hold_at` (in the pile)
and `SavedPoster.prompt_round_id` (being painted for a round). The painter
skips the first and reads the second. Nothing else is stored about a
round's progress: how many are painted, waiting, kept or rerun is read from
the item rows and the paintings they point at, every time (CLAUDE.md rule
8 — prefer a condition you can derive over a number you have to maintain).

WHAT COUNTS AS A REPAINT is "this picture has been painted before" — it
has at least one ProcessedImage. First-time pictures from workers are
never held: the owner decided new work keeps painting normally.

WHY THE PILE IS ALSO FILLED AT CLAIM TIME, not only by the RERUN button:
a repaint can reach the painter by other doors — RETRY on a failure (the
32 pictures parked when the OpenAI account ran out of credit are the
case in hand), RETURN TO PIPELINE on a retired painting. Teaching each door
would be a list somebody must remember to extend. The painter's claim is
the one place every repaint passes, so `hold_waiting_repaints` runs there:
while the mode is on, a repaint that is not already in a round is moved
into the pile instead of being painted.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import (MasterTitle, ProcessedImage, PromptTestItem,
                     PromptTestPrompt, PromptTestRound, SavedPoster)


# The names the painter's claim uses for "waiting to be painted". Kept here
# beside the pile so the two cannot disagree about what "waiting" means.
WAITING_TO_PAINT = ("greenlit", "failed_processing")
MAIN_PROMPT_NAME = "main prompt (from the Settings page)"


def is_on(db: Session, project) -> bool:
    from .pipeline import get_setting
    return bool(get_setting(db, "prompt_test_mode", project=project))


def _scope(db: Session, project):
    from .pipeline import project_scope, _default_project_id
    return project_scope(project.id, default_project_id=_default_project_id(db))


def _painted_before():
    """A picture that already has a painting — i.e. this would be a repaint."""
    from sqlalchemy import select
    return SavedPoster.id.in_(select(ProcessedImage.saved_poster_id))


def pile_query(db: Session, project):
    """Pictures waiting in the pile, oldest first. Only ones that can still
    be painted: a picture deleted or taken off the title since is not
    waiting for anything, whatever its mark says."""
    return (db.query(SavedPoster)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.rerun_hold_at.isnot(None),
                      SavedPoster.deleted_at.is_(None),
                      SavedPoster.pipeline_status.in_(WAITING_TO_PAINT),
                      _scope(db, project))
              .order_by(SavedPoster.rerun_hold_at.asc(), SavedPoster.id.asc()))


def hold_waiting_repaints(db: Session, project) -> int:
    """
    While the mode is on, move every repaint that is about to be painted —
    and is not part of a round — into the pile. Called by the painter's
    claim before it picks anything, so no door can slip a repaint past the
    switch. Returns how many were moved. Commits nothing; the caller does.
    """
    if not is_on(db, project):
        return 0
    rows = (db.query(SavedPoster)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.deleted_at.is_(None),
                      SavedPoster.pipeline_status.in_(WAITING_TO_PAINT),
                      SavedPoster.rerun_hold_at.is_(None),
                      SavedPoster.prompt_round_id.is_(None),
                      _painted_before(),
                      _scope(db, project))
              .all())
    now = datetime.utcnow()
    for sp in rows:
        sp.rerun_hold_at = now
    return len(rows)


def round_prompt_text(db: Session, poster) -> Optional[str]:
    """The prompt a picture must be painted with, when it belongs to a
    round; None means "the main prompt"."""
    if not poster.prompt_round_id:
        return None
    rnd = db.query(PromptTestRound).filter_by(id=poster.prompt_round_id).first()
    if rnd is None:
        return None
    pr = db.query(PromptTestPrompt).filter_by(id=rnd.prompt_id).first()
    return pr.text if pr is not None else None


def file_painting(db: Session, poster, processed) -> None:
    """The painter has just filed `processed` for `poster`. If the picture
    was being painted for a round, record the painting on the round and
    clear the mark — in the caller's commit, so the two never disagree."""
    if not poster.prompt_round_id:
        return
    item = (db.query(PromptTestItem)
              .filter(PromptTestItem.round_id == poster.prompt_round_id,
                      PromptTestItem.saved_poster_id == poster.id,
                      PromptTestItem.processed_id.is_(None))
              .order_by(PromptTestItem.id.desc()).first())
    if item is not None:
        item.processed_id = processed.id
    poster.prompt_round_id = None


def round_painting_ids(round_id: Optional[int] = None):
    """The paintings rounds produced — a subquery; one round's when
    `round_id` is given. Those are judged in their round and nowhere else,
    so the other review doors leave them out. Once the test data is deleted
    they fall back into the ordinary doors, as ordinary paintings."""
    from sqlalchemy import select
    q = select(PromptTestItem.processed_id).where(
        PromptTestItem.processed_id.isnot(None))
    if round_id is not None:
        q = q.where(PromptTestItem.round_id == round_id)
    return q


def is_round_painting(db: Session, processed_id: int) -> bool:
    return db.query(PromptTestItem.id).filter(
        PromptTestItem.processed_id == processed_id).first() is not None


# ── Rounds ──────────────────────────────────────────────────────────────────

def _prompt_row(db: Session, project, name: str, text: str) -> PromptTestPrompt:
    """The saved prompt with this name AND wording, made if new. Two
    different wordings under one name are two prompts — the score must say
    which words painted what."""
    row = (db.query(PromptTestPrompt)
             .filter(PromptTestPrompt.project_id == project.id,
                     PromptTestPrompt.name == name,
                     PromptTestPrompt.text == text)
             .first())
    if row is None:
        row = PromptTestPrompt(project_id=project.id, name=name, text=text)
        db.add(row)
        db.flush()
    return row


def resolve_prompt(db: Session, project, name: str, text: str) -> tuple[str, str]:
    """An empty box means "the main prompt", recorded under a name that
    says so — which is also how a first round measures the old prompt."""
    from .pipeline import get_setting
    name = (name or "").strip()
    text = (text or "").strip()
    if not text:
        return MAIN_PROMPT_NAME, str(get_setting(db, "openai_prompt",
                                                 project=project) or "")
    if not name:
        raise ValueError("Give the prompt a name first, for example "
                         "\"prompt v12\", so the score can say which one "
                         "painted what.")
    return name[:200], text


def start_round(db: Session, project, *, name: str, text: str,
                posters: list, retry_of: Optional[int], by: str) -> PromptTestRound:
    """Turn `posters` (taken from the pile) into a new round, painted with
    the given prompt. Commits nothing."""
    pname, ptext = resolve_prompt(db, project, name, text)
    prompt = _prompt_row(db, project, pname, ptext)
    number = (db.query(func.max(PromptTestRound.number))
                .filter(PromptTestRound.project_id == project.id).scalar() or 0) + 1
    rnd = PromptTestRound(project_id=project.id, number=number,
                          prompt_id=prompt.id, retry_of=retry_of, created_by=by)
    db.add(rnd)
    db.flush()
    for sp in posters:
        db.add(PromptTestItem(round_id=rnd.id, saved_poster_id=sp.id))
        sp.rerun_hold_at = None
        sp.prompt_round_id = rnd.id
        # Straight to the painter's queue. A picture swept in from a parked
        # failure arrives here too, so its old error and attempt count are
        # cleared the way RETRY clears them.
        sp.pipeline_status = "greenlit"
        sp.process_attempts = 0
        sp.process_error = None
        sp.claimed_at = None
        sp.claimed_by = None
    return rnd


def retry_candidates(db: Session, project, rnd: PromptTestRound) -> list:
    """For TRY THIS ROUND AGAIN: the pictures of `rnd` the owner marked
    RERUN that are still waiting in the pile. Kept ones are never
    repainted — they are on their way to the marketplace. A rerun made
    with the mode OFF went straight to painting and is not here to take."""
    rerun_posters = {sp_id for sp_id, in (
        db.query(PromptTestItem.saved_poster_id)
          .join(ProcessedImage, ProcessedImage.id == PromptTestItem.processed_id)
          .filter(PromptTestItem.round_id == rnd.id,
                  ProcessedImage.review_status == "rerun")
          .all())}
    if not rerun_posters:
        return []
    return [sp for sp in pile_query(db, project).all() if sp.id in rerun_posters]


def summaries(db: Session, project) -> list[dict]:
    """Every round, newest first, with its figures read from the pictures."""
    rounds = (db.query(PromptTestRound)
                .filter(PromptTestRound.project_id == project.id)
                .order_by(PromptTestRound.number.desc()).all())
    if not rounds:
        return []
    prompts = {p.id: p for p in db.query(PromptTestPrompt)
                                   .filter(PromptTestPrompt.project_id == project.id)}
    numbers = {r.id: r.number for r in rounds}
    out = []
    for rnd in rounds:
        items = db.query(PromptTestItem).filter_by(round_id=rnd.id).all()
        painted_ids = [i.processed_id for i in items if i.processed_id]
        statuses = dict(db.query(ProcessedImage.id, ProcessedImage.review_status)
                          .filter(ProcessedImage.id.in_(painted_ids)).all()) \
            if painted_ids else {}
        unpainted = [i for i in items if not i.processed_id]
        posters = {sp.id: sp for sp in db.query(SavedPoster).filter(
            SavedPoster.id.in_([i.saved_poster_id for i in unpainted]))} \
            if unpainted else {}
        painting = failed = gone = 0
        for i in unpainted:
            sp = posters.get(i.saved_poster_id)
            if (sp is None or sp.deleted_at is not None
                    or sp.prompt_round_id != rnd.id):
                gone += 1                    # taken off the title since
            elif sp.pipeline_status == "failed_processing":
                failed += 1                  # see Needs Attention
            else:
                painting += 1
        st = list(statuses.values())
        kept = sum(1 for s in st if s == "approved")
        rerun = sum(1 for s in st if s == "rerun")
        waiting = sum(1 for s in st if s == "pending")
        # You chose an OLDER generation over the round's painting with the
        # version picker: the prompt did not win that one either.
        older = sum(1 for s in st if s in ("superseded", "discarded"))
        pr = prompts.get(rnd.prompt_id)
        judged_all = (waiting == 0 and painting == 0 and failed == 0)
        out.append({
            "id": rnd.id,
            "number": rnd.number,
            "prompt_name": pr.name if pr else "(prompt deleted)",
            "prompt_text": pr.text if pr else "",
            "retry_of": numbers.get(rnd.retry_of) if rnd.retry_of else None,
            "created_at": rnd.created_at.isoformat() if rnd.created_at else "",
            "size": len(items),
            "painting": painting,
            "failed": failed,
            "gone": gone,
            "waiting": waiting,
            "kept": kept,
            "rerun": rerun,
            "older": older,
            "can_retry": (judged_all and rerun > 0
                          and bool(retry_candidates(db, project, rnd))),
        })
    return out


def delete_blockers(db: Session, project) -> list[str]:
    """Why DELETE ALL TEST DATA must wait, in plain words. Empty = go."""
    why = []
    if is_on(db, project):
        why.append("Prompt test mode is still on. Turn it off first.")
    n = pile_query(db, project).count()
    if n:
        why.append(f"{n} picture(s) are still in the pile. Paint them with "
                   f"TEST THE PROMPT first, so none is left waiting for ever.")
    busy = (db.query(func.count(SavedPoster.id))
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.prompt_round_id.isnot(None),
                      SavedPoster.deleted_at.is_(None),
                      SavedPoster.pipeline_status.in_(("greenlit", "processing")),
                      _scope(db, project))
              .scalar() or 0)
    if busy:
        why.append(f"{busy} picture(s) from a round are still being painted. "
                   f"Wait for them to finish.")
    return why


def delete_all(db: Session, project) -> dict:
    """Remove every test record of this project. Never a painting. Commits
    nothing. A round picture that FAILED (for example no credit) loses its
    round and will be painted with the main prompt when retried — said on
    the screen before the button is pressed."""
    rounds = [r.id for r in db.query(PromptTestRound.id)
                                .filter(PromptTestRound.project_id == project.id)]
    n_items = 0
    if rounds:
        n_items = (db.query(PromptTestItem)
                     .filter(PromptTestItem.round_id.in_(rounds))
                     .delete(synchronize_session=False))
        (db.query(SavedPoster)
           .filter(SavedPoster.prompt_round_id.in_(rounds))
           .update({SavedPoster.prompt_round_id: None},
                   synchronize_session=False))
    n_rounds = (db.query(PromptTestRound)
                  .filter(PromptTestRound.project_id == project.id)
                  .delete(synchronize_session=False))
    n_prompts = (db.query(PromptTestPrompt)
                   .filter(PromptTestPrompt.project_id == project.id)
                   .delete(synchronize_session=False))
    return {"rounds": n_rounds, "pictures": n_items, "prompts": n_prompts}
