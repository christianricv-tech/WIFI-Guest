# Design Document: Aruba Voucher Backend

## Overview

The Aruba Voucher Backend is a guest WiFi access management system that provides time-limited, single-use voucher codes for authenticating guests onto an Aruba AP-505 wireless network. The backend integrates with FreeRADIUS as the RADIUS server and a GitHub Pages captive portal whose source lives in `captive-portal/` in this repo. The portal is a static page that hands the voucher code to Aruba's `swarm.cgi` endpoint; it never validates a code or holds secrets itself.

### The Big Picture (Beginner-Friendly)

When a guest connects to the WiFi, their device is intercepted by the Aruba AP and redirected to a webpage (the captive portal on GitHub Pages). The guest types in a voucher code that an admin generated beforehand. The portal submits that code to Aruba's authentication endpoint (`securelogin.arubanetworks.com/swarm.cgi`), which causes the AP to send a RADIUS request to FreeRADIUS. FreeRADIUS then asks our FastAPI backend (or queries PostgreSQL directly) whether the code is valid. If it is, the guest gets internet access. If not, they are rejected.

This backend is responsible for:
- Generating and storing voucher codes (the admin-facing side)
- Validating those codes when FreeRADIUS asks (the RADIUS-facing side)
- Providing an admin web interface for managing vouchers
- Logging every authentication attempt for auditing

---

## Architecture

### System Architecture Diagram

```mermaid
graph TD
    Guest["Guest Device"]
    AP["Aruba AP-505\n(InstantOS 8.11)"]
    Portal["Captive Portal\n(GitHub Pages)\nfrom captive-portal/"]
    Aruba["securelogin.arubanetworks.com\n/swarm.cgi"]
    FreeRADIUS["FreeRADIUS Server"]
    FastAPI["FastAPI Application\n(Voucher_API)"]
    PostgreSQL["PostgreSQL Database"]
    Admin["Admin Browser"]

    Guest -->|"1. Connect to SSID"| AP
    AP -->|"2. HTTP 302 redirect with\nmac, essid, ip, apname,\napmac, url, switchip"| Portal
    Portal -->|"3. Guest enters voucher code\nPOST cmd=authenticate\nuser=code, password=code"| Aruba
    Aruba -->|"4. RADIUS Access-Request\nUser-Name=code\nUser-Password=code"| FreeRADIUS
    FreeRADIUS -->|"5a. rlm_rest: POST /radius/validate"| FastAPI
    FreeRADIUS -->|"5b. rlm_sql: SELECT from view"| PostgreSQL
    FastAPI -->|"6. SELECT FOR UPDATE / UPDATE"| PostgreSQL
    FastAPI -->|"7. accept or reject"| FreeRADIUS
    FreeRADIUS -->|"8. Access-Accept or Access-Reject"| AP
    AP -->|"9. Grant or deny internet"| Guest
    Admin -->|"HTTPS"| FastAPI
    FastAPI -->|"ORM / Alembic"| PostgreSQL

    style Portal fill:#f9f,stroke:#333
    style Aruba fill:#faa,stroke:#333
```

### Component Relationships

| Component | Technology | Purpose |
|---|---|---|
| Aruba AP-505 | InstantOS 8.11 | Intercepts guests, issues redirect, sends RADIUS requests |
| Captive Portal | GitHub Pages (static) | Guest-facing voucher entry form; source in `captive-portal/`, posts to Aruba `swarm.cgi` |
| FreeRADIUS | FreeRADIUS 3.x | RADIUS server; validates credentials via rlm_rest or rlm_sql |
| FastAPI Application | Python 3.11+, FastAPI | REST API for voucher lifecycle and admin UI |
| PostgreSQL | PostgreSQL 15+ | Persistent store for vouchers, audit logs, admin accounts |

---

## Components and Interfaces

### 1. FastAPI Application (Voucher_API)

The core backend service. It has three distinct interface surfaces:

**Admin Interface** — HTML pages served by FastAPI (Jinja2 templates) protected by JWT session auth. Lets admins generate, view, search, and revoke vouchers, and view audit logs.

**RADIUS Validation Endpoint** — A lightweight HTTP POST endpoint at `/radius/validate` that FreeRADIUS calls (rlm_rest path). Performs atomic voucher validation and status update. Returns `{"result": "accept"}` or `{"result": "reject"}`.

**RADIUS Post-Auth Endpoint** — Used only in the rlm_sql path. After FreeRADIUS grants access (the DB view determined the voucher is valid), FreeRADIUS calls this endpoint to mark the voucher as consumed.

### 2. PostgreSQL Database

Stores all persistent state. The rlm_sql integration path requires a specific view that FreeRADIUS can query directly. All schema changes are managed via Alembic migrations.

### 3. FreeRADIUS

Acts as the RADIUS server that receives authentication requests from the AP. Two integration paths are supported (configured separately; only one is active at a time):

- **rlm_rest**: FreeRADIUS calls the FastAPI validation endpoint. FastAPI handles all logic atomically.
- **rlm_sql**: FreeRADIUS queries the PostgreSQL view directly for authorization, then calls the FastAPI post-auth endpoint to mark consumed.

### 4. Admin Browser Client

A standard web browser. Communicates with FastAPI over HTTPS in production (HTTP in development mode). Uses HTML forms, server-rendered pages, and light JavaScript only for clipboard copy and download.

---

## Data Models

> **Beginner note:** These are the PostgreSQL table schemas — the structure of the data stored in the database. Each table has columns (like spreadsheet columns), data types, and constraints. Indexes speed up queries.

### Table: `vouchers`

