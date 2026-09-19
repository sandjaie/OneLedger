// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OverviewScreen } from "@/app/overview/overview-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  CardDue,
  CurrencyRow,
  DashboardResponse,
  HouseholdResponse,
  InvestmentCoverage,
  MonthFlows,
  PositionComposition,
  TransactionsResponse,
  WealthHistoryResponse,
} from "@/types/api";

const catalogue: Array<CurrencyRow> = [
  { code: "XAE", name: "Synthetic A", symbol: "AED", decimals: 2, numberFormat: "international" },
  { code: "XIN", name: "Synthetic I", symbol: "₹", decimals: 2, numberFormat: "indian" },
  { code: "XZZ", name: "Synthetic Z", symbol: "Z", decimals: 3, numberFormat: "international" },
];

const household: HouseholdResponse = {
  id: "synthetic-owner",
  name: "Household",
  ownerDisplayName: "Owner",
  ownerTaxResidency: null,
  baseCurrency: "XIN",
  secondaryCurrency: "XAE",
  reportingPairLocked: false,
  currencies: [
    { code: "XIN", symbol: "₹", decimals: 2, numberFormat: "indian" },
    { code: "XAE", symbol: "AED", decimals: 2, numberFormat: "international" },
  ],
};

const MONTHS = [
  "2025-04-01", "2025-05-01", "2025-06-01", "2025-07-01", "2025-08-01", "2025-09-01",
  "2025-10-01", "2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01", "2026-03-01",
];

function month(on: string, figures: Partial<MonthFlows> = {}): MonthFlows {
  return {
    month: on,
    incomeBase: "0.0000",
    incomeSecondary: "0.0000",
    spendingBase: "0.0000",
    spendingSecondary: "0.0000",
    netBase: "0.0000",
    netSecondary: "0.0000",
    savingsRatePctBase: null,
    savingsRatePctSecondary: null,
    ...figures,
  };
}

const march = month("2026-03-01", {
  incomeBase: "500000.0000",
  incomeSecondary: "22000.0000",
  spendingBase: "123456.7800",
  spendingSecondary: "5432.1000",
  netBase: "376543.2200",
  netSecondary: "16567.9000",
  savingsRatePctBase: "75.3086",
  savingsRatePctSecondary: "75.3086",
});

function payload(overrides: Partial<DashboardResponse> = {}): DashboardResponse {
  return {
    netWorth: {
      asOf: "2026-03-15",
      baseCurrency: "XIN",
      secondaryCurrency: "XAE",
      totalBase: "-3904500.0000",
      totalSecondary: "-176000.0000",
      assetsBase: "100000.0000",
      assetsSecondary: "4500.0000",
      liabilitiesBase: "-4004500.0000",
      liabilitiesSecondary: "-180500.0000",
      liquidBase: "95500.0000",
      liquidSecondary: "4300.0000",
      rates: [
        { currency: "XIN", rateToBase: "1", rateToSecondary: "0.0450000000", rateDate: "2026-03-15", carried: false },
        { currency: "XAE", rateToBase: "22.2222222222", rateToSecondary: "1", rateDate: "2026-03-13", carried: true },
      ],
      missing: [],
      investmentCoverage: [],
    },
    thisMonth: march,
    trend: [...MONTHS.slice(0, 11).map((on) => month(on)), march],
    hasRecordedFlows: true,
    flowEvidence: MONTHS.map((on) => ({
      month: on, status: "unknown",
      incomeBaseCoordinate: null, spendingBaseCoordinate: null, netBaseCoordinate: null,
      incomeSecondaryCoordinate: null, spendingSecondaryCoordinate: null, netSecondaryCoordinate: null,
    })),
    cardsDue: [],
    yearToDate: {
      yearStart: "2026-01-01",
      asOf: "2026-03-15",
      incomeBase: "1500000.0000",
      incomeSecondary: "66000.0000",
      spendingBase: "370000.0000",
      spendingSecondary: "16296.3000",
      netBase: "1130000.0000",
      netSecondary: "49703.7000",
      savingsRatePctBase: "75.3333",
      savingsRatePctSecondary: "75.3136",
      investedBase: "200000.0000",
      investedSecondary: "8800.0000",
    },
    upcomingCommitments: {
      asOf: "2026-03-15",
      through: "2026-04-14",
      count: 0,
      totalBase: "0.0000",
      totalSecondary: "0.0000",
      missingBaseCurrencies: [],
      missingSecondaryCurrencies: [],
      next: null,
      items: [],
    },
    positionComposition: composition(),
    ...overrides,
  };
}

