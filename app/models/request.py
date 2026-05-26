from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    role: str
    content: str


@dataclass
class ChatRequest:
    model: str
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None

    @classmethod
    def from_dict(cls, data: dict) -> ChatRequest:
        if "model" not in data:
            raise ValueError("Missing required field: model")
        if "messages" not in data:
            raise ValueError("Missing required field: messages")

        messages = [
            Message(role=m["role"], content=m["content"])
            for m in data["messages"]
        ]
        return cls(
            model=data["model"],
            messages=messages,
            stream=data.get("stream", False),
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
            top_p=data.get("top_p"),
        )

    @property
    def system_prompt(self) -> str | None:
        for m in self.messages:
            if m.role == "system":
                return m.content
        return None

    @property
    def non_system_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role != "system"]
