from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .database import Database
from .llm import ALL_PROVIDERS_UNAVAILABLE, LLMService, ProviderUnavailable, parse_failure_header
from .schemas import ChatMetadata, ChatRequest, ChatResponse, HealthResponse, HistoryMessage, HistoryResponse


logger = logging.getLogger(__name__)

settings = Settings.from_env()
database = Database(settings)
llm_service = LLMService(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.initialize()
    llm_service.configure_cache()
    yield


app = FastAPI(
    title="LLM Chatbot",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        database.health()
    except Exception as exc:
        logger.exception("Database health check failed")
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return HealthResponse(
        status="ok",
        keys_configured=len(settings.api_keys),
        cache_enabled=True,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    x_simulate_failure: str | None = Header(default=None, alias="X-Simulate-Failure"),
) -> ChatResponse:
    started = time.perf_counter()
    try:
        simulated_failures = parse_failure_header(
            x_simulate_failure,
            settings.failure_simulation_enabled,
            llm_service.key_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    system_prompt = payload.system_prompt
    if system_prompt is None:
        system_prompt = database.get_system_prompt(payload.session_id) or settings.default_system_prompt

    stored_history = database.get_history(payload.session_id) or []
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": row["role"], "content": row["content"]} for row in stored_history)
    messages.append({"role": "user", "content": payload.message})

    try:
        result = llm_service.complete(messages, simulated_failures)
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=ALL_PROVIDERS_UNAVAILABLE) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    metadata = ChatMetadata(
        resolution_source=result.resolution_source,
        model=result.model,
        latency_ms=latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        key_id=result.key_id,
        fallback_attempts=result.fallback_attempts,
    )
    database.save_turn(
        session_id=payload.session_id,
        system_prompt=system_prompt,
        user_message=payload.message,
        assistant_reply=result.reply,
        metadata=metadata.model_dump(),
    )
    return ChatResponse(session_id=payload.session_id, reply=result.reply, metadata=metadata)


@app.get("/sessions/{session_id}/messages", response_model=HistoryResponse)
def get_messages(session_id: str) -> HistoryResponse:
    rows = database.get_history(session_id)
    if rows is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = []
    for row in rows:
        raw_metadata = row["metadata"]
        parsed_metadata = None
        if raw_metadata:
            parsed_metadata = json.loads(raw_metadata)
        messages.append(
            HistoryMessage(
                role=row["role"],
                content=row["content"],
                metadata=parsed_metadata,
            )
        )
    return HistoryResponse(session_id=session_id, messages=messages)
