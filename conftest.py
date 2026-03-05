"""
Pytest configuration and fixtures for FastAPI tests.

Sets minimal env vars so Settings load, and provides a TestClient
that uses a no-op lifespan for Redis/DB in unit tests.
"""
import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Set required env vars before app is imported (Settings load at import time)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("SOLIDGATE_PUBLIC_KEY", "test_public_key")
os.environ.setdefault("SOLIDGATE_SECRET_KEY", "test_secret_key")
os.environ.setdefault("MEDUSA_ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("MEDUSA_ADMIN_PASSWORD", "testpass")
os.environ.setdefault("MEDUSA_PUBLISHABLE_KEY", "pk_test")
os.environ.setdefault("SLACK_ALERTS_URL", "https://hooks.slack.com/test")


@pytest.fixture
def client():
    """FastAPI TestClient with mocked Redis lifespan (no real Redis needed)."""
    from app.main import app
    from app.core import redis as redis_module

    async def noop_connect():
        pass

    async def noop_disconnect():
        pass

    with (
        patch.object(redis_module.redis_client, "connect", side_effect=noop_connect),
        patch.object(redis_module.redis_client, "disconnect", side_effect=noop_disconnect),
    ):
        with TestClient(app) as c:
            yield c
