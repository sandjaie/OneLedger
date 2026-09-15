// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
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
  const { identifiers, ...rest } = overrides;
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
    identifiers: identifiers ?? [
      {
        id: "identifier-bank",
        kind: "bank_account",
        masked: "****1234",
        canReveal: true,
        isActive: true,
      },
    ],
    ...rest,
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
  identifiers: [
    {
      id: "identifier-card",
      kind: "card",
      masked: "****4321",
      canReveal: false,
      isActive: true,
    },
  ],
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
  baseCurrency: "XIN",
  request: vi.fn() as unknown as typeof apiFetch,
  onChanged: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Accounts", () => {
  it("keeps identifiers masked and never prefetches a reveal", () => {
    const request = vi.fn();
    render(<AccountsScreen {...props} request={request} />);

    expect(screen.getAllByText("****1234").length).toBeGreaterThan(0);
    expect(screen.queryByText("SYN 0000 1234")).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });

  it("reveals one identifier deliberately and hides it without another request", async () => {
    const request = vi.fn().mockResolvedValue({
      status: "revealed",
      value: "SYN 0000 1234",
      kind: "bank_account",
    });
    render(<AccountsScreen {...props} accounts={[savings]} request={request} totals={[]} />);
    const show = screen.getByRole("button", {
      name: "Show bank account number for Inventory Savings",
    });
    show.focus();

    fireEvent.click(show);

    expect(await screen.findByText("SYN 0000 1234")).toBeTruthy();
    expect(request).toHaveBeenCalledWith(
      "/accounts/acct-savings/identifiers/identifier-bank/reveal",
      expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }),
    );
    const hide = screen.getByRole("button", {
      name: "Hide bank account number for Inventory Savings",
    });
    expect(document.activeElement).toBe(hide);
    fireEvent.click(hide);
    expect(screen.queryByText("SYN 0000 1234")).toBeNull();
    expect(screen.getByText("****1234")).toBeTruthy();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("reveals only the selected identifier and clears it on edit", async () => {
    const accountWithIban = account({
      identifiers: [
        {
          id: "identifier-bank",
          kind: "bank_account",
          masked: "****1234",
          canReveal: true,
          isActive: true,
        },
        {
          id: "identifier-iban",
          kind: "iban",
          masked: "****5678",
          canReveal: true,
          isActive: true,
        },
      ],
    });
    const request = vi.fn().mockResolvedValue({
      status: "revealed",
      value: "SYN 0000 1234",
      kind: "bank_account",
    });
    render(<AccountsScreen {...props} accounts={[accountWithIban]} request={request} totals={[]} />);

    fireEvent.click(screen.getByRole("button", {
      name: "Show bank account number for Inventory Savings",
    }));

    expect(await screen.findByText("SYN 0000 1234")).toBeTruthy();
    expect(screen.getByText("****5678")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit Inventory Savings" }));
    expect(screen.queryByText("SYN 0000 1234")).toBeNull();
  });

  it("cancels an in-flight reveal when edit tears down the identifier view", async () => {
    let finishReveal: ((value: unknown) => void) | undefined;
    const request = vi.fn().mockImplementation(() => new Promise((resolve) => {
      finishReveal = resolve;
    }));
    render(<AccountsScreen {...props} accounts={[savings]} request={request} totals={[]} />);

    fireEvent.click(screen.getByRole("button", {
      name: "Show bank account number for Inventory Savings",
    }));
    const init = request.mock.calls[0]?.[1] as RequestInit;
    expect(init.signal?.aborted).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Edit Inventory Savings" }));
    expect(init.signal?.aborted).toBe(true);
    finishReveal?.({ status: "revealed", value: "SYN 0000 1234", kind: "bank_account" });

    await waitFor(() => expect(screen.queryByText("SYN 0000 1234")).toBeNull());
  });

  it("explains a source-masked identifier and disables its eye", () => {
    render(<AccountsScreen {...props} accounts={[card]} totals={[]} />);

    const unavailable = screen.getByRole("button", {
      name: "Full card number unavailable for Inventory Card",
    }) as HTMLButtonElement;
    expect(unavailable.disabled).toBe(true);
    expect(screen.getByText("Statement supplied only a masked value.")).toBeTruthy();
  });

  it("handles unavailable and failed reveals without retaining plaintext", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({ status: "unavailable", reason: "source_mask_only" })
      .mockRejectedValueOnce(new Error("synthetic failure"));
    const { rerender } = render(
      <AccountsScreen {...props} accounts={[savings]} request={request} totals={[]} />,
    );
    fireEvent.click(screen.getByRole("button", {
      name: "Show bank account number for Inventory Savings",
    }));
    expect(await screen.findByText("Statement supplied only a masked value.")).toBeTruthy();
    expect(screen.queryByText("SYN 0000 1234")).toBeNull();

    rerender(
      <AccountsScreen
        {...props}
        accounts={[account({ identifiers: [{
          ...savings.identifiers[0],
          id: "identifier-retry",
        }] })]}
        request={request}
        totals={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", {
      name: "Show bank account number for Inventory Savings",
    }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The account number could not be revealed.",
    );
    expect(screen.queryByText("SYN 0000 1234")).toBeNull();
  });

  it("does not put a revealed value in browser persistence or navigation state", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const pushState = vi.spyOn(window.history, "pushState");
    const replaceState = vi.spyOn(window.history, "replaceState");
    const request = vi.fn().mockResolvedValue({
      status: "revealed",
      value: "SYN 0000 1234",
      kind: "bank_account",
    });
    const view = render(
      <AccountsScreen {...props} accounts={[savings]} request={request} totals={[]} />,
    );

    fireEvent.click(screen.getByRole("button", {
      name: "Show bank account number for Inventory Savings",
    }));
    expect(await screen.findByText("SYN 0000 1234")).toBeTruthy();
    expect(storage).not.toHaveBeenCalled();
    expect(pushState).not.toHaveBeenCalled();
    expect(replaceState).not.toHaveBeenCalled();
    view.unmount();
    expect(screen.queryByText("SYN 0000 1234")).toBeNull();
  });

  it("presents the inventory as the compact account-card grid from the primary artboard", () => {
    render(<AccountsScreen {...props} />);

    expect(screen.getByRole("heading", { level: 2, name: "Accounts" })).toBeTruthy();
    expect(screen.getByText("2 active accounts · latest balance 15 Jan 2026")).toBeTruthy();

    const assetGroup = screen.getByRole("heading", { name: "Assets · XB" }).closest(".account-group");
    expect(assetGroup?.querySelector("tbody")?.classList.contains("account-card-grid")).toBe(true);
    expect(assetGroup?.querySelector("tbody tr")?.classList.contains("account-card")).toBe(true);
  });

  it("labels the newest balance date without implying that every account was synced", () => {
    render(
      <AccountsScreen
        {...props}
        accounts={[
          savings,
          account({
            id: "acct-older",
            name: "Older balance",
            currentBalanceAsOf: "2025-12-01T00:00:00Z",
          }),
        ]}
      />,
    );

    expect(screen.getByText("2 active accounts · latest balance 15 Jan 2026")).toBeTruthy();
    expect(screen.queryByText(/last synced/i)).toBeNull();
  });

  it("scopes the card-table display override to account groups", () => {
    const css = readFileSync(`${process.cwd()}/app/globals.css`, "utf8");
    expect(css).not.toMatch(/(?:^|\n)\.account-table\s*\{\s*display:\s*block/);
    expect(css).toMatch(/\.account-group \.account-table\s*\{\s*display:\s*block/);
  });

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

  it("keeps a signed opening when a liability is reclassified as an asset", async () => {
    const request = vi.fn().mockResolvedValue(card);
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Inventory Card" }));
    expect((screen.getByLabelText("Amount owed") as HTMLInputElement).value).toBe("4500.0000");
    fireEvent.change(screen.getByLabelText("Account type"), { target: { value: "current" } });
    fireEvent.click(screen.getByRole("button", { name: "Save account" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = JSON.parse(request.mock.calls[0][1].body as string) as { openingBalance: string };
    expect(body.openingBalance).toBe("-4500.0000");
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

  it("submits an owner-entered complete identifier only as accountNumber", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      void path;
      void init;
      return savings;
    });
    render(
      <AccountsScreen
        {...props}
        request={request as unknown as typeof props.request}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Account or card number"), {
      target: { value: "4111 1111 1111 1111" },
    });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "10.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const payload = JSON.parse(request.mock.calls[0][1]?.body as string);
    expect(payload.accountNumber).toBe("4111 1111 1111 1111");
    expect(payload).not.toHaveProperty("accountNumberMasked");
  });

  it("disables edit while a row mutation is pending", async () => {
    const request = vi.fn(() => new Promise(() => undefined));
    render(<AccountsScreen {...props} request={request as unknown as typeof props.request} />);
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge Inventory Savings" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect((screen.getByRole("button", { name: "Edit Inventory Savings" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps row actions disabled until the inventory refreshes", async () => {
    const request = vi.fn().mockResolvedValue(savings);
    const { rerender } = render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge Inventory Savings" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
    expect((screen.getByRole("button", { name: "Edit Inventory Savings" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Acknowledge Inventory Savings" }) as HTMLButtonElement).disabled).toBe(true);
    rerender(
      <AccountsScreen
        {...props}
        request={request}
        accounts={[account({ openingBalanceConfirmedAt: "2026-01-01T00:00:00Z" }), card, closed]}
      />,
    );
    await waitFor(() => {
      expect((screen.getByRole("button", { name: "Edit Inventory Savings" }) as HTMLButtonElement).disabled).toBe(false);
    });
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
