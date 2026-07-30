from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """集中声明后端可通过环境变量配置的运行参数。"""

    model_config = SettingsConfigDict(
        env_prefix="COMMERCE_TRACE_",
        env_file=".env",
        extra="ignore",
    )

    environment: str = "development"
    database_path: Path = Path("data/commerce_trace.db")
    agent_state_path: Path = Path("data/agent_state.db")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-chat"
    cookie_name: str = "commerce_trace_user"
    cookie_secure: bool = False
    statement_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    request_timeout_seconds: int = Field(default=120, ge=10, le=600)
    model_timeout_seconds: int = Field(default=60, ge=5, le=300)
    max_agent_steps: int = Field(default=10, ge=1, le=20)
    max_business_sql_calls: int = Field(default=5, ge=1, le=10)
    max_result_rows: int = Field(default=500, ge=1, le=5_000)
    max_distinct_values: int = Field(default=50, ge=1, le=100)
