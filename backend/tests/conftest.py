from __future__ import annotations

import os
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

TEST_ROOT = tempfile.mkdtemp(prefix="llm-chatbot-tests-")

os.environ["LLM_API_KEYS"] = "unit_test_key_1,unit_test_key_2"
os.environ["LLM_MODEL"] = "gemini/gemini-flash-lite-latest"
os.environ["LLM_MODELS"] = "gemini/gemini-flash-lite-latest,groq/openai/gpt-oss-120b"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_ROOT}/app.db"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"
os.environ["DEFAULT_SYSTEM_PROMPT"] = "You are a helpful assistant."
os.environ["LLM_MOCK_RESPONSE"] = "Mock answer from the test provider."
os.environ["LLM_MAX_TOKENS"] = "256"
os.environ["LLM_REQUEST_TIMEOUT"] = "90"
os.environ["CACHE_TTL_SECONDS"] = "3600"

from app.config import Settings
from app.database import Database
from app.main import app, llm_service


settings = Settings.from_env()
database = Database(settings)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_root():
    yield
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_state():
    if settings.database_path.exists():
        settings.database_path.unlink()
    database.initialize()
    llm_service.reset_cache()
    yield
    llm_service.reset_cache()


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
