/* Pipeline control centre.

   Structure:
     - One module, sections toggled client-side. Each section lazy-loads its
       data on first open so the page is fast even with a big backlog.
     - SETTINGS_GROUPS below is the ONLY place field metadata lives. The
       settings forms are generated from it against whatever the API returns,
       so adding a knob to pipeline.DEFAULTS + a line here is the whole job.
       Never hand-write a settings input in the template.
     - The Live Console polls one job by id. Every long-running action (batch
       run or single-image test) returns a job id and opens the console, so
       there's one place to watch things happen.
*/

(function () {
  const root = document.querySelector('[data-pipeline]');
  if (!root) return;

  const API = '/admin/pipeline/api';

  // ── State ────────────────────────────────────────────────────────────────
  let projectId   = null;      // null = server default project
  let overview    = null;
  let settings    = null;      // {settings, defaults, overrides}
  let accounts    = [];
  let failures    = [];
  let consoleJob  = null;      // job id currently tailed
  let consoleTimer = null;
  let overviewTimer = null;
  let historyChart = null;
  const loaded = {};           // section → true once fetched

  // Field metadata for the generated settings forms. `group` maps to the
  // data-settings-group containers in the template.
  const SETTINGS_GROUPS = {
    processing: [
      ['work_width',          'number', 'Working width (px)',      'Images are normalised to this width before the effect runs. Upscaled with Preserve Details + Smart Sharpen, downscaled with Bicubic Sharper.'],
      ['output_width',        'number', 'Final width (px)',        'Delivery width after the effect. Height scales automatically.'],
      ['jpeg_quality',        'number', 'JPEG quality (1-12)',     'Photoshop scale, not percent. 10 is the current production value.'],
      ['sharpen_amount',      'number', 'Sharpen amount (%)',      'Smart Sharpen amount applied only when upscaling.'],
      ['sharpen_radius',      'number', 'Sharpen radius (px)',     'Smart Sharpen radius.'],
      ['sharpen_noise',       'number', 'Sharpen noise reduction', 'Smart Sharpen noise reduction percentage.'],
      ['output_suffix',       'text',   'Output filename suffix',  'Appended before .jpg — e.g. "_Painted" turns "Title 1.jpg" into "Title 1_Painted.jpg".'],
      ['fx_script_path',      'text',   'FX plugin script path',   'Absolute path to the Real Paint FX .jsx ON THE WORKER NODE.'],
      ['photoshop_exe',       'text',   'Photoshop executable',    'Absolute path to Photoshop.exe on the worker node.'],
      ['process_timeout_s',   'number', 'Per-image timeout (s)',   'A single image taking longer than this is treated as hung: Photoshop is killed and the image retried.'],
      ['photoshop_warmup_s',  'number', 'Photoshop warmup (s)',    'How long to wait for a cold Photoshop to become ready before sending it the first script. Paid once per batch, not per image.'],
      ['photoshop_restart_every','number','Restart Photoshop every N images','Photoshop stays open between images for speed, but degrades over a long run — memory climbs and images get slower. A periodic restart costs ~30s and prevents a slide into timeouts. 0 disables it.'],
      ['process_batch_size',  'number', 'Batch size',              'Images claimed per Photoshop run.'],
      ['process_max_attempts','number', 'Max attempts',            'Retries before an image is parked for review.'],
    ],
    storage: [
      ['storage_root',   'text', 'Storage root (on node)', 'Where processed images are archived — typically a mounted storage box. Database paths are relative to this, so remounting elsewhere only needs a change here.'],
      ['storage_layout', 'text', 'Path layout',            'Variables: {date} {title_folder} {filename} {project} {username} {external_id}. Default mirrors the worker folder structure.'],
    ],
    // ── GPT projects ──────────────────────────────────────────────────
    // Only rendered when the project declares processor = 'gpt'; the
    // template omits the containers entirely otherwise, and
    // renderSettingsGroup() skips a group whose container isn't present.
    gpt: [
      ['openai_api_key',  'password', 'OpenAI API key',   'Used for image generation. Stored encrypted.'],
      ['openai_admin_key','password', 'OpenAI admin key', 'Separate, higher-privilege credential used ONLY by the nightly cost reconciliation. Image generation never uses it. Leave blank to rely on our own metering.'],
      ['openai_model',    'text',   'Model',    'gpt-image-2 unless you have a reason.'],
      ['openai_size',     'select', 'Size',     'auto lets the model choose a ratio to suit the photo. Larger sizes cost proportionally more.', ['auto', '1024x1024', '1024x1536', '1536x1024']],
      ['openai_quality',  'select', 'Quality',  'low is roughly a fifth the price of medium and is upscaled afterwards anyway.', ['auto', 'low', 'medium', 'high']],
    ],
    upscale: [
      ['upscale_width_px',  'number', 'Output width (px)', 'The processed image is resized to this width; height scales in proportion, so 1000x2000 becomes 4000x8000. Lanczos resampling.'],
      ['upscale_sharpen',   'number', 'Sharpening (0-100)','Applied after the upscale. 0 is off. Raise it slowly and judge on a real print — sharpening artefacts are baked in and the review gate is your only chance to catch them.'],
      ['upscale_jpeg_quality','number','JPEG quality (1-100)','Quality of the saved print file. 92 is visually lossless for photographic work; higher mostly buys file size.'],
    ],
    spend: [
      ['spend_cap_usd_month','number','Monthly cap (USD)', '0 disables the cap. Counted from the token usage each API call reports.'],
      ['spend_cap_action',   'select','When the cap is hit','warn posts a dashboard alert. pause also stops dispatching new work.', ['warn', 'pause']],
      ['brave_api_key_free', 'password','Brave key (free)',  'Used for normal searches. 1 request/second, 2,000 a month.'],
      ['brave_api_key_paid', 'password','Brave key (paid)',  'Used for deep searches, which fire two queries at once and would trip the free key\'s 1/second limit. Also the fallback when the free quota runs out.'],
      ['brave_daily_query_cap','number','Daily query cap',   '0 is off. A safety net against a bug looping, not a budget — Brave costs about half a cent a query.'],
    ],
    upload: [
      ['upload_batch_size',   'number', 'Batch size',        'Images per upload run, capped by the account\'s remaining daily quota.'],
      ['upload_max_attempts', 'number', 'Max attempts',      'Retries before an upload is parked for review.'],
      ['upload_sequential',   'bool',   'Sequential uploads','Strongly recommended. One tab at a time. The old parallel-tab approach lost 20-30% of every batch to stale tabs, memory pressure and session timeouts.'],
      ['schedule_mode',       'select', 'Schedule',          'Continuous runs whenever there is work. Daily waits for the start hour.', ['continuous', 'daily']],
      ['daily_start_hour',    'number', 'Daily start hour',  'Node local time, 0-23. Only used in daily mode.'],
      ['poll_interval_s',     'number', 'Poll interval (s)', 'How often the node asks for work while there is work to do.'],
      ['poll_interval_idle_s','number', 'Idle poll interval (s)', 'How often the node asks for work once it has been idle for a while. Stops an overnight idle box making thousands of pointless requests and filling its console. Set it equal to the normal poll interval to switch the back-off off.'],
      ['poll_idle_after_min', 'number', 'Back off after (min)', 'How long the node must find nothing before it slows its polling down. It snaps back to the normal interval the moment work appears, so this never delays a batch.'],
      ['node_log_retention_days','number','Node log retention (days)', "How long the worker node keeps its own local log files on the Windows box. These are the only record when the node cannot reach this server. 0 keeps them forever."],
      ['claim_timeout_min',   'number', 'Claim timeout (min)','Work claimed by a node that stops reporting is automatically returned to the queue after this long.'],
    ],
    templates: [
      ['title_template',      'text',     'Listing title',      'Variables: {title} {year} {letter} {index} {content_type} {external_id}. {letter} is the per-image A/B/C suffix. Output is ASCII-folded automatically.'],
      ['keywords_static',     'text',     'Static keywords',    'Appended to whatever the marketplace pre-fills. Keep the leading comma.'],
      ['description_source',  'select',   'Description source', '"master" uses the title description from the master list. "template" renders the field below.', ['master', 'template']],
      ['description_template','textarea', 'Description template','Variables: {title} {year} {description} {content_type}. Only used when source is "template".'],
    ],
  };

  // ── Helpers ──────────────────────────────────────────────────────────────
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  function withProject(url) {
    if (!projectId) return url;
    return url + (url.includes('?') ? '&' : '?') + 'project_id=' + projectId;
  }

  async function getJSON(url) {
    const sep = url.includes('?') ? '&' : '?';
    const r = await fetch(url + sep + '_t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) {
      let msg = r.status + ' ' + r.statusText;
      try { const d = await r.json(); if (d.detail) msg = d.detail; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      cache: 'no-store',
    });
    let data = null;
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((data && data.detail) || (r.status + ' ' + r.statusText));
    return data;
  }

  let toastEl = null;
  function toast(msg, kind) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.id = 'app-toast';
      document.body.appendChild(toastEl);
    }
    toastEl.className = 'toast toast-' + (kind || 'ok');
    toastEl.textContent = msg;
    toastEl.classList.add('toast-shown');
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(() => toastEl.classList.remove('toast-shown'), 4500);
  }

  function setStatus(el, msg, kind) {
    if (!el) return;
    el.textContent = msg || '';
    el.style.color = kind === 'error' ? 'var(--error)'
                   : kind === 'ok'    ? 'var(--success)' : '';
  }

  const q  = (sel) => root.querySelector(sel);
  const qa = (sel) => Array.from(root.querySelectorAll(sel));

  // ── Section switching ────────────────────────────────────────────────────
  const LOADERS = {
    overview:   loadOverview,
    greenlight: loadGreenlight,
    processing: loadSettings,
    upload:     loadUploadSection,
    test:       loadTestSection,
    failures:   loadFailures,
    nodes:      loadOverview,
  };

  // Keep the sticky sub-nav pinned directly under the main header. The
  // header's height changes when the admin nav wraps, so measure rather than
  // assume.
  function syncStickyOffset() {
    const bar = document.querySelector('.topbar');
    if (!bar) return;
    document.documentElement.style.setProperty(
      '--pipe-tabs-top', bar.offsetHeight + 'px');
  }
  syncStickyOffset();
  window.addEventListener('resize', syncStickyOffset);

  function showSection(name, opts) {
    const silent = opts && opts.silent;

    qa('.pipe-tab').forEach((t) =>
      t.classList.toggle('active', t.dataset.section === name));
    qa('[data-section-panel]').forEach((p) =>
      p.hidden = p.dataset.sectionPanel !== name);

    // Put the URL in charge of which section is open: back/forward work,
    // refreshing keeps your place, and a section can be bookmarked or shared.
    if (!silent && location.hash !== '#' + name) {
      history.pushState({ section: name }, '', '#' + name);
    }

    // Land at the top of the new section rather than wherever you happened to
    // be scrolled to in the previous one.
    window.scrollTo({ top: 0, behavior: 'auto' });

    // Load once per section, then only on explicit action — switching tabs
    // stays instant. `loaded` is cleared again if the loader throws, so a
    // transient failure doesn't leave a section permanently blank.
    if (!loaded[name] && LOADERS[name]) {
      loaded[name] = true;
      Promise.resolve(LOADERS[name]()).catch((e) => {
        loaded[name] = false;
        toast(`Could not load ${name}: ${e.message}`, 'error');
      });
    }
    try { sessionStorage.setItem('pipe-section', name); } catch (e) {}
  }

  qa('.pipe-tab').forEach((tab) => {
    tab.addEventListener('click', () => showSection(tab.dataset.section));
  });

  // ═══════════════════════════════════════════════════════════════════════
  //  OVERVIEW
  // ═══════════════════════════════════════════════════════════════════════

  const FUNNEL_STAGES = [
    ['awaiting_greenlight', 'Awaiting greenlight', 'warn'],
    ['greenlit',            'Greenlit',            ''],
    ['processing',          'Processing',          ''],
    ['processed',           'Processed',           ''],
    ['uploading',           'Uploading',           ''],
    ['uploaded',            'Uploaded',            'ok'],
    ['failed_processing',   'Failed processing',   'error'],
    ['failed_upload',       'Failed upload',       'error'],
  ];

  async function loadOverview() {
    try {
      overview = await getJSON(withProject(API + '/overview'));
    } catch (e) {
      q('[data-funnel]').innerHTML =
        '<div class="muted">Could not load overview: ' + esc(e.message) + '</div>';
      return;
    }
    projectId = overview.project.id;
    renderFunnel();
    renderInFlight();
    renderAccountQuotas();
    renderJobs();
    renderNodes();
    renderHistoryChart();
    renderBadges();

    const modeSel = q('[data-greenlight-mode]');
    if (modeSel) modeSel.value = overview.greenlight_mode;
  }

  function renderBadges() {
    const glBadge = q('[data-badge="greenlight"]');
    const flBadge = q('[data-badge="failures"]');
    const glCount = (overview.funnel.awaiting_greenlight || 0);
    const flCount = (overview.failures.processing || 0) + (overview.failures.upload || 0);
    if (glBadge) { glBadge.textContent = glCount; glBadge.hidden = glCount === 0; }
    if (flBadge) { flBadge.textContent = flCount; flBadge.hidden = flCount === 0; }
  }

  function renderFunnel() {
    const el = q('[data-funnel]');
    const f = overview.funnel;
    el.innerHTML = FUNNEL_STAGES.map(([key, label, tone]) => `
      <button type="button" class="funnel-cell ${tone ? 'funnel-' + tone : ''}"
              data-funnel-stage="${key}" title="Click to list these titles">
        <span class="funnel-num">${(f[key] || 0).toLocaleString()}</span>
        <span class="funnel-label">${esc(label)}</span>
      </button>
    `).join('');

    el.querySelectorAll('[data-funnel-stage]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const stage = btn.dataset.funnelStage;
        showSection('greenlight');
        const sel = q('[data-titles-status]');
        // Map the funnel's per-image failure buckets onto the title-level
        // rollup the browser filters on.
        if (sel) {
          sel.value = stage.startsWith('failed') ? 'failed' : stage;
          loadTitles();
        }
      });
    });
  }

  function fmtElapsed(sec) {
    if (sec == null) return '—';
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60), r = sec % 60;
    return m + 'm ' + String(r).padStart(2, '0') + 's';
  }

  function renderInFlight() {
    const el = q('[data-inflight]');
    if (!el) return;
    const items = overview.in_flight || [];

    const stamp = q('[data-inflight-updated]');
    if (stamp) stamp.textContent = 'updated ' + new Date().toLocaleTimeString();

    if (!items.length) {
      el.innerHTML = '<p class="muted">Nothing in flight. '
        + 'Either there is no greenlit work, or every node is idle between batches.</p>';
      return;
    }

    const staleCount = items.filter((i) => i.stale).length;

    el.innerHTML = `
      <table class="data-table">
        <thead><tr>
          <th style="width:104px">STAGE</th><th>TITLE</th><th>IMAGE</th>
          <th style="width:120px">NODE</th><th style="width:110px">ELAPSED</th>
          <th style="width:90px"></th>
        </tr></thead>
        <tbody>
        ${items.map((i) => `
          <tr class="${i.stale ? 'inflight-stale' : ''}">
            <td><span class="status-pill status-${i.stage === 'processing' ? 'in-progress' : 'pending'}">${esc(i.stage)}</span></td>
            <td>${i.external_id == null ? '' : '<span class="muted mono">#' + i.external_id + '</span> '}${esc(i.title)}
                <span class="muted">(${esc(i.year || '')})</span>
                ${i.remote_title ? `<div class="muted mono" style="font-size:11px">→ ${esc(i.remote_title)}${i.account ? ' · ' + esc(i.account) : ''}</div>` : ''}</td>
            <td class="mono">${esc(i.filename)} <span class="muted">#${i.poster_id}</span></td>
            <td class="mono">${esc(i.node || '—')}</td>
            <td class="mono">${fmtElapsed(i.elapsed_s)}
                ${i.stale ? '<span class="status-pill status-error">STALE</span>' : ''}</td>
            <td><button class="btn btn-ghost btn-tiny"
                  data-release-stage="${i.stage}"
                  data-release-id="${i.stage === 'processing' ? i.poster_id : i.tracking_id}"
                  title="Hand this item back to the queue">RELEASE</button></td>
          </tr>`).join('')}
        </tbody>
      </table>
      ${staleCount ? `<div class="filter-row" style="margin-top:10px">
        <button type="button" class="btn btn-accent btn-tiny" data-action="release-stale">
          RELEASE ${staleCount} STALE CLAIM${staleCount === 1 ? '' : 'S'}
        </button>
      </div>` : ''}
      <p class="setting-help" style="margin-top:8px">
        A single Photoshop run commonly takes 1–6 minutes. STALE means the claim
        has outlived the timeout; it would be reclaimed automatically the next
        time a node asks for work, but only then — so if you stopped the agent
        mid-batch, RELEASE puts the item straight back rather than making you
        wait it out.
      </p>`;

    el.querySelectorAll('[data-release-id]').forEach((b) => {
      b.addEventListener('click', async () => {
        const body = b.dataset.releaseStage === 'processing'
          ? { poster_ids: [parseInt(b.dataset.releaseId, 10)] }
          : { tracking_ids: [parseInt(b.dataset.releaseId, 10)] };
        try {
          await postJSON(API + '/inflight/release', body);
          toast('Released back to the queue.');
          loadOverview();
        } catch (e) { toast(e.message, 'error'); }
      });
    });
  }

  function renderAccountQuotas() {
    const el = q('[data-account-quotas]');
    if (!overview.accounts.length) {
      el.innerHTML = '<p class="muted">No marketplace accounts yet. Add one under the UPLOAD tab.</p>';
      return;
    }
    el.innerHTML = overview.accounts.map((a) => {
      const pct = a.quota.limit ? Math.min(100, Math.round(a.quota.used / a.quota.limit * 100)) : 0;
      const tone = pct >= 100 ? 'error' : pct >= 80 ? 'warn' : 'ok';
      const paused = !a.available;
      return `
        <div class="quota-row">
          <div class="quota-head">
            <strong>${esc(a.name)}</strong>
            <span class="status-pill status-${esc(a.target_site)}">${esc(a.target_site)}</span>
            ${paused ? `<span class="status-pill status-error">PAUSED</span>` : ''}
            ${!a.is_enabled ? `<span class="status-pill">DISABLED</span>` : ''}
            <span class="muted mono">${a.quota.used} / ${a.quota.limit} today · ${a.pending} queued</span>
          </div>
          <div class="pipe-progress">
            <div class="pipe-progress-bar pipe-bar-${tone}" style="width:${pct}%"></div>
          </div>
          ${a.pause_reason ? `<div class="quota-note ${a.pause_active ? '' : 'quota-note-stale'}">
              ${a.pause_active ? '' : '<span class="quota-note-tag">LAST ISSUE</span>'}
              ${esc(a.pause_reason)}
            </div>` : ''}
        </div>`;
    }).join('');
  }

  function renderJobs() {
    const el = q('[data-jobs-list]');
    if (!overview.jobs.length) {
      el.innerHTML = '<p class="muted">No jobs yet.</p>';
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>ID</th><th>KIND</th><th>STATUS</th><th>PROGRESS</th><th>NODE</th><th>WHEN</th><th></th></tr></thead>
        <tbody>
        ${overview.jobs.map((j) => `
          <tr>
            <td class="mono">${j.id}</td>
            <td class="mono">${esc(j.kind)}</td>
            <td><span class="status-pill status-${jobTone(j.status)}">${esc(j.status)}</span></td>
            <td class="mono">${j.progress || 0}%${j.progress_note ? ' · ' + esc(j.progress_note) : ''}</td>
            <td class="mono">${esc(j.claimed_by || '—')}</td>
            <td class="mono">${esc(j.created_at || '')}</td>
            <td><button class="btn btn-ghost btn-tiny" data-open-job="${j.id}">LOG</button></td>
          </tr>`).join('')}
        </tbody>
      </table>`;
    el.querySelectorAll('[data-open-job]').forEach((b) => {
      b.addEventListener('click', () => openConsole(parseInt(b.dataset.openJob, 10)));
    });
  }

  function jobTone(status) {
    return status === 'done' ? 'complete'
         : status === 'error' ? 'error'
         : status === 'running' ? 'in-progress' : 'pending';
  }

  function renderNodes() {
    const el = q('[data-nodes-list]');
    if (!el) return;
    if (!overview.nodes.length) {
      el.innerHTML = '<p class="muted">No worker nodes registered. Click REGISTER NODE to add your Windows VPS.</p>';
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>NAME</th><th>STATUS</th><th>CAPABILITIES</th><th>HOST</th><th>AGENT</th><th>LAST SEEN</th><th>ACTIONS</th></tr></thead>
        <tbody>
        ${overview.nodes.map((n) => `
          <tr>
            <td class="mono"><strong>${esc(n.name)}</strong></td>
            <td>
              <span class="status-pill status-${n.online ? 'complete' : 'error'}">${n.online ? 'ONLINE' : 'OFFLINE'}</span>
              ${!n.is_enabled ? '<span class="status-pill">DISABLED</span>' : ''}
            </td>
            <td class="mono">${esc((n.capabilities || []).join(', '))}</td>
            <td class="mono">${esc(n.hostname || '—')}</td>
            <td class="mono">${esc(n.agent_version || '—')}</td>
            <td class="mono">${esc(n.last_seen_at || 'never')}</td>
            <td class="actions-cell">
              <button class="btn btn-ghost btn-tiny" data-node-rotate="${n.id}">ROTATE TOKEN</button>
              <button class="btn btn-ghost btn-tiny" data-node-toggle="${n.id}" data-enabled="${n.is_enabled ? 1 : 0}">${n.is_enabled ? 'DISABLE' : 'ENABLE'}</button>
              <button class="btn btn-error btn-tiny" data-node-delete="${n.id}">DELETE</button>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;

    el.querySelectorAll('[data-node-rotate]').forEach((b) => {
      b.addEventListener('click', async () => {
        if (!confirm('Rotate this node\'s token? The node will stop working until you update its config with the new token.')) return;
        try {
          const d = await postJSON(`${API}/nodes/${b.dataset.nodeRotate}/rotate`, {});
          showTokenBox(d.token);
        } catch (e) { toast(e.message, 'error'); }
      });
    });
    el.querySelectorAll('[data-node-toggle]').forEach((b) => {
      b.addEventListener('click', async () => {
        try {
          await postJSON(`${API}/nodes/${b.dataset.nodeToggle}`,
            { is_enabled: b.dataset.enabled !== '1' });
          loadOverview();
        } catch (e) { toast(e.message, 'error'); }
      });
    });
    el.querySelectorAll('[data-node-delete]').forEach((b) => {
      b.addEventListener('click', async () => {
        if (!confirm('Delete this node registration?')) return;
        try {
          await postJSON(`${API}/nodes/${b.dataset.nodeDelete}/delete`, {});
          toast('Node deleted.');
          loadOverview();
        } catch (e) { toast(e.message, 'error'); }
      });
    });
  }

  function renderHistoryChart() {
    const canvas = document.getElementById('pipe-history-chart');
    if (!canvas || typeof Chart === 'undefined' || !overview.history) return;
    const labels = overview.history.map((h) => h.date.slice(5));
    const data   = overview.history.map((h) => h.count);
    if (historyChart) historyChart.destroy();

    const css = getComputedStyle(document.documentElement);
    const accent = css.getPropertyValue('--accent').trim() || '#e8b84b';
    const error  = css.getPropertyValue('--error').trim()  || '#e8554b';
    const sub    = css.getPropertyValue('--subtext').trim()|| '#8a8799';

    historyChart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{ label: 'Uploads', data, backgroundColor: accent, borderRadius: 2 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: (i) => overview.history[i[0].dataIndex].date } },
        },
        scales: {
          x: { ticks: { color: sub, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }, grid: { display: false } },
          y: {
            beginAtZero: true,
            suggestedMax: 100,
            ticks: { color: sub, stepSize: 25 },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
        },
      },
      plugins: [{
        // The 100/day cap drawn as a reference line — the number that
        // actually governs how fast the backlog can drain.
        id: 'limitLine',
        afterDraw(chart) {
          const y = chart.scales.y.getPixelForValue(100);
          if (!isFinite(y)) return;
          const { left, right } = chart.chartArea;
          const c = chart.ctx;
          c.save();
          c.strokeStyle = error;
          c.setLineDash([6, 4]);
          c.lineWidth = 1.5;
          c.beginPath(); c.moveTo(left, y); c.lineTo(right, y); c.stroke();
          c.restore();
        },
      }],
    });
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  LIVE CONSOLE
  // ═══════════════════════════════════════════════════════════════════════

  function openConsole(jobId) {
    consoleJob = jobId;
    const panel = q('[data-console-panel]');
    panel.hidden = false;
    q('[data-console-title]').textContent = 'job #' + jobId;
    q('[data-console-log]').textContent = 'Connecting…';
    q('[data-console-result]').hidden = true;
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    pollConsole();
    if (consoleTimer) clearInterval(consoleTimer);
    consoleTimer = setInterval(pollConsole, 2000);
  }

  function closeConsole() {
    consoleJob = null;
    if (consoleTimer) { clearInterval(consoleTimer); consoleTimer = null; }
    q('[data-console-panel]').hidden = true;
  }

  async function pollConsole() {
    if (!consoleJob) return;
    let job;
    try {
      job = await getJSON(`${API}/jobs/${consoleJob}`);
    } catch (e) {
      q('[data-console-log]').textContent = 'Could not read job: ' + e.message;
      return;
    }

    const logEl = q('[data-console-log]');
    const follow = q('[data-console-follow]').checked;
    logEl.textContent = job.log || '(no output yet)';
    if (follow) logEl.scrollTop = logEl.scrollHeight;

    q('[data-console-title]').textContent =
      `job #${job.id} · ${job.kind} · ${job.status}` +
      (job.progress_note ? ' · ' + job.progress_note : '');
    q('[data-console-progress]').style.width = (job.progress || 0) + '%';

    if (job.result || job.error) {
      const box = q('[data-console-result]');
      box.hidden = false;
      box.className = 'pipe-console-result ' + (job.error ? 'pipe-result-error' : 'pipe-result-ok');
      box.innerHTML = job.error
        ? '<strong>Error:</strong> ' + esc(job.error)
        : '<strong>Result:</strong> <pre class="mono">' + esc(JSON.stringify(job.result, null, 2)) + '</pre>';
    }

    // Stop polling a finished job, but leave the output on screen.
    if (['done', 'error', 'cancelled'].includes(job.status)) {
      if (consoleTimer) { clearInterval(consoleTimer); consoleTimer = null; }
      if (loaded.overview) loadOverview();
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  GREENLIGHT
  // ═══════════════════════════════════════════════════════════════════════

  async function loadGreenlight() {
    if (!overview) await loadOverview();
    const el = q('[data-greenlight-queue]');
    let data;
    try {
      data = await getJSON(withProject(API + '/greenlight/queue'));
    } catch (e) {
      el.innerHTML = '<div class="muted">Could not load: ' + esc(e.message) + '</div>';
      return;
    }

    if (!data.dates.length) {
      el.innerHTML = '<p class="muted">Nothing awaiting greenlight — everything completed has been released.</p>';
      updateGlSummary();
      return;
    }

    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th style="width:34px"></th><th>SAVE DATE</th><th>TITLES</th><th>POSTERS</th><th>PAYMENT</th></tr></thead>
        <tbody>
        ${data.dates.map((d) => `
          <tr>
            <td><input type="checkbox" data-gl-date="${d.date}" data-paid="${d.fully_paid ? 1 : 0}"></td>
            <td class="mono">${d.date}</td>
            <td class="mono">${d.titles}</td>
            <td class="mono">${d.posters}</td>
            <td>${d.fully_paid
                  ? '<span class="status-pill status-complete">PAID</span>'
                  : `<span class="status-pill status-pending">${d.paid_posters}/${d.posters} paid</span>`}</td>
          </tr>`).join('')}
        </tbody>
      </table>`;

    el.querySelectorAll('[data-gl-date]').forEach((cb) =>
      cb.addEventListener('change', updateGlSummary));
    updateGlSummary();
  }

  function selectedGlDates() {
    return qa('[data-gl-date]:checked').map((cb) => cb.dataset.glDate);
  }

  function updateGlSummary() {
    const n = selectedGlDates().length;
    setStatus(q('[data-gl-summary]'), n ? `${n} date${n === 1 ? '' : 's'} selected` : '');
  }

  async function doGreenlight(body, statusEl) {
    setStatus(statusEl, 'Working…');
    try {
      const d = await postJSON(API + '/greenlight', { project_id: projectId, ...body });
      setStatus(statusEl,
        `Greenlit ${d.greenlit} titles (${d.posters} posters)` +
        (d.skipped ? `, ${d.skipped} skipped` : ''), 'ok');
      toast(`Greenlit ${d.greenlit} titles.`);
      loadGreenlight();
      loadOverview();
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
    }
  }

  // ── Title browser ───────────────────────────────────────────────────────
  //
  // Selection lives in a Set of title ids held OUTSIDE the render, so it
  // survives paging and re-renders — you can select on page 1, jump to page 4,
  // add more, then act on the whole lot. Same model as the worker-facing
  // master browser, which this deliberately mirrors.

  const titlesState = {
    page: 1, pages: 1, total: 0, pageSize: 100,
    rows: [],                 // current page, in display order
    selected: new Set(),      // title ids, across all pages
    lastIndex: null,          // anchor for shift-click ranges
  };

  function titlesQueryString(extra) {
    const p = new URLSearchParams({
      status: q('[data-titles-status]').value,
      page: titlesState.page,
      page_size: titlesState.pageSize,
    });
    const search = q('[data-titles-q]').value.trim();
    const from = q('[data-titles-from]').value;
    const to = q('[data-titles-to]').value;
    if (search) p.set('q', search);
    if (from) p.set('date_from', from);
    if (to) p.set('date_to', to);
    Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v));
    return p.toString();
  }

  async function loadTitles(resetPage) {
    if (resetPage !== false) titlesState.page = 1;
    titlesState.pageSize = parseInt(q('[data-titles-pagesize]').value, 10) || 100;

    const el = q('[data-titles-list]');
    el.innerHTML = '<div class="muted">Loading…</div>';

    let data;
    try {
      data = await getJSON(withProject(`${API}/titles?${titlesQueryString()}`));
    } catch (e) {
      el.innerHTML = '<div class="muted">Could not load: ' + esc(e.message) + '</div>';
      return;
    }

    titlesState.rows = data.items;
    titlesState.pages = data.pages;
    titlesState.total = data.total;
    titlesState.lastIndex = null;

    if (!data.items.length) {
      el.innerHTML = '<p class="muted">No titles match. Try a different stage or clear the filters.</p>';
      q('[data-titles-pager]').hidden = true;
      q('[data-titles-bulkbar]').hidden = titlesState.selected.size === 0;
      updateTitlesSelectionUI();
      return;
    }

    el.innerHTML = `
      <table class="data-table pipe-titles-table">
        <thead>
          <tr>
            <th style="width:34px"><input type="checkbox" data-titles-head-cb
                   title="Select/clear this page"></th>
            <th style="width:60px">#</th>
            <th>TITLE</th>
            <th style="width:104px">DATE</th>
            <th style="width:132px">STAGE</th>
            <th style="width:120px">IMAGES</th>
          </tr>
        </thead>
        <tbody data-titles-body>
        ${data.items.map((t, i) => titleRowHtml(t, i)).join('')}
        </tbody>
      </table>`;

    // Row click selects — anywhere on the row, like the worker's browser.
    const body = el.querySelector('[data-titles-body]');
    body.addEventListener('click', onTitleRowClick);
    attachTitlesDragSelect(body);

    el.querySelector('[data-titles-head-cb]').addEventListener('click', (ev) => {
      ev.stopPropagation();
      const pageIds = titlesState.rows.filter((r) => r.actionable !== false).map((r) => r.id);
      const allOn = pageIds.every((id) => titlesState.selected.has(id));
      pageIds.forEach((id) => allOn ? titlesState.selected.delete(id)
                                    : titlesState.selected.add(id));
      renderTitlesSelection();
    });

    q('[data-titles-pager]').hidden = false;
    q('[data-titles-pageinfo]').textContent =
      `page ${data.page} / ${data.pages}  ·  ${data.total.toLocaleString()} titles`;

    renderTitlesSelection();
  }

  function titleRowHtml(t, i) {
    const stage = t.pipeline_status
      ? `<span class="status-pill status-${stageTone(t.pipeline_status)}">${esc(t.pipeline_status)}</span>`
      : '<span class="status-pill status-pending">not greenlit</span>';

    // Make it obvious why a row can't be greenlit — either it isn't complete,
    // or every poster is already in the pipeline.
    let hint = '';
    if (!t.actionable) {
      hint = t.status !== 'complete'
        ? `<span class="pipe-hint">worker hasn't finished this title</span>`
        : `<span class="pipe-hint">all ${t.poster_count} already in pipeline</span>`;
    } else if (t.in_pipeline_count > 0) {
      hint = `<span class="pipe-hint">${t.pending_count} of ${t.poster_count} to promote</span>`;
    }

    const errors = t.posters.filter((p) => p.error);

    return `
      <tr data-id="${t.id}" data-idx="${i}"
          class="${titlesState.selected.has(t.id) ? 'selected' : ''} ${t.actionable ? '' : 'row-inert'}">
        <td class="col-cb"><input type="checkbox" ${titlesState.selected.has(t.id) ? 'checked' : ''}
              ${t.actionable ? '' : 'disabled'}></td>
        <td class="mono">${t.external_id == null ? '—' : t.external_id}</td>
        <td>${esc(t.title)} <span class="muted">(${esc(t.year)})</span> ${hint}</td>
        <td class="mono">${esc(t.save_date || '—')}</td>
        <td>${stage}</td>
        <td class="mono">${t.poster_count}${t.uploaded_count ? ` · ${t.uploaded_count} up` : ''}</td>
      </tr>
      ${errors.map((p) => `
        <tr class="pipe-subrow">
          <td></td>
          <td colspan="5" class="muted mono">
            ↳ ${esc(p.filename)} — poster #${p.id} — ${esc(p.error)} (${p.attempts} attempts)
          </td>
        </tr>`).join('')}`;
  }

  function onTitleRowClick(ev) {
    const tr = ev.target.closest('tr[data-id]');
    if (!tr) return;
    const idx = parseInt(tr.dataset.idx, 10);
    const row = titlesState.rows[idx];
    if (!row || row.actionable === false) return;

    if (ev.shiftKey && titlesState.lastIndex !== null) {
      const a = Math.min(titlesState.lastIndex, idx);
      const b = Math.max(titlesState.lastIndex, idx);
      for (let i = a; i <= b; i++) {
        const r = titlesState.rows[i];
        if (r && r.actionable !== false) titlesState.selected.add(r.id);
      }
    } else {
      if (titlesState.selected.has(row.id)) titlesState.selected.delete(row.id);
      else titlesState.selected.add(row.id);
      titlesState.lastIndex = idx;
    }
    renderTitlesSelection();
  }

  // Drag down the list to select a run of rows — the fastest way to grab a
  // block, and what the worker's browser already does.
  function attachTitlesDragSelect(body) {
    let dragging = false, startIdx = null, mode = 'add';

    body.addEventListener('mousedown', (ev) => {
      const tr = ev.target.closest('tr[data-id]');
      if (!tr || ev.target.tagName === 'INPUT') return;
      const row = titlesState.rows[parseInt(tr.dataset.idx, 10)];
      if (!row || row.actionable === false) return;
      dragging = true;
      startIdx = parseInt(tr.dataset.idx, 10);
      mode = titlesState.selected.has(row.id) ? 'remove' : 'add';
    });

    body.addEventListener('mousemove', (ev) => {
      if (!dragging) return;
      const tr = ev.target.closest('tr[data-id]');
      if (!tr) return;
      const a = Math.min(startIdx, parseInt(tr.dataset.idx, 10));
      const b = Math.max(startIdx, parseInt(tr.dataset.idx, 10));
      for (let i = a; i <= b; i++) {
        const r = titlesState.rows[i];
        if (!r || r.actionable === false) continue;
        if (mode === 'add') titlesState.selected.add(r.id);
        else titlesState.selected.delete(r.id);
      }
      renderTitlesSelection();
    });

    document.addEventListener('mouseup', () => { dragging = false; });
  }

  function renderTitlesSelection() {
    qa('tr[data-id]').forEach((tr) => {
      const id = parseInt(tr.dataset.id, 10);
      const on = titlesState.selected.has(id);
      tr.classList.toggle('selected', on);
      const cb = tr.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = on;
    });

    const head = q('[data-titles-head-cb]');
    if (head) {
      const pageIds = titlesState.rows.filter((r) => r.actionable !== false).map((r) => r.id);
      const on = pageIds.length > 0 && pageIds.every((id) => titlesState.selected.has(id));
      const some = pageIds.some((id) => titlesState.selected.has(id));
      head.checked = on;
      head.indeterminate = !on && some;
    }
    updateTitlesSelectionUI();
  }

  function updateTitlesSelectionUI() {
    const n = titlesState.selected.size;
    q('[data-titles-bulkbar]').hidden = false;
    q('[data-titles-selcount]').textContent = n;
    q('[data-action="titles-greenlight"]').disabled = n === 0;
    q('[data-action="titles-ungreenlight"]').disabled = n === 0;
  }

  async function selectAllMatching() {
    try {
      const d = await getJSON(withProject(`${API}/titles?${titlesQueryString({ ids_only: 1 })}`));
      d.ids.forEach((id) => titlesState.selected.add(id));
      renderTitlesSelection();
      toast(`Selected ${d.ids.length.toLocaleString()} titles` +
            (d.truncated ? ' (capped at 20,000)' : '') + '.');
    } catch (e) { toast(e.message, 'error'); }
  }

  async function titlesGoto(page) {
    titlesState.page = Math.max(1, Math.min(titlesState.pages, page));
    await loadTitles(false);
  }

  async function greenlightSelected() {
    const ids = [...titlesState.selected];
    if (!ids.length) return toast('Nothing selected.', 'error');
    const statusEl = q('[data-gl-summary]');
    setStatus(statusEl, `Greenlighting ${ids.length} titles…`);
    try {
      const d = await postJSON(API + '/greenlight',
        { title_ids: ids, project_id: projectId });
      let msg = `Greenlit ${d.greenlit} titles (${d.posters} posters)`;
      if (d.skipped) msg += `, ${d.skipped} had nothing left to promote`;
      setStatus(statusEl, msg, 'ok');
      toast(msg);
      titlesState.selected.clear();
      await loadTitles(false);
      loadOverview();
      if (loaded.greenlight) loadGreenlight();
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
      toast(e.message, 'error');
    }
  }

  async function ungreenlightSelected() {
    const ids = [...titlesState.selected];
    if (!ids.length) return toast('Nothing selected.', 'error');
    if (!confirm(
      `Pull ${ids.length} title(s) back out of the pipeline?\n\n` +
      'Only posters not yet processed are affected — anything already in ' +
      'storage or uploaded stays exactly as it is.'
    )) return;
    try {
      const d = await postJSON(API + '/ungreenlight', { title_ids: ids });
      toast(`Pulled back ${d.titles} titles.`);
      titlesState.selected.clear();
      await loadTitles(false);
      loadOverview();
    } catch (e) { toast(e.message, 'error'); }
  }

  function stageTone(stage) {
    if (!stage) return 'pending';
    if (stage === 'uploaded') return 'complete';
    if (stage.startsWith('failed')) return 'error';
    if (stage === 'greenlit') return 'pending';
    return 'in-progress';
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  SETTINGS (generated forms)
  // ═══════════════════════════════════════════════════════════════════════

  async function loadSettings() {
    try {
      settings = await getJSON(withProject(API + '/settings'));
    } catch (e) {
      toast('Could not load settings: ' + e.message, 'error');
      return;
    }
    Object.keys(SETTINGS_GROUPS).forEach(renderSettingsGroup);
    renderSelectors();
    renderTimings();

    const editor = q('[data-script-editor]');
    if (editor) editor.value = settings.settings.process_script || '';
    const ver = q('[data-script-version]');
    if (ver) ver.textContent = settings.script_version || '—';
  }

  function renderSettingsGroup(group) {
    const container = q(`[data-settings-group="${group}"]`);
    if (!container) return;
    const fields = SETTINGS_GROUPS[group] || [];

    container.innerHTML = fields.map(([key, type, label, help, options]) => {
      const value = settings.settings[key];
      const modified = settings.overrides[key] &&
                       (settings.overrides[key].global || settings.overrides[key].project);
      let input;
      if (type === 'bool') {
        input = `<input type="checkbox" data-setting="${key}" ${value ? 'checked' : ''}>`;
      } else if (type === 'select') {
        input = `<select data-setting="${key}">` +
          (options || []).map((o) =>
            `<option value="${esc(o)}" ${o === value ? 'selected' : ''}>${esc(o)}</option>`).join('') +
          `</select>`;
      } else if (type === 'textarea') {
        input = `<textarea data-setting="${key}" rows="3">${esc(value)}</textarea>`;
      } else {
        input = `<input type="${type}" data-setting="${key}" value="${esc(value)}"
                   ${type === 'number' ? 'step="any"' : ''}>`;
      }
      return `
        <div class="setting-field ${type === 'bool' ? 'setting-bool' : ''}">
          <label>
            <span class="setting-label">
              ${esc(label)}
              ${modified ? '<span class="setting-badge" title="Overridden — not the code default">set</span>' : ''}
            </span>
            ${input}
          </label>
          <p class="setting-help">${esc(help)}</p>
        </div>`;
    }).join('');
  }

  function collectGroup(group) {
    const container = q(`[data-settings-group="${group}"]`);
    const out = {};
    if (!container) return out;
    container.querySelectorAll('[data-setting]').forEach((el) => {
      const key = el.dataset.setting;
      if (el.type === 'checkbox') out[key] = el.checked;
      else if (el.type === 'number') out[key] = el.value === '' ? 0 : Number(el.value);
      else out[key] = el.value;
    });
    return out;
  }

  async function saveGroup(group) {
    const statusEl = q(`[data-settings-status="${group}"]`);
    setStatus(statusEl, 'Saving…');
    try {
      const d = await postJSON(API + '/settings', {
        settings: collectGroup(group),
        project_id: projectId,
        scope: 'global',
      });
      setStatus(statusEl, `Saved ${d.applied.length} settings.`, 'ok');
      toast('Settings saved — the worker node picks these up on its next run.');
      const ver = q('[data-script-version]');
      if (ver && d.script_version) ver.textContent = d.script_version;
      settings = await getJSON(withProject(API + '/settings'));
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
    }
  }

  // ── Selectors ───────────────────────────────────────────────────────────

  function renderSelectors() {
    const container = q('[data-selectors-grid]');
    if (!container) return;
    const map = settings.settings.selectors || {};
    container.innerHTML = Object.keys(map).sort().map((key) => `
      <div class="setting-field">
        <label>
          <span class="setting-label mono">${esc(key)}</span>
          <input type="text" data-selector="${esc(key)}" value="${esc(map[key])}" spellcheck="false">
        </label>
      </div>`).join('') + `
      <p class="setting-help" style="grid-column:1/-1">
        Prefix determines the lookup strategy: <code>css:</code>, <code>xpath:</code> or
        <code>name:</code>. Values without a prefix are treated as CSS. Entries ending in
        <code>_url</code> or <code>_marker</code> are plain strings, not selectors.
        <br><br>
        <strong>upload_url</strong> is used to reach the upload form directly.
        Leave it blank to fall back to loading the profile page and clicking
        <strong>upload_button</strong> — only worth doing if the direct URL stops
        working, since a URL survives site redesigns that break a button's CSS class.
      </p>`;
  }

  async function saveSelectors() {
    const statusEl = q('[data-selectors-status]');
    const map = {};
    qa('[data-selector]').forEach((el) => { map[el.dataset.selector] = el.value; });
    setStatus(statusEl, 'Saving…');
    try {
      await postJSON(API + '/settings', {
        settings: { selectors: map }, project_id: projectId, scope: 'global',
      });
      setStatus(statusEl, 'Saved. Use Test Upload on one image to confirm.', 'ok');
      toast('Selectors saved.');
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
    }
  }

  // ── Timings ─────────────────────────────────────────────────────────────

  const TIMING_HELP = {
    login_wait:       'After submitting credentials.',
    page_load_wait:   'After any navigation.',
    upload_wait:      'After the file is accepted, while the site processes it.',
    form_input_delay: 'Between individual field interactions.',
    submit_wait:      'After clicking submit, before checking the result.',
    element_timeout:  'How long to wait for an element before failing.',
    popup_delay:      'Before attempting to dismiss a popup.',
    between_images:   'Pause between consecutive images in a batch.',
  };

  function renderTimings() {
    const container = q('[data-timings-grid]');
    if (!container) return;
    const map = settings.settings.timings || {};
    container.innerHTML = Object.keys(map).sort().map((key) => `
      <div class="setting-field">
        <label>
          <span class="setting-label mono">${esc(key)}</span>
          <input type="number" step="0.1" min="0" data-timing="${esc(key)}" value="${esc(map[key])}">
        </label>
        <p class="setting-help">${esc(TIMING_HELP[key] || '')}</p>
      </div>`).join('');
  }

  async function saveTimings() {
    const statusEl = q('[data-timings-status]');
    const map = {};
    qa('[data-timing]').forEach((el) => { map[el.dataset.timing] = Number(el.value); });
    setStatus(statusEl, 'Saving…');
    try {
      await postJSON(API + '/settings', {
        settings: { timings: map }, project_id: projectId, scope: 'global',
      });
      setStatus(statusEl, 'Saved.', 'ok');
      toast('Timings saved.');
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
    }
  }

  // ── Script editor ───────────────────────────────────────────────────────

  async function saveScript() {
    const statusEl = q('[data-script-status]');
    setStatus(statusEl, 'Saving…');
    try {
      const d = await postJSON(API + '/settings', {
        settings: { process_script: q('[data-script-editor]').value },
        project_id: projectId, scope: 'global',
      });
      setStatus(statusEl, 'Saved. Run a Test Process to verify.', 'ok');
      q('[data-script-version]').textContent = d.script_version || '—';
      toast('Script saved.');
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
    }
  }

  async function previewScript() {
    const pre = q('[data-script-preview]');
    pre.textContent = 'Rendering…';
    try {
      const d = await getJSON(withProject(API + '/settings/script_preview'));
      pre.textContent = d.script;
      q('[data-script-version]').textContent = d.version;
    } catch (e) {
      pre.textContent = 'Could not render: ' + e.message;
    }
  }

  async function resetScript() {
    if (!confirm('Reset the script to the built-in default? Your current version will be lost.')) return;
    try {
      await postJSON(API + '/settings/reset',
        { key: 'process_script', scope: 'global', project_id: projectId });
      await loadSettings();
      toast('Script reset to default.');
    } catch (e) { toast(e.message, 'error'); }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  UPLOAD SECTION (accounts)
  // ═══════════════════════════════════════════════════════════════════════

  async function loadUploadSection() {
    if (!settings) await loadSettings();
    else { Object.keys(SETTINGS_GROUPS).forEach(renderSettingsGroup); renderSelectors(); renderTimings(); }
    await loadAccounts();
  }

  async function loadAccounts() {
    const el = q('[data-accounts-list]');
    try {
      const d = await getJSON(withProject(API + '/accounts'));
      accounts = d.accounts;
    } catch (e) {
      el.innerHTML = '<div class="muted">Could not load: ' + esc(e.message) + '</div>';
      return;
    }

    // Keep the Test Upload account picker in sync — one less thing to
    // remember when you're debugging.
    const picker = q('[data-test-upload-account]');
    if (picker) {
      picker.innerHTML = '<option value="">—</option>' +
        accounts.map((a) => `<option value="${a.id}">${esc(a.name)} (${esc(a.target_site)})</option>`).join('');
    }

    if (!accounts.length) {
      el.innerHTML = '<p class="muted">No accounts yet. Click ADD ACCOUNT.</p>';
      return;
    }

    el.innerHTML = accounts.map((a) => {
      const s = a.stats || {};
      return `
        <div class="account-card">
          <div class="account-head">
            <strong>${esc(a.name)}</strong>
            <span class="status-pill status-${esc(a.target_site)}">${esc(a.target_site)}</span>
            ${a.is_enabled ? '' : '<span class="status-pill">DISABLED</span>'}
            ${a.available ? '' : '<span class="status-pill status-error">PAUSED</span>'}
            <span class="muted mono">${esc(a.email)}</span>
          </div>
          <div class="account-stats mono">
            turn #${a.rotation_order ?? 100}, ${a.rotation_size || 'default'} per turn ·
            today ${a.quota.used}/${a.quota.limit} ·
            uploaded ${s.uploaded || 0} ·
            pending ${s.pending || 0} ·
            failed ${s.failed || 0} ·
            removed ${s.removed || 0}
            ${a.last_run_at ? ' · last run ' + esc(a.last_run_at.slice(0, 16).replace('T', ' ')) : ''}
          </div>
          ${a.pause_reason ? `<div class="quota-note ${a.pause_active ? '' : 'quota-note-stale'}">
              ${a.pause_active ? '' : '<span class="quota-note-tag">LAST ISSUE</span>'}
              ${esc(a.pause_reason)}
            </div>` : ''}
          <div class="actions-cell">
            <button class="btn btn-ghost btn-tiny" data-acc-edit="${a.id}">EDIT</button>
            ${a.available
              ? (a.pause_reason ? `<button class="btn btn-ghost btn-tiny" data-acc-resume="${a.id}">CLEAR MESSAGE</button>` : '')
              : `<button class="btn btn-accent btn-tiny" data-acc-resume="${a.id}">RESUME</button>`}
            <button class="btn btn-ghost btn-tiny" data-acc-requeue="${a.id}">REQUEUE BACK CATALOGUE</button>
            <button class="btn btn-error btn-tiny" data-acc-delete="${a.id}">DELETE</button>
          </div>
        </div>`;
    }).join('');

    el.querySelectorAll('[data-acc-edit]').forEach((b) =>
      b.addEventListener('click', () => openAccountModal(
        accounts.find((a) => a.id === parseInt(b.dataset.accEdit, 10)))));

    el.querySelectorAll('[data-acc-resume]').forEach((b) =>
      b.addEventListener('click', async () => {
        try {
          await postJSON(`${API}/accounts/${b.dataset.accResume}/resume`, {});
          // Same endpoint either way — it clears paused_until and
          // pause_reason together. The wording just matches what the admin
          // was actually looking at.
          toast(b.textContent.trim() === 'CLEAR MESSAGE'
                ? 'Message cleared.' : 'Account resumed.');
          loadAccounts(); loadOverview();
        } catch (e) { toast(e.message, 'error'); }
      }));

    el.querySelectorAll('[data-acc-requeue]').forEach((b) =>
      b.addEventListener('click', async () => {
        if (!confirm('Queue every processed image for upload on this account?\n\nThis is the ban-recovery path — nothing is reprocessed, the files are already in storage. Images already uploaded to this account are skipped.')) return;
        try {
          const d = await postJSON(`${API}/accounts/${b.dataset.accRequeue}/requeue`, {});
          toast(`Queued ${d.queued} images.`);
          loadAccounts(); loadOverview();
        } catch (e) { toast(e.message, 'error'); }
      }));

    el.querySelectorAll('[data-acc-delete]').forEach((b) =>
      b.addEventListener('click', async () => {
        if (!confirm('Delete this account?\n\nIts upload history is kept — that record is what makes rebuilding onto a new account possible. Only the credentials are removed.')) return;
        try {
          await postJSON(`${API}/accounts/${b.dataset.accDelete}/delete`, {});
          toast('Account deleted.');
          loadAccounts(); loadOverview();
        } catch (e) { toast(e.message, 'error'); }
      }));
  }

  function openAccountModal(account) {
    const m = q('[data-account-modal]');
    q('[data-account-modal-title]').textContent = account ? 'EDIT ACCOUNT' : 'ADD ACCOUNT';
    q('[data-account-id]').value       = account ? account.id : '';
    q('[data-account-name]').value     = account ? account.name : '';
    q('[data-account-target]').value   = account ? account.target_site : 'faa';
    q('[data-account-email]').value    = account ? account.email : '';
    q('[data-account-password]').value = '';
    q('[data-account-profile]').value  = account ? (account.profile_url || '') : '';
    q('[data-account-chrome]').value   = account ? (account.chrome_profile_dir || '') : '';
    q('[data-account-limit]').value    = account ? account.daily_limit : 100;
    q('[data-account-rotorder]').value = account ? (account.rotation_order ?? 100) : 100;
    q('[data-account-rotsize]').value  = account && account.rotation_size ? account.rotation_size : '';
    q('[data-account-enabled]').checked = account ? account.is_enabled : true;
    setStatus(q('[data-account-status]'), '');
    m.hidden = false;
  }

  async function saveAccount() {
    const statusEl = q('[data-account-status]');
    const id = q('[data-account-id]').value;
    const body = {
      project_id:         projectId,
      name:               q('[data-account-name]').value.trim(),
      target_site:        q('[data-account-target]').value,
      email:              q('[data-account-email]').value.trim(),
      profile_url:        q('[data-account-profile]').value.trim(),
      chrome_profile_dir: q('[data-account-chrome]').value.trim(),
      daily_limit:        parseInt(q('[data-account-limit]').value, 10) || 100,
      rotation_order:     parseInt(q('[data-account-rotorder]').value, 10) || 100,
      rotation_size:      q('[data-account-rotsize]').value
                            ? parseInt(q('[data-account-rotsize]').value, 10) : null,
      is_enabled:         q('[data-account-enabled]').checked,
    };
    const password = q('[data-account-password]').value;
    if (password) body.password = password;

    if (!body.name || !body.email) return setStatus(statusEl, 'Name and email are required.', 'error');
    if (!id && !password) return setStatus(statusEl, 'A password is required for a new account.', 'error');

    setStatus(statusEl, 'Saving…');
    try {
      await postJSON(id ? `${API}/accounts/${id}` : `${API}/accounts`, body);
      q('[data-account-modal]').hidden = true;
      toast('Account saved.');
      loadAccounts(); loadOverview();
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  TEST & DEBUG
  // ═══════════════════════════════════════════════════════════════════════

  async function loadTestSection() {
    if (!accounts.length) await loadAccounts();
    loadTestJobs();
  }

  async function loadTestJobs() {
    const el = q('[data-test-jobs]');
    try {
      const d = await getJSON(API + '/jobs?limit=15');
      const tests = d.jobs.filter((j) => j.kind.startsWith('test_'));
      if (!tests.length) {
        el.innerHTML = '<p class="muted">No tests run yet.</p>';
        return;
      }
      el.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ID</th><th>KIND</th><th>STATUS</th><th>WHEN</th><th></th></tr></thead>
          <tbody>
          ${tests.map((j) => `
            <tr>
              <td class="mono">${j.id}</td>
              <td class="mono">${esc(j.kind.replace('test_', ''))}</td>
              <td><span class="status-pill status-${jobTone(j.status)}">${esc(j.status)}</span></td>
              <td class="mono">${esc(j.created_at || '')}</td>
              <td><button class="btn btn-ghost btn-tiny" data-open-job="${j.id}">LOG</button></td>
            </tr>`).join('')}
          </tbody>
        </table>`;
      el.querySelectorAll('[data-open-job]').forEach((b) =>
        b.addEventListener('click', () => openConsole(parseInt(b.dataset.openJob, 10))));
    } catch (e) {
      el.innerHTML = '<div class="muted">Could not load: ' + esc(e.message) + '</div>';
    }
  }

  async function runTest(kind, body, statusEl) {
    setStatus(statusEl, 'Queuing…');
    try {
      const d = await postJSON(`${API}/test/${kind}`, { project_id: projectId, ...body });
      setStatus(statusEl, `Queued as job #${d.job_id}. Watch the console below.`, 'ok');
      openConsole(d.job_id);
      loadTestJobs();
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  FAILURES
  // ═══════════════════════════════════════════════════════════════════════

  async function loadFailures() {
    const el = q('[data-failures-list]');
    const kind = q('[data-failures-kind]').value;
    el.innerHTML = '<div class="muted">Loading…</div>';

    let data;
    try {
      data = await getJSON(withProject(`${API}/failures?kind=${kind}`));
      failures = data.items;
    } catch (e) {
      el.innerHTML = '<div class="muted">Could not load: ' + esc(e.message) + '</div>';
      return;
    }

    if (!failures.length) {
      el.innerHTML = '<p class="muted">No failures. </p>';
      setStatus(q('[data-failures-summary]'), '');
      return;
    }

    if (kind === 'processing') {
      el.innerHTML = `
        <table class="data-table">
          <thead><tr><th style="width:34px"></th><th>TITLE</th><th>FILE</th><th>ATTEMPTS</th><th>ERROR</th></tr></thead>
          <tbody>
          ${failures.map((f) => `
            <tr>
              <td><input type="checkbox" data-fail-pick="${f.poster_id}"></td>
              <td>${esc(f.title)} <span class="muted">(${esc(f.year)})</span></td>
              <td class="mono">${esc(f.filename)}</td>
              <td class="mono">${f.attempts}${f.exhausted ? ' <span class="status-pill status-error">EXHAUSTED</span>' : ''}</td>
              <td class="mono pipe-error-cell">${esc(f.error || '')}</td>
            </tr>`).join('')}
          </tbody>
        </table>`;
    } else {
      el.innerHTML = `
        <table class="data-table">
          <thead><tr><th style="width:34px"></th><th>LISTING TITLE</th><th>ACCOUNT</th><th>ATTEMPTS</th><th>ERROR</th><th>EVIDENCE</th></tr></thead>
          <tbody>
          ${failures.map((f) => `
            <tr>
              <td><input type="checkbox" data-fail-pick="${f.tracking_id}"></td>
              <td>${esc(f.remote_title || f.title)}</td>
              <td class="mono">${esc(f.account)}</td>
              <td class="mono">${f.attempts}${f.exhausted ? ' <span class="status-pill status-error">EXHAUSTED</span>' : ''}</td>
              <td class="mono pipe-error-cell">${esc(f.error || '')}</td>
              <td>${f.screenshot
                    ? `<button class="btn btn-ghost btn-tiny" data-shot="${esc(f.screenshot)}" data-shot-name="${esc(f.remote_title || '')}">SCREENSHOT</button>`
                    : '<span class="muted">—</span>'}</td>
            </tr>`).join('')}
          </tbody>
        </table>`;

      el.querySelectorAll('[data-shot]').forEach((b) =>
        b.addEventListener('click', () => {
          const lb = q('[data-shot-lightbox]');
          q('[data-shot-img]').src = `${API}/artifact?path=${encodeURIComponent(b.dataset.shot)}`;
          q('[data-shot-title]').textContent = b.dataset.shotName || b.dataset.shot;
          lb.hidden = false;
        }));
    }

    setStatus(q('[data-failures-summary]'), `${failures.length} shown`);
  }

  function selectedFailIds() {
    return qa('[data-fail-pick]:checked').map((c) => parseInt(c.dataset.failPick, 10));
  }

  async function failuresAction(action) {
    const ids = selectedFailIds();
    if (!ids.length) return toast('Nothing selected.', 'error');
    const kind = q('[data-failures-kind]').value;
    const key = kind === 'processing' ? 'poster_ids' : 'tracking_ids';

    try {
      if (action === 'retry') {
        const d = await postJSON(API + '/failures/retry', { kind, [key]: ids });
        toast(`Requeued ${d.requeued} items.`);
      } else if (action === 'skip') {
        if (!confirm('Permanently exclude these from the pipeline?')) return;
        const d = await postJSON(API + '/failures/skip', { [key]: ids });
        toast(`Skipped ${d.skipped} items.`);
      } else if (action === 'removed') {
        if (kind === 'processing') return toast('Mark Removed only applies to uploads.', 'error');
        const reason = prompt('Reason for the takedown (optional):') || '';
        const d = await postJSON(API + '/failures/mark_removed',
          { tracking_ids: ids, reason });
        toast(`Marked ${d.marked} as removed.`);
      }
      loadFailures();
      loadOverview();
    } catch (e) { toast(e.message, 'error'); }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  NODES
  // ═══════════════════════════════════════════════════════════════════════

  function showTokenBox(token) {
    const m = q('[data-node-modal]');
    m.hidden = false;
    q('[data-node-token-box]').hidden = false;
    q('[data-node-token]').textContent = token;
    setStatus(q('[data-node-status]'), 'Token generated — copy it now.', 'ok');
  }

  async function saveNode() {
    const statusEl = q('[data-node-status]');
    const name = q('[data-node-name]').value.trim();
    if (!name) return setStatus(statusEl, 'Name is required.', 'error');
    const caps = [];
    if (q('[data-node-cap-process]').checked) caps.push('process');
    if (q('[data-node-cap-upload]').checked)  caps.push('upload');
    if (!caps.length) return setStatus(statusEl, 'Pick at least one capability.', 'error');

    setStatus(statusEl, 'Registering…');
    try {
      const d = await postJSON(API + '/nodes', { name, capabilities: caps });
      showTokenBox(d.token);
      loadOverview();
    } catch (e) {
      setStatus(statusEl, e.message, 'error');
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  EVENT WIRING
  // ═══════════════════════════════════════════════════════════════════════

  root.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;

    switch (action) {
      case 'refresh-overview': loadOverview(); break;

      case 'release-stale':
        if (!confirm('Release all stale claims back to the queue?\n\n'
            + 'Only items still marked as claimed are affected — anything that '
            + 'actually finished has already moved on.')) return;
        try {
          const d = await postJSON(API + '/inflight/release', { all_stale: true });
          toast(`Released ${d.released_posters} processing, ${d.released_uploads} uploading.`);
          loadOverview();
        } catch (e) { toast(e.message, 'error'); }
        break;

      case 'run-process':
      case 'run-upload': {
        const kind = action === 'run-process' ? 'process' : 'upload';
        try {
          const d = await postJSON(API + '/run', { kind, project_id: projectId });
          toast(`${kind} run requested.`);
          openConsole(d.job_id);
        } catch (e) { toast(e.message, 'error'); }
        break;
      }

      case 'console-close': closeConsole(); break;

      case 'save-greenlight-mode': {
        const statusEl = q('[data-greenlight-mode-status]');
        setStatus(statusEl, 'Saving…');
        try {
          await postJSON(API + '/settings', {
            settings: { greenlight_mode: q('[data-greenlight-mode]').value },
            project_id: projectId, scope: 'global',
          });
          setStatus(statusEl, 'Saved.', 'ok');
        } catch (e) { setStatus(statusEl, e.message, 'error'); }
        break;
      }

      case 'gl-select-paid':
        qa('[data-gl-date]').forEach((cb) => { cb.checked = cb.dataset.paid === '1'; });
        updateGlSummary();
        break;

      case 'gl-select-none':
        qa('[data-gl-date]').forEach((cb) => { cb.checked = false; });
        updateGlSummary();
        break;

      case 'gl-greenlight-selected': {
        const dates = selectedGlDates();
        if (!dates.length) return toast('No dates selected.', 'error');
        doGreenlight({ dates }, q('[data-gl-summary]'));
        break;
      }

      case 'gl-greenlight-all-paid':
        if (!confirm('Greenlight every completed title you have already paid for?')) return;
        doGreenlight({ all_paid: true }, q('[data-gl-summary]'));
        break;

      case 'gl-greenlight-range': {
        const start = q('[data-gl-start]').value;
        const end   = q('[data-gl-end]').value;
        if (!start || !end) return setStatus(q('[data-gl-range-status]'), 'Pick both dates.', 'error');
        doGreenlight({ start, end }, q('[data-gl-range-status]'));
        break;
      }

      case 'titles-load': loadTitles(); break;
      case 'titles-select-page': {
        const pageIds = titlesState.rows.filter((r) => r.actionable !== false).map((r) => r.id);
        pageIds.forEach((id) => titlesState.selected.add(id));
        renderTitlesSelection();
        break;
      }
      case 'titles-select-all': selectAllMatching(); break;
      case 'titles-select-none':
        titlesState.selected.clear();
        renderTitlesSelection();
        break;
      case 'titles-greenlight': greenlightSelected(); break;
      case 'titles-ungreenlight': ungreenlightSelected(); break;
      case 'titles-first': titlesGoto(1); break;
      case 'titles-prev': titlesGoto(titlesState.page - 1); break;
      case 'titles-next': titlesGoto(titlesState.page + 1); break;
      case 'titles-last': titlesGoto(titlesState.pages); break;

      case 'save-settings': saveGroup(btn.dataset.group); break;
      case 'save-selectors': saveSelectors(); break;
      case 'save-timings': saveTimings(); break;
      case 'save-script': saveScript(); break;
      case 'script-preview': previewScript(); break;
      case 'script-reset': resetScript(); break;

      case 'selectors-reset':
        if (!confirm('Reset every selector to the built-in defaults?')) return;
        try {
          await postJSON(API + '/settings/reset',
            { key: 'selectors', scope: 'global', project_id: projectId });
          await loadSettings();
          toast('Selectors reset.');
        } catch (e) { toast(e.message, 'error'); }
        break;

      case 'account-new': openAccountModal(null); break;
      case 'account-cancel': q('[data-account-modal]').hidden = true; break;
      case 'account-save': saveAccount(); break;

      case 'node-new':
        q('[data-node-modal]').hidden = false;
        q('[data-node-token-box]').hidden = true;
        q('[data-node-name]').value = '';
        setStatus(q('[data-node-status]'), '');
        break;
      case 'node-cancel': q('[data-node-modal]').hidden = true; break;
      case 'node-save': saveNode(); break;

      case 'test-download': {
        const id = q('[data-test-master-id]').value;
        if (!id) return setStatus(q('[data-test-status="download"]'), 'Enter a title id.', 'error');
        runTest('download', { master_id: parseInt(id, 10) }, q('[data-test-status="download"]'));
        break;
      }
      case 'test-process': {
        const id = q('[data-test-poster-id]').value;
        if (!id) return setStatus(q('[data-test-status="process"]'), 'Enter a poster id.', 'error');
        runTest('process', { poster_id: parseInt(id, 10) }, q('[data-test-status="process"]'));
        break;
      }
      case 'test-upload': {
        const poster = q('[data-test-upload-poster]').value;
        const acc    = q('[data-test-upload-account]').value;
        const statusEl = q('[data-test-status="upload"]');
        if (!poster || !acc) return setStatus(statusEl, 'Pick a poster id and an account.', 'error');
        runTest('upload', {
          poster_id: parseInt(poster, 10), account_id: parseInt(acc, 10),
        }, statusEl);
        break;
      }

      case 'failures-load': loadFailures(); break;
      case 'failures-retry': failuresAction('retry'); break;
      case 'failures-skip': failuresAction('skip'); break;
      case 'failures-removed': failuresAction('removed'); break;

      case 'shot-close': q('[data-shot-lightbox]').hidden = true; break;
    }
  });

  q('[data-failures-kind]').addEventListener('change', loadFailures);
  q('[data-titles-q]').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') loadTitles();
  });
  // Changing a filter reloads immediately — hunting for the LOAD button after
  // every dropdown change was the main friction in the first version.
  ['[data-titles-status]', '[data-titles-pagesize]',
   '[data-titles-from]', '[data-titles-to]'].forEach((sel) => {
    const el = q(sel);
    if (el) el.addEventListener('change', () => loadTitles());
  });

  // Close overlays with Escape — expected everywhere else in the app.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    ['[data-shot-lightbox]', '[data-account-modal]', '[data-node-modal]'].forEach((sel) => {
      const el = q(sel);
      if (el && !el.hidden) el.hidden = true;
    });
  });

  // ── Boot ─────────────────────────────────────────────────────────────────
  // The URL wins, then the last-visited section, then Overview.
  let initial = 'overview';
  const fromHash = (location.hash || '').replace('#', '');
  if (fromHash && q(`[data-section-panel="${fromHash}"]`)) {
    initial = fromHash;
  } else {
    try {
      const saved = sessionStorage.getItem('pipe-section');
      if (saved && q(`[data-section-panel="${saved}"]`)) initial = saved;
    } catch (e) {}
  }

  // Back/forward between sections.
  window.addEventListener('popstate', () => {
    const name = (location.hash || '').replace('#', '') || 'overview';
    if (q(`[data-section-panel="${name}"]`)) showSection(name, { silent: true });
  });

  // Overview and settings both load up front.
  //
  // Settings are fetched eagerly because they populate FOUR separate sections
  // (Processing, Storage, Upload, Templates) plus the selector and timing
  // grids. Loading them lazily per-section meant the Upload tab appeared empty
  // until you had visited Processing first — the forms were there, just never
  // filled in. One extra request at boot removes that whole class of confusion.
  loaded.overview = true;
  loaded.processing = true;

  Promise.all([
    loadOverview().catch((e) => toast('Overview: ' + e.message, 'error')),
    loadSettings().catch((e) => { loaded.processing = false; }),
  ]).then(() => {
    showSection(initial, { silent: true });
  });

  // Refresh the overview periodically so quota and node health stay honest
  // while the tab sits open — but only while it's the visible section.
  // Refresh the overview while it's the visible tab. Cadence adapts: 8s when
  // something is actually in flight so you can watch a batch move, 30s when
  // idle so an open tab isn't polling pointlessly all day.
  let overviewTick = 0;
  overviewTimer = setInterval(() => {
    const active = q('.pipe-tab.active');
    if (!active || active.dataset.section !== 'overview' || document.hidden) return;
    const busy = overview && (overview.in_flight || []).length > 0;
    overviewTick += 1;
    if (busy || overviewTick % 4 === 0) loadOverview();
  }, 8000);
})();
