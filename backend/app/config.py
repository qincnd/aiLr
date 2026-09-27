import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AILR_", extra="ignore")

    model_provider: Literal["ollama", "openai"] = "ollama"
    model_name: str = "qwen2.5vl:7b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    max_image_mb: int = 12
    max_raw_image_mb: int = 100


settings = Settings()
OLLAMA_CONTEXT_SIZE = 8192


class ModelConfig(BaseModel):
    provider: Literal["ollama", "openai"] = "ollama"
    model_name: str = "qwen2.5vl:7b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    openai_base_url: str = "https://api.openai.com/v1"
    api_key: str = Field(default="", repr=False)


CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "model_config.json"


def load_model_config() -> ModelConfig:
    if CONFIG_PATH.is_file():
        return ModelConfig.model_validate_json(CONFIG_PATH.read_text(encoding="utf-8"))
    return ModelConfig(
        provider=settings.model_provider,
        model_name=settings.model_name,
        ollama_base_url=settings.ollama_base_url,
        openai_base_url=settings.openai_base_url,
        api_key=settings.openai_api_key or "",
    )


def save_model_config(config: ModelConfig) -> ModelConfig:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = CONFIG_PATH.with_suffix(".tmp")
    temporary_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    temporary_path.replace(CONFIG_PATH)
    return config


def public_model_config(config: ModelConfig | None = None) -> dict[str, str | bool]:
    current = config or load_model_config()
    return {
        "provider": current.provider,
        "model_name": current.model_name,
        "ollama_base_url": current.ollama_base_url,
        "openai_base_url": current.openai_base_url,
        "api_key_configured": bool(current.api_key),
    }
