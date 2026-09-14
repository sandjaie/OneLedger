// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HoldingScreen } from "@/app/investments/holding-screen";
import { Workspace } from "@/app/workspace";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { HoldingDetail, HouseholdResponse } from "@/types/api";

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

const HOLDING_ID = "00000000-0000-4000-8000-000000000050";

function detail(overrides: Partial<HoldingDetail> = {}): HoldingDetail {
  return {
    holdingId: HOLDING_ID,
    accountId: "00000000-0000-4000-8000-000000000010",
    securityId: "00000000-0000-4000-8000-000000000040",
    securityName: "Synthetic Growth Fund",
    assetClass: "equity",
    currency: "XAA",
    units: "10.00000000",
    openedOn: "2026-01-05",
    closedOn: null,
    events: [
      {
        id: "00000000-0000-4000-8000-000000000060",
        eventDate: "2026-01-05",
        eventKind: "purchase",
        units: "10.00000000",
        unitRatio: null,
        price: "10.000000",
        grossAmount: "100.0000",
        charges: "1.0000",
        withheldTax: "0.0000",
        netAmount: "-101.0000",
        currency: "XAA",
        correctsEventId: null,
        counterpartEventId: null,
        correctionState: "active",
      },
    ],
    eventsTotal: 1,
    cashLinks: [
      {
        investmentEventId: "00000000-0000-4000-8000-000000000060",
        cashTransactionId: "00000000-0000-4000-8000-000000000070",
        role: "funding",
        allocatedAmount: "101.0000",
        currency: "XAA",
      },
    ],
    prices: [{ priceDate: "2026-02-06", price: "10.500000" }],
    openReviewReasons: [],
    ...overrides,
  };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  // `async` wraps a synchronous `throw` inside `handler` into a rejected promise, matching a
  // real fetch failure rather than an uncaught exception during the effect that calls it.
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(cleanup);

describe("HoldingScreen", () => {
  it("renders events, cash links, prices and review reasons as server strings", async () => {
    const request = requestFrom((path) => {
      if (path === `/portfolio/holdings/${HOLDING_ID}`) {
        return detail({ openReviewReasons: ["incomplete_lot_allocation"] });
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<HoldingScreen holdingId={HOLDING_ID} request={request} />);

    expect(await screen.findByRole("heading", { name: "Synthetic Growth Fund" })).toBeTruthy();
    expect(screen.getByText("This disposal is not fully allocated to existing lots.")).toBeTruthy();
    expect(screen.getByText("Events (1)")).toBeTruthy();
    expect(screen.getByLabelText("purchase on 2026-01-05")).toBeTruthy();
    expect(screen.getByText("funding")).toBeTruthy();
    expect(screen.getByText("XAA 101.0000")).toBeTruthy();
    expect(screen.getByText("XAA 10.500000")).toBeTruthy();
  });

  it("shows a not-found state for a foreign or missing holding without a generic error", async () => {
    const request = requestFrom((path) => {
      if (path === `/portfolio/holdings/${HOLDING_ID}`) throw new ApiError(404, "holding not found");
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<HoldingScreen holdingId={HOLDING_ID} request={request} />);

    expect(await screen.findByText("This holding could not be found.")).toBeTruthy();
  });

  it("routes a protected read returning 401 through the workspace to login", async () => {
    const navigate = vi.fn();
    const request = requestFrom((path) => {
      if (path === "/household") return household;
      if (path === `/portfolio/holdings/${HOLDING_ID}`) throw new ApiError(401, "synthetic");
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(
      <Workspace
        holdingId={HOLDING_ID}
        navigate={navigate}
        request={request}
        section="investments-holding"
      />,
    );

    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
  });
});
