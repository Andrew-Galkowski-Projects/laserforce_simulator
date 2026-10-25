# Self-hosting on a home PC with Tailscale Funnel

The Fly.io deploy (`fly.toml`) is still the canonical deploy. This is the alternative: the whole
stack runs on one machine under Docker, and Tailscale Funnel publishes it at a free, stable
HTTPS URL. You need no domain, no router port-forwarding, and your home IP stays hidden.

```
browser --HTTPS--> Tailscale Funnel --tunnel--> tailscale container --HTTP--> nginx (:80)
                                                                              /app/ -> the Angular SPA
                                                                              else  -> app (gunicorn :8000)
                                                                              worker (Celery)
                                                                              db (Postgres 16), redis
```

nginx is the front door (ADR-0046): the Angular SPA is at `https://<name>.<tailnet>.ts.net/app/`,
and every other path (the Django pages, `/api/`, `/admin/`) is proxied to the app.

Files: `docker-compose.selfhost.yml`, `deploy/tailscale/serve.json`, `.env.selfhost.example`,
`frontend/Dockerfile` (the nginx image) and `frontend/nginx/default.conf.template`.

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

The app container runs `migrate` on every start (`entrypoint.sh`); the worker skips it. The site
is also reachable at <http://localhost:8000> on the PC itself, bound to loopback only (Django
directly, without nginx or the SPA).

Then, once the node appears under *Machines* in the admin console:

- open its menu and choose **Disable key expiry**, or the URL stops working after 180 days;
- confirm Funnel is up: `dc exec tailscale tailscale funnel status` should print
  `https://<name>.<tailnet>.ts.net (Funnel on)`. If it prints `No serve config` and the logs say
  *"not able to issue TLS certs"*, HTTPS certificates are off for the tailnet (step 3): enable
  them, then `dc restart tailscale`. No new sign-in is needed.

## Inviting people

Registration is invite-only. Sign in as a superuser, open `/admin/accounts/invite/` and **Add
invite** (optionally lock it to the person's email). The confirmation message shows the sign-up
link, `https://<name>.<tailnet>.ts.net/accounts/register/?invite=<code>`, and the invite list keeps
it while it still works.

An invite is single-use by default. For a group, set **Max uses** (say 10) and an **Expires at**, and
share one link; it stops working once it is used up or expired. The invite list shows uses
(`3 / 10`) and status, and each invite's page lists who signed up with it. An email-locked invite
is always single-use.

## Day two

| Task | Command |
|---|---|
| Update to the latest `main` | `git pull` then `dc up -d --build` |
| Logs | `dc logs -f app worker nginx` |
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
