"""Shared Jinja2 templates instance with palette + helpers globally available."""

import json as _json

from fastapi.templating import Jinja2Templates

from .config import APP_DIR, APP_VERSION, PALETTE
from .timeutil import fmt_local, to_local


templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# Make palette available in every template as `palette`.
templates.env.globals["palette"] = PALETTE
# Cache-bust suffix for static assets — every <script>/<link> in base.html
# appends `?v={{ app_version }}` so deploys force fresh fetches.
templates.env.globals["app_version"] = APP_VERSION


def _from_json(value):
    """Parse a JSON string value, returning [] on any error / None."""
    if not value:
        return []
    try:
        return _json.loads(value)
    except (TypeError, ValueError):
        return []


def _local_dt(value, fmt="%Y-%m-%d %H:%M"):
    """Jinja filter: render a stored UTC datetime as APP_TZ local time."""
    return fmt_local(value, fmt) if value else ""


templates.env.filters["from_json"] = _from_json
templates.env.filters["local_dt"]  = _local_dt