```sql
CREATE TABLE vouchers (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(12)     NOT NULL UNIQUE,
    status          VARCHAR(12)     NOT NULL DEFAULT 'unused'
                                    CHECK (status IN ('unused', 'consumed', 'revoked')),
    activation_mode VARCHAR(15)     NOT NULL DEFAULT 'use-activated'
                                    CHECK (activation_mode IN ('use-activated', 'pre-activated')),
    duration_minutes INTEGER        NOT NULL
                                    CHECK (duration_minutes >= 1 AND duration_minutes <= 525600),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,        -- NULL for use-activated until first use
    consumed_at     TIMESTAMPTZ,        -- NULL until first successful auth
    revoked_at      TIMESTAMPTZ,        -- NULL until revoked
    created_by      UUID            REFERENCES admins(id) ON DELETE SET NULL
);

-- Indexes for common query patterns
CREATE INDEX idx_vouchers_code        ON vouchers (code);
CREATE INDEX idx_vouchers_status      ON vouchers (status);
CREATE INDEX idx_vouchers_created_at  ON vouchers (created_at DESC);
CREATE INDEX idx_vouchers_expires_at  ON vouchers (expires_at);
```

**Column notes:**
- `code` — The 12-character voucher code. Always unique. Uppercase A-Z (no I/O) and digits 2-9.
- `status` — The lifecycle state. Starts as `unused`. One-way transitions: `unused → consumed` or `unused → revoked`.
- `activation_mode` — Controls when the expiry clock starts.
- `duration_minutes` — Stored as whole minutes. 1h = 60, 4h = 240, 24h = 1440, 7d = 10080, 30d = 43200. Maximum 365 days = 525600.
- `expires_at` — Set at creation for pre-activated; set at first auth for use-activated; `NULL` for unused use-activated vouchers.
- `consumed_at` — Timestamp of first (and only) successful authentication.
- `revoked_at` — Timestamp of admin revocation.

### Table: `admins`

```sql
CREATE TABLE admins (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(64)     NOT NULL UNIQUE,
    password_hash   VARCHAR(128)    NOT NULL,    -- bcrypt hash, cost >= 10
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

CREATE INDEX idx_admins_username ON admins (username);
```

**Column notes:**
- `password_hash` — Only bcrypt hashes are ever stored here. Plaintext passwords never touch the database.
- `last_login_at` — Updated on every successful login for auditing purposes.

### Table: `audit_logs`

```sql
CREATE TABLE audit_logs (
    id              BIGSERIAL       PRIMARY KEY,
    logged_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    voucher_code    VARCHAR(12)     NOT NULL,    -- stored even if code doesn't exist in vouchers table
    client_mac      VARCHAR(17),                 -- format: AA:BB:CC:DD:EE:FF (nullable — not always available)
    outcome         VARCHAR(8)      NOT NULL
                                    CHECK (outcome IN ('accepted', 'rejected')),
    rejection_reason VARCHAR(32),               -- NULL when accepted; e.g. 'not_found', 'consumed', 'revoked', 'expired'
    source          VARCHAR(8)      NOT NULL DEFAULT 'rlm_rest'
                                    CHECK (source IN ('rlm_rest', 'rlm_sql'))
);

CREATE INDEX idx_audit_logs_logged_at      ON audit_logs (logged_at DESC);
CREATE INDEX idx_audit_logs_voucher_code   ON audit_logs (voucher_code);
CREATE INDEX idx_audit_logs_client_mac     ON audit_logs (client_mac);
CREATE INDEX idx_audit_logs_outcome        ON audit_logs (outcome);
```

**Column notes:**
- `voucher_code` — Always recorded as submitted, even if the code doesn't exist in the `vouchers` table.
- `client_mac` — Passed by FreeRADIUS from the RADIUS `Calling-Station-Id` attribute. May be absent in some configurations.
- `rejection_reason` — One of: `not_found`, `consumed`, `revoked`, `expired`, `invalid_state`. `NULL` when outcome is `accepted`.
- `source` — Distinguishes rlm_rest-driven logs (created by FastAPI) from rlm_sql-driven logs (created by post-auth callback).

### View: `radius_voucher_view` (rlm_sql integration)

```sql
CREATE OR REPLACE VIEW radius_voucher_view AS
SELECT
    code            AS username,
    code            AS password,       -- voucher code serves as both username and password
    CASE
        WHEN status = 'unused'
             AND (
                 (activation_mode = 'pre-activated' AND expires_at > NOW())
                 OR
                 (activation_mode = 'use-activated' AND expires_at IS NULL)
                 OR
                 (activation_mode = 'use-activated' AND expires_at > NOW())
             )
        THEN 1
        ELSE 0
    END             AS is_valid
FROM vouchers;
```

> **Beginner note:** This view is what FreeRADIUS queries directly when using the rlm_sql path. FreeRADIUS will do something like `SELECT password FROM radius_voucher_view WHERE username = '<code>'` and compare it against the submitted password. The `is_valid` column is used by a custom authorize query to determine Accept or Reject.

### Admin Sessions (JWT, not a DB table)

Sessions are managed as stateless JWTs. No session table is needed. The JWT payload includes:

```json
{
  "sub": "<admin_uuid>",
  "username": "<admin_username>",
  "iat": 1700000000,
  "exp": 1700003600,
  "jti": "<uuid4>"          
}
```

The `jti` (JWT ID) claim enables session invalidation by maintaining a small blocklist of invalidated JTI values in memory or in a Redis-like store. On logout or 60-minute idle timeout, the JTI is blocklisted.

---

## API Design

> **Beginner note:** FastAPI automatically generates interactive API documentation at `/docs` (Swagger UI) and `/redoc`. You can test every endpoint from the browser without needing a separate tool.

All request/response bodies use JSON unless noted. Date-time values are ISO 8601 UTC strings (e.g., `2024-01-15T10:30:00Z`).

### Authentication

Admin endpoints require a `Bearer <token>` JWT in the `Authorization` header (API endpoints) or an `access_token` cookie (browser/UI endpoints).

---

### Admin Auth Endpoints

#### `POST /admin/login`
Login and receive a JWT.

**Request:**
```json
{ "username": "admin", "password": "secret123" }
```
**Response 200:**
```json
{ "access_token": "<jwt>", "token_type": "bearer", "expires_in": 3600 }
```
**Response 401:**
```json
{ "detail": "Invalid credentials" }
```
No distinction between wrong username vs wrong password (per Req 6.3).

