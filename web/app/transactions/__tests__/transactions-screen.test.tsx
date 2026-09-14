// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TransactionsScreen } from "@/app/transactions/transactions-screen";
import { formatMoney } from "@/lib/money/format";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  AccountRow,
  CategoryRow,
  CurrencyRow,
  HouseholdCurrency,
  HouseholdResponse,
  TransactionRow,
  TransferRow,
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
const coffee: CategoryRow = {
  ...food,
  id: "cat-coffee",
  name: "Coffee",
  slug: "food-dining-coffee",
  parentId: "cat-food",
};

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
    originalAmount: null,
    originalCurrency: null,
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
    sourceDocumentId: null,
    sourcePageNumber: null,
    sourceRowNumber: null,
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
      categories={[food, groceries, coffee]}
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
  it("keeps the compact filters and ledger ahead of optional manual entry", () => {
    renderScreen([transaction({})], 1);

    expect(screen.getByText("1 transaction · 1 active account")).toBeTruthy();
    expect(screen.queryByRole("form", { name: "Record a transaction" })).toBeNull();
    expect(screen.getByRole("form", { name: "Filter the ledger" }).className).toContain(
      "transaction-filters",
    );
    expect(screen.getByRole("table", { name: "The transaction ledger" }).className).toContain(
      "transaction-ledger",
    );

    const entryToggle = screen.getByRole("button", { name: "Record a transaction" });
    expect(entryToggle.getAttribute("aria-expanded")).toBe("false");
    expect(entryToggle.getAttribute("aria-controls")).toBe("manual-transaction-entry");

    fireEvent.click(entryToggle);
    expect(screen.getByRole("form", { name: "Record a transaction" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Close manual entry" }).getAttribute("aria-expanded"))
      .toBe("true");
  });

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
    expect(screen.getByText(/Use Record a transaction for a manual entry/)).toBeDefined();
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
  fireEvent.change(screen.getByLabelText("Classification"), {
    target: { value: "unclassified" },
  });
  fireEvent.submit(screen.getByRole("form", { name: "Filter the ledger" }));
  await waitFor(() => expect(request).toHaveBeenCalled());
  const url = (request.mock.calls[0] as Array<string>)[0];
  expect(url).toContain("amountMin=-1000.0000");
  expect(url).toContain("currency=XAE");
  expect(url).toContain("tag=trip");
  expect(url).toContain("reviewState=unreviewed");
  expect(url).toContain("classifiedBy=unclassified");
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

it("pre-fills the source-document filter carried from the Documents screen's URL", () => {
  render(
    <TransactionsScreen
      accounts={[account]}
      categories={[food, groceries, coffee]}
      currencies={currencies}
      currency={reporting}
      household={household}
      initialSourceDocumentId="doc-9"
      onChanged={vi.fn()}
      request={vi.fn()}
      total={1}
      transactions={[transaction({})]}
    />,
  );

  expect((screen.getByLabelText("Source document id") as HTMLInputElement).value).toBe("doc-9");
});

it("links an imported row's page and row number to its original", () => {
  renderScreen(
    [transaction({ sourceDocumentId: "doc-1", sourcePageNumber: 2, sourceRowNumber: 5 })],
    1,
  );

  const link = screen.getByRole("link", { name: "Page 2 · row 5" });
  expect(link.getAttribute("href")).toBe("http://localhost:8000/documents/doc-1/original");
  expect(link.getAttribute("target")).toBe("_blank");
});

it("filters offer every ledger type while entry stays manual-only", () => {
  renderScreen([transaction({})], 1);
  const filterForm = screen.getByRole("form", { name: "Filter the ledger" });
  const filterType = within(filterForm).getByLabelText("Type");
  expect(within(filterType).queryByRole("option", { name: "transfer_out" })).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Record a transaction" }));
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


it("labels a subcategory with its parent path", () => {
  renderScreen([transaction({})], 1);
  fireEvent.click(screen.getByRole("button", { name: "Record a transaction" }));
  const entryForm = screen.getByRole("form", { name: "Record a transaction" });
  const entryCategory = within(entryForm).getByLabelText("Category");
  expect(
    within(entryCategory).queryByRole("option", { name: "Food & Dining → Coffee" }),
  ).not.toBeNull();
});


it("marks every row with its provenance", () => {
  renderScreen([transaction({})], 1);
  const row = screen.getByText("COFFEE").closest("tr");
  expect(row!.textContent).toContain("· manual");
});


it("shows the bank's original conversion when one exists", () => {
  renderScreen(
    [transaction({ originalAmount: "-84.5000", originalCurrency: "XIN" })],
    1,
  );
  const row = screen.getByText("COFFEE").closest("tr");
  expect(row!.textContent).toContain("originally");
});


it("sends the entry note and edits a row note in place", async () => {
  const request = vi
    .fn()
    .mockResolvedValueOnce(transaction({ notes: "row note" })) // PATCH
    .mockResolvedValue({ transactions: [], total: 0 });
  renderScreen([transaction({})], 1, request as unknown as typeof apiFetch);
  const noteField = screen.getByLabelText("Note for COFFEE");
  fireEvent.change(noteField, { target: { value: "row note" } });
  fireEvent.blur(noteField);
  await waitFor(() => expect(request).toHaveBeenCalled());
  const [url, init] = request.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/transactions/txn-1");
  const body = JSON.parse(String(init.body));
  expect(body.notes).toBe("row note");
  expect(body.categoryId).toBe("cat-food");
});

describe("asking how to classify an unclassified row", () => {
  it("previews a digit-free substring before writing anything", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/rules/preview") {
        return { affected: 3, unclassified: 2, sample: [] };
      }
      return {};
    });
    renderScreen(
      [
        transaction({
          categoryId: null,
          categoryName: null,
          classifiedBy: "unclassified",
          descriptionRaw: "NOON MINUTES 1234 / ORDER",
        }),
      ],
      1,
      request as unknown as typeof apiFetch,
    );

    fireEvent.change(screen.getByLabelText("Category for NOON MINUTES 1234 / ORDER"), {
      target: { value: "cat-groceries" },
    });

    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("3 matching rows");
    expect(status.textContent).toContain("2 need a category");
    const preview = request.mock.calls.find(([path]) => path === "/rules/preview") as unknown as
      | [string, RequestInit]
      | undefined;
    const body = JSON.parse(String(preview?.[1].body));
    expect(body.matchDescriptionPattern).toBe("NOON MINUTES");
    expect(body.matchPatternKind).toBe("contains");
    expect(body.setCategoryId).toBe("cat-groceries");
    expect(request.mock.calls.some(([path]) => path === "/transactions/txn-1")).toBe(false);
  });

  it("creates and applies the new rule by default, then reloads the ledger", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/rules/preview") {
        return { affected: 2, unclassified: 2, sample: [] };
      }
      if (path === "/rules" && init?.method === "POST") return { id: "rule-new" };
      if (path === "/classify?ruleId=rule-new") {
        return { considered: 4, changed: 1, applied: true, sample: [] };
      }
      if (path.startsWith("/transactions?")) return { transactions: [], total: 0 };
      return {};
    });
    renderScreen(
      [transaction({ categoryId: null, categoryName: null, classifiedBy: "unclassified" })],
      1,
      request as unknown as typeof apiFetch,
    );
    fireEvent.change(screen.getByLabelText("Category for COFFEE"), {
      target: { value: "cat-groceries" },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Always mark rows like this" }));

    await waitFor(() =>
      expect(request.mock.calls.some(([path]) => path === "/classify?ruleId=rule-new")).toBe(true),
    );
    expect(request.mock.calls.some(([path]) => path.startsWith("/transactions?"))).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("1 row categorized");
    expect(screen.getByRole("status").textContent).toContain(
      "An earlier rule decided 1 matching row",
    );
  });

  it("can apply the category only to the chosen row", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/rules/preview") {
        return { affected: 2, unclassified: 2, sample: [] };
      }
      if (path === "/transactions/txn-1") {
        return transaction({ categoryId: "cat-groceries", categoryName: "Groceries" });
      }
      return {};
    });
    renderScreen(
      [transaction({ categoryId: null, categoryName: null, classifiedBy: "unclassified" })],
      1,
      request as unknown as typeof apiFetch,
    );
    fireEvent.change(screen.getByLabelText("Category for COFFEE"), {
      target: { value: "cat-groceries" },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Just this one" }));

    await waitFor(() =>
      expect(request.mock.calls.some(([path]) => path === "/transactions/txn-1")).toBe(true),
    );
    expect(request.mock.calls.some(([path]) => path === "/rules")).toBe(false);
  });

  it("keeps the prompt open with the service sentence when the one-row change is refused", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/rules/preview") {
        return { affected: 2, unclassified: 2, sample: [] };
      }
      if (path === "/transactions/txn-1") {
        throw new ApiError(409, "this row changed since the prompt opened");
      }
      return {};
    });
    renderScreen(
      [transaction({ categoryId: null, categoryName: null, classifiedBy: "unclassified" })],
      1,
      request as unknown as typeof apiFetch,
    );
    fireEvent.change(screen.getByLabelText("Category for COFFEE"), {
      target: { value: "cat-groceries" },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Just this one" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "this row changed since the prompt opened",
    );
    expect(screen.getByRole("button", { name: "Always mark rows like this" })).toBeTruthy();
  });

  it("disables rule creation when the edited pattern no longer occurs in the row", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/rules/preview") return { affected: 1, unclassified: 1, sample: [] };
      return {};
    });
    renderScreen(
      [transaction({ categoryId: null, categoryName: null, classifiedBy: "unclassified" })],
      1,
      request as unknown as typeof apiFetch,
    );
    fireEvent.change(screen.getByLabelText("Category for COFFEE"), {
      target: { value: "cat-groceries" },
    });
    await screen.findByRole("status");

    fireEvent.change(screen.getByLabelText("Pattern"), { target: { value: "AIRLINE" } });

    expect(
      (screen.getByRole("button", { name: "Always mark rows like this" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows the service sentence when preview is refused", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/rules/preview") {
        throw new ApiError(422, "the proposed rule is too broad");
      }
      return {};
    });
    renderScreen(
      [transaction({ categoryId: null, categoryName: null, classifiedBy: "unclassified" })],
      1,
      request as unknown as typeof apiFetch,
    );

    fireEvent.change(screen.getByLabelText("Category for COFFEE"), {
      target: { value: "cat-groceries" },
    });

    expect((await screen.findByRole("alert")).textContent).toBe("the proposed rule is too broad");
  });
});

