import "client-only";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
  const timeout = AbortSignal.timeout(10_000);
  const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    signal,
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText);
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
