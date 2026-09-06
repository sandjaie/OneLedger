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
      const detail = await response.text().catch(() => response.statusText);
      throw new ApiError(response.status, detail);
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
