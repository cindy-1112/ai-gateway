import os
import pytest
from app.config import load_config, GatewayConfig
from app.main import _encrypt_config_secrets, _validate_config_integrity
from app.security.crypto import SecretCryptoError, encrypt_secret


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


def test_load_config_reads_local_dotenv_and_resolves_placeholders(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_DEFAULT_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        """
OPENAI_API_KEY=sk-from-dotenv
GATEWAY_DEFAULT_API_KEY=gw-from-dotenv
""",
        encoding="utf-8",
    )
    yaml_path = tmp_path / "gateway.yaml"
    yaml_path.write_text("""
routes: []
aliases: {}
keypools:
  openai:
    keys:
      - "${OPENAI_API_KEY}"
fallbacks: []
tenants:
  - name: default
    api_key: "${GATEWAY_DEFAULT_API_KEY}"
pricing: {}
""")

    config = load_config(str(yaml_path))

    assert config.keypools["openai"].keys[0].value == "sk-from-dotenv"
    assert config.tenants[0].api_key == "gw-from-dotenv"


def test_env_placeholder_supports_default_value(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTIONAL_PROVIDER_KEY", raising=False)
    yaml_path = tmp_path / "gateway.yaml"
    yaml_path.write_text("""
routes: []
aliases: {}
keypools:
  openai:
    keys:
      - "${OPTIONAL_PROVIDER_KEY:-}"
fallbacks: []
tenants: []
pricing: {}
""")

    config = load_config(str(yaml_path))

    assert config.keypools["openai"].keys[0].value == ""


def test_load_config_decrypts_encrypted_provider_and_tenant_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_SECRET_KEY", "test-secret-key")
    provider_key = encrypt_secret("sk-provider-secret")
    tenant_key = encrypt_secret("gw-tenant-secret")
    yaml_path = tmp_path / "gateway.yaml"
    yaml_path.write_text(f"""
routes: []
aliases: {{}}
keypools:
  openai:
    keys:
      - "{provider_key}"
fallbacks: []
tenants:
  - name: default
    api_key: "{tenant_key}"
pricing: {{}}
""")

    config = load_config(str(yaml_path))

    assert config.keypools["openai"].keys[0].value == "sk-provider-secret"
    assert config.tenants[0].api_key == "gw-tenant-secret"


def test_encrypted_config_requires_gateway_secret_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_SECRET_KEY", "test-secret-key")
    provider_key = encrypt_secret("sk-provider-secret")
    monkeypatch.delenv("GATEWAY_SECRET_KEY", raising=False)
    yaml_path = tmp_path / "gateway.yaml"
    yaml_path.write_text(f"""
routes: []
aliases: {{}}
keypools:
  openai:
    keys:
      - "{provider_key}"
fallbacks: []
tenants: []
pricing: {{}}
""")

    with pytest.raises(SecretCryptoError, match="GATEWAY_SECRET_KEY"):
        load_config(str(yaml_path))


def test_encrypt_config_secrets_before_save(monkeypatch):
    monkeypatch.setenv("GATEWAY_SECRET_KEY", "test-secret-key")
    raw_config = {
        "keypools": {
            "openai": {
                "keys": [
                    "sk-provider-secret",
                    {"value": "sk-model-secret", "allowed_models": ["gpt-4o"]},
                    "${OPENAI_API_KEY}",
                    "",
                ]
            }
        },
        "tenants": [{"name": "default", "api_key": "gw-tenant-secret"}],
    }

    _encrypt_config_secrets(raw_config)

    keys = raw_config["keypools"]["openai"]["keys"]
    assert keys[0].startswith("enc:v1:")
    assert keys[1]["value"].startswith("enc:v1:")
    assert keys[2] == "${OPENAI_API_KEY}"
    assert keys[3] == ""
    assert raw_config["tenants"][0]["api_key"].startswith("enc:v1:")


def test_encrypt_config_secrets_does_not_encrypt_twice(monkeypatch):
    monkeypatch.setenv("GATEWAY_SECRET_KEY", "test-secret-key")
    encrypted = encrypt_secret("sk-provider-secret")
    raw_config = {
        "keypools": {"openai": {"keys": [encrypted]}},
        "tenants": [{"name": "default", "api_key": "${GATEWAY_DEFAULT_API_KEY:-gw-default-key}"}],
    }

    _encrypt_config_secrets(raw_config)

    assert raw_config["keypools"]["openai"]["keys"][0] == encrypted
    assert raw_config["tenants"][0]["api_key"] == "${GATEWAY_DEFAULT_API_KEY:-gw-default-key}"


def test_validate_config_integrity_accepts_valid_config():
    raw_config = {
        "aliases": {"qwen-plus": "qwen-plus"},
        "routes": [{"pattern": "qwen-*", "provider": "qwen"}],
        "provider_base_urls": {"qwen": "https://example.com/v1"},
        "keypools": {
            "qwen": {
                "keys": [{"value": "****", "allowed_models": ["qwen-plus"]}],
                "strategy": "round-robin",
            }
        },
        "pricing": {},
    }

    assert _validate_config_integrity(raw_config) == []


def test_validate_config_integrity_reports_missing_references():
    raw_config = {
        "aliases": {"qwen-plus": "qwen-plus"},
        "routes": [{"pattern": "bad-*", "provider": "missing-provider"}],
        "provider_base_urls": {},
        "keypools": {
            "qwen": {
                "keys": [{"value": "****", "allowed_models": ["deleted-model"]}],
                "strategy": "round-robin",
            }
        },
        "pricing": {},
    }

    errors = _validate_config_integrity(raw_config)

    assert any("missing-provider" in error for error in errors)
    assert any("deleted-model" in error for error in errors)


def test_load_config_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/gateway.yaml")


def test_load_key_allowed_models(tmp_path):
    yaml_path = tmp_path / "gateway.yaml"
    yaml_path.write_text("""
routes: []
aliases: {}
keypools:
  qwen:
    keys:
      - value: "sk-qwen-plus"
        allowed_models:
          - "qwen3.6-plus"
      - "sk-provider-wide"
fallbacks: []
tenants: []
pricing: {}
""")

    config = load_config(str(yaml_path))

    assert config.keypools["qwen"].keys[0].value == "sk-qwen-plus"
    assert config.keypools["qwen"].keys[0].allowed_models == ["qwen3.6-plus"]
    assert config.keypools["qwen"].keys[1].value == "sk-provider-wide"
    assert config.keypools["qwen"].keys[1].allowed_models == []