#### `POST /admin/logout`
Invalidate the current session. Requires auth.

**Response 200:**
```json
{ "detail": "Logged out successfully" }
```

---

### Voucher Management Endpoints

All require admin JWT auth.

#### `POST /admin/vouchers`
Generate one or more vouchers.

**Request:**
```json
{
  "quantity": 10,
  "duration_minutes": 480,
  "activation_mode": "use-activated"
}
```
- `quantity`: integer 1–500 (default: 1)
- `duration_minutes`: integer 1–43200, OR use `duration_preset` (one of `"1h"`, `"4h"`, `"8h"`, `"24h"`, `"7d"`, `"30d"`)
- `activation_mode`: `"use-activated"` (default) or `"pre-activated"`

**Response 201:**
```json
{
  "generated": 10,
  "vouchers": [
    {
      "code": "AB3CD5EF7GH8",
      "status": "unused",
      "activation_mode": "use-activated",
      "duration_minutes": 480,
      "expires_at": null,
      "created_at": "2024-01-15T10:00:00Z"
    }
  ]
}
```
**Response 400:** Quantity or duration out of range, or invalid activation_mode.
**Response 422:** Malformed request body (FastAPI validation).
**Response 500:** Code collision after 5 retries.

#### `GET /admin/vouchers`
Paginated list of vouchers.

**Query parameters:**
- `page` (int, default 1)
- `status` (optional: `unused`, `consumed`, `revoked`, `expired`)

**Response 200:**
```json
{
  "page": 1,
  "page_size": 25,
  "total": 142,
  "items": [
    {
      "code": "AB3CD5EF7GH8",
      "status": "unused",
      "activation_mode": "use-activated",
      "duration_minutes": 480,
      "expires_at": null,
      "created_at": "2024-01-15T10:00:00Z",
      "consumed_at": null,
      "revoked_at": null
    }
  ]
}
```

> **Note on `expired` status:** The `vouchers` table has no `expired` status column. Expired vouchers are those with `status = 'unused'` and `expires_at < NOW()`. The API computes this dynamically when filtering by `expired`.

#### `GET /admin/vouchers/{code}`
Retrieve a single voucher.

**Response 200:** Single voucher object (same shape as list item).
**Response 404:** `{ "detail": "Voucher not found" }`

#### `DELETE /admin/vouchers/{code}/revoke`
Revoke a voucher.

**Response 200:**
```json
{
  "code": "AB3CD5EF7GH8",
  "status": "revoked",
  "revoked_at": "2024-01-15T12:00:00Z"
}
```
**Response 404:** Voucher not found.
**Response 409:**
```json
{ "detail": "Voucher cannot be revoked: current status is 'consumed'" }
```

---

### RADIUS Validation Endpoints

#### `POST /radius/validate`
Called by FreeRADIUS rlm_rest. **Not authenticated by JWT** — protected by network-level controls (FreeRADIUS is the only caller; bind to localhost or a private interface, or use a shared secret header).

**Request (sent by rlm_rest as JSON):**
```json
{
  "username": "AB3CD5EF7GH8",
  "password": "AB3CD5EF7GH8"
}
```

**Response 200 (accept):**
```json
{ "result": "accept" }
```

**Response 200 (reject):**
```json
{ "result": "reject", "reason": "consumed" }
```

Possible `reason` values: `not_found`, `consumed`, `revoked`, `expired`, `invalid`.

> **Validation logic (atomic):** This endpoint opens a database transaction and issues `SELECT ... FOR UPDATE` on the voucher row identified by `username`. If the row is valid (status=unused, not expired), it immediately updates `status='consumed'`, sets `consumed_at`, sets `expires_at` (for use-activated vouchers), and commits — all in one transaction. This prevents two concurrent requests from both seeing `status='unused'`.

**Rate limiting:** 60 requests per 60 seconds per source IP. Returns HTTP 429 if exceeded.

#### `POST /radius/post-auth`
Called by FreeRADIUS rlm_sql post-auth handler after a successful Access-Accept. Marks the voucher as consumed.

**Request:**
```json
{
  "username": "AB3CD5EF7GH8",
  "client_mac": "AA:BB:CC:DD:EE:FF"
}
```

**Response 200:**
```json
{ "detail": "Voucher marked as consumed" }
```
**Response 404:** Voucher not found.
**Response 409:** Voucher already consumed (idempotent — returns 200 if already consumed with matching timestamp).

---

### Audit Log Endpoints

All require admin JWT auth.

#### `GET /admin/audit-logs`
Paginated, filterable audit log.

**Query parameters:**
- `page` (int, default 1)
- `voucher_code` (string, exact match)
- `client_mac` (string, exact match)
- `outcome` (`accepted` or `rejected`)
- `date_from` (ISO 8601 date, inclusive)
- `date_to` (ISO 8601 date, inclusive)

**Response 200:**
```json
{
  "page": 1,
  "page_size": 50,
  "total": 312,
  "items": [
    {
      "id": 42,
      "logged_at": "2024-01-15T10:30:00Z",
      "voucher_code": "AB3CD5EF7GH8",
      "client_mac": "AA:BB:CC:DD:EE:FF",
      "outcome": "accepted",
      "rejection_reason": null
    }
  ]
}
```

---

### Admin UI Routes (HTML, served by Jinja2)

These routes serve HTML pages (not JSON). They are protected by cookie-based JWT.

| Route | Page |
|---|---|
| `GET /` | Redirect to `/admin/dashboard` |
| `GET /admin/login` | Login form |
| `GET /admin/dashboard` | Overview / voucher list |
| `GET /admin/vouchers/new` | Voucher generation form |
| `GET /admin/audit-logs` | Audit log viewer |

All unauthenticated requests to these routes redirect to `GET /admin/login`.

---

## FreeRADIUS Integration Design

