// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewScreen } from "@/app/import/review-screen";
import { ApiError } from "@/lib/api/client";
import type {
  AccountRow,
  CurrencyRow,
  HouseholdCurrency,
  ReviewBatch,
  ReviewGroup,
  ReviewRow,
} from "@/types/api";

const catalogue: CurrencyRow = {
  code: "XAE",
  name: "Synthetic A",
  symbol: "A",
  decimals: 2,
  numberFormat: "international",
};

// A second currency with different grouping, so a test can tell which one was used.
const other: CurrencyRow = {
  code: "XIN",
  name: "Synthetic I",
  symbol: "I",
  decimals: 2,
  numberFormat: "indian",
};

const currencies: Array<CurrencyRow> = [catalogue, other];
const accounts = [
  { id: "acct-card", name: "Synthetic Card", currency: "XAE" },
  { id: "acct-savings", name: "Synthetic Savings", currency: "XIN" },
] as Array<AccountRow>;
const currency: HouseholdCurrency = {
  code: catalogue.code,
  symbol: catalogue.symbol,
  decimals: catalogue.decimals,
  numberFormat: catalogue.numberFormat,
};

function row(overrides: Partial<ReviewRow> = {}): ReviewRow {
  return {
    id: "row-1",
    sourcePageNumber: 1,
    sourceRowNumber: 1,
    bookedDate: "2026-03-01",
    valueDate: null,
    descriptionRaw: "COFFEE SHOP",
    descriptionClean: null,
    referenceRaw: null,
    amount: "-10.0000",
    currency: "XAE",
    // Deliberately unequal to `amount`: an account in one currency reporting in another, which
    // is the only shape in which a test can tell the two figures apart.
    amountBase: "-40.0000",
    amountSecondary: "-10.0000",
    proposedTxnType: "expense",
    status: "new",
    duplicateOfTransactionId: null,
    nearDuplicateOfId: null,
    ...overrides,
  };
}

function group(status: string, rows: Array<ReviewRow>): ReviewGroup {
  return { status, count: rows.length, rows };
}

function batch(overrides: Partial<ReviewBatch> = {}): ReviewBatch {
  return {
    batchId: "batch-1",
    documentId: "doc-1",
    accountId: "acct-card",
    status: "awaiting_review",
    layoutId: "layout-1",
    layoutVersion: 1,
    period: { start: "2026-03-01", end: "2026-03-31", isDerived: null },
    rowsExtracted: 2,
    reconciliation: {
      status: "matched",
      declaredOpening: "1000.0000",
      openingIsDerived: false,
      declaredClosing: "1490.0000",
      computedClosing: "1490.0000",
      delta: "0.0000",
      firstDivergentRow: null,
      previousBalance: null,
      amount: null,
      candidateRows: [],
      message: "",
    },
    groups: [
      group("new", [
        row(),
        // The converted amount is overridden with it: a row whose frozen pair does not match
        // its own amount is a row no staging produces.
        row({
          id: "row-2",
          sourceRowNumber: 2,
          amount: "500.0000",
          amountBase: "2000.0000",
          amountSecondary: "500.0000",
        }),
      ]),
    ],
    ...overrides,
  };
}

function renderScreen(request: ReturnType<typeof vi.fn>, batchId = "batch-1") {
  render(
    <ReviewScreen
      accounts={accounts}
      batchId={batchId}
      currencies={currencies}
      currency={currency}
      request={request as never}
    />,
  );
}

afterEach(cleanup);

