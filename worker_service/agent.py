"""
Worker node daemon.

Run on the Windows VPS:
    python -m worker_service.agent

One loop, in priority order:

    1. Jobs        — anything the dashboard explicitly asked for. Test jobs
                     come first so a single-image diagnostic never waits
                     behind a batch.
    2. Photoshop   — process greenlit images.
    3. Uploads     — push processed images to marketplace accounts that still
                     have quota left today.

Design notes worth preserving:

  * The node is stateless. Config here is only "where is the server, who am I,
    and where is my scratch space". Everything about *how* work is done comes
    from the dashboard on every cycle, so a settings change needs no restart
    and no deploy.
  * Nothing is fatal. Network drops, a wedged Photoshop, a marketplace bot
    wall — all are logged and retried on the next cycle. The server's
    stale-claim reaper recovers anything this process was holding when it
    died, so a hard crash costs one cycle, not a queue.
  * The loop is deliberately serial. Photoshop and Chrome are both
    resource-hungry and neither tolerates contention well; running one thing
    at a time is what makes an unattended box predictable.
"""

from __future__ import annotations

import argparse
import platform
import socket
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from .client import PipelineClient, PipelineError, load_config
from .processor import ProcessStage
from .uploader import UploadStage


AGENT_VERSION = "1.0.0"

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.json"


