// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CashForecast } from "@/app/plans/cash-forecast";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AccountRow, CurrencyRow, ForecastResponse } from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

const account: AccountRow = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Synthetic paying account",
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
  openingBalance: "1000.0000",
  openingBalanceDate: "2026-01-01",
  openingBalanceNote: null,
  openingBalanceConfirmedAt: null,
  currentBalance: "1000.0000",
  currentBalanceAsOf: "2026-01-01T00:00:00Z",
  creditLimit: null,
  identifiers: [],
};

const fundingAccount: AccountRow = {
  ...account,
  id: "00000000-0000-4000-8000-0000000000a2",
  name: "Synthetic funding account",
};

const excludedAccount: AccountRow = {
  ...account,
  id: "00000000-0000-4000-8000-0000000000a3",
  name: "Synthetic excluded account",
};

const staleAccount: AccountRow = {
  ...account,
  id: "00000000-0000-4000-8000-0000000000a4",
  name: "Synthetic stale account",
};

const cardAccount: AccountRow = {
  ...account,
  id: "00000000-0000-4000-8000-0000000000c9",
  name: "Synthetic card account",
  accountType: "credit_card",
  accountClass: "liability",
};

const commitmentId = "00000000-0000-4000-8000-0000000000cc";
const cardStatementId = "00000000-0000-4000-8000-0000000000d1";

function forecast(overrides: Partial<ForecastResponse> = {}): ForecastResponse {
  return {
    asOf: "2026-03-15",
    accounts: [
      {
        accountId: account.id,
        accountName: account.name,
        accountClass: "asset",
        currency: "XAA",
        startBalance: "1000.0000",
        balanceDate: "2026-01-01",
        points: [
          { days: 30, date: "2026-04-14", balance: "-500.0000", confirmedBalance: "-500.0000", chartBalanceCoordinate: -3333, chartConfirmedCoordinate: -3333 },
          { days: 60, date: "2026-05-14", balance: "1500.0000", confirmedBalance: "1500.0000", chartBalanceCoordinate: 10000, chartConfirmedCoordinate: 10000 },
          { days: 90, date: "2026-06-13", balance: "1500.0000", confirmedBalance: "1500.0000", chartBalanceCoordinate: 10000, chartConfirmedCoordinate: 10000 },
        ],
        occurrences: [
          {
            commitmentId,
            label: "Synthetic outflow",
            date: "2026-02-01",
            amount: "-1500.0000",
            dueByAsOf: true,
            certainty: "confirmed",
            kind: "commitment",
          },
          {
            commitmentId: "00000000-0000-4000-8000-0000000000cd",
            label: "Synthetic card payment",
            date: "2026-03-05",
            amount: "500.0000",
            dueByAsOf: true,
            certainty: "confirmed",
            kind: "card_settlement",
          },
        ],
        unappliedOccurrences: [
          {
            commitmentId,
            label: "Synthetic not-yet-posted rent",
            date: "2025-12-01",
            amount: "-100.0000",
            dueByAsOf: true,
            certainty: "confirmed",
            kind: "commitment",
          },
        ],
        lowest: { balance: "-500.0000", date: "2026-02-01" },
        lowestConfirmed: { balance: "-500.0000", date: "2026-02-01" },
        balanceAgeDays: 73,
        coverageGapMonths: 2,
        shortfall: {
          amount: "500.0000",
          date: "2026-02-01",
          fundingAccounts: [
            { accountId: fundingAccount.id, accountName: fundingAccount.name, lowestBalance: "2000.0000" },
          ],
        },
      },
    ],
    excludedAccounts: [
      { accountId: excludedAccount.id, accountName: excludedAccount.name, reason: "no_balance_date" },
    ],
    excludedCommitments: [
      {
        commitmentId: "00000000-0000-4000-8000-0000000000ce",
        label: "Synthetic mismatched card commitment",
        accountId: cardAccount.id,
        reason: "card_settlement_not_projected",
      },
    ],
    currencies: [
      {
        currency: "XAA",
        accountIds: [account.id, cardAccount.id],
        startBalance: "1500.0000",
        points: [
          { days: 30, date: "2026-04-14", balance: "1500.0000", confirmedBalance: "1500.0000", chartBalanceCoordinate: 10000, chartConfirmedCoordinate: 10000 },
          { days: 60, date: "2026-05-14", balance: "1500.0000", confirmedBalance: "1500.0000", chartBalanceCoordinate: 10000, chartConfirmedCoordinate: 10000 },
          { days: 90, date: "2026-06-13", balance: "1500.0000", confirmedBalance: "1500.0000", chartBalanceCoordinate: 10000, chartConfirmedCoordinate: 10000 },
        ],
        lowest: { balance: "0.0000", date: "2026-02-01" },
        lowestConfirmed: { balance: "0.0000", date: "2026-02-01" },
        excludedAccountIds: [excludedAccount.id],
        staleAccountIds: [staleAccount.id],
      },
    ],
    unplannedCardDues: [
      {
        cardStatementId,
        accountId: cardAccount.id,
        accountName: cardAccount.name,
        currency: "XAA",
        statementBalance: "300.0000",
        paymentDueDate: "2026-04-01",
      },
    ],
    ...overrides,
  };
}

