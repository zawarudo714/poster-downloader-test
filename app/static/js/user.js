/* User dashboard — claim-based queue.

   Focus-stable polling: poll-driven refreshes never tear down the active panel
   from scratch — that's what was stealing focus from the URL/skip inputs and
   snapping the page back to the save field. We only re-create the active
   panel when the *locked title actually changed*; otherwise polls just update
   the dynamic bits (counts, posters list, status pills) in place.

   Cache-busting: every <img> served from /file_own/{id} appends ?v={file_size}
   (file_size changes on every replace). The user is also shown a one-time
   "Refresh page to see the new image" toast after replacing, as a safety net. */

(function () {
  const root = document.querySelector('.user-grid');
  if (!root) return;

  let state;
  try { state = JSON.parse(root.getAttribute('data-state') || '{}'); } catch (e) { state = {}; }

  // Cached refs
  const titleListEl  = root.querySelector('[data-title-list]');
  const activePanel  = root.querySelector('[data-active-panel]');
  const banner       = root.querySelector('[data-revisions-banner]');
  const bannerTitle  = root.querySelector('[data-banner-title]');
  const bannerCount  = root.querySelector('[data-rev-count]');
  const bannerList   = root.querySelector('[data-revisions-list]');
  const tplActive    = document.getElementById('tpl-active-title');
  const tplPoster    = document.getElementById('tpl-poster-card');
  const tplRevision  = document.getElementById('tpl-revision');
  const tplSimilar   = document.getElementById('tpl-similar-poster');

  let renderedLockedId = null;

  // ── Helpers ──────────────────────────────────────────────────────────────
  function setStat(name, value) {
    const el = root.querySelector(`[data-stat="${name}"]`);
    if (el) el.textContent = value;
  }

  function fileUrl(posterId, sizeOrFilename) {
    return `/file_own/${posterId}?v=${encodeURIComponent(sizeOrFilename || 0)}`;
  }

  async function postForm(url, body = {}) {
    const fd = new FormData();
    Object.entries(body).forEach(([k, v]) => fd.append(k, v));
    const r = await fetch(url, { method: 'POST', body: fd, cache: 'no-store' });
    let data = null;
    try { data = await r.json(); } catch (e) {}
    return { ok: r.ok, status: r.status, data };
  }

  async function getJSON(url) {
    // Cache-bust + explicit no-store: browsers will heuristically cache GET
    // responses lacking a Cache-Control header, which made counters stale
    // after rapid saves.
    const sep = url.includes('?') ? '&' : '?';
    const r = await fetch(url + sep + '_t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) return null;
    return r.json();
  }

  function humanSize(b) {
    if (!b) return '';
    if (b > 1_000_000) return (b / 1024 / 1024).toFixed(1) + ' MB';
    if (b > 1000)     return (b / 1024).toFixed(0) + ' KB';
    return b + ' B';
  }

  // Toast — bottom-right transient notice.
  let toastEl = null;
  function showToast(message, kind = 'ok', ms = 4500) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.id = 'app-toast';
      document.body.appendChild(toastEl);
    }
    toastEl.className = 'toast toast-' + kind;
    toastEl.textContent = message;
    toastEl.classList.add('toast-shown');
    clearTimeout(toastEl._timer);
    toastEl._timer = setTimeout(() => toastEl.classList.remove('toast-shown'), ms);
  }

  // ── Queue list ───────────────────────────────────────────────────────────
  // Persisted UI state for the queue list:
  //   - whether the "completed" group is collapsed (default: collapsed once
  //     there are any, since the whole point is to keep them out of the way)
  //   - the current search filter text
  // Stored in module scope so 8s polls don't reset them.
  let queueCollapsedDone = true;
  let queueSearchText = '';

  function renderQueue() {
    if (!titleListEl) return;
    if (!state.queue || state.queue.length === 0) {
      titleListEl.innerHTML = `<div class="empty-hint">
        Your list is empty. Click <strong>GET</strong> to add the next batch from the master list,
        or <strong>BROWSE ALL TITLES</strong> to pick titles manually.
      </div>`;
      return;
    }
    const lockedId = state.locked && state.locked.id;

    // Bucket titles by working state. "Active" = anything you might still
    // touch (pending claims you haven't started + in-progress + flagged);
    // "Done" = complete + skipped, the ones we want out of the way.
    const active = [];
    const done   = [];
    for (const t of state.queue) {
      if (t.status === 'complete' || t.status === 'skipped') done.push(t);
      else active.push(t);
    }

    // Sort within each bucket. Active: locked title pinned to top, then
    // in_progress, then pending. Done: most-recently-touched first using
    // saved_count as a rough proxy when explicit timestamps aren't on the
    // queue dict.
    function sortActive(a, b) {
      if (a.id === lockedId) return -1;
      if (b.id === lockedId) return 1;
      const order = { in_progress: 0, pending: 1 };
      const oa = order[a.status] ?? 9, ob = order[b.status] ?? 9;
      if (oa !== ob) return oa - ob;
      return (a.external_id ?? 0) - (b.external_id ?? 0);
    }
    active.sort(sortActive);
    // Done: keep external_id ordering — predictable when admin scrolls back.
    done.sort((a, b) => (a.external_id ?? 0) - (b.external_id ?? 0));

    // Apply search filter — matches title, year, content_type case-insensitive.
    const needle = queueSearchText.trim().toLowerCase();
    function matches(t) {
      if (!needle) return true;
      const hay = `${t.external_id ?? ''} ${t.title} ${t.year} ${t.content_type || ''}`.toLowerCase();
      return hay.includes(needle);
    }
    const activeShown = active.filter(matches);
    const doneShown   = done.filter(matches);
    const activeHidden = active.length - activeShown.length;
    const doneHidden   = done.length - doneShown.length;

    titleListEl.innerHTML = '';

    // Search box — kept visually compact; persists value across re-renders.
    const searchWrap = document.createElement('div');
    searchWrap.className = 'queue-search';
    searchWrap.innerHTML = `
      <input type="search" class="queue-search-input"
             placeholder="Search your titles…"
             value="${queueSearchText.replace(/"/g, '&quot;')}">
      ${needle ? '<button type="button" class="btn btn-ghost btn-tiny queue-search-clear">CLEAR</button>' : ''}
    `;
    const searchInput = searchWrap.querySelector('.queue-search-input');
    searchInput.addEventListener('input', (e) => {
      queueSearchText = e.target.value;
      renderQueue();
      // Re-focus the input after re-render — it was destroyed and rebuilt.
      requestAnimationFrame(() => {
        const inp = titleListEl.querySelector('.queue-search-input');
        if (inp) {
          inp.focus();
          // Put cursor at end so typing continues naturally.
          const v = inp.value;
          inp.setSelectionRange(v.length, v.length);
        }
      });
    });
    const clearBtn = searchWrap.querySelector('.queue-search-clear');
    if (clearBtn) clearBtn.addEventListener('click', () => {
      queueSearchText = '';
      renderQueue();
    });
    titleListEl.appendChild(searchWrap);

    // Active group — always visible, rendered first so unstarted/in-progress
    // sit at the top regardless of how many completed there are.
    if (activeShown.length > 0) {
      activeShown.forEach((t) => titleListEl.appendChild(buildTitleItem(t, lockedId)));
    } else if (active.length > 0 && needle) {
      const empty = document.createElement('div');
      empty.className = 'queue-empty-section';
      empty.textContent = `No active titles match "${queueSearchText}".`;
      titleListEl.appendChild(empty);
    } else if (active.length === 0 && done.length > 0) {
      const empty = document.createElement('div');
      empty.className = 'queue-empty-section';
      empty.textContent = 'No active titles — everything below is finished. Click GET for more.';
      titleListEl.appendChild(empty);
    }

    // Done group — collapsible. Hidden by default; toggle in header bar.
    if (done.length > 0) {
      const header = document.createElement('div');
      header.className = 'queue-done-header';
      const arrow = queueCollapsedDone ? '▸' : '▾';
      const visibleCount = doneShown.length;
      const hiddenNote = (needle && doneHidden > 0)
        ? ` (${doneHidden} hidden by search)` : '';
      header.innerHTML = `
        <span class="queue-done-arrow mono">${arrow}</span>
        <span class="queue-done-label">${visibleCount} finished${hiddenNote}</span>
        <span class="muted queue-done-hint">${queueCollapsedDone ? 'click to expand' : 'click to collapse'}</span>
      `;
      header.addEventListener('click', () => {
        queueCollapsedDone = !queueCollapsedDone;
        renderQueue();
      });
      titleListEl.appendChild(header);

      if (!queueCollapsedDone) {
        if (doneShown.length > 0) {
          doneShown.forEach((t) => titleListEl.appendChild(buildTitleItem(t, lockedId)));
        } else if (needle) {
          const empty = document.createElement('div');
          empty.className = 'queue-empty-section';
          empty.textContent = `No finished titles match "${queueSearchText}".`;
          titleListEl.appendChild(empty);
        }
      }
    }
  }

  function buildTitleItem(t, lockedId) {
    const item = document.createElement('div');
    const cls = ['title-item', 'status-' + t.status];
    if (lockedId === t.id) cls.push('active');
    if (t.needs_revision) cls.push('flagged');
    if (t.admin_note) cls.push('has-admin-note');
    item.className = cls.join(' ');
    item.dataset.masterId = t.id;
    item.innerHTML = `
      <div class="ti-line1">
        <span class="ti-num mono">${t.external_id ?? '–'}.</span>
        <span class="ti-title"></span>
        <span class="ti-year mono">(${t.year})</span>
        ${t.content_type ? `<span class="ti-type mono">${t.content_type}</span>` : ''}
      </div>
      <div class="ti-line2">
        <span class="ti-count mono">${t.saved_count} saved</span>
        · <span class="status-pill status-${t.status}">${t.status.replace('_', ' ')}</span>
        ${t.needs_revision ? '<span class="status-pill status-flag">flag</span>' : ''}
        ${t.admin_note ? '<span class="status-pill status-admin-note">admin note</span>' : ''}
      </div>`;
    item.querySelector('.ti-title').textContent = t.title;
    item.addEventListener('click', () => lockTitle(t.id));
    return item;
  }

  // ── Active panel: full re-render (only on lock change) ───────────────────
  function fullRenderActive() {
    activePanel.innerHTML = '';
    if (!state.locked) {
      activePanel.innerHTML = `<div class="empty-hint">
        Click any title in your list on the left to open it.
        Then click the <strong>Open TMDB</strong> link to find a poster.
      </div>`;
      renderedLockedId = null;
      return;
    }
    const t = state.locked;
    const node = tplActive.content.cloneNode(true);
    node.querySelector('.att-num').textContent  = (t.external_id != null ? t.external_id + '.' : '');
    node.querySelector('.att-title').textContent = t.title;
    node.querySelector('.att-year').textContent  = '(' + t.year + ')';
    if (t.content_type) node.querySelector('.att-type').textContent = t.content_type;
    node.querySelector('.att-desc').textContent  = t.description || '';

    if (t.admin_note) {
      const an = node.querySelector('.att-admin-note');
      an.hidden = false;
      an.querySelector('.att-admin-note-text').textContent = t.admin_note;
    }
    if (t.skip_reason && t.admin_note) {
      const sn = node.querySelector('.att-skip-note');
      sn.hidden = false;
      sn.querySelector('.att-skip-note-text').textContent = t.skip_reason;
    }

    const tmdb = node.querySelector('.att-tmdb');
    tmdb.href = t.tmdb_search;

    const urlInput = node.querySelector('.save-url');
    const saveBtn  = node.querySelector('[data-action="save"]');
    const flashBar = node.querySelector('.flash-bar');
    const saveMsg  = node.querySelector('.save-msg');
    saveBtn.addEventListener('click', () => doSave(urlInput, saveMsg, flashBar));
    urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSave(urlInput, saveMsg, flashBar); });

    const grid  = node.querySelector('.posters-grid');
    const count = node.querySelector('.posters-count');
    count.textContent = (t.posters || []).length;
    (t.posters || []).forEach((p) => grid.appendChild(buildPosterCard(p)));
    grid.dataset.sig = (t.posters || []).map((p) => `${p.id}:${p.size || 0}`).join('|');

    const completeBtn = node.querySelector('[data-action="complete"]');
    const skipBtn     = node.querySelector('[data-action="skip"]');
    const reopenBtn   = node.querySelector('[data-action="reopen"]');
    const skipReason  = node.querySelector('.skip-reason');
    const doneComment = node.querySelector('.done-comment');
    const unlockBtn   = node.querySelector('[data-action="unlock"]');

    completeBtn.addEventListener('click', () => completeTitle((state.locked && state.locked.id) || t.id, doneComment));
    skipBtn.addEventListener('click',     () => skipTitle((state.locked && state.locked.id) || t.id, skipReason.value));
    reopenBtn.addEventListener('click',   () => reopenTitle((state.locked && state.locked.id) || t.id));
    unlockBtn.addEventListener('click',   () => unlock());

    if (t.status === 'complete' || t.status === 'skipped') {
      reopenBtn.hidden = false;
      completeBtn.hidden = true;
      skipBtn.hidden = true;
    }

    activePanel.appendChild(node);
    renderedLockedId = t.id;
    requestAnimationFrame(() => { try { urlInput.focus({ preventScroll: false }); } catch (e) {} });
  }

  // Passive update — same locked title, refresh dynamic bits without
  // touching inputs or stealing focus.
  function passiveUpdateActive() {
    if (!state.locked) {
      fullRenderActive();
      return;
    }
    const t = state.locked;
    const countEl = activePanel.querySelector('.posters-count');
    if (countEl) countEl.textContent = (t.posters || []).length;
    const grid = activePanel.querySelector('.posters-grid');
    if (!grid) return;
    // Use id+size as the signature — replaces change size, so this catches them.
    const newSig = (t.posters || []).map((p) => `${p.id}:${p.size || 0}`).join('|');
    if ((grid.dataset.sig || '') !== newSig) {
      grid.innerHTML = '';
      (t.posters || []).forEach((p) => grid.appendChild(buildPosterCard(p)));
      grid.dataset.sig = newSig;
    }
    const completeBtn = activePanel.querySelector('[data-action="complete"]');
    const skipBtn     = activePanel.querySelector('[data-action="skip"]');
    const reopenBtn   = activePanel.querySelector('[data-action="reopen"]');
    if (completeBtn && skipBtn && reopenBtn) {
      const finished = (t.status === 'complete' || t.status === 'skipped');
      completeBtn.hidden = finished;
      skipBtn.hidden     = finished;
      reopenBtn.hidden   = !finished;
    }
  }

  function buildPosterCard(p) {
    const node = tplPoster.content.cloneNode(true);
    const img = node.querySelector('.poster-img');
    img.src = fileUrl(p.id, p.size);
    img.alt = p.filename;
    node.querySelector('.poster-name').textContent = p.filename;
    node.querySelector('.poster-size').textContent = humanSize(p.size || 0);
    const replaceUrl = node.querySelector('.poster-replace-url');
    node.querySelector('[data-action="replace"]').addEventListener('click', () => replacePoster(p.id, replaceUrl));
    node.querySelector('[data-action="delete"]').addEventListener('click', () => deletePoster(p.id, { fromRevision: false }));
    return node;
  }

  // ── Revisions banner ─────────────────────────────────────────────────────
  function renderRevisions() {
    const revs = state.revisions || [];
    if (!revs.length) {
      banner.hidden = true;
      bannerList.innerHTML = '';
      return;
    }
    banner.hidden = false;

    const openCount = revs.filter((r) => r.status === 'open').length;
    const awaitingCount = revs.filter((r) => r.status === 'awaiting_approval').length;
    const rejectedCount = revs.filter((r) => r.was_rejected).length;
    let label = 'CHANGES REQUESTED';
    if (rejectedCount && rejectedCount === openCount) label = '⚠ REJECTED — PLEASE REVISE';
    else if (awaitingCount && !openCount) label = 'AWAITING ADMIN APPROVAL';
    bannerTitle.textContent = label;
    bannerCount.textContent = `${revs.length} item${revs.length === 1 ? '' : 's'}`;

    bannerList.innerHTML = '';
    revs.forEach((r) => bannerList.appendChild(buildRevisionItem(r)));
  }

  function buildRevisionItem(r) {
    const node = tplRevision.content.cloneNode(true);
    const wrap = node.querySelector('.revision-item');
    wrap.dataset.revisionId = r.revision_id;
    wrap.dataset.posterId   = r.poster_id;
    if (r.status === 'awaiting_approval') wrap.classList.add('rev-awaiting');
    if (r.was_rejected) wrap.classList.add('rev-rejected');
    if (r.revision_type === 'similar') wrap.classList.add('rev-similar');

    const titleStr = `${r.title} (${r.year})`;
    wrap.querySelector('.rev-title').textContent = titleStr;
    wrap.querySelector('.rev-file').textContent  = `/ ${r.title_folder} / ${r.filename}`;

    // TMDB link for this title
    const tmdbA = wrap.querySelector('.rev-tmdb');
    tmdbA.href = r.tmdb_search || '#';

    // Status pill
    const pill = wrap.querySelector('.rev-status-pill');
    pill.classList.add('status-pill');
    if (r.was_rejected) {
      pill.classList.add('status-rejected');
      pill.textContent = 'rejected — redo';
    } else if (r.status === 'awaiting_approval') {
      pill.classList.add('status-awaiting');
      pill.textContent = 'awaiting approval';
    } else if (r.revision_type === 'similar') {
      pill.classList.add('status-similar');
      pill.textContent = 'similar pair';
    } else {
      pill.classList.add('status-flag');
      pill.textContent = 'open';
    }

    // Rejection banner — make it impossible to miss
    if (r.was_rejected && r.admin_verdict) {
      const rb = wrap.querySelector('.rev-rejected-banner');
      rb.hidden = false;
      rb.querySelector('.rev-verdict-text').textContent = ' — ' + r.admin_verdict;
    }

    wrap.querySelector('.rev-comment').textContent = r.comment || '(no comment)';
    const flagged = wrap.querySelector('.rev-flagged');
    if (r.status === 'awaiting_approval') {
      flagged.textContent = `awaiting admin approval since ${r.submitted_at || ''}`;
    } else {
      flagged.textContent = `flagged by ${r.flagged_by} · ${r.created_at}`;
    }

    // Render either single-poster controls or similar-pair grid.
    // We .remove() the unused branch entirely — using `hidden=true` doesn't
    // win against author CSS like `.rev-actions { display: flex }`, which is
    // why the broken-thumb top section was still showing on similar pairs.
    const simpleControls  = wrap.querySelector('[data-mode="simple"]');
    const similarControls = wrap.querySelector('[data-mode="similar"]');

    if (r.revision_type === 'similar' && r.related && r.related.length >= 2) {
      simpleControls.remove();
      r.related.forEach((p) => similarControls.appendChild(buildSimilarCard(r, p)));
    } else {
      similarControls.remove();
      // Simple single-poster row
      const thumb  = wrap.querySelector('.rev-thumb');
      thumb.src    = fileUrl(r.poster_id, r.filename);
      const urlInp = wrap.querySelector('[data-replace-url]');
      const replaceBtn = wrap.querySelector('[data-action="replace"]');
      const deleteBtn  = wrap.querySelector('[data-action="delete-revision"]');
      const resolveBtn = wrap.querySelector('[data-action="resolve"]');
      if (r.status === 'awaiting_approval') resolveBtn.hidden = true;
      replaceBtn.addEventListener('click', () => replacePoster(r.poster_id, urlInp));
      deleteBtn.addEventListener('click',  () => deletePoster(r.poster_id, { fromRevision: true }));
      resolveBtn.addEventListener('click', () => resolveRevision(r.revision_id));
    }
    return node;
  }

  function buildSimilarCard(rev, p) {
    const node = tplSimilar.content.cloneNode(true);
    const img = node.querySelector('.rev-similar-thumb');
    img.src = fileUrl(p.poster_id, p.size || p.filename);
    node.querySelector('.rev-similar-name').textContent = p.filename;
    const urlInp = node.querySelector('[data-replace-url]');
    node.querySelector('[data-action="replace"]')
        .addEventListener('click', () => replacePoster(p.poster_id, urlInp));
    node.querySelector('[data-action="delete-revision"]')
        .addEventListener('click', () => deletePoster(p.poster_id, { fromRevision: true }));
    return node;
  }

  function renderStats() {
    setStat('saved_today',  state.saved_today);
    setStat('saved_week',   state.saved_week);
    setStat('titles_today', state.titles_today);
    setStat('pending_today', state.pending_today || 0);
    // Hide the "not counted yet" tile when there's nothing pending.
    const tile = document.querySelector('[data-pending-tile]');
    if (tile) tile.hidden = !(state.pending_today && state.pending_today > 0);
  }

  function renderReceipts() {
    const banner = document.querySelector('[data-receipts-banner]');
    if (!banner) return;
    const list = banner.querySelector('[data-receipts-list]');
    const countEl = banner.querySelector('[data-receipts-count]');
    const receipts = state.receipts || [];
    if (!receipts.length) {
      banner.hidden = true;
      list.innerHTML = '';
      return;
    }
    banner.hidden = false;
    countEl.textContent = `${receipts.length} unacknowledged`;
    list.innerHTML = '';
    receipts.forEach((r) => {
      const item = document.createElement('div');
      item.className = 'receipt-item';
      item.innerHTML = `
        <div class="receipt-row">
          <strong class="mono receipt-amount"></strong>
          <span class="muted">for</span>
          <span class="mono receipt-period"></span>
          <span class="muted receipt-count"></span>
        </div>
        <div class="receipt-meta mono muted"></div>
        <div class="receipt-note"></div>
        <button class="btn btn-accent btn-tiny receipt-ack-btn" type="button">ACKNOWLEDGE</button>
      `;
      item.querySelector('.receipt-amount').textContent = `KES ${r.amount_kes}`;
      item.querySelector('.receipt-period').textContent =
        r.period_start === r.period_end ? r.period_start : `${r.period_start} → ${r.period_end}`;
      item.querySelector('.receipt-count').textContent =
        `(${r.poster_count} poster${r.poster_count === 1 ? '' : 's'} × ${r.rate_kes} KES)`;
      item.querySelector('.receipt-meta').textContent =
        (r.reference ? `Ref: ${r.reference} · ` : '') + `Sent ${r.pushed_at || ''}`;
      const noteEl = item.querySelector('.receipt-note');
      if (r.note) noteEl.textContent = r.note; else noteEl.remove();
      item.querySelector('.receipt-ack-btn').addEventListener('click', async () => {
        const btn = item.querySelector('.receipt-ack-btn');
        btn.disabled = true;
        const rr = await fetch(`/api/receipts/${r.id}/ack`, { method: 'POST' });
        if (rr.ok) await refreshState();
        else { btn.disabled = false; alert('Failed.'); }
      });
      list.appendChild(item);
    });
  }

  function renderAll({ fullActive }) {
    renderStats();
    renderQueue();
    if (fullActive) {
      fullRenderActive();
    } else {
      passiveUpdateActive();
    }
    renderRevisions();
    renderReceipts();
  }

  async function refreshState() {
    const data = await getJSON('/api/state');
    if (!data) return;
    const newLockedId = data.locked && data.locked.id;
    const lockChanged = (newLockedId !== renderedLockedId);
    state = data;
    renderAll({ fullActive: lockChanged });
  }

  // ── Actions ──────────────────────────────────────────────────────────────
  async function pullNext() {
    const sizeEl = document.getElementById('pull-size');
    const n = Math.max(1, parseInt(sizeEl.value || '50', 10));
    const r = await postForm('/pull_next', { n });
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function release() {
    if (!confirm('Return all unworked titles back to the pool?')) return;
    const r = await postForm('/release');
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function lockTitle(id) {
    const r = await postForm('/lock/' + id);
    if (!r.ok) {
      alert('Failed to open title: ' + (r.data && r.data.detail || r.status));
      return;
    }
    // We do NOT auto-open TMDB — user clicks the button when ready.
    await refreshState();
  }

  async function unlock() {
    await postForm('/unlock');
    await refreshState();
  }

  async function doSave(urlInput, msgEl, flashEl, opts = {}) {
    const url = (urlInput.value || '').trim();
    if (!url) { msgEl.textContent = 'Paste a URL first.'; msgEl.className = 'save-msg err'; return; }
    msgEl.textContent = 'Saving…'; msgEl.className = 'save-msg';
    const r = await postForm('/save_image', {
      url,
      confirm_duplicate:   opts.confirm_duplicate ? 1 : 0,
      confirm_soft_limit:  opts.confirm_soft_limit ? 1 : 0,
      confirm_low_quality: opts.confirm_low_quality ? 1 : 0,
    });
    if (r.ok) {
      msgEl.textContent = `Saved ${r.data.filename} (${r.data.saved_count_for_title} on this title).`;
      msgEl.className   = 'save-msg ok';
      flashEl.classList.add('flash');
      setTimeout(() => flashEl.classList.remove('flash'), 220);
      urlInput.value = '';
      await refreshState();
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'low_quality') {
      if (confirm(r.data.message + '\n\nClick OK to save it anyway, or Cancel to go back and copy the full-size link.')) {
        return doSave(urlInput, msgEl, flashEl, { ...opts, confirm_low_quality: true });
      }
      msgEl.textContent = 'Cancelled.'; msgEl.className = 'save-msg';
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'duplicate') {
      if (confirm(r.data.message + ' (Already saved as ' + r.data.filename + ')')) {
        return doSave(urlInput, msgEl, flashEl, { ...opts, confirm_duplicate: true });
      }
      msgEl.textContent = 'Cancelled.'; msgEl.className = 'save-msg';
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'soft_limit') {
      if (confirm(r.data.message)) {
        return doSave(urlInput, msgEl, flashEl, { ...opts, confirm_soft_limit: true });
      }
      msgEl.textContent = 'Cancelled.'; msgEl.className = 'save-msg';
      return;
    }
    msgEl.textContent = 'Save failed: ' + (r.data && r.data.detail || r.status);
    msgEl.className   = 'save-msg err';
  }

  async function deletePoster(posterId, { fromRevision }) {
    let note = '';
    if (fromRevision) {
      const ans = prompt('Why are you deleting this file? (the admin will see this)\n\nLeave blank to skip.');
      if (ans === null) return;  // cancelled
      note = ans;
    } else {
      if (!confirm('Delete this poster file?')) return;
    }
    const r = await postForm(`/poster/${posterId}/delete`, { note });
    if (r.ok) await refreshState();
    else alert('Delete failed: ' + (r.data && r.data.detail || r.status));
  }

  async function replacePoster(posterId, urlInput, opts = {}) {
    const url = (urlInput.value || '').trim();
    if (!url) { alert('Paste a replacement URL first.'); urlInput.focus(); return; }
    const r = await postForm(`/poster/${posterId}/replace`, {
      url,
      confirm_low_quality: opts.confirm_low_quality ? 1 : 0,
    });
    if (r.ok) {
      urlInput.value = '';
      await refreshState();
      // Cache-busting via ?v=size usually shows the new image right away. The
      // toast is a fallback hint in case the browser is being stubborn.
      if (r.data && r.data.submitted_revisions && r.data.submitted_revisions.length) {
        showToast('Replacement sent for admin approval. (If the image doesn\'t update, try refreshing.)', 'ok', 5000);
      } else {
        showToast('Replacement saved. (If the image doesn\'t update, try refreshing.)', 'ok', 5000);
      }
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'low_quality') {
      if (confirm(r.data.message + '\n\nClick OK to use it anyway, or Cancel to go back and copy the full-size link.')) {
        return replacePoster(posterId, urlInput, { ...opts, confirm_low_quality: true });
      }
      return;
    }
    let msg = 'Replace failed: ' + (r.data && r.data.detail || r.status);
    if (r.status === 400 && r.data && /image|url/i.test(r.data.detail || '')) {
      msg += '\nMake sure you\'re copying the LINK address (not the image address) from the full-size poster.';
    }
    alert(msg);
  }

  async function completeTitle(masterId, doneCommentEl) {
    // Force a fresh state read first — this defeats both browser caching
    // and any race where parallel saves resolved out of order.
    await refreshState();
    const live = state.locked;
    if (!live || live.id !== masterId) {
      alert('The active title changed in the background — please reopen it.');
      return;
    }
    let comment = (doneCommentEl.value || '').trim();
    const liveCount = (live.posters || []).length;
    if (liveCount < 3 && !comment) {
      const ans = prompt(
        `This title only has ${liveCount} poster${liveCount === 1 ? '' : 's'} saved.\n` +
        `If that's intentional (e.g. "only 2 HD posters available"), type a brief reason — or leave blank to confirm.\n\n` +
        `Reason (optional):`
      );
      if (ans === null) return;
      comment = ans.trim();
    }
    const r = await postForm(`/title/${masterId}/complete`, { comment });
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function skipTitle(id, reason) {
    if (!reason && !confirm('Skip this title without giving a reason?')) return;
    const r = await postForm(`/title/${id}/skip`, { reason: reason || '' });
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function reopenTitle(id) {
    const r = await postForm(`/title/${id}/reopen`);
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function resolveRevision(id) {
    const note = prompt('Optional note for the admin (e.g. "redownloaded HD version"). Leave blank to send anyway.');
    if (note === null) return;
    const r = await postForm(`/revisions/${id}/resolve`, { worker_note: note });
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  // ── Wire up static buttons ───────────────────────────────────────────────
  const pullBtn    = document.getElementById('btn-pull-next');
  const releaseBtn = document.getElementById('btn-release');
  if (pullBtn)    pullBtn.addEventListener('click', pullNext);
  if (releaseBtn) releaseBtn.addEventListener('click', release);

  renderAll({ fullActive: true });
  setInterval(refreshState, 8000);
})();
