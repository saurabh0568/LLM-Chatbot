# LLM Chatbot with Key Failover and Response Caching

## Project description

This repository is a complete full-stack implementation of the supplied assignment: a React chat UI, a Python FastAPI backend, an SQLite session store, and a LiteLLM gateway with a two-key pool, automatic failover, response caching, and per-request token reporting.

The app keeps multi-turn history per `session_id`, sends only the newest user message from the browser, rebuilds the prompt on the server, and returns request metadata for every assistant reply. It includes the required deployment, Docker, API, UI, verification, and documentation pieces, plus optional system prompts and a simulated failure hook.

Repository structure:

- `backend/`: FastAPI app, SQLite persistence, LiteLLM failover/cache logic, pytest suite, multi-stage Dockerfile.
- `frontend/`: React + TypeScript + Vite UI, multi-stage Dockerfile, Nginx runtime, Vercel build config.
- `docker-compose.yml`: local full stack with a named SQLite volume mounted at `/data`.
- `Makefile`: `up`, `down`, `logs`, plus non-Docker development helpers.
- `.env.example`: every environment variable with placeholder values; real secrets are never committed.
- `README.md`: this document.

## 1. Live URLs

| Tier | URL | Status |
| --- | --- | --- |
| Frontend | https://llm-chatbot-cyan.vercel.app | Live public Vercel production deployment |
| Backend | https://llm-chatbot-backend-93ky.onrender.com | Live public Render Docker service |
| GitHub | https://github.com/saurabh0568/LLM-Chatbot.git | Public repository, branch `main` |

Both tiers are connected: the frontend sends `VITE_API_URL` to the Render backend, and the backend allows the Vercel origin through `ALLOWED_ORIGINS`. The deployed backend currently uses Render's free tier, so its SQLite file is ephemeral and history resets on restart/redeploy; the local Docker stack uses a named `/data` volume and preserves history across container restarts.

Submission note: the assignment form also requires a 2–3 minute narrated demo video showing the deployed app, endpoints, cache hit, and failover.

## 2. Run locally: with Docker and without

### With Docker

```bash
cp .env.example .env
make up
```

`make up` builds and runs both services through Docker Compose. The backend container stores SQLite at `/data/app.db` inside the named volume `chatbot-data`; the frontend container serves the production Vite build with Nginx and receives `VITE_API_URL` as a build argument.

Stop and inspect with:

```bash
make down
make logs
```

If `make` is unavailable, use Docker Compose directly:

```bash
docker compose up --build -d
docker compose down
docker compose logs -f
```

### Without Docker

```bash
cp .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
npm --prefix frontend install
make dev-up
```

Stop the two local processes with:

```bash
make dev-down
```

If `make` is unavailable, run the backend and frontend in separate terminals:

```bash
python -m uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000
```

```bash
npm --prefix frontend run dev
```

### Environment variables

| Variable | Where | Meaning |
| --- | --- | --- |
| `LLM_API_KEYS` | backend | Comma-separated key pool; ids are `key_1`, `key_2`, ... in order. |
| `DATABASE_URL` | backend | SQLite location, e.g. `sqlite:////data/app.db`. |
| `ALLOWED_ORIGINS` | backend | Comma-separated CORS origins; must include the deployed frontend origin. |
| `ENABLE_FAILURE_SIMULATION` | backend | `true` enables the `X-Simulate-Failure` test hook. |
| `LLM_MODELS` | backend | Optional comma-separated model per key, e.g. `gemini/gemini-flash-lite-latest,groq/openai/gpt-oss-120b`. |
| `LLM_MODEL` | backend | Fallback model when `LLM_MODELS` is not set. |
| `VITE_API_URL` | frontend | Backend base URL used by the browser client. |
| `LLM_MOCK_RESPONSE` | backend | Optional deterministic reply for offline verification; leave empty for real provider calls. |
| `DEFAULT_SYSTEM_PROMPT` | backend | Optional default session system prompt. |
| `LLM_MAX_TOKENS` | backend | Optional completion token cap. |
| `LLM_REQUEST_TIMEOUT` | backend | Optional provider request timeout in seconds. |
| `CACHE_TTL_SECONDS` | backend | Optional in-memory cache TTL. |

