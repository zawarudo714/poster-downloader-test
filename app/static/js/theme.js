/* Theme toggle — light vs dark. Default is dark.
   Class lives on <html> so the inline head-script can apply it before the
   body even renders, avoiding a flash of dark on light pages. */

(function () {
  const KEY = 'poster-theme';
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;

  function apply(theme) {
    if (theme === 'light') {
      document.documentElement.classList.add('theme-light');
      btn.textContent = '☀';
      btn.title = 'Switch to dark mode';
    } else {
      document.documentElement.classList.remove('theme-light');
      btn.textContent = '☾';
      btn.title = 'Switch to light mode';
    }
  }

  // Initial render — match whatever the inline head-script applied (or default dark).
  apply(document.documentElement.classList.contains('theme-light') ? 'light' : 'dark');

  btn.addEventListener('click', () => {
    const isLight = document.documentElement.classList.contains('theme-light');
    const next = isLight ? 'dark' : 'light';
    apply(next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
  });
})();
