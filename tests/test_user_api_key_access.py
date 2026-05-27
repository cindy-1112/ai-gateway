import os

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import (
    GatewayConfig,
    KeyPoolConfig,
    LoggingConfig,
    QuotaConfig,
    RateLimitConfig,
    RouteRule,
    TenantConfig,
)
from app.main import create_app
from app.models.request import ChatRequest
from app.providers.base import BaseProvider


@pytest.fixture
async def user_key_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    os.environ["TEST_DB_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["ADMIN_PASSWORD"] = "admin123456"

    async def fake_chat(self: BaseProvider, request: ChatRequest, api_key: str) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": request.model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    monkeypatch.setattr(BaseProvider, "chat", fake_chat)

    config = GatewayConfig(
        routes=[RouteRule(pattern="gpt-*", provider="openai")],
        aliases={"user-model": "gpt-4o"},
        keypools={"openai": KeyPoolConfig(keys=["sk-test-provider-key"], strategy="round-robin")},
        tenants=[
            TenantConfig(
                name="default",
                api_key="gw-default-test-key",
                rate_limit=RateLimitConfig(rpm=100, tpm=100000),
                quota=QuotaConfig(daily_tokens=1000000, monthly_tokens=10000000),
            )
        ],
        logging=LoggingConfig(
            access_log=str(tmp_path / "access.log"),
            error_log=str(tmp_path / "error.log"),
        ),
    )
    app = create_app(config)
    lifespan_ctx = app.router.lifespan_context(app)
    await lifespan_ctx.__aenter__()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await lifespan_ctx.__aexit__(None, None, None)
    del os.environ["TEST_DB_URL"]
    del os.environ["ADMIN_PASSWORD"]


async def register_and_login_user(client: AsyncClient, username: str) -> None:
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": username},
    )
    assert code_response.status_code == 200

    register_response = await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": username,
            "password": "password123",
            "code": code_response.json()["debug_code"],
        },
    )
    assert register_response.status_code == 200

    login_response = await client.post(
        "/admin/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert login_response.status_code == 200


async def test_user_api_key_requires_active_bound_model(user_key_client):
    client = user_key_client
    username = "bound-user@example.com"

    await register_and_login_user(client, username)

    request_response = await client.post("/admin/api/model-requests", json={"alias": "user-model"})
    assert request_response.status_code == 200
    request_id = request_response.json()["request"]["id"]

    key_response = await client.post("/admin/api/user-api-keys", json={"name": "test key"})
    assert key_response.status_code == 200
    user_api_key = key_response.json()["api_key"]

    no_binding_response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {user_api_key}"},
        json={"model": "user-model", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert no_binding_response.status_code == 403
    assert "model binding" in no_binding_response.json()["detail"]

    admin_login = await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": "admin123456"},
    )
    assert admin_login.status_code == 200

    approve_response = await client.post(f"/admin/api/model-requests/{request_id}/approve")
    assert approve_response.status_code == 200

    wrong_model_response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {user_api_key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert wrong_model_response.status_code == 403
    assert "not bound" in wrong_model_response.json()["detail"]

    bound_alias_response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {user_api_key}"},
        json={"model": "user-model", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert bound_alias_response.status_code == 200
    assert bound_alias_response.json()["model"] == "gpt-4o"
