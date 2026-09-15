// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  certainty: "confirmed",
  nextDueDate: "2026-02-01",
  status: "upcoming",
  paidTowardNext: "0.0000",
  partlyPaid: false,
  remainingNext: "5000.0000",
  source: "owner",
  sourceCardStatementId: null,
  sourceCardAccountName: null,
};

const emptyForecast: ForecastResponse = {
  asOf: "2026-03-01",
  accounts: [],
  excludedAccounts: [],
  excludedCommitments: [],
  currencies: [],
  unplannedCardDues: [],
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
        { days: 30, date: "2026-03-31", balance: "15000.0000", confirmedBalance: "15000.0000" },
        { days: 60, date: "2026-04-30", balance: "20000.0000", confirmedBalance: "20000.0000" },
        { days: 90, date: "2026-05-30", balance: "25000.0000", confirmedBalance: "25000.0000" },
      ],
      occurrences: [
        {
          commitmentId: commitment.id,
          label: commitment.label,
          date: "2026-03-01",
          amount: "5000.0000",
          dueByAsOf: true,
          certainty: "confirmed",
          kind: "commitment",
        },
      ],
      unappliedOccurrences: [],
      lowest: { balance: "10000.0000", date: "2026-01-01" },
      lowestConfirmed: { balance: "10000.0000", date: "2026-01-01" },
      balanceAgeDays: 59,
      coverageGapMonths: null,
    },
  ],
  excludedAccounts: [],
  excludedCommitments: [],
  currencies: [],
  unplannedCardDues: [],
};

const emptyActions = { asOf: "2026-03-01", items: [], snoozed: [], resolvedFollowUps: [] };

