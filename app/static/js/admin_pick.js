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
    url.addEventListener('input', () => { if (url.value.trim()) file.value = ''; });
    file.addEventListener('change', () => { if (file.files.length) url.value = ''; });
    url.addEventListener('keydown', (e) => { if (e.key === 'Enter') send({}); });

    [urlLab, fileLab, whyLab, status, actions].forEach((n) => form.appendChild(n));
    [head, lead, form].forEach((n) => card.appendChild(n));
    box.appendChild(bg);
    box.appendChild(card);
    document.body.appendChild(box);

    box._parts = { name, url, file, why, status, go, cancel };
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
    box.hidden = false;
    p.url.focus();
  }

  function close() {
    if (busy || !box) return;      // never abandon a save half-way
    box.hidden = true;
    job = null;
  }

  // `ok` carries what has already been confirmed in this press, so a
  // second warning does not undo the answer to the first one.
  async function send(ok) {
    ok = ok || {};
    if (busy || !job) return;
    const p = box._parts;
    const link = p.url.value.trim();
    const chosen = p.file.files && p.file.files[0];
    if (!link && !chosen) {
      p.status.textContent = 'Paste a link or choose a file first.';
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
    p.status.textContent = chosen ? 'Sending your file…' : 'Fetching the picture…';
    const current = job;
    try {
      const r = await fetch(`/admin/title/${current.masterId}/admin_pick`,
                            { method: 'POST', body: fd });
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
      p.status.textContent = 'Not saved: ' + e.message;
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

  window.AdminPick = { open };
})();
