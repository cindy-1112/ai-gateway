import os
TEST_ADMIN_PASSWORD = "test-admin" + "-credential"
TEST_TENANT_KEY = "${GATEWAY_DEFAULT_API_KEY}"
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.config import load_config


@pytest.fixture
async def client(tmp_path):
    db_path = tmp_path / "test.db"
    os.environ["TEST_DB_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["ADMIN_PASSWORD"] = TEST_ADMIN_PASSWORD
    config = load_config("config/gateway.example.yaml")
    app = create_app(config)
    # Manually run lifespan startup (httpx ASGITransport doesn't trigger ASGI lifespan)
    lifespan_ctx = app.router.lifespan_context(app)
    await lifespan_ctx.__aenter__()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await lifespan_ctx.__aexit__(None, None, None)
    del os.environ["TEST_DB_URL"]
    os.environ.pop("ADMIN_PASSWORD", None)


async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_chat_completions_missing_auth(client):
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 401


async def test_chat_completions_invalid_key(client):
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer invalid-key"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 401


async def test_usage_endpoint(client):
    response = await client.get(
        "/v1/usage",
        headers={"Authorization": "Bearer ${GATEWAY_DEFAULT_API_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "daily_tokens_used" in data
    assert "daily_tokens_limit" in data


async def test_keys_status_endpoint(client):
    response = await client.get(
        "/v1/keys/status",
        headers={"Authorization": "Bearer ${GATEWAY_DEFAULT_API_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


async def test_usage_summary_endpoint(client):
    response = await client.get(
        "/v1/usage/summary?period=today",
        headers={"Authorization": "Bearer ${GATEWAY_DEFAULT_API_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "total_tokens" in data
    assert "total_cost_usd" in data
    assert "by_model" in data


async def test_usage_events_requires_auth(client):
    response = await client.get("/v1/usage/events")
    assert response.status_code == 401


async def test_logs_endpoint(client):
    response = await client.get(
        "/v1/logs?limit=10",
        headers={"Authorization": "Bearer ${GATEWAY_DEFAULT_API_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)


async def test_get_config_endpoint(client):
    response = await client.get(
        "/v1/config",
        headers={"Authorization": "Bearer ${GATEWAY_DEFAULT_API_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "routes" in data
    assert "keypools" in data
    for pool in data["keypools"].values():
        assert all(key == "****" for key in pool["keys"])


async def test_admin_can_reveal_plain_keypool_keys(client):
    await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    response = await client.get("/v1/config/keypools/deepseek/keys")
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "deepseek"
    assert data["keys"]
    assert data["keys"][0] != "****"


async def test_regular_user_cannot_reveal_plain_keypool_keys(client):
    target = "no-key-reveal@example.com"
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": target},
    )
    await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": target,
            "password": "password123",
            "code": code_response.json()["debug_code"],
        },
    )
    await client.post(
        "/admin/api/auth/login",
        json={"username": target, "password": "password123"},
    )
    response = await client.get("/v1/config/keypools/deepseek/keys")
    assert response.status_code == 403


async def test_admin_can_list_admin_users(client):
    await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    response = await client.get("/admin/api/users")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert any(user["username"] == "admin" for user in data["items"])
    assert {"id", "username", "role", "tenant", "created_at"} <= set(data["items"][0])


async def test_regular_user_cannot_list_admin_users(client):
    target = "no-user-list@example.com"
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": target},
    )
    await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": target,
            "password": "password123",
            "code": code_response.json()["debug_code"],
        },
    )
    await client.post(
        "/admin/api/auth/login",
        json={"username": target, "password": "password123"},
    )
    response = await client.get("/admin/api/users")
    assert response.status_code == 403


async def test_admin_can_create_update_delete_user(client):
    await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    create_response = await client.post(
        "/admin/api/users",
        json={
            "username": "managed@example.com",
            "password": "password123",
            "email": "managed@example.com",
            "role": "user",
            "tenant": "tenant-a",
        },
    )
    assert create_response.status_code == 200
    user = create_response.json()["user"]
    assert user["username"] == "managed@example.com"
    assert user["tenant"] == "tenant-a"

    update_response = await client.put(
        f"/admin/api/users/{user['id']}",
        json={
            "username": "managed2@example.com",
            "password": "",
            "email": "managed2@example.com",
            "role": "operator",
            "tenant": "tenant-b",
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()["user"]
    assert updated["username"] == "managed2@example.com"
    assert updated["role"] == "operator"
    assert updated["tenant"] == "tenant-b"

    delete_response = await client.delete(f"/admin/api/users/{user['id']}")
    assert delete_response.status_code == 200
    list_response = await client.get("/admin/api/users")
    assert all(item["id"] != user["id"] for item in list_response.json()["items"])


async def test_admin_cannot_delete_self(client):
    await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    me = await client.get("/admin/api/auth/me")
    user_id = me.json()["user"]["id"]
    response = await client.delete(f"/admin/api/users/{user_id}")
    assert response.status_code == 400


async def test_operator_module_permissions(client):
    await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    create_response = await client.post(
        "/admin/api/users",
        json={
            "username": "operator@example.com",
            "password": "password123",
            "email": "operator@example.com",
            "role": "operator",
            "tenant": "default",
        },
    )
    assert create_response.status_code == 200

    await client.post("/admin/api/auth/logout")
    await client.post(
        "/admin/api/auth/login",
        json={"username": "operator@example.com", "password": "password123"},
    )

    me = await client.get("/admin/api/auth/me")
    assert me.json()["user"]["modules"] == ["keys", "usage", "logs"]

    keys = await client.get("/v1/keys/status")
    assert keys.status_code == 200
    config = await client.get("/v1/config")
    assert config.status_code == 403
    users = await client.get("/admin/api/users")
    assert users.status_code == 403


async def test_custom_user_module_permissions_override_role(client):
    await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    create_response = await client.post(
        "/admin/api/users",
        json={
            "username": "custom-modules@example.com",
            "password": "password123",
            "email": "custom-modules@example.com",
            "role": "user",
            "tenant": "default",
            "module_overrides": ["keys"],
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["user"]["modules"] == ["keys"]
    assert create_response.json()["user"]["module_overrides"] == ["keys"]

    await client.post("/admin/api/auth/logout")
    await client.post(
        "/admin/api/auth/login",
        json={"username": "custom-modules@example.com", "password": "password123"},
    )

    me = await client.get("/admin/api/auth/me")
    assert me.json()["user"]["modules"] == ["keys"]
    keys = await client.get("/v1/keys/status")
    assert keys.status_code == 200
    usage = await client.get("/v1/usage/summary")
    assert usage.status_code == 403


async def test_put_config_endpoint(client, tmp_path):
    import os
    config_path = tmp_path / "gateway_test.yaml"
    os.environ["GATEWAY_CONFIG_PATH"] = str(config_path)
    try:
        new_config = {
            "server": {"host": "0.0.0.0", "port": 8000},
            "routes": [{"pattern": "gpt-*", "provider": "openai"}],
            "aliases": {"fast": "gpt-4o-mini"},
            "keypools": {},
            "fallbacks": [],
            "tenants": [
                {
                    "name": "default",
                    "api_key": "****",
                    "rate_limit": {"rpm": 60, "tpm": 100000},
                    "quota": {"daily_tokens": 500000, "monthly_tokens": 10000000},
                }
            ],
            "pricing": {},
            "logging": {
                "access_log": "data/access.log",
                "error_log": "data/error.log",
                "retention_days": 10,
            },
        }
        response = await client.put(
            "/v1/config",
            headers={"Authorization": "Bearer ${GATEWAY_DEFAULT_API_KEY}"},
            json=new_config,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
    finally:
        del os.environ["GATEWAY_CONFIG_PATH"]


async def test_admin_page(client):
    response = await client.get("/admin/")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


async def test_admin_login_page(client):
    response = await client.get("/admin/login")
    assert response.status_code == 200
    assert "登录 AI Gateway".encode("utf-8") in response.content


async def test_send_email_verification_code(client):
    response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": "user@example.com"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["channel"] == "email"
    assert data["target"] == "user@example.com"
    assert len(data["debug_code"]) == 6
    assert data["debug_code"].isdigit()


async def test_send_phone_verification_code(client):
    response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "phone", "target": "13800138000"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["channel"] == "phone"
    assert data["target"] == "13800138000"
    assert len(data["debug_code"]) == 6


async def test_send_verification_code_rejects_invalid_target(client):
    response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": "not-an-email"},
    )
    assert response.status_code == 400


async def test_send_verification_code_rate_limit(client):
    payload = {"channel": "email", "target": "limited@example.com"}
    first = await client.post("/admin/api/auth/verification-code", json=payload)
    second = await client.post("/admin/api/auth/verification-code", json=payload)
    assert first.status_code == 200
    assert second.status_code == 429


async def test_register_with_email_verification_code(client):
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": "new-user@example.com"},
    )
    code = code_response.json()["debug_code"]

    response = await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": "new-user@example.com",
            "password": "password123",
            "code": code,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["username"] == "new-user@example.com"
    assert data["role"] == "user"


async def test_register_with_phone_verification_code(client):
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "phone", "target": "13900139000"},
    )
    code = code_response.json()["debug_code"]

    response = await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "phone",
            "target": "13900139000",
            "password": "password123",
            "code": code,
        },
    )

    assert response.status_code == 200
    assert response.json()["username"] == "13900139000"


async def test_register_rejects_wrong_code(client):
    await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": "wrong-code@example.com"},
    )
    response = await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": "wrong-code@example.com",
            "password": "password123",
            "code": "000000",
        },
    )
    assert response.status_code == 400


async def test_register_rejects_duplicate_account(client):
    target = "duplicate@example.com"
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": target},
    )
    payload = {
        "channel": "email",
        "target": target,
        "password": "password123",
        "code": code_response.json()["debug_code"],
    }
    first = await client.post("/admin/api/auth/register", json=payload)
    second = await client.post("/admin/api/auth/register", json=payload)
    assert first.status_code == 200
    assert second.status_code in {400, 409}