// Needs attention (Task 2) fetches /actions on mount alongside the forecast and commitments;
// every test here is about the rest of the screen, so it gets a quiet empty response unless a
// test overrides /actions itself.
function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn((path: string, init?: RequestInit) => {
    if (path.startsWith("/actions") && (!init || init.method === undefined)) {
      return Promise.resolve(emptyActions);
    }
    return Promise.resolve(handler(path, init));
  }) as Request;
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

  it("plans a card payment from Needs attention: pre-fill, account filter, source, and refetch", async () => {
    const card: AccountRow = {
      ...account,
      id: "00000000-0000-4000-8000-0000000000cc",
      name: "Synthetic card",
      accountType: "credit_card",
      accountClass: "liability",
    };
    const otherCurrency: AccountRow = {
      ...account,
      id: "00000000-0000-4000-8000-0000000000a2",
      name: "Synthetic other-currency account",
      currency: "XBB",
    };
    const inactive: AccountRow = {
      ...account,
      id: "00000000-0000-4000-8000-0000000000a3",
      name: "Synthetic closed account",
      isActive: false,
    };
    const statementId = "00000000-0000-4000-8000-0000000000d1";
    const cardItem = {
      actionKey: `card.${statementId}`,
      kind: "card_due",
      reason: "card_payment_unplanned",
      title: "Synthetic card",
      dueDate: "2026-02-10",
      overdue: false,
      snoozedUntil: null,
      accountId: card.id,
      accountName: card.name,
      amount: "400.0000",
      remaining: "400.0000",
      partlyPaid: false,
      currency: "XAA",
      certainty: null,
      commitmentId: null,
      cardStatementId: statementId,
      actionItemId: null,
    };
    let actionsCalls = 0;
    let posted: Record<string, unknown> | undefined;
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/actions") {
        actionsCalls += 1;
        return { ...emptyActions, items: posted ? [] : [cardItem] };
      }
      if (path === "/forecast") return emptyForecast;
      if (path === "/commitments" && init?.method === "POST") {
        posted = JSON.parse(String(init.body)) as Record<string, unknown>;
        return commitment;
      }
      if (path === "/commitments") return { commitments: [] } satisfies CommitmentsResponse;
      throw new ApiError(500, "unexpected");
    }) as Request;
    render(
      <PlansScreen accounts={[card, otherCurrency, inactive, account]} currencies={currencies} request={request} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Plan payment" }));
    expect(screen.getByRole("heading", { name: "Plan the card payment" })).toBeTruthy();
    const picker = screen.getByLabelText("Account") as HTMLSelectElement;
    expect(Array.from(picker.options).map((option) => option.textContent)).toEqual([account.name]);
    expect((screen.getByLabelText("Label") as HTMLInputElement).value).toBe("Synthetic card");
    expect((screen.getByLabelText(/Amount/) as HTMLInputElement).value).toBe("-400.0000");
    expect((screen.getByLabelText("First date") as HTMLInputElement).value).toBe("2026-02-10");

    const before = actionsCalls;
    fireEvent.click(screen.getByRole("button", { name: "Add commitment" }));
    await waitFor(() => expect(posted).toBeDefined());
    expect(posted).toMatchObject({
      accountId: account.id,
      amount: "-400.0000",
      cadence: "once",
      firstDate: "2026-02-10",
      sourceCardStatementId: statementId,
    });
    await waitFor(() => expect(actionsCalls).toBeGreaterThan(before));
    expect(await screen.findByText("Nothing needs attention right now.")).toBeTruthy();
  });

  it("renders certainty, status, next due date and paid/remaining from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === "/forecast") return emptyForecast;
      if (path === "/commitments") return { commitments: [commitment] } satisfies CommitmentsResponse;
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText(/Confirmed/)).toBeTruthy();
    expect(screen.getByText(/Upcoming/)).toBeTruthy();
    expect(screen.getByText(/Next due 2026-02-01/)).toBeTruthy();
    expect(screen.getByText(/Paid A 0\.00/)).toBeTruthy();
    expect(screen.getByText(/Remaining A 5,000\.00/)).toBeTruthy();
  });

  it("shows Partly paid alongside the status when the server marks it so", async () => {
    const partlyPaid: CommitmentRow = { ...commitment, partlyPaid: true, paidTowardNext: "2000.0000" };
    const request = requestFrom((path) => {
      if (path === "/forecast") return emptyForecast;
      if (path === "/commitments") return { commitments: [partlyPaid] } satisfies CommitmentsResponse;
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText(/Upcoming · Partly paid/)).toBeTruthy();
  });

  it("opens the settlement panel for a commitment with an open occurrence", async () => {
    const request = requestFrom((path) => {
      if (path === "/forecast") return emptyForecast;
      if (path === "/commitments") return { commitments: [commitment] } satisfies CommitmentsResponse;
      if (path === `/commitments/${commitment.id}/payment-candidates`) {
        return { dueDate: commitment.nextDueDate, candidates: [] };
      }
      if (path === `/commitments/${commitment.id}/resolutions`) return { resolutions: [] };
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "Settle" }));
    expect(await screen.findByText("Settle Synthetic salary")).toBeTruthy();
  });

  it("the forecast's Settle and Plan payment open the Phase 3.1 settlement panel and card pre-fill", async () => {
    const statementId = "00000000-0000-4000-8000-0000000000d2";
    const withDues: ForecastResponse = {
      ...forecastWithAccount,
      accounts: [
        {
          ...forecastWithAccount.accounts[0],
          unappliedOccurrences: [{ ...forecastWithAccount.accounts[0].occurrences[0], date: "2025-12-01" }],
        },
      ],
      unplannedCardDues: [{
        cardStatementId: statementId,
        accountId: "00000000-0000-4000-8000-0000000000cc",
        accountName: "Synthetic card",
        currency: "XAA",
        statementBalance: "300.0000",
        paymentDueDate: "2026-04-01",
      }],
    };
    const request = requestFrom((path) => {
      if (path === "/forecast") return withDues;
      if (path === "/commitments") return { commitments: [commitment] } satisfies CommitmentsResponse;
      if (path === `/commitments/${commitment.id}/payment-candidates`) {
        return { dueDate: commitment.nextDueDate, candidates: [] };
      }
      if (path === `/commitments/${commitment.id}/resolutions`) return { resolutions: [] };
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    const notProjected = (await screen.findByRole("heading", { name: "Not projected" })).parentElement as HTMLElement;
    fireEvent.click(within(notProjected).getByRole("button", { name: "Settle" }));
    expect(await screen.findByText("Settle Synthetic salary")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Plan payment" }));
    expect(screen.getByRole("heading", { name: "Plan the card payment" })).toBeTruthy();
    expect((screen.getByLabelText("Label") as HTMLInputElement).value).toBe("Synthetic card");
    expect((screen.getByLabelText(/Amount/) as HTMLInputElement).value).toBe("-300.0000");
    expect((screen.getByLabelText("First date") as HTMLInputElement).value).toBe("2026-04-01");
  });

  it("shows a commitments load failure with a retry that refetches", async () => {
    let calls = 0;
    const request = requestFrom((path) => {
      if (path === "/forecast") return emptyForecast;
      if (path === "/commitments") {
        calls += 1;
        if (calls === 1) return Promise.reject(new ApiError(500, "boom"));
        return { commitments: [] } satisfies CommitmentsResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText("The commitments could not be loaded.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No commitments are recorded yet.")).toBeTruthy();
  });

  it("offers History for a finished commitment", async () => {
    const finished: CommitmentRow = {
      ...commitment,
      nextDueDate: null,
      status: "finished",
      remainingNext: null,
    };
    const request = requestFrom((path) => {
      if (path === "/forecast") return emptyForecast;
      if (path === "/commitments") return { commitments: [finished] } satisfies CommitmentsResponse;
      if (path === `/commitments/${commitment.id}/payment-candidates`) {
        return { dueDate: null, candidates: [] };
      }
      if (path === `/commitments/${commitment.id}/resolutions`) return { resolutions: [] };
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    expect(screen.queryByRole("button", { name: "Settle" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "History" }));
    expect(await screen.findByRole("heading", { name: "History" })).toBeTruthy();
  });

  it("does not resend an unchanged next due date when editing", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const request = requestFrom((path, init) => {
      if (path === "/forecast") return emptyForecast;
      if (path === "/commitments") return { commitments: [commitment] } satisfies CommitmentsResponse;
      if (path === `/commitments/${commitment.id}` && init?.method === "PUT") {
        bodies.push(JSON.parse(String(init.body)));
        return commitment;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<PlansScreen accounts={[account]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(await screen.findByRole("button", { name: /Save/ }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].nextDueDate).toBeNull();
  });
});
