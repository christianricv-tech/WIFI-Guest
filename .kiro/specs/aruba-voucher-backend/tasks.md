# Implementation Plan: Aruba Voucher Backend

## Overview

Build a FastAPI-based guest WiFi voucher system that integrates with FreeRADIUS and PostgreSQL. The implementation is ordered so that each task builds on already-completed code: project scaffolding comes first, then data models and migrations, then core services, then HTTP endpoints, then the HTML admin UI, then FreeRADIUS config files, and finally tests and documentation. Property-based tests are placed close to the code they validate so bugs are caught early.

## Tasks

- [x] 1. Project scaffolding — create the base Python package, configuration loading, and database session
  - [x] 1.1 Create project directory structure and `requirements.txt`
    - Create all directories: `app/models/`, `app/routers/`, `app/services/`, `app/templates/`, `alembic/versions/`, `tests/unit/`, `tests/property/`, `tests/integration/`, `freeradius/mods-available/`, `freeradius/sites-available/`, `freeradius/sql/`, `nginx/`
    - Write `requirements.txt` with exact pinned versions for: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `passlib[bcrypt]`, `python-jose[cryptography]`, `slowapi`, `jinja2`, `python-multipart`, `hypothesis`, `pytest`, `pytest-asyncio`, `httpx`, `python-dotenv`, `limits`
    - Create `app/__init__.py` and `app/models/__init__.py`, `app/routers/__init__.py`, `app/services/__init__.py` as empty init files
    - Create `.env.example` with all required and optional variables documented with comments
    - _Requirements: 12.1, 12.3_

  - [x] 1.2 Implement `app/config.py` — environment variable loading with startup validation
    - Define `REQUIRED_VARS = ["DATABASE_URL", "ADMIN_USERNAME", "ADMIN_PASSWORD", "JWT_SECRET_KEY", "RADIUS_SHARED_SECRET"]`
    - On import, collect any missing/empty vars and call `sys.exit(1)` with a message listing each missing name
    - Expose typed settings: `DATABASE_URL`, `APP_ENV`, `APP_PORT`, `JWT_SECRET_KEY`, `JWT_EXPIRE_SECONDS`, `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW`, `LOG_LEVEL`
    - Use `python-dotenv` to load a `.env` file when present (development convenience)
    - _Requirements: 12.1, 12.2_

  - [x]* 1.3 Write property test for startup validation (Property 21)
    - **Property 21: Missing environment variables cause startup failure**
    - **Validates: Requirements 12.2**
    - Use `hypothesis` to generate non-empty subsets of `REQUIRED_VARS`; for each subset, unset those vars and assert the app exits with a non-zero code and names each missing variable in stderr
    - File: `tests/property/test_config_properties.py`

  - [x] 1.4 Implement `app/database.py` — async SQLAlchemy engine and session factory
    - Create async engine using `asyncpg` dialect from `DATABASE_URL`
    - Implement `AsyncSessionLocal` using `async_sessionmaker`
    - Implement `get_db()` dependency that yields a session and closes it after the request
    - _Requirements: 12.1_

- [x] 2. Database models and Alembic migrations — define all tables and the RADIUS view
  - [x] 2.1 Implement `app/models/admin.py` — `admins` table SQLAlchemy model
    - Define `Admin` model with columns: `id` (UUID PK), `username` (VARCHAR 64, unique), `password_hash` (VARCHAR 128), `created_at` (TIMESTAMPTZ), `last_login_at` (TIMESTAMPTZ nullable)
    - Add index on `username`
    - _Requirements: 11.2_

  - [x] 2.2 Implement `app/models/voucher.py` — `vouchers` table SQLAlchemy model
    - Define `Voucher` model with all columns from the design schema: `id`, `code`, `status` (CHECK constraint), `activation_mode` (CHECK constraint), `duration_minutes` (CHECK constraint 1–525600), `created_at`, `expires_at` (nullable), `consumed_at` (nullable), `revoked_at` (nullable), `created_by` (FK to admins, ON DELETE SET NULL)
    - Add indexes on `code`, `status`, `created_at DESC`, `expires_at`
    - Add a helper method `is_expired() -> bool` that compares `expires_at` against `datetime.utcnow()` (returns `False` if `expires_at` is None — use-activated not yet used)
    - _Requirements: 1.6, 3.5_

  - [x] 2.3 Implement `app/models/audit_log.py` — `audit_logs` table SQLAlchemy model
    - Define `AuditLog` model with columns: `id` (BIGSERIAL PK), `logged_at` (TIMESTAMPTZ), `voucher_code` (VARCHAR 12), `client_mac` (VARCHAR 17, nullable), `outcome` (CHECK: accepted/rejected), `rejection_reason` (VARCHAR 32, nullable), `source` (CHECK: rlm_rest/rlm_sql)
    - Add indexes on `logged_at DESC`, `voucher_code`, `client_mac`, `outcome`
    - _Requirements: 7.1_

  - [x] 2.4 Write Alembic migration `001_create_admins.py`
    - `upgrade()`: creates the `admins` table and its indexes exactly as defined in the model
    - `downgrade()`: drops the table
    - _Requirements: 12.4, 12.5_

  - [x] 2.5 Write Alembic migration `002_create_vouchers.py`
    - `upgrade()`: creates the `vouchers` table with all CHECK constraints, FK, and indexes
    - `downgrade()`: drops the table
    - _Requirements: 12.4, 12.5_

  - [x] 2.6 Write Alembic migration `003_create_audit_logs.py`
    - `upgrade()`: creates the `audit_logs` table and its indexes
    - `downgrade()`: drops the table
    - _Requirements: 12.4, 12.5_

  - [x] 2.7 Write Alembic migration `004_create_radius_view.py`
    - `upgrade()`: creates `radius_voucher_view` using `CREATE OR REPLACE VIEW` with the exact SQL from the design — selects `code AS username`, `code AS password`, and the `is_valid` CASE expression from `vouchers`
    - `downgrade()`: drops the view with `DROP VIEW IF EXISTS radius_voucher_view`
    - _Requirements: 10.3, 10.4_

  - [x] 2.8 Configure `alembic/env.py` to use the async SQLAlchemy engine
    - Import all models so Alembic can detect schema changes automatically
    - Configure `target_metadata` from `Base.metadata`
    - _Requirements: 12.4_

