"""
Writing processed images to the Storage Box from the Linux server.

════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS AT ALL
════════════════════════════════════════════════════════════════════════════
The Photoshop stage writes to `S:` — a drive letter, mounted on the Windows
node. No credentials in the application, no code here.

Moving GPT generation to the Linux server broke that assumption: this box has
no S:. It needs its own way to put a file on the same Storage Box, at the same
relative path, so that the uploader on Windows finds it at `S:\\...` without
knowing or caring who wrote it.

Three options were considered. Mounting the box over CIFS/rclone would be less
code but introduces a network filesystem that can HANG — and a hung mount on a
2 GB box takes the whole site with it. Writing locally and having the node sync
adds a moving part and a delay. Pushing over SFTP fails loudly, needs no
OS-level configuration that a rebuild could lose, and keeps `storage_path`
meaning exactly what it meant before.

════════════════════════════════════════════════════════════════════════════
ONE PATH, TWO WAYS IN
════════════════════════════════════════════════════════════════════════════
`ProcessedImage.storage_path` stays relative to the storage root, as it always
has. The node resolves it against `S:`; this module resolves it against the
SFTP home. Same bytes, same string in the database, two access methods.

If `storage_sftp_host` is blank, this falls back to writing under a local
directory — which is what the dev setup uses, and what makes the pipeline
testable without a Storage Box.
"""

from __future__ import annotations

import io
import logging
import posixpath
from pathlib import Path

from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")


class StorageError(Exception):
    """Could not place the file. The caller must treat the image as unprocessed."""


def _settings(db: Session, project=None) -> dict:
    from .pipeline import get_secret, get_setting
    return {
        "host": str(get_setting(db, "storage_sftp_host", project=project) or "").strip(),
        "port": int(get_setting(db, "storage_sftp_port", project=project) or 22),
        "user": str(get_setting(db, "storage_sftp_user", project=project) or "").strip(),
        "password": get_secret(db, "storage_sftp_password", project=project),
        "root": str(get_setting(db, "storage_sftp_root", project=project) or "").strip("/"),
        "local_root": str(get_setting(db, "storage_local_root", project=project) or ""),
    }


def write_bytes(db: Session, rel_path: str, data: bytes, *, project=None) -> str:
    """
    Put `data` at `rel_path` (relative to the storage root). Returns the path
    that was written, unchanged, so the caller can record it.

    Creates intermediate directories — SFTP has no mkdir -p, so they are made
    one level at a time and "already exists" is ignored rather than treated as
    an error.
    """
    cfg = _settings(db, project)
    rel_path = rel_path.replace("\\", "/").lstrip("/")

    if not cfg["host"]:
        # Local fallback: no Storage Box configured. Used by the dev setup so
        # the whole pipeline can be exercised on a laptop.
        base = Path(cfg["local_root"] or "processed_local").resolve()
        target = base / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel_path

    try:
        import paramiko
    except ImportError:
        raise StorageError(
            "paramiko is not installed, so this server cannot write to the "
            "Storage Box. Add it to requirements.txt and rebuild."
        )

    transport = None
    try:
        transport = paramiko.Transport((cfg["host"], cfg["port"]))
        transport.connect(username=cfg["user"], password=cfg["password"])
        sftp = paramiko.SFTPClient.from_transport(transport)

        full = posixpath.join(cfg["root"], rel_path) if cfg["root"] else rel_path
        _mkdirs(sftp, posixpath.dirname(full))
        with sftp.file(full, "wb") as fh:
            fh.set_pipelined(True)
            fh.write(data)
        return rel_path
    except StorageError:
        raise
    except Exception as e:
        raise StorageError(f"Could not write {rel_path} to the Storage Box: {e}")
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass


def _mkdirs(sftp, directory: str) -> None:
    """mkdir -p over SFTP, which has no such thing natively."""
    if not directory or directory in ("/", "."):
        return
    parts = [p for p in directory.split("/") if p]
    path = ""
    for part in parts:
        path = f"{path}/{part}" if path else part
        try:
            sftp.stat(path)
        except IOError:
            try:
                sftp.mkdir(path)
            except IOError:
                # Raced with another writer, or it appeared between the stat
                # and the mkdir. Either way it exists now.
                pass


def check(db: Session, project=None) -> tuple[bool, str]:
    """
    Can we write? Used by the pipeline's preflight and the Test & Debug panel.

    Deliberately writes and deletes a small file rather than only connecting:
    the failure this catches in practice is a readable-but-not-writable path,
    which a login test would pass.
    """
    probe = ".pipeline_write_test"
    try:
        write_bytes(db, probe, b"ok", project=project)
        return True, "Storage is writable."
    except StorageError as e:
        return False, str(e)
