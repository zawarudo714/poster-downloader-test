/* Shared stats-page rendering used by both worker /stats and admin
   /admin/stats. Expects Chart.js (window.Chart) to already be loaded.

   The page-specific JS provides:
     - fetchStats():       returns a Promise<statsJSON>
     - includeAdminQuality: bool (admin-only quality panel toggle)

   Exposed: window.PDStats.render(data) re-renders all panels. */

(function () {
  function fmtKes(n) {
    if (typeof n !== 'number') n = parseFloat(n) || 0;
    if (!isFinite(n)) return '0';
    if (Number.isInteger(n)) return String(n);
    return n.toFixed(2).replace(/\.?0+$/, '');
  }

  function fmtPct(p) {
    if (p === null || p === undefined) return '—';
    if (!isFinite(p)) return '—';
    const sign = p > 0 ? '+' : '';
    return sign + p.toFixed(1) + '%';
  }

  function paceCls(p) {
    if (p === null || p === undefined) return 'pace-neutral';
    if (p > 5)   return 'pace-up';
    if (p < -5)  return 'pace-down';
    return 'pace-neutral';
  }

  function renderHero(hostEl, data) {
    const t = data.totals;
    hostEl.innerHTML = `
      <div class="stat-pill stat-pill-blue">
        <div class="stat-pill-label">TOTAL POSTERS SAVED</div>
        <div class="stat-pill-num mono">${t.saved}</div>
      </div>
      <div class="stat-pill stat-pill-green">
        <div class="stat-pill-label">EARNED (PAID + ELIGIBLE)</div>
        <div class="stat-pill-num mono">${fmtKes(t.earned_kes)} <span class="stat-pill-unit">KES</span></div>
        <div class="stat-pill-sub muted">${fmtKes(t.paid_kes)} paid · ${fmtKes(t.eligible_unpaid_kes)} pending</div>
      </div>
      <div class="stat-pill stat-pill-purple">
        <div class="stat-pill-label">TITLES COMPLETED</div>
        <div class="stat-pill-num mono">${t.completed_titles}</div>
      </div>
      <div class="stat-pill stat-pill-orange">
        <div class="stat-pill-label">THIS WEEK (PROJECTED)</div>
        <div class="stat-pill-num mono">${data.this_week.projected_count} <span class="stat-pill-unit">posters</span></div>
        <div class="stat-pill-sub muted">≈ ${fmtKes(data.this_week.projected_kes)} KES at current rate</div>
      </div>
    `;
  }

  function renderRecords(hostEl, data) {
    const r = data.records;
    const bestDayDate = r.best_day.date || '—';
    const bestWeekStart = r.best_week.start || '—';
    const streakStart = r.longest_streak.start || '—';
    const streakEnd   = r.longest_streak.end   || '—';
    hostEl.innerHTML = `
      <div class="record-row">
        <div class="record-icon">🏆</div>
        <div class="record-body">
          <div class="record-label">BEST DAY</div>
          <div class="record-value mono"><strong>${r.best_day.count}</strong> posters
            <span class="record-when muted">on ${bestDayDate}</span></div>
        </div>
      </div>
      <div class="record-row">
        <div class="record-icon">📅</div>
        <div class="record-body">
          <div class="record-label">BEST WEEK</div>
          <div class="record-value mono"><strong>${r.best_week.count}</strong> posters
            <span class="record-when muted">week of ${bestWeekStart}</span></div>
        </div>
      </div>
      <div class="record-row">
        <div class="record-icon">🔥</div>
        <div class="record-body">
          <div class="record-label">LONGEST STREAK</div>
          <div class="record-value mono"><strong>${r.longest_streak.days}</strong> day${r.longest_streak.days === 1 ? '' : 's'} in a row
            ${streakStart !== '—' ? `<span class="record-when muted">${streakStart} → ${streakEnd}</span>` : ''}</div>
        </div>
      </div>
    `;
  }

  function renderPace(hostEl, data) {
    const wd = data.deltas.week_vs_last;
    const md = data.deltas.month_vs_last;
    hostEl.innerHTML = `
      <div class="pace-row">
        <div class="pace-period muted">THIS WEEK</div>
        <div class="pace-current mono"><strong>${data.this_week.count}</strong> posters · ${fmtKes(data.this_week.kes)} KES</div>
        <div class="pace-delta ${paceCls(wd)} mono">${fmtPct(wd)} vs last week</div>
        <div class="pace-prev muted">(last week: ${data.last_week.count} posters · ${fmtKes(data.last_week.kes)} KES)</div>
      </div>
      <div class="pace-row">
        <div class="pace-period muted">THIS MONTH</div>
        <div class="pace-current mono"><strong>${data.this_month.count}</strong> posters · ${fmtKes(data.this_month.kes)} KES</div>
        <div class="pace-delta ${paceCls(md)} mono">${fmtPct(md)} vs last month</div>
        <div class="pace-prev muted">(last month: ${data.last_month.count} posters · ${fmtKes(data.last_month.kes)} KES)</div>
      </div>
    `;
  }

  function renderQuality(hostEl, data) {
    const q = data.admin_only;
    if (!q) { hostEl.innerHTML = ''; return; }
    const flagPct = q.flag_rate_pct === null ? '—' : q.flag_rate_pct.toFixed(1) + '%';
    const turnaround = q.avg_turnaround_hours === null ? '—'
      : (q.avg_turnaround_hours < 1
          ? Math.round(q.avg_turnaround_hours * 60) + ' min'
          : q.avg_turnaround_hours.toFixed(1) + ' hrs');
    hostEl.innerHTML = `
      <div class="quality-row">
        <div class="quality-item">
          <div class="quality-label">FLAG RATE</div>
          <div class="quality-value mono"><strong>${flagPct}</strong></div>
          <div class="quality-sub muted">${q.flagged_posters} flagged out of ${data.totals.saved} saved</div>
        </div>
        <div class="quality-item">
          <div class="quality-label">AVG REVISION TURNAROUND</div>
          <div class="quality-value mono"><strong>${turnaround}</strong></div>
          <div class="quality-sub muted">across ${q.resolved_revisions} resolved revisions</div>
        </div>
      </div>
    `;
  }

  // ── Chart ───────────────────────────────────────────────────────────────
  let chartInstance = null;
  let currentMetric = 'count';

  function renderChart(canvas, data, metric) {
    currentMetric = metric;
    const labels = data.series_30.map((d) => d.date.slice(5));   // MM-DD
    const values = data.series_30.map((d) =>
      metric === 'kes' ? parseFloat(d.kes) : d.count
    );
    const cs = getComputedStyle(document.documentElement);
    const accent = cs.getPropertyValue('--accent').trim() || '#f4b400';
    const grid   = (cs.getPropertyValue('--border').trim() || '#333');
    const text   = (cs.getPropertyValue('--text').trim()   || '#ddd');

    if (chartInstance) chartInstance.destroy();
    chartInstance = new window.Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: metric === 'kes' ? 'KES' : 'Posters',
          data: values,
          backgroundColor: accent + 'cc',
          borderColor: accent,
          borderWidth: 1,
          borderRadius: 3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => 'Day ' + items[0].label,
              label: (item) => {
                const v = item.raw;
                return metric === 'kes' ? fmtKes(v) + ' KES' : v + ' poster' + (v === 1 ? '' : 's');
              },
            },
          },
        },
        scales: {
          x: { ticks: { color: text, font: { size: 10 } }, grid: { color: grid + '55' } },
          y: { beginAtZero: true, ticks: { color: text, font: { size: 11 } }, grid: { color: grid + '55' } },
        },
      },
    });
  }

  function wireChartToggle(canvas, data) {
    document.querySelectorAll('.stats-toggle-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.stats-toggle-btn').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        renderChart(canvas, data, btn.getAttribute('data-metric'));
      });
    });
  }

  function render(data, opts = {}) {
    if (!data || !data.ok) {
      const hero = document.getElementById('stats-hero');
      if (hero) hero.innerHTML = '<div class="muted">Failed to load stats.</div>';
      return;
    }
    const hero = document.getElementById('stats-hero');
    const records = document.getElementById('stats-records');
    const pace = document.getElementById('stats-pace');
    const quality = document.getElementById('stats-quality');
    const canvas = document.getElementById('stats-chart');
    if (hero)    renderHero(hero, data);
    if (records) renderRecords(records, data);
    if (pace)    renderPace(pace, data);
    if (quality && opts.adminQuality) renderQuality(quality, data);
    if (canvas)  {
      renderChart(canvas, data, currentMetric);
      wireChartToggle(canvas, data);
    }
  }

  window.PDStats = { render };
})();