const allAccounts = [account, fundingAccount, excludedAccount, staleAccount, cardAccount];

function requestReturning(response: ForecastResponse): Request {
  return vi.fn((path: string) => {
    if (path === "/forecast") return Promise.resolve(response);
    return Promise.reject(new ApiError(500, "unexpected"));
  }) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("cash forecast", () => {
  it("keeps currency and account chart contexts separate and labels both forecast series", async () => {
    const anotherCurrency = {
      ...forecast().currencies[0], currency: "XBB", accountIds: [],
      points: [{ days: 30, date: "2026-04-14", balance: "20.0000", confirmedBalance: "25.0000", chartBalanceCoordinate: 8000, chartConfirmedCoordinate: 10000 }],
    };
    const request = requestReturning(forecast({ currencies: [...forecast().currencies, anotherCurrency] }));
    render(<CashForecast accounts={allAccounts} currencies={[...currencies, { code: "XBB", name: "Other synthetic currency", symbol: "B", decimals: 2, numberFormat: "international" }]}
      onPlanCardPayment={vi.fn()} onSettle={vi.fn()} refreshKey={0} request={request} />);

    expect(await screen.findByRole("table", { name: /XAA · eligible accounts.*exact values/ })).toBeTruthy();
    expect(screen.getAllByText("Confirmed-input forecast").length).toBeGreaterThan(0);
    expect(screen.getAllByText("All planned (includes estimates)").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("Chart context"), { target: { value: "currency:XBB" } });
    const table = screen.getByRole("table", { name: /XBB · eligible accounts.*exact values/ });
    expect(table.textContent).toContain("B 25.00");
    expect(table.textContent).not.toContain("A 1,500.00");
    fireEvent.change(screen.getByLabelText("Chart context"), { target: { value: `account:${account.id}` } });
    expect(screen.getByRole("table", { name: /Synthetic paying account.*exact values/ })).toBeTruthy();
  });

  it("shows incomplete chart points as unknown instead of plotting zero", async () => {
    const data = forecast();
    data.currencies[0].points[1].chartBalanceCoordinate = null;
    render(<CashForecast accounts={allAccounts} currencies={currencies} onPlanCardPayment={vi.fn()}
      onSettle={vi.fn()} refreshKey={0} request={requestReturning(data)} />);
    const table = await screen.findByRole("table", { name: /XAA · eligible accounts.*exact values/ });
    expect(table.textContent).toContain("Not available");
  });

  it("renders the currency summary, per-account lowest/confirmed and badges from server strings", async () => {
    const request = requestReturning(forecast());
    render(
      <CashForecast
        accounts={allAccounts}
        currencies={currencies}
        onPlanCardPayment={vi.fn()}
        onSettle={vi.fn()}
        refreshKey={0}
        request={request}
      />,
    );

    expect(await screen.findByText("Forecast as of 2026-03-15")).toBeTruthy();
    expect(screen.getByText("2 accounts")).toBeTruthy();
    expect(screen.getByText(/Excluded: Synthetic excluded account/)).toBeTruthy();
    expect(screen.getByText(/Stale: Synthetic stale account/)).toBeTruthy();
    expect(screen.getByText(/Start A 1,000\.00 as of 2026-01-01/)).toBeTruthy();
    expect(screen.getByText(/Lowest -A 500\.00 on 2026-02-01/)).toBeTruthy();
    expect(screen.getByText(/Stale balance · 73 days old/)).toBeTruthy();
    expect(screen.getByText(/2 coverage gap months/)).toBeTruthy();
    expect(screen.getByText(/· card settlement/)).toBeTruthy();
  });

  it("shows a shortfall callout naming the funding account without moving anything", async () => {
    const request = requestReturning(forecast());
    render(
      <CashForecast
        accounts={allAccounts}
        currencies={currencies}
        onPlanCardPayment={vi.fn()}
        onSettle={vi.fn()}
        refreshKey={0}
        request={request}
      />,
    );

    expect(await screen.findByText(/Shortfall of A 500\.00 on 2026-02-01/)).toBeTruthy();
    expect(
      screen.getByText(/Could be funded from Synthetic funding account \(A 2,000\.00\)/),
    ).toBeTruthy();
  });

  it("shows excluded accounts and commitments with their server-worded reasons", async () => {
    const request = requestReturning(forecast());
    render(
      <CashForecast
        accounts={allAccounts}
        currencies={currencies}
        onPlanCardPayment={vi.fn()}
        onSettle={vi.fn()}
        refreshKey={0}
        request={request}
      />,
    );

    expect(await screen.findByText(/This account has no ledger balance date\./)).toBeTruthy();
    expect(screen.getByText(/Its card settlement is not projected onto the card\./)).toBeTruthy();
  });

  it("Settle on a Not projected occurrence opens the Phase 3.1 settlement flow", async () => {
    const onSettle = vi.fn();
    const request = requestReturning(forecast());
    render(
      <CashForecast
        accounts={allAccounts}
        currencies={currencies}
        onPlanCardPayment={vi.fn()}
        onSettle={onSettle}
        refreshKey={0}
        request={request}
      />,
    );

    expect(await screen.findByText("Not projected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Settle" }));
    expect(onSettle).toHaveBeenCalledWith(commitmentId);
  });

  it("Plan payment on an unplanned card due hands the row up for the commitment form pre-fill", async () => {
    const onPlanCardPayment = vi.fn();
    const request = requestReturning(forecast());
    render(
      <CashForecast
        accounts={allAccounts}
        currencies={currencies}
        onPlanCardPayment={onPlanCardPayment}
        onSettle={vi.fn()}
        refreshKey={0}
        request={request}
      />,
    );

    expect(await screen.findByText("Card statements with no planned payment")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Plan payment" }));
    expect(onPlanCardPayment).toHaveBeenCalledWith(
      expect.objectContaining({ cardStatementId, statementBalance: "300.0000", paymentDueDate: "2026-04-01" }),
    );
  });

  it("shows an error state with a retry that refetches", async () => {
    let calls = 0;
    const request = vi.fn((path: string) => {
      calls += 1;
      if (path === "/forecast") {
        return calls === 1 ? Promise.reject(new ApiError(500, "boom")) : Promise.resolve(forecast());
      }
      return Promise.reject(new ApiError(500, "unexpected"));
    }) as Request;
    render(
      <CashForecast
        accounts={allAccounts}
        currencies={currencies}
        onPlanCardPayment={vi.fn()}
        onSettle={vi.fn()}
        refreshKey={0}
        request={request}
      />,
    );

    expect(await screen.findByText("The forecast could not be loaded.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Forecast as of 2026-03-15")).toBeTruthy();
  });
});
