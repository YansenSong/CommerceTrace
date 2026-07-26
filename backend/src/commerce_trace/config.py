from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COMMERCE_TRACE_",
        env_file=".env",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql://commerce_app:commerce_app@postgres:5432/commerce_trace"
    query_database_url: str = (
        "postgresql://commerce_reader:commerce_reader@postgres:5432/commerce_trace"
    )
    llm_mode: Literal["fake", "openai"] = "fake"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    cookie_name: str = "commerce_trace_user"
    cookie_secure: bool = False
    statement_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    max_tool_iterations: int = Field(default=10, ge=1, le=20)
    max_business_sql_calls: int = Field(default=5, ge=1, le=10)
    max_sql_retries_per_purpose: int = Field(default=2, ge=0, le=5)
    max_result_rows: int = Field(default=500, ge=1, le=5_000)
    max_distinct_values: int = Field(default=50, ge=1, le=100)
    schema_version: str = "1.0.0"
    knowledge_version: str = "1.0.0"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    chroma_path: Path = Path("chroma_data")
    knowledge_path: Path = Path("knowledge")
    eval_dataset_path: Path = Path("evals/datasets/mvp.yaml")
