// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PlansScreen } from "@/app/plans/plans-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AccountRow, CommitmentRow, CommitmentsResponse, CurrencyRow, ForecastResponse } from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

const account: AccountRow = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Synthetic salary account",
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

const commitment: CommitmentRow = {
  id: "00000000-0000-4000-8000-0000000000c1",
  accountId: account.id,
  accountName: account.name,
  label: "Synthetic salary",
  amount: "5000.0000",
  currency: "XAA",
  cadence: "monthly",
  firstDate: "2026-01-01",
  endDate: null,
  archivedAt: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
};

const emptyForecast: ForecastResponse = {
  asOf: "2026-03-01",
  accounts: [],
  excludedAccounts: [],
  excludedCommitments: [],
};

const forecastWithAccount: ForecastResponse = {
  asOf: "2026-03-01",
  accounts: [
    {
      accountId: account.id,
      accountName: account.name,
      accountClass: "asset",
      currency: "XAA",
      startBalance: "10000.0000",
      balanceDate: "2026-01-01",
      points: [
        { days: 30, date: "2026-03-31", balance: "15000.0000" },
        { days: 60, date: "2026-04-30", balance: "20000.0000" },
        { days: 90, date: "2026-05-30", balance: "25000.0000" },
      ],
      occurrences: [
        { commitmentId: commitment.id, label: commitment.label, date: "2026-03-01", amount: "5000.0000", dueByAsOf: true },
      ],
    },
  ],
  excludedAccounts: [],
  excludedCommitments: [],
};

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn((path: string, init?: RequestInit) => Promise.resolve(handler(path, init))) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("plans screen", () => {
  it("renders the forecast, its assumptions and applied occurrences from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === "/forecast") return forecastWithAccount;
      if (path === "/commitments") return { commitments: [commitment] } satisfies CommitmentsResponse;
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText("Forecast as of 2026-03-01")).toBeTruthy();
    expect(screen.getByText(/Start A 10,000\.00 as of 2026-01-01/)).toBeTruthy();
    expect(screen.getByText(/30d \(2026-03-31\): A 15,000\.00/)).toBeTruthy();
    expect(screen.getByText(/not yet in the ledger/)).toBeTruthy();
    expect(screen.getByText(/Balances start from each account/)).toBeTruthy();
    expect(screen.getByText(/A forecast posts nothing\./)).toBeTruthy();
  });

  it("shows excluded accounts and commitments with their server-worded reasons", async () => {
    const withExclusions: ForecastResponse = {
      ...emptyForecast,
      excludedAccounts: [{ accountId: account.id, accountName: account.name, reason: "not_ledger_valued" }],
      excludedCommitments: [
        { commitmentId: commitment.id, label: commitment.label, accountId: account.id, reason: "account_closed" },
      ],
    };
    const request = requestFrom((path) => {
      if (path === "/forecast") return withExclusions;
      if (path === "/commitments") return { commitments: [] } satisfies CommitmentsResponse;
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText(/valued from holdings or a snapshot/)).toBeTruthy();
    expect(screen.getByText(/Its account is closed\./)).toBeTruthy();
  });

  it("refetches the forecast after adding a commitment", async () => {
    let commitmentsCall = 0;
    const request = requestFrom((path, init) => {
      if (path === "/forecast") return emptyForecast;
      if (path === "/commitments" && (!init || init.method === undefined)) {
        commitmentsCall += 1;
        return { commitments: commitmentsCall === 1 ? [] : [commitment] } satisfies CommitmentsResponse;
      }
      if (path === "/commitments" && init?.method === "POST") return commitment;
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);
    await screen.findByText("No commitments are recorded yet.");

    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Synthetic salary" } });
    fireEvent.change(screen.getByLabelText(/Amount/), { target: { value: "5000.0000" } });
    fireEvent.change(screen.getByLabelText("First date"), { target: { value: "2026-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Add commitment" }));

    await waitFor(() => expect(commitmentsCall).toBe(2));
    expect(await screen.findByText("Commitment added.")).toBeTruthy();
    expect(screen.getByText(/Synthetic salary account · Synthetic salary/)).toBeTruthy();
  });
});
