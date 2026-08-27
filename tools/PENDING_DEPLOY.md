# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v130 — audit batch 3, the admin screens** · targets
178.105.232.196 (test box)

### The server would fetch any address you typed, including its own

Four controls end in the server downloading a URL a person typed — the
worker's SAVE box, the worker's two REPLACE boxes, and the admin's "+ ADD".
All four share one validator, which checked the scheme, that a host was
present, and the file extension.

It never checked WHERE the host pointed. `http://127.0.0.1:8000/x.jpg` or
`http://169.254.169.254/meta.jpg` would make the server fetch its own admin
API, or the cloud provider's credentials endpoint, and save the result as a
poster.

There was a control for this that could never be switched on: an
environment variable (invisible from the dashboard), off by default and
`MEASURED` always off, with an allow-list of TMDB only — so enabling it
would have blocked every MUSIK save.

**Fixed as two separate questions:**

  * Internal / private / loopback / link-local addresses refused ALWAYS,
    no setting, every project. Cannot refuse a legitimate image. The host
    is RESOLVED rather than pattern-matched, so a public-looking name that
    points at 127.0.0.1 is caught.
  * The allow-list is now `allowed_image_hosts`, per-project and
    dashboard-editable. **Blank by default = today's exact behaviour**, so
    nothing changes for either project until you choose to narrow it.

### Listing template help offered variables that render empty

The Upload tab told you `{year}` and `{content_type}` were available for
every project. Both render EMPTY where the project has those capabilities
switched off, so it was inviting you to build a listing template with a
hole in it. Now assembled from the project's declared capabilities.

### AUDIT.md's last two open entries closed

"Paste URL to add one" verified as present and deliberate — it is the
admin's only way to add a MUSIK image directly, so it stays. The help text
above was the other.

### Files

`app/routes/worker.py`, `app/routes/admin.py`, `app/pipeline.py`,
`app/config.py` (129 → 130), `app/templates/admin_pipeline.html`,
`AUDIT.md`.

**Verified:** preflight green. The validator was lifted out of the shipped
file and run against 12 URL cases with an injected resolver — public hosts
pass, every private range and a public name resolving to loopback are
refused, and the two failure messages were asserted not to describe each
other's cause.

**Not verified:** no real image has been saved through the new path. **This
is the thing to test after deploying** — save one image as a worker in each
project. It is the most-used path in the app and I have changed the
function that guards it.
