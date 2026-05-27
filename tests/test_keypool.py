import pytest
from app.keypool.pool import KeyPool
from app.keypool.health import KeyHealth, KeyStatus
from app.config import ProviderKeyConfig


def test_round_robin_selection():
    pool = KeyPool(provider="openai", keys=["sk-1", "sk-2", "sk-3"], strategy="round-robin")
    assert pool.select() == "sk-1"
    assert pool.select() == "sk-2"
    assert pool.select() == "sk-3"
    assert pool.select() == "sk-1"


def test_random_selection():
    pool = KeyPool(provider="openai", keys=["sk-1", "sk-2"], strategy="random")
    key = pool.select()
    assert key in ("sk-1", "sk-2")


def test_least_used_selection():
    pool = KeyPool(provider="openai", keys=["sk-1", "sk-2"], strategy="least-used")
    pool.select()  # sk-1, use_count=1
    key = pool.select()  # should pick sk-2 (use_count=0)
    assert key == "sk-2"


def test_skip_rate_limited_key():
    pool = KeyPool(provider="openai", keys=["sk-1", "sk-2"], strategy="round-robin")
    pool.mark_status("sk-1", KeyStatus.RATE_LIMITED, cooldown_seconds=60)
    assert pool.select() == "sk-2"


def test_all_keys_unavailable():
    pool = KeyPool(provider="openai", keys=["sk-1"], strategy="round-robin")
    pool.mark_status("sk-1", KeyStatus.INVALID)
    with pytest.raises(RuntimeError, match="No available keys"):
        pool.select()


def test_mark_invalid_permanent():
    pool = KeyPool(provider="openai", keys=["sk-1", "sk-2"], strategy="round-robin")
    pool.mark_status("sk-1", KeyStatus.INVALID)
    assert pool.get_status()["total"] == 2
    assert pool.get_status()["available"] == 1


def test_key_health_cooldown_expiry():
    health = KeyHealth(key_suffix="sk-1", status=KeyStatus.RATE_LIMITED, cooldown_seconds=0)
    assert health.is_available()


def test_select_key_by_allowed_model():
    pool = KeyPool(
        provider="qwen",
        keys=[
            ProviderKeyConfig(value="sk-qwen-plus", allowed_models=["qwen3.6-plus"]),
            ProviderKeyConfig(value="sk-qwen-max", allowed_models=["qwen-max"]),
        ],
        strategy="round-robin",
    )

    assert pool.select(model="qwen3.6-plus") == "sk-qwen-plus"
    assert pool.select(model="qwen-max") == "sk-qwen-max"


def test_key_without_allowed_models_is_provider_wide():
    pool = KeyPool(
        provider="qwen",
        keys=[
            ProviderKeyConfig(value="sk-provider-wide"),
        ],
        strategy="round-robin",
    )

    assert pool.select(model="qwen3.6-plus") == "sk-provider-wide"


def test_no_key_for_requested_model():
    pool = KeyPool(
        provider="qwen",
        keys=[
            ProviderKeyConfig(value="sk-qwen-max", allowed_models=["qwen-max"]),
        ],
        strategy="round-robin",
    )

    with pytest.raises(RuntimeError, match="model qwen3.6-plus"):
        pool.select(model="qwen3.6-plus")
