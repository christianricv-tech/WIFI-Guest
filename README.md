# Aruba Voucher Backend

A FastAPI + PostgreSQL backend that issues single-use, time-limited guest WiFi
vouchers for an Aruba AP-505 (InstantOS 8.11) and validates them via FreeRADIUS.
It is paired with the GitHub Pages captive portal at
`https://christianricv-tech.github.io/WIFI-Guest/`, whose source lives in
`captive-portal/` in this repo and submits voucher codes to Aruba's
`swarm.cgi` authentication endpoint (see §7).

- **Admin side:** a server-rendered web UI (Jinja2 + JWT cookie) to generate,
  list, filter, and revoke vouchers, and to browse audit logs.
- **RADIUS side:** a JSON endpoint that FreeRADIUS calls (rlm_rest), plus a
  direct SQL view for the rlm_sql path.

---

## 1. Windows development setup

> Prerequisites: Python 3.11+, PostgreSQL 15+, and (optionally) WSL for
> FreeRADIUS. Install Python from python.org and PostgreSQL from
> postgresql.org/download/windows/.

### Step 1 — Get the code
```powershell
git clone <repo_url>
cd aruba-voucher-backend
```

### Step 2 — Create and activate a virtual environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Step 3 — Install dependencies
```powershell
pip install -r requirements.txt
```

### Step 4 — Create the PostgreSQL database
In pgAdmin or `psql`:
```sql
CREATE USER voucher_user WITH PASSWORD 'devpassword';
CREATE DATABASE voucher_db OWNER voucher_user;
GRANT ALL PRIVILEGES ON DATABASE voucher_db TO voucher_user;
```

### Step 5 — Configure the environment
```powershell
copy .env.example .env
notepad .env
```
Fill in every `[REQUIRED]` value. A development example:

```dotenv
DATABASE_URL=postgresql+asyncpg://voucher_user:devpassword@localhost:5432/voucher_db
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme
JWT_SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
RADIUS_SHARED_SECRET=some_shared_secret
APP_ENV=development
```

If any required variable is missing or empty, the application prints the list of
missing names and exits immediately.

### Step 6 — Run migrations
```powershell
alembic upgrade head
```
This creates `admins`, `vouchers`, `audit_logs`, and the `radius_voucher_view`.

### Step 7 — Start the development server
```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
On first startup the app seeds the admin account from `ADMIN_USERNAME` /
`ADMIN_PASSWORD` if no admin exists yet.

### Step 8 — Open the admin interface
Browse to http://localhost:8000/admin/login and sign in.

### Step 9 — FreeRADIUS on Windows
Use WSL: install FreeRADIUS inside WSL, copy the files from `freeradius/` into
`/etc/freeradius/3.0/`, and enable them (see §3). The FastAPI app is reachable
from WSL at `http://$(hostname -I | awk '{print $1}'):8000`.

Useful URLs: Swagger UI at http://localhost:8000/docs, ReDoc at
http://localhost:8000/redoc.

---

## 2. Linux VPS production setup

1. **Install packages**
   ```bash
   sudo apt update
   sudo apt install -y python3.11 python3.11-venv postgresql-15 nginx \
        freeradius freeradius-rest freeradius-postgresql
   ```
2. **Create the database** (same as Step 4, with a strong password).
3. **Create a system user and deploy directory**
   ```bash
   sudo useradd --system --no-create-home --shell /bin/false voucher
   sudo mkdir /opt/aruba-voucher-backend
   sudo chown voucher:voucher /opt/aruba-voucher-backend
   ```
4. **Deploy code, install deps, and run migrations** as the `voucher` user
   (Steps 1–6 above, with `APP_ENV=production` in `.env`).
5. **Create `/etc/systemd/system/aruba-voucher.service`**
   ```ini
   [Unit]
   Description=Aruba Voucher Backend
   After=network.target postgresql.service

   [Service]
   User=voucher
   WorkingDirectory=/opt/aruba-voucher-backend
   EnvironmentFile=/opt/aruba-voucher-backend/.env
   ExecStart=/opt/aruba-voucher-backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
6. **Configure nginx** from `nginx/voucher.conf` (TLS 1.2+, HTTP→HTTPS redirect).
7. **Obtain a certificate**
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d yourdomain.com
   ```
8. **Configure FreeRADIUS** (see §3).
9. **Enable and start everything**
   ```bash
   sudo systemctl enable --now aruba-voucher postgresql nginx freeradius
   ```

---

## 3. FreeRADIUS configuration guide

Two integration paths are supported. Prefer **rlm_rest** in production.

### rlm_rest (recommended) — atomic, no TOCTOU race

Sequence: AP → FreeRADIUS `authorize` → `POST /radius/validate` → FastAPI locks
the voucher row (`SELECT ... FOR UPDATE`), marks it consumed, and returns
`{"result": "accept"|"reject"}` → FreeRADIUS maps it to Access-Accept/Reject.

