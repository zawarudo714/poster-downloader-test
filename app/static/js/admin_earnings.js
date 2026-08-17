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

  function renderNextPayout(p) {
    var el = q('[data-next-payout]');
    el.innerHTML =
      '<div style="font-size:26px; font-weight:600">probably ' + money(p.amount) + '</div>' +
      '<p class="muted">' + esc(p.caveat) + '</p>' +
      '<p class="muted mono">' + p.sales + ' sale(s) credited since ' +
      (p.since ? 'your last payout on ' + when(p.since) : 'you started') +
      (p.last_payout ? ' (that one was ' + money(p.last_payout) + ')' : '') + '.' +
      (p.unsettled
        ? ' ' + p.unsettled + ' of them are recent enough that they may still change.'
        : '') +
      '</p>';
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
      renderNextPayout(data.next_payout);
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

  function reloadAll() {
    loadOverview();
    loadDesigns();
    loadEntries();
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
    if (t.dataset.action === 'reload-unmatched') { loadUnmatched(); return; }
    if (t.dataset.action === 'read-now') { readNow(t, null); return; }
    if (t.dataset.readAccount) { readNow(t, Number(t.dataset.readAccount)); }
  });

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
})();
