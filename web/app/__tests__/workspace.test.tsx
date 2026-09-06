// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Workspace } from "@/app/workspace";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AccountsResponse, CurrencyRow, FxRateResponse, HouseholdResponse, InstitutionRow } from "@/types/api";

const catalogue: Array<CurrencyRow> = [
  { code: "XAE", name: "Synthetic A", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XIN", name: "Synthetic I", symbol: "I", decimals: 2, numberFormat: "indian" },
  { code: "XZZ", name: "Synthetic Z", symbol: "Z", decimals: 3, numberFormat: "international" },
];

const household: HouseholdResponse = {
  id: "synthetic-owner",
  name: "Household",
  ownerDisplayName: "Owner",
  ownerTaxResidency: null,
  baseCurrency: "XIN",
  secondaryCurrency: "XAE",
  reportingPairLocked: false,
  currencies: [
    { code: "XIN", symbol: "₹", decimals: 2, numberFormat: "indian" },
    { code: "XAE", symbol: "AED", decimals: 2, numberFormat: "international" },
  ],
};

const swappedHousehold: HouseholdResponse = {
  ...household,
  baseCurrency: "XAE",
  secondaryCurrency: "XIN",
  currencies: [household.currencies[1], household.currencies[0]],
};

type Request = typeof apiFetch;
type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: Error) => void;
};

