/* Poll chat unread count for the topbar badge.
   Runs on every page (loaded from base.html), updates #nav-chat-badge in place. */

(function () {
  const badge = document.getElementById('nav-chat-badge');
  if (!badge) return;

  // Detect role from the body — admin layout has the role-badge.
  // Fallback: try the admin endpoint first; if it 403s, use the worker one.
  const isAdmin = !!document.querySelector('.role-badge.role-admin');

  async function tick() {
    try {
      let n = 0;
      if (isAdmin) {
        const r = await fetch('/admin/api/chat/_summary', { cache: 'no-store' });
        if (!r.ok) return;
        const data = await r.json();
        n = data.total_unread || 0;
      } else {
        const r = await fetch('/api/chat?after=0', { cache: 'no-store' });
        if (!r.ok) return;
        const data = await r.json();
        n = data.unread || 0;
      }
      if (n > 0) {
        badge.textContent = n > 99 ? '99+' : String(n);
        badge.hidden = false;
      } else {
        badge.hidden = true;
      }
    } catch (e) { /* ignore */ }
  }

  tick();
  setInterval(tick, 12000);
})();