class Agent:
    def __init__(self, config: dict, *, once: bool = False,
                 project_id: Optional[int] = None,
                 stages: Optional[set[str]] = None):
        self.config = config
        self.once = once
        self.project_id = project_id
        # Lets you dedicate a node to one stage — e.g. a beefier box for
        # Photoshop and a small one for uploads — without separate builds.
        self.stages = stages or {"process", "upload"}

        self.client = PipelineClient(config["server_url"], config["token"])
        self.processor = ProcessStage(self.client, config, self.log)
        self.uploader = UploadStage(self.client, config, self.log)

        self.hostname = socket.gethostname()
        self.capabilities: set[str] = set()
        self.poll_interval = 30
        self.schedule_mode = "continuous"
        self.daily_start_hour = 6

        log_dir = Path(config.get("log_dir") or (Path(config.get("temp_dir", ".")) / "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"agent_{datetime.now():%Y-%m-%d}.log"

    # ── Logging ────────────────────────────────────────────────────────────

    def log(self, message: str, *, level: str = "info") -> None:
        """
        Local log — console plus a daily file.

        Dashboard-visible logging is separate (client.job_log) and attached to
        a job. This one is for the case where the node can't reach the server
        at all, which is exactly when you most need a local record.
        """
        prefix = {"error": "ERROR", "warn": "WARN ", "ok": "OK   "}.get(level, "     ")
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {prefix} {message}"
        print(line, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    # ── Handshake ──────────────────────────────────────────────────────────

    def handshake(self) -> bool:
        """
        Report in and refresh runtime hints. False means "couldn't reach the
        server" — the caller sleeps and retries rather than exiting, because a
        VPS reboot shouldn't need a human to restart the agent.
        """
        try:
            data = self.client.hello(hostname=self.hostname, agent_version=AGENT_VERSION)
        except PipelineError as e:
            self.log(f"Handshake failed: {e}", level="error")
            return False

        node = data.get("node") or {}
        self.capabilities = set(node.get("capabilities") or [])
        self.poll_interval = int(data.get("poll_interval_s") or 30)
        self.schedule_mode = data.get("schedule_mode") or "continuous"
        self.daily_start_hour = int(data.get("daily_start_hour") or 6)
        return True

    def can(self, capability: str) -> bool:
        """True when the server grants it AND this process is running it."""
        return capability in self.capabilities and capability in self.stages

    def within_schedule(self) -> bool:
        """
        Honour 'daily' mode.

        Uses node-local time deliberately: the operator thinks in terms of the
        machine's clock, and uploads are often deliberately timed to look like
        ordinary daytime activity.
        """
        if self.schedule_mode != "daily":
            return True
        return datetime.now().hour >= self.daily_start_hour

    # ── Jobs ───────────────────────────────────────────────────────────────

    def handle_job(self) -> bool:
        """
        Run one queued job if there is one. Returns True if work was done, so
        the caller can loop again immediately instead of sleeping.
        """
        try:
            job = self.client.claim_job()
        except PipelineError as e:
            self.log(f"Could not claim a job: {e}", level="error")
            return False
        if not job:
            return False

        job_id = job["id"]
        kind = job["kind"]
        payload = job.get("payload") or {}
        self.log(f"Job #{job_id} — {kind}")

        # A payload the server couldn't resolve (deleted row, missing
        # derivative) fails fast with the reason, rather than throwing
        # something opaque halfway through.
        if payload.get("error"):
            self.client.finish_job(job_id, ok=False, error=payload["error"])
            self.log(f"Job #{job_id} rejected: {payload['error']}", level="error")
            return True

        try:
            if kind == "test_download":
                result = self.processor.test_download(job_id, payload)
            elif kind == "test_process":
                result = self.processor.test_process(job_id, payload)
            elif kind == "test_upload":
                result = self.uploader.test_upload(job_id, payload)
            elif kind == "process":
                result = self.processor.run_batch(
                    job_id=job_id, project_id=payload.get("project_id") or self.project_id)
            elif kind == "upload":
                result = self.uploader.run_batch(
                    job_id=job_id,
                    account_id=payload.get("account_id"),
                    project_id=payload.get("project_id") or self.project_id)
            else:
                self.client.finish_job(job_id, ok=False, error=f"Unknown job kind: {kind}")
                return True

            self.client.finish_job(job_id, ok=True, result=result)
            self.log(f"Job #{job_id} finished", level="ok")

        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            self.log(f"Job #{job_id} failed: {detail}", level="error")
            # Traceback goes to the dashboard too — the whole point is not
            # having to RDP into this machine to read a log.
            self.client.job_log(job_id, traceback.format_exc().splitlines()[-12:],
                                level="error")
            self.client.finish_job(job_id, ok=False, error=detail)

        return True

    # ── Autonomous stages ──────────────────────────────────────────────────

    def run_process_stage(self) -> bool:
        if not self.can("process"):
            return False
        try:
            summary = self.processor.run_batch(project_id=self.project_id)
            return summary.get("claimed", 0) > 0
        except PipelineError as e:
            self.log(f"Process stage could not talk to the server: {e}", level="error")
        except Exception as e:
            self.log(f"Process stage error: {type(e).__name__}: {e}", level="error")
            self.log(traceback.format_exc(), level="error")
        return False

    def run_upload_stage(self) -> bool:
        if not self.can("upload"):
            return False
        try:
            # Skip the browser launch entirely when nothing has quota left —
            # starting Chrome to discover there's no work is pure waste.
            quotas = self.client.quotas()
            usable = [q for q in quotas if q.get("available") and q.get("remaining", 0) > 0]
            if not usable:
                if quotas:
                    self.log("All accounts are at their daily cap or paused")
                return False

            summary = self.uploader.run_batch(project_id=self.project_id)
            return summary.get("claimed", 0) > 0
        except PipelineError as e:
            self.log(f"Upload stage could not talk to the server: {e}", level="error")
        except Exception as e:
            self.log(f"Upload stage error: {type(e).__name__}: {e}", level="error")
            self.log(traceback.format_exc(), level="error")
        return False

    # ── Main loop ──────────────────────────────────────────────────────────

    def run(self) -> None:
        self.log(f"Agent {AGENT_VERSION} starting on {self.hostname} "
                 f"({platform.system()} {platform.release()})")
        self.log(f"Server: {self.config['server_url']}")
        self.log(f"Stages enabled locally: {', '.join(sorted(self.stages))}")

        while True:
            did_work = False

            if not self.handshake():
                # Unreachable server: back off but keep trying. A reboot or a
                # brief outage must not require restarting the agent.
                time.sleep(min(self.poll_interval * 2, 120))
                if self.once:
                    return
                continue

            if not self.capabilities:
                self.log("This node has no capabilities enabled on the server — "
                         "check Pipeline → Nodes.", level="warn")

            # Jobs first, and drained fully: an operator waiting on a test
            # should never sit behind a batch this node chose to start.
            while self.handle_job():
                did_work = True
                if self.once:
                    return

            if self.within_schedule():
                if self.run_process_stage():
                    did_work = True
                if self.run_upload_stage():
                    did_work = True
            else:
                self.log(f"Outside the run window (starts at "
                         f"{self.daily_start_hour:02d}:00) — idling")

            if self.once:
                return

            # Only sleep when idle. After real work, loop straight back so a
            # backlog drains without an artificial pause between batches.
            if not did_work:
                time.sleep(self.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Poster pipeline worker node (Photoshop + marketplace uploads).",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="Path to config.json (default: alongside this module).")
    parser.add_argument("--once", action="store_true",
                        help="Run a single cycle and exit. Useful for verifying setup.")
    parser.add_argument("--project", type=int, default=None,
                        help="Restrict this node to one project id.")
    parser.add_argument("--stages", default="process,upload",
                        help="Comma-separated stages to run locally (process, upload).")
    args = parser.parse_args()

    config = load_config(args.config)
    stages = {s.strip() for s in args.stages.split(",") if s.strip()}

    agent = Agent(config, once=args.once, project_id=args.project, stages=stages)
    try:
        agent.run()
    except KeyboardInterrupt:
        agent.log("Stopped by operator")
        sys.exit(0)


if __name__ == "__main__":
    main()
