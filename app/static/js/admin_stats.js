/* Admin /admin/stats — fetches selected worker's stats and delegates. */
(function () {
  const grid = document.querySelector('.stats-admin-grid');
  if (!grid) return;
  const wid = parseInt(grid.getAttribute('data-selected-worker-id'), 10) || 0;
  if (!wid) return;
  async function load() {
    const r = await fetch(`/admin/api/stats/${wid}`, { cache: 'no-store' });
    if (!r.ok) {
      const hero = document.getElementById('stats-hero');
      if (hero) hero.innerHTML = '<div class="muted">Failed to load stats.</div>';
      return;
    }
    const data = await r.json();
    window.PDStats.render(data, { adminQuality: true });
  }
  load();
})();
