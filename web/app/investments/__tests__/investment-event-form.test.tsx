// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InvestmentEventForm } from "@/app/investments/investment-event-form";
import type { apiFetch } from "@/lib/api/client";
import type { AccountRow, AcquisitionPosition, PortfolioPositions } from "@/types/api";

type Request = typeof apiFetch;

const HOLDING = { accountId: "00000000-0000-4000-8000-000000000010", securityId: "00000000-0000-4000-8000-000000000040", securityName: "Synthetic Growth Fund", currency: "XAA" };
const EMPTY_POSITIONS: PortfolioPositions = { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };

function position(overrides: Partial<AcquisitionPosition> = {}): AcquisitionPosition {
  return {
    holdingId: "00000000-0000-4000-8000-000000000051",
    accountId: HOLDING.accountId,
    securityId: "00000000-0000-4000-8000-000000000041",
    securityName: "Synthetic Bond Fund",
    assetClass: "fixed_income",
    currency: "XAA",
    asOf: "2026-02-06",
    units: "5.00000000",
    cumulativeContributionsNative: "50.0000",
    cumulativeContributionsBase: "50.0000",
    cumulativeContributionsSecondary: "100.0000",
    priceStatus: "current",
    valueStatus: "available",
    price: "10.000000",
    priceDate: "2026-02-06",
    currentValueNative: "50.0000",
    currentValueBase: "50.0000",
    currentValueSecondary: "100.0000",
    gainNative: "0.0000",
    gainBase: "0.0000",
    gainSecondary: "0.0000",
    missingFx: [],
    fxRates: [],
    closed: false,
    xirrPercent: null,
    xirrReason: null,
    ...overrides,
  };
}

