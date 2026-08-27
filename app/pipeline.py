"""
Post-production pipeline core.

This module is the single place that knows how the automated
Photoshop → marketplace-upload pipeline behaves. The FastAPI routes are thin
wrappers over the functions here; the remote worker node never contains
policy, only execution.

════════════════════════════════════════════════════════════════════════════
DESIGN CONTRACT — please keep these properties when extending
════════════════════════════════════════════════════════════════════════════

1.  NO HARDCODED BEHAVIOUR.
    Every string the pipeline needs at runtime — the Photoshop JSX source,
    every CSS selector, the title format, the keyword list, timings, the
    schedule, the storage layout — is a setting resolved through
    `get_setting()`. Defaults live in DEFAULTS below and are the *only*
    place a literal belongs. The dashboard edits settings; the worker node
    fetches them per run. Adding a knob = adding a DEFAULTS entry.

2.  SETTINGS RESOLVE PROJECT-FIRST, THEN GLOBAL.
        pipeline.<project_slug>.<key>   ← per-niche override
        pipeline.<key>                  ← shared default
        DEFAULTS[<key>]                 ← code default
    So the celebrity niche can have its own JSX and keyword list without
    duplicating anything else, and a brand-new project inherits sane values.

3.  STAGES ARE INDEPENDENT AND RESUMABLE.
    Per-image state lives on SavedPoster.pipeline_status; per-account upload
    state on UploadTracking.status. Work is *claimed* (claimed_at/claimed_by)
    before being done and released on completion, so a node dying mid-batch
    only strands its own claims, which `reap_stale_claims()` recovers. No
    stage ever needs another stage to be re-run.

4.  TITLE FORMAT / KEYWORDS ARE TEMPLATES, NOT CODE.
    See `render_remote_title()` / `render_keywords()`. Both take the same
    variable bag, so a new marketplace or niche is a template edit in the
    dashboard, not a code change.

5.  MULTI-TARGET READY. `target_site` is carried on accounts and tracking
    rows. Adding TeePublic = new settings block under
    `pipeline.<project>.targets.teepublic.*` + a worker-side module. Nothing
    in this file assumes FineArtAmerica.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import string
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, true as sa_true
from sqlalchemy.orm import Session

from .models import (
    AccountProject, AppSetting, MasterTitle, PipelineJob, ProcessedImage,
    Project, SavedPoster, UploadAccount, UploadTracking, WorkerNode,
)
from .timeutil import local_today


# ═════════════════════════════════════════════════════════════════════════
#  SETTINGS
# ═════════════════════════════════════════════════════════════════════════

SETTINGS_ROOT = "pipeline"

# The Photoshop JSX that runs on the worker node. This is the DEFAULT only —
# the live copy is editable from the dashboard (Pipeline → Processing) and
# stored in app_settings, so tweaking the effect never needs a deploy.
#
# Contract with the worker: the node writes the source image path into
# INPUT_FILE and the desired output path into OUTPUT_FILE by prepending two
# `var` lines, then runs Photoshop with this file. Everything else — sizes,
# quality, the FX plugin path — comes from placeholders substituted by
# `render_process_script()` so they stay dashboard-editable too.
DEFAULT_PROCESS_SCRIPT = r"""#target photoshop
// Single-image processor. The worker node prepends:
//     var INPUT_FILE  = "...";
//     var OUTPUT_FILE = "...";
// and then runs this file. Keep it single-image and idempotent — batching,
// retries and folder walking are the pipeline's job, not Photoshop's.

function run() {
    var srcFile = new File(INPUT_FILE);
    if (!srcFile.exists) throw new Error("Input file missing: " + INPUT_FILE);

    var originalRulerUnits = app.preferences.rulerUnits;
    var originalDialogMode = app.displayDialogs;
    app.preferences.rulerUnits = Units.PIXELS;
    // Suppress the "damaged Photoshop data" and similar warnings so an
    // unattended run never blocks on a modal.
    app.displayDialogs = DialogModes.NO;

    try {
        var doc = app.open(srcFile);

        // ── 1. Normalise to the working width, sharpening as appropriate ──
        var workWidth = {{WORK_WIDTH}};
        if (doc.width.value < workWidth) {
            doc.resizeImage(UnitValue(workWidth, "px"), null, null,
                            ResampleMethod.PRESERVEDETAILS);
            applySmartSharpen({{SHARPEN_AMOUNT}}, {{SHARPEN_RADIUS}}, {{SHARPEN_NOISE}});
        } else if (doc.width.value > workWidth) {
            doc.resizeImage(UnitValue(workWidth, "px"), null, null,
                            ResampleMethod.BICUBICSHARPER);
        }

        // ── 2. Apply the painterly effect plugin ──
        var fxFile = new File("{{FX_SCRIPT_PATH}}");
        if (!fxFile.exists) throw new Error("FX script missing: " + fxFile.fsName);
        $.evalFile(fxFile);

        // The plugin may switch the active document and leave helpers open.
        doc = app.activeDocument;
        closeExtraDocuments(doc);
        doc.flatten();

        // ── 3. Upscale to final delivery width and save ──
        doc.resizeImage(UnitValue({{OUTPUT_WIDTH}}, "px"), null, null,
                        ResampleMethod.BICUBICSMOOTHER);

        var opts = new JPEGSaveOptions();
        opts.quality = {{JPEG_QUALITY}};
        var outFile = new File(OUTPUT_FILE);
        doc.saveAs(outFile, opts, true, Extension.LOWERCASE);

        // Verify the save actually landed.
        //
        // app.displayDialogs = DialogModes.NO suppresses Photoshop's error
        // dialogs, and saveAs to an unreachable path can then fail SILENTLY —
        // the script carries on and reports success for a file that was never
        // written. The usual cause is the storage drive not being visible to
        // THIS Photoshop process (Windows maps drives per elevation context,
        // so a drive mapped in an admin console is invisible to a normal one).
        outFile = new File(OUTPUT_FILE);
        if (!outFile.exists) {
            throw new Error(
                "saveAs reported no error but produced no file at " + OUTPUT_FILE +
                ". The drive is most likely not mapped or not writable for the " +
                "user running Photoshop."
            );
        }

        var w = doc.width.value, h = doc.height.value;
        doc.close(SaveOptions.DONOTSAVECHANGES);

        // Release undo history, clipboard and cached tiles.
        //
        // Photoshop stays open across the whole batch, so without this it
        // accumulates state image after image — measurably slower each time,
        // and eventually unstable. Cheap to do, and the difference over a
        // hundred-image run is large.
        try { app.purge(PurgeTarget.ALLCACHES); } catch (e) {}

        writeResult({ ok: true, width: w, height: h });
    } catch (e) {
        writeResult({ ok: false, error: String(e) });
        throw e;
    } finally {
        app.preferences.rulerUnits = originalRulerUnits;
        app.displayDialogs = originalDialogMode;
    }
}

// Photoshop can't talk to the pipeline directly, so it drops a small JSON file
// that the worker node polls for. THIS IS THE COMPLETION SIGNAL — the node
// does not wait for Photoshop to exit (it never does), it waits for this file.
// Always write it, on both the success and failure paths, or the node will
// consider the run hung and kill Photoshop.
//
// RESULT_FILE is a local path supplied by the node. The fallback keeps older
// saved scripts working; it writes beside the output instead, which is on the
// network share and therefore slower to appear.
function writeResult(obj) {
    try {
        var target = (typeof RESULT_FILE !== "undefined" && RESULT_FILE)
                     ? RESULT_FILE
                     : (OUTPUT_FILE + ".result.json");
        var f = new File(target);
        f.open("w"); f.write(toJSON(obj)); f.close();
    } catch (e) {}
}

