from __future__ import annotations

import random
from app.keypool.health import KeyHealth, KeyStatus


class KeyPool:
    def __init__(self, provider: str, keys: list[str], strategy: str = "round-robin"):
        self.provider = provider
        self.strategy = strategy
        self._keys = keys
        self._health: dict[str, KeyHealth] = {}
        self._rr_index = 0

        for key in keys:
            suffix = key[-4:] if len(key) >= 4 else key
            self._health[key] = KeyHealth(key_suffix=suffix)

    def select(self) -> str:
        available = [k for k in self._keys if self._health[k].is_available()]
        if not available:
            raise RuntimeError(f"No available keys for provider {self.provider}")

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
            keys_info.append({
                "suffix": health.key_suffix,
                "status": health.status.value,
                "use_count": health.use_count,
            })
        return {
            "provider": self.provider,
            "total": len(self._keys),
            "available": available,
            "keys": keys_info,
        }
