/*
 * Listing reconciliation — does the marketplace still show what we think?
 *
 * Two jobs. Make it obvious what a sweep is doing and what it found, and
 * make every finding answerable in one click, because a list of problems
 * nobody can act on is just a longer way of worrying.
 *
 * Polls only while something is actually moving. A finished sweep changes
 * when you press a button, so there is nothing to refresh for.
 */
(function () {
  'use strict';

  var API = '/admin/api/listings';
  var state = { timer: null, findings: null };

  function q(sel) { return document.querySelector(sel); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
               '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function when(iso) {
    if (!iso) return 'never';
    var d = new Date(iso);
    return isNaN(d) ? 'never' : d.toLocaleString();
  }

  async function getJSON(url) {
    var r = await fetch(url, { credentials: 'same-origin' });
    var data = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(data.detail || ('HTTP ' + r.status));
    return data;
  }
  async function postJSON(url, body) {
    var r = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    var data = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(data.detail || ('HTTP ' + r.status));
    return data;
  }

  // How long a sweep will actually take, in words rather than seconds.
  // 1.1s per request is MEASURED from the worker machine, not assumed.
  function estimate(count, gapMs) {
    // `Number(gapMs) || 300` would be wrong: a gap of ZERO is a legitimate
    // setting and is falsy, so it fell through to the default and the screen
    // quoted the same time whether or not you had turned the pause off.
    var gap = Number(gapMs);
    if (!isFinite(gap) || gap < 0) gap = 300;
    var seconds = count * (1.1 + gap / 1000);
    var mins = Math.round(seconds / 60);
    if (mins < 1) return 'under a minute';
    if (mins < 90) return mins + ' minute' + (mins === 1 ? '' : 's');
    var hours = Math.floor(mins / 60), rest = mins % 60;
    return hours + ' hour' + (hours === 1 ? '' : 's')
           + (rest ? ' ' + rest + ' minutes' : '');
  }

  // ── "IS IT STUCK?" ───────────────────────────────────────────────────
  //
  // Four situations look identical on a screen that only shows a count:
  // the worker machine is off, it is busy with something else, it is
  // working and simply has not reported yet, or it died mid-chunk. The
  // owner watched "0 of 15" and could not tell which (2026-09-09).
  //
  // So this says which machine has the work, when it last spoke, and what
  // it said. Nothing here declares a job dead — a chunk reports once every
  // 25 addresses, so a minute of silence is normal. It states the facts and
  // names the one number that matters.
  function howLong(seconds) {
    if (seconds == null) return 'never';
    if (seconds < 90) return seconds + ' seconds ago';
    var mins = Math.round(seconds / 60);
    if (mins < 90) return mins + ' minute' + (mins === 1 ? '' : 's') + ' ago';
    var hours = Math.round(mins / 60);
    return hours + ' hour' + (hours === 1 ? '' : 's') + ' ago';
  }

  function renderJob(j) {
    var el = q('[data-sweep-log]');
    if (!el) return;

    if (!j) {
      el.innerHTML = '<p class="muted">The worker machine has not been asked '
        + 'to check anything yet. Start a sweep above and its log appears '
        + 'here.</p>';
      return;
    }

    // Say plainly whether this is happening now or is the last thing that
    // happened. An old log presented as a live one is worse than no log.
    var age = j.live
      ? ''
      : '<p class="muted">This is the LAST sweep (#' + j.sweep_id + ', '
        + esc(j.sweep_status) + '). Nothing is running now.</p>';

    // WHAT EACH STATE MEANS, IN WORDS, WITH WHAT TO DO ABOUT IT.
    var verdict;
    if (j.status === 'queued') {
      verdict = '<p><strong>Waiting for the worker machine to pick this up.</strong> '
        + 'The machine runs one job at a time, so if it is part-way through '
        + 'a Photoshop or upload job this waits its turn. Created '
        + howLong(j.quiet_for_s) + '. If it has been waiting more than a few '
        + 'minutes with nothing else running, the agent on that machine is '
        + 'probably not running — check the Nodes panel on the Pipeline page.</p>';
    } else if (j.status === 'running') {
      var quiet = j.quiet_for_s;
      // 25 addresses per report at roughly 1.4s each is about 35 seconds,
      // so silence becomes interesting somewhere past two minutes. Said as
      // a fact plus a suggestion, never as a diagnosis.
      var worry = quiet != null && quiet > 180;
      verdict = '<p><strong>Running on ' + esc(j.machine || 'the worker machine')
        + '.</strong> ' + (j.progress || 0) + '% of this batch of addresses. '
        + 'Last said something ' + howLong(quiet) + '.</p>'
        + (worry
            ? '<p class="quota-note">It normally speaks up every 30 seconds '
              + 'or so. This long a silence usually means the machine was '
              + 'rebooted or lost its network. Nothing is lost — every '
              + 'address already checked is saved, and the sweep restarts '
              + 'itself from where it got to. You can also press STOP and '
              + 'start again.</p>'
            : '');
    } else if (j.status === 'error') {
      verdict = '<p class="danger"><strong>The job failed.</strong> '
        + esc(j.error || 'No reason was recorded.') + '</p>';
    } else if (j.status === 'cancelled') {
      verdict = '<p class="muted">This batch was cancelled.</p>';
    } else {
      verdict = '<p class="muted">This batch finished. '
        + (j.live
            ? 'The next batch should be handed out within a few seconds.'
            : '') + '</p>';
    }

    el.innerHTML = age + verdict
      + '<p class="muted mono">job #' + j.id + ' · batch ' + j.chunks
      + ' of this sweep · runs on the WINDOWS worker machine, not the Linux '
      + 'server</p>'
      + (j.log && j.log.length
          // The same console styling the Pipeline page uses for a running
          // job, so a log looks like a log wherever you meet one.
          ? '<pre class="pipe-console">' + esc(j.log.join('\n')) + '</pre>'
          : '<p class="muted">The machine has not written anything yet.</p>');
  }

  // ── The sweep ────────────────────────────────────────────────────────
  function renderSweep(data) {
    var s = data.sweep, c = data.counts || {}, el = q('[data-sweep-panel]');
    q('[data-sweep-summary]').textContent = s
      ? 'sweep #' + s.id + ' · started ' + when(s.started_at)
      : '';

    // Accounts with no artist name are SKIPPED entirely, so it is said
    // before anything else. A sweep quietly covering four accounts of six
    // and reporting "all fine" would be worse than not running it.
    var blocked = (c.accounts_blocked || []).length
      ? '<p class="quota-note">' + c.accounts_blocked.length + ' account(s) '
        + 'will be SKIPPED — no artist name yet: '
        + esc((c.accounts_blocked || []).join(', ')) + '. Add one below.</p>'
      : '';

    if (!s) {
      var last = (data.history || [])[0];
      el.innerHTML = blocked +
        (last && last.status === 'failed'
          ? '<p class="quota-note"><strong>The last sweep stopped early.</strong> '
            + esc(last.note) + '</p>' : '') +
        (last && last.status === 'done'
          ? '<p class="muted">Last sweep ' + when(last.finished_at) + ' — '
            + esc(last.note) + '</p>' : '') +
        '<p><strong>' + (c.in_scope || 0) + ' listing(s)</strong> across '
        + (c.accounts_ready || 0) + ' account(s) can be checked'
        + (c.no_artist
            ? ' <span class="muted">(' + c.no_artist + ' more are on accounts '
              + 'with no artist name)</span>' : '')
        + '.</p>' +
        (c.never
          ? '<p class="muted">' + c.never + ' of them have never been checked.</p>'
          : '') +
        '<p><button class="btn btn-accent" data-action="start" type="button"'
        + (c.in_scope ? '' : ' disabled') + '>CHECK ALL ' + (c.in_scope || 0)
        + '</button></p>' +
        // ── THE ESTIMATE USES A MEASURED RATE, NOT A GUESSED ONE ────────
        //
        // 1.1s per request was measured from the worker machine on
        // 2026-08-24 (837-1291ms across four addresses), plus whatever gap
        // is configured. An earlier version assumed 0.75s flat and would
        // have promised an hour for a job that takes nearly two.
        '<p class="muted">Roughly ' + estimate(c.in_scope || 0,
          (data.settings || {}).listing_check_gap_ms)
        + '. Nothing is changed on the marketplace and nothing is changed '
        + 'here — it only looks. processing and uploads keep running in'
        + 'between, so it takes longer on a busy day.</p>';
      return;
    }

    // ── THE GUARD AGAINST A WRONG ARTIST NAME ──────────────────────────
    //
    // Shown above everything, on the running sweep as well as a finished
    // one. It deliberately does not say which explanation is right: an
    // account really can lose everything, and that is what a ban looks
    // like. One address opened by hand settles it in ten seconds.
    var suspect = (s.suspect || []).length
      ? '<p class="quota-note"><strong>No page has ever existed at these '
        + 'addresses for '
        + (s.suspect || []).map(function (x) { return esc(x.account); }).join(', ')
        + '.</strong><br>'
        + (s.suspect || []).map(function (x) {
            return esc(x.account) + ': ' + x.gone + ' of ' + x.checked
              + ' have no page at all, using the artist name <span class="mono">'
              + esc(x.artist_name) + '</span>'
              + (x.example_url ? '<br><a href="' + esc(x.example_url)
                 + '" target="_blank" rel="noopener" class="mono">'
                 + esc(x.example_url) + '</a>' : '');
          }).join('<br>')
        + '<br><br>The marketplace distinguishes REMOVED from NEVER EXISTED, '
        + 'and these say never existed — so this is almost certainly the '
        + 'artist name being spelled differently there than it is here, not '
        + 'a takedown. Open that address to confirm, fix the name below, and '
        + 'run it again.</p>'
      : '';

    var body;
    if (s.status === 'running') {
      // Counted against what THIS sweep is doing. The catalogue total is
      // mentioned as context, never as the denominator — "17 of 1543" on a
      // run covering 627 was true and useless.
      body = '<p><strong>Checking.</strong> ' + (c.checked_this_run || 0)
        + ' of ' + (c.run_total || 0) + ' in this sweep.</p>'
        + '<p class="muted">' + (c.live || 0) + ' still there · '
        + (c.gone || 0) + ' removed'
        + (c.no_page ? ' · ' + c.no_page + ' with no page at that address' : '')
        + (c.unknown ? ' · ' + c.unknown + ' we could not look at' : '')
        + '.</p>'
        + (s.working ? '' : '<p class="quota-note">Waiting for the worker '
            + 'machine to pick this up. If it stays here, check that the '
            + 'agent is running.</p>')
        + '<p class="muted">Processing and uploads are NOT paused for this —'
        + 'it only reads pages. This page updates itself.</p>'
        + '<p><button class="btn btn-ghost btn-tiny" data-action="stop" '
        + 'type="button">STOP</button> <span class="muted">— everything '
        + 'checked so far is kept.</span></p>';
    } else {
      body = '<p><strong>' + esc(s.status) + '.</strong> ' + esc(s.note) + '</p>';
    }

    el.innerHTML = blocked + suspect + body;
  }

  // ── Findings ─────────────────────────────────────────────────────────
  //
  // `mode` decides which buttons a row gets:
  //   'answer'  — the three verdicts, for a real disagreement
  //   'settled' — one button to undo, for something already explained
  //   'none'    — nothing to press
  function group(title, rows, total, help, mode) {
    if (!total) return '';
    var showNote = (mode === 'settled');
    return '<h4 class="section-head">' + esc(title) + ' — ' + total + '</h4>'
      + '<p class="muted">' + esc(help) + '</p>'
      + '<table class="data-table"><thead><tr><th>TITLE</th><th>ACCOUNT</th>'
      + '<th>CODE</th><th>CHECKED</th>'
      + (showNote ? '<th>YOUR NOTE</th>' : '')
      + '<th></th></tr></thead><tbody>'
      + rows.map(function (r) {
          return '<tr>'
            + '<td>' + (r.url
                ? '<a href="' + esc(r.url) + '" target="_blank" '
                  + 'rel="noopener">' + esc(r.title) + '</a>'
                : esc(r.title)) + '</td>'
            + '<td>' + esc(r.account) + '</td>'
            + '<td class="mono">' + (r.http == null ? '—' : r.http) + '</td>'
            + '<td class="mono">' + when(r.checked_at) + '</td>'
            + (showNote
                ? '<td>' + esc(r.note)
                  + '<br><span class="muted mono">'
                  + esc(r.ack_by) + ' · ' + when(r.ack_at) + '</span></td>'
                : '')
            + '<td>' + (mode === 'answer'
                ? '<button class="btn btn-ghost btn-tiny" '
                  + 'data-explain="' + r.id + '" data-answer="taken_down">'
                  + 'TAKEN DOWN</button> '
                  + '<button class="btn btn-ghost btn-tiny" '
                  + 'data-explain="' + r.id + '" data-answer="requeue">'
                  + 'UPLOAD IT AGAIN</button> '
                  + '<button class="btn btn-ghost btn-tiny" '
                  + 'data-explain="' + r.id + '" data-answer="ignore">'
                  + 'I&nbsp;CHECKED&nbsp;IT&nbsp;—&nbsp;STOP&nbsp;ASKING</button>'
                : '')
              + (mode === 'settled'
                ? '<button class="btn btn-ghost btn-tiny" '
                  + 'data-explain="' + r.id + '" data-answer="unsettle">'
                  + 'REPORT IT AGAIN</button>'
                : '') + '</td>'
            + '</tr>';
        }).join('')
      + '</tbody></table>'
      + (total > rows.length
          ? '<p class="muted">Showing ' + rows.length + ' of ' + total + '.</p>'
          : '');
  }

  function renderFindings(f) {
    state.findings = f;
    // Only things that still WANT a decision are counted. Settled rows and
    // not-yet-checked rows both appear on the page, and neither is a thing
    // waiting on him — putting them in this number would make the badge go
    // up when he cleared something, which is the wrong direction.
    var total = f.gone_total + f.no_page_total + f.unknown_total
              + f.back_total + f.impossible_total;
    q('[data-findings-summary]').textContent = total
      ? total + ' to look at' : 'nothing to explain';

    var html =
      group('TAKEN DOWN', f.gone, f.gone_total,
            'The marketplace says these pages were REMOVED — they existed '
            + 'and now they do not. That is a real takedown, or someone '
            + 'deleted them by hand. This is the list worth reading.', 'answer')
      + group('NO PAGE AT THAT ADDRESS', f.no_page, f.no_page_total,
            'The marketplace says no page has ever existed here — which is '
            + 'NOT the same as removed. Usually it means the artist name or '
            + 'the stored title does not match what the marketplace has, so '
            + 'we are looking in the wrong place. Check the artist name '
            + 'before treating any of these as missing.', 'answer')
      + group('COULD NOT LOOK', f.unknown, f.unknown_total,
            'The site refused us or had a moment. This is NOT evidence that '
            + 'anything is missing — run the sweep again later and these '
            + 'usually resolve themselves.', 'none')
      // ── WHICH ONES WERE MISSED ──────────────────────────────────────
      // Named, not counted. A sweep that reached 13 of 15 has to be able
      // to say which two it did not reach, or the only honest reading of
      // the screen is "13 are fine and I know nothing about the rest".
      + group('NOT CHECKED YET', f.not_checked, f.not_checked_total,
            'We believe these are live on the marketplace and no sweep has '
            + 'ever looked at them. This is not a problem with the listing '
            + '— it is the part of the catalogue nobody has been through. '
            + 'Run the check again and it will pick these up.', 'none')
      + group('BACK AGAIN', f.back, f.back_total,
            'You marked these as removed, and their pages are loading again.',
            'answer')
      + group('MARKED UPLOADED WITH NOTHING BEHIND IT', f.impossible,
            f.impossible_total,
            'These say uploaded but no processed image is recorded against '
            + 'them, which cannot happen. The data is wrong rather than the '
            + 'marketplace.', 'answer')
      // ── WHAT YOU HAVE ALREADY SETTLED ───────────────────────────────
      // Kept visible rather than silently dropped, so a decision can be
      // read back and undone. Each note is tied to the answer it was about,
      // so if the marketplace ever says something DIFFERENT about one of
      // these it leaves this list by itself and reappears above.
      + group('YOU HAVE ALREADY DEALT WITH THESE', f.settled, f.settled_total,
            'You looked at each of these and wrote down what it meant, so '
            + 'they are kept out of the lists above. If the marketplace ever '
            + 'gives a DIFFERENT answer about one — the page loads again, or '
            + 'we get blocked — it comes back on its own. Nothing here '
            + 'expires and nothing needs clearing.', 'settled');

    q('[data-findings-list]').innerHTML = html
      || '<p class="muted">Nothing disagrees. Either everything is where we '
         + 'think it is, or no sweep has run yet.</p>';
  }

  // ── Accounts ─────────────────────────────────────────────────────────
  function renderAccounts(list) {
    q('[data-accounts-summary]').textContent =
      list.filter(function (a) { return a.ready; }).length + ' of '
      + list.length + ' ready';

    q('[data-accounts-list]').innerHTML = list.length
      ? '<table class="data-table"><thead><tr><th>ACCOUNT</th>'
        + '<th>ARTIST NAME ON THE MARKETPLACE</th><th>LISTINGS</th>'
        + '<th></th></tr></thead><tbody>'
        + list.map(function (a) {
            return '<tr>'
              + '<td><strong>' + esc(a.name) + '</strong>'
              + (a.ready ? '' : ' <span class="danger">· not set</span>')
              + '</td>'
              + '<td><input type="text" style="width:16rem" '
              + 'data-artist="' + a.id + '" value="' + esc(a.artist_name)
              + '" placeholder="e.g. Golden Reel"></td>'
              + '<td class="mono">' + (a.claimed || 0) + '</td>'
              + '<td><button class="btn btn-ghost btn-tiny" '
              + 'data-save-artist="' + a.id + '">SAVE</button></td>'
              + '</tr>';
          }).join('')
        + '</tbody></table>'
        + '<p class="muted">Saving shows a real address built from the name, '
        + 'so open it and check it loads. That takes five seconds and saves '
        + 'an hour of every listing reading as missing.</p>'
      : '<p class="muted">No FineArtAmerica accounts.</p>';
  }

  // ── Loading ──────────────────────────────────────────────────────────
  async function load() {
    var data;
    try {
      data = await getJSON(API + '/overview');
    } catch (e) {
      q('[data-sweep-panel]').innerHTML =
        '<div class="danger">Could not load: ' + esc(e.message) + '</div>';
      return;
    }

    renderSweep(data);
    // ALWAYS ON SCREEN. It used to be hidden unless a sweep was running,
    // which meant it vanished the moment the sweep it described finished —
    // and "what did the machine do" is asked afterwards more than during.
    var logPanel = q('[data-sweep-log-panel]');
    if (logPanel) logPanel.hidden = false;
    renderJob(data.machine);
    renderAccounts(data.accounts || []);
    Object.keys(data.settings || {}).forEach(function (k) {
      var el = q('[data-set="' + k + '"]');
      if (el && document.activeElement !== el) el.value = data.settings[k];
    });

    try {
      renderFindings(await getJSON(API + '/findings'));
    } catch (e) { /* the sweep panel already showed the failure */ }

    // Poll only while work is actually moving. A finished sweep changes
    // when a button is pressed, and there is nothing to refresh for.
    var moving = data.sweep && data.sweep.status === 'running';
    if (moving && !state.timer) state.timer = setInterval(load, 5000);
    if (!moving && state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  // ── Actions ──────────────────────────────────────────────────────────
  async function act(url, body, confirmText, after) {
    if (confirmText && !confirm(confirmText)) return;
    try {
      var r = await postJSON(url, body);
      if (after) after(r);
      await load();
    } catch (e) {
      alert(e.message);
    }
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-action], [data-save-artist], [data-explain]');
    if (!t) return;

    if (t.dataset.saveArtist) {
      var id = parseInt(t.dataset.saveArtist, 10);
      var input = q('[data-artist="' + id + '"]');
      postJSON(API + '/artist-name',
               { account_id: id, artist_name: input.value })
        .then(function (r) {
          if (r.example) {
            // Shown rather than just saved. A wrong name makes every single
            // listing read as missing, and this is the cheapest way to see
            // that before an hour is spent proving it.
            if (confirm('Saved.\n\nA listing address built from that name '
                        + 'looks like:\n\n' + r.example
                        + '\n\nOpen it now to check it loads?')) {
              window.open(r.example, '_blank', 'noopener');
            }
          }
          return load();
        })
        .catch(function (err) { alert(err.message); });
      return;
    }

    if (t.dataset.explain) {
      var answer = t.dataset.answer;
      var prompts = {
        taken_down: 'Mark this as taken down?\n\nIt stays in the records '
                    + 'with the reason you give, and stops counting as live.',
        requeue:    'Send this back to be uploaded again?\n\nIt goes to the '
                    + 'back of the upload queue and the pipeline will do it '
                    + 'properly this time.',
        ignore:     'Stop reporting this one?\n\nWrite down what you found '
                    + 'when you looked. It moves to "already dealt with" and '
                    + 'stays there while the marketplace keeps giving the '
                    + 'same answer. If the answer ever changes, it comes '
                    + 'back on its own.',
        unsettle:   'Report this one again?\n\nYour note is removed and it '
                    + 'goes back into the lists above.'
      };
      if (!confirm(prompts[answer] || 'Are you sure?')) return;
      var reason = answer === 'unsettle' ? '' : (
        prompt(answer === 'ignore'
          ? 'What did you find? This is kept with the record — for example '
            + '"opened it by hand, the page loads fine".'
          : 'Why? (optional — it is kept with the record)') || '');
      act(API + '/explain',
          { id: parseInt(t.dataset.explain, 10), answer: answer, reason: reason });
      return;
    }

    var a = t.dataset.action;
    if (a === 'start') {
      act(API + '/start', {},
          'Check every listing now?\n\nIt only reads pages — nothing on the '
          + 'marketplace changes, and processing and uploads keep running in'
          + 'between.',
          function (r) {
            if (!r.queued) alert('Nothing to check.');
          });
    } else if (a === 'stop') {
      act(API + '/stop', {},
          'Stop the sweep? Everything checked so far is kept.');
    } else if (a === 'save-settings') {
      var body = {};
      document.querySelectorAll('[data-set]').forEach(function (el) {
        body[el.dataset.set] = Number(el.value);
      });
      q('[data-settings-status]').textContent = 'Saving…';
      postJSON(API + '/settings', body).then(function () {
        q('[data-settings-status]').textContent = 'Saved.';
        setTimeout(function () {
          q('[data-settings-status]').textContent = '';
        }, 2500);
      }).catch(function (err) {
        q('[data-settings-status]').textContent = err.message;
      });
    }
  });

  load();
})();
