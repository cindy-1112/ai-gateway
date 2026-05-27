from __future__ import annotations

import json
import datetime
from typing import Any

from app.time_utils import beijing_now, iso_beijing, parse_beijing_datetime


class AccessLogger:
    def __init__(self, log_path: str, db=None):
        self._file = open(log_path, "a", encoding="utf-8")
        self._db = db

    async def _write_db(self, entry: dict[str, Any]) -> None:
        if self._db is None:
            return
        from app.db.models import RequestLogRecord

        created_at_raw = str(entry.get("timestamp") or "")
        created_at = beijing_now()
        if created_at_raw:
            try:
                created_at = parse_beijing_datetime(created_at_raw)
            except ValueError:
                pass
        async with self._db.session() as session:
            session.add(
                RequestLogRecord(
                    request_id=entry["request_id"],
                    tenant=entry["tenant"],
                    user_id=entry.get("user_id"),
                    username=entry.get("username"),
                    user_api_key_prefix=entry.get("user_api_key_prefix"),
                    model_binding_id=entry.get("model_binding_id"),
                    model=entry["model"],
                    provider=entry["provider"],
                    api_key_suffix=entry.get("api_key_suffix"),
                    status=int(entry["status"]),
                    latency_ms=int(entry["latency_ms"]),
                    prompt_tokens=int(entry.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(entry.get("completion_tokens", 0) or 0),
                    total_tokens=int(entry.get("total_tokens", 0) or 0),
                    stream=1 if entry.get("stream") else 0,
                    created_at=created_at,
                )
            )
            await session.commit()

    async def log(
        self,
        request_id: str,
        tenant: str,
        model: str,
        provider: str,
        api_key_suffix: str,
        status: int,
        latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        stream: bool,
        user_id: int | None = None,
        username: str | None = None,
        user_api_key_prefix: str | None = None,
        model_binding_id: int | None = None,
    ) -> None:
        entry = {
            "timestamp": iso_beijing(beijing_now()),
            "request_id": request_id,
            "tenant": tenant,
            "user_id": user_id,
            "username": username,
            "user_api_key_prefix": user_api_key_prefix,
            "model_binding_id": model_binding_id,
            "model": model,
            "provider": provider,
            "api_key_suffix": api_key_suffix,
            "status": status,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "stream": stream,
        }
        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()
        await self._write_db(entry)

    def close(self) -> None:
        self._file.close()
