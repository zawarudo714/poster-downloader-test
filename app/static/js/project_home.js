/* The project home page. Renders what /admin/api/pulse says — the same
   endpoint the status strip polls, so this page and the strip can never
   disagree. pulse.js broadcasts each answer as a 'pulse' event; this file
   only draws. */

(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var stripEl = document.querySelector('[data-home-strip]');
  var cardsEl = document.querySelector('[data-home-cards]');
  var clearEl = document.querySelector('[data-home-clear]');
  var todayEl = document.querySelector('[data-home-today]');
  if (!stripEl) return;

  // Which card belongs to which badge, with words a stranger can act on.
  var CARDS = [
    { key: 'browse',    label: 'worker images to review',
      hint: 'Judge what the workers found', href: '/admin/browse' },
    { key: 'review',    label: 'artworks to approve',
      hint: 'Judge what the machine painted', href: '/admin/pipeline/review' },
    { key: 'revisions', label: 'fixes awaiting your verdict',
      hint: 'Workers have answered your flags', href: '/admin/revisions' },
    { key: 'attention', label: 'things stuck in the pipeline',
      hint: 'Failures waiting for a decision', href: '/admin/pipeline' },
  ];

  function render(d) {
    if (todayEl) todayEl.textContent = d.today_line || '';

    // ── The journey strip ────────────────────────────────────────────
    // Every step is shown, zeros included: on THIS page the zeros are the
    // shape of the funnel, not noise. (The cards below are the opposite.)
    var steps = d.strip || [];
    stripEl.innerHTML = steps.map(function (s, i) {
      return (i ? '<span class="journey-arrow" aria-hidden="true">→</span>' : '')
        + '<a class="journey-step' + (s.n ? ' has-some' : '') + '" href="' + esc(s.href) + '">'
        + '<span class="journey-n mono">' + s.n + '</span>'
        + '<span class="journey-lbl">' + esc(s.label) + '</span></a>';
    }).join('') || '<div class="muted">No numbers yet — the pulse endpoint '
                 + 'answered without a project. Open a project first.</div>';

    // ── The needs-you cards ──────────────────────────────────────────
    var b = d.badges || {};
    var cards = CARDS.filter(function (c) { return (b[c.key] || 0) > 0; });
    cardsEl.innerHTML = cards.map(function (c) {
      return '<a class="needs-card" href="' + esc(c.href) + '">'
        + '<span class="needs-n mono">' + b[c.key] + '</span>'
        + '<span class="needs-lbl">' + esc(c.label) + '</span>'
        + '<span class="needs-hint muted">' + esc(c.hint) + '</span></a>';
    }).join('');
    if (clearEl) clearEl.hidden = cards.length > 0;
  }

  document.addEventListener('pulse', function (e) { render(e.detail); });

  // First paint without waiting for the next poll tick.
  fetch('/admin/api/pulse', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(render)
    .catch(function () {
      stripEl.innerHTML = '<div class="muted">Could not load the numbers. '
        + 'Reload the page, and if it keeps happening tell the developer '
        + 'the pulse endpoint is failing.</div>';
    });
})();
