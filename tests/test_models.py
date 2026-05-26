import pytest
from app.models.request import ChatRequest, Message
from app.models.response import ChatResponse, Choice, Usage, StreamDelta


def test_chat_request_from_openai_format():
    data = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ],
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 4096,
    }
    req = ChatRequest.from_dict(data)
    assert req.model == "gpt-4o"
    assert len(req.messages) == 2
    assert req.messages[0].role == "system"
    assert req.stream is True
    assert req.temperature == 0.7


def test_chat_request_missing_model():
    data = {"messages": [{"role": "user", "content": "Hi"}]}
    with pytest.raises(ValueError):
        ChatRequest.from_dict(data)


def test_chat_request_missing_messages():
    data = {"model": "gpt-4o"}
    with pytest.raises(ValueError):
        ChatRequest.from_dict(data)


def test_stream_delta_format():
    delta = StreamDelta(
        id="chatcmpl-123",
        model="gpt-4o",
        delta_content="Hello",
        finish_reason=None,
    )
    chunk = delta.to_sse_chunk()
    assert b"data:" in chunk
    assert b"Hello" in chunk


def test_usage_model():
    usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    assert usage.total_tokens == 150