Enable:
```bash
sudo cp freeradius/mods-available/rest /etc/freeradius/3.0/mods-available/rest
sudo cp freeradius/sites-available/voucher /etc/freeradius/3.0/sites-available/voucher
sudo ln -s /etc/freeradius/3.0/mods-available/rest /etc/freeradius/3.0/mods-enabled/rest
sudo ln -s /etc/freeradius/3.0/sites-available/voucher /etc/freeradius/3.0/sites-enabled/voucher
sudo systemctl restart freeradius
```

> The `/radius/validate` endpoint **always** returns HTTP 200; the JSON `result`
> field drives the decision. A 5xx/timeout is treated as reject (fail-closed).

### rlm_sql — direct DB query (only if FreeRADIUS can't reach the API)

Sequence: FreeRADIUS queries `radius_voucher_view` to authorize (no status
update), then calls `POST /radius/post-auth` to mark the voucher consumed.

> **Caveat:** rlm_sql has a TOCTOU race between the view query and the post-auth
> update. Use rlm_rest when possible.

Enable:
```bash
sudo cp freeradius/sql/postgresql.conf /etc/freeradius/3.0/mods-available/sql
sudo ln -s /etc/freeradius/3.0/mods-available/sql /etc/freeradius/3.0/mods-enabled/sql
# edit /etc/freeradius/3.0/mods-available/sql and set the real DB credentials
sudo systemctl restart freeradius
```

### Aruba AP RADIUS client

Copy `freeradius/clients.conf`, replacing `<ap_ip_address>` and
`<shared_secret>` (the secret must match the AP profile and
`RADIUS_SHARED_SECRET`):
```bash
sudo cp freeradius/clients.conf /etc/freeradius/3.0/clients.conf   # or append
sudo systemctl restart freeradius
```

### Quick test from the CLI
```bash
radtest <voucher_code> <voucher_code> 127.0.0.1 0 <shared_secret>
```

---

## 4. Walled garden configuration

While a guest is unauthenticated, the Aruba AP must permit these destinations
(InstantOS 8.11: Configuration → Security → Walled Garden).

| Hostname / IP | Port(s) | Protocol | Reason needed |
|---|---|---|---|
| `<Voucher_API_Host>` | 80 | HTTP | Voucher validation endpoint (development) — Req 8.1 |
| `<Voucher_API_Host>` | 443 | HTTPS | Voucher validation endpoint (production) — Req 8.4 |
| `christianricv-tech.github.io` | 80, 443 | HTTP/HTTPS | Captive portal loading — Req 8.2 |
| `securelogin.arubanetworks.com` | 80, 443 | HTTP/HTTPS | Aruba swarm.cgi authentication endpoint — Req 8.3 |

Example Aruba CLI:
```
walled-garden host <Voucher_API_Host> 80
walled-garden host <Voucher_API_Host> 443
walled-garden host christianricv-tech.github.io 80
walled-garden host christianricv-tech.github.io 443
walled-garden host securelogin.arubanetworks.com 80
walled-garden host securelogin.arubanetworks.com 443
```

---

## 5. Testing

```powershell
pytest tests/unit/ tests/property/ -v
```
Unit tests run without external services. The property tests in
`tests/property/` validate the 21 correctness properties against a real
PostgreSQL database: they create an isolated `voucher_test` schema inside the
database referenced by `DATABASE_URL`, create the tables there, and truncate
them between examples. When PostgreSQL is unreachable the property tests skip
(via `pytest.skip`) instead of failing, so collection still works offline.
Set up the database (Step 4) before running them.

Integration tests under `tests/integration/` require a live PostgreSQL +
FreeRADIUS (docker-compose) and are marked `@pytest.mark.skip` by default.

---

## 6. Notes

- All timestamps are stored and compared in UTC.
- Vouchers have a one-way lifecycle: `unused → consumed` or `unused → revoked`.
- `/radius/validate` is not JWT-protected; it is intended to be called only by
  FreeRADIUS (bind to localhost/private network) and is rate-limited to
  60 requests/minute per IP.
- Admin sessions are stateless JWTs with an in-memory JTI blocklist and a
  60-minute idle timeout.

---

## 7. Captive portal

The guest-facing page lives in `captive-portal/index.html`. It is a static page
(no secrets, no backend keys) intended to be published to the GitHub Pages repo
`christianricv-tech/WIFI-Guest`.

When a guest submits a code, the page POSTs to Aruba's authentication endpoint
with the fields the controller expects:

```
POST https://securelogin.arubanetworks.com/swarm.cgi   (or https://<switchip>/swarm.cgi)
cmd=authenticate
user=<voucher_code>
password=<voucher_code>
```

The AP turns this into a RADIUS Access-Request where `User-Name` and
`User-Password` both equal the code, and FreeRADIUS validates it against the
backend (rlm_rest) or the database (rlm_sql). The page itself never validates a
voucher — see `design.md` for the full sequence.

To publish an update:

```powershell
# copy captive-portal/index.html to the WIFI-Guest repo, commit, and push
```

The portal's host (`christianricv-tech.github.io`) and the Aruba endpoint
(`securelogin.arubanetworks.com`) must both be in the AP's walled garden
while the guest is unauthenticated (§4).
