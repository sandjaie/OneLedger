// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InvestmentAnalysis } from "@/app/investments/investment-analysis";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AcquisitionPosition, AnalysisResponse, EffectWindowOut } from "@/types/api";

type Request = typeof apiFetch;

function windowOut(overrides: Partial<EffectWindowOut> = {}): EffectWindowOut {
  return {
    dietzPercent: "1.25000000",
    dietzReason: null,
    contributions: "50.0000",
    withdrawals: "0.0000",
    income: "0.0000",
    charges: "0.0000",
    marketAndFx: "10.0000",
    opening: "0.0000",
    closing: "60.0000",
    benchmarkDietzPercent: "0.80000000",
    benchmarkDietzReason: null,
    excessPercent: "0.45000000",
    ...overrides,
  };
}

const emptyWindow = windowOut({
  dietzPercent: null,
  dietzReason: "missing_price",
  contributions: null,
  withdrawals: null,
  income: null,
  charges: null,
  marketAndFx: null,
  opening: null,
  closing: null,
  benchmarkDietzPercent: null,
  benchmarkDietzReason: null,
  excessPercent: null,
});

const analysis: AnalysisResponse = {
  asOf: "2024-02-29",
  wealthHistoryComputedAt: "2024-03-01T00:00:00Z",
  wealthHistoryReason: null,
  coverage: [
    {
      accountId: "00000000-0000-4000-8000-000000000030",
      accountName: "Synthetic retirement",
      valuationMode: "snapshot",
    },
  ],
  classes: [
    {
      label: "equity",
      xirrPercent: "12.34560000",
      xirrReason: null,
      valueBase: "24.0000",
      inception: windowOut({ excessPercent: "0.45000000" }),
    },
  ],
  accounts: [
    {
      accountId: "00000000-0000-4000-8000-000000000010",
      accountName: "Synthetic brokerage",
      xirrPercent: "12.34560000",
      xirrReason: null,
      valueBase: "24.0000",
    },
  ],
  inception: windowOut(),
  years: [
    {
      year: 2024,
      incompleteMonths: 0,
      portfolio: windowOut({ contributions: "50.0000" }),
      classes: [{ label: "equity", ...windowOut() }],
    },
  ],
  months: [
    {
      monthEnd: "2024-02-29",
      complete: false,
      statusCounts: { missing_price: 1 },
      portfolio: emptyWindow,
      classes: [
        {
          label: "equity",
          ...emptyWindow,
          valueBase: null,
          ratio: null,
          targetRatio: "1.0000000000",
          drift: null,
        },
      ],
    },
    {
      monthEnd: "2024-01-31",
      complete: true,
      statusCounts: { available: 1 },
      portfolio: windowOut({ contributions: "50.0000" }),
      classes: [
        {
          label: "equity",
          ...windowOut({ contributions: "50.0000", dietzPercent: "2.00000000" }),
          valueBase: "24.0000",
          ratio: "1.0000000000",
          targetRatio: "1.0000000000",
          drift: "0.0000000000",
        },
      ],
    },
  ],
  target: { source: "inferred", groups: [{ label: "equity", ratio: "1.0000000000" }] },
  benchmarks: [
    {
      scopeKind: "portfolio",
      assetClass: null,
      securityId: "00000000-0000-4000-8000-000000000040",
      securityName: "Synthetic equity fund",
    },
  ],
};

