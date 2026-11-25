from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=4000)
    system_prompt: str | None = Field(default=None, max_length=8000)

    @field_validator("session_id", mode="before")
    @classmethod
    def validate_session_id(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("session_id must not be empty")
        return value

    @field_validator("message", mode="before")
    @classmethod
    def validate_message(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("message must not be empty")
            if len(value) > 4000:
                raise ValueError("message must be at most 4000 characters")
        return value

    @field_validator("system_prompt", mode="before")
    @classmethod
    def validate_system_prompt(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ChatMetadata(BaseModel):
    resolution_source: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class HistoryMessage(BaseModel):
    role: str
    content: str
    metadata: dict | None = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[HistoryMessage]


class HealthResponse(BaseModel):
    status: str
    keys_configured: int
    cache_enabled: bool
