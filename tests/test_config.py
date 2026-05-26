import os
import pytest
from app.config import load_config, GatewayConfig


def test_load_config_from_yaml(tmp_path):
    yaml_path = tmp_path / "gateway.yaml"
    yaml_path.write_text("""
server:
  host: "127.0.0.1"
  port: 9000
routes:
  - pattern: "gpt-*"
    provider: openai
aliases: {}
keypools: {}
fallbacks: []
tenants: []
pricing: {}
logging:
  access_log: "data/access.log"
  error_log: "data/error.log"
  retention_days: 10
provider_base_urls:
  openrouter: "https://openrouter.ai/api/v1"
""")
    config = load_config(str(yaml_path))
    assert isinstance(config, GatewayConfig)
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 9000
    assert len(config.routes) == 1
    assert config.routes[0].pattern == "gpt-*"
    assert config.provider_base_urls["openrouter"] == "https://openrouter.ai/api/v1"


def test_load_config_env_override(tmp_path, monkeypatch):
    yaml_path = tmp_path / "gateway.yaml"
    yaml_path.write_text("""
server:
  host: "0.0.0.0"
  port: 8000
routes: []
aliases: {}
keypools: {}
fallbacks: []
tenants: []
pricing: {}
logging:
  access_log: "data/access.log"
  error_log: "data/error.log"
  retention_days: 10
""")
    monkeypatch.setenv("GATEWAY_PORT", "9000")
    config = load_config(str(yaml_path))
    assert config.server.port == 9000


def test_load_config_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/gateway.yaml")