> **Beginner note:** FreeRADIUS configuration lives in `/etc/freeradius/3.0/` on Linux. On Windows for development, you may use WSL or the Windows FreeRADIUS port. The key directory is `raddb/`. Modules are enabled by creating symlinks in `mods-enabled/`.

### rlm_rest Integration Path

This is the simpler and more robust path for most deployments. FreeRADIUS calls the FastAPI `/radius/validate` endpoint.

#### How it works

1. Guest submits voucher code → AP sends RADIUS Access-Request to FreeRADIUS.
2. FreeRADIUS `authorize` section calls rlm_rest → HTTP POST to `http://localhost:8000/radius/validate`.
3. FastAPI performs atomic validation, writes audit log, returns `{"result": "accept"}` or `{"result": "reject"}`.
4. rlm_rest maps HTTP 200 + body → RADIUS Accept/Reject.
5. FreeRADIUS returns Access-Accept or Access-Reject to the AP.

#### FreeRADIUS rlm_rest module configuration

File: `/etc/freeradius/3.0/mods-available/rest`

```
rest {
    connect_uri = "http://127.0.0.1:8000"
    connect_timeout = 5.0

    authorize {
        uri = "${..connect_uri}/radius/validate"
        method = "post"
        body = "json"
        data = '{"username": "%{User-Name}", "password": "%{User-Password}", "client_mac": "%{Calling-Station-Id}"}'
        timeout = 5.0
        force_to = "json"
    }

    pool {
        start = 5
        min = 4
        max = 10
        spare = 3
        uses = 0
        retry_delay = 30
        lifetime = 0
        idle_timeout = 60
    }
}
```

Enable: `ln -s /etc/freeradius/3.0/mods-available/rest /etc/freeradius/3.0/mods-enabled/rest`

#### FreeRADIUS site configuration (virtual server)

File: `/etc/freeradius/3.0/sites-available/voucher`

```
server voucher {
    listen {
        type = auth
        ipaddr = *
        port = 1812
    }

    authorize {
        # Call our REST API to validate the voucher
        rest

        # rlm_rest returns ok (HTTP 200 + JSON body processed)
        # or reject (HTTP 401/403/404) or fail (HTTP 5xx)
        if (ok || updated) {
            # The REST response controls the outcome
            update control {
                Auth-Type := Accept
            }
        }
        elsif (notfound || reject) {
            reject
        }
        else {
            # Backend unreachable / 5xx → fail-closed
            reject
        }
    }

    authenticate {
        Auth-Type Accept {
            ok
        }
    }
}
```

Enable: `ln -s /etc/freeradius/3.0/sites-available/voucher /etc/freeradius/3.0/sites-enabled/voucher`

#### FastAPI response → FreeRADIUS outcome mapping

| FastAPI response | HTTP status | rlm_rest module result | FreeRADIUS action |
|---|---|---|---|
| `{"result": "accept"}` | 200 | `ok`/`updated` | Access-Accept |
| `{"result": "reject", ...}` | 200 | `ok` (body sets Auth-Type Reject via policy) | Access-Reject |
| Timeout / 5xx | — | `fail` | Reject (fail-closed) |
| Unreachable | — | `fail` | Reject (fail-closed) |

> **Important design decision:** We return HTTP 200 for both accept and reject responses (rather than 200/401). This is because rlm_rest maps HTTP status codes to module codes, and a 401 response causes FreeRADIUS to return `reject` — but without the body being processed. By always returning 200 with a JSON body, we ensure the full response is processed and the `result` field drives the decision via the authorize policy.

---

### rlm_sql Integration Path

FreeRADIUS queries the PostgreSQL `radius_voucher_view` directly to authorize, then calls the FastAPI post-auth endpoint to mark the voucher consumed.

#### How it works

1. AP sends RADIUS Access-Request to FreeRADIUS.
2. FreeRADIUS `authorize` section calls rlm_sql → queries `radius_voucher_view` for the code.
3. If `is_valid = 1`, rlm_sql sets `Auth-Type := SQL` and FreeRADIUS proceeds to authenticate.
4. rlm_sql `authenticate` section does a password check (code == code).
5. FreeRADIUS `post-auth` section calls FastAPI `POST /radius/post-auth` to mark the voucher consumed and write the audit log.
6. FreeRADIUS returns Access-Accept or Access-Reject to AP.

> **Caveat:** The rlm_sql path has a TOCTOU (Time-of-Check to Time-of-Use) race condition between the view query and the post-auth update. The rlm_rest path with `SELECT ... FOR UPDATE` is strongly preferred for production. Use rlm_sql only if FreeRADIUS cannot reach the FastAPI HTTP endpoint.

#### FreeRADIUS rlm_sql module configuration

File: `/etc/freeradius/3.0/mods-available/sql`

```
sql {
    dialect = "postgresql"
    driver = "rlm_sql_postgresql"

    server = "localhost"
    port = 5432
    login = "radius_user"
    password = "radius_password"
    radius_db = "voucher_db"

    # Authorization query — reads from the view
    authorize_check_query = "\
        SELECT password AS value, '==' AS op \
        FROM radius_voucher_view \
        WHERE username = '%{SQL-User-Name}' AND is_valid = 1 \
        LIMIT 1"

    # No accounting tables needed
    accounting = no

    pool {
        start = 5
        min = 4
        max = 10
        spare = 3
        idle_timeout = 60
    }
}
```

#### FreeRADIUS post-auth for rlm_sql

In the site virtual server `post-auth` section, add a `rest` call to mark consumed:

```
post-auth {
    if (ok || updated) {
        rest.post-auth  # calls POST /radius/post-auth
    }
}
```

With the rest module configured to have a `post-auth` section:

```
post-auth {
    uri = "${..connect_uri}/radius/post-auth"
    method = "post"
    body = "json"
    data = '{"username": "%{User-Name}", "client_mac": "%{Calling-Station-Id}"}'
    timeout = 5.0
}
```

