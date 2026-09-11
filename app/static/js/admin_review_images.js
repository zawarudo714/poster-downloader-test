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
    // From the settings, not written in here, so changing which key throws
    // the mark left is a box on a screen rather than a deploy.
    if (d.sig_keys) {
      sigKeys.left = d.sig_keys.left || ',';
      sigKeys.right = d.sig_keys.right || '.';
    }
    colors.clear();
    // Decisions come back from the last sitting rather than being wiped.
    // Pruned to the range just loaded, so the store cannot grow for ever
    // with posters that were released weeks ago.
    const kept = loadDecisions();
    const inRange = new Set();
    titles.forEach((t) => t.images.forEach((img) => inRange.add(img.poster_id)));
    decisions = new Map();
    kept.forEach((d, posterId) => {
      if (inRange.has(posterId)) decisions.set(posterId, d);
    });
    saveDecisions();
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

  // ══════════════════════════════════════════════════════════════════════
  //  KEEPING WHAT YOU DID — asked for on 2026-09-09
  // ══════════════════════════════════════════════════════════════════════
  //
  // Every tweak used to live only in this file's memory until SAVE & RELEASE.
  // Closing the tab, following a link, or a reload threw away everything
  // adjusted since. The owner will be doing hundreds of images in a sitting,
  // so one mis-click costing an afternoon of nudging is not acceptable.
  //
  // TWO KINDS OF THING, KEPT IN TWO PLACES, AND THE SPLIT IS DELIBERATE:
  //
  //   · A TWEAK — the colour, and where the signature sits — belongs to the
  //     POSTER. It goes to the server, onto the row, where it survives a
  //     different browser and a different day. The row already has columns
  //     for exactly this.
  //   · A DECISION — keep, rerun, unusable — is unsent INTENT. Storing it on
  //     the server would invent a "decided but not released" state that the
  //     greenlight query, the funnel counts and the worker machine all know
  //     nothing about. It stays in this browser instead.
  //
  // The tweak save is DEBOUNCED, because dragging a slider fires on every
  // pixel and each one would otherwise be a request.

  const REMEMBER_AFTER_MS = 400;
  const rememberTimers = new Map();

  function rememberSoon(pid) {
    clearTimeout(rememberTimers.get(pid));
    rememberTimers.set(pid, setTimeout(() => rememberNow(pid), REMEMBER_AFTER_MS));
  }

  async function rememberNow(pid) {
    rememberTimers.delete(pid);
    const t = current();
    if (!t) return;
    let v = null;
    t.images.forEach((img) => versionsOf(img).forEach((cand) => {
      if (cand.processed_id === pid) v = cand;
    }));
    if (!v) return;
    const body = { processed_id: pid };
    if (v.signature) body.signature = sigFor(v);
    if (colors.has(pid)) body.background_color = colors.get(pid);
    try {
      const r = await fetch(API + '/review/remember', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      // SAY SO WHEN IT FAILS. A silent autosave that has stopped working
      // looks exactly like one that is working, and he would only find out
      // by losing an afternoon of tweaks.
      if (!r.ok) {
        toast('Could not save that adjustment — it will be lost if you '
              + 'leave this page. ' + (await r.text()).slice(0, 120), 'error');
      }
    } catch (err) {
      toast('Could not save that adjustment: ' + err.message, 'error');
    }
  }

  // The decisions, kept in this browser so a stray click cannot wipe an
  // afternoon. Written on every change and read back when the screen loads.
  const DECISIONS_KEY = 'pd_review_decisions_v1';

  function saveDecisions() {
    try {
      const flat = [];
      decisions.forEach((d, posterId) => flat.push([posterId, d]));
      localStorage.setItem(DECISIONS_KEY, JSON.stringify(flat));
    } catch (e) { /* a full or blocked store must never break the screen */ }
  }

  function loadDecisions() {
    try {
      const flat = JSON.parse(localStorage.getItem(DECISIONS_KEY) || '[]');
      const out = new Map();
      flat.forEach(([posterId, d]) => out.set(posterId, d));
      return out;
    } catch (e) { return new Map(); }
  }

  // ══════════════════════════════════════════════════════════════════════
  //  THE SIGNATURE
  // ══════════════════════════════════════════════════════════════════════
  //
  // Held in memory like the colour, and for the same reason: dragging it
  // must cost no requests, and you must be able to change your mind about a
  // poster three titles back. Nothing is sent until SAVE.
  //
  // THE PREVIEW IS THE REAL ARITHMETIC, not a picture of it. The mark is an
  // ordinary <img> positioned by percentage over the poster, at the same
  // opacity and the same colour the server will use — so what you drag is
  // what gets painted. Drawing it any other way would be a second copy of
  // the placement rule, and the two would drift.
  const sigs = new Map();          // processed_id -> {x_pct, w_pct, opacity, dark}

  function sigFor(v) {
    if (!v.signature) return null;
    return sigs.get(v.processed_id) || {
      x_pct: v.signature.x_pct,
      y_pct: v.signature.y_pct,
      w_pct: v.signature.w_pct,
      opacity: v.signature.opacity,
      dark: !!v.signature.dark,
    };
  }

  // How tall the mark is, as a percentage of the poster's WIDTH. Read off the
  // loaded mark rather than assumed, because it depends on the file the owner
  // uploaded. Needed to clamp the vertical slider: the mark's TOP must stay
  // inside the margin too, and the top is the bottom plus this.
  function markHeightPct(pid, wPct) {
    const el = document.querySelector(`[data-sig-mark][data-pid="${pid}"]`);
    if (!el || !el.naturalWidth) return 0;
    return wPct * (el.naturalHeight / el.naturalWidth);
  }

  function setSig(pid, patch) {
    const t = current();
    if (!t) return;
    let v = null;
    t.images.forEach((img) => versionsOf(img).forEach((cand) => {
      if (cand.processed_id === pid) v = cand;
    }));
    if (!v || !v.signature) return;
    const now = Object.assign({}, sigFor(v), patch);
    // The margin is a promise, not a suggestion — the same clamp the server
    // applies, so the preview can never show a position the file will not
    // have. Written once here and once there is unavoidable (one is
    // JavaScript and one is Python); keeping the numbers identical is what
    // the shared `margin_pct` is for.
    const m = v.signature.margin_pct;
    now.w_pct = Math.max(2, Math.min(60, now.w_pct));
    const half = now.w_pct / 2;
    now.x_pct = Math.max(m + half, Math.min(100 - m - half, now.x_pct));
    now.opacity = Math.max(0, Math.min(100, now.opacity));

    // THE VERTICAL LIMIT NEEDS THE POSTER'S SHAPE, which the mark alone does
    // not know. `y_pct` is a gap in percent-of-WIDTH, so the tallest it can
    // be is the poster's height in those same units, less the margin and the
    // mark's own height. On a 4000x6000 poster the height is 150% of the
    // width, which is why this cannot be a plain 100.
    const shape = posterShape(pid);       // height ÷ width, or 0 if unknown
    const hPct = markHeightPct(pid, now.w_pct);
    const ceiling = shape ? (shape * 100) - m - hPct : 100;
    now.y_pct = Math.max(m, Math.min(Math.max(m, ceiling), now.y_pct));

    sigs.set(pid, now);
    paintSig(pid);
    rememberSoon(pid);
  }

  // The poster's height divided by its width, from the picture on screen.
  // Read from the loaded image rather than from the stored width and height,
  // for the same reason the signature layer is measured rather than assumed:
  // the picture is the thing the server measures too.
  function posterShape(pid) {
    const el = document.querySelector(`[data-poster-img][data-pid="${pid}"]`);
    if (!el || !el.naturalWidth) return 0;
    return el.naturalHeight / el.naturalWidth;
  }

  // FULLY LEFT and FULLY RIGHT. Asked for on 2026-09-09: nearly every poster
  // wants the mark hard against one side or the other, and neither is worth
  // aiming a slider at.
  //
  // These pass 0 and 100 and let the clamp above decide where that lands.
  // Working out the limit here as well would be a second copy of the margin
  // rule, and the two would drift the first time the margin changed.
  function throwSignature(pid, side) {
    setSig(pid, { x_pct: side === 'left' ? 0 : 100 });
  }

  // ── THE BOX A PERCENTAGE IS MEASURED AGAINST ────────────────────────────
  //
  // The server places the mark against the PICTURE — `W, H = img.size` in
  // app/signature.py. The preview used to place it against the PLATE, which
  // is wider than the picture on a card and taller than it in the zoom. So
  // the mark sat outside the artwork, and came out a different size in the
  // two views (owner, 2026-09-09).
  //
  // `.sig-layer` is pinned to the poster's rendered box so a percentage here
  // means what it means on the server. It is absolutely positioned, so it
  // adds nothing to layout and cannot feed back into the size it measures.
  function fitSigLayer(imgEl) {
    if (!imgEl) return;
    const plate = imgEl.closest('[data-canvas]');
    if (!plate) return;
    const layer = plate.querySelector('[data-sig-layer]');
    if (!layer) return;
    const pr = plate.getBoundingClientRect();
    const ir = imgEl.getBoundingClientRect();
    if (!ir.width || !ir.height) return;   // not laid out yet; a load or a
                                           // resize will call this again
    layer.style.left = (ir.left - pr.left) + 'px';
    layer.style.top = (ir.top - pr.top) + 'px';
    layer.style.width = ir.width + 'px';
    layer.style.height = ir.height + 'px';
    const pid = Number(imgEl.dataset.pid);
    if (pid) paintSig(pid);      // the bottom margin is in pixels of THIS box
  }

  // Every poster on screen, in both views. Called after a render, after the
  // overlay syncs, when a picture finishes loading, and on resize — a box
  // measured once is a box that is wrong the moment the window moves.
  function fitAllSigLayers() {
    document.querySelectorAll('[data-poster-img]').forEach((imgEl) => {
      if (imgEl.complete && imgEl.naturalWidth) fitSigLayer(imgEl);
      else imgEl.addEventListener('load', () => fitSigLayer(imgEl), { once: true });
    });
  }

  window.addEventListener('resize', fitAllSigLayers);

  // Repaint in place rather than re-render, so dragging stays smooth and
  // the poster image is not refetched on every mouse move.
  function paintSig(pid) {
    const t = current();
    if (!t) return;
    let v = null;
    t.images.forEach((img) => versionsOf(img).forEach((cand) => {
      if (cand.processed_id === pid) v = cand;
    }));
    if (!v || !v.signature) return;
    const s = sigFor(v);
    document.querySelectorAll(`[data-sig-mark][data-pid="${pid}"]`).forEach((el) => {
      el.style.width = s.w_pct + '%';
      el.style.left = s.x_pct + '%';
      // THE GAP UP FROM THE BOTTOM IS A PERCENTAGE OF THE *WIDTH*, because
      // that is what the server does: `gap = W * y_pct` and then
      // `top = H - gap - mark_height`, in app/signature.py. A CSS percentage
      // on `bottom` resolves against the container's HEIGHT instead, so on a
      // 4000x6000 poster the preview showed the mark 30 pixels up where the
      // file puts it at 20 — half again too far, and wrong by a different
      // amount for every shape of poster.
      const layer = el.closest('[data-sig-layer]');
      const w = layer ? layer.getBoundingClientRect().width : 0;
      el.style.bottom = w
        ? (w * s.y_pct / 100) + 'px'
        : s.y_pct + '%';                  // pre-layout; fitSigLayer redoes it
      el.style.opacity = String(s.opacity / 100);
      // The file is white strokes. `invert` is how the same file becomes the
      // black version, which is exactly what the server does by rebuilding
      // the colour from the file's transparency.
      el.style.filter = s.dark ? 'invert(1)' : 'none';
    });
    document.querySelectorAll(`[data-sig-x][data-pid="${pid}"]`).forEach(
      (el) => { el.value = String(Math.round(s.x_pct * 10) / 10); });
    document.querySelectorAll(`[data-sig-y][data-pid="${pid}"]`).forEach(
      (el) => { el.value = String(Math.round(s.y_pct * 10) / 10); });
    document.querySelectorAll(`[data-sig-w][data-pid="${pid}"]`).forEach(
      (el) => { el.value = String(Math.round(s.w_pct * 10) / 10); });
    document.querySelectorAll(`[data-sig-o][data-pid="${pid}"]`).forEach(
      (el) => { el.value = String(Math.round(s.opacity)); });
    document.querySelectorAll(`[data-sig-readout][data-pid="${pid}"]`).forEach(
      (el) => {
        el.textContent = `${Math.round(s.w_pct * 10) / 10}% wide · `
          + `${Math.round(s.opacity)}% solid · ${s.dark ? 'black' : 'white'}`;
      });
  }

  function flipSignature(pid) {
    const t = current();
    if (!t) return;
    let v = null;
    t.images.forEach((img) => versionsOf(img).forEach((cand) => {
      if (cand.processed_id === pid) v = cand;
    }));
    if (!v || !v.signature) return;
    setSig(pid, { dark: !sigFor(v).dark });
    render();                    // the button's own word has to change too
  }

  // Back to the project's defaults for THIS poster. Read from what the
  // server sent rather than from numbers repeated here, so changing the
  // default on the Settings page changes what RESET means.
  function resetSignature(pid) {
    const t = current();
    if (!t) return;
    let v = null;
    t.images.forEach((img) => versionsOf(img).forEach((cand) => {
      if (cand.processed_id === pid) v = cand;
    }));
    if (!v || !v.signature) return;
    sigs.delete(pid);
    render();
  }

  function sigMarkHtml(v) {
    if (!v.signature) return '';
    const s = sigFor(v);
    return `<img class="sig-mark" data-sig-mark data-pid="${v.processed_id}"
                 src="${esc(v.signature.url)}" alt="" draggable="false"
                 style="width:${s.w_pct}%;left:${s.x_pct}%;`
         + `bottom:${v.signature.margin_pct}%;opacity:${s.opacity / 100};`
         + `filter:${s.dark ? 'invert(1)' : 'none'}">`;
  }

  function sigBarHtml(v, where) {
    if (!v.signature) return '';
    const s = sigFor(v);
    return `
      <div class="review-sig" data-pid="${v.processed_id}" data-where="${where}">
        <span class="muted mono">signature</span>
        <button class="btn btn-ghost btn-tiny" data-img-action="sig-left"
                data-pid="${v.processed_id}"
                title="Throw the mark as far left as the gap allows">
          ◀ FAR LEFT <span class="mono">(${esc(sigKeys.left)})</span></button>
        <label class="sig-field">across
          <input type="range" min="0" max="100" step="0.1"
                 data-sig-x data-pid="${v.processed_id}" value="${s.x_pct}"></label>
        <button class="btn btn-ghost btn-tiny" data-img-action="sig-right"
                data-pid="${v.processed_id}"
                title="Throw the mark as far right as the gap allows">
          FAR RIGHT ▶ <span class="mono">(${esc(sigKeys.right)})</span></button>
        <label class="sig-field">height
          <input type="range" min="0" max="100" step="0.1"
                 data-sig-y data-pid="${v.processed_id}" value="${s.y_pct}"
                 title="How far up from the bottom of the picture the mark sits"></label>
        <label class="sig-field">size
          <input type="range" min="2" max="60" step="0.1"
                 data-sig-w data-pid="${v.processed_id}" value="${s.w_pct}"></label>
        <label class="sig-field">solid
          <input type="range" min="0" max="100" step="1"
                 data-sig-o data-pid="${v.processed_id}" value="${s.opacity}"></label>
        <button class="btn btn-ghost btn-tiny" data-img-action="sig-flip"
                data-pid="${v.processed_id}"
                title="White reads on a dark picture, black on a pale one">
          ${s.dark ? 'BLACK' : 'WHITE'} <span class="mono">(B)</span></button>
        <button class="btn btn-ghost btn-tiny" data-img-action="sig-reset"
                data-pid="${v.processed_id}">RESET</button>
        <span class="mono muted" data-sig-readout data-pid="${v.processed_id}"></span>
      </div>`;
  }

  // DRAG ALONG THE X AXIS ONLY. The owner asked for exactly that: the bottom
  // margin is a fixed promise and the only thing that ever needs moving is
  // which side of the poster the mark sits on.
  (function wireDrag() {
    let dragging = null;
    function xPctFrom(ev, plate) {
      const r = plate.getBoundingClientRect();
      const clientX = ev.touches ? ev.touches[0].clientX : ev.clientX;
      return ((clientX - r.left) / r.width) * 100;
    }
    function start(e) {
      const mark = e.target.closest('[data-sig-mark]');
      if (!mark) return;
      // Measured against the SIGNATURE LAYER, which is the picture's box.
      // Dragging against the plate meant the pointer and the mark moved at
      // different rates, because the plate is wider than the artwork.
      const plate = mark.closest('[data-sig-layer]');
      if (!plate) return;
      dragging = { pid: Number(mark.dataset.pid), plate: plate };
      mark.classList.add('is-dragging');
      e.preventDefault();
    }
    function move(e) {
      if (!dragging) return;
      setSig(dragging.pid, { x_pct: xPctFrom(e, dragging.plate) });
      e.preventDefault();
    }
    function end() {
      if (!dragging) return;
      document.querySelectorAll('.sig-mark.is-dragging').forEach(
        (el) => el.classList.remove('is-dragging'));
      dragging = null;
    }
    document.addEventListener('mousedown', start);
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', end);
    document.addEventListener('touchstart', start, { passive: false });
    document.addEventListener('touchmove', move, { passive: false });
    document.addEventListener('touchend', end);
  })();

  document.addEventListener('input', (e) => {
    const t = e.target;
    if (!t.dataset || !t.dataset.pid) return;
    const pid = Number(t.dataset.pid);
    if (t.hasAttribute('data-sig-x')) setSig(pid, { x_pct: Number(t.value) });
    else if (t.hasAttribute('data-sig-y')) setSig(pid, { y_pct: Number(t.value) });
    else if (t.hasAttribute('data-sig-w')) setSig(pid, { w_pct: Number(t.value) });
    else if (t.hasAttribute('data-sig-o')) setSig(pid, { opacity: Number(t.value) });
  });

  // ── The colour chosen per generation, held here until you commit ────────
  //
  // Keyed on processed_id, because each generation has its own picture and
  // its own transparent master. Nothing is sent until SAVE, so dragging a
  // colour picker costs no requests.
  const colors = new Map();
  let defaultBackground = '#000000';
  // Overwritten from the server on every load. The values here are only what
  // the buttons say in the instant before the first reply arrives.
  const sigKeys = { left: ',', right: '.' };

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
    const hex = sampleAt(img, e);
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
                title="Press E, then click a colour in the picture">
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
    // The subject drawing and word — the same strip the worker saw while
    // choosing the photograph, so the judge has the same context.
    $('[data-review-kind]').innerHTML = window.SubjectKind.chip(t.kind);
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
        <figure class="review-img review-img-source">
          <img loading="lazy" src="${img.source_url}" alt=""
               data-zoom-open="${img.poster_id}"
               title="Click to compare side by side, full screen">
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
        <figure class="review-img ${state ? 'is-' + state : ''}" data-pid="${v.processed_id}">
            <span class="review-canvas" data-canvas data-pid="${v.processed_id}"
                  data-zoom-open="${img.poster_id}"
                  title="Click to compare side by side, full screen">
              <img loading="lazy" src="${shown}" alt="" data-poster-img
                   data-pid="${v.processed_id}" crossorigin="anonymous"
                   style="background-color:${esc(bg)}">
              <span class="sig-layer" data-sig-layer data-pid="${v.processed_id}"
                    >${sigMarkHtml(v)}</span>
            </span>
          <figcaption>
            <span class="mono">${esc(v.filename)}</span>
            <span class="muted mono">${v.width || '?'}×${v.height || '?'}${
              versionsOf(img).length > 1 ? ' · generation ' + (v.attempt || 1) : ''}</span>
          </figcaption>
          ${versionBarHtml(img)}
          ${colorBarHtml(v, 'card')}
          ${sigBarHtml(v, 'card')}
          <div class="review-img-actions">
            <button class="btn btn-success btn-tiny" data-img-action="approve"  data-poster="${img.poster_id}">KEEP <span class="mono">(K)</span></button>
            <button class="btn btn-skip btn-tiny"    data-img-action="rerun"    data-poster="${img.poster_id}">RERUN <span class="mono">(R)</span></button>
            <button class="btn btn-error btn-tiny"   data-img-action="unusable" data-poster="${img.poster_id}">UNUSABLE <span class="mono">(U)</span></button>
          </div>
          ${state ? `<span class="review-img-state">${esc(state)}</span>` : ''}
        </figure>`;
    }).join('');

    probeTransparency();
    fitAllSigLayers();
    // ONE place, so a new way of changing a decision cannot forget to save.
    // Every path that changes `decisions` already ends in render(); making
    // this the hook derives the save rather than asking each caller to
    // remember it — the same preference as the quiet window being a window.
    saveDecisions();
    // The sliders and the readout are filled in AFTER the markup exists.
    // Setting them from the template string would mean writing the same
    // numbers twice, and one of the two copies always goes stale.
    t.images.forEach((img) => {
      const v = shownVersion(img);
      if (v.signature) paintSig(v.processed_id);
    });
    updateTally();
    if (zoomOpen) syncZoom();
    // Lock stepping until this title's poster is on screen — see move().
    armAdvanceGate();
  }

  // ── Changing the colour ────────────────────────────────────────────────
  //
  // Repainted in place rather than by re-rendering the whole title. A full
  // render would reload both images from the server on every nudge of the
  // colour picker, which makes dragging it feel broken. Every plate showing
  // this generation is repainted, so the card and the zoom can never drift.
  function setColor(pid, value) {
    colors.set(pid, value);
    rememberSoon(pid);          // kept on the row, not just in this tab
    // ON THE PICTURE, NOT ON THE PLATE. The plate is wider than the artwork
    // on the card and taller than it in the zoom, so a colour painted there
    // showed as bars beside the poster that are not in the finished file
    // (owner, 2026-09-09). An image's background paints its own box and sits
    // behind its see-through pixels, which is both the right composite and
    // the right shape.
    document.querySelectorAll(`[data-poster-img][data-pid="${pid}"]`).forEach((el) => {
      el.style.backgroundColor = value;
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
    toast('Click a colour in the picture.');
  }

  // `box` (the plate) used to be passed in for its background colour and is
  // gone rather than left as an ignored argument — a parameter nothing reads
  // is a false clue about where the colour lives.
  function sampleAt(imgEl, ev) {
    const rect = imgEl.getBoundingClientRect();
    const c = document.createElement('canvas');
    c.width = imgEl.naturalWidth || rect.width;
    c.height = imgEl.naturalHeight || rect.height;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    // Paint the background first, then the picture on top — the same order
    // the server flattens in, so the sampled pixel is the finished one.
    // The colour is read off the PICTURE, because that is where it is
    // painted. `box` is still the plate and is no longer the thing carrying
    // the colour, so reading it here would have sampled a transparent
    // background and every eyedropper pick would have come back black.
    ctx.fillStyle = getComputedStyle(imgEl).backgroundColor;
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

  // HOW MANY WILL ACTUALLY BE RELEASED. Not `totalImages() - decisions.size`,
  // which is what it used to be: KEEP records a decision of 'approve', so
  // every KEEP was being subtracted from the number about to be released.
  // Press KEEP on three and RERUN on one and the button said four fewer when
  // the true answer was one. Counted from what the decisions SAY rather than
  // from how many there are.
  function releasedCount() {
    let out = totalImages();
    decisions.forEach((d) => {
      if (d.action === 'rerun' || d.action === 'unusable') out -= 1;
    });
    return out;
  }

  function updateTally() {
    const marked = { rerun: 0, unusable: 0, approve: 0 };
    decisions.forEach((d) => { marked[d.action] = (marked[d.action] || 0) + 1; });
    // Everything not explicitly marked is approved on commit. Spelling that
    // out is the whole safety of an approve-by-default screen: you should be
    // able to read what is about to happen before you press the button.
    const approving = releasedCount();
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

  // ── HOLD THE STEP UNTIL THE POSTER IS ON SCREEN ──────────────────────
  // The worker photograph loads in a blink; the painted poster is heavier
  // and lags. A fast second NEXT would swap the fast half and carry you past
  // a title whose poster you never actually saw (owner's find, 2026-09-11).
  // So stepping is locked from the moment you move until the NEW title's
  // poster has loaded. The lock ALWAYS clears — on load, on a broken image,
  // or after a safety timeout — so a poster that never arrives can never
  // trap the reviewer (rule 8: every busy state needs every exit).
  let advanceLock = false;
  let advanceTimer = null;

  function armAdvanceGate() {
    const img = document.querySelector('[data-review-pair] [data-poster-img]');
    const hint = $('[data-poster-loading]');
    clearTimeout(advanceTimer);
    // Already there (cached), or nothing to wait for: no lock.
    if (!img || (img.complete && img.naturalWidth)) {
      advanceLock = false;
      if (hint) hint.hidden = true;
      return;
    }
    advanceLock = true;
    if (hint) hint.hidden = false;
    const release = () => {
      advanceLock = false;
      clearTimeout(advanceTimer);
      if (hint) hint.hidden = true;
    };
    img.addEventListener('load', release, { once: true });
    img.addEventListener('error', release, { once: true });
    advanceTimer = setTimeout(release, 4000);   // never trap the reviewer
  }

  function move(step) {
    // A second press while the poster is still loading is ignored, so you
    // cannot skip a title you have not seen. Deciding (KEEP/RERUN) is not
    // affected — only stepping between titles waits.
    if (advanceLock) return;
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
    // The colour goes on the picture, not on this box — see setColor().
    poster.style.backgroundColor = colorFor(v);
    // Scoped to the overlay. `$` takes ONE argument, so `$(sel, canvas)`
    // would have quietly returned the first card's layer instead.
    const zoomLayer = canvas.querySelector('[data-sig-layer]');
    if (zoomLayer) zoomLayer.dataset.pid = String(v.processed_id);
    // The big view carries the mark as well, because judging whether a
    // signature sits well is exactly what a bigger picture is for.
    const zoomMark = $('[data-zoom-sig]');
    if (zoomMark) {
      if (v.signature) {
        zoomMark.hidden = false;
        zoomMark.src = v.signature.url;
        zoomMark.dataset.pid = String(v.processed_id);
        zoomMark.setAttribute('data-sig-mark', '');
      } else {
        zoomMark.hidden = true;
        zoomMark.removeAttribute('data-sig-mark');
      }
    }
    const d = decisions.get(img.poster_id);
    $('[data-zoom-state]').textContent = d ? d.action.toUpperCase() : '';
    $('[data-zoom-controls]').innerHTML =
      versionBarHtml(img) + colorBarHtml(v, 'zoom') + sigBarHtml(v, 'zoom');
    box.hidden = false;
    zoomOpen = true;
    // AFTER the overlay is shown, never before. A hidden element has no size,
    // so measuring the poster while `box.hidden` was still true would have
    // pinned the layer to a zero-sized box and the mark would not appear at
    // all. This is the same trap as the busy-state exit: the ordering only
    // matters on the path where everything is working.
    if (v.signature) paintSig(v.processed_id);
    fitAllSigLayers();
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
      // A click on a PICTURE opens the compare overlay. `data-zoom-open` now
      // sits on the pictures themselves rather than on the whole card, so
      // there is no list of things to exclude.
      //
      // It used to sit on the card, with `.review-color` and
      // `.review-versions` named as exceptions. The signature bar was added
      // later and nobody added it to that list, so every nudge of a slider
      // threw the overlay open (owner, 2026-09-09). A rule that carries its
      // own exceptions is one somebody has to remember to extend; deriving
      // the scope from where the picture actually is cannot rot.
      //
      // The eyedropper still wins, because that click is picking a colour.
      if (e.target.closest('[data-zoom-open]') && !eyedropFor) syncZoom();
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
      if (imgAction === 'sig-left' || imgAction === 'sig-right') {
        throwSignature(parseInt(el.dataset.pid, 10),
                       imgAction === 'sig-left' ? 'left' : 'right');
        return;
      }
      if (imgAction === 'sig-flip') {
        flipSignature(parseInt(el.dataset.pid, 10));
        return;
      }
      if (imgAction === 'sig-reset') {
        resetSignature(parseInt(el.dataset.pid, 10));
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
            + `${releasedCount()} images will go to the upload queue.`)) return;
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

    // THROW THE MARK TO ONE SIDE. Checked BEFORE the fixed letter keys
    // below, because these two are editable and he may well set one of them
    // to a letter. Handled first, the setting always wins; handled after,
    // setting the left key to "k" would silently keep approving instead.
    if (e.key === sigKeys.left || e.key === sigKeys.right) {
      const cur = current();
      if (cur) {
        cur.images.forEach((img) => {
          const v = shownVersion(img);
          if (v && v.signature) {
            throwSignature(v.processed_id,
                           e.key === sigKeys.left ? 'left' : 'right');
          }
        });
        e.preventDefault();
        return;
      }
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
      case 'b':
        if (t && t.images.length) flipSignature(shownVersion(t.images[0]).processed_id);
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
        // Where this poster's mark goes. Sent on every image for the same
        // reason the colour is: an untouched poster still needs a placement
        // recorded, and "the one on screen" is what you approved by not
        // moving it.
        signature: v.signature ? sigFor(v) : null,
      });
    }));

    // ── SENT IN CHUNKS, SO THE PROGRESS IS REAL ────────────────────────
    //
    // Approving is not instant and now it is honest about why: for a gated
    // project this is where the 4000-pixel print file is BUILT, with the
    // colour and the signature in one encode, and then uploaded to the
    // Storage Box. That is a second or two of work plus an upload, per
    // poster (MEASURED 2026-09-09: 1.5s of image work on this machine).
    //
    // The counter is the browser's own — it knows how many it has sent and
    // had answered — rather than a guess at a percentage. A progress bar
    // that is not counting anything real is the thing the owner explicitly
    // did not want.
    const CHUNK = 5;
    const all = payload.decisions;
    const tally = { approved: 0, rerun: 0, unusable: 0, files_removed: 0 };
    try {
      for (let i = 0; i < all.length; i += CHUNK) {
        const part = all.slice(i, i + CHUNK);
        status.textContent =
          `saving ${Math.min(i + part.length, all.length)} of ${all.length}…`;
        const r = await fetch(`${API}/review/decide`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ decisions: part }),
        });
        const d = await r.json();
        if (!r.ok) {
          // STOPS HERE, AND SAYS WHERE. Everything before this chunk is
          // already saved on the server, so telling you how far it got is
          // the difference between "try again" and "try again from where".
          status.textContent =
            `stopped after ${i} of ${all.length} — ${d.detail || 'failed'}`;
          await loadDates();
          return;
        }
        tally.approved += d.approved || 0;
        tally.rerun += d.rerun || 0;
        tally.unusable += d.unusable || 0;
        tally.files_removed += d.files_removed || 0;
      }
      const d = tally;
      status.textContent =
        `saved — ${d.approved} released, ${d.rerun} queued to regenerate, ${d.unusable} retired`
        + (d.files_removed
            ? ` · ${d.files_removed} old file(s) deleted from the archive` : '');
      decisions = new Map();
      saveDecisions();      // released, so the browser must forget them too
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
