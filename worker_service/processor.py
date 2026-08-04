"""
Photoshop stage — runs the dashboard-supplied JSX on one image at a time.

════════════════════════════════════════════════════════════════════════════
HOW PHOTOSHOP IS DRIVEN, AND WHY IT LOOKS ODD
════════════════════════════════════════════════════════════════════════════
`Photoshop.exe -r script.jsx` does NOT behave like a normal CLI tool:

  * If Photoshop isn't running, it launches, runs the script, and then STAYS
    OPEN. The process never exits.
  * If Photoshop IS running, the new process hands the script to the existing
    instance and exits almost immediately — the script may not even have
    started yet, let alone finished.

So process exit tells you nothing about whether the work is done. An earlier
version of this module called `subprocess.run(..., timeout=...)` and waited
for exit; it blocked forever on the very first image, with Photoshop sitting
idle at its Home screen and the whole agent wedged. `subprocess.run` also
can't be relied on to unblock on timeout here, because it drains the captured
pipes afterwards and Photoshop's surviving children hold them open.

The correct model, implemented below:

  1. Ensure exactly one Photoshop instance is alive (start it if not).
  2. Dispatch the script to it and DON'T wait on the launcher.
  3. Poll for a result file the script writes when it finishes.
  4. If that file doesn't appear within the timeout, the run is genuinely
     hung — kill Photoshop and let the next image start it fresh.

Keeping Photoshop alive between images is also a straight speed win: startup
was costing 15-30s per image and is now paid once per batch.

════════════════════════════════════════════════════════════════════════════
ISOLATION IS PRESERVED
════════════════════════════════════════════════════════════════════════════
One image per script run still means:

  * A single bad file can't kill the batch — the legacy script walked whole
    date trees inside Photoshop, so one unopenable image took the rest down.
  * Every image has its own timeout, enforced by polling rather than by
    process exit.
  * Progress is reported per image, so a crash mid-batch keeps credit for
    everything already finished.
  * Retries are granular — one image, not 200.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .client import PipelineClient, PipelineError


# The JSX expects these to already exist as globals; we prepend them rather
# than templating the whole script so the dashboard-edited body stays
# byte-for-byte what the admin wrote.
#
# RESULT_FILE is a LOCAL path, deliberately: the script's completion signal
# must not depend on the network storage mount. Writing it beside OUTPUT_FILE
# on an SMB share meant the poll below could be delayed by client-side caching,
# or miss it entirely if the mount dropped mid-run.
_HEADER = (
    'var INPUT_FILE  = {input};\n'
    'var OUTPUT_FILE = {output};\n'
    'var RESULT_FILE = {result};\n'
)


def _jsx_string(path: Path) -> str:
    """Render a filesystem path as a JSX string literal (forward slashes)."""
    return json.dumps(str(path).replace("\\", "/"))


def read_jpeg_dimensions(path: Path) -> Optional[tuple[int, int]]:
    """
    Read width/height straight from a JPEG's SOF marker.

    The script also reports dimensions, but ExtendScript's loose typing makes
    that unreliable — it was returning booleans, which surfaced as a cheerful
    "TruexTrue" in the logs. The file on disk is the authority, so we measure
    it here and treat the script's numbers as a fallback only.

    Deliberately dependency-free (no Pillow) to keep the node easy to provision.
    """
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"\xff\xd8":
                return None
            while True:
                marker = handle.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                # SOF0-SOF15, excluding DHT/JPG/DAC which share the range
                if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6,
                                 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    handle.read(3)                      # length + precision
                    height = int.from_bytes(handle.read(2), "big")
                    width = int.from_bytes(handle.read(2), "big")
                    return (width, height) if width and height else None
                length = int.from_bytes(handle.read(2), "big")
                if length < 2:
                    return None
                handle.seek(length - 2, 1)
    except (OSError, ValueError):
        return None


class PhotoshopRunner:
    """
    Wraps a Photoshop executable and one script revision.

    The script is written to disk only when its version changes, so a long
    run doesn't rewrite the same file hundreds of times, and the version is
    visible on disk for debugging.
    """

    def __init__(self, *, exe: str, script: str, version: str,
                 work_dir: Path, timeout_s: int,
                 log: Callable[[str], None], warmup_s: int = 60,
                 restart_every: int = 25):
        self.exe = exe
        self.timeout_s = timeout_s
        self.warmup_s = warmup_s
        # Images completed since Photoshop last started. Drives the periodic
        # recycle below; the counter lives on the runner, which survives across
        # batches, so a long backlog is covered rather than just one batch.
        self.restart_every = restart_every
        self.images_since_start = 0
        self.log = log
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.version = version
        self.script_body = script
        self.script_path = self.work_dir / f"process_{version}.jsx"

    # ── Process management ─────────────────────────────────────────────────

    def _process_name(self) -> str:
        return os.path.basename(self.exe) or "Photoshop.exe"

    def is_running(self) -> bool:
        """Whether a Photoshop instance is already up."""
        try:
            import psutil
        except ImportError:
            # Without psutil we can't tell; assume not and let the launcher
            # deal with it. Dispatch is harmless either way.
            return False
        target = self._process_name().lower()
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info.get("name") or "").lower() == target:
                    return True
            except Exception:
                continue
        return False

    def ensure_running(self) -> None:
        """
        Make sure one Photoshop instance is alive, without blocking on it.

        Popen and walk away — Photoshop is a long-lived GUI app, so waiting for
        it to exit is exactly the mistake that wedged the agent before. The
        warmup pause gives it time to finish loading before we hand it a
        script; dispatching too early is silently ignored.
        """
        if self.is_running():
            return

        self.log("Starting Photoshop…")
        try:
            subprocess.Popen(
                [self.exe],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Photoshop executable not found at {self.exe}. "
                "Fix photoshop_exe under Pipeline → Processing."
            )

        deadline = time.time() + self.warmup_s
        while time.time() < deadline:
            if self.is_running():
                break
            time.sleep(1)
        # Even once the process exists it needs a moment before it will accept
        # a script. Short, and only paid once per batch.
        time.sleep(min(8, self.warmup_s))

    def kill(self) -> None:
        """
        Force-kill Photoshop.

        Used when a run hangs — typically a modal dialog nothing can dismiss,
        which would otherwise block every remaining image in the batch.
        """
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", self._process_name()],
                capture_output=True, timeout=30,
            )
            self.log("Killed hung Photoshop process")
            time.sleep(3)
        except Exception as e:
            self.log(f"Could not kill Photoshop: {e}")


    def _recycle_if_due(self) -> None:
        """
        Restart Photoshop periodically during a long run.

        Keeping it open is a large speed win, but Photoshop degrades over
        hundreds of operations — memory climbs and each image gets slower even
        with a purge after every one. A clean restart costs about 30 seconds
        and avoids sliding into timeouts partway through a backlog.

        Configurable from the dashboard; 0 disables it.
        """
        if self.restart_every <= 0:
            return
        if self.images_since_start < self.restart_every:
            return
        self.log(
            f"Recycling Photoshop after {self.images_since_start} images "
            f"(keeps a long run from slowing down)"
        )
        self.kill()
        self.images_since_start = 0

    # ── Running one image ──────────────────────────────────────────────────

    def _write_script(self, source: Path, output: Path, result_file: Path) -> Path:
        """
        Compose the runnable script for one image.

        The filename is unique per run. Reusing a fixed `run_current.jsx` meant
        that whenever Photoshop still held the previous script open — which is
        exactly what happens after a hung or killed run — the next write failed
        with `PermissionError: [Errno 13]` and took the whole batch down with
        it. A file Photoshop is holding is now simply never the one we write to.

        Old scripts are swept below rather than on exit, so a crashed agent
        doesn't leave them behind forever.
        """
        self._sweep_old_scripts()

        header = _HEADER.format(
            input=_jsx_string(source),
            output=_jsx_string(output),
            result=_jsx_string(result_file),
        )
        runnable = self.work_dir / f"run_{os.getpid()}_{int(time.time() * 1000)}.jsx"
        runnable.write_text(header + self.script_body, encoding="utf-8")
        return runnable

    def _sweep_old_scripts(self, keep_seconds: int = 600) -> None:
        """
        Delete generated scripts older than `keep_seconds`.

        Best-effort by design: anything Photoshop still has open will refuse to
        delete, and that's fine — we never write to those names again.
        """
        cutoff = time.time() - keep_seconds
        for path in self.work_dir.glob("run_*.jsx"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
        # Legacy fixed-name file from before this change.
        legacy = self.work_dir / "run_current.jsx"
        try:
            if legacy.exists() and legacy.stat().st_mtime < cutoff:
                legacy.unlink()
        except OSError:
            pass

    def run(self, source: Path, output: Path,
            heartbeat: Optional[Callable[[int], None]] = None) -> dict[str, Any]:
        """
        Process one image and return its metrics.

        Completion is detected by polling for the result file the script
        writes, NOT by waiting for a process to exit — see the module docstring
        for why that distinction matters.

        `heartbeat(elapsed_seconds)` is called roughly every 15s while waiting.
        A single image can take five minutes or more, and without this the node
        looks dead: it stops polling the server (so the dashboard marks it
        OFFLINE) and prints nothing locally, which is indistinguishable from
        being wedged.
        """
        if not source.is_file():
            raise RuntimeError(f"Source file missing: {source}")

        output.parent.mkdir(parents=True, exist_ok=True)

        result_file = self.work_dir / "last_result.json"
        # Older saved scripts write beside the output instead; poll both so a
        # customised script from before this change still works.
        legacy_sidecar = Path(str(output) + ".result.json")

        # Clear stale artefacts — otherwise a previous run's output could be
        # mistaken for this one's.
        for stale in (output, result_file, legacy_sidecar):
            try:
                if stale.exists():
                    stale.unlink()
            except OSError:
                pass

        self._recycle_if_due()
        self.ensure_running()
        runnable = self._write_script(source, output, result_file)
        started = time.time()

        # Dispatch. With Photoshop already up this returns almost immediately;
        # we don't depend on that, and a hang here is caught by the poll below.
        try:
            launcher = subprocess.Popen(
                [self.exe, "-r", str(runnable)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Photoshop executable not found at {self.exe}. "
                "Fix photoshop_exe under Pipeline → Processing."
            )

        # Poll for the script's own completion signal.
        deadline = time.time() + self.timeout_s
        raw: Optional[str] = None
        last_beat = time.time()

        while time.time() < deadline:
            for candidate in (result_file, legacy_sidecar):
                if candidate.is_file():
                    try:
                        raw = candidate.read_text(encoding="utf-8")
                    except OSError:
                        # Still being written; try again next tick.
                        raw = None
                    if raw:
                        break
            if raw:
                break

            # Keep the operator and the server informed while Photoshop works.
            if heartbeat and time.time() - last_beat >= 15:
                last_beat = time.time()
                try:
                    heartbeat(int(time.time() - started))
                except Exception:
                    pass

            time.sleep(0.5)

        try:
            launcher.poll()
            if launcher.returncode is None:
                launcher.kill()
        except Exception:
            pass

        if raw is None:
            # Nothing came back in time. Photoshop is most likely sitting on a
            # modal it can't get past, so kill it — the next image starts a
            # clean instance rather than inheriting the stuck one.
            self.kill()
            raise RuntimeError(
                f"Photoshop did not finish within {self.timeout_s}s. "
                "It was killed and will be restarted for the next image. "
                "If this repeats, run the script by hand in Photoshop — it is "
                "usually a dialog waiting for input (a missing plugin preset, "
                "a colour-profile prompt, or a font warning). "
                "Raise process_timeout_s if the image is simply very large."
            )

        duration_ms = int((time.time() - started) * 1000)

        result: dict[str, Any] = {}
        try:
            result = json.loads(raw)
        except ValueError:
            result = {}
        for artefact in (result_file, legacy_sidecar):
            try:
                if artefact.exists():
                    artefact.unlink()
            except OSError:
                pass

        self.images_since_start += 1

        if result.get("ok") is False:
            raise RuntimeError(f"Script reported: {result.get('error', 'unknown error')}")

        if not output.is_file():
            raise RuntimeError(
                f"Script reported success but no output file exists at {output}. "
                "Check the storage mount is writable from this machine."
            )

        # Measure the file we produced rather than trusting the script's own
        # numbers — see read_jpeg_dimensions for why.
        width = height = None
        measured = read_jpeg_dimensions(output)
        if measured:
            width, height = measured
        else:
            # Fall back to whatever the script said, but only if it's usable.
            for key, target in (("width", "w"), ("height", "h")):
                value = result.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                if target == "w":
                    width = int(value)
                else:
                    height = int(value)

        return {
            "duration_ms": duration_ms,
            "file_size": output.stat().st_size,
            "width": width,
            "height": height,
        }


class ProcessStage:
    """
    Drives the Photoshop stage: claim → download → process → archive → report.

    Failures are per-image and always reported, so the server's retry/backoff
    logic stays authoritative and this class never has to decide policy.
    """

    def __init__(self, client: PipelineClient, config: dict,
                 log: Callable[..., None]):
        self.client = client
        self.config = config
        self.log = log
        self.temp_dir = Path(config.get("temp_dir") or "C:/faa/temp")
        self._runner: Optional[PhotoshopRunner] = None
        self._runner_version: Optional[str] = None

    # ── Storage ────────────────────────────────────────────────────────────

    def _check_storage_writable(self, root: Path) -> None:
        """
        Confirm the archive is reachable and writable before claiming work.

        Windows maps network drives per elevation context, so a drive mapped in
        an elevated console is invisible to a normal one and vice versa. That
        produced a genuinely baffling failure: Python (elevated) could create
        the output folder, Photoshop (not elevated) could not write into it,
        and Photoshop's suppressed dialogs meant it reported success anyway.
        Checking here turns that into one clear message instead of N confusing
        ones.
        """
        probe = root / ".pipeline_write_test"
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            raise RuntimeError(
                f"Archive at {root} is not writable from this process: {e}\n"
                f"  • Is the drive mapped for the user running the agent? "
                f"Windows keeps mappings separate between elevated and normal "
                f"sessions.\n"
                f"  • Fix permanently by setting EnableLinkedConnections and "
                f"rebooting, then re-map the drive.\n"
                f"  • Check with:  net use   and   dir {root}"
            )

    def _storage_root(self, settings: dict) -> Path:
        """
        Where archives are written.

        The dashboard's value wins by default; `storage_root_override` in
        config.json exists for the case where one node has the box mounted at
        a different letter than the others.
        """
        override = self.config.get("storage_root_override")
        root = Path(override or settings["storage_root"])
        return root

    def _ensure_runner(self, settings: dict) -> PhotoshopRunner:
        """
        Build (or rebuild) the runner when the script version changes.

        Comparing versions is how a dashboard script edit reaches this machine
        — no deploy, no file copy, just the next batch picking up a new hash.
        """
        version = settings.get("script_version") or "dev"
        if self._runner is not None and self._runner_version == version:
            return self._runner

        if self._runner_version is not None:
            self.log(f"Script changed ({self._runner_version} → {version}) — reloading")

        self._runner = PhotoshopRunner(
            exe=settings["photoshop_exe"],
            script=settings["script"],
            version=version,
            work_dir=self.temp_dir / "scripts",
            timeout_s=int(settings.get("timeout_s") or 600),
            warmup_s=int(settings.get("warmup_s") or 60),
            restart_every=int(settings.get("restart_every") or 0),
            log=self.log,
        )
        self._runner_version = version
        return self._runner

    # ── Batch ──────────────────────────────────────────────────────────────

    def run_batch(self, *, job_id: Optional[int] = None,
                  project_id: Optional[int] = None) -> dict:
        """
        Process one claimed batch. Returns a summary for the job record.

        Every image is wrapped individually: one failure never aborts the
        rest, which is the difference between a bad night and a lost night.
        """
        settings = self.client.process_settings(project_id)
        batch = self.client.claim_process_batch(project_id=project_id)

        if not batch:
            self.log("Nothing to process")
            return {"claimed": 0, "processed": 0, "failed": 0}

        root = self._storage_root(settings)
        runner = self._ensure_runner(settings)

        # Fail the whole batch up front rather than one image at a time if the
        # archive isn't reachable. Photoshop suppresses its own save errors, so
        # without this the symptom is "script reported success but no output
        # file" repeated for every image in the run.
        self._check_storage_writable(root)

        self.log(f"Claimed {len(batch)} image(s) · script {settings.get('script_version')}")
        self.log(f"Archive: {root}   Photoshop: {settings.get('photoshop_exe')}")
        if job_id:
            self.client.job_log(job_id, [
                f"Claimed {len(batch)} images",
                f"Archive: {root}",
            ], progress=2)

        processed = failed = 0
        source_dir = self.temp_dir / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)

        for index, item in enumerate(batch, start=1):
            label = f"{item.get('title')} · {item.get('source_filename')}"
            source_path = source_dir / f"{item['poster_id']}_{item['source_filename']}"
            target_path = root / item["storage_path"]

            # Announce BEFORE the work, not just after. A single image can take
            # five minutes; previously the log went silent for that entire time
            # and there was no way to tell work from a hang.
            started_msg = f"[{index}/{len(batch)}] START {label}  (poster #{item['poster_id']})"
            self.log(started_msg)
            if job_id:
                self.client.job_log(
                    job_id, started_msg,
                    progress=int((index - 1) / len(batch) * 100),
                    note=f"{processed} done, {failed} failed",
                )

            def beat(elapsed: int, _i=index, _label=label) -> None:
                """Prove liveness locally and to the server during a long run."""
                self.log(f"[{_i}/{len(batch)}] …still working on {_label} ({elapsed}s)")
                # Refresh last_seen so the dashboard doesn't mark the node
                # OFFLINE just because one image is taking a while.
                try:
                    self.client.hello(hostname="", agent_version="")
                except Exception:
                    pass
                if job_id:
                    self.client.job_log(job_id, f"  …{elapsed}s elapsed")

            try:
                self.client.download_source(item["poster_id"], source_path)
                metrics = runner.run(source_path, target_path, heartbeat=beat)

                self.client.report_processed(
                    poster_id=item["poster_id"],
                    storage_path=item["storage_path"],
                    filename=item["output_filename"],
                    script_version=settings.get("script_version"),
                    **metrics,
                )
                processed += 1
                dims = f"{metrics.get('width')}x{metrics.get('height')}"
                message = f"[{index}/{len(batch)}] OK {label} → {dims}, {metrics['duration_ms']}ms"
                self.log(message)
                if job_id:
                    self.client.job_log(
                        job_id, message,
                        progress=int(index / len(batch) * 100),
                        note=f"{processed} done, {failed} failed",
                    )

            except Exception as e:
                failed += 1
                message = f"[{index}/{len(batch)}] FAILED {label}: {e}"
                self.log(message, level="error")
                if job_id:
                    self.client.job_log(job_id, message, level="error")
                try:
                    self.client.report_process_failure(
                        poster_id=item["poster_id"], error=str(e),
                    )
                except PipelineError as report_error:
                    # If we can't even report, the server's stale-claim reaper
                    # will recover this image — don't stop the batch.
                    self.log(f"Could not report failure: {report_error}", level="error")

            finally:
                # The node stays stateless: temp copies never accumulate.
                try:
                    if source_path.exists():
                        source_path.unlink()
                except OSError:
                    pass

        summary = {"claimed": len(batch), "processed": processed, "failed": failed}
        self.log(f"Batch done — {processed} processed, {failed} failed")
        return summary

    # ── Test hooks (Test & Debug panel) ────────────────────────────────────

    def test_download(self, job_id: int, payload: dict) -> dict:
        """
        Verify server → node transfer only.

        Deliberately touches nothing else: if this passes, the URL, token and
        network path are all good and any later failure is Photoshop's or the
        marketplace's, not plumbing.
        """
        items = payload.get("items") or []
        if not items:
            raise RuntimeError("No source images on that title.")

        self.client.job_log(job_id, f"Downloading {len(items)} source image(s)", progress=5)
        target_dir = self.temp_dir / "test_download"
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        results = []
        total_bytes = 0
        for index, item in enumerate(items, start=1):
            started = time.time()
            target = target_dir / item["filename"]
            size = self.client.download_source(item["poster_id"], target)
            elapsed = max(time.time() - started, 0.001)
            total_bytes += size
            speed = size / elapsed / 1024
            self.client.job_log(
                job_id,
                f"[{index}/{len(items)}] {item['filename']} — "
                f"{size / 1024:.0f} KB in {elapsed:.2f}s ({speed:.0f} KB/s)",
                progress=int(index / len(items) * 90),
            )
            results.append({"filename": item["filename"], "bytes": size,
                            "seconds": round(elapsed, 2)})

        self.client.job_log(job_id, "Download test complete", level="ok", progress=100)
        return {"files": results, "total_bytes": total_bytes,
                "temp_dir": str(target_dir)}

    def test_process(self, job_id: int, payload: dict) -> dict:
        """
        Run the current script on exactly one image.

        Output goes to the _tests/ prefix the server chose, so experimenting
        can never overwrite a live derivative. This is the fast edit-and-check
        loop for the JSX: seconds instead of a batch.
        """
        settings = payload["settings"]
        root = self._storage_root(settings)
        runner = self._ensure_runner(settings)

        source = self.temp_dir / "test_process" / payload["source_filename"]
        target = root / payload["storage_path"]

        self.client.job_log(
            job_id,
            f"Script version {settings.get('script_version')} · "
            f"work {settings.get('work_width', '?')}px → out {settings.get('output_width', '?')}px",
            progress=5,
        )

        self.client.job_log(job_id, f"Downloading {payload['source_filename']}", progress=15)
        size = self.client.download_source(payload["poster_id"], source)
        self.client.job_log(job_id, f"Source is {size / 1024:.0f} KB", progress=30)

        self.client.job_log(job_id, "Running Photoshop…", progress=40)
        try:
            metrics = runner.run(source, target)
        finally:
            try:
                if source.exists():
                    source.unlink()
            except OSError:
                pass

        self.client.job_log(
            job_id,
            f"Output {metrics.get('width')}x{metrics.get('height')} · "
            f"{metrics['file_size'] / 1024:.0f} KB · {metrics['duration_ms']}ms",
            level="ok", progress=100,
        )
        return {
            "output_path": str(target),
            "storage_path": payload["storage_path"],
            **metrics,
        }
