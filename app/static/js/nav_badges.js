/* Topbar runtime helpers — runs on every authenticated page from base.html.
   Two responsibilities:
     1. Poll chat unread count and update both the inline #nav-chat-badge
        and the small badge attached to the hamburger button (so the badge
        is visible even when the drawer is closed on phones).
     2. Wire up the hamburger button + drawer scrim. Drawer slides in from
        the left below 700px; clicking outside or any nav link closes it. */

(function () {
  // ── Chat unread polling ──────────────────────────────────────────────────
  // Every badge that shows the chat count carries data-chat-badge in
  // base.html — the Chat link, the People group button, and the phone's
  // hamburger. ONE query finds them all, so a badge added later joins by
  // carrying the attribute rather than by someone extending an id list.
  const badges = Array.from(document.querySelectorAll('[data-chat-badge]'));
  let lastChatCount = null;

  // WHO IS ASKING decides WHICH endpoint answers, and the server now says
  // so outright (body's data-user-role). This used to be sniffed from the
  // ADMIN pill's CSS classes — a name chosen in one file and rendered by
  // another, which fails in silence: guessed wrong, the script asked the
  // worker endpoint as an admin, was refused, and the badge simply never
  // appeared. A worker's message sat unseen all morning (owner's report,
  // 2026-09-18). The old sniff survives only as a fallback.
  const role = document.body.dataset.userRole
    || (document.querySelector('.role-badge.role-admin') ? 'admin' : 'worker');
  const isAdmin = role === 'admin';

  function showChatToast(fresh) {
    let el = document.getElementById('chat-toast');
    if (!el) {
      el = document.createElement('a');
      el.id = 'chat-toast';
      el.className = 'chat-toast';
      el.href = isAdmin ? '/admin/chat' : '/chat';
      document.body.appendChild(el);
      // STAYS until acted on — the 8-second version evaporated while the
      // owner was looking elsewhere, which is the one job a notifier has.
      // Clicking the body opens the chat; the ✕ dismisses without going.
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'chat-toast-close';
      x.textContent = '✕';
      x.title = 'Dismiss — the red count stays until you read the message';
      x.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        el.hidden = true;
      });
      el.appendChild(x);
    }
    const label = el.querySelector('.chat-toast-label') || (() => {
      const s = document.createElement('span');
      s.className = 'chat-toast-label';
      el.insertBefore(s, el.firstChild);
      return s;
    })();
    label.innerHTML = '<span class="chat-toast-dot"></span>'
      + (fresh === 1 ? 'New chat message' : fresh + ' new chat messages')
      + ' — open';
    el.hidden = false;
  }

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
    if (!badges.length) return;
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
          } else {
            console.warn('[chat badge] summary endpoint said', r.status);
          }
        } catch (e) { /* network blip */ }
      } else {
        // Worker view: count of unread in their own thread.
        const r = await fetch('/api/chat?after=0', { cache: 'no-store' });
        if (!r.ok) { console.warn('[chat badge] worker endpoint said', r.status); return; }
        const data = await r.json();
        n = data.unread || 0;
      }
      if (n !== null) {
        // A count that went UP means a message arrived while you were on
        // some other screen — say so where the eye already is, once per
        // rise, and never on the chat page itself (you are looking at it).
        if (lastChatCount !== null && n > lastChatCount
            && !location.pathname.endsWith('/chat')) {
          showChatToast(n - lastChatCount);
          badges.forEach((el) => {
            el.classList.remove('bump');
            void el.offsetWidth;            // restart the animation
            el.classList.add('bump');
          });
        }
        // Reading the message clears the pop-up too, from any page.
        if (n === 0) {
          const toast = document.getElementById('chat-toast');
          if (toast) toast.hidden = true;
        }
        lastChatCount = n;
        badges.forEach((el) => setBadge(el, n));
      }
    } catch (e) { /* ignore */ }
  }

  if (badges.length) {
    tickBadges();
    setInterval(tickBadges, 12000);
  }

  // ── Nav group dropdowns (desktop) ────────────────────────────────────────
  // Click to open, click anywhere else to close. Hover-only menus fail on
  // touch screens, which is half the point of grouping. In the phone drawer
  // the CSS renders every group permanently open, so this code simply never
  // matters there.
  const groups = Array.from(document.querySelectorAll('[data-nav-group]'));
  function closeGroups(except) {
    groups.forEach((g) => {
      if (g !== except) {
        g.classList.remove('open');
        const b = g.querySelector('.nav-group-btn');
        if (b) b.setAttribute('aria-expanded', 'false');
      }
    });
  }
  groups.forEach((g) => {
    const btn = g.querySelector('.nav-group-btn');
    if (!btn) return;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const opening = !g.classList.contains('open');
      closeGroups(null);
      if (opening) {
        g.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
      }
    });
  });
  document.addEventListener('click', () => closeGroups(null));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeGroups(null);
  });

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
