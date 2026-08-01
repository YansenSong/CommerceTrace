from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """集中声明后端可通过环境变量配置的运行参数。"""

    model_config = SettingsConfigDict(
        env_prefix="COMMERCE_TRACE_",
        env_file=Path(__file__).resolve().parents[3] / ".env",
        extra="ignore",
    )

    database_path: Path = Path("data/commerce_trace.db")
    agent_state_path: Path = Path("data/agent_state.db")
    knowledge_dir: Path = Path("knowledge/sql")
    model_base_url: str = "https://api.deepseek.com"
    model_api_key: SecretStr | None = None
    model: str = "deepseek-chat"
    cookie_name: str = "commerce_trace_user"
    cookie_secure: bool = False
    statement_timeout_ms: int = 5_000
    model_timeout_seconds: int = 60
    max_result_rows: int = 500
    max_distinct_values: int = 50