function toJSON(o) {
    var parts = [];
    for (var k in o) {
        var v = o[k];
        parts.push('"' + k + '":' + (typeof v === "number" ? v
                  : typeof v === "boolean" ? (v ? "true" : "false")
                  : '"' + String(v).replace(/"/g, '\\"') + '"'));
    }
    return "{" + parts.join(",") + "}";
}

function closeExtraDocuments(keepDoc) {
    for (var i = app.documents.length - 1; i >= 0; i--) {
        var d = app.documents[i];
        if (d !== keepDoc) {
            try { d.close(SaveOptions.DONOTSAVECHANGES); } catch (e) {}
        }
    }
}

function applySmartSharpen(amount, radius, noise) {
    try {
        var desc = new ActionDescriptor();
        desc.putEnumerated(stringIDToTypeID("presetKind"),
                           stringIDToTypeID("presetKindType"),
                           stringIDToTypeID("presetKindCustom"));
        desc.putUnitDouble(charIDToTypeID("Amnt"), charIDToTypeID("#Prc"), amount);
        desc.putUnitDouble(charIDToTypeID("Rds "), charIDToTypeID("#Pxl"), radius);
        desc.putUnitDouble(stringIDToTypeID("noiseReduction"), charIDToTypeID("#Prc"), noise);
        desc.putEnumerated(charIDToTypeID("blur"), stringIDToTypeID("blurType"),
                           stringIDToTypeID("lensBlur"));
        executeAction(stringIDToTypeID("smartSharpen"), desc, DialogModes.NO);
    } catch (e) {
        app.activeDocument.activeLayer.applyUnSharpMask(amount, radius, 0);
    }
}

run();
"""


# Marketplace DOM map. These are the strings that break when a site
# redesigns its upload form — which is exactly why they are settings and not
# code. Edit in the dashboard (Pipeline → Upload Settings → Selectors), hit
# Test Upload on one image, done.
# TeePublic's sign-in, measured from the real page 2026-08-20.
#
# There is deliberately NO new login code for this. The uploader's login()
# already does the right things: it treats the first "click through to the
# artist login" step as optional, and it decides whether to type credentials
# by whether the email field is on the page. TeePublic redirects an
# already-signed-in visitor away from the sign-in page, so the field is
# absent and the session is reused — the same signal, for free.
#
# Only the strings differ, which is the whole point of keeping them as
# settings.
DEFAULT_TEEPUBLIC_SELECTORS = {
    "login_url":         "https://www.teepublic.com/users/sign_in",
    # No equivalent of FAA's "choose your account type" page. Left blank on
    # purpose: login() logs one line and carries on when it cannot find it.
    "artist_login_link": "",
    "username_field":    "css:#user_email",
    "password_field":    "css:#user_password",
    "login_submit":      "css:#login",
    "control_panel_url": "https://www.teepublic.com/account/sales",
    "popup_close":       "css:.jsCloseFlash",
    # TeePublic sits behind Cloudflare, which serves a "managed challenge" —
    # an interstitial that clears itself for a browser that looks like a
    # browser. Headless is one of the loudest signals it looks for, so this
    # marketplace runs with a visible window. The node has a desktop anyway.
    "headless":          "0",
}


DEFAULT_FAA_SELECTORS = {
    # FineArtAmerica has never objected to headless, so it stays: no window,
    # less memory, nothing on screen if you happen to be on that desktop.
    "headless":             "1",
    "login_url":            "https://fineartamerica.com/loginchoosetype.php",
    "artist_login_link":    "css:a.buttonlogin[href='/artists/index.php']",
    "username_field":       "name:username",
    "password_field":       "name:password",
    "login_submit":         "css:a.button[href='javascript: document.loginartist.submit();']",
    # Fallback landing page when an account has no profile_url set. Session
    # reuse is detected by the absence of the username field, not by a marker
    # element — that proved more reliable across the site's interstitials.
    "control_panel_url":    "https://fineartamerica.com/controlpanel/activity.html",
    "popup_close":          "css:div.popupContent a.popupClose, div.popupContent a.close",
    # BLANK BY DESIGN — do not put a URL here without good reason.
    #
    # The upload form's address carries a per-session id:
    #   …/controlpanel/updateartwork.html?newartwork=true&sessionid=a4bca890…
    # so it cannot be hardcoded. When this is blank the node loads the profile
    # page, reads the address out of the Upload Image link, and navigates to
    # it — which picks up the current session id automatically.
    #
    # Set it only as an emergency override if that flow ever breaks.
    "upload_url":           "",
    # The link whose address is read (and clicked, if it turns out to be a
    # script link rather than a real one).
    "upload_button":        "css:a.buttonEditProfile[href*='updateartwork.html?newartwork=true']",
    "file_input":           "css:input.uploadImageInput[type='file']",
    "upload_confirm":       "css:a.button[href*='uploadArtwork']",
    "title_field":          "name:artworkname",
    "keywords_field":       "name:artworkkeywords",
    "description_field":    "name:artworkdescription",
    "submit_button":        "xpath://div[contains(@id, 'submittopdiv')]//a",
    # If the URL still matches this after submitting, the submit silently failed.
    "still_on_form_marker": "updateartwork",
}


# Selenium waits, previously the Tkinter "Settings" tab. Per-account
# overrides live in UploadAccount.timing_json.
DEFAULT_TIMINGS = {
    # How long to let a self-clearing security check finish before calling it
    # a wall. Cloudflare's managed challenge takes a few seconds; failing at
    # three meant never getting past a site that would have let us in.
    "bot_wall_wait_s":  30,
    "login_wait":       2.0,
    "page_load_wait":   2.0,
    "upload_wait":      5.0,
    "form_input_delay": 0.4,
    "submit_wait":      2.5,
    "element_timeout":  30.0,
    "popup_delay":      2.0,
    # Pause between consecutive images in a batch. Sequential uploading is
    # what fixed the old 20-30% failure rate; a small human-ish gap also
    # keeps us well clear of rate heuristics.
    "between_images":   3.0,
}


DEFAULTS: dict[str, Any] = {
    # ── Greenlight policy ────────────────────────────────────────────────
    # 'manual'  — only the Pipeline tab's Greenlight button promotes work
    # 'payment' — marking a PaymentRun paid greenlights that date range
    # 'both'    — payment auto-greenlights, and manual still available
    "greenlight_mode": "both",

    # ── Photoshop stage ──────────────────────────────────────────────────
    "process_script":     DEFAULT_PROCESS_SCRIPT,
    "fx_script_path":     "C:/Program Files/Adobe/Adobe Photoshop 2023/Real Paint FX/Scripts (actions)/Real-Paint-FX.jsx",
    "work_width":         2000,
    "output_width":       4000,
    "jpeg_quality":       10,
    "sharpen_amount":     150,
    "sharpen_radius":     1.0,
    "sharpen_noise":      10,
    "output_suffix":      "_Painted",
    "photoshop_exe":      "C:/Program Files/Adobe/Adobe Photoshop 2023/Photoshop.exe",
    # Seconds before a single-image Photoshop run is considered hung.
    "process_timeout_s":  600,
    # How long to wait for a cold Photoshop to become ready before
    # dispatching the first script. Only paid once per batch.
    "photoshop_warmup_s": 60,
    # Restart Photoshop every N images. It stays open between images for
    # speed, but degrades over a long unattended run — memory creeps up and
    # throughput drops even with a purge after each image. A periodic clean
    # restart costs ~30s and prevents a slow slide into timeouts.
    # 0 disables it entirely.
    "photoshop_restart_every": 25,
    "process_batch_size": 20,
    "process_max_attempts": 3,

    # ── Storage ──────────────────────────────────────────────────────────
    # Root on the worker node where processed output is written. Kept as a
    # setting so remounting the Storage Box elsewhere is a dashboard edit.
    "storage_root":       "S:",
    # Layout template for the archive, relative to storage_root.
    #
    # The {site}/{project} prefix is what keeps ten pipelines from colliding
    # in one flat tree — and, more importantly, what makes recovery from a
    # marketplace ban a copy of one folder rather than a query. Read it as:
    #
    #     S:/Fineartamerica/MovieSeries/processed/2026-05-24/50. Pulp Fiction (1994)/50_1_Painted.jpg
    #
    # {site} is the project's target marketplace, {project} its name. Both are
    # slugified for the filesystem — see `_path_token()`.
    "storage_layout":     "{site}/{project}/processed/{date}/{title_folder}/{filename}",

    # ── Upload stage ─────────────────────────────────────────────────────
    "upload_batch_size":  40,
    "upload_max_attempts": 3,
    # Sequential single-tab uploading. The legacy tool opened N tabs at once
    # and lost 20-30% of a 35-image batch to stale tabs, memory pressure and
    # session timeouts. Do not reintroduce parallel tabs without a very good
    # reason — unattended reliability beats throughput here.
    "upload_sequential":  True,
    "selectors":          DEFAULT_FAA_SELECTORS,
    # Chosen by the account's marketplace, not by its project — an account
    # may serve no project at all, and the sign-in form belongs to the SITE.
    "selectors_teepublic": DEFAULT_TEEPUBLIC_SELECTORS,
    "timings":            DEFAULT_TIMINGS,

    # Title submitted to the marketplace. Variables: {title} {year}
    # {letter} {index} {content_type} {external_id}
    "title_template":     "{title} - {year} {letter}",
    # Appended to the auto-derived keywords. Leading comma intentional.
    "keywords_static":    ", movie, series, tv, film, show, actor, cinema",
    # Where the listing description comes from: 'master' uses
    # MasterTitle.description, 'template' renders description_template.
    "description_source": "master",
    "description_template": "{description}",

    # ── Scheduling ───────────────────────────────────────────────────────
    # 'continuous' — the node processes/uploads whenever there is work
    # 'daily'      — only start a run after daily_start_hour (node local time)
    "schedule_mode":      "continuous",
    "daily_start_hour":   6,

    # ── Stopping the pipeline on purpose ─────────────────────────────────
    # 'run'   — normal
    # 'drain' — hand out NO new work, but let whatever is already claimed
    #           finish and report back. This is what you want before a
    #           reboot, a deploy or a settings change: the queue empties
    #           itself and nothing is left half-done.
    # 'halt'  — hand out nothing. Same as drain from the dispatcher's point
    #           of view; kept as a distinct word because "I am stopping for
    #           five minutes" and "I am stopping until further notice" are
    #           different intentions and the dashboard says which.
    #
    # There is deliberately no mode that kills work in flight. Both stages
    # cost real money or real minutes per item, and abandoning an image
    # mid-generation would pay for something nobody receives. Stopping the
    # INTAKE and waiting is always the cheaper stop.
    "run_mode":           "run",
    # Why it was stopped, shown wherever the paused state is displayed.
    "run_mode_reason":    "",
    # How long a claim may sit untouched before reap_stale_claims() frees it.
    "claim_timeout_min":  45,
    # ── Per-project behaviour that used to be hardcoded ──────────────────
    # Everything below was a constant in config.py or a literal in a route
    # until MUSIK made a second niche real. They live here so a THIRD project
    # needs one registry entry plus overrides — never another rewrite.
    #
    # THE RULE: if a screen shows a value that could differ between niches, it
    # must resolve through get_setting(project=...). A literal in a template
    # or a constant in config.py is a defect.

    # Worker pay, per item saved. Overridable per project: MUSIK may be worth
    # more or less per image than a movie poster.
    "pay_rate_kes":       "5",
    # Soft warning when a worker saves more than this for one title. Falls
    # back to the project's images_per_title when that is set.
    "soft_limit_per_title": 3,
    # Where the worker is sent to find source images. `{query}` and
    # `{content_type}` are substituted. Empty means the project searches
    # in-page instead of linking out — which is what MUSIK does.
    "source_search_url":  "https://www.themoviedb.org/search?query={query}",
    # Hosts a worker may download from. Empty means unrestricted.
    "allowed_download_hosts": "",
    # Below this width the admin gallery outlines an image in red. It exists
    # because a movie poster under 800px prints badly. MUSIK sources are
    # deliberately small — GPT redraws them and the result is upscaled — so
    # the warning would fire on every single image and mean nothing.
    # 0 turns it off.
    "review_min_width_px": 800,

    # ── Brave image search (MUSIK) ───────────────────────────────────────
    # Two keys. Normal searches use the free key; deep searches go straight to
    # the paid one because they fire two queries at once and the free key
    # allows only 1 request/second. A normal search that hits a 429 retries
    # once on the paid key rather than showing the worker an error.
    "brave_api_key_free": "",
    "brave_api_key_paid": "",
    "brave_query_normal": 'site:pinterest.com "{artist}"',
    # Deep search runs EVERY line below and merges the results, de-duplicated
    # by image URL. Two queries because no single phrase serves both bands and
    # solo artists: "U2 musician" returns Bono, "Kanye West band" returns
    # nothing useful. At half a cent a query, paying beats being clever.
    "brave_query_deep":   '"{artist}" band\n"{artist}" musician',
    "brave_min_dimension": 300,
    "brave_results_per_query": 50,
    # Off by default — turn it on only if a bug starts looping.
    "brave_daily_query_cap": 0,

    # ── OpenAI image generation (MUSIK) ──────────────────────────────────
    "openai_api_key":     "",
    # Separate, higher-privilege credential. Only the nightly cost
    # reconciliation uses it; image generation never does.
    "openai_admin_key":   "",
    # Written by the nightly reconciler, never by a human. They live in
    # DEFAULTS because set_setting() validates every key against it — a
    # value written by the app still has to be declared here.
    "openai_reconcile_result": "",
    "openai_reconcile_date":   "",
    "openai_model":       "gpt-image-2",
    "openai_size":        "auto",
    "openai_quality":     "low",
    "openai_prompt": (
        "Transform the style of the second image to style of the first image. "
        "Favor a clean, minimal treatment with broad shapes and restrained "
        "detail. Use second image color scheme. Zoom in to the upper body "
        "areas and strictly avoid empty space.\n"
        "Ratio should either be 1:1, 2:3, 3:2 or 16:9 depending on second "
        "image overall structure and which it would look best in."
    ),
    # Style reference, uploaded from the dashboard. Stored relative to the
    # workspace. Changing it does NOT retroactively affect already-processed
    # images — each ProcessedImage records the script/style version it used.
    "openai_style_image": "",
    # ── Storage access from THIS server ──────────────────────────────────
    # The Windows node writes to the drive letter in `storage_root`. This
    # server has no such drive, so the GPT stage pushes over SFTP to the same
    # box — same relative storage_path, two ways in. See app/storage_remote.py.
    # Leave the host blank to write to a local folder instead, which is what
    # the dev setup does.
    "storage_sftp_host":     "",
    "storage_sftp_port":     22,
    "storage_sftp_user":     "",
    "storage_sftp_password": "",     # Fernet-encrypted, like account passwords
    "storage_sftp_root":     "",     # subfolder on the box, usually blank
    "storage_local_root":    "processed_local",

    # ── Upscaling to print size ──────────────────────────────────────────
    # GPT returns roughly 1024px. Print needs more, so the server resizes
    # after generation rather than paying for a larger generation — output
    # size drives the token bill directly, and Lanczos costs nothing.
    #
    # Height scales in proportion: 1000x2000 at width 4000 becomes 4000x8000.
    "upscale_width_px":   4000,
    # Applied AFTER the upscale. 0 = off, which is the default deliberately:
    # sharpening artefacts are permanent and the review gate is the only
    # place they would be caught. Raise it once you have seen a real print.
    "upscale_sharpen":    0,
    "upscale_jpeg_quality": 92,

    # Spend guard. 'warn' posts a dashboard alert; 'pause' also stops
    # dispatching. Default warn — a hard stop on a bad estimate is worse than
    # a message you can act on.
    "spend_cap_usd_month": 0,
    "spend_cap_action":   "warn",

    # ── The optional GPT review gate ─────────────────────────────────────
    # On by default. When off, newly processed images go straight to upload —
    # but anything ALREADY waiting stays waiting, so turning it off can never
    # release work you hadn't looked at.
    "gpt_review_required": 1,

    # Node poll interval hint (seconds). The node honours this.
    "poll_interval_s":    30,
    # Idle back-off. After `poll_idle_after_min` minutes with nothing to do,
    # the node stretches its polling towards `poll_interval_idle_s`, snapping
    # straight back to `poll_interval_s` the moment work appears.
    #
    # This is purely about noise and pointless requests: 30s polling while a
    # box sits idle overnight is ~2,900 wasted round trips and a console you
    # can't read. Responsiveness when there IS work is unchanged, because the
    # first batch resets the interval.
    #
    # Set poll_interval_idle_s equal to poll_interval_s to disable back-off.
    "poll_interval_idle_s": 180,
    "poll_idle_after_min":  10,
    # Local agent logs on the node are deleted after this many days. 0 keeps
    # them forever. They are the only record when the node cannot reach the
    # server, so this is generous by default.
    "node_log_retention_days": 14,

    # ── Earnings ─────────────────────────────────────────────────────────
    # Bookkeeping for the nightly marketplace read, not knobs — they are
    # written by the scheduler and read by the Earnings page. They live here
    # because get_setting/set_setting REFUSE any key not declared in
    # DEFAULTS, which is what makes a typo impossible and is also why an
    # undeclared key is a 500 rather than a silent None.
    #
    # `_day` is the local date of the last attempt and is what stops the
    # read running sixty times an hour; `_at` is the timestamp shown on the
    # page. Two keys because "did we try today" and "when did we last get
    # something" are different questions, and after a failure they differ.
    "earnings_last_run_day": "",
    "earnings_last_run_at":  "",

    # ── Where the earnings reader looks ──────────────────────────────────
    # Settings, not constants, for the same reason every other marketplace
    # URL in this file is: when FineArtAmerica moves a page you change it in
    # the dashboard and press READ NOW, instead of waiting for a deploy.
    #
    # The SIGN-IN page is deliberately NOT here — it is `login_url` in the
    # selectors map above, the one the uploader already uses. One account has
    # one login page, and having two copies of it guarantees they drift.
    "earnings_balance_url": "https://fineartamerica.com/controlpanel/balance",
    "earnings_sales_url":   "https://fineartamerica.com/controlpanel/sales",
    # Ceiling on pages fetched per account per run. A nightly read touches
    # one or two; only a first-ever read walks a long history, and the cap
    # stops that one account eating the whole quiet window. It resumes the
    # next night on its own, because the stop rule is "the first row we
    # already have" and nothing needs remembering.
    "earnings_max_pages_per_run": 25,

    # ── The quiet window ─────────────────────────────────────────────────
    # HH:MM, node-local. `earnings_quiet_from` is when new batches stop being
    # handed out; `earnings_run_at` is when the reads are queued. Times
    # rather than plain hours so this can be tested at three minutes' notice
    # instead of only on the hour.
    #
    # Blank quiet_from disables the window entirely — earnings then simply
    # queue and take their turn like any other job.
    "earnings_quiet_from":  "22:00",
    "earnings_run_at":      "22:00",
    # How long after the scheduled read we keep re-queueing accounts that did
    # not get read. The GAP between tries is not set here — it is the account's
    # own cooldown (3h after a general failure, 12h after a signed-out one),
    # so a signed-out account waits for a person instead of being knocked on
    # repeatedly. 0 switches retrying off.
    # Which hosts a project's images may be downloaded from. BLANK = any
    # public host, which is what the app has always done — the old
    # RESTRICT_HOSTS env var was never switched on, and listed TMDB only, so
    # enabling it would have blocked every MUSIK save. Per project because
    # what counts as a legitimate source differs per niche. Internal and
    # private addresses are refused regardless of this setting.
    "allowed_image_hosts": "",
    "earnings_retry_window_hours": 8,
    # When the SCHEDULED read was dispatched, UTC ISO. The retry measures
    # from this rather than from the calendar day, so it behaves the same at
    # 23:50 and 00:10. Written only by run_daily_if_due().
    "earnings_daily_run_started_at": "",

    # ── The interstitial wall ────────────────────────────────────────────
    # TeePublic serves a full-page wall whose dismiss control is sealed
    # inside a closed shadow root, so it cannot be clicked by selector — only
    # by position, from a recorded mouse path. See models.WallPath.
    #
    # `wall_wait_s` is how long to let the page settle before deciding we are
    # looking at the wall rather than a slow account page. It is deliberately
    # NOT the old bot-wall timer: two numbers that mean different things must
    # not share one setting, or changing either forces you to move both.
    "wall_wait_s":          5,
    # Attempts per read, each using the NEXT recording. Three because a path
    # that has failed twice is unlikely to work on a third go, and every
    # extra attempt is another click at a page we may have misread.
    "wall_max_attempts":    3,
    # Where the sequential rotation has got to. A single counter across all
    # accounts, so no account is ever tied to one path. Stored rather than
    # derived because it must survive both the node and the server restarting.
    "wall_path_cursor":     0,

    # ── Marketplace visibility scan ──────────────────────────────────────
    # How many accounts are scanned at the same time. Each one holds ONE
    # browser open for its whole account — not one per design, which is what
    # the owner's original script did and what made a scan take ten hours.
    # Three at roughly 400MB each is comfortable on the node's 12GB, and the
    # pipeline is held anyway while this runs, so Photoshop is not competing.
    "scan_parallel_accounts": 3,
    # How deep to page through search results before calling a design
    # missing. A design that IS visible is usually found on page one; this
    # bound is what stops a genuinely missing one paging forever.
    "scan_max_search_pages":  25,
    # Seconds between designs, per account. Not a rate limit the site asked
    # for — a courtesy, and the knob to turn if it ever starts complaining.
    "scan_delay_s":           1,
    # Stop each account after this many designs. 0 = check them all, which is
    # the real setting; anything else is for TESTING the later stages without
    # sitting through ninety designs first. Kept as a dashboard value rather
    # than a code constant because that is exactly when you need to change it
    # and exactly when editing code is most annoying.
    "scan_limit_per_account": 0,
    # After this many completed deactivate/reactivate cycles, a design that is
    # STILL missing is flagged as a probable vague tag rather than cycled
    # again. The search only pages 25 deep; a tag like "Queen" has tens of
    # thousands of results, so a healthy design can read MISSING forever and
    # no amount of cycling will change that. 2 because the cure is reliable
    # when it is the right cure — a third failure is evidence, not bad luck.
    "scan_vague_after_fixes": 2,
    # How long to wait before trying again after a TRANSIENT failure — the
    # wall, a maintenance page. Growing gaps, and the list length is also how
    # many times it will try before giving up. Three attempts inside one
    # minute is not three chances; these are.
    "scan_retry_delays_min": "30,60,90",
    # CONTINUE skips designs checked more recently than this. It is what
    # makes "carry on where the night left off" work without freezing the
    # catalogue: a design checked 20 hours ago is still current, one checked
    # last week is not.
    "scan_continue_within_h": 24,
    # How many times to restart ONE account's switching after the worker
    # machine stopped reporting. The usual cause is dull — a reboot, a Chrome
    # that would not start — and the work itself is fine, so retrying is
    # right. But retrying for ever is a loop that holds Photoshop and the
    # uploads all night doing nothing, so the run gives up and says so.
    "store_stage_max_attempts": 3,
    # How many designs in a row may be blocked by the wall before the whole
    # account is given up on and the run waits. Blocked designs cost three
    # seconds each and tell us nothing, so grinding through 161 of them is
    # four minutes of writing errors against healthy designs. Was a bare 5
    # in the node's own code, which is exactly the kind of number the owner
    # cannot change without editing a file on a machine he does not read.
    "store_wall_give_up_after": 5,
    # Immediate second attempts at ONE design after a blocked one. Covers
    # the wall arriving between two page loads, which clearing it and going
    # again fixes in seconds. Small on purpose: a wall that is properly in
    # the way is not beaten by trying harder in the same ten seconds — that
    # is what the spaced run-level wait above is for.
    "store_design_retries": 1,
    # After this many GENUINE failures — the page was ours, the button was
    # not there — a design is flagged for a person instead of being retried
    # at the front of every future sweep. Failures caused by the wall never
    # count here, because the wall says nothing about the design. Same
    # reasoning and same number as the vague-tag flag above.
    "store_action_give_up_after": 3,
    # Read the marketplace's own count of switched-off designs at both ends
    # of an account's switching turn, and say so when it disagrees with what
    # we believe we did. Two page loads against an hour of work. A switch
    # only because it needs to be turnable off from the screen if TeePublic
    # ever moves the number — never because it is optional in principle.
    "store_count_check": 1,

    # ── Archive index (what is already on the storage box?) ──────────────
    # How many file paths the worker machine sends home at a time. It runs
    # ONE job at a time, so this is really "how long may Photoshop be made
    # to wait" — the walk itself takes seconds and the server's matching is
    # the slow half. 200 keeps each round trip under a second or two.
    "archive_index_chunk": 200,

    # ── Listing reconciliation (does the marketplace still show it?) ─────
    # How many addresses go out in one job. The worker machine runs ONE job
    # at a time, so this is really "how long may Photoshop be made to wait" —
    # 200 HEAD requests is two or three minutes. A single job covering all
    # 4,811 would block the pipeline for an hour, which a read-only check
    # has no right to do.
    "listing_check_chunk": 200,
    # Pause between requests, milliseconds. These are public pages we are
    # entitled to and nothing counts them, but several thousand in a burst
    # from one address is how a site that was indifferent becomes interested.
    "listing_check_gap_ms": 300,
    # ── The guard against a wrong artist name ───────────────────────────
    # The address is built from a name typed in by hand. One wrong character
    # makes EVERY listing on that account 404, and the screen would report
    # thousands of copyright takedowns. Above this fraction gone, the sweep
    # says the address is probably wrong instead of presenting findings.
    # 0.5 because a real account losing half its catalogue is already worth
    # stopping over, whichever of the two explanations is true.
    "listing_check_alarm_ratio": 0.5,
    # …but only once enough of that account has been seen for the fraction
    # to mean anything. Three gone out of three is not a pattern.
    "listing_check_min_sample": 20,
    # Give up on a sweep after this many chunks are dispatched and never
    # reported. Same reasoning as store_stage_max_attempts: retry the dull
    # causes, then stop rather than loop.
    "listing_check_max_attempts": 3,

    # ── How many failures in a row before an account is parked ───────────
    # Only applies to failures the node marks as "might be systemic" — a
    # missing form field. A bot wall or rejected credentials still parks the
    # account on the first one, because those are certain.
    #
    # 3 because FineArtAmerica serves two versions of its upload form at
    # random: one miss means we got the other page, three in a row means the
    # form really has changed. Raise it if FAA's split gets more even; set it
    # to 1 to restore the old pause-immediately behaviour.
    "upload_pause_after_failures": 3,
}


def _setting_row(db: Session, key: str) -> Optional[AppSetting]:
    return db.query(AppSetting).filter_by(key=key).first()


def _coerce(value: str, like: Any) -> Any:
    """Cast a stored string back to the type implied by its DEFAULTS entry."""
    if isinstance(like, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int) and not isinstance(like, bool):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return like
    if isinstance(like, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return like
    if isinstance(like, (dict, list)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return like
        # Merge over the default so a partial override doesn't drop keys —
        # this is what lets us add a new selector in DEFAULTS later without
        # breaking installs that saved the old map.
        if isinstance(like, dict) and isinstance(parsed, dict):
            merged = dict(like)
            merged.update(parsed)
            return merged
        return parsed
    return value


def get_setting(db: Session, key: str, *, project: Optional[Project | str] = None) -> Any:
    """
    Resolve a pipeline setting, most specific first:
        pipeline.<project_slug>.<key>  →  pipeline.<key>  →  DEFAULTS[key]

    `key` must exist in DEFAULTS. That's deliberate: it keeps the set of
    knobs discoverable in one place and stops silent typos from resolving
    to None at runtime.
    """
    if key not in DEFAULTS:
        raise KeyError(f"Unknown pipeline setting {key!r}. Add it to DEFAULTS.")
    default = DEFAULTS[key]

    slug = project.slug if isinstance(project, Project) else project
    if slug:
        row = _setting_row(db, f"{SETTINGS_ROOT}.{slug}.{key}")
        if row is not None and row.value != "":
            return _coerce(row.value, default)

    row = _setting_row(db, f"{SETTINGS_ROOT}.{key}")
    if row is not None and row.value != "":
        return _coerce(row.value, default)

    return default


def set_setting(
    db: Session,
    key: str,
    value: Any,
    *,
    project: Optional[Project | str] = None,
    by: Optional[str] = None,
) -> None:
    """Persist a setting. Pass project to scope it to one niche."""
    if key not in DEFAULTS:
        raise KeyError(f"Unknown pipeline setting {key!r}. Add it to DEFAULTS.")

    slug = project.slug if isinstance(project, Project) else project
    full = f"{SETTINGS_ROOT}.{slug}.{key}" if slug else f"{SETTINGS_ROOT}.{key}"

    if isinstance(value, (dict, list)):
        raw = json.dumps(value, indent=2)
    elif isinstance(value, bool):
        raw = "1" if value else "0"
    else:
        raw = str(value)

    row = _setting_row(db, full)
    if row is None:
        db.add(AppSetting(key=full, value=raw, updated_by=by))
    else:
        row.value = raw
        row.updated_by = by
        row.updated_at = datetime.utcnow()


def clear_setting(db: Session, key: str, *, project: Optional[Project | str] = None) -> None:
    """Remove an override so the next tier down applies again."""
    slug = project.slug if isinstance(project, Project) else project
    full = f"{SETTINGS_ROOT}.{slug}.{key}" if slug else f"{SETTINGS_ROOT}.{key}"
    row = _setting_row(db, full)
    if row is not None:
        db.delete(row)


def all_settings(db: Session, *, project: Optional[Project | str] = None) -> dict[str, Any]:
    """Every knob with its effective value — what the dashboard renders."""
    return {k: get_setting(db, k, project=project) for k in DEFAULTS}


# ═════════════════════════════════════════════════════════════════════════
#  PROJECTS
# ═════════════════════════════════════════════════════════════════════════

DEFAULT_PROJECT_SLUG = "tell-a-vision"

# WHICH PROCESSORS RUN ON THE WINDOWS NODE.
#
# A project declares how its images are made — 'photoshop' or 'gpt' — and the
# stage that performs it must claim ONLY its own kind. Photoshop needs a
# machine with Photoshop on it; generation runs in the web process and needs
# nothing but an API key.
#
# This existed only as an assumption until MUSIK arrived, at which point the
# node happily claimed its images, ran the movie project's painterly effect
# over them, and filed the results under the movie project's folder. The
# GPT worker was claiming the same rows from the other side at the same time.
#
# Add the new value here when a processor is added that the node performs.
# A processor missing from this tuple is simply never handed to a node, which
# is the safe direction to fail.
NODE_PROCESSORS = ("photoshop",)


# ═══════════════════════════════════════════════════════════════════════════
#  THE MARKETPLACES AN ACCOUNT CAN BELONG TO
# ═══════════════════════════════════════════════════════════════════════════
# One canonical spelling each, and the ONLY values `target_site` may take.
#
# It has to be a closed list because the name is load-bearing: it selects the
# reader, the sign-in selectors, the capability row that decides which panels
# the Earnings page shows, and it is copied onto every stored row. A typo
# produces an account that is silently inert — never read, absent from every
# total, and showing up in the site filter as a marketplace that does not
# exist. Nothing would say why.
#
# Adding one means adding it here, plus a reader and a capability row.
MARKETPLACES = ("fineartamerica", "teepublic")

# ════════════════════════════════════════════════════════════════════════════
#  HUMAN LABELS FOR THE THINGS A PROJECT PLUGS INTO
# ════════════════════════════════════════════════════════════════════════════
# A project stores machine keys — 'tmdb', 'brave', 'fineartamerica' — because
# those are stable and safe in a path. Screens need words. Keeping the mapping
# HERE means adding a marketplace is one line, and no template ever hardcodes
# the name of a site it happens to know about today.
#
# An unknown key falls back to itself rather than to a movie-project default,
# so a missing entry reads as an odd label rather than as a confident lie.
SITE_LABELS = {
    "tmdb":           "TMDB",
    "brave":          "Brave image search",
    "pinterest":      "Pinterest",
    "fineartamerica": "FineArtAmerica",
    "teepublic":      "TeePublic",
    "redbubble":      "Redbubble",
}

PROCESSOR_LABELS = {
    "photoshop": "Photoshop",
    "gpt":       "AI image generation",
}


# ═════════════════════════════════════════════════════════════════════════
#  PROJECT REGISTRY
# ═════════════════════════════════════════════════════════════════════════
#
# Projects are declared HERE, in code, and reconciled into the database on
# every startup. There is deliberately no screen for creating or renaming one.
#
# WHY NOT A FORM
# --------------
# Everything the operator tweaks day to day lives in the dashboard, and that
# rule stands. A project is not a tweak — it is a new pipeline. Standing one up
# means a source site, a Photoshop script, a keyword strategy, a title
# template, marketplace accounts and worker assignments, and those arrive as a
# code change anyway. A form that creates the row but none of the rest produces
# a half-built project that looks finished, which is worse than no form.
#
# The name is here for the same reason its folder name is derived from it:
# renaming a project renames its storage directory, and that should be a
# deliberate, reviewed, deployed act rather than a text box someone edits.
#
# ADDING OR RENAMING A PROJECT
# ----------------------------
# Add or edit an entry below, deploy. `sync_projects()` creates what's missing
# and updates the display fields of what exists. The SLUG is the identity and
# must never change once live — every per-project setting is stored under
# `pipeline.<slug>.<key>`, so changing it silently orphans them all and the
# project falls back to global defaults with no error.

PROJECT_DEFS: list[dict] = [
    {
        "slug":             DEFAULT_PROJECT_SLUG,     # 'tell-a-vision' — frozen, it is the identity
        "name":             "GR(Movie&Series)",
        "source_site":      "tmdb",
        "target_site":      "fineartamerica",
        "images_per_title": 3,
        "notes":            "Original workflow: TMDB posters -> Real Paint FX -> FineArtAmerica.",
        "item_noun":        "poster",
        "item_noun_plural": "posters",
        "processor":        "photoshop",
        "has_year":         1,
        "has_content_type": 1,
        "has_review_gate":  0,
        "search_mode":      "external",
    },
    {
        "slug":             "musik",
        "name":             "MUSIK",
        "source_site":      "brave",
        "target_site":      "fineartamerica",
        "images_per_title": 2,
        "notes":            "Music artists: Brave image search -> GPT Image 2 restyle -> FineArtAmerica.",
        "item_noun":        "image",
        "item_noun_plural": "images",
        "processor":        "gpt",
        # Searches inside the site, so no "Open TMDB" button.
        "search_mode":      "inpage",
        # Source resolution is irrelevant here: GPT redraws the image and the
        # result is upscaled to print size, so a red "640px wide" warning on
        # every artist would be pure noise.
        "settings":         {
            "review_min_width_px": 0,
            # No external source. Belt and braces alongside the search_mode
            # check in _source_search_url(): without this the project simply
            # inherits the global default, which is TMDB.
            "source_search_url": "",
            # "Carla Bruni #A" / "Carla Bruni #B". No year — artists have
            # none.
            #
            # The '#' is doing real work: an artist called "Alison A" sitting
            # beside a plain "Alison" makes a bare letter suffix ambiguous at
            # a glance in a list of 500 listings. "Alison #A" cannot be read
            # as part of a name.
            "title_template": "{title} #{letter}",
        },
        # One column of artist names — no year, no movie/tv distinction.
        "has_year":         0,
        "has_content_type": 0,
        # GPT output varies in a way Photoshop's deterministic effect does
        # not, so it gets an approval step before anything is listed.
        "has_review_gate":  1,
    },
]

# Fields sync_projects() will overwrite on an existing row. Deliberately
# excludes `slug` (identity), `process_weight` and `is_active` — those are
# operational levers the dashboard owns, and a deploy must not silently reset
# a project you turned off or re-weighted.
_SYNCED_FIELDS = ("name", "source_site", "target_site", "images_per_title", "notes",
                  "item_noun", "item_noun_plural", "processor",
                  "has_year", "has_content_type", "has_review_gate", "search_mode")


def sync_projects(db: Session) -> list[str]:
    """
    Reconcile PROJECT_DEFS into the projects table. Idempotent.

    Returns a list of human-readable changes, which main.py logs on startup so
    a rename is visible in `docker compose logs` rather than being invisible.
    """
    changes: list[str] = []

    for spec in PROJECT_DEFS:
        proj = db.query(Project).filter_by(slug=spec["slug"]).first()

        if proj is None:
            proj = Project(**{k: spec.get(k) for k in ("slug", *_SYNCED_FIELDS)})
            db.add(proj)
            db.flush()
            changes.append(f"created project '{spec['slug']}' ({spec['name']})")
            # NOTE: no `continue` here. An earlier version skipped straight to
            # the next spec, so a project created on one deploy never received
            # the setting overrides declared alongside it — they only landed on
            # the SECOND deploy, if at all.

        # Per-project setting overrides declared alongside the project. Only
        # written when absent, so a value you later change in the dashboard is
        # never stamped back over on the next deploy.
        for key, value in (spec.get("settings") or {}).items():
            existing = _setting_row(db, f"{SETTINGS_ROOT}.{spec['slug']}.{key}")
            if existing is None:
                set_setting(db, key, value, project=proj, by="registry")
                changes.append(f"{spec['slug']}.{key} set to {value!r}")

        for field in _SYNCED_FIELDS:
            if field not in spec:
                continue
            current = getattr(proj, field)
            if current != spec[field]:
                setattr(proj, field, spec[field])
                if field == "name":
                    changes.append(
                        f"renamed '{spec['slug']}': {current!r} -> {spec[field]!r}"
                    )
                else:
                    changes.append(f"{spec['slug']}.{field} -> {spec[field]!r}")

    return changes


def ensure_default_project(db: Session) -> Project:
    """
    Guarantee the primary project exists, and return it.

    Kept as its own function because it is called from query-scoping paths
    that must not depend on startup having run — a fresh database, a restored
    backup, or the dev setup tool all reach here first.
    """
    proj = db.query(Project).filter_by(slug=DEFAULT_PROJECT_SLUG).first()
    if proj is None:
        spec = next(s for s in PROJECT_DEFS if s["slug"] == DEFAULT_PROJECT_SLUG)
        proj = Project(**{k: spec.get(k) for k in ("slug", *_SYNCED_FIELDS)})
        db.add(proj)
        db.flush()
    return proj


def resolve_project(db: Session, project_id: Optional[int]) -> Project:
    """
    Map an optional project_id to a Project, defaulting to the primary one.
    Pipeline code calls this instead of assuming project 1, so the day a
    second niche appears nothing silently operates on the wrong data.
    """
    if project_id:
        proj = db.query(Project).filter_by(id=project_id).first()
        if proj is not None:
            return proj
    return ensure_default_project(db)


def project_for_title(db: Session, title: MasterTitle) -> Project:
    return resolve_project(db, title.project_id)


def review_gate_enabled(db: Session, project: Optional[Project]) -> bool:
    """
    Is the image review gate active for this project right now?

    Two separate questions, deliberately kept apart:

      Project.has_review_gate  — CAN this project have one. A structural fact
                                 about the pipeline: Photoshop is
                                 deterministic and has nothing to judge, so
                                 the movie project has no gate to switch on.

      gpt_review_required      — is it currently ON. A dashboard setting, so
                                 you can stop reviewing once you trust the
                                 output and start again if it drifts.

    Anything asking "should this image wait for approval" must come through
    here rather than reading either half on its own — a check that consults
    only the column ignores the switch, and one that consults only the
    setting would invent a review queue for a project that has no reviewer.
    """
    if project is None or not project.has_review_gate:
        return False
    return bool(int(get_setting(db, "gpt_review_required", project=project) or 0))


# ═════════════════════════════════════════════════════════════════════════
#  TEXT RENDERING (title / keywords / description templates)
# ═════════════════════════════════════════════════════════════════════════

_LETTERS = list(string.ascii_uppercase)

# Transliteration map carried over from the legacy uploader. Marketplaces
# reject most non-ASCII in titles, so we fold accents rather than strip them
# (which would turn "Amélie" into "Amlie").
_ACCENT_MAP = {
    "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe", "ß": "ss",
    "Ø": "O", "ø": "o", "Ð": "D", "ð": "d", "Þ": "T", "þ": "t",
    "Ł": "L", "ł": "l", "İ": "I", "ı": "i",
}


def letter_for_index(index: int) -> str:
    """0→A … 25→Z, then Z1, Z2 … so a title with >26 images can't collide."""
    if index < 26:
        return _LETTERS[index]
    return f"Z{index - 25}"


# ═════════════════════════════════════════════════════════════════════════
#  MARKETPLACE TITLE NORMALISATION
# ═════════════════════════════════════════════════════════════════════════
#
# FineArtAmerica SILENTLY rewrites artwork titles. It does not warn, it does
# not error — it just saves something different from what you sent. Measured
# on 2026-08-13 by submitting all 165 distinct non-ASCII characters in the
# celebrity database and reading back what saved.
#
# WHY THIS RUNS HERE AND NOT IN THE UPLOADER
# The stored `remote_title` must equal what the listing actually shows. If we
# store "blink-182" while FAA shows "Blink182", the reconciliation scanner
# compares the two and reports a mismatch on every single listing. Normalise
# at render time and the database records reality.
#
# THE RULES, AS MEASURED
#   · Latin-1 letters and s/z carons fold to ASCII        é -> e   ß -> Ss
#   · Almost everything else is deleted: apostrophes, quotes, UNICODE hyphens
#     and dashes, Eastern European diacritics (ł ć š ż), Turkish dotless i,
#     macrons, symbols, arrows, superscripts
#   · The exceptions are the ASCII hyphen '-' and '#', both of which survive.
#     Neither appeared in the original test — it used the 165 non-ASCII
#     characters found in the database, so no ASCII punctuation was covered.
#     Both were confirmed separately by submitting them.
#   · Max 100 characters, truncated silently
#   · The first character is upper-cased
#   · A title that comes out EMPTY is rejected with an HTML error page
#     reading "Please use only A-Z in your artwork title"
#
# Case is NOT reliably preserved: Á->A but Ë->e, È->e, Ì->i, Õ->o. There is no
# rule to derive, so the table below is the observed behaviour, not an
# inference. Do not "tidy" it.

_FAA_FOLD = {
    "Á": "A", "Â": "A", "Ä": "A", "Å": "A", "Æ": "A", "Ç": "C",
    "È": "e", "É": "E", "Ë": "e", "Ì": "i", "Í": "I", "Ï": "I",
    "Ñ": "N", "Ó": "O", "Ô": "O", "Õ": "o", "Ö": "O", "Ø": "O",
    "Ú": "U", "Ü": "U", "ß": "Ss", "à": "a", "á": "a", "â": "a",
    "ã": "a", "ä": "a", "å": "a", "æ": "a", "ç": "c", "è": "e",
    "é": "e", "ê": "e", "ë": "e", "ì": "i", "í": "i", "î": "i",
    "ï": "i", "ð": "o", "ñ": "n", "ò": "o", "ó": "o", "ô": "o",
    "õ": "o", "ö": "o", "ø": "o", "ù": "u", "ú": "u", "û": "u",
    "ü": "u", "ý": "y", "þ": "b", "ÿ": "y", "Š": "S", "š": "s",
    "Ž": "Z", "ž": "z",}

# Characters FAA keeps as-is. Everything not here and not in _FAA_FOLD is
# deleted — including the apostrophe, which is the surprising part.
#
# The ORDINARY hyphen-minus (U+002D) survives — confirmed by submitting one.
# Note that the unicode hyphens and dashes do NOT: U+2010 in "blink‐182" is
# deleted, so that name lists as "Blink182". Only this exact byte is safe,
# which is why the MUSIK title template uses a typed "-" and nothing else.
_MARKETPLACE_KEEP = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -#"
)

# The ordinary hyphen and '#' both SURVIVE — confirmed by submitting them,
# 2026-08-13. Neither was in the original character test, which covered the
# 165 non-ASCII characters in the database and no ASCII punctuation at all.
#
# So the rule is not "all punctuation is deleted", as the first pass through
# the test data suggested. It is closer to "most punctuation is deleted, and
# these two are not". Anything else stays out of this set until it has been
# submitted and read back — the cost of guessing wrong is that stored titles
# stop matching live listings, and the reconciliation scanner reports
# thousands of false mismatches.

MARKETPLACE_TITLE_MAX = 100

# OUR substitution, not FAA's behaviour — kept in a separate table for exactly
# that reason. FAA DELETES every one of these, so "blink‐182" (U+2010 HYPHEN,
# not an ordinary one) would list as "Blink182". 259 artist names carry a
# unicode dash of some kind; swapping it for the plain hyphen that FAA does
# keep gives "Blink-182", which is how the band spells it anyway.
#
# This is safe only because the swap happens BEFORE we send: what we store as
# remote_title is still exactly what the listing shows, so the reconciliation
# scanner has nothing to disagree about.
_DASH_FOLD = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
    "−": "-",   # MINUS SIGN, which turns up in a handful of names
}


def clean_for_marketplace(text: str, *, max_length: int = MARKETPLACE_TITLE_MAX) -> str:
    """
    Render a title exactly as the marketplace will store it.

    Truncation is at a WORD boundary rather than mid-word as FAA does — the
    result still fits, and "Bulgarian State Radio And Television Female Vocal"
    reads better than "...Female Voc".
    """
    text = (text or "").replace("&", "AND")
    for bad, good in _DASH_FOLD.items():
        text = text.replace(bad, good)
    out = []
    for ch in text:
        if ch in _MARKETPLACE_KEEP:
            out.append(ch)
        elif ch in _FAA_FOLD:
            out.append(_FAA_FOLD[ch])
        # else: deleted, exactly as the marketplace would
    cleaned = re.sub(r"\s+", " ", "".join(out)).strip()

    if len(cleaned) > max_length:
        cut = cleaned[:max_length]
        if " " in cut:
            cut = cut[:cut.rfind(" ")]
        cleaned = cut.strip()

    return (cleaned[:1].upper() + cleaned[1:]) if cleaned else ""


def tidy_separators(text: str) -> str:
    """
    Clean up hyphens left stranded by the stripping above.

    "!!! - 1" loses its whole name and would list as "- 1"; "AC/DC -- 2" can
    collapse two separators together. Neither is wrong enough for FAA to
    reject, which is precisely why it has to be caught here — it would list
    quietly and look sloppy on the storefront.

    Deliberately NOT part of clean_for_marketplace(): that function is also
    used to MEASURE the suffix budget, and stripping the leading "- " off a
    measurement would understate the suffix and let truncation eat it.
    """
    # Spacing is left EXACTLY as written. An earlier version normalised every
    # hyphen to " - " and turned "Jay-Z" into "Jay - Z"; a hyphen inside a name
    # is part of the name, and only the separator we added is ours to tidy.
    out = re.sub(r"-{2,}", "-", text or "")             # "--" from an empty field
    out = re.sub(r"(\s-\s)(?:\s*-\s*)+", r"\1", out)    # repeated separators
    out = out.strip(" -")
    out = re.sub(r"\s+", " ", out).strip()
    return (out[:1].upper() + out[1:]) if out else ""


def validate_marketplace_title(original: str, rendered: str) -> Optional[str]:
    """
    Is this title safe to send? Returns a reason to hold it, or None.

    Checked BEFORE dispatch, because a title that normalises to nothing is
    rejected by FAA with an HTML page rather than an HTTP error — the node
    would count it as a failure, retry it, and fail identically. Better to
    never send it and put it in front of the admin with an editable field.
    """
    if not rendered:
        return "The title contains no characters FineArtAmerica accepts."

    # Judge the NAME, not the assembled title. The template contributes an
    # index, so "1" always survives and the title is never empty — checking
    # the whole string would pass a name that vanished completely.
    original = (original or "").strip()
    name_kept = clean_for_marketplace(original)

    if not name_kept:
        return (f"Nothing of {original!r} survives what FineArtAmerica accepts, "
                f"so the listing would be named after its number alone.")

    # NOT a letters test. 104 artists in the database are digits only — 311,
    # 112, 702, 54-40 — and those are their actual names, not damage.
    if len(name_kept) < 2:
        return f"{original!r} reduces to {name_kept!r}, which is too short to list."

    # More than half lost usually means an abbreviated name collapsing —
    # "M.I.A." to "MIA" is fine, "MØ" to "M" is not.
    if len(name_kept) < len(original) * 0.5 and len(name_kept) < 6:
        return (f"{original!r} becomes {name_kept!r} — most of the name is lost. "
                f"Edit it before listing.")
    return None


def _title_vars(title: MasterTitle, poster: SavedPoster, index: int) -> dict[str, Any]:
    return {
        "title":        title.title or "",
        "year":         title.year or "",
        "letter":       letter_for_index(index),
        "index":        index + 1,
        "content_type": title.content_type or "",
        "external_id":  title.external_id if title.external_id is not None else "",
        "description":  title.description or "",
        "filename":     poster.filename or "",
    }


def _render(template: str, variables: dict[str, Any]) -> str:
    """
    Substitute {placeholders}. Unknown placeholders are left intact rather
    than raising, so a typo in a dashboard-edited template degrades to
    visible text instead of breaking an unattended run.
    """
    out = template or ""
    for key, value in variables.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def render_remote_title(
    db: Session, title: MasterTitle, poster: SavedPoster, index: int,
    *, project: Optional[Project] = None,
) -> str:
    project = project or project_for_title(db, title)
    template = get_setting(db, "title_template", project=project)
    vars_ = _title_vars(title, poster, index)

    # ── The 100-character budget belongs to the NAME, not the suffix ────────
    # FAA truncates at 100 silently. Letting it cut wherever it lands can eat
    # the " - 1994 A" that distinguishes one image from another — two listings
    # would end up with the same title and nothing to tell them apart.
    #
    # So: render the template with an empty title to measure what the suffix
    # costs, give the name whatever is left, and assemble.
    suffix_only = clean_for_marketplace(
        _render(template, {**vars_, "title": ""}), max_length=MARKETPLACE_TITLE_MAX)
    budget = max(8, MARKETPLACE_TITLE_MAX - len(suffix_only))

    trimmed = clean_for_marketplace(str(vars_.get("title") or ""), max_length=budget)
    return tidy_separators(
        clean_for_marketplace(_render(template, {**vars_, "title": trimmed})))


def render_keywords(
    db: Session, title: MasterTitle,
    *, project: Optional[Project] = None,
) -> str:
    """
    Marketplace keyword string. The site itself derives keywords from the
    title, so we only supply the static niche tail — matching the legacy
    behaviour of appending to whatever the form pre-filled.
    """
    project = project or project_for_title(db, title)
    return get_setting(db, "keywords_static", project=project)


def render_description(
    db: Session, title: MasterTitle,
    *, project: Optional[Project] = None,
) -> str:
    project = project or project_for_title(db, title)
    source = get_setting(db, "description_source", project=project)
    if source == "master":
        return (title.description or "").strip()
    template = get_setting(db, "description_template", project=project)
    return _render(template, {
        "title": title.title or "",
        "year": title.year or "",
        "description": title.description or "",
        "content_type": title.content_type or "",
    }).strip()


def render_process_script(db: Session, *, project: Optional[Project] = None) -> str:
    """
    The JSX the worker will run, with the dashboard's numeric settings
    substituted into the {{PLACEHOLDER}} slots. Returned to the node via the
    API so Photoshop settings stay editable without redeploying anything.
    """
    script = get_setting(db, "process_script", project=project)
    substitutions = {
        "WORK_WIDTH":      get_setting(db, "work_width", project=project),
        "OUTPUT_WIDTH":    get_setting(db, "output_width", project=project),
        "JPEG_QUALITY":    get_setting(db, "jpeg_quality", project=project),
        "SHARPEN_AMOUNT":  get_setting(db, "sharpen_amount", project=project),
        "SHARPEN_RADIUS":  get_setting(db, "sharpen_radius", project=project),
        "SHARPEN_NOISE":   get_setting(db, "sharpen_noise", project=project),
        # JSX string literal — normalise separators so a Windows-style path
        # pasted into the dashboard doesn't produce escape sequences.
        "FX_SCRIPT_PATH":  str(get_setting(db, "fx_script_path", project=project)).replace("\\", "/"),
    }
    for name, value in substitutions.items():
        script = script.replace("{{" + name + "}}", str(value))
    return script


def script_version(db: Session, *, project: Optional[Project] = None) -> str:
    """
    Short hash of the effective script. Stored on each ProcessedImage so you
    can tell which images predate an effect change and reprocess just those.
    """
    rendered = render_process_script(db, project=project)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


# Characters Windows refuses in a path component. Slashes are excluded too:
# a project named "Movies/TV" must not silently become two directory levels.
_PATH_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _path_token(value: str) -> str:
    """
    Make one path component out of a name the admin typed in a text box.

    Project and marketplace names go straight into a filesystem path on a
    Windows node, and the admin can name a project anything. Without this,
    a colon or a slash produces either a crash mid-batch or — worse — a file
    written somewhere nobody looks for it.

    Spaces are kept (Windows is fine with them and the folders are read by a
    human); everything Windows rejects is collapsed to a single dash.
    """
    cleaned = _PATH_UNSAFE.sub("-", (value or "").strip())
    # Trailing dots and spaces are legal to write but unopenable on Windows.
    cleaned = cleaned.rstrip(". ")
    return cleaned or "unnamed"


def storage_path_for(
    db: Session, title: MasterTitle, poster: SavedPoster,
    *, project: Optional[Project] = None,
) -> tuple[str, str]:
    """
    Return (relative_path, filename) for a poster's processed derivative.

    Relative to the configured storage root — never absolute — so the archive
    can be remounted or moved between providers without rewriting the DB.
    """
    project = project or project_for_title(db, title)
    suffix = get_setting(db, "output_suffix", project=project)
    layout = get_setting(db, "storage_layout", project=project)

    stem, _ext = os.path.splitext(poster.filename or "image.jpg")
    filename = f"{stem}{suffix}.jpg"

    rel = _render(layout, {
        "date":         (poster.original_save_date or local_today()).isoformat(),
        "title_folder": poster.title_folder_path or "",
        "filename":     filename,
        # {project} is the human name, not the slug — these folders are opened
        # by hand on a Windows box, and "MovieSeries" beats "tell-a-vision"
        # when you're looking for something at 1am. {project_slug} is still
        # available for anyone who wants the stable identifier.
        "project":      _path_token(project.name or project.slug),
        "project_slug": project.slug,
        "site":         _path_token(project.target_site or "unknown"),
        "username":     poster.username or "",
        "external_id":  title.external_id if title.external_id is not None else "",
    })
    return rel.replace("\\", "/"), filename


# ═════════════════════════════════════════════════════════════════════════
#  GREENLIGHT
# ═════════════════════════════════════════════════════════════════════════

# Poster states that mean "already in the pipeline". Greenlighting must never
# touch these: re-promoting a poster that is processed or uploaded would queue
# duplicate Photoshop work and duplicate marketplace listings.
IN_PIPELINE_STATES = frozenset({
    "greenlit", "processing", "processed", "uploading", "uploaded",
    "failed_processing", "failed_upload",
    # 'unusable' is terminal by admin decision. Greenlighting a date range
    # again must never drag it back in — that would silently undo a call the
    # admin made deliberately, and re-spend money to produce the same bad
    # image. Only the admin's own "return to pipeline" action reverses it.
    "unusable",
})


def greenlight_titles(
    db: Session, title_ids: Iterable[int], *, by: str, reason: str = "manual",
) -> dict[str, int]:
    """
    Promote completed titles into the pipeline.

    Decided PER POSTER, not per title. A poster is promoted only from a
    not-yet-in-pipeline state (NULL / "" / skipped); anything already greenlit,
    processing, processed, uploading or uploaded is left exactly as it is.

    Two consequences that both matter:

      * Re-running this is genuinely safe. Greenlighting a date range twice,
        or the payment hook firing on an overlapping range, cannot re-queue
        work that is already moving — so no duplicate Photoshop runs and no
        duplicate listings.

      * A title that gained a NEW poster after being greenlit still gets that
        poster picked up. An earlier version short-circuited on
        `title.greenlit_at is not None` and silently stranded those posters
        forever.

    `reason` is recorded on the title as `greenlit_source`. It used to be
    accepted and thrown away, which made "was this released because it was
    paid for, or because someone clicked the button?" unanswerable — and that
    is the one question worth asking, since a manual release can put unpaid
    work onto the marketplace.

    Returns counts of titles that had at least one poster promoted, titles
    where there was nothing left to do, and the number of posters promoted.
    """
    ids = [int(i) for i in title_ids]
    if not ids:
        return {"greenlit": 0, "skipped": 0, "posters": 0}

    titles = (
        db.query(MasterTitle)
          .filter(MasterTitle.id.in_(ids), MasterTitle.status == "complete")
          .all()
    )

    now = datetime.utcnow()
    greenlit = skipped = poster_count = 0
    default_project_id: Optional[int] = None

    for title in titles:
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.master_title_id == title.id,
                      SavedPoster.deleted_at.is_(None))
              .all()
        )
        if not posters:
            skipped += 1
            continue

        promoted = 0
        for poster in posters:
            if poster.pipeline_status in IN_PIPELINE_STATES:
                continue          # already moving — leave it alone
            poster.pipeline_status = "greenlit"
            poster.process_attempts = 0
            poster.process_error = None
            promoted += 1

        if promoted == 0:
            # Every poster is already in the pipeline. Nothing to do, but make
            # sure the title carries a greenlight stamp so the UI is honest
            # about how it got there.
            if title.greenlit_at is None:
                title.greenlit_at = now
                title.greenlit_by = by
                title.greenlit_source = reason
            skipped += 1
            continue

        # First promotion on this title stamps it; later top-ups keep the
        # original stamp so "when was this approved" stays meaningful.
        if title.greenlit_at is None:
            title.greenlit_at = now
            title.greenlit_by = by
            title.greenlit_source = reason
        if title.project_id is None:
            if default_project_id is None:
                default_project_id = ensure_default_project(db).id
            title.project_id = default_project_id

        recompute_title_status(db, title)
        greenlit += 1
        poster_count += promoted

    return {"greenlit": greenlit, "skipped": skipped, "posters": poster_count}


