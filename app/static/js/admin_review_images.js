/* Review Images — the admin's quality gate on AI output.
 *
 * DESIGN NOTES
 *
 * APPROVAL IS THE DEFAULT. Roughly 10 images in 500 come out badly, so the
 * screen is built around finding those ten — you skim, mark the exceptions,
 * and everything you did not touch is released. An earlier version made you
 * approve each title explicitly, which is 490 confirmations of "yes, fine".
 *
 * Decisions are held in memory and committed in ONE request at the end.
 * A round trip per keypress would make arrow-keying feel broken, and holding
 * them locally means you can go back three titles and change your mind — which
 * you will, once you've seen what "good" looks like across a batch.
 *
 * The unit is the TITLE, not the image: an artist's pair is judged together
 * because that is how a buyer sees them, and it is the only way to notice
 * "these two are the same photo with a different filter".
 *
 * Previews are 1200px, not the 4000px print files. Two of the latter per
 * screen is ~6 MB; nobody arrow-keys through 250 of those. Click an image to
 * open the full-resolution version when you actually want to look closely.
 */
(function () {
  'use strict';

  const API = '/admin/pipeline/api';
  const $ = (s) => document.querySelector(s);

  const picker  = $('[data-review-picker]');
  const stage   = $('[data-review-stage]');
  if (!picker || !stage) return;

  const datesBody = $('[data-review-dates]');
  const summary   = $('[data-review-summary]');
  const startEl   = $('[data-review-start]');
  const endEl     = $('[data-review-end]');

  let titles   = [];        // the range being reviewed
  let index    = 0;
  let decisions = new Map(); // processed_id -> {action, reason}
  let mode     = 'pending';

  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  // ── Picker ───────────────────────────────────────────────────────────────

  async function loadDates() {
    try {
      const r = await fetch(`${API}/review/dates`);
      const d = await r.json();
      const rows = d.dates || [];
      $('[data-rerun-count]').textContent = d.reruns || 0;

      if (!rows.length) {
        datesBody.innerHTML =
          '<tr><td colspan="4" class="muted">Nothing is waiting for review.</td></tr>';
        summary.textContent = '';
        return;
      }
      const totalTitles = rows.reduce((a, r2) => a + r2.titles, 0);
      const totalImages = rows.reduce((a, r2) => a + r2.images, 0);
      summary.textContent = `${totalTitles} titles · ${totalImages} images`;

      datesBody.innerHTML = rows.map((r2) => `
        <tr>
          <td class="mono">${esc(r2.date)}</td>
          <td class="mono">${r2.titles}</td>
          <td class="mono">${r2.images}</td>
          <td><button class="btn btn-ghost btn-tiny"
                      data-action="review-day" data-date="${esc(r2.date)}">REVIEW</button></td>
        </tr>`).join('');

      // Default the range to everything waiting — the common case is "clear
      // the backlog", and narrowing is easier than widening.
      if (!startEl.value) startEl.value = rows[rows.length - 1].date;
      if (!endEl.value)   endEl.value   = rows[0].date;
    } catch (e) {
      datesBody.innerHTML = `<tr><td colspan="4" class="error">Could not load: ${esc(e.message)}</td></tr>`;
    }
  }

  async function openRange(start, end, status) {
    mode = status || 'pending';
    const qs = new URLSearchParams({ status: mode });
    if (start) qs.set('start', start);
    if (end)   qs.set('end', end);

    const r = await fetch(`${API}/review/queue?${qs}`);
    const d = await r.json();
    titles = d.titles || [];
    // The dashboard's colour, so RESET goes back to what the
    // project is set to rather than to a number hardcoded here.
    defaultBackground = d.default_background || '#000000';
    colors.clear();
    if (!titles.length) { alert('Nothing to review in that range.'); return; }

    index = 0;
    decisions = new Map();
    picker.hidden = true;
    stage.hidden = false;
    $('[data-review-range]').textContent =
      mode === 'rerun' ? 'reruns' : `${start || 'start'} → ${end || 'today'}`;
    render();
  }

  // ── The reviewer ─────────────────────────────────────────────────────────

  function current() { return titles[index]; }

  // ── The colour chosen per image, held here until you commit ─────────────
  //
  // Keyed on processed_id, like the decisions. Nothing is sent until SAVE,
  // so dragging a colour picker costs no requests and you can change your
  // mind about a poster three titles back.
  const colors = new Map();
  let defaultBackground = '#000000';

  function colorFor(img) {
    return colors.get(img.processed_id)
        || img.background_color
        || defaultBackground;
  }

  document.addEventListener('input', (e) => {
    if (e.target.matches('[data-color-input]')) {
      setColor(Number(e.target.dataset.pid), e.target.value);
    }
  });

  // The eyedropper click. Capture phase, because the poster sits inside a
  // link to the full-size file — without this the browser opens that file
  // instead of sampling the pixel.
  document.addEventListener('click', (e) => {
    if (eyedropFor === null) return;
    const img = e.target.closest('[data-poster-img]');
    if (!img || Number(img.dataset.pid) !== eyedropFor) return;
    e.preventDefault();
    e.stopPropagation();
    const box = img.closest('[data-canvas]');
    const hex = sampleAt(img, box, e);
    if (hex) setColor(eyedropFor, hex);
    box.classList.remove('is-picking');
    eyedropFor = null;
  }, true);

  function render() {
    const t = current();
    if (!t) return;

    $('[data-review-title]').textContent = `${t.external_id ?? '–'}. ${t.title}`;
    $('[data-review-meta]').textContent = `saved ${t.date}`;
    $('[data-review-progress]').textContent = `${index + 1} / ${titles.length}`;

    $('[data-review-pair]').innerHTML = t.images.map((img) => {
      const d = decisions.get(img.processed_id);
      const state = d ? d.action : '';
      const bg = colorFor(img);

      // THE SOURCE PHOTOGRAPH, BESIDE THE POSTER.
      //
      // The one question this screen exists to answer is whether the model
      // painted the place the worker actually found, or wandered off and
      // invented a grander building of the same type. That cannot be judged
      // from the poster alone — you have to see what it was given.
      const source = `
        <figure class="review-img review-img-source">
          <a href="${img.source_url}" target="_blank" rel="noopener"
             title="Open the worker's photograph">
            <img loading="lazy" src="${img.source_url}" alt="">
          </a>
          <figcaption><span class="muted mono">what the worker found</span></figcaption>
        </figure>`;

      // THE POSTER, SITTING ON ITS COLOUR.
      //
      // When there is a transparent original we show THAT, on a coloured
      // box, and let the browser composite the two. The browser does exactly
      // the arithmetic the server does when flattening, so this preview is
      // the finished poster rather than an approximation of it — and it
      // updates the instant you change the colour, with no round trip.
      const shown = img.can_recolor ? img.master_url : img.preview_url;
      const colorBar = img.can_recolor ? `
          <div class="review-color" data-pid="${img.processed_id}">
            <span class="muted mono">background</span>
            <input type="color" value="${esc(bg)}" data-color-input
                   data-pid="${img.processed_id}" title="Pick a colour">
            <button class="btn btn-ghost btn-tiny" data-img-action="eyedrop"
                    data-pid="${img.processed_id}"
                    title="Click this, then click a colour in the poster">
              EYEDROPPER</button>
            <button class="btn btn-ghost btn-tiny" data-img-action="color-reset"
                    data-pid="${img.processed_id}">RESET</button>
            <span class="mono review-color-value">${esc(bg)}</span>
          </div>` : '';

      return `
        ${source}
        <figure class="review-img ${state ? 'is-' + state : ''}" data-pid="${img.processed_id}">
          <a href="${img.preview_url}?full=1" target="_blank" rel="noopener"
             title="Open the full-resolution file">
            <span class="review-canvas" data-canvas data-pid="${img.processed_id}"
                  style="background-color:${esc(bg)}">
              <img loading="lazy" src="${shown}" alt="" data-poster-img
                   data-pid="${img.processed_id}" crossorigin="anonymous">
            </span>
          </a>
          <figcaption>
            <span class="mono">${esc(img.filename)}</span>
            <span class="muted mono">${img.width || '?'}×${img.height || '?'}${img.attempt > 1 ? ' · attempt ' + img.attempt : ''}</span>
          </figcaption>
          ${colorBar}
          <div class="review-img-actions">
            <button class="btn btn-success btn-tiny" data-img-action="approve"  data-pid="${img.processed_id}">KEEP</button>
            <button class="btn btn-skip btn-tiny"    data-img-action="rerun"    data-pid="${img.processed_id}">RERUN</button>
            <button class="btn btn-error btn-tiny"   data-img-action="unusable" data-pid="${img.processed_id}">UNUSABLE</button>
          </div>
          ${state ? `<span class="review-img-state">${esc(state)}</span>` : ''}
        </figure>`;
    }).join('');

    updateTally();
  }

  // ── Changing the colour ────────────────────────────────────────────────
  //
  // Repainted in place rather than by re-rendering the whole title. A full
  // render would reload both images from the server on every nudge of the
  // colour picker, which makes dragging it feel broken.
  function setColor(pid, value) {
    colors.set(pid, value);
    const box = document.querySelector(`[data-canvas][data-pid="${pid}"]`);
    if (box) box.style.backgroundColor = value;
    const input = document.querySelector(`[data-color-input][data-pid="${pid}"]`);
    if (input && input.value !== value) input.value = value;
    const label = box && box.closest('figure')
        ? box.closest('figure').querySelector('.review-color-value') : null;
    if (label) label.textContent = value;
  }

  // ── The eyedropper ─────────────────────────────────────────────────────
  //
  // Samples the COMPOSITED poster, not the raw transparent file. Click the
  // Bangkok sky and you want the blue you can see — which is a
  // half-transparent pixel already sitting on the current background — not
  // the raw value hiding underneath, which is not what anybody is looking at.
  let eyedropFor = null;

  function startEyedrop(pid) {
    eyedropFor = pid;
    const box = document.querySelector(`[data-canvas][data-pid="${pid}"]`);
    if (box) box.classList.add('is-picking');
    toast('Click a colour in the poster.');
  }

  function sampleAt(imgEl, box, ev) {
    const rect = imgEl.getBoundingClientRect();
    const c = document.createElement('canvas');
    c.width = imgEl.naturalWidth || rect.width;
    c.height = imgEl.naturalHeight || rect.height;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    // Paint the background first, then the picture on top — the same order
    // the server flattens in, so the sampled pixel is the finished one.
    ctx.fillStyle = getComputedStyle(box).backgroundColor;
    ctx.fillRect(0, 0, c.width, c.height);
    try {
      ctx.drawImage(imgEl, 0, 0, c.width, c.height);
      const x = Math.floor((ev.clientX - rect.left) / rect.width * c.width);
      const y = Math.floor((ev.clientY - rect.top) / rect.height * c.height);
      const [r, g, b] = ctx.getImageData(x, y, 1, 1).data;
      const hex = '#' + [r, g, b].map(
          (v) => v.toString(16).padStart(2, '0')).join('');
      return hex;
    } catch (e) {
      // Reading pixels back needs the image to be same-origin. It is — both
      // come from this server — so this should not happen; if it ever does,
      // say so rather than silently doing nothing.
      toast('Could not read that pixel: ' + e.message, 'error');
      return null;
    }
  }

  function totalImages() {
    return titles.reduce((n, t) => n + t.images.length, 0);
  }

  function updateTally() {
    const marked = { rerun: 0, unusable: 0, approve: 0 };
    decisions.forEach((d) => { marked[d.action] = (marked[d.action] || 0) + 1; });
    // Everything not explicitly marked is approved on commit. Spelling that
    // out is the whole safety of an approve-by-default screen: you should be
    // able to read what is about to happen before you press the button.
    const approving = totalImages() - marked.rerun - marked.unusable;
    $('[data-review-tally]').textContent =
      `${approving} will be released · ${marked.rerun} rerun · ${marked.unusable} retired`;
  }

  function decide(pid, action, reason) {
    decisions.set(pid, { action, reason: reason || '' });
    render();
  }

  function clearTitleMarks() {
    const t = current();
    if (!t) return;
    t.images.forEach((img) => decisions.delete(img.processed_id));
    render();
  }

  function move(step) {
    const next = index + step;
    if (next < 0 || next >= titles.length) return;
    index = next;
    render();
  }

  // ── Events ───────────────────────────────────────────────────────────────

  document.addEventListener('click', async (e) => {
    const el = e.target.closest('[data-action], [data-img-action]');
    if (!el) return;

    const imgAction = el.dataset.imgAction;
    if (imgAction) {
      const pid = parseInt(el.dataset.pid, 10);
      // Pressing the same button again clears the mark, which puts the image
      // back into the approved majority. Without this, an accidental tap
      // could only be undone by discarding the whole session.
      const existing = decisions.get(pid);
      if (existing && existing.action === imgAction) {
        decisions.delete(pid);
        render();
        return;
      }

      if (imgAction === 'eyedrop') {
        startEyedrop(pid);
        return;
      }

      if (imgAction === 'color-reset') {
        setColor(pid, defaultBackground);
        return;
      }

      if (imgAction === 'unusable') {
        // The reason is mandatory server-side too. It is the only record of
        // why this image is out of the pipeline, read by someone (probably
        // you) years from now with no memory of the decision.
        const reason = prompt(
          'Why can this image never be used?\n\n' +
          'e.g. "AI merges her with the background every time"\n\n' +
          'This is kept permanently — the file and the worker\'s pay are not affected.');
        if (!reason || !reason.trim()) return;
        decide(pid, 'unusable', reason.trim());
      } else {
        decide(pid, imgAction);
      }
      return;
    }

    switch (el.dataset.action) {
      case 'review-day':
        await openRange(el.dataset.date, el.dataset.date, 'pending');
        break;
      case 'review-start':
        await openRange(startEl.value, endEl.value, 'pending');
        break;
      case 'review-reruns':
        await openRange('', '', 'rerun');
        break;
      case 'review-prev':    move(-1); break;
      case 'review-next':    move(1);  break;
      case 'review-clear-marks': clearTitleMarks(); break;
      case 'review-approve-all':
        if (!confirm(
            `Release everything in this range that you have not marked?\n\n`
            + `${totalImages() - decisions.size} images will go to the upload queue.`)) return;
        await commit();
        break;
      case 'review-exit':
        if (!confirm('Leave without saving? Nothing in this range will be released.')) return;
        stage.hidden = true; picker.hidden = false;
        await loadDates();
        break;
      case 'review-commit':  await commit(); break;
    }
  });

  document.addEventListener('keydown', (e) => {
    if (stage.hidden) return;
    if (e.target.matches('input, textarea, select')) return;
    if (e.key === 'ArrowRight') { move(1);  e.preventDefault(); }
    if (e.key === 'ArrowLeft')  { move(-1); e.preventDefault(); }
    if (e.key.toLowerCase() === 'c') { clearTitleMarks(); e.preventDefault(); }
  });

  async function commit() {
    const status = $('[data-review-commit-status]');
    if (!titles.length) { status.textContent = 'nothing loaded'; return; }
    status.textContent = 'saving…';

    // Send an explicit decision for EVERY image in the range — approvals for
    // the untouched ones included. The server never infers "approved" from
    // absence: a dropped request or a half-loaded page would otherwise
    // release work nobody looked at.
    const payload = { decisions: [] };
    titles.forEach((t) => t.images.forEach((img) => {
      const d = decisions.get(img.processed_id);
      payload.decisions.push({
        processed_id: img.processed_id,
        action: d ? d.action : 'approve',
        reason: d ? d.reason : '',
        // Sent on every image, not just the ones you touched. An untouched
        // poster still needs a colour recorded, and "the one showing on
        // screen" is exactly what you just approved by not changing it.
        background_color: img.can_recolor ? colorFor(img) : '',
      });
    }));

    try {
      const r = await fetch(`${API}/review/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (!r.ok) { status.textContent = d.detail || 'failed'; return; }
      status.textContent =
        `saved — ${d.approved} released, ${d.rerun} queued to regenerate, ${d.unusable} retired`;
      decisions = new Map();
      stage.hidden = true;
      picker.hidden = false;
      await loadDates();
    } catch (err) {
      status.textContent = 'failed: ' + err.message;
    }
  }

  loadDates();
})();
