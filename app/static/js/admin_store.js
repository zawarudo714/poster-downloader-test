/*
 * TeePublic listing health.
 *
 * Two jobs: make it obvious what the run is doing and what you can do next,
 * and let you read a catalogue of several thousand designs without drowning
 * in it. Hence per-account summaries with the detail one click away, rather
 * than one enormous list.
 *
 * Polls only while something is actually moving. A run waiting at a gate
 * changes when you press a button, so there is nothing to refresh for.
 */
(function () {
  'use strict';

  var API = '/admin/api/store';
  var state = { account: null, accountName: '', status: 'missing', timer: null };

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

  // ── The run ──────────────────────────────────────────────────────────
  function renderRun(data) {
    var run = data.run, el = q('[data-run-panel]'), summary = q('[data-run-summary]');

    // Live listings switched off and not put back. Shown ABOVE everything
    // else and whether or not a run is going, because it is money leaking
    // and it outlives whichever run caused it.
    var rescue = data.stranded
      ? '<p class="quota-note"><strong>' + data.stranded + ' design(s) are '
        + 'switched OFF right now.</strong> They earn nothing until they go '
        + 'back on.'
        + (run ? ' Wait for this run to finish, then put them back.'
               : ' <button class="btn btn-accent btn-tiny" '
                 + 'data-action="reactivate-all" type="button">SWITCH '
                 + data.stranded + ' BACK ON</button>')
        + '</p>'
      : '';

    if (!run) {
      summary.textContent = '';
      var blocked = data.blocked || [];
      var last = (data.history || [])[0];
      var t = data.totals || {};
      el.innerHTML = rescue +
        (last && last.status === 'failed'
          ? '<p class="quota-note"><strong>The last sweep failed.</strong> '
            + esc(last.note) + '</p>' : '') +
        (blocked.length
          ? '<p class="quota-note">' + blocked.length + ' account(s) will be '
            + 'SKIPPED — no store address: ' + esc(blocked.join(', ')) + '</p>'
          : '') +
        (data.wall_paths ? ''
          : '<p class="quota-note">No mouse paths recorded yet. Run '
            + 'RECORD_PATHS.bat on the worker machine before a sweep.</p>') +
        '<p>Nothing running. ' + (t.total || 0) + ' designs known across '
        + data.ready + ' account(s)'
        + (t.missing ? ', <strong>' + t.missing + ' currently missing</strong>' : '')
        + '.</p>' +
        '<p><label class="inline-check"><input type="checkbox" data-auto> '
        + 'run it all automatically — no confirmation between stages</label></p>' +
        '<p>'
        + (data.continue_left && data.continue_left < (t.total || 0)
            ? '<button class="btn btn-accent" data-action="start-continue" '
              + 'type="button">CONTINUE — ' + data.continue_left
              + ' LEFT TO CHECK</button> ' : '')
        + '<button class="btn ' + (data.continue_left && data.continue_left < (t.total || 0)
            ? 'btn-ghost' : 'btn-accent') + '" data-action="start" type="button"'
        + (data.ready ? '' : ' disabled') + '>FULL SWEEP</button> ' +
        '<button class="btn btn-ghost" data-action="start-missing" type="button"'
        + (t.missing ? '' : ' disabled') + '>RECHECK THE ' + (t.missing || 0)
        + ' MISSING</button></p>' +
        (data.continue_left && data.continue_left < (t.total || 0)
          ? '<p class="muted">CONTINUE skips the '
            + ((t.total || 0) - data.continue_left) + ' designs already checked '
            + 'in the last ' + data.continue_within_h + ' hours — use it when a '
            + 'sweep was interrupted. FULL SWEEP rechecks everything, which is '
            + 'what you want normally, because a design that was fine last week '
            + 'may not be fine today.</p>'
          : '') +
        '<p class="muted">A recheck only looks at designs already marked '
        + 'missing, so it takes minutes rather than hours.</p>' +
        // ── ACT ON WHAT WE ALREADY KNOW, WITHOUT SCANNING AGAIN ─────────
        //
        // "Missing" is recorded on the DESIGN, not on the run that found
        // it, so it survives a run being stopped halfway. Making him scan
        // for hours to re-learn facts already on file would be busywork —
        // and the mirror image, SWITCH BACK ON, has always worked this way.
        (t.to_deactivate
          ? '<hr class="rule"><p><button class="btn btn-ghost" '
            + 'data-action="deactivate-missing" type="button">SWITCH OFF THE '
            + t.to_deactivate + ' ALREADY KNOWN MISSING</button></p>'
            + '<p class="muted">Goes straight to switching off, with no scan '
            + 'first — for when a sweep was stopped partway and you just want '
            + 'it finished. Switching off and back on again is the cure that '
            + 'usually puts a design back in search.'
            + (t.missing > t.to_deactivate
                ? ' ' + (t.missing - t.to_deactivate) + ' of the ' + t.missing
                  + ' missing are held back as excluded or probably-vague '
                  + 'tags.'
                : '')
            + '</p>'
          : '');
      return;
    }

    var c = run.counts || {};
    summary.textContent = 'run #' + run.id + ' · ' + run.mode
      + (run.auto ? ' · automatic' : ' · step by step')
      + ' · started ' + when(run.started_at);

    var body = '';
    if (run.retry_at && new Date(run.retry_at) > new Date()) {
      // Not a failure and not a pause: the far side had a moment and we are
      // waiting it out. Photoshop and uploads have the machine meanwhile.
      body = '<p><strong>Waiting to try again</strong> at '
        + when(run.retry_at) + ' (attempt ' + run.retry_count + ').</p>'
        + '<p class="muted">' + esc(run.retry_note) + '</p>'
        + '<p class="muted">Photoshop and uploads have the machine until '
        + 'then. Nothing is lost.</p>'
        + '<p><button class="btn btn-accent" data-action="resume" '
        + 'type="button">TRY NOW</button></p>';
    } else if (run.paused) {
      body = '<p><strong>Paused</strong> at the ' + esc(run.status) + ' stage'
        + (run.paused_by ? ' by ' + esc(run.paused_by) : '') + '.</p>' +
        '<p class="muted">Photoshop and uploads have the machine back. '
        + 'Nothing is lost — resuming carries on from here.</p>' +
        (c.deactivated
          ? '<p class="quota-note">' + c.deactivated + ' design(s) are '
            + 'switched OFF while this is paused.</p>' : '') +
        '<p><button class="btn btn-accent" data-action="resume" type="button">'
        + 'RESUME</button></p>';
    } else if (run.status === 'scanning') {
      // Counted against what THIS run set out to do, not the whole
      // catalogue. A CONTINUE covering 627 reporting "17 of 1543" was true
      // and useless — you could only tell by reading the node's console.
      var scope = run.mode === 'continue' ? 'in this continue'
                : run.mode === 'missing_only' ? 'of the ones that were missing'
                : 'in this sweep';
      body = '<p><strong>Checking designs.</strong> ' + c.checked + ' of '
        + c.run_total + ' ' + scope
        + (c.run_total < c.total
            ? ' <span class="muted">(' + c.total + ' in the catalogue — the '
              + 'rest were checked recently)</span>' : '')
        + '.</p>'
        + (c.missing
            ? '<p class="muted">' + c.missing + ' designs are marked missing '
              + 'across the whole catalogue right now — not all from this run.</p>'
            : '')
        + '<p class="muted">Photoshop and uploads are paused while this runs. '
        + 'This page updates itself.</p>'
        + (c.checked
            ? '<p><button class="btn btn-accent" data-action="stop-scanning" '
              + 'type="button">STOP AND REVIEW THE ' + c.checked
              + ' CHECKED SO FAR</button></p>' : '');
    } else if (run.status === 'reviewing') {
      body = '<p><strong>' + c.to_deactivate + ' designs will be switched off '
        + 'and back on.</strong></p>'
        + '<p class="muted">' + c.checked + ' checked in this run · '
        + c.missing + ' missing across the catalogue'
        + (c.vague ? ' · ' + c.vague + ' held back as probably-vague tags' : '')
        + (c.excluded ? ' · ' + c.excluded + ' excluded by you' : '')
        + '.</p>'
        + '<p><button class="btn btn-accent" data-action="deactivate" '
        + 'type="button">SWITCH OFF ' + c.to_deactivate + ' DESIGNS</button></p>';
    } else if (run.status === 'deactivating') {
      body = '<p><strong>Switching the missing designs off.</strong> '
        + c.deactivated + ' switched off so far, ' + c.to_deactivate
        + ' still to go.</p>'
        + accountProgress(run);
    } else if (run.status === 'confirming') {
      body = '<p><strong>' + c.deactivated + ' designs are switched off.</strong></p>'
        + '<p class="quota-note">These are OFF right now. They come back when '
        + 'you press the button below.</p>'
        + '<p><button class="btn btn-accent" data-action="reactivate" '
        + 'type="button">REACTIVATE THEM</button></p>';
    } else if (run.status === 'reactivating') {
      body = '<p><strong>Switching them back on.</strong> ' + c.deactivated
        + ' still switched off.</p>'
        + accountProgress(run);
    }

    // ── WORK THIS RUN GAVE UP ON, SAID OUT LOUD ──────────────────────────
    //
    // A design that would not switch is SKIPPED for the rest of the run, so
    // that the stage can end instead of handing the same failure round for
    // ever. Skipping quietly and then reporting the stage finished would be
    // a run claiming to have done something it did not.
    var stuck = run.stuck_total
      ? '<p class="quota-note"><strong>' + run.stuck_total + ' design(s) '
        + 'would not switch and were skipped.</strong> They keep their place '
        + 'in the catalogue and will be tried again on the next run.<br>'
        + (run.stuck || []).map(function (s) {
            return '<span class="mono">' + esc(s.title) + '</span> — '
                   + esc(s.why);
          }).join('<br>')
        + (run.stuck_total > (run.stuck || []).length
            ? '<br>…and ' + (run.stuck_total - (run.stuck || []).length)
              + ' more.' : '')
        + '</p>'
      : '';

    el.innerHTML = rescue + body + stuck +
      '<p class="muted mono">' + esc(run.note || '') + '</p>' +
      (run.paused ? '' :
        '<p><button class="btn btn-ghost btn-tiny" data-action="pause" '
        + 'type="button">PAUSE</button> ' +
        '<button class="btn btn-ghost btn-tiny" data-action="abandon" '
        + 'type="button">STOP THIS RUN</button> ' +
        '<span class="muted">— pause gives the machine back and keeps your '
        + 'place; stop ends the run.</span></p>');
  }

  // These stages run one job per account, and the node does one job at a
  // time. Saying which account we are on is the difference between "is this
  // stuck?" and "it is on the third of five".
  function accountProgress(run) {
    if (!run.stage_jobs_total) return '';
    return '<p class="muted">Account ' + (run.stage_jobs_done + 1) + ' of '
      + run.stage_jobs_total + ' — each account is done in turn.</p>';
  }

  // ── Accounts ─────────────────────────────────────────────────────────
  function renderAccounts(list) {
    q('[data-accounts-summary]').textContent =
      list.reduce(function (n, a) { return n + (a.designs || 0); }, 0)
      + ' designs · ' + list.length + ' accounts';

    q('[data-accounts-list]').innerHTML = list.length
      ? '<table class="data-table"><thead><tr><th>ACCOUNT</th><th>DESIGNS</th>'
        + '<th>VISIBLE</th><th>MISSING</th><th>VAGUE</th><th>EXCLUDED</th>'
        + '<th>GONE</th><th>LAST CHECKED</th></tr></thead><tbody>'
        + list.map(function (a) {
            return '<tr data-open-account="' + a.id + '" style="cursor:pointer">'
              + '<td><strong>' + esc(a.name) + '</strong>'
              + (a.ready ? '' : ' <span class="danger">· no address</span>')
              + '</td>'
              + '<td>' + (a.designs || 0) + '</td>'
              + '<td>' + (a.visible || 0) + '</td>'
              + '<td' + (a.missing ? ' class="danger"' : '') + '>'
              + (a.missing || 0) + '</td>'
              + '<td>' + (a.vague || 0) + '</td>'
              + '<td>' + (a.excluded || 0) + '</td>'
              + '<td>' + (a.removed || 0) + '</td>'
              + '<td class="muted mono">' + when(a.checked_at) + '</td></tr>';
          }).join('') + '</tbody></table>'
      : '<p class="muted">No TeePublic accounts yet.</p>';
  }

  // ── Designs ──────────────────────────────────────────────────────────
  async function loadDesigns() {
    var el = q('[data-designs-list]');
    q('[data-designs-title]').firstChild.nodeValue =
      (state.status === 'missing' ? 'MISSING' : state.status.toUpperCase())
      + ' — ' + (state.account ? state.accountName : 'ALL ACCOUNTS') + ' ';

    try {
      var url = API + '/designs?status=' + encodeURIComponent(state.status)
        + (state.account ? '&account_id=' + state.account : '');
      var rows = (await getJSON(url)).designs || [];
      q('[data-designs-count]').textContent = rows.length + ' shown';

      if (!rows.length) {
        el.innerHTML = '<p class="muted">Nothing here.</p>';
        return;
      }
      el.innerHTML =
        '<table class="data-table"><thead><tr><th>DESIGN</th><th>ACCOUNT</th>'
        + '<th>TAG</th><th>STATE</th><th>TRIED</th><th></th></tr></thead><tbody>'
        + rows.map(function (d) {
            // The flag that matters: several failed cures means the TAG is
            // probably the problem, not the listing. Said in words rather
            // than left as a number nobody would interpret.
            var note = d.excluded ? '<span class="muted">excluded'
                         + (d.exclude_reason ? ' — ' + esc(d.exclude_reason) : '')
                         + '</span>'
                     : d.vague ? '<span class="danger">VAGUE TAG — check by hand</span>'
                     : d.deactivated ? '<span class="muted">switched off</span>'
                     : d.action_error ? '<span class="danger">' + esc(d.action_error) + '</span>'
                     : d.error ? '<span class="muted">' + esc(d.error) + '</span>' : '';
            return '<tr><td>'
              + (d.url ? '<a href="' + esc(d.url) + '" target="_blank" '
                         + 'rel="noopener">' + esc(d.title) + '</a>'
                       : esc(d.title))
              + ' <span class="muted mono">#' + esc(d.design_id) + '</span></td>'
              + '<td><span class="muted">' + esc(d.account) + '</span></td>'
              + '<td class="muted">' + esc(d.tag || '—') + '</td>'
              + '<td' + (d.status === 'missing' ? ' class="danger"' : '') + '>'
              + esc(d.status) + '</td>'
              + '<td class="muted mono">' + d.fix_attempts + '×</td>'
              + '<td>' + note + ' <button class="btn btn-ghost btn-tiny" '
              + 'data-toggle-exclude="' + d.id + '" data-excluded="'
              + (d.excluded ? '1' : '0') + '">'
              + (d.excluded ? 'SCAN AGAIN' : 'EXCLUDE') + '</button></td></tr>';
          }).join('') + '</tbody></table>';
    } catch (e) {
      el.innerHTML = '<p class="muted">Could not load: ' + esc(e.message) + '</p>';
    }
  }

  function renderUrls(list) {
    q('[data-urls-list]').innerHTML = list.length
      ? '<table class="data-table"><tbody>' + list.map(function (a) {
          return '<tr><td>' + esc(a.name) + '</td>'
            + '<td><input type="text" style="width:100%" data-store-url="'
            + a.id + '" value="' + esc(a.store_url) + '" '
            + 'placeholder="https://www.teepublic.com/user/yourname"></td>'
            + '<td><button class="btn btn-ghost btn-tiny" data-save-url="'
            + a.id + '" type="button">SAVE</button></td></tr>';
        }).join('') + '</tbody></table>'
      : '<p class="muted">No TeePublic accounts yet.</p>';
  }

  // Only refill a box you are not currently typing into. The page reloads
  // itself every few seconds while a sweep runs, and overwriting a
  // half-typed number would make the field unusable.
  function renderSettings(values) {
    Object.keys(values).forEach(function (key) {
      var el = q('[data-set="' + key + '"]');
      if (el && document.activeElement !== el) el.value = values[key];
    });
  }

  function renderHistory(rows) {
    q('[data-history-list]').innerHTML = rows.length
      ? '<table class="data-table"><tbody>' + rows.map(function (r) {
          return '<tr><td>#' + r.id + '</td><td>' + esc(r.status) + '</td>'
            + '<td class="muted">' + esc(r.mode) + (r.auto ? ' · auto' : '')
            + '</td><td class="muted mono">' + when(r.started_at) + '</td>'
            + '<td class="muted">' + esc(r.note) + '</td></tr>';
        }).join('') + '</tbody></table>'
      : '<p class="muted">None yet.</p>';
  }

  async function reload() {
    try {
      var data = await getJSON(API + '/overview');
      renderRun(data);
      renderAccounts(data.accounts || []);
      renderUrls(data.accounts || []);
      renderHistory(data.history || []);
      renderSettings(data.settings || {});
      await loadDesigns();

      // ── KEEP LOOKING WHILE A RUN EXISTS ────────────────────────────
      //
      // Polling used to stop the moment a run was paused, on the reasoning
      // that a paused run does not change. It does: the node takes up to a
      // design to wind down, and the server moves the run when it reports.
      // So the panel sat there saying "paused at the scanning stage" while
      // the database had moved on — and the next button pressed acted on
      // the real state, not the shown one. A screen that is confidently
      // wrong is worse than one that is slow.
      var run = data.run;
      var moving = run
        && (['scanning', 'deactivating', 'reactivating'].indexOf(run.status) >= 0
            || !!run.retry_at);
      clearTimeout(state.timer);
      if (moving) state.timer = setTimeout(reload, run.paused ? 15000 : 5000);
    } catch (e) {
      q('[data-run-panel]').innerHTML =
        '<p class="muted">Could not load: ' + esc(e.message) + '</p>';
    }
  }

  function act(url, body, confirmText, after) {
    if (confirmText && !confirm(confirmText)) return;
    postJSON(url, body).then(function (r) {
      if (after) after(r);
      reload();
    }).catch(function (err) { alert(err.message); });
  }

  document.addEventListener('click', function (e) {
    var row = e.target.closest('[data-open-account]');
    var t = e.target.closest('[data-action], [data-save-url], [data-toggle-exclude]');

    if (row && !t) {
      state.account = Number(row.dataset.openAccount);
      state.accountName = row.querySelector('strong').textContent;
      loadDesigns();
      return;
    }
    if (!t) return;

    if (t.dataset.saveUrl) {
      act(API + '/account-url', {
        id: Number(t.dataset.saveUrl),
        store_url: q('[data-store-url="' + t.dataset.saveUrl + '"]').value.trim()
      });
      return;
    }
    if (t.dataset.toggleExclude) {
      var on = t.dataset.excluded === '1';
      var reason = on ? '' : (prompt('Why exclude it? (optional)') || '');
      act(API + '/listing', { id: Number(t.dataset.toggleExclude),
                              excluded: !on, reason: reason });
      return;
    }

    var a = t.dataset.action, auto = !!(q('[data-auto]') || {}).checked;
    if (a === 'start') {
      act(API + '/start', { auto: auto, mode: 'full' },
          'Start a FULL sweep? Every design is rechecked, including ones '
          + 'checked recently. Photoshop and uploads pause until it is done.');
    } else if (a === 'start-continue') {
      act(API + '/start', { auto: auto, mode: 'continue' },
          'Carry on from where the last sweep stopped? Designs checked in '
          + 'the last day are skipped.');
    } else if (a === 'start-missing') {
      act(API + '/start', { auto: auto, mode: 'missing_only' },
          'Recheck only the designs currently marked missing?');
    } else if (a === 'deactivate') {
      act(API + '/advance', { stage: 'deactivate' },
          'Turn the missing designs OFF? They stay off until reactivated.');
    } else if (a === 'reactivate') {
      act(API + '/advance', { stage: 'reactivate' }, 'Turn them back ON?');
    } else if (a === 'stop-scanning') {
      act(API + '/stop-scanning', {},
          'Stop scanning and review what has been found so far?',
          function (r) { alert('Stopped after ' + r.checked + ' — ' + r.missing
                               + ' missing.'); });
    } else if (a === 'pause') {
      act(API + '/pause', {},
          'Pause? Photoshop and uploads get the machine back and you can '
          + 'resume later.',
          function (r) {
            if (r.left_deactivated) {
              alert(r.left_deactivated + ' design(s) are switched OFF while '
                    + 'this is paused.');
            }
          });
    } else if (a === 'resume') {
      act(API + '/resume', {});
    } else if (a === 'abandon') {
      act(API + '/abandon', {},
          'Stop this run? Anything already deactivated stays off.',
          function (r) {
            if (r.left_deactivated) {
              alert(r.left_deactivated + ' design(s) were left switched off.');
            }
          });
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
    } else if (a === 'reactivate-all') {
      act(API + '/reactivate-all', {},
          'Switch every design that is currently off back on again?',
          function (r) {
            alert('Putting ' + r.designs + ' design(s) back on across '
                  + r.accounts + ' account(s).');
          });
    } else if (a === 'deactivate-missing') {
      // Spelled out because it switches LIVE listings off with no scan
      // first, and the confirmation is the only place that can say so.
      act(API + '/deactivate-missing', {},
          'Switch off every design already marked missing, without scanning '
          + 'first?\n\nThey stay off until they are switched back on, and '
          + 'Photoshop and uploads pause while it works. Roughly an hour per '
          + 'account.',
          function (r) {
            alert('Switching off ' + r.designs + ' design(s) across '
                  + r.accounts + ' account(s), one account at a time.');
          });
    } else if (a === 'all-accounts') {
      state.account = null; state.accountName = '';
      loadDesigns();
    }
  });

  document.addEventListener('change', function (e) {
    if (e.target.matches('[data-filter-status]')) {
      state.status = e.target.value;
      loadDesigns();
    }
  });

  reload();
})();