- [x] 3. Checkpoint — verify project scaffolding and models
  - Ensure `alembic upgrade head` runs cleanly against a test PostgreSQL database. Ensure all model files import without errors. Ask the user if questions arise.

- [x] 4. Voucher service — code generation, expiry logic, collision handling
  - [x] 4.1 Implement `app/services/voucher_service.py` — `generate_voucher_code()` function
    - Allowed character set: uppercase A–Z minus I and O, plus digits 2–9 (total 32 chars: `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`)
    - Use `secrets.choice()` (cryptographically secure) to pick 12 characters
    - Return the 12-character string
    - _Requirements: 1.1_

  - [x]* 4.2 Write property test for voucher code character set (Property 1)
    - **Property 1: Voucher code character set**
    - **Validates: Requirements 1.1**
    - Use `@given(st.integers())` as a seed parameter; call `generate_voucher_code()` repeatedly; assert `len(code) == 12` and `all(c in ALLOWED_CHARS for c in code)` and none of `I`, `O`, `0`, `1` appear
    - File: `tests/property/test_voucher_code_properties.py`

  - [x] 4.3 Implement `generate_vouchers_batch()` in `voucher_service.py` — batch generation with collision retry
    - Accept `quantity`, `duration_minutes`, `activation_mode`, `admin_id`, and db session
    - For each voucher: attempt `generate_voucher_code()`, query DB for existing code, retry up to 5 times on collision, raise HTTP 500 (`code_generation_failed`) if all 5 fail
    - For `pre-activated`: set `expires_at = now() + timedelta(minutes=duration_minutes)` at creation
    - For `use-activated`: leave `expires_at = None` at creation
    - Persist all `Voucher` objects and commit
    - _Requirements: 1.2, 1.3, 1.6, 1.7, 1.8, 3.1_

  - [x]* 4.4 Write property test for batch quantity boundary (Property 2)
    - **Property 2: Batch quantity boundary**
    - **Validates: Requirements 1.3**
    - Use `@given(st.integers())` for quantity; test that quantities in [1, 500] return HTTP 201 with exactly that many vouchers, and quantities outside [1, 500] return HTTP 400 with zero vouchers created
    - File: `tests/property/test_voucher_code_properties.py`

  - [x]* 4.5 Write property test for custom duration boundary (Property 3)
    - **Property 3: Custom duration boundary**
    - **Validates: Requirements 1.5**
    - Use `@given(st.integers())` for duration_minutes; assert that durations in [1, 43200] succeed and durations outside that range return HTTP 400
    - File: `tests/property/test_voucher_code_properties.py`

  - [x]* 4.6 Write property test for generated vouchers start unused (Property 4)
    - **Property 4: Generated vouchers start unused**
    - **Validates: Requirements 1.6**
    - Use `@given(st.integers(1, 10), st.integers(1, 43200), st.sampled_from(["use-activated", "pre-activated"]))` — generate vouchers with any valid params and assert every returned voucher has `status = 'unused'`
    - File: `tests/property/test_voucher_code_properties.py`

  - [x]* 4.7 Write property test for invalid activation mode rejected (Property 5)
    - **Property 5: Invalid activation mode rejected**
    - **Validates: Requirements 1.9**
    - Use `@given(st.text())` filtered to exclude `"use-activated"` and `"pre-activated"`; assert HTTP 400 and zero vouchers created
    - File: `tests/property/test_voucher_code_properties.py`

  - [x] 4.8 Implement expiry logic helpers in `voucher_service.py`
    - `set_use_activated_expiry(voucher, consumed_at)`: sets `voucher.expires_at = consumed_at + timedelta(minutes=voucher.duration_minutes)` — called inside the RADIUS validate transaction
    - `compute_pre_activated_expiry(duration_minutes) -> datetime`: returns `utcnow() + timedelta(minutes=duration_minutes)` — used at generation time
    - _Requirements: 3.1, 3.2, 3.3, 3.5_

  - [x]* 4.9 Write property test for pre-activated expiry correctness (Property 9)
    - **Property 9: Pre-activated expiry correctness**
    - **Validates: Requirements 3.1**
    - Use `@given(st.datetimes(), st.integers(1, 43200))` as `(created_at, duration_minutes)`; call `compute_pre_activated_expiry` and assert `abs((expires_at - (created_at + timedelta(minutes=duration_minutes))).total_seconds()) <= 1`
    - File: `tests/property/test_expiry_properties.py`

  - [x]* 4.10 Write property test for use-activated expiry correctness (Property 10)
    - **Property 10: Use-activated expiry correctness**
    - **Validates: Requirements 3.2**
    - Use `@given(st.datetimes(), st.integers(1, 43200))` as `(consumed_at, duration_minutes)`; call `set_use_activated_expiry` and assert `expires_at == consumed_at + timedelta(minutes=duration_minutes)` within 1 second
    - File: `tests/property/test_expiry_properties.py`

  - [x]* 4.11 Write property test for use-activated voucher not pre-expired (Property 11)
    - **Property 11: Use-activated voucher not pre-expired**
    - **Validates: Requirements 3.3**
    - Use `@given(st.datetimes(max_value=...))` as creation time (up to many years in the past); create a use-activated voucher with `expires_at = None` and assert `voucher.is_expired() == False`
    - File: `tests/property/test_expiry_properties.py`

