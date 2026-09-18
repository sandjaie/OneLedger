// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewScreen } from "@/app/import/review-screen";
import { ApiError } from "@/lib/api/client";
import type {
  AccountRow,
  CurrencyRow,
  HouseholdCurrency,
  HouseholdResponse,
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
// XAE is the household's base and XIN its secondary, so `amountBase` belongs under XAE and
// `amountSecondary` under XIN — which is the whole point of the toggle.
const household = {
  id: "hh-1",
  name: "Synthetic",
  baseCurrency: "XAE",
  secondaryCurrency: "XIN",
} as HouseholdResponse;

const secondary: HouseholdCurrency = {
  code: other.code,
  symbol: other.symbol,
  decimals: other.decimals,
  numberFormat: other.numberFormat,
};

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
    // The default row is in the household's base currency, so its own amount and `amountBase`
    // are necessarily the same figure — a fixture where they differ describes a row that cannot
    // exist. `amountSecondary` is the other side of the pair, and differs from both, which is
    // what lets a test say which column the screen actually read.
    amountBase: "-10.0000",
    amountSecondary: "-2500.0000",
    proposedTxnType: "expense",
    status: "new",
    duplicateOfTransactionId: null,
    nearDuplicateOfId: null,
    // The cascade's proposal travels with the row from staging (slice 1.3 task 3a4). The
    // screen does not render it yet — that is 3a5 — but the payload carries it, and a fixture
    // that omitted it would be testing a shape the API no longer sends.
    proposedCategoryId: null,
    proposedCategoryName: null,
    classifiedBy: "unclassified",
    classificationConfidence: "0.000",
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

function renderScreen(
  request: ReturnType<typeof vi.fn>,
  batchId = "batch-1",
  displayed: HouseholdCurrency = currency,
) {
  render(
    <ReviewScreen
      accounts={accounts}
      batchId={batchId}
      currencies={currencies}
      currency={displayed}
      household={household}
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
    // `multi-currency-and-fx.html` section 2: the toggle **swaps a column**, it does not
    // recompute. Formatting `amountBase` with whatever the toggle happens to be prints a
    // base-currency figure under the secondary's symbol and grouping — real money, wrong
    // currency. `transactions-screen.tsx` already reads it this way; this is the same rule one
    // screen over.
    const request = vi.fn().mockResolvedValue(batch());
    renderScreen(request, "batch-1", secondary);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    // The statement's own figure is unchanged; the conversion is the *secondary* column.
    expect(within(first).getByText("-A 10.00", { selector: ".review-amount" })).toBeTruthy();
    expect(within(first).getByText("-I 2,500.00", { selector: ".review-converted" })).toBeTruthy();
  });

  it("keeps a row's cells in their columns whatever the row is", async () => {
    // `.review-row` is a fixed six-column grid and CSS grid fills by position, so a row that
    // renders fewer cells does not leave a gap — every later cell shifts one column left, and
    // the description lands in the date's 96px column. Three of the cells are conditional (the
    // checkbox, the conversion, the duplicate marker), so the count must not decide the column.
    const shapes = [
      // committed, in the displayed currency, not a duplicate — the fewest cells
      { batch: batch({ status: "completed", groups: [group("accepted", [row()])] }), display: currency },
      // open, converted, and a duplicate — the most
      {
        batch: batch({
          groups: [group("duplicate", [row({ status: "duplicate", duplicateOfTransactionId: "t1" })])],
        }),
        display: secondary,
      },
    ];

    const counts: Array<number> = [];
    for (const shape of shapes) {
      const request = vi.fn().mockResolvedValue(shape.batch);
      renderScreen(request, "batch-1", shape.display);
      const first = await screen.findByRole("listitem", { name: /row 1/i });
      counts.push(first.childElementCount);
      cleanup();
    }

    expect(counts[0]).toBe(counts[1]);
  });

  it("shows no conversion for a row already in the displayed currency", async () => {
    // Converting a figure into its own currency says nothing — and printing `amountBase` beside
    // an equal `amount` under one symbol invites the reader to think they are two figures. The
    // ledger screen omits it for the same reason.
    const request = vi.fn().mockResolvedValue(batch());
    renderScreen(request);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    expect(within(first).getByText("-A 10.00", { selector: ".review-amount" })).toBeTruthy();
    // The cell holds its column and shows nothing — not absent, which would shift every cell
    // after it one column left.
    expect(first.querySelector(".review-converted")?.textContent).toBe("");
  });

  it("formats the converted figure in the household's currency, not the account's", async () => {
    // The row carries two figures in two different currencies: its own amount, in whatever the
    // statement was written in, and `amountBase`, frozen at staging in the household's base
    // currency. A card denominated in something other than the base — the ordinary case for
    // this screen — is where formatting both with the account's currency prints the base figure
    // under the wrong symbol and the wrong grouping.
    const request = vi.fn().mockResolvedValue(
      batch({
        accountId: "acct-savings",
        groups: [group("new", [row({ currency: "XIN", amountBase: "-5982530.0000" })])],
      }),
    );
    renderScreen(request);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    // The statement's own figure, in the account's XIN: indian grouping, the I symbol.
    expect(within(first).getByText("-I 10.00", { selector: ".review-amount" })).toBeTruthy();
    // The frozen conversion, in the household's XAE: international grouping, the A symbol.
    expect(
      within(first).getByText("-A 5,982,530.00", { selector: ".review-converted" }),
    ).toBeTruthy();
  });

  it("prints a row's amount raw with its code when the catalogue does not hold it", async () => {
    // Borrowing the account's or the household's symbol would put real money under the wrong
    // currency; the raw decimal and its own code are the honest fallback (ADR-0012).
    const request = vi.fn().mockResolvedValue(
      batch({
        accountId: "acct-savings",
        groups: [group("new", [row({ currency: "XZZ", amountBase: "-40.0000" })])],
      }),
    );
    renderScreen(request);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    expect(within(first).getByText("-10.0000 XZZ", { selector: ".review-amount" })).toBeTruthy();
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

    for (const box of screen.getAllByRole("checkbox")) {
      expect(box.getAttribute("aria-checked")).toBe("false");
      expect(box.getAttribute("data-slot")).toBe("checkbox");
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

    fireEvent.click(screen.getByRole("button", { name: "Select all" }));

    const ticked = screen
      .getAllByRole("checkbox")
      .filter((box) => box.getAttribute("aria-checked") === "true" && box.getAttribute("aria-label")?.startsWith("Row"));
    expect(ticked).toHaveLength(2);
    fireEvent.click(screen.getByRole("checkbox", { name: /select the 2 new rows shown/i }));
    expect(screen.getByRole("button", { name: "Select all" })).toBeTruthy();
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
  it("commits, refreshes recurring discovery and reports what reached the ledger", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch({ groups: [group("accepted", [row({ status: "accepted" })])] }))
      .mockResolvedValueOnce({ batchId: "batch-1", rowsImported: 1, status: "completed" })
      .mockResolvedValueOnce({ seriesCreated: 0, seriesTouched: 0, membersAdded: 0 })
      .mockResolvedValueOnce(batch({ status: "completed", groups: [] }));
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    fireEvent.click(screen.getByRole("button", { name: /import 1 row/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(4));
    expect(request.mock.calls[1][0]).toBe("/import/batch-1/commit");
    expect(request.mock.calls[1][1].method).toBe("POST");
    expect(request.mock.calls[2][0]).toBe("/recurring/refresh");
    expect(request.mock.calls[2][1].method).toBe("POST");
    expect(await screen.findByText(/1 row imported/i)).toBeTruthy();
  });

  it("does not surface a recurring-refresh failure as a commit failure", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch({ groups: [group("accepted", [row({ status: "accepted" })])] }))
      .mockResolvedValueOnce({ batchId: "batch-1", rowsImported: 1, status: "completed" })
      .mockRejectedValueOnce(new ApiError(503, "recurring discovery is unavailable"))
      .mockResolvedValueOnce(batch({ status: "completed", groups: [] }));
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    fireEvent.click(screen.getByRole("button", { name: /import 1 row/i }));

    expect(await screen.findByText(/1 row imported/i)).toBeTruthy();
    expect(screen.queryByText("recurring discovery is unavailable")).toBeNull();
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

describe("discarding a batch", () => {
  it("asks before it discards, and says what will go", async () => {
    // Destructive and unreversible, so it is two clicks rather than one. The confirmation names
    // the count and says the ledger is untouched, because "cancel" on its own reads as "close
    // this screen" at least as readily as "throw the batch away".
    //
    // `rowsExtracted` is deliberately not the staged count here: it is what the statement
    // yielded, and a confirmation that quoted it would promise to discard nine rows while
    // deleting two.
    const request = vi.fn().mockResolvedValue(batch({ rowsExtracted: 9 }));
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    fireEvent.click(screen.getByRole("button", { name: /discard this batch/i }));

    expect(screen.getByText(/2 staged rows/i)).toBeTruthy();
    expect(screen.getByText(/nothing.*ledger/i)).toBeTruthy();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("does not count to zero at a batch that staged nothing", async () => {
    // Reachable, not hypothetical: `reconcile.py` reconciles a dormant month against its stated
    // opening and closing, so a statement with no transactions stages and then holds its period
    // like any other. It still has to be discardable, and "Discard 0 rows" is not the way to ask.
    const request = vi.fn().mockResolvedValue(batch({ groups: [], rowsExtracted: 0 }));
    renderScreen(request);
    await screen.findByRole("button", { name: /discard this batch/i });

    fireEvent.click(screen.getByRole("button", { name: /discard this batch/i }));

    // Scoped to the confirmation: the reconciliation banner legitimately says "0 rows
    // extracted", which is the statement's own figure and not a count of what is about to go.
    const confirmation = screen.getByRole("group", { name: /confirm discarding/i });
    expect(within(confirmation).queryByText(/0 (staged )?rows?/i)).toBeNull();
    expect(within(confirmation).getByRole("button", { name: /^discard it$/i })).toBeTruthy();
    expect(within(confirmation).getByText(/nothing from it has reached your ledger/i)).toBeTruthy();
  });

  it("discards nothing when the confirmation is declined", async () => {
    const request = vi.fn().mockResolvedValue(batch());
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });
    fireEvent.click(screen.getByRole("button", { name: /discard this batch/i }));

    fireEvent.click(screen.getByRole("button", { name: /keep it/i }));

    expect(request).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/2 staged rows/i)).toBeNull();
    expect(screen.getByRole("button", { name: /discard this batch/i })).toBeTruthy();
  });

  it("posts the cancel and re-reads the batch", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch())
      .mockResolvedValueOnce({ batchId: "batch-1", status: "cancelled", rowsDiscarded: 2 })
      .mockResolvedValueOnce(batch({ status: "cancelled", groups: [] }));
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });
    fireEvent.click(screen.getByRole("button", { name: /discard this batch/i }));

    fireEvent.click(screen.getByRole("button", { name: /^discard 2 rows$/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    expect(request.mock.calls[1][0]).toBe("/import/batch-1/cancel");
    expect(request.mock.calls[1][1].method).toBe("POST");
    expect(request.mock.calls[2][0]).toBe("/import/batch-1");
  });

  it("says what happened to a discarded batch, not that it was imported", async () => {
    // The one message for every closed state said "already imported", which is the opposite of
    // what a discarded batch did. The owner needs to read that its account and period are free.
    const request = vi.fn().mockResolvedValue(batch({ status: "cancelled", groups: [] }));
    renderScreen(request);

    expect(await screen.findByText(/discarded/i)).toBeTruthy();
    expect(screen.queryByText(/already imported/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /discard this batch/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^accept selected/i })).toBeNull();
  });

  it("offers no discard once the batch is committed", async () => {
    const request = vi
      .fn()
      .mockResolvedValue(batch({ status: "completed", groups: [group("accepted", [row()])] }));
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });

    expect(screen.queryByRole("button", { name: /discard this batch/i })).toBeNull();
    expect(screen.getByText(/already imported/i)).toBeTruthy();
  });

  it("names a state it has no sentence for rather than calling it imported", async () => {
    const request = vi.fn().mockResolvedValue(batch({ status: "failed", groups: [] }));
    renderScreen(request);

    expect(await screen.findByText(/this batch is failed/i)).toBeTruthy();
    expect(screen.queryByText(/already imported/i)).toBeNull();
  });

  it("shows the service's own refusal when the discard is rejected", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(batch())
      .mockRejectedValueOnce(new ApiError(409, "this batch has already been imported"));
    renderScreen(request);
    await screen.findByRole("listitem", { name: /row 1/i });
    fireEvent.click(screen.getByRole("button", { name: /discard this batch/i }));

    fireEvent.click(screen.getByRole("button", { name: /^discard 2 rows$/i }));

    expect(await screen.findByText("this batch has already been imported")).toBeTruthy();
  });
});

