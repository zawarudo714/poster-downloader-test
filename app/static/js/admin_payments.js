/* Admin Payments page.
   - Two date pickers + presets drive a live preview per worker (count × rate).
   - "PER-DAY ▾" expands a row to show the daily breakdown for that worker.
   - "MARK PAID" opens a dialog → POST /admin/payments/mark_paid.
   - History rows have PUSH (send receipt) and DELETE (undo run). */

(function () {
  const startInp = document.getElementById('pay-start');
  const endInp   = document.getElementById('pay-end');
  if (!startInp) return;  // not on this page

  const weekInfo  = document.getElementById('pay-week-info');
  const weekStartDay = parseInt(weekInfo.getAttribute('data-week-start') || '0', 10);
  const rateKes      = weekInfo.getAttribute('data-rate-kes') || '0';

  const $ = (id) => document.getElementById(id);

  // ── Date helpers ────────────────────────────────────────────────────────
  function parseISO(s) {
    const [y, m, d] = s.split('-').map(Number);
    return new Date(Date.UTC(y, m - 1, d));
  }
  function fmtISO(d) {
    return d.toISOString().slice(0, 10);
  }
  function addDays(d, n) {
    const r = new Date(d.getTime());
    r.setUTCDate(r.getUTCDate() + n);
    return r;
  }
  // 0=Sun..6=Sat in JS, but we use 0=Mon..6=Sun in our convention.
  function jsToOurWeekday(js) { return (js + 6) % 7; }

  function weekBoundsContaining(d, weekStart) {
    const wd = jsToOurWeekday(d.getUTCDay());
    const delta = (wd - weekStart + 7) % 7;
    const start = addDays(d, -delta);
    const end   = addDays(start, 6);
    return [start, end];
  }

  function todayUTC() {
    const n = new Date();
    return new Date(Date.UTC(n.getFullYear(), n.getMonth(), n.getDate()));
  }

  // ── Presets ─────────────────────────────────────────────────────────────
  $('pay-preset-today').addEventListener('click', () => {
    const t = fmtISO(todayUTC());
    startInp.value = t; endInp.value = t;
    refreshAll();
  });
  $('pay-preset-week').addEventListener('click', () => {
    const [s, e] = weekBoundsContaining(todayUTC(), weekStartDay);
    startInp.value = fmtISO(s); endInp.value = fmtISO(e);
    refreshAll();
  });
  $('pay-preset-prevweek').addEventListener('click', () => {
    const [thisStart] = weekBoundsContaining(todayUTC(), weekStartDay);
    const prevStart = addDays(thisStart, -7);
    const prevEnd   = addDays(prevStart, 6);
    startInp.value = fmtISO(prevStart); endInp.value = fmtISO(prevEnd);
    refreshAll();
  });

  startInp.addEventListener('change', refreshAll);
  endInp.addEventListener('change', refreshAll);

  // ── Per-row preview fetch ───────────────────────────────────────────────
  async function refreshRow(tr) {
    const wid = tr.getAttribute('data-worker-id');
    const start = startInp.value;
    const end   = endInp.value;
    const countCell = tr.querySelector('.pay-cell-count');
    const totalCell = tr.querySelector('.pay-cell-total');
    countCell.textContent = '…';
    totalCell.textContent = '…';
    const params = new URLSearchParams({ worker_id: wid, start, end });
    let data;
    try {
      const r = await fetch('/admin/api/payments/preview?' + params.toString());
      data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.detail || r.status);
    } catch (e) {
      countCell.textContent = '!';
      totalCell.textContent = 'error';
      return;
    }
    countCell.textContent = data.poster_count;
    totalCell.textContent = data.computed_total_kes;
    // Stash on the row for the per-day expander.
    tr._byDay = data.by_day || {};
    tr._rate = data.rate_kes || rateKes;
    tr._previewIds = data.poster_ids || [];
    // Auto-expand the per-day breakdown when the range spans more than one
    // day — the single-day case is already covered by the row total, but
    // multi-day ranges benefit from seeing how each day contributes.
    const detailsRow = document.querySelector(`.pay-details-row[data-details-for="${wid}"]`);
    const detailsBtn = tr.querySelector('button[data-action="details"]');
    if (detailsRow) {
      const days = Math.round((parseISO(endInp.value).getTime() - parseISO(startInp.value).getTime()) / (24*3600*1000)) + 1;
      const shouldShow = days > 1 && data.poster_count > 0;
      detailsRow.hidden = !shouldShow;
      if (detailsBtn) detailsBtn.textContent = shouldShow ? 'PER-DAY ▴' : 'PER-DAY ▾';
      if (shouldShow) renderDetails(detailsRow, tr);
    }
  }

  function refreshAll() {
    document.querySelectorAll('tr[data-worker-id]').forEach(refreshRow);
    // Update the help line ("week is Mon→Sun, total range = …")
    const days = (Math.round((parseISO(endInp.value).getTime() - parseISO(startInp.value).getTime()) / (24 * 3600 * 1000)) + 1);
    weekInfo.textContent = `${days} day${days === 1 ? '' : 's'} · rate ${rateKes} KES`;
  }

  // ── Per-day breakdown (auto-shown for multi-day ranges) ─────────────────
  function renderDetails(detailsRow, sourceRow) {
    const cell = detailsRow.querySelector('.pay-details-cell');
    const byDay = sourceRow._byDay || {};
    const rate  = parseFloat(sourceRow._rate || rateKes) || 0;
    const dates = Object.keys(byDay).sort();
    if (!dates.length) {
      cell.innerHTML = '<span class="muted">No eligible posters in this range.</span>';
      return;
    }
    // Day-by-day breakdown: date · count × rate = amount
    let runningTotal = 0;
    let html = '<div class="pay-day-list">';
    html += '<div class="pay-day-list-head"><span>DATE</span><span>POSTERS</span><span>×&nbsp;RATE</span><span>SUBTOTAL</span></div>';
    dates.forEach((d) => {
      const c = byDay[d];
      const sub = (c * rate);
      runningTotal += sub;
      html += `
        <div class="pay-day-list-row">
          <span class="mono">${d}</span>
          <span class="mono pay-day-count">${c}</span>
          <span class="mono muted">× ${rate}</span>
          <span class="mono pay-day-amount">${formatAmount(sub)} KES</span>
        </div>`;
    });
    html += `
      <div class="pay-day-list-row pay-day-list-total">
        <span class="muted" colspan>TOTAL</span>
        <span class="mono"></span>
        <span class="muted"></span>
        <span class="mono">${formatAmount(runningTotal)} KES</span>
      </div>`;
    html += '</div>';
    cell.innerHTML = html;
  }

  function formatAmount(n) {
    if (Number.isInteger(n)) return String(n);
    return n.toFixed(2).replace(/\.?0+$/, '');
  }

  document.querySelectorAll('button[data-action="details"]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tr = btn.closest('tr[data-worker-id]');
      const wid = tr.getAttribute('data-worker-id');
      const detailsRow = document.querySelector(`.pay-details-row[data-details-for="${wid}"]`);
      if (!detailsRow) return;
      detailsRow.hidden = !detailsRow.hidden;
      btn.textContent = detailsRow.hidden ? 'PER-DAY ▾' : 'PER-DAY ▴';
      if (!detailsRow.hidden) renderDetails(detailsRow, tr);
    });
  });

  // ── Mark-paid dialog ────────────────────────────────────────────────────
  const dialog = $('pay-dialog');
  const dialogSummary = $('pay-dialog-summary');
  const dialogAmount  = $('pay-dialog-amount');
  const dialogRef     = $('pay-dialog-ref');
  const dialogNote    = $('pay-dialog-note');
  const dialogPush    = $('pay-dialog-push');
  const dialogConfirm = $('pay-dialog-confirm');

  function closeDialog() { dialog.hidden = true; }
  document.querySelectorAll('[data-lightbox-close]').forEach((el) => {
    el.addEventListener('click', closeDialog);
  });

  let activeRow = null;
  document.querySelectorAll('button[data-action="pay"]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tr = btn.closest('tr[data-worker-id]');
      activeRow = tr;
      const name = tr.getAttribute('data-worker-name');
      const cnt  = tr.querySelector('.pay-cell-count').textContent;
      const total = tr.querySelector('.pay-cell-total').textContent;
      dialogSummary.textContent =
        `Pay ${name} for ${startInp.value} → ${endInp.value} ` +
        `(${cnt} eligible posters, computed ${total} KES).`;
      dialogAmount.value = total === '…' || total === 'error' ? '' : total;
      dialogRef.value  = '';
      dialogNote.value = '';
      dialogPush.checked = true;
      dialog.hidden = false;
      dialogAmount.focus();
    });
  });

  dialogConfirm.addEventListener('click', async () => {
    if (!activeRow) return;
    const wid = activeRow.getAttribute('data-worker-id');
    const fd = new FormData();
    fd.append('worker_id', wid);
    fd.append('start', startInp.value);
    fd.append('end',   endInp.value);
    fd.append('amount_kes', dialogAmount.value);
    fd.append('reference',  dialogRef.value);
    fd.append('note',       dialogNote.value);
    fd.append('push_to_worker', dialogPush.checked ? '1' : '0');
    dialogConfirm.disabled = true;
    dialogConfirm.textContent = 'SAVING…';
    const r = await fetch('/admin/payments/mark_paid', { method: 'POST', body: fd });
    const data = await r.json().catch(() => ({}));
    dialogConfirm.disabled = false;
    dialogConfirm.textContent = 'CONFIRM PAYMENT';
    if (r.ok) {
      closeDialog();
      alert(`Payment recorded — ${data.poster_count} posters marked as paid.`);
      location.reload();
    } else {
      alert('Failed: ' + (data.detail || r.status));
    }
  });

  // ── History row actions ─────────────────────────────────────────────────
  document.querySelectorAll('tr[data-run-id] button[data-action="push"]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const tr = btn.closest('tr');
      const id = tr.getAttribute('data-run-id');
      if (!confirm('Push this receipt to the worker for acknowledgement?')) return;
      btn.disabled = true;
      const r = await fetch(`/admin/payments/${id}/push`, { method: 'POST' });
      if (r.ok) location.reload();
      else { btn.disabled = false; alert('Push failed.'); }
    });
  });
  document.querySelectorAll('tr[data-run-id] button[data-action="delete"]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const tr = btn.closest('tr');
      const id = tr.getAttribute('data-run-id');
      if (!confirm(
        'DELETE this payment run?\n\n' +
        'Its posters will become eligible for payment again. Use only to fix mistakes.'
      )) return;
      btn.disabled = true;
      const r = await fetch(`/admin/payments/${id}/delete`, { method: 'POST' });
      if (r.ok) location.reload();
      else { btn.disabled = false; alert('Delete failed.'); }
    });
  });

  // First load
  refreshAll();
})();