- [x] 5. Auth service — bcrypt password hashing, JWT creation, and session management
  - [x] 5.1 Implement `app/services/auth_service.py` — password hashing and JWT operations
    - Configure `CryptContext(schemes=["bcrypt"], bcrypt__rounds=12)`
    - Implement `hash_password(plaintext: str) -> str` and `verify_password(plaintext: str, hashed: str) -> bool`
    - Implement `create_access_token(sub, username) -> str`: builds JWT payload with `sub`, `username`, `iat`, `exp` (now + `JWT_EXPIRE_SECONDS`), and a fresh `jti` (UUID4)
    - Implement `decode_token(token: str) -> dict`: decodes and validates JWT, raises HTTP 401 on invalid/expired
    - _Requirements: 6.1, 6.2, 11.2_

  - [x] 5.2 Implement in-memory JTI blocklist and idle-timeout tracker in `auth_service.py`
    - `BLOCKLIST: set[str]` — JTI values of invalidated sessions; populated on logout
    - `LAST_SEEN: dict[str, datetime]` — maps JTI → last-activity timestamp
    - `check_session(jti, now)`: raises HTTP 401 if `jti` is in blocklist OR if `now - LAST_SEEN[jti] > JWT_EXPIRE_SECONDS`; otherwise updates `LAST_SEEN[jti] = now`
    - `invalidate_session(jti)`: adds to blocklist, removes from `LAST_SEEN`
    - Implement `get_current_admin(token)` FastAPI dependency: decodes token, calls `check_session`, returns admin payload
    - _Requirements: 6.10, 6.11_

  - [x]* 5.3 Write property test for admin password always bcrypt-hashed (Property 18)
    - **Property 18: Admin password always bcrypt-hashed**
    - **Validates: Requirements 11.2**
    - Use `@given(st.text(min_size=1))` for passwords; call `hash_password(p)` and assert: result starts with `$2b$`, cost factor ≥ 10 (parse from hash string), and the plaintext `p` does not appear verbatim in the hash
    - File: `tests/property/test_auth_properties.py` (create this file)

- [x] 6. Audit service — fail-safe log writing
  - [x] 6.1 Implement `app/services/audit_service.py`
    - Implement `write_audit_log(db, voucher_code, client_mac, outcome, rejection_reason, source)` as an async function
    - Create and add an `AuditLog` object; call `await db.commit()`
    - Wrap in `try/except Exception`: on failure, write a structured entry to the Python `logging` module with all fields and the error message, then return without re-raising
    - Never allow an audit log failure to propagate to the caller
    - _Requirements: 7.1, 7.2, 7.5_

