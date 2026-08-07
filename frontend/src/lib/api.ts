/**
 * Client for the FastAPI gateway.
 *
 * The stream endpoint is a POST, so `EventSource` cannot be used — it only
 * issues GETs and offers no way to send a body. Instead the response is read as
 * a `ReadableStream` and the SSE wire format is parsed manually. That is a few
 * more lines than EventSource, and it buys the ability to send the query in a
 * body rather than smuggling it through a URL where it would land in every
 * access log along the way.
 */

export type RouteEvent =
  | { event: "classified"; data: ClassifiedPayload }
  | { event: "step"; data: StepPayload }
  | { event: "sources"; data: { sources: SourcePayload[] } }
  | { event: "answer"; data: { answer: string } }
  | { event: "audit"; data: AuditPayload }
  | { event: "error"; data: { detail: string; error?: string } };

export interface ClassifiedPayload {
  queryClass: string;
  path: string;
  confidence: number;
  rationale: string;
  isLlm: boolean;
  method: string;
  usage: { costUsd: number; latencyMs: number };
}

export interface StepPayload {
  label: string;
  detail: string;
  elapsedMs: number;
}

export interface SourcePayload {
  ref: string;
  kind: string;
  detail: string;
}

export interface AuditPayload {
  queryId: string;
  path: string;
  queryClass: string;
  confidence: number;
  rationale: string;
  answer: string;
  latencyMs: number;
  costUsd: number;
  retries: number;
  fullySpecified: boolean;
  warnings: string[];
  sources: SourcePayload[];
}

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** How long to wait for the gateway before declaring it absent. */
const HEALTH_TIMEOUT_MS = 2500;

/**
 * Probe the gateway. Used to decide live vs fixture mode.
 *
 * The timeout is load-bearing, not defensive. Until the probe settles the
 * console sits in `checking`, which disables every control — so a gateway that
 * accepts the connection and then never answers (a suspended free-tier dyno is
 * exactly this) leaves the UI permanently inert with no error to explain it.
 * Resolving `false` degrades to fixture mode, which the page states on screen.
 */
export async function checkHealth(signal?: AbortSignal): Promise<boolean> {
  const timeout = AbortSignal.timeout(HEALTH_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE}/health`, {
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      cache: "no-store",
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Normalise SSE line endings.
 *
 * The spec permits `\r\n`, `\n` *or* a bare `\r` as a line terminator, and
 * servers genuinely differ — sse-starlette emits `\r\n`. Splitting on `\n\n`
 * alone silently never finds a frame boundary against a `\r\n` server, so the
 * stream connects, returns 200, and delivers nothing.
 */
function normaliseNewlines(text: string): string {
  return text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

/** Minimal SSE frame parser: accumulates `event:` / `data:` pairs. */
function parseFrame(frame: string): RouteEvent | null {
  let name = "";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }

  if (!name || dataLines.length === 0) return null;
  try {
    return { event: name, data: JSON.parse(dataLines.join("\n")) } as RouteEvent;
  } catch {
    return null;
  }
}

/**
 * Stream a routed query, yielding events as the router emits them.
 *
 * The gateway sends `classified` first by design — the Route Badge has to land
 * before the retrieval steps, because seeing *why* a path was chosen before
 * seeing what it found is the whole demonstration.
 */
export async function* streamQuery(
  query: string,
  signal?: AbortSignal,
): AsyncGenerator<RouteEvent> {
  const response = await fetch(`${API_BASE}/query/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Gateway returned ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += normaliseNewlines(decoder.decode(value, { stream: true }));

      // SSE frames are separated by a blank line. Anything after the last
      // separator is a partial frame and stays in the buffer.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const parsed = parseFrame(frame);
        if (parsed) yield parsed;
      }
    }

    const trailing = parseFrame(buffer);
    if (trailing) yield trailing;
  } finally {
    reader.releaseLock();
  }
}
