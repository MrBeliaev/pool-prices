from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MULTICALL_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    database_url: str = Field(description="Postgres URL")
    rpc_url: str = Field(description="Ethereum HTTP RPC")
    multicall_address: str = MULTICALL_ADDRESS
    request_timeout_seconds: float = Field(default=10.0, gt=0)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
