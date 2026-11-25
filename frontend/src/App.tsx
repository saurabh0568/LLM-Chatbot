import { useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { sendChat } from "./api";
import type { ChatItem, ChatMetadata, ChatResponse } from "./types";

type DiagnosticResult = {
  id: string;
  label: string;
  results: ChatResponse[];
  error?: string;
};

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function createSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `s-${crypto.randomUUID()}`;
  }
  return createId("s");
}

function shortSessionId(sessionId: string): string {
  return sessionId.length > 22 ? `${sessionId.slice(0, 22)}…` : sessionId;
}

function MetadataPanel({ metadata }: { metadata: ChatMetadata }) {
  return (
    <div className="metadata">
      <span className="metadata-item">
        <span className="metadata-label">Source</span>
        <strong data-testid="meta-source">{metadata.resolution_source}</strong>
      </span>
      <span className="metadata-item">
        <span className="metadata-label">Model</span>
        <strong data-testid="meta-model">{metadata.model}</strong>
      </span>
      <span className="metadata-item">
        <span className="metadata-label">Latency</span>
        <strong data-testid="meta-latency">{metadata.latency_ms} ms</strong>
      </span>
      <span className="metadata-item">
        <span className="metadata-label">Input tokens</span>
        <strong data-testid="meta-input-tokens">{metadata.input_tokens}</strong>
      </span>
      <span className="metadata-item">
        <span className="metadata-label">Output tokens</span>
        <strong data-testid="meta-output-tokens">{metadata.output_tokens}</strong>
      </span>
    </div>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState(createSessionId);
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diagnosticResults, setDiagnosticResults] = useState<DiagnosticResult[]>([]);

  function diagnosticMessage(kind: string, fallback: string): string {
    return `${input.trim() || fallback} [${createId(kind)}]`;
  }

  async function runDiagnostic(
    label: string,
    message: string,
    options: { simulateFailure?: string; repeat?: boolean } = {},
  ) {
    if (sending) {
      return;
    }

    const resultId = createId("diagnostic");
    const results: ChatResponse[] = [];
    setError(null);
    setSending(true);
    setDiagnosticResults((current) => [{ id: resultId, label, results }, ...current].slice(0, 4));

    try {
      const rounds = options.repeat ? 2 : 1;
      for (let round = 0; round < rounds; round += 1) {
        const result = await sendChat(
          {
            session_id: createSessionId(),
            message,
            system_prompt: systemPrompt.trim() || undefined,
          },
          { simulateFailure: options.simulateFailure },
        );
        results.push(result);
        setDiagnosticResults((current) =>
          current.map((item) => (item.id === resultId ? { ...item, results: [...results] } : item)),
        );
      }
    } catch (err) {
      const messageText = err instanceof Error ? err.message : "Something went wrong.";
      setError(messageText);
      setDiagnosticResults((current) =>
        current.map((item) => (item.id === resultId ? { ...item, error: messageText } : item)),
      );
    } finally {
      setSending(false);
    }
  }

  async function sendMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) {
      return;
    }

    setMessages((current) => [
      ...current,
      { id: createId("user"), role: "user", content: trimmed },
    ]);
    setInput("");
    setError(null);
    setSending(true);

    try {
      const result = await sendChat({
        session_id: sessionId,
        message: trimmed,
        system_prompt: systemPrompt.trim() || undefined,
      });
      setMessages((current) => [
        ...current,
        {
          id: createId("assistant"),
          role: "assistant",
          content: result.reply,
          metadata: result.metadata,
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSending(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendMessage(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage(input);
    }
  }

  function startNewSession() {
    setSessionId(createSessionId());
    setMessages([]);
    setInput("");
    setError(null);
    setDiagnosticResults([]);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">LLM</span>
          <div>
            <h1>LLM Chatbot</h1>
            <p>Chat here. Verify failover, cache, and errors in the tools panel.</p>
          </div>
        </div>
        <button type="button" className="neutral-button" onClick={startNewSession}>
          New session
        </button>
      </header>

      <div className="workspace">
        <section className="chat-card" aria-labelledby="chat-title">
          <div className="chat-card-header">
            <div>
              <span className="eyebrow">Primary workspace</span>
              <h2 id="chat-title">Chat with the LLM</h2>
            </div>
            <div className="session-chip" title={sessionId}>
              <span>Session</span>
              <code>{shortSessionId(sessionId)}</code>
            </div>
          </div>

          <section className="chat-log" aria-live="polite">
            {messages.length === 0 && (
              <div className="empty-state">
                <strong>Start the conversation.</strong>
                <span>Type a message below and press Enter or Send message.</span>
              </div>
            )}

            {messages.map((item) =>
              item.role === "user" ? (
                <div key={item.id} data-testid="user-message" className="bubble user-bubble">
                  <span className="bubble-label">You</span>
                  <p>{item.content}</p>
                </div>
              ) : (
                <article key={item.id} data-testid="assistant-message" className="bubble assistant-bubble">
                  <span className="bubble-label">Assistant</span>
                  <p className="assistant-reply">{item.content}</p>
                  {item.metadata && <MetadataPanel metadata={item.metadata} />}
                </article>
              ),
            )}

            {error && (
              <div data-testid="error-message" role="alert" className="error-message">
                {error}
              </div>
            )}

            {sending && <div className="typing-indicator">Waiting for the assistant…</div>}
          </section>

          <form className="composer" onSubmit={handleSubmit}>
            <label className="composer-label" htmlFor="chat-input">
              Message to LLM
            </label>
            <div className="composer-row">
              <textarea
                id="chat-input"
                data-testid="chat-input"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask the LLM anything…"
                rows={3}
                maxLength={4000}
                aria-label="Message"
                disabled={sending}
              />
              <button data-testid="send-button" type="submit" disabled={sending || !input.trim()}>
                {sending ? "Sending…" : "Send message"}
              </button>
            </div>
            <p className="composer-hint">Enter sends · Shift+Enter adds a new line</p>
          </form>
        </section>

        <aside className="side-panel" aria-label="Optional controls">
          <section className="panel tools-panel" aria-labelledby="diagnostics-title">
            <div className="panel-header">
              <span className="eyebrow">Optional</span>
              <h2 id="diagnostics-title">Verification tools</h2>
              <p>These run separate test requests; they do not replace the chat above.</p>
            </div>

            <div className="diagnostic-actions">
              <button
                type="button"
                className="neutral-button"
                data-testid="test-live-button"
                disabled={sending}
                onClick={() =>
                  void runDiagnostic("LiteLLM live", diagnosticMessage("live", "Explain caching in one sentence."))
                }
              >
                Test live
              </button>
              <button
                type="button"
                className="neutral-button"
                data-testid="test-failover-button"
                disabled={sending}
                onClick={() =>
                  void runDiagnostic("Key failover", diagnosticMessage("failover", "Explain failover in one sentence."), {
                    simulateFailure: "key_1=429",
                  })
                }
              >
                Test failover
              </button>
              <button
                type="button"
                className="neutral-button"
                data-testid="test-cache-button"
                disabled={sending}
                onClick={() =>
                  void runDiagnostic("Cache hit", diagnosticMessage("cache", "Explain response caching in one sentence."), {
                    repeat: true,
                  })
                }
              >
                Test cache
              </button>
              <button
                type="button"
                className="danger-button"
                data-testid="test-all-failed-button"
                disabled={sending}
                onClick={() =>
                  void runDiagnostic("All keys failed", diagnosticMessage("failed", "This request must fail."), {
                    simulateFailure: "*=429",
                  })
                }
              >
                Test 503 error
              </button>
            </div>

            {diagnosticResults.length > 0 && (
              <div className="diagnostic-results" data-testid="diagnostic-results">
                {diagnosticResults.map((item) => (
                  <article key={item.id} data-testid="diagnostic-result" className="diagnostic-card">
                    <div className="diagnostic-card-header">
                      <strong>{item.label}</strong>
                      <span>{item.results.length > 1 ? "2 sessions" : "1 request"}</span>
                    </div>
                    {item.results.map((result, index) => (
                      <div key={`${item.id}-${index}`} className="diagnostic-reply">
                        <span className="diagnostic-reply-label">
                          {item.results.length > 1 ? `Request ${index + 1}` : "Response"}
                        </span>
                        <p>{result.reply}</p>
                        <MetadataPanel metadata={result.metadata} />
                      </div>
                    ))}
                    {item.error && <div className="diagnostic-error">{item.error}</div>}
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="panel settings-panel" aria-labelledby="settings-title">
            <div className="panel-header">
              <span className="eyebrow">Optional</span>
              <h2 id="settings-title">Session instructions</h2>
              <p>Set a system prompt for this session, or leave it blank for the default.</p>
            </div>
            <label className="system-prompt-field" htmlFor="system-prompt">
              <span>System prompt (session override)</span>
              <textarea
                id="system-prompt"
                data-testid="system-prompt-input"
                value={systemPrompt}
                onChange={(event) => setSystemPrompt(event.target.value)}
                placeholder="You are a helpful assistant."
                rows={3}
                disabled={sending}
              />
            </label>
          </section>
        </aside>
      </div>
    </main>
  );
}
