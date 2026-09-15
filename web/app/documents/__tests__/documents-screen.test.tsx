// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DocumentsScreen } from "@/app/documents/documents-screen";
import { type apiFetch } from "@/lib/api/client";
import type { AccountRow, DocumentRow } from "@/types/api";

const account = {
  id: "acct-1",
  name: "Ledger Savings",
} as unknown as AccountRow;

function document(overrides: Partial<DocumentRow>): DocumentRow {
  return {
    id: "doc-1",
    documentType: "bank_statement",
    pageCount: 3,
    isPasswordProtected: false,
    parsedStatus: "parsed",
    uploadedOn: "2026-03-10T12:00:00Z",
    accountId: "acct-1",
    accountName: "Ledger Savings",
    accountNumberMasked: "****7890",
    period: { start: "2026-03-01", end: "2026-03-31", isDerived: true },
    importKind: "statement",
    importId: "batch-1",
    importStatus: "awaiting_review",
    reconciliationStatus: null,
    layoutId: "layout-1",
    layoutVersion: 1,
    newerLayoutId: null,
    title: null,
    ...overrides,
  };
}

const upload = document({
  id: "doc-5",
  documentType: "insurance_policy",
  accountId: null,
  accountName: null,
  accountNumberMasked: null,
  period: { start: null, end: null, isDerived: null },
  importKind: "upload",
  importId: null,
  importStatus: null,
  title: "Synthetic policy schedule",
});

const investment = document({
  id: "doc-2",
  documentType: "investment_statement",
  accountId: null,
  accountName: null,
  accountNumberMasked: null,
  period: { start: null, end: null, isDerived: null },
  importKind: "investment",
  importId: "inv-1",
  importStatus: "staged",
});

const payslip = document({
  id: "doc-3",
  documentType: "payslip",
  accountId: null,
  accountName: null,
  accountNumberMasked: null,
  period: { start: null, end: null, isDerived: null },
  importKind: "payslip",
  importId: null,
  importStatus: "completed",
});

const completedWithNewerLayout = document({
  id: "doc-4",
  importId: "batch-4",
  importStatus: "completed",
  reconciliationStatus: "matched",
  newerLayoutId: "layout-2",
});

function renderScreen(
  documents: Array<DocumentRow>,
  total: number,
  request: typeof apiFetch = vi.fn(),
) {
  return render(
    <DocumentsScreen accounts={[account]} documents={documents} request={request} total={total} />,
  );
}

afterEach(cleanup);

describe("the documents screen", () => {
  it("shows type, account, period, pages, upload date and import status", () => {
    renderScreen([document({})], 1);

    expect(screen.getByText("1 document")).toBeTruthy();
    const table = within(screen.getByRole("table"));
    expect(table.getByText("bank_statement")).toBeTruthy();
    expect(table.getByText("Ledger Savings · ****7890")).toBeTruthy();
    expect(table.getByText("2026-03-01 – 2026-03-31 (derived from the rows)")).toBeTruthy();
    expect(table.getByText("2026-03-10")).toBeTruthy();
    expect(table.getByText("awaiting_review")).toBeTruthy();
  });

  it("shows an em dash for a document with no account or period", () => {
    renderScreen([investment], 1);
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("round-trips every filter as a query parameter", async () => {
    const request = vi.fn().mockResolvedValue({ documents: [], total: 0 });
    renderScreen([document({})], 1, request as unknown as typeof apiFetch);

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "payslip" } });
    fireEvent.change(screen.getByLabelText("Account"), { target: { value: "acct-1" } });
    fireEvent.change(screen.getByLabelText("Period from"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("Period to"), { target: { value: "2026-03-31" } });
    fireEvent.change(screen.getByLabelText("Import status"), {
      target: { value: "awaiting_review" },
    });
    fireEvent.submit(screen.getByRole("form", { name: "Filter documents" }));

    await waitFor(() => expect(request).toHaveBeenCalled());
    const url = (request.mock.calls[0] as Array<string>)[0];
    expect(url).toBe(
      "/documents?documentType=payslip&accountId=acct-1&periodFrom=2026-03-01"
        + "&periodTo=2026-03-31&importStatus=awaiting_review&limit=50&offset=0",
    );
  });

  it("links Original for every row and Transactions only for a statement", () => {
    renderScreen([document({}), investment], 2);

    const originals = screen.getAllByRole("link", { name: "Original" });
    expect(originals).toHaveLength(2);
    expect(originals[0].getAttribute("href")).toBe("http://localhost:8000/documents/doc-1/original");

    expect(screen.getByRole("link", { name: "Transactions" }).getAttribute("href")).toBe(
      "/transactions?sourceDocumentId=doc-1",
    );
  });

  it("offers Review for a batch awaiting review and a staged investment import, not a payslip", () => {
    renderScreen([document({}), investment, payslip], 3);

    const reviews = screen.getAllByRole("link", { name: "Review" });
    expect(reviews.map((link) => link.getAttribute("href")).sort()).toEqual(
      ["/import/batch-1", "/investments/import/inv-1"].sort(),
    );
  });

  it("offers re-read descriptions only for a completed import with a newer layout", () => {
    renderScreen([document({}), completedWithNewerLayout], 2);

    const button = screen.getByRole("button", { name: "Re-read descriptions" });
    fireEvent.click(button);
    expect(screen.getByText("A newer layout is available for this import.")).toBeTruthy();
  });

  it("shows an upload by its title with no import link", () => {
    renderScreen([upload], 1);

    const table = within(screen.getByRole("table"));
    expect(table.getByText("Synthetic policy schedule")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Transactions" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Review" })).toBeNull();
  });

  it("offers more rows when the library is longer than the page", () => {
    renderScreen([document({})], 5);
    expect(screen.getByText("Load 4 more")).toBeTruthy();
  });
});