def greenlight_date_range(
    db: Session, start: date, end: date, *, by: str,
    worker_id: Optional[int] = None, reason: str = "manual",
) -> dict[str, int]:
    """
    Greenlight every completed title whose save date falls in [start, end].

    Note this does NOT pre-filter on `greenlit_at IS NULL`. Deciding what to
    promote is greenlight_titles' job and it does so per poster, so passing
    already-greenlit titles through is both safe and necessary — that's how a
    title which gained a new poster gets topped up.
    """
    query = (
        db.query(MasterTitle.id)
          .filter(MasterTitle.status == "complete",
                  MasterTitle.original_save_date >= start,
                  MasterTitle.original_save_date <= end)
    )
    if worker_id:
        query = query.filter(MasterTitle.claimed_by_id == worker_id)
    return greenlight_titles(db, [r[0] for r in query.all()], by=by, reason=reason)


def awaiting_greenlight_poster_filter():
    """
    The canonical "not yet in the pipeline" predicate for live posters.

    Shared by the funnel, the greenlight queue and the title browser so all
    three agree. Poster-based rather than keyed off MasterTitle.greenlit_at,
    because a title can be greenlit and still hold a poster added afterwards.
    """
    return or_(SavedPoster.pipeline_status.is_(None),
               SavedPoster.pipeline_status == "")


