/* The little message that slides in at the corner — ONE definition, global.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * WHY THIS IS ITS OWN FILE
 * ══════════════════════════════════════════════════════════════════════════
 * It used to live inside admin_pipeline.js's wrapper, where nothing outside
 * that one file could reach it. The Approve Artwork screen called `toast(...)`
 * anyway — for "Click a colour in the poster" when the eyedropper is armed,
 * and for the message when a pixel cannot be read. Those two scripts never
 * load on the same page, so on Approve Artwork the name did not exist and
 * every call threw.
 *
 * The eyedropper still ARMED, because the throw came after the state was
 * set, so it looked like it worked and simply never said anything. The worse
 * half is the second call: the one place that reports a failure was itself
 * failing (found by preflight, 2026-09-09).
 *
 * So it is a real global now, loaded from base.html on every page, defined
 * once. A helper two screens want is a helper that belongs to neither.
 */
(function () {
  'use strict';

  let el = null;

  // Attached to `window` deliberately rather than left as a bare `function`
  // declaration. Being explicit is what makes it obvious to the next reader
  // that this is meant to be reachable from other files, and it is what
  // preflight's scope check reads when deciding that a call to `toast()` in
  // some other wrapper is fine.
  window.toast = function toast(msg, kind) {
    if (!el) {
      el = document.createElement('div');
      el.id = 'app-toast';
      document.body.appendChild(el);
    }
    el.className = 'toast toast-' + (kind || 'ok');
    el.textContent = msg;
    el.classList.add('toast-shown');
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove('toast-shown'); }, 4500);
  };
})();
