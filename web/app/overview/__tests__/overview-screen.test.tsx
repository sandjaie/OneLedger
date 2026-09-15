// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
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
    ...overrides,
  };
}

function renderWith(
  body: DashboardResponse | Error,
  currency = household.currencies[0],
  actions: unknown = { asOf: "2026-03-01", items: [], snoozed: [], resolvedFollowUps: [] },
) {
  // Needs attention (Task 2) fetches /actions alongside /dashboard; every test here is about
  // the rest of the screen, so it gets a quiet empty response unless a test overrides it.
  const request = vi.fn(async (path: string) => {
    if (path === "/actions") return actions;
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

  it("presents the selected reporting currency as a glanceable net-worth headline", async () => {
    renderWith(payload());

    expect(await screen.findByRole("heading", { name: "Overview", level: 1 })).toBeTruthy();
    const worth = screen.getByRole("region", { name: "Net worth" });
    expect(within(worth).getByText("Primary reporting currency · XIN")).toBeTruthy();
    expect(
      within(worth).getByRole("heading", { name: "Net worth in XIN: -₹39,04,500.00" }),
    ).toBeTruthy();

    const summary = screen.getByRole("region", { name: "Position summary" });
    for (const label of ["Total assets", "Total liabilities", "Liquid net worth", "Savings rate"]) {
      expect(within(summary).getByText(label)).toBeTruthy();
    }
    expect(within(summary).queryByText("Cash available")).toBeNull();
  });

  it("links net worth to Wealth, invested to Investments, and offers a Coverage link", async () => {
    renderWith(payload());
    const worth = await screen.findByRole("region", { name: "Net worth" });

    expect(within(worth).getByRole("link", { name: "-₹39,04,500.00" })).toHaveProperty("pathname", "/wealth");
    expect(screen.getByRole("link", { name: "Coverage" })).toHaveProperty("pathname", "/coverage");

    const year = screen.getByRole("region", { name: "This year so far" });
    expect(within(year).getByRole("link", { name: "₹2,00,000.00" })).toHaveProperty("pathname", "/investments");
  });

  it("renders net worth in both currencies, flipping a liability in presentation only", async () => {
    const request = renderWith(payload());
    const worth = await screen.findByRole("region", { name: "Net worth" });

    // A negative net worth is a negative figure, not something owed; liabilities are owed.
    expect(within(worth).getByText("-₹39,04,500.00")).toBeTruthy();
    expect(within(worth).getByText("-AED 176,000.00")).toBeTruthy();
    expect(within(worth).getByText("You owe ₹40,04,500.00")).toBeTruthy();
    expect(within(worth).getByText("You owe AED 180,500.00")).toBeTruthy();
    expect(within(worth).getByText("₹1,00,000.00")).toBeTruthy();
    expect(within(worth).getByText("₹95,500.00")).toBeTruthy();
    expect(request.mock.calls.map((call: unknown[]) => call[0])).toEqual(["/dashboard", "/actions"]);
  });

  it("labels a carried rate with its real date, and states a missing rate instead of a number", async () => {
    const base = payload().netWorth;
    renderWith(
      payload({
        netWorth: {
          ...base,
          totalSecondary: null,
          assetsSecondary: null,
          liabilitiesSecondary: null,
          liquidSecondary: null,
          missing: [{ currency: "XZZ", reportingCurrency: "XAE", asOf: "2026-03-15" }],
        },
      }),
    );
    const worth = await screen.findByRole("region", { name: "Net worth" });

    expect(within(worth).getByText("XAE: as of the rate for 2026-03-13, carried forward")).toBeTruthy();
    expect(
      within(worth).getByText(
        "No XZZ rate is stored on or before 2026-03-15, so net worth in XAE cannot be stated.",
      ),
    ).toBeTruthy();
    // The secondary side states its reason and no figure at all.
    for (const figure of ["-AED 176,000.00", "AED 4,500.00", "You owe AED 180,500.00", "AED 4,300.00"]) {
      expect(within(worth).queryByText(figure)).toBeNull();
    }
    expect(within(worth).getByText("-₹39,04,500.00")).toBeTruthy();
  });

  it("renders this month and the trend from the payload, and a changed payload changes every figure", async () => {
    renderWith(payload());
    const thisMonth = await screen.findByRole("region", { name: "This month" });
    for (const shown of ["₹5,00,000.00", "₹1,23,456.78", "₹3,76,543.22", "75.31%"]) {
      expect(within(thisMonth).getByText(shown)).toBeTruthy();
    }
    const rows = within(screen.getByRole("table", { name: "The last twelve months" })).getAllByRole("row");
    expect(rows).toHaveLength(13);
    expect(within(rows[1]).getByText("Apr 2025")).toBeTruthy();
    expect(within(rows[1]).getAllByText("₹0.00")).toHaveLength(3);
    expect(within(rows[1]).getByText("Not applicable")).toBeTruthy();
    expect(within(rows[12]).getByText("Mar 2026")).toBeTruthy();
    expect(within(rows[12]).getByText("₹3,76,543.22")).toBeTruthy();

    cleanup();
    const april = month("2026-03-01", {
      incomeBase: "600000.0000",
      spendingBase: "150000.0000",
      netBase: "450000.0000",
      savingsRatePctBase: "75.0000",
    });
    renderWith(payload({ thisMonth: april, trend: [...MONTHS.slice(0, 11).map((on) => month(on)), april] }));
    const changed = await screen.findByRole("region", { name: "This month" });
    for (const shown of ["₹6,00,000.00", "₹1,50,000.00", "₹4,50,000.00", "75.00%"]) {
      expect(within(changed).getByText(shown)).toBeTruthy();
    }
    for (const gone of ["₹5,00,000.00", "₹1,23,456.78", "₹3,76,543.22", "75.31%"]) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });

  it("shows the month and the trend in the reporting currency the owner chose", async () => {
    renderWith(payload(), household.currencies[1]);
    const thisMonth = await screen.findByRole("region", { name: "This month" });
    expect(within(thisMonth).getByText("AED 22,000.00")).toBeTruthy();
    expect(within(thisMonth).getByText("AED 5,432.10")).toBeTruthy();
    expect(within(thisMonth).queryByText(/₹/)).toBeNull();
    const trend = screen.getByRole("table", { name: "The last twelve months" });
    expect(within(trend).getByText("AED 16,567.90")).toBeTruthy();
    expect(within(trend).queryByText(/₹/)).toBeNull();
  });

  it("renders the year-to-date panel from the payload, switching currency with the toggle", async () => {
    renderWith(payload());
    const year = await screen.findByRole("region", { name: "This year so far" });
    expect(within(year).getByText("This year so far · 2026-01-01 to 2026-03-15")).toBeTruthy();
    for (const shown of ["₹15,00,000.00", "₹3,70,000.00", "₹11,30,000.00", "75.33%", "₹2,00,000.00"]) {
      expect(within(year).getByText(shown)).toBeTruthy();
    }

    cleanup();
    renderWith(payload(), household.currencies[1]);
    const secondary = await screen.findByRole("region", { name: "This year so far" });
    expect(within(secondary).getByText("AED 66,000.00")).toBeTruthy();
    expect(within(secondary).getByText("AED 8,800.00")).toBeTruthy();
    expect(within(secondary).queryByText(/₹/)).toBeNull();
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
    expect(within(first).getByText("Z 40.000")).toBeTruthy();
    expect(within(first).getByText("2026-03-20 · in 18 days")).toBeTruthy();
    expect(within(first).getByText("22.50%")).toBeTruthy();
    expect(within(second).getByText("₹1,200.00")).toBeTruthy();
    expect(within(second).getByText("2026-03-10 · 5 days overdue")).toBeTruthy();
    expect(within(second).getByText("No limit stated")).toBeTruthy();
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
      }),
    );

    expect(await screen.findByText(/Nothing to report yet/)).toBeTruthy();
    expect(screen.queryByText(/0\.00/)).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows an investment coverage reason with a link, and skips a fully current one", async () => {
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
    const carriedSnapshot: InvestmentCoverage = {
      accountId: "acct-2",
      holdingId: null,
      valuationMode: "snapshot",
      status: "carried_snapshot",
      valueDate: "2026-02-01",
      carried: true,
      ageDays: 42,
      method: "owner_entered",
      confidence: "medium",
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
    renderWith(
      payload({
        netWorth: { ...base, investmentCoverage: [missingPrice, carriedSnapshot, current] },
      }),
    );

    const region = await screen.findByRole("region", { name: "Why some figures may be incomplete" });
    expect(within(region).getByText(/No accepted price is recorded for this holding\./)).toBeTruthy();
    expect(within(region).getByRole("link", { name: "View holding" })).toHaveProperty(
      "pathname",
      "/investments/holding-1",
    );
    expect(within(region).getByText(/carried forward/)).toBeTruthy();
    const snapshotLink = within(region).getByRole("link", { name: "View account" });
    expect(snapshotLink).toHaveProperty("pathname", "/accounts");
    // A snapshot-mode account's coverage reason opens straight into its valuation panel.
    expect(snapshotLink).toHaveProperty("search", "?valuation=acct-2");
    // The fully current entry names no reason and gets no row.
    expect(within(region).getAllByRole("listitem")).toHaveLength(2);
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
    renderWith(payload(), undefined, { asOf: "2026-03-01", items, snoozed: [], resolvedFollowUps: [] });

    const heading = await screen.findByRole("link", { name: "Needs attention (6)" });
    expect(heading).toHaveProperty("pathname", "/plans");
    expect(heading).toHaveProperty("hash", "#actions");
    const region = screen.getByRole("region", { name: "Needs attention (6)" });
    expect(within(region).getAllByRole("listitem")).toHaveLength(5);
    expect(within(region).getByText(/Synthetic item 4/)).toBeTruthy();
    expect(within(region).queryByText(/Synthetic item 5/)).toBeNull();
  });

  it("shows nothing when there is nothing to attend to", async () => {
    renderWith(payload());
    await screen.findByRole("heading", { name: "Overview", level: 1 });
    expect(screen.queryByText(/Needs attention/)).toBeNull();
  });

  it("renders the service's own sentence when the request fails", async () => {
    renderWith(new ApiError(503, "The ledger is being restored; try again in a minute."));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The ledger is being restored; try again in a minute.");
    expect(alert.textContent).not.toContain("{");
  });
});
