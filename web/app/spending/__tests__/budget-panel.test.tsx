// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BudgetPanel } from "@/app/spending/budget-panel";
import { type apiFetch } from "@/lib/api/client";
import type { BudgetCopyResponse, BudgetLineRow, BudgetsResponse, CategoryRow, CurrencyRow } from "@/types/api";

type Request = typeof apiFetch;

const CURRENCIES: Array<CurrencyRow> = [
  { code: "AED", name: "UAE Dirham", symbol: "AED", decimals: 4, numberFormat: "international" },
];

const GROCERIES_ID = "00000000-0000-4000-8000-0000000000c1";
const DINING_ID = "00000000-0000-4000-8000-0000000000c2";

const CATEGORIES: Array<CategoryRow> = [
  {
    id: GROCERIES_ID, parentId: null, name: "Synthetic groceries", slug: "synthetic-groceries",
    kind: "expense", bucket: "need", sortOrder: 0, isSystem: false, isActive: true,
  },
  {
    id: DINING_ID, parentId: null, name: "Synthetic dining", slug: "synthetic-dining",
    kind: "expense", bucket: "want", sortOrder: 1, isSystem: false, isActive: true,
  },
];

function line(overrides: Partial<BudgetLineRow> = {}): BudgetLineRow {
  return {
    id: "00000000-0000-4000-8000-0000000000b1",
    categoryId: GROCERIES_ID,
    categoryName: "Synthetic groceries",
    bucket: null,
    amountBase: "100.0000",
    currency: "AED",
    rolloverEnabled: false,
    note: null,
    actualBase: "60.0000",
    carriedBase: "0.0000",
    availableBase: "100.0000",
    varianceBase: "40.0000",
    carryMonths: 0,
    carryStoppedReason: "rollover_off",
    excludedCategoryIds: [],
    ...overrides,
  };
}

function budgets(overrides: Partial<BudgetsResponse> = {}): BudgetsResponse {
  return {
    lines: [line()],
    totals: {
      amountBase: "100.0000", actualBase: "60.0000", carriedBase: "0.0000",
      availableBase: "100.0000", varianceBase: "40.0000",
    },
    from: "2026-03-01",
    to: "2026-03-31",
    month: "2026-03-01",
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

describe("budget panel", () => {
  it("shows a line's variance, carried figure and excluded categories from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === "/budgets?month=2026-03-01") {
        return budgets({
          lines: [
            line({
              rolloverEnabled: true,
              carriedBase: "20.0000",
              carryMonths: 1,
              carryStoppedReason: "no_prior_line",
            }),
            line({
              id: "00000000-0000-4000-8000-0000000000b2",
              categoryId: null,
              categoryName: null,
              bucket: "want",
              amountBase: "200.0000",
              actualBase: "30.0000",
              availableBase: "200.0000",
              varianceBase: "170.0000",
              excludedCategoryIds: [DINING_ID],
            }),
          ],
        });
      }
      throw new Error(`unexpected path ${path}`);
    });

    render(
      <BudgetPanel
        baseCurrency="AED"
        categories={CATEGORIES}
        currencies={CURRENCIES}
        month="2026-03-01"
        request={request}
      />,
    );

    await waitFor(() => expect(screen.getAllByText("Synthetic groceries").length).toBeGreaterThan(0));
    expect(screen.getByText("AED 40.0000")).toBeTruthy();
    const carriedNote = screen.getByText(/carried/);
    expect(carriedNote.textContent).toContain("20.0000");
    expect(carriedNote.textContent).toContain("over 1 month");
    expect(carriedNote.textContent).toContain("no earlier line to carry from");
    expect(screen.getByText(/excludes Synthetic dining/)).toBeTruthy();
  });

  it("sets a new budget line and refreshes the list", async () => {
    let created = false;
    const request = requestFrom((path, init) => {
      if (path === "/budgets?month=2026-03-01") {
        return created
          ? budgets({ lines: [line(), line({ id: "b2", categoryId: DINING_ID, categoryName: "Synthetic dining" })] })
          : budgets({ lines: [] });
      }
      if (path === "/budgets" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toMatchObject({
          periodMonth: "2026-03-01",
          categoryId: DINING_ID,
          bucket: null,
          amountBase: "75.0000",
          rolloverEnabled: false,
        });
        expect(typeof body.idempotencyKey).toBe("string");
        created = true;
        return line({ id: "b2", categoryId: DINING_ID, categoryName: "Synthetic dining", amountBase: "75.0000" });
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${path}`);
    });

    render(
      <BudgetPanel baseCurrency="AED" categories={CATEGORIES} currencies={CURRENCIES} month="2026-03-01" request={request} />,
    );

    await waitFor(() => expect(screen.getByText("No budget lines are set for this month.")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Category", { selector: "select" }), {
      target: { value: DINING_ID },
    });
    fireEvent.change(screen.getByLabelText("Amount (AED)"), { target: { value: "75.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Add budget" }));

    await waitFor(() => expect(screen.getAllByText("Synthetic dining").length).toBeGreaterThan(0));
  });

  it("toggles rollover on an existing line through PATCH", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/budgets?month=2026-03-01") return budgets();
      if (path.startsWith("/budgets/") && init?.method === "PATCH") {
        const body = JSON.parse(init.body as string);
        expect(body).toEqual({ amountBase: "100.0000", rolloverEnabled: true, note: null });
        return line({ rolloverEnabled: true });
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${path}`);
    });

    render(
      <BudgetPanel baseCurrency="AED" categories={CATEGORIES} currencies={CURRENCIES} month="2026-03-01" request={request} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const editForm = document.querySelector(".budget-line-form") as HTMLElement;
    fireEvent.click(within(editForm).getByLabelText(/Carry the unspent or overspent remainder/));
    fireEvent.click(within(editForm).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.queryByText("Edit Synthetic groceries")).toBeNull());
  });

  it("copies last month and reports what was skipped", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/budgets?month=2026-03-01") return budgets();
      if (path === "/budgets/copy" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toMatchObject({ fromMonth: "2026-02-01", toMonth: "2026-03-01" });
        const response: BudgetCopyResponse = { created: [line({ id: "copied" })], skippedCount: 1 };
        return response;
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${path}`);
    });

    render(
      <BudgetPanel baseCurrency="AED" categories={CATEGORIES} currencies={CURRENCIES} month="2026-03-01" request={request} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Copy last month" }));

    await screen.findByText(/Copied 1 line/);
    expect(screen.getByText(/1 already had a budget this month/)).toBeTruthy();
  });
});
