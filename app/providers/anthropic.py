from __future__ import annotations

from app.models.request import ChatRequest
from app.providers.base import BaseProvider


class AnthropicProvider(BaseProvider):
    def convert_request(self, request: ChatRequest) -> dict:
        if request.max_tokens is None:
            raise ValueError("Anthropic requires max_tokens")

        result: dict = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.non_system_messages
            ],
        }
        if request.system_prompt:
            result["system"] = request.system_prompt
        if request.stream:
            result["stream"] = True
        if request.temperature is not None:
            result["temperature"] = request.temperature
        if request.top_p is not None:
            result["top_p"] = request.top_p
        return result

    def auth_header(self, api_key: str) -> dict:
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def chat_endpoint(self) -> str:
        return "/v1/messages"
