/* Admin shared JS — gallery review (with quality badges + similar-select),
   fs tree, release-user buttons, lightbox flag/unflag, ZIP day. */

(function () {

  // ── Filesystem tree (admin dashboard) ────────────────────────────────────
  const fsTree = document.getElementById('fs-tree');
  if (fsTree) {
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

  // ── Release-user-queue buttons ───────────────────────────────────────────
  document.querySelectorAll('[data-release-user-id]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.getAttribute('data-release-user-id');
      const keepStarted = btn.getAttribute('data-keep-started') || '1';
      const label = keepStarted === '1' ? 'unworked' : 'ALL';
      if (!confirm(`Return ${label} titles for this user back to the pool?`)) return;
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
  const MIN_WIDTH = 800;

  let titles = [];
  let titleIdx = 0;
  let currentLightbox = null;
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
    titles = data.titles || [];
    titleIdx = 0;
    clearSelection();
    $('ib-summary').textContent = `${data.title_count} title(s) · ${data.poster_count} poster(s) total`;
    renderGallery();
  }

  function renderGallery() {
    gallery.innerHTML = '';
    if (titles.length === 0) {
      gallery.innerHTML = '<div class="empty-hint">No saved posters for this user / date.</div>';
      $('ib-title-counter').textContent = '— / —';
      return;
    }
    titles.forEach((t, i) => {
      const node = tplTitle.content.cloneNode(true);
      const section = node.querySelector('.g-title');
      section.id = `g-title-${i}`;
      if (i === titleIdx) section.classList.add('current');
      if (t.needs_revision) section.classList.add('flagged');
      node.querySelector('.g-title-name').textContent = `${t.title} (${t.year})`;
      node.querySelector('.g-title-meta').textContent =
        `${t.posters.length} poster${t.posters.length === 1 ? '' : 's'} · ${t.title_folder}`;
      const grid = node.querySelector('.g-title-posters');
      t.posters.forEach((p) => {
        const btn = buildPosterCell(t, p);
        grid.appendChild(btn);
      });
      gallery.appendChild(node);
    });
    $('ib-title-counter').textContent = `${titleIdx + 1} / ${titles.length}`;
    const cur = document.getElementById(`g-title-${titleIdx}`);
    if (cur) cur.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function buildPosterCell(t, p) {
    const node = tplPoster.content.cloneNode(true);
    const btn = node.querySelector('.g-poster');
    btn.dataset.posterId = p.poster_id;
    btn.querySelector('.g-poster-img').src = fileUrl(p.poster_id, p.size || p.filename);
    btn.querySelector('.g-poster-img').alt = p.filename;
    btn.querySelector('.g-poster-name').textContent = p.filename;

    // Quality classes/badges.
    const isSub800 = (p.image_width != null && p.image_width < MIN_WIDTH);
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

    btn.addEventListener('click', (e) => {
      // Only count as a checkbox click if the click target IS the checkbox.
      // Everything else opens the lightbox.
      if (e.target === check) return;
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
          `You already have ${selected.size} poster${selected.size === 1 ? '' : 's'} selected from "${selectionTitleName}". ` +
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
    // Re-render with the new current highlighted (no full reload — preserves selection).
    document.querySelectorAll('.g-title.current').forEach((el) => el.classList.remove('current'));
    const cur = document.getElementById(`g-title-${titleIdx}`);
    if (cur) {
      cur.classList.add('current');
      cur.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    $('ib-title-counter').textContent = `${titleIdx + 1} / ${titles.length}`;
  }

  // ── Lightbox ─────────────────────────────────────────────────────────────
  const lightbox = $('ib-lightbox');
  const lbImg    = $('ib-lb-img');
  const lbMeta   = $('ib-lb-meta');
  const lbFlag   = $('ib-lb-flag');
  const lbComment    = $('ib-lb-comment');
  const lbFlagBtn    = $('ib-lb-flag-btn');
  const lbUnflagBtn  = $('ib-lb-unflag-btn');

  function openLightbox(t, p) {
    currentLightbox = { master: t, poster: p };
    lbImg.src = fileUrl(p.poster_id, p.size || p.filename);
    lbImg.alt = p.filename;
    const dims = (p.image_width && p.image_height) ? ` · ${p.image_width}×${p.image_height}` : '';
    const lq   = p.low_quality_url ? ' · ⚠ LQ-URL bypassed' : '';
    lbMeta.textContent = `${t.title} (${t.year}) — ${p.filename}${dims}${lq}`;
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
  }

  function closeLightbox() {
    lightbox.hidden = true;
    currentLightbox = null;
  }

  document.querySelectorAll('[data-lightbox-close]').forEach((el) => {
    el.addEventListener('click', closeLightbox);
  });

  lbFlagBtn.addEventListener('click', async () => {
    if (!currentLightbox) return;
    const { poster } = currentLightbox;
    const fd = new FormData();
    fd.append('comment', lbComment.value || '');
    const r = await fetch(`/admin/poster/${poster.poster_id}/flag`, { method: 'POST', body: fd });
    if (r.ok) { closeLightbox(); loadList(); }
    else { alert('Flag failed.'); }
  });

  lbUnflagBtn.addEventListener('click', async () => {
    if (!currentLightbox) return;
    const { poster } = currentLightbox;
    const r = await fetch(`/admin/poster/${poster.poster_id}/unflag`, { method: 'POST' });
    if (r.ok) { closeLightbox(); loadList(); }
    else { alert('Unflag failed.'); }
  });

  // Keyboard nav
  document.addEventListener('keydown', (e) => {
    const ae = document.activeElement;
    if (ae && ['INPUT', 'SELECT', 'TEXTAREA'].includes(ae.tagName)) return;
    if (!lightbox.hidden) {
      if (e.key === 'Escape') closeLightbox();
      return;
    }
    if (e.key === 'ArrowLeft')  navTitle(-1);
    if (e.key === 'ArrowRight') navTitle(1);
  });

  $('ib-load').addEventListener('click', loadList);
  $('ib-worker').addEventListener('change', () => {
    // When worker changes, reload the page so the server returns fresh dates.
    const url = new URL(window.location.href);
    url.searchParams.set('worker', $('ib-worker').value);
    url.searchParams.delete('date');
    window.location.href = url.toString();
  });
  $('ib-prev-title').addEventListener('click', () => navTitle(-1));
  $('ib-next-title').addEventListener('click', () => navTitle(1));

  // ── Date navigation (prev/next + grid modal) ───────────────────────────
  const dates = window.__dates || [];
  const dateInput = $('ib-date');
  const dateLabel = $('ib-date-label');

  function setDate(d) {
    dateInput.value = d;
    dateLabel.textContent = d;
    loadList();
  }

  $('ib-date-prev').addEventListener('click', () => {
    const idx = dates.indexOf(dateInput.value);
    // dates is newest-first, so "prev" = next index (older)
    if (idx >= 0 && idx < dates.length - 1) setDate(dates[idx + 1]);
  });
  $('ib-date-next').addEventListener('click', () => {
    const idx = dates.indexOf(dateInput.value);
    // "next" = previous index (newer)
    if (idx > 0) setDate(dates[idx - 1]);
  });

  // Grid modal
  const gridModal = $('date-grid-modal');
  const gridEl    = $('date-grid');
  const gridZip   = $('date-grid-zip');
  let gridChecked = new Set();

  function openDateGrid() {
    gridChecked.clear();
    gridEl.innerHTML = '';
    dates.forEach((d) => {
      const chip = document.createElement('label');
      chip.className = 'date-chip';
      if (d === dateInput.value) chip.classList.add('date-chip-active');
      chip.innerHTML = `<input type="checkbox" value="${d}"><span class="date-chip-label">${d}</span>`;
      const cb = chip.querySelector('input');
      cb.addEventListener('change', () => {
        if (cb.checked) gridChecked.add(d); else gridChecked.delete(d);
        updateGridZipBtn();
      });
      chip.querySelector('.date-chip-label').addEventListener('click', (e) => {
        // Clicking the label text (not checkbox) = navigate to that date
        if (e.target === chip.querySelector('.date-chip-label')) {
          setDate(d);
          gridModal.hidden = true;
        }
      });
      gridEl.appendChild(chip);
    });
    updateGridZipBtn();
    gridModal.hidden = false;
  }

  function updateGridZipBtn() {
    const n = gridChecked.size;
    gridZip.textContent = `ZIP SELECTED (${n})`;
    gridZip.disabled = n === 0;
  }

  $('ib-date-grid-btn').addEventListener('click', openDateGrid);
  gridModal.querySelectorAll('[data-date-grid-close]').forEach((el) =>
    el.addEventListener('click', () => { gridModal.hidden = true; })
  );
  // Esc closes date grid
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !gridModal.hidden) { gridModal.hidden = true; e.stopPropagation(); }
  });

  // Multi-day zip from grid
  gridZip.addEventListener('click', async () => {
    if (gridChecked.size === 0) return;
    const worker = $('ib-worker').value;
    if (!worker) return;
    const fd = new FormData();
    fd.append('worker', worker);
    fd.append('dates', Array.from(gridChecked).sort().join(','));
    gridZip.disabled = true;
    gridZip.textContent = 'STARTING…';
    const r = await fetch('/admin/zip/start', { method: 'POST', body: fd });
    const data = await r.json().catch(() => ({}));
    gridZip.disabled = false;
    updateGridZipBtn();
    if (!r.ok) { alert('Zip start failed.'); return; }
    gridModal.hidden = true;
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
