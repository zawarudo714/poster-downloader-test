/* USE MY OWN PICTURE — the one dialog every admin screen opens (v239).

   The owner puts his own picture on a title in place of whatever the
   worker saved. Worker Images, Changes Requested, Skipped and the two
   retired lists in Needs Attention all call window.AdminPick.open(); none
   of them carries a copy of the form, so the five doors cannot drift apart
   — the old + ADD box was a door of its own and had drifted into adding a
   SECOND picture instead of replacing one.

   The dialog builds its own markup on first use, so no page has to include
   a template for it and no page can forget to.

   What pressing USE THIS PICTURE does is the server's business
   (routes/admin.admin_pick_title): the picture arrives first, the worker's
   picture is taken down (the worker is still paid), the title is finished
   — a retired one comes back — and the picture goes straight to painting.
   This file only asks, sends, and reports. */

(function () {
  'use strict';

  let box = null;          // the dialog, built once
  let job = null;          // { masterId, title, onDone } while open
  let busy = false;
  let pasted = null;       // { file, w, h, url } — a picture pasted from the clipboard

  // ── PASTE A COPIED PICTURE (owner, 2026-10-09) ─────────────────────────
  // "Like Photopea": copy an image in Google, then paste it here. Two ways
  // in, because a BUTTON may only read the clipboard on a secure (https)
  // page, and this site is plain http today — so on http the button cannot
  // read anything by itself and the paste key (Ctrl+V) is the way. The
  // PASTE event works on http, so Ctrl+V always works; the button appears
  // only where the browser says it can read.
  const canReadClipboard = () => !!(window.isSecureContext && navigator.clipboard
                                    && navigator.clipboard.read);

  // The clipboard usually holds a PNG even for a photograph, which can be
  // ten times the JPEG the website served — on a slow link that is minutes
  // of upload. So the picture is redrawn as a high-quality JPEG first; a
  // photograph loses nothing you can see. Its real size is shown, so a
  // thumbnail copied by mistake is obvious before it is sent.
  async function takeImage(blob) {
    const p = box._parts;
    p.status.textContent = 'Reading the pasted picture…';
    let bmp;
    try {
      bmp = await createImageBitmap(blob);
    } catch (e) {
      p.status.textContent = 'That was not a picture this browser can read. Copy the image again and paste.';
      return false;
    }
    const c = document.createElement('canvas');
    c.width = bmp.width;
    c.height = bmp.height;
    const g = c.getContext('2d');
    g.fillStyle = '#fff';                 // a see-through PNG flattens onto white
    g.fillRect(0, 0, c.width, c.height);
    g.drawImage(bmp, 0, 0);
    const jpeg = await new Promise((res) => c.toBlob(res, 'image/jpeg', 0.95));
    if (!jpeg) {
      p.status.textContent = 'Could not prepare the pasted picture. Try copying it again.';
      return false;
    }
    if (pasted && pasted.url) URL.revokeObjectURL(pasted.url);
    pasted = { file: new File([jpeg], 'pasted.jpg', { type: 'image/jpeg' }),
               w: bmp.width, h: bmp.height, url: URL.createObjectURL(jpeg) };
    // One picture at a time: a paste replaces any link or file typed in,
    // so what is sent is always what is on screen.
    p.url.value = '';
    p.file.value = '';
    p.prevImg.src = pasted.url;
    p.prevCap.textContent = `Pasted picture · ${pasted.w} × ${pasted.h} pixels`;
    p.prev.hidden = false;
    p.status.textContent = 'Ready. Press USE THIS PICTURE (or Enter).';
    p.go.focus();
    return true;
  }

  function clearPasted() {
    if (!box) return;
    if (pasted && pasted.url) URL.revokeObjectURL(pasted.url);
    pasted = null;
    const p = box._parts;
    p.prev.hidden = true;
    p.prevImg.removeAttribute('src');
    p.prevCap.textContent = '';
  }

  // The first image on a paste event, or null. A pasted LINK is left for
  // the browser to type into whichever box has the cursor.
  function imageFromPaste(e) {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const it of items) {
      if (it.kind === 'file' && /^image\//.test(it.type)) return it.getAsFile();
    }
    return null;
  }

  // The button's way in, where the browser allows it (https only).
  async function readClipboard() {
    const p = box._parts;
    try {
      const items = await navigator.clipboard.read();
      for (const it of items) {
        const t = it.types.find((x) => /^image\//.test(x));
        if (t) return takeImage(await it.getType(t));
      }
      p.status.textContent = 'There is no picture on the clipboard. In Google, right-click the picture and choose Copy image.';
    } catch (e) {
      p.status.textContent = 'The browser would not let this page read the clipboard. Press Ctrl+V instead.';
    }
    return false;
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function build() {
    box = el('div', 'lightbox admin-pick');
    box.hidden = true;
    const bg = el('div', 'lightbox-bg');
    bg.addEventListener('click', close);
    const card = el('div', 'lightbox-card delete-user-card admin-pick-card');

    const head = el('h3', null, 'USE MY OWN PICTURE — ');
    const name = el('span', 'admin-pick-title');
    head.appendChild(name);

    const lead = el('p', 'muted',
      'Your picture takes the place of the worker’s. The worker’s '
      + 'picture is removed and the worker is still paid for it. The title '
      + 'is finished, and your picture goes straight to painting. A retired '
      + 'title comes back.');

    const form = el('div', 'delete-user-form');
    const urlLab = el('label', 'filter-label', 'Paste the picture’s link');
    const url = el('input');
    url.type = 'text';
    url.autocomplete = 'off';
    url.placeholder = 'https://…/picture.jpg';
    urlLab.appendChild(url);

    const fileLab = el('label', 'filter-label', '…or choose a file from this computer');
    const file = el('input');
    file.type = 'file';
    file.accept = 'image/jpeg,image/png,image/webp,image/gif';
    fileLab.appendChild(file);

    // …or paste a copied picture. The zone is editable only so a phone
    // offers its Paste menu on a long press; nothing typed into it is kept.
    const pasteLab = el('div', 'filter-label', '…or paste a picture you copied');
    const zone = el('div', 'admin-pick-paste',
      'Press Ctrl+V to paste a copied picture. On a phone, press and hold here, then choose Paste.');
    zone.contentEditable = 'true';
    zone.spellcheck = false;
    zone.addEventListener('beforeinput', (e) => e.preventDefault());
    const clipBtn = el('button', 'btn btn-ghost btn-tiny', 'PASTE FROM CLIPBOARD');
    clipBtn.type = 'button';
    clipBtn.hidden = !canReadClipboard();
    clipBtn.addEventListener('click', () => readClipboard());
    const prev = el('div', 'admin-pick-preview');
    prev.hidden = true;
    const prevImg = el('img');
    prevImg.alt = 'The pasted picture';
    const prevCap = el('div', 'mono muted');
    const prevClear = el('button', 'btn btn-ghost btn-tiny', 'REMOVE');
    prevClear.type = 'button';
    prevClear.addEventListener('click', () => { clearPasted(); box._parts.status.textContent = ''; });
    prev.appendChild(prevImg);
    prev.appendChild(prevCap);
    prev.appendChild(prevClear);
    pasteLab.appendChild(zone);
    pasteLab.appendChild(clipBtn);
    pasteLab.appendChild(prev);

    const whyLab = el('label', 'filter-label', 'Why? (optional — shown beside the ADMIN PICK label)');
    const why = el('input');
    why.type = 'text';
    why.autocomplete = 'off';
    why.placeholder = 'e.g. the worker’s view missed the old town';
    whyLab.appendChild(why);

    // Progress sits here, next to what is changing — never on the button.
    const status = el('div', 'admin-pick-status mono muted');

    const actions = el('div', 'pcc-actions');
    const cancel = el('button', 'btn btn-ghost', 'CANCEL');
    cancel.type = 'button';
    cancel.addEventListener('click', close);
    const go = el('button', 'btn btn-accent', 'USE THIS PICTURE');
    go.type = 'button';
    go.addEventListener('click', () => send({}));
    actions.appendChild(cancel);
    actions.appendChild(go);

    // A link and a file are either/or: choosing one clears the other, so
    // what is sent is always what is on screen.
    url.addEventListener('input', () => { if (url.value.trim()) { file.value = ''; clearPasted(); } });
    file.addEventListener('change', () => { if (file.files.length) { url.value = ''; clearPasted(); } });
    url.addEventListener('keydown', (e) => { if (e.key === 'Enter') send({}); });
    // After a paste the focus sits on USE THIS PICTURE, so Enter sends.

    [urlLab, fileLab, pasteLab, whyLab, status, actions].forEach((n) => form.appendChild(n));
    [head, lead, form].forEach((n) => card.appendChild(n));
    box.appendChild(bg);
    box.appendChild(card);
    document.body.appendChild(box);

    box._parts = { name, url, file, why, status, go, cancel,
                   zone, prev, prevImg, prevCap };
    // A pasted PICTURE, anywhere in the open dialog, is the picture to use.
    // A pasted link or text is left alone, so the link box still works.
    document.addEventListener('paste', (e) => {
      if (!box || box.hidden || busy) return;
      const img = imageFromPaste(e);
      if (!img) return;
      e.preventDefault();
      takeImage(img);
    }, true);
    // While the dialog is up, keys belong to it: Escape closes it (not the
    // zoom underneath), and arrows or the keep key pressed outside its
    // boxes must not step or mark the picture behind it.
    document.addEventListener('keydown', (e) => {
      if (!box || box.hidden) return;
      if (e.key === 'Escape') {
        e.stopPropagation();
        close();
        return;
      }
      if (!box.contains(e.target)) e.stopPropagation();
    }, true);
  }

  function open(opts) {
    if (!opts || !opts.masterId) return;
    if (!box) build();
    job = opts;
    const p = box._parts;
    p.name.textContent = opts.title || '';
    p.url.value = '';
    p.file.value = '';
    p.why.value = '';
    p.status.textContent = '';
    p.go.disabled = false;
    clearPasted();
    box.hidden = false;
    // Opened by a paste (Ctrl+V in the zoom): the picture is already here.
    if (opts.pasteImage) {
      takeImage(opts.pasteImage);
    } else if (opts.paste) {
      // Opened by the zoom's PASTE PICTURE button.
      if (canReadClipboard()) readClipboard();
      else {
        p.zone.focus();
        p.status.textContent = 'Press Ctrl+V now to paste the picture you copied.';
      }
    } else {
      p.url.focus();
    }
  }

  function close() {
    if (busy || !box) return;      // never abandon a save half-way
    box.hidden = true;
    clearPasted();
    job = null;
  }

  // `ok` carries what has already been confirmed in this press, so a
  // second warning does not undo the answer to the first one.
  async function send(ok) {
    ok = ok || {};
    if (busy || !job) return;
    const p = box._parts;
    const link = p.url.value.trim();
    const chosen = (pasted && pasted.file) || (p.file.files && p.file.files[0]);
    if (!link && !chosen) {
      p.status.textContent = 'Paste a link, choose a file or paste a picture first.';
      p.url.focus();
      return;
    }
    const fd = new FormData();
    if (chosen) fd.append('file', chosen);
    else fd.append('url', link);
    fd.append('reason', p.why.value.trim());
    if (ok.same) fd.append('confirm_same_picture', '1');
    // Changes Requested passes flagSeen: the flag is on the very card the
    // button sits on, so the "you flagged this" warning would only repeat it.
    if (ok.flagged || job.flagSeen) fd.append('confirm_flagged', '1');

    busy = true;
    p.go.disabled = true;
    p.cancel.disabled = true;
    const current = job;
    // A WAIT THAT SHOWS IT IS ALIVE, AND ENDS (owner, 2026-10-09: it sat on
    // "Fetching the picture…" and the picture turned out to be saved after
    // a reload). The seconds tick so a slow website reads as slow, not as
    // frozen; past WAIT_S the screen stops waiting, says plainly that the
    // picture may still have been saved, and has the page re-read the
    // title so he can see which. The server gives up on a slow website
    // after 60 seconds on its own (imagefetch.TOTAL_S), so WAIT_S is set
    // above that.
    const WAIT_S = 120;
    const word = chosen ? 'Sending your file' : 'Fetching the picture';
    const started = Date.now();
    const tick = () => {
      const s = Math.round((Date.now() - started) / 1000);
      p.status.textContent = s < 3 ? `${word}…`
        : `${word}… ${s}s${s >= 30
            ? (chosen ? ' (the connection is slow)' : ' (this website is slow)') : ''}`;
    };
    tick();
    const ticker = setInterval(tick, 1000);
    const ctrl = new AbortController();
    const giveUp = setTimeout(() => ctrl.abort(), WAIT_S * 1000);
    try {
      let r;
      try {
        r = await fetch(`/admin/title/${current.masterId}/admin_pick`,
                        { method: 'POST', body: fd, signal: ctrl.signal });
      } finally {
        clearInterval(ticker);
        clearTimeout(giveUp);
      }
      let d = {};
      try { d = await r.json(); } catch (e) { d = {}; }
      if (r.status === 409 && d.reason === 'flagged_title') {
        busy = false;
        if (confirm(d.message || 'You sent this title back to the worker. Use your own picture anyway?')) {
          await send(Object.assign({}, ok, { flagged: true }));
        } else {
          p.status.textContent = 'Not saved. The title is still waiting on the worker.';
        }
        return;
      }
      if (r.status === 409 && d.reason === 'same_picture') {
        busy = false;
        if (confirm(d.message || 'This picture is already used for another title. Use it anyway?')) {
          await send(Object.assign({}, ok, { same: true }));
        } else {
          p.status.textContent = 'Not saved. Choose a different picture.';
        }
        return;
      }
      if (!r.ok) {
        p.status.textContent = 'Not saved: ' + (d.detail || r.status);
        return;
      }
      const bits = [];
      bits.push(d.replaced ? `Replaced ${d.replaced} picture(s)` : 'Picture added');
      if (d.unretired) bits.push('the title is back from retirement');
      bits.push(d.sent_to_painting ? 'sent to painting' : 'waiting for painting');
      busy = false;
      close();
      if (current.onDone) current.onDone(d, bits.join(' · ') + '.');
    } catch (e) {
      if (e && e.name === 'AbortError') {
        // We stopped waiting; the server may still have finished. Re-read
        // the title so what is on screen is the truth either way.
        p.status.textContent = `No answer from the server after ${WAIT_S} `
          + 'seconds. Your picture may still have been saved — the title '
          + 'has been refreshed behind this box, so check it before trying again.';
        if (current.onRefresh) {
          try { await current.onRefresh(); } catch (e2) { /* the message above stands */ }
        }
      } else {
        p.status.textContent = 'Not saved: ' + e.message;
      }
    } finally {
      // Every way out leaves the dialog usable again (rule 8: a busy state
      // is a claim, and needs an exit on every path).
      busy = false;
      if (box) {
        p.go.disabled = false;
        p.cancel.disabled = false;
      }
    }
  }

  window.AdminPick = { open, isOpen: () => !!(box && !box.hidden) };
})();
