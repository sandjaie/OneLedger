// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Register } from "@/app/plans/register";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AccountRow, CurrencyRow, RegisterResponse, RegisterRow } from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

function account(overrides: Partial<AccountRow> = {}): AccountRow {
  return {
    id: "00000000-0000-4000-8000-0000000000a1",
    name: "Synthetic asset account",
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
    ...overrides,
  };
}

const depositAccount = account({
  id: "00000000-0000-4000-8000-0000000000a2",
  name: "Synthetic fixed deposit",
  accountType: "fixed_deposit",
});

function record(overrides: Partial<RegisterRow> = {}): RegisterRow {
  return {
    id: "00000000-0000-4000-8000-0000000000r1",
    kind: "insurance_policy",
    title: "Synthetic policy",
    counterparty: "Synthetic insurer",
    covered: "Synthetic car",
    amount: "500000.0000",
    currency: "XAA",
    premiumAmount: "1200.0000",
    premiumCadence: "annual",
    keyDate: "2027-01-01",
    accountId: null,
    accountName: null,
    instruction: null,
    nomineeRecorded: null,
    lastReviewedOn: null,
    reviewDue: false,
    completedAt: null,
    archivedAt: null,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function response(overrides: Partial<RegisterResponse> = {}): RegisterResponse {
  return { records: [record()], ...overrides };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("register", () => {
  it("shows loading, then groups a record under its kind heading", async () => {
    const request = requestFrom((path) => {
      if (path === "/register?includeArchived=true") return response();
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Register accounts={[account()]} currencies={currencies} request={request} />);

    expect(screen.getByText("Loading the register…")).toBeTruthy();

    expect(await screen.findByRole("heading", { name: "Insurance policies" })).toBeTruthy();
    expect(screen.getByText(/Synthetic policy/)).toBeTruthy();
    expect(screen.getByText(/Renews 2027-01-01/)).toBeTruthy();
  });

  it("shows the error state and lets the owner try again", async () => {
    const request = requestFrom(() => {
      throw new ApiError(500, "boom");
    });
    render(<Register accounts={[account()]} currencies={currencies} request={request} />);
    expect(await screen.findByText("The register could not be loaded.")).toBeTruthy();
  });

  it("posts an insurance_policy body with only its own fields", async () => {
    const request = requestFrom((path, init) => {
      if (path === "/register?includeArchived=true") return response({ records: [] });
      if (path === "/register" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toMatchObject({
          kind: "insurance_policy",
          title: "Synthetic new policy",
          keyDate: "2027-06-01",
          premiumAmount: "100.0000",
          premiumCadence: "annual",
          currency: "XAA",
          accountId: null,
          instruction: null,
          nomineeRecorded: null,
        });
        expect(typeof body.idempotencyKey).toBe("string");
        return record({ title: "Synthetic new policy" });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Register accounts={[account()]} currencies={currencies} request={request} />);
    await screen.findByText("No records are kept yet.");

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Synthetic new policy" } });
    fireEvent.change(screen.getByLabelText("Premium amount"), { target: { value: "100.0000" } });
    fireEvent.change(screen.getByLabelText("Renewal date"), { target: { value: "2027-06-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Add record" }));

    expect(await screen.findByText("Record added.")).toBeTruthy();
  });

  it("switches to the nominee kind's own fields and filters the account picker for a deposit", async () => {
    const request = requestFrom((path) => {
      if (path === "/register?includeArchived=true") return response({ records: [] });
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(
      <Register
        accounts={[account(), depositAccount]}
        currencies={currencies}
        request={request}
      />,
    );
    await screen.findByText("No records are kept yet.");

    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "nominee" } });
    expect(screen.queryByLabelText("Coverage amount")).toBeNull();
    expect(screen.getByLabelText("Nominee recorded")).toBeTruthy();
    const nomineeAccountOptions = screen.getAllByRole("option");
    expect(nomineeAccountOptions.some((option) => option.textContent === depositAccount.name)).toBe(
      true,
    );

    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "deposit" } });
    expect(screen.queryByRole("option", { name: account().name })).toBeNull();
    expect(screen.getByRole("option", { name: depositAccount.name })).toBeTruthy();
  });

  it("marks a policy reviewed", async () => {
    let reviewed = false;
    const request = requestFrom((path) => {
      if (path === "/register?includeArchived=true") {
        return response({ records: [record({ lastReviewedOn: reviewed ? "2026-06-01" : null })] });
      }
      if (path === "/register/00000000-0000-4000-8000-0000000000r1/reviewed") {
        reviewed = true;
        return record({ lastReviewedOn: "2026-06-01" });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Register accounts={[account()]} currencies={currencies} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark reviewed" }));
    expect(await screen.findByText(/Reviewed 2026-06-01/)).toBeTruthy();
  });

  it("renews a policy via the inline date form", async () => {
    let keyDate = "2027-01-01";
    const request = requestFrom((path, init) => {
      if (path === "/register?includeArchived=true") return response({ records: [record({ keyDate })] });
      if (path === "/register/00000000-0000-4000-8000-0000000000r1/renewed") {
        const body = JSON.parse(init?.body as string);
        expect(body).toEqual({ nextRenewalDate: "2028-01-01" });
        keyDate = "2028-01-01";
        return record({ keyDate });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Register accounts={[account()]} currencies={currencies} request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: "Renewed" }));
    fireEvent.change(screen.getByLabelText(/Next renewal date/), {
      target: { value: "2028-01-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm renewal" }));
    expect(await screen.findByText(/Renews 2028-01-01/)).toBeTruthy();
  });

  it("completes then reopens a claim", async () => {
    let completedAt: string | null = null;
    const request = requestFrom((path) => {
      if (path === "/register?includeArchived=true") {
        return response({ records: [record({ kind: "claim", keyDate: "2026-01-01", completedAt })] });
      }
      if (path === "/register/00000000-0000-4000-8000-0000000000r1/complete") {
        completedAt = "2026-06-01T00:00:00Z";
        return record({ kind: "claim", keyDate: "2026-01-01", completedAt });
      }
      if (path === "/register/00000000-0000-4000-8000-0000000000r1/reopen") {
        completedAt = null;
        return record({ kind: "claim", keyDate: "2026-01-01", completedAt });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Register accounts={[account()]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "Complete" }));
    const reopenButton = await screen.findByRole("button", { name: "Reopen" });
    expect(screen.getByText(/Completed/)).toBeTruthy();

    fireEvent.click(reopenButton);
    expect(await screen.findByRole("button", { name: "Complete" })).toBeTruthy();
  });

  it("archives a record and hides its resolve controls", async () => {
    let archivedAt: string | null = null;
    const request = requestFrom((path) => {
      if (path === "/register?includeArchived=true") return response({ records: [record({ archivedAt })] });
      if (path === "/register/00000000-0000-4000-8000-0000000000r1/archive") {
        archivedAt = "2026-06-01T00:00:00Z";
        return record({ archivedAt });
      }
      throw new ApiError(500, `unexpected ${path}`);
    });
    render(<Register accounts={[account()]} currencies={currencies} request={request} />);

    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));
    expect(await screen.findByText(/Archived/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });
});
