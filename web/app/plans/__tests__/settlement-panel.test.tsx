// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SettlementPanel } from "@/app/plans/settlement-panel";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  CommitmentRow,
  CurrencyRow,
  PaymentCandidatesResponse,
  ResolutionsResponse,
} from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

const commitment: CommitmentRow = {
  id: "00000000-0000-4000-8000-0000000000c1",
  accountId: "00000000-0000-4000-8000-0000000000a1",
  accountName: "Synthetic salary account",
  label: "Synthetic salary",
  amount: "5000.0000",
  currency: "XAA",
  cadence: "monthly",
  firstDate: "2026-01-01",
  endDate: null,
  archivedAt: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  certainty: "confirmed",
  nextDueDate: "2026-02-01",
  status: "due",
  paidTowardNext: "0.0000",
  partlyPaid: false,
  remainingNext: "5000.0000",
};

const candidate = {
  transactionId: "00000000-0000-4000-8000-0000000000t1",
  bookedDate: "2026-02-01",
  amount: "5000.0000",
  currency: "XAA",
  description: "Synthetic deposit",
};

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn((path: string, init?: RequestInit) => Promise.resolve(handler(path, init))) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("settlement panel", () => {
  it("lists payment candidates and history from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === `/commitments/${commitment.id}/payment-candidates`) {
        return { dueDate: "2026-02-01", candidates: [candidate] } satisfies PaymentCandidatesResponse;
      }
      if (path === `/commitments/${commitment.id}/resolutions`) {
        return {
          resolutions: [
            {
              id: "r1",
              dueDate: "2026-01-01",
              kind: "dismissed",
              closes: true,
              reason: "not needed",
              createdAt: "2026-01-01T00:00:00Z",
              undoable: false,
              transaction: null,
            },
          ],
        } satisfies ResolutionsResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <SettlementPanel
        commitment={commitment}
        currencies={currencies}
        request={request}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );

    expect(await screen.findByText(/Synthetic deposit/)).toBeTruthy();
    expect(screen.getByText(/Dismissed: not needed/)).toBeTruthy();
  });

  it("sends the selected transaction and closes when recording a full payment", async () => {
    let resolutionBody: unknown;
    const request = requestFrom((path, init) => {
      if (path === `/commitments/${commitment.id}/payment-candidates`) {
        return { dueDate: "2026-02-01", candidates: [candidate] } satisfies PaymentCandidatesResponse;
      }
      if (path === `/commitments/${commitment.id}/resolutions` && init?.method === "POST") {
        resolutionBody = JSON.parse(init.body as string);
        return commitment;
      }
      if (path === `/commitments/${commitment.id}/resolutions`) {
        return { resolutions: [] } satisfies ResolutionsResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    const onChanged = vi.fn();
    const onClose = vi.fn();
    render(
      <SettlementPanel
        commitment={commitment}
        currencies={currencies}
        request={request}
        onClose={onClose}
        onChanged={onChanged}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Select" }));
    fireEvent.click(await screen.findByRole("button", { name: "Record full payment" }));

    await waitFor(() =>
      expect(resolutionBody).toEqual({
        dueDate: "2026-02-01",
        kind: "payment",
        transactionId: candidate.transactionId,
        closes: true,
      }),
    );
    expect(onChanged).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("sends closes: false for a part payment and stays open", async () => {
    let resolutionBody: unknown;
    const request = requestFrom((path, init) => {
      if (path === `/commitments/${commitment.id}/payment-candidates`) {
        return { dueDate: "2026-02-01", candidates: [candidate] } satisfies PaymentCandidatesResponse;
      }
      if (path === `/commitments/${commitment.id}/resolutions` && init?.method === "POST") {
        resolutionBody = JSON.parse(init.body as string);
        return commitment;
      }
      if (path === `/commitments/${commitment.id}/resolutions`) {
        return { resolutions: [] } satisfies ResolutionsResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    const onClose = vi.fn();
    render(
      <SettlementPanel
        commitment={commitment}
        currencies={currencies}
        request={request}
        onClose={onClose}
        onChanged={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Select" }));
    fireEvent.click(await screen.findByRole("button", { name: "Record part payment" }));

    await waitFor(() =>
      expect(resolutionBody).toMatchObject({ kind: "payment", closes: false }),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it("offers Undo only where the server marks a row undoable", async () => {
    const request = requestFrom((path) => {
      if (path === `/commitments/${commitment.id}/payment-candidates`) {
        return { dueDate: "2026-02-01", candidates: [] } satisfies PaymentCandidatesResponse;
      }
      if (path === `/commitments/${commitment.id}/resolutions`) {
        return {
          resolutions: [
            {
              id: "undoable",
              dueDate: "2026-01-01",
              kind: "completed",
              closes: true,
              reason: null,
              createdAt: "2026-01-01T00:00:00Z",
              undoable: true,
              transaction: null,
            },
            {
              id: "not-undoable",
              dueDate: "2025-12-01",
              kind: "completed",
              closes: true,
              reason: null,
              createdAt: "2025-12-01T00:00:00Z",
              undoable: false,
              transaction: null,
            },
          ],
        } satisfies ResolutionsResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <SettlementPanel
        commitment={commitment}
        currencies={currencies}
        request={request}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );

    await screen.findByText(/2026-01-01/);
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(1);
  });

  it("requires a reason before dismissing", async () => {
    const request = requestFrom((path) => {
      if (path === `/commitments/${commitment.id}/payment-candidates`) {
        return { dueDate: "2026-02-01", candidates: [] } satisfies PaymentCandidatesResponse;
      }
      if (path === `/commitments/${commitment.id}/resolutions`) {
        return { resolutions: [] } satisfies ResolutionsResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <SettlementPanel
        commitment={commitment}
        currencies={currencies}
        request={request}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    const confirm = screen.getByRole("button", { name: "Confirm dismissal" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "not needed" } });
    expect(confirm.hasAttribute("disabled")).toBe(false);
  });
});