---

### Aruba AP RADIUS Client Configuration

In Aruba InstantOS 8.11 web UI or CLI:

```
# CLI equivalent
wlan auth-server FreeRADIUS
  ip <freeradius_server_ip>
  port 1812
  key <shared_secret>
  timeout 10
  retry-count 3
```

The `<shared_secret>` must match the FreeRADIUS `clients.conf` entry:

```
client aruba_ap {
    ipaddr  = <ap_ip_address>
    secret  = <shared_secret>
    shortname = aruba-ap-505
}
```

---

## Security Design

### Rate Limiting

The `/radius/validate` endpoint is rate-limited to prevent brute-force attacks:

- **Library:** `slowapi` (built on `limits`, designed for FastAPI/Starlette)
- **Strategy:** Fixed window — 60 requests per 60-second window per source IP
- **Key:** `request.client.host` (the IP address of the caller)
- **Response:** HTTP 429 with `Retry-After` header

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/radius/validate")
@limiter.limit("60/minute")
async def radius_validate(request: Request, body: RadiusValidateRequest, db: AsyncSession = Depends(get_db)):
    ...
```

> **Beginner note:** FreeRADIUS runs on the same server (or a trusted internal network), so its IP will rarely trigger rate limiting in practice. This protection is mainly against someone trying to brute-force voucher codes by hitting the endpoint directly.

### JWT Structure and Session Management

**Library:** `python-jose` for JWT encoding/decoding, `passlib[bcrypt]` for password hashing.

**JWT claims:**
```json
{
  "sub": "550e8400-e29b-41d4-a716-446655440000",
  "username": "admin",
  "iat": 1700000000,
  "exp": 1700003600,
  "jti": "a4f3d2c1-..."
}
```

**Session lifetime:** 3600 seconds (60 minutes). The expiry is a hard deadline; there is no sliding window — activity resets the idle timer but the JWT itself expires after 60 minutes. On each authenticated request, the server checks the `iat` claim against the last-activity time tracked in memory (or a small DB table) to enforce the idle timeout.

**Idle timeout implementation:** A `last_seen` dictionary maps `jti → timestamp` in memory. On each request, the difference between `now()` and `last_seen[jti]` is checked. If it exceeds 3600 seconds, the request is rejected even if the JWT has not expired yet.

**Logout / invalidation:** On logout, the `jti` is added to a blocklist set stored in memory. The blocklist is checked on every authenticated request. This set is bounded in size by the maximum number of concurrent sessions.

**Persistent sessions across restarts:** The in-memory blocklist and last-seen map are lost on restart. For production, these should be persisted in a dedicated table (`admin_sessions`) or Redis. The current design uses in-memory for simplicity; a migration to a `admin_sessions` table is straightforward.

### Password Hashing

```python
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
```

- Algorithm: bcrypt
- Cost factor: 12 (exceeds the minimum of 10 required by Req 11.2)
- Verification: `pwd_context.verify(plaintext, stored_hash)`
- Storage: only the hash is stored; the plaintext password is never written to disk or logs

### Atomic Voucher Validation (Concurrency Safety)

The validation sequence uses a database-level row lock to prevent race conditions (Req 4.3, 4.4):

```python
async with db.begin():
    # Lock the row immediately
    result = await db.execute(
        select(Voucher)
        .where(Voucher.code == ilike(username))
        .with_for_update()
    )
    voucher = result.scalar_one_or_none()

    if not voucher:
        # write audit log, return reject
        ...
    elif voucher.status != 'unused':
        # write audit log, return reject
        ...
    elif voucher.is_expired():
        # write audit log, return reject
        ...
    else:
        # Mark consumed atomically within the same transaction
        voucher.status = 'consumed'
        voucher.consumed_at = datetime.utcnow()
        if voucher.activation_mode == 'use-activated':
            voucher.expires_at = voucher.consumed_at + timedelta(minutes=voucher.duration_minutes)
        # write audit log
        await db.commit()
        return {"result": "accept"}
