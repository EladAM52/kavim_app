# Deploying Kavim

Target: `https://srv1515969.hstgr.cloud/kavim`, behind the host's existing nginx.

Everything runs in containers — Postgres, Redis, MinIO, FastAPI, the Celery worker, and beat. The
SPA is baked into the backend image, so the whole app is one origin and one port (SPEC §5.5).
Local development is unaffected: `infra/docker-compose.yml` and your existing `.env` keep working
exactly as before.

| | |
|---|---|
| Stack | [`infra/docker-compose.prod.yml`](../infra/docker-compose.prod.yml) |
| nginx | [`infra/nginx/kavim.conf`](../infra/nginx/kavim.conf) |
| Env template | [`infra/env.production.example`](../infra/env.production.example) |

---

## The subpath, and why it needs more than nginx

Serving under `/kavim` rather than at a host root touches four things. Three of them nginx can
handle by stripping the prefix; the fourth it cannot.

| Concern | Handled by |
|---|---|
| Asset URLs in `index.html` | `VITE_BASE_PATH` — a **build** argument, baked in by Vite |
| Client-side routes | React Router's `basename`, read from `import.meta.env.BASE_URL` |
| API and health calls | Derived from the same `BASE_URL` in `lib/basePath.ts` |
| **Refresh cookie `path`** | **`APP_PUBLIC_PATH`** — nothing else can fix this |

The cookie is the one to understand. nginx strips `/kavim` before proxying, so the application
routes at `/` and never learns where it is mounted. But the *browser* matches a cookie's `path`
against the address bar, which still says `/kavim/...`. Set the cookie at `/api/v1/auth` and the
browser stores it and never sends it back: **login succeeds, and the next page load signs the user
out.** It looks like a token bug and is not one. `APP_PUBLIC_PATH=/kavim` makes the cookie path
`/kavim/api/v1/auth`, and `core/config.py` refuses to start on a malformed value.

Because `VITE_BASE_PATH` is a build argument, an image built for the root **cannot** be moved to a
subpath by changing an environment variable. Changing the mount point means rebuilding.

> Building by hand on Windows: Git Bash rewrites a value that looks like a Unix path, so
> `VITE_BASE_PATH=/kavim/ npm run build` silently produces
> `src="/Users/…/Git/kavim/assets/…"`. Use PowerShell, or prefix the command with
> `MSYS_NO_PATHCONV=1`. The server builds on Linux and is unaffected.

---

## First deploy

### 1. Get the code and the environment onto the server

```bash
ssh <you>@srv1515969.hstgr.cloud
sudo mkdir -p /opt/kavim && sudo chown "$USER" /opt/kavim
git clone https://github.com/EladAM52/kavim_app.git /opt/kavim
cd /opt/kavim

cp infra/env.production.example .env
chmod 600 .env
```

Now edit `.env` and replace every `CHANGE_ME`. Generate the secrets **on the server** — a secret
that has been pasted into a chat, an issue, or a recorded terminal is a leaked secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # STORAGE_ACCESS_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # STORAGE_SECRET_KEY
```

`POSTGRES_PASSWORD` appears twice — once on its own and once inside `DATABASE_URL`. They must
match.

`SMTP_PASSWORD` is the Gmail App Password. It is the same credential the development machine uses;
if it is ever rotated, both copies change.

### 2. Build and start

```bash
docker compose -f infra/docker-compose.prod.yml --env-file .env up -d --build
```

The build compiles the SPA with `VITE_BASE_PATH=/kavim/` and copies it into the backend image, so
the first build is slow and later ones are cached.

**`--env-file .env` is required on every compose command here**, and it is easy to lose. Compose
resolves `${VAR}` from a `.env` in the *project directory*, which defaults to the folder holding
the compose file — `infra/`, not the repo root. Without the flag every variable is empty and the
`:?` guards stop the run with *"required variable POSTGRES_PASSWORD is missing a value"*. That is
the guards working: the alternative is Postgres silently initialising with a blank password.

`env_file: ../.env` inside the compose file is a *different* mechanism — it passes variables into
the containers, and its path is relative to the compose file. Both end up at the same repo-root
`.env`.

### 3. Migrate, then seed

```bash
docker compose -f infra/docker-compose.prod.yml --env-file .env run --rm migrate
```

Migrations are a deliberate one-shot command, not part of `up`. A container that restarts at 3am
must never rewrite the schema on its own.

The full seed creates a demo board and **seven accounts with a published password**, which has no
business on a server real users can reach. `seed.py` already refuses it — `--reference` is
mandatory when `APP_ENV=production`, and a bare `seed` exits with *"refusing to seed demo data in
production"*. Reference data is the permissions, the roles, and the seeded role matrix, which the
system genuinely cannot run without.

```bash
docker compose -f infra/docker-compose.prod.yml --env-file .env run --rm backend \
  python -m app.scripts.seed --reference
