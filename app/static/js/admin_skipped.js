/* Admin "Skipped Titles" page — wires up Send Back. */

(function () {
  document.querySelectorAll('tr[data-master-id]').forEach((row) => {
    const masterId = row.getAttribute('data-master-id');
    const noteInp = row.querySelector('[data-skip-note]');
    const btn = row.querySelector('[data-action="send-back"]');
    if (!btn) return;

    btn.addEventListener('click', async () => {
      const note = (noteInp.value || '').trim();
      if (!note) {
        alert('Please type a note explaining what you want the user to do.');
        noteInp.focus();
        return;
      }
      if (!confirm('Send this title back to the user with your note?')) return;

      const fd = new FormData();
      fd.append('note', note);
      const r = await fetch(`/admin/title/${masterId}/skip_revise`, { method: 'POST', body: fd });
      if (r.ok) {
        // Fade the row out, then remove
        row.style.transition = 'opacity 0.3s';
        row.style.opacity = '0';
        setTimeout(() => row.remove(), 320);
      } else {
        let msg = r.status;
        try { const d = await r.json(); msg = d.detail || msg; } catch (e) {}
        alert('Failed: ' + msg);
      }
    });

    // Enter in the note field submits
    noteInp.addEventListener('keydown', (e) => { if (e.key === 'Enter') btn.click(); });
  });
})();