- [x] 7. RADIUS validation endpoint — atomic single-use enforcement
  - [x] 7.1 Implement `app/routers/radius.py` — `POST /radius/validate`
    - Define `RadiusValidateRequest` Pydantic model: `username: str`, `password: str`, `client_mac: Optional[str]`
    - Open a DB transaction, issue `SELECT ... WHERE code ILIKE :username FOR UPDATE` to atomically lock the row
    - Validation logic (in this order): not found → reject `not_found`; status consumed → reject `consumed`; status revoked → reject `revoked`; `is_expired()` → reject `expired`; otherwise → accept
    - On accept: set `status='consumed'`, `consumed_at=utcnow()`, call `set_use_activated_expiry` if `activation_mode='use-activated'`; commit
    - Call `write_audit_log()` for every path (accept and all reject branches)
    - Always return HTTP 200 with `{"result": "accept"}` or `{"result": "reject", "reason": "..."}` — never 401/403
    - Apply `@limiter.limit("60/minute")` from slowapi; configure `Limiter(key_func=get_remote_address)` on the FastAPI app
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 4.1, 4.2, 4.3, 9.3, 9.5, 11.6_

  - [x]* 7.2 Write property test for valid voucher always accepted (Property 6)
    - **Property 6: Valid voucher always accepted**
    - **Validates: Requirements 2.2**
    - Use `@given(st.sampled_from(["use-activated", "pre-activated"]), st.integers(1, 43200))` to create valid unused vouchers, call `/radius/validate`, assert `{"result": "accept"}`
    - File: `tests/property/test_validation_properties.py`

  - [x]* 7.3 Write property test for non-existent code always rejected (Property 7)
    - **Property 7: Non-existent code always rejected**
    - **Validates: Requirements 2.3**
    - Use `@given(st.text(alphabet=st.characters(), min_size=1, max_size=20))` filtered to exclude any real voucher code; assert `{"result": "reject", "reason": "not_found"}`
    - File: `tests/property/test_validation_properties.py`

  - [x]* 7.4 Write property test for invalid-state voucher always rejected (Property 8)
    - **Property 8: Invalid-state voucher always rejected**
    - **Validates: Requirements 2.4, 2.5, 2.6**
    - Use `@given(st.sampled_from(["consumed", "revoked"]))` and a separately-crafted expired voucher; for each case call `/radius/validate` and assert `{"result": "reject"}`
    - File: `tests/property/test_validation_properties.py`

  - [x]* 7.5 Write property test for successful validation sets consumed state (Property 12)
    - **Property 12: Successful validation sets consumed state**
    - **Validates: Requirements 4.1**
    - After each accept, query the DB and assert `status = 'consumed'` and `consumed_at IS NOT NULL`
    - File: `tests/property/test_validation_properties.py`

  - [x]* 7.6 Write property test for concurrent validation atomicity (Property 13)
    - **Property 13: Concurrent validation atomicity**
    - **Validates: Requirements 4.3, 4.4**
    - Use `@given(st.integers(min_value=2, max_value=10))` for N; fire N concurrent `asyncio` tasks all calling `/radius/validate` for the same unused voucher; assert exactly one `accept` and N-1 `reject` with `reason='consumed'`
    - File: `tests/property/test_concurrency_properties.py`

  - [x] 7.7 Implement `app/routers/radius.py` — `POST /radius/post-auth` (rlm_sql path)
    - Define `RadiusPostAuthRequest` Pydantic model: `username: str`, `client_mac: Optional[str]`
    - Look up voucher by code; if not found return 404; if already consumed return 200 (idempotent)
    - Update `status='consumed'`, `consumed_at=utcnow()`, call `set_use_activated_expiry` if applicable; commit
    - Call `write_audit_log()` with `source='rlm_sql'`
    - _Requirements: 10.5_

- [x] 8. Admin auth endpoints — login, logout, JWT session
  - [x] 8.1 Implement `app/routers/admin_auth.py` — `POST /admin/login`
    - Define `LoginRequest` Pydantic model: `username: str`, `password: str`
    - Query `admins` table by username; if not found OR `verify_password` fails, return HTTP 401 `{"detail": "Invalid credentials"}` (no field-level distinction)
    - On success: call `create_access_token()`, update `admin.last_login_at`, commit; return `{"access_token": ..., "token_type": "bearer", "expires_in": JWT_EXPIRE_SECONDS}`
    - _Requirements: 6.3_

  - [x] 8.2 Implement `POST /admin/logout` in `admin_auth.py`
    - Require auth via `get_current_admin` dependency
    - Extract `jti` from decoded token, call `invalidate_session(jti)`, return `{"detail": "Logged out successfully"}`
    - _Requirements: 6.10_

  - [x] 8.3 Implement admin seeding logic in `app/main.py`
    - On startup (`@app.on_event("startup")`), check if any admin exists; if not, create one from `ADMIN_USERNAME`/`ADMIN_PASSWORD` env vars using `hash_password()`
    - This ensures a usable admin account after a fresh `alembic upgrade head`
    - _Requirements: 12.1_

