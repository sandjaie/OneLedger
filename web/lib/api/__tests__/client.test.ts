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

  it("unwraps the service's own sentence from the error envelope", async () => {
    // Every refusal this service writes arrives as FastAPI's {"detail": …} envelope, and the
    // sentence inside it is written for the owner — the date a duplicate first arrived, the
    // layouts that scored, why a batch will not commit. Handing the envelope to a screen puts
    // braces and quotes in front of that sentence, so it is unwrapped here rather than at each
    // of the nine places that display it.
    const request = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "this file was already imported on 2026-01-05" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", request);

    const error = await apiFetch("/parse").catch((caught: unknown) => caught);

    expect(error).toMatchObject({
      status: 409,
      detail: "this file was already imported on 2026-01-05",
    });
  });

  it("joins a list detail's msg strings with '; '", async () => {
    // main.py answers a validation failure — the layout mapper's own draft included — with a
    // list of field diagnostics rather than one sentence. Rendering "[object Object]" is worse
    // than saying nothing, and showing only the first diagnostic hides the rest of what failed,
    // so every msg is read out and joined.
    const request = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            { type: "missing", loc: ["body", "name"], msg: "field required" },
            { type: "value_error", loc: ["body", "dateFormat"], msg: "dateFormat must be one of: dd/MM/yyyy" },
          ],
        }),
        { status: 422, statusText: "Unprocessable Content", headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", request);

    const error = await apiFetch("/layouts").catch((caught: unknown) => caught);

    expect(error).toMatchObject({
      status: 422,
      detail: "field required; dateFormat must be one of: dd/MM/yyyy",
    });
  });

  it("falls back to the status text when a list detail carries no msg strings", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: [{ type: "missing", loc: ["body"] }] }), {
        status: 422,
        statusText: "Unprocessable Content",
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", request);

    const error = await apiFetch("/accounts").catch((caught: unknown) => caught);

    expect(error).toMatchObject({ status: 422, detail: "Unprocessable Content" });
  });

  it("names the status when the refusal arrives with no body and no status text", async () => {
    // A gateway can answer with neither, and HTTP/2 carries no reason phrase at all. An empty
    // alert is the one outcome worse than a blunt one, so the code itself is the last resort.
    const request = vi.fn().mockResolvedValue(
      new Response(null, { status: 502, statusText: "" }),
    );
    vi.stubGlobal("fetch", request);

    const error = await apiFetch("/health").catch((caught: unknown) => caught);

    expect(error).toMatchObject({
      status: 502,
      detail: "Request failed with status 502",
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
