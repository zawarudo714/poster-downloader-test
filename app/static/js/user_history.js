/* Worker save-history page.
   - Loads /api/history/days for the per-day summary.
   - Each row expands on click to show titles for that day (lazy-loaded
     via /api/history/day/{date}). */

(function () {
  const listEl = document.getElementById('history-list');
  const rateEl = document.getElementById('history-rate');
  if (!listEl) return;

  // Cache per-day title fetches so re-collapsing then re-expanding is instant.
  const dayCache = {};

  async function load() {
    const r = await fetch('/api/history/days', { cache: 'no-store' });
    if (!r.ok) {
      listEl.innerHTML = '<div class="empty-hint">Failed to load history.</div>';
      return;
    }
    const data = await r.json();
    rateEl.textContent = data.rate_kes || '?';
    if (!data.days || data.days.length === 0) {
      listEl.innerHTML = '<div class="empty-hint">No saved posters yet — your history will show up here once you start saving.</div>';
      return;
    }
    listEl.innerHTML = '';
    data.days.forEach((d) => listEl.appendChild(buildDayRow(d)));
  }

  function buildDayRow(d) {
    const wrap = document.createElement('div');
    wrap.className = 'history-day';
    wrap.dataset.date = d.date;
    const total = (d.paid || 0) + (d.eligible || 0) + (d.pending || 0);
    wrap.innerHTML = `
      <div class="history-day-head">
        <span class="history-day-arrow mono">▸</span>
        <span class="history-day-date mono"></span>
        <span class="history-day-counts">
          <span class="history-bucket paid"     data-tooltip="Already in a past payment">${d.paid || 0} paid</span>
          <span class="history-bucket eligible" data-tooltip="Counts toward your next payment">${d.eligible || 0} eligible</span>
          ${d.pending ? `<span class="history-bucket pending"  data-tooltip="Waiting on a revision fix">${d.pending} pending</span>` : ''}
          <span class="history-bucket total mono">total ${total}</span>
        </span>
        <span class="history-day-amount mono"></span>
      </div>
      <div class="history-day-body" hidden>
        <div class="empty-hint">Loading…</div>
      </div>
    `;
    wrap.querySelector('.history-day-date').textContent = d.date;
    const eligibleAmount = d.eligible_amount_kes || '0';
    const paidAmount     = d.paid_amount_kes     || '0';
    wrap.querySelector('.history-day-amount').innerHTML = `
      <span class="history-amount-eligible">${eligibleAmount} KES</span>
      ${parseFloat(paidAmount) > 0 ? `<span class="history-amount-paid muted">+ ${paidAmount} paid</span>` : ''}
    `;

    const head = wrap.querySelector('.history-day-head');
    const body = wrap.querySelector('.history-day-body');
    head.addEventListener('click', () => toggleDay(wrap, body, d.date));
    return wrap;
  }

  async function toggleDay(wrap, body, dateStr) {
    const arrow = wrap.querySelector('.history-day-arrow');
    const isOpen = !body.hidden;
    if (isOpen) {
      body.hidden = true;
      arrow.textContent = '▸';
      return;
    }
    body.hidden = false;
    arrow.textContent = '▾';
    if (dayCache[dateStr]) {
      renderDayBody(body, dayCache[dateStr]);
      return;
    }
    const r = await fetch(`/api/history/day/${dateStr}`, { cache: 'no-store' });
    if (!r.ok) {
      body.innerHTML = '<div class="empty-hint">Failed to load that day.</div>';
      return;
    }
    const data = await r.json();
    dayCache[dateStr] = data;
    renderDayBody(body, data);
  }

  function renderDayBody(body, data) {
    if (!data.titles || data.titles.length === 0) {
      body.innerHTML = '<div class="empty-hint">(no titles)</div>';
      return;
    }
    let html = '<div class="history-titles">';
    data.titles.forEach((t) => {
      const totalCells = [];
      if (t.paid)     totalCells.push(`<span class="history-bucket-mini paid">${t.paid} paid</span>`);
      if (t.eligible) totalCells.push(`<span class="history-bucket-mini eligible">${t.eligible} eligible</span>`);
      if (t.pending)  totalCells.push(`<span class="history-bucket-mini pending">${t.pending} pending</span>`);
      html += `
        <div class="history-title-row">
          <span class="history-title-name"></span>
          <span class="history-title-year mono"></span>
          <span class="history-title-buckets">${totalCells.join('')}</span>
          <span class="history-title-total mono">${t.total}</span>
        </div>`;
    });
    html += '</div>';
    body.innerHTML = html;
    // Set text content safely (avoid HTML-injection from titles)
    const rows = body.querySelectorAll('.history-title-row');
    rows.forEach((row, i) => {
      row.querySelector('.history-title-name').textContent = data.titles[i].title;
      row.querySelector('.history-title-year').textContent = '(' + data.titles[i].year + ')';
    });
  }

  load();
})();
