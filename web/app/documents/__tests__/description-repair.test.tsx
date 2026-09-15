// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DescriptionRepair } from "@/app/documents/description-repair";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { DocumentRow } from "@/types/api";

function document(overrides: Partial<DocumentRow>): DocumentRow {
  return {
    id: "doc-4",
    documentType: "bank_statement",
    pageCount: 2,
    isPasswordProtected: false,
    parsedStatus: "parsed",
    uploadedOn: "2026-03-10T12:00:00Z",
    accountId: "acct-1",
    accountName: "Ledger Savings",
    accountNumberMasked: "****7890",
    period: { start: "2026-03-01", end: "2026-03-31", isDerived: false },
    importKind: "statement",
    importId: "batch-4",
    importStatus: "completed",
    reconciliationStatus: "matched",
    layoutId: "layout-1",
    layoutVersion: 1,
    newerLayoutId: "layout-2",
    title: null,
    ...overrides,
  };
}

afterEach(cleanup);

describe("the description repair panel", () => {
  it("previews counts before offering Apply, against the newer layout and no password", async () => {
    const request = vi.fn().mockResolvedValue({
      batchId: "batch-4",
      applied: false,
      rowsCompared: 4,
      descriptionsChanged: 2,
      rowsQueuedForReview: 0,
    });
    render(
      <DescriptionRepair
        document={document({})}
        onApplied={vi.fn()}
        request={request as unknown as typeof apiFetch}
      />,
    );

    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Preview re-read" }));

    await waitFor(() =>
      expect(screen.getByText("4 rows compared · 2 descriptions would change.")).toBeTruthy(),
    );
    expect(request).toHaveBeenCalledWith("/import/batch-4/reparse-descriptions", {
      method: "POST",
      body: JSON.stringify({ layoutId: "layout-2", apply: false, password: null }),
      headers: { "Content-Type": "application/json" },
      timeoutMs: 120_000,
    });
    expect(screen.getByRole("button", { name: "Apply" })).toBeTruthy();
  });

  it("sends the typed password only for a protected document, and applies once", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce({
        batchId: "batch-4",
        applied: false,
        rowsCompared: 4,
        descriptionsChanged: 2,
        rowsQueuedForReview: 0,
      })
      .mockResolvedValueOnce({
        batchId: "batch-4",
        applied: true,
        rowsCompared: 4,
        descriptionsChanged: 2,
        rowsQueuedForReview: 1,
      });
    const onApplied = vi.fn();
    render(
      <DescriptionRepair
        document={document({ isPasswordProtected: true })}
        onApplied={onApplied}
        request={request as unknown as typeof apiFetch}
      />,
    );

    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview re-read" }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(request).toHaveBeenNthCalledWith(1, "/import/batch-4/reparse-descriptions", {
      method: "POST",
      body: JSON.stringify({ layoutId: "layout-2", apply: false, password: "secret" }),
      headers: { "Content-Type": "application/json" },
      timeoutMs: 120_000,
    });

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    expect(screen.getByText("4 rows compared · 2 descriptions would change · 1 queued for review.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  });

  it("shows the service sentence when the re-read is refused", async () => {
    const request = vi.fn().mockRejectedValue(new ApiError(422, "the reparsed statement does not match"));
    render(
      <DescriptionRepair
        document={document({})}
        onApplied={vi.fn()}
        request={request as unknown as typeof apiFetch}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Preview re-read" }));
    expect(await screen.findByText("the reparsed statement does not match")).toBeTruthy();
  });
});
