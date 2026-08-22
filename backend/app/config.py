"""Application settings, loaded from the environment (.env)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent

# Load .env from the project root first, then backend/.env as an override.
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_ROOT / ".env", override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./data/commerce.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- AI ---------------------------------------------------------------
    llm_provider: str = "auto"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    llm_timeout_seconds: float = 20.0
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # Free-tier cloud models — an alternative to installing Ollama locally.
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"

    # --- Razorpay ---------------------------------------------------------
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_timeout_seconds: float = 15.0

    # --- Policy guardrails (rupees in env, paise internally) --------------
    max_order_value: int = 100_000
    max_discount_percent: int = 20
    max_campaign_budget: int = 50_000
    max_items_per_order: int = 20
    max_quantity_per_line: int = 5
    require_payment_confirmation: bool = True
    allowed_currency: str = "INR"
    tax_percent: float = 18.0
    quote_ttl_seconds: int = 600

    @field_validator("razorpay_key_id")
    @classmethod
    def _refuse_live_keys(cls, v: str) -> str:
        """Hard stop: this project is test-mode only."""
        if v and v.strip().startswith("rzp_live_"):
            raise ValueError(
                "RAZORPAY_KEY_ID looks like a LIVE key (rzp_live_...). "
                "This application refuses live credentials by design. "
                "Use a test key (rzp_test_...) from the Razorpay dashboard."
            )
        return v.strip()

    # --- Derived helpers --------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_order_value_paise(self) -> int:
        return int(self.max_order_value) * 100

    @property
    def max_campaign_budget_paise(self) -> int:
        return int(self.max_campaign_budget) * 100

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def sqlalchemy_url(self) -> str:
        """Resolve a relative sqlite path against the backend directory."""
        url = self.database_url
        prefix = "sqlite:///./"
        if url.startswith(prefix):
            abs_path = (BACKEND_ROOT / url[len(prefix):]).resolve()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{abs_path.as_posix()}"
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests that mutate the environment."""
    get_settings.cache_clear()


settings = get_settings()

__all__ = ["Settings", "get_settings", "reset_settings_cache", "settings",
           "BACKEND_ROOT", "PROJECT_ROOT", "os"]
