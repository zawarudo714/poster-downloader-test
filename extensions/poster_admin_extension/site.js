/* ════════════════════════════════════════════════════════════════════════
   THE PART THAT RUNS ON THE POSTER SITE'S ADMIN PAGES
   ════════════════════════════════════════════════════════════════════════
   Two jobs:
     1. Tell the page this add-on is here, by setting
        data-pd-google-helper="1" on <html>. The zoom reads that and hands
        its Google searches to us instead of opening a new tab each time.
     2. Pass each "show this search" on to the add-on's background part,
        which is the only piece allowed to steer another tab.

   Why an add-on at all: a web page cannot keep hold of a Google tab.
   Google's pages cut the link back to whoever opened them, so the site's
   own attempt (v234) lost the tab the moment Google loaded.
   ════════════════════════════════════════════════════════════════════════ */

(function () {
  document.documentElement.setAttribute('data-pd-google-helper', '1');

  // Only Google search addresses are ever passed on. The page asks with a
  // window message; anything that is not from this very page, or is not a
  // Google search, is ignored.
  function isGoogleSearch(url) {
    try {
      var u = new URL(url);
      return u.protocol === 'https:' &&
             /^www\.google\.[a-z.]+$/.test(u.hostname) &&
             u.pathname === '/search';
    } catch (e) { return false; }
  }

  window.addEventListener('message', function (e) {
    if (e.source !== window || e.origin !== location.origin) return;
    var m = e.data;
    if (!m || m.type !== 'PD_GOOGLE') return;
    if (m.mode !== 'open' && m.mode !== 'follow') return;
    if (!isGoogleSearch(m.url)) return;
    try {
      chrome.runtime.sendMessage({ type: 'PD_GOOGLE', mode: m.mode, url: m.url });
    } catch (err) {
      // The add-on was reloaded while this page stayed open; its old script
      // can no longer reach it. A page reload fixes that — say so once.
      if (!window.__pdGoogleWarned) {
        window.__pdGoogleWarned = true;
        alert('The Google add-on was updated. Reload this page (F5) to reconnect it.');
      }
    }
  });
})();
