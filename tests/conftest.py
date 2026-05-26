import pytest
from app.config import load_config


@pytest.fixture
def config():
    return load_config("config/gateway.example.yaml")