function account(overrides: Partial<AccountRow> = {}): AccountRow {
  return {
    id: "00000000-0000-4000-8000-000000000011",
    name: "Synthetic second folio",
    institutionId: null,
    institutionName: null,
    accountType: "brokerage",
    accountClass: "asset",
    accountSubtype: null,
    currency: "XAA",
    country: "XZ",
    openedOn: "2026-01-01",
    closedOn: null,
    isActive: true,
    includeInNetWorth: true,
    isLiquid: false,
    openingBalance: "0.0000",
    openingBalanceDate: "2026-01-01",
    openingBalanceNote: null,
    openingBalanceConfirmedAt: "2026-01-01T00:00:00Z",
    currentBalance: "0.0000",
    currentBalanceAsOf: null,
    creditLimit: null,
    identifiers: [],
    ...overrides,
  };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(cleanup);

describe("InvestmentEventForm", () => {
  it("shows only the fields a cash distribution allows and sends exact strings, null for the rest", async () => {
    const posted: Array<unknown> = [];
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return EMPTY_POSITIONS;
      if (path === "/investment-events/cash-lifecycle" && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return { id: "evt-1" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "cash_distribution" } });

    expect(screen.queryByLabelText("Units")).toBeNull();
    expect(screen.queryByLabelText("Event price")).toBeNull();
    expect(screen.getByLabelText("Gross amount (XAA)")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Gross amount (XAA)"), { target: { value: "5.0000" } });
    fireEvent.change(screen.getByLabelText("Charges (XAA)"), { target: { value: "0.0000" } });
    fireEvent.change(screen.getByLabelText("Withheld tax (XAA)"), { target: { value: "0.5000" } });
    fireEvent.change(screen.getByLabelText("Net cash amount (XAA)"), { target: { value: "4.5000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      accountId: HOLDING.accountId, securityId: HOLDING.securityId, eventDate: "2026-02-10",
      eventKind: "cash_distribution", units: null, price: null, grossAmount: "5.0000",
      charges: "0.0000", withheldTax: "0.5000", netAmount: "4.5000", currency: "XAA",
    });
  });

  it("shows only bonus's own units field and posts to the unit-actions route", async () => {
    const posted: Array<unknown> = [];
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return EMPTY_POSITIONS;
      if (path === "/investment-events/unit-actions" && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return { kind: "bonus" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "bonus" } });

    expect(screen.queryByLabelText("Charges (XAA)")).toBeNull();
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Bonus units"), { target: { value: "2.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      kind: "bonus", accountId: HOLDING.accountId, securityId: HOLDING.securityId, eventDate: "2026-02-10",
      units: "2.00000000", currency: "XAA", idempotencyKey: expect.any(String),
    });
  });

  it("reuses the same idempotency key on a retry and creates a new one after editing a field", async () => {
    const posted: Array<{ idempotencyKey: string }> = [];
    let attempts = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return EMPTY_POSITIONS;
      if (path === "/investment-events/unit-actions" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { idempotencyKey: string };
        posted.push(body);
        attempts += 1;
        if (attempts < 2) throw new Error("network hiccup");
        return { kind: "bonus" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "bonus" } });
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Bonus units"), { target: { value: "2.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await screen.findByRole("button", { name: "Retry event" });
    fireEvent.click(screen.getByRole("button", { name: "Retry event" }));
    await vi.waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1].idempotencyKey).toBe(posted[0].idempotencyKey);

    // A successful submit already reset the draft; a fresh entry is a new draft and gets a new key.
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "bonus" } });
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-11" } });
    fireEvent.change(screen.getByLabelText("Bonus units"), { target: { value: "3.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));
    await vi.waitFor(() => expect(posted).toHaveLength(3));
    expect(posted[2].idempotencyKey).not.toBe(posted[0].idempotencyKey);
  });

  it("posts a split with only its own unitRatio field", async () => {
    const posted: Array<unknown> = [];
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return EMPTY_POSITIONS;
      if (path === "/investment-events/unit-actions" && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return { kind: "split" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "split" } });
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Unit ratio"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      kind: "split", accountId: HOLDING.accountId, securityId: HOLDING.securityId, eventDate: "2026-02-10",
      unitRatio: "2", idempotencyKey: expect.any(String),
    });
  });

  it("posts a switch's exact out and in leg strings, security chosen from another holding in the same account", async () => {
    const posted: Array<unknown> = [];
    const other = position();
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return { ...EMPTY_POSITIONS, positions: [other] };
      if (path === "/investment-events/unit-actions" && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return { kind: "switch" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "switch" } });
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(await screen.findByLabelText("Security switched into"), { target: { value: other.securityId } });
    fireEvent.change(screen.getByLabelText("Units out"), { target: { value: "-4.00000000" } });
    fireEvent.change(screen.getByLabelText("Price out"), { target: { value: "10.000000" } });
    fireEvent.change(screen.getByLabelText("Gross amount out (XAA)"), { target: { value: "40.0000" } });
    fireEvent.change(screen.getByLabelText("Units in"), { target: { value: "8.00000000" } });
    fireEvent.change(screen.getByLabelText("Price in"), { target: { value: "5.000000" } });
    fireEvent.change(screen.getByLabelText("Gross amount in (XAA)"), { target: { value: "40.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      kind: "switch", accountId: HOLDING.accountId, outSecurityId: HOLDING.securityId, inSecurityId: other.securityId,
      eventDate: "2026-02-10", outUnits: "-4.00000000", outPrice: "10.000000", outGrossAmount: "40.0000",
      inUnits: "8.00000000", inPrice: "5.000000", inGrossAmount: "40.0000", currency: "XAA",
      idempotencyKey: expect.any(String),
    });
  });

  it("shows a retryable error when positions fail to load, and clears it on retry", async () => {
    let attempts = 0;
    const request = requestFrom((path) => {
      if (path === "/portfolio/positions") {
        attempts += 1;
        if (attempts === 1) throw new Error("network hiccup");
        return EMPTY_POSITIONS;
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);

    const retry = await screen.findByRole("button", { name: "Try again" });
    fireEvent.click(retry);

    await vi.waitFor(() => expect(attempts).toBe(2));
    await vi.waitFor(() => expect(screen.queryByRole("button", { name: "Try again" })).toBeNull());
  });

  it("reloads positions after a successful record", async () => {
    let loads = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") {
        loads += 1;
        return EMPTY_POSITIONS;
      }
      if (path === "/investment-events/unit-actions" && init?.method === "POST") {
        return { kind: "bonus" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    await vi.waitFor(() => expect(loads).toBe(1));

    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "bonus" } });
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Bonus units"), { target: { value: "2.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await vi.waitFor(() => expect(loads).toBe(2));
  });

  it("posts a platform transfer's exact unit strings, receiving account chosen from the accounts list", async () => {
    const posted: Array<unknown> = [];
    const receiving = account();
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return EMPTY_POSITIONS;
      if (path === "/investment-events/unit-actions" && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return { kind: "platform_transfer" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[receiving]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "platform_transfer" } });
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Account transferred into"), { target: { value: receiving.id } });
    fireEvent.change(screen.getByLabelText("Units out"), { target: { value: "-6.00000000" } });
    fireEvent.change(screen.getByLabelText("Units in"), { target: { value: "6.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      kind: "platform_transfer", outAccountId: HOLDING.accountId, inAccountId: receiving.id,
      securityId: HOLDING.securityId, eventDate: "2026-02-10", outUnits: "-6.00000000", inUnits: "6.00000000",
      currency: "XAA", idempotencyKey: expect.any(String),
    });
  });
});
