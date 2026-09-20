// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountsScreen } from "@/app/accounts/accounts-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AccountRow, CurrencyRow, DashboardResponse, HouseholdCurrency, InstitutionRow } from "@/types/api";

const currencies: Array<CurrencyRow> = [
  { code: "XAE", name: "Synthetic A", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XIN", name: "Synthetic I", symbol: "I", decimals: 2, numberFormat: "indian" },
  { code: "XZZ", name: "Synthetic Z", symbol: "Z", decimals: 3, numberFormat: "international" },
];

const institutions: Array<InstitutionRow> = [
  { id: "inst-1", name: "Synthetic Bank", shortName: "SB", country: "XB", institutionType: "bank" },
];

const reporting: HouseholdCurrency = { code: "XIN", symbol: "I", decimals: 2, numberFormat: "indian" };

// Accounts reads `/dashboard` for its reporting-pair strip and for the account ids a typed
// coverage response already names. Every test here is about the rest of the screen, so the
// wrapper answers that one path and delegates everything else to the test's own mock.
const dashboardBody = {
  netWorth: {
    asOf: "2026-01-15",
    baseCurrency: "XIN",
    secondaryCurrency: "XAE",
    totalBase: "245500.0000",
    totalSecondary: "11047.5000",
    assetsBase: "250000.0000",
    assetsSecondary: "11250.0000",
    liabilitiesBase: "-4500.0000",
    liabilitiesSecondary: "-202.5000",
    liquidBase: "245500.0000",
    liquidSecondary: "11047.5000",
    rates: [],
    missing: [],
    investmentCoverage: [],
  },
} as unknown as DashboardResponse;

/** Opens an account's contextual menu if it is closed, then returns the named action inside it. */
function rowAction(accountName: string, action: string | RegExp) {
  const trigger = screen.getByRole("button", { name: `Actions for ${accountName}` });
  if (trigger.getAttribute("aria-expanded") !== "true") fireEvent.click(trigger);
  return screen.getByRole("button", { name: action });
}

function withDashboard(inner: unknown, body: DashboardResponse = dashboardBody): typeof apiFetch {
  return ((path: string, init?: unknown) =>
    path === "/dashboard"
      ? Promise.resolve(body)
      : (inner as (p: string, i?: unknown) => unknown)(path, init)) as unknown as typeof apiFetch;
}

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
const retirementNoSnapshot = account({
  id: "acct-retirement",
  name: "Inventory Retirement",
  accountType: "retirement",
  accountSubtype: null,
  valuationMode: "snapshot",
  latestValuationDate: null,
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
  currency: reporting,
  accounts: [savings, card, closed],
  totals: [
    { accountClass: "asset" as const, country: "XB", currency: "XIN", total: "250000.0000" },
    { accountClass: "liability" as const, country: "XA", currency: "XAE", total: "-4500.0000" },
  ],
  institutions,
  currencies,
  baseCurrency: "XIN",
  request: withDashboard(vi.fn()),
  onChanged: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Accounts", () => {
  it("keeps the Accounts page title and a clear next action in the empty state", () => {
    render(<AccountsScreen {...props} accounts={[]} totals={[]} />);
    expect(screen.getByRole("heading", { level: 1, name: "Accounts" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Add your first account" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add account" })).toBeTruthy();
  });

  it("states the reporting pair from the dashboard and keeps native totals separate", async () => {
    render(<AccountsScreen {...props} />);

    const summary = await screen.findByRole("region", { name: "Position summary" });
    // The reporting-pair strip follows the top-bar currency; XIN is the base here.
    expect(within(summary).getByText("I 2,50,000.00")).toBeTruthy();
    expect(within(summary).getByText("Across 1 asset account")).toBeTruthy();
    expect(within(summary).getByText("I 4,500.00")).toBeTruthy();
    expect(within(summary).getByText("Owed across 1 liability account")).toBeTruthy();
    // The grouped subtotals stay in each group's own native currency, never converted.
    const totals = within(screen.getByRole("region", { name: "Grouped native totals table" }));
    expect(totals.getByRole("row", { name: /Liabilities · XA/ }).textContent).toContain("A 4,500.00 owed");
  });

  it("follows the top-bar currency into the reporting strip", async () => {
    render(
      <AccountsScreen
        {...props}
        currency={{ code: "XAE", symbol: "A", decimals: 2, numberFormat: "international" }}
      />,
    );

    const summary = await screen.findByRole("region", { name: "Position summary" });
    expect(within(summary).getByText("A 11,250.00")).toBeTruthy();
    expect(within(summary).getByText("A 202.50")).toBeTruthy();
  });

  it("withholds a reporting total the dashboard could not state, keeping native evidence", async () => {
    const withheld = {
      netWorth: {
        ...dashboardBody.netWorth,
        assetsBase: null,
        assetsSecondary: null,
        liabilitiesBase: null,
        liabilitiesSecondary: null,
      },
    } as unknown as DashboardResponse;
    render(<AccountsScreen {...props} request={withDashboard(vi.fn(), withheld)} />);

    const summary = await screen.findByRole("region", { name: "Position summary" });
    expect(within(summary).getAllByText("Not available")).toHaveLength(2);
    expect(within(summary).getAllByText("Not stated in XIN for this date")).toHaveLength(2);
    expect(within(summary).queryByText(/2,50,000/)).toBeNull();
    // The native grouped totals are unaffected: they never needed a rate.
    expect(within(screen.getByRole("region", { name: "Grouped native totals table" }))
      .getByRole("row", { name: /Assets · XB/ }).textContent).toContain("I 2,50,000.00");
  });

  it("counts the accounts needing an evidence action and filters to exactly those", async () => {
    const unvalued = account({ ...retirementNoSnapshot, openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" });
    render(<AccountsScreen {...props} accounts={[savings, unvalued, card]} />);

    const summary = await screen.findByRole("region", { name: "Position summary" });
    expect(within(summary).getByText("2 accounts")).toBeTruthy();
    expect(within(summary).getByText("1 opening · 1 snapshot")).toBeTruthy();

    const attention = screen.getByRole("region", { name: "2 accounts need an evidence action" });
    fireEvent.click(within(attention).getByRole("button", { name: "Show accounts needing evidence" }));

    expect(screen.getByText(/^2 of 3 open accounts/)).toBeTruthy();
    const table = within(screen.getByRole("region", { name: "Open accounts by role table" }));
    expect(table.getByRole("row", { name: /Inventory Savings/ })).toBeTruthy();
    expect(table.getByRole("row", { name: /Inventory Retirement/ })).toBeTruthy();
    expect(table.queryByRole("row", { name: /Inventory Card/ })).toBeNull();
  });

  it("names a typed coverage problem the dashboard already reported", async () => {
    const flagged = {
      netWorth: {
        ...dashboardBody.netWorth,
        investmentCoverage: [
          { accountId: "acct-card", holdingId: null, valuationMode: "snapshot", status: "stale_price", valueDate: "2025-01-01", carried: true, ageDays: 400, method: null, confidence: null },
        ],
      },
    } as unknown as DashboardResponse;
    render(<AccountsScreen {...props} request={withDashboard(vi.fn(), flagged)} />);

    await screen.findByRole("region", { name: "Position summary" });
    expect(within(screen.getByRole("row", { name: /Inventory Card/ })).getByText("Coverage problem")).toBeTruthy();
  });

  it("keeps a long account label from displacing its amount or its actions", () => {
    const long = account({
      id: "acct-long",
      name: "A deliberately long synthetic account label that must not displace the aligned amount",
      openingBalanceConfirmedAt: "2024-01-01T00:00:00Z",
    });
    render(<AccountsScreen {...props} accounts={[long]} totals={[]} />);

    const row = within(screen.getByRole("row", { name: /deliberately long synthetic account label/ }));
    expect(row.getByLabelText(/balance I 2,50,000\.00$/)).toBeTruthy();
    expect(row.getByRole("button", { name: `Actions for ${long.name}` })).toBeTruthy();
  });

  it("keeps identifiers masked and never prefetches a reveal", () => {
    const request = vi.fn();
    render(<AccountsScreen {...props} request={withDashboard(request)} />);

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
    render(<AccountsScreen {...props} accounts={[savings]} request={withDashboard(request)} totals={[]} />);
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
    render(<AccountsScreen {...props} accounts={[accountWithIban]} request={withDashboard(request)} totals={[]} />);

    fireEvent.click(screen.getByRole("button", {
      name: "Show bank account number for Inventory Savings",
    }));

    expect(await screen.findByText("SYN 0000 1234")).toBeTruthy();
    expect(screen.getByText("****5678")).toBeTruthy();
    fireEvent.click(rowAction("Inventory Savings", "Edit Inventory Savings"));
    expect(screen.queryByText("SYN 0000 1234")).toBeNull();
  });

  it("cancels an in-flight reveal when edit tears down the identifier view", async () => {
    let finishReveal: ((value: unknown) => void) | undefined;
    const request = vi.fn().mockImplementation(() => new Promise((resolve) => {
      finishReveal = resolve;
    }));
    render(<AccountsScreen {...props} accounts={[savings]} request={withDashboard(request)} totals={[]} />);

    fireEvent.click(screen.getByRole("button", {
      name: "Show bank account number for Inventory Savings",
    }));
    const init = request.mock.calls[0]?.[1] as RequestInit;
    expect(init.signal?.aborted).toBe(false);

    fireEvent.click(rowAction("Inventory Savings", "Edit Inventory Savings"));
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
      <AccountsScreen {...props} accounts={[savings]} request={withDashboard(request)} totals={[]} />,
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
        request={withDashboard(request)}
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
      <AccountsScreen {...props} accounts={[savings]} request={withDashboard(request)} totals={[]} />,
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

  it("presents the inventory as the approved compact console table", () => {
    render(<AccountsScreen {...props} />);

    expect(screen.getByRole("heading", { level: 1, name: "Accounts" })).toBeTruthy();
    expect(screen.getByText("2 open accounts · latest balance 15 Jan 2026")).toBeTruthy();
    // Page actions, not a heading-level call to action.
    expect(screen.getByRole("link", { name: "Import evidence" })).toHaveProperty("pathname", "/import");
    expect(screen.getByRole("button", { name: "Add account" })).toBeTruthy();
    // The wide table scrolls only inside its own named region.
    expect(screen.getByRole("region", { name: "Open accounts by role table" })).toBeTruthy();
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

    expect(screen.getByText("2 open accounts · latest balance 15 Jan 2026")).toBeTruthy();
    expect(screen.queryByText(/last synced/i)).toBeNull();
  });

  it("leaves no card-grid override behind the console table", () => {
    const css = readFileSync(`${process.cwd()}/app/globals.css`, "utf8");
    // The rejected editorial inventory turned the table into a card grid; the console does not.
    expect(css).not.toMatch(/\.account-table\s*\{[^}]*display:\s*block/);
    expect(css).not.toMatch(/\.account-card-grid/);
  });

  it("groups open accounts by the specification's display roles, in its own order", () => {
    render(
      <AccountsScreen
        {...props}
        accounts={[
          card,
          account({ id: "acct-prop", name: "Inventory Property", accountType: "property", openingBalanceConfirmedAt: "2024-01-01T00:00:00Z", valuationMode: "snapshot", latestValuationDate: "2026-01-10" }),
          savings,
          account({ id: "acct-cash", name: "Inventory Cash", accountType: "cash", openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" }),
          account({ id: "acct-loan", name: "Inventory Loan", accountType: "home_loan", accountClass: "liability", currentBalance: "-1000.0000", openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" }),
        ]}
      />,
    );
    const table = within(screen.getByRole("region", { name: "Open accounts by role table" }));
    const groups = table.getAllByRole("row")
      .map((row) => row.textContent ?? "")
      .filter((text) => / · \d+ accounts?$/.test(text));
    expect(groups).toEqual([
      "Everyday · 1 account",
      "Reserve · 1 account",
      "Property · 1 account",
      "Credit · 1 account",
      "Loan · 1 account",
    ]);
    expect(within(screen.getByRole("region", { name: "Open accounts by role table" }))
      .queryAllByRole("heading")).toHaveLength(0);
  });

  it("shows a native balance formatted by its own currency row", () => {
    render(<AccountsScreen {...props} />);
    // XIN declares Indian grouping and 2 decimals in the catalogue. Letter symbols keep a gap.
    expect(screen.getByLabelText("Inventory Savings balance I 2,50,000.00")).toBeTruthy();
    // A liability reads as what is owed while the stored value stays negative.
    expect(screen.getByLabelText("Inventory Card balance You owe A 4,500.00")).toBeTruthy();
  });

  it("discloses closed accounts without losing their closing date", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getByText("Closed accounts (1)")).toBeTruthy();
    const closedRow = screen.getByRole("row", { name: /Old Savings/ });
    expect(closedRow.textContent).toContain("Closed 30 Jun 2026");
  });

  it("states each evidence status without inventing an age threshold", () => {
    const unvalued = account({ ...retirementNoSnapshot, openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" });
    render(<AccountsScreen {...props} accounts={[savings, unvalued, card]} />);
    // An unacknowledged opening outranks everything; a snapshot account with no valuation is
    // missing one; the card is old but current, because nothing typed says otherwise.
    expect(within(screen.getByRole("row", { name: /Inventory Savings/ })).getByText("Needs confirmation")).toBeTruthy();
    expect(within(screen.getByRole("row", { name: /Inventory Retirement/ })).getByText("Missing snapshot")).toBeTruthy();
    expect(within(screen.getByRole("row", { name: /Inventory Card/ })).getByText("Current")).toBeTruthy();
    expect(screen.queryByText(/stale/i)).toBeNull();
    // A snapshot account with no valuation states that, rather than its placeholder balance.
    expect(within(screen.getByRole("row", { name: /Inventory Retirement/ })).getByText("Not available")).toBeTruthy();
    expect(screen.getByLabelText("Inventory Retirement balance Not available")).toBeTruthy();
  });

  it("never pairs a snapshot account's stored ledger balance with its valuation date", () => {
    const valued = account({
      id: "acct-valued",
      name: "Inventory Property",
      accountType: "property",
      valuationMode: "snapshot",
      latestValuationDate: "2026-02-28",
      currentBalance: "4000000.0000",
      openingBalanceConfirmedAt: "2024-01-01T00:00:00Z",
    });
    render(<AccountsScreen {...props} accounts={[valued]} totals={[]} />);

    const row = within(screen.getByRole("row", { name: /Inventory Property/ }));
    // `/accounts` carries no snapshot value, and the evidence date belongs to the valuation —
    // so the superseded ledger figure is never presented as the account's current value.
    expect(row.queryByText(/40,00,000/)).toBeNull();
    expect(row.getByText("See valuation")).toBeTruthy();
    expect(row.getByText("28 Feb 2026")).toBeTruthy();
    expect(row.getByText("Current")).toBeTruthy();
  });

  it("orders a role group by name and then by the stable id", () => {
    const shared = { accountType: "savings" as const, openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" };
    render(
      <AccountsScreen
        {...props}
        accounts={[
          account({ ...shared, id: "acct-z", name: "Zephyr reserve" }),
          account({ ...shared, id: "acct-b", name: "Same name reserve" }),
          account({ ...shared, id: "acct-a", name: "Same name reserve" }),
          account({ ...shared, id: "acct-m", name: "Alpha reserve" }),
        ]}
        totals={[]}
      />,
    );

    // Sorted by name, then — for the two identical names — by the stable id, never by input
    // order. The trigger ids carry the account id, so one list proves both.
    const triggers = screen.getAllByRole("button", { name: /^Actions for/ }).map((b) => b.id);
    expect(triggers).toEqual([
      "account-actions-acct-m",
      "account-actions-acct-a",
      "account-actions-acct-b",
      "account-actions-acct-z",
    ]);
  });

  it("groups an unknown account type and a receivable under Other", () => {
    render(
      <AccountsScreen
        {...props}
        accounts={[
          account({ id: "acct-recv", name: "Inventory Receivable", accountType: "receivable", openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" }),
          account({ id: "acct-new", name: "Inventory Unknown", accountType: "a_type_this_build_does_not_know", openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" }),
          account({ id: "acct-crypto", name: "Inventory Crypto", accountType: "crypto", openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" }),
        ]}
        totals={[]}
      />,
    );

    const table = within(screen.getByRole("region", { name: "Open accounts by role table" }));
    const groups = table.getAllByRole("row")
      .map((row) => row.textContent ?? "")
      .filter((text) => / · \d+ accounts?$/.test(text));
    expect(groups).toEqual(["Investment · 1 account", "Other · 2 accounts"]);
  });

  it("does not report an all-clear while the coverage source is unavailable", async () => {
    const failing = vi.fn(async (path: string) => {
      if (path === "/dashboard") throw new ApiError(503, "synthetic");
      return undefined;
    });
    render(
      <AccountsScreen
        {...props}
        accounts={[card]}
        request={failing as unknown as typeof apiFetch}
        totals={[]}
      />,
    );

    const summary = await screen.findByRole("region", { name: "Position summary" });
    await waitFor(() => expect(
      within(summary).getAllByText("The reporting total could not be loaded"),
    ).toHaveLength(2));
    // The card's opening is acknowledged and it needs no snapshot, but a coverage problem the
    // dashboard would have named cannot be ruled out — so the tile does not claim completeness.
    expect(within(summary).queryByText("Every open account has current evidence")).toBeNull();
    expect(within(summary).getByText(/coverage problems could not be loaded/)).toBeTruthy();
  });

  it("teaches rather than showing a blank table when there are no accounts", () => {
    render(<AccountsScreen {...props} accounts={[]} totals={[]} />);
    expect(screen.getByRole("heading", { name: /first account/i })).toBeTruthy();
  });

  it("offers every catalogue currency and every institution when adding an account", () => {
    render(<AccountsScreen {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
    expect(screen.getByRole("heading", { level: 2, name: "New account" })).toBeTruthy();
    const currency = screen.getByLabelText("Currency") as HTMLSelectElement;
    expect([...currency.options].map((o) => o.value)).toEqual(["XAE", "XIN", "XZZ"]);
    expect(screen.getByLabelText("Institution")).toBeTruthy();
    expect(screen.getByRole("button", { name: /add institution/i })).toBeTruthy();
  });

  it("uses Nova controls for the account entry path", () => {
    render(<AccountsScreen {...props} />);
    const add = screen.getByRole("button", { name: "Add account" });
    expect(add.getAttribute("data-slot")).toBe("button");
    fireEvent.click(add);
    expect(screen.getByLabelText("Account name").getAttribute("data-slot")).toBe("input");
    expect(screen.getByLabelText("Institution").getAttribute("data-slot")).toBe("native-select");
    expect(screen.getByRole("checkbox", { name: "Include in net worth" }).getAttribute("data-slot")).toBe("checkbox");
  });

  it("offers every database-supported institution type", () => {
    render(<AccountsScreen {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
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
    fireEvent.click(rowAction("Inventory Savings", /edit inventory savings/i));
    expect(screen.getByLabelText("Account type")).toBeTruthy();
    expect(screen.getByLabelText("Currency")).toBeTruthy();
    expect(screen.getByLabelText("Country")).toBeTruthy();
    expect(screen.getByLabelText("Opening balance")).toBeTruthy();
    expect(screen.getByLabelText("Opening balance date")).toBeTruthy();
    expect(screen.getByLabelText("Credit limit")).toBeTruthy();
  });

  it("requires a credit limit when the type is a credit card", () => {
    render(<AccountsScreen {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
    const limit = screen.getByLabelText("Credit limit") as HTMLInputElement;
    expect(limit.required).toBe(false);
    fireEvent.change(screen.getByLabelText("Account type"), { target: { value: "credit_card" } });
    expect((screen.getByLabelText("Credit limit") as HTMLInputElement).required).toBe(true);
  });

  it("names every account-table column for screen-reader navigation", () => {
    render(<AccountsScreen {...props} />);
    const table = within(screen.getByRole("region", { name: "Open accounts by role table" }));
    expect(table.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Account", "Role", "Currency", "Evidence", "Status", "Balance", "Actions",
    ]);
  });

  it("sends the opening balance as a decimal string", async () => {
    const request = vi.fn().mockResolvedValue(props.accounts[0]);
    render(<AccountsScreen {...props} request={withDashboard(request)} />);
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
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
    render(<AccountsScreen {...props} request={withDashboard(request)} />);
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
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
    render(<AccountsScreen {...props} request={withDashboard(request)} />);
    fireEvent.click(rowAction("Inventory Card", "Edit Inventory Card"));
    expect((screen.getByLabelText("Amount owed") as HTMLInputElement).value).toBe("4500.0000");
    fireEvent.change(screen.getByLabelText("Account type"), { target: { value: "current" } });
    fireEvent.click(screen.getByRole("button", { name: "Save account" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = JSON.parse(request.mock.calls[0][1].body as string) as { openingBalance: string };
    expect(body.openingBalance).toBe("-4500.0000");
  });

  it("keeps edit and reopen on a closed account", () => {
    render(<AccountsScreen {...props} />);
    expect(rowAction("Old Savings", /edit old savings/i)).toBeTruthy();
    expect(rowAction("Old Savings", "Reopen Old Savings")).toBeTruthy();
  });

  it("names acknowledge after the account it confirms, and offers it only where it applies", () => {
    render(<AccountsScreen {...props} />);
    expect(rowAction("Inventory Savings", "Acknowledge Inventory Savings")).toBeTruthy();
    // The card's opening is already acknowledged, so its menu does not offer the action.
    fireEvent.click(screen.getByRole("button", { name: "Actions for Inventory Card" }));
    expect(screen.queryByRole("button", { name: "Acknowledge Inventory Card" })).toBeNull();
  });

  it("names close and reopen after the account they mutate", () => {
    render(<AccountsScreen {...props} />);
    expect(rowAction("Inventory Savings", "Close Inventory Savings")).toBeTruthy();
    expect(rowAction("Inventory Card", "Close Inventory Card")).toBeTruthy();
    expect(rowAction("Old Savings", "Reopen Old Savings")).toBeTruthy();
  });

  it("returns focus to the row action that opened a contextual menu", () => {
    render(<AccountsScreen {...props} />);
    const trigger = screen.getByRole("button", { name: "Actions for Inventory Savings" });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "Enter" });

    const region = screen.getByRole("region", { name: "Actions for Inventory Savings" });
    within(region).getByRole("button", { name: "Edit Inventory Savings" }).focus();
    fireEvent.keyDown(region, { key: "Escape" });

    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger);
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
    const totals = within(screen.getByRole("region", { name: "Grouped native totals table" }));
    expect(totals.getByRole("row", { name: /Assets · XB/ }).textContent).toContain("-I 2,50,000.00");
    const liabilities = totals.getByRole("row", { name: /Liabilities · XA/ });
    expect(liabilities.textContent).toContain("A 4,500.00 owed");
    expect(liabilities.textContent).not.toContain("-A");
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
    const totals = within(screen.getByRole("region", { name: "Grouped native totals table" }));
    // One row per currency: the exact native totals are never combined into one figure.
    expect(totals.getByRole("row", { name: /Assets · XB XIN/ }).textContent)
      .toContain("I 2,50,000.00");
    expect(totals.getByRole("row", { name: /Assets · XB XAE/ }).textContent)
      .toContain("A 1,000.00");
    expect(totals.getByRole("row", { name: /Assets · XB XIN/ }).textContent).toContain("15 Jan 2026");
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
        request={withDashboard(request)}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
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
    render(<AccountsScreen {...props} request={withDashboard(request)} />);
    fireEvent.click(rowAction("Inventory Savings", "Acknowledge Inventory Savings"));
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect((rowAction("Inventory Savings", "Edit Inventory Savings") as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps row actions disabled until the inventory refreshes", async () => {
    const request = vi.fn().mockResolvedValue(savings);
    const { rerender } = render(<AccountsScreen {...props} request={withDashboard(request)} />);
    fireEvent.click(rowAction("Inventory Savings", "Acknowledge Inventory Savings"));
    await waitFor(() => expect(request).toHaveBeenCalled());
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
    expect((rowAction("Inventory Savings", "Edit Inventory Savings") as HTMLButtonElement).disabled).toBe(true);
    expect((rowAction("Inventory Savings", "Acknowledge Inventory Savings") as HTMLButtonElement).disabled).toBe(true);
    rerender(
      <AccountsScreen
        {...props}
        request={withDashboard(request)}
        accounts={[account({ openingBalanceConfirmedAt: "2026-01-01T00:00:00Z" }), card, closed]}
      />,
    );
    await waitFor(() => {
      expect((rowAction("Inventory Savings", "Edit Inventory Savings") as HTMLButtonElement).disabled).toBe(false);
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
    const row = within(screen.getByRole("region", { name: "Grouped native totals table" }))
      .getByRole("row", { name: /Liabilities · XA/ });
    expect(row.textContent).toContain("A 250.00");
    expect(row.textContent).not.toContain("owed");
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
    const row = within(screen.getByRole("region", { name: "Grouped native totals table" }))
      .getByRole("row", { name: /Assets · XB/ });
    expect(row.textContent).toContain("I 2,50,100.00");
    expect(row.textContent).toContain("No shared date");
  });

  it("marks an account that is excluded from group totals and leaves it out of the caption", async () => {
    render(
      <AccountsScreen
        {...props}
        accounts={[
          account({ id: "acct-off", name: "Off books", includeInNetWorth: false }),
          account({ id: "acct-on", name: "On books", openingBalanceConfirmedAt: "2024-01-01T00:00:00Z" }),
        ]}
        totals={[]}
      />,
    );
    expect(screen.getByRole("row", { name: /Off books/ }).textContent).toContain("Excluded from totals");
    // The reporting total sums included accounts only, so its caption counts only those.
    const summary = await screen.findByRole("region", { name: "Position summary" });
    expect(within(summary).getByText("Across 1 asset account")).toBeTruthy();
  });

  it("shows the valuation mode and a direct Add valuation entry for an unvalued snapshot account", () => {
    render(<AccountsScreen {...props} accounts={[retirementNoSnapshot]} totals={[]} />);
    expect(screen.getByRole("row", { name: /Inventory Retirement/ }).textContent).toContain(
      "Dated valuation",
    );
    expect(rowAction("Inventory Retirement", "Add valuation for Inventory Retirement")).toBeTruthy();
  });

  it("opens the valuation panel for a ?valuation= account id from the overview's coverage link", async () => {
    window.history.replaceState(null, "", "/accounts?valuation=acct-retirement");
    const request = vi.fn().mockResolvedValue({
      accountId: "acct-retirement",
      currentMode: "snapshot",
      currentEffectiveFrom: "2026-01-01",
      periods: [{ id: "period-1", valuationMode: "snapshot", effectiveFrom: "2026-01-01", effectiveTo: null }],
      snapshots: [],
    });
    render(
      <AccountsScreen
        {...props}
        accounts={[retirementNoSnapshot]}
        request={withDashboard(request)}
        totals={[]}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Valuation — Inventory Retirement" })).toBeTruthy();
    window.history.replaceState(null, "", "/accounts");
  });

  it("returns focus to the row action that opened the valuation panel", async () => {
    const request = vi.fn().mockResolvedValue({
      accountId: "acct-retirement",
      currentMode: "snapshot",
      currentEffectiveFrom: "2026-01-01",
      periods: [{ id: "period-1", valuationMode: "snapshot", effectiveFrom: "2026-01-01", effectiveTo: null }],
      snapshots: [],
    });
    render(
      <AccountsScreen
        {...props}
        accounts={[retirementNoSnapshot]}
        request={withDashboard(request)}
        totals={[]}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Actions for Inventory Retirement" });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "Add valuation for Inventory Retirement" }));
    expect(await screen.findByRole("heading", { name: "Valuation — Inventory Retirement" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Actions for Inventory Retirement" }),
    ));
    expect(screen.queryByRole("heading", { name: /^Valuation/ })).toBeNull();
  });

  it("ignores a ?valuation= id that is not an active asset account", () => {
    window.history.replaceState(null, "", `/accounts?valuation=${closed.id}`);
    render(<AccountsScreen {...props} />);

    expect(screen.queryByRole("heading", { name: /^Valuation/ })).toBeNull();
    window.history.replaceState(null, "", "/accounts");
  });

  it("drops a leftover credit limit when the type is not a card", async () => {
    const request = vi.fn().mockResolvedValue(props.accounts[0]);
    render(<AccountsScreen {...props} request={withDashboard(request)} />);
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
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
