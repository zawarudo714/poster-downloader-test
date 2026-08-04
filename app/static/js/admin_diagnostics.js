/* Diagnostics page.
 *
 * Renders whatever /admin/api/diagnostics returns — it does not know the
 * names of the individual checks. Adding a check server-side needs no change
 * here, which is the point: the scan will grow as more niches and
 * marketplaces are added and the front-end shouldn't be a second place to
 * remember to update.
 */
(function () {
  'use strict';

  var runBtn  = document.getElementById('diag-run');
  var results = document.getElementById('diag-results');
  var summary = document.getElementById('diag-summary');
  if (!runBtn) return;

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  runBtn.addEventListener('click', async function () {
    runBtn.disabled = true;
    runBtn.textContent = 'SCANNING…';
    results.innerHTML = '<p class="muted">Walking the workspace and comparing records…</p>';
    summary.hidden = true;

    var data;
    try {
      var r = await fetch('/admin/api/diagnostics');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      data = await r.json();
    } catch (e) {
      results.innerHTML = '<p class="error">Scan failed: ' + esc(e.message) + '</p>';
      runBtn.disabled = false;
      runBtn.textContent = 'RUN SCAN';
      return;
    }

    var t = data.totals || {};
    summary.hidden = false;
    summary.innerHTML =
      '<div class="diag-summary">' +
        '<span class="diag-chip diag-error">' + (t.errors || 0) + ' needing attention</span>' +
        '<span class="diag-chip diag-warn">' + (t.warnings || 0) + ' worth a look</span>' +
        '<span class="diag-chip diag-info">' + (t.info || 0) + ' informational</span>' +
        '<span class="muted mono">scanned ' + esc((data.generated_at || '').replace('T', ' ')) + ' UTC</span>' +
      '</div>';

    var html = '';
    (data.checks || []).forEach(function (c) {
      var clean = !c.error && c.count === 0;
      html +=
        '<details class="diag-check diag-' + esc(c.severity) + (clean ? ' is-clean' : '') + '"' +
          (clean ? '' : ' open') + '>' +
          '<summary>' +
            '<span class="diag-check-title">' + esc(c.title) + '</span>' +
            '<span class="diag-count mono">' +
              (c.error ? 'could not run' : (clean ? 'clean' : c.count + (c.truncated ? '+' : ''))) +
            '</span>' +
          '</summary>' +
          '<div class="diag-check-body">' +
            '<p class="diag-explain muted">' + esc(c.explain) + '</p>';

      if (c.error) {
        html += '<p class="error mono">' + esc(c.error) + '</p>';
      } else if (clean) {
        html += '<p class="ok">Nothing found.</p>';
      } else {
        html += '<ul class="diag-findings">';
        (c.findings || []).forEach(function (f) {
          html +=
            '<li>' +
              (f.link
                ? '<a href="' + esc(f.link) + '">' + esc(f.what) + '</a>'
                : '<span>' + esc(f.what) + '</span>') +
              (f.detail ? '<div class="diag-detail mono muted">' + esc(f.detail) + '</div>' : '') +
            '</li>';
        });
        html += '</ul>';
        if (c.truncated) {
          html += '<p class="muted">Showing the first ' + (c.findings || []).length +
                  ' of ' + c.count + '.</p>';
        }
      }
      html += '</div></details>';
    });

    results.innerHTML = html;
    runBtn.disabled = false;
    runBtn.textContent = 'RUN SCAN AGAIN';
  });
})();
