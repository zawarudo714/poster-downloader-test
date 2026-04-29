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

  // ── Gallery image browser ────────────────────────────────────────────────
  const gallery = document.getElementById('ib-gallery');
  if (!gallery) return;

  // Threshold for sub-800 highlighting. Posters under 800px wide get a red border.
  const MIN_WIDTH = 800;

  let titles = [];
  let titleIdx = 0;
  let currentLightbox = null;
  // Multi-select state for "mark similar". Set of poster IDs.
  const selected = new Set();
  let selectMode = false;

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
    selected.clear();
    updateSimilarBtn();
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

    btn.addEventListener('click', () => {
      if (selectMode) {
        toggleSelected(p.poster_id, btn);
      } else {
        openLightbox(t, p);
      }
    });
    return node;
  }

  function toggleSelected(id, btn) {
    if (selected.has(id)) {
      selected.delete(id);
      btn.classList.remove('p-selected');
    } else {
      selected.add(id);
      btn.classList.add('p-selected');
    }
    updateSimilarBtn();
  }

  function updateSimilarBtn() {
    const btn = $('ib-mark-similar');
    if (!btn) return;
    btn.textContent = `MARK SELECTED AS SIMILAR (${selected.size})`;
    btn.disabled = (selected.size < 2);
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
  $('ib-worker').addEventListener('change', loadList);
  $('ib-date').addEventListener('change', loadList);
  $('ib-prev-title').addEventListener('click', () => navTitle(-1));
  $('ib-next-title').addEventListener('click', () => navTitle(1));

  // ── Select mode + Mark Similar ───────────────────────────────────────────
  const selectToggle = $('ib-select-mode');
  const markSimilar  = $('ib-mark-similar');
  if (selectToggle) {
    selectToggle.addEventListener('click', () => {
      selectMode = !selectMode;
      selectToggle.classList.toggle('on', selectMode);
      selectToggle.textContent = selectMode ? '✕ EXIT SELECT MODE' : '☐ SELECT MODE';
      if (!selectMode) {
        selected.clear();
        document.querySelectorAll('.g-poster.p-selected').forEach((b) => b.classList.remove('p-selected'));
        updateSimilarBtn();
      }
      gallery.classList.toggle('select-mode', selectMode);
    });
  }
  if (markSimilar) {
    markSimilar.addEventListener('click', async () => {
      if (selected.size < 2) return;
      // Verify all picks belong to one title (the API also checks).
      const picksByTitle = {};
      titles.forEach((t) => t.posters.forEach((p) => {
        if (selected.has(p.poster_id)) picksByTitle[t.master_id] = (picksByTitle[t.master_id] || 0) + 1;
      }));
      if (Object.keys(picksByTitle).length !== 1) {
        alert('All selected posters must belong to the same title.');
        return;
      }
      const note = prompt(
        `Mark these ${selected.size} posters as too similar?\n\n` +
        `Optional note for the user (e.g. "Both are alternate covers — pick one"):`
      );
      if (note === null) return;
      const fd = new FormData();
      fd.append('poster_ids', Array.from(selected).join(','));
      fd.append('comment', note || '');
      const r = await fetch('/admin/posters/mark_similar', { method: 'POST', body: fd });
      const data = await r.json().catch(() => ({}));
      if (r.ok) {
        alert('Flagged as similar. The user will be notified.');
        selected.clear();
        if (selectMode) selectToggle.click();
        loadList();
      } else {
        alert('Failed: ' + (data.detail || r.status));
      }
    });
  }

  // ── ZIP day ──────────────────────────────────────────────────────────────
  $('ib-zip').addEventListener('click', async () => {
    const worker = $('ib-worker').value;
    const date   = $('ib-date').value;
    if (!worker || !date) return;
    const fd = new FormData();
    fd.append('worker', worker);
    fd.append('date', date);
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