describe("reading a batch", () => {
  it("shows the reconciliation banner and every group in the order the API sent", async () => {
    const request = vi.fn().mockResolvedValue(
      batch({
        groups: [
          group("needs_review", [row({ id: "r1", status: "needs_review" })]),
          group("new", [row({ id: "r2" })]),
          group("duplicate", [row({ id: "r3", status: "duplicate" })]),
        ],
      }),
    );
    renderScreen(request);

    await screen.findByRole("heading", { name: /review/i });
    expect(request).toHaveBeenCalledWith("/import/batch-1");
    const headings = screen
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent ?? "");
    expect(headings.map((text) => text.split(" ")[0].toLowerCase())).toEqual([
      "needs_review",
      "new",
      "duplicate",
    ]);
    expect(screen.getByText(/reconciled/i)).toBeTruthy();
  });

  it("formats every amount and computes none of them", async () => {
    const request = vi.fn().mockResolvedValue(batch());
    renderScreen(request);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    expect(within(first).getByText("-A 10.00", { selector: ".review-amount" })).toBeTruthy();
    expect(within(first).getByText("COFFEE SHOP")).toBeTruthy();
    expect(within(first).getByText("2026-03-01")).toBeTruthy();
    // The declared closing is the statement's own figure, formatted from its string.
    expect(screen.getByText(/A 1,490\.00/)).toBeTruthy();
  });

  it("formats the account's figures in the account's currency, not the household's", async () => {
    // The declared closing is the account's figure. A committed batch carries no groups at all,
    // so reading the currency off the first row leaves the banner formatting XIN money with the
    // household's XAE symbol.
    const request = vi.fn().mockResolvedValue(
      batch({
        accountId: "acct-savings",
        status: "completed",
        groups: [],
        reconciliation: { ...batch().reconciliation, declaredClosing: "5982530.0000" },
      }),
    );
    renderScreen(request);

    expect(await screen.findByText(/I 59,82,530\.00/)).toBeTruthy();
  });

  it("shows the frozen converted amount beside the original, never the conversion alone", async () => {
    // `ui-information-architecture.html` section 4. The frozen pair is the whole point of
    // freezing at staging, and this is the screen where the owner decides whether it is written.
    const request = vi.fn().mockResolvedValue(batch());
    renderScreen(request);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    expect(within(first).getByText("-A 10.00", { selector: ".review-amount" })).toBeTruthy();
    expect(within(first).getByText("-A 40.00", { selector: ".review-converted" })).toBeTruthy();
  });

  it("says a batch cannot be read rather than rendering an empty screen", async () => {
    const request = vi.fn().mockRejectedValue(new ApiError(404, "no such import batch"));
    renderScreen(request);

    expect(await screen.findByText("no such import batch")).toBeTruthy();
  });
});

describe("deciding rows", () => {
  it("accepts nothing by default", async () => {
    const request = vi.fn().mockResolvedValue(batch());
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    for (const box of screen.getAllByRole<HTMLInputElement>("checkbox")) {
      expect(box.checked).toBe(false);
    }
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: /^accept selected/i }).disabled,
    ).toBe(true);
  });

  it("sends only the rows the owner ticked, and reloads the batch after", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch())
      .mockResolvedValueOnce({ updated: 1 })
      .mockResolvedValueOnce(batch({ groups: [group("accepted", [row({ status: "accepted" })])] }));
    renderScreen(request);
    const first = await screen.findByRole("listitem", { name: /row 1/i });
    fireEvent.click(within(first).getByRole("checkbox"));

    fireEvent.click(screen.getByRole("button", { name: /^accept selected/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    const [path, init] = request.mock.calls[1];
    expect(path).toBe("/import/batch-1/rows");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({
      rowIds: ["row-1"],
      decision: "accepted",
    });
    expect(request.mock.calls[2][0]).toBe("/import/batch-1");
  });

  it("rejects on its own action, with the same selection", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch())
      .mockResolvedValueOnce({ updated: 1 })
      .mockResolvedValueOnce(batch());
    renderScreen(request);
    const first = await screen.findByRole("listitem", { name: /row 1/i });
    fireEvent.click(within(first).getByRole("checkbox"));

    fireEvent.click(screen.getByRole("button", { name: /^reject selected/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    expect(JSON.parse(request.mock.calls[1][1].body as string).decision).toBe("rejected");
  });

  it("selects a whole group at once without touching the others", async () => {
    const request = vi.fn().mockResolvedValue(
      batch({
        groups: [
          group("new", [row({ id: "a" }), row({ id: "b", sourceRowNumber: 2 })]),
          group("duplicate", [row({ id: "c", sourceRowNumber: 3, status: "duplicate" })]),
        ],
      }),
    );
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    fireEvent.click(screen.getByRole("checkbox", { name: /select the 2 new rows shown/i }));

    const ticked = screen
      .getAllByRole<HTMLInputElement>("checkbox")
      .filter((box) => box.checked && box.getAttribute("aria-label")?.startsWith("Row"));
    expect(ticked).toHaveLength(2);
  });

  it("names the rows it will tick, not the group's total", async () => {
    // `count` is stated separately from `rows` because a later page of a large group will not
    // carry every row. A label promising more than it ticks stays true only until then.
    const request = vi.fn().mockResolvedValue(
      batch({
        groups: [
          { status: "new", count: 240, rows: [row(), row({ id: "b", sourceRowNumber: 2 })] },
        ],
      }),
    );
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    expect(screen.getByRole("checkbox", { name: "Select the 2 new rows shown" })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: /240/ })).toBeNull();
  });

  it("stays busy until the reloaded batch has arrived", async () => {
    // The buttons must not re-enable over a batch the screen has already discarded.
    let land: (value: unknown) => void = () => {};
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch())
      .mockResolvedValueOnce({ updated: 1 })
      .mockImplementationOnce(() => new Promise((resolve) => { land = resolve; }));
    renderScreen(request);
    const first = await screen.findByRole("listitem", { name: /row 1/i });
    fireEvent.click(within(first).getByRole("checkbox"));

    fireEvent.click(screen.getByRole("button", { name: /^accept selected/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: /^reject selected/i }).disabled,
    ).toBe(true);
    land(batch({ groups: [group("accepted", [row({ status: "accepted" })])] }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /import 1 row/i })).toBeTruthy(),
    );
  });

  it("says what a decision refused rather than leaving the screen unchanged", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch())
      .mockRejectedValueOnce(new ApiError(422, "some of those rows are not in this batch"));
    renderScreen(request);
    const first = await screen.findByRole("listitem", { name: /row 1/i });
    fireEvent.click(within(first).getByRole("checkbox"));

    fireEvent.click(screen.getByRole("button", { name: /^accept selected/i }));

    expect(await screen.findByText("some of those rows are not in this batch")).toBeTruthy();
  });
});

