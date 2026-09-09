import "client-only";

import { apiUrl } from "@/lib/api/url";
const REQUEST_TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const controller = new AbortController();
  const callerSignal = init.signal;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  const timeout = setTimeout(
    () => controller.abort(new DOMException("Request timed out", "TimeoutError")),
    REQUEST_TIMEOUT_MS,
  );

  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener("abort", abortFromCaller, { once: true });

  try {
    const response = await fetch(apiUrl(path), {
      ...init,
      credentials: "include",
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new ApiError(response.status, await problem(response));
    }

    if (response.status === 204) {
      return undefined as T;
    }

    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }
}

/** The sentence a refusal was written to show, taken out of the envelope it arrives in.
 *
 * Every refusal this service writes reaches the browser as FastAPI's `{"detail": …}`, and the
 * string inside it is composed for the owner — the date a duplicate first arrived, the layouts
 * that scored, why a batch will not commit. Handed the envelope instead, every screen prints
 * braces and quotes in front of that sentence, so it is unwrapped once here rather than at each
 * of the places that display `ApiError.detail`.
 *
 * Two bodies carry no sentence and must not be shown raw: `main.py` answers a validation failure
 * with a list of field diagnostics, and a proxy or the runtime may answer with HTML. The status
 * text stands in for both — and for itself when HTTP/2 leaves it empty.
 */
async function problem(response: Response): Promise<string> {
  const fallback = response.statusText || `Request failed with status ${response.status}`;
  const body = await response.text().catch(() => "");

  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    // Not JSON at all, so there is no envelope to open and the body is already the message.
    return body || fallback;
  }

  const detail = (parsed as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" ? detail : fallback;
}
