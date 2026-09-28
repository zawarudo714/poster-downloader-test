# Not yet deployed

## v244 — every picture needs a mark on Approve Artwork (2026-09-28)

- Approve-by-default is gone. SAVE & RELEASE refuses while any picture in
  the batch has no mark, and jumps to the first one. The second button is
  now SAVE ONLY WHAT I MARKED: marked pictures are saved, bare ones stay
  waiting. Nothing unmarked is ever released. The Photoshop door is
  unchanged.
- Files: `static/js/admin_review_images.js`,
  `templates/admin_review_images.html`.
- v243 below was announced but not deployed; one deploy carries both.

## v243 — EVERYTHING BEFORE THIS WEEK on Payments (2026-09-28)

- A new range button on Payments: from the oldest save day up to the day
  before this week starts (by the "week starts on" setting). Already-paid
  pictures are left out by the preview as always.
- Files: `routes/admin.py` (payments_page), `templates/admin_payments.html`,
  `static/js/admin_payments.js`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