```

The `SELECT ... FOR UPDATE` acquires a row-level lock. Any concurrent transaction attempting the same select will block until the first transaction commits or rolls back, ensuring mutual exclusion.

### TLS / HTTPS

**Production:**
- FastAPI is deployed behind a reverse proxy (nginx or Caddy)
- The proxy terminates TLS using a certificate from Let's Encrypt
- TLS 1.2 or higher is enforced at the proxy level
- HTTP requests are redirected to HTTPS with a 301 response

**Development (`APP_ENV=development`):**
- FastAPI runs with `uvicorn --host 0.0.0.0 --port 8000` without TLS
- HTTP is accepted for local testing

**nginx TLS configuration snippet:**
```nginx
server {
    listen 443 ssl;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_certificate /etc/letsencrypt/live/yourdomain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    return 301 https://$host$request_uri;
}
```

### Input Validation

FastAPI uses Pydantic v2 models for all request body validation. Invalid fields return HTTP 422 automatically with the field name and error reason. Custom validators handle domain-specific constraints (e.g., quantity range, duration range, activation mode enum).

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

**Property-based testing library:** `hypothesis` (Python). Each property is a `@given` decorated test function. Minimum 100 iterations per test (Hypothesis default).

### Property 1: Voucher code character set

*For any* generated voucher code, the code has exactly 12 characters and every character is in the allowed set (`{A-Z} \ {I, O}` union `{2-9}`), with no characters from the excluded set `{I, O, 0, 1}`.

**Validates: Requirements 1.1**

### Property 2: Batch quantity boundary

*For any* integer quantity Q, if Q is in [1, 500] then the generation request creates exactly Q vouchers and returns HTTP 201; if Q is less than 1 or greater than 500 then the generation request returns HTTP 400 and zero vouchers are created.

**Validates: Requirements 1.3**

### Property 3: Custom duration boundary

*For any* integer duration D in minutes, if D is in [1, 43200] then the generation request is accepted and the stored voucher has `duration_minutes == D`; if D is less than 1 or greater than 43200 then the request returns HTTP 400 and zero vouchers are created.

**Validates: Requirements 1.5**

### Property 4: Generated vouchers start unused

*For any* valid generation request with any combination of valid parameters, all generated vouchers have `status = 'unused'` when retrieved immediately after creation.

**Validates: Requirements 1.6**

### Property 5: Invalid activation mode rejected

*For any* string S that is not equal to `"use-activated"` or `"pre-activated"`, a generation request with `activation_mode = S` returns HTTP 400 and no vouchers are created.

**Validates: Requirements 1.9**

### Property 6: Valid voucher always accepted

*For any* voucher code C where the corresponding row has `status = 'unused'` and either (`activation_mode = 'pre-activated'` and `expires_at > now()`) or (`activation_mode = 'use-activated'` and `expires_at IS NULL` or `expires_at > now()`), calling `/radius/validate` with `username = C` and `password = C` returns `{"result": "accept"}`.

**Validates: Requirements 2.2**

### Property 7: Non-existent code always rejected

*For any* string S that does not correspond to any row in the `vouchers` table, calling `/radius/validate` with `username = S` and `password = S` returns `{"result": "reject"}` with `reason = "not_found"`.

**Validates: Requirements 2.3**

### Property 8: Invalid-state voucher always rejected

*For any* voucher code C where the corresponding row has `status = 'consumed'` OR `status = 'revoked'` OR (`expires_at IS NOT NULL` AND `expires_at <= now()`), calling `/radius/validate` returns `{"result": "reject"}`.

**Validates: Requirements 2.4, 2.5, 2.6**

### Property 9: Pre-activated expiry correctness

*For any* pre-activated voucher created at timestamp T with duration D minutes, `expires_at == T + D minutes` (within 1-second tolerance for clock drift).

**Validates: Requirements 3.1**

### Property 10: Use-activated expiry correctness

*For any* use-activated voucher first authenticated at timestamp T with duration D minutes, after the successful authentication `expires_at == T + D minutes` (within 1-second tolerance).

**Validates: Requirements 3.2**

### Property 11: Use-activated voucher not pre-expired

*For any* use-activated voucher that has never been authenticated, regardless of how much time has elapsed since creation, the voucher is not rejected with reason `"expired"` by `/radius/validate`.

**Validates: Requirements 3.3**

### Property 12: Successful validation sets consumed state

*For any* valid unused voucher code C, after a successful call to `/radius/validate`, the voucher row has `status = 'consumed'` and `consumed_at IS NOT NULL`.

**Validates: Requirements 4.1**

### Property 13: Concurrent validation atomicity

*For any* valid unused voucher code C, if N concurrent requests (N ≥ 2) are made to `/radius/validate` simultaneously, exactly one request receives `{"result": "accept"}` and all remaining N-1 requests receive `{"result": "reject"}` with `reason = "consumed"`.

**Validates: Requirements 4.3, 4.4**

### Property 14: Revocation sets revoked state

*For any* voucher with `status = 'unused'`, after calling `DELETE /admin/vouchers/{code}/revoke`, the voucher row has `status = 'revoked'` and `revoked_at IS NOT NULL`.

**Validates: Requirements 5.1**

### Property 15: Paginated voucher list completeness

*For any* set of N vouchers in the database, the paginated list response covers all N vouchers across ⌈N/25⌉ pages, and each page contains at most 25 items. Every item in the response contains all required fields: `code`, `status`, `activation_mode`, `duration_minutes`, `expires_at`, `created_at`, `consumed_at`, `revoked_at`.

**Validates: Requirements 6.4**

### Property 16: Audit log written for every validation

*For any* call to `/radius/validate` with any voucher code C, MAC address M, and any outcome, an audit log entry is created with `voucher_code = C`, `client_mac = M`, and `outcome` matching the result, with a non-null UTC `logged_at` timestamp.

**Validates: Requirements 7.1, 7.2**

### Property 17: Audit log filter correctness

*For any* set of audit log entries and any combination of filter parameters (voucher_code, client_mac, outcome, date_from, date_to), the filtered response contains exactly those entries that satisfy all supplied filter criteria, and no entries that do not satisfy them.

**Validates: Requirements 7.4**

### Property 18: Admin password always bcrypt-hashed

*For any* admin account creation with password P, the value stored in `admins.password_hash` is a valid bcrypt hash with cost factor ≥ 10, and `P` does not appear anywhere in the stored hash string.

**Validates: Requirements 11.2**

### Property 19: Input validation returns field-level errors

*For any* API request that contains one or more fields with invalid values, the response is HTTP 422 and the response body identifies each invalid field by name along with the reason for rejection.

**Validates: Requirements 11.3**

### Property 20: Rate limit enforcement

*For any* source IP address, after exactly 60 requests to `/radius/validate` within a 60-second window, the 61st and subsequent requests within that window return HTTP 429.

**Validates: Requirements 11.6**

### Property 21: Missing environment variables cause startup failure

*For any* non-empty subset S of the required environment variables (`DATABASE_URL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `JWT_SECRET_KEY`, `RADIUS_SHARED_SECRET`) that are absent or empty at startup, the application exits with a non-zero exit code and the error output names each variable in S.

**Validates: Requirements 12.2**

---

## Error Handling

### HTTP Error Response Format

All error responses follow the same shape (Pydantic `HTTPException` handler):

```json
{
  "detail": "Human-readable error message",
  "code": "machine_readable_error_code"
}
```

For validation errors (HTTP 422), FastAPI's default Pydantic format is used:

```json
{
  "detail": [
    {
      "loc": ["body", "quantity"],
      "msg": "Value must be between 1 and 500",
      "type": "value_error"
    }
  ]
}
```

### Error Codes Reference

