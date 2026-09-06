import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "@/lib/api/client";

describe("apiFetch", () => {
  afterEach(() => {
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
});
