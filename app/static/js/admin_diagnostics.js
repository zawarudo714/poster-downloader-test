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
  var project = document.getElementById('diag-project');
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
      var pid = project ? (parseInt(project.value, 10) || 0) : 0;
      var r = await fetch('/admin/api/diagnostics?project_id=' + pid);
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
        '<span class="muted mono">' + esc((data.project || {}).name || 'all projects') +
          ' · scanned ' + esc((data.generated_at || '').replace('T', ' ')) + ' UTC</span>' +
      '</div>';

    var html = '';
    (data.checks || []).forEach(function (c) {
      // "Not applicable here" is not the same as "checked, all clear", and
      // showing both as a green tick would be quietly misleading.
      var skipped = !!c.skipped;
      var clean = !c.error && !skipped && c.count === 0;
      html +=
        '<details class="diag-check diag-' + esc(c.severity) + (clean ? ' is-clean' : '') + '"' +
          (clean ? '' : ' open') + '>' +
          '<summary>' +
            '<span class="diag-check-title">' + esc(c.title) + '</span>' +
            '<span class="diag-count mono">' +
              (c.error ? 'could not run'
                : skipped ? 'n/a here'
                : (clean ? 'clean' : c.count + (c.truncated ? '+' : ''))) +
            '</span>' +
          '</summary>' +
          '<div class="diag-check-body">' +
            '<p class="diag-explain muted">' + esc(c.explain) + '</p>';

      if (c.error) {
        html += '<p class="error mono">' + esc(c.error) + '</p>';
      } else if (skipped) {
        html += '<p class="muted">Not checked: ' + esc(c.skipped) + '</p>';
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
              (f.project ? '<span class="diag-project mono">' + esc(f.project) + '</span>' : '') +
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


/* ═══════════════════════════════════════════════════════════════════════════
   FAILURE EVIDENCE
   Screenshots and page dumps the worker machine captured when something went
   wrong. Listed straight from the folder rather than looked up through an
   upload row, so evidence from a job with no project — an earnings read, say
   — is reachable too.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var list = document.getElementById('art-list');
  var reload = document.getElementById('art-reload');
  if (!list) return;

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function when(iso) {
    var d = new Date(iso + 'Z');
    return isNaN(d) ? esc(iso) : d.toLocaleString();
  }
  function url(path) {
    return '/admin/pipeline/api/artifact?path=' + encodeURIComponent(path);
  }

  async function load() {
    list.innerHTML = '<div class="muted">Loading…</div>';
    var data;
    try {
      var r = await fetch('/admin/pipeline/api/artifacts', { credentials: 'same-origin' });
      data = await r.json();
    } catch (e) {
      list.innerHTML = '<div class="muted">Could not load: ' + esc(e.message) + '</div>';
      return;
    }

    var rows = (data.artifacts || []);
    if (!rows.length) {
      list.innerHTML = '<p class="muted">Nothing captured yet — which is the '
        + 'good outcome.</p>';
      return;
    }

    list.innerHTML = rows.map(function (a) {
      var isImage = a.kind === 'screenshot';
      return '<div style="display:inline-block; vertical-align:top; margin:0 12px 14px 0; max-width:260px">'
        + (isImage
            // Thumbnail, and the image itself opens full size in a new tab.
            ? '<a href="' + url(a.path) + '" target="_blank" rel="noopener">'
              + '<img src="' + url(a.path) + '" alt="' + esc(a.name) + '" '
              + 'style="max-width:260px; border:1px solid rgba(255,255,255,.12); '
              + 'border-radius:4px; display:block"></a>'
            : '<a class="btn btn-ghost btn-tiny" href="' + url(a.path) + '" '
              + 'target="_blank" rel="noopener">OPEN PAGE SOURCE</a>')
        + '<div class="muted mono" style="font-size:11px; margin-top:4px; word-break:break-all">'
        + esc(a.name) + '</div>'
        + '<div class="muted mono" style="font-size:11px">' + when(a.when)
        + ' · ' + Math.round(a.bytes / 1024) + ' KB</div>'
        + '</div>';
    }).join('');
  }

  if (reload) reload.addEventListener('click', load);
  load();
})();
