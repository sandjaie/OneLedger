// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FundingPanel } from "@/app/investments/funding-panel";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  FundingConfirmationResponse,
  FundingProposalResponse,
  FundingUnlinkResponse,
  InvestmentEventRow,
  TransactionRow,
  TransactionsResponse,
  TransferRow,
  TransfersResponse,
} from "@/types/api";

const EVENT_A = "00000000-0000-4000-8000-000000000101";
const EVENT_B = "00000000-0000-4000-8000-000000000102";
const CASH_A = "00000000-0000-4000-8000-000000000201";
const CASH_B = "00000000-0000-4000-8000-000000000202";
const CASH_TRANSFER = "00000000-0000-4000-8000-000000000203";

function investmentEvent(overrides: Partial<InvestmentEventRow> = {}): InvestmentEventRow {
  return {
    id: EVENT_A,
    holdingId: "00000000-0000-4000-8000-000000000301",
    accountId: "00000000-0000-4000-8000-000000000302",
    securityId: "00000000-0000-4000-8000-000000000303",
    eventDate: "2026-04-08",
    eventKind: "purchase",
    units: "4.00000000",
    price: "7.250000",
    grossAmount: "29.0000",
    charges: "1.0000",
    withheldTax: "0.0000",
    netAmount: "-30.0000",
    currency: "XAA",
    fxRateDate: "2026-04-08",
    fxRateToBase: "1.0000000000",
    fxRateToSecondary: "2.0000000000",
    netAmountBase: "-30.0000",
    netAmountSecondary: "-60.0000",
    sourceKind: "owner_entered",
    correctsEventId: null,
    correctionState: "uncorrected",
    currentUnits: "4.00000000",
    lot: {
      id: "00000000-0000-4000-8000-000000000304",
      acquisitionDate: "2026-04-08",
      acquiredUnits: "4.00000000",
      costBasisNative: "30.0000",
      currency: "XAA",
    },
    ...overrides,
  };
}

function cash(overrides: Partial<TransactionRow> = {}): TransactionRow {
  return {
    id: CASH_A,
    accountId: "00000000-0000-4000-8000-000000000401",
    accountName: "Synthetic cash account",
    bookedDate: "2026-04-08",
    descriptionRaw: "Synthetic investment debit",
    descriptionClean: "Synthetic investment debit",
    amount: "-30.0000",
    currency: "XAA",
    originalAmount: null,
    originalCurrency: null,
    amountBase: "-30.0000",
    amountSecondary: "-60.0000",
    fxRateDate: "2026-04-08",
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
    ...overrides,
  };
}

const transactions: TransactionsResponse = {
  transactions: [
    cash(),
    cash({ id: CASH_B, bookedDate: "2026-04-09", descriptionRaw: "Synthetic second debit", amount: "-11.0000" }),
    cash({ id: CASH_TRANSFER, descriptionRaw: "Synthetic confirmed transfer leg", transferId: "00000000-0000-4000-8000-000000000501" }),
  ],
  total: 3,
};

const transfer: TransferRow = {
  id: "00000000-0000-4000-8000-000000000501",
  sourceAccountId: "00000000-0000-4000-8000-000000000401",
  destinationAccountId: "00000000-0000-4000-8000-000000000402",
  sourceTransactionId: CASH_TRANSFER,
  destinationTransactionId: "00000000-0000-4000-8000-000000000204",
  sourceAmount: "12.0000",
  sourceCurrency: "XBB",
  destinationAmount: "24.0000",
  destinationCurrency: "XAA",
  transferDate: "2026-04-07",
  settlementDate: "2026-04-08",
  impliedRate: "2.0000000000",
  marketRate: "2.0000000000",
  fxSpreadPct: "0.0000",
  fxSpreadCostSource: "0.0000",
  explicitFeeAmount: null,
  explicitFeeCurrency: null,
  totalCostSource: "0.0000",
  transferKind: "remittance",
  provider: null,
  matchConfidence: null,
  matchMethod: "manual",
  status: "confirmed",
  notes: null,
  costIsApproximate: false,
};

