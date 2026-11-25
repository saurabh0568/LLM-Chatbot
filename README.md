# LLM Chatbot

Full-stack React + FastAPI chatbot with multi-turn SQLite history, LiteLLM response caching, two-key failover, and a simulated failure hook.

## 1. Live URLs

| Tier | URL | Status |
| --- | --- | --- |
| Frontend | _pending Vercel deployment_ | Import the repo into Vercel with root directory `frontend`; `frontend/vercel.json` supplies the Vite build settings |
| Backend | _pending Render deployment_ | Deploy from `render.yaml`; set `LLM_API_KEYS` and `ALLOWED_ORIGINS` in the Render dashboard |
| GitHub | https://github.com/saurabh0568/LLM-Chatbot.git | Pushed to `main` |

Deployment configs are included: `render.yaml` defines the Docker web service, `/data` persistent disk, `/health` check, and non-secret environment values; `frontend/vercel.json` defines the Vite build. Deploy the backend first, set `ALLOWED_ORIGINS=http://localhost:3000` temporarily, then deploy the frontend with `VITE_API_URL` set to the Render backend base URL, then update Render `ALLOWED_ORIGINS` to the Vercel frontend URL and redeploy. Keep `DATABASE_URL=sqlite:////data/app.db` so session history survives backend restarts.

## 2. Run locally: with Docker and without

### With Docker

```bash
cp .env.example .env
make up
```

`make up` runs `docker compose up --build -d` and starts:

- Backend: http://localhost:8000
- Frontend: http://localhost:3000

The backend uses a multi-stage Dockerfile and stores SQLite at `/data/app.db` inside the named volume `chatbot-data`, so history survives container restarts. The frontend uses a multi-stage Node build and an Nginx runtime image, with `VITE_API_URL` supplied as a build argument.

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

If `make` is unavailable, run the two processes in separate terminals:

```bash
python -m uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000
```

```bash
npm --prefix frontend run dev
```

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

### Key fallback

- `LLM_API_KEYS` is parsed in order into `key_1`, `key_2`, ...; key values are never returned by the API.
- `LLM_MODELS` optionally maps one model per key, e.g. `gemini/gemini-flash-lite-latest,groq/openai/gpt-oss-120b`; if omitted, `LLM_MODEL` is used for every key.
- Each `/chat` request tries the keys in order with `litellm.completion(...)`, using that key's model.
- Transient timeouts, 429 and 5xx errors are retried once on the same key before failing over.
- Any provider exception, quota error, 429 or 5xx is caught, logged by key id, and the next key is tried. The response never contains a stack trace.
- `fallback_attempts` counts the keys that failed before the answering key.
- If every key fails, the backend returns exactly `503 {"detail": "all providers unavailable"}` and writes nothing to session history.
- With `ENABLE_FAILURE_SIMULATION=true`, the `X-Simulate-Failure` header forces chosen keys to fail for one request only. It supports `key_1=429`, `key_1=500,key_2=502`, and `*=429`. Simulated requests still call the real provider for the surviving key, so the user gets a real answer, not an error string wrapped in a 200.
- What stops failures here: validation errors are JSON 422, all provider failures are converted to a clean 503, unknown routes/errors are JSON, the frontend renders readable error text, and failed requests are not persisted.

### Caching

- LiteLLM's built-in in-memory cache is enabled on startup.
- The cache key is a SHA-256 hash of `model + full message list` (`system_prompt + stored history + new message`). A provider-safe top-level `cache_key` is passed to LiteLLM so the API key is not part of the cache key; new sessions therefore share cached answers for the same model.
- A cache hit returns the identical reply, `resolution_source: "LLM_CACHE"`, `key_id: null`, `fallback_attempts: 0`, and the token counts stored with the cached response.
- A new generation returns `resolution_source: "LIVE_INFERENCE"` with the answering `key_id`.
- A different message or a different system prompt changes the message list and cannot reuse an unrelated cache entry.

### SQLite session storage