| HTTP Status | Scenario | `code` value |
|---|---|---|
| 400 | Quantity out of range | `quantity_out_of_range` |
| 400 | Duration out of range | `duration_out_of_range` |
| 400 | Invalid activation mode | `invalid_activation_mode` |
| 401 | Missing or invalid JWT | `unauthorized` |
| 401 | Wrong login credentials | `invalid_credentials` |
| 404 | Voucher not found | `voucher_not_found` |
| 409 | Voucher already consumed/revoked | `voucher_not_revocable` |
| 422 | Request schema validation failure | (Pydantic format) |
| 429 | Rate limit exceeded | `rate_limit_exceeded` |
| 500 | Code collision after max retries | `code_generation_failed` |

### Audit Log Fail-Safe

If the database write for an audit log entry fails, the error is handled as follows:

1. The exception is caught.
2. A structured log entry is written to the application log (via Python `logging`) containing: UTC timestamp, voucher code, client MAC, intended outcome, and the database error message.
3. The validation result is returned to FreeRADIUS regardless. Guest access is not affected by a logging failure.

```python
try:
    db.add(AuditLog(...))
    await db.commit()
except Exception as e:
    logger.error(
        "Audit log write failed",
        extra={"voucher_code": code, "client_mac": mac, "outcome": outcome, "error": str(e)}
    )
    # Do not re-raise — continue to return validation result
```

### FreeRADIUS Fail-Closed Behavior

If the FastAPI endpoint is unreachable, returns an HTTP 5xx error, or does not respond within 5 seconds, FreeRADIUS rlm_rest returns a `fail` module result. The site policy maps `fail` to Access-Reject (never Access-Accept), ensuring that a backend outage does not grant unauthorized access.

---

## Testing Strategy

### Unit Tests

Use `pytest` with `pytest-asyncio` for async tests. Use `httpx.AsyncClient` with the FastAPI `TestClient` for endpoint tests. Use a test PostgreSQL database (or `pytest-postgresql`) for integration tests.

Unit tests cover:
- Voucher code generation: character set validation, length, no forbidden characters
- Expiry timestamp calculations: pre-activated and use-activated
- Input validation: boundary values for quantity and duration
- Password hashing: verify hash format and cost factor
- JWT creation and verification

### Property-Based Tests

Use `hypothesis` for property-based tests.

```python
from hypothesis import given, settings
import hypothesis.strategies as st

# Feature: aruba-voucher-backend, Property 1: voucher code character set
@given(st.integers(min_value=0, max_value=1000))
@settings(max_examples=200)
def test_voucher_code_character_set(seed):
    code = generate_voucher_code(seed=seed)
    allowed = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    assert len(code) == 12
    assert all(c in allowed for c in code)
```

Each property test references its design property in a comment: `# Feature: aruba-voucher-backend, Property N: <property text>`

Minimum 100 iterations per property test (configured via `@settings(max_examples=100)`).

### Integration Tests

Integration tests run against a real FreeRADIUS and PostgreSQL instance (Docker Compose in CI):

- Full rlm_rest flow: submit RADIUS request, verify accept/reject
- Full rlm_sql flow: verify view query and post-auth callback
- Concurrent validation: send parallel requests for the same voucher, verify atomicity
- Audit log completeness: verify every request produces a log entry
- Session expiry: verify 60-minute idle timeout

### Test Structure

```
tests/
├── unit/
│   ├── test_voucher_generation.py
│   ├── test_expiry_logic.py
│   ├── test_auth.py
│   └── test_input_validation.py
├── property/
│   ├── test_voucher_code_properties.py
│   ├── test_validation_properties.py
│   ├── test_expiry_properties.py
│   └── test_concurrency_properties.py
└── integration/
    ├── test_radius_flow.py
    ├── test_admin_api.py
    └── test_audit_log.py
```

---

## Deployment Architecture

### Directory Structure

```
aruba-voucher-backend/
├── app/
│   ├── main.py                  # FastAPI app factory
│   ├── config.py                # Environment variable loading and validation
│   ├── models/
│   │   ├── voucher.py           # SQLAlchemy Voucher model
│   │   ├── audit_log.py         # SQLAlchemy AuditLog model
│   │   └── admin.py             # SQLAlchemy Admin model
│   ├── routers/
│   │   ├── admin_vouchers.py    # POST/GET/DELETE /admin/vouchers/*
│   │   ├── admin_auth.py        # POST /admin/login, /admin/logout
│   │   ├── admin_audit.py       # GET /admin/audit-logs
│   │   ├── admin_ui.py          # HTML page routes
│   │   └── radius.py            # POST /radius/validate, /radius/post-auth
│   ├── services/
│   │   ├── voucher_service.py   # Business logic: generate, validate, revoke
│   │   ├── auth_service.py      # JWT creation, verification, session management
│   │   └── audit_service.py     # Audit log writing with fail-safe
│   ├── templates/               # Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── dashboard.html
│   │   ├── vouchers_new.html
│   │   └── audit_logs.html
│   └── database.py              # SQLAlchemy async engine and session factory
├── alembic/
│   ├── env.py
│   ├── versions/
│   │   ├── 001_create_admins.py
│   │   ├── 002_create_vouchers.py
│   │   ├── 003_create_audit_logs.py
│   │   └── 004_create_radius_view.py
│   └── alembic.ini
├── tests/
│   ├── unit/
│   ├── property/
│   └── integration/
├── freeradius/
│   ├── mods-available/
│   │   └── rest               # rlm_rest config
│   ├── mods-enabled/          # symlinks go here
│   ├── sites-available/
│   │   └── voucher            # Virtual server config
│   ├── sites-enabled/         # symlinks go here
│   ├── sql/
│   │   └── postgresql.conf    # rlm_sql config
│   └── clients.conf           # RADIUS client (AP) config
├── .env.example               # Template for environment variables
├── requirements.txt           # Pinned Python dependencies
├── README.md
└── nginx/
    └── voucher.conf           # nginx reverse proxy config
```

### Environment Variables

