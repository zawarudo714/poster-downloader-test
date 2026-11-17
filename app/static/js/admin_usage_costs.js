/* The two small panels under the Usage charts (v298, owner 2026-11-17):
   the monthly server prices, and each worker's pictures and pay per month.
   Kept apart from the charts on purpose: those show what the OpenAI key
   cost, these are fixed bills and wages. */
(() => {
  'use strict';
  const servers = document.querySelector('[data-usage-servers]');
  const pay = document.querySelector('[data-usage-pay]');
  if (!servers || !pay) return;

  const esc = s => String(s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthName = m => `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`;
  const kes = n => Number(n).toLocaleString('en-US');

  function drawServers(list, billDay, paidMonth) {
    servers.innerHTML = list.map(s =>
      `<span class="usage-server"><b>${esc(s.name)}</b> <span class="mono">${esc(s.symbol)}${esc(s.price)}</span>` +
      ` <button type="button" class="link-btn" data-edit="${esc(s.key)}" data-name="${esc(s.name)}"` +
      ` data-price="${esc(s.price)}" title="Change this price">edit</button></span>`).join('') +
      `<span class="muted">Due on the ${billDay}th each month` +
      (paidMonth ? ` · last ticked paid: ${esc(monthName(paidMonth))}` : '') + '</span>';
  }

  function drawPay(data) {
    if (!data.workers.length) { pay.innerHTML = '<span class="muted">No worker pictures in the last 12 months.</span>'; return; }
    pay.innerHTML = data.workers.map(w => {
      const rows = w.rows.filter(r => r.pictures || r.paid_kes !== '0');
      const body = rows.map(r =>
        `<tr><td>${esc(monthName(r.month))}${r.so_far ? ' <span class="muted">(so far)</span>' : ''}</td>` +
        `<td class="mono">${kes(r.pictures)}</td><td class="mono">${kes(r.pay_kes)}</td>` +
        `<td class="mono">${r.paid_kes === '0' ? '<span class="muted">—</span>' : kes(r.paid_kes)}</td></tr>`).join('');
      return `<div class="usage-pay-worker"><div class="usage-pay-name">${esc(w.name)}</div>` +
        '<table class="usage-tbl"><thead><tr><th>Month</th><th>Pictures</th>' +
        `<th title="Pictures × today's rate (${esc(data.rate_kes)} KES)">Pay KES</th>` +
        '<th title="What payment runs ending that month actually sent, all projects">Paid KES</th></tr></thead>' +
        `<tbody>${body}</tbody></table></div>`;
    }).join('') +
    `<div class="muted usage-pay-note">Pictures that count toward pay, by the day they were saved, in this project. ` +
    `Pay KES = pictures × today's rate (${esc(data.rate_kes)} KES). Paid KES = what was actually sent.</div>`;
  }

  async function load() {
    try {
      const r = await fetch('/admin/api/usage/costs', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      drawServers(data.servers, data.bill_day, data.paid_month);
      drawPay(data);
    } catch (e) {
      servers.innerHTML = pay.innerHTML = `<span class="muted">Could not load: ${esc(e.message)}</span>`;
    }
  }

  servers.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-edit]');
    if (!b) return;
    const price = prompt(`${b.dataset.name} price per month:`, b.dataset.price);
    if (price === null || price.trim() === b.dataset.price) return;
    const r = await fetch('/admin/api/usage/server_prices', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: b.dataset.edit, price: price.trim() }),
    });
    if (!r.ok) { alert((await r.json().catch(() => ({}))).detail || `Not saved (HTTP ${r.status}).`); return; }
    load();
  });

  load();
})();
