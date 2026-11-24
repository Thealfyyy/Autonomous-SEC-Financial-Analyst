"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime settings with SEC fair-access guardrails."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    sec_user_agent: str = Field(
        default="AutonomousSECAnalyst/0.1 (contact@example.com)",
        description="Descriptive User-Agent required by SEC EDGAR fair-access policy.",
    )
    sec_rate_limit_rps: float = Field(
        default=8.0,
        gt=0.0,
        le=10.0,
        description="Max SEC EDGAR requests per second (SEC hard cap is 10 req/s).",
    )
    sec_cache_dir: Path = Field(
        default=Path("data/raw_sec"),
        description="Local directory for caching raw SEC filings and JSON responses.",
    )

    database_url: str = Field(
        default="postgresql://sec_analyst:sec_analyst@localhost:5432/sec_analyst",
    )
    database_readonly_url: str = Field(
        default="postgresql://sec_readonly:sec_readonly@localhost:5432/sec_analyst",
    )

    llm_base_url: str = Field(default="http://localhost:8080/v1")
    llm_model: str = Field(default="Qwen3-8B-Q4_K_M")
    embedding_model: str = Field(default="BAAI/bge-m3")
    embedding_dim: int = Field(default=1024, gt=0)
    reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3")

    @field_validator("sec_user_agent")
    @classmethod
    def validate_user_agent(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or " " not in cleaned:
            raise ValueError(
                "SEC_USER_AGENT must be descriptive (e.g., 'AppName/0.1 contact@domain.com')."
            )
        return cleaned
