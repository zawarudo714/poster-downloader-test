/* Activity Log v2 — readable card layout.
   Each entry is a row with WHEN | USER · ACTION | TARGET. Rows that have a
   "comment" attached (worker save with note, skip reason, deletion reason,
   admin verdict, complete-with-comment, etc.) get an attention-getting
   amber stripe and the comment text spans the bottom of the card. */

(function () {
  const $ = (id) => document.getElementById(id);
  const listEl  = $('al-list');
  const summaryEl = $('al-summary');
  const userInput = $('al-user');
  const actionSel = $('al-action');
  const commentsOnlyCb = $('al-comments-only');
  const applyBtn = $('al-apply');
  const moreBtn = $('al-load-more');

  let oldestId = 0;       // for pagination — load entries older than this
  let totalShown = 0;
  let allEntries = [];    // accumulated rows so we can re-filter without refetch

  // ── Visual labels per action ─────────────────────────────────────────────
  // Mapping action → friendly label + a CSS class that colors the pill.
  // Actions not mapped show as their raw name with a default style.
  const ACTION_META = {
    saved:           { label: 'saved poster',          cls: 'act-save' },
    deleted:         { label: 'deleted poster',        cls: 'act-delete' },
    replaced:        { label: 'replaced poster',       cls: 'act-replace' },
    locked:          { label: 'opened title',          cls: 'act-claim' },
    unlocked:        { label: 'closed title',          cls: 'act-claim' },
    claimed:         { label: 'claimed batch',         cls: 'act-claim' },
    released:        { label: 'released claims',       cls: 'act-claim' },
    completed:       { label: 'COMPLETED title',       cls: 'act-complete' },
    skipped:         { label: 'SKIPPED title',         cls: 'act-skip' },
    reopened:        { label: 'reopened title',        cls: 'act-claim' },
    submitted_for_approval: { label: 'sent for approval', cls: 'act-await' },
    flagged:         { label: 'FLAGGED poster',        cls: 'act-flag' },
    flagged_similar: { label: 'FLAGGED as similar',    cls: 'act-flag' },
    unflagged:       { label: 'cleared flag',          cls: 'act-resolve' },
    resolved:        { label: 'resolved revision',     cls: 'act-resolve' },
    approved:        { label: 'APPROVED fix',          cls: 'act-resolve' },
    rejected:        { label: 'REJECTED fix',          cls: 'act-flag' },
    acknowledged:    { label: 'acknowledged deletion', cls: 'act-resolve' },
    escalated:       { label: 'escalated deletion',    cls: 'act-flag' },
    paid:            { label: 'PAID worker',           cls: 'act-pay' },
    receipt_push:    { label: 'pushed receipt',        cls: 'act-pay' },
    receipt_ack:     { label: 'acknowledged receipt',  cls: 'act-pay' },
    payment_run_deleted: { label: 'undid payment',     cls: 'act-flag' },
    chat_sent:       { label: 'sent chat',             cls: 'act-chat' },
    user_created:    { label: 'created user',          cls: 'act-admin' },
    user_toggled:    { label: 'toggled user',          cls: 'act-admin' },
    password_reset:  { label: 'reset password',        cls: 'act-admin' },
    imported:        { label: 'imported master',       cls: 'act-admin' },
    bulk_status:     { label: 'bulk status change',    cls: 'act-admin' },
  };

  function actionMeta(a) {
    return ACTION_META[a] || { label: a.replace(/_/g, ' '), cls: 'act-other' };
  }

  // ── Target description from details + target_type ────────────────────────
  function targetDesc(row) {
    const d = row.details || {};
    // Prefer the most useful identifier per target type.
    if (row.target_type === 'master_title') {
      const title = d.title || d.master_title || `master#${row.target_id}`;
      return title;
    }
    if (row.target_type === 'saved_poster') {
      return d.filename || `saved#${row.target_id}`;
    }
    if (row.target_type === 'revision') {
      return `rev#${row.target_id}` + (d.poster_filename ? ` · ${d.poster_filename}` : '');
    }
    if (row.target_type === 'payment_run') {
      const w = d.worker || '';
      const amt = d.amount ? `KES ${d.amount}` : '';
      const period = d.period || (d.start && d.end ? `${d.start}..${d.end}` : '');
      return [w, amt, period].filter(Boolean).join(' · ');
    }
    if (row.target_type === 'chat') {
      return d.to_worker_id ? `→ worker#${d.to_worker_id}` : 'thread';
    }
    if (row.target_type === 'user') {
      return d.username || `user#${row.target_id}`;
    }
    if (row.target_type) {
      return `${row.target_type}#${row.target_id ?? '–'}`;
    }
    return '';
  }

  // ── Render ───────────────────────────────────────────────────────────────
  function render(entries) {
    if (!entries.length) {
      listEl.innerHTML = '<div class="empty-hint">No matching activity.</div>';
      summaryEl.textContent = '';
      return;
    }
    listEl.innerHTML = '';
    let withComment = 0;
    entries.forEach((row) => {
      const meta = actionMeta(row.action);
      const card = document.createElement('div');
      card.className = 'activity-card ' + meta.cls + (row.comment ? ' has-comment' : '');
      card.innerHTML = `
        <div class="activity-card-head">
          <span class="al-when mono"></span>
          <span class="al-user mono"></span>
          <span class="al-action-pill"></span>
          <span class="al-target"></span>
        </div>
        ${row.comment ? '<div class="al-comment-line"><span class="al-comment-icon">💬</span><span class="al-comment-text"></span></div>' : ''}
      `;
      card.querySelector('.al-when').textContent = row.created_at;
      card.querySelector('.al-user').textContent = row.username || '(system)';
      const pill = card.querySelector('.al-action-pill');
      pill.textContent = meta.label;
      card.querySelector('.al-target').textContent = targetDesc(row);
      if (row.comment) {
        card.querySelector('.al-comment-text').textContent = row.comment;
        withComment += 1;
      }
      listEl.appendChild(card);
    });
    summaryEl.textContent = `${entries.length} entries · ${withComment} with notes`;
  }

  function applyAndRender() {
    let filtered = allEntries.slice();
    if (commentsOnlyCb.checked) filtered = filtered.filter((r) => r.comment);
    render(filtered);
  }

  async function fetchPage({ reset }) {
    if (reset) {
      oldestId = 0;
      totalShown = 0;
      allEntries = [];
      listEl.innerHTML = '<div class="empty-hint">Loading…</div>';
    }
    const params = new URLSearchParams({
      since_id:       String(oldestId),
      limit:          '200',
      user_filter:    userInput.value.trim(),
      action_filter:  actionSel.value,
      // We always fetch with comments_only=0 to keep the local filter live;
      // the checkbox just narrows what's rendered from `allEntries`.
      comments_only:  '0',
    });
    const r = await fetch('/admin/api/activity?' + params.toString());
    if (!r.ok) {
      listEl.innerHTML = '<div class="empty-hint">Load failed.</div>';
      return;
    }
    const data = await r.json();
    if (!data.rows.length) {
      if (allEntries.length === 0) {
        listEl.innerHTML = '<div class="empty-hint">No matching activity.</div>';
      } else {
        moreBtn.disabled = true;
        moreBtn.textContent = 'NO OLDER ENTRIES';
      }
      return;
    }
    allEntries = allEntries.concat(data.rows);
    oldestId = data.rows[data.rows.length - 1].id;
    totalShown = allEntries.length;
    moreBtn.disabled = false;
    moreBtn.textContent = 'LOAD OLDER ENTRIES ↓';
    applyAndRender();
  }

  // ── Wire up ─────────────────────────────────────────────────────────────
  applyBtn.addEventListener('click', () => fetchPage({ reset: true }));
  userInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') fetchPage({ reset: true }); });
  actionSel.addEventListener('change', () => fetchPage({ reset: true }));
  commentsOnlyCb.addEventListener('change', applyAndRender);
  moreBtn.addEventListener('click', () => fetchPage({ reset: false }));

  // First load
  fetchPage({ reset: true });
})();