- [x] 9. Admin voucher API endpoints
  - [x] 9.1 Implement `app/routers/admin_vouchers.py` — `POST /admin/vouchers`
    - Define `VoucherGenerateRequest` Pydantic model: `quantity: int` (1–500, default 1), `duration_minutes: Optional[int]` (1–43200), `duration_preset: Optional[str]` (one of 1h/4h/8h/24h/7d/30d), `activation_mode: str` (default `use-activated`)
    - Validate that exactly one of `duration_minutes` or `duration_preset` is provided; resolve preset to minutes (1h=60, 4h=240, 8h=480, 24h=1440, 7d=10080, 30d=43200)
    - Validate `activation_mode` is `use-activated` or `pre-activated`; reject others with HTTP 400 `invalid_activation_mode`
    - Call `generate_vouchers_batch()` from `voucher_service`; return HTTP 201 with `{"generated": N, "vouchers": [...]}`
    - Require `get_current_admin` dependency
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 6.6_

  - [x] 9.2 Implement `GET /admin/vouchers` — paginated list with optional status filter
    - Accept query params: `page: int = 1`, `status: Optional[str]`
    - For `status='expired'`: query `WHERE status='unused' AND expires_at < now()`; for other status values: query `WHERE status = :status`; for no filter: return all
    - Order by `created_at DESC`; apply `LIMIT 25 OFFSET (page-1)*25`; also run a COUNT query for `total`
    - Return `{"page": page, "page_size": 25, "total": total, "items": [...]}`
    - Require `get_current_admin` dependency
    - _Requirements: 6.4, 6.5_

  - [x] 9.3 Implement `GET /admin/vouchers/{code}` — single voucher lookup
    - Query by code (case-insensitive); return HTTP 404 `voucher_not_found` if not found
    - Require `get_current_admin` dependency
    - _Requirements: 6.4_

  - [x] 9.4 Implement `DELETE /admin/vouchers/{code}/revoke` — revocation endpoint
    - Look up voucher; return 404 if not found
    - If `status` is `consumed` or `revoked`: return HTTP 409 with `{"detail": "Voucher cannot be revoked: current status is '...'"}`
    - Set `status='revoked'`, `revoked_at=utcnow()`, commit; return the updated voucher object
    - Require `get_current_admin` dependency
    - _Requirements: 5.1, 5.3, 5.4, 6.7, 6.8_

  - [x]* 9.5 Write property test for paginated voucher list completeness (Property 15)
    - **Property 15: Paginated voucher list completeness**
    - **Validates: Requirements 6.4**
    - Use `@given(st.integers(1, 100))` for N; insert N vouchers, page through all `⌈N/25⌉` pages, collect all items, assert exactly N unique codes and each item has all required fields
    - File: `tests/property/test_voucher_code_properties.py`

  - [x]* 9.6 Write property test for revocation sets revoked state (Property 14)
    - **Property 14: Revocation sets revoked state**
    - **Validates: Requirements 5.1**
    - Use `@given(st.integers(1, 43200), st.sampled_from(["use-activated", "pre-activated"]))` to create unused vouchers; call `DELETE /admin/vouchers/{code}/revoke` and assert `status='revoked'` and `revoked_at IS NOT NULL`
    - File: `tests/property/test_validation_properties.py`

- [x] 10. Admin audit log API endpoint
  - [x] 10.1 Implement `app/routers/admin_audit.py` — `GET /admin/audit-logs`
    - Accept query params: `page: int = 1`, `voucher_code: Optional[str]`, `client_mac: Optional[str]`, `outcome: Optional[str]`, `date_from: Optional[date]`, `date_to: Optional[date]`
    - Build a dynamic SQLAlchemy query applying each filter that is present; `date_from`/`date_to` match against `logged_at` (start of day UTC inclusive, end of day UTC inclusive)
    - Order by `logged_at DESC`; `LIMIT 50 OFFSET (page-1)*50`; also run COUNT for `total`
    - Return `{"page": page, "page_size": 50, "total": total, "items": [...]}`
    - All filter + pagination combinations must complete within 3 seconds (aided by indexes on `logged_at`, `voucher_code`, `client_mac`, `outcome`)
    - Require `get_current_admin` dependency
    - _Requirements: 7.3, 7.4_

  - [x]* 10.2 Write property test for audit log written for every validation (Property 16)
    - **Property 16: Audit log written for every validation**
    - **Validates: Requirements 7.1, 7.2**
    - Use `@given(st.text(min_size=12, max_size=12), st.from_regex(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}"))` for (code, mac); call `/radius/validate` and immediately query `audit_logs`; assert one entry exists with matching `voucher_code`, `client_mac`, and correct `outcome`
    - File: `tests/property/test_validation_properties.py`

  - [x]* 10.3 Write property test for audit log filter correctness (Property 17)
    - **Property 17: Audit log filter correctness**
    - **Validates: Requirements 7.4**
    - Use `@given(st.lists(st.fixed_dictionaries({...}), min_size=1, max_size=30))` to insert a set of audit log entries; then apply random filter combinations and assert the response contains exactly the matching entries and none that don't match
    - File: `tests/property/test_validation_properties.py`

