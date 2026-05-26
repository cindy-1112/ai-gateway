import pytest
from httpx import ASGITransport, AsyncClient
from app.config import load_config, TenantConfig, RateLimitConfig, QuotaConfig, KeyPoolConfig
from app.main import create_app


@pytest.fixture
async def integration_client(tmp_path):
    import os
    db_path = tmp_path / "test.db"
    os.environ["TEST_DB_URL"] = f"sqlite+aiosqlite:///{db_path}"

    config = load_config("config/gateway.example.yaml")
    config.tenants = [
        TenantConfig(
            name="test",
            api_key="gw-test-key",
            rate_limit=RateLimitConfig(rpm=100, tpm=100000),
            quota=QuotaConfig(daily_tokens=1000000, monthly_tokens=10000000),
        )
    ]
    config.keypools = {
        "openai": KeyPoolConfig(keys=["sk-test-fake-key"], strategy="round-robin"),
    }

    app = create_app(config)
    # Manually run lifespan startup (httpx ASGITransport doesn't trigger ASGI lifespan)
    lifespan_ctx = app.router.lifespan_context(app)
    await lifespan_ctx.__aenter__()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await lifespan_ctx.__aexit__(None, None, None)
    del os.environ["TEST_DB_URL"]


async def test_full_request_flow_auth_error(integration_client):
    client = integration_client

    # Auth fails
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 401

    # Auth succeeds but provider call fails (fake key)
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer gw-test-key"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 502

    # Usage endpoint works
    resp = await client.get(
        "/v1/usage",
        headers={"Authorization": "Bearer gw-test-key"},
    )
    assert resp.status_code == 200


async def test_log_retention_cleanup(tmp_path):
    from app.db.database import Database
    from datetime import datetime, timedelta

    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.init()

    from app.db.models import UsageRecord
    old = UsageRecord(
        request_id="old", tenant="t", model="m", provider="p",
        prompt_tokens=1, completion_tokens=1, total_tokens=2,
        cost_usd=0.0, created_at=datetime.utcnow() - timedelta(days=15),
    )
    new = UsageRecord(
        request_id="new", tenant="t", model="m", provider="p",
        prompt_tokens=1, completion_tokens=1, total_tokens=2,
        cost_usd=0.0,
    )
    async with db.session() as session:
        session.add(old)
        session.add(new)
        await session.commit()

    deleted = await db.cleanup_old_records(days=10)
    assert deleted == 1

    from sqlalchemy import select
    async with db.session() as session:
        result = await session.execute(select(UsageRecord))
        records = result.scalars().all()
        assert len(records) == 1
        assert records[0].request_id == "new"

    await db.close()
