// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Workspace } from "@/app/workspace";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  AccountsResponse,
  ClassifyResult,
  CurrencyRow,
  DashboardResponse,
  FxRateResponse,
  HouseholdResponse,
  InstitutionRow,
  MonthFlows,
  RuleRow,
  RuleSuggestion,
  TransactionsResponse,
} from "@/types/api";

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

const emptyLedger: TransactionsResponse = {
  transactions: [],
  total: 0,
  summary: {
    reportableIncomeBase: "0.0000",
    reportableIncomeSecondary: "0.0000",
    reportableSpendingBase: "0.0000",
    reportableSpendingSecondary: "0.0000",
    reportableNetBase: "0.0000",
    reportableNetSecondary: "0.0000",
    unreviewedCount: 0,
    uncategorizedCount: 0,
    attentionCount: 0,
    moneyScope: "reportable",
    attentionScope: "ledger",
  },
};

type Request = typeof apiFetch;

/**
 * Settings renders sections that load their own rules and data-quality report, and these tests
 * mock `request` as a fixed sequence. Any request the sequence does not expect eats the next
 * entry and the test fails somewhere unrelated — which is exactly what happened when the rules
 * section landed.
 *
 * `withRules` answers those out of band so the sequence keeps describing only what the test is
 * about. A section added later needs one more line in `outOfBand`, not a re-counted chain.
 */
function outOfBand(path: string): Promise<unknown> | undefined {
  if (path === "/rules") return Promise.resolve([]);
  if (path === "/rules/packs") return Promise.resolve([]);
  // Malformed on purpose: the analytics/suggestions panels render nothing until they see the
  // shape they expect (`.coverage`, `.suggestions`), so an empty object here is "no data yet",
  // exactly like every other section this sequence does not care about.
  if (path === "/rules/analytics") return Promise.resolve({});
  if (path === "/rules/suggestions") return Promise.resolve({});
  if (path === "/doctor") return Promise.resolve({ sections: [] });
  if (path === "/merchant-knowledge") return Promise.resolve([]);
  if (path === "/classification-runs?limit=20&offset=0") return Promise.resolve([]);
  // Needs attention (Task 2): the overview and plans screens both fetch this alongside their own
  // pinned sequence; every test here is about the rest of the screen, so it sees an empty list.
  if (path === "/actions") {
    return Promise.resolve({
      asOf: "2026-03-01",
      items: [],
      snoozed: [],
      resolvedFollowUps: [],
      previousMonthReview: { month: "2026-02-01", completedAt: null },
    });
  }
  if (path === "/transactions?limit=5&offset=0") {
    return Promise.resolve(emptyLedger);
  }
  // Net-position history (Phase 4.8 Task 2): Overview plots the stored month ends beside the
  // dashboard; every test here is about the rest of the screen, so it sees none stored yet.
  if (path === "/wealth-history") {
    return Promise.resolve({ baseCurrency: "XIN", secondaryCurrency: "XAE", months: [] });
  }
  // Goals (Phase 3.3 Task 1) fetches alongside the forecast and commitments on Plans; every test
  // here is about the rest of the screen, so it sees no goals or reservable accounts.
  if (path === "/goals?includeClosed=true") {
    return Promise.resolve({ asOf: "2026-03-01", goals: [], accounts: [] });
  }
  // Monthly review (Task 2) fetches alongside the forecast and commitments on Plans; every test
  // here is about the rest of the screen, so it gets a quiet, already-resolved response.
  if (path.startsWith("/monthly-review")) {
    return Promise.resolve({
      month: "2026-02-01",
      completedAt: null,
      steps: {
        coverage: { gapCells: 0, rowsWithGaps: 0 },
        transfers: { candidateCount: 0 },
        classification: { unclassifiedCount: 0 },
        position: {
          monthEnd: "2026-02-28",
          totalBase: null,
          complete: false,
          previousMonthEnd: "2026-01-31",
          previousTotalBase: null,
          previousComplete: false,
          changeBase: null,
          baseCurrency: "XIN",
        },
        commitments: { dueNext30Days: 0, overdue: 0 },
        actions: { open: 0, snoozed: 0 },
      },
    });
  }
  // Register (Phase 3.4 Task 1) fetches alongside the forecast and commitments on Plans; every
  // test here is about the rest of the screen, so it sees an empty register.
  if (path === "/register?includeArchived=true") {
    return Promise.resolve({ records: [] });
  }
  // Emergency reserve (Phase 3.3 Task 2) fetches alongside the forecast and commitments on
  // Plans; every test here is about the rest of the screen, so it sees an unconfigured reserve.
  if (path === "/emergency-reserve") {
    return Promise.resolve({
      asOf: "2026-03-01",
      configured: false,
      settings: null,
      baseline: null,
      accounts: [],
      eligibleBase: null,
      coverageMonths: null,
      reservedMonths: null,
      targetBase: null,
      gapBase: null,
      coverageStatus: "not_enough_information",
      reasons: ["not_configured"],
      currencies: [],
      coverageGapMonths: 0,
      baseCurrency: "XIN",
      reservations: [],
    });
  }
  // Salary analysis (Phase 4.2 Task 2) fetches alongside the payslip list and deduction rules on
  // Salary; every test here is about the rest of the screen, so it sees no payslips and no
  // credits without one.
  if (path === "/salary/analysis") {
    return Promise.resolve({ currencies: [], salaryCreditsWithoutPayslip: [] });
  }
  return undefined;
}

