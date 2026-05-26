import pytest
from app.db.database import Database
from app.db.models import UsageRecord


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.init()
    yield database
    await database.close()


async def test_database_init_creates_tables(db):
    async with db.session() as session:
        result = await session.execute(UsageRecord.__table__.select())
        rows = result.fetchall()
        assert rows == []


async def test_insert_and_query_usage(db):
    record = UsageRecord(
        request_id="req-001",
        tenant="default",
        model="gpt-4o",
        provider="openai",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=0.001,
    )
    async with db.session() as session:
        session.add(record)
        await session.commit()

    async with db.session() as session:
        result = await session.execute(
            UsageRecord.__table__.select().where(
                UsageRecord.request_id == "req-001"
            )
        )
        row = result.fetchone()
        assert row is not None
        assert row.tenant == "default"
        assert row.total_tokens == 150


async def test_cleanup_old_records(db):
    from datetime import datetime, timedelta

    old_record = UsageRecord(
        request_id="req-old",
        tenant="default",
        model="gpt-4o",
        provider="openai",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=0.001,
        created_at=datetime.utcnow() - timedelta(days=15),
    )
    new_record = UsageRecord(
        request_id="req-new",
        tenant="default",
        model="gpt-4o",
        provider="openai",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=0.001,
    )
    async with db.session() as session:
        session.add(old_record)
        session.add(new_record)
        await session.commit()

    await db.cleanup_old_records(days=10)

    async with db.session() as session:
        result = await session.execute(UsageRecord.__table__.select())
        rows = result.fetchall()
        assert len(rows) == 1
        assert rows[0].request_id == "req-new"
