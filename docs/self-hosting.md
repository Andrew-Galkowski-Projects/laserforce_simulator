# Self-hosting on a home PC with Tailscale Funnel

The Fly.io deploy (`fly.toml`) is still the canonical deploy. This is the alternative: the whole
stack runs on one machine under Docker, and Tailscale Funnel publishes it at a free, stable
HTTPS URL. You need no domain, no router port-forwarding, and your home IP stays hidden.

```
browser --HTTPS--> Tailscale Funnel --tunnel--> tailscale container --HTTP--> app (gunicorn :8000)
                                                                              worker (Celery)
                                                                              db (Postgres 16), redis
```

Files: `docker-compose.selfhost.yml`, `deploy/tailscale/serve.json`, `.env.selfhost.example`.

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
4. **Auth key.** *Settings > Keys > Generate auth key*. A one-off key is fine, because the node
   identity is kept in the `tsstate` volume after first boot. Once the node shows up under
   *Machines*, open its menu and choose **Disable key expiry**, or the URL stops working after
   180 days.
5. **Env file.** `cp .env.selfhost.example .env.selfhost` (git-ignored) and fill in:
   - `TS_AUTHKEY`: the key from step 4.
   - `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
   - `ALLOWED_HOSTS`: your Funnel hostname, plus `localhost,127.0.0.1`.
   - `POSTGRES_PASSWORD`: anything strong. The compose file builds `DATABASE_URL` from it.
   - `R2_*`: optional, but see *Media files* below.

## Start it

```bash
docker compose --env-file .env.selfhost -f docker-compose.selfhost.yml up -d --build
docker compose --env-file .env.selfhost -f docker-compose.selfhost.yml exec app \
    python laserforce_simulator/manage.py createsuperuser
```

The app container runs `migrate` on every start (`entrypoint.sh`); the worker skips it. The site
is also reachable at <http://localhost:8000> on the PC itself, bound to loopback only.

## Day two

Abbreviate the command prefix as `dc = docker compose --env-file .env.selfhost -f docker-compose.selfhost.yml`.

| Task | Command |
|---|---|
| Update to the latest `main` | `git pull` then `dc up -d --build` |
| Logs | `dc logs -f app worker` |
| Funnel status / URL | `dc exec tailscale tailscale funnel status` |
| Back up the DB | `dc exec -T db pg_dump -U laserforce laserforce > backup.sql` |
| Restore a backup | `dc exec -T db psql -U laserforce laserforce < backup.sql` |
| Stop (keeps data) | `dc down` |
| Wipe everything | `dc down -v` (deletes the database **and** the Tailscale node identity) |

## Caveats

- **The site is up only while the PC is.** Set Windows power settings to never sleep while plugged
  in. `restart: unless-stopped` brings every container back after a reboot, once Docker Desktop
  starts at sign-in.
- **Start from a fresh database.** Do not restore a pre-UX-01 Fly dump. See the
  `AUTH_USER_MODEL` hazard in the root `CLAUDE.md`.
- **Media files.** With `DEBUG=False`, Django does not serve `/media/`. Configure the `R2_*`
  variables (the same bucket as Fly works) if anything you use uploads files.
- **Funnel limits.** Funnel is meant for light traffic. It caps bandwidth and listens only on ports
  443, 8443 and 10000. That is fine for a hobby simulator, but it is not a production CDN.
- **TLS ends at Tailscale.** Gunicorn sees plain HTTP plus `X-Forwarded-Proto: https`. Django
  trusts that header through `SECURE_PROXY_SSL_HEADER` in `settings.py`; without it every login
  POST fails CSRF. Tests: `accounts/tests/test_https_proxy.py`.
- **Shell scripts must stay LF.** `.gitattributes` pins `*.sh` to LF; a CRLF `entrypoint.sh` makes
  the app container crash-loop with `exec /app/entrypoint.sh: no such file or directory`.
