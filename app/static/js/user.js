/* User dashboard — claim-based queue.

   Focus-stable polling: poll-driven refreshes never tear down the active panel
   from scratch — that's what was stealing focus from the URL/skip inputs and
   snapping the page back to the save field. We only re-create the active
   panel when the *locked title actually changed*; otherwise polls just update
   the dynamic bits (counts, posters list, status pills) in place.

   Cache-busting: every <img> served from /file_own/{id} appends ?v={file_size}
   (file_size changes on every replace). The user is also shown a one-time
   "Refresh page to see the new image" toast after replacing, as a safety net. */

(function () {
  const root = document.querySelector('.user-grid');
  if (!root) return;

  // ── Peek mode: admin viewing worker's dashboard read-only ───────────────
  const peekUsername = root.dataset.peekUsername || null;
  const isPeek = !!peekUsername;
  // Override API URL in peek mode to use admin's peek endpoint.
  const stateUrl = isPeek ? `/admin/api/peek/${encodeURIComponent(peekUsername)}` : '/api/state';
  // In peek mode, file URLs go through admin endpoint (admin doesn't have
  // /file_own access for another user's files).
  function fileUrl(posterId, sizeOrFilename) {
    if (isPeek) return `/admin/file/${posterId}?v=${encodeURIComponent(sizeOrFilename || 0)}`;
    return `/file_own/${posterId}?v=${encodeURIComponent(sizeOrFilename || 0)}`;
  }

  let state;
  try { state = JSON.parse(root.getAttribute('data-state') || '{}'); } catch (e) { state = {}; }

  // Cached refs
  const titleListEl  = root.querySelector('[data-title-list]');
  const activePanel  = root.querySelector('[data-active-panel]');
  const banner       = root.querySelector('[data-revisions-banner]');
  const bannerTitle  = root.querySelector('[data-banner-title]');
  const bannerCount  = root.querySelector('[data-rev-count]');
  const bannerList   = root.querySelector('[data-revisions-list]');
  const tplActive    = document.getElementById('tpl-active-title');
  const tplPoster    = document.getElementById('tpl-poster-card');
  const tplRevision  = document.getElementById('tpl-revision');
  const tplSimilar   = document.getElementById('tpl-similar-poster');

  let renderedLockedId = null;

  // ── Helpers ──────────────────────────────────────────────────────────────
  function setStat(name, value) {
    const el = root.querySelector(`[data-stat="${name}"]`);
    if (el) el.textContent = value;
  }

  // ── ONE PRESS IS ONE REQUEST, HOWEVER MANY TIMES IT IS PRESSED ──────────
  //
  // A title takes about a second to open, and a worker who presses again in
  // that second used to send a second, third and fourth request — every one
  // of them real, every one of them written to the Activity Log (owner's
  // find, 2026-09-10). On a phone, on a slow connection, that is not
  // impatience, it is the normal way people use a button that has not
  // visibly done anything yet.
  //
  // So a POST that is already in flight to the same address hands back the
  // SAME promise instead of starting another one. Every caller still gets
  // its answer and nothing downstream changes. The moment it settles the
  // entry is cleared, so pressing again LATER works exactly as before —
  // this collapses a burst, it does not remember a decision.
  //
  // The server refuses to record an unchanged skip or re-open as well, and
  // that half is the one that counts: a browser guard can always be got
  // round by a refresh, and this one cannot see a second tab at all.
  const inFlight = new Map();

  async function postForm(url, body = {}) {
    const key = url + '|' + JSON.stringify(body);
    if (inFlight.has(key)) return inFlight.get(key);

    const run = (async () => {
      const fd = new FormData();
      Object.entries(body).forEach(([k, v]) => fd.append(k, v));
      const r = await fetch(url, { method: 'POST', body: fd, cache: 'no-store' });
      let data = null;
      try { data = await r.json(); } catch (e) {}
      return { ok: r.ok, status: r.status, data };
    })();

    inFlight.set(key, run);
    // `finally` so a FAILED request clears too. Leaving a rejected promise
    // in the map would make the button dead for the rest of the session —
    // a busy state that never ends, which is the defect this is fixing
    // wearing its opposite face.
    try { return await run; } finally { inFlight.delete(key); }
  }

  async function getJSON(url) {
    // Cache-bust + explicit no-store: browsers will heuristically cache GET
    // responses lacking a Cache-Control header, which made counters stale
    // after rapid saves.
    const sep = url.includes('?') ? '&' : '?';
    const r = await fetch(url + sep + '_t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) return null;
    return r.json();
  }

  function humanSize(b) {
    if (!b) return '';
    if (b > 1_000_000) return (b / 1024 / 1024).toFixed(1) + ' MB';
    if (b > 1000)     return (b / 1024).toFixed(0) + ' KB';
    return b + ' B';
  }

  // Toast — bottom-right transient notice.
  let toastEl = null;
  function showToast(message, kind = 'ok', ms = 4500) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.id = 'app-toast';
      document.body.appendChild(toastEl);
    }
    toastEl.className = 'toast toast-' + kind;
    toastEl.textContent = message;
    toastEl.classList.add('toast-shown');
    clearTimeout(toastEl._timer);
    toastEl._timer = setTimeout(() => toastEl.classList.remove('toast-shown'), ms);
  }

  // ── Queue list ───────────────────────────────────────────────────────────
  // Persisted UI state for the queue list:
  //   - whether the "completed" group is collapsed (default: collapsed once
  //     there are any, since the whole point is to keep them out of the way)
  //   - the current search filter text
  // Stored in module scope so 8s polls don't reset them.
  let queueCollapsedDone = true;
  let queueSearchText = '';

  function renderQueue() {
    if (!titleListEl) return;
    if (!state.queue || state.queue.length === 0) {
      titleListEl.innerHTML = `<div class="empty-hint">
        Your list is empty. Click <strong>GET</strong> to add the next batch from the master list,
        or <strong>BROWSE ALL TITLES</strong> to pick titles manually.
      </div>`;
      return;
    }
    const lockedId = state.locked && state.locked.id;

    // Bucket titles by working state. "Active" = anything you might still
    // touch (pending claims you haven't started + in-progress + flagged);
    // "Done" = complete + skipped, the ones we want out of the way.
    const active = [];
    const done   = [];
    for (const t of state.queue) {
      if (t.status === 'complete' || t.status === 'skipped') done.push(t);
      else active.push(t);
    }

    // Sort within each bucket. Active: locked title pinned to top, then
    // in_progress, then pending. Done: most-recently-touched first using
    // saved_count as a rough proxy when explicit timestamps aren't on the
    // queue dict.
    function sortActive(a, b) {
      if (a.id === lockedId) return -1;
      if (b.id === lockedId) return 1;
      const order = { in_progress: 0, pending: 1 };
      const oa = order[a.status] ?? 9, ob = order[b.status] ?? 9;
      if (oa !== ob) return oa - ob;
      return (a.external_id ?? 0) - (b.external_id ?? 0);
    }
    active.sort(sortActive);
    // Done: keep external_id ordering — predictable when admin scrolls back.
    done.sort((a, b) => (a.external_id ?? 0) - (b.external_id ?? 0));

    // Apply search filter — matches title, year, content_type case-insensitive.
    const needle = queueSearchText.trim().toLowerCase();
    function matches(t) {
      if (!needle) return true;
      const hay = `${t.external_id ?? ''} ${t.title} ${t.year || ''} ${t.content_type || ''}`.toLowerCase();
      return hay.includes(needle);
    }
    const activeShown = active.filter(matches);
    const doneShown   = done.filter(matches);
    const activeHidden = active.length - activeShown.length;
    const doneHidden   = done.length - doneShown.length;

    titleListEl.innerHTML = '';

    // Search box — kept visually compact; persists value across re-renders.
    const searchWrap = document.createElement('div');
    searchWrap.className = 'queue-search';
    searchWrap.innerHTML = `
      <input type="search" class="queue-search-input"
             placeholder="Search your titles…"
             value="${queueSearchText.replace(/"/g, '&quot;')}">
      ${needle ? '<button type="button" class="btn btn-ghost btn-tiny queue-search-clear">CLEAR</button>' : ''}
    `;
    const searchInput = searchWrap.querySelector('.queue-search-input');
    searchInput.addEventListener('input', (e) => {
      queueSearchText = e.target.value;
      renderQueue();
      // Re-focus the input after re-render — it was destroyed and rebuilt.
      requestAnimationFrame(() => {
        const inp = titleListEl.querySelector('.queue-search-input');
        if (inp) {
          inp.focus();
          // Put cursor at end so typing continues naturally.
          const v = inp.value;
          inp.setSelectionRange(v.length, v.length);
        }
      });
    });
    const clearBtn = searchWrap.querySelector('.queue-search-clear');
    if (clearBtn) clearBtn.addEventListener('click', () => {
      queueSearchText = '';
      renderQueue();
    });
    titleListEl.appendChild(searchWrap);

    // Active group — always visible, rendered first so unstarted/in-progress
    // sit at the top regardless of how many completed there are.
    if (activeShown.length > 0) {
      activeShown.forEach((t) => titleListEl.appendChild(buildTitleItem(t, lockedId)));
    } else if (active.length > 0 && needle) {
      const empty = document.createElement('div');
      empty.className = 'queue-empty-section';
      empty.textContent = `No active titles match "${queueSearchText}".`;
      titleListEl.appendChild(empty);
    } else if (active.length === 0 && done.length > 0) {
      const empty = document.createElement('div');
      empty.className = 'queue-empty-section';
      empty.textContent = 'No active titles — everything below is finished. Click GET for more.';
      titleListEl.appendChild(empty);
    }

    // Done group — collapsible. Hidden by default; toggle in header bar.
    if (done.length > 0) {
      const header = document.createElement('div');
      header.className = 'queue-done-header';
      const arrow = queueCollapsedDone ? '▸' : '▾';
      const visibleCount = doneShown.length;
      const hiddenNote = (needle && doneHidden > 0)
        ? ` (${doneHidden} hidden by search)` : '';
      header.innerHTML = `
        <span class="queue-done-arrow mono">${arrow}</span>
        <span class="queue-done-label">${visibleCount} finished${hiddenNote}</span>
        <span class="muted queue-done-hint">${queueCollapsedDone ? 'click to expand' : 'click to collapse'}</span>
      `;
      header.addEventListener('click', () => {
        queueCollapsedDone = !queueCollapsedDone;
        renderQueue();
      });
      titleListEl.appendChild(header);

      if (!queueCollapsedDone) {
        if (doneShown.length > 0) {
          doneShown.forEach((t) => titleListEl.appendChild(buildTitleItem(t, lockedId)));
        } else if (needle) {
          const empty = document.createElement('div');
          empty.className = 'queue-empty-section';
          empty.textContent = `No finished titles match "${queueSearchText}".`;
          titleListEl.appendChild(empty);
        }
      }
    }
  }

  function buildTitleItem(t, lockedId) {
    const item = document.createElement('div');
    const cls = ['title-item', 'status-' + t.status];
    if (lockedId === t.id) cls.push('active');
    if (t.needs_revision) cls.push('flagged');
    if (t.admin_note) cls.push('has-admin-note');
    item.className = cls.join(' ');
    item.dataset.masterId = t.id;
    item.innerHTML = `
      <div class="ti-line1">
        <span class="ti-num mono">${t.external_id ?? '–'}.</span>
        <span class="ti-title"></span>
        ${realYear(t.year) ? `<span class="ti-year mono">(${realYear(t.year)})</span>` : ''}
        ${t.content_type ? `<span class="ti-type mono">${t.content_type}</span>` : ''}
      </div>
      <div class="ti-line2">
        <span class="ti-count mono">${t.saved_count} saved</span>
        · <span class="status-pill status-${t.status}">${t.status.replace('_', ' ')}</span>
        ${t.needs_revision ? '<span class="status-pill status-flag">flag</span>' : ''}
        ${t.admin_note ? '<span class="status-pill status-admin-note">admin note</span>' : ''}
      </div>`;
    item.querySelector('.ti-title').textContent = t.title;
    item.addEventListener('click', () => lockTitle(t.id));
    return item;
  }

  // ── Active panel: full re-render (only on lock change) ───────────────────
  function fullRenderActive() {
    activePanel.innerHTML = '';
    if (!state.locked) {
      // The second sentence depends on HOW this project finds images, and
      // it used to name one outside site and "a poster" outright — a
      // instruction shown to every worker in every niche. A traveller
      // looking for Kyoto was told to click a link to a film database that
      // does not appear anywhere on the page.
      //
      // Same defect as the flag card's "Open source" button: renaming would
      // have made it worse, because it would then have looked right.
      const hint = PD.searchMode === 'inpage'
        ? `Then search for an ${PD.noun} and tap the one you want to save it.`
        : `Then click the <strong>Open ${PD.sourceLabel}</strong> link to find an ${PD.noun}.`;
      activePanel.innerHTML = `<div class="empty-hint">
        Click any title in your list on the left to open it.
        ${hint}
      </div>`;
      renderedLockedId = null;
      return;
    }
    const t = state.locked;
    const node = tplActive.content.cloneNode(true);
    node.querySelector('.att-num').textContent  = (t.external_id != null ? t.external_id + '.' : '');
    node.querySelector('.att-title').textContent = t.title;
    // Year and type are shown only by projects that HAVE them. A music
    // artist has no release year and no movie/tv distinction, and rendering
    // "(N/A)" beside every name trains the eye to skip that whole line.
    const yearEl = node.querySelector('.att-year');
    const typeEl = node.querySelector('.att-type');
    if (t.has_year !== false && realYear(t.year)) {
      yearEl.textContent = '(' + realYear(t.year) + ')';
      yearEl.hidden = false;
    } else {
      yearEl.textContent = '';
      yearEl.hidden = true;
    }
    if (t.has_content_type !== false && t.content_type) {
      typeEl.textContent = t.content_type;
      typeEl.hidden = false;
    } else {
      typeEl.textContent = '';
      typeEl.hidden = true;
    }
    node.querySelector('.att-desc').textContent  = t.description || '';
    // The same word again, lower down, as "Subject: City" with its drawing.
    // It is repeated on purpose: up here it is part of the title's details,
    // down there it is the reminder you want while looking at search results.
    renderSubject(node, t.description);

    if (t.admin_note) {
      const an = node.querySelector('.att-admin-note');
      an.hidden = false;
      an.querySelector('.att-admin-note-text').textContent = t.admin_note;
    }
    if (t.skip_reason && t.admin_note) {
      const sn = node.querySelector('.att-skip-note');
      sn.hidden = false;
      sn.querySelector('.att-skip-note-text').textContent = t.skip_reason;
    }
    // Count of unresolved flags ON THIS title — surfaces inside the
    // workplace so workers fixing posters in-place don't lose sight
    // of related flags they should also resolve.
    const myRevs = (state.revisions || []).filter(
      (r) => r.master_id === t.id && r.status === 'open'
    );
    if (myRevs.length > 0) {
      const fb = node.querySelector('.att-active-flags-banner');
      fb.hidden = false;
      fb.querySelector('.att-active-flags-count').textContent = String(myRevs.length);
      fb.querySelector('.att-active-flags-plural').textContent = myRevs.length === 1 ? '' : 's';
    }

    // ── TWO INDEPENDENT QUESTIONS, ASKED SEPARATELY ────────────────────
    //
    // Does this project search IN-PAGE, and does it have an OUTSIDE LINK.
    // These used to be one either/or — a project got the grid or the link,
    // never both — and that was correct until travel needed both. Brave's
    // picture catalogue is thinner than Google's, so the grid is the first
    // try and Google is the backstop when it comes up short.
    //
    // The paste-a-URL box belongs to the LINK, not to the absence of a
    // grid: it is how an image found on the outside site gets back here.
    const sourceLink = node.querySelector('.att-source-link');
    const searchBox = node.querySelector('[data-search-box]');
    const saveBox = node.querySelector('.save-box');
    const hasLink = !!t.source_link;

    if (searchBox) {
      searchBox.hidden = (t.search_mode !== 'inpage');
      if (!searchBox.hidden) wireSearch(searchBox, t);
    }

    sourceLink.hidden = !hasLink;
    if (hasLink) {
      sourceLink.href = t.source_link;
      // The button is named by the project, not by the markup. A worker on
      // a niche that has never heard of a given site should never be told
      // to open it — and the day a third source appears, this needs no edit.
      sourceLink.textContent = `↗ Open ${t.source_link_label || t.source_label || 'source'}`;
    }
    // Shown with the link and hidden without it. A URL field a worker can
    // never sensibly fill is clutter, and on a phone it is what summons the
    // keyboard.
    if (saveBox) saveBox.hidden = !hasLink;

    // The project's own word for what is being saved — "posters" for movies,
    // "images" for MUSIK. Every worker-facing label reads this.
    // PD holds the project's words for every script — see base.html.
    // Inventing a fallback here is what spread the movie vocabulary
    // through four files in the first place.
    const nouns = t.item_nouns || PD.nouns;
    const noun  = t.item_noun  || PD.noun;
    node.querySelectorAll('[data-noun]').forEach((el) => { el.textContent = noun; });
    node.querySelectorAll('[data-nouns]').forEach((el) => {
      // Headings are upper-case in this UI; inline mentions are not.
      el.textContent = el.dataset.nouns === 'upper' ? nouns.toUpperCase() : nouns;
    });

    const urlInput = node.querySelector('.save-url');
    const saveBtn  = node.querySelector('[data-action="save"]');
    const flashBar = node.querySelector('.flash-bar');
    const saveMsg  = node.querySelector('.save-msg');
    saveBtn.addEventListener('click', () => doSave(urlInput, saveMsg, flashBar));
    urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSave(urlInput, saveMsg, flashBar); });

    const grid  = node.querySelector('.posters-grid');
    const count = node.querySelector('.posters-count');
    count.textContent = (t.posters || []).length;
    (t.posters || []).forEach(
      (p) => grid.appendChild(buildPosterCard(p, !!t.source_link)));
    grid.dataset.sig = (t.posters || []).map((p) => `${p.id}:${p.size || 0}`).join('|');

    const completeBtn = node.querySelector('[data-action="complete"]');
    const skipBtn     = node.querySelector('[data-action="skip"]');
    const reopenBtn   = node.querySelector('[data-action="reopen"]');
    const skipReason  = node.querySelector('.skip-reason');
    const doneComment = node.querySelector('.done-comment');
    const unlockBtn   = node.querySelector('[data-action="unlock"]');

    completeBtn.addEventListener('click', () => completeTitle((state.locked && state.locked.id) || t.id, doneComment));
    skipBtn.addEventListener('click',     () => skipTitle((state.locked && state.locked.id) || t.id, skipReason.value));
    reopenBtn.addEventListener('click',   () => reopenTitle((state.locked && state.locked.id) || t.id));
    unlockBtn.addEventListener('click',   () => unlock());

    if (t.status === 'complete' || t.status === 'skipped') {
      reopenBtn.hidden = false;
      completeBtn.hidden = true;
      skipBtn.hidden = true;
    }
    syncDoneButton(node, t);

    activePanel.appendChild(node);
    renderedLockedId = t.id;

    // Deliberately NOT focusing the URL box. On a phone, focusing an input
    // opens the on-screen keyboard, which covered half the screen every time
    // a worker opened a title. Nobody types a URL first — they go to the
    // source, or (in-page projects) tap a thumbnail — so the focus was only
    // ever costing a tap to dismiss.
    //
    // Desktop users lose nothing: the field is one click away and Enter still
    // saves once you are in it.
  }

  // Passive update — same locked title, refresh dynamic bits without
  // touching inputs or stealing focus.
  function passiveUpdateActive() {
    if (!state.locked) {
      fullRenderActive();
      return;
    }
    const t = state.locked;
    const countEl = activePanel.querySelector('.posters-count');
    if (countEl) countEl.textContent = (t.posters || []).length;
    const grid = activePanel.querySelector('.posters-grid');
    if (!grid) return;
    // Use id+size as the signature — replaces change size, so this catches them.
    const newSig = (t.posters || []).map((p) => `${p.id}:${p.size || 0}`).join('|');
    if ((grid.dataset.sig || '') !== newSig) {
      grid.innerHTML = '';
      (t.posters || []).forEach(
        (p) => grid.appendChild(buildPosterCard(p, !!t.source_link)));
      grid.dataset.sig = newSig;
    }
    const completeBtn = activePanel.querySelector('[data-action="complete"]');
    const skipBtn     = activePanel.querySelector('[data-action="skip"]');
    const reopenBtn   = activePanel.querySelector('[data-action="reopen"]');
    if (completeBtn && skipBtn && reopenBtn) {
      const finished = (t.status === 'complete' || t.status === 'skipped');
      completeBtn.hidden = finished;
      skipBtn.hidden     = finished;
      reopenBtn.hidden   = !finished;
    }
    // Saving the first image is what unlocks DONE, and that happens through
    // this passive path rather than a full re-render — so the enabling has
    // to live here too, not only in fullRenderActive(). A guard applied on
    // one of two paths is the same defect as no guard at all.
    syncDoneButton(activePanel, t);
    renderSubject(activePanel, t.description);
  }

  // hasLink is the TITLE's own answer to "is there an outside site to paste
  // an address from". Passed in by the caller rather than read from the
  // project the worker happens to be standing in — a flag card can belong
  // to their other project, and the two would disagree.
  //
  // It used to be the title's search MODE, on the reasoning that a project
  // with a grid has nothing to paste. That stopped being true when travel
  // got both a Brave grid and a Google link.
  // A YEAR IS FOUR DIGITS OR IT IS NOTHING.
  // The importer no longer stores "N/A" (fixed 2026-09-06), but rows
  // imported before that still carry the text, and "N/A" is truthy — which
  // is exactly why it kept rendering beside every title on the phone. Guard
  // at the point of DISPLAY as well as at the door.
  function realYear(v) {
    const m = String(v == null ? '' : v).match(/\b(1[0-9]{3}|2[0-9]{3})\b/);
    return m ? m[0] : '';
  }

  // ── WHAT KIND OF PLACE THIS IS ────────────────────────────────────────
  //
  // The drawings themselves live in subject_icons.js now, shared with the
  // two admin screens that show the same subject while reviewing — three
  // private copies of seventeen drawings would be three chances to drift.
  // The page loads that file first, so window.SubjectKind is always there.
  const subjectIcon  = (kind) => window.SubjectKind.icon(kind);
  const subjectWords = (kind) => window.SubjectKind.words(kind);

  function renderSubject(root, kind) {
    const strip = root.querySelector('[data-subject-strip]');
    if (!strip) return;
    const words = subjectWords(kind);
    if (!words) { strip.hidden = true; return; }
    strip.hidden = false;
    strip.querySelector('[data-subject-kind]').textContent = words;
    strip.querySelector('[data-subject-icon]').innerHTML = subjectIcon(kind);
  }

  // ── The loading icon for a picture that is still arriving ─────────────
  // Deliberately NOT on the SAVE SELECTED button. A control that renames
  // itself mid-press is confusing, and the owner reported exactly that. The
  // spinner sits beside the saved-images count, which is where the picture
  // is about to appear, and it is the only thing that changes.
  function showThumbLoading(on) {
    document.querySelectorAll('[data-thumb-loading]').forEach((el) => {
      el.hidden = !on;
    });
  }

  // ── DONE cannot be pressed before there is a picture ──────────────────
  // The server already refuses to complete a title with no live image, so
  // an enabled button here was a promise the server would break. The owner:
  // there is no case where a worker finishes before saving an image — if
  // there were, that is what SKIP is for.
  function syncDoneButton(root, t) {
    const btn = root.querySelector('[data-action="complete"]');
    if (!btn) return;
    const has = ((t && t.posters) || []).length > 0;
    btn.disabled = !has;
    btn.title = has
      ? ''
      : 'Save an image first. If there is no usable image for this place, '
      + 'use SKIP instead.';
  }

  function buildPosterCard(p, hasLink) {
    const node = tplPoster.content.cloneNode(true);
    const img = node.querySelector('.poster-img');
    img.src = fileUrl(p.id, p.size);
    img.alt = p.filename;
    img.style.cursor = 'zoom-in';
    img.title = 'Click to enlarge';
    img.addEventListener('click', (e) => {
      e.stopPropagation();
      openLightbox(fileUrl(p.id, p.size), p.filename);
    });
    // LONG-PRESS OPENS THE BIG VIEW, not Chrome's "open image in new tab"
    // menu. Asked for 2026-09-06 so a worker can check detail on a phone.
    // contextmenu is what a long press fires on Android; suppressing it and
    // opening our own viewer replaces the browser menu rather than fighting
    // it. Desktop right-click gets the same, which is harmless.
    img.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      openLightbox(fileUrl(p.id, p.size), p.filename);
    });
    img.style.webkitTouchCallout = 'none';
    node.querySelector('.poster-name').textContent = p.filename;
    node.querySelector('.poster-size').textContent = humanSize(p.size || 0);
    // REPLACE-BY-URL ONLY EXISTS WHERE THERE IS A URL TO PASTE.
    //
    // With no outside site the worker picked from a grid and would have no
    // idea what to type here; their way to change their mind is DELETE and
    // pick again, which is why that button stays. Removed rather than
    // disabled: a control that can never do anything is not a labelling
    // problem.
    const replaceUrl = node.querySelector('.poster-replace-url');
    const replaceBtn = node.querySelector('[data-action="replace"]');
    if (!hasLink) {
      replaceUrl.remove();
      replaceBtn.remove();
    } else {
      replaceBtn.addEventListener('click', () => replacePoster(p.id, replaceUrl));
    }
    // The DELETE button's job depends on how the image was chosen.
    //   URL projects: they can also REPLACE (paste a new address), so
    //     DELETE stays the reasoned removal.
    //   IN-PAGE (Brave grid) projects: there is no URL to paste, so the
    //     grid IS the undo. One tap removes this pick and drops you back at
    //     the results to choose another — no reason dialog, because this is
    //     a workflow correction, not a quality rejection (owner's ask,
    //     2026-09-06). The grid is still populated from the last search.
    const delBtn = node.querySelector('[data-action="delete"]');
    if (hasLink) {
      delBtn.addEventListener('click', () => deletePoster(p.id, { fromRevision: false }));
    } else {
      delBtn.textContent = '↩ PICK ANOTHER';
      delBtn.title = 'Remove this image and choose a different one from the search results';
      delBtn.addEventListener('click', () => undoSavedPick(p.id));
    }
    return node;
  }

  // Quick undo for a Brave-grid pick: delete without the reason dialog,
  // then reveal the still-populated results so the worker can pick again.
  // Resolves once every saved thumbnail has finished loading (or failed),
  // so the caller can stop showing a busy state at the right moment.
  function waitForSavedThumbs() {
    const imgs = [...document.querySelectorAll('.poster-img')];
    const pending = imgs.filter((im) => !im.complete);
    if (!pending.length) return Promise.resolve();
    return Promise.all(pending.map((im) => new Promise((done) => {
      im.addEventListener('load', done, { once: true });
      im.addEventListener('error', done, { once: true });
      // Never hang the button on a thumbnail that silently stalls.
      setTimeout(done, 8000);
    })));
  }

  // ── SWAP, ASKED FOR FROM THE SEARCH GRID ────────────────────────────────
  //
  // An EVENT rather than a function call, and that is deliberate.
  // `undoSavedPick` is declared inside this wrapper; `wireSearch` is a
  // top-level function outside it. Calling across that boundary is the
  // quietest failure in this codebase — the signature UPLOAD button did
  // exactly that, threw inside an async handler, and did nothing visible at
  // all. An event crosses the boundary without either side reaching into
  // the other's scope.
  document.addEventListener('pd:swap-saved', () => {
    const saved = (state.locked && state.locked.posters) || [];
    if (saved.length) undoSavedPick(saved[saved.length - 1].id);
  });

  async function undoSavedPick(posterId) {
    const r = await postForm(`/poster/${posterId}/delete`,
                             { note: 'Re-picking a different image',
                               reason_source: 'undo' });
    if (!r.ok) { alert('Could not undo: ' + (r.data && r.data.detail || r.status)); return; }
    await refreshState();
    const box = document.querySelector('[data-search-box]');
    if (box) {
      box.hidden = false;
      const grid = box.querySelector('[data-search-grid]');
      (grid && grid.children.length ? grid : box)
        .scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  // ── Lightbox (90% screen) ────────────────────────────────────────────────
  function openLightbox(src, caption) {
    let lb = document.getElementById('worker-lightbox');
    if (!lb) {
      lb = document.createElement('div');
      lb.id = 'worker-lightbox';
      lb.className = 'lightbox worker-lightbox';
      lb.innerHTML = `
        <div class="lightbox-bg" data-lb-close></div>
        <div class="lightbox-card worker-lightbox-card">
          <button type="button" class="worker-lightbox-close" data-lb-close aria-label="Close">×</button>
          <img class="worker-lightbox-img" alt="">
          <div class="worker-lightbox-caption mono"></div>
        </div>
      `;
      document.body.appendChild(lb);
      lb.querySelectorAll('[data-lb-close]').forEach((el) => el.addEventListener('click', closeLightbox));
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !lb.hidden) closeLightbox();
      });
    }
    lb.querySelector('.worker-lightbox-img').src = src;
    lb.querySelector('.worker-lightbox-img').alt = caption || '';
    lb.querySelector('.worker-lightbox-caption').textContent = caption || '';
    lb.hidden = false;
  }
  function closeLightbox() {
    const lb = document.getElementById('worker-lightbox');
    if (lb) lb.hidden = true;
  }

  // ── Reason picker modal ─────────────────────────────────────────────────
  // Generic modal for "give a reason" flows (complete with <3 posters,
  // skip a title). Returns a Promise<{ text, source } | null>. `source`
  // is 'preset' if user clicked a preset, 'manual' if they typed.
  // Returns null if cancelled.
  function pickReason(opts) {
    return new Promise((resolve) => {
      const modal      = document.getElementById('reason-modal');
      const titleEl    = document.getElementById('reason-title');
      const subEl      = document.getElementById('reason-sub');
      const hintEl     = document.getElementById('reason-hint');
      const presetWrap = document.getElementById('reason-preset-list');
      const manualWrap = document.getElementById('reason-manual');
      const manualInp  = document.getElementById('reason-manual-input');
      const confirmBtn = document.getElementById('reason-confirm');
      const toggleBtn  = document.getElementById('reason-toggle-manual');

      titleEl.textContent = opts.title || 'Reason needed';
      subEl.textContent   = opts.sub   || '';
      // Optional secondary hint, used e.g. on delete to suggest REPLACE
      // for accidentally-saved-wrong-image cases.
      if (hintEl) {
        if (opts.hint) {
          hintEl.textContent = opts.hint;
          hintEl.hidden = false;
        } else {
          hintEl.textContent = '';
          hintEl.hidden = true;
        }
      }
      presetWrap.innerHTML = '';
      manualWrap.hidden = true;
      manualInp.value = '';
      confirmBtn.disabled = true;
      toggleBtn.textContent = 'TYPE OWN REASON';

      let chosen = null;  // { text, source }

      // Render presets.
      (opts.presets || []).forEach((text) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'reason-preset-btn';
        b.textContent = text;
        b.addEventListener('click', () => {
          // Mark this preset as selected; clear others.
          presetWrap.querySelectorAll('.reason-preset-btn').forEach((x) => x.classList.remove('selected'));
          b.classList.add('selected');
          chosen = { text, source: 'preset' };
          confirmBtn.disabled = false;
        });
        presetWrap.appendChild(b);
      });
      // ── A PLAIN CONFIRM MUST NOT OFFER A TYPING BOX ──────────────────
      // "Are you sure?" with a TYPE OWN REASON button beside it is still
      // asking for a reason, just more quietly. When there is nothing to
      // record, the toggle is hidden and the dialog is two buttons.
      if (toggleBtn) toggleBtn.hidden = !!opts.noText;
      if (opts.noText && manualWrap) manualWrap.hidden = true;

      // If opts.allowEmpty (e.g. complete-with-comment is optional),
      // include a "no reason" preset so worker can confirm without typing.
      if (opts.allowEmpty) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'reason-preset-btn reason-preset-empty';
        b.textContent = opts.emptyLabel || 'No reason — just confirm';
        b.addEventListener('click', () => {
          presetWrap.querySelectorAll('.reason-preset-btn').forEach((x) => x.classList.remove('selected'));
          b.classList.add('selected');
          chosen = { text: '', source: 'preset' };
          confirmBtn.disabled = false;
        });
        presetWrap.appendChild(b);
      }

      function close(result) {
        modal.hidden = true;
        // Disconnect listeners so they don't leak across opens.
        modal.querySelectorAll('[data-reason-close]').forEach((el) => el.onclick = null);
        toggleBtn.onclick = null;
        confirmBtn.onclick = null;
        manualInp.oninput = null;
        document.removeEventListener('keydown', onKey);
        resolve(result);
      }

      function onKey(e) {
        if (e.key === 'Escape') close(null);
      }

      modal.querySelectorAll('[data-reason-close]').forEach((el) => {
        el.onclick = () => close(null);
      });
      toggleBtn.onclick = () => {
        manualWrap.hidden = !manualWrap.hidden;
        if (!manualWrap.hidden) {
          manualInp.focus();
          // Clear any preset selection — they're switching to manual.
          presetWrap.querySelectorAll('.reason-preset-btn').forEach((x) => x.classList.remove('selected'));
          chosen = null;
          confirmBtn.disabled = true;
        }
      };
      manualInp.oninput = () => {
        const v = manualInp.value.trim();
        if (v) {
          chosen = { text: v, source: 'manual' };
          confirmBtn.disabled = false;
        } else if (opts.allowEmpty) {
          chosen = { text: '', source: 'manual' };
          confirmBtn.disabled = false;
        } else {
          chosen = null;
          confirmBtn.disabled = true;
        }
      };
      confirmBtn.onclick = () => close(chosen);

      document.addEventListener('keydown', onKey);
      modal.hidden = false;
    });
  }
  // Shows every live poster currently saved on a master title. Read-only —
  // for awareness of what's already there before replacing/saving. The
  // "GO TO TITLE" button delegates to goToTitle() which opens the workplace.
  let catalogActiveMasterId = null;

  function openCatalog(masterId, opts = {}) {
    const modal = document.getElementById('catalog-modal');
    if (!modal) return;
    catalogActiveMasterId = masterId;
    document.getElementById('catalog-title').textContent = opts.titleHint || '…';
    document.getElementById('catalog-sub').textContent = '';
    document.getElementById('catalog-grid').innerHTML =
      '<div class="empty-hint">Loading…</div>';
    modal.hidden = false;
    // Wire close handlers once. We re-attach safely since they're idempotent.
    modal.querySelectorAll('[data-catalog-close]').forEach((el) => {
      el.onclick = closeCatalog;
    });
    document.getElementById('catalog-go-to-title').onclick = () => {
      const mid = catalogActiveMasterId;
      closeCatalog();
      if (mid != null) goToTitle(mid);
    };
    fetch(`/api/title/${masterId}/catalog`, { cache: 'no-store' })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data, status: r.status })))
      .then(({ ok, data, status }) => {
        if (!ok) {
          document.getElementById('catalog-grid').innerHTML =
            `<div class="empty-hint">${data.detail || ('Failed (' + status + ')')}</div>`;
          return;
        }
        document.getElementById('catalog-title').textContent =
          realYear(data.year) ? `${data.title} (${realYear(data.year)})` : data.title;
        document.getElementById('catalog-sub').textContent =
          `${data.posters.length} ${data.posters.length === 1 ? PD.noun : PD.nouns} on this title · status: ${data.status.replace('_', ' ')}`;
        const grid = document.getElementById('catalog-grid');
        if (data.posters.length === 0) {
          grid.innerHTML = `<div class="empty-hint">No ${PD.nouns} saved on this title.</div>`;
          return;
        }
        grid.innerHTML = '';
        data.posters.forEach((p) => {
          const card = document.createElement('div');
          card.className = 'catalog-poster';
          card.innerHTML = `
            <img class="catalog-poster-img" loading="lazy" alt="">
            <div class="catalog-poster-name mono"></div>
            <div class="catalog-poster-meta mono muted"></div>
          `;
          const img = card.querySelector('.catalog-poster-img');
          img.src = p.url + '?v=' + (p.size || 0);
          img.alt = p.filename;
          img.style.cursor = 'zoom-in';
          img.title = 'Click to enlarge';
          img.addEventListener('click', () => openLightbox(img.src, p.filename));
          card.querySelector('.catalog-poster-name').textContent = p.filename;
          const dims = (p.width && p.height) ? `${p.width}×${p.height}` : '';
          card.querySelector('.catalog-poster-meta').textContent =
            [humanSize(p.size), dims, p.saved_on].filter(Boolean).join(' · ');
          grid.appendChild(card);
        });
      })
      .catch(() => {
        document.getElementById('catalog-grid').innerHTML =
          '<div class="empty-hint">Network error.</div>';
      });
  }

  function closeCatalog() {
    const modal = document.getElementById('catalog-modal');
    if (modal) modal.hidden = true;
    catalogActiveMasterId = null;
  }

  // ── Go to title ─────────────────────────────────────────────────────────
  // Server-side reopen + lock, then refresh state. The active panel will
  // auto-render the now-locked title; we scroll to it for clarity.
  async function goToTitle(masterId) {
    const r = await fetch(`/title/${masterId}/go_to`, { method: 'POST' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert('Failed: ' + (data.detail || r.status));
      return;
    }
    await refreshState();
    // Scroll the active panel into view. Mobile: also collapse drawer if open.
    requestAnimationFrame(() => {
      const panel = document.querySelector('[data-active-panel]');
      if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  // ── Revisions banner ─────────────────────────────────────────────────────
  function renderRevisions() {
    const revs = state.revisions || [];
    if (!revs.length) {
      banner.hidden = true;
      bannerList.innerHTML = '';
      return;
    }
    banner.hidden = false;

    const openCount = revs.filter((r) => r.status === 'open').length;
    const awaitingCount = revs.filter((r) => r.status === 'awaiting_approval').length;
    const rejectedCount = revs.filter((r) => r.was_rejected).length;
    let label = 'CHANGES REQUESTED';
    if (rejectedCount && rejectedCount === openCount) label = '⚠ REJECTED — PLEASE REVISE';
    else if (awaitingCount && !openCount) label = 'AWAITING ADMIN APPROVAL';
    bannerTitle.textContent = label;
    bannerCount.textContent = `${revs.length} item${revs.length === 1 ? '' : 's'}`;

    bannerList.innerHTML = '';
    revs.forEach((r) => bannerList.appendChild(buildRevisionItem(r)));
  }

  function buildRevisionItem(r) {
    const node = tplRevision.content.cloneNode(true);
    const wrap = node.querySelector('.revision-item');
    wrap.dataset.revisionId = r.revision_id;
    wrap.dataset.posterId   = r.poster_id;
    if (r.status === 'awaiting_approval') wrap.classList.add('rev-awaiting');
    if (r.was_rejected) wrap.classList.add('rev-rejected');
    if (r.revision_type === 'similar') wrap.classList.add('rev-similar');

    // "(N/A)" after every artist name is noise, and noise is what teaches
    // people to stop reading labels. Projects without a year show none.
    const titleStr = realYear(r.year) ? `${r.title} (${realYear(r.year)})` : r.title;
    wrap.querySelector('.rev-title').textContent = titleStr;
    wrap.querySelector('.rev-file').textContent  = `/ ${r.title_folder} / ${r.filename}`;

    // Source link. REMOVED, not hidden, for a project with no external
    // source — GO TO TITLE already takes the worker to the in-page search,
    // which is the only place their images come from.
    // r.search_mode, NOT PD.searchMode. PD is the project the worker is
    // STANDING IN; a flag card can be for a title in their other project, and
    // then the two disagree. Standing in MUSIK with a flagged movie poster,
    // the global check removed the paste-a-URL box — and pasting a URL is the
    // only way a movie poster can be replaced, so the worker could not fix
    // their own flagged image. The server already sends the title's own mode
    // with every revision; this just has to read it.
    // The link stands or falls on its own now — a project can search
    // in-page AND have one, which travel does.
    const sourceLinkEl = wrap.querySelector('.rev-source-link');
    if (!r.source_link) {
      sourceLinkEl.remove();
    } else {
      sourceLinkEl.href = r.source_link;
      sourceLinkEl.textContent = `↗ Open ${r.source_link_label || r.source_label || 'source'}`;
    }

    // VIEW ALL POSTERS — opens the catalog modal so the worker can see
    // the rest of their saves on this title without leaving the flag list.
    wrap.querySelector('.rev-view-catalog').addEventListener('click', () => {
      openCatalog(r.master_id, { titleHint: titleStr });
    });
    // GO TO TITLE — reopens (if needed) + locks + scrolls the worker into
    // the active title workplace for hands-on edits.
    wrap.querySelector('.rev-go-to-title').addEventListener('click', () => {
      goToTitle(r.master_id);
    });

    // Status pill
    const pill = wrap.querySelector('.rev-status-pill');
    pill.classList.add('status-pill');
    if (r.was_rejected) {
      pill.classList.add('status-rejected');
      pill.textContent = 'rejected — redo';
    } else if (r.status === 'awaiting_approval') {
      pill.classList.add('status-awaiting');
      pill.textContent = 'awaiting approval';
    } else if (r.revision_type === 'similar') {
      pill.classList.add('status-similar');
      pill.textContent = 'similar pair';
    } else {
      pill.classList.add('status-flag');
      pill.textContent = 'open';
    }

    // Rejection banner — make it impossible to miss
    if (r.was_rejected && r.admin_verdict) {
      const rb = wrap.querySelector('.rev-rejected-banner');
      rb.hidden = false;
      rb.querySelector('.rev-verdict-text').textContent = ' — ' + r.admin_verdict;
    }

    wrap.querySelector('.rev-comment').textContent = r.comment || '(no comment)';
    const flagged = wrap.querySelector('.rev-flagged');
    if (r.status === 'awaiting_approval') {
      // Distinguish what the worker did so they know what kind of approval is pending.
      let actionLabel;
      if (r.worker_action === 'deleted') actionLabel = 'your deletion is awaiting admin approval';
      else if (r.worker_action === 'replaced') actionLabel = 'your replacement is awaiting admin approval';
      else                                     actionLabel = 'awaiting admin approval';
      flagged.textContent = `${actionLabel} since ${r.submitted_at || ''}`;
    } else {
      flagged.textContent = `flagged by ${r.flagged_by} · ${r.created_at}`;
    }

    // Render either single-poster controls or similar-pair grid.
    // We .remove() the unused branch entirely — using `hidden=true` doesn't
    // win against author CSS like `.rev-actions { display: flex }`, which is
    // why the broken-thumb top section was still showing on similar pairs.
    const simpleControls  = wrap.querySelector('[data-mode="simple"]');
    const similarControls = wrap.querySelector('[data-mode="similar"]');

    if (r.revision_type === 'similar' && r.related && r.related.length >= 2) {
      simpleControls.remove();
      r.related.forEach((p) => similarControls.appendChild(buildSimilarCard(r, p)));
    } else if (r.revision_type === 'similar' && r.poster_deleted) {
      // Edge case: a similar-pair revision where the primary poster was
      // deleted AND the remaining related list dropped below 2. We still
      // need to show a card so the worker knows admin is reviewing. Use
      // the simple-mode template with the placeholder thumb.
      similarControls.remove();
      _renderDeletedRow(wrap, r);
    } else {
      similarControls.remove();
      if (r.poster_deleted) {
        _renderDeletedRow(wrap, r);
      } else {
        // Simple single-poster row, live file.
        const thumb  = wrap.querySelector('.rev-thumb');
        thumb.src    = fileUrl(r.poster_id, r.filename);
        const urlInp = wrap.querySelector('[data-replace-url]');
        const replaceBtn = wrap.querySelector('[data-action="replace"]');
        const deleteBtn  = wrap.querySelector('[data-action="delete-revision"]');
        const resolveBtn = wrap.querySelector('[data-action="resolve"]');
        if (r.status === 'awaiting_approval') resolveBtn.hidden = true;
        // REPLACING BY URL BELONGS TO THE OUTSIDE LINK, not to the absence
        // of a grid. Travel has both, and a worker whose image was flagged
        // needs to be able to paste a better one from Google.
        //
        // Still the TITLE's own values, never the project the worker happens
        // to be standing in — a flag card can be for a title in their other
        // project, and the two would disagree.
        if (!r.source_link) {
          urlInp.remove();
          replaceBtn.remove();
        } else {
          replaceBtn.addEventListener('click', () => replacePoster(r.poster_id, urlInp));
        }

        // ── FIX IT FROM THE SEARCH GRID, NOT ONLY FROM A URL ─────────────
        // A flagged image could previously only be replaced by pasting an
        // address, which is useless for an in-page project where the worker
        // never has a URL (owner's ask, 2026-09-06). This opens the title
        // and drops them at the results; saving a new pick REPLACES the
        // flagged one through the same replace path the grid uses.
        if (r.search_mode === 'inpage') {
          const findBtn = document.createElement('button');
          findBtn.type = 'button';
          findBtn.className = 'btn btn-accent btn-tiny';
          findBtn.textContent = '🔍 FIND A REPLACEMENT';
          findBtn.title = 'Open this title and search for a different image';
          findBtn.addEventListener('click', async () => {
            await goToTitle(r.master_id);
            const box = document.querySelector('[data-search-box]');
            if (box) {
              box.hidden = false;
              box.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
          });
          (replaceBtn.parentNode || wrap.querySelector('.rev-actions'))
            .insertBefore(findBtn, replaceBtn.nextSibling || null);
        }
        deleteBtn.addEventListener('click',  () => deletePoster(r.poster_id, { fromRevision: true }));
        resolveBtn.addEventListener('click', () => resolveRevision(r.revision_id));
      }
    }
    return node;
  }

  // When the underlying poster is gone (worker deleted it), we still want
  // the revision card to appear in the worker's flag panel so they know
  // admin is reviewing the deletion. Swap the thumb to the placeholder,
  // strip the action buttons, and add a minimal status note.
  function _renderDeletedRow(wrap, r) {
    const thumb = wrap.querySelector('.rev-thumb');
    if (thumb) {
      thumb.src = '/static/img/deleted-poster.svg';
      thumb.alt = `${PD.noun} deleted`;
      thumb.classList.add('rev-thumb-placeholder');
    }
    // Remove the URL input + action buttons row entirely — there's nothing
    // to replace or re-delete; the worker just waits.
    const urlRow = wrap.querySelector('.rev-actions');
    if (urlRow) urlRow.remove();
    // Add a minimal "info-only" status line in place of the controls so the
    // worker has something to read.
    const simpleControls = wrap.querySelector('[data-mode="simple"]');
    if (simpleControls) {
      const info = document.createElement('div');
      info.className = 'rev-deleted-info muted';
      if (r.status === 'awaiting_approval') {
        info.textContent =
          `You deleted this ${PD.noun}. Admin will review the deletion and approve or send it back.`;
      } else if (r.was_rejected) {
        info.textContent =
          `Admin sent back your deletion. Read the note above — you may need to save a new ${PD.noun} on this title.`;
      } else {
        info.textContent = `${PD.Noun} deleted — admin reviewing.`;
      }
      simpleControls.appendChild(info);
    }
  }

  function buildSimilarCard(rev, p) {
    const node = tplSimilar.content.cloneNode(true);
    const img = node.querySelector('.rev-similar-thumb');
    img.src = fileUrl(p.poster_id, p.size || p.filename);
    node.querySelector('.rev-similar-name').textContent = p.filename;
    const urlInp = node.querySelector('[data-replace-url]');
    node.querySelector('[data-action="replace"]')
        .addEventListener('click', () => replacePoster(p.poster_id, urlInp));
    node.querySelector('[data-action="delete-revision"]')
        .addEventListener('click', () => deletePoster(p.poster_id, { fromRevision: true }));
    return node;
  }

  function renderStats() {
    setStat('saved_today',  state.saved_today);
    setStat('saved_week',   state.saved_week);
    setStat('titles_today', state.titles_today);
    setStat('pending_today', state.pending_today || 0);
    // Hide the "not counted yet" tile when there's nothing pending.
    const tile = document.querySelector('[data-pending-tile]');
    if (tile) tile.hidden = !(state.pending_today && state.pending_today > 0);
  }

  function renderReceipts() {
    const banner = document.querySelector('[data-receipts-banner]');
    if (!banner) return;
    const list = banner.querySelector('[data-receipts-list]');
    const countEl = banner.querySelector('[data-receipts-count]');
    const receipts = state.receipts || [];
    if (!receipts.length) {
      banner.hidden = true;
      list.innerHTML = '';
      return;
    }
    banner.hidden = false;
    countEl.textContent = `${receipts.length} unacknowledged`;
    list.innerHTML = '';
    receipts.forEach((r) => {
      const item = document.createElement('div');
      item.className = 'receipt-item';
      const hasBackPay = (r.back_pay_dates && r.back_pay_dates.length > 0);
      const byDayDates = Object.keys(r.by_day || {}).sort();
      // Only shown when the run actually spans more than one project — a
      // worker on a single niche doesn't need a line telling them so.
      const byProject = r.by_project || {};
      const projNames = Object.keys(byProject);
      const showProjects = projNames.length > 1;
      item.innerHTML = `
        <div class="receipt-row">
          <strong class="mono receipt-amount"></strong>
          <span class="muted">for</span>
          <span class="mono receipt-period"></span>
          <span class="muted receipt-count"></span>
        </div>
        <div class="receipt-meta mono muted"></div>
        <div class="receipt-note"></div>
        ${byDayDates.length > 0 ? `
          <details class="receipt-breakdown">
            <summary class="muted">▸ See per-day breakdown</summary>
            <div class="receipt-day-list"></div>
          </details>
        ` : ''}
        ${showProjects ? `<div class="receipt-projects muted"></div>` : ''}
        ${hasBackPay ? `<div class="receipt-backpay">includes back-pay from <span class="receipt-backpay-dates"></span></div>` : ''}
        <div class="receipt-actions">
          <button class="btn btn-accent btn-tiny receipt-ack-btn" type="button">ACKNOWLEDGE</button>
          <button class="btn btn-danger btn-tiny receipt-nr-btn" type="button">NOT RECEIVED</button>
        </div>
      `;
      item.querySelector('.receipt-amount').textContent = `KES ${r.amount_kes}`;
      item.querySelector('.receipt-period').textContent =
        r.period_start === r.period_end ? r.period_start : `${r.period_start} → ${r.period_end}`;
      item.querySelector('.receipt-count').textContent =
        `(${r.poster_count} ${r.poster_count === 1 ? PD.noun : PD.nouns} × ${r.rate_kes} KES)`;
      item.querySelector('.receipt-meta').textContent =
        (r.reference ? `Ref: ${r.reference} · ` : '') + `Sent ${r.pushed_at || ''}`;
      const noteEl = item.querySelector('.receipt-note');
      if (r.note) noteEl.textContent = r.note; else noteEl.remove();

      // Per-day breakdown.
      if (byDayDates.length > 0) {
        const dayList = item.querySelector('.receipt-day-list');
        const rate = parseFloat(r.rate_kes) || 0;
        const backPaySet = new Set(r.back_pay_dates || []);
        let html = '';
        byDayDates.forEach((d) => {
          const c = r.by_day[d];
          const sub = (c * rate);
          const isBack = backPaySet.has(d);
          html += `
            <div class="receipt-day-row${isBack ? ' is-back-pay' : ''}">
              <span class="mono">${d}${isBack ? ' <span class="bp-tag">back-pay</span>' : ''}</span>
              <span class="mono">${c} × ${r.rate_kes}</span>
              <span class="mono">${formatKes(sub)} KES</span>
            </div>`;
        });
        dayList.innerHTML = html;
      }

      // One payment, several projects — say so explicitly.
      if (showProjects) {
        item.querySelector('.receipt-projects').textContent =
          'Covers ' + projNames.map((n) => `${n}: ${byProject[n]}`).join(' · ');
      }

      // Back-pay summary line.
      if (hasBackPay) {
        item.querySelector('.receipt-backpay-dates').textContent = r.back_pay_dates.join(', ');
      }

      item.querySelector('.receipt-ack-btn').addEventListener('click', async () => {
        const btn = item.querySelector('.receipt-ack-btn');
        btn.disabled = true;
        const rr = await fetch(`/api/receipts/${r.id}/ack`, { method: 'POST' });
        if (rr.ok) await refreshState();
        else { btn.disabled = false; alert('Failed.'); }
      });

      item.querySelector('.receipt-nr-btn').addEventListener('click', async () => {
        if (!confirm('Are you sure you have NOT received this payment? The admin will be notified.')) return;
        const btn = item.querySelector('.receipt-nr-btn');
        btn.disabled = true;
        // The two receipt buttons sit side by side on a phone, so a slip of
        // the thumb could tell the admin his payment never arrived. Asking
        // first costs one tap; a false alarm costs a confused conversation.
        if (!confirm('Report this payment as NOT received?\n\nOnly press OK '
            + 'if the money truly has not arrived. If you tapped this by '
            + 'accident, press Cancel.')) return;
        const rr = await fetch(`/api/receipts/${r.id}/not_received`, { method: 'POST' });
        if (rr.ok) {
          showToast('Marked as not received — admin has been notified.', 'ok', 4000);
          await refreshState();
        } else { btn.disabled = false; alert('Failed.'); }
      });
      list.appendChild(item);
    });
  }

  function formatKes(n) {
    if (Number.isInteger(n)) return String(n);
    return n.toFixed(2).replace(/\.?0+$/, '');
  }

  function renderAll({ fullActive }) {
    renderStats();
    renderQueue();
    if (fullActive) {
      fullRenderActive();
    } else {
      passiveUpdateActive();
    }
    renderRevisions();
    renderPendingComplete();
    renderReceipts();
  }

  function renderPendingComplete() {
    const banner = document.querySelector('[data-pending-complete-banner]');
    if (!banner) return;
    const list = banner.querySelector('[data-pending-complete-list]');
    const countEl = banner.querySelector('[data-pending-complete-count]');
    const items = state.pending_complete_titles || [];
    if (!items.length) {
      banner.hidden = true;
      list.innerHTML = '';
      return;
    }
    banner.hidden = false;
    countEl.textContent =
      `${items.length} title${items.length === 1 ? '' : 's'} awaiting review`;
    list.innerHTML = '';
    items.forEach((t) => {
      const row = document.createElement('div');
      row.className = 'pending-complete-item';
      row.innerHTML = `
        <div class="pci-title"></div>
        <div class="pci-meta mono muted"></div>
        ${t.comment ? '<div class="pci-comment"></div>' : ''}
      `;
      row.querySelector('.pci-title').textContent =
        realYear(t.year) ? `${t.title} (${realYear(t.year)})` : t.title;
      row.querySelector('.pci-meta').textContent = `submitted ${t.submitted_at}`;
      if (t.comment) row.querySelector('.pci-comment').textContent = `Your note: ${t.comment}`;
      list.appendChild(row);
    });
  }

  async function refreshState() {
    const data = await getJSON(stateUrl);
    if (!data) return;
    const newLockedId = data.locked && data.locked.id;
    const lockChanged = (newLockedId !== renderedLockedId);
    state = data;
    renderAll({ fullActive: lockChanged });
    document.dispatchEvent(new Event('pd-state-refreshed'));
  }

  // ── Actions ──────────────────────────────────────────────────────────────
  async function pullNext() {
    const sizeEl = document.getElementById('pull-size');
    const n = Math.max(1, parseInt(sizeEl.value || '50', 10));
    const r = await postForm('/pull_next', { n });
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function release() {
    if (!confirm('Return all unworked titles back to the pool?')) return;
    const r = await postForm('/release');
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function lockTitle(id) {
    const r = await postForm('/lock/' + id);
    if (!r.ok) {
      alert('Failed to open title: ' + (r.data && r.data.detail || r.status));
      return;
    }
    // We do NOT auto-open the outside site — the worker clicks when ready.
    await refreshState();
    // On a phone the list and the work area are stacked, so opening a
    // title used to leave you staring at the list you just clicked and
    // scrolling by hand (owner's ask, 2026-09-06). Opening a title means
    // "I am going to work on it now" — go there.
    if (window.innerWidth <= 900) {
      requestAnimationFrame(() => {
        const panel = document.querySelector('[data-active-panel]');
        if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
  }

  // ── The floating "back to the work" button ──────────────────────────
  // Appears only while a title is open AND its panel is off the top or the
  // bottom of the screen — the DONE PICKING button's twin, pointing the
  // other way. One tap returns to the search/save area from anywhere.
  (function () {
    const jump = document.getElementById('jump-to-work');
    if (!jump || !('IntersectionObserver' in window)) return;
    let panelVisible = true;

    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => { panelVisible = en.isIntersecting; });
      sync();
    }, { threshold: 0.05 });

    function sync() {
      const hasOpen = !!(state && state.locked);
      jump.hidden = panelVisible || !hasOpen;
      if (!jump.hidden) {
        const panel = document.querySelector('[data-active-panel]');
        if (panel) {
          const below = panel.getBoundingClientRect().top > window.innerHeight;
          jump.querySelector('span').textContent = below ? '↓' : '↑';
        }
      }
    }

    const panel = document.querySelector('[data-active-panel]');
    if (panel) io.observe(panel);
    document.addEventListener('pd-state-refreshed', sync);
    window.addEventListener('scroll', () => { if (!jump.hidden || (state && state.locked)) sync(); },
                            { passive: true });
    jump.addEventListener('click', () => {
      const p2 = document.querySelector('[data-active-panel]');
      if (p2) p2.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  })();

  async function unlock() {
    await postForm('/unlock');
    await refreshState();
  }

  async function doSave(urlInput, msgEl, flashEl, opts = {}) {
    const url = (urlInput.value || '').trim();
    if (!url) { msgEl.textContent = 'Paste a URL first.'; msgEl.className = 'save-msg err'; return; }
    msgEl.textContent = 'Saving…'; msgEl.className = 'save-msg';
    const r = await postForm('/save_image', {
      url,
      confirm_duplicate:    opts.confirm_duplicate ? 1 : 0,
      confirm_cross_title:  opts.confirm_cross_title ? 1 : 0,
      confirm_soft_limit:   opts.confirm_soft_limit ? 1 : 0,
      confirm_low_quality:  opts.confirm_low_quality ? 1 : 0,
    });
    if (r.ok) {
      msgEl.textContent = `Saved ${r.data.filename} (${r.data.saved_count_for_title} on this title).`;
      msgEl.className   = 'save-msg ok';
      flashEl.classList.add('flash');
      setTimeout(() => flashEl.classList.remove('flash'), 220);
      urlInput.value = '';
      await refreshState();
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'low_quality') {
      if (confirm(r.data.message + '\n\nClick OK to save it anyway, or Cancel to go back and copy the full-size link.')) {
        return doSave(urlInput, msgEl, flashEl, { ...opts, confirm_low_quality: true });
      }
      msgEl.textContent = 'Cancelled.'; msgEl.className = 'save-msg';
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'duplicate') {
      if (confirm(r.data.message + ' (Already saved as ' + r.data.filename + ')')) {
        return doSave(urlInput, msgEl, flashEl, { ...opts, confirm_duplicate: true });
      }
      msgEl.textContent = 'Cancelled.'; msgEl.className = 'save-msg';
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'cross_title_duplicate') {
      if (confirm(r.data.message)) {
        return doSave(urlInput, msgEl, flashEl, { ...opts, confirm_cross_title: true });
      }
      msgEl.textContent = 'Cancelled — same image was on another title.'; msgEl.className = 'save-msg';
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'soft_limit') {
      if (confirm(r.data.message)) {
        return doSave(urlInput, msgEl, flashEl, { ...opts, confirm_soft_limit: true });
      }
      msgEl.textContent = 'Cancelled.'; msgEl.className = 'save-msg';
      return;
    }
    msgEl.textContent = 'Save failed: ' + (r.data && r.data.detail || r.status);
    msgEl.className   = 'save-msg err';
  }

  async function deletePoster(posterId, { fromRevision }) {
    // ── A DELETION IS NOT AN EVENT ANYBODY READS ABOUT ────────────────────
    //
    // This used to demand a reason from three preset buttons. The owner had
    // them removed (2026-09-10): "if a worker wants to delete, it should
    // only ask if they are sure, not give a reason, because the reason would
    // be given when they go to skip it."
    //
    // The presets were worse than merely useless. They were written for the
    // MOVIE project, where a film needed three posters and the interesting
    // question was why you had fewer — so they said "Only 0 usable posters
    // available", a word this project does not use, about a count that
    // cannot mean anything when a title takes ONE image. A question nobody
    // can answer sensibly gets answered by reflex, which is the same defect
    // as a warning that fires on the normal case.
    //
    // THE FLAGGED PATH KEEPS ITS NOTE, and that distinction is the whole
    // point. A deletion the ADMIN asked for goes back to the admin for
    // approval, so a real person reads the sentence. An ordinary delete has
    // no reader, and the reason that matters arrives at SKIP.
    let result;
    if (fromRevision) {
      const picked = await pickReason({
        title: `Delete this ${PD.noun}?`,
        sub:   `The admin flagged this ${PD.noun}. Say why you are deleting `
             + `it rather than fixing it — the admin reads this and has to `
             + `approve it.`,
        presets: [],
        allowEmpty: false,
      });
      if (picked === null) return;
      result = picked;
    } else {
      const ok = await pickReason({
        title: `Delete this ${PD.noun}?`,
        sub:   `This permanently removes the file. You can search again and `
             + `save a different one.`,
        hint:  `💡 Just swapping it? Use REPLACE instead — paste a new URL above.`,
        presets: [],
        allowEmpty: true,
        emptyLabel: 'DELETE',
        noText: true,
      });
      if (ok === null) return;
      result = { text: '', source: '' };
    }

    const r = await postForm(`/poster/${posterId}/delete`,
                             { note: result.text || '',
                               reason_source: result.source || '' });
    if (r.ok) {
      await refreshState();
      // If the delete was on a flagged poster, the server responds with
      // submitted_for_approval:true so we can tell the worker their action
      // is pending (not silently complete). UI-wise the flag card will now
      // render in awaiting-approval state too, so this toast is a nudge.
      if (r.data && r.data.submitted_for_approval) {
        showToast('Deletion sent to admin for approval.', 'ok', 5000);
      }
      return;
    }
    alert('Delete failed: ' + (r.data && r.data.detail || r.status));
  }

  async function completeTitle(masterId, doneCommentEl) {
    // Force a fresh state read first — this defeats both browser caching
    // and any race where parallel saves resolved out of order.
    await refreshState();
    const live = state.locked;
    if (!live || live.id !== masterId) {
      alert('The active title changed in the background — please reopen it.');
      return;
    }
    let comment = (doneCommentEl.value || '').trim();
    let reason_source = comment ? 'manual' : '';
    const liveCount = (live.posters || []).length;
    // The target comes from the project, not a hardcoded 3. A MUSIK title
    // with 2 images is COMPLETE and must not be interrogated about it.
    // ONE fallback, not two. This read `|| 3` while the search grid
    // twenty lines of scrolling away read `|| 2` — two copies of one
    // fact, already disagreeing, both of them a movie-era number. A
    // project that does not say gets ONE, which is the only count that
    // cannot ask a worker for images the project never wanted.
    const limit = live.images_per_title || 1;
    const noun  = live.item_noun  || PD.noun;
    const nouns = live.item_nouns || PD.nouns;
    if (liveCount < limit && !comment) {
      const result = await pickReason({
        title: 'Confirm completion',
        sub: `This title only has ${liveCount} ${liveCount === 1 ? noun : nouns} saved ` +
             `out of ${limit}. Pick a reason — or confirm anyway if that's fine here.`,
        // "Only N usable" is a one-click excuse. It made sense when a movie
        // needed 3 posters and the source sometimes only had 1. For a 2-image
        // project with abundant sources it is almost always laziness, so it
        // is offered only where the target is 3 or more.
        presets: (limit >= 3
          ? [`Only ${liveCount} usable ${liveCount === 1 ? noun : nouns} available`,
             `All the ${nouns} available are similar`]
          : [`Both ${nouns} available are too similar`]),
        allowEmpty: true,
        emptyLabel: 'No reason — confirm anyway',
      });
      if (result === null) return;
      comment = result.text;
      reason_source = result.source;
    }
    return submitComplete(masterId, comment, reason_source);
  }

  async function submitComplete(masterId, comment, reason_source) {
    const r = await postForm(`/title/${masterId}/complete`,
                             { comment, reason_source: reason_source || '' });
    if (r.ok) {
      // Two possible "ok" responses now:
      //  - {pending_approval: true} → title routed to complete_pending state
      //    because the worker made changes on a flagged title. Admin must
      //    approve the whole batch.
      //  - default ok → title went straight to complete (no pending state).
      await refreshState();
      if (r.data && r.data.pending_approval) {
        showToast(
          'Sent to admin for approval. The title will show "awaiting approval" until they review your changes.',
          'ok', 6000,
        );
      }
      return;
    }
    alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function skipTitle(id, reason) {
    let reason_source = reason ? 'manual' : '';
    if (!reason) {
      const result = await pickReason({
        title: 'Why are you skipping this title?',
        sub:   'Pick a common reason or type your own. Skipped titles go to the admin for review.',
        // Free text only, deliberately. A preset here is a one-click way to
        // skip without thinking; for an artist you genuinely want to know
        // WHY — obscure act, no photos, wrong person entirely.
        presets: [],
        allowEmpty: false,
      });
      if (result === null) return;
      reason = result.text;
      reason_source = result.source;
    }
    const r = await postForm(`/title/${id}/skip`,
                             { reason: reason || '',
                               reason_source: reason_source || '' });
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function replacePoster(posterId, urlInput, opts = {}) {
    const url = (urlInput.value || '').trim();
    if (!url) { alert('Paste a replacement URL first.'); urlInput.focus(); return; }
    const r = await postForm(`/poster/${posterId}/replace`, {
      url,
      confirm_low_quality: opts.confirm_low_quality ? 1 : 0,
    });
    if (r.ok) {
      urlInput.value = '';
      await refreshState();
      // Cache-busting via ?v=size usually shows the new image right away. The
      // toast is a fallback hint in case the browser is being stubborn.
      if (r.data && r.data.submitted_revisions && r.data.submitted_revisions.length) {
        showToast('Replacement sent for admin approval. (If the image doesn\'t update, try refreshing.)', 'ok', 5000);
      } else {
        showToast('Replacement saved. (If the image doesn\'t update, try refreshing.)', 'ok', 5000);
      }
      return;
    }
    if (r.status === 409 && r.data && r.data.reason === 'low_quality') {
      if (confirm(r.data.message + '\n\nClick OK to use it anyway, or Cancel to go back and copy the full-size link.')) {
        return replacePoster(posterId, urlInput, { ...opts, confirm_low_quality: true });
      }
      return;
    }
    let msg = 'Replace failed: ' + (r.data && r.data.detail || r.status);
    if (r.status === 400 && r.data && /image|url/i.test(r.data.detail || '')) {
      msg += `\nMake sure you're copying the LINK address (not the image address) from the full-size ${PD.noun}.`;
    }
    alert(msg);
  }

  async function reopenTitle(id) {
    const r = await postForm(`/title/${id}/reopen`);
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  async function resolveRevision(id) {
    const note = prompt('Optional note for the admin (e.g. "redownloaded HD version"). Leave blank to send anyway.');
    if (note === null) return;
    const r = await postForm(`/revisions/${id}/resolve`, { worker_note: note });
    if (r.ok) await refreshState();
    else alert('Failed: ' + (r.data && r.data.detail || r.status));
  }

  // ── Wire up static buttons ───────────────────────────────────────────────
  const pullBtn    = document.getElementById('btn-pull-next');
  const releaseBtn = document.getElementById('btn-release');
  if (pullBtn)    pullBtn.addEventListener('click', pullNext);
  if (releaseBtn) releaseBtn.addEventListener('click', release);

  renderAll({ fullActive: true });
  setInterval(refreshState, 8000);
})();


/* ═══════════════════════════════════════════════════════════════════════════
   IN-PAGE IMAGE SEARCH
   For projects that search inside the site instead of sending the worker to
   an external source. Tap to select, tap again to deselect, save the lot.

   The selection is deliberately capped at the project's images-per-title:
   letting someone select five and then rejecting three of them at save time
   wastes their effort and reads as a bug.
   ═══════════════════════════════════════════════════════════════════════════ */

function wireSearch(box, title) {
  if (box.dataset.wiredFor === String(title.id)) return;   // re-render, same title
  box.dataset.wiredFor = String(title.id);

  const grid    = box.querySelector('[data-search-grid]');
  const saveBtn = box.querySelector('[data-action="search-save"]');
  const clearBtn= box.querySelector('[data-action="search-clear"]');
  const barCount= box.querySelector('.search-bar-count');
  const status  = box.querySelector('.search-status');
  const note    = box.querySelector('[data-search-note]');

  const head    = box.querySelector('.search-head');
  const jump    = box.querySelector('[data-search-jump]');

  const limit    = title.images_per_title || 1;   // see submitComplete
  const selected = new Set();
  let probed     = false;   // has the free cached-results check come back yet

  // Is the button row actually off screen? Asking rather than assuming means
  // the jump button never appears pointing at something already visible —
  // which is what would happen on a desktop, or on a short result list.
  let headVisible = true;
  if (head && 'IntersectionObserver' in window) {
    new IntersectionObserver(([entry]) => {
      headVisible = entry.isIntersecting;
      refreshJump();
    }, { threshold: 0.6 }).observe(head);
  }

  function backToActions() {
    // 'center' rather than 'start': DONE and SKIP sit just ABOVE the save
    // row, and the point is to reach all three at once.
    head.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function refreshJump() {
    if (!jump) return;
    // Nothing floats until the opening cache probe has answered. Without
    // this, a title with cached results showed SEARCH for a frame and then
    // took it away again as the results landed.
    if (!probed) { jump.hidden = true; return; }
    const room = Math.max(0, limit - alreadySaved());
    // Only once they have picked everything this title can take. Showing it
    // at one-of-two would interrupt someone mid-choice.
    jump.hidden = !(room > 0 && selected.size >= room && !headVisible);
  }

  // Scrolls, and does NOT save. Reverted deliberately: saving is
  // irreversible from the worker's side, and a big button under the thumb
  // that commits their picks the moment it is brushed is the wrong trade.
  // It takes them TO the save button, and they press that.
  if (jump) jump.addEventListener("click", () => { backToActions(); jump.hidden = true; });

  let results    = [];
  // How many of the results name the place, how many do not, and
  // whether the worker has asked to see the off-topic ones. Held
  // here rather than recomputed, so the count on the button and the
  // number of cards drawn can never disagree.
  let onTopic    = 0;
  let offTopic   = 0;
  let showAll    = false;
  let noteBits   = [];

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function alreadySaved() { return (title.posters || []).length; }

  function refreshBar() {
    const room = Math.max(0, limit - alreadySaved());
    saveBtn.disabled  = selected.size === 0;
    clearBtn.disabled = selected.size === 0;
    barCount.textContent = `${selected.size} selected · ${alreadySaved()}/${limit} saved`;

    // ── SAY WHY THE PICTURES ARE DEAD ─────────────────────────────────────
    //
    // With one image per title, EVERY title enters this state the moment the
    // worker saves — so it is the normal case, not a corner. The owner met
    // it on his phone after saving on the PC and read it as a broken page:
    // "the images are there but greyed out, I click them and nothing
    //  happens, so essentially I cant do any replacement" (2026-09-10).
    //
    // Nothing was broken. The grid had correctly refused a second pick and
    // said so only in a small line reading "0 selected · 1/1 saved", which
    // is the answer to a question he had not thought to ask. A working
    // feature that looks like a fault is a bad feature — so the reason goes
    // where the dead pictures are, in words, with the way out beside it.
    if (note) {
      if (room === 0 && alreadySaved() >= limit) {
        note.innerHTML =
          `You already have your ${limit === 1 ? '' : limit + ' '}`
          + `${limit === 1 ? PD.noun : PD.nouns} for this title, so nothing `
          + `else can be picked. `
          + `<button type="button" class="btn btn-ghost btn-tiny" `
          + `data-action="search-free-slot">SWAP IT FOR ANOTHER</button>`;
        note.hidden = false;
      } else if (note.dataset.owned === 'cap') {
        note.hidden = true;
      }
      note.dataset.owned = (room === 0 && alreadySaved() >= limit) ? 'cap' : '';
    }
    grid.querySelectorAll('.sr-card').forEach((card) => {
      const on = selected.has(card.dataset.url);
      card.classList.toggle('is-selected', on);
      const badge = card.querySelector('.sr-badge');
      badge.hidden = !on;
      if (on) badge.textContent = String([...selected].indexOf(card.dataset.url) + 1);
      // Grey out the rest once the worker has picked their quota.
      card.classList.toggle('is-blocked', !on && selected.size >= room);
    });
    refreshJump();
  }

  function render() {
    if (!results.length) {
      // NAMES THE NEXT STEP, rather than just reporting the emptiness.
      // This is the moment the Google button exists for, and a worker
      // staring at "no images found" should not have to work that out.
      grid.innerHTML = '<p class="muted">Nothing found. Try one of the other '
                     + 'search buttons, or open Google above and paste an '
                     + 'image address. If Google has nothing either, SKIP '
                     + 'this title and say why.</p>';
      return;
    }
    // ── HIDE THE OFF-TOPIC ONES, BUT NEVER THROW THEM AWAY ──────────────
    //
    // The server has already put the results that NAME the place first. The
    // rest are not rubbish — a good photograph on a Kisumu page may be
    // titled "Sunset over the lake", and naming the place is not its job —
    // so they are folded away behind a count rather than deleted.
    //
    // A COUNT WITH AN ESCAPE, not a silent trim. The owner's own rule for
    // this screen: a filter that quietly thins the grid is indistinguishable
    // from a bad search, and the worker can only tell the difference if the
    // screen says which happened.
    const shown = showAll ? results : results.slice(0, Math.max(onTopic, 0));
    const drawn = shown.length ? shown : results;   // never draw an empty grid

    grid.innerHTML = drawn.map((r) => `
      <div class="sr-card" data-url="${esc(r.url)}">
        <img class="sr-img" loading="lazy" src="${esc(r.thumb)}" alt="">
        <span class="sr-badge" hidden></span>
        <div class="sr-caption">
          <span class="sr-title">${esc(r.title || '')}</span>
          <span class="sr-dim mono">${r.width || '?'}×${r.height || '?'}</span>
        </div>
      </div>`).join('');

    if (note) {
      const parts = [];
      if (noteBits.length) parts.push(`Hidden: ${noteBits.join(', ')}.`);
      if (offTopic > 0 && shown.length) {
        const place = (title.title || '').split(',')[0].trim();
        parts.push(showAll
          ? `Showing everything, including ${offTopic} that do not mention `
            + `${esc(place)}. <button type="button" class="btn btn-ghost `
            + `btn-tiny" data-action="search-fewer">SHOW THE BEST ONLY</button>`
          : `${offTopic} more hidden that do not mention ${esc(place)}. `
            + `<button type="button" class="btn btn-ghost btn-tiny" `
            + `data-action="search-show-all">SHOW THEM</button>`);
      }
      if (parts.length) { note.innerHTML = parts.join(' '); note.hidden = false; }
      else if (note.dataset.owned !== 'cap') { note.hidden = true; }
    }

    grid.querySelectorAll('.sr-card').forEach((card) => {
      // A thumbnail that 404s is noise the worker can't act on — drop it.
      card.querySelector('.sr-img').addEventListener('error', () => card.remove());
      card.addEventListener('click', () => {
        const url = card.dataset.url;
        if (selected.has(url)) selected.delete(url);
        else {
          const room = Math.max(0, limit - alreadySaved());
          if (selected.size >= room) {
            // A CLICK THAT DOES NOTHING AT ALL IS THE FAULT. Silence here is
            // what made a working cap read as a dead page. Whichever kind of
            // full it is, the person is told which.
            status.textContent = (alreadySaved() >= limit)
              ? `You already have your ${limit === 1 ? PD.noun : limit + ' ' + PD.nouns}`
                + ` for this title — use SWAP IT FOR ANOTHER to change it.`
              : `That is ${limit === 1 ? 'the one' : 'all ' + limit} you can`
                + ` pick. Unpick one first.`;
            return;
          }
          selected.add(url);
        }
        refreshBar();
      });
    });
    refreshBar();
  }

  // `kind` is 'normal' or a number — the index of one of the owner's own
  // phrasings. A number rather than the words themselves because the server
  // must decide what gets searched: letting the page post a sentence would
  // let anyone with the console open spend Brave credits on anything they
  // liked.
  async function run(kind, force, cacheOnly) {
    selected.clear();
    refreshJump();
    if (!cacheOnly) {
      status.textContent = 'searching…';
      grid.innerHTML = '<p class="muted">Searching…</p>';
    }
    note.hidden = true;
    try {
      const phraseIndex = (typeof kind === 'number') ? kind : -1;
      const qs = `?`
               + (phraseIndex >= 0 ? `phrase=${phraseIndex}` : '')
               + (force ? '&refresh=1' : '')
               + (cacheOnly ? '&cache_only=1' : '');
      const r  = await fetch(`/api/search/${title.id}${qs}`);
      const d  = await r.json();
      if (!r.ok || !d.ok) {
        grid.innerHTML = `<p class="error">${esc(d.message || 'Search failed.')}</p>`;
        status.textContent = '';
        return;
      }
      if (d.not_searched) {
        // Opened without searching. Nothing spent, nothing shown.
        grid.innerHTML = '<p class="muted">Press SEARCH to look for images.</p>';
        status.textContent = '';
        results = [];
        refreshBar();
        return;
      }
      results = d.results || [];
      // The words the SERVER used, never the words on the button. If those
      // two ever disagree — because the admin edited the list a moment ago —
      // the worker needs to see which one actually happened.
      // Only the owner's OWN words, never the raw template. The server
      // sends the phrasing with {title}/{kind} still in it; showing that to
      // a worker is noise they cannot act on (owner's ask, 2026-09-06).
      const shownPhrase = String(d.phrase_used || '')
        .replace(/\{[a-z_]+\}/gi, ' ').replace(/\s{2,}/g, ' ').trim();
      status.textContent = `${results.length} result${results.length === 1 ? '' : 's'}`
                         + (shownPhrase ? ` · ${shownPhrase}` : '')
                         + (d.cached ? ' · cached' : '');
      // ── WHAT THE FILTERS DID, IN WORDS ────────────────────────────────
      //
      // A grid that is thinner than expected has two possible causes — a
      // poor search, or our own filtering — and they call for opposite
      // reactions. Saying which happened is the difference between "try
      // another phrasing" and "that is fine, carry on".
      const bits = [];
      if (d.filtered_small) {
        bits.push(`${d.filtered_small} too small`);
      }
      if (d.filtered_junk) {
        bits.push(`${d.filtered_junk} maps, flags or clipart`);
      }
      onTopic  = Number(d.on_topic || 0);
      offTopic = Math.max(0, results.length - onTopic);
      noteBits = bits;
      showAll  = false;
      render();
    } catch (e) {
      grid.innerHTML = `<p class="error">Search failed: ${esc(e.message)}</p>`;
      status.textContent = '';
    } finally {
      probed = true;
      refreshJump();
    }
  }

  box.querySelector('[data-action="search-normal"]')
     .addEventListener('click', () => run('normal', false));
  // querySelectorAll, because there are as many of these as the admin typed
  // lines — possibly none, in which case this loop simply does nothing.
  box.querySelectorAll('[data-action="search-phrase"]').forEach((b) => {
    b.addEventListener('click', () => run(Number(b.dataset.phraseIndex), false));
  });
  box.querySelector('[data-action="search-clear"]')
     .addEventListener('click', () => { selected.clear(); refreshBar(); });

  // Delegated, because the button is drawn by refreshBar() and replaced
  // every time the bar redraws — a listener bound to the element itself
  // would be attached to a button that no longer exists.
  if (note) {
    note.addEventListener('click', (e) => {
      if (e.target.closest('[data-action="search-show-all"]')) {
        showAll = true;  render();  return;
      }
      if (e.target.closest('[data-action="search-fewer"]')) {
        showAll = false; render();  return;
      }
      const b = e.target.closest('[data-action="search-free-slot"]');
      if (!b) return;
      document.dispatchEvent(new CustomEvent('pd:swap-saved'));
    });
  }

  // Extracted so the floating button and the toolbar button are the SAME
  // action rather than two implementations that drift apart. The floating one
  // previously only scrolled, which read as "save" and quietly did nothing —
  // the worst possible split between what a button says and what it does.
  async function saveSelected() {
    const btn = box.querySelector('[data-action="search-save"]');
    if (btn.disabled || !selected.size) return 0;
    btn.disabled = true;
    const urls = [...selected];
    let saved = 0;
    for (const url of urls) {
      btn.textContent = `SAVING ${saved + 1}/${urls.length}…`;
      try {
        const send = async (replace) => {
          const fd = new FormData();
          fd.append('url', url);
          if (replace) fd.append('replace', '1');
          const r = await fetch(`/api/search_save/${title.id}`,
                                { method: 'POST', body: fd });
          return { r, d: await r.json() };
        };
        let { r, d } = await send(false);
        // Already holding the maximum? Offer to SWAP rather than dead-end.
        // Picking a better shot from the same grid is the normal case; the
        // old flow refused and made you delete first.
        if (!r.ok && d && d.reason === 'soft_limit' && d.can_replace) {
          if (!confirm('You already saved an image for this title.\n\n'
                     + 'Replace it with this one?')) { break; }
          ({ r, d } = await send(true));
        }
        if (!r.ok || !d.ok) { alert(d.message || 'Save failed.'); break; }
        saved += 1;
      } catch (e) { alert('Save failed: ' + e.message); break; }
    }
    // ── THE BUTTON GOES STRAIGHT BACK TO NORMAL ────────────────────────
    //
    // It used to rename itself to "LOADING IMAGE…" and stay that way while
    // the thumbnail downloaded, because the fetch finishes before the
    // picture is on screen and a button that looks idle gets pressed twice.
    // The cure was worse than the problem: the control the worker had just
    // pressed turned into different words and, since it was never
    // re-enabled, sat there greyed out reading "LOADING IMAGE…" until
    // something else re-rendered the box (owner's find, 2026-09-09).
    //
    // The waiting is now shown by a spinner beside the saved-images count,
    // which is where the picture is about to appear. The button says one
    // thing for its whole life.
    btn.textContent = 'SAVE SELECTED';
    btn.disabled = false;
    selected.clear();
    refreshBar();

    if (saved) showThumbLoading(true);
    try {
      await refreshState();
      if (saved) await waitForSavedThumbs();
    } finally {
      // In a `finally` because a failed refresh must not leave a spinner
      // turning for ever — a busy state with nothing behind it is exactly
      // the fault this whole change is fixing.
      showThumbLoading(false);
    }

    // Saving is the end of the picking, so it ends by putting DONE and SKIP
    // in front of you. Without this you are left at the bottom of a long
    // grid having to scroll back to the only two buttons that matter next.
    if (saved) backToActions();
    return saved;
  }

  box.querySelector('[data-action="search-save"]')
     .addEventListener('click', () => saveSelected());

  // Do NOT search on open. Opening a title is not the same as wanting a
  // search: a worker reopening something to check what they saved would
  // otherwise spend a paid query they never asked for. Cached results from
  // an earlier search on this title still appear, because those are free.
  run('normal', false, true);
}
