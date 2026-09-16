// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RecurringPanel } from "@/app/spending/recurring-panel";
import { type apiFetch } from "@/lib/api/client";
import type { CandidateRow, CategoryRow, ConfirmedSeriesRow, CurrencyRow, RecurringResponse } from "@/types/api";

type Request = typeof apiFetch;

const CURRENCIES: Array<CurrencyRow> = [
  { code: "AED", name: "UAE Dirham", symbol: "AED", decimals: 4, numberFormat: "international" },
];

const GROCERIES_ID = "00000000-0000-4000-8000-0000000000c1";
const CATEGORIES: Array<CategoryRow> = [
  {
    id: GROCERIES_ID,
    parentId: null,
    name: "Synthetic groceries",
    slug: "synthetic-groceries",
    kind: "expense",
    bucket: "need",
    sortOrder: 0,
    isSystem: false,
    isActive: true,
  },
];

function candidate(overrides: Partial<CandidateRow> = {}): CandidateRow {
  return {
    identity: { merchantId: null, matchKey: "synthetic gym membership" },
    label: "Synthetic gym membership",
    cadence: "monthly",
    occurrences: 4,
    firstSeenOn: "2026-01-05",
    lastSeenOn: "2026-04-05",
    lastAmountBase: "50.0000",
    typicalAmountBase: "50.0000",
    categoryId: null,
    categoryName: null,
    transactionIds: ["t1", "t2", "t3", "t4"],
    ...overrides,
  };
}

function confirmed(overrides: Partial<ConfirmedSeriesRow> = {}): ConfirmedSeriesRow {
  return {
    id: "00000000-0000-4000-8000-0000000000a1",
    label: "Synthetic streaming",
    identity: { merchantId: "00000000-0000-4000-8000-0000000000m1", matchKey: null },
    cadence: "monthly",
    categoryId: null,
    categoryName: null,
    status: "active",
    expectedAmountBase: "20.0000",
    lastAmountBase: "20.0000",
    lastSeenOn: "2026-08-01",
    nextExpectedOn: "2026-09-01",
    memberCount: 6,
    currency: "AED",
    ...overrides,
  };
}

function recurring(overrides: Partial<RecurringResponse> = {}): RecurringResponse {
  return { confirmed: [], candidates: [], ...overrides };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("recurring panel", () => {
  it("confirms a candidate with the prefilled form and refreshes the list", async () => {
    let confirmedNow = false;
    const request = requestFrom((path, init) => {
      if (path === "/recurring") {
        return confirmedNow
          ? recurring({ confirmed: [confirmed({ label: "Synthetic gym membership" })] })
          : recurring({ candidates: [candidate()] });
      }
      if (path === "/recurring/confirm" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toMatchObject({
          identity: { merchantId: null, matchKey: "synthetic gym membership" },
          label: "Synthetic gym membership",
          cadence: "monthly",
          expectedAmountBase: "50.0000",
          categoryId: null,
        });
        expect(typeof body.idempotencyKey).toBe("string");
        confirmedNow = true;
        return confirmed({ label: "Synthetic gym membership" });
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${path}`);
    });

    render(
      <RecurringPanel baseCurrency="AED" categories={CATEGORIES} currencies={CURRENCIES} request={request} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this series" }));

    // Only the confirmed row carries "recorded"; the candidate row does not, so this — unlike
    // the shared label — only becomes true once the candidate has actually moved.
    await screen.findByText(/recorded/);
    expect(screen.getByText("No repeated payment has been found yet.")).toBeTruthy();
  });

  it("dismisses a candidate", async () => {
    let dismissed = false;
    const request = requestFrom((path, init) => {
      if (path === "/recurring") {
        return dismissed ? recurring() : recurring({ candidates: [candidate()] });
      }
      if (path === "/recurring/dismiss" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toEqual({ identity: { merchantId: null, matchKey: "synthetic gym membership" } });
        dismissed = true;
        return { dismissed: true };
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${path}`);
    });

    render(
      <RecurringPanel baseCurrency="AED" categories={CATEGORIES} currencies={CURRENCIES} request={request} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));

    await waitFor(() =>
      expect(screen.getByText("No repeated payment has been found yet.")).toBeTruthy(),
    );
  });

  it("accepts a price change with the confirmed series' own expected amount", async () => {
    const priceChanged = confirmed({ status: "price_changed", expectedAmountBase: "20.0000", lastAmountBase: "25.0000" });
    let accepted = false;
    const request = requestFrom((path, init) => {
      if (path === "/recurring") {
        return recurring({ confirmed: [accepted ? { ...priceChanged, status: "active", expectedAmountBase: "25.0000" } : priceChanged] });
      }
      if (path === `/recurring/${priceChanged.id}/accept-price` && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toEqual({ expectedAmountBase: "20.0000" });
        accepted = true;
        return { ...priceChanged, status: "active", expectedAmountBase: "25.0000" };
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${path}`);
    });

    render(
      <RecurringPanel baseCurrency="AED" categories={CATEGORIES} currencies={CURRENCIES} request={request} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Accept new amount" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Accept new amount" })).toBeNull(),
    );
  });

  it("ends an overdue series with a reason", async () => {
    const overdue = confirmed({ status: "overdue" });
    let ended = false;
    const request = requestFrom((path, init) => {
      if (path === "/recurring") {
        return ended ? recurring() : recurring({ confirmed: [overdue] });
      }
      if (path === `/recurring/${overdue.id}/end` && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toEqual({ endedReason: "synthetic: cancelled" });
        ended = true;
        return { ...overdue };
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${path}`);
    });

    render(
      <RecurringPanel baseCurrency="AED" categories={CATEGORIES} currencies={CURRENCIES} request={request} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "End series" }));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "synthetic: cancelled" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm end" }));

    await waitFor(() =>
      expect(screen.getByText("No recurring payment is confirmed yet.")).toBeTruthy(),
    );
  });
});
