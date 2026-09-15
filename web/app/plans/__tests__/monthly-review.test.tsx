// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MonthlyReview } from "@/app/plans/monthly-review";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AccountRow, CurrencyRow, MonthlyReviewResponse } from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

const account: AccountRow = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Synthetic account",
  institutionId: null,
  institutionName: null,
  accountType: "savings",
  accountClass: "asset",
  accountSubtype: null,
  currency: "XAA",
  country: "XZ",
  openedOn: "2026-01-01",
  closedOn: null,
  isActive: true,
  includeInNetWorth: true,
  isLiquid: true,
  openingBalance: "10000.0000",
  openingBalanceDate: "2026-01-01",
  openingBalanceNote: null,
  openingBalanceConfirmedAt: null,
  currentBalance: "10000.0000",
  currentBalanceAsOf: "2026-01-01T00:00:00Z",
  creditLimit: null,
  identifiers: [],
};

function review(overrides: Partial<MonthlyReviewResponse> = {}): MonthlyReviewResponse {
  return {
    month: "2026-02-01",
    completedAt: null,
    steps: {
      coverage: { gapCells: 2, rowsWithGaps: 2 },
      transfers: { candidateCount: 3 },
      classification: { unclassifiedCount: 4 },
      position: {
        monthEnd: "2026-02-28",
        totalBase: "1500.0000",
        complete: true,
        previousMonthEnd: "2026-01-31",
        previousTotalBase: "1000.0000",
        previousComplete: true,
        changeBase: "500.0000",
        baseCurrency: "XAA",
      },
      commitments: { dueNext30Days: 1, overdue: 1 },
      actions: { open: 2, snoozed: 1 },
    },
    ...overrides,
  };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("monthly review", () => {
  it("shows the loading state, then step counts and links from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === "/monthly-review") return review();
      throw new ApiError(500, "unexpected");
    });
    render(<MonthlyReview accounts={[account]} currencies={currencies} request={request} />);

    expect(screen.getByText("Loading the monthly review…")).toBeTruthy();

    expect(await screen.findByText(/2 gap cells across 2 rows/)).toBeTruthy();
    const coverageLink = screen.getByRole("link", { name: "Coverage" });
    expect(coverageLink).toHaveProperty("pathname", "/coverage");

    expect(screen.getByText(/3 candidates/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Transfers" })).toHaveProperty("pathname", "/transactions");

    expect(screen.getByText(/4 unclassified/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Categories" })).toHaveProperty("pathname", "/review");

    expect(screen.getByText(/A 1,500.00 as of 2026-02-28/)).toBeTruthy();
    expect(screen.getByText(/previous A 1,000.00/)).toBeTruthy();
    expect(screen.getByText(/change A 500.00/)).toBeTruthy();

    expect(screen.getByText(/1 due in the next 30 days · 1 overdue/)).toBeTruthy();
    expect(screen.getByText(/2 open actions · 1 snoozed/)).toBeTruthy();
    const commitmentsLink = screen.getByRole("link", { name: "Commitments" });
    expect(commitmentsLink).toHaveProperty("pathname", "/plans");
    expect(commitmentsLink).toHaveProperty("hash", "#actions");

    expect(screen.getByLabelText("Month")).toHaveProperty("value", "2026-02");
    expect(screen.getByRole("button", { name: "Mark review complete" })).toBeTruthy();
  });

  it("shows the error state and lets the owner try again", async () => {
    const request = requestFrom(() => {
      throw new ApiError(500, "boom");
    });
    render(<MonthlyReview accounts={[account]} currencies={currencies} request={request} />);
    expect(await screen.findByText("The monthly review could not be loaded.")).toBeTruthy();
  });

  it("says the position is not yet refreshed, linking to Wealth", async () => {
    const request = requestFrom((path) => {
      if (path === "/monthly-review") {
        return review({
          steps: {
            ...review().steps,
            position: {
              monthEnd: "2026-02-28",
              totalBase: null,
              complete: false,
              previousMonthEnd: "2026-01-31",
              previousTotalBase: null,
              previousComplete: false,
              changeBase: null,
              baseCurrency: "XAA",
            },
          },
        });
      }
      throw new ApiError(500, "unexpected");
    });
    render(<MonthlyReview accounts={[account]} currencies={currencies} request={request} />);
    expect(await screen.findByText(/Not yet refreshed for this month/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Refresh" })).toHaveProperty("pathname", "/wealth");
  });

  it("marks the review complete and shows the completion state", async () => {
    let completed = false;
    const request = requestFrom((path, init) => {
      if (path === "/monthly-review") return review({ completedAt: completed ? "2026-03-01T09:00:00Z" : null });
      if (path === "/monthly-review/2026-02/completion" && init?.method === "PUT") {
        completed = true;
        return review({ completedAt: "2026-03-01T09:00:00Z" });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<MonthlyReview accounts={[account]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "Mark review complete" }));

    expect(await screen.findByText(/Review complete since 2026-03-01T09:00:00Z/)).toBeTruthy();
    expect(screen.getByText(/2 actions remaining/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reopen review" })).toBeTruthy();
  });

  it("reopens the review and refetches", async () => {
    let completed = true;
    const request = requestFrom((path, init) => {
      if (path === "/monthly-review") return review({ completedAt: completed ? "2026-03-01T09:00:00Z" : null });
      if (path === "/monthly-review/2026-02/completion" && init?.method === "DELETE") {
        completed = false;
        return undefined;
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<MonthlyReview accounts={[account]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "Reopen review" }));

    expect(await screen.findByRole("button", { name: "Mark review complete" })).toBeTruthy();
    expect(screen.queryByText(/Review complete since/)).toBeNull();
  });

  it("adds a follow-up inline through the existing follow-up create call, then refetches", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/monthly-review") return review();
      if (path === "/actions/follow-ups" && init?.method === "POST") {
        expect(JSON.parse(init.body as string)).toMatchObject({ title: "Synthetic follow-up" });
        return { id: "f1" };
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<MonthlyReview accounts={[account]} currencies={currencies} request={request} />);
    await screen.findByRole("button", { name: "Mark review complete" });

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Synthetic follow-up" } });
    fireEvent.click(screen.getByRole("button", { name: "Add follow-up" }));

    expect(await screen.findByText("Follow-up added.")).toBeTruthy();
    await waitFor(() =>
      expect((request as ReturnType<typeof vi.fn>).mock.calls.filter((c) => c[0] === "/monthly-review")).toHaveLength(2),
    );
  });
});
