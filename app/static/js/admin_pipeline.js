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
    // TWO MACHINES, ONE ARCHIVE. The Windows node writes through a mounted
    // drive letter; this server has no such drive and pushes over SFTP. Both
    // end up at the same relative path on the same Storage Box, which is why
    // storage_path in the database means the same thing either way.
    storage: [
      ['storage_root',   'text', 'Drive on the worker node', 'Used by the PHOTOSHOP stage, which runs on the Windows box. Typically S: — the mounted Storage Box. Database paths are relative to this, so remounting elsewhere only needs a change here.'],
      ['storage_layout', 'text', 'Path layout',            'Shared by both machines. Variables: {site} {project} {date} {title_folder} {filename} {username} {external_id}.'],
    ],
    storage_sftp: [
      ['storage_sftp_host',     'text',     'Storage Box host',  'Used by stages that run on THIS server (GPT generation), which has no mapped drive. e.g. u642720.your-storagebox.de. Leave blank to write to a local folder instead — useful for testing without the box.'],
      ['storage_sftp_port',     'number',   'Port',              'Hetzner Storage Boxes use 23 for SFTP, not the usual 22.'],
      ['storage_sftp_user',     'text',     'Username',          'Same as the box name, e.g. u642720.'],
      ['storage_sftp_password', 'password', 'Password',          'Stored encrypted, like the marketplace passwords.'],
      ['storage_sftp_root',     'text',     'Subfolder on the box', 'Normally blank — paths are then relative to the box root, matching what the node sees at its drive letter.'],
      ['storage_local_root',    'text',     'Local fallback folder', 'Where this server writes when no host is set. Fine for a first test; not where a real archive should live.'],
    ],
    // ── GPT projects ──────────────────────────────────────────────────
    // Only rendered when the project declares processor = 'gpt'; the
    // template omits the containers entirely otherwise, and
    // renderSettingsGroup() skips a group whose container isn't present.
    gpt: [
      ['openai_model',    'text',   'Model',    'gpt-image-2 unless you have a reason.'],
      ['openai_size',     'select', 'Size',     'auto lets the model choose a ratio to suit the photo. Larger sizes cost proportionally more.', ['auto', '1024x1024', '1024x1536', '1536x1024']],
      ['openai_quality',  'select', 'Quality',  'low is roughly a fifth the price of medium and is upscaled afterwards anyway.', ['auto', 'low', 'medium', 'high']],
      ['gpt_review_required', 'bool', 'Review images before upload', 'On, every generated image waits for you on the Review Images tab. Off, they go straight to the upload queue. Turning it OFF does not release what is already waiting — those still need approving, so nothing is ever listed that you never looked at.'],
    ],
    upscale: [
      ['upscale_width_px',  'number', 'Output width (px)', 'The processed image is resized to this width; height scales in proportion, so 1000x2000 becomes 4000x8000. Lanczos resampling.'],
      ['upscale_sharpen',   'number', 'Sharpening (0-100)','Applied after the upscale. 0 is off. Raise it slowly and judge on a real print — sharpening artefacts are baked in and the review gate is your only chance to catch them.'],
      ['upscale_jpeg_quality','number','JPEG quality (1-100)','Quality of the saved print file. 92 is visually lossless for photographic work; higher mostly buys file size.'],
    ],
    // Credentials live in their OWN panel. They were originally inside the
    // SPENDING group, where nobody found them — "where do I put my API key"
    // is not a question anyone should have to ask twice.
    keys: [
      ['brave_api_key_free', 'password','Brave key — free plan', 'Used for NORMAL searches. 1 request/second, 2,000 a month.'],
      ['brave_api_key_paid', 'password','Brave key — paid plan', 'Used for DEEP searches, which fire two queries at once and would trip the free key\'s 1/second limit. Also the fallback when the free quota runs out.'],
      ['openai_api_key',     'password','OpenAI key',            'Generates the images.'],
      ['openai_admin_key',   'password','OpenAI admin key',      "Optional, and a DIFFERENT key from the one above — an admin key (sk-admin-...). Used once a night to compare OpenAI's own billing against what we calculated, and nothing else; image generation never touches it. Leave blank and the cross-check is simply skipped."],
    ],
    spend: [
      ['spend_cap_usd_month','number','Monthly cap (USD)', '0 disables the cap. Counted from the token usage each API call reports.'],
      ['spend_cap_action',   'select','When the cap is hit','warn posts a dashboard alert. pause also stops dispatching new work.', ['warn', 'pause']],
      ['brave_daily_query_cap','number','Brave daily query cap', '0 is off. A safety net against a bug looping, not a budget — Brave costs about half a cent a query.'],
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
      // The nightly earnings check. Here rather than on the Earnings page
      // because what it CHANGES is when the pipeline hands out work, and this
      // is the page that governs that.
      ['earnings_quiet_from', 'text',   'Go quiet at (HH:MM)', "Each night from this time the pipeline stops handing out NEW work until that night's earnings check has been dealt with. Anything already running finishes normally. It reopens on its own — when the check finishes, when it fails, or at midnight if the worker machine was off. Blank turns the quiet time off entirely. Node local time."],
      ['earnings_run_at',     'text',   'Check earnings at (HH:MM)', 'When the nightly marketplace read is queued. Normally the same as the quiet time. Set both to a few minutes from now to watch the whole thing happen instead of waiting until tonight.'],
      ['earnings_max_pages_per_run', 'number', 'Max pages per account per run', "Ceiling on how many ledger pages one account reads in one go. A normal night touches one or two; only a first-ever read walks a long history, and this stops that one account eating the whole quiet window. It picks up where it left off the next night on its own."],
    ],
    templates: [
      ['title_template',      'text',     'Listing title',      'Variables: {title} {letter} {index} {external_id} — plus {year} and {content_type} where the project has them (this one may not; check the Title List). {letter} is the per-image A/B/C suffix. Output is folded to what the marketplace accepts, automatically.'],
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
    // NOTE: never actually called — boot sets loaded.processing = true and
    // fetches these eagerly, because the settings feed four sections at once.
    // Kept so the map stays a complete description of each section, and so
    // that reaching this section by any other route still works.
    processing: async () => { await loadSettings(); await loadSpend(); },
    upload:     loadUploadSection,
    test:       loadTestSection,
    attention:  loadAttention,
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
      // Reported to the caller, not just to the screen. The auto-refresh needs
      // to KNOW this failed so it can back off; swallowing it here meant the
      // timer kept firing every 3s at a server that was already struggling.
      return false;
    }
    projectId = overview.project.id;
    renderFunnel();
    renderInFlight();
    renderAccountQuotas();
    renderJobs();
    renderNodes();
    renderHistoryChart();
    renderBadges();
    renderRunMode();

    const modeSel = q('[data-greenlight-mode]');
    if (modeSel) modeSel.value = overview.greenlight_mode;
    return true;
  }

  // ── Stopped on purpose ──────────────────────────────────────────────
  // Rendered from the overview payload so it stays honest as the page
  // polls: while draining, the count of work still in flight ticks down
  // and you can see the queue actually emptying rather than guessing.
  function renderRunMode() {
    const box = q('[data-run-banner]');
    if (!box) return;
    const rm = (overview && overview.run_mode) || { running: true };
    if (rm.running) { box.hidden = true; return; }

    const flying = (overview.in_flight || []).length;
    const quiet = rm.quiet || {};
    box.hidden = false;

    // The nightly earnings window is not a fault and must never read like
    // one. Work going quiet with no explanation is exactly what sent you
    // hunting for a bug when an account had simply been paused.
    // RESUME sets run_mode back to 'run' — but during quiet time run_mode is
    // ALREADY 'run', so the button would do nothing at all. A control that
    // cannot act is not a label problem; it is hidden.
    const resumeBtn = q('[data-action="run-resume"]');
    if (resumeBtn) resumeBtn.hidden = !!quiet.blocking;

    if (quiet.blocking) {
      q('[data-run-title]').textContent = 'QUIET TIME — CHECKING EARNINGS';
      q('[data-run-detail]').textContent =
        `Since ${quiet.starts_at}. Nothing new is being handed out while the `
        + `marketplaces are read; it resumes on its own as soon as that `
        + `finishes. ` + (flying
            ? `${flying} item(s) already started are still finishing.`
            : 'Nothing is in flight.');
      return;
    }

    q('[data-run-title]').textContent =
      rm.mode === 'halt' ? 'PIPELINE HALTED' : 'PAUSED — DRAINING';
    q('[data-run-detail]').textContent =
      (rm.reason ? rm.reason + ' · ' : '')
      + (flying
          ? `${flying} item(s) still finishing — nothing new is being started.`
          : 'Nothing in flight. Safe to reboot the node or deploy.');
  }

  function renderBadges() {
    const glBadge = q('[data-badge="greenlight"]');
    const atBadge = q('[data-badge="attention"]');
    const glCount = (overview.funnel.awaiting_greenlight || 0);
    // Computed server-side so the badge and the tab agree. Counting only
    // 'failed' rows here would show 0 while a stalled node held work.
    const atCount = overview.attention != null
      ? overview.attention
      : (overview.failures.processing || 0) + (overview.failures.upload || 0);
    if (glBadge) { glBadge.textContent = glCount; glBadge.hidden = glCount === 0; }
    if (atBadge) { atBadge.textContent = atCount; atBadge.hidden = atCount === 0; }
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
          ${a.banned ? `<div class="quota-note">
              <span class="quota-note-tag">BANNED ${esc((a.banned_at || '').slice(0, 10))}</span>
              ${esc(a.banned_reason || '')}
              ${a.replaced_by_id
                ? ' · catalogue moved to ' + esc((accounts.find((x) => x.id === a.replaced_by_id) || {}).name || ('#' + a.replaced_by_id))
                : ' · <strong>its listings are gone and have not been rebuilt anywhere</strong>'}
            </div>` : ''}
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
            <td><button class="btn btn-ghost btn-tiny" data-open-job="${j.id}">LOG</button>
              ${['done', 'error', 'cancelled'].includes(j.status) ? ''
                : ` <button class="btn btn-ghost btn-tiny" data-cancel-job="${j.id}">CANCEL</button>`}
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;
    el.querySelectorAll('[data-open-job]').forEach((b) => {
      b.addEventListener('click', () => openConsole(parseInt(b.dataset.openJob, 10)));
    });
    // ── THE CANCEL BUTTON THAT WAS NEVER THERE ─────────────────────────
    //
    // The endpoint has existed since jobs did, and NOTHING ever called it —
    // so the only way to stop a queued job was the browser console. That
    // mattered on the day a stopped sweep left two accounts' worth of
    // switching queued and there was no way to reach it from the screen.
    // Zero callers of a working endpoint is a defect, not a style question.
    el.querySelectorAll('[data-cancel-job]').forEach((b) => {
      b.addEventListener('click', async () => {
        const id = parseInt(b.dataset.cancelJob, 10);
        if (!confirm('Cancel job #' + id + '?\n\nA job that has not started '
                     + 'yet will never start. One already running stops when '
                     + 'it next reports in, which can take a minute.')) return;
        b.disabled = true;
        try {
          await postJSON(`${API}/jobs/${id}/cancel`, {});
          await loadOverview();
          renderJobs();
        } catch (err) {
          b.disabled = false;
          alert('Could not cancel: ' + err.message);
        }
      });
    });
  }

  function jobTone(status) {
    return status === 'done' ? 'complete'
         : status === 'error' ? 'error'
         : status === 'running' ? 'in-progress' : 'pending';
  }

  // How long since a node last spoke, in words.
  //
  // ONLINE only means "seen in the last five minutes" — a window that stops
  // the label flickering between 30-second polls. The side effect is that a
  // node which died two minutes ago still reads ONLINE beside a clock time
  // you have to subtract in your head. This says the age out loud, and turns
  // it amber once a healthy node would have checked in twice over, so a
  // machine going quiet is visible well before the label admits it.
  function nodeAge(n) {
    if (n.last_seen_age_s == null) return '';
    const s = n.last_seen_age_s;
    const txt = s < 90 ? `${s}s ago`
              : s < 5400 ? `${Math.round(s / 60)}m ago`
              : `${Math.round(s / 3600)}h ago`;
    // The agent polls every 30s; anything past ~2 minutes is a missed beat.
    const late = s > 120;
    return `<span class="node-age mono ${late ? 'is-late' : ''}">${txt}</span>`;
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
              ${nodeAge(n)}
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

  // A number from a field, falling back ONLY when it is blank or unreadable.
  // `parseInt(x) || fallback` cannot tell a real zero from an empty box, and
  // for a daily upload limit those two mean opposite things.
  function numOr(selector, fallback) {
    var el = q(selector);
    var n = parseInt((el && el.value) || '', 10);
    return isNaN(n) ? fallback : n;
  }

  async function loadTitles(resetPage) {
    if (resetPage !== false) titlesState.page = 1;
    // A page size of zero would show nothing, so the fallback is right here —
    // written explicitly so it reads as a decision rather than an accident.
    titlesState.pageSize = Math.max(1, numOr('[data-titles-pagesize]', 100));

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
      const ov = settings.overrides[key] || {};
      const modified = ov.global || ov.project;
      // A secret is never sent to the browser, so "is it set" has to be told
      // to us separately — otherwise an empty box is ambiguous between "no
      // key" and "key hidden".
      const secretSet = ov.has_value === true;
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
        input = `<input type="${type}" data-setting="${key}"
                   value="${type === 'password' ? '' : esc(value)}"
                   ${type === 'password' ? 'placeholder="leave blank to keep the saved value" autocomplete="new-password"' : ''}
                   ${type === 'number' ? 'step="any"' : ''}>`;
      }
      return `
        <div class="setting-field ${type === 'bool' ? 'setting-bool' : ''}">
          <label>
            <span class="setting-label">
              ${esc(label)}
              ${type === 'password'
                  ? (secretSet
                      ? '<span class="setting-badge" title="A value is stored. Leave blank to keep it.">saved</span>'
                      : '<span class="setting-badge setting-badge-empty" title="No value stored yet.">not set</span>')
                  : (modified ? '<span class="setting-badge" title="Overridden — not the code default">set</span>' : '')}
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

  // ── WHICH SCOPE A SAVE WRITES TO ─────────────────────────────────────────
  //
  // Everything on this page is edited while standing INSIDE a project, so a
  // save writes a project-scoped override. It used to send scope:'global'
  // for everything, which produced a save that silently did nothing:
  // sync_projects() had already written pipeline.musik.title_template, the
  // save wrote pipeline.title_template, and the project override still won
  // on read. The field reverted on refresh with no error anywhere.
  //
  // MARKETPLACE_WIDE is the exception. Selectors and timings describe
  // FineArtAmerica's DOM, not a niche — when the site moves a button, it
  // moves for every project, and fixing it once should fix it everywhere.
  const MARKETPLACE_WIDE = new Set(['selectors', 'timings']);

  function scopeFor(key) {
    return MARKETPLACE_WIDE.has(key) ? 'global' : 'project';
  }

  async function saveGroup(group) {
    const statusEl = q(`[data-settings-status="${group}"]`);
    setStatus(statusEl, 'Saving…');
    try {
      const d = await postJSON(API + '/settings', {
        settings: collectGroup(group),
        project_id: projectId,
        scope: 'project',
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
        // Marketplace-wide on purpose — see MARKETPLACE_WIDE above.
        settings: { selectors: map }, project_id: projectId, scope: scopeFor('selectors'),
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
        settings: { timings: map }, project_id: projectId, scope: scopeFor('timings'),
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
        project_id: projectId, scope: 'project',
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
        { key: 'process_script', scope: 'project', project_id: projectId });
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
            ${a.banned ? '<span class="status-pill status-error">BANNED</span>' : ''}
            ${a.is_enabled || a.banned ? '' : '<span class="status-pill">DISABLED</span>'}
            ${a.available || a.banned ? '' : '<span class="status-pill status-error">PAUSED</span>'}
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
          ${a.banned ? `<div class="quota-note">
              <span class="quota-note-tag">BANNED ${esc((a.banned_at || '').slice(0, 10))}</span>
              ${esc(a.banned_reason || '')}
              ${a.replaced_by_id
                ? ' · catalogue moved to ' + esc((accounts.find((x) => x.id === a.replaced_by_id) || {}).name || ('#' + a.replaced_by_id))
                : ' · <strong>its listings are gone and have not been rebuilt anywhere</strong>'}
            </div>` : ''}
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
            ${a.banned
              ? `<button class="btn btn-accent btn-tiny" data-acc-handover="${a.id}">HAND OVER TO…</button>`
              : `<button class="btn btn-error btn-tiny" data-acc-ban="${a.id}">MARK BANNED</button>`}
            ${(a.project_ids || []).length > 1
              ? `<button class="btn btn-ghost btn-tiny" data-acc-detach="${a.id}"
                   title="This account also serves ${(a.project_ids || []).length - 1} other project(s), so it cannot be deleted from here — that would stop them uploading too. Remove it from each project first; the last one offers DELETE.">REMOVE FROM THIS PROJECT</button>`
              : `<button class="btn btn-error btn-tiny" data-acc-delete="${a.id}">DELETE</button>`}
          </div>
        </div>`;
    }).join('');

    // DELETE is offered only when this project is the account's last one.
    // Otherwise the honest action is REMOVE FROM THIS PROJECT — deleting a
    // shared account here would silently stop the OTHER niche uploading,
    // which is a per-project screen doing damage outside its own project.
    el.querySelectorAll('[data-acc-detach]').forEach((b) =>
      b.addEventListener('click', async () => {
        const acc = accounts.find((a) => a.id === parseInt(b.dataset.accDetach, 10)) || {};
        if (!confirm(`Stop uploading this project's work through "${acc.name}"?\n\n`
          + 'The account, its password and its whole upload history stay exactly '
          + 'as they are, and other projects keep using it. Anything already '
          + 'queued for this project is left in place, it just stops being sent.')) return;
        try {
          const res = await postJSON(`${API}/accounts/${acc.id}/detach`, {});
          toast(res.left_queued
            ? `Removed. ${res.left_queued} queued item(s) left untouched.`
            : 'Removed from this project.');
          await loadAccounts();
        } catch (e) { toast(e.message, 'error'); }
      }));

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

    // ── Banned ──────────────────────────────────────────────────────────
    // Two steps, never one. Banning is a fact to record now; choosing where
    // the catalogue goes is a decision, and the replacement account usually
    // does not exist yet at the moment a ban is discovered.
    el.querySelectorAll('[data-acc-ban]').forEach((b) =>
      b.addEventListener('click', async () => {
        const acc = accounts.find((a) => a.id === parseInt(b.dataset.accBan, 10)) || {};
        const live = (acc.stats || {}).uploaded || 0;
        const reason = prompt(
          `Mark ${acc.name} as BANNED by the marketplace?\n\n`
          + `This records that its ${live} live listing(s) no longer exist, so they `
          + `can be rebuilt on another account. Nothing is deleted and no files are `
          + `touched.\n\nWhy was it banned? (kept permanently)`);
        if (reason === null || !reason.trim()) return;
        try {
          const d = await postJSON(`${API}/accounts/${acc.id}/ban`, { reason: reason.trim() });
          toast(`${d.account} marked banned — ${d.listings_lost} listing(s) written off, `
                + `${d.images_needing_relisting} image(s) need re-listing.`);
          loadAccounts(); loadOverview();
        } catch (e) { toast(e.message, 'error'); }
      }));

    el.querySelectorAll('[data-acc-handover]').forEach((b) =>
      b.addEventListener('click', async () => {
        const dead = accounts.find((a) => a.id === parseInt(b.dataset.accHandover, 10)) || {};
        // Only accounts that could actually take the work: same project,
        // not itself, not also banned.
        const options = accounts.filter((a) =>
          a.id !== dead.id && !a.banned && a.project_id === dead.project_id);
        if (!options.length) {
          toast('No other account in this project to hand over to. Add one first.', 'error');
          return;
        }
        const list = options.map((a, i) => `${i + 1}. ${a.name}`).join('\n');
        const pick = prompt(
          `Rebuild ${dead.name}'s catalogue on which account?\n\n${list}\n\n`
          + `Enter a number. Everything it had listed is queued for upload there; `
          + `nothing is uploaded immediately.`);
        if (pick === null) return;
        const chosen = options[parseInt(pick, 10) - 1];
        if (!chosen) return toast('No account picked.', 'error');
        try {
          const d = await postJSON(`${API}/accounts/${dead.id}/handover`,
                                   { replacement_id: chosen.id });
          toast(`${d.queued} image(s) queued on ${chosen.name}.`);
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

  // ── Attaching an account that already exists ──────────────────────────
  //
  // One FineArtAmerica account carries both niches. Creating it twice gave
  // two Chrome profiles, two copies of the password, and a daily upload
  // limit the marketplace applies ONCE being counted as two — so this list
  // is the normal path for FAA, and ADD NEW is the exception.
  async function toggleAttachPanel() {
    const panel = q('[data-attach-panel]');
    panel.hidden = !panel.hidden;
    if (panel.hidden) return;
    await loadAttachable();
  }

  async function loadAttachable() {
    const box = q('[data-attach-list]');
    box.innerHTML = 'Loading…';
    try {
      const data = await getJSON(`${API}/accounts/available`);
      const rows = data.accounts || [];
      if (!rows.length) {
        box.innerHTML = '<span class="muted">Every account you have is already '
          + 'attached to this project. Use ADD NEW to create another.</span>';
        return;
      }
      box.innerHTML = rows.map((a) => `
        <div class="attach-row" style="padding:6px 0">
          <strong>${esc(a.name)}</strong>
          <span class="muted">· ${esc(a.target_site)} · ${esc(a.email)}</span>
          ${a.used_by && a.used_by.length
            ? `<span class="muted"> · already used by ${esc(a.used_by.join(', '))}</span>`
            : '<span class="muted"> · not used by any project</span>'}
          ${a.banned ? '<span class="muted"> · BANNED</span>' : ''}
          ${a.banned ? '' :
            `<button class="btn btn-accent btn-tiny" data-acc-attach="${a.id}"
               style="margin-left:8px">ATTACH</button>`}
        </div>`).join('');

      box.querySelectorAll('[data-acc-attach]').forEach((b) =>
        b.addEventListener('click', async () => {
          b.disabled = true;
          try {
            await postJSON(`${API}/accounts/${b.dataset.accAttach}/attach`, {});
            toast('Account attached to this project.');
            await loadAttachable();
            await loadAccounts();
          } catch (e) {
            b.disabled = false;
            toast(e.message, 'error');
          }
        }));
    } catch (e) {
      box.innerHTML = `<span class="muted">Could not load: ${esc(e.message)}</span>`;
    }
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
      // `|| 100` treated a deliberate ZERO as "not filled in". A daily limit
      // of 0 is how you say "do not upload to this account today", and it
      // became a hundred instead — on a real marketplace, silently. Only a
      // BLANK field should fall back to the default.
      daily_limit:        numOr('[data-account-limit]', 100),
      rotation_order:     numOr('[data-account-rotorder]', 100),
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

  // ── Spending ─────────────────────────────────────────────────────────────
  //
  // The headline is COST PER IMAGE, not the monthly total. The backlog is
  // counted in images, so "$0.021 each · 3,161 left · about $66" answers the
  // question actually being asked. A month-to-date figure on its own tells
  // you what has happened, not what is about to.

  // Every number is coerced. The panel reads values from an endpoint that
  // can legitimately return null (no cap set, no images yet, so no per-image
  // figure), and calling .toFixed on one of those throws mid-template — which
  // left the box showing "Loading…" for ever with nothing logged anywhere.
  // A panel that lies about its own state is worse than one that says it
  // failed.
  const num = (v) => (typeof v === 'number' && isFinite(v) ? v : 0);

  // OpenAI's own figure next to ours.
  //
  // Shown as a cross-check, never as a correction. Their number covers the
  // whole organisation and lags; ours is per-image and immediate. They
  // answer different questions — the only interesting event is them
  // DISAGREEING, which usually means our hardcoded per-token prices have
  // gone stale after a price change.
  function renderReconcile(r) {
    if (!r) {
      return '<p class="muted">No admin key set, so OpenAI\'s own billing is not '
           + 'being cross-checked. Optional — add one under API KEYS if you want '
           + 'the monthly cap verified against their figures.</p>';
    }
    const ours = num(parseFloat(r.ours));
    const theirs = num(parseFloat(r.theirs));
    const gap = num(parseFloat(r.gap));
    const when = esc((r.checked_at || '').replace('T', ' '));
    if (!r.significant) {
      return `<p class="muted">OpenAI's own billing agrees: they report `
           + `$${theirs.toFixed(2)} this month against our $${ours.toFixed(2)}. `
           + `Checked ${when}.</p>`;
    }
    return `<p class="error-text"><strong>Our figure and OpenAI's disagree.</strong> `
         + `We metered $${ours.toFixed(2)}; OpenAI reports $${theirs.toFixed(2)} `
         + `(${gap > 0 ? '+' : ''}$${gap.toFixed(2)}). The usual cause is a price `
         + `change on their side, which makes our per-image cost — and therefore `
         + `the monthly cap — wrong until the rates in the code are updated. `
         + `Checked ${when}.</p>`;
  }

  async function loadSpend() {
    const box = q('[data-spend-summary]');
    if (!box) return;                       // not a project that spends money

    try {
      await renderSpend(box);
    } catch (e) {
      box.innerHTML = `<p class="muted">Could not load spending: ${esc(e.message)}</p>`;
      console.error('spend panel:', e);
    }
  }

  async function renderSpend(box) {
    const d = await getJSON(withProject(`${API}/spend`));
    const m = d.month || {};
    const spent   = num(m.spent);
    const cap     = num(m.cap);
    const images  = num(m.images);
    const backlog = num(m.backlog);
    const capped  = cap > 0;
    const pct     = capped ? Math.min(100, (spent / cap) * 100) : 0;

    box.innerHTML = `
      <div class="spend-figures">
        <div class="spend-fig">
          <span class="spend-num">$${spent.toFixed(2)}</span>
          <span class="spend-lbl">this month</span>
        </div>
        <div class="spend-fig">
          <span class="spend-num">${m.per_image != null ? '$' + num(m.per_image).toFixed(4) : '—'}</span>
          <span class="spend-lbl">per image</span>
        </div>
        <div class="spend-fig">
          <span class="spend-num">${images.toLocaleString()}</span>
          <span class="spend-lbl">images this month</span>
        </div>
        <div class="spend-fig">
          <span class="spend-num">${m.backlog_cost != null ? '$' + num(m.backlog_cost).toFixed(2) : '—'}</span>
          <span class="spend-lbl">to finish ${backlog.toLocaleString()} queued</span>
        </div>
      </div>
      ${capped ? `
        <div class="spend-bar" title="${spent.toFixed(2)} of ${cap.toFixed(2)}">
          <span style="width:${pct.toFixed(1)}%"
                class="${m.over ? 'is-over' : ''}"></span>
        </div>
        <p class="muted">
          $${spent.toFixed(2)} of the $${cap.toFixed(2)} monthly cap
          ${m.over
            ? (m.action === 'pause'
                ? '— <strong class="error-text">reached, so generation is paused</strong>'
                : '— <strong class="error-text">reached; generation continues because the action is set to warn</strong>')
            : ''}
        </p>`
        : '<p class="muted">No monthly cap set. Generation will keep running whatever it costs.</p>'}
      ${m.per_image != null
        ? '<p class="muted">Cost per image is measured from the token usage each call reports, '
          + 'and the finish-the-queue figure assumes the same rate holds — the model picks a size '
          + 'per photo, so treat it as a guide.</p>'
        : ''}
      ${renderReconcile(d.reconcile)}`;

    const daysEl = q('[data-spend-days]');
    if (!daysEl) return;
    const days = (d.days || []).filter((x) => x.total > 0);
    daysEl.innerHTML = !days.length
      ? '<p class="muted" style="margin-top:14px">Nothing spent in this period.</p>'
      : `<table class="data-table" style="margin-top:14px">
          <thead><tr><th>DAY</th><th>OPENAI</th><th>BRAVE</th><th>TOTAL</th><th>CALLS</th></tr></thead>
          <tbody>${days.map((x) => `
            <tr>
              <td class="mono">${esc(x.date)}</td>
              <td class="mono">$${num(x.openai).toFixed(4)}</td>
              <td class="mono">$${num(x.brave).toFixed(4)}</td>
              <td class="mono">$${num(x.total).toFixed(4)}</td>
              <td class="mono">${num(x.calls)}</td>
            </tr>`).join('')}
          </tbody></table>`;
  }

  // ── Generation test ──────────────────────────────────────────────────────
  //
  // Runs inline rather than through the job queue, because the stage itself
  // runs on the server. That means one request held open for ~60s, so the
  // button has to say so — a silent minute reads as a hang, and the natural
  // response to a hang is to press the button again and pay twice.

  async function runGptTest() {
    const idEl = q('[data-test-gpt-poster]');
    const statusEl = q('[data-test-status="gpt"]');
    const out = q('[data-test-gpt-result]');
    const id = parseInt(idEl.value, 10);
    if (!id) return setStatus(statusEl, 'Enter an image id.', 'error');

    const btn = q('[data-action="test-gpt"]');
    btn.disabled = true;
    setStatus(statusEl, 'generating — this takes about a minute…');
    out.hidden = true;

    const started = Date.now();
    const tick = setInterval(() => setStatus(statusEl,
      `generating — ${Math.round((Date.now() - started) / 1000)}s elapsed…`), 1000);

    try {
      // project_id goes in the BODY. withProject() puts it in the query
      // string, which POST handlers here read from the payload — it would be
      // silently ignored and the call would fall back to the active project.
      const d = await postJSON(`${API}/test/gpt_process`,
                               { poster_id: id, project_id: projectId || null });
      clearInterval(tick);

      if (!d.ok) {
        // A refusal is a RESULT, not an error: the request worked and the
        // model declined. Shown in full, with the policy categories, because
        // that is the thing you are trying to learn from the test.
        setStatus(statusEl, d.fatal ? 'refused' : 'failed, but retryable', 'error');
        out.hidden = false;
        out.innerHTML = `
          <p class="attn-why">${esc(d.error || 'No detail returned.')}</p>
          ${(d.categories && d.categories.length)
            ? '<p>' + d.categories.map((c) => `<span class="status-pill status-error">${esc(c)}</span>`).join(' ') + '</p>'
            : ''}
          <pre class="pipe-log">${esc((d.log || []).join('\n'))}</pre>`;
        return;
      }

      setStatus(statusEl, `done in ${Math.round(d.total_ms / 1000)}s`, 'ok');
      out.hidden = false;
      out.innerHTML = `
        <div class="filter-row" style="gap:14px;margin-bottom:10px">
          <span class="mono">${d.width}×${d.height}</span>
          <span class="mono">${(d.bytes / 1024 / 1024).toFixed(2)} MB</span>
          <span class="mono">$${d.cost_usd}</span>
          <span class="muted mono">${d.input_tokens || 0} in / ${d.output_tokens || 0} out</span>
          ${d.stored ? '' : '<span class="status-pill status-error">NOT STORED</span>'}
        </div>
        ${d.preview_path
          ? `<img class="pipe-test-img" alt="Test output"
                  src="${API}/test/image?path=${encodeURIComponent(d.preview_path)}&t=${Date.now()}">`
          : '<p class="muted">No preview — the image could not be written to storage.</p>'}
        <pre class="pipe-log">${esc((d.log || []).join('\n'))}</pre>`;
    } catch (e) {
      clearInterval(tick);
      setStatus(statusEl, e.message, 'error');
    } finally {
      clearInterval(tick);
      btn.disabled = false;
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  NEEDS ATTENTION
  // ═══════════════════════════════════════════════════════════════════════
  //
  // Findings, not a table dump. Each group is rendered from what the server
  // says about it — label, why it matters, what to press — so adding a check
  // is a server change only. Nothing here knows what GPT or Photoshop is.
  //
  // The action buttons a group offers come from its key. A group that should
  // not offer RETRY simply is not given one: 'title_held' rows would fail
  // identically on retry, and offering the button would teach you to press
  // something that cannot work.

  let attention = [];

  const ATTENTION_ACTIONS = {
    rejected:      ['return_to_worker', 'unusable'],
    process_failed:['retry_process', 'return_to_worker', 'unusable'],
    config_blocked:['retry_process_all'],
    title_held:    ['retitle'],
    upload_failed: ['retry_upload', 'mark_removed', 'skip_upload'],
    stalled:       ['release'],
    unusable:      ['return_to_pipeline'],
    short_titles:  [],
    spend_capped:  [],
    generation_stopped: [],
    spend_mismatch:     [],
    node_offline:       [],
  };

  const ACTION_LABEL = {
    retry_process:      'RETRY',
    retry_process_all:  'RETRY ALL AFFECTED',
    retry_upload:       'RETRY',
    return_to_worker:   'RETURN TO WORKER',
    unusable:           'MARK UNUSABLE',
    return_to_pipeline: 'RETURN TO PIPELINE',
    mark_removed:       'MARK REMOVED',
    skip_upload:        'SKIP PERMANENTLY',
    release:            'RELEASE CLAIM',
    retitle:            '',
  };

  const SEVERITY_PILL = {
    stop: '<span class="status-pill status-error">STOPPED</span>',
    warn: '',
    info: '<span class="status-pill">FYI</span>',
  };

  async function loadAttention() {
    const el = q('[data-attention-list]');
    el.innerHTML = '<div class="muted">Checking…</div>';

    let d;
    try {
      d = await getJSON(withProject(`${API}/attention`));
    } catch (e) {
      el.innerHTML = '<div class="muted">Could not load: ' + esc(e.message) + '</div>';
      return;
    }
    attention = d.findings || [];
    setStatus(q('[data-attention-checked]'), `checked ${d.checked_at}`);

    if (!attention.length) {
      // Worth saying plainly. An empty page is only reassuring if you know
      // what was actually looked at.
      el.innerHTML = `
        <p class="pipe-clear">Nothing needs attention in this project.</p>
        <p class="muted">Checked for: images the model refused, titles the
        marketplace would reject, failed uploads, work claimed but never
        finished, spending limits, and titles that will list with fewer
        images than planned.</p>`;
      return;
    }

    el.innerHTML = attention.map((f) => `
      <section class="attn-group attn-${esc(f.severity)}" data-attn-key="${esc(f.key)}">
        <div class="attn-head">
          <span class="attn-title">${esc(f.label)}
            ${SEVERITY_PILL[f.severity] || ''}
            <span class="attn-count mono">${f.count}</span>
          </span>
        </div>
        <p class="attn-why">${esc(f.why)}</p>
        <p class="attn-do"><strong>What to do:</strong> ${esc(f.action)}</p>
        ${renderAttentionItems(f)}
        ${f.note ? `<p class="muted mono">${esc(f.note)}</p>` : ''}
        ${renderAttentionButtons(f)}
      </section>`).join('');

    wireAttention(el);
  }

  function renderAttentionItems(f) {
    if (!f.items.length) return '';

    if (f.key === 'spend_capped') {
      const i = f.items[0];
      return `<p class="mono">$${esc(i.spent)} spent · $${esc(i.cap)} cap</p>`;
    }

    if (f.key === 'spend_mismatch') {
      const i = f.items[0] || {};
      return `<p class="mono">we metered $${esc(i.spent)} · OpenAI reports $${esc(i.cap)}</p>`;
    }

    // The generation worker's own state. There is nothing to tick or act on
    // here — the useful content is WHY it is stopped and whether it keeps
    // happening, so it renders as one line of facts.
    if (f.key === 'node_offline') {
      return `<table class="data-table">
        <thead><tr><th>MACHINE</th><th>LAST SEEN</th></tr></thead>
        <tbody>${f.items.map((i) => `
          <tr><td class="mono">${esc(i.name)}</td>
              <td class="mono">${esc(i.last_seen)}</td></tr>`).join('')}
        </tbody></table>`;
    }

    if (f.key === 'generation_stopped') {
      const i = f.items[0] || {};
      return `<p class="mono">${i.alive ? 'running but idle' : 'not running'}`
           + `${i.age_s != null ? ` · last activity ${i.age_s}s ago` : ''}`
           + ` · ${i.waiting || 0} waiting`
           + ` · ${i.processed || 0} generated since start`
           + `${i.restarts ? ` · ${i.restarts} restart(s)` : ''}</p>`;
    }

    if (f.key === 'config_blocked') {
      return `<table class="data-table"><tbody>${f.items.map((i) => `
        <tr><td class="mono">${i.count}</td>
            <td class="mono pipe-error-cell">${esc(i.error)}</td></tr>`).join('')}
      </tbody></table>`;
    }

    // Held titles get an editable field rather than a row, because the fix IS
    // the edit. The preview under it comes from the server so there is only
    // ever one implementation of what the marketplace keeps.
    if (f.key === 'title_held') {
      return `<table class="data-table">
        <thead><tr><th>ITEM</th><th>WHY</th><th>NEW TITLE</th><th></th></tr></thead>
        <tbody>${f.items.map((i) => `
          <tr data-attn-row="${i.tracking_id}">
            <td>${esc(i.title)}</td>
            <td class="mono pipe-error-cell">${esc(i.error)}</td>
            <td>
              <input type="text" class="attn-title-input" value="${esc(i.title)}"
                     data-attn-retitle="${i.tracking_id}">
              <span class="muted mono" data-attn-preview="${i.tracking_id}"></span>
            </td>
            <td><button class="btn btn-accent btn-tiny"
                        data-attn-save="${i.tracking_id}">SAVE &amp; QUEUE</button></td>
          </tr>`).join('')}
        </tbody></table>`;
    }

    if (f.key === 'short_titles') {
      return `<table class="data-table">
        <thead><tr><th>TITLE</th><th>WILL LIST</th></tr></thead>
        <tbody>${f.items.map((i) => `
          <tr><td>${esc(i.title)}</td>
              <td class="mono">${i.have} of ${i.expected}</td></tr>`).join('')}
        </tbody></table>`;
    }

    if (f.key === 'unusable') {
      return `<table class="data-table">
        <thead><tr><th style="width:34px"></th><th>TITLE</th><th>REASON</th><th>RETIRED</th></tr></thead>
        <tbody>${f.items.map((i) => `
          <tr>
            <td><input type="checkbox" data-attn-pick="${i.poster_id}" data-attn-kind="poster"></td>
            <td>${esc(i.title)}</td>
            <td>${esc(i.reason || '')}</td>
            <td class="mono">${esc(i.at || '')} ${esc(i.by || '')}</td>
          </tr>`).join('')}
        </tbody></table>`;
    }

    if (f.key === 'stalled') {
      return `<table class="data-table">
        <thead><tr><th style="width:34px"></th><th>TITLE</th><th>STAGE</th><th>MACHINE</th><th>HELD</th></tr></thead>
        <tbody>${f.items.map((i) => `
          <tr>
            <td><input type="checkbox"
                       data-attn-pick="${i.kind === 'poster' ? i.poster_id : i.tracking_id}"
                       data-attn-kind="${esc(i.kind)}"></td>
            <td>${esc(i.title)}</td>
            <td class="mono">${esc(i.stage)}</td>
            <td class="mono">${esc(i.node || '—')}</td>
            <td class="mono">${i.held_min} min</td>
          </tr>`).join('')}
        </tbody></table>`;
    }

    if (f.key === 'upload_failed') {
      return `<table class="data-table">
        <thead><tr><th style="width:34px"></th><th>LISTING TITLE</th><th>ACCOUNT</th><th>TRIES</th><th>ERROR</th><th>EVIDENCE</th></tr></thead>
        <tbody>${f.items.map((i) => `
          <tr>
            <td><input type="checkbox" data-attn-pick="${i.tracking_id}" data-attn-kind="tracking"></td>
            <td>${esc(i.remote_title || i.title)}</td>
            <td class="mono">${esc(i.account)}</td>
            <td class="mono">${i.attempts}${i.exhausted ? ' <span class="status-pill status-error">EXHAUSTED</span>' : ''}</td>
            <td class="mono pipe-error-cell">${esc(i.error || '')}</td>
            <td>${i.screenshot
                  ? `<button class="btn btn-ghost btn-tiny" data-shot="${esc(i.screenshot)}" data-shot-name="${esc(i.remote_title || '')}">SCREENSHOT</button>`
                  : '<span class="muted">—</span>'}</td>
          </tr>`).join('')}
        </tbody></table>`;
    }

    // Default: a poster-level problem (rejected, process_failed).
    return `<table class="data-table">
      <thead><tr><th style="width:34px"></th><th>TITLE</th><th>FILE</th><th>TRIES</th><th>WHAT THE STAGE SAID</th></tr></thead>
      <tbody>${f.items.map((i) => `
        <tr>
          <td><input type="checkbox" data-attn-pick="${i.poster_id}" data-attn-kind="poster"></td>
          <td>${esc(i.title)}</td>
          <td class="mono">${esc(i.filename || '')}</td>
          <td class="mono">${i.attempts == null ? '' : i.attempts}</td>
          <td class="mono pipe-error-cell">
            ${(i.categories && i.categories.length)
              ? i.categories.map((c) => `<span class="status-pill status-error">${esc(c)}</span>`).join(' ') + ' '
              : ''}${esc(i.error || '')}</td>
        </tr>`).join('')}
      </tbody></table>`;
  }

  function renderAttentionButtons(f) {
    const acts = (ATTENTION_ACTIONS[f.key] || []).filter((a) => ACTION_LABEL[a]);
    if (!acts.length) return '';
    return `<div class="filter-row attn-actions">
      ${acts.map((a) => `<button class="btn ${a.startsWith('retry') ? 'btn-accent' : 'btn-ghost'} btn-tiny"
                                 data-attn-action="${a}" data-attn-group="${esc(f.key)}">${ACTION_LABEL[a]}</button>`).join('')}
      <span class="muted">applies to the ticked rows</span>
    </div>`;
  }

  function wireAttention(root_) {
    root_.querySelectorAll('[data-shot]').forEach((b) =>
      b.addEventListener('click', () => {
        q('[data-shot-img]').src = `${API}/artifact?path=${encodeURIComponent(b.dataset.shot)}`;
        q('[data-shot-title]').textContent = b.dataset.shotName || b.dataset.shot;
        q('[data-shot-lightbox]').hidden = false;
      }));

    // Live preview of what the marketplace would actually store. Debounced,
    // because this is a keystroke handler hitting the server.
    root_.querySelectorAll('[data-attn-retitle]').forEach((input) => {
      let timer = null;
      const preview = root_.querySelector(`[data-attn-preview="${input.dataset.attnRetitle}"]`);
      const run = async () => {
        try {
          const d = await postJSON(API + '/attention/preview_title', { title: input.value });
          preview.textContent = d.problem ? `✕ ${d.problem}` : `→ ${d.rendered}  (${d.length})`;
          preview.style.color = d.problem ? 'var(--error)' : 'var(--success)';
        } catch (e) { preview.textContent = ''; }
      };
      input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(run, 300);
      });
      run();
    });

    root_.querySelectorAll('[data-attn-save]').forEach((b) =>
      b.addEventListener('click', async () => {
        const id = parseInt(b.dataset.attnSave, 10);
        const input = root_.querySelector(`[data-attn-retitle="${id}"]`);
        try {
          const d = await postJSON(API + '/attention/retitle',
                                   { tracking_id: id, title: input.value });
          toast(`Queued as "${d.remote_title}".`);
          loadAttention();
          loadOverview();
        } catch (e) { toast(e.message, 'error'); }
      }));

    root_.querySelectorAll('[data-attn-action]').forEach((b) =>
      b.addEventListener('click', () => attentionAction(b.dataset.attnAction,
                                                        b.dataset.attnGroup)));
  }

  function attentionPicks(groupKey) {
    const group = document.querySelector(`[data-attn-key="${groupKey}"]`);
    if (!group) return { posters: [], trackings: [] };
    const picks = Array.from(group.querySelectorAll('[data-attn-pick]:checked'));
    return {
      posters:   picks.filter((c) => c.dataset.attnKind === 'poster')
                      .map((c) => parseInt(c.dataset.attnPick, 10)),
      trackings: picks.filter((c) => c.dataset.attnKind === 'tracking')
                      .map((c) => parseInt(c.dataset.attnPick, 10)),
    };
  }

  async function attentionAction(action, groupKey) {
    const { posters, trackings } = attentionPicks(groupKey);
    const group = attention.find((f) => f.key === groupKey) || { items: [] };

    try {
      if (action === 'retry_process_all') {
        // The whole point of this group is that every row failed for ONE
        // reason, so ticking them individually would be busywork.
        // The server re-runs the same query and requeues everything it
        // matches. Sending ids from here would only ever cover the rows this
        // page happened to render, which is not what the button says.
        if (!confirm('Retry every image affected by this? Fix the setting first.')) return;
        const d = await postJSON(API + '/attention/retry_group', { key: groupKey });
        toast(`Requeued ${d.requeued}.`);
      } else if (!posters.length && !trackings.length) {
        return toast('Tick the rows you want this to apply to.', 'error');
      } else if (action === 'retry_process') {
        const d = await postJSON(API + '/failures/retry',
                                 { kind: 'processing', poster_ids: posters });
        toast(`Requeued ${d.requeued}.`);
      } else if (action === 'retry_upload') {
        const d = await postJSON(API + '/failures/retry',
                                 { kind: 'upload', tracking_ids: trackings });
        toast(`Requeued ${d.requeued}.`);
      } else if (action === 'return_to_worker') {
        const comment = prompt(
          'What should the worker be told?\n\n' +
          'Leave blank for the default: "find a different picture of the same subject".'
        );
        if (comment === null) return;
        const d = await postJSON(API + '/attention/return_to_worker',
                                 { poster_ids: posters, comment });
        toast(`Sent ${d.sent} back to the worker.`);
      } else if (action === 'unusable') {
        const reason = prompt('Why can this never be used? (kept permanently)');
        if (!reason || !reason.trim()) return;
        const d = await postJSON(API + '/images/unusable',
                                 { poster_ids: posters, reason: reason.trim() });
        toast(`Retired ${d.count}.`);
      } else if (action === 'return_to_pipeline') {
        const d = await postJSON(API + '/images/return_to_pipeline', { poster_ids: posters });
        toast(`Returned ${d.count} to the pipeline.`);
      } else if (action === 'mark_removed') {
        const reason = prompt('Reason for the takedown (optional):') || '';
        const d = await postJSON(API + '/failures/mark_removed',
                                 { tracking_ids: trackings, reason });
        toast(`Marked ${d.marked} as removed.`);
      } else if (action === 'skip_upload') {
        if (!confirm('Permanently exclude these from the pipeline?')) return;
        const d = await postJSON(API + '/failures/skip', { tracking_ids: trackings });
        toast(`Skipped ${d.skipped}.`);
      } else if (action === 'release') {
        const d = await postJSON(API + '/attention/release',
                                 { poster_ids: posters, tracking_ids: trackings });
        toast(`Freed ${d.freed}.`);
      }
      loadAttention();
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
            project_id: projectId, scope: 'project',
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
            { key: 'selectors', scope: scopeFor('selectors'), project_id: projectId });
          await loadSettings();
          toast('Selectors reset.');
        } catch (e) { toast(e.message, 'error'); }
        break;

      case 'account-new': openAccountModal(null); break;
      case 'account-attach': toggleAttachPanel(); break;
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

      case 'test-gpt': runGptTest(); break;

      case 'run-drain': {
        const reason = prompt(
          'Pause this project?\n\n'
          + 'No new work is handed out. Anything already claimed finishes and '
          + 'reports back as normal.\n\nWhy? (shown on the dashboard while paused)');
        if (reason === null) return;
        try {
          const d = await postJSON(`${API}/run_mode`,
                                   { mode: 'drain', reason: reason.trim(), project_id: projectId });
          toast(d.in_flight
                ? `Paused. ${d.in_flight} item(s) still finishing.`
                : 'Paused. Nothing was in flight.');
          loadOverview();
        } catch (e) { toast(e.message, 'error'); }
        break;
      }

      case 'run-resume': {
        try {
          await postJSON(`${API}/run_mode`, { mode: 'run', project_id: projectId });
          toast('Resumed.');
          loadOverview();
        } catch (e) { toast(e.message, 'error'); }
        break;
      }

      case 'attention-load': loadAttention(); break;

      case 'shot-close': q('[data-shot-lightbox]').hidden = true; break;
    }
  });

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
    // Loaded here for the same reason as the settings, and because of the
    // line above: `loaded.processing = true` means LOADERS.processing is
    // NEVER invoked. Anything the Processing tab needs has to be fetched
    // right here — putting it in the loader map looks correct and silently
    // does nothing, which is exactly how the spend panel sat on "Loading…".
    loadSpend(),
  ]).then(() => {
    showSection(initial, { silent: true });
  });

  // Refresh the overview periodically so quota and node health stay honest
  // while the tab sits open — but only while it's the visible section.
  // Refresh the overview while it's the visible tab. Cadence adapts: 8s when
  // something is actually in flight so you can watch a batch move, 30s when
  // idle so an open tab isn't polling pointlessly all day.
  // ── Live-ish refresh ────────────────────────────────────────────────
  //
  // 3 seconds while you are actually looking at the overview, so the funnel
  // moves by itself and you can watch a batch climb it. Two brakes, both
  // deliberate:
  //
  //   · nothing at all while the tab is hidden or you are on another
  //     section — an overview left open overnight would otherwise make
  //     ~29,000 requests before morning, all of them unread
  //   · on an error it backs right off instead of hammering a server that is
  //     already unhappy, and returns to 3s the moment a request succeeds
  let overviewFailures = 0;
  overviewTimer = setInterval(() => {
    const active = q('.pipe-tab.active');
    if (!active || active.dataset.section !== 'overview' || document.hidden) return;
    // After a failure, only try every 4th tick (~12s) until one works.
    if (overviewFailures && (Date.now() / 3000 | 0) % 4 !== 0) return;

    // Never redraw underneath an open dialog. These panels are rebuilt from
    // scratch on every refresh, so a redraw while you are reaching for a
    // button moves the button — and at 3s that would happen constantly.
    if (document.querySelector('.pipe-modal:not([hidden])')) return;

    loadOverview().then((ok) => { overviewFailures = ok ? 0 : overviewFailures + 1; });
  }, 3000);
})();


