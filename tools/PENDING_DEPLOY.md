# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it. It is a
SEPARATE file from `DEPLOY_LOG.md` because the deploy tool rewrites that one
from scratch every time, so anything hand-written there is destroyed on the
next deploy — which is exactly what happened to the first version of this
mechanism, an hour after it was built.

---

**Waiting: v125** — targets 178.105.232.196 (test box)

  * `tools/deploy_gui.py`
      * clears this file after a confirmed deploy
      * records the APP_VERSION in each log line as its own field, and warns
        when a version is about to be reused against a different commit —
        two commits both shipped as v124 on 2026-08-27 and nothing noticed
      * the log header now says it is machine-written, so nobody puts a
        hand-written note there again
  * `app/config.py` — APP_VERSION 124 → 125