### No real API keys yet

Put a deterministic mock reply in `.env` to exercise the whole stack without network calls:

```env
LLM_MOCK_RESPONSE=Local mock answer for verification
LLM_API_KEYS=dummy_key_1,dummy_key_2
```

Leave `LLM_MOCK_RESPONSE` empty when real provider keys are available.

### Useful commands

```bash
make test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

## 3. Design decisions: key fallback, caching, SQLite session storage

### API and UI contract

The backend exposes the three required endpoints:

- `GET /health` returns `{"status":"ok","keys_configured":2,"cache_enabled":true}` once the database and key pool are ready.
- `POST /chat` accepts `session_id`, `message`, and optional `system_prompt`, and returns `session_id`, `reply`, and `metadata` with `resolution_source`, `model`, `latency_ms`, `input_tokens`, `output_tokens`, `key_id`, and `fallback_attempts`.
- `GET /sessions/{session_id}/messages` returns oldest-first `{role, content, metadata?}` history without the system prompt.

All errors are JSON with a `detail` field: validation failures are 422, every-key failure is `503 {"detail":"all providers unavailable"}`, and an unknown history session is `404 {"detail":"session not found"}`. Failed 422/503 requests are never written to history.

The UI implements `chat-input`, `send-button`, `assistant-message`, `meta-source`, `meta-model`, `meta-latency`, `meta-input-tokens`, `meta-output-tokens`, `system-prompt-input`, and `error-message`. A new session starts on page load, and errors render as readable messages instead of a blank screen.

### Key fallback

- `LLM_API_KEYS` is parsed in order into `key_1`, `key_2`, ...; key values are never returned by the API.
- `LLM_MODELS` optionally maps one model per key; if omitted, `LLM_MODEL` is used for every key.
- Each `/chat` request calls `litellm.completion(...)` with the current key's model.
- Transient timeouts, 429, quota, and 5xx errors are caught, logged by key id, and retried once before failing over.
- `fallback_attempts` counts the keys that failed before the answering key.
- If every key fails, the backend returns exactly `503 {"detail":"all providers unavailable"}` and writes nothing to session history.
- With `ENABLE_FAILURE_SIMULATION=true`, the `X-Simulate-Failure` header forces chosen keys to fail for one request only. It supports `key_1=429`, `key_1=429,key_2=500`, and `*=429`; simulated requests still call the real provider for the surviving key.
- Validation errors, provider failures, unknown routes, and unexpected errors are all returned as JSON, so the browser never receives an HTML error page or a stack trace.

### Caching

- LiteLLM's built-in in-memory cache is enabled on startup.
- The cache key is a hash of the model plus the full message list: system prompt + stored history + new message. A provider-safe top-level `cache_key` prevents the API key from entering the cache key.
- The same first message in a new session returns the identical reply with `resolution_source: "LLM_CACHE"`, `key_id: null`, `fallback_attempts: 0`, and the stored token counts.
- A new generation returns `resolution_source: "LIVE_INFERENCE"` with the answering `key_id`.
- A different message or system prompt changes the message list and cannot reuse an unrelated cache entry. Cache hits are faster because no provider call is made.

### SQLite session storage

- SQLite is created automatically on startup at `DATABASE_URL`. The Docker Compose value is `sqlite:////data/app.db` on the named `chatbot-data` volume.
- `sessions` stores the per-session system prompt; `messages` stores user and assistant turns with ISO-8601 timestamps and JSON metadata on assistant rows.
- The client sends only the new message. The backend rebuilds the prompt from stored history, so sessions stay separate and the bot remembers earlier turns.
- `GET /sessions/{session_id}/messages` returns oldest-first user/assistant history and never returns the system prompt.
- Token counts are per request, not conversation totals.

## 4. Verification: how to see failover, cache hits and per-message token usage

