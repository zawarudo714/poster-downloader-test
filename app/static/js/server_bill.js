/* The monthly server-bill reminder (v298, owner 2026-11-17).
   From the 24th a small pill sits in the top bar until the owner ticks
   Paid; it comes back on the next 24th. Admins only (base.html). Nothing
   is paid from here: it is a note, the tick only hides it. */
(() => {
  'use strict';
  const bar = document.querySelector('.user-pill');
  if (!bar) return;
  const esc = s => String(s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function check() {
    let data;
    try {
      const r = await fetch('/admin/api/server_bill', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) return;
      data = await r.json();
    } catch (e) { return; }
    if (!data.due) return;
    const pill = document.createElement('label');
    pill.className = 'bill-pill';
    pill.title = 'Monthly server bills. Tick Paid once both are paid; it comes back on the next 24th.';
    pill.innerHTML = 'Pay servers: ' +
      data.servers.map(s => `${esc(s.name)} ${esc(s.symbol)}${esc(s.price)}`).join(' + ') +
      ' <input type="checkbox" aria-label="Paid"> Paid';
    pill.querySelector('input').addEventListener('change', async ev => {
      const box = ev.target;
      if (!box.checked) return;
      box.disabled = true;
      const r = await fetch('/admin/api/server_bill/paid', { method: 'POST', credentials: 'same-origin' });
      if (r.ok) pill.remove(); else { box.checked = false; box.disabled = false; }
    });
    bar.insertBefore(pill, bar.firstChild);
  }
  check();
})();