def greenlight_for_payment_run(db: Session, run, *, by: str) -> dict[str, int]:
    """
    Hook called from the payments flow. Greenlights exactly the posters that
    were just paid for — including any back-pay days folded into the run —
    which is why it works off poster_ids_json rather than the period bounds.

    Respects `greenlight_mode`: a no-op when the admin has chosen manual-only.
    """
    mode = get_setting(db, "greenlight_mode")
    if mode not in ("payment", "both"):
        return {"greenlit": 0, "skipped": 0, "posters": 0, "disabled": 1}

    try:
        poster_ids = json.loads(run.poster_ids_json or "[]")
    except (TypeError, ValueError):
        poster_ids = []
    if not poster_ids:
        return {"greenlit": 0, "skipped": 0, "posters": 0}

    title_ids = [
        r[0] for r in
        db.query(SavedPoster.master_title_id)
          .filter(SavedPoster.id.in_(poster_ids))
          .distinct()
          .all()
    ]
    return greenlight_titles(db, title_ids, by=by, reason=f"payment:{run.id}")


def ungreenlight_titles(db: Session, title_ids: Iterable[int]) -> int:
    """
    Pull titles back out of the pipeline. Only affects images that haven't
    been processed yet — anything already in storage or uploaded stays put,
    because un-greenlighting is a scheduling decision, not a retraction.
    """
    ids = [int(i) for i in title_ids]
    if not ids:
        return 0

    titles = db.query(MasterTitle).filter(MasterTitle.id.in_(ids)).all()
    affected = 0
    for title in titles:
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.master_title_id == title.id,
                      SavedPoster.pipeline_status.in_(("greenlit", "failed_processing")))
              .all()
        )
        for poster in posters:
            poster.pipeline_status = None
            poster.claimed_at = None
            poster.claimed_by = None
        title.greenlit_at = None
        title.greenlit_by = None
        recompute_title_status(db, title)
        affected += 1
    return affected


