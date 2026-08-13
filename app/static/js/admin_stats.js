/* Admin /admin/stats — fetches the selected worker's stats and delegates
   rendering to the shared panel code.

   COMBINED BY DEFAULT. A worker's throughput, streak and flag rate describe
   the PERSON, not the project, so the page opens on everything they have
   done and the filter narrows it. Splitting by default would answer a
   question nobody asked, and would make a two-project worker look half as
   productive as they are.

   The chosen filter is remembered for the session, because the reason to
   narrow it — comparing one niche against another — takes several clicks
   through different workers to answer. */
(function () {
  const grid = document.querySelector('.stats-admin-grid');
  if (!grid) return;

  const wid = parseInt(grid.getAttribute('data-selected-worker-id'), 10) || 0;
  if (!wid) return;

  const picker = document.querySelector('[data-stats-project]');
  const note   = document.querySelector('[data-stats-scope-note]');
  const KEY    = 'pd-stats-project';

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
    if (hero) hero.innerHTML = '<div class="stats-hero-loading muted">Loading…</div>';

    if (note) {
      note.textContent = pid
        ? 'Narrowed to one project. Totals, records and pay below cover only that work.'
        : '';
    }

    const r = await fetch(`/admin/api/stats/${wid}?project_id=${pid}`, { cache: 'no-store' });
    if (!r.ok) {
      if (hero) hero.innerHTML = '<div class="muted">Failed to load stats.</div>';
      return;
    }
    window.PDStats.render(await r.json(), { adminQuality: true });
  }

  load();
})();
