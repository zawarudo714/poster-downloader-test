"""
Photoshop stage — runs the dashboard-supplied JSX on one image at a time.

Why one image per Photoshop invocation rather than a folder walk:

  * A single bad file can no longer kill a whole batch. The legacy script
    walked entire date trees inside Photoshop, so one unopenable image or one
    plugin hiccup took the rest of the run down with it.
  * Every image gets its own timeout. A hung Photoshop is detected and killed
    instead of stalling the queue overnight.
  * Progress is reported per image, so a crash mid-batch keeps credit for
    everything already finished.
  * Retries become granular — the pipeline retries one image, not 200.

The tradeoff is Photoshop startup cost per image. That's the right trade for
an unattended box: reliability over throughput.
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


# The JSX expects INPUT_FILE and OUTPUT_FILE to already exist as globals; we
# prepend them rather than templating the whole script so the dashboard-edited
# body stays byte-for-byte what the admin wrote.
_HEADER = 'var INPUT_FILE = {input};\nvar OUTPUT_FILE = {output};\n'


def _jsx_string(path: Path) -> str:
    """Render a filesystem path as a JSX string literal (forward slashes)."""
    return json.dumps(str(path).replace("\\", "/"))


class PhotoshopRunner:
    """
    Wraps a Photoshop executable and one script revision.

    The script is written to disk only when its version changes, so a long
    run doesn't rewrite the same file hundreds of times, and the version is
    visible on disk for debugging.
    """

    def __init__(self, *, exe: str, script: str, version: str,
                 work_dir: Path, timeout_s: int,
                 log: Callable[[str], None]):
        self.exe = exe
        self.timeout_s = timeout_s
        self.log = log
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.version = version
        self.script_body = script
        self.script_path = self.work_dir / f"process_{version}.jsx"

    def _write_script(self, source: Path, output: Path) -> Path:
        """
        Compose the runnable script for one image.

        Written per-image because the header carries that image's paths. Kept
        beside the versioned body so a failure can be reproduced by hand with
        the exact file Photoshop was given.
        """
        header = _HEADER.format(input=_jsx_string(source), output=_jsx_string(output))
        runnable = self.work_dir / "run_current.jsx"
        runnable.write_text(header + self.script_body, encoding="utf-8")
        return runnable

    def run(self, source: Path, output: Path) -> dict[str, Any]:
        """
        Process one image. Returns the sidecar result dict from the JSX.

        Photoshop's ExtendScript can't talk back over stdout reliably, so the
        script writes `<output>.result.json` and we read it. Absence of that
        file is itself diagnostic: it means the script died before its own
        error handler ran.
        """
        if not source.is_file():
            raise RuntimeError(f"Source file missing: {source}")

        output.parent.mkdir(parents=True, exist_ok=True)
        sidecar = Path(str(output) + ".result.json")

        # Clear stale artefacts so we can't mistake a previous run's output
        # for this one's.
        for stale in (output, sidecar):
            if stale.exists():
                try:
                    stale.unlink()
                except OSError:
                    pass

        runnable = self._write_script(source, output)
        started = time.time()

        try:
            completed = subprocess.run(
                [self.exe, "-r", str(runnable)],
                timeout=self.timeout_s,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            self._kill_photoshop()
            raise RuntimeError(
                f"Photoshop timed out after {self.timeout_s}s. "
                "The process was killed; raise process_timeout_s if this is a large image."
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Photoshop executable not found at {self.exe}. "
                "Fix photoshop_exe under Pipeline → Processing."
            )

        duration_ms = int((time.time() - started) * 1000)

        result: dict[str, Any] = {}
        if sidecar.is_file():
            try:
                result = json.loads(sidecar.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                result = {}
            finally:
                try:
                    sidecar.unlink()
                except OSError:
                    pass

        if result.get("ok") is False:
            raise RuntimeError(f"Script reported: {result.get('error', 'unknown error')}")

        if not output.is_file():
            # No output and no sidecar means Photoshop failed before the
            # script's own error path — surface whatever it printed.
            stderr = (completed.stderr or b"").decode("utf-8", "replace").strip()
            stdout = (completed.stdout or b"").decode("utf-8", "replace").strip()
            detail = stderr or stdout or "no output from Photoshop"
            raise RuntimeError(
                f"Photoshop produced no output file. Exit={completed.returncode}. {detail[:500]}"
            )

        return {
            "duration_ms": duration_ms,
            "file_size": output.stat().st_size,
            "width": result.get("width"),
            "height": result.get("height"),
        }

    def _kill_photoshop(self) -> None:
        """
        Force-kill Photoshop after a timeout.

        Necessary because a hung Photoshop holds a modal dialog that would
        block every subsequent image — one stuck file would otherwise end the
        night's work.
        """
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", os.path.basename(self.exe)],
                capture_output=True, timeout=30,
            )
            self.log("Killed hung Photoshop process")
        except Exception as e:
            self.log(f"Could not kill Photoshop: {e}")


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
        self.log(f"Claimed {len(batch)} image(s) · script {settings.get('script_version')}")
        if job_id:
            self.client.job_log(job_id, f"Claimed {len(batch)} images", progress=2)

        processed = failed = 0
        source_dir = self.temp_dir / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)

        for index, item in enumerate(batch, start=1):
            label = f"{item.get('title')} · {item.get('source_filename')}"
            source_path = source_dir / f"{item['poster_id']}_{item['source_filename']}"
            target_path = root / item["storage_path"]

            try:
                self.client.download_source(item["poster_id"], source_path)
                metrics = runner.run(source_path, target_path)

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
