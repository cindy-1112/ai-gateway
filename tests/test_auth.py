import pytest
from app.config import TenantConfig, RateLimitConfig, QuotaConfig
from app.auth.middleware import AuthMiddleware


@pytest.fixture
def middleware():
    tenants = [
        TenantConfig(
            name="alice",
            api_key="gw-alice-key",
            rate_limit=RateLimitConfig(rpm=60, tpm=100000),
            quota=QuotaConfig(daily_tokens=500000, monthly_tokens=10000000),
        ),
        TenantConfig(
            name="bob",
            api_key="gw-bob-key",
            rate_limit=RateLimitConfig(rpm=30, tpm=50000),
            quota=QuotaConfig(daily_tokens=200000, monthly_tokens=5000000),
        ),
    ]
    return AuthMiddleware(tenants)


def test_authenticate_valid_key(middleware):
    tenant = middleware.authenticate("gw-alice-key")
    assert tenant.name == "alice"


def test_authenticate_another_valid_key(middleware):
    tenant = middleware.authenticate("gw-bob-key")
    assert tenant.name == "bob"


def test_authenticate_invalid_key(middleware):
    with pytest.raises(PermissionError, match="Invalid API key"):
        middleware.authenticate("gw-invalid-key")


def test_authenticate_empty_key(middleware):
    with pytest.raises(PermissionError, match="Missing API key"):
        middleware.authenticate("")


def test_extract_bearer_token(middleware):
    token = middleware.extract_bearer("Bearer gw-alice-key")
    assert token == "gw-alice-key"


def test_extract_bearer_no_prefix(middleware):
    with pytest.raises(PermissionError, match="Missing Bearer token"):
        middleware.extract_bearer("Basic abc123")


def test_extract_bearer_empty_header(middleware):
    with pytest.raises(PermissionError, match="Missing Bearer token"):
        middleware.extract_bearer("")
