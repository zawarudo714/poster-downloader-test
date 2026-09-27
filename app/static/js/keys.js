/* ONE answer to "did this key press mean that setting?" — shared by every
 * screen whose keys come from the dashboard (Approve Artwork, Worker Images
 * and its zoom), so no two screens can disagree about a key.
 *
 * Why it is more than `e.key === setting`: the owner steps through pictures
 * with the number pad's 4 and 6 and wants 7 / 8 / 9 for the decisions
 * (2026-09-27). With Num Lock OFF those number-pad keys do not send "7", "8"
 * or "9" at all — they send Home, ArrowUp and PageUp — so a plain comparison
 * would silently do nothing on exactly the keys he asked for. The key's
 * PHYSICAL name (e.code, "Numpad7") is the same with Num Lock on or off, so
 * a digit setting matches both the top-row digit and that number-pad key.
 */
(function () {
  'use strict';
  function matches(e, setting) {
    if (!e || setting == null) return false;
    const k = String(setting).trim();
    if (!k) return false;
    if (e.key && e.key.toLowerCase() === k.toLowerCase()) return true;
    return /^[0-9]$/.test(k) && e.code === 'Numpad' + k;
  }
  window.PDKeys = Object.freeze({ matches });
})();
