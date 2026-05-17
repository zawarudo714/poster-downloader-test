/* Topbar runtime helpers — runs on every authenticated page from base.html.
   Two responsibilities:
     1. Poll chat unread count and update both the inline #nav-chat-badge
        and the small badge attached to the hamburger button (so the badge
        is visible even when the drawer is closed on phones).
     2. Wire up the hamburger button + drawer scrim. Drawer slides in from
        the left below 700px; clicking outside or any nav link closes it. */

(function () {
  // ── Chat unread polling ──────────────────────────────────────────────────
  const inlineBadge = document.getElementById('nav-chat-badge');
  const toggleBadge = document.getElementById('nav-toggle-badge');
  const isAdmin = !!document.querySelector('.role-badge.role-admin');

  function setBadge(el, n) {
    if (!el) return;
    if (n > 0) {
      el.textContent = n > 99 ? '99+' : String(n);
      el.hidden = false;
    } else {
      el.hidden = true;
    }
  }

  async function tickBadges() {
    if (!inlineBadge && !toggleBadge) return;
    try {
      let n = null;
      if (isAdmin) {
        // Admin endpoint. If it fails, leave the badge alone — we don't
        // want to overwrite with a misleading 0 from the wrong endpoint.
        try {
          const r = await fetch('/admin/api/chat/_summary', { cache: 'no-store' });
          if (r.ok) {
            const data = await r.json();
            n = data.total_unread || 0;
          }
        } catch (e) { /* network blip */ }
      } else {
        // Worker view: count of unread in their own thread.
        const r = await fetch('/api/chat?after=0', { cache: 'no-store' });
        if (!r.ok) return;
        const data = await r.json();
        n = data.unread || 0;
      }
      if (n !== null) {
        setBadge(inlineBadge, n);
        setBadge(toggleBadge, n);
      }
    } catch (e) { /* ignore */ }
  }

  if (inlineBadge || toggleBadge) {
    tickBadges();
    setInterval(tickBadges, 12000);
  }

  // ── Hamburger drawer ─────────────────────────────────────────────────────
  const toggle = document.getElementById('nav-toggle');
  const nav    = document.getElementById('topnav');
  const scrim  = document.getElementById('nav-scrim');
  if (!toggle || !nav) return;

  function open() {
    nav.classList.add('open');
    toggle.classList.add('open');
    toggle.setAttribute('aria-expanded', 'true');
    if (scrim) scrim.hidden = false;
    // Lock body scroll while drawer is open.
    document.body.style.overflow = 'hidden';
  }
  function close() {
    nav.classList.remove('open');
    toggle.classList.remove('open');
    toggle.setAttribute('aria-expanded', 'false');
    if (scrim) scrim.hidden = true;
    document.body.style.overflow = '';
  }
  function isOpen() { return nav.classList.contains('open'); }

  toggle.addEventListener('click', () => (isOpen() ? close() : open()));
  if (scrim) scrim.addEventListener('click', close);
  // Tapping a nav link inside the drawer should also close it.
  nav.querySelectorAll('a.navlink').forEach((a) => {
    a.addEventListener('click', () => { if (isOpen()) close(); });
  });
  // Close on Escape.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen()) close();
  });
  // If viewport grows past breakpoint, drop drawer state so desktop nav reappears clean.
  window.addEventListener('resize', () => {
    if (window.innerWidth > 700 && isOpen()) close();
  });
})();