# ═════════════════════════════════════════════════════════════════════════
#  STATUS ROLLUP
# ═════════════════════════════════════════════════════════════════════════

# Order matters: the rollup reports the least-advanced meaningful stage so
# the dashboard shows work as "processing" until every image is past it.
_STAGE_ORDER = [
    "failed_processing", "failed_upload", "greenlit", "processing",
    "processed", "uploading", "uploaded",
]


def recompute_title_status(db: Session, title: MasterTitle) -> Optional[str]:
    """
    Refresh MasterTitle.pipeline_status from its live posters.

    This is a denormalized convenience for listing thousands of titles; the
    per-poster rows remain authoritative. Call it after any mutation that
    changes a poster's stage.

    The flush is REQUIRED, not defensive. SessionLocal is configured with
    `autoflush=False`, so the query below would otherwise read the pre-change
    values still in the database and compute a status from stale data. Callers
    normally invoke this immediately after assigning poster.pipeline_status in
    memory — without the flush, greenlighting a title left every poster
    correctly marked but the title's rollup reset to NULL, which made the
    Pipeline tab show freshly-greenlit work as "not greenlit".
    """
    db.flush()

    statuses = [
        s for (s,) in
        db.query(SavedPoster.pipeline_status)
          .filter(SavedPoster.master_title_id == title.id,
                  SavedPoster.deleted_at.is_(None))
          .all()
    ]
    active = [s for s in statuses if s and s != "skipped"]

    if not active:
        title.pipeline_status = None
        return None

    if all(s == "uploaded" for s in active):
        title.pipeline_status = "uploaded"
    elif any(s and s.startswith("failed") for s in active):
        title.pipeline_status = "failed"
    else:
        for stage in _STAGE_ORDER:
            if stage in active:
                title.pipeline_status = stage
                break
        else:
            title.pipeline_status = "partial"

    return title.pipeline_status


# ═════════════════════════════════════════════════════════════════════════
#  WORK DISPATCH — Photoshop stage
# ═════════════════════════════════════════════════════════════════════════

def release_claims_for_node(db: Session, node_name: str) -> dict[str, int]:
    """
    Release EVERY batch claim held by one named node, right now.

    For the node's first hello after starting: a process that has just begun
    cannot still be running a batch it claimed before, so its posters go back
    to 'greenlit' and its upload rows back to 'pending' immediately instead
    of waiting out the reaper's timeout.

    The v1.27.0 handshake fix did exactly this for PIPELINE JOBS and left the
    batch claims to the reaper — the same one-of-two-siblings gap as the
    reaper's own liveness check. Reviewed 2026-08-27. With `claim_timeout_min`
    at 45, every one of the 65 restarts in the August logs left up to 45
    minutes of claimed work sitting still; jobs were freed in seconds while
    batches waited.

    Same resets as the reaper, in one place, so the two paths cannot drift.
    """
    posters = (
        db.query(SavedPoster)
          .filter(SavedPoster.pipeline_status == "processing",
                  SavedPoster.claimed_by == node_name)
          .all()
    )
    for poster in posters:
        poster.pipeline_status = "greenlit"
        poster.claimed_at = None
        poster.claimed_by = None

    rows = (
        db.query(UploadTracking)
          .filter(UploadTracking.status == "uploading",
                  UploadTracking.claimed_by == node_name)
          .all()
    )
    for row in rows:
        row.status = "pending"
        row.claimed_at = None
        row.claimed_by = None

    return {"posters": len(posters), "uploads": len(rows)}


def reap_stale_claims(db: Session) -> dict[str, int]:
    """
    Release work claimed by a node that never reported back (crash, reboot,
    network drop). Without this a dead node's claims would block the queue
    forever. Runs before every dispatch, so recovery needs no intervention.
    """
    timeout = int(get_setting(db, "claim_timeout_min"))
    cutoff = datetime.utcnow() - timedelta(minutes=timeout)

    # ── A LONG BATCH IS NOT AN ABANDONED ONE, EITHER ────────────────────
    #
    # The same mistake the jobs section below already documents, on the two
    # tables above it. `claimed_at` is when the batch STARTED. A batch that
    # takes longer than the timeout gets its remaining items released while
    # the node is actively working on them, and the next claim can hand the
    # same rows out again — which for an upload means a duplicate listing
    # on a real marketplace.
    #
    # MEASURED 2026-08-27 from 13 days of node logs: an upload takes a
    # median of 16-22 seconds, a p90 of 55-59 seconds, and outliers of five
    # to nine minutes appear on every single day. `upload_batch_size` is 40.
    # At p90 that is 37 minutes of a 45-minute timeout, and one outlier
    # tips it over.
    #
    # Liveness is the last thing the NODE said, not when it started. Every
    # item it finishes leaves a stamped, node-attributed row — `uploaded_at`
    # on the tracking row for uploads, and a ProcessedImage carrying
    # `processed_by` and `created_at` for Photoshop work — so a node working
    # steadily through a long batch is visibly alive even though the item in
    # its hand is not.
    #
    # BOTH signals, deliberately. The first version of this fix read only
    # `uploaded_at`, while its own comment claimed processing counted too —
    # so a node mid-Photoshop-batch, having uploaded nothing for 45 minutes
    # because it was busy PAINTING, was invisible to the very check meant to
    # protect it, and its remaining posters were released mid-stride. The
    # same half-fix shape as the jobs reaper before it: reviewed 2026-08-27,
    # one day after shipping, by asking what evidence each work type leaves.
    live_nodes = {
        name for (name,) in
        db.query(UploadTracking.claimed_by)
          .filter(UploadTracking.claimed_by.isnot(None),
                  UploadTracking.uploaded_at.isnot(None),
                  UploadTracking.uploaded_at >= cutoff)
          .distinct().all()
        if name
    } | {
        name for (name,) in
        db.query(ProcessedImage.processed_by)
          .filter(ProcessedImage.processed_by.isnot(None),
                  ProcessedImage.created_at >= cutoff)
          .distinct().all()
        if name
    }

    posters = [
        p for p in db.query(SavedPoster)
                     .filter(SavedPoster.pipeline_status == "processing",
                             SavedPoster.claimed_at.isnot(None),
                             SavedPoster.claimed_at < cutoff).all()
        if p.claimed_by not in live_nodes
    ]
    for poster in posters:
        poster.pipeline_status = "greenlit"
        poster.claimed_at = None
        poster.claimed_by = None

    rows = [
        r for r in db.query(UploadTracking)
                     .filter(UploadTracking.status == "uploading",
                             UploadTracking.claimed_at.isnot(None),
                             UploadTracking.claimed_at < cutoff).all()
        if r.claimed_by not in live_nodes
    ]
    for row in rows:
        row.status = "pending"
        row.claimed_at = None
        row.claimed_by = None

    # ── A LONG JOB IS NOT AN ABANDONED ONE ──────────────────────────────
    #
    # This compared `started_at` against the timeout, so any job running
    # longer than 45 minutes was declared abandoned no matter how healthily
    # it was working. Switching a TeePublic account's designs off takes
    # about an hour — measured — and reports once per design, so at minute
    # 45 a job with 8 designs left was killed mid-stride. Those 8 were then
    # handed out again and came back "already inactive".
    #
    # Liveness is now the last thing the job SAID. `started_at` is only the
    # fallback for a job that has not managed to say anything at all, which
    # is the case this check was actually written for: a node that died
    # between claiming and starting.
    jobs = [
        j for j in db.query(PipelineJob)
                     .filter(PipelineJob.status == "running").all()
        if (j.last_report_at or j.started_at or datetime.utcnow()) < cutoff
    ]
    for job in jobs:
        job.status = "error"
        job.error = ("Abandoned — the worker node stopped reporting for "
                     f"{timeout} minutes.")
        job.finished_at = datetime.utcnow()

    return {"posters": len(posters), "uploads": len(rows), "jobs": len(jobs)}


