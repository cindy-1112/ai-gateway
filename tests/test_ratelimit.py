import time
import pytest
from app.ratelimit.limiter import TokenBucketLimiter


def test_allows_within_capacity():
    limiter = TokenBucketLimiter(capacity=5, refill_rate=1.0)
    for _ in range(5):
        assert limiter.allow() is True


def test_rejects_over_capacity():
    limiter = TokenBucketLimiter(capacity=3, refill_rate=1.0)
    for _ in range(3):
        limiter.allow()
    assert limiter.allow() is False


def test_refills_over_time():
    limiter = TokenBucketLimiter(capacity=3, refill_rate=10.0)
    for _ in range(3):
        limiter.allow()
    assert limiter.allow() is False
    time.sleep(0.2)
    assert limiter.allow() is True


def test_retry_after_seconds():
    limiter = TokenBucketLimiter(capacity=2, refill_rate=10.0)
    limiter.allow()
    limiter.allow()
    result = limiter.allow()
    assert result is False
    retry = limiter.retry_after_seconds()
    assert retry > 0


def test_rpm_limiter():
    limiter = TokenBucketLimiter(capacity=10, refill_rate=10.0 / 60.0)
    for _ in range(10):
        assert limiter.allow() is True
    assert limiter.allow() is False


from app.ratelimit.quota import QuotaManager
from app.db.database import Database


@pytest.fixture
async def quota_manager(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.init()
    manager = QuotaManager(db, daily_limit=1000, monthly_limit=10000)
    yield manager
    await db.close()


async def test_quota_allows_within_limit(quota_manager):
    assert await quota_manager.check_and_reserve("default", 100) is True


async def test_quota_rejects_over_daily_limit(quota_manager):
    assert await quota_manager.check_and_reserve("default", 999) is True
    assert await quota_manager.check_and_reserve("default", 2) is False


async def test_quota_daily_reset(quota_manager):
    await quota_manager.check_and_reserve("default", 800)
    await quota_manager.reset_daily("default")
    assert await quota_manager.check_and_reserve("default", 800) is True


async def test_quota_persists_across_sessions(tmp_path):
    db_path = tmp_path / "test.db"
    db1 = Database(f"sqlite+aiosqlite:///{db_path}")
    await db1.init()
    mgr1 = QuotaManager(db1, daily_limit=1000, monthly_limit=10000)
    await mgr1.check_and_reserve("default", 500)
    await db1.close()

    db2 = Database(f"sqlite+aiosqlite:///{db_path}")
    await db2.init()
    mgr2 = QuotaManager(db2, daily_limit=1000, monthly_limit=10000)
    assert await mgr2.check_and_reserve("default", 400) is True
    assert await mgr2.check_and_reserve("default", 200) is False
    await db2.close()


async def test_quota_get_usage(quota_manager):
    await quota_manager.check_and_reserve("default", 300)
    usage = await quota_manager.get_usage("default")
    assert usage["daily_tokens_used"] == 300
    assert usage["daily_tokens_limit"] == 1000
