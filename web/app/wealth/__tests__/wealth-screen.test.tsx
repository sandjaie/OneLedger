// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WealthScreen } from "@/app/wealth/wealth-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  CurrencyRow,
  HouseholdCurrency,
  MonthAttributionResponse,
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
      chartRatio: "1.0000",
      chartCoordinate: 10000,
    },
    {
      // Fails closed: an incomplete month's totals are null, never a partial sum — and its
      // chartRatio is null too, so the chart draws no bar for it.
      monthEnd: "2024-02-29",
      totalBase: null,
      totalSecondary: null,
      complete: false,
      statusCounts: { available: 1, before_opening: 1 },
      computedAt: "2026-09-14T00:00:00Z",
      chartRatio: null,
      chartCoordinate: null,
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

    const table = await screen.findByRole("table", { name: /Month-end net worth, in/ });
    expect(within(table).getByText("2024-01-31")).toBeTruthy();
    expect(within(table).getByText("AED 1,500.0000")).toBeTruthy();
    expect(within(table).getByText("Complete")).toBeTruthy();
    expect(within(table).getByText("Incomplete month")).toBeTruthy();
    expect(within(table).getByText("Unavailable — incomplete")).toBeTruthy();
  });

  it("draws the Nova trend from server coordinates with an exact-value gap", async () => {
    const request = requestFrom((path) => {
      if (path === "/wealth-history") return history;
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    const table = await screen.findByRole("table", { name: /Month-end net worth.*exact values/ });
    expect(within(table).getByText("AED 1,500.0000")).toBeTruthy();
    expect(within(table).getByText("Not available")).toBeTruthy();
  });

  it("focuses the selected panel when reviewing an incomplete month", async () => {
    const request = requestFrom((path) => {
      if (path === "/wealth-history") return history;
      if (path === "/wealth-history/2024-02-29") return { ...january, monthEnd: "2024-02-29", positions: [] };
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: /Review incomplete month/ }));
    const heading = await screen.findByRole("heading", { name: "2024-02-29 positions" });
    expect(document.activeElement).toBe(heading.closest("section"));
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
    expect(within(positions).getByText("Available")).toBeTruthy();
    expect(within(positions).getAllByText("AED 1,500.0000").length).toBeGreaterThan(0);
  });

  it("labels each position status and links the ones that leave a figure unavailable", async () => {
    const withGaps: MonthPositionsResponse = {
      ...january,
      positions: [
        { ...january.positions[0], accountId: "acct-price", status: "missing_price" },
        { ...january.positions[0], accountId: "acct-snapshot", status: "no_snapshot" },
        { ...january.positions[0], accountId: "acct-rate", status: "missing_rate" },
      ],
    };
    const request = requestFrom((path) => {
      if (path === "/wealth-history") return history;
      if (path === "/wealth-history/2024-01-31") return withGaps;
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "2024-01-31" }));
    const positions = await screen.findByRole("table", { name: /2024-01-31, one row per/ });

    expect(within(positions).getByRole("link", { name: "Missing price" })).toHaveProperty("pathname", "/investments");
    expect(within(positions).getByRole("link", { name: "No valuation" })).toHaveProperty("pathname", "/accounts");
    expect(within(positions).getByRole("link", { name: "Missing exchange rate" }).getAttribute("href")).toBe("/settings#fx-title");
  });

  it("shows an incomplete month's status counts, the refresh counts, and last refreshed", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/wealth-history") return history;
      if (path === "/wealth-history/refresh" && init?.method === "POST") {
        return { monthsComputed: 2, positionsComputed: 3, byStatus: { available: 2, before_opening: 1 } } satisfies RefreshCounts;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    const table = await screen.findByRole("table", { name: /Month-end net worth, in/ });
    expect(within(table).getByText("Before opening: 1")).toBeTruthy();
    fireEvent.click(screen.getByText("How this history is stored"));
    expect(screen.getByText(/Last refreshed /)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));

    expect(await screen.findByText("Refreshed 2 months, 3 positions.")).toBeTruthy();
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

    await waitFor(() => expect(screen.getAllByText("AED 1,600.0000").length).toBeGreaterThan(0));
  });

  it("reloads the selected month's positions after a refresh", async () => {
    let refreshed = false;
    const updatedJanuary: MonthPositionsResponse = {
      ...january,
      positions: [{ ...january.positions[0], nativeValue: "1800.0000", valueBase: "1800.0000" }],
    };
    const request = requestFrom((path, init) => {
      if (path === "/wealth-history") return history;
      if (path === "/wealth-history/2024-01-31") return refreshed ? updatedJanuary : january;
      if (path === "/wealth-history/refresh" && init?.method === "POST") {
        refreshed = true;
        return { monthsComputed: 1, positionsComputed: 1, byStatus: { available: 1 } } satisfies RefreshCounts;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "2024-01-31" }));
    const positions = await screen.findByRole("table", { name: /2024-01-31, one row per/ });
    expect(within(positions).getAllByText("AED 1,500.0000").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));

    await waitFor(() =>
      expect(within(positions).getAllByText("AED 1,800.0000").length).toBeGreaterThan(0),
    );
  });

  it("selecting a month shows the attribution waterfall with the residual, from server strings", async () => {
    const attributionBody: MonthAttributionResponse = {
      monthEnd: "2024-01-31",
      baseCurrency: "AED",
      secondaryCurrency: "USD",
      base: {
        available: true,
        reasons: [],
        opening: "1000.0000",
        closing: "1500.0000",
        actualChange: "500.0000",
        retainedIncome: "500.0000",
        investmentIncome: "0.0000",
        internalMovementsAndCosts: "0.0000",
        otherExternalChanges: "0.0000",
        marketMovement: "0.0000",
        assetRevaluation: "0.0000",
        fxTranslation: "0.0000",
        residual: "0.0000",
      },
      secondary: {
        available: true,
        reasons: [],
        opening: "500.0000",
        closing: "750.0000",
        actualChange: "250.0000",
        retainedIncome: "250.0000",
        investmentIncome: "0.0000",
        internalMovementsAndCosts: "0.0000",
        otherExternalChanges: "0.0000",
        marketMovement: "0.0000",
        assetRevaluation: "0.0000",
        fxTranslation: "0.0000",
        residual: "0.0000",
      },
    };
    const request = requestFrom((path) => {
      if (path === "/wealth-history") return history;
      if (path === "/wealth-history/2024-01-31") return january;
      if (path === "/wealth-history/2024-01-31/attribution") return attributionBody;
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "2024-01-31" }));

    const waterfall = await screen.findByText("Retained income");
    const section = waterfall.closest("dl");
    expect(section).toBeTruthy();
    expect(within(section as HTMLElement).getAllByText("AED 500.0000").length).toBeGreaterThan(0);
    expect(within(section as HTMLElement).getByText("Residual")).toBeTruthy();
    expect(within(section as HTMLElement).getAllByText("AED 0.0000").length).toBeGreaterThan(0);
  });

  it("labels an unavailable month's attribution with its reasons instead of a waterfall", async () => {
    const unavailable: MonthAttributionResponse = {
      monthEnd: "2024-01-31",
      baseCurrency: "AED",
      secondaryCurrency: "USD",
      base: {
        available: false,
        reasons: ["Synthetic savings account is before_opening at 2024-01-31"],
        opening: null,
        closing: null,
        actualChange: null,
        retainedIncome: null,
        investmentIncome: null,
        internalMovementsAndCosts: null,
        otherExternalChanges: null,
        marketMovement: null,
        assetRevaluation: null,
        fxTranslation: null,
        residual: null,
      },
      secondary: {
        available: false,
        reasons: ["Synthetic savings account is before_opening at 2024-01-31"],
        opening: null,
        closing: null,
        actualChange: null,
        retainedIncome: null,
        investmentIncome: null,
        internalMovementsAndCosts: null,
        otherExternalChanges: null,
        marketMovement: null,
        assetRevaluation: null,
        fxTranslation: null,
        residual: null,
      },
    };
    const request = requestFrom((path) => {
      if (path === "/wealth-history") return history;
      if (path === "/wealth-history/2024-01-31") return january;
      if (path === "/wealth-history/2024-01-31/attribution") return unavailable;
      throw new ApiError(500, "unexpected");
    });
    render(<WealthScreen currencies={CURRENCIES} currency={CURRENCY} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "2024-01-31" }));

    expect(await screen.findByText("Attribution is unavailable for 2024-01-31.")).toBeTruthy();
    expect(
      screen.getByText("Synthetic savings account is before_opening at 2024-01-31"),
    ).toBeTruthy();
  });
});