- [x] 11. Checkpoint — core API verified
  - Run `pytest tests/unit/ tests/property/` with a test database. Ensure all RADIUS and admin API endpoints respond correctly. Ask the user if questions arise.

- [x] 12. FastAPI app wiring — register routers, slowapi, startup
  - [x] 12.1 Implement `app/main.py` — FastAPI application factory
    - Create the `FastAPI` app instance; attach `slowapi` `Limiter` and its exception handler (`_rate_limit_exceeded_handler`)
    - Register all routers: `radius`, `admin_auth`, `admin_vouchers`, `admin_audit`, `admin_ui`
    - Add redirect: `GET /` → `GET /admin/dashboard`
    - Add `@app.on_event("startup")` hook that runs the admin seeding logic from task 8.3
    - Configure `logging` with `LOG_LEVEL` from settings
    - Mount `app/templates/` for Jinja2 via `Jinja2Templates`
    - _Requirements: 11.6, 12.7_

  - [x]* 12.2 Write property test for rate limit enforcement (Property 20)
    - **Property 20: Rate limit enforcement**
    - **Validates: Requirements 11.6**
    - Use `@given(st.integers(min_value=61, max_value=100))` for N; fire 60 valid requests, then N - 60 additional requests; assert all of the 61st-onward return HTTP 429
    - File: `tests/property/test_validation_properties.py`

  - [x]* 12.3 Write property test for input validation returns field-level errors (Property 19)
    - **Property 19: Input validation returns field-level errors**
    - **Validates: Requirements 11.3**
    - Use `@given(st.fixed_dictionaries({"quantity": st.integers() | st.text(), "duration_minutes": st.integers() | st.text()}))` with at least one invalid field; call `POST /admin/vouchers` and assert HTTP 422 with `detail` list naming each invalid field
    - File: `tests/property/test_voucher_code_properties.py`

- [x] 13. Admin HTML UI — Jinja2 templates and cookie-based auth routes
  - [x] 13.1 Create `app/templates/base.html` — shared layout template
    - Include a `<nav>` with links to Dashboard, New Voucher, Audit Logs, and Logout
    - Define `{% block content %}{% endblock %}` slot
    - Load minimal inline CSS for a functional, readable layout (no external CDN required)
    - _Requirements: 6.1, 6.12_

  - [x] 13.2 Create `app/templates/login.html` and `app/templates/dashboard.html`
    - `login.html`: form with `username` and `password` fields, POST to `/admin/login`; render error message block when `error` context variable is set
    - `dashboard.html`: extends `base.html`; renders paginated voucher table with columns: code, status, activation mode, duration, expires_at, created_at, consumed_at; links for revoke (unused only) and page navigation; status filter dropdown
    - _Requirements: 6.3, 6.4, 6.5, 6.7, 6.8_

  - [x] 13.3 Create `app/templates/vouchers_new.html`
    - Extends `base.html`; form fields: quantity (1–500), duration preset selector, custom duration input, activation mode radio
    - After successful generation, display all generated codes in a `<textarea>` for easy copy; render a "Download as .txt" link pointing to a download route
    - _Requirements: 6.6, 6.9_

  - [x] 13.4 Create `app/templates/audit_logs.html`
    - Extends `base.html`; filter form with fields: voucher_code, client_mac, outcome dropdown, date_from, date_to; table of audit entries (logged_at UTC, code, MAC, outcome, rejection_reason); pagination links
    - _Requirements: 7.3, 7.4_

  - [x] 13.5 Implement `app/routers/admin_ui.py` — HTML page routes
    - Cookie-based JWT: read `access_token` cookie; redirect to `/admin/login` if missing or invalid
    - `GET /admin/login` + `POST /admin/login`: render template or handle form POST; on success set `access_token` cookie and redirect to `/admin/dashboard`; on failure re-render with error
    - `GET /admin/dashboard`: render `dashboard.html` with paginated voucher list (calls voucher service internally)
    - `GET /admin/vouchers/new` + `POST /admin/vouchers/new`: render form or handle generation; on success render template with generated codes
    - `GET /admin/vouchers/{code}/revoke` (POST with method override or dedicated POST route): call revoke logic, redirect back to dashboard
    - `GET /admin/audit-logs`: render `audit_logs.html` with filtered+paginated results
    - `GET /admin/vouchers/download`: stream generated codes as `text/plain` attachment
    - `POST /admin/logout`: invalidate session, delete `access_token` cookie, redirect to `/admin/login`
    - _Requirements: 6.1, 6.2, 6.9, 6.10, 6.11, 6.12_

