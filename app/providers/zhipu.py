from __future__ import annotations

from app.models.request import ChatRequest
from app.providers.base import BaseProvider


class ZhipuProvider(BaseProvider):
    def convert_request(self, request: ChatRequest) -> dict:
        result: dict = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.stream:
            result["stream"] = True
        if request.temperature is not None:
            result["temperature"] = request.temperature
        if request.max_tokens is not None:
            result["max_tokens"] = request.max_tokens
        return result

    def auth_header(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}"}

    def chat_endpoint(self) -> str:
        return "/chat/completions"
