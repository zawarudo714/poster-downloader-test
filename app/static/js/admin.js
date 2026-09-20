/* Admin shared JS — gallery review (with quality badges + similar-select),
   fs tree, release-user buttons, lightbox flag/unflag, ZIP day. */

(function () {

  // ── Filesystem tree (admin dashboard) ────────────────────────────────────
  // Loaded on the BUTTON, not on page load (Mega Audit speed pass,
  // 2026-09-06): this endpoint walks the whole workspace on disk, and the
  // dashboard is the first page of every session. With a real catalogue the
  // walk is the slowest thing on the page, spent answering a question
  // nobody asked yet.
  const fsTree = document.getElementById('fs-tree');
  const fsLoad = document.getElementById('fs-tree-load');
  function loadTree() {
    fsTree.innerHTML = '<p class="muted">Walking the workspace…</p>';
    fetch('/admin/api/tree').then((r) => r.json()).then((data) => {
      fsTree.innerHTML = '';
      if (!data.workers || !data.workers.length) {
        fsTree.innerHTML = '<p class="muted">No workspaces yet.</p>';
        return;
      }
      data.workers.forEach((u) => {
        const det = document.createElement('details');
        const sum = document.createElement('summary');
        sum.textContent = u.name;
        det.appendChild(sum);
        u.children.forEach((d) => {
          const det2 = document.createElement('details');
          const sum2 = document.createElement('summary');
          sum2.textContent = d.name;
          det2.appendChild(sum2);
          d.children.forEach((tf) => {
            const div = document.createElement('div');
            div.className = 'leaf';
            div.appendChild(document.createTextNode(tf.name));
            const span = document.createElement('span');
            span.className = 'count';
            span.textContent = tf.count;
            div.appendChild(span);
            det2.appendChild(div);
          });
          det.appendChild(det2);
        });
        fsTree.appendChild(det);
      });
    });
  }
  if (fsTree) {
    fsTree.innerHTML = '<p class="muted">Press LOAD TREE to walk the '
      + 'workspace folders. Not loaded automatically — the walk is the '
      + 'slowest thing this page could do.</p>';
    if (fsLoad) fsLoad.addEventListener('click', loadTree);
  }

  // ── Release-user-queue buttons ───────────────────────────────────────────
  document.querySelectorAll('[data-release-user-id]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.getAttribute('data-release-user-id');
      const keepStarted = btn.getAttribute('data-keep-started') || '1';
      const label = keepStarted === '1' ? 'unworked' : 'ALL';
      const warning = keepStarted === '1'
        ? 'Return this user\'s UNWORKED titles to the pool? Titles they have started stay with them.'
        : 'Return ALL of this user\'s titles to the pool — including ones they are working on right now? They would lose their place.';
      if (!confirm(warning)) return;
      const fd = new FormData();
      fd.append('keep_started', keepStarted);
      const r = await fetch(`/admin/users/${id}/release_queue`, { method: 'POST', body: fd });
      const data = await r.json().catch(() => ({}));
      if (r.ok) { alert(`Returned ${data.released} titles.`); location.reload(); }
      else { alert('Failed: ' + (data.detail || r.status)); }
    });
  });

  // ── Delete-user modal ─────────────────────────────────────────────────
  const delDialog = document.getElementById('delete-user-dialog');
  if (delDialog) {
    const usernameEcho = document.getElementById('del-username-echo');
    const usernameShow = document.getElementById('del-username-show');
    const confirmInput = document.getElementById('del-confirm-username');
    const passwordInput = document.getElementById('del-admin-password');
    const confirmBtn   = document.getElementById('del-confirm-btn');
    const errorEl      = document.getElementById('del-error');
    let activeId = null;
    let activeUsername = null;

    function closeDel() {
      delDialog.hidden = true;
      confirmInput.value = '';
      passwordInput.value = '';
      errorEl.hidden = true;
      errorEl.textContent = '';
      confirmBtn.disabled = true;
      activeId = null;
    }
    delDialog.querySelectorAll('[data-lightbox-close]').forEach((el) => {
      el.addEventListener('click', closeDel);
    });

    function recheck() {
      const okUser = confirmInput.value.trim() === activeUsername;
      const okPw   = (passwordInput.value || '').length >= 1;
      confirmBtn.disabled = !(okUser && okPw);
    }
    confirmInput.addEventListener('input', recheck);
    passwordInput.addEventListener('input', recheck);

    document.querySelectorAll('[data-delete-user-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        activeId = btn.getAttribute('data-delete-user-id');
        activeUsername = btn.getAttribute('data-delete-username');
        usernameEcho.textContent = activeUsername;
        usernameShow.textContent = activeUsername;
        delDialog.hidden = false;
        confirmInput.focus();
      });
    });

    confirmBtn.addEventListener('click', async () => {
      errorEl.hidden = true;
      confirmBtn.disabled = true;
      confirmBtn.textContent = 'DELETING…';
      const fd = new FormData();
      fd.append('confirm_username', confirmInput.value.trim());
      fd.append('admin_password',   passwordInput.value);
      const r = await fetch(`/admin/users/${activeId}/delete`, { method: 'POST', body: fd });
      const data = await r.json().catch(() => ({}));
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'DELETE';
      if (r.ok) {
        alert(`User deleted. ${data.released_claims || 0} title claim(s) released.`);
        location.reload();
      } else {
        errorEl.hidden = false;
        errorEl.textContent = data.detail || `Failed (${r.status})`;
      }
    });
  }

  // ── Gallery image browser ────────────────────────────────────────────────
  const gallery = document.getElementById('ib-gallery');
  if (!gallery) return;

  // Threshold for sub-800 highlighting. Posters under 800px wide get a red border.
  // Per project, delivered by /admin/api/browse. 0 disables the warning —
  // which is what a project whose sources are meant to be small wants.
  let MIN_WIDTH = 800;

  let titles = [];
  let titleIdx = 0;
  // The place check's page-level facts, delivered by /admin/api/browse:
  // whether it is on, whether a key exists, and this month's usage. The
  // per-image verdicts ride on each poster in `titles`, so the pills and
  // the panel read ONE set of data and can never disagree.
  let placeCheck = { enabled: false, key_present: false, checked_this_month: 0 };
  let currentLightbox = null;
  // Which poster the lightbox is showing, for the resume memory — 0 when
  // it is closed. Written into the saved state on every open and close,
  // so leaving mid-look reopens exactly here, while a deliberate close
  // is remembered as closed.
  let lbOpenPoster = 0;
  // Multi-select state for "mark similar". Set of poster IDs. Selection
  // is single-title — moving between titles prompts to clear.
  const selected = new Set();

  const $ = (id) => document.getElementById(id);
  const tplTitle  = document.getElementById('tpl-gallery-title');
  const tplPoster = document.getElementById('tpl-gallery-poster');

  function fileUrl(posterId, sizeOrFilename) {
    return `/admin/file/${posterId}?v=${encodeURIComponent(sizeOrFilename || 0)}`;
  }

  async function loadList() {
    const worker = $('ib-worker').value;
    const date   = $('ib-date').value;
    if (!worker || !date) { gallery.innerHTML = ''; return; }
    gallery.innerHTML = '<div class="empty-hint">Loading…</div>';
    const params = new URLSearchParams({ worker, date });
    const r = await fetch('/admin/api/browse?' + params.toString() + '&_t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) { gallery.innerHTML = '<div class="empty-hint">Load failed.</div>'; return; }
    const data = await r.json();
    // The project decides its own quality threshold; 0 means "don't warn".
    if (typeof data.min_width === 'number') MIN_WIDTH = data.min_width;
    titles = data.titles || [];
    placeCheck = data.place_check || placeCheck;
    updatePlaceButton();
    sortTitles();
    // Restore title index from URL if available and valid, else 0.
    titleIdx = (restoredIdx > 0 && restoredIdx < titles.length) ? restoredIdx : 0;
    clearSelection();
    refreshSummary();
    renderGallery();
    saveStateToUrl();
    // Resume into the lightbox the last visit left open. One-shot, and
    // self-healing: an image that was released or deleted since is simply
    // not found, and the page stays on the normal gallery.
    if (pendingLightbox) {
      const pid = pendingLightbox;
      pendingLightbox = 0;
      for (let i = 0; i < titles.length; i++) {
        const p = (titles[i].posters || []).find((x) => x.poster_id === pid);
        if (p) {
          titleIdx = i;
          renderGallery();
          openLightbox(titles[i], p);
          break;
        }
      }
    }
  }

  // ── HOW THE DAY'S TITLES ARE ORDERED — the admin's choice, remembered.
  // 'number' is the default and it FIXES a quiet fault: the server sorts
  // by folder NAME, and as text "10. Foo" comes before "2. Bar", so the
  // sheet order looked shuffled (found 2026-09-13 while adding this).
  // Sorting numerically here is what anyone reading the numbers expects.
  const BROWSE_SORT_KEY = 'pd-browse-sort';

  function currentBrowseSort() {
    try {
      const v = localStorage.getItem(BROWSE_SORT_KEY) || 'number';
      const ok = ['number', 'newest', 'flagged', 'place'].includes(v) ? v : 'number';
      // 'place' only means anything while the place check is on. If it is
      // off, fall back to number rather than offering an order that cannot
      // sort by anything — a control that does nothing is worse than absent.
      if (ok === 'place' && !placeCheck.enabled) return 'number';
      return ok;
    } catch (e) { return 'number'; }
  }

  // The worst (most-urgent) place verdict among a title's images, as a rank
  // — lower is more urgent. Reuses placeVerdict() so this order and the
  // PLACE CHECK panel's order are the SAME ordering read from one place, and
  // cannot drift apart. A title with no images sorts as "no problem".
  function worstPlaceRank(t) {
    const ranks = (t.posters || []).map((p) => placeVerdict(p).rank);
    return ranks.length ? Math.min(...ranks) : 99;
  }

  // One spelling of each pill, so the grid box and the zoomed view show the
  // same thing. The source pill (Brave/Google/pasted) and the place-check
  // pill both live here and are reused in both places.
  function sourcePillNode(p) {
    if (!p.image_source) return null;
    const s = document.createElement('span');
    s.className = 'status-pill status-img-source';
    s.textContent = { brave: 'Brave', google: 'Google',
                      pasted: 'pasted' }[p.image_source] || p.image_source;
    s.title = { brave: 'Found with the in-page Brave search',
                google: 'Sent from Google by the phone add-on',
                pasted: 'Pasted as a link by hand' }[p.image_source] || '';
    return s;
  }
  function placePillNode(p) {
    const v = placeVerdict(p);
    const s = document.createElement('span');
    s.className = 'place-pill ' + v.cls;
    s.textContent = v.word;
    s.title = v.tip;
    return s;
  }

  // A title is DONE when every live picture on it carries the admin's K
  // mark. Derived from the pictures, never stored on the title — so a new
  // picture arriving on a finished title un-greens it by itself, with no
  // second record to keep in step.
  function titleReviewed(t) {
    const ps = t.posters || [];
    return ps.length > 0 && ps.every((p) => p.reviewed);
  }

  // The day's headline: titles, pictures — and how many still owed an eye,
  // because a fast scroll can miss a green outline and the number cannot
  // be missed (owner's ask, 2026-09-18). Recomputed from the list, so a K
  // press updates it live.
  function refreshSummary() {
    const nT = titles.length;
    const nP = titles.reduce((n, t) => n + (t.posters || []).length, 0);
    const un = titles.reduce(
      (n, t) => n + (t.posters || []).filter((p) => !p.reviewed).length, 0);
    const tail = nP === 0 ? ''
      : (un === 0 ? ' · all reviewed ✓' : ` · ${un} NOT YET REVIEWED`);
    $('ib-summary').textContent =
      `${nT} title(s) · ${nP} ${nP === 1 ? PD.noun : PD.nouns} total${tail}`;
  }

  function sortTitles() {
    const numOf = (t) => (t.external_id == null ? Infinity : Number(t.external_id));
    const newestOf = (t) => Math.max(0, ...(t.posters || []).map((p) => p.poster_id || 0));
    const mode = currentBrowseSort();
    if (mode === 'newest') {
      // Row ids only ever grow, so the biggest id is the latest save.
      titles.sort((a, b) => newestOf(b) - newestOf(a));
    } else if (mode === 'flagged') {
      titles.sort((a, b) =>
        ((b.needs_revision ? 1 : 0) - (a.needs_revision ? 1 : 0))
        || (numOf(a) - numOf(b)));
    } else if (mode === 'place') {
      // Problems on top: worth-a-look, then no-read, then not-checked, then
      // the fine ones — the same order as the PLACE CHECK panel. Sheet
      // number breaks ties so the top group is still in a stable order.
      titles.sort((a, b) => (worstPlaceRank(a) - worstPlaceRank(b))
        || (numOf(a) - numOf(b)));
    } else {
      titles.sort((a, b) => numOf(a) - numOf(b));
    }
    // REVIEWED SINKS, whatever the dropdown says: the ones still owed an
    // eye float to the top in the chosen order, the finished ones follow in
    // the same order (owner's ask, 2026-09-18). A stable sort keeps both
    // groups internally ordered. Applied at SORT time only — pressing K
    // never reorders the page under the cursor; the mark sinks on the next
    // load or dropdown change.
    titles.sort((a, b) => (titleReviewed(a) ? 1 : 0) - (titleReviewed(b) ? 1 : 0));
  }

  function renderGallery() {
    gallery.innerHTML = '';
    if (titles.length === 0) {
      gallery.innerHTML = `<div class="empty-hint">No saved ${PD.nouns} for this user on this date. Try <strong>ALL DATES</strong>, use the ‹ › arrows, or pick another worker above.</div>`;
      $('ib-title-counter').textContent = '— / —';
      return;
    }
    titles.forEach((t, i) => {
      const node = tplTitle.content.cloneNode(true);
      const section = node.querySelector('.g-title');
      section.id = `g-title-${i}`;
      if (i === titleIdx) section.classList.add('current');
      // Red outranks green: a flag needs him, the K mark is only memory.
      if (t.needs_revision) section.classList.add('flagged');
      else if (titleReviewed(t)) section.classList.add('reviewed');
      // Projects without a year render the name alone rather than "(N/A)".
      node.querySelector('.g-title-name').textContent =
        t.year ? `${t.title} (${t.year})` : t.title;
      // The subject drawing and word — the same strip the worker saw while
      // choosing, so the judge knows a castle was wanted and not a city.
      node.querySelector('.g-title-kind').innerHTML =
        window.SubjectKind ? window.SubjectKind.chip(t.kind) : '';
      node.querySelector('.g-title-meta').textContent =
        `${t.posters.length} ${t.posters.length === 1 ? PD.noun : PD.nouns} · ${t.title_folder}`;
      const grid = node.querySelector('.g-title-posters');
      t.posters.forEach((p) => {
        const btn = buildPosterCell(t, p);
        grid.appendChild(btn);
      });
      // Wire up admin add-poster button
      const addUrl = node.querySelector('.g-add-url');
      const addBtn = node.querySelector('.g-add-btn');
      addBtn.addEventListener('click', async () => {
        const u = (addUrl.value || '').trim();
        if (!u) { addUrl.focus(); return; }
        addBtn.disabled = true;
        addBtn.textContent = '…';
        const fd = new FormData();
        fd.append('master_id', t.master_id);
        fd.append('url', u);
        const r = await fetch('/admin/poster/add', { method: 'POST', body: fd });
        addBtn.disabled = false;
        addBtn.textContent = '+ ADD';
        if (r.ok) { addUrl.value = ''; loadList(); }
        else {
          let msg = r.status;
          try { const d = await r.json(); msg = d.detail || msg; } catch (e) {}
          alert('Add failed: ' + msg);
        }
      });
      gallery.appendChild(node);
    });
    $('ib-title-counter').textContent = `${titleIdx + 1} / ${titles.length}`;
    const cur = document.getElementById(`g-title-${titleIdx}`);
    if (cur) cur.scrollIntoView({ block: 'nearest' });
  }

  function buildPosterCell(t, p) {
    const node = tplPoster.content.cloneNode(true);
    const btn = node.querySelector('.g-poster');
    btn.dataset.posterId = p.poster_id;
    // The K mark — same green outline the Approve Artwork keep uses.
    if (p.reviewed) btn.classList.add('reviewed');
    btn.querySelector('.g-poster-img').src = fileUrl(p.poster_id, p.size || p.filename);
    btn.querySelector('.g-poster-img').alt = p.filename;
    btn.querySelector('.g-poster-name').textContent = p.filename;

    // Quality classes/badges.
    const isSub800 = (MIN_WIDTH > 0 && p.image_width != null && p.image_width < MIN_WIDTH);
    if (isSub800) btn.classList.add('p-sub800');
    if (p.low_quality_url) btn.classList.add('p-lq-bypass');

    // Build the badge stack (status pills row at the bottom).
    const pillsHost = btn.querySelector('.g-poster-pill');
    pillsHost.innerHTML = '';
    if (isSub800) {
      const pill = document.createElement('span');
      pill.className = 'status-pill status-sub800';
      pill.textContent = `${p.image_width}px wide`;
      pillsHost.appendChild(pill);
    }
    if (p.low_quality_url) {
      const pill = document.createElement('span');
      pill.className = 'status-pill status-lq-bypass';
      pill.textContent = 'LQ URL bypassed';
      pillsHost.appendChild(pill);
    }
    // Where the worker found the picture. Old saves carry no source and
    // show no pill — absence, not a guess.
    if (p.image_source) {
      const pill = document.createElement('span');
      pill.className = 'status-pill status-img-source';
      pill.textContent = { brave: 'Brave', google: 'Google',
                           pasted: 'pasted' }[p.image_source] || p.image_source;
      pill.title = { brave: 'Found with the in-page Brave search',
                     google: 'Sent from Google by the phone add-on',
                     pasted: 'Pasted as a link by hand' }[p.image_source] || '';
      pillsHost.appendChild(pill);
    }
    // The place check's verdict. A DOT marker rather than a rectangle, so
    // at a glance it cannot be confused with the source pill beside it —
    // "found via Google" and "Google says it is the right place" are two
    // different facts. Absent entirely when the feature is off.
    if (placeCheck.enabled) {
      const pv = placeVerdict(p);
      const pill = document.createElement('span');
      pill.className = 'place-pill ' + pv.cls;
      pill.textContent = pv.word;
      pill.title = pv.tip;
      pillsHost.appendChild(pill);
    }
    if (p.added_by) {
      const pill = document.createElement('span');
      pill.className = 'status-pill status-admin-added';
      pill.textContent = 'ADMIN';
      pill.title = `Added by ${p.added_by} (not worker)`;
      pillsHost.appendChild(pill);
      btn.classList.add('p-admin-added');
    }
    if (p.flagged) {
      const pill = document.createElement('span');
      pill.className = 'status-pill';
      if (p.revision_status === 'awaiting_approval') {
        pill.classList.add('status-awaiting');
        pill.textContent = 'awaiting approval';
        btn.classList.add('p-awaiting');
      } else if (p.revision_type === 'similar') {
        pill.classList.add('status-similar');
        pill.textContent = 'similar pair';
        btn.classList.add('p-flagged');
      } else {
        pill.classList.add('status-flag');
        pill.textContent = 'flagged';
        btn.classList.add('p-flagged');
      }
      pillsHost.appendChild(pill);
    }

    if (selected.has(p.poster_id)) btn.classList.add('p-selected');

    // Per-poster checkbox for similar-mark. Visible always; clicking it
    // toggles selection and auto-reveals the floating bulk-action bar.
    // Clicking the image (NOT the checkbox) still opens the lightbox.
    const check = document.createElement('button');
    check.type = 'button';
    check.className = 'g-poster-check';
    check.setAttribute('aria-label', 'Select for similar-mark');
    check.title = 'Tick 2+ to mark as similar';
    check.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSelected(p.poster_id, btn, t.master_id, t.title);
    });
    btn.appendChild(check);

    // Admin delete button — small ✕ in top-right corner.
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'g-poster-delete';
    delBtn.setAttribute('aria-label', `Delete ${PD.noun}`);
    delBtn.title = 'Admin delete (does not count against worker)';
    delBtn.textContent = '✕';
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete ${p.filename}? This removes the file and doesn't count against the worker.`)) return;
      const fd = new FormData();
      fd.append('note', 'admin delete from browse');
      const r = await fetch(`/admin/poster/${p.poster_id}/delete`, { method: 'POST', body: fd });
      if (r.ok) loadList();
      else alert('Delete failed.');
    });
    btn.appendChild(delBtn);

    // History — the full provenance of this one image. Read-only, so it sits
    // next to the destructive controls without any confirmation of its own.
    const histBtn = document.createElement('button');
    histBtn.type = 'button';
    histBtn.className = 'g-poster-history';
    histBtn.setAttribute('aria-label', `${PD.Noun} history`);
    histBtn.title = 'Where this image came from and everything that happened to it';
    histBtn.textContent = '🕑';
    histBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openTimeline(p.poster_id);
    });
    btn.appendChild(histBtn);

    btn.addEventListener('click', (e) => {
      if (e.target === check || e.target === delBtn) return;
      openLightbox(t, p);
    });
    return node;
  }

  // Track which master_id all current selections belong to. Clearing the
  // selection (or clicking a poster from a different title) resets this.
  let selectionMasterId = null;
  let selectionTitleName = '';

  function toggleSelected(id, btn, masterId, titleName) {
    if (selected.has(id)) {
      selected.delete(id);
      btn.classList.remove('p-selected');
      if (selected.size === 0) {
        selectionMasterId = null;
        selectionTitleName = '';
      }
    } else {
      // Enforce single-title rule. If admin starts a new title's selection,
      // confirm whether they want to clear the previous one.
      if (selectionMasterId !== null && selectionMasterId !== masterId) {
        if (!confirm(
          `You already have ${selected.size} ${selected.size === 1 ? PD.noun : PD.nouns} selected from "${selectionTitleName}". ` +
          `Switch to selecting from "${titleName}" instead? (Current selection will clear.)`
        )) return;
        selected.clear();
        document.querySelectorAll('.g-poster.p-selected').forEach((b) => b.classList.remove('p-selected'));
        selectionMasterId = null;
      }
      selected.add(id);
      btn.classList.add('p-selected');
      selectionMasterId = masterId;
      selectionTitleName = titleName;
    }
    updateBulkBar();
  }

  function updateBulkBar() {
    const bar = $('ib-bulk-bar');
    if (!bar) return;
    const n = selected.size;
    if (n === 0) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    $('ib-bulk-count-n').textContent = String(n);
    $('ib-bulk-title-name').textContent = selectionTitleName ? `(from "${selectionTitleName}")` : '';
    const btn = $('ib-bulk-mark-similar');
    btn.textContent = n < 2 ? `MARK SIMILAR (need ${2 - n} more)` : `MARK SIMILAR (${n})`;
    btn.disabled = (n < 2);
  }

  function clearSelection() {
    selected.clear();
    selectionMasterId = null;
    selectionTitleName = '';
    document.querySelectorAll('.g-poster.p-selected').forEach((b) => b.classList.remove('p-selected'));
    updateBulkBar();
  }

  function navTitle(d) {
    if (titles.length === 0) return;
    titleIdx = Math.max(0, Math.min(titles.length - 1, titleIdx + d));
    document.querySelectorAll('.g-title.current').forEach((el) => el.classList.remove('current'));
    const cur = document.getElementById(`g-title-${titleIdx}`);
    if (cur) {
      cur.classList.add('current');
      cur.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    $('ib-title-counter').textContent = `${titleIdx + 1} / ${titles.length}`;
    saveStateToUrl();
  }

  // ── Lightbox ─────────────────────────────────────────────────────────────
  const lightbox = $('ib-lightbox');
  const lbImg    = $('ib-lb-img');
  const lbMeta   = $('ib-lb-meta');
  const lbFlag   = $('ib-lb-flag');
  const lbComment    = $('ib-lb-comment');
  const lbFlagBtn    = $('ib-lb-flag-btn');
  const lbUnflagBtn  = $('ib-lb-unflag-btn');

  // Every (title, image) pair currently on screen, flattened in reading
  // order — one image per title in travel, so stepping through images IS
  // stepping through titles. Asked for by the owner 2026-09-06: click one
  // image, then arrow through the whole day without closing the lightbox.
  function lightboxList() {
    const out = [];
    (titles || []).forEach((t) => (t.posters || []).forEach((p) => out.push({ t, p })));
    return out;
  }
  function lightboxStep(delta) {
    const list = lightboxList();
    if (!list.length || !currentLightbox) return;
    const at = list.findIndex((e) => e.p.poster_id === currentLightbox.poster.poster_id);
    const next = list[at + delta];
    if (next) openLightbox(next.t, next.p);
  }

  function openLightbox(t, p) {
    currentLightbox = { master: t, poster: p };
    // The PLACE, named first and large — a bare image of somewhere you
    // cannot name is exactly what the owner asked to never see again.
    const nameEl = $('ib-lb-title');
    if (nameEl) {
      nameEl.textContent = (t.external_id != null ? t.external_id + '. ' : '')
        + t.title + (t.year ? ` (${t.year})` : '');
    }
    const kindEl = $('ib-lb-kind');
    if (kindEl) kindEl.innerHTML = window.SubjectKind ? window.SubjectKind.chip(t.kind) : '';
    const posEl = $('ib-lb-pos');
    if (posEl) {
      const list = lightboxList();
      const at = list.findIndex((e) => e.p.poster_id === p.poster_id);
      posEl.textContent = at >= 0 ? `${at + 1} / ${list.length}` : '';
    }
    lbImg.src = fileUrl(p.poster_id, p.size || p.filename);
    lbImg.alt = p.filename;
    const dims = (p.image_width && p.image_height) ? ` · ${p.image_width}×${p.image_height}` : '';
    const lq   = p.low_quality_url ? ' · ⚠ LQ-URL bypassed' : '';
    const from = { brave: ' · found on Brave', google: ' · found on Google',
                   pasted: ' · pasted link' }[p.image_source] || '';
    lbMeta.textContent =
      `${t.title}${t.year ? ` (${t.year})` : ''} — ${p.filename}${dims}${lq}${from}`;

    // The same pills the grid box carries, so you can judge the place from
    // the zoom without going back — the source pill, and the place-check
    // verdict when the check is on.
    const pillsHost = $('ib-lb-pills');
    if (pillsHost) {
      pillsHost.innerHTML = '';
      const src = sourcePillNode(p);
      if (src) pillsHost.appendChild(src);
      if (placeCheck.enabled) pillsHost.appendChild(placePillNode(p));
    }
    // The K mark, so the zoom agrees with the grid about what you have seen.
    renderLbReviewed(p);
    // The "checked, it's fine" button, right here in the zoom — shown only
    // for a verdict that CAN be acknowledged (Google disagreed, or had no
    // opinion). It reads the same p.place_acked the grid does, so pressing
    // it here and reopening the grid agree.
    const ackBtn = $('ib-lb-place-ack');
    if (ackBtn) {
      const ackable = placeCheck.enabled
        && (p.place_status === 'mismatch' || p.place_status === 'no_opinion');
      ackBtn.hidden = !ackable;
      ackBtn.textContent = p.place_acked ? 'UNDO — MARK IT A PROBLEM AGAIN'
                                         : "CHECKED, IT'S FINE";
    }
    // CHECK GOOGLE — opens Google Images for this place in a new tab, so the
    // scenic view can be confirmed without leaving the zoom. The URL is built
    // by the server the same way the worker's GOOGLE button is; "" means the
    // project has no source link, so the button stays hidden.
    const gBtn = $('ib-lb-google');
    if (gBtn) gBtn.hidden = !(t && t.google_url);

    if (p.flagged) {
      lbFlag.hidden = false;
      const pill = lbFlag.querySelector('.lb-status-pill');
      pill.className = 'status-pill';
      if (p.revision_status === 'awaiting_approval') {
        pill.classList.add('status-awaiting');
        pill.textContent = 'awaiting approval';
      } else {
        pill.classList.add('status-flag');
        pill.textContent = 'open';
      }
      lbFlag.querySelector('.lb-flag-comment').textContent = p.comment || '(no comment)';
      lbFlag.querySelector('.lb-worker-note').textContent =
        p.worker_note ? `User note: ${p.worker_note}` : '';
      lbUnflagBtn.hidden = false;
    } else {
      lbFlag.hidden = true;
      lbUnflagBtn.hidden = true;
    }
    lbComment.value = '';
    lightbox.hidden = false;
    // Remembered open, so an interrupted visit reopens right here.
    lbOpenPoster = p.poster_id;
    saveStateToUrl();
  }

  function closeLightbox() {
    lightbox.hidden = true;
    currentLightbox = null;
    // A deliberate close is remembered as closed — the next visit opens
    // the plain gallery, not a lightbox nobody asked for.
    lbOpenPoster = 0;
    saveStateToUrl();
  }

  // ── RETIRE TITLE — no good photo of this place exists ─────────────────
  // Opened from the zoom. Two typed locks (a reason + the word Confirm),
  // both re-checked by the server: this deletes a paid-for file with no
  // undo, so a stray click must not be enough. The worker keeps their pay.
  const retireDialog = document.getElementById('retire-dialog');
  const retireBtn    = document.getElementById('ib-lb-retire');
  if (retireDialog && retireBtn) {
    const nameEl    = document.getElementById('retire-title-name');
    const reasonEl  = document.getElementById('retire-reason');
    const confirmEl = document.getElementById('retire-confirm');
    const goBtn     = document.getElementById('retire-go');

    function retireArm() {
      goBtn.disabled = !(reasonEl.value.trim()
                         && confirmEl.value.trim() === 'Confirm');
    }
    reasonEl.addEventListener('input', retireArm);
    confirmEl.addEventListener('input', retireArm);

    function retireClose() {
      retireDialog.hidden = true;
      reasonEl.value = '';
      confirmEl.value = '';
      retireArm();
    }
    retireDialog.querySelectorAll('[data-retire-close]').forEach((el) => {
      el.addEventListener('click', retireClose);
    });

    retireBtn.addEventListener('click', () => {
      if (!currentLightbox) return;
      nameEl.textContent = currentLightbox.master.title || '';
      retireDialog.hidden = false;
      reasonEl.focus();
    });

    goBtn.addEventListener('click', async () => {
      if (!currentLightbox) return;
      const masterId = currentLightbox.master.master_id;
      goBtn.disabled = true;
      try {
        const fd = new FormData();
        fd.append('reason', reasonEl.value.trim());
        fd.append('confirm', confirmEl.value.trim());
        const r = await fetch(`/admin/title/${masterId}/retire`,
                              { method: 'POST', body: fd });
        if (r.ok) {
          retireClose();
          closeLightbox();
          loadList();          // the title leaves this day's gallery
          return;
        }
        let msg = String(r.status);
        try { const d = await r.json(); msg = d.detail || msg; } catch (e) {}
        alert('Could not retire: ' + msg);
      } catch (e) {
        alert('Could not retire: ' + e);
      }
      // Only reached on failure — success closed everything above.
      retireArm();
    });
  }

  // ── THE K MARK — "I have looked at this one" ──────────────────────────
  // A place-keeper for a review interrupted halfway: green outline here,
  // in the grid and on the title box, and the day header counts what is
  // still owed an eye. It decides nothing — see the column comment in
  // models.py. Same key as Approve Artwork's keep, on purpose (muscle
  // memory), and the same key undoes it.
  function renderLbReviewed(p) {
    lightbox.classList.toggle('lb-reviewed', !!p.reviewed);
    const host = $('ib-lb-pills');
    if (!host) return;
    let pill = host.querySelector('.status-reviewed');
    if (p.reviewed && !pill) {
      pill = document.createElement('span');
      pill.className = 'status-pill status-reviewed';
      pill.textContent = 'REVIEWED ✓';
      pill.title = 'You marked this one as looked-at (K). Press K again to undo.';
      host.appendChild(pill);
    } else if (!p.reviewed && pill) {
      pill.remove();
    }
  }

  async function toggleReviewed(t, p) {
    const r = await fetch(`/admin/poster/${p.poster_id}/reviewed`, { method: 'POST' });
    if (!r.ok) { alert('Could not save the mark: ' + r.status); return; }
    const d = await r.json();
    p.reviewed = !!d.reviewed;
    // Everything DERIVED from the mark gets redone — the grid cell, the
    // title outline, the day count, and the zoom if it shows this one.
    // Deliberately NOT re-sorted here: the page must never reorder under
    // the cursor mid-review; reviewed ones sink on the next load or when
    // the order dropdown is touched.
    renderGallery();
    refreshSummary();
    if (currentLightbox && currentLightbox.poster.poster_id === p.poster_id) {
      renderLbReviewed(p);
    }
  }

  async function toggleReviewedTitle(t) {
    // K on a title in the grid: if anything on it is still unmarked, mark
    // it all — otherwise unmark it all. One intention per press, never a
    // mixed flip. (Travel holds one picture per title, so this is simply
    // a toggle there.)
    const ps = (t && t.posters) || [];
    if (!ps.length) return;
    const marking = ps.some((p) => !p.reviewed);
    for (const p of ps) {
      if (!!p.reviewed !== marking) {
        await toggleReviewed(t, p);
      }
    }
  }

  document.querySelectorAll('[data-lightbox-close]').forEach((el) => {
    el.addEventListener('click', closeLightbox);
  });
  const lbPrev = $('ib-lb-prev'), lbNext = $('ib-lb-next');
  if (lbPrev) lbPrev.addEventListener('click', () => lightboxStep(-1));
  if (lbNext) lbNext.addEventListener('click', () => lightboxStep(1));

  // Rebuild ONE card in place — no full reload, so the gallery keeps its
  // scroll position and the zoom stays open. Flagging used to close the zoom
  // and reload the whole day, which threw the owner back to the top and
  // broke stepping to the next image (2026-09-15).
  function rerenderPosterCard(t, p) {
    const old = gallery.querySelector(`.g-poster[data-poster-id="${p.poster_id}"]`);
    if (old) old.replaceWith(buildPosterCell(t, p));
  }

  // The title's red outline (.g-title.flagged) is set once at render from
  // needs_revision. Flagging or clearing a flag inside the zoom rebuilds only
  // the ONE image card, so without this the title outline lingered after the
  // last flag was cleared — the admin resolved a flag and the title was still
  // ringed red until a full reload (owner, 2026-09-17). Recompute the title's
  // flag state from the posters we already hold and toggle the class live.
  function refreshTitleFlagOutline(master, poster) {
    const anyFlagged = (master.posters || []).some((pp) => pp.flagged);
    master.needs_revision = anyFlagged;
    const card = gallery.querySelector(`.g-poster[data-poster-id="${poster.poster_id}"]`);
    const section = card ? card.closest('.g-title') : null;
    if (section) section.classList.toggle('flagged', anyFlagged);
  }

  lbFlagBtn.addEventListener('click', async () => {
    if (!currentLightbox) return;
    const { master, poster } = currentLightbox;
    lbFlagBtn.disabled = true;
    try {
      const fd = new FormData();
      fd.append('comment', lbComment.value || '');
      const r = await fetch(`/admin/poster/${poster.poster_id}/flag`, { method: 'POST', body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { alert('Flag failed: ' + (d.detail || r.status)); return; }
      // Update the data in place, then refresh just this card and the zoom.
      // The zoom stays open on the same image, so ← / → still step to the
      // next one — flagged or not — without a reload.
      poster.flagged = true;
      poster.revision_id = d.id ?? poster.revision_id;
      poster.revision_status = 'open';
      poster.revision_type = null;
      poster.comment = lbComment.value || '';
      rerenderPosterCard(master, poster);
      refreshTitleFlagOutline(master, poster);
      openLightbox(master, poster);
    } finally {
      lbFlagBtn.disabled = false;
    }
  });

  lbUnflagBtn.addEventListener('click', async () => {
    if (!currentLightbox) return;
    const { master, poster } = currentLightbox;
    lbUnflagBtn.disabled = true;
    try {
      const r = await fetch(`/admin/poster/${poster.poster_id}/unflag`, { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { alert('Unflag failed: ' + (d.detail || r.status)); return; }
      poster.flagged = false;
      poster.revision_id = null;
      poster.revision_status = null;
      poster.revision_type = null;
      poster.comment = '';
      rerenderPosterCard(master, poster);
      refreshTitleFlagOutline(master, poster);
      openLightbox(master, poster);
    } finally {
      lbUnflagBtn.disabled = false;
    }
  });

  // Acknowledge the place check from inside the zoom, so you can arrow
  // through the flagged ones and clear each without going back to the grid.
  // Stays in the lightbox afterward — the point is to keep moving. The
  // underlying poster object is updated in place, so the pill and the grid
  // behind agree the moment you look at them.
  const lbGoogle = $('ib-lb-google');
  if (lbGoogle) {
    lbGoogle.addEventListener('click', () => {
      if (!currentLightbox || !currentLightbox.master.google_url) return;
      // A new tab, so the zoom stays open behind it and you can keep arrowing.
      window.open(currentLightbox.master.google_url, '_blank', 'noopener');
    });
  }

  const lbPlaceAck = $('ib-lb-place-ack');
  if (lbPlaceAck) {
    lbPlaceAck.addEventListener('click', async () => {
      if (!currentLightbox) return;
      const { master, poster } = currentLightbox;
      lbPlaceAck.disabled = true;
      try {
        const r = await fetch('/admin/api/place_check/ack', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ poster_id: poster.poster_id, on: !poster.place_acked }),
        });
        const d = await r.json();
        if (r.ok && d.ok) {
          poster.place_acked = d.acked;   // same object the grid renders
          openLightbox(master, poster);   // refresh the pill and the button
        } else {
          alert('Could not save that: ' + (d.detail || r.status));
        }
      } finally { lbPlaceAck.disabled = false; }
    });
  }

  // Keyboard nav
  document.addEventListener('keydown', (e) => {
    const ae = document.activeElement;
    if (ae && ['INPUT', 'SELECT', 'TEXTAREA'].includes(ae.tagName)) return;
    if (!lightbox.hidden) {
      if (e.key === 'Escape') closeLightbox();
      if (e.key === 'ArrowLeft')  lightboxStep(-1);
      if (e.key === 'ArrowRight') lightboxStep(1);
      // K = "I have looked at this one", K again undoes — the same key
      // Approve Artwork uses for keep, so the hand already knows it.
      if ((e.key === 'k' || e.key === 'K') && currentLightbox) {
        toggleReviewed(currentLightbox.master, currentLightbox.poster);
      }
      return;
    }
    if (e.key === 'ArrowLeft')  navTitle(-1);
    if (e.key === 'ArrowRight') navTitle(1);
    // K in the plain gallery marks the CURRENT title — the one the arrows
    // stand on and the outline highlights — so arrow-arrow-K works the
    // same here as in the zoom.
    if ((e.key === 'k' || e.key === 'K') && titles[titleIdx]) {
      toggleReviewedTitle(titles[titleIdx]);
    }
  });

  $('ib-load').addEventListener('click', loadList);
  $('ib-worker').addEventListener('change', () => {
    const url = new URL(window.location.href);
    url.searchParams.set('worker', $('ib-worker').value);
    url.searchParams.delete('date');
    url.searchParams.delete('idx');
    window.location.href = url.toString();
  });
  $('ib-prev-title').addEventListener('click', () => navTitle(-1));
  $('ib-next-title').addEventListener('click', () => navTitle(1));

  // ── The place check ─────────────────────────────────────────────────────
  // Each saved image was shown to Google, and Google's words are compared
  // to the title. The pill on each box and the rows in this panel read the
  // SAME per-poster fields off `titles`, so they cannot disagree. The fill
  // loop below asks the server for one small chunk at a time and the stop
  // signal is simply not asking again — nothing is ever queued server-side,
  // so closing the panel IS the stop button.

  // One spelling of each state's word, colour class and explanation, shared
  // by the pill and the panel row.
  function placeVerdict(p) {
    if (p.place_status === 'match') {
      return { cls: 'place-ok', word: 'place ✓', rank: 4,
               tip: 'Google sees: ' + (p.place_guess || '(nothing)') };
    }
    if (p.place_status === 'mismatch' || p.place_status === 'no_opinion') {
      if (p.place_acked) {
        return { cls: 'place-acked', word: 'checked ✓', rank: 3,
                 tip: 'You looked at this one and marked it fine. Google saw: '
                      + (p.place_guess || 'nothing') };
      }
      if (p.place_status === 'mismatch') {
        return { cls: 'place-warn', word: 'CHECK PLACE', rank: 0,
                 tip: 'Google\'s words share nothing with the title. Google sees: '
                      + (p.place_guess || '(nothing)') };
      }
      return { cls: 'place-none', word: 'no read', rank: 1,
               tip: 'Google had no opinion on this picture — not a fault, just no answer.' };
    }
    if (p.place_error) {
      return { cls: 'place-err', word: 'check failed', rank: 2,
               tip: 'The check could not run. Open PLACE CHECK THIS DAY to see why and retry.' };
    }
    return { cls: 'place-wait', word: 'not checked', rank: 2,
             tip: 'Not checked yet. Press PLACE CHECK THIS DAY to fill it in.' };
  }

  function updatePlaceButton() {
    const btn = $('ib-place-btn');
    if (btn) btn.hidden = !placeCheck.enabled;
    // The "problems first" order only appears while the place check is on.
    const opt = document.querySelector('[data-place-sort]');
    if (opt) opt.hidden = !placeCheck.enabled;
    // Keep the visible dropdown showing the order actually in use. The
    // dropdown is first set at page load, before this page knows whether the
    // place check is on, so a remembered 'place' choice could otherwise show
    // 'number' in the menu while the grid really is sorted by place (or the
    // reverse once it turns off). currentBrowseSort() is the one source of
    // truth for both the menu and the sort.
    const sel = $('ib-sort');
    if (sel) sel.value = currentBrowseSort();
  }

  const placeModal = $('ib-place-modal');
  let placeRunning = false;

  function allPosters() {
    const out = [];
    titles.forEach((t) => (t.posters || []).forEach((p) => out.push({ t, p })));
    return out;
  }

  function renderPlacePanel() {
    if (!placeModal) return;
    const rows = allPosters();
    const unchecked = rows.filter((r) => r.p.place_status == null).length;
    const worry = rows.filter((r) => placeVerdict(r.p).rank === 0).length;

    $('ib-place-day').textContent = `${$('ib-worker').value} · ${$('ib-date').value}`;
    $('ib-place-month').textContent =
      `${placeCheck.checked_this_month} image(s) checked this month across all days · Google's first 1,000 each month are free`;

    let summary;
    if (!placeCheck.key_present) {
      summary = 'No Google Vision key yet, so nothing can be checked. '
        + 'Paste the key into the KEYS panel on the Pipeline page first.';
    } else if (rows.length === 0) {
      summary = 'Nothing saved on this day.';
    } else {
      summary = `${rows.length} image(s) on this day · ${worry} worth a look · ${unchecked} not checked yet. `
        + 'Rows are ordered by how much they need you: Google disagreeing first, '
        + 'then no answer, then the rest.';
    }
    $('ib-place-summary').textContent = summary;

    const runBtn = $('ib-place-run');
    runBtn.textContent = placeRunning
      ? 'CHECKING…'
      : `CHECK THE ${unchecked} UNCHECKED ON THIS DAY`;
    runBtn.disabled = placeRunning || unchecked === 0 || !placeCheck.key_present;

    const list = $('ib-place-list');
    list.innerHTML = '';
    rows
      .map((r) => ({ ...r, v: placeVerdict(r.p) }))
      .sort((a, b) => (a.v.rank - b.v.rank)
        || ((a.t.external_id == null ? Infinity : a.t.external_id)
          - (b.t.external_id == null ? Infinity : b.t.external_id)))
      .forEach(({ t, p, v }) => {
        const row = document.createElement('div');
        row.className = 'place-row ' + v.cls;

        const img = document.createElement('img');
        img.src = fileUrl(p.poster_id, p.size || p.filename);
        img.loading = 'lazy';
        img.alt = '';
        row.appendChild(img);

        const body = document.createElement('div');
        body.className = 'place-row-body';
        const name = document.createElement('div');
        name.className = 'place-row-title';
        name.textContent = (t.external_id != null ? t.external_id + '. ' : '') + t.title;
        body.appendChild(name);
        const guess = document.createElement('div');
        guess.className = 'place-row-guess muted';
        if (p.place_status != null) {
          guess.textContent = p.place_guess
            ? 'Google sees: ' + p.place_guess
            : 'Google had no opinion.';
        } else if (p.place_error) {
          guess.textContent = 'Check failed — press the button above to retry.';
        } else {
          guess.textContent = 'Not checked yet.';
        }
        body.appendChild(guess);
        row.appendChild(body);

        const chip = document.createElement('span');
        chip.className = 'place-pill ' + v.cls;
        chip.textContent = v.word;
        row.appendChild(chip);

        // The way OUT of the worth-a-look list, on the same screen as the
        // flag — otherwise the count only ever climbs and stops being read.
        if (p.place_status === 'mismatch' || p.place_status === 'no_opinion') {
          const ack = document.createElement('button');
          ack.type = 'button';
          ack.className = 'btn btn-ghost btn-tiny';
          ack.textContent = p.place_acked ? 'UNDO' : 'CHECKED, IT\'S FINE';
          ack.addEventListener('click', async () => {
            ack.disabled = true;
            try {
              const r = await fetch('/admin/api/place_check/ack', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ poster_id: p.poster_id, on: !p.place_acked }),
              });
              const d = await r.json();
              if (r.ok && d.ok) { p.place_acked = d.acked; renderPlacePanel(); }
              else alert('Could not save that: ' + (d.detail || r.status));
            } finally { ack.disabled = false; }
          });
          row.appendChild(ack);
        }
        list.appendChild(row);
      });
  }

  async function placeRunLoop() {
    if (placeRunning) return;
    placeRunning = true;
    renderPlacePanel();
    const statusEl = $('ib-place-status');
    let done = 0;
    try {
      while (placeRunning) {
        const r = await fetch('/admin/api/place_check/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ worker: $('ib-worker').value, date: $('ib-date').value }),
        });
        const d = await r.json();
        if (!r.ok || !d.ok) {
          statusEl.textContent = d.detail || ('The server answered ' + r.status + '.');
          break;
        }
        let failed = '';
        (d.results || []).forEach((res) => {
          for (const { p } of allPosters()) {
            if (p.poster_id === res.poster_id) {
              p.place_status = res.status;
              p.place_guess = res.guess;
              p.place_error = !!res.error;
              if (res.status != null) placeCheck.checked_this_month += 1;
              if (res.error) failed = res.error;
              break;
            }
          }
        });
        done += (d.results || []).length;
        renderPlacePanel();
        if (failed) {
          // The server stops a chunk at the first failure so one broken
          // key reports once instead of stamping every row. Stopping the
          // loop too keeps the message on screen instead of repeating it.
          statusEl.textContent = 'Stopped: ' + failed;
          break;
        }
        if (!d.remaining) {
          statusEl.textContent = `Done — ${done} checked just now.`;
          break;
        }
        statusEl.textContent = `${done} checked · ${d.remaining} to go…`;
      }
    } catch (e) {
      statusEl.textContent = 'Stopped: ' + (e && e.message ? e.message : e);
    } finally {
      // Whatever entered the busy state leaves it on every path.
      placeRunning = false;
      renderPlacePanel();
    }
  }

  if ($('ib-place-btn') && placeModal) {
    $('ib-place-btn').addEventListener('click', () => {
      placeModal.hidden = false;
      $('ib-place-status').textContent = '';
      renderPlacePanel();
    });
    placeModal.querySelectorAll('[data-place-close]').forEach((el) => {
      el.addEventListener('click', () => {
        placeRunning = false;          // closing the panel IS the stop button
        placeModal.hidden = true;
        renderGallery();               // pills catch up with what was checked
      });
    });
    $('ib-place-run').addEventListener('click', placeRunLoop);
    updatePlaceButton();
  }

  // ── Date data (must come before saved-state restore) ────────────────────
  const dates = window.__dates || [];
  const dateSet = new Set(dates);
  const dateInput = $('ib-date');
  const dateLabel = $('ib-date-label');

  // ── Saved state helpers ─────────────────────────────────────────────────
  const BROWSE_STATE_KEY = 'pd-browse-state';
  function saveStateToUrl() {
    const url = new URL(window.location.href);
    url.searchParams.set('worker', $('ib-worker').value);
    url.searchParams.set('date', dateInput.value);
    url.searchParams.set('idx', String(titleIdx));
    history.replaceState(null, '', url.toString());
    try {
      localStorage.setItem(BROWSE_STATE_KEY, JSON.stringify({
        worker: $('ib-worker').value,
        date: dateInput.value,
        idx: titleIdx,
        // Which poster the lightbox is on, or 0 for closed — see
        // lbOpenPoster. This is what "walk back in where I was" reads.
        lb: lbOpenPoster,
      }));
    } catch (e) {}
  }
  const urlParams = new URLSearchParams(window.location.search);
  if (!urlParams.has('date') && !urlParams.has('idx')) {
    try {
      const saved = JSON.parse(localStorage.getItem(BROWSE_STATE_KEY) || 'null');
      if (saved && saved.date && dates.indexOf(saved.date) >= 0) {
        if (saved.date !== dateInput.value) {
          dateInput.value = saved.date;
          dateLabel.textContent = saved.date;
        }
      }
    } catch (e) {}
  }
  const restoredIdx = parseInt(urlParams.get('idx'), 10) || (function() {
    try {
      const saved = JSON.parse(localStorage.getItem(BROWSE_STATE_KEY) || 'null');
      return (saved && saved.idx) || 0;
    } catch (e) { return 0; }
  })();

  // The lightbox the last visit left open (poster id, 0 = none). Consumed
  // once by the first loadList; a poster that no longer exists is simply
  // not found, and nothing opens.
  let pendingLightbox = (function () {
    try {
      const saved = JSON.parse(localStorage.getItem(BROWSE_STATE_KEY) || 'null');
      return (saved && saved.lb) || 0;
    } catch (e) { return 0; }
  })();

  // The order dropdown: reflect the remembered choice, re-sort on change.
  const sortSel = $('ib-sort');
  if (sortSel) {
    sortSel.value = currentBrowseSort();
    sortSel.addEventListener('change', () => {
      try { localStorage.setItem(BROWSE_SORT_KEY, sortSel.value); }
      catch (e) { /* a blocked store must never break the screen */ }
      sortTitles();
      titleIdx = 0;
      renderGallery();
      saveStateToUrl();
    });
  }

  // ── Date navigation (prev/next + calendar modal) ────────────────────────

  function setDate(d) {
    dateInput.value = d;
    dateLabel.textContent = d;
    loadList();
    saveStateToUrl();
  }

  $('ib-date-prev').addEventListener('click', () => {
    const idx = dates.indexOf(dateInput.value);
    if (idx >= 0 && idx < dates.length - 1) setDate(dates[idx + 1]);
  });
  $('ib-date-next').addEventListener('click', () => {
    const idx = dates.indexOf(dateInput.value);
    if (idx > 0) setDate(dates[idx - 1]);
  });

  // Calendar modal
  const calModal   = $('date-grid-modal');
  const calGrid    = $('cal-grid');
  const calLabel   = $('cal-month-label');
  const calZip     = $('date-grid-zip');
  let calYear, calMonth;
  let calChecked = new Set();

  function renderCalendar() {
    calLabel.textContent = `${['January','February','March','April','May','June','July','August','September','October','November','December'][calMonth]} ${calYear}`;
    calGrid.innerHTML = '';
    // First day of month (0=Sun..6=Sat) → convert to Mon-based (0=Mon..6=Sun)
    const firstDow = new Date(calYear, calMonth, 1).getDay();
    const monBased = (firstDow + 6) % 7; // 0=Mon
    const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    // Empty cells before first day
    for (let i = 0; i < monBased; i++) {
      const e = document.createElement('div');
      e.className = 'cal-day cal-day-empty';
      calGrid.appendChild(e);
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const iso = `${calYear}-${String(calMonth + 1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
      const hasData = dateSet.has(iso);
      const isActive = iso === dateInput.value;
      const el = document.createElement('div');
      el.className = 'cal-day' +
        (hasData ? ' cal-day-has' : ' cal-day-none') +
        (isActive ? ' cal-day-active' : '') +
        (calChecked.has(iso) ? ' cal-day-checked' : '');
      el.textContent = d;
      if (hasData) {
        // Checkbox for multi-zip
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'cal-day-cb';
        cb.checked = calChecked.has(iso);
        cb.addEventListener('change', (e) => {
          e.stopPropagation();
          if (cb.checked) calChecked.add(iso); else calChecked.delete(iso);
          el.classList.toggle('cal-day-checked', cb.checked);
          updateCalZip();
        });
        el.appendChild(cb);
        // Click the day number to navigate
        el.addEventListener('click', (e) => {
          if (e.target === cb) return; // let checkbox handle itself
          setDate(iso);
          calModal.hidden = true;
        });
      }
      calGrid.appendChild(el);
    }
    updateCalZip();
  }

  function updateCalZip() {
    const n = calChecked.size;
    calZip.textContent = `ZIP SELECTED (${n})`;
    calZip.disabled = n === 0;
  }

  function openCalendar() {
    // Start on the month of the currently selected date
    const cur = dateInput.value || dates[0] || new Date().toISOString().slice(0, 10);
    const parts = cur.split('-');
    calYear = parseInt(parts[0], 10);
    calMonth = parseInt(parts[1], 10) - 1;
    calChecked.clear();
    renderCalendar();
    calModal.hidden = false;
  }

  $('ib-date-grid-btn').addEventListener('click', openCalendar);
  $('cal-prev-month').addEventListener('click', () => {
    calMonth--;
    if (calMonth < 0) { calMonth = 11; calYear--; }
    renderCalendar();
  });
  $('cal-next-month').addEventListener('click', () => {
    calMonth++;
    if (calMonth > 11) { calMonth = 0; calYear++; }
    renderCalendar();
  });
  calModal.querySelectorAll('[data-date-grid-close]').forEach((el) =>
    el.addEventListener('click', () => { calModal.hidden = true; })
  );
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !calModal.hidden) { calModal.hidden = true; e.stopPropagation(); }
  });

  // Multi-day zip from calendar
  calZip.addEventListener('click', async () => {
    if (calChecked.size === 0) return;
    const worker = $('ib-worker').value;
    if (!worker) return;
    const fd = new FormData();
    fd.append('worker', worker);
    fd.append('dates', Array.from(calChecked).sort().join(','));
    calZip.disabled = true;
    calZip.textContent = 'STARTING…';
    const r = await fetch('/admin/zip/start', { method: 'POST', body: fd });
    const data = await r.json().catch(() => ({}));
    calZip.disabled = false;
    updateCalZip();
    if (!r.ok) { alert('Zip start failed.'); return; }
    calModal.hidden = true;
    pollZip(data.job_id);
  });

  // ── Density toggle (1-up vs 2-up gallery layout) ────────────────────────
  const densityBtn = $('ib-density-toggle');
  const DENSITY_KEY = 'pd-browse-density';
  function applyDensity(mode) {
    const isTwoUp = (mode === '2up');
    gallery.classList.toggle('density-2up', isTwoUp);
    if (densityBtn) densityBtn.textContent = isTwoUp ? '☷ 2-UP' : '⊞ 1-UP';
  }
  let densityMode = '1up';
  try {
    const saved = localStorage.getItem(DENSITY_KEY);
    if (saved === '2up' || saved === '1up') densityMode = saved;
  } catch (e) {}
  applyDensity(densityMode);
  if (densityBtn) {
    densityBtn.addEventListener('click', () => {
      densityMode = (densityMode === '2up') ? '1up' : '2up';
      try { localStorage.setItem(DENSITY_KEY, densityMode); } catch (e) {}
      applyDensity(densityMode);
    });
  }

  // ── Bulk action bar (sticky bottom, auto-reveals at 2+ selections) ──────
  const bulkBtn    = $('ib-bulk-mark-similar');
  const bulkCancel = $('ib-bulk-cancel');
  const bulkComment = $('ib-bulk-comment');
  if (bulkBtn) {
    bulkBtn.addEventListener('click', async () => {
      if (selected.size < 2) return;
      const note = (bulkComment.value || '').trim();
      const fd = new FormData();
      fd.append('poster_ids', Array.from(selected).join(','));
      fd.append('comment', note);
      bulkBtn.disabled = true;
      bulkBtn.textContent = 'SAVING…';
      const r = await fetch('/admin/posters/mark_similar', { method: 'POST', body: fd });
      const data = await r.json().catch(() => ({}));
      bulkBtn.disabled = false;
      if (r.ok) {
        clearSelection();
        bulkComment.value = '';
        loadList();
      } else {
        alert('Failed: ' + (data.detail || r.status));
        updateBulkBar();  // restore correct label
      }
    });
  }
  if (bulkCancel) {
    bulkCancel.addEventListener('click', () => {
      clearSelection();
      bulkComment.value = '';
    });
  }
  // Esc clears selection if bar is open.
  document.addEventListener('keydown', (e) => {
    const bar = $('ib-bulk-bar');
    if (e.key === 'Escape' && bar && !bar.hidden) {
      // But only if no lightbox is open (lightbox owns Esc when visible).
      const lb = $('ib-lightbox');
      if (!lb || lb.hidden) {
        clearSelection();
        bulkComment.value = '';
      }
    }
  });

  // ── ZIP day ──────────────────────────────────────────────────────────────
  $('ib-zip').addEventListener('click', async () => {
    const worker = $('ib-worker').value;
    const date   = $('ib-date').value;
    if (!worker || !date) return;
    const fd = new FormData();
    fd.append('worker', worker);
    fd.append('dates', date);  // single date, but same endpoint
    const r = await fetch('/admin/zip/start', { method: 'POST', body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { alert('Zip start failed.'); return; }
    pollZip(data.job_id);
  });

  async function pollZip(jobId) {
    const status = $('ib-zip-status');
    while (true) {
      const r = await fetch(`/admin/zip/status/${jobId}`);
      if (!r.ok) { status.textContent = 'Job lookup failed.'; return; }
      const job = await r.json();
      const total = job.total || 0;
      const pct = total ? Math.round((job.done / total) * 100) : 0;
      status.textContent = `zip · ${job.state} · ${pct}%`;
      if (job.state === 'done') {
        status.innerHTML = `<a href="/admin/zip/download/${jobId}" download>↓ ${job.name}</a>`;
        return;
      }
      if (job.state === 'error') { status.textContent = 'zip failed: ' + (job.error || ''); return; }
      await new Promise((r) => setTimeout(r, 700));
    }
  }

  loadList();
})();


/* ═══════════════════════════════════════════════════════════════════════════
   POSTER TIMELINE
   One image's whole life: downloaded → flagged → fixed → greenlit →
   Photoshopped → uploaded → (removed). Assembled server-side from six tables
   by /admin/api/poster/<id>/timeline; this only renders it.
   ═══════════════════════════════════════════════════════════════════════════ */

async function openTimeline(posterId) {
  let host = document.getElementById('pd-timeline');
  if (!host) {
    host = document.createElement('div');
    host.id = 'pd-timeline';
    host.className = 'pd-modal';
    host.hidden = true;
    host.innerHTML =
      '<div class="pd-modal-backdrop" data-close="1"></div>' +
      '<div class="pd-modal-panel" role="dialog" aria-modal="true" aria-label="History">' +
        '<div class="pd-modal-head">' +
          `<span class="pd-modal-title">${PD.NOUN} HISTORY</span>` +
          '<button type="button" class="pd-modal-close" data-close="1" aria-label="Close">✕</button>' +
        '</div>' +
        '<div class="pd-modal-body"><p class="muted">Loading…</p></div>' +
      '</div>';
    document.body.appendChild(host);
    host.addEventListener('click', (e) => {
      if (e.target.dataset && e.target.dataset.close) host.hidden = true;
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') host.hidden = true;
    });
  }

  const body = host.querySelector('.pd-modal-body');
  body.innerHTML = '<p class="muted">Loading…</p>';
  host.hidden = false;

  let data;
  try {
    const r = await fetch('/admin/api/poster/' + posterId + '/timeline');
    if (!r.ok) throw new Error(r.status);
    data = await r.json();
  } catch (err) {
    body.innerHTML = '<p class="error">Could not load history (' + err.message + ').</p>';
    return;
  }

  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const p = data.poster, t = data.title;
  let html =
    '<div class="tl-head">' +
      '<div class="tl-head-name mono">' + esc(p.filename) + '</div>' +
      // Guarded like every other year on the site. Unguarded, a project with
      // no year rendered "(N/A)" from the old column default and would render
      // an empty "()" once that is NULL — noise either way.
      '<div class="tl-head-sub muted">' + esc(t.name)
        + (t.year ? ' (' + esc(t.year) + ')' : '') +
        (t.external_id != null ? ' · #' + esc(t.external_id) : '') + '</div>' +
      '<div class="tl-head-facts">' +
        '<span>worker <b class="mono">' + esc(p.worker) + '</b></span>' +
        '<span>saved <b class="mono">' + esc(p.saved_on) + '</b></span>' +
        (p.dimensions ? '<span>size <b class="mono">' + esc(p.dimensions) + '</b></span>' : '') +
        '<span>stage <b class="mono">' + esc(p.pipeline_status) + '</b></span>' +
        (p.deleted ? '<span class="error">DELETED</span>' : '') +
      '</div>' +
    '</div>';

  if (!data.events.length) {
    html += '<p class="muted">No recorded events.</p>';
  } else {
    html += '<ol class="tl-list">';
    data.events.forEach((e) => {
      html +=
        '<li class="tl-item tl-' + esc(e.kind) + '">' +
          '<div class="tl-when mono">' + esc(e.at.replace('T', ' ').slice(0, 16)) + '</div>' +
          '<div class="tl-what">' +
            '<div class="tl-text">' + esc(e.text) +
              (e.actor ? ' <span class="muted">· ' + esc(e.actor) + '</span>' : '') + '</div>' +
            (e.detail ? '<div class="tl-detail mono">' + esc(e.detail) + '</div>' : '') +
          '</div>' +
        '</li>';
    });
    html += '</ol>';
  }
  body.innerHTML = html;
}