- SQLite is created automatically on startup at `DATABASE_URL` (local default `sqlite:///./data/app.db`; container-style value `sqlite:////data/app.db` is also supported).
- `sessions` stores the per-session system prompt; `messages` stores user and assistant turns with ISO-8601 timestamps and JSON metadata on assistant rows.
- The client sends only the new message. The backend rebuilds the prompt from stored history, so sessions stay separate and the bot remembers earlier turns.
- `GET /sessions/{session_id}/messages` returns oldest-first user/assistant history and never returns the system prompt.
- A 422 or 503 request is not added to history.

## 4. Verification: how to see failover, cache hits and per-message token usage

The **Quick tests** panel in the UI calls the same `/chat` endpoint as the composer:

- **Test live** sends a normal LiteLLM request and shows the live model metadata.
- **Test failover** sends `X-Simulate-Failure: key_1=429`; expect `key_2` and `fallback_attempts: 1`.
- **Test cache** sends the same unique prompt twice in two new sessions; expect `LIVE_INFERENCE` then `LLM_CACHE` with identical reply and token counts.
- **Test 503 error** sends `X-Simulate-Failure: *=429`; expect the readable `all providers unavailable` error and no stored history.

Health:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok","keys_configured":2,"cache_enabled":true}
```

Live call:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"verify-1","message":"What is the capital of France?"}'
```

Check `metadata.resolution_source == "LIVE_INFERENCE"`, `metadata.key_id == "key_1"`, and the per-request `input_tokens` / `output_tokens`.

Cache hit in a new session with the same first message:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"verify-2","message":"What is the capital of France?"}'
```

Check that the reply is identical, `metadata.resolution_source == "LLM_CACHE"`, `metadata.key_id == null`, `metadata.fallback_attempts == 0`, and token counts match the first response. The cached call should have a lower `latency_ms`.

Failover (use a new, unique message so it is not already cached):

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=429" \
  -d '{"session_id":"verify-failover","message":"Explain failover with a unique sentence."}'
```

Check `metadata.key_id == "key_2"` and `metadata.fallback_attempts == 1`. `X-Simulate-Failure: key_1=500` behaves the same way.

All keys failing:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: *=429" \
  -d '{"session_id":"verify-all-failed","message":"This must fail."}'
```

Expected: `503 {"detail":"all providers unavailable"}`. The following history call returns 404:

```bash
curl http://localhost:8000/sessions/verify-all-failed/messages
```

History and token usage:

```bash
curl http://localhost:8000/sessions/verify-1/messages
```

Each assistant message includes its own `input_tokens` and `output_tokens`; the UI renders them under every assistant reply as `meta-input-tokens` and `meta-output-tokens`.

Backend tests cover health, validation, history, cache hits, different-message cache separation, simulated failover, all-providers-failed, and session-scoped system prompts:

```bash
make test
```

## 5. Setup Used: your editor and AI tools

- Editor/CLI: opencode
- AI assistant: opencode/big-pickle
- Backend: Python 3.13+, FastAPI, LiteLLM, SQLite, pytest
- Frontend: Node.js 22+, React 19, TypeScript, Vite 7
- Documentation source: the supplied assignment PDF, extracted and checked section by section

## 6. Bonus features (if any): what you added beyond the requirements and why

- **Session-scoped system prompt**: `system_prompt` is persisted per session and shown in an optional UI field; later requests reuse it, and history never leaks it.
- **New session button**: lets a reviewer start a fresh session without reloading the page; page load already creates a new session.
- **Mock provider mode**: `LLM_MOCK_RESPONSE` makes local verification and automated tests deterministic without real keys, while production still uses real LiteLLM provider calls.
- **Docker and non-Docker entry points**: `make up` / `make down` run the full stack with Docker, while `make dev-up` / `make dev-down` keep local Python and Node servers usable.
- **JSON-only error contract**: custom exception handlers return `{"detail": ...}` for HTTP and unexpected errors, so the UI never receives an HTML error page.