describe("transfers on the transactions screen", () => {
  function linked(id: string, transferId: string | null): TransactionRow {
    return transaction({
      id,
      descriptionRaw: id === "txn-out" ? "OUTWARD REMIT" : "INWARD REMIT",
      amount: id === "txn-out" ? "-310.5000" : "310.5000",
      transferId,
    });
  }

  const made: TransferRow = {
    id: "tr-1",
    sourceAccountId: "acct-card",
    destinationAccountId: "acct-card-2",
    sourceTransactionId: "txn-out",
    destinationTransactionId: "txn-in",
    sourceAmount: "310.5000",
    sourceCurrency: "XAE",
    destinationAmount: "310.5000",
    destinationCurrency: "XAE",
    transferDate: "2024-02-02",
    settlementDate: null,
    impliedRate: null,
    marketRate: null,
    fxSpreadPct: null,
    fxSpreadCostSource: null,
    explicitFeeAmount: null,
    explicitFeeCurrency: null,
    totalCostSource: null,
    transferKind: "internal_same_currency",
    provider: null,
    matchConfidence: "0.950",
    matchMethod: "auto",
    status: "confirmed",
    notes: null,
    costIsApproximate: false,
  };

  /** Answers by method and path; the ledger reload is any GET under /transactions?. */
  function answering(table: Record<string, unknown>, ledger: () => unknown) {
    return vi.fn(async (path: string, init?: RequestInit) => {
      if (path.startsWith("/transactions?")) return ledger();
      const answer = table[`${init?.method ?? "GET"} ${path}`];
      if (answer === undefined) throw new Error(`unexpected request: ${path}`);
      return answer;
    });
  }

  function legs() {
    return screen.queryAllByRole("img", { name: "Transfer leg, linked to its peer" });
  }

  it("presents both confirmed legs as one connected transfer movement", () => {
    const outgoing = linked("txn-out", "tr-1");
    const incoming = {
      ...linked("txn-in", "tr-1"),
      accountId: "acct-card-2",
      accountName: "Savings account",
      amount: "6210.0000",
      currency: "XIN",
    };

    renderScreen([outgoing, incoming], 2);

    const movement = screen.getByLabelText(
      "Confirmed transfer from Ledger Card to Savings account",
    );
    expect(movement.textContent).toContain("TRANSFER");
    expect(movement.textContent).toContain("-A 310.50");
    expect(movement.textContent).toContain("I 6,210.00");
    expect(screen.getAllByRole("row").filter((row) =>
      row.className.includes("transaction-transfer-row"),
    )).toHaveLength(2);
  });

  it("offers Link as transfer only on a row that is not already a leg", () => {
    // A leg already linked cannot be linked again; offering it would lead only to a refusal.
    renderScreen([linked("txn-out", "tr-1"), linked("txn-in", null)], 2);

    expect(screen.queryByRole("button", { name: "Link OUTWARD REMIT as a transfer" })).toBeNull();
    expect(screen.getByRole("button", { name: "Link INWARD REMIT as a transfer" })).toBeDefined();
  });

  it("offers the transfer queue without loading it", () => {
    const request = vi.fn();
    renderScreen([linked("txn-out", null)], 1, request as unknown as typeof apiFetch);

    expect(screen.getByRole("button", { name: "Find transfers" })).toBeDefined();
    expect(request).not.toHaveBeenCalled();
  });

  it("confirming a pair re-renders both of its legs as a transfer", async () => {
    let confirmed = false;
    const request = answering(
      {
        "GET /transfers/candidates": {
          candidates: [
            {
              debit: linked("txn-out", null),
              credit: linked("txn-in", null),
              score: 0.95,
              band: "high",
              proposedKind: "internal_same_currency",
            },
          ],
          total: 1,
        },
        "GET /transfers?status=confirmed": { transfers: [], total: 0 },
        "POST /transfers": made,
      },
      () => ({
        transactions: [
          linked("txn-out", confirmed ? "tr-1" : null),
          linked("txn-in", confirmed ? "tr-1" : null),
        ],
        total: 2,
      }),
    );
    renderScreen([linked("txn-out", null), linked("txn-in", null)], 2, request as unknown as typeof apiFetch);
    expect(legs()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));
    await screen.findByRole("group", { name: /High confidence/ });
    confirmed = true;
    fireEvent.click(screen.getByRole("button", { name: "Confirm selected" }));

    await waitFor(() => expect(legs()).toHaveLength(2));
  });

  it("unlinking returns both legs to how they looked before they were linked", async () => {
    let released = false;
    const request = answering(
      {
        "GET /transfers/candidates": { candidates: [], total: 0 },
        "GET /transfers?status=confirmed": { transfers: [made], total: 1 },
        "DELETE /transfers/tr-1": { ...made, status: "rejected" },
      },
      () => ({
        transactions: [
          linked("txn-out", released ? null : "tr-1"),
          linked("txn-in", released ? null : "tr-1"),
        ],
        total: 2,
      }),
    );
    renderScreen([linked("txn-out", "tr-1"), linked("txn-in", "tr-1")], 2, request as unknown as typeof apiFetch);
    expect(legs()).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "Find transfers" }));
    const unlink = await screen.findByRole("button", { name: /Unlink/ });
    released = true;
    fireEvent.click(unlink);

    await waitFor(() => expect(legs()).toHaveLength(0));
  });
});