const positions: Array<AcquisitionPosition> = [
  {
    holdingId: "00000000-0000-4000-8000-000000000050",
    accountId: "00000000-0000-4000-8000-000000000010",
    securityId: "00000000-0000-4000-8000-000000000040",
    securityName: "Synthetic equity fund",
    assetClass: "equity",
    currency: "XAA",
    asOf: "2024-02-29",
    units: "4.00000000",
    cumulativeContributionsNative: "20.0000",
    cumulativeContributionsBase: "20.0000",
    cumulativeContributionsSecondary: "40.0000",
    priceStatus: "current",
    valueStatus: "available",
    price: "6.000000",
    priceDate: "2024-02-29",
    currentValueNative: "24.0000",
    currentValueBase: "24.0000",
    currentValueSecondary: "48.0000",
    gainNative: "4.0000",
    gainBase: "4.0000",
    gainSecondary: "8.0000",
    missingFx: [],
    fxRates: [],
    closed: false,
    xirrPercent: "12.34560000",
    xirrReason: null,
  },
];

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("investment analysis", () => {
  it("renders class XIRR, contribution lines, drift, excess and an incomplete month from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === "/portfolio/analysis") return analysis;
      throw new ApiError(500, "unexpected");
    });
    render(
      <InvestmentAnalysis baseCurrency="XAA" positions={positions} refreshKey={0} request={request} />,
    );

    const classes = await screen.findByRole("table", { name: "Returns by asset class" });
    expect(classes.textContent).toContain("12.34560000%");
    expect(classes.textContent).toContain("XAA 24.0000");
    expect(classes.textContent).toContain("XAA 50.0000");
    expect(classes.textContent).toContain("0.45000000%");

    const years = screen.getByRole("table", { name: "Returns by year" });
    expect(years.textContent).toContain("2024");
    expect(years.textContent).toContain("equity");

    const months = screen.getByRole("table", { name: "Returns by month" });
    expect(months.textContent).toContain("Incomplete month");
    expect(months.textContent).toContain("1 missing price");
    expect(months.textContent).toContain("drift 0.0000000000");
    expect(months.textContent).toContain("XAA 50.0000");

    expect(screen.getByText("Inferred", { selector: "span" })).toBeTruthy();
    expect(screen.getByText("Synthetic retirement · snapshot — excluded from holdings analysis")).toBeTruthy();
  });

  it("shows the empty state when there are no holdings", async () => {
    const request = requestFrom((path) => {
      if (path === "/portfolio/analysis") {
        return {
          ...analysis,
          classes: [],
          accounts: [],
          coverage: [],
          years: [],
          months: [],
          target: { source: "none", groups: [] },
          benchmarks: [],
          wealthHistoryReason: "wealth_history_not_refreshed",
        } satisfies AnalysisResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<InvestmentAnalysis baseCurrency="XAA" positions={[]} refreshKey={0} request={request} />);

    await screen.findByText("No holdings to analyse.");
    expect(screen.getByText(/Wealth history has not been refreshed/)).toBeTruthy();
  });

  it("shows an error with retry", async () => {
    let calls = 0;
    const request = requestFrom((path) => {
      if (path === "/portfolio/analysis") {
        calls += 1;
        if (calls === 1) throw new ApiError(500, "boom");
        return { ...analysis, classes: [], accounts: [] } satisfies AnalysisResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<InvestmentAnalysis baseCurrency="XAA" positions={[]} refreshKey={0} request={request} />);

    await screen.findByText("Investment analysis could not be loaded.");
    fireEvent.click(screen.getByText("Try again"));
    await screen.findByText("No holdings to analyse.");
  });

  it("PUTs the full target list and reloads", async () => {
    let saved: unknown;
    let loads = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/analysis") {
        loads += 1;
        return analysis;
      }
      if (path === "/portfolio/allocation-targets" && init?.method === "PUT") {
        saved = JSON.parse(String(init.body));
        return { groups: [{ label: "equity", ratio: "1.0000000000" }] };
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <InvestmentAnalysis baseCurrency="XAA" positions={positions} refreshKey={0} request={request} />,
    );

    const input = await screen.findByLabelText("Target ratio for equity");
    fireEvent.change(input, { target: { value: "1.0000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save target" }));

    await waitFor(() =>
      expect(saved).toEqual({ groups: [{ label: "equity", ratio: "1.0000000000" }] }),
    );
    await waitFor(() => expect(loads).toBe(2));
  });

  it("DELETEs the portfolio benchmark and reloads", async () => {
    let deleted = false;
    let loads = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/analysis") {
        loads += 1;
        return analysis;
      }
      if (path === "/portfolio/benchmarks/portfolio" && init?.method === "DELETE") {
        deleted = true;
        return undefined;
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <InvestmentAnalysis baseCurrency="XAA" positions={positions} refreshKey={0} request={request} />,
    );

    const select = await screen.findByLabelText("Portfolio benchmark");
    fireEvent.change(select, { target: { value: "" } });

    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() => expect(loads).toBe(2));
  });

  it("DELETEs a stored target when the owner chooses Use inference", async () => {
    let deleted = false;
    let loads = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/analysis") {
        loads += 1;
        return { ...analysis, target: { source: "rule", groups: [{ label: "equity", ratio: "1.0000000000" }] } };
      }
      if (path === "/portfolio/allocation-targets" && init?.method === "DELETE") {
        deleted = true;
        return undefined;
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <InvestmentAnalysis baseCurrency="XAA" positions={positions} refreshKey={0} request={request} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Use inference" }));
    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() => expect(loads).toBe(2));
  });
});
