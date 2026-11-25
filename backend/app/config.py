from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class Settings:
    api_keys: tuple[str, ...]
    model: str
    models: tuple[str, ...]
    database_url: str
    allowed_origins: tuple[str, ...]
    failure_simulation_enabled: bool
    default_system_prompt: str
    mock_response: str | None
    max_tokens: int
    request_timeout: float
    cache_ttl_seconds: int

    def __post_init__(self) -> None:
        if self.models and len(self.models) != len(self.api_keys):
            raise ValueError("LLM_MODELS must contain one model per LLM_API_KEYS entry")

    @classmethod
    def from_env(cls) -> "Settings":
        api_keys = _csv(os.getenv("LLM_API_KEYS", ""))
        model = os.getenv("LLM_MODEL", "gemini/gemini-flash-lite-latest").strip() or "gemini/gemini-flash-lite-latest"
        models = _csv(os.getenv("LLM_MODELS", "")) or tuple(model for _ in api_keys)
        return cls(
            api_keys=api_keys,
            model=model,
            models=models,
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/app.db").strip()
            or "sqlite:///./data/app.db",
            allowed_origins=_csv(
                os.getenv(
                    "ALLOWED_ORIGINS",
                    "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000",
                )
            ),
            failure_simulation_enabled=_bool("ENABLE_FAILURE_SIMULATION", False),
            default_system_prompt=os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant."),
            mock_response=os.getenv("LLM_MOCK_RESPONSE", "").strip() or None,
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "1024")),
            request_timeout=float(os.getenv("LLM_REQUEST_TIMEOUT", "30")),
            cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
        )

    @property
    def database_path(self) -> Path:
        url = self.database_url
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///") :]
        elif url.startswith("sqlite:"):
            raw = url[len("sqlite:") :]
        else:
            raise ValueError("DATABASE_URL must be a SQLite URL")
        if raw.startswith("file:"):
            raw = raw[len("file:") :]
        raw = raw.split("?", 1)[0]
        if not raw:
            raise ValueError("DATABASE_URL must include a database path")
        return Path(os.path.normpath(raw))
