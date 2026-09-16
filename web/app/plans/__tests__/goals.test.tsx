// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Goals } from "@/app/plans/goals";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AccountRow, CurrencyRow, GoalRow, GoalsResponse, ResourceAccountRow } from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XBB", name: "Synthetic Secondary Currency", symbol: "B", decimals: 2, numberFormat: "international" },
  { code: "XCC", name: "Synthetic Other Currency", symbol: "C", decimals: 2, numberFormat: "international" },
];

// The household's reporting pair (spec 3.3 "Goal progress": a goal's currency is the household
// base or secondary currency only) — XCC above is neither, to prove the picker excludes it.
const householdCurrencies = { base: "XAA", secondary: "XBB" };

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

const resourceAccount: ResourceAccountRow = {
  accountId: account.id,
  accountName: account.name,
  currency: "XAA",
  valuationMode: "ledger_balance",
  value: "10000.0000",
  valueDate: "2026-01-01",
  reason: null,
  reserved: "6000.0000",
  unreserved: "4000.0000",
  overReserved: false,
  wholeAccountReserved: false,
};

function goal(overrides: Partial<GoalRow> = {}): GoalRow {
  return {
    id: "00000000-0000-4000-8000-0000000000g1",
    name: "Synthetic trip fund",
    targetAmount: "10000.0000",
    targetCurrency: "XAA",
    targetDate: "2027-01-01",
    priority: 2,
    plannedMonthly: "500.0000",
    state: "active",
    closedAt: null,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    progressStatus: "available",
    reasons: [],
    current: "6000.0000",
    contributed: "6000.0000",
    market: "0.0000",
    fx: "0.0000",
    projected: "7000.0000",
    shortfall: "3000.0000",
    surplus: "0.0000",
    percent: "60.00",
    monthsRemaining: 2,
    reservations: [],
    ...overrides,
  };
}

