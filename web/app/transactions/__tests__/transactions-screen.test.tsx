// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TransactionsScreen } from "@/app/transactions/transactions-screen";
import { formatMoney } from "@/lib/money/format";
import type { apiFetch } from "@/lib/api/client";
import type {
  AccountRow,
  CategoryRow,
  CurrencyRow,
  HouseholdCurrency,
  HouseholdResponse,
  TransactionRow,
} from "@/types/api";

const currencies: Array<CurrencyRow> = [
  { code: "XAE", name: "Synthetic A", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XIN", name: "Synthetic I", symbol: "I", decimals: 2, numberFormat: "indian" },
];

const reporting: HouseholdCurrency = {
  code: "XIN",
  symbol: "I",
  decimals: 2,
  numberFormat: "indian",
};

const household = {
  id: "hh-1",
  name: "Test Household",
  ownerDisplayName: "Owner",
  ownerTaxResidency: "XB",
  baseCurrency: "XIN",
  secondaryCurrency: "XAE",
  reportingPairLocked: false,
  currencies: [reporting, { code: "XAE", symbol: "A", decimals: 2, numberFormat: "international" }],
} as unknown as HouseholdResponse;

const account = {
  id: "acct-card",
  name: "Ledger Card",
  institutionId: null,
  institutionName: null,
  accountType: "credit_card",
  accountClass: "liability",
  accountSubtype: null,
  currency: "XAE",
  country: "XA",
  accountNumberMasked: null,
  openedOn: null,
  closedOn: null,
  isActive: true,
  includeInNetWorth: true,
  isLiquid: true,
  openingBalance: "0.0000",
  openingBalanceDate: "2024-01-01",
  openingBalanceNote: null,
  openingBalanceConfirmedAt: null,
  currentBalance: "-310.5000",
  currentBalanceAsOf: null,
  creditLimit: "20000.0000",
} as unknown as AccountRow;

const food: CategoryRow = {
  id: "cat-food",
  parentId: null,
  name: "Food & Dining",
  slug: "food-dining",
  kind: "expense",
  bucket: "want",
  sortOrder: 140,
  isSystem: true,
  isActive: true,
};

const groceries: CategoryRow = { ...food, id: "cat-groceries", name: "Groceries", slug: "groceries" };

function transaction(overrides: Partial<TransactionRow>): TransactionRow {
  return {
    id: "txn-1",
    accountId: "acct-card",
    accountName: "Ledger Card",
    bookedDate: "2024-02-02",
    descriptionRaw: "COFFEE",
    descriptionClean: null,
    amount: "-310.5000",
    currency: "XAE",
    amountBase: "-6210.0000",
    amountSecondary: "-310.5000",
    fxRateDate: "2024-02-02",
    fxRateIsCarried: false,
    fxRateToBase: "20.0000000000",
    fxRateToSecondary: "1.0000000000",
    txnType: "expense",
    categoryId: "cat-food",
    categoryName: "Food & Dining",
    merchantId: null,
    transferId: null,
    parentTransactionId: null,
    isSplitParent: false,
    isExcluded: false,
    classifiedBy: "user_manual",
    source: "manual",
    notes: null,
    ...overrides,
  };
}

function renderScreen(
  rows: Array<TransactionRow>,
  total: number,
  request: typeof apiFetch = vi.fn(),
) {
  return render(
    <TransactionsScreen
      accounts={[account]}
      categories={[food, groceries]}
      currencies={currencies}
      currency={reporting}
      household={household}
      onChanged={vi.fn()}
      request={request}
      total={total}
      transactions={rows}
    />,
  );
}

afterEach(cleanup);

describe("the transactions screen", () => {
  it("renders money exactly as sent, original first and converted second", () => {
    renderScreen([transaction({})], 1);
    const row = screen.getByText("COFFEE").closest("tr");
    expect(row).not.toBeNull();
    expect(row!.textContent).toContain(formatMoney("-310.5000", currencies[0]));
    expect(row!.textContent).toContain(formatMoney("-6210.0000", reporting));
    expect(row!.textContent).toContain("FX 20.0000000000 on 2024-02-02");
  });

  it("labels a carried-forward rate", () => {
    renderScreen([transaction({ fxRateIsCarried: true, fxRateDate: "2024-01-31" })], 1);
    expect(screen.getByText(/FX 20.0000000000 on 2024-01-31 \(carried forward\)/)).toBeDefined();
  });

  it("round-trips filters as query parameters, never interpolated", async () => {
    const request = vi.fn().mockResolvedValue({ transactions: [], total: 0 });
    renderScreen([transaction({})], 1, request as unknown as typeof apiFetch);
    fireEvent.change(screen.getByLabelText("Search descriptions"), {
      target: { value: "coffee" },
    });
    fireEvent.submit(screen.getByRole("form", { name: "Filter the ledger" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const url = (request.mock.calls[0] as Array<string>)[0];
    expect(url).toContain("/transactions?");
    expect(url).toContain("q=coffee");
    expect(url).toContain("limit=50");
    expect(url).toContain("offset=0");
  });

  it("PATCHes an inline category edit and re-renders the returned row", async () => {
    const request = vi
      .fn()
      .mockResolvedValue(transaction({ categoryId: "cat-groceries", categoryName: "Groceries" }));
    renderScreen([transaction({})], 1, request as unknown as typeof apiFetch);
    fireEvent.change(screen.getByLabelText("Category for COFFEE"), {
      target: { value: "cat-groceries" },
    });
    await waitFor(() => expect(request).toHaveBeenCalled());
    const [url, init] = request.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/transactions/txn-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body)).categoryId).toBe("cat-groceries");
    await waitFor(() =>
      expect((screen.getByLabelText("Category for COFFEE") as HTMLSelectElement).value).toBe(
        "cat-groceries",
      ),
    );
  });

  it("renders a 100-row fixture in one pass", () => {
    const rows = Array.from({ length: 100 }, (_, index) =>
      transaction({ id: `txn-${index}`, descriptionRaw: `ROW ${index}` }),
    );
    renderScreen(rows, 100);
    expect(screen.getAllByRole("row")).toHaveLength(101); // 100 rows plus the header
    expect(screen.queryByRole("button", { name: /Load/ })).toBeNull();
  });

  it("offers more rows when the ledger is longer than the page", () => {
    renderScreen([transaction({})], 120);
    expect(screen.getByRole("button", { name: "Load 50 more" })).toBeDefined();
  });

  it("teaches the first action when the ledger is empty", () => {
    renderScreen([], 0);
    expect(screen.getByText(/Record your first transaction/)).toBeDefined();
  });

  it("marks a transfer leg with its peer link", () => {
    renderScreen([transaction({ transferId: "tr-1", txnType: "transfer_out" })], 1);
    expect(screen.getByLabelText("Transfer leg, linked to its peer")).toBeDefined();
  });
});


it("round-trips a non-default sort as bounded parameters", async () => {
  const request = vi.fn().mockResolvedValue({ transactions: [], total: 0 });
  renderScreen([transaction({})], 1, request as unknown as typeof apiFetch);
  fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "amount" } });
  fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "asc" } });
  fireEvent.submit(screen.getByRole("form", { name: "Filter the ledger" }));
  await waitFor(() => expect(request).toHaveBeenCalled());
  const url = (request.mock.calls[0] as Array<string>)[0];
  expect(url).toContain("sort=amount");
  expect(url).toContain("direction=asc");
});