describe("a duplicate row", () => {
  it("is marked, and accepting it states the consequence", async () => {
    const request = vi.fn().mockResolvedValue(
      batch({
        groups: [
          group("duplicate", [
            row({ id: "dup", status: "duplicate", duplicateOfTransactionId: "txn-1" }),
          ]),
        ],
      }),
    );
    renderScreen(request);

    const duplicate = await screen.findByRole("listitem", { name: /row 1/i });

    expect(within(duplicate).getByText(/already in the ledger/i)).toBeTruthy();
    expect(
      screen.getByText(/accepting .* imports it as a separate transaction/i),
    ).toBeTruthy();
  });

  it("is not ticked when its group is selected wholesale", async () => {
    // Nothing imports without a click, and a duplicate is excluded by default. Sweeping a
    // group of them into a bulk accept is the click the owner did not mean to make.
    const request = vi.fn().mockResolvedValue(
      batch({
        groups: [group("duplicate", [row({ id: "dup", status: "duplicate" })])],
      }),
    );
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    expect(screen.queryByRole("checkbox", { name: /select the .* duplicate/i })).toBeNull();
  });
});

describe("committing", () => {
  it("commits and reports what reached the ledger", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch({ groups: [group("accepted", [row({ status: "accepted" })])] }))
      .mockResolvedValueOnce({ batchId: "batch-1", rowsImported: 1, status: "completed" })
      .mockResolvedValueOnce(batch({ status: "completed", groups: [] }));
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    fireEvent.click(screen.getByRole("button", { name: /import 1 row/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    expect(request.mock.calls[1][0]).toBe("/import/batch-1/commit");
    expect(request.mock.calls[1][1].method).toBe("POST");
    expect(await screen.findByText(/1 row imported/i)).toBeTruthy();
  });

  it("offers no commit while nothing is accepted", async () => {
    const request = vi.fn().mockResolvedValue(batch());
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    expect(screen.queryByRole("button", { name: /import \d+ row/i })).toBeNull();
    expect(screen.getByText(/accept some rows/i)).toBeTruthy();
  });

  it("shows the database's own refusal when the commit is rejected", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch({ groups: [group("accepted", [row({ status: "accepted" })])] }))
      .mockRejectedValueOnce(
        new ApiError(409, "the account does not reconcile as of 2026-03-31"),
      );
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    fireEvent.click(screen.getByRole("button", { name: /import 1 row/i }));

    expect(
      await screen.findByText("the account does not reconcile as of 2026-03-31"),
    ).toBeTruthy();
  });

  it("stops offering decisions once the batch is committed", async () => {
    const request = vi
      .fn()
      .mockResolvedValue(batch({ status: "completed", groups: [group("accepted", [row()])] }));
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    expect(screen.queryByRole("button", { name: /^accept selected/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /import \d+ row/i })).toBeNull();
    expect(screen.getByText(/already imported/i)).toBeTruthy();
  });
});