```

### 3b. The first administrator

A fresh installation has reference data and **no users**, and every normal way in needs somebody
who is already inside: `POST /admin/invitations` needs a bearer token, and `app.scripts.invite`
refuses to run in production on purpose — a shell that can mint invitations defeats the point of
an invitation, which is that it proves a manager sent it. It also needs an existing admin to
record as the inviter.

So there is exactly one bootstrap door:

```bash
docker compose -f infra/docker-compose.prod.yml --env-file .env run --rm backend \
  python -m app.scripts.bootstrap_admin elad.amir@audiocodes.com "Elad Amir"
```

It prompts for the password twice and never takes it as an argument — arguments land in shell
history and in `ps`. `run --rm` allocates a TTY, so the prompt works.

**It disables itself.** The guard is the state of the database, not `APP_ENV`: once any active
user holds `user:manage_permissions`, it refuses. Run it a second time and it says so. Everyone
after the first person arrives through the invitation flow, which records who invited them.

From here, sign in and invite the rest from **ניהול → הזמנות**. Those invitations are emailed
through the outbox, so beat and the worker must be up — they are, from step 2 — and each link is
valid for `INVITATION_TTL_DAYS` (7).

### 4. Wire up nginx

```bash
sudo mkdir -p /etc/nginx/snippets
sudo cp infra/nginx/kavim.conf /etc/nginx/snippets/kavim.conf
```

If `BACKEND_PORT` in `.env` is not 8000 — because something else on the box already held it — the
snippet has to follow, or every request is a 502 against a port nothing is listening on:

```bash
sudo sed -i 's/127.0.0.1:8000/127.0.0.1:9000/g' /etc/nginx/snippets/kavim.conf
```

Add to the `http { }` block, once per install:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

And inside the `server { }` block for `srv1515969.hstgr.cloud`:

```nginx
include /etc/nginx/snippets/kavim.conf;
```

Then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Verify, in this order

```bash
# 1. The stack is healthy
docker compose -f infra/docker-compose.prod.yml --env-file .env ps

# 2. The API answers locally, before nginx is in the picture
curl -s localhost:8000/health/ready

# 3. And through nginx, at the public URL
curl -s https://srv1515969.hstgr.cloud/kavim/health/ready
```

Then in a browser, and this is the part worth doing carefully:

1. Open `https://srv1515969.hstgr.cloud/kavim` — the login screen renders in Hebrew, RTL.
2. Sign in.
3. **Reload the page.** Still signed in? Then the cookie path is right. Bounced to login? →
   `APP_PUBLIC_PATH` is wrong or the image was built with the wrong `VITE_BASE_PATH`.
4. Open devtools → Application → Cookies. `kavim_refresh` must show `Path=/kavim/api/v1/auth`,
   `HttpOnly`, `Secure`, `SameSite=Strict`.
5. Invite yourself from **ניהול → הזמנות** and confirm the emailed link points at
   `https://srv1515969.hstgr.cloud/kavim/invite/...` — that comes from `APP_BASE_URL`.

---

## Updating

```bash
cd /opt/kavim
git pull
docker compose -f infra/docker-compose.prod.yml --env-file .env up -d --build
docker compose -f infra/docker-compose.prod.yml --env-file .env run --rm migrate
```

Rebuild is required for a frontend change even when nothing in the backend moved: the SPA lives
inside the backend image.

## Backups

Nothing here backs anything up yet. The two volumes that matter are `pgdata` (every quality record
and the audit log, which has a 24-month retention requirement) and `miniodata` (attachments, from
Phase 6).

```bash
# Postgres, to a file on the host
docker compose -f infra/docker-compose.prod.yml --env-file .env exec -T db \
  pg_dump -U kavim kavim | gzip > "kavim-$(date +%F).sql.gz"
```

Put that on a schedule and copy it off the box. A backup that lives only on the machine it is
backing up is not a backup.

## Known gaps in this deployment

| Gap | Consequence |
|---|---|
| No automated backups | The `pg_dump` above is manual. Nothing is scheduled and nothing leaves the host |
| No CI/CD | Deploys are `git pull` + rebuild, by hand, over SSH |
| MinIO is single-node | No replication. It satisfies the durability guard and survives container rebuilds, not disk loss |
| `/health/ready` is public | Reachable through the catch-all nginx rule; it names which dependency is down. Restricting it is a commented block in `kavim.conf` |
| Log rotation is Docker's default | `json-file` grows unbounded on a busy day. Set `max-size`/`max-file` in the daemon config |
| No staging environment | This is the first non-development deployment; a bad release is noticed in production |
| Secrets live in one `.env` | Adequate for a single-host pilot. A secrets manager is the answer if this outlives the pilot |
