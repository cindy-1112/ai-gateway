from __future__ import annotations

import json
import datetime


class AccessLogger:
    def __init__(self, log_path: str):
        self._file = open(log_path, "a", encoding="utf-8")

    def log(
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
    ) -> None:
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "request_id": request_id,
            "tenant": tenant,
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

    def close(self) -> None:
        self._file.close()