function withRules(sequenced: Request): Request {
  return ((path: string, init?: RequestInit) =>
    outOfBand(path) ?? sequenced(path, init)) as Request;
}

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: Error) => void;
};

function requestThat(...results: Array<unknown | Error>): Request {
  const request = vi.fn(async (path: string) => {
    // Answered out of band for the reason `withRules` gives: the sequence describes what the
    // test is about, and a section that loads its own data must not renumber it.
    const answered = outOfBand(path);
    if (answered) return answered;
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

const march: MonthFlows = {
  month: "2026-03-01",
  incomeBase: "500000.0000",
  incomeSecondary: "22000.0000",
  spendingBase: "100000.0000",
  spendingSecondary: "4400.0000",
  netBase: "400000.0000",
  netSecondary: "17600.0000",
  savingsRatePctBase: "80.0000",
  savingsRatePctSecondary: "80.0000",
};

// The overview's figures. The base total formats to the string the Phase 0 preview used to show,
// so the tests that wait on it now wait on the owner's own net worth.
const dashboard: DashboardResponse = {
  netWorth: {
    asOf: "2026-03-15",
    baseCurrency: "XIN",
    secondaryCurrency: "XAE",
    totalBase: "1234567.8900",
    totalSecondary: "55555.5500",
    assetsBase: "2000000.0000",
    assetsSecondary: "90000.0000",
    liabilitiesBase: "-765432.1100",
    liabilitiesSecondary: "-34444.4500",
    liquidBase: "300000.0000",
    liquidSecondary: "13500.0000",
    rates: [{ currency: "XIN", rateToBase: "1", rateToSecondary: "0.0450000000", rateDate: "2026-03-15", carried: false }],
    missing: [],
    investmentCoverage: [],
  },
  thisMonth: march,
  trend: [march],
  hasRecordedFlows: true,
  flowEvidence: [{
    month: march.month, status: "unknown",
    incomeBaseCoordinate: null, spendingBaseCoordinate: null, netBaseCoordinate: null,
    incomeSecondaryCoordinate: null, spendingSecondaryCoordinate: null, netSecondaryCoordinate: null,
  }],
  cardsDue: [],
  yearToDate: {
    yearStart: "2026-01-01",
    asOf: "2026-03-15",
    incomeBase: "1500000.0000",
    incomeSecondary: "66000.0000",
    spendingBase: "300000.0000",
    spendingSecondary: "13200.0000",
    netBase: "1200000.0000",
    netSecondary: "52800.0000",
    savingsRatePctBase: "80.0000",
    savingsRatePctSecondary: "80.0000",
    investedBase: "0.0000",
    investedSecondary: "0.0000",
  },
  upcomingCommitments: {
    asOf: "2026-03-15",
    through: "2026-04-14",
    count: 0,
    totalBase: "0.0000",
    totalSecondary: "0.0000",
    missingBaseCurrencies: [],
    missingSecondaryCurrencies: [],
    next: null,
    items: [],
  },
  positionComposition: {
    rows: [
      {
        key: "cash",
        label: "Cash and equivalents",
        valueBase: "2000000.0000",
        valueSecondary: "90000.0000",
        sharePercentBase: "72.3271",
        sharePercentSecondary: "72.3271",
        chartCoordinateBase: 7233,
        chartCoordinateSecondary: 7233,
      },
      {
        key: "liabilities",
        label: "Liabilities",
        valueBase: "-765432.1100",
        valueSecondary: "-34444.4500",
        sharePercentBase: "27.6729",
        sharePercentSecondary: "27.6729",
        chartCoordinateBase: 2767,
        chartCoordinateSecondary: 2767,
      },
    ],
    missing: [],
  },
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
    const pending = deferred<HouseholdResponse>();
    const rest = requestThat(catalogue, dashboard);
    const request = vi.fn((path: string, init?: RequestInit) =>
      path === "/household" ? pending.promise : rest(path, init),
    ) as Request;

    render(<Workspace request={request} navigate={vi.fn()} />);
    expect(screen.getByText("Checking owner access…")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Reporting currency" })).toBeNull();

    pending.resolve(household);
    expect(await screen.findByRole("group", { name: "Reporting currency" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Search OneLedger" })).toBeTruthy();
    expect(await screen.findAllByText("₹12,34,567.89")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Workspace" }).closest("[data-slot=sidebar]")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/settings");
    expect(screen.getByRole("link", { name: "Accounts" }).getAttribute("href")).toBe("/accounts");
    expect(screen.getByRole("link", { name: "Import" }).getAttribute("href")).toBe("/import");
    expect(screen.getByRole("link", { name: "Plans" }).getAttribute("href")).toBe("/plans");
  });

  it("identifies the owner in the persistent product-mock toolbar", async () => {
    render(<Workspace request={requestThat(household, catalogue, dashboard)} navigate={vi.fn()} />);

    const owner = await screen.findByLabelText("Owner account");
    expect(owner.textContent).toBe("O");
    expect(owner.closest("header")?.classList.contains("app-toolbar")).toBe(true);
    expect(screen.getAllByRole("banner")).toHaveLength(1);
  });

  it("offers a keyboard shortcut past the workspace navigation", async () => {
    render(<Workspace request={requestThat(household, catalogue, dashboard)} navigate={vi.fn()} />);
    const navigation = await screen.findByRole("navigation", { name: "Workspace" });
    const skip = screen.getByRole("link", { name: "Skip to content" });
    const target = document.getElementById("workspace-content");

    expect(skip.getAttribute("href")).toBe("#workspace-content");
    expect(target?.getAttribute("tabindex")).toBe("-1");
    expect(Boolean(navigation.compareDocumentPosition(target!) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });

  it("opens compact navigation and closes it after a destination is chosen", async () => {
    render(<Workspace request={requestThat(household, catalogue, dashboard)} navigate={vi.fn()} />);
    await screen.findByRole("navigation", { name: "Workspace" });

    const menu = screen.getByRole("button", { name: "Open workspace menu" });
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(menu);
    expect(screen.getByRole("navigation", { name: "Mobile workspace" })).toBeTruthy();

    const destination = within(screen.getByRole("navigation", { name: "Mobile workspace" })).getByRole("link", { name: "Accounts" });
    destination.addEventListener("click", (event) => event.preventDefault(), { once: true });
    fireEvent.click(destination);
    expect(screen.queryByRole("navigation", { name: "Mobile workspace" })).toBeNull();
  });

  it("dismisses compact navigation with Escape", async () => {
    render(<Workspace request={requestThat(household, catalogue, dashboard)} navigate={vi.fn()} />);
    await screen.findByRole("navigation", { name: "Workspace" });
    fireEvent.click(screen.getByRole("button", { name: "Open workspace menu" }));

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("navigation", { name: "Mobile workspace" })).toBeNull());
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Open workspace menu" }));
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
    expect(screen.queryByRole("group", { name: "Reporting currency" })).toBeNull();
  });

  it("offers retry after a service failure instead of exposing owner content", async () => {
    const request = requestThat(new TypeError("offline"), household, catalogue, dashboard);
    render(<Workspace request={request} navigate={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "OneLedger is unavailable" })).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Reporting currency" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByText("₹12,34,567.89")).toBeTruthy();
  });

  it("keeps the owner view on transient logout failure and clears it after retry succeeds", async () => {
    const navigate = vi.fn();
    const request = requestThat(household, catalogue, dashboard, new TypeError("offline"), undefined);
    render(<Workspace request={request} navigate={navigate} />);
    await screen.findAllByText("₹12,34,567.89");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect((await screen.findByRole("alert")).textContent).toContain("could not confirm");
    expect(screen.getAllByText("₹12,34,567.89").length).toBeGreaterThan(0);

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
    const request = requestThat(household, catalogue, dashboard, new ApiError(status, "synthetic"));
    render(<Workspace request={request} navigate={navigate} />);
    await screen.findAllByText("₹12,34,567.89");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByText("₹12,34,567.89")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("persists the configured toggle, falls back from obsolete choices, and tolerates storage errors", async () => {
    localStorage.setItem("oneledger.reportingCurrency", "obsolete");
    const first = render(<Workspace request={requestThat(household, catalogue, dashboard)} navigate={vi.fn()} />);
    expect(await screen.findAllByText("₹12,34,567.89")).toBeTruthy();

    const secondary = screen.getByRole("button", { name: "XAE" });
    expect(secondary.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(secondary);
    expect(within(screen.getByRole("region", { name: "Position composition" })).getByText("AED 90,000.00")).toBeTruthy();
    expect(localStorage.getItem("oneledger.reportingCurrency")).toBe("XAE");

    first.unmount();
    render(<Workspace request={requestThat(household, catalogue, dashboard)} navigate={vi.fn()} />);
    expect(within(await screen.findByRole("region", { name: "Position composition" })).getByText("AED 90,000.00")).toBeTruthy();

    cleanup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<Workspace request={requestThat(household, catalogue, dashboard)} navigate={vi.fn()} />);
    expect(await screen.findAllByText("₹12,34,567.89")).toBeTruthy();
  });

  it("opens Settings on the data-quality report", async () => {
    window.history.replaceState(null, "", "/settings");
    const request = requestThat(household, catalogue, []);
    render(<Workspace request={request} navigate={vi.fn()} />);

    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.click(screen.getByRole("button", { name: "Data health" }));
    expect(screen.getByRole("heading", { name: "Data health" })).toBeTruthy();
    expect(request).toHaveBeenCalledWith("/doctor", undefined);
  });

  it("opens bare Settings on General with one Save and keeps long sections out of view", async () => {
    window.history.replaceState(null, "", "/settings");
    render(<Workspace request={requestThat(household, catalogue, [])} navigate={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Name and reporting pair" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "General" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getAllByRole("button", { name: "Save household" })).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Data health" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Stored rate" })).toBeNull();
  });

  it.each([
    ["#household-title", "General", "household-title"],
    ["#data-quality-title", "Data health", "data-quality-title"],
    ["#categories-title", "Categories", "categories-title"],
    ["#rules-title", "Rules", "rules-title"],
    ["#rules-analytics-title", "Rules", "rules-analytics-title"],
    ["#rules-suggestions-title", "Rules", "rules-suggestions-title"],
    ["#merchant-knowledge-title", "Rules", "merchant-knowledge-title"],
    ["#fx-title", "Exchange rates", "fx-title"],
    ["#fx-backfill-title", "Exchange rates", "fx-backfill-title"],
  ])("selects and focuses the owner of a Settings deep link %s", async (hash, section, target) => {
    window.history.replaceState(null, "", `/settings${hash}`);
    render(<Workspace request={requestThat(household, catalogue, [])} navigate={vi.fn()} />);

    await waitFor(() => expect(document.activeElement?.id).toBe(target));
    expect(screen.getByRole("button", { name: section }).getAttribute("aria-current")).toBe("page");
    expect(window.location.hash).toBe(hash);
  });

  it("ignores an unknown Settings hash and opens General", async () => {
    window.history.replaceState(null, "", "/settings#unknown-section");
    render(<Workspace request={requestThat(household, catalogue, [])} navigate={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Name and reporting pair" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "General" }).getAttribute("aria-current")).toBe("page");
  });

  it("shows a data-health summary only after real findings load", async () => {
    window.history.replaceState(null, "", "/settings");
    const health = deferred<{ sections: Array<{ title: string; findings: Array<{ level: "warning"; message: string }> }> }>();
    const base = requestThat(household, catalogue, []);
    const request = ((path: string, init?: RequestInit) => path === "/doctor" ? health.promise : base(path, init)) as Request;
    render(<Workspace request={request} navigate={vi.fn()} />);

    await screen.findByRole("heading", { name: "Settings" });
    expect(screen.queryByText(/data finding needs attention/i)).toBeNull();
    health.resolve({ sections: [{ title: "Coverage", findings: [{ level: "warning", message: "Synthetic coverage gap" }] }] });
    expect(await screen.findByText("1 data finding needs attention")).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: "Review data health" }));
    expect(screen.getByRole("heading", { name: "Data health" })).toBeTruthy();
    expect(window.location.hash).toBe("#data-quality-title");
  });

  it("does not call an unavailable data-health report clean", async () => {
    window.history.replaceState(null, "", "/settings");
    const base = requestThat(household, catalogue, []);
    const request = ((path: string, init?: RequestInit) => path === "/doctor"
      ? Promise.reject(new ApiError(500, "Report unavailable")) : base(path, init)) as Request;
    render(<Workspace request={request} navigate={vi.fn()} />);

    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.click(screen.getByRole("button", { name: "Data health" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Report unavailable");
    expect(screen.queryByText(/data findings? needs? attention/i)).toBeNull();
    expect(screen.queryByText(/all clear|no issues|clean/i)).toBeNull();
  });

  it("keeps an unsaved General draft while Settings navigation and Back change the visible section", async () => {
    window.history.replaceState(null, "", "/settings");
    render(<Workspace request={requestThat(household, catalogue, [])} navigate={vi.fn()} />);
    const name = await screen.findByLabelText<HTMLInputElement>("Household name");
    fireEvent.change(name, { target: { value: "Edited household" } });

    fireEvent.click(screen.getByRole("button", { name: "Categories" }));
    expect(window.location.hash).toBe("#categories-title");
    expect(screen.getByRole("heading", { name: "Categories" })).toBeTruthy();
    expect(document.activeElement?.id).toBe("categories-title");
    expect(name.closest(".settings-panel")?.hasAttribute("hidden")).toBe(true);

    window.history.replaceState(null, "", "/settings#household-title");
    fireEvent.popState(window);
    expect(screen.getByRole("button", { name: "General" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByLabelText<HTMLInputElement>("Household name").value).toBe("Edited household");
    await waitFor(() => expect(document.activeElement?.id).toBe("household-title"));
  });

  it("renders the Settings FX rate provenance for the requested date", async () => {
    window.history.replaceState(null, "", "/settings");
    render(<Workspace request={requestThat(household, catalogue, [], [rate])} navigate={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "Exchange rates" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Exchange rates" }));
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
    const request = withRules(
      vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(fx.promise) as Request);
    render(<Workspace request={request} navigate={vi.fn()} />);
    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.click(screen.getByRole("button", { name: "Exchange rates" }));

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
    const request = withRules(
      vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(stale.promise)
      .mockReturnValueOnce(latest.promise) as Request);

    render(<Workspace request={request} navigate={vi.fn()} />);
    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.click(screen.getByRole("button", { name: "Exchange rates" }));

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

  it("posts the backfill card's date range and shows how many rates were written", async () => {
    window.history.replaceState(null, "", "/settings");
    const sequenced = vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ written: 14 });
    const request = withRules(sequenced as Request);
    render(<Workspace request={request} navigate={vi.fn()} />);
    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.click(screen.getByRole("button", { name: "Exchange rates" }));

    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2024-01-01" } });
    fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2024-01-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Backfill rates" }));

    expect(await screen.findByText("Wrote 14 rates.")).toBeTruthy();
    expect(sequenced).toHaveBeenLastCalledWith(
      "/fx/backfill?start=2024-01-01&end=2024-01-31",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it.each([
    [401, "/login"], [403, "/unauthorized"],
  ])("clears protected content when FX returns %i", async (status, destination) => {
    window.history.replaceState(null, "", "/settings");
    const navigate = vi.fn();
    render(<Workspace request={requestThat(household, catalogue, [], new ApiError(status, "synthetic"))} navigate={navigate} />);

    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.click(screen.getByRole("button", { name: "Exchange rates" }));
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
    const request = withRules(
      vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(fx.promise)
      .mockReturnValueOnce(logout.promise) as Request);

    render(<Workspace request={request} navigate={navigate} />);
    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.click(screen.getByRole("button", { name: "Exchange rates" }));

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
    const request = withRules(
      vi
      .fn()
      .mockResolvedValueOnce(household)
      .mockResolvedValueOnce(catalogue)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(fx.promise)
      .mockRejectedValueOnce(new TypeError("offline")) as Request);

    render(<Workspace request={request} navigate={navigate} />);
    await screen.findByRole("heading", { name: "Settings" });
    fireEvent.click(screen.getByRole("button", { name: "Exchange rates" }));

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
        identifiers: [],
      }],
      totals: [{ accountClass: "asset", country: "XB", currency: "XIN", total: "250000.0000" }],
    };
    const institutions: Array<InstitutionRow> = [
      { id: "inst-1", name: "Synthetic Bank", shortName: "SB", country: "XB", institutionType: "bank" },
    ];
    render(
      <Workspace
        section="accounts"
        request={requestThat(household, inventory, institutions, catalogue, dashboard)}
        navigate={vi.fn()}
      />,
    );
    expect((await screen.findByRole("link", { name: "Accounts" })).getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("Inventory Savings")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Accounts by role" })).toBeTruthy();
  });

  it("renders the import screen and marks the nav current", async () => {
    const inventory: AccountsResponse = { accounts: [], totals: [] };
    render(
      <Workspace
        section="import"
        request={requestThat(household, inventory, catalogue)}
        navigate={vi.fn()}
      />,
    );

    expect((await screen.findByRole("link", { name: "Import" })).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Statement import" })).toBeTruthy();
    expect(screen.getByLabelText("Statement files")).toBeTruthy();
  });

  it("renders the documents screen and marks the nav current", async () => {
    const documents = {
      documents: [
        {
          id: "doc-1",
          documentType: "bank_statement",
          pageCount: 2,
          isPasswordProtected: false,
          parsedStatus: "parsed",
          uploadedOn: "2026-03-10T12:00:00Z",
          accountId: null,
          accountName: null,
          accountNumberMasked: null,
          period: { start: null, end: null, isDerived: null },
          importKind: "statement",
          importId: null,
          importStatus: null,
          reconciliationStatus: null,
          layoutId: null,
          layoutVersion: null,
          newerLayoutId: null,
        },
      ],
      total: 1,
    };
    const inventory: AccountsResponse = { accounts: [], totals: [] };
    render(
      <Workspace
        section="documents"
        request={requestThat(household, documents, inventory)}
        navigate={vi.fn()}
      />,
    );

    expect((await screen.findByRole("link", { name: "Documents" })).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(screen.getByText("1 document")).toBeTruthy();
  });

  it("forwards a source-document id from the transactions URL to its first ledger request", async () => {
    window.history.replaceState(null, "", "/transactions?sourceDocumentId=doc-9");
    const ledger = emptyLedger;
    const inventory: AccountsResponse = { accounts: [], totals: [] };
    // The taxonomy is fetched before the ledger page, so a deep-linked category can be checked
    // against it before the page is asked for.
    const request = requestThat(household, [], ledger, inventory, catalogue);
    render(<Workspace section="transactions" request={request} navigate={vi.fn()} />);

    await screen.findByRole("heading", { name: "Transactions" });
    const paths = (request as ReturnType<typeof vi.fn>).mock.calls.map((call) => call[0]);
    expect(paths).toContain("/transactions?limit=50&offset=0&sourceDocumentId=doc-9");
  });

  it("forwards a validated search-result transaction id to its first ledger request", async () => {
    const transactionId = "018f2f95-6a58-7f8c-9e61-2d388d928b42";
    window.history.replaceState(null, "", `/transactions?transactionId=${transactionId}`);
    const ledger = emptyLedger;
    const inventory: AccountsResponse = { accounts: [], totals: [] };
    const request = requestThat(household, [], ledger, inventory, catalogue);
    render(<Workspace section="transactions" request={request} navigate={vi.fn()} />);

    await screen.findByRole("heading", { name: "Transactions" });
    const paths = (request as ReturnType<typeof vi.fn>).mock.calls.map((call) => call[0]);
    expect(paths).toContain(
      `/transactions?limit=50&offset=0&transactionId=${transactionId}`,
    );
  });

  it("filters its first ledger page by a spending drill-through link", async () => {
    window.history.replaceState(
      null,
      "",
      "/transactions?categoryId=cat-1&dateFrom=2026-03-01&dateTo=2026-03-31",
    );
    const ledger = emptyLedger;
    const inventory: AccountsResponse = { accounts: [], totals: [] };
    const taxonomy = [
      { id: "cat-1", parentId: null, name: "Synthetic groceries", slug: "synthetic-groceries",
        kind: "expense", bucket: "need", sortOrder: 1, isSystem: true, isActive: true },
    ];
    const request = requestThat(household, taxonomy, ledger, inventory, catalogue);
    render(<Workspace section="transactions" request={request} navigate={vi.fn()} />);

    await screen.findByRole("heading", { name: "Transactions" });
    const paths = (request as ReturnType<typeof vi.fn>).mock.calls.map((call) => call[0]);
    expect(paths).toContain(
      "/transactions?limit=50&offset=0&categoryId=cat-1&dateFrom=2026-03-01&dateTo=2026-03-31",
    );
  });

  it("ignores a drill-through category that is not in the loaded taxonomy", async () => {
    window.history.replaceState(
      null,
      "",
      "/transactions?categoryId=cat-missing&dateTo=nonsense&transactionId=not-a-uuid",
    );
    const ledger = emptyLedger;
    const inventory: AccountsResponse = { accounts: [], totals: [] };
    const request = requestThat(household, [], ledger, inventory, catalogue);
    render(<Workspace section="transactions" request={request} navigate={vi.fn()} />);

    await screen.findByRole("heading", { name: "Transactions" });
    const paths = (request as ReturnType<typeof vi.fn>).mock.calls.map((call) => call[0]);
    expect(paths).toContain("/transactions?limit=50&offset=0");
  });

  it("renders the review screen for the batch it was given", async () => {
    const inventory: AccountsResponse = { accounts: [], totals: [] };
    const staged = {
      batchId: "batch-1",
      documentId: null,
      accountId: "acct-1",
      status: "awaiting_review",
      layoutId: null,
      layoutVersion: null,
      period: { start: "2026-03-01", end: "2026-03-31", isDerived: null },
      rowsExtracted: 0,
      reconciliation: {
        status: "matched", declaredOpening: null, openingIsDerived: true,
        declaredClosing: null, computedClosing: null, delta: null, firstDivergentRow: null,
        previousBalance: null, amount: null, candidateRows: [], message: "",
      },
      groups: [],
    };
    render(
      <Workspace
        batchId="batch-1"
        section="review"
        request={requestThat(household, inventory, catalogue, staged)}
        navigate={vi.fn()}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Review" })).toBeTruthy();
    // Import stays the current nav entry: review is where an import ends up, not a sibling.
    expect(screen.getByRole("link", { name: "Import" }).getAttribute("aria-current")).toBe("page");
  });

  it("renders grouped classification review as its own destination", async () => {
    render(
      <Workspace
        section="classification-review"
        request={requestThat(
          household,
          [],
          { groups: [], totalGroups: 0, totalOccurrences: 0, candidatesScanned: 0, candidateLimitReached: false },
        )}
        navigate={vi.fn()}
      />,
    );

    expect((await screen.findByRole("link", { name: "Classify" })).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Classify" })).toBeTruthy();
    expect(await screen.findByText("Nothing needs classification review.")).toBeTruthy();
  });

  it("renders the salary screen with the currency catalogue and marks the nav current", async () => {
    render(
      <Workspace
        section="salary"
        request={requestThat(household, catalogue, [], { payslips: [], total: 0 })}
        navigate={vi.fn()}
      />,
    );

    expect((await screen.findByRole("link", { name: "Salary" })).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Salary" })).toBeTruthy();
    expect(await screen.findByText("No payslips are recorded yet.")).toBeTruthy();
  });

  it("renders the plans screen with the account and currency catalogue and marks the nav current", async () => {
    const inventory: AccountsResponse = {
      accounts: [{
        id: "acct-plans",
        name: "Plans Savings",
        institutionId: null,
        institutionName: null,
        accountType: "savings",
        accountClass: "asset",
        accountSubtype: null,
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
        currentBalanceAsOf: "2024-01-01T00:00:00Z",
        creditLimit: null,
        identifiers: [],
      }],
      totals: [],
    };
    const forecast = {
      asOf: "2026-03-01",
      accounts: [],
      excludedAccounts: [],
      excludedCommitments: [],
      currencies: [],
      unplannedCardDues: [],
    };
    render(
      <Workspace
        section="plans"
        request={requestThat(household, inventory, catalogue, forecast, { commitments: [] })}
        navigate={vi.fn()}
      />,
    );

    expect((await screen.findByRole("link", { name: "Plans" })).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Plans" })).toBeTruthy();
    expect(await screen.findByText("No account can be forecast yet.")).toBeTruthy();
  });

  it("renders the subscriptions screen with the currency catalogue and marks the nav current", async () => {
    const emptySummary = {
      monthlyBase: "0.0000",
      annualBase: "0.0000",
      currentCount: 0,
      renewingIn30Days: 0,
      priceChangesIn90Days: 0,
      baseCurrency: "XIN",
      upcoming: [],
    };
    render(
      <Workspace
        section="subscriptions"
        request={requestThat(household, catalogue, {}, { series: [] }, emptySummary, { accounts: [] })}
        navigate={vi.fn()}
      />,
    );

    expect((await screen.findByRole("link", { name: "Subscriptions" })).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Subscriptions" })).toBeTruthy();
    expect(await screen.findByText("No current subscription has been found yet.")).toBeTruthy();
  });

  it("renders the subscription detail route for a series id and marks Subscriptions current", async () => {
    const detail = {
      id: "series-1",
      label: "Synthetic streaming service",
      identity: { merchantId: null, matchKey: "synthetic streaming service" },
      cadence: "monthly",
      customIntervalDays: null,
      lifecycle: "active",
      categoryId: null,
      categoryName: null,
      isSubscription: true,
      detectionMethod: "detected",
      needsName: false,
      nativeAmount: "15.0000",
      nativeCurrency: "USD",
      priceChangedFrom: null,
      priceChangedOn: null,
      lastSeenOn: "2026-08-01",
      isCurrent: true,
      currentPeriodStartedOn: "2026-06-01",
      lastPeriodEndedOn: null,
      lastEndReason: null,
      periodCount: 1,
      resumed: false,
      nextExpectedOn: "2026-09-01",
      status: "active",
      paymentChannelId: null,
      paymentChannelName: null,
      payingAccountId: null,
      payingAccountName: null,
      payingAccountMasked: null,
      annualAmount: "180.0000",
      monthlyAmount: "15.0000",
      annualAmountBase: "180.0000",
      monthlyAmountBase: "15.0000",
      firstSeenOn: "2026-06-01",
      spentSoFarBase: "15.0000",
      last12MonthsBase: "15.0000",
      periods: [],
      priceHistory: [],
      charges: [],
    };
    render(
      <Workspace
        section="subscriptions-detail"
        seriesId="series-1"
        request={requestThat(household, catalogue, detail, { channels: [] })}
        navigate={vi.fn()}
      />,
    );

    expect((await screen.findByRole("link", { name: "Subscriptions" })).getAttribute("aria-current")).toBe("page");
    expect(await screen.findByRole("heading", { name: "Synthetic streaming service" })).toBeTruthy();
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
        identifiers: [],
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
        request={requestThat(household, inventory, institutions, catalogue, dashboard, new ApiError(status, "synthetic"))}
        navigate={navigate}
      />,
    );
    await screen.findByText("Inventory Savings");
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "1.00" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"), { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByText("Inventory Savings")).toBeNull();
  });

  it("loads the currency catalogue, the dashboard and needs attention on overview, and nothing else", async () => {
    const request = requestThat(household, catalogue, dashboard);
    render(<Workspace request={request} navigate={vi.fn()} />);
    await screen.findAllByText("₹12,34,567.89");
    expect(vi.mocked(request).mock.calls.map((call) => call[0])).toEqual([
      "/household",
      "/currencies",
      "/dashboard",
      "/actions",
      "/transactions?limit=5&offset=0",
      "/wealth-history",
    ]);
    expect(screen.queryByText(/Phase 0/)).toBeNull();
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

  it("creates a suggested rule, checks past rows and applies — surviving an unrelated re-render", async () => {
    const suggestion: RuleSuggestion = {
      pattern: "WHOLE FOODS",
      direction: "debit",
      categoryId: "cat-1",
      categoryName: "Groceries",
      txnType: "expense",
      evidenceRows: 4,
      affected: 6,
      unclassified: 5,
    };
    const createdRule: RuleRow = {
      id: "rule-1",
      origin: "user",
      timesApplied: 0,
      lastAppliedAt: null,
      name: "Mark Groceries",
      priority: 100,
      isActive: true,
      matchDescriptionPattern: "WHOLE FOODS",
      matchPatternKind: "contains",
      matchDirection: "debit",
      setCategoryId: "cat-1",
      setTxnType: "expense",
    };
    const preview: ClassifyResult = {
      considered: 5,
      changed: 3,
      sample: [{ id: "row-1", bookedDate: "2026-01-02", descriptionRaw: "WHOLE FOODS #123", amount: "-42.0000", currency: "XIN", classifiedBy: "unclassified" }],
      applied: false,
    };
    const applied: ClassifyResult = { ...preview, applied: true };

    let suggestionCalls = 0;
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (path === "/household") return household;
      if (path === "/currencies") return catalogue;
      if (path === "/categories") return [];
      if (path === "/rules" && method === "GET") return [];
      if (path === "/rules" && method === "POST") return createdRule;
      if (path === "/rules/analytics") return {};
      if (path === "/rules/suggestions") {
        suggestionCalls += 1;
        return { suggestions: [suggestion] };
      }
      if (path === "/doctor") return { sections: [] };
      if (path === "/classify?ruleId=rule-1&preview=true" && method === "POST") return preview;
      if (path === "/classify?ruleId=rule-1" && method === "POST") return applied;
      throw new Error(`Unexpected synthetic route ${method} ${path}`);
    }) as Request;

    render(<Workspace section="settings" request={request} navigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Rules" }));

    const card = (await screen.findByText(/narration contains .WHOLE FOODS., debit → Groceries/)).closest("li");
    if (!card) throw new Error("suggestion card not found");
    expect(suggestionCalls).toBe(1);

    fireEvent.click(within(card).getByRole("button", { name: "Create rule" }));
    await within(card).findByRole("button", { name: "Check past rows" });

    // An unrelated re-render — the reporting-currency toggle — must not reload the
    // suggestions the card's own state now depends on staying put.
    fireEvent.click(screen.getByRole("button", { name: "XAE" }));
    expect(suggestionCalls).toBe(1);

    fireEvent.click(within(card).getByRole("button", { name: "Check past rows" }));
    await within(card).findByText("3 of 5 unclassified rows would change. Checking changes nothing; nothing is applied until confirmed.");

    fireEvent.click(within(card).getByRole("button", { name: "Apply 3 changes" }));
    await within(card).findByText("3 of 5 unclassified rows updated.");

    // The card — its pattern, its now-created rule, its apply outcome — stayed mounted
    // through the whole flow rather than being replaced by a reloaded suggestions list.
    expect(screen.getByText(/narration contains .WHOLE FOODS., debit → Groceries/)).toBeTruthy();
    expect(suggestionCalls).toBe(1);
  });
});
