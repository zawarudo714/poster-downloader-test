/* Worker save-history page.
   - Loads /api/history/days for the per-day summary.
   - Each row expands on click to show titles for that day.
   - Search box filters the list as you type. To make matching usefully
     shallow we proactively prefetch each day's titles when search is
     active (we want to know which DAYS contain a matching TITLE), but
     this is throttled by the cache so a single search pass is one fetch
     per day, max. */

(function () {
  const listEl    = document.getElementById('history-list');
  const rateEl    = document.getElementById('history-rate');
  const searchInp = document.getElementById('history-search');
  const clearBtn  = document.getElementById('history-search-clear');
  const hintEl    = document.getElementById('history-search-hint');
  if (!listEl) return;

  // Cache per-day title fetches keyed by date.
  const dayCache = {};
  // Cache built day-row DOM elements so search re-renders are cheap.
  let allDays = [];   // server-returned day summaries
  let dayElements = new Map();   // date → wrapper element

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
    allDays = data.days;
    listEl.innerHTML = '';
    dayElements.clear();
    data.days.forEach((d) => {
      const el = buildDayRow(d);
      dayElements.set(d.date, el);
      listEl.appendChild(el);
    });
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

  async function toggleDay(wrap, body, dateStr, opts = {}) {
    const force = opts.force; // 'open' | 'close' | undefined
    const arrow = wrap.querySelector('.history-day-arrow');
    const wantOpen = force === 'open' ? true
                   : force === 'close' ? false
                   : body.hidden;
    if (!wantOpen) {
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

  function renderDayBody(body, data, highlight) {
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
    const rows = body.querySelectorAll('.history-title-row');
    rows.forEach((row, i) => {
      row.querySelector('.history-title-name').textContent = data.titles[i].title;
      row.querySelector('.history-title-year').textContent = '(' + data.titles[i].year + ')';
      // Highlight matching titles in search mode.
      if (highlight && data.titles[i].title.toLowerCase().includes(highlight)) {
        row.classList.add('history-match');
      }
    });
  }

  // ── Search ───────────────────────────────────────────────────────────────
  let searchToken = 0;
  async function applySearch() {
    const q = (searchInp.value || '').trim().toLowerCase();
    clearBtn.hidden = !q;
    if (!q) {
      // Reset everything to its default collapsed/clean state.
      hintEl.textContent = '';
      for (const [date, el] of dayElements) {
        el.classList.remove('history-day-dimmed');
        const body = el.querySelector('.history-day-body');
        const arrow = el.querySelector('.history-day-arrow');
        body.hidden = true;
        arrow.textContent = '▸';
      }
      return;
    }
    const myToken = ++searchToken;
    hintEl.textContent = 'Searching…';

    // Fetch any uncached day so we can scan its titles. Sequential to keep
    // the server happy. Bail if user typed more during the loop.
    let matchedDays = 0;
    let totalMatches = 0;
    for (const day of allDays) {
      if (myToken !== searchToken) return; // user kept typing
      if (!dayCache[day.date]) {
        try {
          const r = await fetch(`/api/history/day/${day.date}`, { cache: 'no-store' });
          if (!r.ok) continue;
          dayCache[day.date] = await r.json();
        } catch { continue; }
      }
      const titles = dayCache[day.date].titles || [];
      const dayMatches = titles.filter(t => t.title.toLowerCase().includes(q));
      const el = dayElements.get(day.date);
      if (!el) continue;
      const body = el.querySelector('.history-day-body');
      const arrow = el.querySelector('.history-day-arrow');
      if (dayMatches.length > 0) {
        el.classList.remove('history-day-dimmed');
        body.hidden = false;
        arrow.textContent = '▾';
        renderDayBody(body, dayCache[day.date], q);
        matchedDays += 1;
        totalMatches += dayMatches.length;
      } else {
        el.classList.add('history-day-dimmed');
        body.hidden = true;
        arrow.textContent = '▸';
      }
    }
    hintEl.textContent = `${totalMatches} title${totalMatches === 1 ? '' : 's'} across ${matchedDays} day${matchedDays === 1 ? '' : 's'}`;
  }

  let typingTimer = null;
  if (searchInp) {
    searchInp.addEventListener('input', () => {
      clearTimeout(typingTimer);
      typingTimer = setTimeout(applySearch, 220);
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      searchInp.value = '';
      applySearch();
      searchInp.focus();
    });
  }

  load();
})();
