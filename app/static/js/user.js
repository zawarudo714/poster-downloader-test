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

  // ── Peek mode: admin viewing worker's dashboard read-only ───────────────
  const peekUsername = root.dataset.peekUsername || null;
  const isPeek = !!peekUsername;
  // Override API URL in peek mode to use admin's peek endpoint.
  const stateUrl = isPeek ? `/admin/api/peek/${encodeURIComponent(peekUsername)}` : '/api/state';
  // In peek mode, file URLs go through admin endpoint (admin doesn't have
  // /file_own access for another user's files).
  function fileUrl(posterId, sizeOrFilename) {
    if (isPeek) return `/admin/file/${posterId}?v=${encodeURIComponent(sizeOrFilename || 0)}`;
    return `/file_own/${posterId}?v=${encodeURIComponent(sizeOrFilename || 0)}`;
  }

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
    // Count of unresolved flags ON THIS title — surfaces inside the
    // workplace so workers fixing posters in-place don't lose sight
    // of related flags they should also resolve.
    const myRevs = (state.revisions || []).filter(
      (r) => r.master_id === t.id && r.status === 'open'
    );
    if (myRevs.length > 0) {
      const fb = node.querySelector('.att-active-flags-banner');
      fb.hidden = false;
      fb.querySelector('.att-active-flags-count').textContent = String(myRevs.length);
      fb.querySelector('.att-active-flags-plural').textContent = myRevs.length === 1 ? '' : 's';
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
    img.style.cursor = 'zoom-in';
    img.title = 'Click to enlarge';
    img.addEventListener('click', (e) => {
      e.stopPropagation();
      openLightbox(fileUrl(p.id, p.size), p.filename);
    });
    node.querySelector('.poster-name').textContent = p.filename;
    node.querySelector('.poster-size').textContent = humanSize(p.size || 0);
    const replaceUrl = node.querySelector('.poster-replace-url');
    node.querySelector('[data-action="replace"]').addEventListener('click', () => replacePoster(p.id, replaceUrl));
    node.querySelector('[data-action="delete"]').addEventListener('click', () => deletePoster(p.id, { fromRevision: false }));
    return node;
  }

  // ── Lightbox (90% screen) ────────────────────────────────────────────────
  function openLightbox(src, caption) {
    let lb = document.getElementById('worker-lightbox');
    if (!lb) {
      lb = document.createElement('div');
      lb.id = 'worker-lightbox';
      lb.className = 'lightbox worker-lightbox';
      lb.innerHTML = `
        <div class="lightbox-bg" data-lb-close></div>
        <div class="lightbox-card worker-lightbox-card">
          <button type="button" class="worker-lightbox-close" data-lb-close aria-label="Close">×</button>
          <img class="worker-lightbox-img" alt="">
          <div class="worker-lightbox-caption mono"></div>
        </div>
      `;
      document.body.appendChild(lb);
      lb.querySelectorAll('[data-lb-close]').forEach((el) => el.addEventListener('click', closeLightbox));
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !lb.hidden) closeLightbox();
      });
    }
    lb.querySelector('.worker-lightbox-img').src = src;
    lb.querySelector('.worker-lightbox-img').alt = caption || '';
    lb.querySelector('.worker-lightbox-caption').textContent = caption || '';
    lb.hidden = false;
  }
  function closeLightbox() {
    const lb = document.getElementById('worker-lightbox');
    if (lb) lb.hidden = true;
  }

  // ── Reason picker modal ─────────────────────────────────────────────────
  // Generic modal for "give a reason" flows (complete with <3 posters,
  // skip a title). Returns a Promise<{ text, source } | null>. `source`
  // is 'preset' if user clicked a preset, 'manual' if they typed.
  // Returns null if cancelled.
  function pickReason(opts) {
    return new Promise((resolve) => {
      const modal      = document.getElementById('reason-modal');
      const titleEl    = document.getElementById('reason-title');
      const subEl      = document.getElementById('reason-sub');
      const hintEl     = document.getElementById('reason-hint');
      const presetWrap = document.getElementById('reason-preset-list');
      const manualWrap = document.getElementById('reason-manual');
      const manualInp  = document.getElementById('reason-manual-input');
      const confirmBtn = document.getElementById('reason-confirm');
      const toggleBtn  = document.getElementById('reason-toggle-manual');

      titleEl.textContent = opts.title || 'Reason needed';
      subEl.textContent   = opts.sub   || '';
      // Optional secondary hint, used e.g. on delete to suggest REPLACE
      // for accidentally-saved-wrong-image cases.
      if (hintEl) {
        if (opts.hint) {
          hintEl.textContent = opts.hint;
          hintEl.hidden = false;
        } else {
          hintEl.textContent = '';
          hintEl.hidden = true;
        }
      }
      presetWrap.innerHTML = '';
      manualWrap.hidden = true;
      manualInp.value = '';
      confirmBtn.disabled = true;
      toggleBtn.textContent = 'TYPE OWN REASON';

      let chosen = null;  // { text, source }

      // Render presets.
      (opts.presets || []).forEach((text) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'reason-preset-btn';
        b.textContent = text;
        b.addEventListener('click', () => {
          // Mark this preset as selected; clear others.
          presetWrap.querySelectorAll('.reason-preset-btn').forEach((x) => x.classList.remove('selected'));
          b.classList.add('selected');
          chosen = { text, source: 'preset' };
          confirmBtn.disabled = false;
        });
        presetWrap.appendChild(b);
      });
      // If opts.allowEmpty (e.g. complete-with-comment is optional),
      // include a "no reason" preset so worker can confirm without typing.
      if (opts.allowEmpty) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'reason-preset-btn reason-preset-empty';
        b.textContent = opts.emptyLabel || 'No reason — just confirm';
        b.addEventListener('click', () => {
          presetWrap.querySelectorAll('.reason-preset-btn').forEach((x) => x.classList.remove('selected'));
          b.classList.add('selected');
          chosen = { text: '', source: 'preset' };
          confirmBtn.disabled = false;
        });
        presetWrap.appendChild(b);
      }

      function close(result) {
        modal.hidden = true;
        // Disconnect listeners so they don't leak across opens.
        modal.querySelectorAll('[data-reason-close]').forEach((el) => el.onclick = null);
        toggleBtn.onclick = null;
        confirmBtn.onclick = null;
        manualInp.oninput = null;
        document.removeEventListener('keydown', onKey);
        resolve(result);
      }

      function onKey(e) {
        if (e.key === 'Escape') close(null);
      }

      modal.querySelectorAll('[data-reason-close]').forEach((el) => {
        el.onclick = () => close(null);
      });
      toggleBtn.onclick = () => {
        manualWrap.hidden = !manualWrap.hidden;
        if (!manualWrap.hidden) {
          manualInp.focus();
          // Clear any preset selection — they're switching to manual.
          presetWrap.querySelectorAll('.reason-preset-btn').forEach((x) => x.classList.remove('selected'));
          chosen = null;
          confirmBtn.disabled = true;
        }
      };
      manualInp.oninput = () => {
        const v = manualInp.value.trim();
        if (v) {
          chosen = { text: v, source: 'manual' };
          confirmBtn.disabled = false;
        } else if (opts.allowEmpty) {
          chosen = { text: '', source: 'manual' };
          confirmBtn.disabled = false;
        } else {
          chosen = null;
          confirmBtn.disabled = true;
        }
      };
      confirmBtn.onclick = () => close(chosen);

      document.addEventListener('keydown', onKey);
      modal.hidden = false;
    });
  }
  // Shows every live poster currently saved on a master title. Read-only —
  // for awareness of what's already there before replacing/saving. The
  // "GO TO TITLE" button delegates to goToTitle() which opens the workplace.
  let catalogActiveMasterId = null;

  function openCatalog(masterId, opts = {}) {
    const modal = document.getElementById('catalog-modal');
    if (!modal) return;
    catalogActiveMasterId = masterId;
    document.getElementById('catalog-title').textContent = opts.titleHint || '…';
    document.getElementById('catalog-sub').textContent = '';
    document.getElementById('catalog-grid').innerHTML =
      '<div class="empty-hint">Loading…</div>';
    modal.hidden = false;
    // Wire close handlers once. We re-attach safely since they're idempotent.
    modal.querySelectorAll('[data-catalog-close]').forEach((el) => {
      el.onclick = closeCatalog;
    });
    document.getElementById('catalog-go-to-title').onclick = () => {
      const mid = catalogActiveMasterId;
      closeCatalog();
      if (mid != null) goToTitle(mid);
    };
    fetch(`/api/title/${masterId}/catalog`, { cache: 'no-store' })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data, status: r.status })))
      .then(({ ok, data, status }) => {
        if (!ok) {
          document.getElementById('catalog-grid').innerHTML =
            `<div class="empty-hint">${data.detail || ('Failed (' + status + ')')}</div>`;
          return;
        }
        document.getElementById('catalog-title').textContent =
          `${data.title} (${data.year})`;
        document.getElementById('catalog-sub').textContent =
          `${data.posters.length} poster${data.posters.length === 1 ? '' : 's'} on this title · status: ${data.status.replace('_', ' ')}`;
        const grid = document.getElementById('catalog-grid');
        if (data.posters.length === 0) {
          grid.innerHTML = '<div class="empty-hint">No posters saved on this title.</div>';
          return;
        }
        grid.innerHTML = '';
        data.posters.forEach((p) => {
          const card = document.createElement('div');
          card.className = 'catalog-poster';
          card.innerHTML = `
            <img class="catalog-poster-img" loading="lazy" alt="">
            <div class="catalog-poster-name mono"></div>
            <div class="catalog-poster-meta mono muted"></div>
          `;
          const img = card.querySelector('.catalog-poster-img');
          img.src = p.url + '?v=' + (p.size || 0);
          img.alt = p.filename;
          img.style.cursor = 'zoom-in';
          img.title = 'Click to enlarge';
          img.addEventListener('click', () => openLightbox(img.src, p.filename));
          card.querySelector('.catalog-poster-name').textContent = p.filename;
          const dims = (p.width && p.height) ? `${p.width}×${p.height}` : '';
          card.querySelector('.catalog-poster-meta').textContent =
            [humanSize(p.size), dims, p.saved_on].filter(Boolean).join(' · ');
          grid.appendChild(card);
        });
      })
      .catch(() => {
        document.getElementById('catalog-grid').innerHTML =
          '<div class="empty-hint">Network error.</div>';
      });
  }

  function closeCatalog() {
    const modal = document.getElementById('catalog-modal');
    if (modal) modal.hidden = true;
    catalogActiveMasterId = null;
  }

  // ── Go to title ─────────────────────────────────────────────────────────
  // Server-side reopen + lock, then refresh state. The active panel will
  // auto-render the now-locked title; we scroll to it for clarity.
  async function goToTitle(masterId) {
    const r = await fetch(`/title/${masterId}/go_to`, { method: 'POST' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert('Failed: ' + (data.detail || r.status));
      return;
    }
    await refreshState();
    // Scroll the active panel into view. Mobile: also collapse drawer if open.
    requestAnimationFrame(() => {
      const panel = document.querySelector('[data-active-panel]');
      if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
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

    // VIEW ALL POSTERS — opens the catalog modal so the worker can see
    // the rest of their saves on this title without leaving the flag list.
    wrap.querySelector('.rev-view-catalog').addEventListener('click', () => {
      openCatalog(r.master_id, { titleHint: titleStr });
    });
    // GO TO TITLE — reopens (if needed) + locks + scrolls the worker into
    // the active title workplace for hands-on edits.
    wrap.querySelector('.rev-go-to-title').addEventListener('click', () => {
      goToTitle(r.master_id);
    });

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
      // Distinguish what the worker did so they know what kind of approval is pending.
      let actionLabel;
      if (r.worker_action === 'deleted') actionLabel = 'your deletion is awaiting admin approval';
      else if (r.worker_action === 'replaced') actionLabel = 'your replacement is awaiting admin approval';
      else                                     actionLabel = 'awaiting admin approval';
      flagged.textContent = `${actionLabel} since ${r.submitted_at || ''}`;
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
    } else if (r.revision_type === 'similar' && r.poster_deleted) {
      // Edge case: a similar-pair revision where the primary poster was
      // deleted AND the remaining related list dropped below 2. We still
      // need to show a card so the worker knows admin is reviewing. Use
      // the simple-mode template with the placeholder thumb.
      similarControls.remove();
      _renderDeletedRow(wrap, r);
    } else {
      similarControls.remove();
      if (r.poster_deleted) {
        _renderDeletedRow(wrap, r);
      } else {
        // Simple single-poster row, live file.
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
    }
    return node;
  }

  // When the underlying poster is gone (worker deleted it), we still want
  // the revision card to appear in the worker's flag panel so they know
  // admin is reviewing the deletion. Swap the thumb to the placeholder,
  // strip the action buttons, and add a minimal status note.
  function _renderDeletedRow(wrap, r) {
    const thumb = wrap.querySelector('.rev-thumb');
    if (thumb) {
      thumb.src = '/static/img/deleted-poster.svg';
      thumb.alt = 'poster deleted';
      thumb.classList.add('rev-thumb-placeholder');
    }
    // Remove the URL input + action buttons row entirely — there's nothing
    // to replace or re-delete; the worker just waits.
    const urlRow = wrap.querySelector('.rev-actions');
    if (urlRow) urlRow.remove();
    // Add a minimal "info-only" status line in place of the controls so the
    // worker has something to read.
    const simpleControls = wrap.querySelector('[data-mode="simple"]');
    if (simpleControls) {
      const info = document.createElement('div');
      info.className = 'rev-deleted-info muted';
      if (r.status === 'awaiting_approval') {
        info.textContent =
          'You deleted this poster. Admin will review the deletion and approve or send it back.';
      } else if (r.was_rejected) {
        info.textContent =
          'Admin sent back your deletion. Read the note above — you may need to upload a new poster on this title.';
      } else {
        info.textContent = 'Poster deleted — admin reviewing.';
      }
      simpleControls.appendChild(info);
    }
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
      const hasBackPay = (r.back_pay_dates && r.back_pay_dates.length > 0);
      const byDayDates = Object.keys(r.by_day || {}).sort();
      // Only shown when the run actually spans more than one project — a
      // worker on a single niche doesn't need a line telling them so.
      const byProject = r.by_project || {};
      const projNames = Object.keys(byProject);
      const showProjects = projNames.length > 1;
      item.innerHTML = `
        <div class="receipt-row">
          <strong class="mono receipt-amount"></strong>
          <span class="muted">for</span>
          <span class="mono receipt-period"></span>
          <span class="muted receipt-count"></span>
        </div>
        <div class="receipt-meta mono muted"></div>
        <div class="receipt-note"></div>
        ${byDayDates.length > 0 ? `
          <details class="receipt-breakdown">
            <summary class="muted">▸ See per-day breakdown</summary>
            <div class="receipt-day-list"></div>
          </details>
        ` : ''}
        ${showProjects ? `<div class="receipt-projects muted"></div>` : ''}
        ${hasBackPay ? `<div class="receipt-backpay">includes back-pay from <span class="receipt-backpay-dates"></span></div>` : ''}
        <div class="receipt-actions">
          <button class="btn btn-accent btn-tiny receipt-ack-btn" type="button">ACKNOWLEDGE</button>
          <button class="btn btn-danger btn-tiny receipt-nr-btn" type="button">NOT RECEIVED</button>
        </div>
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

      // Per-day breakdown.
      if (byDayDates.length > 0) {
        const dayList = item.querySelector('.receipt-day-list');
        const rate = parseFloat(r.rate_kes) || 0;
        const backPaySet = new Set(r.back_pay_dates || []);
        let html = '';
        byDayDates.forEach((d) => {
          const c = r.by_day[d];
          const sub = (c * rate);
          const isBack = backPaySet.has(d);
          html += `
            <div class="receipt-day-row${isBack ? ' is-back-pay' : ''}">
              <span class="mono">${d}${isBack ? ' <span class="bp-tag">back-pay</span>' : ''}</span>
              <span class="mono">${c} × ${r.rate_kes}</span>
              <span class="mono">${formatKes(sub)} KES</span>
            </div>`;
        });
        dayList.innerHTML = html;
      }

      // One payment, several projects — say so explicitly.
      if (showProjects) {
        item.querySelector('.receipt-projects').textContent =
          'Covers ' + projNames.map((n) => `${n}: ${byProject[n]}`).join(' · ');
      }

      // Back-pay summary line.
      if (hasBackPay) {
        item.querySelector('.receipt-backpay-dates').textContent = r.back_pay_dates.join(', ');
      }

      item.querySelector('.receipt-ack-btn').addEventListener('click', async () => {
        const btn = item.querySelector('.receipt-ack-btn');
        btn.disabled = true;
        const rr = await fetch(`/api/receipts/${r.id}/ack`, { method: 'POST' });
        if (rr.ok) await refreshState();
        else { btn.disabled = false; alert('Failed.'); }
      });

      item.querySelector('.receipt-nr-btn').addEventListener('click', async () => {
        if (!confirm('Are you sure you have NOT received this payment? The admin will be notified.')) return;
        const btn = item.querySelector('.receipt-nr-btn');
        btn.disabled = true;
        const rr = await fetch(`/api/receipts/${r.id}/not_received`, { method: 'POST' });
        if (rr.ok) {
          showToast('Marked as not received — admin has been notified.', 'ok', 4000);
          await refreshState();
        } else { btn.disabled = false; alert('Failed.'); }
      });
      list.appendChild(item);
    });
  }

  function formatKes(n) {
    if (Number.isInteger(n)) return String(n);
    return n.toFixed(2).replace(/\.?0+$/, '');
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
    renderPendingComplete();
    renderReceipts();
  }

  function renderPendingComplete() {
    const banner = document.querySelector('[data-pending-complete-banner]');
    if (!banner) return;
    const list = banner.querySelector('[data-pending-complete-list]');
    const countEl = banner.querySelector('[data-pending-complete-count]');
    const items = state.pending_complete_titles || [];
    if (!items.length) {
      banner.hidden = true;
      list.innerHTML = '';
      return;
    }
    banner.hidden = false;
    countEl.textContent =
      `${items.length} title${items.length === 1 ? '' : 's'} awaiting review`;
    list.innerHTML = '';
    items.forEach((t) => {
      const row = document.createElement('div');
      row.className = 'pending-complete-item';
      row.innerHTML = `
        <div class="pci-title"></div>
        <div class="pci-meta mono muted"></div>
        ${t.comment ? '<div class="pci-comment"></div>' : ''}
      `;
      row.querySelector('.pci-title').textContent = `${t.title} (${t.year})`;
      row.querySelector('.pci-meta').textContent = `submitted ${t.submitted_at}`;
      if (t.comment) row.querySelector('.pci-comment').textContent = `Your note: ${t.comment}`;
      list.appendChild(row);
    });
  }

  async function refreshState() {
    const data = await getJSON(stateUrl);
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
      confirm_duplicate:    opts.confirm_duplicate ? 1 : 0,
      confirm_cross_title:  opts.confirm_cross_title ? 1 : 0,
      confirm_soft_limit:   opts.confirm_soft_limit ? 1 : 0,
      confirm_low_quality:  opts.confirm_low_quality ? 1 : 0,
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
    if (r.status === 409 && r.data && r.data.reason === 'cross_title_duplicate') {
      if (confirm(r.data.message)) {
        return doSave(urlInput, msgEl, flashEl, { ...opts, confirm_cross_title: true });
      }
      msgEl.textContent = 'Cancelled — same image was on another title.'; msgEl.className = 'save-msg';
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
    // Find the poster to figure out how many other live posters exist on
    // the same title — needed for the dynamic "Only N usable poster(s)
    // available" preset. We look in state.locked.posters since deletion
    // can only happen from the active title.
    const live = state.locked;
    const livePosters = (live && live.posters) || [];
    // Count POSTERS BESIDES the one being deleted; that's the worker's
    // post-delete view of the title.
    const remaining = Math.max(0, livePosters.filter((p) => p.id !== posterId).length);

    // Presets differ by context. From a flag card the only meaningful reason
    // is "this poster is bad/similar"; the N-usable preset is meaningless
    // (zero context for what N would mean). From the title panel both
    // presets are relevant, plus a quick-confirm escape since first-time
    // mistake deletes are common ("I downloaded the wrong image").
    const presets = [];
    if (!fromRevision) {
      // Title-panel delete: include the dynamic count preset.
      presets.push(`Only ${remaining} usable poster${remaining === 1 ? '' : 's'} available`);
      presets.push('All the posters available are similar');
      presets.push('Other posters not usable');
    } else {
      // Flag-panel delete: focus on quality reasons.
      presets.push('All the posters available are similar');
      presets.push('Other posters not usable');
    }

    const hint = fromRevision
      ? null
      : '💡 Downloaded by mistake? Use REPLACE instead — paste a new URL above.';

    const result = await pickReason({
      title: 'Delete this poster?',
      sub:   fromRevision
        ? 'The admin flagged this poster. Pick a reason for deletion (admin will be notified).'
        : 'This will permanently remove the file. Pick a reason or type your own.',
      hint:  hint,
      presets,
      allowEmpty: false,
    });
    if (result === null) return;

    const r = await postForm(`/poster/${posterId}/delete`,
                             { note: result.text || '',
                               reason_source: result.source || '' });
    if (r.ok) {
      await refreshState();
      // If the delete was on a flagged poster, the server responds with
      // submitted_for_approval:true so we can tell the worker their action
      // is pending (not silently complete). UI-wise the flag card will now
      // render in awaiting-approval state too, so this toast is a nudge.
      if (r.data && r.data.submitted_for_approval) {
        showToast('Deletion sent to admin for approval.', 'ok', 5000);
      }
      return;
    }
    alert('Delete failed: ' + (r.data && r.data.detail || r.status));
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
    let reason_source = comment ? 'manual' : '';
    const liveCount = (live.posters || []).length;
    if (liveCount < 3 && !comment) {
      const result = await pickReason({
        title: 'Confirm completion',
        sub: `This title only has ${liveCount} poster${liveCount === 1 ? '' : 's'} saved. ` +
             `Pick a reason — or click "no reason" if 1–2 is just fine here.`,
        presets: [
          `Only ${liveCount} usable poster${liveCount === 1 ? '' : 's'} available`,
          'All the posters available are similar',
        ],
        allowEmpty: true,
        emptyLabel: 'No reason — confirm anyway',
      });
      if (result === null) return;
      comment = result.text;
      reason_source = result.source;
    }
    return submitComplete(masterId, comment, reason_source);
  }

  async function submitComplete(masterId, comment, reason_source) {
    const r = await postForm(`/title/${masterId}/complete`,
                             { comment, reason_source: reason_source || '' });
    if (r.ok) {
      // Two possible "ok" responses now:
      //  - {pending_approval: true} → title routed to complete_pending state
      //    because the worker made changes on a flagged title. Admin must
      //    approve the whole batch.
      //  - default ok → title went straight to complete (no pending state).
      await refreshState();
      if (r.data && r.data.pending_approval) {
        showToast(
          'Sent to admin for approval. The title will show "awaiting approval" until they review your changes.',
          'ok', 6000,
        );
      }
      return;
    }
    alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function skipTitle(id, reason) {
    let reason_source = reason ? 'manual' : '';
    if (!reason) {
      const result = await pickReason({
        title: 'Why are you skipping this title?',
        sub:   'Pick a common reason or type your own. Skipped titles go to the admin for review.',
        presets: [
          'No appropriate posters available',
        ],
        allowEmpty: false,
      });
      if (result === null) return;
      reason = result.text;
      reason_source = result.source;
    }
    const r = await postForm(`/title/${id}/skip`,
                             { reason: reason || '',
                               reason_source: reason_source || '' });
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
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
