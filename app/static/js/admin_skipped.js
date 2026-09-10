/* Admin "Skipped Titles" page — Send Back, and the read/unread mark.
 *
 * Every row has THREE exits now, not one. SEND BACK bounces the title to
 * the worker. LEAVE IT SKIPPED marks the skip as read, so it stops counting
 * as "waiting on you" — before that button existed, every skip raised the
 * home-page card by one for ever (owner's find, 2026-09-10). ASK ME AGAIN
 * takes the mark back.
 */

(function () {
  async function post(url, fields) {
    const fd = new FormData();
    Object.entries(fields || {}).forEach(([k, v]) => fd.append(k, v));
    const r = await fetch(url, { method: 'POST', body: fd });
    if (!r.ok) {
      let msg = r.status;
      try { const d = await r.json(); msg = d.detail || msg; } catch (e) {}
      alert('Failed: ' + msg);
      return false;
    }
    return true;
  }

  function fadeOut(row) {
    row.style.transition = 'opacity 0.3s';
    row.style.opacity = '0';
    setTimeout(() => row.remove(), 320);
  }

  document.querySelectorAll('tr[data-master-id]').forEach((row) => {
    const masterId = row.getAttribute('data-master-id');
    const noteInp = row.querySelector('[data-skip-note]');
    const backBtn = row.querySelector('[data-action="send-back"]');
    const ackBtn = row.querySelector('[data-action="ack-skip"]');
    const unackBtn = row.querySelector('[data-action="unack-skip"]');

    if (backBtn) {
      backBtn.addEventListener('click', async () => {
        const note = (noteInp.value || '').trim();
        if (!note) {
          alert('Please type a note explaining what you want the user to do.');
          noteInp.focus();
          return;
        }
        if (!confirm('Send this title back to the user with your note?')) return;
        if (await post(`/admin/title/${masterId}/skip_revise`, { note })) {
          fadeOut(row);
        }
      });
      // Enter in the note field submits
      if (noteInp) {
        noteInp.addEventListener('keydown', (e) => { if (e.key === 'Enter') backBtn.click(); });
      }
    }

    if (ackBtn) {
      ackBtn.addEventListener('click', async () => {
        // No confirm dialog: the action is cheap, visible, and reversible
        // from the ALREADY READ section below.
        if (await post(`/admin/skipped/${masterId}/ack`, { undo: 0 })) {
          // The row moves sections, and the counts in both panel headers
          // change — a reload draws the truth rather than imitating it.
          location.reload();
        }
      });
    }

    if (unackBtn) {
      unackBtn.addEventListener('click', async () => {
        if (await post(`/admin/skipped/${masterId}/ack`, { undo: 1 })) {
          location.reload();
        }
      });
    }
  });
})();
