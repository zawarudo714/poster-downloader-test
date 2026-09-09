/* Approve Artwork — the admin's quality gate on AI output.
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
 * ══════════════════════════════════════════════════════════════════════════
 * A DECISION BELONGS TO THE POSTER, NOT TO ONE GENERATION OF IT
 * ══════════════════════════════════════════════════════════════════════════
 * Reworked 2026-09-09 at the owner's request. A rerun no longer replaces the
 * picture — every generation is kept, with its own file, and this screen lets
 * you flip between them and settle on whichever one is best. So "keep",
 * "rerun" and "unusable" are recorded against the POSTER, and the chosen
 * version travels with the decision. Keying them on the generation instead
 * would mean marking v3 and then switching to v2 silently kept the mark on a
 * picture you had stopped looking at.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * EVERYTHING HAS A KEY, AND THE KEYS WORK IN THE ZOOM TOO
 * ══════════════════════════════════════════════════════════════════════════
 * The owner called the old screen unintuitive: the colour could only be
 * judged in the zoom view, and RERUN was mouse-only. So the colour controls
 * now sit in BOTH places and work the same way in each, and every decision
 * has a single key. The zoom is a bigger look at the same title, never a
 * different mode with different powers.
 *
 * Previews are 1200px, not the 4000px print files. Two of the latter per
 * screen is ~6 MB; nobody arrow-keys through 250 of those.
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
  let decisions = new Map(); // poster_id -> {action, reason}
  let chosen    = new Map(); // poster_id -> processed_id being looked at
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
      const waitEl = $('[data-review-waiting-count]');
      if (waitEl) {
        const totalImgs = (d.dates || []).reduce((n, r2) => n + (r2.images || 0), 0);
        waitEl.textContent = totalImgs ? `(${totalImgs})` : '(0)';
      }

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
    decisions = new Map();
    chosen = new Map();
    if (!titles.length) { alert('Nothing to review in that range.'); return; }

    index = 0;
    picker.hidden = true;
    stage.hidden = false;
    $('[data-review-range]').textContent =
      mode === 'rerun' ? 'reruns' : `${start || 'start'} → ${end || 'today'}`;
    render();
  }

  // ── The reviewer ─────────────────────────────────────────────────────────

  function current() { return titles[index]; }

  // WHICH GENERATION IS ON SCREEN for a poster. Defaults to the one the
  // queue handed us, which is the newest. Everything else on the screen —
  // the colour, the decision, the zoom — reads through here, so there is one
  // answer to "what am I looking at" rather than one per widget.
  function shownVersion(img) {
    const pid = chosen.get(img.poster_id);
    const list = img.versions && img.versions.length ? img.versions : [img];
    return list.find((v) => v.processed_id === pid) || list[0];
  }

  function versionsOf(img) {
    return (img.versions && img.versions.length) ? img.versions : [img];
  }

  // ── The colour chosen per generation, held here until you commit ────────
  //
  // Keyed on processed_id, because each generation has its own picture and
  // its own transparent master. Nothing is sent until SAVE, so dragging a
  // colour picker costs no requests.
  const colors = new Map();
  let defaultBackground = '#000000';

  function colorFor(v) {
    return colors.get(v.processed_id)
        || v.background_color
        || defaultBackground;
  }

  document.addEventListener('input', (e) => {
    if (e.target.matches('[data-color-input]')) {
      setColor(Number(e.target.dataset.pid), e.target.value);
    }
  });

  // The eyedropper click. Capture phase, because the poster sits inside a
  // link to the full-size file — without this the browser opens that file
  // instead of sampling the pixel. Works on the inline card AND in the zoom
  // overlay: both mark their picture with data-poster-img and sit inside a
  // plate marked data-canvas, so one handler serves both.
  document.addEventListener('click', (e) => {
    if (eyedropFor === null) return;
    const img = e.target.closest('[data-poster-img]');
    if (!img || Number(img.dataset.pid) !== eyedropFor) return;
    e.preventDefault();
    e.stopPropagation();
    const box = img.closest('[data-canvas]');
    const hex = sampleAt(img, box, e);
    if (hex) setColor(eyedropFor, hex);
    document.querySelectorAll('.is-picking').forEach(
      (el) => el.classList.remove('is-picking'));
    eyedropFor = null;
  }, true);

  // ── The colour bar, built once and used in both places ─────────────────
  function colorBarHtml(v, where) {
    if (!v.can_recolor) return '';
    const bg = colorFor(v);
    return `
      <div class="review-color" data-pid="${v.processed_id}" data-where="${where}">
        <span class="muted mono">background</span>
        <input type="color" value="${esc(bg)}" data-color-input
               data-pid="${v.processed_id}" title="Pick a colour">
        <button class="btn btn-ghost btn-tiny" data-img-action="eyedrop"
                data-pid="${v.processed_id}"
                title="Press E, then click a colour in the poster">
          EYEDROPPER <span class="mono">(E)</span></button>
        <button class="btn btn-ghost btn-tiny" data-img-action="color-reset"
                data-pid="${v.processed_id}">RESET</button>
        <span class="mono review-color-value">${esc(bg)}</span>
      </div>`;
  }

  // ── The version picker ─────────────────────────────────────────────────
  // Only drawn when there is actually a choice to make. One generation is
  // the normal case and a row of buttons reading "v1" would be furniture.
  function versionBarHtml(img) {
    const list = versionsOf(img);
    if (list.length < 2) return '';
    const showing = shownVersion(img);
    return `
      <div class="review-versions" data-versions="${img.poster_id}">
        <span class="muted mono">generations</span>
        ${list.map((v, i) => `
          <button class="btn btn-tiny ${v.processed_id === showing.processed_id
                    ? 'btn-accent' : 'btn-ghost'}"
                  data-pick-version="${v.processed_id}"
                  data-poster="${img.poster_id}"
                  title="${esc(v.filename)}${v.is_current ? ' · newest' : ''}">
            v${v.attempt}${i < 9 ? ` <span class="mono">(${i + 1})</span>` : ''}
          </button>`).join('')}
        <span class="muted">press 1-9, or V to step through</span>
      </div>`;
  }

  function render() {
    const t = current();
    if (!t) return;

    $('[data-review-title]').textContent = `${t.external_id ?? '–'}. ${t.title}`;
    $('[data-review-meta]').textContent = `saved ${t.date}`;
    $('[data-review-progress]').textContent = `${index + 1} / ${titles.length}`;

    $('[data-review-pair]').innerHTML = t.images.map((img) => {
      const d = decisions.get(img.poster_id);
      const state = d ? d.action : '';
      const v = shownVersion(img);
      const bg = colorFor(v);

      // THE SOURCE PHOTOGRAPH, BESIDE THE POSTER.
      //
      // The one question this screen exists to answer is whether the model
      // painted the place the worker actually found, or wandered off and
      // invented a grander building of the same type. That cannot be judged
      // from the poster alone — you have to see what it was given.
      const source = `
        <figure class="review-img review-img-source" data-zoom-open="${img.poster_id}"
                title="Click to compare side by side, full screen">
          <img loading="lazy" src="${img.source_url}" alt="">
          <figcaption><span class="muted mono">what the worker found · click to enlarge</span></figcaption>
        </figure>`;

      // THE POSTER, SITTING ON ITS COLOUR.
      //
      // When there is a transparent original we show THAT, on a coloured
      // box, and let the browser composite the two. The browser does exactly
      // the arithmetic the server does when flattening, so this preview is
      // the finished poster rather than an approximation of it — and it
      // updates the instant you change the colour, with no round trip.
      const shown = v.can_recolor ? v.master_url : v.preview_url;

      return `
        ${source}
        <figure class="review-img ${state ? 'is-' + state : ''}" data-pid="${v.processed_id}"
                data-zoom-open="${img.poster_id}"
                title="Click to compare side by side, full screen">
            <span class="review-canvas" data-canvas data-pid="${v.processed_id}"
                  style="background-color:${esc(bg)}">
              <img loading="lazy" src="${shown}" alt="" data-poster-img
                   data-pid="${v.processed_id}" crossorigin="anonymous">
            </span>
          <figcaption>
            <span class="mono">${esc(v.filename)}</span>
            <span class="muted mono">${v.width || '?'}×${v.height || '?'}${
              versionsOf(img).length > 1 ? ' · generation ' + (v.attempt || 1) : ''}</span>
          </figcaption>
          ${versionBarHtml(img)}
          ${colorBarHtml(v, 'card')}
          <div class="review-img-actions">
            <button class="btn btn-success btn-tiny" data-img-action="approve"  data-poster="${img.poster_id}">KEEP <span class="mono">(K)</span></button>
            <button class="btn btn-skip btn-tiny"    data-img-action="rerun"    data-poster="${img.poster_id}">RERUN <span class="mono">(R)</span></button>
            <button class="btn btn-error btn-tiny"   data-img-action="unusable" data-poster="${img.poster_id}">UNUSABLE <span class="mono">(U)</span></button>
          </div>
          ${state ? `<span class="review-img-state">${esc(state)}</span>` : ''}
        </figure>`;
    }).join('');

    probeTransparency();
    updateTally();
    if (zoomOpen) syncZoom();
  }

  // ── Changing the colour ────────────────────────────────────────────────
  //
  // Repainted in place rather than by re-rendering the whole title. A full
  // render would reload both images from the server on every nudge of the
  // colour picker, which makes dragging it feel broken. Every plate showing
  // this generation is repainted, so the card and the zoom can never drift.
  function setColor(pid, value) {
    colors.set(pid, value);
    document.querySelectorAll(`[data-canvas][data-pid="${pid}"]`).forEach((box) => {
      box.style.backgroundColor = value;
    });
    document.querySelectorAll(`[data-color-input][data-pid="${pid}"]`).forEach((input) => {
      if (input.value !== value) input.value = value;
    });
    document.querySelectorAll(`.review-color[data-pid="${pid}"] .review-color-value`)
      .forEach((label) => { label.textContent = value; });
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
    document.querySelectorAll(`[data-canvas][data-pid="${pid}"]`).forEach(
      (box) => box.classList.add('is-picking'));
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

  function decide(posterId, action, reason) {
    decisions.set(posterId, { action, reason: reason || '' });
    render();
  }

  // Pressing the same decision again clears it, which puts the poster back
  // into the approved majority. Without this, an accidental tap could only
  // be undone by discarding the whole session.
  function toggleDecision(posterId, action) {
    const existing = decisions.get(posterId);
    if (existing && existing.action === action) {
      decisions.delete(posterId);
      render();
      return;
    }
    if (action === 'unusable') {
      // The reason is mandatory server-side too. It is the only record of
      // why this image is out of the pipeline, read by someone (probably
      // you) years from now with no memory of the decision.
      const reason = prompt(
        'Why can this image never be used?\n\n' +
        'e.g. "AI merges her with the background every time"\n\n' +
        'This is kept permanently — the file and the worker\'s pay are not affected.');
      if (!reason || !reason.trim()) return;
      decide(posterId, 'unusable', reason.trim());
      return;
    }
    decide(posterId, action);
  }

  // Apply a decision to every poster of the title on screen. There is
  // normally one, but a title with a pair must not need two keystrokes when
  // one key is what the legend promises.
  function decideThisTitle(action) {
    const t = current();
    if (!t) return;
    t.images.forEach((img) => toggleDecision(img.poster_id, action));
  }

  function clearTitleMarks() {
    const t = current();
    if (!t) return;
    t.images.forEach((img) => decisions.delete(img.poster_id));
    render();
  }

  // ── Choosing a generation ──────────────────────────────────────────────
  function pickVersion(posterId, processedId) {
    chosen.set(posterId, processedId);
    render();
  }

  function pickVersionByNumber(n) {
    const t = current();
    if (!t || !t.images.length) return;
    const img = t.images[0];
    const list = versionsOf(img);
    if (n < 1 || n > list.length) return;
    pickVersion(img.poster_id, list[n - 1].processed_id);
  }

  function stepVersion() {
    const t = current();
    if (!t || !t.images.length) return;
    const img = t.images[0];
    const list = versionsOf(img);
    if (list.length < 2) return;
    const at = list.findIndex(
      (v) => v.processed_id === shownVersion(img).processed_id);
    pickVersion(img.poster_id, list[(at + 1) % list.length].processed_id);
  }

  function move(step) {
    const next = index + step;
    if (next < 0 || next >= titles.length) return;
    index = next;
    render();
  }

  // ── The compare overlay ──────────────────────────────────────────────
  // Both pictures at reading size, side by side, with the PLACE named at
  // the top and ← → stepping through titles without closing. The colour
  // controls and every decision key work here exactly as they do on the
  // card — the overlay is a bigger look, not a different mode.
  let zoomOpen = false;

  function syncZoom() {
    const t = current();
    if (!t || !t.images.length) { closeZoom(); return; }
    const img = t.images[0];
    const v = shownVersion(img);
    const box = $('[data-review-zoom]');
    $('[data-zoom-title]').textContent = `${t.external_id ?? '–'}. ${t.title}`;
    $('[data-zoom-pos]').textContent = `${index + 1} / ${titles.length}`;
    $('[data-zoom-source]').src = img.source_url;
    const poster = $('[data-zoom-poster]');
    poster.src = v.can_recolor ? v.master_url : v.preview_url;
    poster.dataset.pid = v.processed_id;
    const canvas = $('[data-zoom-canvas]');
    canvas.dataset.pid = v.processed_id;
    canvas.style.backgroundColor = colorFor(v);
    const d = decisions.get(img.poster_id);
    $('[data-zoom-state]').textContent = d ? d.action.toUpperCase() : '';
    $('[data-zoom-controls]').innerHTML =
      versionBarHtml(img) + colorBarHtml(v, 'zoom');
    box.hidden = false;
    zoomOpen = true;
  }

  function closeZoom() {
    const box = $('[data-review-zoom]');
    if (box) box.hidden = true;
    zoomOpen = false;
  }

  // ── The honest colour bar ────────────────────────────────────────────
  // A master can be RGBA and still fully opaque — gpt-image-2 sometimes
  // paints its own background. Offering a colour then is a dead knob (the
  // owner set red and nothing happened). After the master loads, its
  // pixels are probed; a fully opaque one swaps the bar for one plain
  // sentence. Server-side the same probe now runs at generation time, so
  // this mostly matters for images made before that fix.
  function probeTransparency() {
    document.querySelectorAll('[data-poster-img]').forEach((imgEl) => {
      if (imgEl.dataset.alphaProbed) return;
      const pid = parseInt(imgEl.dataset.pid, 10);
      const t = current();
      if (!t) return;
      let v = null;
      t.images.forEach((img) => {
        versionsOf(img).forEach((cand) => {
          if (cand.processed_id === pid) v = cand;
        });
      });
      if (!v || !v.can_recolor) return;
      const run = () => {
        imgEl.dataset.alphaProbed = '1';
        try {
          const c = document.createElement('canvas');
          const w = Math.min(imgEl.naturalWidth || 64, 256);
          const h = Math.min(imgEl.naturalHeight || 64, 256);
          c.width = w; c.height = h;
          const ctx = c.getContext('2d');
          ctx.drawImage(imgEl, 0, 0, w, h);
          const data = ctx.getImageData(0, 0, w, h).data;
          let transparent = false;
          for (let i2 = 3; i2 < data.length; i2 += 4) {
            if (data[i2] < 255) { transparent = true; break; }
          }
          if (!transparent) {
            document.querySelectorAll(
              `.review-color[data-pid="${pid}"]`).forEach((bar) => {
              bar.innerHTML = '<span class="review-color-dead">This artwork '
                + 'has no see-through areas, so a background colour cannot '
                + 'change it. New generations made with Background = '
                + 'transparent will.</span>';
            });
          }
        } catch (e2) { /* a probe must never break the review */ }
      };
      if (imgEl.complete && imgEl.naturalWidth) run();
      else imgEl.addEventListener('load', run, { once: true });
    });
  }

  // ── Events ───────────────────────────────────────────────────────────────

  document.addEventListener('click', async (e) => {
    const pick = e.target.closest('[data-pick-version]');
    if (pick) {
      pickVersion(parseInt(pick.dataset.poster, 10),
                  parseInt(pick.dataset.pickVersion, 10));
      return;
    }

    const el = e.target.closest('[data-action], [data-img-action]');
    if (!el) {
      // A click on either PICTURE opens the compare overlay — but never
      // while the eyedropper is armed (that click is picking a colour),
      // and never on the colour bar or the version buttons.
      const zoomEl = e.target.closest('[data-zoom-open]');
      if (zoomEl && !eyedropFor && !e.target.closest('.review-color')
          && !e.target.closest('.review-versions')) {
        syncZoom();
      }
      return;
    }

    const imgAction = el.dataset.imgAction;
    if (imgAction) {
      if (imgAction === 'eyedrop') {
        startEyedrop(parseInt(el.dataset.pid, 10));
        return;
      }
      if (imgAction === 'color-reset') {
        setColor(parseInt(el.dataset.pid, 10), defaultBackground);
        return;
      }
      toggleDecision(parseInt(el.dataset.poster, 10), imgAction);
      return;
    }

    switch (el.dataset.action) {
      case 'review-day':
        await openRange(el.dataset.date, el.dataset.date, 'pending');
        break;
      case 'review-all':
        await openRange('', '', 'pending');
        break;
      case 'review-start':
        await openRange(startEl.value, endEl.value, 'pending');
        break;
      case 'zoom-close':
        closeZoom();
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
        stage.hidden = true; picker.hidden = false; closeZoom();
        await loadDates();
        break;
      case 'review-commit':  await commit(); break;
    }
  });

  // ── THE KEYS ─────────────────────────────────────────────────────────
  // One key per decision, and they work whether or not the zoom is open.
  // The owner asked for a RERUN shortcut; giving only that one would have
  // left the screen half keyboard-driven, which is worse than neither.
  document.addEventListener('keydown', (e) => {
    if (stage.hidden) return;
    if (e.target.matches('input, textarea, select')) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    if (e.key === 'Escape') {
      if (eyedropFor !== null) {
        document.querySelectorAll('.is-picking').forEach(
          (el) => el.classList.remove('is-picking'));
        eyedropFor = null;
        e.preventDefault();
        return;
      }
      if (zoomOpen) { closeZoom(); e.preventDefault(); }
      return;
    }
    if (e.key === 'ArrowRight') { move(1);  e.preventDefault(); return; }
    if (e.key === 'ArrowLeft')  { move(-1); e.preventDefault(); return; }

    if (e.key >= '1' && e.key <= '9') {
      pickVersionByNumber(Number(e.key));
      e.preventDefault();
      return;
    }

    const t = current();
    switch (e.key.toLowerCase()) {
      case 'r': decideThisTitle('rerun');    e.preventDefault(); break;
      case 'k': decideThisTitle('approve');  e.preventDefault(); break;
      case 'u': decideThisTitle('unusable'); e.preventDefault(); break;
      case 'c': clearTitleMarks();           e.preventDefault(); break;
      case 'v': stepVersion();               e.preventDefault(); break;
      case 'z':
        if (zoomOpen) closeZoom(); else syncZoom();
        e.preventDefault();
        break;
      case 'e':
        if (t && t.images.length) {
          const v = shownVersion(t.images[0]);
          if (v.can_recolor) startEyedrop(v.processed_id);
        }
        e.preventDefault();
        break;
      default: break;
    }
  });

  async function commit() {
    const status = $('[data-review-commit-status]');
    if (!titles.length) { status.textContent = 'nothing loaded'; return; }
    status.textContent = 'saving…';

    // Send an explicit decision for EVERY poster in the range — approvals for
    // the untouched ones included. The server never infers "approved" from
    // absence: a dropped request or a half-loaded page would otherwise
    // release work nobody looked at.
    //
    // `processed_id` is the generation ON SCREEN, which is what makes
    // choosing v2 mean anything. The server moves is_current to whichever
    // one arrives here.
    const payload = { decisions: [] };
    titles.forEach((t) => t.images.forEach((img) => {
      const d = decisions.get(img.poster_id);
      const v = shownVersion(img);
      payload.decisions.push({
        processed_id: v.processed_id,
        action: d ? d.action : 'approve',
        reason: d ? d.reason : '',
        // Sent on every image, not just the ones you touched. An untouched
        // poster still needs a colour recorded, and "the one showing on
        // screen" is exactly what you just approved by not changing it.
        background_color: v.can_recolor ? colorFor(v) : '',
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
      chosen = new Map();
      closeZoom();
      stage.hidden = true;
      picker.hidden = false;
      await loadDates();
    } catch (err) {
      status.textContent = 'failed: ' + err.message;
    }
  }

  loadDates();
})();
