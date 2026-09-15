// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HoldingScreen } from "@/app/investments/holding-screen";
import { Workspace } from "@/app/workspace";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  FundingProposalResponse,
  HoldingDetail,
  HoldingEventRow,
  HoldingLotRow,
  HouseholdResponse,
  LotAllocationProposalResponse,
  LotAllocationResponse,
  TransactionsResponse,
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
        fundingComplete: true,
        counterpartAccountId: null,
        counterpartSecurityId: null,
        counterpartUnits: null,
        counterpartPrice: null,
        counterpartGrossAmount: null,
        lotAllocationOpen: false,
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
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) {
        return detail({ openReviewReasons: ["incomplete_lot_allocation"] });
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);

    expect(await screen.findByRole("heading", { name: "Synthetic Growth Fund" })).toBeTruthy();
    expect(screen.getByText("This disposal is not fully allocated to existing lots.")).toBeTruthy();
    expect(screen.getByText("Events (1 of 1)")).toBeTruthy();
    expect(screen.getByLabelText("purchase on 2026-01-05")).toBeTruthy();
    expect(screen.getByText("funding")).toBeTruthy();
    expect(screen.getByText("XAA 101.0000")).toBeTruthy();
    expect(screen.getByText("XAA 10.500000")).toBeTruthy();
    // The event already has a confirmed cash link, so it is funded and cannot be corrected —
    // matching V40's own trigger, which refuses a correction against any existing allocation.
    expect(screen.getByText("Unlink its funding first to correct this event.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Correct" })).toBeNull();
  });

  it("shows the unlink message for a purchase that is only partly funded, not the Correct action", async () => {
    const request = requestFrom((path) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) {
        return detail({
          events: [{
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
            // Not fully funded by the remaining-amount comparison, but one allocation already
            // exists — V40's correction trigger refuses regardless of how much is covered.
            fundingComplete: false,
            counterpartAccountId: null,
            counterpartSecurityId: null,
            counterpartUnits: null,
            counterpartPrice: null,
            counterpartGrossAmount: null,
        lotAllocationOpen: false,
          }],
          cashLinks: [{
            investmentEventId: "00000000-0000-4000-8000-000000000060",
            cashTransactionId: "00000000-0000-4000-8000-000000000070",
            role: "funding",
            allocatedAmount: "40.0000",
            currency: "XAA",
          }],
        });
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);
    await screen.findByRole("heading", { name: "Synthetic Growth Fund" });

    expect(screen.getByText("Unlink its funding first to correct this event.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Correct" })).toBeNull();
  });

  it("records a dated price with the exact strings typed and reloads the holding", async () => {
    let loads = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) {
        loads += 1;
        return loads === 1 ? detail() : detail({ prices: [{ priceDate: "2026-02-10", price: "11.250000" }] });
      }
      if (path === "/investment-catalogue/securities/00000000-0000-4000-8000-000000000040/manual-prices" && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({ priceDate: "2026-02-10", price: "11.250000" });
        return { id: "price-1", securityId: "00000000-0000-4000-8000-000000000040", priceDate: "2026-02-10", price: "11.250000", currency: "XAA", status: "recorded" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);
    await screen.findByRole("heading", { name: "Synthetic Growth Fund" });

    fireEvent.change(screen.getByLabelText("Price date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Price (XAA)"), { target: { value: "11.250000" } });
    fireEvent.click(screen.getByRole("button", { name: "Record price" }));

    expect(await screen.findByText(/Dated price recorded\./)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Refresh Wealth" })).toHaveProperty("pathname", "/wealth");
    expect(await screen.findByText("XAA 11.250000")).toBeTruthy();
    expect(loads).toBe(2);
  });

  it("shows the service's own refusal when a dated price is not accepted, and does not reload", async () => {
    let loads = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) {
        loads += 1;
        return detail();
      }
      if (path === "/investment-catalogue/securities/00000000-0000-4000-8000-000000000040/manual-prices" && init?.method === "POST") {
        throw new ApiError(422, "manual prices require a household-owned security");
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);
    await screen.findByRole("heading", { name: "Synthetic Growth Fund" });

    fireEvent.change(screen.getByLabelText("Price date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Price (XAA)"), { target: { value: "11.250000" } });
    fireEvent.click(screen.getByRole("button", { name: "Record price" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "manual prices require a household-owned security",
    );
    expect(screen.queryByText(/Dated price recorded\./)).toBeNull();
    expect(loads).toBe(1);
  });

  it("lists a purchase that is not fully funded and confirms funding through the existing flow", async () => {
    const unfunded = detail({
      events: [{
        id: "00000000-0000-4000-8000-000000000061",
        eventDate: "2026-01-06",
        eventKind: "purchase",
        units: "5.00000000",
        unitRatio: null,
        price: "10.000000",
        grossAmount: "50.0000",
        charges: "0.0000",
        withheldTax: "0.0000",
        netAmount: "-50.0000",
        currency: "XAA",
        correctsEventId: null,
        counterpartEventId: null,
        correctionState: "active",
        fundingComplete: false,
        counterpartAccountId: null,
        counterpartSecurityId: null,
        counterpartUnits: null,
        counterpartPrice: null,
        counterpartGrossAmount: null,
        lotAllocationOpen: false,
      }],
      cashLinks: [],
    });
    const cashTransactions: TransactionsResponse = {
      transactions: [{
        id: "00000000-0000-4000-8000-000000000080",
        accountId: "00000000-0000-4000-8000-000000000011",
        accountName: "Synthetic cash account",
        bookedDate: "2026-01-06",
        descriptionRaw: "Synthetic funding debit",
        descriptionClean: "Synthetic funding debit",
        amount: "-50.0000",
        currency: "XAA",
        originalAmount: null,
        originalCurrency: null,
        amountBase: "-50.0000",
        amountSecondary: "-100.0000",
        fxRateDate: "2026-01-06",
        fxRateIsCarried: false,
        fxRateToBase: "1.0000000000",
        fxRateToSecondary: "2.0000000000",
        txnType: "expense",
        categoryId: null,
        categoryName: null,
        merchantId: null,
        transferId: null,
        parentTransactionId: null,
        isSplitParent: false,
        isExcluded: false,
        classifiedBy: "unclassified",
        source: "statement_import",
        notes: null,
        sourceDocumentId: null,
        sourcePageNumber: null,
        sourceRowNumber: null,
      }],
      total: 1,
    };
    const proposalResponse: FundingProposalResponse = {
      state: "ready",
      expectedState: "a".repeat(64),
      allocations: [{
        investmentEventId: "00000000-0000-4000-8000-000000000061",
        cashTransactionId: "00000000-0000-4000-8000-000000000080",
        allocatedAmount: "50.0000",
        eventRemainingAmount: "50.0000",
        cashAmount: "50.0000",
        currency: "XAA",
        priorTransactionType: "expense",
        role: "funding",
      }],
    };
    const funded = detail({
      events: [{
        id: "00000000-0000-4000-8000-000000000061",
        eventDate: "2026-01-06",
        eventKind: "purchase",
        units: "5.00000000",
        unitRatio: null,
        price: "10.000000",
        grossAmount: "50.0000",
        charges: "0.0000",
        withheldTax: "0.0000",
        netAmount: "-50.0000",
        currency: "XAA",
        correctsEventId: null,
        counterpartEventId: null,
        correctionState: "active",
        fundingComplete: true,
        counterpartAccountId: null,
        counterpartSecurityId: null,
        counterpartUnits: null,
        counterpartPrice: null,
        counterpartGrossAmount: null,
        lotAllocationOpen: false,
      }],
      cashLinks: [{
        investmentEventId: "00000000-0000-4000-8000-000000000061",
        cashTransactionId: "00000000-0000-4000-8000-000000000080",
        role: "funding",
        allocatedAmount: "50.0000",
        currency: "XAA",
      }],
    });
    let holdingLoads = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) {
        holdingLoads += 1;
        return holdingLoads === 1 ? unfunded : funded;
      }
      if (path === "/transactions?amountMax=0&sort=bookedDate&direction=desc&limit=50&offset=0") return cashTransactions;
      if (path === "/investment-funding/proposals" && init?.method === "POST") return proposalResponse;
      if (path === "/investment-funding/confirmations" && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          allocations: proposalResponse.allocations.map(({ investmentEventId, cashTransactionId, allocatedAmount }) => (
            { investmentEventId, cashTransactionId, allocatedAmount }
          )),
          expectedState: proposalResponse.expectedState,
        });
        return { state: "confirmed", allocations: [{
          id: "alloc-1",
          investmentEventId: "00000000-0000-4000-8000-000000000061",
          cashTransactionId: "00000000-0000-4000-8000-000000000080",
          role: "funding",
          allocatedAmount: "50.0000",
          priorTransactionType: "expense",
          cashTransactionType: "investment_buy",
        }] };
      }
      throw new Error(`Unexpected synthetic route ${init?.method ?? "GET"} ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);
    await screen.findByRole("heading", { name: "Synthetic Growth Fund" });

    expect(screen.getByText("Not fully funded.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Fund this purchase" }));
    fireEvent.click(await screen.findByRole("button", { name: "Find cash entries" }));
    await screen.findByRole("group", { name: "Funding allocation 1" });
    fireEvent.change(screen.getByLabelText("Purchase for allocation 1"), { target: { value: "00000000-0000-4000-8000-000000000061" } });
    fireEvent.change(screen.getByLabelText("Cash entry for allocation 1"), { target: { value: "00000000-0000-4000-8000-000000000080" } });
    fireEvent.change(screen.getByLabelText("Allocated amount for allocation 1"), { target: { value: "50.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review funding proposal" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm funding" }));

    // Confirming reloads the holding rather than trusting the panel's own transient state, so
    // the purchase's card — and the panel it re-seeds — reflect the server's own fresh answer.
    await waitFor(() => expect(holdingLoads).toBe(2));
    expect(await screen.findByText("Fully funded. Unlink existing funding before correcting this purchase.")).toBeTruthy();
    // The outcome survives the reload, and the open panel re-seeds from the fresh holding: the
    // purchase is now fully funded, so its allocation form is no longer offered.
    expect(screen.getByText(/Funding updated\. The purchase below shows the refreshed status\./)).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("group", { name: "Funding allocation 1" })).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Hide funding" }));
    expect(screen.getByRole("button", { name: "Review funding" })).toBeTruthy();
  });

  it("shows a not-found state for a foreign or missing holding without a generic error", async () => {
    const request = requestFrom((path) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) throw new ApiError(404, "holding not found");
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);

    expect(await screen.findByText("This holding could not be found.")).toBeTruthy();
  });

  it("routes a protected read returning 401 through the workspace to login", async () => {
    const navigate = vi.fn();
    const request = requestFrom((path) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === "/household") return household;
      if (path === "/accounts") return { accounts: [], totals: [] };
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

  it("allocates lots for an open disposal and reloads the holding on success", async () => {
    const disposalEventId = "00000000-0000-4000-8000-000000000062";
    const acquisitionEventId = "00000000-0000-4000-8000-000000000060";
    const lotId = "00000000-0000-4000-8000-000000000090";
    const disposalEvent = {
      id: disposalEventId,
      eventDate: "2026-01-10",
      eventKind: "sale",
      units: "-5.00000000",
      unitRatio: null,
      price: "12.000000",
      grossAmount: "60.0000",
      charges: "0.0000",
      withheldTax: "0.0000",
      netAmount: "60.0000",
      currency: "XAA",
      correctsEventId: null,
      counterpartEventId: null,
      correctionState: "active",
      fundingComplete: true,
      counterpartAccountId: null,
      counterpartSecurityId: null,
      counterpartUnits: null,
      counterpartPrice: null,
      counterpartGrossAmount: null,
      lotAllocationOpen: true,
    } as const;
    const openDetail = detail({ events: [detail().events[0], disposalEvent], eventsTotal: 2 });
    const resolvedDetail = detail({
      events: [detail().events[0], { ...disposalEvent, lotAllocationOpen: false }],
      eventsTotal: 2,
    });
    const lots: Array<HoldingLotRow> = [{
      lotId,
      acquisitionEventId,
      acquiredOn: "2026-01-05",
      acquiredUnits: "10.00000000",
      remainingUnits: "10.00000000",
      costBasisNative: "101.0000",
      eligible: true,
    }];
    const proposal: LotAllocationProposalResponse = {
      state: "ready",
      expectedState: "b".repeat(64),
      disposalEventId,
      allocations: [{ acquisitionLotId: lotId, allocatedUnits: "5.00000000" }],
    };
    const confirmation: LotAllocationResponse = {
      state: "confirmed",
      disposalEventId,
      allocations: [{ acquisitionLotId: lotId, allocatedUnits: "5.00000000" }],
      realisedCostNative: "50.5000",
      realisedGainNative: "9.5000",
      remainingBasisNative: "50.5000",
      reviewReasons: [],
    };
    let holdingLoads = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) {
        holdingLoads += 1;
        return holdingLoads === 1 ? openDetail : resolvedDetail;
      }
      if (path === `/portfolio/holdings/${HOLDING_ID}/lots`) return lots;
      if (path === "/investment-funding/lot-allocation-proposals" && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          disposalEventId,
          allocations: [{ acquisitionLotId: lotId, allocatedUnits: "5.00000000" }],
        });
        return proposal;
      }
      if (path === "/investment-funding/lot-allocations" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        expect(body.disposalEventId).toBe(disposalEventId);
        expect(body.allocations).toEqual([{ acquisitionLotId: lotId, allocatedUnits: "5.00000000" }]);
        expect(body.expectedState).toBe(proposal.expectedState);
        expect(typeof body.idempotencyKey).toBe("string");
        return confirmation;
      }
      throw new Error(`Unexpected synthetic route ${init?.method ?? "GET"} ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);
    await screen.findByRole("heading", { name: "Synthetic Growth Fund" });

    fireEvent.click(screen.getByRole("button", { name: "Allocate lots" }));
    await screen.findByRole("heading", { name: "Allocate lots" });
    const unitsInput = await screen.findByLabelText(/Acquired 5 Jan 2026/);
    fireEvent.change(unitsInput, { target: { value: "5.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review allocation proposal" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm allocation" }));

    await waitFor(() => expect(holdingLoads).toBe(2));
    expect(
      await screen.findByText(/Lot allocation confirmed\. The events below show the refreshed state\./),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Allocate lots" })).toBeNull();
    const confirmed = screen.getByLabelText("Confirmed lot allocation");
    expect(confirmed.textContent).toContain("50.5000");
    expect(confirmed.textContent).toContain("9.5000");
  });

  it("reuses the lot allocation key on a retry and replaces it after an edit", async () => {
    const disposalEventId = "00000000-0000-4000-8000-000000000072";
    const lotId = "00000000-0000-4000-8000-000000000091";
    const disposalEvent = {
      ...detail().events[0],
      id: disposalEventId,
      eventDate: "2026-01-10",
      eventKind: "sale",
      units: "-5.00000000",
      lotAllocationOpen: true,
    } as HoldingEventRow;
    const openDetail = detail({ events: [detail().events[0], disposalEvent], eventsTotal: 2 });
    const lots: Array<HoldingLotRow> = [{
      lotId,
      acquisitionEventId: detail().events[0].id,
      acquiredOn: "2026-01-05",
      acquiredUnits: "10.00000000",
      remainingUnits: "10.00000000",
      costBasisNative: "101.0000",
      eligible: true,
    }];
    const keys: Array<unknown> = [];
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) return openDetail;
      if (path === `/portfolio/holdings/${HOLDING_ID}/lots`) return lots;
      if (path === "/investment-funding/lot-allocation-proposals" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { allocations: Array<{ allocatedUnits: string }> };
        return {
          state: "ready",
          expectedState: "c".repeat(64),
          disposalEventId,
          allocations: [{ acquisitionLotId: lotId, allocatedUnits: body.allocations[0].allocatedUnits }],
        };
      }
      if (path === "/investment-funding/lot-allocations" && init?.method === "POST") {
        keys.push((JSON.parse(String(init.body)) as Record<string, unknown>).idempotencyKey);
        throw new ApiError(500, "Synthetic failure");
      }
      throw new Error(`Unexpected synthetic route ${init?.method ?? "GET"} ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);
    await screen.findByRole("heading", { name: "Synthetic Growth Fund" });
    fireEvent.click(screen.getByRole("button", { name: "Allocate lots" }));
    fireEvent.change(await screen.findByLabelText(/Acquired 5 Jan 2026/), { target: { value: "5.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review allocation proposal" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm allocation" }));
    await waitFor(() => expect(keys).toHaveLength(1));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm allocation" }));
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[1]).toBe(keys[0]);

    fireEvent.click(screen.getByRole("button", { name: "Edit allocation" }));
    fireEvent.change(await screen.findByLabelText(/Acquired 5 Jan 2026/), { target: { value: "4.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review allocation proposal" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm allocation" }));
    await waitFor(() => expect(keys).toHaveLength(3));
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("loads more events, appending to the first page without replacing it", async () => {
    const firstPageDetail = detail({ eventsTotal: 3 });
    const secondEvent = { ...firstPageDetail.events[0], id: "00000000-0000-4000-8000-000000000063", eventDate: "2026-01-06" };
    const request = requestFrom((path) => {
      if (path === "/portfolio/positions") return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };
      if (path === `/portfolio/holdings/${HOLDING_ID}`) return firstPageDetail;
      if (path === `/portfolio/holdings/${HOLDING_ID}?eventsOffset=1`) {
        return { ...firstPageDetail, events: [secondEvent] };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<HoldingScreen accounts={[]} holdingId={HOLDING_ID} request={request} />);
    await screen.findByRole("heading", { name: "Synthetic Growth Fund" });

    expect(screen.getByText("Events (1 of 3)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Load more events" }));

    expect(await screen.findByText("Events (2 of 3)")).toBeTruthy();
    expect(screen.getByLabelText("purchase on 2026-01-06")).toBeTruthy();
    expect(screen.getByLabelText("purchase on 2026-01-05")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Load more events" })).toBeTruthy();
  });
});
