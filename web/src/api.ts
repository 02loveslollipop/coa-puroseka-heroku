export type Relation = {
  source: string;
  column: string;
  target: string;
  target_column: string;
  name: string;
  definition: string;
  status: string;
  unresolved_count: number;
};
export type Entity = {
  class: string;
  label: string;
  definition: string;
  aliases?: string[];
  row_count: number;
  fields: Record<string, { type: string; definition: string }>;
};
export type Catalog = {
  version: string;
  description: string;
  ranking_source_files?: number;
  data_coverage?: {
    ranking_events_with_observations: number;
    metadata_events: number;
  };
  tables: Record<string, Entity>;
  relationships: Relation[];
  rules: string[];
};
export type Run = {
  mode: string;
  question: string;
  status: string;
  answer: string;
  error?: string;
  sql: string | null;
  rows: Record<string, unknown>[];
  truncated?: boolean;
  latency_ms: number;
  usage: Record<string, number>;
  context: unknown;
  query_messages: unknown;
  answer_messages?: unknown;
  model: string;
  semantic_version: string | null;
  query_purpose?: string;
};
let csrf = "";
// Public build configuration only. Sessions remain in an HttpOnly API cookie.
const apiOrigin = (import.meta.env.VITE_API_ORIGIN || "").replace(/\/$/, "");
export function apiUrl(path: string): string { return apiOrigin + "/api" + path; }
export function setCsrf(value: string) { csrf = value; }
export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter = 0) { super(message); }
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  if (path === "/inference" && init?.method === "POST") {
    return runInference<T>("/inference/jobs", JSON.parse(String(init.body)));
  }
  const headers = new Headers(init?.headers);
  if (init?.method && !["GET", "HEAD"].includes(init.method.toUpperCase())) {
    headers.set("X-CSRF-Token", csrf);
  }
  const r = await fetch(apiUrl(path), { ...init, headers, credentials: "include" });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    if (r.status === 401 && path !== "/auth/login") window.dispatchEvent(new Event("coa-session-expired"));
    throw new ApiError(r.status, typeof body.detail === "string" ? body.detail : `Request failed (${r.status}).`, Number(r.headers.get("Retry-After")) || 0);
  }
  return r.json();
}

export type Activity = { sequence: number; kind: "tool" | "model"; name: string; status: string; latency_ms?: number };
export type ExecutionProgress = { job_id: string; activity: Activity[] };

export async function runInference<T>(path: string, body: unknown, onProgress?: (progress: ExecutionProgress) => void): Promise<T> {
  const job = await api<{ id: string }>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  onProgress?.({ job_id: job.id, activity: [] });
  for (let attempt = 0; attempt < 600; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const state = await api<{ state: string; result: T; activity?: Activity[] }>(`/jobs/${job.id}`);
    onProgress?.({ job_id: job.id, activity: state.activity || [] });
    if (state.state === "done") return state.result;
    if (state.state === "expired") throw new Error("The response was interrupted. Submit the question again.");
  }
  throw new Error("The response took too long. Try again.");
}

export type TokenUsage = {
  enabled: boolean; token_limit: number; window_seconds: number;
  used_tokens: number; reserved_tokens: number; remaining_tokens: number; resets_at: string;
};
