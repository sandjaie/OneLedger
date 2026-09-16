// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmergencyReserve } from "@/app/plans/emergency-reserve";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  AccountRow,
  CurrencyRow,
  EligibleAccountRow,
  EmergencyReserveResponse,
} from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

const account: AccountRow = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Synthetic emergency account",
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

const eligibleAccount: EligibleAccountRow = {
  accountId: account.id,
  accountName: account.name,
  currency: "XAA",
  valuationMode: "ledger_balance",
  value: "10000.0000",
  valueDate: "2026-01-01",
  reason: null,
  goalReserved: "0.0000",
  emergencyReserved: "4000.0000",
  eligible: "10000.0000",
  accessDays: 3,
  restrictionNote: "Synthetic notice period",
  upcomingCommitments: [],
};

function response(overrides: Partial<EmergencyReserveResponse> = {}): EmergencyReserveResponse {
  return {
    asOf: "2026-03-01",
    configured: true,
    settings: {
      targetMonths: "6.0",
      baselineMethod: "manual",
      referenceMonths: 6,
      buckets: ["need", "debt_service"],
      manualMonthlyBase: "2000.0000",
    },
    baseline: {
      method: "manual",
      monthlyBase: "2000.0000",
      referenceStart: null,
      referenceEnd: null,
      uncategorisedBase: null,
      status: "available",
    },
    accounts: [eligibleAccount],
    eligibleBase: "10000.0000",
    coverageMonths: "5.00",
    reservedMonths: "2.00",
    targetBase: "12000.0000",
    gapBase: "2000.0000",
    coverageStatus: "available",
    reasons: [],
    currencies: [{ currency: "XAA", eligibleNative: "10000.0000", rate: "1.0000000000", rateDate: "2026-03-01" }],
    coverageGapMonths: 0,
    baseCurrency: "XAA",
    reservations: [],
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

describe("emergency reserve", () => {
  it("shows loading, then coverage and settings figures from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === "/emergency-reserve") return response();
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<EmergencyReserve accounts={[account]} currencies={currencies} request={request} />);

    expect(screen.getByText("Loading the emergency reserve…")).toBeTruthy();

    expect(await screen.findByText(/5.00 months of coverage/)).toBeTruthy();
    expect(screen.getByText(/Target A 12,000.00/)).toBeTruthy();
    expect(screen.getByText(/Gap A 2,000.00/)).toBeTruthy();
    expect(screen.getByText(/Synthetic emergency account/)).toBeTruthy();
    expect(screen.getByText(/Synthetic notice period/)).toBeTruthy();
  });

  it("shows not enough information from the server, not a computed figure", async () => {
    const request = requestFrom((path) => {
      if (path === "/emergency-reserve") {
        return response({
          baseline: {
            method: "manual",
            monthlyBase: null,
            referenceStart: null,
            referenceEnd: null,
            uncategorisedBase: null,
            status: "not_enough_information",
          },
          coverageStatus: "not_enough_information",
          reasons: ["baseline_not_enough_information"],
          eligibleBase: null,
          coverageMonths: null,
          targetBase: null,
          gapBase: null,
          reservedMonths: null,
        });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<EmergencyReserve accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText(/Baseline not enough information/)).toBeTruthy();
    expect(screen.getByText(/Coverage: not enough information/)).toBeTruthy();
    expect(screen.getByText(/spending is not yet known well enough/)).toBeTruthy();
  });

  it("distinguishes an unavailable coverage from not enough information", async () => {
    const request = requestFrom((path) => {
      if (path === "/emergency-reserve") {
        return response({
          coverageStatus: "unavailable",
          reasons: ["missing_rate"],
          eligibleBase: null,
          coverageMonths: null,
          targetBase: null,
          gapBase: null,
          reservedMonths: null,
        });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<EmergencyReserve accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText(/Coverage unavailable/)).toBeTruthy();
    expect(screen.queryByText(/not enough information/)).toBeNull();
  });

  it("shows a sentence for an eligible account's reason code, not the raw code", async () => {
    const request = requestFrom((path) => {
      if (path === "/emergency-reserve") {
        return response({
          accounts: [{ ...eligibleAccount, value: null, valueDate: null, reason: "mixed_currency" }],
        });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<EmergencyReserve accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText(/this account holds a position in another currency/)).toBeTruthy();
    expect(screen.queryByText(/mixed_currency/)).toBeNull();
  });

  it("shows the error state and lets the owner try again", async () => {
    const request = requestFrom(() => {
      throw new ApiError(500, "boom");
    });
    render(<EmergencyReserve accounts={[account]} currencies={currencies} request={request} />);

    expect(await screen.findByText("The emergency reserve could not be loaded.")).toBeTruthy();
    fireEvent.click(screen.getByText("Try again"));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  });

  it("posts the settings body the owner edits", async () => {
    const calls: Array<{ path: string; body: unknown }> = [];
    const request = requestFrom((path, init) => {
      if (path === "/emergency-reserve" && (!init || init.method === undefined)) return response();
      if (path === "/emergency-reserve" && init?.method === "PUT") {
        calls.push({ path, body: init.body ? JSON.parse(init.body as string) : undefined });
        return response();
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<EmergencyReserve accounts={[account]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByText("Edit settings"));
    const targetInput = screen.getByLabelText("Target months");
    fireEvent.change(targetInput, { target: { value: "8.0" } });
    fireEvent.click(screen.getByText("Save settings"));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      path: "/emergency-reserve",
      body: {
        targetMonths: "8.0",
        baselineMethod: "manual",
        referenceMonths: 6,
        buckets: ["need", "debt_service"],
        manualMonthlyBase: "2000.0000",
      },
    });
  });

  it("posts an emergency reservation for the chosen account and amount", async () => {
    const calls: Array<{ path: string; body: unknown }> = [];
    const request = requestFrom((path, init) => {
      if (path === "/emergency-reserve" && (!init || init.method === undefined)) return response();
      if (path === "/reservations" && init?.method === "POST") {
        calls.push({ path, body: init.body ? JSON.parse(init.body as string) : undefined });
        return {};
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<EmergencyReserve accounts={[account]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByText("Reserve for emergencies"));
    fireEvent.change(screen.getByLabelText(/Amount/), { target: { value: "500.0000" } });
    fireEvent.click(screen.getByText("Reserve"));

    await waitFor(() => expect(calls).toHaveLength(1));
    const body = calls[0].body as Record<string, unknown>;
    expect(body.purpose).toBe("emergency");
    expect(body.goalId).toBeNull();
    expect(body.accountId).toBe(account.id);
    expect(body.kind).toBe("amount");
    expect(body.amount).toBe("500.0000");
  });

  it("removes an eligible account and surfaces the server's 409 detail", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/emergency-reserve" && (!init || init.method === undefined)) return response();
      if (path === `/emergency-reserve/accounts/${account.id}` && init?.method === "DELETE") {
        throw new ApiError(409, "release this account's emergency reservations first");
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<EmergencyReserve accounts={[account]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByText("Remove"));

    expect(
      await screen.findByText("release this account's emergency reservations first"),
    ).toBeTruthy();
  });
});