function composition(overrides: Partial<PositionComposition> = {}): PositionComposition {
  return {
    rows: [
      {
        key: "cash",
        label: "Cash and equivalents",
        valueBase: "100000.0000",
        valueSecondary: "4500.0000",
        sharePercentBase: "2.4364",
        sharePercentSecondary: "2.4364",
        chartCoordinateBase: 244,
        chartCoordinateSecondary: 244,
      },
      {
        key: "liabilities",
        label: "Liabilities",
        valueBase: "-4004500.0000",
        valueSecondary: "-180500.0000",
        sharePercentBase: "97.5636",
        sharePercentSecondary: "97.5636",
        chartCoordinateBase: 9756,
        chartCoordinateSecondary: 9756,
      },
    ],
    missing: [],
    ...overrides,
  };
}

const HISTORY: WealthHistoryResponse = {
  baseCurrency: "XIN",
  secondaryCurrency: "XAE",
  months: [
    {
      monthEnd: "2026-01-31", totalBase: "90000.0000", totalSecondary: "4050.0000", complete: true,
      statusCounts: {}, computedAt: "2026-03-01T00:00:00Z", chartRatio: "0.9000", chartCoordinate: 9000,
    },
    {
      monthEnd: "2026-02-28", totalBase: null, totalSecondary: null, complete: false,
      statusCounts: {}, computedAt: "2026-03-01T00:00:00Z", chartRatio: null, chartCoordinate: null,
    },
    {
      monthEnd: "2026-03-31", totalBase: "100000.0000", totalSecondary: "4500.0000", complete: true,
      statusCounts: {}, computedAt: "2026-03-01T00:00:00Z", chartRatio: "1.0000", chartCoordinate: 10000,
    },
  ],
};

function renderWith(
  body: DashboardResponse | Error,
  currency = household.currencies[0],
  actions: unknown = {
    asOf: "2026-03-01",
    items: [],
    snoozed: [],
    resolvedFollowUps: [],
    previousMonthReview: { month: "2026-02-01", completedAt: null },
  },
  recent: TransactionsResponse = { transactions: [], total: 0 },
  history: WealthHistoryResponse | Error = HISTORY,
) {
  // Needs attention (Task 2) fetches /actions alongside /dashboard; every test here is about
  // the rest of the screen, so it gets a quiet empty response unless a test overrides it.
  const request = vi.fn(async (path: string) => {
    if (path === "/actions") return actions;
    if (path === "/transactions?limit=5&offset=0") return recent;
    if (path === "/wealth-history") {
      if (history instanceof Error) throw history;
      return history;
    }
    if (body instanceof Error) throw body;
    return body;
  });
  render(
    <OverviewScreen
      currencies={catalogue}
      currency={currency}
      household={household}
      request={request as unknown as typeof apiFetch}
    />,
  );
  return request;
}

