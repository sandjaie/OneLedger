// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PeerPicker, TransferQueue } from "@/app/transactions/transfer-panel";
import type { apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type {
  Candidate,
  CurrencyRow,
  HouseholdResponse,
  TransactionRow,
  TransferRow,
} from "@/types/api";

// Two synthetic currencies that format differently — one symbol and grouping each — so a
// figure rendered in the wrong currency's row reads visibly wrong rather than coincidentally
// right. Every figure is invented.
const away: CurrencyRow = {
  code: "XAE",
  name: "Synthetic A",
  symbol: "A",
  decimals: 2,
  numberFormat: "international",
};
const home: CurrencyRow = {
  code: "XIN",
  name: "Synthetic I",
  symbol: "I",
  decimals: 2,
  numberFormat: "indian",
};
const currencies = [away, home];

const household = {
  id: "hh-1",
  baseCurrency: "XIN",
  secondaryCurrency: "XAE",
} as unknown as HouseholdResponse;

function leg(overrides: Partial<TransactionRow>): TransactionRow {
  return {
    id: "txn-out",
    accountId: "acct-a",
    accountName: "Account A",
    bookedDate: "2026-03-02",
    descriptionRaw: "OUTWARD REMIT",
    descriptionClean: "OUTWARD REMIT",
    amount: "-100.0000",
    currency: "XIN",
    originalAmount: null,
    originalCurrency: null,
    amountBase: "-100.0000",
    amountSecondary: "-25.0000",
    fxRateDate: "2026-03-02",
    fxRateIsCarried: false,
    fxRateToBase: "1.0000000000",
    fxRateToSecondary: "0.2500000000",
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

function candidate(band: string, score: number, suffix: string): Candidate {
  return {
    debit: leg({ id: `out-${suffix}`, descriptionRaw: `OUT ${suffix}` }),
    credit: leg({
      id: `in-${suffix}`,
      accountId: "acct-b",
      accountName: "Account B",
      descriptionRaw: `IN ${suffix}`,
      amount: "100.0000",
      amountBase: "100.0000",
    }),
    score,
    band,
    proposedKind: "internal_same_currency",
  };
}

function transfer(overrides: Partial<TransferRow>): TransferRow {
  return {
    id: "tr-1",
    sourceAccountId: "acct-a",
    destinationAccountId: "acct-b",
    sourceTransactionId: "out-1",
    destinationTransactionId: "in-1",
    sourceAmount: "5000.0000",
    sourceCurrency: "XAE",
    destinationAmount: "111000.0000",
    destinationCurrency: "XIN",
    transferDate: "2026-03-10",
    settlementDate: null,
    impliedRate: "22.2000000000",
    marketRate: "22.5500000000",
    fxSpreadPct: "1.5521",
    fxSpreadCostSource: "77.6050",
    explicitFeeAmount: null,
    explicitFeeCurrency: null,
    totalCostSource: "77.6050",
    transferKind: "remittance",
    provider: null,
    matchConfidence: "0.912",
    matchMethod: "auto",
    status: "confirmed",
    notes: null,
    costIsApproximate: false,
    ...overrides,
  };
}

type Answer = unknown | Error | ((init?: RequestInit) => unknown);

/** A request that answers by method and path, so a component's extra request never shifts
 * another test's expectation along a fixed sequence. */
function routes(table: Record<string, Answer>) {
  return vi.fn(async (path: string, init?: RequestInit) => {
    const answer = table[`${init?.method ?? "GET"} ${path}`];
    if (answer === undefined) throw new Error(`unexpected request: ${init?.method ?? "GET"} ${path}`);
    if (answer instanceof Error) throw answer;
    return typeof answer === "function" ? (answer as (i?: RequestInit) => unknown)(init) : answer;
  });
}

/** The shape `apiFetch` rejects with: the service's own sentence in `detail`. */
function refusal(status: number, detail: string): Error {
  return Object.assign(new Error(`${status}: ${detail}`), { status, detail });
}

function queue(request: ReturnType<typeof routes>, onChanged = vi.fn()) {
  render(
    <TransferQueue
      currencies={currencies}
      household={household}
      onChanged={onChanged}
      request={request as unknown as typeof apiFetch}
    />,
  );
  return onChanged;
}

afterEach(cleanup);

describe("the transfer queue", () => {
  it("loads nothing until asked, and posts nothing when it opens", async () => {
    const request = routes({
      "GET /transfers/candidates": { candidates: [candidate("high", 0.95, "1")], total: 1 },
      "GET /transfers?status=confirmed": { transfers: [], total: 0 },
    });
    queue(request);

    expect(request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));

    await screen.findByText("OUT 1", { exact: false });
    const methods = request.mock.calls.map(([, init]) => (init as RequestInit | undefined)?.method ?? "GET");
    expect(methods.every((method) => method === "GET")).toBe(true);
  });

  it("groups by band and pre-ticks only the high-confidence pairs", async () => {
    queue(
      routes({
        "GET /transfers/candidates": {
          candidates: [
            candidate("high", 0.95, "1"),
            candidate("probable", 0.8, "2"),
            candidate("weak", 0.6, "3"),
          ],
          total: 3,
        },
        "GET /transfers?status=confirmed": { transfers: [], total: 0 },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));

    const high = await screen.findByRole("group", { name: /High confidence/ });
    const probable = screen.getByRole("group", { name: /Probable/ });
    const weak = screen.getByRole("group", { name: /Weak/ });
    expect((within(high).getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    expect((within(probable).getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
    expect((within(weak).getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
  });

  it("confirming posts once per ticked pair, as a detected pair, and reports the change", async () => {
    const request = routes({
      "GET /transfers/candidates": {
        candidates: [candidate("high", 0.95, "1"), candidate("probable", 0.8, "2")],
        total: 2,
      },
      "GET /transfers?status=confirmed": { transfers: [], total: 0 },
      "POST /transfers": transfer({ sourceTransactionId: "out-1", destinationTransactionId: "in-1" }),
    });
    const onChanged = queue(request);
    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));
    await screen.findByRole("group", { name: /High confidence/ });

    fireEvent.click(screen.getByRole("button", { name: "Confirm selected" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    const posts = request.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String((posts[0][1] as RequestInit).body))).toEqual({
      sourceTransactionId: "out-1",
      destinationTransactionId: "in-1",
      fromCandidate: true,
    });
  });

  it("confirms one pair per leg when two ticked pairs share one", async () => {
    // Two high-confidence pairs proposing the same debit against different credits. Both are
    // pre-ticked; only one can become a transfer, so confirming must post once and drop the
    // other rather than send it on to a certain 409.
    const first = candidate("high", 0.95, "1");
    const sibling = candidate("high", 0.93, "2");
    sibling.debit = first.debit;
    const request = routes({
      "GET /transfers/candidates": { candidates: [first, sibling], total: 2 },
      "GET /transfers?status=confirmed": { transfers: [], total: 0 },
      "POST /transfers": transfer({}),
    });
    const onChanged = queue(request);
    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));
    await screen.findByRole("group", { name: /High confidence/ });

    fireEvent.click(screen.getByRole("button", { name: "Confirm selected" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    const posts = request.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(posts).toHaveLength(1);
    await waitFor(() => expect(screen.queryByText("IN 2", { exact: false })).toBeNull());
  });

  it("a refused confirmation shows the service's own sentence", async () => {
    queue(
      routes({
        "GET /transfers/candidates": { candidates: [candidate("high", 0.95, "1")], total: 1 },
        "GET /transfers?status=confirmed": { transfers: [], total: 0 },
        "POST /transfers": refusal(409, "a transaction is already a leg of a confirmed transfer"),
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));
    await screen.findByRole("group", { name: /High confidence/ });

    fireEvent.click(screen.getByRole("button", { name: "Confirm selected" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "a transaction is already a leg of a confirmed transfer",
    );
  });

  it("unlinking deletes once and reports the change", async () => {
    const request = routes({
      "GET /transfers/candidates": { candidates: [], total: 0 },
      "GET /transfers?status=confirmed": { transfers: [transfer({})], total: 1 },
      "DELETE /transfers/tr-1": transfer({ status: "rejected" }),
    });
    const onChanged = queue(request);
    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));

    fireEvent.click(await screen.findByRole("button", { name: /Unlink/ }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    const deletes = request.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "DELETE");
    expect(deletes.map(([path]) => path)).toEqual(["/transfers/tr-1"]);
    await waitFor(() => expect(screen.queryByRole("button", { name: /Unlink/ })).toBeNull());
  });

  it("says a cost is approximate when its market rate was carried forward, and not otherwise", async () => {
    queue(
      routes({
        "GET /transfers/candidates": { candidates: [], total: 0 },
        "GET /transfers?status=confirmed": {
          transfers: [
            transfer({ id: "tr-carried", costIsApproximate: true }),
            transfer({ id: "tr-observed", costIsApproximate: false }),
          ],
          total: 2,
        },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));

    const carried = await screen.findByTestId("transfer-tr-carried");
    const observed = screen.getByTestId("transfer-tr-observed");
    expect(carried.textContent).toMatch(/approximate/i);
    expect(observed.textContent).not.toMatch(/approximate/i);
  });

  it("formats each leg in its own currency, the base figure in the household's, and a cost in its source's", async () => {
    const abroad = candidate("high", 0.95, "1");
    abroad.debit = leg({
      id: "out-1",
      currency: "XAE",
      amount: "-100000.0000",
      amountBase: "-2255000.0000",
    });
    queue(
      routes({
        "GET /transfers/candidates": { candidates: [abroad], total: 1 },
        "GET /transfers?status=confirmed": { transfers: [transfer({})], total: 1 },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));

    const group = await screen.findByRole("group", { name: /High confidence/ });
    expect(group.textContent).toContain(formatMoney("-100000.0000", away));
    expect(group.textContent).toContain(formatMoney("-2255000.0000", home));
    expect(group.textContent).toContain(formatMoney("100.0000", home));
    const cost = screen.getByTestId("transfer-tr-1");
    expect(cost.textContent).toContain(formatMoney("77.6050", away));
    expect(cost.textContent).toContain("1.5521%");
    expect(cost.textContent).toContain("22.2000000000");
  });
});

describe("linking one transaction by hand", () => {
  it("ranks its peers, narrows them by search, and links the chosen one as the owner's", async () => {
    const subject = leg({ id: "txn-out" });
    const near = candidate("probable", 0.8, "near");
    near.debit = subject;
    const far = candidate("hidden", 0.1, "far");
    far.debit = subject;
    far.credit = { ...far.credit, descriptionRaw: "SOMETHING ELSE" };
    const request = routes({
      "GET /transactions/txn-out/transfer-candidates": { peers: [near, far] },
      "POST /transfers": transfer({}),
    });
    const onChanged = vi.fn();
    const onClose = vi.fn();
    render(
      <PeerPicker
        currencies={currencies}
        household={household}
        onChanged={onChanged}
        onClose={onClose}
        request={request as unknown as typeof apiFetch}
        transaction={subject}
      />,
    );

    const options = await screen.findAllByRole("button", { name: /^Link / });
    expect(options.map((option) => option.getAttribute("aria-label"))).toEqual([
      "Link IN near",
      "Link SOMETHING ELSE",
    ]);
    fireEvent.change(screen.getByLabelText("Search peers"), { target: { value: "near" } });
    expect(screen.getAllByRole("button", { name: /^Link / })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Link IN near" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onChanged).toHaveBeenCalledTimes(1);
    const post = request.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({
      sourceTransactionId: "txn-out",
      destinationTransactionId: "in-near",
      fromCandidate: false,
    });
  });
});
