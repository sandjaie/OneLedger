// @vitest-environment jsdom

import { readFileSync } from "node:fs";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpendingScreen } from "@/app/spending/spending-screen";
import { type apiFetch } from "@/lib/api/client";
import type {
  BudgetsResponse,
  CategoryRow,
  CategorySpending,
  CurrencyRow,
  HouseholdCurrency,
  SpendingResponse,
} from "@/types/api";

type Request = typeof apiFetch;

const CURRENCY: HouseholdCurrency = { code: "AED", symbol: "AED", decimals: 4, numberFormat: "international" };
const CURRENCIES: Array<CurrencyRow> = [
  { code: "AED", name: "UAE Dirham", symbol: "AED", decimals: 4, numberFormat: "international" },
  { code: "USD", name: "US Dollar", symbol: "$", decimals: 4, numberFormat: "international" },
];
const CATEGORIES: Array<CategoryRow> = [];

const GROCERIES_ID = "00000000-0000-4000-8000-0000000000c1";

const spending: SpendingResponse = {
  byCategory: [
    {
      categoryId: GROCERIES_ID,
      categoryName: "Synthetic groceries",
      parentCategoryId: null,
      parentCategoryName: null,
      bucket: "need",
      amountBase: "100.0000",
      amountSecondary: "25.0000",
      transactionCount: 2,
      previousAmountBase: "70.0000",
      chartRatio: "1.0000",
      chartCoordinate: 10000,
    },
    {
      categoryId: null,
      categoryName: "Uncategorised",
      parentCategoryId: null,
      parentCategoryName: null,
      bucket: null,
      amountBase: "30.0000",
      amountSecondary: "7.5000",
      transactionCount: 1,
      previousAmountBase: "0.0000",
      chartRatio: "0.3000",
      chartCoordinate: 3000,
    },
  ],
  byBucket: [
    {
      bucket: "need",
      amountBase: "100.0000",
      amountSecondary: "25.0000",
      transactionCount: 2,
      previousAmountBase: "70.0000",
      chartRatio: "1.0000",
      chartCoordinate: 10000,
    },
  ],
  totalBase: "130.0000",
  totalSecondary: "32.5000",
  previousTotalBase: "70.0000",
  from: "2026-03-01",
  to: "2026-03-31",
  baseCurrency: "AED",
  secondaryCurrency: "USD",
};

