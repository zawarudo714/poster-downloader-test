/* The Usage tab (v292): what this project's OpenAI key has cost, drawn from
   /admin/api/usage. Numbers and charts only, by the owner's ask. Chart.js is
   loaded by the page, the same build the Stats tab uses. */
(function () {
  'use strict';

  const API = '/admin/api/usage';
  const root = document.querySelector('[data-usage-root]');
  if (!root || !window.Chart) return;

  // Fixed order, one colour per series whatever the filters show, so a
  // filter never repaints the survivors. Validated for colour-blind
  // separation on both themes (dataviz validate_palette.js, 2026-11-04).
  const SERIES = [
    ['s1_first',   'Painting'],
    ['s1_repaint', 'Painting repaint'],
    ['s2_first',   'Step 2'],
    ['s2_repaint', 'Step 2 redo'],
    ['test',       'Test'],
  ];
  const DARK  = { s1_first: '#9085e9', s1_repaint: '#d95926', s2_first: '#3987e5',
                  s2_repaint: '#c98500', test: '#8a8fa3', avg: '#e8e8f0' };
  const LIGHT = { s1_first: '#6250d6', s1_repaint: '#eb6834', s2_first: '#2a78d6',
                  s2_repaint: '#eda100', test: '#8a8fa3', avg: '#1b1b26' };

  const state = { days: 14, start: '', end: '', stage: 'all', kind: 'all',
                  mode: 'all', prompt: 'all' };
  const charts = {};

  // ── Pure helpers (lifted into node by tools/test_ai_usage.py) ──────────
  function pctChange(cur, prev) {
    if (cur == null || prev == null) return null;
    if (prev === 0) return cur === 0 ? 0 : null;
    return (cur - prev) / prev;
  }

  function rollingAvg(values, n) {
    const out = [];
    for (let i = 0; i < values.length; i++) {
      const win = values.slice(Math.max(0, i - n + 1), i + 1).filter((v) => v != null);
      out.push(win.length ? win.reduce((a, b) => a + b, 0) / win.length : null);
    }
    return out;
  }

  function fmtUsd(v) {
    if (v == null) return '—';
    return '$' + (Math.abs(v) < 1 ? v.toFixed(3) : v.toFixed(2));
  }

  // ── Drawing ────────────────────────────────────────────────────────────
  function palette() {
    return document.documentElement.classList.contains('theme-light') ? LIGHT : DARK;
  }

  function ink() {
    const cs = getComputedStyle(document.documentElement);
    return { text: cs.getPropertyValue('--subtext').trim() || '#999',
             grid: (cs.getPropertyValue('--border').trim() || '#333') + '66',
             card: cs.getPropertyValue('--card').trim() || '#1c1e2a' };
  }

  // Dashed line on each day a setting changed, with its label at the top.
  const markerPlugin = {
    id: 'usageMarkers',
    afterDatasetsDraw(chart, _args, opts) {
      const marks = (opts && opts.marks) || [];
      if (!marks.length) return;
      const { ctx, chartArea, scales } = chart;
      const days = chart.data.labels;
      ctx.save();
      ctx.strokeStyle = ink().text; ctx.fillStyle = ink().text;
      ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
      ctx.font = '10px ui-monospace, Consolas, monospace';
      const seen = {};
      marks.forEach((m) => {
        const i = days.indexOf(m.day);
        if (i < 0) return;
        const x = scales.x.getPixelForValue(i);
        ctx.beginPath(); ctx.moveTo(x, chartArea.top); ctx.lineTo(x, chartArea.bottom); ctx.stroke();
        const row = seen[m.day] = (seen[m.day] || 0) + 1;
        ctx.fillText(m.label, Math.min(x + 3, chartArea.right - 80), chartArea.top + 10 * row);
      });
      ctx.restore();
    },
  };

  function baseOptions(yFmt, marks) {
    const c = ink();
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: c.text, boxWidth: 10, font: { size: 11 } } },
        tooltip: { callbacks: { label: (it) => `${it.dataset.label}: ${yFmt(it.raw)}` } },
        usageMarkers: { marks },
      },
      scales: {
        x: { ticks: { color: c.text, font: { size: 10 } }, grid: { display: false } },
        y: { beginAtZero: true, grid: { color: c.grid },
             ticks: { color: c.text, font: { size: 10 }, callback: (v) => yFmt(v) } },
      },
    };
  }

  function draw(key, config) {
    if (charts[key]) charts[key].destroy();
    const canvas = root.querySelector(`[data-usage-chart="${key}"]`);
    config.plugins = [markerPlugin];
    charts[key] = new window.Chart(canvas, config);
  }

  const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
  const num = (v) => (v == null ? '—' : Math.round(v).toLocaleString());

  function line(label, data, color, extra) {
    return Object.assign({ type: 'line', label, data, borderColor: color,
      backgroundColor: color, borderWidth: 2, pointRadius: 3, pointHoverRadius: 5,
      spanGaps: true, tension: 0.25 }, extra || {});
  }

  function render(d) {
    const col = palette();
    const labels = d.days.map((x) => x.day);
    const marks = d.markers || [];

    const bars = SERIES.map(([k, label]) => ({
      type: 'bar', label, data: d.days.map((x) => x.spend[k]),
      backgroundColor: col[k], borderColor: ink().card, borderWidth: { top: 2 },
      borderRadius: 3, borderSkipped: 'bottom', stack: 'spend',
    })).filter((s) => s.data.some((v) => v > 0));
    const totals = d.days.map((x) => x.total);
    bars.push(line('7-day average', rollingAvg(totals, 7), col.avg,
                   { borderDash: [6, 4], pointRadius: 0, order: -1 }));
    const spendOpts = baseOptions(fmtUsd, marks);
    spendOpts.scales.x.stacked = true; spendOpts.scales.y.stacked = true;
    spendOpts.plugins.tooltip.callbacks.footer =
      (items) => 'Total ' + fmtUsd(totals[items[0].dataIndex]);
    draw('spend', { data: { labels, datasets: bars }, options: spendOpts });

    draw('repaint', { data: { labels, datasets: [
      line('Paintings', d.days.map((x) => x.repaint_rate), col.s1_repaint),
      line('Step 2', d.days.map((x) => x.step2_repaint_rate), col.s2_repaint),
    ] }, options: baseOptions(pct, marks) });

    draw('approved', { data: { labels, datasets: [
      line('Per approved design', d.days.map((x) => x.per_approved), col.s1_first),
    ] }, options: baseOptions(fmtUsd, marks) });

    draw('tokens', { data: { labels, datasets: [
      line('Text tokens per painting', d.days.map((x) => x.text_per_painting), col.s1_first),
    ] }, options: baseOptions(num, marks) });

    const countOpts = baseOptions(num, marks);
    countOpts.scales.x.stacked = true; countOpts.scales.y.stacked = true;
    draw('count', { data: { labels, datasets: [
      ['Painting', (x) => x.paintings - x.painting_repaints, col.s1_first],
      ['Painting repaint', (x) => x.painting_repaints, col.s1_repaint],
      ['Step 2', (x) => x.step2 - x.step2_repaints, col.s2_first],
      ['Step 2 redo', (x) => x.step2_repaints, col.s2_repaint],
    ].map(([label, f, color]) => ({
      type: 'bar', label, data: d.days.map(f), backgroundColor: color,
      borderColor: ink().card, borderWidth: { top: 2 }, borderRadius: 3,
      borderSkipped: 'bottom', stack: 'n',
    })) }, options: countOpts });

    renderTiles(d.tiles);
    renderTable(d.days);
    const sum = totals.reduce((a, b) => a + b, 0);
    root.querySelector('[data-usage-total]').textContent =
      `${fmtUsd(sum)} · ${d.start} → ${d.end} UTC`;
    root.querySelector('[data-usage-foot]').textContent =
      `batch $/M: text ${d.prices.batch_per_m.text_in} · image in ${d.prices.batch_per_m.image_in}`
      + ` · image out ${d.prices.batch_per_m.image_out} · live ×${d.prices.live_factor}`
      + (d.unrecorded_batches ? ` · ${d.unrecorded_batches} batches still being counted` : '');
    fillPrompts(d.prompt_versions);
  }

  // A tile's ▲/▼ compares with the 7 days before. For spend, cost and
  // repaints, down is good, so down wears the success colour.
  function tile(label, value, change, sub) {
    const arrow = change == null ? '' :
      `<span class="usage-delta ${change > 0.005 ? 'up' : change < -0.005 ? 'down' : ''}">`
      + `${change > 0 ? '▲' : change < 0 ? '▼' : '■'} ${Math.abs(Math.round(change * 100))}%</span>`;
    return `<div class="stat-tile"><div class="stat-label">${label}</div>`
      + `<div class="stat-value mono">${value}</div>`
      + `<div class="usage-tile-sub mono">${arrow} <span class="muted">${sub || ''}</span></div></div>`;
  }

  function renderTiles(t) {
    root.querySelector('[data-usage-tiles]').innerHTML = [
      tile('TODAY', fmtUsd(t.today.spend), pctChange(t.today.spend, t.yesterday.spend), 'vs yesterday'),
      tile('LAST 7 DAYS', fmtUsd(t.last7.spend), pctChange(t.last7.spend, t.prev7.spend), 'vs 7 before'),
      tile('MONTH SO FAR', fmtUsd(t.mtd.spend), null, `${t.mtd.approved} approved`),
      tile('MONTH FORECAST', fmtUsd(t.forecast), null, 'at the 7-day pace'),
      tile('PER APPROVED DESIGN', fmtUsd(t.last7.per_approved),
           pctChange(t.last7.per_approved, t.prev7.per_approved), '7 days'),
      tile('REPAINT RATE', pct(t.last7.repaint_rate),
           pctChange(t.last7.repaint_rate, t.prev7.repaint_rate), 'paintings, 7 days'),
    ].join('');
  }

  function renderTable(days) {
    const head = '<tr><th>Day</th><th>Spend</th><th>Paintings</th><th>Repaints</th>'
      + '<th>Step 2</th><th>Redos</th><th>Approved</th><th>Per approved</th><th>Text tokens</th></tr>';
    const rows = days.slice().reverse().map((x) => `<tr><td>${x.day}</td><td>${fmtUsd(x.total)}</td>`
      + `<td>${x.paintings}</td><td>${x.painting_repaints}</td><td>${x.step2}</td>`
      + `<td>${x.step2_repaints}</td><td>${x.approved}</td><td>${fmtUsd(x.per_approved)}</td>`
      + `<td>${num(x.text_per_painting)}</td></tr>`).join('');
    root.querySelector('[data-usage-table]').innerHTML =
      `<table class="usage-tbl mono">${head}${rows}</table>`;
  }

  function fillPrompts(versions) {
    const sel = root.querySelector('[data-usage-filter="prompt"]');
    if (sel.options.length > 1) return;
    versions.forEach((v) => {
      const from = v.from ? v.from.slice(0, 10) : 'start';
      const to = v.to ? v.to.slice(0, 10) : 'now';
      sel.add(new Option(`#${Number(v.id) + 1} · ${from} → ${to}`, v.id));
    });
  }

  async function load() {
    const qs = new URLSearchParams();
    if (state.start && state.end) { qs.set('start', state.start); qs.set('end', state.end); }
    else qs.set('days', state.days);
    ['stage', 'kind', 'mode', 'prompt'].forEach((k) => qs.set(k, state[k]));
    const r = await fetch(`${API}?${qs}`, { credentials: 'same-origin' });
    if (!r.ok) {
      root.querySelector('[data-usage-tiles]').innerHTML =
        `<div class="error">Could not load: ${r.status}</div>`;
      return;
    }
    render(await r.json());
  }

  root.querySelectorAll('[data-days]').forEach((b) => b.addEventListener('click', () => {
    root.querySelectorAll('[data-days]').forEach((x) => x.classList.toggle('active', x === b));
    state.days = Number(b.dataset.days); state.start = state.end = '';
    root.querySelector('[data-usage-start]').value = '';
    root.querySelector('[data-usage-end]').value = '';
    load();
  }));
  ['start', 'end'].forEach((k) => root.querySelector(`[data-usage-${k}]`)
    .addEventListener('change', (e) => {
      state[k] = e.target.value;
      if (state.start && state.end) {
        root.querySelectorAll('[data-days]').forEach((x) => x.classList.remove('active'));
        load();
      }
    }));
  root.querySelectorAll('[data-usage-filter]').forEach((s) => s.addEventListener('change', () => {
    state[s.dataset.usageFilter] = s.value; load();
  }));

  load();
  // Live while the tab is open: batches land a few times a day.
  setInterval(() => { if (!document.hidden) load(); }, 5 * 60 * 1000);
})();
