import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "@/lib/api/client";

const stalled = <T>(signal?: AbortSignal | null) =>
  new Promise<T>((_resolve, reject) => {
    if (signal?.aborted) reject(signal.reason);
    else signal?.addEventListener(
      "abort", () => reject(signal.reason), { once: true },
    );
  });
const stalledFetch = (_url: RequestInfo | URL, init?: RequestInit) =>
  stalled<Response>(init?.signal);

describe("apiFetch", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("returns successful JSON and includes browser credentials", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ready" }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", request);

    await expect(apiFetch<{ status: string }>("/health")).resolves.toEqual({
      status: "ready",
    });
    expect(request).toHaveBeenCalledWith(
      expect.stringContaining("/health"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("returns undefined for a successful response without content", async () => {
    const request = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", request);

    await expect(
      apiFetch<void>("/auth/logout", { method: "POST" }),
    ).resolves.toBeUndefined();
    expect(request).toHaveBeenCalledWith(
      expect.stringContaining("/auth/logout"),
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("preserves caller method, body, and Headers instance", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ accepted: true }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", request);
    const headers = new Headers({
      "Content-Type": "application/json",
      "X-Request-ID": "trace-123",
    });

    await apiFetch<{ accepted: boolean }>("/entries", {
      method: "POST",
      body: '{"note":"draft"}',
      headers,
    });

    expect(request).toHaveBeenCalledWith(
      expect.stringContaining("/entries"),
      expect.objectContaining({
        method: "POST",
        body: '{"note":"draft"}',
        headers,
        credentials: "include",
      }),
    );
  });

  it("throws an ApiError with the response status and detail", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response("sign in required", {
        status: 401,
        statusText: "Unauthorized",
      }),
    );
    vi.stubGlobal("fetch", request);

    const error = await apiFetch("/entries").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 401,
      detail: "sign in required",
      message: "401: sign in required",
    });
  });

  it("keeps the timeout with a caller signal when AbortSignal.any is unavailable", async () => {
    vi.useFakeTimers();
    const timeout = AbortSignal.timeout.bind(AbortSignal);
    vi.stubGlobal("AbortSignal", { timeout });
    vi.stubGlobal("fetch", vi.fn(stalledFetch));
    const result = apiFetch("/household", { signal: new AbortController().signal });
    const rejection = expect(result).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersByTimeAsync(10_000);
    await rejection;
  });

  it("keeps the timeout active while the response body is read", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => stalled(init?.signal),
      } as Response),
    ));
    const result = apiFetch("/household");
    const rejection = expect(result).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersByTimeAsync(10_000);
    await rejection;
  });

  it("preserves caller cancellation while a timeout is active", async () => {
    vi.stubGlobal("fetch", vi.fn(stalledFetch));
    const caller = new AbortController();
    const result = apiFetch("/fx/on", { signal: caller.signal });
    caller.abort(new DOMException("superseded", "AbortError"));
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects immediately when the caller signal is already aborted", async () => {
    vi.stubGlobal("fetch", vi.fn(stalledFetch));
    const caller = new AbortController();
    caller.abort(new DOMException("superseded", "AbortError"));
    await expect(apiFetch("/fx/on", { signal: caller.signal }))
      .rejects.toMatchObject({ name: "AbortError" });
  });
});
