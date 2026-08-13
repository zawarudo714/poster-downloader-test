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
    AppSetting, MasterTitle, PipelineJob, ProcessedImage, Project,
    SavedPoster, UploadAccount, UploadTracking, WorkerNode,
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
DEFAULT_FAA_SELECTORS = {
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
        "settings":         {"review_min_width_px": 0},
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


def clean_for_marketplace(text: str) -> str:
    """
    ASCII-fold and strip characters marketplaces reject from titles.
    Kept permissive: letters, digits, space, apostrophe, hyphen, slash.
    """
    text = (text or "").replace("&", "AND")
    for src, dst in _ACCENT_MAP.items():
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^A-Za-z0-9 '\-/]", "", text)
    return re.sub(r"\s+", " ", text).strip()


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
    return clean_for_marketplace(_render(template, _title_vars(title, poster, index)))


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

def reap_stale_claims(db: Session) -> dict[str, int]:
    """
    Release work claimed by a node that never reported back (crash, reboot,
    network drop). Without this a dead node's claims would block the queue
    forever. Runs before every dispatch, so recovery needs no intervention.
    """
    timeout = int(get_setting(db, "claim_timeout_min"))
    cutoff = datetime.utcnow() - timedelta(minutes=timeout)

    posters = (
        db.query(SavedPoster)
          .filter(SavedPoster.pipeline_status == "processing",
                  SavedPoster.claimed_at.isnot(None),
                  SavedPoster.claimed_at < cutoff)
          .all()
    )
    for poster in posters:
        poster.pipeline_status = "greenlit"
        poster.claimed_at = None
        poster.claimed_by = None

    rows = (
        db.query(UploadTracking)
          .filter(UploadTracking.status == "uploading",
                  UploadTracking.claimed_at.isnot(None),
                  UploadTracking.claimed_at < cutoff)
          .all()
    )
    for row in rows:
        row.status = "pending"
        row.claimed_at = None
        row.claimed_by = None

    jobs = (
        db.query(PipelineJob)
          .filter(PipelineJob.status == "running",
                  PipelineJob.started_at.isnot(None),
                  PipelineJob.started_at < cutoff)
          .all()
    )
    for job in jobs:
        job.status = "error"
        job.error = "Abandoned — worker node stopped reporting."
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
    limit = limit or int(get_setting(db, "process_batch_size", project=project))
    max_attempts = int(get_setting(db, "process_max_attempts", project=project))

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
            p for p in db.query(Project).filter(Project.is_active == 1).all()
            if pending_query(p.id).limit(1).first() is not None
        ]

        if len(active) <= 1:
            rows = pending_query(active[0].id if active else None).limit(limit).all()
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
        rel_path, filename = storage_path_for(db, title, poster, project=project)
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
    accounts = (
        db.query(UploadAccount)
          .filter(UploadAccount.project_id == project.id,
                  UploadAccount.is_enabled == 1)
          .all()
    )
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
    limit = int(account.daily_limit or 100)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def account_is_available(account: UploadAccount) -> bool:
    """False while an account is paused (bot-check, bad credentials, etc.)."""
    if not account.is_enabled:
        return False
    if account.paused_until and account.paused_until > datetime.utcnow():
        return False
    return True


def pause_account(db: Session, account: UploadAccount, *, minutes: int, reason: str) -> None:
    account.paused_until = datetime.utcnow() + timedelta(minutes=minutes)
    account.pause_reason = (reason or "")[:1000]


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
        accounts_q = accounts_q.filter(UploadAccount.project_id == project_id)

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

        project = resolve_project(db, account.project_id)
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
            "account": account_payload(db, account, include_secret=True),
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
    if account is not None and (account.pause_reason or account.paused_until):
        account.pause_reason = None
        account.paused_until = None
    if remote_id:
        tracking.remote_id = remote_id[:128]

    poster = db.query(SavedPoster).filter_by(id=tracking.saved_poster_id).first()
    if poster is None:
        return

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
    pause_reason: Optional[str] = None,
) -> None:
    """
    Record an upload failure and let the row be retried on a later run.

    `pause_minutes` is how the node reports a *systemic* problem — bot check,
    rejected credentials, a missing form field — which should stop the whole
    account rather than burning attempts on every queued image.
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

    if pause_minutes > 0:
        account = db.query(UploadAccount).filter_by(id=tracking.account_id).first()
        if account is not None:
            pause_account(db, account, minutes=pause_minutes,
                          reason=pause_reason or error)


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
) -> int:
    """
    Seed pending rows on `account_id` for every processed image — the
    ban-recovery path. Optionally mirror only what a specific dead account
    had uploaded, so a replacement account rebuilds exactly that catalogue.
    """
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise ValueError("Account not found")
    project = resolve_project(db, account.project_id)

    query = (
        db.query(ProcessedImage, SavedPoster, MasterTitle)
          .join(SavedPoster, ProcessedImage.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(ProcessedImage.is_current == 1,
                  ProcessedImage.project_id == project.id,
                  SavedPoster.deleted_at.is_(None))
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
) -> dict[str, Any]:
    """
    Serialize an account. `include_secret` is only ever true for an
    authenticated worker node — the browser never receives the password.
    """
    project = resolve_project(db, account.project_id)
    timings = dict(get_setting(db, "timings", project=project))
    if account.timing_json:
        try:
            timings.update(json.loads(account.timing_json))
        except (TypeError, ValueError):
            pass

    selectors = dict(get_setting(db, "selectors", project=project))
    if account.selectors_json:
        try:
            selectors.update(json.loads(account.selectors_json))
        except (TypeError, ValueError):
            pass

    data: dict[str, Any] = {
        "id":                 account.id,
        "project_id":         account.project_id,
        "project_slug":       project.slug,
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
    stamp = datetime.utcnow().strftime("%H:%M:%S")
    prefix = {"error": "ERROR", "warn": "WARN", "ok": "OK"}.get(level, "")
    line = f"[{stamp}] {prefix + ' ' if prefix else ''}{message}"
    job.log_text = ((job.log_text or "") + line + "\n")[-200_000:]


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
