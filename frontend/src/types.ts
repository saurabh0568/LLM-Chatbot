export type ChatMetadata = {
  resolution_source: "LIVE_INFERENCE" | "LLM_CACHE";
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  key_id: string | null;
  fallback_attempts: number;
};

export type ChatResponse = {
  session_id: string;
  reply: string;
  metadata: ChatMetadata;
};

export type ChatItem = {
  id: string;
  role: "user" | "assistant";
  content: string;
  metadata?: ChatMetadata;
};
