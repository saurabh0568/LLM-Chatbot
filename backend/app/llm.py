from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass

import litellm
from litellm.litellm_core_utils.hidden_params import served_from_cache

from .config import Settings


logger = logging.getLogger(__name__)

SIMULATED_STATUSES = {"429", "500", "502", "503"}
ALL_PROVIDERS_UNAVAILABLE = "all providers unavailable"
TRANSIENT_EXCEPTIONS = (
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.RateLimitError,
    litellm.InternalServerError,
    litellm.ServiceUnavailableError,
    litellm.BudgetExceededError,
)
QUOTA_MARKERS = ("quota", "budget", "insufficient_quota", "rate limit", "too many requests")


class ProviderUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ProviderResult:
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int
    resolution_source: str


def parse_failure_header(header: str | None, enabled: bool, key_ids: list[str]) -> dict[str, int]:
    if not enabled or not header or not header.strip():
        return {}
    failures: dict[str, int] = {}
    known_ids = set(key_ids)
    for part in header.split(","):
        pair = part.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError("X-Simulate-Failure must use <key_id>=<status> pairs")
        key_id, status = (item.strip() for item in pair.split("=", 1))
        if status not in SIMULATED_STATUSES:
            raise ValueError("simulated status must be 429, 500, 502 or 503")
        if key_id == "*":
            targets = key_ids
        elif key_id in known_ids:
            targets = [key_id]
        else:
            raise ValueError(f"unknown key id in X-Simulate-Failure: {key_id}")
        for target in targets:
            failures[target] = int(status)
    return failures


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (status_code == 429 or status_code >= 500):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in QUOTA_MARKERS)


class LLMService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def key_ids(self) -> list[str]:
        return [f"key_{index}" for index in range(1, len(self.settings.api_keys) + 1)]

    def configure_cache(self) -> None:
        litellm.cache = litellm.Cache(type="local", ttl=self.settings.cache_ttl_seconds)

    def reset_cache(self) -> None:
        self.configure_cache()

    def _cache_key(self, messages: list[dict[str, str]], model: str) -> str:
        material = json.dumps(
            {"model": model, "messages": messages},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _cached_result(self, cache_key: str, model: str) -> ProviderResult | None:
        cached = litellm.cache.get_cache(cache_key=cache_key, caching=True)
        if not isinstance(cached, dict):
            return None
        choices = cached.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        reply = message.get("content") or ""
        if not isinstance(reply, str) or not reply.strip():
            return None
        usage = cached.get("usage") or {}
        return ProviderResult(
            reply=reply,
            model=model,
            input_tokens=_as_int(usage.get("prompt_tokens")),
            output_tokens=_as_int(usage.get("completion_tokens")),
            key_id=None,
            fallback_attempts=0,
            resolution_source="LLM_CACHE",
        )

    def _complete_with_key(
        self,
        key_id: str,
        api_key: str,
        messages: list[dict[str, str]],
        cache_key: str,
        mock_response: str | None,
        model: str,
    ) -> ProviderResult | None:
        kwargs: dict[str, object] = {
            "model": model,
            "messages": messages,
            "api_key": api_key,
            "caching": True,
            "cache_key": cache_key,
            "max_tokens": self.settings.max_tokens,
            "timeout": self.settings.request_timeout,
        }
        if mock_response is not None:
            kwargs["mock_response"] = mock_response
        response = litellm.completion(**kwargs)
        if served_from_cache(response):
            cached = self._cached_result(cache_key, model)
            if cached is not None:
                return cached
        reply = response.choices[0].message.content or ""
        if not isinstance(reply, str) or not reply.strip():
            return None
        usage = getattr(response, "usage", None)
        return ProviderResult(
            reply=reply,
            model=model,
            input_tokens=_as_int(getattr(usage, "prompt_tokens", 0)),
            output_tokens=_as_int(getattr(usage, "completion_tokens", 0)),
            key_id=key_id,
            fallback_attempts=0,
            resolution_source="LIVE_INFERENCE",
        )

    def complete(
        self,
        messages: list[dict[str, str]],
        simulated_failures: dict[str, int] | None = None,
    ) -> ProviderResult:
        simulated_failures = simulated_failures or {}
        for model in dict.fromkeys(self.settings.models):
            cached = self._cached_result(self._cache_key(messages, model), model)
            if cached is not None:
                return cached

        for index, (key_id, api_key) in enumerate(
            (f"key_{position}", api_key)
            for position, api_key in enumerate(self.settings.api_keys, start=1)
        ):
            model = self.settings.models[index]
            cache_key = self._cache_key(messages, model)
            simulated_status = simulated_failures.get(key_id)
            if simulated_status is not None:
                logger.info("Simulated %s failure for %s", simulated_status, key_id)
                continue
            for attempt in range(2):
                try:
                    result = self._complete_with_key(
                        key_id=key_id,
                        api_key=api_key,
                        messages=messages,
                        cache_key=cache_key,
                        mock_response=self.settings.mock_response,
                        model=model,
                    )
                except Exception as exc:
                    if attempt == 0 and _is_transient(exc):
                        logger.warning(
                            "Provider %s had a transient %s failure; retrying once",
                            key_id,
                            type(exc).__name__,
                        )
                        time.sleep(0.5)
                        continue
                    logger.warning("Provider %s failed with %s: %s", key_id, type(exc).__name__, str(exc)[:300])
                    break
                if result is not None:
                    return ProviderResult(
                        reply=result.reply,
                        model=result.model,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        key_id=result.key_id,
                        fallback_attempts=index,
                        resolution_source=result.resolution_source,
                    )
                break
        raise ProviderUnavailable(ALL_PROVIDERS_UNAVAILABLE)
