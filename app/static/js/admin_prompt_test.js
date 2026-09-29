/* PROMPT TEST MODE — the panel under WAITING FOR REVIEW on Approve Artwork.
 *
 * The owner is tuning the painting prompt (2026-09-29). While the switch is
 * on, every repaint waits in a pile; TEST THE PROMPT ON N paints the N
 * oldest with the prompt typed here and makes them a ROUND; the round is
 * reviewed on the ordinary screen (window.ReviewScreen.openRound), judged
 * KEEP or RERUN only; and the table keeps each round's score beside the
 * prompt's name. The rules live on the server in app/prompt_test.py — this
 * file only shows them and sends the presses.
 */
(function () {
  'use strict';

  const API = '/admin/pipeline/api/prompt_test';
  const panel = document.querySelector('[data-prompt-test]');
  if (!panel) return;
  const q = (sel) => panel.querySelector(sel);

  const onBox    = q('[data-pt-on]');
  const pileEl   = q('[data-pt-pile]');
  const nameEl   = q('[data-pt-name]');
  const textEl   = q('[data-pt-text]');
  const savedSel = q('[data-pt-saved]');
  const sizeEl   = q('[data-pt-size]');
  const sizeLbl  = q('[data-pt-size-label]');
  const roundNote = q('[data-pt-round-note]');
  const roundsEl = q('[data-pt-rounds]');
  const delNote  = q('[data-pt-delete-note]');
  const summary  = q('[data-pt-summary]');

  let state = null;
  let pollTimer = null;
  // The prompt being written survives a reload of the page (it is only
  // saved on the server once a round is painted with it).
  const DRAFT_KEY = 'pd.promptTest.draft';

  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  function say(msg, kind) {
    if (window.toast) window.toast(msg, kind);
  }

  async function send(path, body) {
    const r = await fetch(`${API}/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    let d = {};
    try { d = await r.json(); } catch (e) { d = {}; }
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d;
  }

  function saveDraft() {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(
        { name: nameEl.value, text: textEl.value }));
    } catch (e) { /* a blocked store must never break the screen */ }
  }
  function loadDraft() {
    try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); }
    catch (e) { return null; }
  }

  function promptWords() {
    const name = nameEl.value.trim();
    return name || (textEl.value.trim() ? '(no name yet)' : 'the main prompt');
  }

  async function refresh() {
    let d;
    try {
      const r = await fetch(`${API}/state`, { cache: 'no-store' });
      d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    } catch (e) {
      roundsEl.innerHTML = `<tr><td colspan="5" class="error">Could not load the test panel: ${esc(e.message)}</td></tr>`;
      return;
    }
    const first = state === null;
    state = d;
    render(first);
  }

  function render(first) {
    const d = state;
    onBox.checked = !!d.on;
    if (document.activeElement !== sizeEl) sizeEl.value = d.round_size;
    sizeLbl.textContent = sizeEl.value || d.round_size;
    panel.classList.toggle('is-on', !!d.on);

    // The pile, and what will happen to it — said in words, whether the
    // switch is on or off, because the pile outlives the switch.
    if (d.pile) {
      pileEl.innerHTML = `<strong>${d.pile}</strong> picture(s) in the pile, waiting for TEST THE PROMPT.`
        + (d.on ? '' : ' Test mode is off, so new reruns paint straight away, but these stay here until you paint them with the button below.');
    } else {
      pileEl.textContent = d.on
        ? 'The pile is empty. Mark pictures RERUN on the review screen to fill it.'
        : '';
    }
    summary.textContent = d.on ? `ON · ${d.pile} in the pile` : (d.pile ? `off · ${d.pile} still in the pile` : 'off');

    // The saved prompts, newest first.
    const keep = savedSel.value;
    savedSel.innerHTML = '<option value="">—</option>' + d.prompts.map((p) =>
      `<option value="${p.id}">${esc(p.name)}</option>`).join('');
    savedSel.value = keep && d.prompts.some((p) => String(p.id) === keep) ? keep : '';

    // First load: the draft being written, or else the newest saved prompt.
    if (first && !nameEl.value && !textEl.value) {
      const draft = loadDraft();
      if (draft && (draft.name || draft.text)) {
        nameEl.value = draft.name || '';
        textEl.value = draft.text || '';
      } else if (d.prompts.length) {
        nameEl.value = d.prompts[0].name;
        textEl.value = d.prompts[0].text;
      }
    }

    const btn = q('[data-pt-action="round"]');
    btn.disabled = !d.pile;
    roundNote.textContent = d.pile
      ? `Paints the ${Math.min(Number(sizeEl.value) || d.round_size, d.pile)} oldest in the pile with ${promptWords()}.`
      : 'The pile is empty, so there is nothing to paint.';

    renderRounds();

    const blockers = d.delete_blockers || [];
    q('[data-pt-action="delete"]').disabled = blockers.length > 0;
    delNote.textContent = blockers.length
      ? blockers.join(' ')
      : (d.rounds.length || d.prompts.length
          ? 'Removes the saved test prompts, the rounds and the scores. Paintings are never deleted.'
          : 'There is no test data.');

    // Keep the table current while a round is being painted.
    const busy = d.rounds.some((r) => r.painting > 0);
    clearTimeout(pollTimer);
    if (busy && !panel.hidden) pollTimer = setTimeout(refresh, 15000);
  }

  function renderRounds() {
    const rounds = state.rounds;
    if (!rounds.length) {
      roundsEl.innerHTML = '<tr><td colspan="5" class="muted">No rounds yet.</td></tr>';
      return;
    }
    roundsEl.innerHTML = rounds.map((r) => {
      const painted = r.size - r.painting - r.failed - r.gone;
      const bits = [`${painted} of ${r.size} painted`];
      if (r.painting) bits.push(`${r.painting} still painting`);
      if (r.failed) bits.push(`${r.failed} failed — see Needs Attention`);
      if (r.gone) bits.push(`${r.gone} taken off their title`);
      const judged = r.kept + r.rerun + r.older;
      const score = judged
        ? `<strong>${r.kept}</strong> kept · <strong>${r.rerun}</strong> rerun`
          + (r.older ? ` · ${r.older} older version kept` : '')
          + ` <span class="muted">(${Math.round(100 * r.kept / judged)}% kept)</span>`
        : '<span class="muted">not judged yet</span>';
      const actions = [];
      if (r.waiting) {
        actions.push(`<button class="btn btn-primary btn-tiny" data-pt-review="${r.id}" data-pt-number="${r.number}">REVIEW ${r.waiting}</button>`);
      }
      if (r.can_retry) {
        actions.push(`<button class="btn btn-ghost btn-tiny" data-pt-retry="${r.id}" data-pt-number="${r.number}" data-pt-count="${r.rerun}" title="Paint the pictures you marked RERUN in this round again, with the prompt in the box now. The ones you kept are left alone.">TRY THIS ROUND AGAIN</button>`);
      }
      return `
        <tr>
          <td class="mono">${r.number}${r.retry_of ? ` <span class="muted">retry of ${r.retry_of}</span>` : ''}</td>
          <td title="${esc(r.prompt_text)}">${esc(r.prompt_name)}</td>
          <td class="mono">${bits.join(' · ')}</td>
          <td>${score}</td>
          <td>${actions.join(' ')}</td>
        </tr>`;
    }).join('');
  }

  // ── Presses ──────────────────────────────────────────────────────────────
  onBox.addEventListener('change', async () => {
    const want = onBox.checked;
    try {
      await send('settings', { on: want });
      say(want
        ? 'Prompt test mode is on. Reruns now wait in the pile.'
        : 'Prompt test mode is off. New reruns paint straight away again.');
    } catch (e) {
      onBox.checked = !want;
      say('Could not change test mode: ' + e.message, 'error');
    }
    refresh();
  });

  sizeEl.addEventListener('input', () => {
    sizeLbl.textContent = sizeEl.value || '?';
  });
  sizeEl.addEventListener('change', async () => {
    try {
      await send('settings', { round_size: Number(sizeEl.value) });
    } catch (e) {
      say('Could not save the round size: ' + e.message, 'error');
    }
    refresh();
  });

  nameEl.addEventListener('input', saveDraft);
  textEl.addEventListener('input', saveDraft);

  savedSel.addEventListener('change', () => {
    const p = state && state.prompts.find((x) => String(x.id) === savedSel.value);
    if (!p) return;
    nameEl.value = p.name;
    textEl.value = p.text;
    saveDraft();
    render(false);
  });

  panel.addEventListener('click', async (e) => {
    const act = e.target.closest('[data-pt-action]');
    const review = e.target.closest('[data-pt-review]');
    const retry = e.target.closest('[data-pt-retry]');

    if (review) {
      if (!window.ReviewScreen) return;
      const ok = await window.ReviewScreen.openRound(
        Number(review.dataset.ptReview), review.dataset.ptNumber);
      if (!ok) refresh();
      return;
    }

    if (retry) {
      const n = retry.dataset.ptCount;
      if (!confirm(`Paint again the ${n} picture(s) you marked RERUN in round ${retry.dataset.ptNumber}, with ${promptWords()}?\n\nEach one costs a generation. The pictures you kept are left alone.`)) return;
      try {
        const d = await send('retry', { round_id: Number(retry.dataset.ptRetry),
                                        name: nameEl.value, text: textEl.value });
        say(`Round ${d.round} started: ${d.pictures} picture(s) being painted.`);
      } catch (err) {
        say('Could not start the retry: ' + err.message, 'error');
      }
      refresh();
      return;
    }

    if (!act) return;
    const what = act.dataset.ptAction;

    if (what === 'copy-main') {
      if (textEl.value.trim() && !confirm('Replace what is in the box with the main prompt?')) return;
      textEl.value = (state && state.main_prompt) || '';
      nameEl.value = '';
      saveDraft();
      nameEl.focus();
      say('The main prompt is in the box. Give it a name, then edit it.');
      render(false);
      return;
    }

    if (what === 'round') {
      const size = Math.min(Number(sizeEl.value) || state.round_size, state.pile);
      if (textEl.value.trim() && !nameEl.value.trim()) {
        nameEl.focus();
        say('Give the prompt a name first, for example "prompt v12".', 'error');
        return;
      }
      if (!confirm(`Paint the ${size} oldest picture(s) in the pile with ${promptWords()}?\n\nEach one costs a generation.`)) return;
      try {
        const d = await send('round', { size, name: nameEl.value, text: textEl.value });
        say(`Round ${d.round} started: ${d.pictures} picture(s) being painted. Press REVIEW on its row when they are ready.`);
      } catch (err) {
        say('Could not start the round: ' + err.message, 'error');
      }
      refresh();
      return;
    }

    if (what === 'delete') {
      const failed = state.failed_in_rounds
        ? `\n\n${state.failed_in_rounds} round picture(s) failed to paint. After this they will be painted with the main prompt when retried.`
        : '';
      if (!confirm('Delete every saved test prompt, round and score?\n\nNo painting is deleted. This cannot be undone.' + failed)) return;
      try {
        const d = await send('delete');
        try { localStorage.removeItem(DRAFT_KEY); } catch (e2) { /* ignore */ }
        nameEl.value = '';
        textEl.value = '';
        say(`Deleted ${d.rounds} round(s) and ${d.prompts} prompt(s).`);
      } catch (err) {
        say('Could not delete: ' + err.message, 'error');
      }
      refresh();
    }
  });

  window.PromptTest = { refresh };
  refresh();
})();
