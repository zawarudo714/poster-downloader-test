"""Shared Jinja2 templates instance with palette + helpers globally available."""

import json as _json

from fastapi.templating import Jinja2Templates

from .config import APP_DIR, PALETTE


templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# Make palette available in every template as `palette`.
templates.env.globals["palette"] = PALETTE


def _from_json(value):
    """Parse a JSON string value, returning [] on any error / None."""
    if not value:
        return []
    try:
        return _json.loads(value)
    except (TypeError, ValueError):
        return []


templates.env.filters["from_json"] = _from_json
