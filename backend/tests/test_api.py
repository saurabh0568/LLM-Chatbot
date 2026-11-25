from __future__ import annotations

import pytest


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("message", "status_code"),
    [
        ("rate limit exceeded", 429),
        ("service unavailable", 503),
        ("insufficient_quota for project", None),
    ],
)
def test_transient_provider_errors_fall_back_to_next_key(client, monkeypatch, message, status_code):
    from app import llm as llm_module

    real_completion = llm_module.litellm.completion
    calls: list[str] = []

    def flaky_completion(**kwargs):
        calls.append(str(kwargs.get("api_key")))
        if len(calls) <= 2:
            raise ProviderError(message, status_code)
        return real_completion(**kwargs)

    monkeypatch.setattr(llm_module.litellm, "completion", flaky_completion)
    monkeypatch.setattr(llm_module.time, "sleep", lambda _seconds: None)

    response = client.post(
        "/chat",
        json={
            "session_id": f"fallback-{status_code or 'quota'}",
            "message": f"Transient {message} test",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1
    assert calls[:2] == [calls[0], calls[0]]
    assert calls[2] != calls[0]


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "keys_configured": 2,
        "cache_enabled": True,
    }


def test_chat_stores_history_and_per_message_tokens(client):
    first = client.post(
        "/chat",
        json={"session_id": "s-1", "message": "Hello there"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["session_id"] == "s-1"
    assert first_body["reply"] == "Mock answer from the test provider."
    assert first_body["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert first_body["metadata"]["model"] == "gemini/gemini-flash-lite-latest"
    assert first_body["metadata"]["key_id"] == "key_1"
    assert first_body["metadata"]["fallback_attempts"] == 0
    assert first_body["metadata"]["input_tokens"] == 10
    assert first_body["metadata"]["output_tokens"] == 20
    assert isinstance(first_body["metadata"]["latency_ms"], int)

    second = client.post(
        "/chat",
        json={"session_id": "s-1", "message": "And now?"},
    )
    assert second.status_code == 200

    history = client.get("/sessions/s-1/messages")
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "Hello there"
    assert messages[1]["content"] == "Mock answer from the test provider."
    assert messages[1]["metadata"]["input_tokens"] == 10
    assert messages[1]["metadata"]["output_tokens"] == 20
    assert all(message["role"] != "system" for message in messages)


def test_cache_hit_across_new_sessions(client):
    first = client.post(
        "/chat",
        json={"session_id": "cache-a", "message": "Cache this exact question"},
    )
    second = client.post(
        "/chat",
        json={"session_id": "cache-b", "message": "Cache this exact question"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second.json()["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second.json()["reply"] == first.json()["reply"]
    assert second.json()["metadata"]["key_id"] is None
    assert second.json()["metadata"]["fallback_attempts"] == 0
    assert second.json()["metadata"]["input_tokens"] == first.json()["metadata"]["input_tokens"]
    assert second.json()["metadata"]["output_tokens"] == first.json()["metadata"]["output_tokens"]


def test_different_message_is_not_a_cache_hit(client):
    first = client.post("/chat", json={"session_id": "s-a", "message": "First unique question"})
    second = client.post("/chat", json={"session_id": "s-b", "message": "Different unique question"})
    assert first.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_failover_on_simulated_429(client):
    response = client.post(
        "/chat",
        json={"session_id": "failover", "message": "Failover unique question"},
        headers={"X-Simulate-Failure": "key_1=429"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["model"] == "groq/openai/gpt-oss-120b"
    assert body["metadata"]["fallback_attempts"] == 1
    assert body["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert "unavailable" not in body["reply"].lower()


def test_failover_on_simulated_500(client):
    response = client.post(
        "/chat",
        json={"session_id": "failover-500", "message": "Another failover question"},
        headers={"X-Simulate-Failure": "key_1=500"},
    )
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_failed_returns_503_and_writes_no_history(client):
    response = client.post(
        "/chat",
        json={"session_id": "all-failed", "message": "This request must fail"},
        headers={"X-Simulate-Failure": "*=429"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    history = client.get("/sessions/all-failed/messages")
    assert history.status_code == 404
    assert history.json() == {"detail": "session not found"}


def test_validation_errors_are_json(client):
    empty = client.post("/chat", json={"session_id": "s-1", "message": "   "})
    assert empty.status_code == 422
    assert "detail" in empty.json()

    long_message = client.post("/chat", json={"session_id": "s-2", "message": "x" * 4001})
    assert long_message.status_code == 422

    malformed = client.post(
        "/chat",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 422
    assert "detail" in malformed.json()


def test_unknown_session_history_returns_404(client):
    response = client.get("/sessions/does-not-exist/messages")
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}


def test_system_prompt_is_session_scoped_and_hidden_from_history(client):
    first = client.post(
        "/chat",
        json={
            "session_id": "prompted",
            "message": "Remember this style",
            "system_prompt": "Answer in one short sentence.",
        },
    )
    second = client.post(
        "/chat",
        json={"session_id": "prompted", "message": "Continue"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    history = client.get("/sessions/prompted/messages")
    assert history.status_code == 200
    assert [message["role"] for message in history.json()["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
