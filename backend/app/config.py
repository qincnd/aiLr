from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AILR_", extra="ignore")

    model_provider: Literal["ollama", "openai"] = "ollama"
    model_name: str = "qwen2.5vl:7b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    max_image_mb: int = 12


settings = Settings()
