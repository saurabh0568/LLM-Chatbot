import type { ChatResponse } from "./types";

const API_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

type ChatPayload = {
  session_id: string;
  message: string;
  system_prompt?: string;
};

type SendChatOptions = {
  simulateFailure?: string;
};

function detailToString(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (item && typeof item === "object" && "msg" in item) {
        return String(item.msg);
      }
      return String(item);
    });
    return parts.join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String(detail.message);
  }
  return "The request could not be completed.";
}

export async function sendChat(
  payload: ChatPayload,
  options: SendChatOptions = {},
): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (options.simulateFailure) {
    headers["X-Simulate-Failure"] = options.simulateFailure;
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error("Cannot reach the backend. Check the API URL and try again.");
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    throw new Error(detailToString(detail));
  }

  if (!body || typeof body !== "object" || !("reply" in body) || !("metadata" in body)) {
    throw new Error("The backend returned an unexpected response.");
  }
  return body as ChatResponse;
}
