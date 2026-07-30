"""
Worker node package — Photoshop processing and marketplace uploads.

Runs on a Windows VPS alongside Photoshop and Chrome. Holds no policy: the
dashboard supplies the script, selectors, timings, templates and credentials
on every cycle, so this machine is disposable and rebuildable from its token
alone.

Entry point:
    python -m worker_service.agent
"""

__version__ = "1.0.0"
