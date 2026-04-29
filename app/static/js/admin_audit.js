/* Audit log — paginated, filterable. */
(function () {
  if (!document.getElementById('al-body')) return;

  let page = 1, pages = 1, total = 0;
  const $ = (id) => document.getElementById(id);

  async function load() {
    const params = new URLSearchParams({
      page, page_size: 100,
      user: $('al-user').value.trim(),
      action: $('al-action').value,
    });
    $('al-body').innerHTML = '<tr><td colspan="5" class="loading">Loading…</td></tr>';
    const r = await fetch('/admin/api/audit?' + params.toString());
    if (!r.ok) {
      $('al-body').innerHTML = '<tr><td colspan="5" class="loading">Failed to load.</td></tr>';
      return;
    }
    const data = await r.json();
    page = data.page; pages = data.pages; total = data.total;
    render(data.items);
  }

  function render(items) {
    const body = $('al-body');
    body.innerHTML = '';
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="5" class="loading">No matching activity.</td></tr>';
    } else {
      items.forEach((row) => {
        const tr = document.createElement('tr');
        const target = row.target_type ? `${row.target_type}#${row.target_id ?? '–'}` : '';
        const details = row.details ? JSON.stringify(row.details) : '';
        tr.innerHTML = `
          <td class="mono">${row.created_at}</td>
          <td class="mono">${row.username || '(system)'}</td>
          <td class="mono">${row.action}</td>
          <td class="mono muted">${target}</td>
          <td class="mono"></td>`;
        // Set details safely (no innerHTML for user data)
        tr.lastElementChild.textContent = details.length > 200 ? details.slice(0, 200) + '…' : details;
        body.appendChild(tr);
      });
    }
    $('al-page-info').textContent = `page ${page} / ${pages || 1}`;
    $('al-total').textContent = total + ' rows';
  }

  $('al-apply').addEventListener('click', () => { page = 1; load(); });
  $('al-prev').addEventListener('click', () => { if (page > 1) { page--; load(); } });
  $('al-next').addEventListener('click', () => { if (page < pages) { page++; load(); } });
  $('al-user').addEventListener('keydown', (e) => { if (e.key === 'Enter') { page = 1; load(); } });

  load();
})();