The **Quick tests** panel in the UI calls the same `/chat` endpoint as the composer:

- **Test live** sends a normal LiteLLM request and shows live model metadata.
- **Test failover** sends `X-Simulate-Failure: key_1=429`; expect `key_2` and `fallback_attempts: 1`.
- **Test cache** sends the same unique prompt twice in two new sessions; expect `LIVE_INFERENCE` then `LLM_CACHE` with identical reply and token counts.
- **Test 503 error** sends `X-Simulate-Failure: *=429`; expect the readable `all providers unavailable` error and no stored history.

The same checks can be run against the deployed backend:

```bash
BACKEND_URL=https://llm-chatbot-backend-93ky.onrender.com
```

Health:

```bash
curl "$BACKEND_URL/health"
```

Expected:

```json
{"status":"ok","keys_configured":2,"cache_enabled":true}
```

Live call:

```bash
curl -X POST "$BACKEND_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"verify-1","message":"What is the capital of France?"}'
```

Check `metadata.resolution_source == "LIVE_INFERENCE"`, a non-null `metadata.key_id`, and per-request `input_tokens` / `output_tokens`.

Cache hit in a new session with the same first message:

```bash
curl -X POST "$BACKEND_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"verify-2","message":"What is the capital of France?"}'
```

Check identical reply text, `metadata.resolution_source == "LLM_CACHE"`, `metadata.key_id == null`, `metadata.fallback_attempts == 0`, matching token counts, and lower `latency_ms`.

Failover with a new, unique message:

```bash
curl -X POST "$BACKEND_URL/chat" \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=429" \
  -d '{"session_id":"verify-failover","message":"Explain failover with a unique sentence."}'
```

Check `metadata.key_id == "key_2"` and `metadata.fallback_attempts == 1`. `key_1=500` behaves the same way.

All keys failing:

```bash
curl -X POST "$BACKEND_URL/chat" \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: *=429" \
  -d '{"session_id":"verify-all-failed","message":"This must fail."}'
```

Expected: `503 {"detail":"all providers unavailable"}`. The following history call returns 404:

```bash
curl "$BACKEND_URL/sessions/verify-all-failed/messages"
```

History and token usage:

```bash
curl "$BACKEND_URL/sessions/verify-1/messages"
```

Each assistant message includes its own `input_tokens` and `output_tokens`; the UI renders them under every assistant reply as `meta-input-tokens` and `meta-output-tokens`.

Backend tests cover health, validation, history, cache hits, different-message cache separation, simulated failover, all-providers-failed, and session-scoped system prompts:

```bash
make test
```

## 5. Setup Used: your editor and AI tools

- Editor/CLI: opencode
- AI assistant: opencode/big-pickle
- Backend: Python 3.11 in Docker, Python 3.13 locally, FastAPI, LiteLLM, SQLite, pytest
- Frontend: Node.js 22+, React 19, TypeScript, Vite 7, Nginx
- Deployment: Vercel for the frontend, Render for the Docker backend
- Documentation source: the supplied assignment PDF, extracted and checked section by section

## 6. Bonus features (if any): what you added beyond the requirements and why

- **Session-scoped system prompt**: `system_prompt` is persisted per session and shown in an optional UI field; later requests reuse it, while history never leaks it.
- **New session button**: lets a reviewer start a fresh session without reloading the page; page load already creates a new session.
- **Quick test buttons**: one-click live, failover, cache, and 503 checks make the required behavior easy to demonstrate in the deployed UI.
- **Mock provider mode**: `LLM_MOCK_RESPONSE` makes local verification and automated tests deterministic without real keys, while production still uses real LiteLLM provider calls.
- **Docker and non-Docker entry points**: `make up` / `make down` run the full stack with Docker, while `make dev-up` / `make dev-down` keep local Python and Node servers usable.
- **JSON-only error contract**: custom exception handlers return `{"detail": ...}` for HTTP and unexpected errors, so the UI never receives an HTML error page.
- **Deployment configs**: `render.yaml` and `frontend/vercel.json` make the deployed architecture reproducible from the repository.
