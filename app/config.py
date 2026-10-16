"""
app/config.py — Environment variable loading with startup validation.

Loads a .env file when present (development convenience), validates that all
required variables are set, and exposes typed settings as module-level
constants for use throughout the application.
"""

import os
import sys

# Load .env file when present (python-dotenv; no-op if not installed or file absent)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover — dotenv is in requirements.txt; missing only in bare envs
    pass

# ---------------------------------------------------------------------------
# Startup validation — required variables
# ---------------------------------------------------------------------------

REQUIRED_VARS = [
    "DATABASE_URL",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "JWT_SECRET_KEY",
    "RADIUS_SHARED_SECRET",
]

missing = [var for var in REQUIRED_VARS if not os.getenv(var)]
if missing:
    print(
        f"ERROR: Missing required environment variables: {', '.join(missing)}",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Typed settings (required)
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.environ["DATABASE_URL"]
ADMIN_USERNAME: str = os.environ["ADMIN_USERNAME"]
ADMIN_PASSWORD: str = os.environ["ADMIN_PASSWORD"]
JWT_SECRET_KEY: str = os.environ["JWT_SECRET_KEY"]
RADIUS_SHARED_SECRET: str = os.environ["RADIUS_SHARED_SECRET"]

# ---------------------------------------------------------------------------
# Typed settings (optional with defaults)
# ---------------------------------------------------------------------------

APP_ENV: str = os.getenv("APP_ENV", "development")
APP_PORT: int = int(os.getenv("APP_PORT", "8000"))

JWT_EXPIRE_SECONDS: int = int(os.getenv("JWT_EXPIRE_SECONDS", "3600"))  # 60 minutes

RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))  # requests per window
RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))      # window in seconds

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