it("paginates on the applied filters, not edited-but-unapplied controls", async () => {
  const request = vi.fn().mockResolvedValue({ transactions: [], total: 120 });
  renderScreen([transaction({})], 120, request as unknown as typeof apiFetch);
  fireEvent.change(screen.getByLabelText("Search descriptions"), {
    target: { value: "unapplied" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Load 50 more" }));
  await waitFor(() => expect(request).toHaveBeenCalled());
  const url = (request.mock.calls[0] as Array<string>)[0];
  expect(url).not.toContain("q=unapplied");
  expect(url).toContain("offset=1");
});


it("refetches the page when a recategorized row may leave the applied filter", async () => {
  const request = vi
    .fn()
    .mockResolvedValueOnce({ transactions: [transaction({})], total: 1 }) // apply filter
    .mockResolvedValueOnce(transaction({ categoryId: "cat-groceries" })) // PATCH
    .mockResolvedValueOnce({ transactions: [], total: 0 }); // refetch
  renderScreen([transaction({})], 1, request as unknown as typeof apiFetch);
  const filterForm = screen.getByRole("form", { name: "Filter the ledger" });
  fireEvent.change(within(filterForm).getByLabelText("Category"), {
    target: { value: "cat-food" },
  });
  fireEvent.submit(filterForm);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText("Category for COFFEE"), {
    target: { value: "cat-groceries" },
  });
  await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
  const refetch = (request.mock.calls[2] as Array<string>)[0];
  expect(refetch).toContain("categoryId=cat-food");
});

it("round-trips the remaining filter controls", async () => {
  const request = vi.fn().mockResolvedValue({ transactions: [], total: 0 });
  renderScreen([transaction({})], 1, request as unknown as typeof apiFetch);
  fireEvent.change(screen.getByLabelText("Amount from"), { target: { value: "-1000.0000" } });
  fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "XAE" } });
  fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "trip" } });
  fireEvent.change(screen.getByLabelText("Review state"), { target: { value: "unreviewed" } });
  fireEvent.submit(screen.getByRole("form", { name: "Filter the ledger" }));
  await waitFor(() => expect(request).toHaveBeenCalled());
  const url = (request.mock.calls[0] as Array<string>)[0];
  expect(url).toContain("amountMin=-1000.0000");
  expect(url).toContain("currency=XAE");
  expect(url).toContain("tag=trip");
  expect(url).toContain("reviewState=unreviewed");
});


