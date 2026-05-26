import pytest
from app.config import RouteRule
from app.router.matcher import RouterMatcher


@pytest.fixture
def matcher():
    rules = [
        RouteRule(pattern="gpt-*", provider="openai"),
        RouteRule(pattern="claude-*", provider="anthropic"),
        RouteRule(pattern="deepseek-*", provider="deepseek"),
        RouteRule(pattern="qwen-*", provider="qwen"),
        RouteRule(pattern="*", provider="openai"),
    ]
    aliases = {
        "fast": "gpt-4o-mini",
        "smart": "claude-sonnet-4-6",
    }
    return RouterMatcher(rules, aliases)


def test_match_exact_pattern(matcher):
    provider, model = matcher.match("gpt-4o")
    assert provider == "openai"
    assert model == "gpt-4o"


def test_match_wildcard_pattern(matcher):
    provider, model = matcher.match("claude-opus-4-7")
    assert provider == "anthropic"
    assert model == "claude-opus-4-7"


def test_match_fallback_star(matcher):
    provider, model = matcher.match("unknown-model")
    assert provider == "openai"
    assert model == "unknown-model"


def test_alias_resolution(matcher):
    provider, model = matcher.match("fast")
    assert provider == "openai"
    assert model == "gpt-4o-mini"


def test_alias_resolved_then_routed(matcher):
    provider, model = matcher.match("smart")
    assert provider == "anthropic"
    assert model == "claude-sonnet-4-6"


def test_no_alias_no_match_with_strict():
    rules = [RouteRule(pattern="gpt-*", provider="openai")]
    matcher = RouterMatcher(rules, {}, strict=True)
    with pytest.raises(ValueError, match="No route"):
        matcher.match("unknown-model")
