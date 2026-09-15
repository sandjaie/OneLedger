import "client-only";

import { apiUrl } from "@/lib/api/url";
const REQUEST_TIMEOUT_MS = 10_000;

/** For the handful of calls that parse a statement, commit an import, or refresh prices/wealth
 * history/rule analytics — genuinely slow server work the 10-second default would cut off. */
export const LONG_REQUEST_TIMEOUT_MS = 120_000;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

/** The service's own refusal sentence when the error carries one; otherwise the caller's fallback,
 * because a network or browser failure has nothing to say about the request. */
export function errorText(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail ? detail : fallback;
}

function isTimeout(error: unknown): boolean {
  return error instanceof DOMException && error.name === "TimeoutError";
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = REQUEST_TIMEOUT_MS, ...requestInit } = init;
  const controller = new AbortController();
  const callerSignal = requestInit.signal;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  const timeout = setTimeout(
    () => controller.abort(new DOMException("Request timed out", "TimeoutError")),
    timeoutMs,
  );

  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener("abort", abortFromCaller, { once: true });

  try {
    const response = await fetch(apiUrl(path), {
      ...requestInit,
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
  } catch (error) {
    // A write that timed out may still be running on the server (the household lock does not
    // release just because the browser gave up waiting) — that is a different sentence from "this
    // failed", so it is distinguished here, once, from the plain `TimeoutError` a GET raises.
    const method = (requestInit.method ?? "GET").toUpperCase();
    if (isTimeout(error) && method !== "GET") {
      throw new ApiError(0, "The server may still be finishing this request. Reload before retrying.");
    }
    throw error;
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
 * `main.py` answers a validation failure with a list of field diagnostics rather than one
 * sentence — the layout mapper's own draft is validated this way — so a list `detail` is read as
 * its `msg` strings joined by "; ", which is readable without a bespoke renderer per field. A
 * proxy or the runtime may also answer with HTML, carrying no sentence at all; the status text
 * stands in for that case — and for itself when HTTP/2 leaves it empty.
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
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item !== null && typeof item === "object" ? (item as { msg?: unknown }).msg : undefined))
      .filter((msg): msg is string => typeof msg === "string");
    if (messages.length > 0) return messages.join("; ");
  }
  return fallback;
}
