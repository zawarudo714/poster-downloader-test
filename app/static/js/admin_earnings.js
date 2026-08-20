/* Earnings page.
 *
 * One filter state, shared by every panel. Change the site, the period or the
 * set of accounts and everything on the page re-reads with the same filter —
 * because a page where the headline says "all sites" while the list below
 * still shows one is worse than no page at all.
 *
 * Nothing here computes money. Every figure is rendered exactly as the server
 * sent it: totals are Decimal arithmetic over stored rows server-side, and
 * doing any of it again in JavaScript floats would put two different answers
 * on one screen.
 */
(function () {
  'use strict';

  var API = '/admin/api/earnings';
  var root = document.querySelector('[data-figures]');
  if (!root) return;

  // ── State ────────────────────────────────────────────────────────────
  var state = {
    marketplace: '',
    days: 30,
    accounts: [],        // empty = every account
    sort: 'amount',
    type: '',
    allAccounts: []
  };

  function q(sel) { return document.querySelector(sel); }
  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function money(v) { return '$' + esc(v); }
  function when(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d) ? esc(iso) : d.toLocaleDateString();
  }

  function params(extra) {
    var p = new URLSearchParams();
    if (state.marketplace) p.set('marketplace', state.marketplace);
    if (state.accounts.length) p.set('accounts', state.accounts.join(','));
    p.set('days', state.days);
    Object.keys(extra || {}).forEach(function (k) { p.set(k, extra[k]); });
    return p.toString();
  }

  async function getJSON(url) {
    var r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
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

  // ── Headline figures ─────────────────────────────────────────────────
  function renderSummary(s) {
    // "+$25 today" is the format asked for: the delta is what tells you
    // something happened, and it is arithmetic over rows rather than a
    // stored number, so it stays correct across a month boundary.
    // Reuses the pipeline funnel's classes rather than inventing new ones,
    // so the two screens stay visually identical for free.
    root.innerHTML =
      '<div class="funnel-grid">' +
        card('EARNED', money(s.earned), s.sales_count + ' sale(s)') +
        card('TODAY', '+' + money(s.today.amount), s.today.sales + ' sale(s)') +
        card('LAST 7 DAYS', '+' + money(s.week.amount), s.week.sales + ' sale(s)') +
        card('REFUNDED', '-' + money(s.refunded), 'taken back') +
        card('PAID OUT', money(s.paid_out), 'already received') +
      '</div>' +
      (s.unmatched
        ? '<p class="muted" style="margin-top:8px">' + s.unmatched +
          ' sale(s) in this period are not tied to a design yet — they still ' +
          'count in the totals above, but not in WHAT SOLD.</p>'
        : '') +
      '<p class="muted" style="margin-top:8px">Figures can revise downward ' +
      'for about ' + s.revision_window_h + ' hours after a sale — a total ' +
      'that drops is a refund or a correction, not a fault.</p>';
  }

  function card(label, value, sub) {
    return '<div class="funnel-cell"><div class="funnel-num">' + value +
           '</div><div class="funnel-label">' + esc(label) + '</div>' +
           '<div class="muted mono" style="font-size:11px">' + esc(sub) +
           '</div></div>';
  }

  function renderNextPayout(p, checks) {
    var el = q('[data-next-payout]');

    // Their figure is the headline BECAUSE it is theirs. Everything else on
    // this page is arithmetic we did; this line is the marketplace stating
    // what it owes. An earlier version showed our estimate instead and read
    // "probably $1,477.21" against a real balance of $298.28.
    var head = p.owed_known
      ? '<div style="font-size:30px; font-weight:600">' + money(p.owed) + '</div>'
        + '<p class="muted">Owed to you right now — FineArtAmerica\'s own figure'
        + (p.accounts_reporting > 1
            ? ', totalled across ' + p.accounts_reporting + ' accounts' : '')
        + '.</p>'
      : '<div style="font-size:22px" class="muted">Not read yet</div>'
        + '<p class="muted">Press READ NOW — the balance comes straight from '
        + 'the marketplace.</p>';

    var since = '<p class="muted mono">' + money(p.credited_since_payout)
      + ' credited from ' + p.sales_since_payout + ' sale(s) since '
      + (p.since ? 'your last payout on ' + when(p.since) : 'you started')
      + (p.last_payout ? ' (that one was ' + money(p.last_payout) + ')' : '') + '.'
      + (p.unsettled
          ? ' ' + p.unsettled + ' are recent enough that they may still change.'
          : '') + '</p>';

    el.innerHTML = head + '<p class="muted">' + esc(p.rule) + '</p>' + since
      + renderReconcile(checks);
  }

  // Sales minus payouts must land on their balance. When it does not, we have
  // missed rows — and every total above is understated by exactly that much.
  function renderReconcile(checks) {
    if (!checks || !checks.length) return '';
    var bad = checks.filter(function (c) { return !c.agrees; });
    if (!bad.length) {
      return '<p class="muted mono">Our figures reconcile with theirs exactly '
        + '(' + checks.length + ' account(s) checked).</p>';
    }
    return bad.map(function (c) {
      return '<div class="quota-note" style="margin-top:8px">'
        + '<strong>' + esc(c.account) + ' does not add up.</strong> '
        + 'We hold ' + c.sales + ' sale(s) and ' + c.payouts + ' payout(s), '
        + 'which come to ' + money(c.ours) + '. They say ' + money(c.theirs)
        + ' — a difference of ' + money(c.difference) + '. '
        + 'That means rows are missing, so the totals above are wrong. '
        + 'Try READ NOW; if it persists, tell Claude.'
        + '</div>';
    }).join('');
  }

  // ── Accounts filter ──────────────────────────────────────────────────
  function renderAccounts(list) {
    state.allAccounts = list;

    var sites = {};
    list.forEach(function (a) { if (a.marketplace) sites[a.marketplace] = 1; });
    var sel = q('[data-filter-marketplace]');
    var current = sel.value;
    sel.innerHTML = '<option value="">All sites</option>' +
      Object.keys(sites).sort().map(function (m) {
        return '<option value="' + esc(m) + '">' + esc(m) + '</option>';
      }).join('');
    sel.value = current;

    var box = q('[data-account-filter]');
    box.innerHTML = list.map(function (a) {
      var checked = state.accounts.indexOf(a.id) >= 0 ? ' checked' : '';
      return '<label class="inline-check" style="margin-right:14px">' +
        '<input type="checkbox" data-account="' + a.id + '"' + checked + '> ' +
        esc(a.name) + ' <span class="muted">(' + esc(a.marketplace || '—') +
        ' · ' + a.sales + ' sales' +
        (a.banned ? ' · banned' : '') +
        (a.readable ? '' : ' · cannot be read') + ')</span>' +
        (a.readable
          ? ' <button type="button" class="btn btn-ghost btn-tiny" ' +
            'data-read-account="' + a.id + '">READ</button>'
          : '') +
        '</label>';
    }).join('') || '<span class="muted">No marketplace accounts yet.</span>';

    q('[data-account-filter-summary]').textContent =
      state.accounts.length
        ? state.accounts.length + ' of ' + list.length + ' accounts'
        : 'all ' + list.length + ' accounts';
  }

  // ── Designs ──────────────────────────────────────────────────────────
  function renderDesigns(rows) {
    var el = q('[data-designs]');
    if (!rows.length) {
      el.innerHTML = '<p class="muted">Nothing sold in this period.</p>';
      return;
    }
    el.innerHTML =
      '<table class="data-table"><thead><tr>' +
      '<th>DESIGN</th><th>SALES</th><th>EARNED</th><th>LAST SOLD</th>' +
      '</tr></thead><tbody>' +
      rows.map(function (r) {
        return '<tr><td>' + esc(r.title) +
          (r.matched ? '' : ' <span class="muted">· unmatched</span>') +
          '</td><td>' + r.sales + '</td><td>' + money(r.amount) +
          '</td><td class="muted mono">' + when(r.last_sold) + '</td></tr>';
      }).join('') +
      '</tbody></table>';
  }

  // ── Unmatched, and matching one ──────────────────────────────────────
  function renderUnmatched(rows) {
    var el = q('[data-unmatched]');
    if (!rows.length) {
      el.innerHTML = '<p class="muted">Every sale is tied to a design.</p>';
      return;
    }
    el.innerHTML = rows.map(function (r, i) {
      return '<div data-match-row="' + i + '" ' +
        'data-name="' + esc(r.artwork_name) + '" ' +
        'data-marketplace="' + esc(r.marketplace) + '" ' +
        'style="padding:8px 0; border-bottom:1px solid rgba(255,255,255,.06)">' +
        '<strong>' + esc(r.artwork_name) + '</strong> ' +
        '<span class="muted">· ' + r.sales + ' sale(s) · ' + esc(r.marketplace) +
        ' · first seen ' + when(r.first_seen) + '</span>' +
        '<div class="filter-row" style="gap:8px; margin-top:6px">' +
        '<input type="text" data-search placeholder="Search your titles…" ' +
        'style="min-width:280px">' +
        '<span class="muted" data-match-status></span>' +
        '</div>' +
        '<div data-results></div>' +
        '</div>';
    }).join('');
  }

  var searchTimer = null;
  document.addEventListener('input', function (e) {
    var input = e.target.closest('[data-search]');
    if (!input) return;
    var row = input.closest('[data-match-row]');
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async function () {
      var term = input.value.trim();
      var out = row.querySelector('[data-results]');
      if (term.length < 2) { out.innerHTML = ''; return; }
      var data = await getJSON(API + '/title-search?q=' + encodeURIComponent(term));
      out.innerHTML = (data.titles || []).map(function (t) {
        return '<button type="button" class="btn btn-ghost btn-tiny" ' +
          'data-pick="' + t.id + '" style="margin:3px 4px 0 0">' +
          esc(t.title) + (t.year ? ' (' + t.year + ')' : '') + '</button>';
      }).join('') || '<span class="muted">No title matches that.</span>';
    }, 250);
  });

  document.addEventListener('click', async function (e) {
    var pick = e.target.closest('[data-pick]');
    if (!pick) return;
    var row = pick.closest('[data-match-row]');
    var status = row.querySelector('[data-match-status]');
    status.textContent = 'Saving…';
    try {
      var res = await postJSON(API + '/match', {
        marketplace: row.dataset.marketplace,
        artwork_name: row.dataset.name,
        master_title_id: Number(pick.dataset.pick)
      });
      status.textContent = 'Matched — ' + res.resolved + ' sale(s) resolved.';
      row.querySelector('[data-results]').innerHTML = '';
      loadUnmatched();
      loadDesigns();
    } catch (err) {
      status.textContent = 'Failed: ' + err.message;
    }
  });

  // ── Raw ledger ───────────────────────────────────────────────────────
  function renderEntries(rows) {
    var el = q('[data-entries]');
    if (!rows.length) {
      el.innerHTML = '<p class="muted">Nothing in this period.</p>';
      return;
    }
    el.innerHTML =
      '<table class="data-table"><thead><tr>' +
      '<th>WHEN</th><th>ACCOUNT</th><th>TYPE</th><th>WHAT</th>' +
      '<th>IN</th><th>OUT</th></tr></thead><tbody>' +
      rows.map(function (r) {
        return '<tr><td class="mono">' + when(r.occurred_at) +
          '</td><td>' + esc(r.account) +
          '</td><td>' + esc(r.type) +
          '</td><td>' + esc(r.artwork_name || r.product || '—') +
          (r.website ? ' <span class="muted">· ' + esc(r.website) + '</span>' : '') +
          (r.quantity > 1 ? ' <span class="muted">× ' + r.quantity + '</span>' : '') +
          (r.type === 'sale' && !r.matched
            ? ' <span class="muted">· unmatched</span>' : '') +
          '</td><td>' + (r.credit !== '0' ? money(r.credit) : '') +
          '</td><td>' + (r.debit !== '0' ? money(r.debit) : '') +
          '</td></tr>';
      }).join('') +
      '</tbody></table>';
  }

  // ── Loading ──────────────────────────────────────────────────────────
  async function loadOverview() {
    try {
      var data = await getJSON(API + '/overview?' + params());
      renderSummary(data.summary);
      renderNextPayout(data.next_payout, data.reconcile);
      renderAccounts(data.accounts);
    } catch (e) {
      root.innerHTML = '<p class="muted">Could not load earnings: ' +
                       esc(e.message) + '</p>';
    }
  }
  async function loadDesigns() {
    try {
      var data = await getJSON(API + '/designs?' + params({ sort: state.sort }));
      renderDesigns(data.designs || []);
    } catch (e) {
      q('[data-designs]').innerHTML = '<p class="muted">Could not load: ' +
                                      esc(e.message) + '</p>';
    }
  }
  async function loadUnmatched() {
    try {
      var data = await getJSON(API + '/unmatched');
      renderUnmatched(data.unmatched || []);
    } catch (e) {
      q('[data-unmatched]').innerHTML = '<p class="muted">Could not load: ' +
                                        esc(e.message) + '</p>';
    }
  }
  async function loadEntries() {
    var el = q('[data-entries]');
    if (el.hidden) return;            // never fetch what nobody is looking at
    try {
      var extra = state.type ? { entry_type: state.type } : {};
      var data = await getJSON(API + '/entries?' + params(extra));
      renderEntries(data.entries || []);
    } catch (e) {
      el.innerHTML = '<p class="muted">Could not load: ' + esc(e.message) + '</p>';
    }
  }

  // The nightly decision, spelled out. Refreshed on a timer so you can watch
  // it flip at the quiet time rather than reloading and hoping.
  async function loadSchedule() {
    var el = q('[data-schedule-line]');
    try {
      var d = await getJSON(API + '/schedule');
      var s = d.quiet || {};

      // Only refill a box you are not currently typing into. This line
      // refreshes every 3 seconds, and overwriting a half-typed time would
      // make the field unusable.
      [['[data-quiet-from]', d.quiet_from], ['[data-run-at]', d.run_at]]
        .forEach(function (pair) {
          var el = q(pair[0]);
          if (el && document.activeElement !== el) el.value = pair[1] || '';
        });

      if (!s.enabled) {
        el.textContent = 'Quiet time off · checks run at ' + esc(d.run_at || '—')
          + ' · new work: ALLOWED';
        return;
      }
      el.textContent =
        'Now ' + s.now +
        ' · quiet from ' + s.starts_at +
        " · tonight's check: " + (s.done_today ? 'done' : 'not done yet') +
        ' · new work: ' + (s.blocking ? 'BLOCKED' : 'ALLOWED');
    } catch (e) {
      el.textContent = 'Could not read the schedule: ' + e.message;
    }
  }

  function reloadAll() {
    loadOverview();
    loadDesigns();
    loadEntries();
    loadSchedule();
  }

  // ── Events ───────────────────────────────────────────────────────────
  q('[data-filter-marketplace]').addEventListener('change', function () {
    state.marketplace = this.value;
    reloadAll();
  });
  q('[data-filter-days]').addEventListener('change', function () {
    state.days = Number(this.value);
    reloadAll();
  });
  q('[data-filter-sort]').addEventListener('change', function () {
    state.sort = this.value;
    loadDesigns();
  });
  q('[data-filter-type]').addEventListener('change', function () {
    state.type = this.value;
    loadEntries();
  });

  document.addEventListener('change', function (e) {
    var box = e.target.closest('[data-account]');
    if (!box) return;
    var id = Number(box.dataset.account);
    var at = state.accounts.indexOf(id);
    if (box.checked && at < 0) state.accounts.push(id);
    if (!box.checked && at >= 0) state.accounts.splice(at, 1);
    reloadAll();
  });

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-action], [data-read-account]');
    if (!t) return;

    if (t.dataset.action === 'toggle-accounts') {
      var box = q('[data-account-filter]');
      box.hidden = !box.hidden;
      return;
    }
    if (t.dataset.action === 'toggle-entries') {
      var el = q('[data-entries]');
      el.hidden = !el.hidden;
      t.textContent = el.hidden ? 'SHOW' : 'HIDE';
      loadEntries();
      return;
    }
    if (t.dataset.action === 'save-schedule') {
      var status = q('[data-schedule-status]');
      status.textContent = 'Saving…';
      // Written through the same endpoint the Pipeline settings use, so the
      // key is validated in one place and there is still only one stored
      // value behind both screens.
      postJSON('/admin/pipeline/api/settings', {
        scope: 'global',
        settings: {
          earnings_quiet_from: q('[data-quiet-from]').value.trim(),
          earnings_run_at: q('[data-run-at]').value.trim()
        }
      }).then(function () {
        status.textContent = 'Saved.';
        loadSchedule();
      }).catch(function (e) { status.textContent = 'Failed: ' + e.message; });
      return;
    }
    if (t.dataset.action === 'rearm') {
      t.disabled = true;
      postJSON(API + '/rearm', {})
        .then(function () { loadSchedule(); })
        .catch(function (e) { alert('Failed: ' + e.message); })
        .then(function () { t.disabled = false; });
      return;
    }
    if (t.dataset.action === 'toggle-add') {
      var box = q('[data-add-account]');
      box.hidden = !box.hidden;
      return;
    }
    if (t.dataset.action === 'create-account') { createAccount(t); return; }
    if (t.dataset.action === 'reload-unmatched') { loadUnmatched(); return; }
    if (t.dataset.action === 'read-now') { readNow(t, null); return; }
    if (t.dataset.readAccount) { readNow(t, Number(t.dataset.readAccount)); }
  });

  // Creates an account attached to NO project — nothing will ever be
  // uploaded to it. Posts to the same endpoint the Upload tab uses, with
  // attach_to_project false, so there is one way to create an account rather
  // than two that can drift apart.
  async function createAccount(button) {
    var status = q('[data-add-status]');
    var body = {
      name: q('[data-new-name]').value.trim(),
      target_site: q('[data-new-site]').value.trim().toLowerCase(),
      email: q('[data-new-email]').value.trim(),
      password: q('[data-new-password]').value,
      attach_to_project: false
    };
    if (!body.name || !body.email || !body.password || !body.target_site) {
      status.textContent = 'Name, marketplace, email and password are all needed.';
      return;
    }
    button.disabled = true;
    status.textContent = 'Saving…';
    try {
      await postJSON('/admin/pipeline/api/accounts', body);
      status.textContent = 'Added.';
      q('[data-new-name]').value = '';
      q('[data-new-email]').value = '';
      q('[data-new-password]').value = '';
      loadOverview();
    } catch (e) {
      status.textContent = 'Failed: ' + e.message;
    } finally {
      button.disabled = false;
    }
  }

  async function readNow(button, accountId) {
    var panel = q('[data-read-panel]');
    var logEl = q('[data-read-log]');
    var label = button.textContent;
    button.disabled = true;
    button.textContent = 'READING…';
    panel.hidden = false;
    logEl.textContent = 'Signing in and reading pages…';

    try {
      var res = await postJSON(API + '/read',
                               accountId ? { account_id: accountId } : {});
      var lines = (res.log || []).slice();
      if (res.error) lines.push('FAILED — ' + res.error);
      if (res.result && res.result.errors && res.result.errors.length) {
        lines = lines.concat(res.result.errors.map(function (m) {
          return 'FAILED — ' + m;
        }));
      }
      logEl.textContent = lines.join('\n') || 'Nothing new.';
      reloadAll();
      loadUnmatched();
    } catch (e) {
      logEl.textContent = 'Failed: ' + e.message;
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  reloadAll();
  loadUnmatched();

  // Refreshed on its own so the quiet time can be watched flipping — 3s to
  // match the pipeline funnel, and skipped entirely while the tab is hidden
  // so a page left open overnight is not polling for nothing.
  setInterval(function () {
    if (!document.hidden) loadSchedule();
  }, 3000);
})();
