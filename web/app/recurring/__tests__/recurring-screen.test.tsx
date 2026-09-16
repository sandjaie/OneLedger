// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RecurringScreen } from "@/app/recurring/recurring-screen";
import { type apiFetch } from "@/lib/api/client";
import type { CurrencyRow, RecurringResponse, RecurringSeriesRow } from "@/types/api";

type Request = typeof apiFetch;

const CURRENCIES: Array<CurrencyRow> = [
  { code: "USD", name: "US Dollar", symbol: "$", decimals: 4, numberFormat: "international" },
];

function aSeries(overrides: Partial<RecurringSeriesRow> = {}): RecurringSeriesRow {
  return {
    id: "series-1",
    label: "Synthetic streaming service",
    identity: { merchantId: null, matchKey: "synthetic streaming service" },
    cadence: "monthly",
    categoryId: null,
    categoryName: null,
    isSubscription: true,
    detectionMethod: "detected",
    needsName: false,
    nativeAmount: "15.0000",
    nativeCurrency: "USD",
    priceChangedFrom: null,
    priceChangedOn: null,
    lastSeenOn: "2026-08-01",
    status: "active",
    ...overrides,
  };
}

const empty: RecurringResponse = { series: [] };

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("recurring screen", () => {
  it("refreshes once on open, then lists the returned series from server strings", async () => {
    const response: RecurringResponse = { series: [aSeries()] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      throw new Error(`unexpected path ${path}`);
    });

    const { rerender } = render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);

    await screen.findByText(/Synthetic streaming service/);
    expect(screen.getByText(/\$15\.0000/)).toBeTruthy();
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[0][0]).toBe("/recurring/refresh");
    const [, refreshInit] = request.mock.calls[0] as unknown as [string, RequestInit];
    expect(refreshInit).toEqual(expect.objectContaining({ method: "POST" }));
    expect(request.mock.calls[1][0]).toBe("/recurring");

    // A re-render with the same stable `request` must not run refresh again.
    rerender(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  });

  it("flips the subscription flag by posting isSubscription", async () => {
    const response: RecurringResponse = { series: [aSeries({ isSubscription: true })] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      if (path === "/recurring/series-1") return Promise.resolve(aSeries({ isSubscription: false }));
      throw new Error(`unexpected path ${path}`);
    });

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findByText(/Synthetic streaming service/);

    fireEvent.click(screen.getByRole("button", { name: "Not a subscription" }));

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1",
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ isSubscription: false }) }),
      ),
    );
  });

  it("asks before removing, and only posts remove on confirmation", async () => {
    const response: RecurringResponse = { series: [aSeries()] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      if (path === "/recurring/series-1/remove") return Promise.resolve({ removed: true });
      throw new Error(`unexpected path ${path}`);
    });
    const confirm = vi.spyOn(window, "confirm");

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findByText(/Synthetic streaming service/);

    confirm.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(request).not.toHaveBeenCalledWith(
      "/recurring/series-1/remove",
      expect.anything(),
    );

    confirm.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1/remove",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("shows nothing found instead of an empty table", async () => {
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(empty);
      throw new Error(`unexpected path ${path}`);
    });

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    expect(await screen.findByText(/no recurring payment has been found yet/i)).toBeTruthy();
  });
});