def claim_process_batch(
    db: Session, *, node: str, limit: Optional[int] = None,
    project_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Hand a worker node its next Photoshop batch and mark those posters
    claimed in the same transaction.

    Ordering is oldest-save-date-first so the backlog drains chronologically
    and a title's images stay together.
    """
    reap_stale_claims(db)
    project = resolve_project(db, project_id)

    # Drained or halted: hand out nothing. Work already claimed is NOT
    # recalled — the node finishes what it holds and reports back, which is
    # the whole point of draining rather than stopping.
    if not intake_open(db, project):
        return []

    limit = limit or int(get_setting(db, "process_batch_size", project=project))
    max_attempts = int(get_setting(db, "process_max_attempts", project=project))

    # ── The node only does Photoshop ────────────────────────────────────
    #
    # This filter is not an optimisation, it is a correctness fix. Without
    # it the Windows node claimed GREENLIT work from EVERY active project,
    # including ones whose processor is 'gpt' — so MUSIK images were opened
    # in Photoshop, run through the movie project's JSX, and filed under
    # fineartamerica/GR(Movie&Series)/processed/.
    #
    # Worse, gpt_worker was claiming the same rows from the other side, so
    # the two stages raced for them.
    #
    # Asking the PROJECT what its processor is keeps this correct for
    # project three without another edit here.
    if project_id and project.processor not in NODE_PROCESSORS:
        return []

    def pending_query(only_project: Optional[int]):
        q = (
            db.query(SavedPoster, MasterTitle)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.deleted_at.is_(None),
                      SavedPoster.process_attempts < max_attempts,
                      or_(SavedPoster.pipeline_status == "greenlit",
                          SavedPoster.pipeline_status == "failed_processing"))
        )
        if only_project:
            # NULL project_id belongs to the default project only. Without
            # the second argument every project's dispatcher would pick up
            # the 101,605 unassigned movie rows — a celebrity node would be
            # handed movie posters and upload them to the wrong account.
            q = q.filter(project_scope(only_project,
                                       default_project_id=_default_project_id(db)))
        return q.order_by(SavedPoster.original_save_date.asc(),
                          MasterTitle.external_id.asc().nullslast(),
                          SavedPoster.id.asc())

    if project_id:
        # Node pinned to one project — no sharing to do.
        rows = pending_query(project_id).limit(limit).all()
    else:
        # ── Share the batch between projects that have work ─────────────
        #
        # Taking the globally oldest images looks fair but isn't: a large older
        # backlog in one niche blocks every newer niche entirely until it
        # drains. Instead each project with pending work gets a slice of the
        # batch, sized by its process_weight, and stays oldest-first within its
        # own slice.
        #
        # With a single project this is identical to the simple query above.
        active = [
            p for p in db.query(Project)
                         .filter(Project.is_active == 1,
                                 Project.processor.in_(NODE_PROCESSORS))
                         .all()
            if pending_query(p.id).limit(1).first() is not None
        ]

        # ── A batch may only mix projects that run the SAME script ──────
        #
        # The node fetches one script per batch and rewrites its local .jsx
        # when the version changes. Handing it images from two projects with
        # different scripts would silently process half of them with the
        # wrong effect — and the output would look plausible, which is the
        # worst kind of wrong.
        #
        # Today every Photoshop project shares one script, so this changes
        # nothing. It becomes load-bearing the moment a second one has its
        # own effect, which is exactly when nobody would think to check.
        if len(active) > 1:
            # Compared as RENDERED, not as the stored template. Two projects
            # can share one script and still differ in output width or
            # sharpening, and those are substituted in at render time — so
            # the template being equal proves nothing.
            lead_script = render_process_script(db, project=active[0])
            active = [
                p for p in active
                if render_process_script(db, project=p) == lead_script
            ]

        if not active:
            # NOTHING for a node to do. Returned explicitly, because the
            # obvious-looking `pending_query(None)` means "do not filter by
            # project at all" — so an empty list of eligible projects became
            # a query over EVERY project, and the node was handed the GPT
            # project's queue the moment Photoshop ran out of work.
            #
            # "No projects match" and "no project specified" are opposite
            # instructions that looked identical at the call site.
            rows = []
        elif len(active) == 1:
            rows = pending_query(active[0].id).limit(limit).all()
        else:
            total_weight = sum(max(1, p.process_weight or 1) for p in active)
            rows = []
            for index, proj in enumerate(active):
                weight = max(1, proj.process_weight or 1)
                # Everyone with work gets at least one slot, so a low-weight
                # project can never be starved outright.
                share = max(1, round(limit * weight / total_weight))
                # Last project mops up the remainder, so rounding never leaves
                # the batch short.
                if index == len(active) - 1:
                    share = max(1, limit - len(rows))
                if len(rows) >= limit:
                    break
                share = min(share, limit - len(rows))
                rows.extend(pending_query(proj.id).limit(share).all())

    now = datetime.utcnow()
    batch: list[dict[str, Any]] = []

    for poster, title in rows:
        poster.pipeline_status = "processing"
        poster.claimed_at = now
        poster.claimed_by = node
        # The path must come from the project THIS TITLE belongs to, not from
        # the batch-level `project`. That one is resolve_project(None) when a
        # node asks for shared work, which is the DEFAULT project — so every
        # image in a mixed batch was filed under the movie project's folder
        # regardless of where it came from.
        row_project = project_for_title(db, title)
        rel_path, filename = storage_path_for(db, title, poster, project=row_project)
        batch.append({
            "poster_id":     poster.id,
            "master_id":     title.id,
            "external_id":   title.external_id,
            "title":         title.title,
            "year":          title.year,
            "source_url":    f"/api/pipeline/source/{poster.id}",
            "source_filename": poster.filename,
            "storage_path":  rel_path,
            "output_filename": filename,
            "attempt":       poster.process_attempts + 1,
        })
        recompute_title_status(db, title)

    return batch


def report_processed(
    db: Session, *, poster_id: int, node: str, storage_path: str,
    filename: str, file_size: Optional[int] = None,
    width: Optional[int] = None, height: Optional[int] = None,
    duration_ms: Optional[int] = None, version: Optional[str] = None,
) -> ProcessedImage:
    """
    Record a successful Photoshop run and advance the poster to `processed`.

    Supersedes any previous derivative (is_current=0) rather than deleting
    it, so a reprocess keeps the audit trail and you can tell what changed.
    Also seeds pending upload rows for every enabled account in the project,
    which is what makes a new marketplace account automatically pick up the
    whole back catalogue.
    """
    poster = db.query(SavedPoster).filter_by(id=poster_id).first()
    if poster is None:
        raise ValueError(f"Poster {poster_id} not found")

    title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
    project = project_for_title(db, title) if title else ensure_default_project(db)

    db.query(ProcessedImage).filter(
        ProcessedImage.saved_poster_id == poster_id,
        ProcessedImage.is_current == 1,
    ).update({"is_current": 0}, synchronize_session=False)

    processed = ProcessedImage(
        saved_poster_id=poster_id,
        project_id=project.id,
        storage_path=storage_path,
        filename=filename,
        file_size=file_size,
        output_width=width,
        output_height=height,
        script_version=version or script_version(db, project=project),
        processed_by=node,
        duration_ms=duration_ms,
        is_current=1,
    )
    db.add(processed)
    db.flush()

    poster.pipeline_status = "processed"
    poster.process_error = None
    poster.claimed_at = None
    poster.claimed_by = None

    if title:
        ensure_upload_rows(db, poster=poster, title=title,
                           processed=processed, project=project)
        recompute_title_status(db, title)

    return processed


def report_process_failure(
    db: Session, *, poster_id: int, node: str, error: str,
) -> None:
    """
    Record a Photoshop failure. The poster goes to `failed_processing`, which
    the dispatcher retries until `process_max_attempts`; past that it stays
    put and surfaces in the dashboard's failure list for a human look.
    """
    poster = db.query(SavedPoster).filter_by(id=poster_id).first()
    if poster is None:
        return

    poster.process_attempts = (poster.process_attempts or 0) + 1
    poster.process_error = (error or "")[:4000]
    poster.pipeline_status = "failed_processing"
    poster.claimed_at = None
    poster.claimed_by = None

    title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
    if title:
        recompute_title_status(db, title)


# ═════════════════════════════════════════════════════════════════════════
#  WORK DISPATCH — Upload stage
# ═════════════════════════════════════════════════════════════════════════

def ensure_upload_rows(
    db: Session, *, poster: SavedPoster, title: MasterTitle,
    processed: Optional[ProcessedImage] = None,
    project: Optional[Project] = None,
) -> list[UploadTracking]:
    """
    Make sure a pending UploadTracking row exists for each enabled account in
    the project.

    This is the account-recovery mechanism: add a fresh account after a ban
    and every processed image gains a pending row for it, with no
    reprocessing and no manual reconciliation.
    """
    project = project or project_for_title(db, title)
    accounts = [a for a in accounts_for_project(db, project.id) if a.is_enabled]
    if not accounts:
        return []

    if processed is None:
        processed = (
            db.query(ProcessedImage)
              .filter(ProcessedImage.saved_poster_id == poster.id,
                      ProcessedImage.is_current == 1)
              .first()
        )

    # Index of this poster within its title decides the A/B/C suffix. Derived
    # from creation order over live posters so it is stable across reruns.
    siblings = [
        pid for (pid,) in
        db.query(SavedPoster.id)
          .filter(SavedPoster.master_title_id == title.id,
                  SavedPoster.deleted_at.is_(None))
          .order_by(SavedPoster.created_at.asc(), SavedPoster.id.asc())
          .all()
    ]
    index = siblings.index(poster.id) if poster.id in siblings else 0

    created: list[UploadTracking] = []
    for account in accounts:
        existing = (
            db.query(UploadTracking)
              .filter_by(saved_poster_id=poster.id, account_id=account.id)
              .first()
        )
        if existing is not None:
            # Keep the derivative pointer fresh after a reprocess, but never
            # resurrect a row that already succeeded or was deliberately skipped.
            if processed is not None and existing.status in ("pending", "failed"):
                existing.processed_image_id = processed.id
            continue

        row = UploadTracking(
            saved_poster_id=poster.id,
            processed_image_id=processed.id if processed else None,
            account_id=account.id,
            project_id=project.id,
            target_site=account.target_site,
            remote_title=render_remote_title(db, title, poster, index, project=project),
            letter_index=index,
            status="pending",
        )
        db.add(row)
        created.append(row)

    return created


def uploads_today(db: Session, account_id: int, *, day: Optional[date] = None) -> int:
    """
    Confirmed uploads for an account on a calendar day — the number the
    marketplace's daily cap applies to.
    """
    day = day or local_today()
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    return (
        db.query(func.count(UploadTracking.id))
          .filter(UploadTracking.account_id == account_id,
                  UploadTracking.status == "uploaded",
                  UploadTracking.uploaded_at >= start,
                  UploadTracking.uploaded_at < end)
          .scalar() or 0
    )


def account_quota(db: Session, account: UploadAccount, *, day: Optional[date] = None) -> dict[str, int]:
    used = uploads_today(db, account.id, day=day)
    # `or 100` here turned a deliberate ZERO into a hundred. Setting an
    # account's daily limit to 0 is the obvious way to say "do not upload to
    # this one today", and it did the exact opposite — silently, on a real
    # marketplace. The column is NOT NULL with a default of 100, so None can
    # only mean a row that predates it.
    limit = 100 if account.daily_limit is None else int(account.daily_limit)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def intake_open(db: Session, project: Optional[Project] = None) -> bool:
    """
    May the dispatcher hand out NEW work right now?

    One question asked in one place, by every stage — the node's process
    claim, the node's upload claim, and the generation worker. A stop that
    only some stages honoured would be worse than none, because the queue
    would look stopped while something quietly kept spending.

    Per-project, so one niche can be paused while another keeps running.
    """
    if str(get_setting(db, "run_mode", project=project) or "run") != "run":
        return False
    # A marketplace visibility run holds everything: it is hours of browser
    # work on the same node, and it is a deliberate manual operation. The
    # hold belongs to the RUN and is released when the run ends, so there is
    # no switch anyone has to remember to turn back on.
    from .earnings.store_health import holds_pipeline
    if holds_pipeline(db):
        return False
    return not quiet_window_state(db)["blocking"]


def _parse_hhmm(text: Any) -> Optional[tuple[int, int]]:
    """"22:00" -> (22, 0). Anything unreadable means "no window"."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        hh, _, mm = raw.partition(":")
        h, m = int(hh), int(mm or 0)
    except ValueError:
        return None
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def quiet_window_state(db: Session) -> dict:
    """
    Is the nightly earnings read holding new work back right now?

    ════════════════════════════════════════════════════════════════════════
    A WINDOW, NOT A SWITCH
    ════════════════════════════════════════════════════════════════════════
    Nothing is stored and nothing is toggled. This looks at the clock and at
    whether tonight's read has been dealt with, and answers fresh every time
    it is asked.

    That is the whole design. A switch has two edges — turn work off, turn it
    back on — and the second one can be lost, which leaves uploading dead
    while everything looks healthy. There is no second edge here:

      · the read finishes      -> the day is marked done -> work resumes
      · the read fails         -> the day is still marked -> work resumes
      · the node was off all night -> midnight arrives, new day -> resumes

    There is no state that can be left in the wrong position, because there
    is no state.

    Returns everything the screen needs to EXPLAIN itself, because work
    stopping with no visible reason looks exactly like a fault.
    """
    from .timeutil import local_now, local_today

    start = _parse_hhmm(get_setting(db, "earnings_quiet_from"))
    now = local_now()
    today = local_today().isoformat()
    done_day = str(get_setting(db, "earnings_last_run_day") or "")
    done_today = done_day == today

    after_start = bool(start) and (now.hour, now.minute) >= start
    blocking = bool(start) and after_start and not done_today

    return {
        "enabled": bool(start),
        "blocking": blocking,
        "starts_at": f"{start[0]:02d}:{start[1]:02d}" if start else "",
        "now": now.strftime("%H:%M"),
        "done_today": done_today,
        "reason": (
            "Paused for the nightly earnings check — new work resumes as soon "
            "as it finishes." if blocking else ""
        ),
    }


def run_mode_state(db: Session, project: Optional[Project] = None) -> dict:
    from .earnings.store_health import holds_pipeline

    mode = str(get_setting(db, "run_mode", project=project) or "run")
    quiet = quiet_window_state(db)
    held = holds_pipeline(db)
    return {
        "mode": mode,
        # "running" must reflect what the dispatcher will ACTUALLY do, not
        # just the switch, or the page says running while nothing moves.
        "running": mode == "run" and not quiet["blocking"] and not held,
        # The store run's sentence wins when it applies, because it is the
        # most specific answer to "why is nothing happening" — and it names
        # the screen to go and look at.
        "reason": (held or quiet["reason"] if (held or quiet["blocking"])
                   else str(get_setting(db, "run_mode_reason", project=project) or "")),
        "quiet": quiet,
        "store_run": held or "",
    }


# ════════════════════════════════════════════════════════════════════════════
#  WHICH ACCOUNTS SERVE WHICH PROJECTS
# ════════════════════════════════════════════════════════════════════════════
# An account exists ONCE and may serve several projects, none, or one. Every
# query that needs "the accounts for this project" or "the projects for this
# account" goes through here — the same rule as scope_titles(): nothing
# filters on the legacy `project_id` column by hand.

def project_ids_for_account(db: Session, account_id: int) -> list[int]:
    """Projects this account uploads for. Empty means it is earn-only."""
    return [
        pid for (pid,) in db.query(AccountProject.project_id)
                            .filter(AccountProject.account_id == account_id).all()
    ]


def accounts_for_project(db: Session, project_id: int) -> list[UploadAccount]:
    """
    Every account attached to one project.

    Note there is deliberately NO "or project_id IS NULL" here. An
    unattached account is not a wildcard, it is an account nothing is
    uploaded to. Three endpoints once made that mistake with titles and
    MUSIK inherited 101,605 movie rows; the same shape here would upload a
    celebrity portrait into a movie account.
    """
    return (
        db.query(UploadAccount)
          .join(AccountProject, AccountProject.account_id == UploadAccount.id)
          .filter(AccountProject.project_id == project_id)
          .order_by(UploadAccount.rotation_order.asc(), UploadAccount.id.asc())
          .all()
    )


def attach_account(db: Session, *, account_id: int, project_id: int,
                   by: Optional[str] = None) -> bool:
    """Let a project upload through an existing account. Idempotent."""
    existing = (
        db.query(AccountProject)
          .filter(AccountProject.account_id == account_id,
                  AccountProject.project_id == project_id).first()
    )
    if existing:
        return False
    db.add(AccountProject(account_id=account_id, project_id=project_id,
                          attached_by=by))
    return True


def detach_account(db: Session, *, account_id: int, project_id: int) -> bool:
    """
    Stop uploading this project's work through this account.

    The account and its whole upload history survive — this only says "no
    more of THIS project's work goes here". Anything already queued for that
    pair is left alone rather than deleted, because a half-uploaded set that
    silently vanished would be unexplainable later.
    """
    rows = (
        db.query(AccountProject)
          .filter(AccountProject.account_id == account_id,
                  AccountProject.project_id == project_id).delete()
    )
    return bool(rows)


# The marketplace used to be stored as "faa" on accounts while projects called
# the same site "fineartamerica". Nothing compared the two, so it never broke —
# but the moment anything did, every earnings total would have split in half.
MARKETPLACE_RENAMES = {"faa": "fineartamerica", "tp": "teepublic"}


def backfill_marketplace_names(db: Session) -> int:
    """
    One-off: settle on one spelling per marketplace.

    ════════════════════════════════════════════════════════════════════════
    THE NAME IS COPIED, SO RENAMING IS A MIGRATION
    ════════════════════════════════════════════════════════════════════════
    `LedgerEntry.marketplace` and `TitleAlias.marketplace` are DENORMALISED
    copies of the account's `target_site` — deliberately, so a filter needs no
    join. That makes a rename a data migration rather than a spelling change:
    change the account alone and its existing rows keep the old name, so the
    account reads $0 while its money sits under a marketplace nothing points
    at any more.

    All three move together, here, in one transaction.
    """
    from .models import LedgerEntry, TitleAlias

    changed = 0
    for old, new in MARKETPLACE_RENAMES.items():
        for model, field in ((UploadAccount, "target_site"),
                             (LedgerEntry, "marketplace"),
                             (TitleAlias, "marketplace")):
            column = getattr(model, field)
            changed += (
                db.query(model).filter(column == old)
                  .update({field: new}, synchronize_session=False)
            )
    if changed:
        db.commit()
    return changed


def backfill_account_projects(db: Session) -> int:
    """
    One-off: turn the old single `project_id` column into link rows.

    Idempotent and cheap, so it runs at startup like the workspace move.
    Without it, every existing account would come back from the upgrade
    serving NO projects — which reads as "uploading is broken" and would be
    the worst possible first impression of this change.
    """
    made = 0
    for account in db.query(UploadAccount).filter(
            UploadAccount.project_id.isnot(None)).all():
        if attach_account(db, account_id=account.id,
                          project_id=account.project_id, by="upgrade"):
            made += 1
    if made:
        db.commit()
    return made


def account_is_available(account: UploadAccount) -> bool:
    """False while an account is paused (bot-check, bad credentials, etc.)."""
    if not account.is_enabled:
        return False
    # A ban is permanent. Checked separately from is_enabled so that
    # re-enabling a banned account by mistake cannot start uploading into an
    # account the marketplace has closed.
    if account.banned_at is not None:
        return False
    if account.paused_until and account.paused_until > datetime.utcnow():
        return False
    return True


# ════════════════════════════════════════════════════════════════════════════
#  BAN RECOVERY
# ════════════════════════════════════════════════════════════════════════════
# When a marketplace closes an account, two things are true at once:
#
#   1. Nothing can be uploaded there again. Ever. Not after a cooling-off
#      period, which is what separates this from `paused_until`.
#   2. Everything it had already listed is GONE from the marketplace. The
#      files still exist in our archive and the database still knows what
#      they were — but the public listings vanished with the account.
#
# Point 2 is the one that matters and the one that is easy to miss. Marking
# the account disabled and moving on would leave the database asserting that
# several thousand images are live on a site where they no longer are, and
# every one of them would be skipped by future dispatch because their
# tracking rows still say 'uploaded'.
#
# So a ban does three things, and the middle one is the point:
#
#   · the account is marked banned and can never be handed work again
#   · its 'uploaded' rows become 'removed', with the reason recorded —
#     the same state a copyright takedown produces, because the outcome is
#     identical: it was listed, and now it is not
#   · its pending/failed rows are dropped, since they describe intent to
#     upload somewhere that no longer exists
#
# Nothing is deleted. `removed` rows keep their remote_id and their history,
# which is what lets you answer "where did this listing used to live".


def ban_account(
    db: Session, account: UploadAccount, *, reason: str, by: str = "",
) -> dict[str, int]:
    """
    Mark an account destroyed and retire everything it had listed.

    Returns counts so the caller can say what actually happened rather than
    "done" — the number of listings written off is the fact worth seeing.
    """
    now = datetime.utcnow()
    account.banned_at = now
    account.banned_reason = (reason or "")[:1000]
    account.is_enabled = 0

    uploaded = (
        db.query(UploadTracking)
          .filter(UploadTracking.account_id == account.id,
                  UploadTracking.status == "uploaded")
          .all()
    )
    for row in uploaded:
        row.status = "removed"
        row.removed_at = now
        row.removed_reason = (f"account banned: {reason}" if reason
                              else "account banned")[:1000]

    dropped = (
        db.query(UploadTracking)
          .filter(UploadTracking.account_id == account.id,
                  UploadTracking.status.in_(("pending", "failed", "uploading")))
          .delete(synchronize_session=False)
    )

    # Posters that were only ever live on this account are no longer live
    # anywhere, so they go back to 'processed' — ready to be queued onto a
    # replacement. One that is still uploaded elsewhere is left alone.
    restored = 0
    for row in uploaded:
        still_live = (
            db.query(func.count(UploadTracking.id))
              .filter(UploadTracking.saved_poster_id == row.saved_poster_id,
                      UploadTracking.status == "uploaded")
              .scalar() or 0
        )
        if still_live:
            continue
        poster = db.query(SavedPoster).filter_by(id=row.saved_poster_id).first()
        if poster is not None and poster.pipeline_status == "uploaded":
            poster.pipeline_status = "processed"
            title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            if title:
                recompute_title_status(db, title)
            restored += 1

    return {"listings_lost": len(uploaded), "queued_work_dropped": int(dropped or 0),
            "images_needing_relisting": restored}


def hand_over_account(
    db: Session, *, dead_id: int, replacement_id: int,
) -> dict[str, int]:
    """
    Rebuild a banned account's catalogue on a replacement.

    Deliberately a SEPARATE step from the ban. Banning is urgent and factual —
    it happened, record it. Choosing where the work goes is a decision, and
    may not even be possible yet: the replacement account often does not
    exist at the moment the ban is discovered.

    Both accounts must be in the same project. Rebuilding a movie catalogue
    on the celebrity account would be a very public mistake, and the check
    costs nothing.
    """
    dead = db.query(UploadAccount).filter_by(id=dead_id).first()
    new = db.query(UploadAccount).filter_by(id=replacement_id).first()
    if dead is None or new is None:
        raise ValueError("Account not found")
    if dead.id == new.id:
        raise ValueError("An account cannot replace itself.")
    if set(project_ids_for_account(db, dead.id)) != set(
            project_ids_for_account(db, new.id)):
        raise ValueError(
            f"{dead.name} belongs to a different project than {new.name}. "
            f"Listings cannot move between projects."
        )
    if new.banned_at is not None:
        raise ValueError(f"{new.name} is itself banned.")

    # requeue_for_account already does exactly this: seed pending rows on the
    # target for every processed image the source had listed. Reused rather
    # than reimplemented so the review-gate rules it enforces apply here too.
    created = requeue_for_account(db, account_id=new.id, source_account_id=dead.id)
    dead.replaced_by_id = new.id
    return {"queued": created}


def pause_account(db: Session, account: UploadAccount, *, minutes: int, reason: str) -> None:
    account.paused_until = datetime.utcnow() + timedelta(minutes=minutes)
    account.pause_reason = (reason or "")[:1000]


def _has_upload_work(db: Session, account_id: int, project_id: Optional[int]) -> bool:
    """
    Is there anything queued for this (account, project) pair?

    Asked before committing an account's turn to a project, so an account
    serving two projects doesn't waste its turn on the empty one and leave
    the busy one waiting for the next rotation.
    """
    return _oldest_upload_wait(db, account_id, project_id) is not None


def _oldest_upload_wait(db: Session, account_id: int,
                        project_id: Optional[int]) -> Optional[datetime]:
    """
    When the LONGEST-WAITING queued upload for this pair was created.
    None when there is nothing queued.

    ════════════════════════════════════════════════════════════════════════
    THIS IS WHAT MAKES "WHICHEVER WAITED LONGEST" TRUE
    ════════════════════════════════════════════════════════════════════════
    `claim_upload_batch` said in a comment that an account's turn goes to
    whichever of its projects has waited longest. It did not. It asked
    `project_ids_for_account()`, which runs a query with NO `ORDER BY`, and
    took the first one that had any work at all — so in practice the project
    ATTACHED FIRST won every turn.

    MEASURED 2026-08-27, traced from producer to consumer: the query has no
    ordering, SQLite returns rows in rowid order, and the loop breaks on the
    first match. The consequence is not subtle — an account serving two
    projects where the first one always has work means the SECOND NEVER
    UPLOADS AT ALL. The movie project carries a backlog of roughly three
    thousand posters, so "always has work" is its normal state, and one FAA
    account is meant to serve both niches after migration.

    Derived from the WORK rather than stored in a counter, which is the same
    choice the quiet window and `scan_incomplete` make: there is no
    per-project "last served" column to keep correct, nothing to forget to
    update, and nothing that can be left wrong by a path nobody listed.
    """
    q = db.query(func.min(UploadTracking.created_at)).filter(
        UploadTracking.account_id == account_id,
        UploadTracking.status.in_(("pending", "failed")),
    )
    if project_id:
        q = q.filter(UploadTracking.project_id == project_id)
    return q.scalar()


def claim_upload_batch(
    db: Session, *, node: str, account_id: Optional[int] = None,
    limit: Optional[int] = None, project_id: Optional[int] = None,
) -> dict[str, Any]:
    """
    Hand a node its next upload batch for one account, capped by whatever the
    account has left of today's quota.

    Returns the account's credentials and effective settings alongside the
    items, so the node needs exactly one round trip to start working and
    holds no local config of its own.
    """
    reap_stale_claims(db)

    accounts_q = db.query(UploadAccount).filter(UploadAccount.is_enabled == 1)
    if account_id:
        accounts_q = accounts_q.filter(UploadAccount.id == account_id)
    if project_id:
        # Through the link table, never the legacy column. An account serving
        # both projects must be reachable from both.
        accounts_q = accounts_q.join(
            AccountProject, AccountProject.account_id == UploadAccount.id
        ).filter(AccountProject.project_id == project_id)

    # ── Take turns between accounts ─────────────────────────────────────
    #
    # Ordering by last_run_at means whichever account was served longest ago
    # goes next, which produces a rotation without storing any cursor. On the
    # first pass every account has last_run_at NULL, so rotation_order decides
    # the sequence; afterwards it only breaks ties.
    #
    # Self-correcting by construction: an account that's paused, out of quota
    # or out of work simply keeps its old timestamp and jumps to the front the
    # moment it can run again.
    accounts = accounts_q.order_by(
        UploadAccount.last_run_at.asc().nullsfirst(),
        UploadAccount.rotation_order.asc(),
        UploadAccount.id.asc(),
    ).all()

    for account in accounts:
        if not account_is_available(account):
            continue

        quota = account_quota(db, account)
        if quota["remaining"] <= 0:
            continue

        # ── ONE PROJECT PER TURN ─────────────────────────────────────────
        #
        # An account can now serve several projects, but a batch cannot: the
        # node gets ONE settings blob, and the title template, keywords and
        # description all differ per project. A mixed batch would upload
        # MUSIK artwork under the movie project's title template — a public
        # mistake on a real marketplace.
        #
        # So the account's turn goes to whichever of its projects has waited
        # longest, and the whole batch is that project's work.
        candidates = ([project_id] if project_id
                      else project_ids_for_account(db, account.id))

        # Genuinely oldest-first, not first-attached-first. `candidates`
        # comes back unordered, and taking the first with work meant the
        # project attached first won every turn — see _oldest_upload_wait().
        # An account serving two projects where one always has work would
        # never upload the other's work at all.
        ranked: list[tuple[datetime, Any]] = []
        for pid in candidates:
            candidate_project = resolve_project(db, pid)
            # run_mode is per project: one may be paused while another runs.
            if not intake_open(db, candidate_project):
                continue
            waited = _oldest_upload_wait(db, account.id, pid)
            if waited is None:
                continue
            ranked.append((waited, candidate_project))
        if not ranked:
            continue
        # Oldest queued item wins. Ties break on project id so the choice is
        # deterministic rather than dependent on dictionary order.
        ranked.sort(key=lambda pair: (pair[0], pair[1].id))
        chosen = ranked[0][1]

        project = chosen
        # How many this account gets per turn. Its own rotation_size wins;
        # otherwise the project's batch size. Always capped by whatever is
        # left of today's marketplace allowance.
        per_turn = account.rotation_size or int(
            get_setting(db, "upload_batch_size", project=project))
        batch_size = limit or per_turn
        take = min(batch_size, quota["remaining"])
        max_attempts = int(get_setting(db, "upload_max_attempts", project=project))

        rows = (
            db.query(UploadTracking, SavedPoster, MasterTitle, ProcessedImage)
              .join(SavedPoster, UploadTracking.saved_poster_id == SavedPoster.id)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .join(ProcessedImage, UploadTracking.processed_image_id == ProcessedImage.id)
              .filter(UploadTracking.account_id == account.id,
                      # The project chosen for THIS turn — not the account's
                      # old single project_id, which no longer means
                      # anything now that one account serves several.
                      #
                      # This filter is what keeps a batch single-project,
                      # and it must stay: the settings sent with the batch
                      # are this project's, so a row from another project
                      # would be listed under the wrong title template.
                      UploadTracking.project_id == project.id,
                      UploadTracking.status.in_(("pending", "failed")),
                      UploadTracking.attempts < max_attempts,
                      SavedPoster.deleted_at.is_(None))
              .order_by(SavedPoster.original_save_date.asc(),
                        MasterTitle.external_id.asc().nullslast(),
                        UploadTracking.letter_index.asc())
              .limit(take)
              .all()
        )
        if not rows:
            continue

        now = datetime.utcnow()
        items: list[dict[str, Any]] = []
        for tracking, poster, title, processed in rows:
            tracking.status = "uploading"
            tracking.claimed_at = now
            tracking.claimed_by = node
            poster.pipeline_status = "uploading"

            # Re-render rather than trusting the stored value: the admin may
            # have edited the title template since this row was seeded.
            tracking.remote_title = render_remote_title(
                db, title, poster, tracking.letter_index or 0, project=project
            )

            # ── Never dispatch a title the marketplace will refuse ──────────
            # FAA rejects an unrenderable title with an HTML error PAGE, not
            # an HTTP error. The node would read that as a generic failure,
            # retry it to the attempt cap, and burn the account's daily quota
            # discovering something we can see from here in one comparison.
            #
            # Held rather than failed: the fix is an admin editing the title,
            # not a retry.
            problem = validate_marketplace_title(title.title or "", tracking.remote_title)
            if problem:
                tracking.status = "failed"
                tracking.last_error = f"title held: {problem}"
                tracking.claimed_at = None
                tracking.claimed_by = None
                poster.pipeline_status = "processed"   # stays ready, not sent
                recompute_title_status(db, title)
                continue

            items.append({
                "tracking_id":  tracking.id,
                "poster_id":    poster.id,
                "master_id":    title.id,
                "storage_path": processed.storage_path,
                "filename":     processed.filename,
                "remote_title": tracking.remote_title,
                "keywords":     render_keywords(db, title, project=project),
                "description":  render_description(db, title, project=project),
                "letter_index": tracking.letter_index,
                "attempt":      (tracking.attempts or 0) + 1,
            })
            recompute_title_status(db, title)

        account.last_run_at = now

        return {
            "account": account_payload(db, account, include_secret=True,
                                       project=project),
            "settings": upload_settings_payload(db, project=project),
            "quota": account_quota(db, account),
            "items": items,
        }

    return {"account": None, "items": [], "quota": None, "settings": None}


def report_uploaded(
    db: Session, *, tracking_id: int, node: str,
    remote_id: Optional[str] = None,
) -> None:
    """Mark one image confirmed live. Called per-image, never per-batch, so a
    crash mid-run never loses credit for what already succeeded."""
    tracking = db.query(UploadTracking).filter_by(id=tracking_id).first()
    if tracking is None:
        return

    tracking.status = "uploaded"
    tracking.uploaded_at = datetime.utcnow()
    tracking.attempts = (tracking.attempts or 0) + 1
    tracking.last_error = None
    tracking.claimed_at = None
    tracking.claimed_by = None

    # A successful upload proves whatever paused this account is over, so the
    # explanation goes with it. Every other error field in the app already
    # self-clears on success (process_error, last_error); pause_reason was the
    # exception, which is why a selector failure from weeks ago sat on the
    # dashboard in red with no button to remove it. Stale alarms train you to
    # ignore real ones.
    account = db.query(UploadAccount).filter_by(id=tracking.account_id).first()
    if account is not None:
        if account.pause_reason or account.paused_until:
            account.pause_reason = None
            account.paused_until = None
        # This is what makes the counter mean "in a ROW". Without it, three
        # selector misses spread across a week of successful uploads would
        # eventually park a perfectly healthy account.
        account.consecutive_failures = 0
    if remote_id:
        tracking.remote_id = remote_id[:128]

    poster = db.query(SavedPoster).filter_by(id=tracking.saved_poster_id).first()
    if poster is None:
        return

    # REQUIRED, not defensive. SessionLocal is autoflush=False, so the count
    # below reads what is on DISK — and on disk this very row is still
    # 'uploading', because the assignment above lives only in the session.
    # Every poster therefore counted itself as outstanding, never reached
    # zero, and stayed 'uploading' forever: uploads succeeded, the account
    # quota went up, and the funnel's UPLOADED column sat at 0.
    #
    # recompute_title_status() carries the same flush for the same reason.
    db.flush()

    # A poster is 'uploaded' once it is live on every account that still
    # wants it; otherwise it stays in flight for the remaining accounts.
    outstanding = (
        db.query(func.count(UploadTracking.id))
          .filter(UploadTracking.saved_poster_id == poster.id,
                  UploadTracking.status.in_(("pending", "uploading", "failed")))
          .scalar() or 0
    )
    poster.pipeline_status = "uploaded" if outstanding == 0 else "uploading"

    title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
    if title:
        recompute_title_status(db, title)


def report_upload_failure(
    db: Session, *, tracking_id: int, node: str, error: str,
    screenshot: Optional[str] = None, pause_minutes: int = 0,
    pause_reason: Optional[str] = None, pause_immediate: bool = True,
) -> None:
    """
    Record an upload failure and let the row be retried on a later run.

    `pause_minutes` is how the node reports a problem it believes affects the
    whole account rather than this one image.

    ════════════════════════════════════════════════════════════════════════
    CERTAIN vs SUSPECTED, AND WHY THAT DISTINCTION EXISTS
    ════════════════════════════════════════════════════════════════════════
    Two very different things used to be treated identically:

      · A bot wall, or credentials the marketplace rejected. CERTAIN. Every
        remaining image will fail the same way, so the account is parked on
        the first one. `pause_immediate=True`.

      · A form field that could not be found. SUSPECTED — and, as it turns
        out, usually wrong. FineArtAmerica serves TWO versions of its upload
        form (updateartwork.html and updateartwork2025.html) and picks one
        per request, so a missing field normally means "this request got the
        other page", not "the form has changed".

    Parking the account on the first selector miss cost forty-five minutes of
    a hundred-image day to punish ninety-nine images that would have landed
    on the good page. So a suspected-systemic failure now increments a
    counter and only parks the account once `upload_pause_after_failures` of
    them happen IN A ROW. A genuine redesign trips that within a few images;
    an intermittent variant costs one image and the run continues.

    The counter is reset by any success (see `report_uploaded`), which is
    what makes it mean "a run of failures" rather than "failures ever".
    """
    tracking = db.query(UploadTracking).filter_by(id=tracking_id).first()
    if tracking is None:
        return

    tracking.status = "failed"
    tracking.attempts = (tracking.attempts or 0) + 1
    tracking.last_error = (error or "")[:4000]
    tracking.claimed_at = None
    tracking.claimed_by = None
    if screenshot:
        tracking.last_screenshot = screenshot[:768]

    poster = db.query(SavedPoster).filter_by(id=tracking.saved_poster_id).first()
    if poster is not None:
        max_attempts = int(get_setting(db, "upload_max_attempts"))
        # Only mark the image itself failed once retries are exhausted —
        # otherwise a transient blip would light up the dashboard in red.
        if tracking.attempts >= max_attempts:
            poster.pipeline_status = "failed_upload"
        else:
            poster.pipeline_status = "processed"
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        if title:
            recompute_title_status(db, title)

    account = db.query(UploadAccount).filter_by(id=tracking.account_id).first()
    if account is None:
        return

    if pause_minutes <= 0:
        # An ordinary one-image failure. It says nothing about the account,
        # so the run of suspected-systemic failures is untouched.
        return

    if pause_immediate:
        account.consecutive_failures = 0
        pause_account(db, account, minutes=pause_minutes,
                      reason=pause_reason or error)
        return

    # ── Suspected systemic: pause only on a RUN of them ──────────────────
    threshold = max(1, int(get_setting(db, "upload_pause_after_failures")))
    account.consecutive_failures = (account.consecutive_failures or 0) + 1

    if account.consecutive_failures >= threshold:
        pause_account(
            db, account, minutes=pause_minutes,
            reason=f"{pause_reason or error} "
                   f"({account.consecutive_failures} in a row)",
        )
        account.consecutive_failures = 0
    else:
        # Deliberately NOT a pause, and deliberately still recorded: the
        # admin should be able to see that something intermittent is
        # happening before it becomes a stoppage.
        account.pause_reason = (
            f"{pause_reason or error} — {account.consecutive_failures} of "
            f"{threshold} in a row; still uploading"
        )[:1000]


def retry_uploads(db: Session, tracking_ids: Iterable[int]) -> int:
    """Reset failed rows so the next run picks them up, clearing the attempt
    count so an exhausted row gets a genuine fresh start."""
    ids = [int(i) for i in tracking_ids]
    if not ids:
        return 0

    rows = db.query(UploadTracking).filter(UploadTracking.id.in_(ids)).all()
    for row in rows:
        row.status = "pending"
        row.attempts = 0
        row.last_error = None
        row.claimed_at = None
        row.claimed_by = None
        poster = db.query(SavedPoster).filter_by(id=row.saved_poster_id).first()
        if poster is not None and poster.pipeline_status == "failed_upload":
            poster.pipeline_status = "processed"
    return len(rows)


def mark_removed(
    db: Session, tracking_ids: Iterable[int], *, reason: str = "",
) -> int:
    """
    Flag listings taken down by the marketplace (copyright/DMCA). Kept
    distinct from 'failed' because a removal is a permanent outcome for that
    account, not something to retry — but the processed file stays in storage
    so the image can be re-listed elsewhere.
    """
    ids = [int(i) for i in tracking_ids]
    if not ids:
        return 0

    rows = db.query(UploadTracking).filter(UploadTracking.id.in_(ids)).all()
    for row in rows:
        row.status = "removed"
        row.removed_at = datetime.utcnow()
        row.removed_reason = (reason or "")[:1000]
    return len(rows)


def requeue_for_account(
    db: Session, *, account_id: int, source_account_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> int:
    """
    Seed pending rows on `account_id` for every processed image — the
    ban-recovery path. Optionally mirror only what a specific dead account
    had uploaded, so a replacement account rebuilds exactly that catalogue.

    `project_id` says WHICH catalogue. An account serving both niches has two,
    and requeueing "the account" without saying which would queue the movie
    back catalogue into a MUSIK rebuild. Omitted is only safe when the account
    serves exactly one project, which is checked rather than assumed.
    """
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise ValueError("Account not found")

    attached = project_ids_for_account(db, account_id)
    if project_id is None:
        if len(attached) > 1:
            raise ValueError(
                f"{account.name} serves {len(attached)} projects — say which "
                f"one to requeue.")
        project_id = attached[0] if attached else None
    elif project_id not in attached:
        raise ValueError(f"{account.name} is not attached to that project.")
    project = resolve_project(db, project_id)

    query = (
        db.query(ProcessedImage, SavedPoster, MasterTitle)
          .join(SavedPoster, ProcessedImage.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(ProcessedImage.is_current == 1,
                  ProcessedImage.project_id == project.id,
                  SavedPoster.deleted_at.is_(None))
    )
    if project.has_review_gate:
        # A gated project's derivatives are NOT all releasable. Without this,
        # requeueing an account would hand the marketplace every generated
        # image including the ones still waiting to be looked at and the ones
        # judged unusable — quietly undoing the gate from a button labelled
        # "requeue back catalogue".
        #
        # Excluded by verdict rather than required to equal 'approved',
        # because an image generated while the gate was OFF has review_status
        # NULL — it was legitimately released without review, and demanding
        # 'approved' would strand it forever. Keyed on has_review_gate, not
        # on whether the gate is on TODAY: images from both eras coexist.
        query = query.filter(
            or_(ProcessedImage.review_status.is_(None),
                ProcessedImage.review_status == "approved")
        )
    if source_account_id:
        query = query.join(
            UploadTracking,
            (UploadTracking.saved_poster_id == SavedPoster.id) &
            (UploadTracking.account_id == source_account_id),
        ).filter(UploadTracking.status.in_(("uploaded", "removed")))

    created = 0
    for processed, poster, title in query.all():
        existing = (
            db.query(UploadTracking)
              .filter_by(saved_poster_id=poster.id, account_id=account.id)
              .first()
        )
        if existing is not None:
            continue
        rows = ensure_upload_rows(db, poster=poster, title=title,
                                  processed=processed, project=project)
        created += len(rows)
    return created


# ═════════════════════════════════════════════════════════════════════════
#  PAYLOAD BUILDERS (shared by admin UI and worker API)
# ═════════════════════════════════════════════════════════════════════════

def _fernet():
    """
    Lazily build the Fernet cipher for account passwords.

    The key derives from PIPELINE_SECRET (falling back to SESSION_SECRET) so
    a standard deploy needs no extra setup, while a dedicated secret can be
    rotated independently. Import is local because cryptography is only
    needed when accounts are actually in play.
    """
    import base64
    from cryptography.fernet import Fernet
    from .config import SESSION_SECRET

    secret = os.environ.get("PIPELINE_SECRET") or SESSION_SECRET
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_secret(raw: str) -> str:
    return _fernet().encrypt((raw or "").encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:
        # A rotated/mismatched secret must not crash a run — the account will
        # simply fail to log in and get paused with a clear reason.
        return ""


def account_payload(
    db: Session, account: UploadAccount, *, include_secret: bool = False,
    project: Optional[Project] = None,
) -> dict[str, Any]:
    """
    Serialize an account. `include_secret` is only ever true for an
    authenticated worker node — the browser never receives the password.

    `project` decides which settings the payload carries (timings, selectors,
    title template). It must be passed by anything that is about to DO
    something project-specific, because one account now serves several and
    the account itself no longer knows which one you mean. Omitting it falls
    back to the account's first project, then to the default — fine for a
    read-only listing, wrong for a batch.
    """
    if project is None:
        attached = project_ids_for_account(db, account.id)
        project = resolve_project(db, attached[0] if attached else None)
    timings = dict(get_setting(db, "timings", project=project))
    if account.timing_json:
        try:
            timings.update(json.loads(account.timing_json))
        except (TypeError, ValueError):
            pass

    # ── Selectors belong to the SITE, not the project ────────────────────
    # A TeePublic account may serve no project at all, so resolving its
    # sign-in form through a project would hand it FineArtAmerica's — which
    # is how it would end up looking for an "artist login" link that does not
    # exist on TeePublic.
    site = (account.target_site or "").lower()
    base_key = f"selectors_{site}" if f"selectors_{site}" in DEFAULTS else "selectors"
    selectors = dict(get_setting(db, base_key, project=project))
    if account.selectors_json:
        try:
            selectors.update(json.loads(account.selectors_json))
        except (TypeError, ValueError):
            pass

    data: dict[str, Any] = {
        "id":                 account.id,
        "project_id":         project.id,
        "project_slug":       project.slug,
        # Every project this account serves, so a screen can say "shared with
        # MUSIK" instead of implying it belongs to whichever one you are
        # standing in.
        "project_ids":        project_ids_for_account(db, account.id),
        "name":               account.name,
        "target_site":        account.target_site,
        "email":              account.email,
        "profile_url":        account.profile_url,
        "chrome_profile_dir": account.chrome_profile_dir,
        "daily_limit":        account.daily_limit,
        "rotation_order":     account.rotation_order,
        "rotation_size":      account.rotation_size,
        "is_enabled":         bool(account.is_enabled),
        "paused_until":       account.paused_until.isoformat() if account.paused_until else None,
        "pause_reason":       account.pause_reason,
        # Distinguishes "paused right now" from "was paused, and here's what
        # happened". The UI shows the first in red and the second in grey with
        # a dismiss button — they are very different messages to wake up to.
        "pause_active":       bool(account.paused_until
                                   and account.paused_until > datetime.utcnow()),
        "last_run_at":        account.last_run_at.isoformat() if account.last_run_at else None,
        # Banned is a separate, permanent state — never inferred from
        # is_enabled, because "switched off" and "closed by the marketplace,
        # its listings gone" need very different actions.
        "banned":             bool(account.banned_at),
        "banned_at":          account.banned_at.isoformat() if account.banned_at else None,
        "banned_reason":      account.banned_reason,
        "replaced_by_id":     account.replaced_by_id,
        "timings":            timings,
        "selectors":          selectors,
    }
    if include_secret:
        data["password"] = decrypt_secret(account.password_enc)
    return data


def upload_settings_payload(
    db: Session, *, project: Optional[Project] = None,
) -> dict[str, Any]:
    """The subset of settings a node needs for an upload run."""
    return {
        "sequential":      get_setting(db, "upload_sequential", project=project),
        "max_attempts":    get_setting(db, "upload_max_attempts", project=project),
        "title_template":  get_setting(db, "title_template", project=project),
        "keywords_static": get_setting(db, "keywords_static", project=project),
        "storage_root":    get_setting(db, "storage_root", project=project),
        "poll_interval_s": get_setting(db, "poll_interval_s", project=project),
        "schedule_mode":   get_setting(db, "schedule_mode", project=project),
        "daily_start_hour": get_setting(db, "daily_start_hour", project=project),
        "poll_interval_idle_s":    get_setting(db, "poll_interval_idle_s", project=project),
        "poll_idle_after_min":     get_setting(db, "poll_idle_after_min", project=project),
        "node_log_retention_days": get_setting(db, "node_log_retention_days", project=project),
    }


def process_settings_payload(
    db: Session, *, project: Optional[Project] = None,
) -> dict[str, Any]:
    """Everything a node needs for a Photoshop run, script included."""
    return {
        "script":           render_process_script(db, project=project),
        "script_version":   script_version(db, project=project),
        "photoshop_exe":    get_setting(db, "photoshop_exe", project=project),
        "storage_root":     get_setting(db, "storage_root", project=project),
        "timeout_s":        get_setting(db, "process_timeout_s", project=project),
        "warmup_s":         get_setting(db, "photoshop_warmup_s", project=project),
        "restart_every":    get_setting(db, "photoshop_restart_every", project=project),
        # Echoed back so the node's own log can state what it is applying —
        # the test log printed "work ?px -> out ?px" without these.
        "work_width":       get_setting(db, "work_width", project=project),
        "output_width":     get_setting(db, "output_width", project=project),
        "batch_size":       get_setting(db, "process_batch_size", project=project),
        "output_suffix":    get_setting(db, "output_suffix", project=project),
        "poll_interval_s":  get_setting(db, "poll_interval_s", project=project),
        "schedule_mode":    get_setting(db, "schedule_mode", project=project),
        "daily_start_hour": get_setting(db, "daily_start_hour", project=project),
        "poll_interval_idle_s":    get_setting(db, "poll_interval_idle_s", project=project),
        "poll_idle_after_min":     get_setting(db, "poll_idle_after_min", project=project),
        "node_log_retention_days": get_setting(db, "node_log_retention_days", project=project),
    }


# ═════════════════════════════════════════════════════════════════════════
#  WORKER NODE AUTH
# ═════════════════════════════════════════════════════════════════════════

def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def create_node(db: Session, *, name: str, capabilities: str = "process,upload") -> tuple[WorkerNode, str]:
    """
    Register a node and return it with its plaintext token. The token is
    shown once at creation and only its hash is stored — same contract as
    any API key.
    """
    token = secrets.token_urlsafe(32)
    node = WorkerNode(
        name=name.strip(),
        token_hash=hash_token(token),
        capabilities=capabilities,
    )
    db.add(node)
    db.flush()
    return node, token


def authenticate_node(db: Session, token: str) -> Optional[WorkerNode]:
    if not token:
        return None
    node = (
        db.query(WorkerNode)
          .filter(WorkerNode.token_hash == hash_token(token),
                  WorkerNode.is_enabled == 1)
          .first()
    )
    if node is not None:
        node.last_seen_at = datetime.utcnow()
    return node


def rotate_node_token(db: Session, node: WorkerNode) -> str:
    token = secrets.token_urlsafe(32)
    node.token_hash = hash_token(token)
    return token


# ═════════════════════════════════════════════════════════════════════════
#  JOBS (batch runs + Test & Debug)
# ═════════════════════════════════════════════════════════════════════════

def create_job(
    db: Session, *, kind: str, payload: Optional[dict] = None,
    project_id: Optional[int] = None, requested_by: Optional[str] = None,
) -> PipelineJob:
    job = PipelineJob(
        project_id=project_id,
        kind=kind,
        status="queued",
        payload_json=json.dumps(payload or {}),
        requested_by=requested_by,
    )
    db.add(job)
    db.flush()
    return job


def claim_job(
    db: Session, *, node: str, kinds: Optional[list[str]] = None,
) -> Optional[PipelineJob]:
    """
    Take the oldest queued job the node can handle.

    Test jobs are prioritised over batch work: when you're debugging you want
    your one-image test to run now, not behind a 40-image batch.
    """
    query = db.query(PipelineJob).filter(PipelineJob.status == "queued")
    if kinds:
        query = query.filter(PipelineJob.kind.in_(kinds))

    jobs = query.order_by(PipelineJob.created_at.asc()).limit(25).all()
    if not jobs:
        return None

    jobs.sort(key=lambda j: (0 if j.kind.startswith("test_") else 1, j.created_at))
    job = jobs[0]
    job.status = "running"
    job.claimed_by = node
    job.started_at = datetime.utcnow()
    return job


def append_job_log(db: Session, job: PipelineJob, message: str, *, level: str = "info") -> None:
    """
    Append one line to a job's log. This is what the dashboard's Live Console
    tails, so the node calls it as it goes rather than dumping at the end —
    that's the difference between watching a failure happen and guessing.
    """
    now = datetime.utcnow()
    stamp = now.strftime("%H:%M:%S")
    prefix = {"error": "ERROR", "warn": "WARN", "ok": "OK"}.get(level, "")
    line = f"[{stamp}] {prefix + ' ' if prefix else ''}{message}"
    job.log_text = ((job.log_text or "") + line + "\n")[-200_000:]
    # ── THE HEARTBEAT ───────────────────────────────────────────────────
    # Every log line the node writes lands here, so this is the one place
    # that knows a job is still alive. Stamped here rather than at each
    # caller for the same reason the log itself is: one definition, and no
    # path that reports progress without also proving it is breathing.
    job.last_report_at = now


def finish_job(
    db: Session, job: PipelineJob, *, ok: bool,
    result: Optional[dict] = None, error: Optional[str] = None,
) -> None:
    job.status = "done" if ok else "error"
    job.finished_at = datetime.utcnow()
    job.progress = 100 if ok else job.progress
    if result is not None:
        job.result_json = json.dumps(result)
    if error:
        job.error = error[:4000]


# ═════════════════════════════════════════════════════════════════════════
#  DASHBOARD AGGREGATES
# ═════════════════════════════════════════════════════════════════════════

def get_secret(db: Session, key: str, *, project=None) -> str:
    """
    Read a stored credential, decrypting it.

    Tolerates a plaintext value: settings written before encryption existed,
    or pasted straight into the database during a debugging session, would
    otherwise fail to decrypt and look like "no key configured" — which sends
    you hunting in the wrong place entirely.
    """
    raw = str(get_setting(db, key, project=project) or "").strip()
    if not raw:
        return ""
    try:
        return decrypt_secret(raw)
    except Exception:
        return raw


def _default_project_id(db: Session) -> Optional[int]:
    """
    The default project's id, read (never created) by slug.

    A plain lookup rather than ensure_default_project() so that scoping a
    query can't insert a row as a side effect.
    """
    row = db.query(Project.id).filter_by(slug=DEFAULT_PROJECT_SLUG).first()
    return row[0] if row else None


def project_scope(project_id: Optional[int], *, default_project_id: Optional[int] = None):
    """
    Scope a MasterTitle query to a project, treating NULL as the DEFAULT one.

    Titles imported through the admin CSV/XLSX importer are created without a
    project_id, and the 100k rows that predate multi-project support are NULL
    too. Matching on equality alone would make all of them invisible.

    ════════════════════════════════════════════════════════════════════════
    WHY default_project_id IS NOT OPTIONAL IN PRACTICE
    ════════════════════════════════════════════════════════════════════════
    This used to fold NULL into EVERY project's scope. That was harmless while
    there was one project and quietly catastrophic the moment there were two:
    the celebrity project's Title List would show all 101,605 movie rows, its
    worker queue would hand them out, and its pipeline would send movie
    posters to the celebrity marketplace account.

    NULL means "the default project" and nothing else. Callers that know the
    default pass it; the two-argument form is the one to use.

    Passing `default_project_id=None` keeps the old behaviour of folding NULL
    in, which is still correct when the caller has already established that
    the project IS the default one.
    """
    if not project_id:
        return sa_true()
    if default_project_id is None or project_id == default_project_id:
        return or_(MasterTitle.project_id == project_id,
                   MasterTitle.project_id.is_(None))
    return MasterTitle.project_id == project_id


def funnel_counts(db: Session, *, project_id: Optional[int] = None) -> dict[str, int]:
    """
    The Pipeline tab's headline numbers: how many live posters sit at each
    stage, plus what is complete-but-not-yet-greenlit (the actionable
    backlog).
    """
    query = (
        db.query(SavedPoster.pipeline_status, func.count(SavedPoster.id))
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.deleted_at.is_(None))
          .filter(project_scope(project_id,
                                default_project_id=_default_project_id(db)))
    )

    counts = {
        "awaiting_greenlight": 0, "greenlit": 0, "processing": 0,
        "processed": 0, "uploading": 0, "uploaded": 0,
        "failed_processing": 0, "failed_upload": 0, "skipped": 0,
    }
    for status, count in query.group_by(SavedPoster.pipeline_status).all():
        if status is None:
            continue
        if status in counts:
            counts[status] = count

    # Poster-based, matching greenlight_titles' own decision rule — so the
    # number here is exactly what clicking Greenlight would promote.
    backlog_q = (
        db.query(func.count(SavedPoster.id))
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.deleted_at.is_(None),
                  MasterTitle.status == "complete",
                  awaiting_greenlight_poster_filter())
          .filter(project_scope(project_id,
                                default_project_id=_default_project_id(db)))
    )
    counts["awaiting_greenlight"] = backlog_q.scalar() or 0

    return counts


def upload_history(db: Session, *, days: int = 30, account_id: Optional[int] = None) -> list[dict]:
    """Per-day upload counts for the dashboard chart, zero-filled so the
    chart shows gaps as gaps rather than compressing them away."""
    today = local_today()
    start = today - timedelta(days=days - 1)

    query = (
        db.query(func.date(UploadTracking.uploaded_at), func.count(UploadTracking.id))
          .filter(UploadTracking.status == "uploaded",
                  UploadTracking.uploaded_at.isnot(None),
                  func.date(UploadTracking.uploaded_at) >= start.isoformat())
    )
    if account_id:
        query = query.filter(UploadTracking.account_id == account_id)

    raw = {str(day): count for day, count in query.group_by(func.date(UploadTracking.uploaded_at)).all()}
    return [
        {"date": (start + timedelta(days=i)).isoformat(),
         "count": raw.get((start + timedelta(days=i)).isoformat(), 0)}
        for i in range(days)
    ]
