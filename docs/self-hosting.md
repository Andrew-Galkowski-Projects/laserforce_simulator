# Self-hosting on a home PC with Tailscale Funnel

This is the canonical deploy. The whole stack runs on one machine under Docker, and Tailscale
Funnel publishes it at a free, stable HTTPS URL. You need no domain, no router port-forwarding,
and your home IP stays hidden. The Fly.io deploy (`fly.toml`) is optional and currently off: CI
deploys there only once a `FLY_API_TOKEN` repo secret is set, and it runs the same image.

```
browser --HTTPS--> Tailscale Funnel --tunnel--> tailscale container --HTTP--> app (gunicorn :8000)
                                                                              /api/ /admin/ /static/ /media/ -> Django
                                                                              else  -> the Angular SPA (index.html)
                                                                              worker (Celery)
                                                                              db (Postgres 16), redis
```

Django serves the SPA itself (ADR-0049): the image builds the Angular bundle into `/srv/spa`, and
the Angular SPA is at `https://<name>.<tailnet>.ts.net/`, with every deep link falling back to
`index.html`. `/api/`, `/admin/`, `/static/` and `/media/` are Django's own. An old `/app/<path>`
link redirects to `/<path>`.

Files: `docker-compose.selfhost.yml`, `deploy/tailscale/serve.json`, `.env.selfhost.example` and
the root `Dockerfile`.

## The URL

