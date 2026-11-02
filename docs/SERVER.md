<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## Deployment

```bash
ssh root@178.105.34.144
cd /path/to/poster_downloader_web
git pull
# Back up poster.db BEFORE any schema change.
docker compose up -d --build
```

**There is no separate migration step any more.** Startup runs
`create_all()`, then `migrate_schema()`, then `sync_projects()`, in that
order and for that reason — see `app/main.py`. `scripts/migrate_pipeline.py`
was deleted on 2026-09-01: 920 lines of one-off legacy import, plus a
re-export of `migrate_schema` that made a disposable script load-bearing for
`dev_setup.py`.

The Windows node is updated by COPYING `worker_service/`. That has no
receipt, so `AGENT_VERSION` is bumped in the same edit and read back on the
Nodes tab.

### THE SERVER IS SHARED — `DECIDED 2026-10-24`

The owner is putting a SECOND, unrelated project on `178.105.34.144`, run
from a different chat. **This project owns only these, and must touch
nothing else on the box:**

  * the folder `/opt/poster` (code, `docker-compose.yml`, `.env`) and
    everything under `/opt/poster/data` (database, pictures, backups);
  * the Docker Compose project `poster` and its one service, `web`;
  * port **80** on the host (`"80:8000"` in `docker-compose.yml`).

Anything else on the server — another folder under `/opt`, another
container, another port, a cron job, a web server — belongs to the other
project. Do not read its secrets, stop it, edit it or "tidy" it. And never
run anything that acts on EVERY project at once: `docker system prune`,
`docker image prune -a`, `docker volume prune`, `docker network prune`,
restarting the Docker service, rebooting the server, changing the firewall
or SSH settings, or upgrading the operating system. Those need the owner's
explicit go-ahead in the moment, because they reach the other project too.

`LEAD`, not yet settled: if the other project also needs port 80/443 or a
domain name, both will have to sit behind one front-door program (a reverse
proxy), which means changing this project's port line. Plan that with the
owner; never let either project quietly take the port.

---