async def test_login_default_admin(client):
    response = await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["user"]["username"] == "admin"
    assert data["user"]["role"] == "admin"
    assert "gw_admin_session" in response.cookies


async def test_login_registered_user(client):
    target = "login-user@example.com"
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": target},
    )
    await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": target,
            "password": "password123",
            "code": code_response.json()["debug_code"],
        },
    )

    response = await client.post(
        "/admin/api/auth/login",
        json={"username": target, "password": "password123"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["username"] == target


async def test_login_rejects_wrong_password(client):
    response = await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert response.status_code == 401


async def test_admin_session_allows_admin_page_and_management_api(client):
    login = await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    assert login.status_code == 200

    page = await client.get("/admin/")
    me = await client.get("/admin/api/auth/me")
    keys = await client.get("/v1/keys/status")

    assert page.status_code == 200
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "admin"
    assert keys.status_code == 200


async def test_regular_user_cannot_access_config_or_keys(client):
    target = "limited-user@example.com"
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": target},
    )
    await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": target,
            "password": "password123",
            "code": code_response.json()["debug_code"],
        },
    )
    login = await client.post(
        "/admin/api/auth/login",
        json={"username": target, "password": "password123"},
    )
    assert login.status_code == 200

    keys = await client.get("/v1/keys/status")
    config = await client.get("/v1/config")

    assert keys.status_code == 403
    assert config.status_code == 403


