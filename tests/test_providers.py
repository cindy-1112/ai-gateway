import pytest
from app.models.request import ChatRequest, Message
from app.providers.openai import OpenAIProvider
from app.providers.anthropic import AnthropicProvider
from app.providers.deepseek import DeepSeekProvider


def test_openai_convert_request():
    provider = OpenAIProvider(base_url="https://api.openai.com")
    req = ChatRequest(
        model="gpt-4o",
        messages=[Message(role="user", content="Hello")],
        stream=True,
        temperature=0.7,
    )
    result = provider.convert_request(req)
    assert result["model"] == "gpt-4o"
    assert result["messages"] == [{"role": "user", "content": "Hello"}]
    assert result["stream"] is True
    assert result["temperature"] == 0.7


def test_anthropic_convert_request():
    provider = AnthropicProvider(base_url="https://api.anthropic.com")
    req = ChatRequest(
        model="claude-sonnet-4-6",
        messages=[
            Message(role="system", content="Be helpful"),
            Message(role="user", content="Hello"),
        ],
        stream=True,
        max_tokens=4096,
    )
    result = provider.convert_request(req)
    assert result["model"] == "claude-sonnet-4-6"
    assert result["system"] == "Be helpful"
    assert result["messages"] == [{"role": "user", "content": "Hello"}]
    assert result["max_tokens"] == 4096
    assert result["stream"] is True


def test_anthropic_requires_max_tokens():
    provider = AnthropicProvider(base_url="https://api.anthropic.com")
    req = ChatRequest(
        model="claude-sonnet-4-6",
        messages=[Message(role="user", content="Hello")],
    )
    with pytest.raises(ValueError, match="max_tokens"):
        provider.convert_request(req)


def test_deepseek_convert_request():
    provider = DeepSeekProvider(base_url="https://api.deepseek.com")
    req = ChatRequest(
        model="deepseek-r1",
        messages=[Message(role="user", content="Hello")],
        stream=True,
    )
    result = provider.convert_request(req)
    assert result["model"] == "deepseek-r1"
    assert result["stream"] is True


def test_openai_auth_header():
    provider = OpenAIProvider(base_url="https://api.openai.com")
    header = provider.auth_header("sk-test123")
    assert header == {"Authorization": "Bearer sk-test123"}


def test_anthropic_auth_header():
    provider = AnthropicProvider(base_url="https://api.anthropic.com")
    header = provider.auth_header("sk-ant-test123")
    assert header == {
        "x-api-key": "sk-ant-test123",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