// The budget panel mounted at the bottom of this screen (Phase 4.1 Task 2) fetches its own data;
// these tests exercise only the spending breakdown above it, so it is always empty here.
const emptyBudgets: BudgetsResponse = {
  lines: [],
  totals: {
    amountBase: "0.0000",
    actualBase: "0.0000",
    carriedBase: "0.0000",
    availableBase: "0.0000",
    varianceBase: "0.0000",
  },
  from: "2026-03-01",
  to: "2026-03-31",
  month: "2026-03-01",
};

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn((path: string, init?: RequestInit) => Promise.resolve(handler(path, init))) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("spending screen", () => {
  it("renders the bucket and category breakdown from server strings", async () => {
    const request = requestFrom((path) => {
      if (path.startsWith("/spending?")) return spending;
      if (path.startsWith("/budgets?")) return emptyBudgets;
      throw new Error(`unexpected path ${path}`);
    });

    render(
      <SpendingScreen categories={CATEGORIES} currencies={CURRENCIES} currency={CURRENCY} request={request} />,
    );

    await waitFor(() => expect(screen.getAllByText("Synthetic groceries").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Uncategorised").length).toBeGreaterThan(0);
    // The current-period figure and the previous-period figure are both the server's own
    // strings — never a value the browser computed from them.
    expect(screen.getAllByText("AED 100.0000").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AED 70.0000").length).toBeGreaterThan(0);
    screen.getByText("AED 130.0000");
    const metrics = screen.getByRole("region", { name: "Spending summary" });
    expect(metrics.textContent).toContain("This period");
    expect(metrics.textContent).toContain("Previous period");
    expect(screen.getByRole("group", { name: "Date range" }).className).toContain("data-toolbar");
    expect(document.querySelector(".spending-evidence-chart")?.getAttribute("data-dense")).toBe("true");
    expect(screen.getByRole("table", { name: "Spending by bucket" }).className).toContain("console-table");
    expect(screen.getByRole("table", { name: "Spending by bucket" }).classList.contains("spending-table--bucket")).toBe(true);
    expect(screen.getByRole("table", { name: "Spending by category" }).classList.contains("spending-table--category")).toBe(true);
  });

  it("keeps the adopted screen grid and Spending numeric columns scoped in CSS", () => {
    const css = readFileSync(`${process.cwd()}/app/globals.css`, "utf8");

    expect(css).toContain(".workspace-grid > section:is(.coverage-screen, .wealth-screen, .spending-screen)");
    expect(css).toContain(".spending-table--bucket :is(th:nth-child(2), th:nth-child(3), td:nth-child(n + 2))");
    expect(css).toContain(".spending-table--category :is(th:nth-child(n + 3), td:nth-child(n + 3))");
  });

  it("carries the category and dates on the drill-through link", async () => {
    const request = requestFrom((path) =>
      path.startsWith("/spending?") ? spending : path.startsWith("/budgets?") ? emptyBudgets : {},
    );

    render(
      <SpendingScreen categories={CATEGORIES} currencies={CURRENCIES} currency={CURRENCY} request={request} />,
    );

    await waitFor(() => expect(screen.getAllByText("Synthetic groceries").length).toBeGreaterThan(0));
    const heading = screen.getAllByText("Synthetic groceries").find((node) => node.tagName === "TH");
    expect(heading).toBeDefined();
    const row = (heading as HTMLElement).closest("tr");
    expect(row).not.toBeNull();
    const link = within(row as HTMLElement).getByRole("link");
    expect(link.getAttribute("href")).toBe(
      `/transactions?categoryId=${GROCERIES_ID}&dateFrom=2026-03-01&dateTo=2026-03-31`,
    );
  });

  it("marks a net refund from its exact signed amount even when chart geometry is zero", async () => {
    const refund: CategorySpending = {
      ...spending.byCategory[0], categoryId: "00000000-0000-4000-8000-0000000000c2",
      categoryName: "Synthetic tiny refund", amountBase: "-0.0001", chartRatio: "0.0000", chartCoordinate: 0,
    };
    const request = requestFrom((path) => path.startsWith("/spending?")
      ? { ...spending, byCategory: [refund] }
      : emptyBudgets);
    render(<SpendingScreen categories={CATEGORIES} currencies={CURRENCIES} currency={CURRENCY} request={request} />);
    expect(await screen.findByText("Net refund")).toBeTruthy();
  });

  it("prompts for a range instead of throwing when the month input is cleared", async () => {
    const calls: Array<string> = [];
    const request = requestFrom((path) => {
      calls.push(path);
      return path.startsWith("/spending?") ? spending : path.startsWith("/budgets?") ? emptyBudgets : {};
    });

    render(
      <SpendingScreen categories={CATEGORIES} currencies={CURRENCIES} currency={CURRENCY} request={request} />,
    );
    await waitFor(() => expect(screen.getAllByText("Synthetic groceries").length).toBeGreaterThan(0));
    const before = calls.length;

    // `type="month"` is a plain text box in Firefox and Safari, and Chrome lets it be cleared.
    fireEvent.change(screen.getByLabelText("Month"), { target: { value: "" } });

    expect(screen.getByText("Choose a from and to date.")).toBeTruthy();
    expect(calls.length).toBe(before);
  });

  it("points the download link at the export path for the same range", async () => {
    const request = requestFrom((path) =>
      path.startsWith("/spending?") ? spending : path.startsWith("/budgets?") ? emptyBudgets : {},
    );

    render(
      <SpendingScreen categories={CATEGORIES} currencies={CURRENCIES} currency={CURRENCY} request={request} />,
    );

    await waitFor(() => expect(screen.getAllByText("Synthetic groceries").length).toBeGreaterThan(0));
    const download = screen.getByText("Download CSV");
    expect(download.getAttribute("href")).toContain("/spending/export.csv?from=");
    expect(download.getAttribute("href")).toContain("&to=");
  });

  it("links to the Recurring screen", async () => {
    const request = requestFrom((path) =>
      path.startsWith("/spending?") ? spending : path.startsWith("/budgets?") ? emptyBudgets : {},
    );

    render(
      <SpendingScreen categories={CATEGORIES} currencies={CURRENCIES} currency={CURRENCY} request={request} />,
    );

    await waitFor(() => expect(screen.getAllByText("Synthetic groceries").length).toBeGreaterThan(0));
    expect(screen.getByRole("link", { name: "Recurring" }).getAttribute("href")).toBe("/recurring");
  });
});
