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

  // ── ONE CHIP ─────────────────────────────────────────────────────────
  // Every item on the strip is built here so they cannot drift into
  // looking like four different things. `tone` colours the dot and the
  // border; `label` is the quiet word saying what the figure MEANS, which
  // is the difference between "idle" and "the machine is idle".
  function chip(tone, label, value, opts) {
    opts = opts || {};
    var inner =
      (opts.dot ? '<span class="pulse-dot"></span>' : '')
      + '<span class="pulse-label">' + esc(label) + '</span>'
      + (value ? '<span class="pulse-value">' + value + '</span>' : '');
    if (opts.href) {
      return '<a class="pulse-chip pulse-' + tone + '" href="' + esc(opts.href)
        + '" title="' + esc(opts.title || '') + '">' + inner + '</a>';
    }
    return '<span class="pulse-chip pulse-' + tone + '"'
      + ' title="' + esc(opts.title || '') + '">' + inner + '</span>';
  }

  function render(d) {
    var bits = [];
    var n = d.node || {};

    // ── THE WORKER MACHINE ────────────────────────────────────────────
    //
    // Three states, not two. No machine registered at all is a SETUP
    // state, not a fault, so it reads as plain words in grey — an alarm
    // colour on a box nobody has plugged in yet teaches you to ignore the
    // colour.
    if (!n.total) {
      bits.push(chip('idle', 'worker machine', 'not set up yet',
        { dot: true, href: '/admin/pipeline',
          title: 'No Windows machine has ever checked in.' }));
    } else if (n.offline && n.offline.length) {
      bits.push(chip('bad', 'worker machine', 'OFFLINE',
        { dot: true, href: '/admin/pipeline',
          title: n.offline.join(', ') + ' has not been seen for over 5 '
                 + 'minutes. Processing and uploads are stopped.' }));
    } else {
      bits.push(chip('ok', 'worker machine', 'on',
        { dot: true, href: '/admin/pipeline',
          title: 'The Windows machine checked in within the last 5 minutes.' }));
    }

    // ── WHAT IT IS DOING RIGHT NOW ────────────────────────────────────
    // Painting and uploading are counted in IMAGES, because that is the
    // number worth knowing. Everything else the machine does is a JOB with
    // no picture attached — the listing check, the earnings read, a profile
    // cleanup, the test jobs — and those used to be invisible here, so the
    // strip read "nothing" through an hour-long listing check.
    var doing = [];
    if (n.processing) doing.push(n.processing + ' painting');
    if (n.uploading) doing.push(n.uploading + ' uploading');
    (n.jobs || []).forEach(function (word) { doing.push(word); });
    bits.push(chip(doing.length ? 'busy' : 'idle', 'doing now',
      doing.length ? doing.join(' · ') : 'nothing',
      { title: 'Everything the worker machine is doing right this second — '
               + 'painting, uploading, checking listings, reading the money, '
               + 'or a test. Counted across every project, because the '
               + 'machine is shared and does one thing at a time.' }));

    // ── WHAT IS QUEUED BEHIND IT ──────────────────────────────────────
    //
    // This is the pair that makes "idle" mean something. Idle with a
    // hundred waiting is a fault; idle with nothing waiting is a finished
    // day. The chip is only drawn when there IS a queue, so a quiet system
    // stays quiet.
    var waiting = [];
    if (n.to_process) waiting.push(n.to_process + ' to paint');
    if (n.to_upload) waiting.push(n.to_upload + ' to upload');
    if (waiting.length) {
      bits.push(chip('queue', 'waiting', waiting.join(' · '),
        { href: '/admin/pipeline',
          title: 'Work already approved and queued for the machine. If this '
                 + 'stays still while the machine says it is doing nothing, '
                 + 'something is stuck.' }));
    }

    // ── THE PIPELINE SWITCH ───────────────────────────────────────────
    // Only shown when it is NOT simply running. A chip saying "running"
    // every minute of every day is furniture.
    if (d.run && !d.run.running) {
      bits.push(chip('warn', 'pipeline', esc(d.run.reason || 'not running'),
        { href: '/admin/pipeline',
          title: 'New work is not being handed out. Nothing is lost — '
                 + 'anything already in flight finishes.' }));
    }
    if (d.quiet && d.quiet.blocking) {
      bits.push(chip('warn', 'quiet window', 'reading earnings',
        { href: '/admin/earnings',
          title: 'Each night the machine finishes what it holds and reads '
                 + 'the money before taking new work. This clears itself.' }));
    }

    // ── WAITING ON YOU ────────────────────────────────────────────────
    if (d.needs_you) {
      bits.push(chip('you', 'waiting on you', String(d.needs_you),
        { href: '/admin/home',
          title: 'Things in this project that need a decision from you — '
                 + 'the same total as the numbers beside the menu.' }));
    }

    bits.push(chip('idle', 'workers online', String(d.workers_online),
      { title: 'Paid workers whose screen has spoken to the site in the '
               + 'last 5 minutes.' }));

    var stamp = new Date().toLocaleTimeString();
    var alarms = d.alarms || [];
    var alarmHtml = alarms.map(function (a) {
      return '<a class="pulse-alarm" href="' + esc(a.href) + '">'
        + '<span class="pulse-alarm-mark">!</span>' + esc(a.text) + '</a>';
    }).join('');

    strip.innerHTML =
      '<div class="pulse-row">' + bits.join('')
      + '<span class="pulse-stamp mono">' + esc(stamp) + '</span></div>'
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
