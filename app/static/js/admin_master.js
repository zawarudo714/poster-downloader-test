/* Admin master sheet — paginated, filterable, bulk + per-row status, background import. */
(function () {
  if (!document.getElementById('ms-body')) return;

  let page = 1, pages = 1, total = 0, pageSize = 100;
  let currentRows = [];
  const selected = new Set();
  let lastClickedIdx = null;

  const $ = (id) => document.getElementById(id);
  const debounce = (fn, ms = 250) => {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  };

  async function load() {
    const params = new URLSearchParams({
      page, page_size: pageSize,
      q: $('ms-q').value.trim(),
      status: $('ms-status').value,
      content_type: $('ms-type').value,
      needs_revision: $('ms-flag').checked ? 1 : 0,
    });
    $('ms-body').innerHTML = '<tr><td colspan="9" class="loading">Loading…</td></tr>';
    const r = await fetch('/admin/api/master?' + params.toString());
    if (!r.ok) {
      $('ms-body').innerHTML = '<tr><td colspan="9" class="loading">Failed to load.</td></tr>';
      return;
    }
    const data = await r.json();
    page = data.page; pages = data.pages; total = data.total; pageSize = data.page_size;
    currentRows = data.items;
    render();
  }

  function render() {
    const body = $('ms-body');
    body.innerHTML = '';
    if (currentRows.length === 0) {
      body.innerHTML = '<tr><td colspan="9" class="loading">No matches.</td></tr>';
    } else {
      currentRows.forEach((row, i) => body.appendChild(rowToTr(row, i)));
    }
    $('ms-page-info').textContent = `page ${page} / ${pages || 1}`;
    $('ms-total').textContent = total + ' rows';
    updateSelectionUI();
  }

  function rowToTr(row, idx) {
    const tr = document.createElement('tr');
    tr.dataset.id = row.id;
    tr.dataset.idx = idx;
    if (row.status) tr.classList.add('status-' + row.status);
    if (row.needs_revision) tr.classList.add('flagged');
    if (selected.has(row.id)) tr.classList.add('selected');
    tr.innerHTML = `
      <td class="col-cb"><input type="checkbox" ${selected.has(row.id) ? 'checked' : ''}></td>
      <td class="col-num mono">${row.external_id ?? '–'}</td>
      <td class="col-type mono">${row.content_type || ''}</td>
      <td class="col-title"></td>
      <td class="col-year mono">${row.year}</td>
      <td class="col-status"><span class="status-pill status-${row.status}">${row.status.replace('_', ' ')}</span>${row.needs_revision ? ' <span class="status-pill status-flag">flag</span>' : ''}</td>
      <td class="col-claim mono">${row.claimed_by || ''}</td>
      <td class="mono">${row.started || ''}</td>
      <td>
        <select class="status-select" data-id="${row.id}">
          <option value="">…</option>
          <option value="pending">pending</option>
          <option value="in_progress">in_progress</option>
          <option value="complete">complete</option>
          <option value="skipped">skipped</option>
        </select>
      </td>`;
    tr.querySelector('.col-title').textContent = row.title;
    tr.addEventListener('click', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'OPTION') return;
      onRowClick(e, row, idx);
    });
    tr.querySelector('input[type="checkbox"]').addEventListener('change', (e) => {
      if (e.target.checked) selected.add(row.id); else selected.delete(row.id);
      tr.classList.toggle('selected', selected.has(row.id));
      updateSelectionUI();
    });
    tr.querySelector('.status-select').addEventListener('change', async (e) => {
      const v = e.target.value;
      if (!v) return;
      const fd = new FormData();
      fd.append('status', v);
      const resp = await fetch(`/admin/master/${row.id}/status`, { method: 'POST', body: fd });
      if (resp.ok) {
        e.target.classList.add('flash');
        setTimeout(() => e.target.classList.remove('flash'), 220);
        load();
      } else {
        alert('Failed to set status.');
        e.target.value = '';
      }
    });
    return tr;
  }

  function onRowClick(e, row, idx) {
    if (e.shiftKey && lastClickedIdx !== null) {
      const a = Math.min(lastClickedIdx, idx);
      const b = Math.max(lastClickedIdx, idx);
      for (let i = a; i <= b; i++) {
        const r = currentRows[i];
        if (r) selected.add(r.id);
      }
    } else {
      if (selected.has(row.id)) selected.delete(row.id);
      else selected.add(row.id);
      lastClickedIdx = idx;
    }
    rerenderSelection();
  }

  function rerenderSelection() {
    [...$('ms-body').querySelectorAll('tr')].forEach((tr) => {
      const id = parseInt(tr.dataset.id, 10);
      tr.classList.toggle('selected', selected.has(id));
      const cb = tr.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = selected.has(id);
    });
    updateSelectionUI();
  }

  function updateSelectionUI() {
    $('ms-selected').textContent = selected.size;
    $('ms-bulk-apply').disabled = selected.size === 0 || !$('ms-bulk-status').value;
  }

  // Drag-select
  let dragging = false, dragStart = null, dragMode = 'add';
  $('ms-body').addEventListener('mousedown', (e) => {
    const tr = e.target.closest('tr');
    if (!tr || !tr.dataset.idx) return;
    if (['INPUT', 'SELECT', 'OPTION'].includes(e.target.tagName)) return;
    dragging = true;
    dragStart = parseInt(tr.dataset.idx, 10);
    dragMode = selected.has(parseInt(tr.dataset.id, 10)) ? 'remove' : 'add';
  });
  $('ms-body').addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const tr = e.target.closest('tr');
    if (!tr || !tr.dataset.idx) return;
    const a = Math.min(dragStart, parseInt(tr.dataset.idx, 10));
    const b = Math.max(dragStart, parseInt(tr.dataset.idx, 10));
    for (let i = a; i <= b; i++) {
      const row = currentRows[i];
      if (!row) continue;
      if (dragMode === 'add') selected.add(row.id); else selected.delete(row.id);
    }
    rerenderSelection();
  });
  document.addEventListener('mouseup', () => { dragging = false; });

  $('ms-cb-all').addEventListener('change', (e) => {
    if (e.target.checked) currentRows.forEach((r) => selected.add(r.id));
    else currentRows.forEach((r) => selected.delete(r.id));
    rerenderSelection();
  });
  $('ms-clear').addEventListener('click', () => { selected.clear(); rerenderSelection(); });

  $('ms-bulk-status').addEventListener('change', updateSelectionUI);
  $('ms-bulk-apply').addEventListener('click', async () => {
    const status = $('ms-bulk-status').value;
    if (!status || selected.size === 0) return;
    if (!confirm(`Apply status "${status}" to ${selected.size} rows?`)) return;
    const fd = new FormData();
    fd.append('ids', [...selected].join(','));
    fd.append('status', status);
    const r = await fetch('/admin/master/bulk_status', { method: 'POST', body: fd });
    const data = await r.json().catch(() => ({}));
    if (r.ok) {
      $('ms-msg').textContent = `Updated ${data.updated} rows.`;
      selected.clear();
      load();
    } else {
      alert('Failed: ' + (data.detail || r.status));
    }
  });

  // Filters / paging
  ['ms-q'].forEach((id) => $(id).addEventListener('input', debounce(() => { page = 1; load(); }, 250)));
  ['ms-status', 'ms-type'].forEach((id) => $(id).addEventListener('change', () => { page = 1; load(); }));
  $('ms-flag').addEventListener('change', () => { page = 1; load(); });
  $('ms-prev').addEventListener('click', () => { if (page > 1) { page--; load(); } });
  $('ms-next').addEventListener('click', () => { if (page < pages) { page++; load(); } });
  $('ms-jump-btn').addEventListener('click', () => {
    const v = parseInt($('ms-jump').value, 10);
    if (v >= 1 && v <= pages) { page = v; load(); }
  });

  // Clear all
  $('ms-clear-all').addEventListener('click', async () => {
    if (!confirm(`Clear ALL titles from the list? This cannot be undone (anything already saved keeps its files).`)) return;
    if (!confirm('REALLY clear everything? Last chance.')) return;
    const r = await fetch('/admin/master/clear', { method: 'POST' });
    if (r.ok) load();
    else alert('Clear failed.');
  });

  // Background import
  const importForm = $('import-form');
  if (importForm) {
    importForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(importForm);
      const r = await fetch('/admin/master/upload', { method: 'POST', body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert('Import start failed: ' + (data.detail || r.status)); return; }
      $('import-progress').hidden = false;
      pollImport(data.job_id);
    });
  }

  async function pollImport(id) {
    const fill = document.querySelector('.import-bar-fill');
    const meta = document.querySelector('.import-meta');
    while (true) {
      const r = await fetch(`/admin/import/${id}`);
      if (!r.ok) { meta.textContent = 'Job lookup failed.'; return; }
      const job = await r.json();
      const total = job.total || 0;
      const pct = total ? Math.min(100, Math.round((job.done / total) * 100)) : (job.state === 'done' ? 100 : 0);
      fill.style.width = pct + '%';
      meta.textContent = `${job.state} · ${job.done}${total ? ' / ' + total : ''} rows${job.error ? ' · ' + job.error : ''}`;
      if (job.state === 'done' || job.state === 'error') {
        if (job.state === 'done') load();
        return;
      }
      await new Promise((r) => setTimeout(r, 800));
    }
  }

  // Quick paste-append form (small batches only)
  const pasteForm = $('paste-master-form') || document.querySelector('#paste-master-form');
  if (pasteForm) {
    pasteForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      // Reuse the upload endpoint by writing the textarea into a synthetic CSV file.
      const text = pasteForm.querySelector('textarea').value || '';
      if (!text.trim()) return;
      const csv = 'num\ttitle\tyear\n' + text;
      const blob = new Blob([csv], { type: 'text/csv' });
      const fd = new FormData();
      fd.append('file', blob, 'paste.tsv');
      const r = await fetch('/admin/master/upload', { method: 'POST', body: fd });
      if (r.ok) { pasteForm.querySelector('textarea').value = ''; load(); }
      else alert('Append failed.');
    });
  }

  load();
})();