- [x] 14. FreeRADIUS configuration files
  - [x] 14.1 Write `freeradius/mods-available/rest` — rlm_rest module config
    - `connect_uri = "http://127.0.0.1:8000"`, `connect_timeout = 5.0`
    - `authorize` block: URI `${..connect_uri}/radius/validate`, method POST, body JSON, data template with `%{User-Name}`, `%{User-Password}`, `%{Calling-Station-Id}`
    - `post-auth` block: URI `${..connect_uri}/radius/post-auth`, method POST, body JSON, data template with `%{User-Name}` and `%{Calling-Station-Id}`
    - Connection `pool` block: start=5, min=4, max=10, spare=3, idle_timeout=60
    - _Requirements: 10.1, 10.2, 2.7_

  - [x] 14.2 Write `freeradius/sites-available/voucher` — virtual server config
    - `listen` block: type=auth, ipaddr=*, port=1812
    - `authorize` section: call `rest`; `if (ok || updated)` → set `Auth-Type := Accept`; `elsif (notfound || reject)` → reject; `else` → reject (fail-closed for 5xx / timeout)
    - `authenticate` section: `Auth-Type Accept { ok }`
    - `post-auth` section: `if (ok || updated) { rest.post-auth }`
    - Include comment block explaining the HTTP 200 always-returned design decision
    - _Requirements: 10.2, 2.7, 2.8_

  - [x] 14.3 Write `freeradius/sql/postgresql.conf` — rlm_sql module config
    - `dialect = "postgresql"`, `driver = "rlm_sql_postgresql"`, connection params with placeholder values
    - `authorize_check_query`: selects `password AS value, '==' AS op` from `radius_voucher_view` where `username = '%{SQL-User-Name}'` AND `is_valid = 1 LIMIT 1`
    - `accounting = no`
    - Pool block: start=5, min=4, max=10, spare=3, idle_timeout=60
    - _Requirements: 10.3, 10.4_

  - [x] 14.4 Write `freeradius/clients.conf` — RADIUS client (AP) config template
    - `client aruba_ap { ipaddr = <ap_ip_address>; secret = <shared_secret>; shortname = aruba-ap-505 }`
    - Comment explaining that `<ap_ip_address>` and `<shared_secret>` must be replaced with real values
    - _Requirements: 10.6_

- [x] 15. nginx reverse proxy configuration
  - [x] 15.1 Write `nginx/voucher.conf` — production nginx config
    - `server` block listening on port 443 SSL: `ssl_protocols TLSv1.2 TLSv1.3`; `ssl_certificate` and `ssl_certificate_key` pointing to Let's Encrypt paths; `proxy_pass http://127.0.0.1:8000`; `proxy_set_header X-Forwarded-For` and `X-Forwarded-Proto`
    - Second `server` block on port 80: `return 301 https://$host$request_uri`
    - _Requirements: 6.12, 11.4, 11.5_

- [x] 16. Unit tests
  - [x] 16.1 Write unit tests for voucher generation in `tests/unit/test_voucher_generation.py`
    - Test that `generate_voucher_code()` always returns 12 chars from the allowed set
    - Test that `I`, `O`, `0`, `1` never appear
    - Test that generating 1000 codes produces no duplicates (birthday-paradox sanity check)
    - _Requirements: 1.1_

  - [x] 16.2 Write unit tests for expiry logic in `tests/unit/test_expiry_logic.py`
    - Test `compute_pre_activated_expiry`: result equals `now + duration`
    - Test `set_use_activated_expiry`: `expires_at` set correctly when consumed
    - Test `Voucher.is_expired()`: returns `True` when `expires_at < utcnow()`, `False` when `expires_at > utcnow()`, `False` when `expires_at is None`
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 16.3 Write unit tests for auth in `tests/unit/test_auth.py`
    - Test `hash_password` and `verify_password` round-trip
    - Test that bcrypt cost factor is ≥ 12 by inspecting the hash string
    - Test `create_access_token` and `decode_token` round-trip with valid payload
    - Test that expired tokens raise HTTP 401
    - Test that blocklisted JTI raises HTTP 401
    - _Requirements: 6.10, 6.11, 11.2_

  - [x] 16.4 Write unit tests for input validation in `tests/unit/test_input_validation.py`
    - Test `POST /admin/vouchers` with quantity = 0, 501, 1, 500 — assert 400 or 201
    - Test with duration_minutes = 0, 43201, 1, 43200 — assert 400 or 201
    - Test with `activation_mode = "invalid"` — assert 400
    - Test that missing required field returns 422 with field name in error response
    - _Requirements: 1.3, 1.5, 1.9, 11.3_

