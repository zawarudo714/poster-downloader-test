"""
Deploy from the command line — the same steps and checks as deploy_gui.py.

WHY: from 2026-11-05 the AI session does every deploy (DECIDED by the
owner, docs/DECISIONS.md). The window needs a person at it; this does not.
It signs in with this computer's SSH key, so no password is typed or stored.

It never records a deploy it has not proved: the server's commit must match
the one pushed AND /healthz must report the APP_VERSION in app/config.py.
Only then are DEPLOY_LOG.md and PENDING_DEPLOY.md written, through the very
function the window uses (deploy_gui.record_deploy).

    python tools/deploy_cli.py "v294: what changed"

Production only (origin -> 178.105.34.144). Stops at the first failure.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from deploy_gui import (DEFAULT_SERVER_CMDS, DISK_WARN_PCT,  # noqa: E402
                        SERVER_FREE_MARKER, SERVER_HEALTH_MARKER,
                        SERVER_SHA_MARKER, VERIFY_CMDS, local_app_version,
                        record_deploy, version_from_healthz)

HOST = "178.105.34.144"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", f"root@{HOST}"]
REMOTE, BRANCH = "origin", "main"
TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def emit(text, tag=""):
    print(("!! " if tag == "err" else "") + text, flush=True)


def run(args, **kw):
    emit("$ " + " ".join(args))
    r = subprocess.run(args, cwd=REPO, text=True, encoding="utf-8",
                       errors="replace", capture_output=True, **kw)
    out = (r.stdout or "") + (r.stderr or "")
    if out.strip():
        print(out.rstrip(), flush=True)
    return r.returncode, out


def fail(why):
    emit(why, "err")
    emit("NOT deployed and NOT recorded.", "err")
    sys.exit(1)


def main():
    # A Windows console or a redirect defaults to cp1252, which cannot print
    # the server's output and stopped the first run before it did anything.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        fail('Give a commit message: python tools/deploy_cli.py "vNNN: what changed"')
    message = sys.argv[1].strip()
    version = local_app_version(REPO)

    code, _ = run([sys.executable, "tools/preflight.py"])
    if code != 0:
        fail("preflight failed")

    run(["git", "add", "-A"])
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO).returncode != 0
    if staged:
        code, _ = run(["git", "commit", "-m", message + TRAILER])
        if code != 0:
            fail("commit failed")
    code, _ = run(["git", "push", REMOTE, BRANCH])
    if code != 0:
        fail("push failed")
    pushed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                            capture_output=True, text=True).stdout.strip()

    sha = health = used = ""
    for command in (DEFAULT_SERVER_CMDS, VERIFY_CMDS):
        code, out = run(SSH + [f"export TERM=dumb GIT_PAGER=cat; {command}"], timeout=900)
        for line in out.splitlines():
            if SERVER_SHA_MARKER in line:
                sha = line.split(SERVER_SHA_MARKER, 1)[1].strip()
            elif SERVER_HEALTH_MARKER in line:
                health = line.split(SERVER_HEALTH_MARKER, 1)[1].strip()
            elif SERVER_FREE_MARKER in line:
                used = line.split(SERVER_FREE_MARKER, 1)[1].strip()
        if code != 0:
            fail(f"server command failed (exit {code})")

    if used.isdigit() and int(used) >= DISK_WARN_PCT:
        emit(f"WARNING: the server disk is {used}% full.", "err")
    live = version_from_healthz(health)
    if not live:
        fail(f"the site reported no version: {health[:120]!r}")
    if version and live != version:
        fail(f"pushed v{version} but the site serves v{live}")
    if not (sha and (sha.startswith(pushed[:8]) or pushed.startswith(sha[:8]))):
        fail(f"pushed {pushed[:8]} but the server runs {sha[:8] or '?'}")

    emit(f"Live: v{live} at {sha[:8]} — matches what was pushed.")
    record_deploy(HOST, sha, version, message, emit)


if __name__ == "__main__":
    main()
