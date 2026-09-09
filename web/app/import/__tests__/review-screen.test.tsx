// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewScreen } from "@/app/import/review-screen";
import { ApiError } from "@/lib/api/client";
import type {
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

const currencies: Array<CurrencyRow> = [catalogue];
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
    amountBase: "-10.0000",
    amountSecondary: "-40.0000",
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
    groups: [group("new", [row(), row({ id: "row-2", sourceRowNumber: 2, amount: "500.0000" })])],
    ...overrides,
  };
}

function renderScreen(request: ReturnType<typeof vi.fn>, batchId = "batch-1") {
  render(
    <ReviewScreen
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

    expect(within(first).getByText("-A 10.00")).toBeTruthy();
    expect(within(first).getByText("COFFEE SHOP")).toBeTruthy();
    expect(within(first).getByText("2026-03-01")).toBeTruthy();
    // The declared closing is the statement's own figure, formatted from its string.
    expect(screen.getByText(/A 1,490\.00/)).toBeTruthy();
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

    fireEvent.click(screen.getByRole("checkbox", { name: /select all 2 new/i }));

    const ticked = screen
      .getAllByRole<HTMLInputElement>("checkbox")
      .filter((box) => box.checked && box.getAttribute("aria-label")?.startsWith("Row"));
    expect(ticked).toHaveLength(2);
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

    expect(screen.queryByRole("checkbox", { name: /select all 1 duplicate/i })).toBeNull();
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
