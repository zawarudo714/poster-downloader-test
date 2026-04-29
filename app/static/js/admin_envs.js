/* Admin Test Environments page — create, reset, delete. ENTER and SWITCH TO
   LIVE are plain form submits (so they reload the page server-side, which is
   what we want — every page on the site needs to re-render with the new env's
   data). */

(function () {

  // ── Create ──────────────────────────────────────────────────────────────
  const nameInput = document.getElementById('env-create-name');
  const createBtn = document.getElementById('env-create-btn');
  const createMsg = document.getElementById('env-create-msg');

  if (createBtn) {
    createBtn.addEventListener('click', async () => {
      const name = (nameInput.value || '').trim();
      if (!name) {
        createMsg.textContent = 'Type a name first.';
        nameInput.focus();
        return;
      }
      createMsg.textContent = 'Creating…';
      const fd = new FormData();
      fd.append('name', name);
      const r = await fetch('/admin/envs/create', { method: 'POST', body: fd });
      if (r.ok) {
        createMsg.textContent = 'Created. Reloading…';
        nameInput.value = '';
        location.reload();
      } else {
        const data = await r.json().catch(() => ({}));
        createMsg.textContent = 'Failed: ' + (data.detail || r.status);
      }
    });
    nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') createBtn.click(); });
  }

  // ── Per-env actions ─────────────────────────────────────────────────────
  document.querySelectorAll('tr[data-env-name]').forEach((row) => {
    const name = row.getAttribute('data-env-name');
    const resetBtn  = row.querySelector('[data-action="reset"]');
    const deleteBtn = row.querySelector('[data-action="delete"]');

    if (resetBtn) {
      resetBtn.addEventListener('click', async () => {
        if (!confirm(`Reset test env "${name}"? All data in it will be wiped — but the env itself stays so you can keep using it.`)) return;
        const fd = new FormData();
        fd.append('name', name);
        const r = await fetch('/admin/envs/reset', { method: 'POST', body: fd });
        if (r.ok) {
          alert(`Reset "${name}".`);
          location.reload();
        } else {
          const data = await r.json().catch(() => ({}));
          alert('Failed: ' + (data.detail || r.status));
        }
      });
    }

    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        if (!confirm(
          `DELETE test env "${name}" entirely? This cannot be undone.\n\n` +
          `Any workers pinned to "${name}" will be auto-bumped to live on their next ` +
          `request — they'll suddenly see live data, which is probably surprising for them. ` +
          `Consider DISABLING those users first via the Users page.`
        )) return;
        if (!confirm(`Really delete "${name}"? Last chance.`)) return;
        const fd = new FormData();
        fd.append('name', name);
        const r = await fetch('/admin/envs/delete', { method: 'POST', body: fd });
        if (r.ok) {
          location.reload();
        } else {
          const data = await r.json().catch(() => ({}));
          alert('Failed: ' + (data.detail || r.status));
        }
      });
    }
  });

  // ── "Switch back to live" link in the muted help line ───────────────────
  const leaveLink = document.getElementById('leave-now');
  if (leaveLink) {
    leaveLink.addEventListener('click', async (e) => {
      e.preventDefault();
      const r = await fetch('/admin/envs/leave', { method: 'POST' });
      if (r.ok) location.reload();
      else alert('Failed to leave env.');
    });
  }

})();
