/*
 * TeePublic listing health.
 *
 * The page has one job: make it obvious what the run is doing and what you
 * can do next. Everything else is detail underneath that.
 *
 * It polls while a run is active, because a scan takes hours and the whole
 * point is being able to glance at it. It stops polling when nothing is
 * running, so an idle tab costs nothing.
 */
(function () {
  'use strict';

  var API = '/admin/api/store';
  var state = { onlyMissing: true, timer: null };

  function q(sel) { return document.querySelector(sel); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
               '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function when(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleString();
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

  // ── The run panel ────────────────────────────────────────────────────
  //
  // Written as a sentence about what is happening plus the one button that
  // makes sense right now. A stage list with four greyed-out buttons would
  // be more "complete" and much harder to act on.
  function renderRun(data) {
    var run = data.run;
    var el = q('[data-run-panel]');
    var summary = q('[data-run-summary]');

    if (!run) {
      summary.textContent = '';
      var blocked = data.blocked || [];
      // A run that FAILED released the pipeline correctly and is therefore
      // "not running" — but it is the most important thing on the page, and
      // burying it in the history list would read as "nothing happened".
      var last = (data.history || [])[0];
      var failed = last && last.status === 'failed'
        ? '<p class="quota-note"><strong>The last sweep failed.</strong> '
          + esc(last.note) + '</p>'
        : '';
      el.innerHTML = failed +
        '<p>Nothing running. A sweep checks every design on ' + data.ready +
        ' account(s) and pauses Photoshop and uploads until it is done.</p>' +
        (blocked.length
          ? '<p class="quota-note">' + blocked.length + ' account(s) will be '
            + 'SKIPPED because they have no store address: '
            + esc(blocked.join(', ')) + '. Add it below.</p>'
          : '') +
        (data.wall_paths
          ? ''
          : '<p class="quota-note">No mouse paths recorded yet. Scanning is '
            + 'fine without them, but turning designs off and on again may '
            + 'hit the wall and stop. Record some with RECORD_PATHS.bat on '
            + 'the worker machine.</p>') +
        '<p><button class="btn btn-accent" data-action="start" type="button"' +
        (data.ready ? '' : ' disabled') + '>START A SWEEP</button></p>';
      return;
    }

    var c = run.counts || {};
    summary.textContent = 'run #' + run.id + ' · started ' + when(run.started_at);

    var body = '';
    if (run.status === 'scanning') {
      body =
        '<p><strong>Scanning.</strong> ' + c.checked + ' of ' + c.total +
        ' designs checked' + (c.missing ? ', ' + c.missing + ' missing so far' : '') +
        '.</p>' +
        '<p class="muted">Everything else on the worker machine is paused. ' +
        'This can take hours — the page updates itself.</p>' +
        // Stop early but KEEP the results. Distinct from STOP THIS RUN,
        // which throws them away — and it is the one you want when testing
        // the later stages, or when you have simply seen enough.
        (c.checked
          ? '<p><button class="btn btn-accent" data-action="stop-scanning" ' +
            'type="button">STOP SCANNING — REVIEW THE ' + c.checked +
            ' CHECKED SO FAR</button></p>'
          : '');
    } else if (run.status === 'reviewing') {
      body =
        '<p><strong>' + c.missing + ' design(s) are missing from search</strong>' +
        ' out of ' + c.checked + ' checked.</p>' +
        (c.errors ? '<p class="muted">' + c.errors + ' could not be read and '
                    + 'will be left alone.</p>' : '') +
        '<p class="muted">Next step turns those ' + c.missing + ' off. Nothing ' +
        'happens until you press it.</p>' +
        '<p><button class="btn btn-accent" data-action="deactivate" type="button">' +
        'DEACTIVATE ' + c.missing + ' DESIGN(S)</button></p>';
    } else if (run.status === 'deactivating') {
      body = '<p><strong>Deactivating.</strong> ' + c.deactivated + ' of ' +
             c.missing + ' done.</p>';
    } else if (run.status === 'confirming') {
      body =
        '<p><strong>' + c.deactivated + ' design(s) are now off.</strong>' +
        (c.action_errors ? ' ' + c.action_errors + ' refused — listed below.' : '') +
        '</p>' +
        // Said plainly because this is the dangerous pause: stopping here
        // leaves live listings switched off, which costs money quietly.
        '<p class="quota-note">These are OFF right now. They only come back ' +
        'when you press the button below.</p>' +
        '<p><button class="btn btn-accent" data-action="reactivate" type="button">' +
        'REACTIVATE ' + c.deactivated + ' DESIGN(S)</button></p>';
    } else if (run.status === 'reactivating') {
      body = '<p><strong>Reactivating.</strong> ' + c.reactivated + ' of ' +
             c.deactivated + ' back on.</p>';
    }

    el.innerHTML = body +
      '<p class="muted mono">' + esc(run.note || '') + '</p>' +
      '<p><button class="btn btn-ghost btn-tiny" data-action="abandon" ' +
      'type="button">STOP THIS RUN</button> ' +
      '<span class="muted">— releases Photoshop and uploads straight away.</span></p>';
  }

  // ── Designs ──────────────────────────────────────────────────────────
  function renderDesigns(run) {
    var panel = q('[data-designs-panel]');
    if (!run || !(run.designs || []).length) {
      panel.hidden = !run;
      if (run) {
        q('[data-designs-list]').innerHTML =
          '<p class="muted">Every design checked so far is visible — nothing '
          + 'to do.</p>';
        q('[data-designs-count]').textContent = run.visible_sample + ' visible';
      }
      return;
    }
    panel.hidden = false;

    var rows = run.designs.filter(function (d) {
      return state.onlyMissing ? d.status === 'missing' : true;
    });
    q('[data-designs-count]').textContent =
      rows.length + ' shown · ' + run.visible_sample + ' visible';

    q('[data-designs-list]').innerHTML =
      '<table class="data-table"><thead><tr>' +
      '<th>DESIGN</th><th>ACCOUNT</th><th>STATE</th><th></th>' +
      '</tr></thead><tbody>' +
      rows.map(function (d) {
        var note = d.reactivated ? 'back on'
                 : d.deactivated ? 'turned off'
                 : d.action_error ? esc(d.action_error)
                 : d.error ? esc(d.error) : '';
        return '<tr><td>' +
          (d.url ? '<a href="' + esc(d.url) + '" target="_blank" rel="noopener">'
                   + esc(d.title) + '</a>' : esc(d.title)) +
          ' <span class="muted mono">#' + esc(d.design_id) + '</span></td>' +
          '<td>' + esc(d.account) + '</td>' +
          '<td' + (d.status === 'missing' ? ' class="danger"' : '') + '>' +
          esc(d.status) + '</td>' +
          '<td class="muted">' + note + '</td></tr>';
      }).join('') +
      '</tbody></table>';
  }

  // ── Accounts ─────────────────────────────────────────────────────────
  function renderAccounts(list) {
    q('[data-accounts-summary]').textContent =
      list.filter(function (a) { return a.ready; }).length +
      ' of ' + list.length + ' ready';

    q('[data-accounts-list]').innerHTML = list.length
      ? '<table class="data-table"><thead><tr><th>ACCOUNT</th>' +
        '<th>STORE ADDRESS</th><th></th></tr></thead><tbody>' +
        list.map(function (a) {
          return '<tr><td>' + esc(a.name) +
            (a.ready ? '' : ' <span class="danger">· no address</span>') +
            '</td><td><input type="text" style="width:100%" ' +
            'data-store-url="' + a.id + '" value="' + esc(a.store_url) + '" ' +
            'placeholder="https://www.teepublic.com/user/yourname"></td>' +
            '<td><button class="btn btn-ghost btn-tiny" ' +
            'data-save-url="' + a.id + '" type="button">SAVE</button></td></tr>';
        }).join('') + '</tbody></table>'
      : '<p class="muted">No TeePublic accounts yet.</p>';
  }

  function renderHistory(rows) {
    q('[data-history-list]').innerHTML = rows.length
      ? '<table class="data-table"><tbody>' + rows.map(function (r) {
          return '<tr><td>#' + r.id + '</td><td>' + esc(r.status) + '</td>' +
            '<td class="muted mono">' + when(r.started_at) + '</td>' +
            '<td class="muted">' + esc(r.note) + '</td></tr>';
        }).join('') + '</tbody></table>'
      : '<p class="muted">None yet.</p>';
  }

  // ── Loading ──────────────────────────────────────────────────────────
  async function reload() {
    try {
      var data = await getJSON(API + '/overview');
      renderRun(data);
      renderDesigns(data.run);
      renderAccounts(data.accounts || []);
      renderHistory(data.history || []);

      // Poll only while something is actually moving. A run waiting at a
      // gate changes only when you press a button, so there is nothing to
      // refresh for.
      var moving = data.run && ['scanning', 'deactivating', 'reactivating']
        .indexOf(data.run.status) >= 0;
      clearTimeout(state.timer);
      if (moving) state.timer = setTimeout(reload, 5000);
    } catch (e) {
      q('[data-run-panel]').innerHTML =
        '<p class="muted">Could not load: ' + esc(e.message) + '</p>';
    }
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-action], [data-save-url]');
    if (!t) return;

    if (t.dataset.saveUrl) {
      var input = q('[data-store-url="' + t.dataset.saveUrl + '"]');
      postJSON(API + '/account-url', {
        id: Number(t.dataset.saveUrl), store_url: input.value.trim()
      }).then(reload).catch(function (err) { alert(err.message); });
      return;
    }

    var action = t.dataset.action;
    if (action === 'start') {
      if (!confirm('Start a sweep? Photoshop and uploads pause until it is done.')) return;
      postJSON(API + '/start').then(reload)
        .catch(function (err) { alert(err.message); });
    } else if (action === 'stop-scanning') {
      if (!confirm('Stop scanning and review what has been found so far?\n\n'
                   + 'Everything already checked is kept. The worker stops '
                   + 'within one design.')) return;
      postJSON(API + '/stop-scanning').then(function (r) {
        alert('Stopped after ' + r.checked + ' design(s) — ' + r.missing
              + ' missing.');
        reload();
      }).catch(function (err) { alert(err.message); });
    } else if (action === 'deactivate' || action === 'reactivate') {
      var word = action === 'deactivate'
        ? 'Turn the missing designs OFF? They stay off until you reactivate them.'
        : 'Turn them back ON?';
      if (!confirm(word)) return;
      postJSON(API + '/advance', { stage: action }).then(reload)
        .catch(function (err) { alert(err.message); });
    } else if (action === 'abandon') {
      if (!confirm('Stop this run? Anything already deactivated stays off.')) return;
      postJSON(API + '/abandon', {}).then(function (r) {
        if (r.left_deactivated) {
          alert(r.left_deactivated + ' design(s) were left switched off. '
                + 'Reactivate them from TeePublic, or run another sweep.');
        }
        reload();
      }).catch(function (err) { alert(err.message); });
    }
  });

  document.addEventListener('change', function (e) {
    if (e.target.matches('[data-only-missing]')) {
      state.onlyMissing = e.target.checked;
      reload();
    }
  });

  reload();
})();
