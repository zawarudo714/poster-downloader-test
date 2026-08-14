"""
Shared Jinja2 templates instance with palette + helpers globally available.

════════════════════════════════════════════════════════════════════════════
PROJECT VOCABULARY IS INJECTED INTO EVERY TEMPLATE
════════════════════════════════════════════════════════════════════════════
This app runs several niches side by side. The movie project calls its files
"posters", finds them on TMDB and processes them in Photoshop; MUSIK calls
them "images", finds them through Brave and generates them with AI. A label
that says "poster" or "TMDB" is therefore WRONG on at least one screen, and
will be wrong on more of them with every niche added.

Rather than have each route remember to pass the words through, the subclass
below injects them into every render, so any template can write:

    {{ noun }} {{ nouns }} {{ Noun }} {{ NOUNS }}     poster / posters / …
    {{ source_label }}                                TMDB · Brave image search
    {{ target_label }}                                FineArtAmerica
    {{ processor_label }}                             Photoshop · AI generation
    {{ has_year }} {{ has_content_type }}             capability flags

WHY A SUBCLASS AND NOT env.globals: globals are static, and these change per
request with the active project. And why not `{% set %}` in base.html — a
top-level set in a PARENT template is not visible inside a child's blocks,
which is a trap this codebase has already fallen into once.

Values fall back to the movie project's words when there is no active project
(the login page, an error page), so nothing ever renders blank.
"""

import json as _json

from fastapi.templating import Jinja2Templates

from .config import APP_DIR, APP_VERSION, PALETTE
from .timeutil import fmt_local, to_local


class ProjectAwareTemplates(Jinja2Templates):
    """Jinja2Templates that adds the active project's vocabulary to every context."""

    def TemplateResponse(self, *args, **kwargs):  # noqa: N802 (Starlette's name)
        # Starlette supports both TemplateResponse(request, name, ctx) and the
        # older TemplateResponse(name, ctx). Both are used in this codebase, so
        # the context dict is found by type rather than by position.
        context = None
        for arg in args:
            if isinstance(arg, dict):
                context = arg
                break
        if context is None:
            context = kwargs.get("context")

        if isinstance(context, dict):
            request = context.get("request")
            for arg in args:
                if hasattr(arg, "state") and hasattr(arg, "url"):
                    request = arg
                    break
            context.update(_vocabulary(getattr(getattr(request, "state", None),
                                               "project_ctx", None)))
        return super().TemplateResponse(*args, **kwargs)


def _vocabulary(pctx) -> dict:
    """The words and capability flags a template needs, with safe fallbacks."""
    pctx = pctx or {}
    proj = pctx.get("active_project")

    noun = (pctx.get("item_noun") or "poster")
    nouns = (pctx.get("item_nouns") or "posters")

    from .pipeline import PROCESSOR_LABELS, SITE_LABELS

    source = getattr(proj, "source_site", "") or ""
    target = getattr(proj, "target_site", "") or ""
    processor = getattr(proj, "processor", "") or ""

    return {
        "noun": noun,
        "nouns": nouns,
        "Noun": noun[:1].upper() + noun[1:],
        "Nouns": nouns[:1].upper() + nouns[1:],
        "NOUN": noun.upper(),
        "NOUNS": nouns.upper(),
        "source_label": SITE_LABELS.get(source, source or "the source site"),
        "target_label": SITE_LABELS.get(target, target or "the marketplace"),
        "processor_label": PROCESSOR_LABELS.get(processor, processor or "processing"),
        "has_year": bool(getattr(proj, "has_year", 1)),
        "has_content_type": bool(getattr(proj, "has_content_type", 1)),
        "search_mode": getattr(proj, "search_mode", "external"),
        "project_name": getattr(proj, "name", ""),
    }


templates = ProjectAwareTemplates(directory=str(APP_DIR / "templates"))

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
