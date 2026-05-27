from __future__ import annotations

import random
from dataclasses import dataclass, field
from app.keypool.health import KeyHealth, KeyStatus


@dataclass
class PoolKey:
    value: str
    allowed_models: list[str] = field(default_factory=list)


class KeyPool:
    def __init__(self, provider: str, keys: list[str | object], strategy: str = "round-robin"):
        self.provider = provider
        self.strategy = strategy
        self._key_configs = [self._normalize_key(key) for key in keys]
        self._keys = [key.value for key in self._key_configs]
        self._health: dict[str, KeyHealth] = {}
        self._rr_index = 0

        for key in self._keys:
            suffix = key[-4:] if len(key) >= 4 else key
            self._health[key] = KeyHealth(key_suffix=suffix)

    def _normalize_key(self, key: str | object) -> PoolKey:
        if isinstance(key, str):
            return PoolKey(value=key)
        value = getattr(key, "value", "")
        allowed_models = getattr(key, "allowed_models", [])
        return PoolKey(value=value, allowed_models=list(allowed_models or []))

    def _model_allowed(self, key: PoolKey, model: str | None) -> bool:
        if not key.allowed_models:
            return True
        if not model:
            return False
        return model in key.allowed_models

    def select(self, model: str | None = None) -> str:
        allowed_values = {
            key.value for key in self._key_configs if self._model_allowed(key, model)
        }
        available = [
            k for k in self._keys
            if k in allowed_values and self._health[k].is_available()
        ]
        if not available:
            scope = f" and model {model}" if model else ""
            raise RuntimeError(f"No available keys for provider {self.provider}{scope}")

        if self.strategy == "round-robin":
            return self._round_robin_select(available)
        elif self.strategy == "least-used":
            return self._least_used_select(available)
        else:
            return random.choice(available)

    def _round_robin_select(self, available: list[str]) -> str:
        for _ in range(len(self._keys)):
            key = self._keys[self._rr_index % len(self._keys)]
            self._rr_index += 1
            if key in available:
                self._health[key].use_count += 1
                self._health[key].last_used_at = __import__("time").monotonic()
                return key
        return available[0]

    def _least_used_select(self, available: list[str]) -> str:
        key = min(available, key=lambda k: self._health[k].use_count)
        self._health[key].use_count += 1
        self._health[key].last_used_at = __import__("time").monotonic()
        return key

    def mark_status(self, key: str, status: KeyStatus, cooldown_seconds: int = 0) -> None:
        if key in self._health:
            self._health[key].mark(status, cooldown_seconds)

    def get_status(self) -> dict:
        available = sum(1 for h in self._health.values() if h.is_available())
        keys_info = []
        for key, health in self._health.items():
            key_config = next((item for item in self._key_configs if item.value == key), None)
            keys_info.append({
                "suffix": health.key_suffix,
                "status": health.status.value,
                "use_count": health.use_count,
                "allowed_models": key_config.allowed_models if key_config else [],
            })
        return {
            "provider": self.provider,
            "total": len(self._keys),
            "available": available,
            "keys": keys_info,
        }
