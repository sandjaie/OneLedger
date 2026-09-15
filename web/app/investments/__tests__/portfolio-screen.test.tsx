// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PortfolioScreen } from "@/app/investments/portfolio-screen";
import { Workspace } from "@/app/workspace";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  AcquisitionPosition,
  HouseholdResponse,
  PortfolioPositions,
  PortfolioSummary,
  RefreshOutcome,
} from "@/types/api";

type Request = typeof apiFetch;

const household: HouseholdResponse = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Synthetic household",
  ownerDisplayName: "Owner",
  ownerTaxResidency: null,
  baseCurrency: "XAA",
  secondaryCurrency: "XBB",
  reportingPairLocked: false,
  currencies: [
    { code: "XAA", symbol: "A", decimals: 2, numberFormat: "international" },
    { code: "XBB", symbol: "B", decimals: 2, numberFormat: "international" },
  ],
};

function summary(overrides: Partial<PortfolioSummary> = {}): PortfolioSummary {
  return {
    asOf: "2026-02-06",
    baseCurrency: "XAA",
    secondaryCurrency: "XBB",
    valueBase: "100.0000",
    valueSecondary: "200.0000",
    valueComplete: true,
    contributionsBase: "90.0000",
    contributionsSecondary: "180.0000",
    gainBase: "10.0000",
    gainSecondary: "20.0000",
    xirrPercent: "12.34560000",
    xirrReason: null,
    allocationByAssetClass: [{ label: "equity", valueBase: "100.0000", ratio: "1.0000000000" }],
    allocationByAccount: [{ label: "acct", valueBase: "100.0000", ratio: "1.0000000000" }],
    allocationComplete: true,
    valuedCount: 1,
    unvaluedCount: 0,
    staleCount: 0,
    priceStatus: {
      lastSuccessfulCheck: null,
      mappedCount: 1,
      unmappedCount: 0,
      latestPriceDate: "2026-02-05",
    },
    ...overrides,
  };
}

function position(overrides: Partial<AcquisitionPosition> = {}): AcquisitionPosition {
  return {
    holdingId: "00000000-0000-4000-8000-000000000050",
    accountId: "00000000-0000-4000-8000-000000000010",
    securityId: "00000000-0000-4000-8000-000000000040",
    securityName: "Synthetic Growth Fund",
    assetClass: "equity",
    currency: "XAA",
    asOf: "2026-02-06",
    units: "10.00000000",
    cumulativeContributionsNative: "90.0000",
    cumulativeContributionsBase: "90.0000",
    cumulativeContributionsSecondary: "180.0000",
    priceStatus: "current",
    valueStatus: "available",
    price: "10.000000",
    priceDate: "2026-02-06",
    currentValueNative: "100.0000",
    currentValueBase: "100.0000",
    currentValueSecondary: "200.0000",
    gainNative: "10.0000",
    gainBase: "10.0000",
    gainSecondary: "20.0000",
    missingFx: [],
    fxRates: [],
    closed: false,
    xirrPercent: "12.34560000",
    xirrReason: null,
    ...overrides,
  };
}

function positions(rows: Array<AcquisitionPosition> = [position()]): PortfolioPositions {
  return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: rows };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  // `async` wraps a synchronous `throw` inside `handler` into a rejected promise, matching a
  // real fetch failure rather than an uncaught exception during the effect that calls it.
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(cleanup);

describe("PortfolioScreen", () => {
  it("renders server value, gain and XIRR strings with an unavailable reason when withheld", async () => {
    const request = requestFrom((path) => {
      if (path === "/portfolio/summary") {
        return summary({ gainBase: null, gainSecondary: null, xirrPercent: null, xirrReason: "no_sign_change" });
      }
      if (path === "/portfolio/positions?includeClosed=true") return positions();
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<PortfolioScreen accounts={[]} request={request} />);

    expect((await screen.findByLabelText("Value")).textContent).toContain("XAA 100.0000");
    expect(screen.getByText("No sign change across the cash flows.")).toBeTruthy();
    const gain = screen.getByLabelText("Gain");
    expect(gain.textContent).not.toMatch(/10\.0000/);
    expect(gain.textContent).toContain("one or more holdings withhold gain");
  });

  it("shows the refresh action only when the price check is due, and posts exactly once", async () => {
    const posted: Array<string> = [];
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/summary") return summary();
      if (path === "/portfolio/positions?includeClosed=true") return positions();
      if (path === "/investment-prices/refresh" && init?.method === "POST") {
        posted.push(path);
        const outcome: RefreshOutcome = { outcome: "refreshed", status: summary().priceStatus };
        return outcome;
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<PortfolioScreen accounts={[]} request={request} />);

    const button = await screen.findByRole("button", { name: "Check for new prices" });
    fireEvent.click(button);

    await waitFor(() => expect(posted).toEqual(["/investment-prices/refresh"]));
    expect(await screen.findByText("Prices refreshed from the official source.")).toBeTruthy();
  });

  it("hides the refresh action and never posts once checked within 24 hours", async () => {
    const request = requestFrom((path) => {
      if (path === "/portfolio/summary") {
        return summary({ priceStatus: { ...summary().priceStatus, lastSuccessfulCheck: new Date().toISOString() } });
      }
      if (path === "/portfolio/positions?includeClosed=true") return positions();
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<PortfolioScreen accounts={[]} request={request} />);

    expect(await screen.findByText("Checked within the last 24 hours.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check for new prices" })).toBeNull();
    expect(request).not.toHaveBeenCalledWith("/investment-prices/refresh", expect.anything());
  });

  it("labels a stale and a missing holding price without computing a value", async () => {
    const request = requestFrom((path) => {
      if (path === "/portfolio/summary") return summary();
      if (path === "/portfolio/positions?includeClosed=true") {
        return positions([
          position({
            holdingId: "00000000-0000-4000-8000-000000000051",
            securityName: "Stale Fund",
            priceStatus: "stale",
          }),
          position({
            holdingId: "00000000-0000-4000-8000-000000000052",
            securityName: "Missing Fund",
            priceStatus: "missing",
            valueStatus: "missing_price",
            price: null,
            priceDate: null,
            currentValueNative: null,
            currentValueBase: null,
            currentValueSecondary: null,
            gainNative: null,
            gainBase: null,
            gainSecondary: null,
          }),
        ]);
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<PortfolioScreen accounts={[]} request={request} />);

    expect(await screen.findByText(/Price XAA 10.000000/, { selector: "p" })).toBeTruthy();
    expect(screen.getByText("No dated price recorded.")).toBeTruthy();
    expect(screen.getByText("Unavailable")).toBeTruthy();
  });

  it("states allocation is incomplete rather than a false percentage", async () => {
    const request = requestFrom((path) => {
      if (path === "/portfolio/summary") return summary({ allocationComplete: false });
      if (path === "/portfolio/positions?includeClosed=true") return positions();
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<PortfolioScreen accounts={[]} request={request} />);

    expect(await screen.findByText("Incomplete — one or more holdings are unvalued.")).toBeTruthy();
  });

  it("routes a protected read returning 401 through the workspace to login", async () => {
    const navigate = vi.fn();
    const request = requestFrom((path) => {
      if (path === "/household") return household;
      if (path === "/accounts") return { accounts: [], totals: [] };
      if (path === "/portfolio/summary") throw new ApiError(401, "synthetic");
      if (path === "/portfolio/positions?includeClosed=true") return positions([]);
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<Workspace section="investments" request={request} navigate={navigate} />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
  });
});