function candidateReads(rows = transactions, paths: Array<TransferRow> = [transfer]) {
  const transfers: TransfersResponse = { transfers: paths, total: paths.length };
  return {
    "GET /transactions?amountMax=0&sort=bookedDate&direction=desc&limit=50": rows,
    "GET /transfers?status=confirmed&limit=50": transfers,
  };
}

function proposal(overrides: Partial<FundingProposalResponse> = {}): FundingProposalResponse {
  return {
    state: "ready",
    expectedState: "a".repeat(64),
    allocations: [{
      investmentEventId: EVENT_A,
      cashTransactionId: CASH_A,
      allocatedAmount: "30.0000",
      eventRemainingAmount: "30.0000",
      cashAmount: "30.0000",
      currency: "XAA",
      priorTransactionType: "expense",
    }],
    ...overrides,
  };
}

type Answer = unknown | Error | ((init?: RequestInit) => unknown | Promise<unknown>);

function routes(table: Record<string, Answer>) {
  return vi.fn(async (path: string, init?: RequestInit) => {
    const answer = table[`${init?.method ?? "GET"} ${path}`];
    if (answer === undefined) throw new Error(`unexpected synthetic route ${init?.method ?? "GET"} ${path}`);
    if (answer instanceof Error) throw answer;
    return typeof answer === "function" ? (answer as (value?: RequestInit) => unknown)(init) : answer;
  });
}

function renderPanel(request: ReturnType<typeof routes>, events = [investmentEvent()]) {
  render(<FundingPanel events={events} request={request as unknown as typeof apiFetch} />);
}

async function loadCandidates() {
  fireEvent.click(screen.getByRole("button", { name: "Find cash entries" }));
  await screen.findByRole("group", { name: "Funding allocation 1" });
}

function fillAllocation(index: number, eventId: string, cashId: string, amount: string) {
  fireEvent.change(screen.getByLabelText(`Purchase for allocation ${index}`), { target: { value: eventId } });
  fireEvent.change(screen.getByLabelText(`Cash entry for allocation ${index}`), { target: { value: cashId } });
  fireEvent.change(screen.getByLabelText(`Allocated amount for allocation ${index}`), { target: { value: amount } });
}

afterEach(cleanup);

