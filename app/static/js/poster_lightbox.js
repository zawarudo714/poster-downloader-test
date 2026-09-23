/* The shared image zoom — ONE mechanism for every admin screen that shows
   a worker's picture (Worker Images and Changes Requested today).

   Why shared: the zoom used to live inside admin.js, so Changes Requested
   could only open pictures in a new browser tab — no details, no Google
   button, no K mark (owner, 2026-09-23). Copying the zoom there would have
   been a second copy that drifts; lifting it out gives both pages the same
   one, and a fix lands on both at once.

   Division of labour, on purpose:
     - THIS FILE owns what is the same everywhere: opening, closing,
       stepping with the arrows, the title/meta lines, the source pill,
       the reviewed (K) pill and its save, the flag-info panel, the
       CHECK GOOGLE button, and the keyboard (Esc / arrows / K).
     - THE PAGE owns its decisions. Flagging, clearing a flag, the place
       check's "checked, it's fine" and RETIRE TITLE are Worker Images
       actions and stay bound in admin.js; Changes Requested decides with
       APPROVE / REJECT on its cards instead — a second door to "send
       back" inside the zoom would be two paths to one action.
   The `features` switches below only show or hide those page-owned
   controls; they never bind them.

   Markup contract: the ids this file queries live in
   templates/_poster_lightbox.html, included by every page that loads
   this script. */