`https://<TS_HOSTNAME>.<tailnet-name>.ts.net`, e.g. `https://laserforce.tail1234.ts.net`.
`TS_HOSTNAME` defaults to `laserforce`. The tailnet name is on the
[DNS page](https://login.tailscale.com/admin/dns) of the Tailscale admin console; you can re-roll it
once to a friendlier random name, but you cannot choose it freely. A custom domain needs a
different route (Cloudflare Tunnel or a reverse proxy) instead of Funnel.

## One-time setup

1. **Docker Desktop.** Install Docker Desktop, then enable *Settings > General > Start Docker Desktop
   when you sign in*.
2. **Tailscale account.** Sign up at <https://tailscale.com>. You do not need to install the
   Tailscale client on the PC; the container joins the tailnet itself.
3. **Tailnet settings** (admin console):
   - *DNS*: MagicDNS **on**, and *HTTPS Certificates* **enabled**.
   - *Access controls*: the policy must grant the `funnel` node attribute. New tailnets ship with
     it; if yours does not, add:
     ```json
     "nodeAttrs": [{ "target": ["autogroup:member"], "attr": ["funnel"] }]
     ```
4. **Auth key (recommended).** *Settings > Keys > Generate auth key*. A one-off key is fine,
   because the node identity is kept in the `tsstate` volume after first boot; after that the key
   is never read again.

   *Alternative: no key.* Leave `TS_AUTHKEY` empty and the container prints a sign-in link
   (`dc logs tailscale | grep login.tailscale.com`). The catch: the container waits only about
   **60 seconds** for you to approve it, then restarts with a **new** link, forever. Approve the
   newest link within that minute (be signed in to Tailscale first, so it is one click).
5. **Env file.** `cp .env.selfhost.example .env.selfhost` (git-ignored) and fill in:
   - `TS_AUTHKEY`: the key from step 4.
   - `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
   - `ALLOWED_HOSTS`: your Funnel hostname, plus `localhost,127.0.0.1`.
   - `POSTGRES_PASSWORD`: anything strong. The compose file builds `DATABASE_URL` from it.
   - `R2_*`: optional, but see *Media files* below.

## Start it

Abbreviate the command prefix as `dc = docker compose --env-file .env.selfhost -f docker-compose.selfhost.yml`.

```bash
docker compose --env-file .env.selfhost -f docker-compose.selfhost.yml up -d --build
docker compose --env-file .env.selfhost -f docker-compose.selfhost.yml exec app \
    python laserforce_simulator/manage.py createsuperuser
```

The app container runs `migrate` on every start (`entrypoint.sh`); the worker skips it. The app
is also reachable at <http://localhost:8000/> (the SPA, `/api/` and `/admin/`) on the PC itself,
bound to loopback only.

Then, once the node appears under *Machines* in the admin console:

- open its menu and choose **Disable key expiry**, or the URL stops working after 180 days;
- confirm Funnel is up: `dc exec tailscale tailscale funnel status` should print
  `https://<name>.<tailnet>.ts.net (Funnel on)`. If it prints `No serve config` and the logs say
  *"not able to issue TLS certs"*, HTTPS certificates are off for the tailnet (step 3): enable
  them, then `dc restart tailscale`. No new sign-in is needed.

## Inviting people

Registration is invite-only. Sign in to Django Admin (`/admin/`) as a superuser, open
`/admin/accounts/invite/` and **Add invite** (optionally lock it to the person's email). The
confirmation message shows the SPA's sign-up link,
`https://<name>.<tailnet>.ts.net/register?invite=<code>`, and the invite list keeps
it while it still works.

An invite is single-use by default. For a group, set **Max uses** (say 10) and an **Expires at**, and
share one link; it stops working once it is used up or expired. The invite list shows uses
(`3 / 10`) and status, and each invite's page lists who signed up with it. An email-locked invite
is always single-use.

## Day two

| Task | Command |
|---|---|
| Back up before an update | `dc exec -T db pg_dump -Fc -U laserforce laserforce > selfhost-<date>.dump` (restore with `pg_restore -c`) |
| Update to the latest `main` | `git pull` then `dc up -d --build` |
| Copy a local map's configs to the live site | Export on the PC: `python scripts/lfstats_import/export_map_fixture.py "<map name>" <zone size> map.json` (local database). Then `docker cp map.json <app container>:/tmp/map.json` and `dc exec app python laserforce_simulator/manage.py seed_demo /tmp/map.json`. The configs go on the live map of the same name if it has none yet (an uploaded map keeps its image); a missing map is created without an image. The committed `data/maps/syracuse.json` is already in the image |
| Make or refresh the demo Account | `dc exec app python laserforce_simulator/manage.py seed_demo data/maps/syracuse.json`: creates `demo@laserforce.demo` with two Teams on the first run, sets a new password every run and plays one more full-fidelity Round on the map; prints the email, password and Round id |
| First update after ANG-10 (nginx removed) | `git pull`, `dc up -d --build --remove-orphans` (drops the old nginx container), then `dc restart tailscale` (re-reads `serve.json`, now proxying to `app:8000`) |
| Logs | `dc logs -f app worker` |
| Funnel status / URL | `dc exec tailscale tailscale funnel status` |
| Back up the DB | `dc exec -T db pg_dump -U laserforce laserforce > backup.sql` |
| Restore a backup | `dc exec -T db psql -U laserforce laserforce < backup.sql` |
| Rename the URL | set `TS_HOSTNAME` and the matching `ALLOWED_HOSTS` entry in `.env.selfhost`, then `dc up -d`. The node keeps its identity, gets a new certificate, and the old name stops resolving |
| Stop (keeps data) | `dc down` |
| Wipe everything | `dc down -v` (deletes the database **and** the Tailscale node identity) |

## Caveats

- **The site is up only while the PC is.** Set Windows power settings to never sleep while plugged
  in. `restart: unless-stopped` brings every container back after a reboot, once Docker Desktop
  starts at sign-in.
- **Start from a fresh database.** Do not restore a pre-UX-01 Fly dump. See the
  `AUTH_USER_MODEL` hazard in the root `CLAUDE.md`.
- **Media files.** Uploaded map images live in the `media` volume, so they survive rebuilds.
  Without R2, Django serves them itself (`SERVE_MEDIA: "True"` in the compose file; with
  `DEBUG=False` nothing else serves `/media/`). That is fine at hobby traffic. To use R2 instead,
  set the `R2_*` variables (the same bucket as Fly works); `MEDIA_URL` then points at the bucket.
- **Funnel limits.** Funnel is meant for light traffic. It caps bandwidth and listens only on ports
  443, 8443 and 10000. That is fine for a hobby simulator, but it is not a production CDN.
- **TLS ends at Tailscale.** Gunicorn sees plain HTTP plus `X-Forwarded-Proto: https`. Django
  trusts that header through `SECURE_PROXY_SSL_HEADER` in `settings.py`; without it every login
  POST fails CSRF. Tests: `accounts/tests/test_https_proxy.py`.
- **Shell scripts must stay LF.** `.gitattributes` pins `*.sh` to LF; a CRLF `entrypoint.sh` makes
  the app container crash-loop with `exec /app/entrypoint.sh: no such file or directory`.
