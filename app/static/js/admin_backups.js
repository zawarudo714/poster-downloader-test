/* Backups admin page — snapshot / restore / delete buttons. */

(function () {
  const $ = (id) => document.getElementById(id);

  // ── Manual snapshot ─────────────────────────────────────────────────────
  const snapBtn = $('bk-snapshot-create');
  const snapName = $('bk-snapshot-name');
  const snapStatus = $('bk-snapshot-status');

  if (snapBtn) {
    snapBtn.addEventListener('click', async () => {
      snapBtn.disabled = true;
      snapStatus.textContent = 'saving…';
      const fd = new FormData();
      fd.append('name', snapName.value || '');
      const r = await fetch('/admin/backups/snapshot', { method: 'POST', body: fd });
      const data = await r.json().catch(() => ({}));
      if (r.ok) {
        snapStatus.textContent = `saved → ${data.filename}`;
        snapName.value = '';
        // Reload to show it in the list.
        setTimeout(() => location.reload(), 800);
      } else {
        snapStatus.textContent = 'failed: ' + (data.detail || r.status);
        snapBtn.disabled = false;
      }
    });
  }

  // ── Per-row Restore / Delete ────────────────────────────────────────────
  document.querySelectorAll('tr[data-filename]').forEach((row) => {
    const filename = row.getAttribute('data-filename');
    const restoreBtn = row.querySelector('[data-action="restore"]');
    const deleteBtn  = row.querySelector('[data-action="delete"]');

    if (restoreBtn) {
      restoreBtn.addEventListener('click', async () => {
        if (!confirm(
            `Restore from ${filename}?\n\n` +
            `This replaces the live database with this backup file.\n` +
            `A safety snapshot of the current state is created first, so you can\n` +
            `undo by restoring that snapshot.\n\n` +
            `Anyone using the app right now may need to refresh once.`
        )) return;
        if (!confirm('Are you sure? This is the last warning.')) return;

        restoreBtn.disabled = true;
        restoreBtn.textContent = 'RESTORING…';

        const fd = new FormData();
        fd.append('filename', filename);
        const r = await fetch('/admin/backups/restore', { method: 'POST', body: fd });
        const data = await r.json().catch(() => ({}));
        if (r.ok) {
          let msg = 'Restore complete.';
          if (data.safety_snapshot) msg += `\nSafety snapshot saved as: ${data.safety_snapshot}`;
          alert(msg);
          location.reload();
        } else {
          alert('Restore failed: ' + (data.detail || r.status));
          restoreBtn.disabled = false;
          restoreBtn.textContent = 'RESTORE';
        }
      });
    }

    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        if (!confirm(`Delete ${filename}? This cannot be undone.`)) return;
        const fd = new FormData();
        fd.append('filename', filename);
        const r = await fetch('/admin/backups/delete', { method: 'POST', body: fd });
        if (r.ok) {
          row.style.transition = 'opacity 0.3s';
          row.style.opacity = '0';
          setTimeout(() => row.remove(), 320);
        } else {
          const data = await r.json().catch(() => ({}));
          alert('Delete failed: ' + (data.detail || r.status));
        }
      });
    }
  });
})();
