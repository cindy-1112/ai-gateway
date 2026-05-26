from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class Choice:
    index: int
    message_content: str
    finish_reason: str | None


@dataclass
class ChatResponse:
    id: str
    model: str
    choices: list[Choice]
    usage: Usage | None = None


@dataclass
class StreamDelta:
    id: str
    model: str
    delta_content: str | None = None
    finish_reason: str | None = None
    usage: Usage | None = None

    def to_sse_chunk(self) -> bytes:
        delta = {}
        if self.delta_content is not None:
            delta["content"] = self.delta_content
        if self.finish_reason is not None:
            delta["finish_reason"] = self.finish_reason

        chunk = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": self.finish_reason,
                }
            ],
        }
        if self.usage is not None:
            chunk["usage"] = {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
            }
        return f"data: {json.dumps(chunk)}\n\n".encode()


def make_done_chunk() -> bytes:
    return b"data: [DONE]\n\n"