```bash
# Required — application will not start without these
DATABASE_URL=postgresql+asyncpg://voucher_user:password@localhost:5432/voucher_db
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme_in_production
JWT_SECRET_KEY=a-long-random-secret-key-at-least-32-chars
RADIUS_SHARED_SECRET=radius_shared_secret

# Optional with defaults
APP_ENV=development          # "development" or "production"
APP_PORT=8000
JWT_EXPIRE_SECONDS=3600      # 60 minutes
RATE_LIMIT_REQUESTS=60       # per window
RATE_LIMIT_WINDOW=60         # seconds
LOG_LEVEL=INFO
```

### Startup Validation

`app/config.py` reads all required variables at import time and exits immediately if any are missing:

```python
import sys
import os

REQUIRED_VARS = ["DATABASE_URL", "ADMIN_USERNAME", "ADMIN_PASSWORD", "JWT_SECRET_KEY", "RADIUS_SHARED_SECRET"]

missing = [var for var in REQUIRED_VARS if not os.getenv(var)]
if missing:
    print(f"ERROR: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)
```

---

### Windows Development Setup (Step-by-Step)

> **Beginner note:** These steps assume you have Python 3.11+ and PostgreSQL 15 installed on Windows. If not, download Python from python.org and PostgreSQL from postgresql.org/download/windows/.

1. **Clone the repository and enter the project directory:**
   ```powershell
   git clone <repo_url>
   cd aruba-voucher-backend
   ```

2. **Create and activate a virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Create the PostgreSQL database:**
   Open pgAdmin or psql and run:
   ```sql
   CREATE USER voucher_user WITH PASSWORD 'devpassword';
   CREATE DATABASE voucher_db OWNER voucher_user;
   GRANT ALL PRIVILEGES ON DATABASE voucher_db TO voucher_user;
   ```

5. **Copy and configure the environment file:**
   ```powershell
   copy .env.example .env
   # Edit .env with your actual values
   notepad .env
   ```

6. **Run Alembic migrations to create the database schema:**
   ```powershell
   alembic upgrade head
   ```

7. **Start the development server:**
   ```powershell
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

8. **Open the admin interface:** http://localhost:8000/admin/login

9. **For FreeRADIUS on Windows:** Use WSL (Windows Subsystem for Linux) and install FreeRADIUS inside WSL. Copy the config files from `freeradius/` to `/etc/freeradius/3.0/` inside WSL. The FastAPI app will be reachable from WSL at `http://$(hostname -I | awk '{print $1}'):8000`.

---

### Linux VPS Production Setup

1. **Install system packages:**
   ```bash
   sudo apt update
   sudo apt install -y python3.11 python3.11-venv postgresql-15 nginx freeradius freeradius-rest freeradius-postgresql
   ```

2. **Set up the database (same steps as development, but with a stronger password).**

3. **Create the application user and directory:**
   ```bash
   sudo useradd --system --no-create-home --shell /bin/false voucher
   sudo mkdir /opt/aruba-voucher-backend
   sudo chown voucher:voucher /opt/aruba-voucher-backend
   ```

4. **Deploy code, install dependencies, run migrations** (same as development steps 1–6, running as the `voucher` user).

5. **Create a systemd service file** at `/etc/systemd/system/aruba-voucher.service`:
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

6. **Configure nginx** using the TLS configuration from the Security Design section above.

7. **Obtain a TLS certificate:**
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d yourdomain.com
   ```

8. **Configure FreeRADIUS:** Copy configs from `freeradius/`, enable the site and module, start the service.

9. **Enable and start all services:**
   ```bash
   sudo systemctl enable --now aruba-voucher postgresql nginx freeradius
   ```

---

### Walled Garden Configuration

The following entries must be added to the Aruba AP's walled garden in the InstantOS 8.11 web UI (Configuration → Security → Walled Garden) or via CLI:

| Hostname | Port | Protocol | Purpose |
|---|---|---|---|
| `<Voucher_API_Host>` | 80 | HTTP | Voucher validation endpoint (development) |
| `<Voucher_API_Host>` | 443 | HTTPS | Voucher validation endpoint (production) |
| `christianricv-tech.github.io` | 80 | HTTP | Captive portal loading |
| `christianricv-tech.github.io` | 443 | HTTPS | Captive portal loading (GitHub Pages redirects to HTTPS) |
| `securelogin.arubanetworks.com` | 80 | HTTP | Aruba authentication endpoint |
| `securelogin.arubanetworks.com` | 443 | HTTPS | Aruba authentication endpoint |

---

## Key Design Decisions

### Why rlm_rest is preferred over rlm_sql

The rlm_rest path gives full atomicity: FastAPI wraps the entire `SELECT ... FOR UPDATE` + `UPDATE status='consumed'` in a single database transaction. The rlm_sql path has a TOCTOU gap between the view query (authorize) and the post-auth callback (mark consumed) — two concurrent requests could both pass the view query before either marks the voucher consumed. For a low-traffic guest WiFi, this race condition is unlikely but not impossible.

### Why we return HTTP 200 for both accept and reject from /radius/validate

FreeRADIUS rlm_rest maps HTTP status codes directly to RADIUS module outcomes. A 401 response causes rlm_rest to return `reject` without processing the JSON body. By always returning 200, we ensure the body is processed and the `result` field drives the decision through the FreeRADIUS authorize policy. This gives us full control over the reject reason.

### Why use-activated is the default Activation_Mode

Pre-activated vouchers start expiring the moment they are generated, even if the guest hasn't arrived yet. A voucher generated in the morning for an afternoon guest would already have burned through several hours. Use-activated vouchers give the guest the full configured time from the moment they connect, which is almost always the intended behavior.

### Why stateless JWT instead of a session table

A session table adds complexity (extra query on every request, cleanup job for expired sessions). For a single-admin or low-concurrency admin interface, the stateless JWT approach with an in-memory JTI blocklist is simpler and performant. The trade-off is that the blocklist is lost on restart — but since admin sessions expire after 60 minutes anyway, this is an acceptable trade-off for a guest WiFi management tool.
