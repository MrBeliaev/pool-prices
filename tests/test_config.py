import pytest
from pydantic import ValidationError

from pool_prices.config import MULTICALL_ADDRESS, Settings


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url: str = "postgresql://user:pass@localhost/db"
    rpc_url: str = "http://localhost:8545"

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RPC_URL", rpc_url)

    settings = Settings(_env_file=None)

    assert settings.database_url == database_url
    assert settings.rpc_url == rpc_url
    assert settings.multicall_address == MULTICALL_ADDRESS
    assert settings.request_timeout_seconds == 10.0
    assert settings.log_level == "INFO"


def test_settings_require_database_url_and_rpc_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RPC_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