/* ═══════════════════════════════════════════════════════════════════════════
   GPT PROJECTS — prompt, style reference, spend
   Only present when the project declares processor='gpt'; every lookup here
   bails on a missing element, so a Photoshop project runs none of it.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  const API = '/admin/pipeline/api';
  const $ = (sel) => document.querySelector(sel);

  const promptBox = $('[data-gpt-prompt]');
  if (!promptBox) return;            // not a GPT project

  const promptStatus = $('[data-gpt-prompt-status]');
  const styleImg     = $('[data-gpt-style-preview]');
  const styleEmpty   = $('[data-gpt-style-empty]');
  const styleFile    = $('[data-gpt-style-file]');
  const styleStatus  = $('[data-gpt-style-status]');

  async function load() {
    try {
      const r = await fetch(`${API}/gpt`);
      if (!r.ok) return;
      const d = await r.json();
      promptBox.value = d.prompt || '';
      if (d.style_url) {
        styleImg.src = d.style_url;
        styleImg.hidden = false;
        styleEmpty.hidden = true;
      } else {
        styleImg.hidden = true;
        styleEmpty.hidden = false;
      }
      const s = d.spend || {};
      const el = document.querySelector('[data-settings-status="spend"]');
      if (el) {
        el.textContent = `month to date $${Number(s.month_to_date || 0).toFixed(2)}`
          + ` (OpenAI $${Number(s.openai || 0).toFixed(2)}`
          + ` · Brave $${Number(s.brave || 0).toFixed(2)})`
          + (s.over ? ' — CAP REACHED' : '');
      }
    } catch (e) { /* the tab still works without it */ }
  }

  document.addEventListener('click', async (e) => {
    const action = e.target.dataset && e.target.dataset.action;

    if (action === 'save-gpt-prompt') {
      promptStatus.textContent = 'saving…';
      const r = await fetch(`${API}/gpt/prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: promptBox.value }),
      });
      promptStatus.textContent = r.ok ? 'saved' : 'failed';
      return;
    }

    if (action === 'reset-gpt-prompt') {
      // Server-side default, so "reset" means the same thing here as it does
      // for every other setting rather than a copy pasted into the JS.
      const r = await fetch(`${API}/settings/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'openai_prompt' }),
      });
      if (r.ok) { await load(); promptStatus.textContent = 'reset to default'; }
      return;
    }

    if (action === 'test-storage') {
      const el = document.querySelector('[data-storage-test-status]');
      el.textContent = 'writing a test file…';
      try {
        const r = await fetch(`${API}/storage/test`, { method: 'POST' });
        const d = await r.json();
        el.textContent = d.message || (d.ok ? 'ok' : 'failed');
        el.className = d.ok ? 'muted mono' : 'error mono';
      } catch (err) {
        el.textContent = 'Test failed: ' + err.message;
        el.className = 'error mono';
      }
      return;
    }

    if (action === 'upload-gpt-style') {
      if (!styleFile.files || !styleFile.files[0]) {
        styleStatus.textContent = 'pick a file first';
        return;
      }
      styleStatus.textContent = 'uploading…';
      const fd = new FormData();
      fd.append('file', styleFile.files[0]);
      const r = await fetch(`${API}/gpt/style`, { method: 'POST', body: fd });
      if (r.ok) { styleStatus.textContent = 'uploaded'; await load(); }
      else {
        let msg = 'failed';
        try { const d = await r.json(); msg = d.detail || msg; } catch (err) {}
        styleStatus.textContent = msg;
      }
    }
  });

  load();
})();