(function () {
  'use strict';

  const $id = (id) => document.getElementById(id);

  let opts = null;          // what the page told init()
  let current = null;       // { master, poster } while open, else null

  function fileUrl(p) {
    if (opts && opts.fileUrl) return opts.fileUrl(p);
    return `/admin/file/${p.poster_id}?v=${encodeURIComponent(p.size || p.filename || 0)}`;
  }

  // One spelling of the source pill (Brave / Google / pasted), so every
  // screen names the worker's source with the same word and tooltip.
  function sourcePill(p) {
    if (!p.image_source) return null;
    const s = document.createElement('span');
    s.className = 'status-pill status-img-source';
    s.textContent = { brave: 'Brave', google: 'Google',
                      pasted: 'pasted' }[p.image_source] || p.image_source;
    s.title = { brave: 'Found with the in-page Brave search',
                google: 'Sent from Google by the phone add-on',
                pasted: 'Pasted as a link by hand' }[p.image_source] || '';
    return s;
  }

  function list() {
    return (opts && opts.list) ? (opts.list() || []) : [];
  }

  function step(delta) {
    const l = list();
    if (!l.length || !current) return;
    const at = l.findIndex((e) => e.p.poster_id === current.poster.poster_id);
    const next = l[at + delta];
    if (next) open(next.t, next.p);
  }

  function renderReviewed(p) {
    const lightbox = $id('ib-lightbox');
    if (!lightbox) return;
    lightbox.classList.toggle('lb-reviewed', !!p.reviewed);
    const host = $id('ib-lb-pills');
    if (!host) return;
    let pill = host.querySelector('.status-reviewed');
    if (p.reviewed && !pill) {
      pill = document.createElement('span');
      pill.className = 'status-pill status-reviewed';
      pill.textContent = 'REVIEWED ✓';
      pill.title = 'You marked this one as looked-at (K). Press K again to undo.';
      host.appendChild(pill);
    } else if (!p.reviewed && pill) {
      pill.remove();
    }
  }

  // The K mark — "I have looked at this one". Saves, updates the object in
  // place, then hands the page a turn so everything DERIVED from the mark
  // (grid cell, title outline, day count) is redone by the code that owns it.
  async function toggleReviewed(t, p) {
    const r = await fetch(`/admin/poster/${p.poster_id}/reviewed`, { method: 'POST' });
    if (!r.ok) { alert('Could not save the mark: ' + r.status); return; }
    const d = await r.json();
    p.reviewed = !!d.reviewed;
    if (opts && opts.onReviewed) opts.onReviewed(t, p);
    if (current && current.poster.poster_id === p.poster_id) renderReviewed(p);
  }

  function open(t, p) {
    const lightbox = $id('ib-lightbox');
    if (!lightbox) return;
    current = { master: t, poster: p };

    // The PLACE, named first and large — a bare image of somewhere you
    // cannot name is exactly what the owner asked to never see again.
    const nameEl = $id('ib-lb-title');
    if (nameEl) {
      nameEl.textContent = (t.external_id != null ? t.external_id + '. ' : '')
        + t.title + (t.year ? ` (${t.year})` : '');
    }
    const kindEl = $id('ib-lb-kind');
    if (kindEl) kindEl.innerHTML = window.SubjectKind ? window.SubjectKind.chip(t.kind) : '';
    const posEl = $id('ib-lb-pos');
    if (posEl) {
      const l = list();
      const at = l.findIndex((e) => e.p.poster_id === p.poster_id);
      posEl.textContent = at >= 0 ? `${at + 1} / ${l.length}` : '';
    }

    const lbImg = $id('ib-lb-img');
    lbImg.src = fileUrl(p);
    lbImg.alt = p.filename || '';
    const dims = (p.image_width && p.image_height) ? ` · ${p.image_width}×${p.image_height}` : '';
    const lq   = p.low_quality_url ? ' · ⚠ LQ-URL bypassed' : '';
    const from = { brave: ' · found on Brave', google: ' · found on Google',
                   pasted: ' · pasted link' }[p.image_source] || '';
    $id('ib-lb-meta').textContent =
      `${t.title}${t.year ? ` (${t.year})` : ''} — ${p.filename}${dims}${lq}${from}`;

    // Pills: the source pill is universal; the page adds its own after it
    // (the place-check verdict on Worker Images). The reviewed pill is
    // re-rendered last so it always sits at the end.
    const pillsHost = $id('ib-lb-pills');
    if (pillsHost) {
      pillsHost.innerHTML = '';
      const src = sourcePill(p);
      if (src) pillsHost.appendChild(src);
      if (opts && opts.extraPills) {
        (opts.extraPills(t, p) || []).forEach((n) => { if (n) pillsHost.appendChild(n); });
      }
    }
    renderReviewed(p);

    // Page-owned controls: shown only where the page said it binds them.
    const f = (opts && opts.features) || {};
    const commentEl = $id('ib-lb-comment');
    if (commentEl) {
      // The text box serves the page's own purpose: a flag comment on
      // Worker Images, the verdict on Changes Requested.
      commentEl.hidden = !(f.flag || f.commentPlaceholder);
      commentEl.placeholder = f.commentPlaceholder
        || '(optional) comment for the user';
      commentEl.value = '';
    }
    const flagBtn = $id('ib-lb-flag-btn');
    if (flagBtn) flagBtn.hidden = !f.flag;

    // The page's own decision buttons (APPROVE / REJECT on Changes
    // Requested). The zoom only DRAWS them; what they do belongs to the
    // page, so closing the zoom to press a card button is never needed.
    const actionsHost = $id('ib-lb-actions');
    if (actionsHost) {
      actionsHost.innerHTML = '';
      if (opts && opts.actions) {
        (opts.actions(t, p) || []).forEach((a) => {
          const b = document.createElement('button');
          b.className = a.className || 'btn btn-ghost';
          b.textContent = a.label;
          b.addEventListener('click', a.onClick);
          actionsHost.appendChild(b);
        });
      }
    }
    const retireBtn = $id('ib-lb-retire');
    if (retireBtn) retireBtn.hidden = !f.retire;
    // "Checked, it's fine" — the page decides per image whether the verdict
    // can be acknowledged at all (Worker Images only).
    const ackBtn = $id('ib-lb-place-ack');
    if (ackBtn) {
      const ackable = !!(f.placeAck && f.placeAck(t, p));
      ackBtn.hidden = !ackable;
      if (ackable) {
        ackBtn.textContent = p.place_acked ? 'UNDO — MARK IT A PROBLEM AGAIN'
                                           : "CHECKED, IT'S FINE";
      }
    }

    // CHECK GOOGLE — opens Google Images for this place in a new tab, so
    // the scenic view can be confirmed without leaving the zoom. "" means
    // the project has no source link, so the button stays hidden.
    const gBtn = $id('ib-lb-google');
    if (gBtn) gBtn.hidden = !(t && t.google_url);

    // The flag-info panel is universal DISPLAY: what is flagged and why.
    const lbFlag = $id('ib-lb-flag');
    const unflagBtn = $id('ib-lb-unflag-btn');
    if (p.flagged) {
      lbFlag.hidden = false;
      const pill = lbFlag.querySelector('.lb-status-pill');
      pill.className = 'status-pill';
      if (p.revision_status === 'awaiting_approval') {
        pill.classList.add('status-awaiting');
        pill.textContent = 'awaiting approval';
      } else {
        pill.classList.add('status-flag');
        pill.textContent = 'open';
      }
      lbFlag.querySelector('.lb-flag-comment').textContent = p.comment || '(no comment)';
      lbFlag.querySelector('.lb-worker-note').textContent =
        p.worker_note ? `User note: ${p.worker_note}` : '';
      if (unflagBtn) unflagBtn.hidden = !f.flag;
    } else {
      lbFlag.hidden = true;
      if (unflagBtn) unflagBtn.hidden = true;
    }

    lightbox.hidden = false;
    if (opts && opts.onOpen) opts.onOpen(t, p);
  }

  function close() {
    const lightbox = $id('ib-lightbox');
    if (lightbox) lightbox.hidden = true;
    current = null;
    if (opts && opts.onClose) opts.onClose();
  }

  function isOpen() {
    const lightbox = $id('ib-lightbox');
    return !!(lightbox && !lightbox.hidden);
  }

  function init(o) {
    const lightbox = $id('ib-lightbox');
    if (!lightbox) return false;
    opts = o || {};

    // Close controls — scoped to the zoom itself, so another dialog that
    // happens to reuse the class is not grabbed by accident.
    lightbox.querySelectorAll('[data-lightbox-close]').forEach((el) => {
      el.addEventListener('click', close);
    });
    const prev = $id('ib-lb-prev'), next = $id('ib-lb-next');
    if (prev) prev.addEventListener('click', () => step(-1));
    if (next) next.addEventListener('click', () => step(1));

    // CHECK GOOGLE — a new tab, so the zoom stays open behind it and you
    // can keep arrowing.
    const gBtn = $id('ib-lb-google');
    if (gBtn) {
      gBtn.addEventListener('click', () => {
        if (!current || !current.master.google_url) return;
        window.open(current.master.google_url, '_blank', 'noopener');
      });
    }

    // Keyboard, only while the zoom is open — the page keeps its own keys
    // for the plain view and steps aside by asking isOpen().
    document.addEventListener('keydown', (e) => {
      const ae = document.activeElement;
      if (ae && ['INPUT', 'SELECT', 'TEXTAREA'].includes(ae.tagName)) return;
      if (!isOpen()) return;
      if (e.key === 'Escape') close();
      if (e.key === 'ArrowLeft')  step(-1);
      if (e.key === 'ArrowRight') step(1);
      // K = "I have looked at this one", K again undoes — the same key
      // Approve Artwork uses for keep, so the hand already knows it.
      // Switched off where approving already carries the mark (Changes
      // Requested), so one screen never has two ways to say "seen".
      const f = (opts && opts.features) || {};
      if ((e.key === 'k' || e.key === 'K') && current && f.reviewK !== false) {
        toggleReviewed(current.master, current.poster);
      }
    });
    return true;
  }

  window.PosterLightbox = {
    init, open, close, step, isOpen, toggleReviewed, sourcePill,
    current: () => current,
  };
})();
