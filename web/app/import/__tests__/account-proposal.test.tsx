// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountProposalPanel } from "@/app/import/account-proposal";
import { ApiError } from "@/lib/api/client";
import type {
  AccountProposal,
  AccountRow,
  CurrencyRow,
  InstitutionRow,
} from "@/types/api";

const currencies: Array<CurrencyRow> = [
  {
    code: "XAE",
    name: "Synthetic A",
    symbol: "A",
    decimals: 2,
    numberFormat: "international",
  },
];

const institutions: Array<InstitutionRow> = [
  {
    id: "institution-1",
    name: "Example Institution",
    shortName: "Example",
    country: "XY",
    institutionType: "bank",
  },
];

function proposal(overrides: Partial<AccountProposal> = {}): AccountProposal {
  return {
    accountNumberMasked: "****1234",
    suggestedAccounts: [],
    name: "Example savings",
    accountType: "savings",
    institutionId: "institution-1",
    country: "XY",
    currency: "XAE",
    openingBalance: "1490.0000",
    openingBalanceIsDerived: false,
    openingBalanceDate: "2026-03-01",
    openingBalanceNote: "Declared opening balance for the statement period",
    creditLimit: null,
    ...overrides,
  };
}

function account(): AccountRow {
  return {
    id: "account-created",
    name: "Example savings",
    institutionId: "institution-1",
    institutionName: "Example Institution",
    accountType: "credit_card",
    accountSubtype: null,
    accountClass: "liability",
    currency: "XAE",
    country: "XY",
    accountNumberMasked: "****1234",
    openedOn: null,
    closedOn: null,
    includeInNetWorth: true,
    isLiquid: true,
    openingBalance: "-1490.0000",
    openingBalanceDate: "2026-03-01",
    openingBalanceNote: "Declared opening balance for the statement period",
    openingBalanceConfirmedAt: null,
    creditLimit: "5000.0000",
    currentBalance: "-1490.0000",
    currentBalanceAsOf: "2026-03-01",
    isActive: true,
    identifiers: [],
  };
}

afterEach(cleanup);

describe("an account proposed by a statement", () => {
  it("renders and formats the prefill, then posts money as strings without identity fields", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/institutions") return institutions;
      if (path === "/parse/account") return account();
      throw new Error(`Unexpected path ${path} ${init?.method ?? "GET"}`);
    });
    const onResolved = vi.fn();
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        layoutId="layout-1"
        onResolved={onResolved}
        password="secret"
        proposal={proposal({
          accountType: "credit_card",
          openingBalance: "-1490.0000",
          creditLimit: "5000.0000",
        })}
        request={request as never}
      />,
    );

    expect(screen.getByDisplayValue("Example savings")).toBeTruthy();
    expect(screen.getByText("****1234")).toBeTruthy();
    expect(screen.getByText("-A 1,490.00")).toBeTruthy();
    expect(screen.getByText("A 5,000.00")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create account and continue" }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledWith(account()));
    const call = request.mock.calls.find(([path]) => path === "/parse/account");
    const form = call?.[1]?.body as FormData;
    expect(form.get("password")).toBe("secret");
    expect(form.get("layoutId")).toBe("layout-1");
    expect(form.get("accountId")).toBeNull();
    const sent = JSON.parse(form.get("account") as string);
    expect(sent).toMatchObject({
      openingBalance: "-1490.0000",
      creditLimit: "5000.0000",
      currency: "XAE",
    });
    expect(sent).not.toHaveProperty("accountNumber");
    expect(sent).not.toHaveProperty("accountNumberMasked");
  });

  it("keeps create disabled until every required null prefill is supplied", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return institutions;
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onResolved={vi.fn()}
        proposal={proposal({ accountType: null, country: null, currency: null, openingBalance: null })}
        request={request as never}
      />,
    );

    const create = screen.getByRole<HTMLButtonElement>("button", {
      name: "Create account and continue",
    });
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Account type (required)"), {
      target: { value: "savings" },
    });
    fireEvent.change(screen.getByLabelText("Currency (required)"), {
      target: { value: "XAE" },
    });
    fireEvent.change(screen.getByLabelText("Country (required)"), {
      target: { value: "XY" },
    });
    fireEvent.change(screen.getByLabelText(/^Opening balance \(required\)/), {
      target: { value: "0.0000" },
    });
    expect(create.disabled).toBe(false);
  });

  it("does not offer a create body that the account contract would refuse", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return institutions;
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onResolved={vi.fn()}
        proposal={proposal({
          accountType: "credit_card",
          openingBalance: "1490.0000",
          creditLimit: null,
        })}
        request={request as never}
      />,
    );
    const create = screen.getByRole<HTMLButtonElement>("button", {
      name: "Create account and continue",
    });

    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^Opening balance \(required\)/), {
      target: { value: "-1490.0000" },
    });
    const creditLimit = screen.getByLabelText(/^Credit limit/);
    fireEvent.change(creditLimit, {
      target: { value: "5000.00000" },
    });
    expect(create.disabled).toBe(true);
    fireEvent.change(creditLimit, {
      target: { value: "5000.0000" },
    });
    expect(create.disabled).toBe(false);

    fireEvent.change(screen.getByLabelText("Account type (required)"), {
      target: { value: "savings" },
    });
    expect(create.disabled).toBe(true);
    fireEvent.change(creditLimit, { target: { value: "" } });
    expect(create.disabled).toBe(false);
  });

  it("adopts a suggested account and shows the service's own conflict sentence", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/institutions") return institutions;
      if (path === "/parse/account") throw new ApiError(409, "That identity belongs elsewhere.");
      throw new Error(`Unexpected path ${path} ${init?.method ?? "GET"}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onResolved={vi.fn()}
        proposal={proposal({
          suggestedAccounts: [
            { id: "account-existing", name: "Existing savings", accountNumberMasked: "****1234" },
          ],
        })}
        request={request as never}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "This is Existing savings ****1234" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "That identity belongs elsewhere.",
    );
    const call = request.mock.calls.find(([path]) => path === "/parse/account");
    const form = call?.[1]?.body as FormData;
    expect(form.get("accountId")).toBe("account-existing");
    expect(form.get("account")).toBeNull();
  });
});
