// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountsScreen } from "@/app/accounts/accounts-screen";
import type { apiFetch } from "@/lib/api/client";
import type { AccountRow, CurrencyRow, InstitutionRow } from "@/types/api";

const currencies: Array<CurrencyRow> = [
  { code: "XAE", name: "Synthetic A", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XIN", name: "Synthetic I", symbol: "I", decimals: 2, numberFormat: "indian" },
  { code: "XZZ", name: "Synthetic Z", symbol: "Z", decimals: 3, numberFormat: "international" },
];

const institutions: Array<InstitutionRow> = [
  { id: "inst-1", name: "Synthetic Bank", shortName: "SB", country: "XB", institutionType: "bank" },
];

function account(overrides: Partial<AccountRow>): AccountRow {
  return {
    id: "acct-savings",
    name: "Inventory Savings",
    institutionId: "inst-1",
    institutionName: "Synthetic Bank",
    accountType: "savings",
    accountClass: "asset",
    accountSubtype: "NRE",
    currency: "XIN",
    country: "XB",
    accountNumberMasked: "****1234",
    openedOn: "2024-01-01",
    closedOn: null,
    isActive: true,
    includeInNetWorth: true,
    isLiquid: true,
    openingBalance: "250000.0000",
    openingBalanceDate: "2024-01-01",
    openingBalanceNote: null,
    openingBalanceConfirmedAt: null,
    currentBalance: "250000.0000",
    currentBalanceAsOf: "2026-01-15T00:00:00Z",
    creditLimit: null,
    ...overrides,
  };
}

const savings = account({});
const card = account({
  id: "acct-card",
  name: "Inventory Card",
  accountType: "credit_card",
  accountClass: "liability",
  accountSubtype: null,
  currency: "XAE",
  country: "XA",
  openingBalance: "-4500.0000",
  currentBalance: "-4500.0000",
  openingBalanceConfirmedAt: "2026-01-01T00:00:00Z",
  creditLimit: "20000.0000",
});
const closed = account({
  id: "acct-old",
  name: "Old Savings",
  isActive: false,
  closedOn: "2026-06-30",
  openingBalance: "100.0000",
  currentBalance: "100.0000",
  openingBalanceConfirmedAt: "2024-01-01T00:00:00Z",
});

const props = {
  accounts: [savings, card, closed],
  totals: [
    { accountClass: "asset" as const, country: "XB", currency: "XIN", total: "250000.0000" },
    { accountClass: "liability" as const, country: "XA", currency: "XAE", total: "-4500.0000" },
  ],
  institutions,
  currencies,
  request: vi.fn() as unknown as typeof apiFetch,
  onChanged: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Accounts", () => {
  it("groups by class then country, as UI §6 specifies over the artboard", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent))
      .toEqual(["Assets · XB", "Liabilities · XA"]);
  });

  it("shows a native balance formatted by its own currency row", () => {
    render(<AccountsScreen {...props} />);
    // XIN declares Indian grouping and 2 decimals in the catalogue. Letter symbols keep a gap.
    expect(screen.getByLabelText("Inventory Savings balance I 2,50,000.00")).toBeTruthy();
    // A liability reads as what is owed while the stored value stays negative.
    expect(screen.getByLabelText("Inventory Card balance You owe A 4,500.00")).toBeTruthy();
  });

  it("keeps closed accounts visible with their closing date", () => {
    render(<AccountsScreen {...props} />);
    const closedRow = screen.getByRole("row", { name: /Old Savings/ });
    expect(closedRow.textContent).toContain("Closed 30 Jun 2026");
  });

  it("marks an unacknowledged opening balance as stated, not verified", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getByText("Opening balance not acknowledged")).toBeTruthy();
  });

  it("teaches rather than showing a blank table when there are no accounts", () => {
    render(<AccountsScreen {...props} accounts={[]} totals={[]} />);
    expect(screen.getByRole("heading", { name: /first account/i })).toBeTruthy();
  });

  it("offers every catalogue currency and every institution when adding an account", () => {
    render(<AccountsScreen {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    const currency = screen.getByLabelText("Currency") as HTMLSelectElement;
    expect([...currency.options].map((o) => o.value)).toEqual(["XAE", "XIN", "XZZ"]);
    expect(screen.getByLabelText("Institution")).toBeTruthy();
    expect(screen.getByRole("button", { name: /add institution/i })).toBeTruthy();
  });

  it("offers every database-supported institution type", () => {
    render(<AccountsScreen {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.click(screen.getByRole("button", { name: /add institution/i }));
    const type = screen.getByLabelText("Institution type") as HTMLSelectElement;
    expect([...type.options].map((option) => option.value)).toEqual([
      "bank",
      "card_issuer",
      "broker",
      "fund_house",
      "lender",
      "govt",
      "insurer",
      "employer",
      "exchange_house",
      "other",
    ]);
  });

  it("lets the owner correct type, currency, country and the opening anchor", () => {
    render(<AccountsScreen {...props} />);
    fireEvent.click(screen.getByRole("button", { name: /edit inventory savings/i }));
    expect(screen.getByLabelText("Account type")).toBeTruthy();
    expect(screen.getByLabelText("Currency")).toBeTruthy();
    expect(screen.getByLabelText("Country")).toBeTruthy();
    expect(screen.getByLabelText("Opening balance")).toBeTruthy();
    expect(screen.getByLabelText("Opening balance date")).toBeTruthy();
    expect(screen.getByLabelText("Credit limit")).toBeTruthy();
  });

  it("requires a credit limit when the type is a credit card", () => {
    render(<AccountsScreen {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    const limit = screen.getByLabelText("Credit limit") as HTMLInputElement;
    expect(limit.required).toBe(false);
    fireEvent.change(screen.getByLabelText("Account type"), { target: { value: "credit_card" } });
    expect((screen.getByLabelText("Credit limit") as HTMLInputElement).required).toBe(true);
  });

  it("names every account-table column for screen-reader navigation", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Institution",
      "Account",
      "Detail",
      "Masked number",
      "Balance",
      "Status",
      "Evidence",
      "Actions",
      "Institution",
      "Account",
      "Detail",
      "Masked number",
      "Balance",
      "Status",
      "Evidence",
      "Actions",
      "Institution",
      "Account",
      "Detail",
      "Masked number",
      "Balance",
      "Status",
      "Evidence",
      "Actions",
    ]);
  });

  it("sends the opening balance as a decimal string", async () => {
    const request = vi.fn().mockResolvedValue(props.accounts[0]);
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "1234.50" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = JSON.parse(request.mock.calls[0][1].body as string) as { openingBalance: string };
    expect(body.openingBalance).toBe("1234.50");
    expect(typeof body.openingBalance).toBe("string");
  });

  it("sends a liability opening as a negative decimal string", async () => {
    const request = vi.fn().mockResolvedValue(props.accounts[1]);
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "Card" } });
    fireEvent.change(screen.getByLabelText("Account type"), { target: { value: "credit_card" } });
    fireEvent.change(screen.getByLabelText("Amount owed"), { target: { value: "4500.00" } });
    fireEvent.change(screen.getByLabelText("Credit limit"), { target: { value: "20000.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = JSON.parse(request.mock.calls[0][1].body as string) as { openingBalance: string };
    expect(body.openingBalance).toBe("-4500.00");
  });

  it("keeps edit and reopen on a closed account", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getByRole("button", { name: /edit old savings/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /reopen/i })).toBeTruthy();
  });

  it("names acknowledge after the account it confirms", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getByRole("button", { name: "Acknowledge Inventory Savings" })).toBeTruthy();
  });

  it("names close and reopen after the account they mutate", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getByRole("button", { name: "Close Inventory Savings" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Close Inventory Card" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reopen Old Savings" })).toBeTruthy();
  });

  it("keeps the minus on a negative asset subtotal", () => {
    render(
      <AccountsScreen
        {...props}
        totals={[
          { accountClass: "asset", country: "XB", currency: "XIN", total: "-250000.0000" },
          { accountClass: "liability", country: "XA", currency: "XAE", total: "-4500.0000" },
        ]}
      />,
    );
    const assets = screen.getByRole("heading", { name: "Assets · XB" }).closest(".account-group-head");
    expect(assets?.textContent).toContain("-I 2,50,000.00");
    const liabilities = screen.getByRole("heading", { name: "Liabilities · XA" }).closest(".account-group-head");
    expect(liabilities?.textContent).toContain("A 4,500.00 owed");
    expect(liabilities?.textContent).not.toContain("-A");
  });

  it("separates native subtotals when a group has more than one currency", () => {
    render(
      <AccountsScreen
        {...props}
        totals={[
          ...props.totals,
          { accountClass: "asset", country: "XB", currency: "XAE", total: "1000.0000" },
        ]}
      />,
    );
    const head = screen.getByRole("heading", { name: "Assets · XB" }).closest(".account-group-head");
    expect(head?.textContent).toContain("I 2,50,000.00 as of 15 Jan 2026 · A 1,000.00");
  });

  it("does not transmit a full account number", async () => {
    const request = vi.fn();
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Masked number"), { target: { value: "4111111111111111" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "10.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/mask/i));
    expect(request).not.toHaveBeenCalled();
  });

  it("does not transmit a full number written with Unicode digits", async () => {
    const request = vi.fn();
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Masked number"), { target: { value: "٤١١١١١١١١١١١١١١١" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "10.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/mask/i));
    expect(request).not.toHaveBeenCalled();
  });

  it("does not transmit a full number written with compatibility digits", async () => {
    const request = vi.fn();
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Masked number"), { target: { value: "፩፪፫፬፭" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "10.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/mask/i));
    expect(request).not.toHaveBeenCalled();
  });

  it("does not transmit a full number written with Ethiopic digits", async () => {
    const request = vi.fn();
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Masked number"), { target: { value: "፩፪፫፬፭" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "10.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/mask/i));
    expect(request).not.toHaveBeenCalled();
  });

  it("disables edit while a row mutation is pending", async () => {
    const request = vi.fn(() => new Promise(() => undefined));
    render(<AccountsScreen {...props} request={request as unknown as typeof props.request} />);
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge Inventory Savings" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect((screen.getByRole("button", { name: "Edit Inventory Savings" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a positive liability balance as a credit, not as amount owed", () => {
    render(
      <AccountsScreen
        {...props}
        accounts={[account({
          ...card,
          currentBalance: "250.0000",
          openingBalanceConfirmedAt: "2026-01-01T00:00:00Z",
        })]}
        totals={[{ accountClass: "liability", country: "XA", currency: "XAE", total: "250.0000" }]}
      />,
    );
    expect(screen.getByLabelText("Inventory Card balance A 250.00")).toBeTruthy();
    const head = screen.getByRole("heading", { name: "Liabilities · XA" }).closest(".account-group-head");
    expect(head?.textContent).toContain("A 250.00");
    expect(head?.textContent).not.toContain("owed");
  });

  it("omits the subtotal date when an included account has none", () => {
    render(
      <AccountsScreen
        {...props}
        accounts={[
          account({ currentBalanceAsOf: "2026-01-15T00:00:00Z" }),
          account({ id: "acct-undated", name: "Undated", currentBalanceAsOf: null }),
        ]}
        totals={[{ accountClass: "asset", country: "XB", currency: "XIN", total: "250100.0000" }]}
      />,
    );
    const head = screen.getByRole("heading", { name: "Assets · XB" }).closest(".account-group-head");
    expect(head?.textContent).toContain("I 2,50,100.00");
    expect(head?.textContent).not.toContain("as of");
  });

  it("marks an account that is excluded from group totals", () => {
    render(
      <AccountsScreen
        {...props}
        accounts={[account({ id: "acct-off", name: "Off books", includeInNetWorth: false })]}
        totals={[]}
      />,
    );
    expect(screen.getByRole("row", { name: /Off books/ }).textContent).toContain("Excluded from totals");
  });

  it("drops a leftover credit limit when the type is not a card", async () => {
    const request = vi.fn().mockResolvedValue(props.accounts[0]);
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Credit limit"), { target: { value: "20000.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "10.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = JSON.parse(request.mock.calls[0][1].body as string) as { creditLimit: string | null };
    expect(body.creditLimit).toBeNull();
  });
});