describe("the cascade's proposal", () => {
  it("counts rows that Transactions will ask the owner to categorize", async () => {
    const request = vi.fn().mockResolvedValue(
      batch({
        groups: [
          group("new", [
            row(),
            row({
              id: "row-2",
              sourceRowNumber: 2,
              proposedCategoryId: "cat-groceries",
              proposedCategoryName: "Groceries",
              classifiedBy: "seed_rule",
              classificationConfidence: "0.800",
            }),
          ]),
        ],
      }),
    );
    renderScreen(request);

    expect(await screen.findByText(/1 row has no category/)).toBeTruthy();
    expect(screen.getByText(/Transactions.*Needs a category/)).toBeTruthy();
  });

  it("shows the proposed category and which step proposed it", async () => {
    const request = vi.fn().mockResolvedValue(
      batch({
        groups: [
          group("new", [
            row({
              proposedCategoryId: "cat-groceries",
              proposedCategoryName: "Groceries",
              classifiedBy: "seed_rule",
              classificationConfidence: "0.800",
            }),
          ]),
        ],
      }),
    );
    renderScreen(request);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    expect(within(first).getByText("Groceries")).toBeTruthy();
    expect(within(first).getByText("seed rule")).toBeTruthy();
    expect(within(first).getByTitle("confidence 0.800")).toBeTruthy();
  });

  it("says nothing about a row the cascade could not decide", async () => {
    const request = vi.fn().mockResolvedValue(batch());
    renderScreen(request);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    // No proposal, no provenance label, and no empty placeholder pretending to be one.
    expect(within(first).queryByText("unclassified", { selector: ".review-proposal" })).toBeNull();
    expect(first.querySelector(".review-proposal")).toBeNull();
  });

  it("offers no way to categorise from review, and that is deliberate", async () => {
    /**
     * The proposal is shown; a correction is made on the transactions screen, which already
     * does it. Bulk categorise-in-review with the keyboard interactions
     * `statement-ingestion.html` §10 describes is a later branch, and until it exists two
     * places to set a category would be two places for it to behave differently.
     */
    const request = vi.fn().mockResolvedValue(
      batch({
        groups: [
          group("new", [
            row({ proposedCategoryName: "Groceries", classifiedBy: "seed_rule",
                  classificationConfidence: "0.800" }),
          ]),
        ],
      }),
    );
    renderScreen(request);

    const first = await screen.findByRole("listitem", { name: /row 1/i });

    expect(within(first).queryByRole("combobox")).toBeNull();
    expect(within(first).queryByRole("button", { name: /categor/i })).toBeNull();
  });
});
