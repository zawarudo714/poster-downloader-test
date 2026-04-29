/* Master browse — read-only paginated table with multi-select and "add to my queue". */
(function () {
  if (!document.getElementById('mb-table')) return;

  let page = 1;
  let pages = 1;
  let pageSize = 100;
  let total = 0;
  const selected = new Set();   // master_title_ids selected (across pages, persistent)
  let lastClickedIndex = null;  // for shift-click ranges (within current page)
  let currentRows = [];          // items shown on current page

  const $ = (id) => document.getElementById(id);
  const debounce = (fn, ms = 250) => {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  };

  async function load() {
    const params = new URLSearchParams({
      page, page_size: pageSize,
      q: $('mb-q').value.trim(),
      status: $('mb-status').value,
      content_type: $('mb-type').value,
      only_unclaimed: $('mb-only-unclaimed').checked ? 1 : 0,
    });
    $('mb-body').innerHTML = '<tr><td colspan="7" class="loading">Loading…</td></tr>';
    const r = await fetch('/api/master?' + params.toString());
    if (!r.ok) {
      $('mb-body').innerHTML = '<tr><td colspan="7" class="loading">Failed to load.</td></tr>';
      return;
    }
    const data = await r.json();
    page = data.page; pages = data.pages; total = data.total; pageSize = data.page_size;
    currentRows = data.items;
    render();
  }

  function render() {
    const body = $('mb-body');
    body.innerHTML = '';
    if (currentRows.length === 0) {
      body.innerHTML = '<tr><td colspan="7" class="loading">No matches.</td></tr>';
    } else {
      currentRows.forEach((row, i) => body.appendChild(rowToTr(row, i)));
    }
    $('mb-page-info').textContent = `page ${page} / ${pages || 1}`;
    $('mb-total').textContent = total + ' rows';
    updateSelectionUI();
  }

  function rowToTr(row, idx) {
    const tr = document.createElement('tr');
    tr.dataset.id = row.id;
    tr.dataset.idx = idx;
    const claimedByOther = row.claimed_by && !row.mine;
    const selectable = (row.status === 'pending' && !claimedByOther);
    if (claimedByOther) tr.classList.add('row-claimed-other');
    if (row.status) tr.classList.add('status-' + row.status);
    if (row.needs_revision) tr.classList.add('flagged');
    if (selected.has(row.id)) tr.classList.add('selected');

    tr.innerHTML = `
      <td class="col-cb"><input type="checkbox" ${selectable ? '' : 'disabled'} ${selected.has(row.id) ? 'checked' : ''}></td>
      <td class="col-num mono">${row.external_id ?? '–'}</td>
      <td class="col-type mono">${row.content_type || ''}</td>
      <td class="col-title"></td>
      <td class="col-year mono">${row.year}</td>
      <td class="col-status"><span class="status-pill status-${row.status}">${row.status.replace('_', ' ')}</span>${row.needs_revision ? ' <span class="status-pill status-flag">flag</span>' : ''}</td>
      <td class="col-claim mono">${row.claimed_by || ''}${row.mine ? ' (you)' : ''}</td>
    `;
    tr.querySelector('.col-title').textContent = row.title;
    if (selectable) {
      tr.addEventListener('click', (e) => onRowClick(e, row, idx, tr));
    }
    return tr;
  }

  function onRowClick(e, row, idx, tr) {
    // Don't intercept the checkbox itself's native toggle — handle uniformly:
    if (e.shiftKey && lastClickedIndex !== null) {
      const start = Math.min(lastClickedIndex, idx);
      const end   = Math.max(lastClickedIndex, idx);
      for (let i = start; i <= end; i++) {
        const r = currentRows[i];
        if (r && r.status === 'pending' && !(r.claimed_by && !r.mine)) selected.add(r.id);
      }
    } else {
      if (selected.has(row.id)) selected.delete(row.id);
      else selected.add(row.id);
      lastClickedIndex = idx;
    }
    rerenderSelection();
  }

  function rerenderSelection() {
    [...$('mb-body').querySelectorAll('tr')].forEach((tr) => {
      const id = parseInt(tr.dataset.id, 10);
      tr.classList.toggle('selected', selected.has(id));
      const cb = tr.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = selected.has(id);
    });
    updateSelectionUI();
  }

  function updateSelectionUI() {
    $('mb-selected').textContent = selected.size;
    $('mb-add').disabled = selected.size === 0;
  }

  // Drag-select (lasso) with mousedown/mousemove
  let dragging = false, dragStartIdx = null, dragMode = 'add';
  $('mb-body').addEventListener('mousedown', (e) => {
    const tr = e.target.closest('tr');
    if (!tr || !tr.dataset.idx) return;
    if (e.target.tagName === 'INPUT') return;  // let checkbox click happen normally
    dragging = true;
    dragStartIdx = parseInt(tr.dataset.idx, 10);
    dragMode = selected.has(parseInt(tr.dataset.id, 10)) ? 'remove' : 'add';
  });
  $('mb-body').addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const tr = e.target.closest('tr');
    if (!tr || !tr.dataset.idx) return;
    const a = Math.min(dragStartIdx, parseInt(tr.dataset.idx, 10));
    const b = Math.max(dragStartIdx, parseInt(tr.dataset.idx, 10));
    for (let i = a; i <= b; i++) {
      const row = currentRows[i];
      if (!row) continue;
      const selectable = row.status === 'pending' && !(row.claimed_by && !row.mine);
      if (!selectable) continue;
      if (dragMode === 'add') selected.add(row.id);
      else selected.delete(row.id);
    }
    rerenderSelection();
  });
  document.addEventListener('mouseup', () => { dragging = false; });

  // Bulk checkbox
  $('mb-cb-all').addEventListener('change', (e) => {
    const want = e.target.checked;
    currentRows.forEach((r) => {
      const selectable = r.status === 'pending' && !(r.claimed_by && !r.mine);
      if (!selectable) return;
      if (want) selected.add(r.id);
      else selected.delete(r.id);
    });
    rerenderSelection();
  });

  $('mb-clear').addEventListener('click', () => { selected.clear(); rerenderSelection(); });

  $('mb-add').addEventListener('click', async () => {
    if (selected.size === 0) return;
    const ids = [...selected].join(',');
    const fd = new FormData();
    fd.append('ids', ids);
    const r = await fetch('/select_titles', { method: 'POST', body: fd });
    const data = await r.json().catch(() => null);
    if (r.ok) {
      $('mb-msg').textContent = `Added ${data.claimed} titles to your list${data.skipped ? ' (' + data.skipped + ' skipped — already claimed)' : ''}.`;
      selected.clear();
      load();
    } else {
      $('mb-msg').textContent = 'Failed: ' + (data && data.detail || r.status);
    }
  });

  // Filters & paging
  ['mb-q'].forEach((id) => $(id).addEventListener('input', debounce(() => { page = 1; load(); }, 250)));
  ['mb-status', 'mb-type'].forEach((id) => $(id).addEventListener('change', () => { page = 1; load(); }));
  $('mb-only-unclaimed').addEventListener('change', () => { page = 1; load(); });
  $('mb-prev').addEventListener('click', () => { if (page > 1) { page--; load(); } });
  $('mb-next').addEventListener('click', () => { if (page < pages) { page++; load(); } });
  $('mb-jump-btn').addEventListener('click', () => {
    const v = parseInt($('mb-jump').value, 10);
    if (v >= 1 && v <= pages) { page = v; load(); }
  });

  load();
})();