describe("overview screen", () => {
  afterEach(() => cleanup());

  it("states the position, liquid balance and next-30-day commitments from exact server strings", async () => {
    const next = {
      commitmentId: "commitment-1",
      label: "Synthetic annual service",
      dueDate: "2026-03-22",
      nativeAmount: "-1234.5600",
      nativeCurrency: "XIN",
      remainingBase: "-1234.5600",
      remainingSecondary: "-54.4400",
    };
    renderWith(payload({
      upcomingCommitments: {
        asOf: "2026-03-15",
        through: "2026-04-14",
        count: 1,
        totalBase: "-1234.5600",
        totalSecondary: "-54.4400",
        missingBaseCurrencies: [],
        missingSecondaryCurrencies: [],
        next,
        items: [next],
      },
    }));

    expect(await screen.findByRole("heading", { name: "Overview", level: 1 })).toBeTruthy();
    const summary = screen.getByRole("region", { name: "Position summary" });
    expect(within(summary).getByText("Net position")).toBeTruthy();
    expect(within(summary).getByText("-₹39,04,500.00")).toBeTruthy();
    expect(within(summary).getByText("₹95,500.00")).toBeTruthy();
    expect(within(summary).getByText("Known next 30 days")).toBeTruthy();
    expect(within(summary).getByText("You owe ₹1,234.56")).toBeTruthy();
    expect(within(summary).getByText("1 commitment due through 2026-04-14")).toBeTruthy();
  });

  it("renders the approved six modules and keeps no promotional panel", async () => {
    renderWith(payload());
    await screen.findByRole("heading", { name: "Overview", level: 1 });

    for (const name of [
      "Position composition",
      "Net position history",
      "Cash flow",
      "Needs attention",
      "Upcoming commitments — next 30 days",
      "Recent activity",
    ]) {
      expect(screen.getByRole("region", { name })).toBeTruthy();
    }
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.queryByText(/Your money/)).toBeNull();
  });

  it("renders the position composition from the server's own shares and keeps a liability signed", async () => {
    renderWith(payload());

    const panel = await screen.findByRole("region", { name: "Position composition" });
    const [cash, liabilities] = within(panel).getAllByRole("listitem");
    expect(within(cash).getByText("Cash and equivalents")).toBeTruthy();
    expect(within(cash).getByText("2.44% of the position")).toBeTruthy();
    expect(within(cash).getByText("₹1,00,000.00")).toBeTruthy();
    expect(within(liabilities).getByText("Liabilities")).toBeTruthy();
    // The share is absolute so the loan is visible; the amount is still what is owed.
    expect(within(liabilities).getByText("97.56% of the position")).toBeTruthy();
    expect(within(liabilities).getByText("You owe ₹40,04,500.00")).toBeTruthy();
    // The bar is drawn from the server's coordinates; nothing is divided in the browser.
    const bar = panel.querySelector(".position-bar");
    expect([...(bar?.children ?? [])].map((part) => part.getAttribute("style"))).toEqual([
      "--share: 244;", "--share: 9756;",
    ]);
  });

  it("withholds every share when one reporting currency has no value, keeping the other intact", async () => {
    const withheld = composition({
      rows: composition().rows.map((row) => ({
        ...row,
        valueSecondary: null,
        sharePercentSecondary: null,
        chartCoordinateSecondary: null,
      })),
      missing: [{ accountId: "account-1", currency: "XAE" }],
    });
    renderWith(payload({ positionComposition: withheld }), household.currencies[1]);

    const panel = await screen.findByRole("region", { name: "Position composition" });
    expect(within(panel).getAllByText("Not available")).toHaveLength(2);
    expect(within(panel).getAllByText("Share unavailable")).toHaveLength(2);
    expect(within(panel).getByText(/cannot be stated in XAE/)).toBeTruthy();
    expect(panel.querySelector(".position-bar")).toBeNull();
  });

  it("plots the stored month ends with a gap for an incomplete month", async () => {
    renderWith(payload());

    const panel = await screen.findByRole("region", { name: "Net position history" });
    expect(within(panel).getByText("1 of 3 months are incomplete and remain gaps, never zero.")).toBeTruthy();
    fireEvent.click(within(panel).getByText("Exact values"));
    const table = within(panel).getByRole("table", { name: "Month-end net position · XIN exact values" });
    expect(within(table).getByRole("row", { name: "2026-01 ₹90,000.00" })).toBeTruthy();
    expect(within(table).getByRole("row", { name: "2026-02 Not available" })).toBeTruthy();
  });

  it("states missing stored history without inventing a flat line", async () => {
    renderWith(payload(), undefined, undefined, undefined, { ...HISTORY, months: [] });

    const panel = await screen.findByRole("region", { name: "Net position history" });
    expect(within(panel).getByText(/No month end has been stored yet/)).toBeTruthy();
    expect(within(panel).queryByRole("table")).toBeNull();
  });

  it("plots evidenced monthly flows with explicit gaps and exact values", async () => {
    const ordinary = payload();
    const january = month("2026-01-01", {
      incomeBase: "100.0000", incomeSecondary: "4.0000",
      spendingBase: "25.0000", spendingSecondary: "1.0000",
      netBase: "75.0000", netSecondary: "3.0000",
    });
    ordinary.trend[9] = january;
    ordinary.flowEvidence[9] = {
      month: january.month, status: "complete",
      incomeBaseCoordinate: 10000, spendingBaseCoordinate: 2500, netBaseCoordinate: 7500,
      incomeSecondaryCoordinate: 10000, spendingSecondaryCoordinate: 2500, netSecondaryCoordinate: 7500,
    };
    renderWith(ordinary);

    const panel = await screen.findByRole("region", { name: "Cash flow" });
    expect(within(panel).getByText(/11 months have incomplete statement coverage/)).toBeTruthy();
    fireEvent.click(within(panel).getByText("Exact values"));
    const exact = within(panel).getByRole("table", { name: "Income, spending and net trend exact values" });
    const rows = within(exact).getAllByRole("row");
    expect(within(rows[10]).getByText("₹100.00")).toBeTruthy();
    expect(within(rows[11]).getAllByText("Not available")).toHaveLength(3);
  });

  it("shows this month's net and both flow sides from the payload", async () => {
    renderWith(payload());
    const panel = await screen.findByRole("region", { name: "Cash flow" });
    expect(within(panel).getByText("₹3,76,543.22")).toBeTruthy();
    expect(within(panel).getByText("Net this month · ₹5,00,000.00 in, ₹1,23,456.78 out")).toBeTruthy();
    expect(within(panel).getByText("Savings rate 75.31%")).toBeTruthy();
  });

  it("shows every figure in the reporting currency the owner chose", async () => {
    renderWith(payload(), household.currencies[1]);
    const panel = await screen.findByRole("region", { name: "Cash flow" });
    expect(within(panel).getByText("Net this month · AED 22,000.00 in, AED 5,432.10 out")).toBeTruthy();
    expect(within(panel).queryByText(/₹/)).toBeNull();
    const composed = screen.getByRole("region", { name: "Position composition" });
    expect(within(composed).getByText("AED 4,500.00")).toBeTruthy();
    expect(within(composed).queryByText(/₹/)).toBeNull();
  });

  it("names every reason a figure may be incomplete, including a carried and a missing rate", async () => {
    const missingPrice: InvestmentCoverage = {
      accountId: "acct-1",
      holdingId: "holding-1",
      valuationMode: "holdings",
      status: "missing_price",
      valueDate: null,
      carried: false,
      ageDays: null,
      method: null,
      confidence: null,
    };
    const current: InvestmentCoverage = {
      accountId: "acct-3",
      holdingId: "holding-3",
      valuationMode: "holdings",
      status: "current_price",
      valueDate: "2026-03-15",
      carried: false,
      ageDays: 0,
      method: null,
      confidence: null,
    };
    const base = payload().netWorth;
    renderWith(payload({
      netWorth: {
        ...base,
        totalSecondary: null,
        assetsSecondary: null,
        liabilitiesSecondary: null,
        liquidSecondary: null,
        missing: [{ currency: "XZZ", reportingCurrency: "XAE", asOf: "2026-03-15" }],
        investmentCoverage: [missingPrice, current],
      },
    }));

    const region = await screen.findByRole("region", { name: "Why some figures may be incomplete" });
    expect(within(region).getByText(
      "No XZZ rate is stored on or before 2026-03-15, so net worth in XAE cannot be stated.",
    )).toBeTruthy();
    expect(within(region).getByText("XAE: as of the rate for 2026-03-13, carried forward")).toBeTruthy();
    expect(within(region).getByText(/No accepted price is recorded for this holding\./)).toBeTruthy();
    expect(within(region).getByRole("link", { name: "View holding" })).toHaveProperty(
      "pathname",
      "/investments/holding-1",
    );
    // The fully current holding names no reason and gets no row.
    expect(within(region).getAllByRole("listitem")).toHaveLength(3);
  });

  it("keeps both reporting currencies exact in the detail table and never states a withheld one", async () => {
    const base = payload().netWorth;
    renderWith(payload({
      netWorth: {
        ...base,
        totalSecondary: null,
        assetsSecondary: null,
        liabilitiesSecondary: null,
        liquidSecondary: null,
        missing: [{ currency: "XZZ", reportingCurrency: "XAE", asOf: "2026-03-15" }],
      },
    }));

    await screen.findByRole("heading", { name: "Overview", level: 1 });
    fireEvent.click(screen.getByText("Detailed totals and evidence"));
    const detail = screen.getByRole("region", { name: "Position detail" });
    const [, first, second] = within(detail).getAllByRole("row");
    expect(within(first).getByText("-₹39,04,500.00")).toBeTruthy();
    expect(within(first).getByText("You owe ₹40,04,500.00")).toBeTruthy();
    expect(within(second).getAllByText("Not available")).toHaveLength(4);
    expect(screen.queryByText("-AED 176,000.00")).toBeNull();
  });

  it("keeps year-to-date and the recorded monthly totals as progressive detail", async () => {
    renderWith(payload());
    await screen.findByRole("heading", { name: "Overview", level: 1 });
    fireEvent.click(screen.getByText("Detailed totals and evidence"));

    const year = screen.getByRole("region", { name: "This year so far" });
    for (const shown of ["₹15,00,000.00", "₹3,70,000.00", "₹11,30,000.00", "75.33%", "₹2,00,000.00"]) {
      expect(within(year).getByText(shown)).toBeTruthy();
    }
    expect(within(year).getByRole("link", { name: "₹2,00,000.00" })).toHaveProperty("pathname", "/investments");

    const rows = within(screen.getByRole("table", { name: "The last twelve months" })).getAllByRole("row");
    expect(rows).toHaveLength(13);
    expect(within(rows[1]).getByText("Apr 2025")).toBeTruthy();
    expect(within(rows[12]).getByText("₹3,76,543.22")).toBeTruthy();
  });

  it("renders each card's dues in that card's own currency, with its due date and days remaining", async () => {
    const card: CardDue = {
      accountId: "card-1",
      accountName: "Synthetic Card",
      currency: "XZZ",
      periodEnd: "2026-02-28",
      statementBalance: "4500.0000",
      minimumDue: "40.0000",
      paymentDueDate: "2026-03-20",
      daysRemaining: 18,
      isPaidInFull: false,
      utilisationPct: "22.5000",
    };
    const late: CardDue = {
      ...card,
      accountId: "card-2",
      accountName: "Late Card",
      currency: "XIN",
      statementBalance: "1200.0000",
      minimumDue: null,
      paymentDueDate: "2026-03-10",
      daysRemaining: -5,
      isPaidInFull: null,
      utilisationPct: null,
    };
    renderWith(payload({ cardsDue: [card, late] }));
    const [, first, second] = within(await screen.findByRole("table", { name: "Cards due" })).getAllByRole("row");

    expect(within(first).getByText("Synthetic Card")).toBeTruthy();
    expect(within(first).getByText("Z 4,500.000")).toBeTruthy();
    expect(within(first).getByText("2026-03-20 · in 18 days")).toBeTruthy();
    expect(within(first).getByText("22.50%")).toBeTruthy();
    expect(within(second).getByText("₹1,200.00")).toBeTruthy();
    expect(within(second).getByText("2026-03-10 · 5 days overdue")).toBeTruthy();
    expect(within(second).getByText("No limit stated")).toBeTruthy();
  });

  it("does not present a confident commitment total when either reporting conversion is incomplete", async () => {
    const missing = {
      commitmentId: "commitment-2",
      label: "Synthetic mixed-currency service",
      dueDate: "2026-03-24",
      nativeAmount: "-99.9900",
      nativeCurrency: "XZZ",
      remainingBase: null,
      remainingSecondary: null,
    };
    renderWith(payload({
      upcomingCommitments: {
        asOf: "2026-03-15",
        through: "2026-04-14",
        count: 1,
        totalBase: null,
        totalSecondary: null,
        missingBaseCurrencies: ["XZZ"],
        missingSecondaryCurrencies: ["XZZ"],
        next: missing,
        items: [missing],
      },
    }));

    const region = await screen.findByRole("region", { name: "Upcoming commitments — next 30 days" });
    expect(within(region).getByText(/cannot be stated in XIN/i)).toBeTruthy();
    expect(within(region).getByText(/Missing rates: XZZ/)).toBeTruthy();
    expect(within(region).getByText("Total unavailable in XIN")).toBeTruthy();
    expect(within(region).queryByText("₹0.00")).toBeNull();
    const summary = screen.getByRole("region", { name: "Position summary" });
    expect(within(summary).getByText("Not available")).toBeTruthy();
  });

  it("states the evidenced absence of open commitments without inventing a total", async () => {
    renderWith(payload());

    const region = await screen.findByRole("region", { name: "Upcoming commitments — next 30 days" });
    expect(within(region).getByText("No open commitments fall in this period.")).toBeTruthy();
    expect(within(region).queryByText("You owe ₹0.00")).toBeNull();
  });

  it("renders recent activity from server strings without recomputing its amount", async () => {
    renderWith(payload(), undefined, undefined, {
      total: 1,
      transactions: [{
        id: "transaction-1",
        accountId: "account-1",
        accountName: "Synthetic current account",
        bookedDate: "2026-03-14",
        descriptionRaw: "Synthetic merchant reference",
        descriptionClean: "Synthetic merchant",
        amount: "-987.6500",
        currency: "XIN",
        originalAmount: null,
        originalCurrency: null,
        amountBase: "-987.6500",
        amountSecondary: "-43.4566",
        fxRateDate: "2026-03-14",
        fxRateIsCarried: false,
        fxRateToBase: "1.0000000000",
        fxRateToSecondary: "0.0440000000",
        txnType: "debit",
        categoryId: null,
        categoryName: null,
        merchantId: null,
        transferId: null,
        parentTransactionId: null,
        isSplitParent: false,
        isExcluded: false,
        classifiedBy: "system",
        source: "statement",
        notes: null,
        sourceDocumentId: null,
        sourcePageNumber: null,
        sourceRowNumber: null,
      }],
    });

    const region = await screen.findByRole("region", { name: "Recent activity" });
    expect(within(region).getByText("Synthetic merchant")).toBeTruthy();
    expect(within(region).getByText("-₹987.65")).toBeTruthy();
    expect(within(region).queryByText(/You owe/)).toBeNull();
    expect(within(region).getByText(/Synthetic current account · 2026-03-14/)).toBeTruthy();
    expect(within(region).getByRole("link", { name: "View all transactions" })).toHaveProperty("pathname", "/transactions");
  });

  it("shows an empty state for an empty ledger rather than zeros presented as facts", async () => {
    const zero = "0.0000";
    renderWith(
      payload({
        netWorth: {
          ...payload().netWorth,
          totalBase: zero,
          totalSecondary: zero,
          assetsBase: zero,
          assetsSecondary: zero,
          liabilitiesBase: zero,
          liabilitiesSecondary: zero,
          liquidBase: zero,
          liquidSecondary: zero,
          rates: [],
        },
        thisMonth: month("2026-03-01"),
        trend: MONTHS.map((on) => month(on)),
        hasRecordedFlows: false,
        positionComposition: { rows: [], missing: [] },
      }),
    );

    expect(await screen.findByText(/Nothing to report yet/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Add an account" })).toHaveProperty("pathname", "/accounts");
    // The page action and the empty state both offer the same real next step.
    for (const link of screen.getAllByRole("link", { name: "Import a statement" })) {
      expect(link).toHaveProperty("pathname", "/import");
    }
    expect(await screen.findByRole("region", { name: "Needs attention" })).toBeTruthy();
    expect(screen.queryByText(/0\.00/)).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("keeps upcoming commitments visible when the current position and flows are empty", async () => {
    const next = {
      commitmentId: "commitment-excluded-account",
      label: "Synthetic excluded-account commitment",
      dueDate: "2026-03-25",
      nativeAmount: "-45.6700",
      nativeCurrency: "XIN",
      remainingBase: "-45.6700",
      remainingSecondary: "-2.0095",
    };
    renderWith(payload({
      netWorth: {
        ...payload().netWorth,
        totalBase: "0.0000",
        totalSecondary: "0.0000",
        assetsBase: "0.0000",
        assetsSecondary: "0.0000",
        liabilitiesBase: "0.0000",
        liabilitiesSecondary: "0.0000",
        liquidBase: "0.0000",
        liquidSecondary: "0.0000",
        rates: [],
      },
      thisMonth: month("2026-03-01"),
      trend: MONTHS.map((on) => month(on)),
      hasRecordedFlows: false,
      positionComposition: { rows: [], missing: [] },
      upcomingCommitments: {
        asOf: "2026-03-15",
        through: "2026-04-14",
        count: 1,
        totalBase: "-45.6700",
        totalSecondary: "-2.0095",
        missingBaseCurrencies: [],
        missingSecondaryCurrencies: [],
        next,
        items: [next],
      },
    }));

    expect(await screen.findByText("Nothing to report yet")).toBeTruthy();
    const commitments = screen.getByRole("region", { name: "Upcoming commitments — next 30 days" });
    expect(within(commitments).getByText("You owe ₹45.67 total")).toBeTruthy();
    expect(within(commitments).getByText("Synthetic excluded-account commitment")).toBeTruthy();
  });

  it("keeps closed-account history visible without inventing a current position", async () => {
    const past = payload();
    past.netWorth.rates = [];
    past.cardsDue = [];
    past.trend[9] = month("2026-01-01", {
      incomeBase: "100.0000", spendingBase: "25.0000", netBase: "75.0000",
      incomeSecondary: "4.0000", spendingSecondary: "1.0000", netSecondary: "3.0000",
    });
    past.flowEvidence[9] = {
      month: "2026-01-01", status: "complete",
      incomeBaseCoordinate: 10000, spendingBaseCoordinate: 2500, netBaseCoordinate: 7500,
      incomeSecondaryCoordinate: 10000, spendingSecondaryCoordinate: 2500, netSecondaryCoordinate: 7500,
    };
    renderWith(past);

    expect(await screen.findByText("No current position")).toBeTruthy();
    expect(screen.queryByText("Nothing to report yet")).toBeNull();
    const flow = screen.getByRole("region", { name: "Cash flow" });
    fireEvent.click(within(flow).getByText("Exact values"));
    expect(within(flow).getByText("₹100.00")).toBeTruthy();
  });

  it("shows the open count and the first five needs-attention items, linking to Plans", async () => {
    const items = Array.from({ length: 6 }, (_, i) => ({
      actionKey: `commitment.item-${i}.2026-03-0${i + 1}`,
      kind: "commitment" as const,
      reason: "commitment_due" as const,
      title: `Synthetic item ${i}`,
      dueDate: `2026-03-0${i + 1}`,
      overdue: false,
      snoozedUntil: null,
      accountId: null,
      accountName: null,
      amount: "-100.0000",
      remaining: "-100.0000",
      partlyPaid: false,
      currency: "XIN",
      certainty: "confirmed" as const,
      commitmentId: `item-${i}`,
      cardStatementId: null,
      actionItemId: null,
    }));
    renderWith(payload(), undefined, {
      asOf: "2026-03-01",
      items,
      snoozed: [],
      resolvedFollowUps: [],
      previousMonthReview: { month: "2026-02-01", completedAt: null },
    });

    const region = await screen.findByRole("region", { name: "Needs attention (6)" });
    expect(within(region).getByRole("heading", { name: "Needs attention (6)" })).toBeTruthy();
    const action = within(region).getByRole("link", { name: "Review plans" });
    expect(action).toHaveProperty("pathname", "/plans");
    expect(action).toHaveProperty("hash", "#actions");
    expect(within(region).getAllByRole("listitem")).toHaveLength(5);
    expect(within(region).getByText(/Synthetic item 4/)).toBeTruthy();
    expect(within(region).queryByText(/Synthetic item 5/)).toBeNull();
  });

  it("shows only the review status when there is nothing else to attend to", async () => {
    renderWith(payload());
    await screen.findByRole("heading", { name: "Overview", level: 1 });
    const region = screen.getByRole("region", { name: "Needs attention" });
    expect(within(region).queryAllByRole("listitem")).toHaveLength(0);
    expect(within(region).getByText("The previous monthly review is still open.")).toBeTruthy();
    const reviewLink = within(region).getByRole("link", { name: "Last month's review is not yet complete" });
    expect(reviewLink).toHaveProperty("pathname", "/plans");
    expect(reviewLink).toHaveProperty("hash", "#review");
  });

  it("states when last month's review is complete, linking to Plans", async () => {
    renderWith(payload(), undefined, {
      asOf: "2026-03-01",
      items: [],
      snoozed: [],
      resolvedFollowUps: [],
      previousMonthReview: { month: "2026-02-01", completedAt: "2026-03-02T10:00:00Z" },
    });
    await screen.findByRole("heading", { name: "Overview", level: 1 });
    // The date only, not the raw ISO timestamp (documents-screen.tsx's own convention).
    const reviewLink = screen.getByRole("link", {
      name: "Last month's review is complete (2026-03-02)",
    });
    expect(reviewLink).toHaveProperty("hash", "#review");
  });

  it("renders the service's own sentence when the request fails", async () => {
    renderWith(new ApiError(503, "The ledger is being restored; try again in a minute."));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The ledger is being restored; try again in a minute.");
    expect(alert.textContent).not.toContain("{");
  });
});