function response(overrides: Partial<GoalsResponse> = {}): GoalsResponse {
  return {
    asOf: "2026-01-15",
    goals: [goal()],
    accounts: [resourceAccount],
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

describe("goals", () => {
  it("shows loading, then a goal's figures from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === "/goals?includeClosed=true") return response();
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);

    expect(screen.getByText("Loading goals…")).toBeTruthy();

    expect(await screen.findByText(/Synthetic trip fund/)).toBeTruthy();
    expect(screen.getByText(/Current A 6,000.00 \(60.00%\)/)).toBeTruthy();
    expect(screen.getByText(/Shortfall A 3,000.00/)).toBeTruthy();
    expect(screen.getByText(/Planned A 500.00\/month/)).toBeTruthy();
  });

  it("shows the error state and lets the owner try again", async () => {
    const request = requestFrom(() => {
      throw new ApiError(500, "boom");
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    expect(await screen.findByText("Goals could not be loaded.")).toBeTruthy();
  });

  it("names an unavailable progress from its reasons", async () => {
    const request = requestFrom((path) => {
      if (path === "/goals?includeClosed=true") {
        return response({
          goals: [
            goal({
              progressStatus: "unavailable",
              reasons: ["missing_value_or_rate"],
              current: null, contributed: null, market: null, fx: null, projected: null,
              shortfall: null, surplus: null, percent: null,
            }),
          ],
        });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    expect(await screen.findByText(/Progress unavailable — a value or exchange rate is unavailable/)).toBeTruthy();
  });

  it("shows the over-reserved warning from the server's reason", async () => {
    const request = requestFrom((path) => {
      if (path === "/goals?includeClosed=true") {
        return response({ goals: [goal({ reasons: ["over_reserved"] })] });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    expect(
      await screen.findByText(/this account is reserved for more than it is currently worth/),
    ).toBeTruthy();
  });

  it("posts the goal create body with a fresh idempotency key", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/goals?includeClosed=true") return response({ goals: [] });
      if (path === "/goals" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toMatchObject({
          name: "Synthetic new goal",
          targetAmount: "5000.0000",
          targetCurrency: "XAA",
          targetDate: "2027-06-01",
          priority: 3,
          plannedMonthly: null,
        });
        expect(typeof body.idempotencyKey).toBe("string");
        return goal({ name: "Synthetic new goal" });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    await screen.findByText("No goals are recorded yet.");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Synthetic new goal" } });
    fireEvent.change(screen.getByLabelText("Target amount"), { target: { value: "5000.0000" } });
    fireEvent.change(screen.getByLabelText("Target date"), { target: { value: "2027-06-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));

    expect(await screen.findByText("Goal added.")).toBeTruthy();
  });

  it("edits a goal via PUT with the same fields and no idempotency key", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/goals?includeClosed=true") return response();
      if (path === `/goals/${goal().id}` && init?.method === "PUT") {
        const body = JSON.parse(init.body as string);
        expect(body.idempotencyKey).toBeUndefined();
        expect(body).toMatchObject({ name: "Synthetic renamed fund" });
        return goal({ name: "Synthetic renamed fund" });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Synthetic renamed fund" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Goal updated.")).toBeTruthy();
  });

  it("posts a reservation with the chosen account, kind and amount", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/goals?includeClosed=true") return response();
      if (path === "/reservations" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toMatchObject({
          purpose: "goal", goalId: goal().id, accountId: account.id, kind: "amount",
          amount: "1000.0000",
        });
        expect(typeof body.idempotencyKey).toBe("string");
        return { id: "r1" };
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: "Reserve" }));
    fireEvent.change(screen.getByLabelText(/Amount/), { target: { value: "1000.0000" } });
    const reserveButtons = screen.getAllByRole("button", { name: "Reserve" });
    fireEvent.click(reserveButtons[reserveButtons.length - 1]);

    await waitFor(() =>
      expect(
        (request as ReturnType<typeof vi.fn>).mock.calls.some((c) => c[0] === "/reservations"),
      ).toBe(true),
    );
  });

  it("releases a reservation", async () => {
    const active = goal({
      reservations: [
        {
          id: "res-1", purpose: "goal", goalId: goal().id, accountId: account.id,
          accountName: account.name, kind: "amount", nativeAmount: "1000.0000", currency: "XAA",
          reservedOn: "2026-01-10", rateAtReservation: "1.0000000000", nativeNow: "1000.0000",
          currentValue: "1000.0000", releasedAt: null, releaseReason: null,
        },
      ],
    });
    const request = requestFrom((path, init) => {
      if (path === "/goals?includeClosed=true") return response({ goals: [active] });
      if (path === "/reservations/res-1/release" && init?.method === "POST") return { id: "res-1" };
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: "Release" }));

    await waitFor(() =>
      expect(
        (request as ReturnType<typeof vi.fn>).mock.calls.some(
          (c) => c[0] === "/reservations/res-1/release",
        ),
      ).toBe(true),
    );
  });

  it("confirms before marking a goal achieved, and releases on confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const request = requestFrom((path, init) => {
      if (path === "/goals?includeClosed=true") return response();
      if (path === `/goals/${goal().id}/close` && init?.method === "POST") {
        expect(JSON.parse(init.body as string)).toEqual({ state: "achieved" });
        return goal({ state: "achieved", closedAt: "2026-01-20T00:00:00Z" });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark achieved" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/reservations will be released/));
    await waitFor(() =>
      expect(
        (request as ReturnType<typeof vi.fn>).mock.calls.some((c) => c[0] === `/goals/${goal().id}/close`),
      ).toBe(true),
    );
  });

  it("does not close a goal when the achieved confirmation is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const request = requestFrom((path) => {
      if (path === "/goals?includeClosed=true") return response();
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark achieved" }));

    expect(
      (request as ReturnType<typeof vi.fn>).mock.calls.some((c) => c[0] === `/goals/${goal().id}/close`),
    ).toBe(false);
  });

  it("confirms before abandoning a goal, and releases on confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const request = requestFrom((path, init) => {
      if (path === "/goals?includeClosed=true") return response();
      if (path === `/goals/${goal().id}/close` && init?.method === "POST") {
        expect(JSON.parse(init.body as string)).toEqual({ state: "abandoned" });
        return goal({ state: "abandoned", closedAt: "2026-01-20T00:00:00Z" });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: "Abandon" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/reservations will be released/));
    await waitFor(() =>
      expect(
        (request as ReturnType<typeof vi.fn>).mock.calls.some((c) => c[0] === `/goals/${goal().id}/close`),
      ).toBe(true),
    );
  });

  it("offers only the household's base and secondary currency for a new goal", async () => {
    const request = requestFrom((path) => {
      if (path === "/goals?includeClosed=true") return response({ goals: [] });
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    await screen.findByText("No goals are recorded yet.");

    const picker = screen.getByLabelText("Currency") as HTMLSelectElement;
    expect(Array.from(picker.options).map((option) => option.value)).toEqual(["XAA", "XBB"]);
  });

  it("lists resources with value, reserved and unreserved", async () => {
    const request = requestFrom((path) => {
      if (path === "/goals?includeClosed=true") return response();
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    await screen.findByText("Resources");
    expect(screen.getByText("A 10,000.00")).toBeTruthy();
    expect(screen.getByText("A 4,000.00")).toBeTruthy();
  });

  it("shows a resource's valuation mode and the snapshot assumption", async () => {
    const request = requestFrom((path) => {
      if (path === "/goals?includeClosed=true") {
        return response({ accounts: [{ ...resourceAccount, valuationMode: "snapshot" }] });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    expect(await screen.findByText("snapshot")).toBeTruthy();
    expect(screen.getByText(/treated as valuation movement/)).toBeTruthy();
  });

  it("shows a sentence for an unavailable resource's reason code, not the raw code", async () => {
    const request = requestFrom((path) => {
      if (path === "/goals?includeClosed=true") {
        return response({
          accounts: [{ ...resourceAccount, value: null, valueDate: null, reason: "missing_price" }],
        });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Goals accounts={[account]} currencies={currencies} householdCurrencies={householdCurrencies} request={request} />);
    expect(await screen.findByText(/a price is missing for one of this account's holdings/)).toBeTruthy();
    expect(screen.queryByText(/missing_price/)).toBeNull();
  });
});
