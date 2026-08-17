"""
HTTP client for the pipeline API.

The node holds no policy and no configuration beyond the server URL and its
token — every setting, script, selector and credential arrives through this
client at runtime. That is what makes the dashboard the single control
surface, and what lets this machine be wiped and rebuilt with only
config.json restored.

Everything here is deliberately dependency-light (requests only) so the node
can be provisioned on a bare Windows box without a build toolchain.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

import requests


class PipelineError(RuntimeError):
    """Raised for a non-2xx response, carrying the server's detail message."""


class PipelineClient:
    def __init__(self, base_url: str, token: str, *, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "X-Worker-Token": token,
            "Accept": "application/json",
        })

    # ── Low-level ──────────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{self.base}/api/pipeline{path}"

    def _request(self, method: str, path: str, **kwargs) -> Any:
        """
        One HTTP call with a short retry on transient network failure.

        Retries only connection-level errors, never HTTP errors — a 400 means
        the request was wrong and repeating it is pointless, while a dropped
        connection is worth another go before abandoning a batch.
        """
        url = self._url(path)
        kwargs.setdefault("timeout", self.timeout)

        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = self.session.request(method, url, **kwargs)
                break
            except requests.RequestException as e:
                last_error = e
                if attempt == 2:
                    raise PipelineError(f"Cannot reach {url}: {e}") from e
                time.sleep(2 * (attempt + 1))
        else:  # pragma: no cover — loop always breaks or raises
            raise PipelineError(str(last_error))

        if response.status_code >= 400:
            detail = response.text[:400]
            try:
                payload = response.json()
                detail = payload.get("detail", detail)
            except ValueError:
                pass
            raise PipelineError(f"{response.status_code} on {path}: {detail}")

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.content

    def get(self, path: str, **params) -> Any:
        return self._request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, payload: Optional[dict] = None) -> Any:
        return self._request("POST", path, json=payload or {})

    # ── Handshake ──────────────────────────────────────────────────────────

    def hello(self, *, hostname: str, agent_version: str) -> dict:
        """
        Announce presence and pick up poll/schedule hints.

        Called every cycle, not just at startup, so the dashboard's node
        health indicator reflects reality and schedule changes take effect
        without a restart.
        """
        return self.post("/hello", {"hostname": hostname, "agent_version": agent_version})

    # ── Photoshop stage ────────────────────────────────────────────────────

    def process_settings(self, project_id: Optional[int] = None) -> dict:
        return self.get("/process/settings", project_id=project_id)

    def claim_process_batch(self, *, limit: Optional[int] = None,
                            project_id: Optional[int] = None) -> list[dict]:
        data = self.post("/process/claim", {"limit": limit, "project_id": project_id})
        return data.get("items", [])

    def download_source(self, poster_id: int, target: Path) -> int:
        """
        Stream a source image to disk. Returns bytes written.

        Streamed rather than buffered because a batch can be dozens of
        multi-megabyte files and the node may be memory-constrained.
        """
        url = self._url(f"/source/{poster_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(url, stream=True, timeout=self.timeout) as response:
            if response.status_code >= 400:
                raise PipelineError(f"{response.status_code} downloading poster {poster_id}")
            written = 0
            with open(target, "wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        handle.write(chunk)
                        written += len(chunk)
        return written

    def report_processed(self, **fields) -> dict:
        """Report success. Expects poster_id, storage_path, filename, plus optional metrics."""
        return self.post("/process/report", {"ok": True, **fields})

    def report_process_failure(self, *, poster_id: int, error: str) -> dict:
        return self.post("/process/report",
                         {"ok": False, "poster_id": poster_id, "error": error})

    # ── Upload stage ───────────────────────────────────────────────────────

    def claim_upload_batch(self, *, account_id: Optional[int] = None,
                           limit: Optional[int] = None,
                           project_id: Optional[int] = None) -> dict:
        """
        Claim an upload batch. The response is self-contained: account
        credentials, selectors, timings, quota and per-image rendered
        title/keywords/description.
        """
        return self.post("/upload/claim", {
            "account_id": account_id, "limit": limit, "project_id": project_id,
        })

    def quotas(self, account_id: Optional[int] = None) -> list[dict]:
        data = self.post("/upload/quota", {"account_id": account_id})
        return data.get("accounts", [])

    def report_uploaded(self, *, tracking_id: int, remote_id: Optional[str] = None) -> dict:
        return self.post("/upload/report",
                         {"ok": True, "tracking_id": tracking_id, "remote_id": remote_id})

    def report_upload_failure(self, *, tracking_id: int, error: str,
                              screenshot: Optional[str] = None,
                              pause_minutes: int = 0,
                              pause_reason: Optional[str] = None,
                              pause_immediate: bool = True) -> dict:
        """
        Report a failure. `pause_minutes` signals a problem affecting the
        whole account rather than this one image, so the server can park it
        instead of burning attempts on every queued image.

        `pause_immediate=False` says "this MIGHT be systemic" — the server
        then waits for a run of them before parking. A missing form field is
        the case that matters: FineArtAmerica serves two versions of its
        upload form, so one miss usually means we got the other page.
        """
        return self.post("/upload/report", {
            "ok": False,
            "tracking_id": tracking_id,
            "error": error,
            "screenshot": screenshot,
            "pause_minutes": pause_minutes,
            "pause_reason": pause_reason,
            "pause_immediate": pause_immediate,
        })

    def download_processed(self, tracking_id: int, target: Path) -> int:
        """
        Fetch a processed image over HTTP.

        Only used when the storage mount isn't readable — normally the node
        reads straight off its mounted drive. Having this fallback means a
        broken mount degrades to slow rather than to a stopped pipeline.
        """
        url = self._url(f"/upload/image/{tracking_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(url, stream=True, timeout=self.timeout) as response:
            if response.status_code >= 400:
                raise PipelineError(f"{response.status_code} downloading image for {tracking_id}")
            written = 0
            with open(target, "wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        handle.write(chunk)
                        written += len(chunk)
        return written

    # ── Jobs ───────────────────────────────────────────────────────────────

    def claim_job(self, kinds: Optional[list[str]] = None) -> Optional[dict]:
        data = self.post("/jobs/claim", {"kinds": kinds})
        return data.get("job")

    def job_log(self, job_id: int, lines, *, level: str = "info",
                progress: Optional[int] = None, note: Optional[str] = None) -> None:
        """
        Append to a job's log. Failures here are swallowed: losing a log line
        must never abort real work, and the run's outcome is still recorded by
        the report endpoints.
        """
        if isinstance(lines, str):
            lines = [lines]
        try:
            self.post(f"/jobs/{job_id}/log", {
                "lines": lines, "level": level, "progress": progress, "note": note,
            })
        except PipelineError:
            pass

    def finish_job(self, job_id: int, *, ok: bool,
                   result: Optional[dict] = None,
                   error: Optional[str] = None) -> None:
        try:
            self.post(f"/jobs/{job_id}/finish",
                      {"ok": ok, "result": result, "error": error})
        except PipelineError:
            pass

    # ── Artefacts ──────────────────────────────────────────────────────────

    def upload_artifact(self, *, kind: str, name: str, data: bytes) -> Optional[str]:
        """
        Push a failure screenshot or page dump to the server and return its
        recorded path.

        Artefacts live server-side rather than on this disposable node, so
        they're still there when you open the dashboard to work out why an
        upload broke.
        """
        url = self._url("/artifact")
        try:
            response = self.session.post(
                url, params={"kind": kind, "name": name}, data=data,
                timeout=self.timeout,
                headers={"Content-Type": "application/octet-stream"},
            )
            if response.status_code >= 400:
                return None
            return response.json().get("path")
        except (requests.RequestException, ValueError):
            return None


def load_config(path: Path) -> dict:
    """
    Read config.json.

    Only three things belong in here — where the server is, who this node is,
    and local scratch/storage paths. Anything that affects *how* work is done
    belongs in the dashboard so it can be changed without touching this
    machine.
    """
    if not path.is_file():
        raise SystemExit(
            f"Missing config file: {path}\n\n"
            "Create it with:\n"
            "{\n"
            '  "server_url": "http://YOUR.SERVER.IP:8000",\n'
            '  "token": "<token shown when you registered the node>",\n'
            '  "temp_dir": "C:/faa/temp",\n'
            '  "storage_root_override": null\n'
            "}\n"
        )
    config = json.loads(path.read_text(encoding="utf-8"))
    for required in ("server_url", "token"):
        if not config.get(required):
            raise SystemExit(f"config.json is missing '{required}'.")
    return config