async def test_regular_user_usage_summary_is_tenant_scoped(client):
    from app.db.models import UsageRecord
    from app.db.database import Database
    import os

    db = Database(os.environ["TEST_DB_URL"])
    await db.init()
    async with db.session() as session:
        session.add(
            UsageRecord(
                request_id="default-usage",
                tenant="default",
                model="gpt-4o",
                provider="openai",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                cost_usd=0.001,
            )
        )
        session.add(
            UsageRecord(
                request_id="other-usage",
                tenant="other",
                model="gpt-4o",
                provider="openai",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                cost_usd=0.01,
            )
        )
        await session.commit()
    await db.close()

    target = "tenant-user@example.com"
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": target},
    )
    await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": target,
            "password": "password123",
            "code": code_response.json()["debug_code"],
        },
    )
    await client.post(
        "/admin/api/auth/login",
        json={"username": target, "password": "password123"},
    )

    response = await client.get("/v1/usage/summary?period=today")

    assert response.status_code == 200
    data = response.json()
    assert data["total_tokens"] == 15
    assert "default" in data["by_tenant"]
    assert "other" not in data["by_tenant"]


async def test_regular_user_logs_are_tenant_scoped(client):
    import json
    from app.config import load_config

    log_path = load_config("config/gateway.example.yaml").logging.access_log
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"request_id": "default-log", "tenant": "default"}) + "\n")
        f.write(json.dumps({"request_id": "other-log", "tenant": "other"}) + "\n")

    target = "logs-user@example.com"
    code_response = await client.post(
        "/admin/api/auth/verification-code",
        json={"channel": "email", "target": target},
    )
    await client.post(
        "/admin/api/auth/register",
        json={
            "channel": "email",
            "target": target,
            "password": "password123",
            "code": code_response.json()["debug_code"],
        },
    )
    await client.post(
        "/admin/api/auth/login",
        json={"username": target, "password": "password123"},
    )

    response = await client.get("/v1/logs")

    assert response.status_code == 200
    request_ids = [item["request_id"] for item in response.json()["items"]]
    assert request_ids == ["default-log"]


async def test_admin_logout_revokes_session(client):
    await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    logout = await client.post("/admin/api/auth/logout")
    me = await client.get("/admin/api/auth/me")

    assert logout.status_code == 200
    assert me.status_code == 401


async def test_admin_page_has_content(client):
    await client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    response = await client.get("/admin/")
    assert response.status_code == 200
    assert b"AI Gateway" in response.content


async def test_logs_endpoint_structure(client):
    response = await client.get(
        "/v1/logs",
        headers={"Authorization": "Bearer ${GATEWAY_DEFAULT_API_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["total"], int)
    assert isinstance(data["items"], list)


async def test_config_endpoint_structure(client):
    response = await client.get(
        "/v1/config",
        headers={"Authorization": "Bearer ${GATEWAY_DEFAULT_API_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "routes" in data
    assert "aliases" in data
    assert "keypools" in data
    assert "tenants" in data
    assert "pricing" in data