function requestThat(...results: Array<unknown | Error>): Request {
  const request = vi.fn(async () => {
    const result = results.shift();
    if (result instanceof Error) throw result;
    return result;
  });
  return request as Request;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const rate: FxRateResponse = {
  base: "XIN",
  quote: "XAE",
  rate: "0.0441234567",
  source: "synthetic-closing-rate",
  carried: true,
  carried_from: "2024-01-05",
};

describe("authenticated workspace", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState(null, "", "/");
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("guards owner content with the household request and labels unavailable modules", async () => {
    let resolve!: (value: HouseholdResponse) => void;
    const request = vi.fn(() => new Promise<HouseholdResponse>((done) => (resolve = done))) as Request;

    render(<Workspace request={request} navigate={vi.fn()} />);
    expect(screen.getByText("Checking owner access…")).toBeTruthy();
    expect(screen.queryByText("Reporting currency")).toBeNull();

    resolve(household);
    expect(await screen.findByText("Reporting currency")).toBeTruthy();
    expect(screen.getByText("₹12,34,567.89")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/settings");
    expect(screen.getByRole("link", { name: "Accounts" }).getAttribute("href")).toBe("/accounts");
    expect(screen.getByRole<HTMLButtonElement>("button", { name: /Plans.*unavailable/i }).disabled).toBe(true);
  });

  it.each([
    [401, "/login"], [403, "/unauthorized"],
  ])("routes a protected %i response to %s", async (status, destination) => {
    const navigate = vi.fn();
    render(
      <Workspace
        request={requestThat(new ApiError(status, "synthetic"))}
        navigate={navigate}
      />,
    );
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByText("Reporting currency")).toBeNull();
  });

  it("offers retry after a service failure instead of exposing owner content", async () => {
    const request = requestThat(new TypeError("offline"), household);
    render(<Workspace request={request} navigate={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "OneLedger is unavailable" })).toBeTruthy();
    expect(screen.queryByText("Reporting currency")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("₹12,34,567.89")).toBeTruthy();
  });

  it("keeps the owner view on transient logout failure and clears it after retry succeeds", async () => {
    const navigate = vi.fn();
    const request = requestThat(household, new TypeError("offline"), undefined);
    render(<Workspace request={request} navigate={navigate} />);
    await screen.findByText("₹12,34,567.89");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect((await screen.findByRole("alert")).textContent).toContain("could not confirm");
    expect(screen.getByText("₹12,34,567.89")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Retry sign out" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("₹12,34,567.89")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
    expect(request).toHaveBeenLastCalledWith("/auth/logout", { method: "POST" });
  });

  it.each([
    [401, "/login"], [403, "/unauthorized"],
  ])("clears the owner view when logout returns %i and routes to %s", async (status, destination) => {
    const navigate = vi.fn();
    const request = requestThat(household, new ApiError(status, "synthetic"));
    render(<Workspace request={request} navigate={navigate} />);
    await screen.findByText("₹12,34,567.89");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByText("₹12,34,567.89")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("persists the configured toggle, falls back from obsolete choices, and tolerates storage errors", async () => {
    localStorage.setItem("oneledger.reportingCurrency", "obsolete");
    const first = render(<Workspace request={requestThat(household)} navigate={vi.fn()} />);
    expect(await screen.findByText("₹12,34,567.89")).toBeTruthy();

    const secondary = screen.getByRole("button", { name: "XAE" });
    expect(secondary.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(secondary);
    expect(screen.getByText("AED 1,234,567.89")).toBeTruthy();
    expect(localStorage.getItem("oneledger.reportingCurrency")).toBe("XAE");

    first.unmount();
    render(<Workspace request={requestThat(household)} navigate={vi.fn()} />);
    expect(await screen.findByText("AED 1,234,567.89")).toBeTruthy();

    cleanup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<Workspace request={requestThat(household)} navigate={vi.fn()} />);
    expect(await screen.findByText("₹12,34,567.89")).toBeTruthy();
  });

  it("renders the Settings FX rate provenance for the requested date", async () => {
    window.history.replaceState(null, "", "/settings");
    render(<Workspace request={requestThat(household, catalogue, [], [rate])} navigate={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByLabelText<HTMLInputElement>("FX date").value).toBe("");
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Check FX rate" }).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("FX date"), { target: { value: "2024-01-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Check FX rate" }));

    expect(await screen.findByText("0.0441234567")).toBeTruthy();
    expect(screen.getByText("XIN/XAE")).toBeTruthy();
    expect(screen.getByText("synthetic-closing-rate")).toBeTruthy();
    expect(screen.getByText("Carried from 2024-01-05")).toBeTruthy();
    expect(screen.getByText("As of 2024-01-31")).toBeTruthy();
  });

  it("shows empty and retryable FX service states", async () => {
    window.history.replaceState(null, "", "/settings");
    const request = requestThat(household, catalogue, [], [], new TypeError("offline"), [rate]);
    render(<Workspace request={request} navigate={vi.fn()} />);

    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.change(screen.getByLabelText("FX date"), { target: { value: "2024-01-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Check FX rate" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("No FX rates are stored for 2024-01-31."));

    fireEvent.click(screen.getByRole("button", { name: "Check FX rate" }));
    expect((await screen.findByRole("alert")).textContent).toContain("FX rates could not be loaded.");

    fireEvent.click(screen.getByRole("button", { name: "Retry FX rate" }));
    expect(await screen.findByText("0.0441234567")).toBeTruthy();
  });

  it("announces FX loading and success while focus stays on the submit button", async () => {
    window.history.replaceState(null, "", "/settings");
    const fx = deferred<Array<FxRateResponse>>();
    const request = vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(fx.promise) as Request;
    render(<Workspace request={request} navigate={vi.fn()} />);
    await screen.findByRole("heading", { name: "Settings" });

    fireEvent.change(screen.getByLabelText("FX date"), { target: { value: "2024-01-31" } });
    const submit = screen.getByRole("button", { name: "Check FX rate" });
    submit.focus();
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("");
    fireEvent.click(submit);

    expect(document.activeElement).toBe(submit);
    expect(screen.getByRole("status")).toBe(status);
    expect(status.textContent).toContain("Checking stored FX rates for 2024-01-31.");

    fx.resolve([rate]);

    await waitFor(() => expect(status.textContent).toContain("Loaded 1 FX rate for 2024-01-31."));
    expect(screen.getByRole("status")).toBe(status);
    expect(document.activeElement).toBe(submit);
  });

  it("keeps FX results tied to the latest requested date", async () => {
    window.history.replaceState(null, "", "/settings");
    const stale = deferred<Array<FxRateResponse>>();
    const latest = deferred<Array<FxRateResponse>>();
    const request = vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(stale.promise)
      .mockReturnValueOnce(latest.promise) as Request;

    render(<Workspace request={request} navigate={vi.fn()} />);
    await screen.findByRole("heading", { name: "Settings" });

    fireEvent.change(screen.getByLabelText("FX date"), { target: { value: "2024-01-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Check FX rate" }));
    fireEvent.change(screen.getByLabelText("FX date"), { target: { value: "2024-02-02" } });
    fireEvent.click(screen.getByRole("button", { name: "Check FX rate" }));

    stale.resolve([{ ...rate, rate: "9.9999999999", source: "stale-rate" }]);
    latest.resolve([{ ...rate, rate: "0.0500000000", carried: false, carried_from: null }]);

    expect(await screen.findByText("0.0500000000")).toBeTruthy();
    expect(screen.getByText("As of 2024-02-02")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("9.9999999999")).toBeNull());
  });

  it.each([
    [401, "/login"], [403, "/unauthorized"],
  ])("clears protected content when FX returns %i", async (status, destination) => {
    window.history.replaceState(null, "", "/settings");
    const navigate = vi.fn();
    render(<Workspace request={requestThat(household, catalogue, [], new ApiError(status, "synthetic"))} navigate={navigate} />);

    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.change(screen.getByLabelText("FX date"), { target: { value: "2024-01-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Check FX rate" }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByRole("heading", { name: "Settings" })).toBeNull();
  });

  it("keeps successful logout routing stable when active FX returns 403 during sign-out", async () => {
    window.history.replaceState(null, "", "/settings");
    const fx = deferred<Array<FxRateResponse>>();
    const logout = deferred<void>();
    const navigate = vi.fn();
    const request = vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(fx.promise)
      .mockReturnValueOnce(logout.promise) as Request;

    render(<Workspace request={request} navigate={navigate} />);
    await screen.findByRole("heading", { name: "Settings" });

    fireEvent.change(screen.getByLabelText("FX date"), { target: { value: "2024-01-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Check FX rate" }));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Check FX rate" }).disabled).toBe(true);

    fx.reject(new ApiError(403, "revoked"));
    await Promise.resolve();
    await Promise.resolve();
    expect(navigate).not.toHaveBeenCalledWith("/unauthorized");

    logout.resolve();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));

    await Promise.resolve();
    await Promise.resolve();

    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalledWith("/unauthorized");
  });

  it("keeps FX usable when sign-out fails after cancelling an active lookup", async () => {
    window.history.replaceState(null, "", "/settings");
    const fx = deferred<Array<FxRateResponse>>();
    const navigate = vi.fn();
    const request = vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(fx.promise)
      .mockRejectedValueOnce(new TypeError("offline")) as Request;

    render(<Workspace request={request} navigate={navigate} />);
    await screen.findByRole("heading", { name: "Settings" });

    fireEvent.change(screen.getByLabelText("FX date"), { target: { value: "2024-01-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Check FX rate" }));
    expect(screen.getByText("Checking stored FX rates for 2024-01-31.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect((await screen.findByRole("alert")).textContent).toContain("could not confirm");

    expect(screen.queryByText("Checking stored FX rates for 2024-01-31.")).toBeNull();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Check FX rate" }).disabled).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("refreshes the reporting-currency toggle after the pair changes", async () => {
    const request = requestThat(household, catalogue, [], undefined, swappedHousehold, catalogue, []);
    render(<Workspace section="settings" request={request} navigate={vi.fn()} />);
    await screen.findByLabelText("Base currency");
    fireEvent.change(screen.getByLabelText("Base currency"), { target: { value: "XAE" } });
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));
    await waitFor(() =>
      expect(screen.getByRole("group", { name: "Reporting currency" }).textContent).toContain("XAE"),
    );
  });

  it("renders the accounts inventory and marks the nav current", async () => {
    const inventory: AccountsResponse = {
      accounts: [{
        id: "acct-savings",
        name: "Inventory Savings",
        institutionId: "inst-1",
        institutionName: "Synthetic Bank",
        accountType: "savings",
        accountClass: "asset",
        accountSubtype: null,
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
        currentBalanceAsOf: null,
        creditLimit: null,
      }],
      totals: [{ accountClass: "asset", country: "XB", currency: "XIN", total: "250000.0000" }],
    };
    const institutions: Array<InstitutionRow> = [
      { id: "inst-1", name: "Synthetic Bank", shortName: "SB", country: "XB", institutionType: "bank" },
    ];
    render(
      <Workspace
        section="accounts"
        request={requestThat(household, inventory, institutions, catalogue)}
        navigate={vi.fn()}
      />,
    );
    expect((await screen.findByRole("link", { name: "Accounts" })).getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("Inventory Savings")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Assets · XB" })).toBeTruthy();
  });

  it.each([
    [401, "/login"], [403, "/unauthorized"],
  ])("clears Accounts when create returns %i", async (status, destination) => {
    const inventory: AccountsResponse = {
      accounts: [{
        id: "acct-savings",
        name: "Inventory Savings",
        institutionId: "inst-1",
        institutionName: "Synthetic Bank",
        accountType: "savings",
        accountClass: "asset",
        accountSubtype: null,
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
        currentBalanceAsOf: null,
        creditLimit: null,
      }],
      totals: [],
    };
    const institutions: Array<InstitutionRow> = [
      { id: "inst-1", name: "Synthetic Bank", shortName: "SB", country: "XB", institutionType: "bank" },
    ];
    const navigate = vi.fn();
    render(
      <Workspace
        section="accounts"
        request={requestThat(household, inventory, institutions, catalogue, new ApiError(status, "synthetic"))}
        navigate={navigate}
      />,
    );
    await screen.findByText("Inventory Savings");
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "1.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"), { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByText("Inventory Savings")).toBeNull();
  });

  it("does not fetch the currency catalogue on overview", async () => {
    const request = requestThat(household);
    render(<Workspace request={request} navigate={vi.fn()} />);
    await screen.findByText("₹12,34,567.89");
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("/household");
  });

  it.each([
    [401, "/login"], [403, "/unauthorized"],
  ])("clears Settings when household save returns %i", async (status, destination) => {
    const navigate = vi.fn();
    const request = requestThat(household, catalogue, [], new ApiError(status, "synthetic"));
    render(<Workspace section="settings" request={request} navigate={navigate} />);
    await screen.findByLabelText("Household name");
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByLabelText("Household name")).toBeNull();
  });
});
