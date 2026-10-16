"""Property 21: Missing environment variables cause startup failure.

Feature: aruba-voucher-backend, Property 21.

_For any_ non-empty subset S of the required environment variables that are
absent or empty at startup, the application exits with a non-zero exit code and
the error output names each variable in S.

Validates: Requirements 12.2
"""

from __future__ import annotations

import importlib
import io
import os
from contextlib import contextmanager, redirect_stderr

import dotenv
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import app.config

REQUIRED_VARS = [
    "DATABASE_URL",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "JWT_SECRET_KEY",
    "RADIUS_SHARED_SECRET",
]


@contextmanager
def _neutralised_dotenv():
    """Disable .env loading and restore the environment/reload afterwards."""
    original = dotenv.load_dotenv
    saved = {var: os.environ.get(var) for var in REQUIRED_VARS}
    dotenv.load_dotenv = lambda *args, **kwargs: None
    try:
        yield
    finally:
        dotenv.load_dotenv = original
        for var, value in saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        # Restore a fully-valid configuration for every other test module.
        importlib.reload(app.config)


@given(missing=st.sets(st.sampled_from(REQUIRED_VARS), min_size=1))
@settings(max_examples=25, deadline=None)
def test_missing_env_vars_cause_startup_failure(missing):
    with _neutralised_dotenv():
        for var in REQUIRED_VARS:
            if var in missing:
                os.environ.pop(var, None)
            else:
                os.environ[var] = "placeholder-value"

        stderr = io.StringIO()
        with pytest.raises(SystemExit) as exc_info, redirect_stderr(stderr):
            importlib.reload(app.config)

        assert exc_info.value.code != 0
        output = stderr.getvalue()
        for var in missing:
            assert var in output
