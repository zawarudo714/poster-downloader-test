/* Worker /stats — fetches own stats and delegates to PDStats.render(). */
(function () {
  async function load() {
    const r = await fetch('/api/stats/me', { cache: 'no-store' });
    if (!r.ok) {
      document.getElementById('stats-hero').innerHTML =
        '<div class="muted">Failed to load stats.</div>';
      return;
    }
    const data = await r.json();
    window.PDStats.render(data, { adminQuality: false });
  }
  load();
})();
