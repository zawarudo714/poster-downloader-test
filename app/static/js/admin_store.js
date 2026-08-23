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

    if (!run) {
      summary.textContent = '';
      var blocked = data.blocked || [];
      var last = (data.history || [])[0];
      var t = data.totals || {};
      el.innerHTML =
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
        '<p><button class="btn btn-accent" data-action="start" type="button"'
        + (data.ready ? '' : ' disabled') + '>FULL SWEEP</button> ' +
        '<button class="btn btn-ghost" data-action="start-missing" type="button"'
        + (t.missing ? '' : ' disabled') + '>RECHECK THE ' + (t.missing || 0)
        + ' MISSING</button></p>' +
        '<p class="muted">A recheck only looks at designs already marked '
        + 'missing, so it takes minutes rather than hours.</p>';
      return;
    }

    var c = run.counts || {};
    summary.textContent = 'run #' + run.id + ' · ' + run.mode
      + (run.auto ? ' · automatic' : ' · step by step')
      + ' · started ' + when(run.started_at);

    var body = '';
    if (run.paused) {
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
      body = '<p><strong>Scanning.</strong> ' + c.checked + ' of ' + c.total
        + ' checked' + (c.missing ? ', ' + c.missing + ' missing' : '') + '.</p>'
        + '<p class="muted">Everything else on the worker machine is paused. '
        + 'The page updates itself.</p>'
        + (c.checked
            ? '<p><button class="btn btn-accent" data-action="stop-scanning" '
              + 'type="button">STOP SCANNING — REVIEW THE ' + c.checked
              + ' CHECKED</button></p>' : '');
    } else if (run.status === 'reviewing') {
      body = '<p><strong>' + c.missing + ' missing</strong> of ' + c.checked
        + ' checked.'
        + (c.vague ? ' ' + c.vague + ' look like vague tags and will be left '
                     + 'alone.' : '') + '</p>'
        + '<p><button class="btn btn-accent" data-action="deactivate" '
        + 'type="button">DEACTIVATE THEM</button></p>';
    } else if (run.status === 'deactivating') {
      body = '<p><strong>Deactivating.</strong> ' + c.deactivated + ' done.</p>';
    } else if (run.status === 'confirming') {
      body = '<p><strong>' + c.deactivated + ' are now off.</strong></p>'
        + '<p class="quota-note">These are OFF right now. They come back when '
        + 'you press the button below.</p>'
        + '<p><button class="btn btn-accent" data-action="reactivate" '
        + 'type="button">REACTIVATE THEM</button></p>';
    } else if (run.status === 'reactivating') {
      body = '<p><strong>Reactivating.</strong> ' + c.deactivated
        + ' still to go.</p>';
    }

    el.innerHTML = body +
      '<p class="muted mono">' + esc(run.note || '') + '</p>' +
      (run.paused ? '' :
        '<p><button class="btn btn-ghost btn-tiny" data-action="pause" '
        + 'type="button">PAUSE</button> ' +
        '<button class="btn btn-ghost btn-tiny" data-action="abandon" '
        + 'type="button">STOP THIS RUN</button> ' +
        '<span class="muted">— pause gives the machine back and keeps your '
        + 'place; stop ends the run.</span></p>');
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
      await loadDesigns();

      var run = data.run;
      var moving = run && !run.paused
        && ['scanning', 'deactivating', 'reactivating'].indexOf(run.status) >= 0;
      clearTimeout(state.timer);
      if (moving) state.timer = setTimeout(reload, 5000);
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
      act(API + '/start', { auto: auto },
          'Start a full sweep? Photoshop and uploads pause until it is done.');
    } else if (a === 'start-missing') {
      act(API + '/start', { auto: auto, missing_only: true },
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
