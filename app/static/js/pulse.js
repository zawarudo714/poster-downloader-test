/* The pulse: ONE poll feeding every live element on every admin screen.
   ─ the status strip under the top bar (node · pipeline · quiet · workers)
   ─ the red alarm line, whatever screen you are on
   ─ the nav badges on Worker Images / Approve Artwork / Changes Requested
   ─ the project home page (listens for the 'pulse' event; never polls)

   One request every 15 seconds per open tab. Five widgets polling five
   endpoints is how a dashboard quietly becomes a load problem, so anything
   new that wants live data should ride in /admin/api/pulse and read the
   event, not add a timer. */

(function () {
  'use strict';

  var strip = document.getElementById('pulse-strip');
  if (!strip) return;                    // not an admin page

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function setBadge(name, n) {
    document.querySelectorAll('[data-nav-badge="' + name + '"]').forEach(function (el) {
      if (n > 0) { el.textContent = n > 99 ? '99+' : String(n); el.hidden = false; }
      else { el.hidden = true; }
    });
  }

  function render(d) {
    var bits = [];

    // The worker machine. No machines registered at all is a setup state,
    // not an alarm, so it reads as words rather than red.
    if (!d.node || !d.node.total) {
      bits.push('<span class="pulse-item muted">no worker machine registered</span>');
    } else if (d.node.offline && d.node.offline.length) {
      bits.push('<span class="pulse-item pulse-bad">● machine offline</span>');
    } else {
      bits.push('<span class="pulse-item pulse-ok">● machine on</span>'
        + '<span class="pulse-item mono">' + esc(d.node.busy) + '</span>');
    }

    if (d.run && !d.run.running && d.run.reason) {
      bits.push('<span class="pulse-item pulse-warn">' + esc(d.run.reason) + '</span>');
    }
    if (d.quiet && d.quiet.blocking) {
      bits.push('<span class="pulse-item pulse-warn">quiet window — earnings read running</span>');
    }
    bits.push('<span class="pulse-item">' + d.workers_online
      + ' worker' + (d.workers_online === 1 ? '' : 's') + ' online</span>');

    var alarms = d.alarms || [];
    var alarmHtml = alarms.map(function (a) {
      return '<a class="pulse-alarm" href="' + esc(a.href) + '">⚠ ' + esc(a.text) + '</a>';
    }).join('');

    strip.innerHTML = '<div class="pulse-row">' + bits.join('') + '</div>'
      + (alarmHtml ? '<div class="pulse-alarms">' + alarmHtml + '</div>' : '');
    strip.hidden = false;

    var b = d.badges || {};
    setBadge('browse', b.browse || 0);
    setBadge('revisions', b.revisions || 0);
    setBadge('skipped', b.skipped || 0);
    setBadge('review', b.review || 0);
    setBadge('greenlight', b.greenlight || 0);
    setBadge('attention', b.attention || 0);

    // Anyone else who wants this answer (the home page) hears it here.
    document.dispatchEvent(new CustomEvent('pulse', { detail: d }));
  }

  function tick() {
    fetch('/admin/api/pulse', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) render(d); })
      .catch(function () { /* a network blip must not blank the strip */ });
  }

  tick();
  setInterval(tick, 15000);
})();
