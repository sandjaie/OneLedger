// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewQueue } from "@/app/investments/review-queue";
import type { apiFetch } from "@/lib/api/client";
import type { ReviewItemRow } from "@/types/api";

afterEach(cleanup);

describe("ReviewQueue", () => {
  it("links a holding review to its holding and an import review to its import", async () => {
    const rows: Array<ReviewItemRow> = [
      {
        id: "00000000-0000-4000-8000-000000000101",
        reason: "incomplete_lot_allocation",
        isBlocking: false,
        holdingId: "00000000-0000-4000-8000-000000000050",
        securityName: "Synthetic Growth Fund",
        accountName: "Synthetic broker",
        importId: null,
        investmentTransactionId: "00000000-0000-4000-8000-000000000062",
        createdAt: "2026-01-10T00:00:00Z",
      },
      {
        id: "00000000-0000-4000-8000-000000000102",
        reason: "unknown_security",
        isBlocking: true,
        holdingId: null,
        securityName: null,
        accountName: null,
        importId: "00000000-0000-4000-8000-000000000070",
        investmentTransactionId: null,
        createdAt: "2026-01-09T00:00:00Z",
      },
    ];
    const request = vi.fn(async () => rows) as unknown as typeof apiFetch;

    render(<ReviewQueue request={request} />);

    expect((await screen.findByRole("link", { name: "View holding" })).getAttribute("href")).toBe(
      "/investments/00000000-0000-4000-8000-000000000050",
    );
    expect(screen.getByRole("link", { name: "View import" }).getAttribute("href")).toBe(
      "/investments/import/00000000-0000-4000-8000-000000000070",
    );
    expect(screen.getByRole("cell", { name: "Synthetic Growth Fund · Synthetic broker" })).toBeTruthy();
    expect(screen.getByText("2 open")).toBeTruthy();
  });

  it("labels a full page as partial rather than claiming it is the open total", async () => {
    const rows: Array<ReviewItemRow> = Array.from({ length: 20 }, (_, index) => ({
      id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
      reason: "unknown_security",
      isBlocking: true,
      holdingId: null,
      securityName: null,
      accountName: null,
      importId: `00000000-0000-4000-8001-${String(index).padStart(12, "0")}`,
      investmentTransactionId: null,
      createdAt: "2026-01-09T00:00:00Z",
    }));
    const request = vi.fn(async () => rows) as unknown as typeof apiFetch;

    render(<ReviewQueue request={request} />);

    expect(await screen.findByText("20+ loaded")).toBeTruthy();
    expect(screen.queryByText("20 open")).toBeNull();
  });
});
