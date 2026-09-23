/* Admin "Changes Requested" page — wires up Approve / Reject / Clear-flag,
   and opens the shared zoom (poster_lightbox.js) on every thumbnail. */

(function () {
  // ── The shared zoom ──────────────────────────────────────────────────
  // Same overlay as Worker Images: details line, source pill, kind chip,
  // CHECK GOOGLE, the K mark, arrow through every picture on the page.
  // The DECISIONS stay on the cards (APPROVE / REJECT with a typed
  // verdict), so flagging, place-ack and retire are switched off here —
  // a second door to "send back" inside the zoom would be two paths to
  // one action.
  let zoomData = {};
  try {
    const el = document.getElementById('lb-data');
    zoomData = el ? (JSON.parse(el.textContent) || {}) : {};
  } catch (e) { zoomData = {}; }

  // Reading order of the thumbnails on the page, deduplicated — a picture
  // can appear both in a card and in a "title now holds" strip, and the
  // arrows should visit it once.
  function zoomList() {
    const out = [];
    const seen = new Set();
    document.querySelectorAll('[data-lb]').forEach((a) => {
      const item = zoomData[a.getAttribute('data-lb')];
      if (!item || seen.has(item.poster.poster_id)) return;
      seen.add(item.poster.poster_id);
      out.push({ t: item.master, p: item.poster });
    });
    return out;
  }

  const LB = window.PosterLightbox;
  const zoomReady = LB && LB.init({
    list: zoomList,
    // The K mark's green ring on every copy of this picture on the page,
    // so the grid view agrees with the zoom the moment it closes.
    onReviewed: (t, p) => {
      document.querySelectorAll(`[data-lb="${p.poster_id}"]`)
        .forEach((a) => a.classList.toggle('lb-marked', !!p.reviewed));
    },
    features: { flag: false, retire: false, placeAck: false },
  });

  if (zoomReady) {
    document.querySelectorAll('[data-lb]').forEach((a) => {
      const item = zoomData[a.getAttribute('data-lb')];
      if (!item) return;   // no payload (e.g. file missing) → keep the plain link
      if (item.poster.reviewed) a.classList.add('lb-marked');
      a.addEventListener('click', (e) => {
        e.preventDefault();
        LB.open(item.master, item.poster);
      });
    });
  }
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

  // DELETE THIS RECORD — shown only on cards whose picture FILE is gone
  // from the workspace. Neither approve nor reject can help there (the
  // worker has nothing to fix, the admin nothing to see — the loop that
  // ran 2026-09-15..18 on Atlanta), so the card offers the one exit:
  // the existing admin delete, which resolves the flags and returns the
  // title to the queue for a fresh picture.
  document.querySelectorAll('[data-missing-delete]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const pid = btn.getAttribute('data-missing-delete');
      if (!confirm('Delete this record? The flag is closed and the title '
                   + 'goes back to the queue so a fresh picture can be saved. '
                   + 'This does not count against the worker.')) return;
      btn.disabled = true;
      try {
        const r = await fetch(`/admin/poster/${pid}/delete`, { method: 'POST' });
        if (r.ok) { location.reload(); return; }
        alert('Delete failed: ' + r.status);
      } catch (e) {
        alert('Delete failed: ' + e);
      }
      // Only reached on failure — success reloads the page.
      btn.disabled = false;
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
  // Pending-completion cards — APPROVE COMPLETION / REJECT & SEND BACK
  document.querySelectorAll('.pending-complete-card').forEach((card) => {
    const masterId = card.getAttribute('data-master-id');
    const verdictInp = card.querySelector('[data-pcc-verdict]');
    const approveBtn = card.querySelector('[data-action="approve-complete"]');
    const rejectBtn  = card.querySelector('[data-action="reject-complete"]');

    if (approveBtn) approveBtn.addEventListener('click', async () => {
      if (!confirm('Approve this completion? All flags on this title will be resolved and the title becomes COMPLETE.')) return;
      const fd = new FormData();
      fd.append('verdict', verdictInp.value || '');
      const r = await fetch(`/admin/title/${masterId}/approve_complete`, { method: 'POST', body: fd });
      if (r.ok) location.reload();
      else {
        let msg = r.status;
        try { const d = await r.json(); msg = d.detail || msg; } catch (e) {}
        alert('Failed: ' + msg);
      }
    });

    if (rejectBtn) rejectBtn.addEventListener('click', async () => {
      const v = (verdictInp.value || '').trim();
      if (!v) {
        alert('Please type what you want changed before rejecting — the worker will see this on every reopened flag.');
        verdictInp.focus();
        return;
      }
      if (!confirm('Reject this completion?\nThe title returns to in-progress, all flags re-open, and your note pins to the title + each flag.')) return;
      const fd = new FormData();
      fd.append('verdict', v);
      const r = await fetch(`/admin/title/${masterId}/reject_complete`, { method: 'POST', body: fd });
      if (r.ok) location.reload();
      else {
        let msg = r.status;
        try { const d = await r.json(); msg = d.detail || msg; } catch (e) {}
        alert('Failed: ' + msg);
      }
    });
  });

})();
