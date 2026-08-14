/* Worker /stats — fetches own stats and delegates to PDStats.render().

   Combined across projects by default, narrowable by the filter above the
   chart when the worker covers more than one. The filter is a VIEW: the
   server checks the requested project against the worker's own assignments,
   so this cannot be used to look at a niche they have no part in.

   The choice is remembered for the session only. It is a "let me look at
   this for a second" control, not a preference — coming back tomorrow to a
   silently filtered total would be the same trap the admin page avoids. */
(function () {
  const picker = document.querySelector('[data-stats-project]');
  const note   = document.querySelector('[data-stats-scope-note]');
  const KEY    = 'pd-my-stats-project';

  if (picker) {
    try {
      const saved = sessionStorage.getItem(KEY);
      if (saved && picker.querySelector(`option[value="${saved}"]`)) picker.value = saved;
    } catch (e) {}
    picker.addEventListener('change', () => {
      try { sessionStorage.setItem(KEY, picker.value); } catch (e) {}
      load();
    });
  }

  async function load() {
    const pid = picker ? (parseInt(picker.value, 10) || 0) : 0;
    const hero = document.getElementById('stats-hero');
    if (hero) hero.innerHTML = '<div class="stats-hero-loading muted">Loading your stats…</div>';

    if (note) {
      note.textContent = pid
        ? 'Showing one project only — totals, records and pay below cover just that work.'
        : '';
    }

    const r = await fetch(`/api/stats/me?project_id=${pid}`, { cache: 'no-store' });
    if (!r.ok) {
      if (hero) hero.innerHTML = '<div class="muted">Failed to load stats.</div>';
      return;
    }
    window.PDStats.render(await r.json(), { adminQuality: false });
  }

  load();
})();