it("refetches when the applied review-state filter can go stale", async () => {
  const request = vi
    .fn()
    .mockResolvedValueOnce({ transactions: [transaction({})], total: 1 }) // apply filter
    .mockResolvedValueOnce(transaction({ categoryId: "cat-groceries" })) // PATCH marks reviewed
    .mockResolvedValueOnce({ transactions: [], total: 0 }); // refetch
  renderScreen([transaction({})], 1, request as unknown as typeof apiFetch);
  const filterForm = screen.getByRole("form", { name: "Filter the ledger" });
  fireEvent.change(within(filterForm).getByLabelText("Review state"), {
    target: { value: "unreviewed" },
  });
  fireEvent.submit(filterForm);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText("Category for COFFEE"), {
    target: { value: "cat-groceries" },
  });
  await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
  const refetch = (request.mock.calls[2] as Array<string>)[0];
  expect(refetch).toContain("reviewState=unreviewed");
});

it("round-trips merchant and source-document filters", async () => {
  const request = vi.fn().mockResolvedValue({ transactions: [], total: 0 });
  renderScreen([transaction({})], 1, request as unknown as typeof apiFetch);
  fireEvent.change(screen.getByLabelText("Merchant id"), { target: { value: "m-1" } });
  fireEvent.change(screen.getByLabelText("Source document id"), { target: { value: "doc-1" } });
  fireEvent.submit(screen.getByRole("form", { name: "Filter the ledger" }));
  await waitFor(() => expect(request).toHaveBeenCalled());
  const url = (request.mock.calls[0] as Array<string>)[0];
  expect(url).toContain("merchantId=m-1");
  expect(url).toContain("sourceDocumentId=doc-1");
});

it("filters offer every ledger type while entry stays manual-only", () => {
  renderScreen([transaction({})], 1);
  const filterForm = screen.getByRole("form", { name: "Filter the ledger" });
  const filterType = within(filterForm).getByLabelText("Type");
  expect(within(filterType).queryByRole("option", { name: "transfer_out" })).not.toBeNull();
  const entryForm = screen.getByRole("form", { name: "Record a transaction" });
  const entryType = within(entryForm).getByLabelText("Type");
  expect(within(entryType).queryByRole("option", { name: "transfer_out" })).toBeNull();
});


it("disables category editing on a split container", () => {
  renderScreen([transaction({ isSplitParent: true })], 1);
  const select = screen.getByLabelText("Category for COFFEE") as HTMLSelectElement;
  expect(select.disabled).toBe(true);
  expect(select.title).toContain("recategorize its children");
});