- [x] 17. Integration test stubs
  - [x] 17.1 Write integration test stubs in `tests/integration/test_radius_flow.py`
    - Stub (marked `@pytest.mark.skip`) for: full rlm_rest flow, rlm_sql view query and post-auth, concurrent voucher validation atomicity, audit log completeness after each validation
    - Each stub contains a docstring describing the test intent and the Docker Compose setup required
    - _Requirements: 10.1, 10.5, 4.3_

  - [x] 17.2 Write integration test stubs in `tests/integration/test_admin_api.py`
    - Stub for: login → generate → list → revoke → attempt validate (should reject)
    - Stub for: 60-minute idle session timeout end-to-end
    - _Requirements: 6.1, 6.11_

  - [x] 17.3 Write integration test stubs in `tests/integration/test_audit_log.py`
    - Stub for: verify every radius_validate call creates one audit_log entry regardless of outcome
    - Stub for: verify that a DB failure during audit log write does not affect the validation response
    - _Requirements: 7.2, 7.5_

- [x] 18. Checkpoint — full test suite passes
  - Run `pytest tests/unit/ tests/property/ -v --tb=short`. All non-skipped tests must pass. Ask the user if questions arise.

- [x] 19. README documentation
  - [x] 19.1 Write `README.md` — Windows development setup (Steps 1–9)
    - Step-by-step: install Python 3.11+, create venv, `pip install -r requirements.txt`, create PostgreSQL user/database, copy `.env.example` to `.env` and fill in values, `alembic upgrade head`, `uvicorn app.main:app --reload`, visit `http://localhost:8000/admin/login`
    - FreeRADIUS on Windows: WSL-based FreeRADIUS setup, copy config files from `freeradius/`, create symlinks in `mods-enabled/` and `sites-enabled/`
    - _Requirements: 12.6_

  - [x] 19.2 Write Linux VPS production setup section in `README.md`
    - Steps: install system packages, create `voucher` system user, deploy code, configure `.env` with production values (`APP_ENV=production`), run migrations, create systemd service, configure nginx from `nginx/voucher.conf`, obtain Let's Encrypt certificate with certbot
    - _Requirements: 12.6_

  - [x] 19.3 Write FreeRADIUS configuration guide section in `README.md`
    - Explain both rlm_rest and rlm_sql paths side-by-side: what each does, when to prefer one over the other (note the TOCTOU caveat for rlm_sql)
    - Provide complete step-by-step for each: copy config files, enable modules and virtual server (symlinks), restart FreeRADIUS
    - _Requirements: 10.6, 12.6_

  - [x] 19.4 Write walled garden configuration table in `README.md`
    - Table with four rows (one per Req 8.1–8.4): hostname/IP, ports, protocol, reason needed
    - Example Aruba CLI commands to add each entry to the walled garden
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 20. Final checkpoint — full system review
  - Run `pytest tests/unit/ tests/property/ -v`. Verify `alembic upgrade head` and `alembic downgrade base` complete cleanly. Confirm all files in the directory structure from the design exist. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP, but they validate the 21 correctness properties from the design document
- Each task references specific requirements for traceability — format `X.Y` maps to `Requirement X, Criterion Y` in `requirements.md`
- The 21 property tests are distributed across tasks 4, 5, 7, 9, 10, and 12 — close to the code they test
- All timestamps are UTC throughout; never use `datetime.now()`, always `datetime.utcnow()` or timezone-aware equivalents
- The `/radius/validate` endpoint MUST always return HTTP 200 (even for reject) — this is a deliberate design decision so rlm_rest processes the JSON body
- The `SELECT ... FOR UPDATE` in the validate endpoint is the sole mechanism preventing double-use; do not remove it
- FreeRADIUS config files under `freeradius/` are templates — IP addresses and shared secrets use placeholder values and must be filled in per-deployment
- Integration tests under `tests/integration/` require a running PostgreSQL and FreeRADIUS; they are stubbed and marked `@pytest.mark.skip` for CI without those services

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4", "2.1", "2.2", "2.3"] },
    { "id": 2, "tasks": ["2.4", "2.5", "2.6", "2.7", "2.8"] },
    { "id": 3, "tasks": ["4.1", "5.1", "6.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "5.2", "5.3"] },
    { "id": 5, "tasks": ["4.4", "4.5", "4.6", "4.7", "4.8"] },
    { "id": 6, "tasks": ["4.9", "4.10", "4.11", "7.1", "8.1", "8.2", "8.3"] },
    { "id": 7, "tasks": ["7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "9.1"] },
    { "id": 8, "tasks": ["9.2", "9.3", "9.4", "10.1"] },
    { "id": 9, "tasks": ["9.5", "9.6", "10.2", "10.3", "12.1"] },
    { "id": 10, "tasks": ["12.2", "12.3", "13.1"] },
    { "id": 11, "tasks": ["13.2", "13.3", "13.4"] },
    { "id": 12, "tasks": ["13.5", "14.1", "14.2", "14.3", "14.4", "15.1"] },
    { "id": 13, "tasks": ["16.1", "16.2", "16.3", "16.4"] },
    { "id": 14, "tasks": ["17.1", "17.2", "17.3"] },
    { "id": 15, "tasks": ["19.1", "19.2", "19.3", "19.4"] }
  ]
}
```
