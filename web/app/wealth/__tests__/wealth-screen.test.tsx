// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WealthScreen } from "@/app/wealth/wealth-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  CurrencyRow,
  HouseholdCurrency,
  MonthPositionsResponse,
  RefreshCounts,
  WealthHistoryResponse,
} from "@/types/api";

type Request = typeof apiFetch;

const CURRENCY: HouseholdCurrency = { code: "AED", symbol: "AED", decimals: 4, numberFormat: "international" };
const CURRENCIES: Array<CurrencyRow> = [
  { code: "AED", name: "UAE Dirham", symbol: "AED", decimals: 4, numberFormat: "international" },
  { code: "USD", name: "US Dollar", symbol: "$", decimals: 4, numberFormat: "international" },
];

const history: WealthHistoryResponse = {
  baseCurrency: "AED",
  secondaryCurrency: "USD",
  months: [
    {
      monthEnd: "2024-01-31",
      totalBase: "1500.0000",
      totalSecondary: "750.0000",
      complete: true,
      statusCounts: { available: 1 },
      computedAt: "2026-09-14T00:00:00Z",
    },
    {
      monthEnd: "2024-02-29",
      totalBase: "1700.0000",
      totalSecondary: "680.0000",
      complete: false,
      statusCounts: { available: 1, before_opening: 1 },
      computedAt: "2026-09-14T00:00:00Z",
    },
  ],
};

const january: MonthPositionsResponse = {
  monthEnd: "2024-01-31",
  baseCurrency: "AED",
  secondaryCurrency: "USD",
  positions: [
    {
      accountId: "00000000-0000-4000-8000-0000000000a1",
      accountName: "Synthetic savings account",
      holdingId: null,
      securityName: null,
      valuationMode: "ledger_balance",
      currency: "AED",
      nativeValue: "1500.0000",
      evidenceDate: "2024-01-01",
      rateToBase: "1",
      rateToBaseDate: "2024-01-01",
      rateToBaseCarried: false,
      rateToSecondary: "0.5000000000",
      rateToSecondaryDate: "2024-01-01",
      rateToSecondaryCarried: false,
      valueBase: "1500.0000",
      valueSecondary: "750.0000",
      status: "available",
    },
  ],
};

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn((path: string, init?: RequestInit) => Promise.resolve(handler(path, init))) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("wealth screen", () => {
  it("renders the month table from server strings, labelling an incomplete month", async () => {
    const request = requestFrom((path) => {
      if (path === "/wealth-history") return history;
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    const table = await screen.findByRole("table", { name: /Month-end net worth/ });
    expect(within(table).getByText("2024-01-31")).toBeTruthy();
    expect(within(table).getByText("AED 1,500.0000")).toBeTruthy();
    expect(within(table).getByText("Complete")).toBeTruthy();
    expect(within(table).getByText(/Incomplete/)).toBeTruthy();
  });

  it("selecting a month shows its positions table with server strings only", async () => {
    const request = requestFrom((path) => {
      if (path === "/wealth-history") return history;
      if (path === "/wealth-history/2024-01-31") return january;
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "2024-01-31" }));

    const positions = await screen.findByRole("table", { name: /2024-01-31, one row per/ });
    expect(within(positions).getByText("Synthetic savings account")).toBeTruthy();
    expect(within(positions).getByText("ledger_balance")).toBeTruthy();
    expect(within(positions).getByText("available")).toBeTruthy();
    expect(within(positions).getAllByText("AED 1,500.0000").length).toBeGreaterThan(0);
  });

  it("refresh posts to the refresh route and reloads the month table", async () => {
    let refreshed = false;
    const request = requestFrom((path, init) => {
      if (path === "/wealth-history") {
        return refreshed
          ? { ...history, months: [{ ...history.months[0], totalBase: "1600.0000" }] }
          : { ...history, months: [] };
      }
      if (path === "/wealth-history/refresh" && init?.method === "POST") {
        refreshed = true;
        return { monthsComputed: 1, positionsComputed: 1, byStatus: { available: 1 } } satisfies RefreshCounts;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    expect(await screen.findByText("No history yet. Refresh to compute it.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));

    await waitFor(() => expect(screen.getByText("AED 1,600.0000")).toBeTruthy());
  });
});
