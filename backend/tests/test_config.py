import pytest
from pydantic import ValidationError

from app.config import Settings


def test_prod_rejects_mock_mode():
    with pytest.raises(ValidationError):
        Settings(app_env="prod", provider_mode="mock", secret_key="x" * 40)


def test_prod_rejects_weak_secret():
    with pytest.raises(ValidationError):
        Settings(app_env="prod", provider_mode="live", secret_key="short")


def test_prod_accepts_valid_config():
    s = Settings(app_env="prod", provider_mode="live", secret_key="x" * 40)
    assert s.app_env == "prod"