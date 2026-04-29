/* Admin "Changes Requested" page — wires up Approve / Reject / Clear-flag. */

(function () {
  // Approve / Reject buttons inside awaiting-approval cards
  document.querySelectorAll('.rev-card-awaiting').forEach((card) => {
    const revId = card.getAttribute('data-revision-id');
    const verdictInp = card.querySelector('[data-verdict-input]');
    const approveBtn = card.querySelector('[data-action="approve"]');
    const rejectBtn  = card.querySelector('[data-action="reject"]');

    approveBtn.addEventListener('click', async () => {
      if (!confirm('Approve this fix? The flag will clear from the user side.')) return;
      await act(`/admin/revisions/${revId}/approve`, verdictInp.value || '');
    });

    rejectBtn.addEventListener('click', async () => {
      const v = (verdictInp.value || '').trim();
      if (!v) {
        alert('Please type what you want changed before rejecting — the user will see this as the new instruction.');
        verdictInp.focus();
        return;
      }
      if (!confirm('Reject and send back to the user with your verdict appended to the original comment?')) return;
      await act(`/admin/revisions/${revId}/reject`, v);
    });
  });

  // Clear-flag buttons inside open cards
  document.querySelectorAll('[data-unflag-poster-id]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const pid = btn.getAttribute('data-unflag-poster-id');
      if (!confirm('Clear this flag without requiring any change?')) return;
      const r = await fetch(`/admin/poster/${pid}/unflag`, { method: 'POST' });
      if (r.ok) location.reload();
      else alert('Failed: ' + r.status);
    });
  });

  // Deletion-review cards — ACKNOWLEDGE / SEND BACK
  document.querySelectorAll('.rev-card-deletion').forEach((card) => {
    const revId = card.getAttribute('data-revision-id');
    const noteInp = card.querySelector('[data-deletion-note]');
    const ackBtn  = card.querySelector('[data-action="ack-deletion"]');
    const sendBtn = card.querySelector('[data-action="send-back-deletion"]');

    if (ackBtn) ackBtn.addEventListener('click', async () => {
      const r = await fetch(`/admin/deletions/${revId}/acknowledge`, { method: 'POST' });
      if (r.ok) {
        card.style.transition = 'opacity 0.3s';
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 320);
      } else {
        alert('Failed.');
      }
    });

    if (sendBtn) sendBtn.addEventListener('click', async () => {
      const note = (noteInp.value || '').trim();
      if (!note) {
        alert('Please type a note for the user before sending back.');
        noteInp.focus();
        return;
      }
      if (!confirm('Send this back to the user?\nThe title will revert to in-progress with your note pinned to it.')) return;
      const fd = new FormData();
      fd.append('note', note);
      const r = await fetch(`/admin/deletions/${revId}/escalate`, { method: 'POST', body: fd });
      if (r.ok) {
        card.style.transition = 'opacity 0.3s';
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 320);
      } else {
        let msg = r.status;
        try { const d = await r.json(); msg = d.detail || msg; } catch (e) {}
        alert('Failed: ' + msg);
      }
    });
  });

  async function act(url, verdict) {
    const fd = new FormData();
    fd.append('verdict', verdict);
    const r = await fetch(url, { method: 'POST', body: fd });
    if (r.ok) location.reload();
    else {
      let msg = r.status;
      try { const d = await r.json(); msg = d.detail || msg; } catch (e) {}
      alert('Failed: ' + msg);
    }
  }
})();
