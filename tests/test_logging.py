import json
import pytest
import asyncio
from app.logging.access import AccessLogger
from app.logging.usage import UsageEventBroker, UsageLogger
from app.db.database import Database


def test_access_logger_writes_json(tmp_path):
    log_path = tmp_path / "access.log"
    logger = AccessLogger(str(log_path))
    logger.log(
        request_id="req-001",
        tenant="default",
        model="gpt-4o",
        provider="openai",
        api_key_suffix="xxx1",
        status=200,
        latency_ms=100,
        prompt_tokens=50,
        completion_tokens=30,
        total_tokens=80,
        stream=True,
    )
    logger.close()

    content = log_path.read_text()
    data = json.loads(content.strip())
    assert data["request_id"] == "req-001"
    assert data["model"] == "gpt-4o"
    assert data["total_tokens"] == 80


async def test_usage_logger_records_to_db(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.init()
    logger = UsageLogger(db)
    await logger.record(
        request_id="req-001",
        tenant="default",
        model="gpt-4o",
        provider="openai",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=0.00125,
    )
    await db.close()

    db2 = Database(f"sqlite+aiosqlite:///{db_path}")
    await db2.init()
    from sqlalchemy import select
    from app.db.models import UsageRecord
    async with db2.session() as session:
        result = await session.execute(select(UsageRecord))
        records = result.scalars().all()
        assert len(records) == 1
        assert records[0].request_id == "req-001"
        assert records[0].cost_usd == 0.00125
    await db2.close()


async def test_usage_logger_publishes_event(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.init()
    broker = UsageEventBroker()
    queue = broker.subscribe()
    logger = UsageLogger(db, broker)

    await logger.record(
        request_id="req-event",
        tenant="default",
        model="gemini-2.5-flash",
        provider="gemini",
        prompt_tokens=12,
        completion_tokens=8,
        total_tokens=20,
        cost_usd=0.0001,
    )

    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event["request_id"] == "req-event"
    assert event["model"] == "gemini-2.5-flash"
    assert event["total_tokens"] == 20

    broker.unsubscribe(queue)
    await db.close()
