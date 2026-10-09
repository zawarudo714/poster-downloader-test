/* ════════════════════════════════════════════════════════════════════════
   THE PART THAT STEERS THE GOOGLE TAB
   ════════════════════════════════════════════════════════════════════════
   Keeps ONE Google tab. The site's zoom sends two kinds of request:

     open   — you pressed CHECK GOOGLE. Show this search in the Google tab,
              and open that tab (beside the site tab) if there is none yet.
     follow — the zoom moved to another picture. Show its search, but ONLY
              if the Google tab is already open. Stepping never opens a tab.

   Updating the tab does not bring it to the front, so the keyboard stays
   on the site and the arrow keys keep working. The tab's id is kept in
   session storage because Chrome may stop this background part between
   uses; if you close the tab, the next CHECK GOOGLE simply opens a new one.
   ════════════════════════════════════════════════════════════════════════ */

var KEY = 'pdGoogleTabId';

function storedTabId() {
  return chrome.storage.session.get(KEY).then(function (o) { return o[KEY] || null; });
}

function tabStillThere(id) {
  if (!id) return Promise.resolve(false);
  return chrome.tabs.get(id).then(function () { return true; },
                                   function () { return false; });
}

chrome.runtime.onMessage.addListener(function (msg, sender) {
  if (!msg || msg.type !== 'PD_GOOGLE' || !msg.url) return;

  storedTabId().then(function (id) {
    return tabStillThere(id).then(function (alive) {
      if (alive) {
        // Same tab, new search. Not made active: you stay on the site.
        return chrome.tabs.update(id, { url: msg.url });
      }
      if (msg.mode !== 'open') return null;   // stepping never opens a tab
      var opts = { url: msg.url, active: true };
      if (sender && sender.tab) {
        opts.index = sender.tab.index + 1;
        opts.windowId = sender.tab.windowId;
      }
      return chrome.tabs.create(opts).then(function (tab) {
        var o = {}; o[KEY] = tab.id;
        return chrome.storage.session.set(o);
      });
    });
  }).catch(function (e) { console.warn('Google tab:', e); });
});

// Forget the tab the moment it is closed, so a stale id is never reused.
chrome.tabs.onRemoved.addListener(function (tabId) {
  storedTabId().then(function (id) {
    if (id === tabId) chrome.storage.session.remove(KEY);
  });
});