describe("investment funding", () => {
  it("loads owner-visible debit candidates only when asked and never chooses one automatically", async () => {
    const request = routes({
      ...candidateReads(),
    });
    renderPanel(request);

    expect(request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Find cash entries" }));

    const group = await screen.findByRole("group", { name: "Funding allocation 1" });
    expect((within(group).getByLabelText("Cash entry for allocation 1") as HTMLSelectElement).value).toBe("");
    expect(screen.getByText("Synthetic confirmed transfer leg")).toBeTruthy();
    expect(screen.getByText("XBB 12.0000 → XAA 24.0000")).toBeTruthy();
    expect(screen.getByText(/Keep this confirmed transfer chain separate/)).toBeTruthy();
    expect(document.body.textContent).not.toContain(CASH_A);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("posts exact owner-entered strings to create a proposal and does not auto-confirm it", async () => {
    const ready = proposal({
      allocations: [
        ...proposal().allocations,
        { ...proposal().allocations[0], cashTransactionId: CASH_B, allocatedAmount: "11.0000", cashAmount: "11.0000" },
      ],
    });
    const request = routes({
      ...candidateReads(),
      "POST /investment-funding/proposals": ready,
    });
    renderPanel(request);
    await loadCandidates();
    fillAllocation(1, EVENT_A, CASH_A, "30.0000");
    fireEvent.click(screen.getByRole("button", { name: "Add allocation row" }));
    fillAllocation(2, EVENT_A, CASH_B, "11.0000");

    fireEvent.click(screen.getByRole("button", { name: "Review funding proposal" }));

    const review = await screen.findByRole("group", { name: "Confirm funding" });
    expect(within(review).getByText("Server accepted every selected cash leaf as fully allocated.")).toBeTruthy();
    expect(within(review).getAllByText("XAA 30.0000", { exact: false }).length).toBeGreaterThan(0);
    expect(within(review).getByText("XAA 11.0000", { exact: false })).toBeTruthy();
    expect(document.activeElement).toBe(within(review).getByRole("button", { name: "Confirm funding" }));
    expect(request).not.toHaveBeenCalledWith("/investment-funding/confirmations", expect.anything());

    const post = request.mock.calls.find(([path]) => path === "/investment-funding/proposals");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ allocations: [
      { investmentEventId: EVENT_A, cashTransactionId: CASH_A, allocatedAmount: "30.0000" },
      { investmentEventId: EVENT_A, cashTransactionId: CASH_B, allocatedAmount: "11.0000" },
    ] });
  });

  it("preserves a many-to-many allocation matrix without client-side money arithmetic", async () => {
    const secondEvent = investmentEvent({ id: EVENT_B, eventDate: "2026-04-10", netAmount: "-5.0000" });
    const matrix = [
      { ...proposal().allocations[0], allocatedAmount: "25.0000" },
      { ...proposal().allocations[0], investmentEventId: EVENT_B, allocatedAmount: "5.0000" },
    ];
    const request = routes({
      ...candidateReads(),
      "POST /investment-funding/proposals": proposal({ allocations: matrix }),
    });
    renderPanel(request, [investmentEvent(), secondEvent]);
    await loadCandidates();
    fillAllocation(1, EVENT_A, CASH_A, "25.0000");
    fireEvent.click(screen.getByRole("button", { name: "Add allocation row" }));
    fillAllocation(2, EVENT_B, CASH_A, "5.0000");
    fireEvent.click(screen.getByRole("button", { name: "Review funding proposal" }));

    await screen.findByRole("group", { name: "Confirm funding" });
    const post = request.mock.calls.find(([path]) => path === "/investment-funding/proposals");
    expect(JSON.parse(String(post?.[1]?.body)).allocations).toEqual([
      { investmentEventId: EVENT_A, cashTransactionId: CASH_A, allocatedAmount: "25.0000" },
      { investmentEventId: EVENT_B, cashTransactionId: CASH_A, allocatedAmount: "5.0000" },
    ]);
    expect(document.body.textContent).toContain("XAA 25.0000");
    expect(document.body.textContent).toContain("XAA 5.0000");
  });

  it("keeps a partial or otherwise ineligible proposal editable and shows the service refusal", async () => {
    const request = routes({
      ...candidateReads(),
      "POST /investment-funding/proposals": new ApiError(422, "funding allocation is not a full eligible cash-leaf decision"),
    });
    renderPanel(request);
    await loadCandidates();
    fillAllocation(1, EVENT_A, CASH_A, "20.0000");
    fireEvent.click(screen.getByRole("button", { name: "Review funding proposal" }));

    expect((await screen.findByRole("alert")).textContent).toBe("funding allocation is not a full eligible cash-leaf decision");
    expect(screen.getByRole("group", { name: "Funding allocation 1" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Confirm funding" })).toBeNull();
  });

  it("refuses a stale confirmation and requires a new explicit proposal", async () => {
    let proposals = 0;
    const request = routes({
      ...candidateReads(),
      "POST /investment-funding/proposals": () => {
        proposals += 1;
        return proposal({ expectedState: String(proposals).repeat(64) });
      },
      "POST /investment-funding/confirmations": new ApiError(409, "funding proposal state changed"),
    });
    renderPanel(request);
    await loadCandidates();
    fillAllocation(1, EVENT_A, CASH_A, "30.0000");
    fireEvent.click(screen.getByRole("button", { name: "Review funding proposal" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm funding" }));

    expect((await screen.findByRole("alert")).textContent).toBe("funding proposal state changed");
    expect(screen.queryByRole("button", { name: "Confirm funding" })).toBeNull();
    expect(proposals).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Review current state" }));
    await screen.findByRole("button", { name: "Confirm funding" });
    expect(proposals).toBe(2);
  });

  it("confirms the exact proposal once and removes a cash leaf only after a second explicit action", async () => {
    const confirmed: FundingConfirmationResponse = {
      state: "confirmed",
      allocations: [{
        id: "00000000-0000-4000-8000-000000000601",
        investmentEventId: EVENT_A,
        cashTransactionId: CASH_A,
        role: "funding",
        allocatedAmount: "30.0000",
        priorTransactionType: "expense",
        cashTransactionType: "investment_buy",
      }],
    };
    const unlinked: FundingUnlinkResponse = {
      state: "unlinked",
      cashTransactionId: CASH_A,
      restoredTransactionType: "expense",
      removedAllocationCount: 1,
    };
    let releaseConfirmation!: (value: FundingConfirmationResponse) => void;
    const request = routes({
      ...candidateReads(),
      "POST /investment-funding/proposals": proposal(),
      "POST /investment-funding/confirmations": () => new Promise((resolve) => {
        releaseConfirmation = resolve;
      }),
      [`DELETE /investment-funding/cash-transactions/${CASH_A}`]: unlinked,
    });
    renderPanel(request);
    await loadCandidates();
    fillAllocation(1, EVENT_A, CASH_A, "30.0000");
    fireEvent.click(screen.getByRole("button", { name: "Review funding proposal" }));
    const confirmButton = await screen.findByRole("button", { name: "Confirm funding" });
    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);
    releaseConfirmation(confirmed);

    const record = await screen.findByRole("article", { name: "Confirmed funding for Synthetic investment debit" });
    expect(within(record).getByText("XAA 30.0000")).toBeTruthy();
    const confirmationPosts = request.mock.calls.filter(([path]) => path === "/investment-funding/confirmations");
    expect(confirmationPosts).toHaveLength(1);
    expect(JSON.parse(String(confirmationPosts[0][1]?.body))).toEqual({
      allocations: [{ investmentEventId: EVENT_A, cashTransactionId: CASH_A, allocatedAmount: "30.0000" }],
      expectedState: "a".repeat(64),
    });

    fireEvent.click(within(record).getByRole("button", { name: "Review removal" }));
    expect(request.mock.calls.some(([path]) => path.includes("cash-transactions"))).toBe(false);
    const removal = screen.getByRole("group", { name: "Confirm funding removal" });
    expect(document.activeElement).toBe(within(removal).getByRole("button", { name: "Remove funding link" }));
    fireEvent.click(within(removal).getByRole("button", { name: "Remove funding link" }));
    expect(await screen.findByText("1 funding allocation removed. Cash entry restored to expense.")).toBeTruthy();
    expect(screen.queryByRole("article", { name: /Confirmed funding/ })).toBeNull();
  });

  it("shows loading, empty and retryable candidate states", async () => {
    let attempts = 0;
    let release!: (value: TransactionsResponse) => void;
    const request = routes({
      "GET /transactions?amountMax=0&sort=bookedDate&direction=desc&limit=50": () => {
        attempts += 1;
        if (attempts === 1) throw new ApiError(403, "owner session required");
        return new Promise((resolve) => { release = resolve; });
      },
      "GET /transfers?status=confirmed&limit=50": { transfers: [], total: 0 },
    });
    renderPanel(request);
    fireEvent.click(screen.getByRole("button", { name: "Find cash entries" }));
    expect((await screen.findByRole("alert")).textContent).toBe("owner session required");
    fireEvent.click(screen.getByRole("button", { name: "Try cash entries again" }));
    expect(screen.getByText("Loading cash entries…")).toBeTruthy();
    release({ transactions: [], total: 0 });
    expect(await screen.findByText("No eligible cash debits are recorded.")).toBeTruthy();
  });

  it("contains no advisory language", () => {
    renderPanel(routes({}));
    expect(document.body.textContent).not.toMatch(/\b(buy|sell|hold|recommend|rebalance)\b/i);
  });
});
