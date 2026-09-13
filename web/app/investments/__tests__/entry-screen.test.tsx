// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InvestmentEntryScreen } from "@/app/investments/entry-screen";
import { Workspace } from "@/app/workspace";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  AccountRow,
  AccountsResponse,
  AcquisitionPosition,
  HouseholdResponse,
  InvestmentEventRow,
  PortfolioPositions,
  SecurityCandidate,
} from "@/types/api";

type Request = typeof apiFetch;

const household: HouseholdResponse = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Synthetic household",
  ownerDisplayName: "Owner",
  ownerTaxResidency: null,
  baseCurrency: "XAA",
  secondaryCurrency: "XBB",
  reportingPairLocked: false,
  currencies: [
    { code: "XAA", symbol: "A", decimals: 2, numberFormat: "international" },
    { code: "XBB", symbol: "B", decimals: 2, numberFormat: "international" },
  ],
};

const investmentAccount: AccountRow = {
  id: "00000000-0000-4000-8000-000000000010",
  name: "Synthetic folio",
  institutionId: "00000000-0000-4000-8000-000000000020",
  institutionName: "Synthetic platform",
  accountType: "mutual_fund_folio",
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
  identifiers: [{
    id: "00000000-0000-4000-8000-000000000030",
    kind: "folio",
    masked: "****2048",
    canReveal: true,
    isActive: true,
  }],
};

const candidate: SecurityCandidate = {
  id: "00000000-0000-4000-8000-000000000040",
  securityType: "mutual_fund",
  name: "Synthetic Growth Fund",
  currency: "XAA",
  country: "XZ",
  assetClass: "equity",
  subAssetClass: null,
  isin: "XSYN00000001",
  ticker: null,
  exchange: null,
  manualPriceEligible: true,
};

const position: AcquisitionPosition = {
  holdingId: "00000000-0000-4000-8000-000000000050",
  accountId: investmentAccount.id,
  securityId: candidate.id,
  securityName: candidate.name,
  assetClass: "equity",
  currency: "XAA",
  asOf: "2026-02-06",
  units: "3.12500000",
  cumulativeContributionsNative: "25.7500",
  cumulativeContributionsBase: "25.7500",
  cumulativeContributionsSecondary: "51.5000",
  priceStatus: "current",
  valueStatus: "available",
  price: "9.000000",
  priceDate: "2026-02-06",
  currentValueNative: "28.1250",
  currentValueBase: "28.1250",
  currentValueSecondary: "56.2500",
  gainNative: "2.3750",
  gainBase: "2.3750",
  gainSecondary: "4.7500",
  missingFx: [],
  fxRates: [
    { reportingCurrency: "XAA", rate: "1", rateDate: "2026-02-06", carried: false },
    { reportingCurrency: "XBB", rate: "2.0000000000", rateDate: "2026-02-06", carried: false },
  ],
};

const event: InvestmentEventRow = {
  id: "00000000-0000-4000-8000-000000000060",
  holdingId: position.holdingId,
  accountId: investmentAccount.id,
  securityId: candidate.id,
  eventDate: "2026-02-05",
  eventKind: "purchase",
  units: "3.12500000",
  price: "8.000000",
  grossAmount: "25.0000",
  charges: "0.7500",
  withheldTax: "0.0000",
  netAmount: "-25.7500",
  currency: "XAA",
  fxRateDate: "2026-02-05",
  fxRateToBase: "1",
  fxRateToSecondary: "2.0000000000",
  netAmountBase: "-25.7500",
  netAmountSecondary: "-51.5000",
  sourceKind: "owner_entered",
  correctsEventId: null,
  correctionState: "uncorrected",
  currentUnits: "3.12500000",
  lot: {
    id: "00000000-0000-4000-8000-000000000070",
    acquisitionDate: "2026-02-05",
    acquiredUnits: "3.12500000",
    costBasisNative: "25.7500",
    currency: "XAA",
  },
};

function positions(rows: Array<AcquisitionPosition> = []): PortfolioPositions {
  return { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: rows };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn((path: string, init?: RequestInit) => Promise.resolve(handler(path, init))) as Request;
}

function renderScreen(
  request: Request,
  accounts: Array<AccountRow> = [investmentAccount],
  createIdempotencyKey = () => "00000000-0000-4000-8000-000000000080",
) {
  return render(
    <InvestmentEntryScreen
      accounts={accounts}
      createIdempotencyKey={createIdempotencyKey}
      request={request}
    />,
  );
}

function fillPurchase() {
  fireEvent.change(screen.getByLabelText("Purchase date"), { target: { value: "2026-02-05" } });
  fireEvent.change(screen.getByLabelText("Units acquired"), { target: { value: "3.12500000" } });
  fireEvent.change(screen.getByLabelText("Transaction price"), { target: { value: "8.000000" } });
  fireEvent.change(screen.getByLabelText("Gross amount"), { target: { value: "25.0000" } });
  fireEvent.change(screen.getByLabelText("Charges"), { target: { value: "0.7500" } });
  fireEvent.change(screen.getByLabelText("Withheld tax"), { target: { value: "0.0000" } });
  fireEvent.change(screen.getByLabelText("Net cash amount"), { target: { value: "-25.7500" } });
}

async function resolveExactInstrument() {
  fireEvent.change(screen.getByLabelText("Public identifier"), { target: { value: candidate.isin } });
  fireEvent.click(screen.getByRole("button", { name: "Find instrument" }));
  await screen.findByText(candidate.name);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("investment entry", () => {
  it("loads owner positions and presents server strings without advice", async () => {
    let release!: (value: PortfolioPositions) => void;
    const request = requestFrom(() => new Promise<PortfolioPositions>((resolve) => { release = resolve; }));
    renderScreen(request);

    expect(screen.getByText("Loading recorded holdings…")).toBeTruthy();
    release(positions([position]));

    const holding = await screen.findByRole("article", { name: candidate.name });
    expect(within(holding).getByText("3.12500000 units")).toBeTruthy();
    expect(within(holding).getByText("XAA 28.1250")).toBeTruthy();
    expect(within(holding).getByText("Price XAA 9.000000 · 6 Feb 2026")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/\b(buy|sell|hold|recommend|rebalance)\b/i);
  });

  it("shows empty and missing-data states without manufacturing zero", async () => {
    const missing = { ...position, priceStatus: "future_price_only" as const, valueStatus: "missing_price" as const,
      price: null, priceDate: null, currentValueNative: null, currentValueBase: null,
      currentValueSecondary: null, gainNative: null, gainBase: null, gainSecondary: null };
    const first = renderScreen(requestFrom(() => positions()));
    expect(await screen.findByText("No investment holdings are recorded yet.")).toBeTruthy();

    first.unmount();
    renderScreen(requestFrom(() => positions([missing])));
    const holding = await screen.findByRole("article", { name: candidate.name });
    expect(within(holding).getByText("Only future-dated price evidence is recorded.")).toBeTruthy();
    expect(within(holding).getByText("Current value unavailable")).toBeTruthy();
    expect(within(holding).queryByText(/XAA 0/)).toBeNull();
  });

  it("renders dated price evidence independently from stale and unsupported value coverage", async () => {
    const staleOutside = {
      ...position,
      priceStatus: "stale" as const,
      valueStatus: "outside_supported_range" as const,
      currentValueNative: null,
      currentValueBase: null,
      currentValueSecondary: null,
      gainNative: null,
      gainBase: null,
      gainSecondary: null,
    };
    renderScreen(requestFrom(() => positions([staleOutside])));

    const holding = await screen.findByRole("article", { name: candidate.name });
    expect(within(holding).getByText("Price XAA 9.000000 · 6 Feb 2026")).toBeTruthy();
    expect(within(holding).getByText("The latest recorded price is stale.")).toBeTruthy();
    expect(within(holding).getByText("The exact value is outside the supported reporting range.")).toBeTruthy();
    expect(within(holding).getByText("Current value unavailable")).toBeTruthy();
  });

  it("uses only active investment accounts and displays their masked identifiers", async () => {
    const nonInvestment = { ...investmentAccount, id: "00000000-0000-4000-8000-000000000011", accountType: "savings", name: "Synthetic savings" };
    const accountWithUnsafeExtra = {
      ...investmentAccount,
      completeIdentifier: "complete-folio-value",
    } as AccountRow;
    renderScreen(requestFrom(() => positions()), [accountWithUnsafeExtra, nonInvestment]);

    await screen.findByText("No investment holdings are recorded yet.");
    const account = screen.getByLabelText("Investment account");
    expect(account.textContent).toContain("Synthetic folio · ****2048");
    expect(account.textContent).not.toContain("Synthetic savings");
    expect(document.body.textContent).not.toContain("complete-folio-value");
  });

  it("directs an owner with no investment account to the account inventory", async () => {
    renderScreen(requestFrom(() => positions()), []);

    expect(await screen.findByText("An investment account is required before recording a purchase.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Add an investment account" }).getAttribute("href")).toBe("/accounts");
    expect(screen.queryByRole("button", { name: "Find instrument" })).toBeNull();
  });

  it("resolves an exact public identity but requires confirmation for name-only evidence", async () => {
    const request = requestFrom(async (path, init) => {
      if (path === "/portfolio/positions") return positions();
      if (path === "/investment-catalogue/securities/resolve") {
        const body = JSON.parse(String(init?.body));
        return body.name
          ? { status: "needs_confirmation", reason: "name_only", candidates: [candidate] }
          : { status: "matched", reason: "exact_public_identity", candidates: [candidate] };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");

    await resolveExactInstrument();
    expect(screen.getByText("Matched by exact public identity.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Lookup method"), { target: { value: "name" } });
    fireEvent.change(screen.getByLabelText("Instrument name"), { target: { value: "Synthetic Growth Fund" } });
    fireEvent.click(screen.getByRole("button", { name: "Find instrument" }));
    const choices = await screen.findByRole("group", { name: "Confirm instrument" });
    expect(within(choices).getByRole("radio", { name: /Synthetic Growth Fund/ })).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Review purchase" }).disabled).toBe(true);
    fireEvent.click(within(choices).getByRole("radio", { name: /Synthetic Growth Fund/ }));
    fireEvent.click(within(choices).getByRole("button", { name: "Use selected instrument" }));
    expect(screen.getByText("Instrument confirmed by owner.")).toBeTruthy();
  });

  it("invalidates a name candidate and an exact ticker match when their evidence changes", async () => {
    const tickerCandidate = { ...candidate, isin: null, ticker: "SYN", exchange: "XSYN" };
    const request = requestFrom(async (path, init) => {
      if (path === "/portfolio/positions") return positions();
      if (path === "/investment-catalogue/securities/resolve") {
        const body = JSON.parse(String(init?.body));
        return body.name
          ? { status: "needs_confirmation", reason: "name_only", candidates: [candidate] }
          : { status: "matched", reason: "exact_public_identity", candidates: [tickerCandidate] };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");

    fireEvent.change(screen.getByLabelText("Lookup method"), { target: { value: "name" } });
    fireEvent.change(screen.getByLabelText("Instrument name"), { target: { value: candidate.name } });
    fireEvent.click(screen.getByRole("button", { name: "Find instrument" }));
    await screen.findByRole("group", { name: "Confirm instrument" });
    fireEvent.change(screen.getByLabelText("Instrument name"), { target: { value: "Changed synthetic name" } });
    expect(screen.queryByRole("group", { name: "Confirm instrument" })).toBeNull();

    fireEvent.change(screen.getByLabelText("Lookup method"), { target: { value: "ticker" } });
    fireEvent.change(screen.getByLabelText("Ticker"), { target: { value: "SYN" } });
    fireEvent.change(screen.getByLabelText("Exchange"), { target: { value: "XSYN" } });
    fireEvent.click(screen.getByRole("button", { name: "Find instrument" }));
    expect(await screen.findByText("Matched by exact public identity.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Exchange"), { target: { value: "XALT" } });
    expect(screen.queryByText("Matched by exact public identity.")).toBeNull();
    expect(screen.queryByText(tickerCandidate.name)).toBeNull();
  });

  it("ignores a late catalogue response after the lookup evidence changes", async () => {
    let release!: (value: unknown) => void;
    const request = requestFrom((path) => {
      if (path === "/portfolio/positions") return positions();
      if (path === "/investment-catalogue/securities/resolve") {
        return new Promise((resolve) => { release = resolve; });
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");
    fireEvent.change(screen.getByLabelText("Public identifier"), { target: { value: candidate.isin } });
    fireEvent.click(screen.getByRole("button", { name: "Find instrument" }));
    fireEvent.change(screen.getByLabelText("Public identifier"), { target: { value: "XSYN00000002" } });
    release({ status: "matched", reason: "exact_public_identity", candidates: [candidate] });

    await waitFor(() => expect(screen.getByRole("button", { name: "Find instrument" }).textContent).toBe("Find instrument"));
    expect(screen.queryByText(candidate.name)).toBeNull();
  });

  it("does not offer manual pricing for a shared catalogue instrument", async () => {
    const shared = { ...candidate, manualPriceEligible: false };
    const request = requestFrom(async (path) => {
      if (path === "/portfolio/positions") return positions();
      if (path === "/investment-catalogue/securities/resolve") {
        return { status: "matched", reason: "exact_public_identity", candidates: [shared] };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");
    await resolveExactInstrument();

    expect(screen.queryByLabelText("Record a dated valuation price after this purchase")).toBeNull();
    expect(screen.getByText("Manual dated prices are unavailable for this shared catalogue instrument.")).toBeTruthy();
  });

  it("requires a second explicit action and sends every financial field unchanged", async () => {
    const request = requestFrom(async (path) => {
      if (path === "/portfolio/positions") return positions();
      if (path === "/investment-catalogue/securities/resolve") {
        return { status: "matched", reason: "exact_public_identity", candidates: [candidate] };
      }
      if (path === "/investment-events/acquisitions") return event;
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");
    await resolveExactInstrument();
    fillPurchase();

    fireEvent.click(screen.getByRole("button", { name: "Review purchase" }));
    expect(request).not.toHaveBeenCalledWith("/investment-events/acquisitions", expect.anything());
    const confirmation = screen.getByRole("group", { name: "Confirm purchase" });
    expect(within(confirmation).getByText("3.12500000 units")).toBeTruthy();
    expect(within(confirmation).getByText("Net cash XAA -25.7500")).toBeTruthy();
    expect(document.activeElement).toBe(within(confirmation).getByRole("button", { name: "Confirm purchase" }));

    fireEvent.click(within(confirmation).getByRole("button", { name: "Confirm purchase" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "/investment-events/acquisitions",
      expect.objectContaining({ method: "POST" }),
    ));
    const call = vi.mocked(request).mock.calls.find(([path]) => path === "/investment-events/acquisitions");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      accountId: investmentAccount.id,
      securityId: candidate.id,
      eventDate: "2026-02-05",
      eventKind: "purchase",
      units: "3.12500000",
      price: "8.000000",
      grossAmount: "25.0000",
      charges: "0.7500",
      withheldTax: "0.0000",
      netAmount: "-25.7500",
      currency: "XAA",
      idempotencyKey: "00000000-0000-4000-8000-000000000080",
    });
  });

  it("offers explicit cash-funding review for the event returned by a completed purchase", async () => {
    const request = requestFrom(async (path) => {
      if (path === "/portfolio/positions") return positions();
      if (path === "/investment-catalogue/securities/resolve") {
        return { status: "matched", reason: "exact_public_identity", candidates: [candidate] };
      }
      if (path === "/investment-events/acquisitions") return event;
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");
    await resolveExactInstrument();
    fillPurchase();
    fireEvent.click(screen.getByRole("button", { name: "Review purchase" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm purchase" }));

    expect(await screen.findByRole("heading", { name: "Purchase funding" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Find cash entries" })).toBeTruthy();
    expect(vi.mocked(request).mock.calls.some(([path]) => path.startsWith("/transactions?"))).toBe(false);
  });

  it("uses the selected security currency for the purchase and confirmation", async () => {
    const otherCurrency = { ...candidate, currency: "XBB" };
    const request = requestFrom(async (path) => {
      if (path === "/portfolio/positions") return positions();
      if (path === "/investment-catalogue/securities/resolve") {
        return { status: "matched", reason: "exact_public_identity", candidates: [otherCurrency] };
      }
      if (path === "/investment-events/acquisitions") return event;
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");
    await resolveExactInstrument();
    fillPurchase();
    fireEvent.click(screen.getByRole("button", { name: "Review purchase" }));

    const confirmation = screen.getByRole("group", { name: "Confirm purchase" });
    expect(within(confirmation).getByText("XBB 25.0000")).toBeTruthy();
    expect(within(confirmation).getByText("Net cash XBB -25.7500")).toBeTruthy();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Confirm purchase" }));

    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "/investment-events/acquisitions",
      expect.objectContaining({ method: "POST" }),
    ));
    const call = vi.mocked(request).mock.calls.find(([path]) => path === "/investment-events/acquisitions");
    expect(JSON.parse(String(call?.[1]?.body)).currency).toBe("XBB");
  });

  it("records an optional dated valuation after the purchase and reloads the holding", async () => {
    let reads = 0;
    const request = requestFrom(async (path) => {
      if (path === "/portfolio/positions") return positions(reads++ === 0 ? [] : [position]);
      if (path === "/investment-catalogue/securities/resolve") {
        return { status: "matched", reason: "exact_public_identity", candidates: [candidate] };
      }
      if (path === "/investment-events/acquisitions") return event;
      if (path === `/investment-catalogue/securities/${candidate.id}/manual-prices`) {
        return { id: "00000000-0000-4000-8000-000000000090", securityId: candidate.id,
          priceDate: "2026-02-06", price: "9.000000", currency: "XAA", status: "recorded" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");
    await resolveExactInstrument();
    fillPurchase();
    fireEvent.click(screen.getByLabelText("Record a dated valuation price after this purchase"));
    fireEvent.change(screen.getByLabelText("Valuation date"), { target: { value: "2026-02-06" } });
    fireEvent.change(screen.getByLabelText("Valuation price"), { target: { value: "9.000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review purchase" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm purchase" }));

    expect(await screen.findByRole("article", { name: candidate.name })).toBeTruthy();
    const priceCall = vi.mocked(request).mock.calls.find(([path]) => path.includes("manual-prices"));
    expect(priceCall?.[0]).toBe(`/investment-catalogue/securities/${candidate.id}/manual-prices`);
    expect(JSON.parse(String(priceCall?.[1]?.body))).toEqual({ priceDate: "2026-02-06", price: "9.000000" });
    expect(screen.getByText("Purchase and dated price recorded.")).toBeTruthy();
  });

  it("keeps a refused mutation retryable with the same idempotency key", async () => {
    let attempts = 0;
    const request = requestFrom(async (path) => {
      if (path === "/portfolio/positions") return positions();
      if (path === "/investment-catalogue/securities/resolve") {
        return { status: "matched", reason: "exact_public_identity", candidates: [candidate] };
      }
      if (path === "/investment-events/acquisitions" && attempts++ === 0) {
        throw new ApiError(409, "synthetic refusal");
      }
      if (path === "/investment-events/acquisitions") return event;
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request);
    await screen.findByText("No investment holdings are recorded yet.");
    await resolveExactInstrument();
    fillPurchase();
    fireEvent.click(screen.getByRole("button", { name: "Review purchase" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm purchase" }));
    expect((await screen.findByRole("alert")).textContent).toContain("The purchase was not recorded");
    fireEvent.click(screen.getByRole("button", { name: "Retry purchase" }));
    await screen.findByText("Purchase recorded. A current value still needs a dated price.");

    const bodies = vi.mocked(request).mock.calls
      .filter(([path]) => path === "/investment-events/acquisitions")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies).toHaveLength(2);
    expect(bodies[0].idempotencyKey).toBe(bodies[1].idempotencyKey);
  });

  it("locks a recorded purchase while a refused dated price is edited and retried", async () => {
    let priceAttempts = 0;
    let reads = 0;
    let releasePrice!: (value: unknown) => void;
    const createKey = vi.fn(() => "00000000-0000-4000-8000-000000000080");
    const request = requestFrom(async (path) => {
      if (path === "/portfolio/positions") return positions(reads++ === 0 ? [] : [position]);
      if (path === "/investment-catalogue/securities/resolve") {
        return { status: "matched", reason: "exact_public_identity", candidates: [candidate] };
      }
      if (path === "/investment-events/acquisitions") return event;
      if (path.includes("manual-prices") && priceAttempts++ === 0) throw new ApiError(422, "synthetic refusal");
      if (path.includes("manual-prices")) return new Promise((resolve) => { releasePrice = resolve; });
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request, [investmentAccount], createKey);
    await screen.findByText("No investment holdings are recorded yet.");
    await resolveExactInstrument();
    fillPurchase();
    fireEvent.click(screen.getByLabelText("Record a dated valuation price after this purchase"));
    fireEvent.change(screen.getByLabelText("Valuation date"), { target: { value: "2026-02-06" } });
    fireEvent.change(screen.getByLabelText("Valuation price"), { target: { value: "9.000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review purchase" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm purchase" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The purchase was recorded, but the dated price was not");
    const priceGroup = screen.getByRole("group", { name: "Confirm dated price" });
    expect(within(priceGroup).getByText("The purchase is already recorded. Confirm only the remaining dated price.")).toBeTruthy();
    expect(within(priceGroup).queryByText(/appends a new owner-entered event/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit dated price" }));
    expect(screen.getByLabelText<HTMLInputElement>("Gross amount").disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Valuation price"), { target: { value: "9.100000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review dated price" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm dated price" }));
    expect(screen.getByRole("button", { name: "Recording dated price…" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Recording purchase…" })).toBeNull();
    releasePrice({ status: "recorded" });
    await screen.findByRole("article", { name: candidate.name });

    expect(vi.mocked(request).mock.calls.filter(([path]) => path === "/investment-events/acquisitions")).toHaveLength(1);
    expect(createKey).toHaveBeenCalledTimes(1);
    const priceBodies = vi.mocked(request).mock.calls.filter(([path]) => path.includes("manual-prices"));
    expect(JSON.parse(String(priceBodies[1][1]?.body)).price).toBe("9.100000");
  });

  it("reloads holdings after a committed purchase without resubmitting the acquisition", async () => {
    let reads = 0;
    let releaseHoldings!: (value: unknown) => void;
    const createKey = vi.fn(() => "00000000-0000-4000-8000-000000000080");
    const request = requestFrom(async (path) => {
      if (path === "/portfolio/positions") {
        reads += 1;
        if (reads === 1) return positions();
        if (reads === 2) throw new TypeError("offline");
        return new Promise((resolve) => { releaseHoldings = resolve; });
      }
      if (path === "/investment-catalogue/securities/resolve") {
        return { status: "matched", reason: "exact_public_identity", candidates: [candidate] };
      }
      if (path === "/investment-events/acquisitions") return event;
      if (path.includes("manual-prices")) return { status: "recorded" };
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    renderScreen(request, [investmentAccount], createKey);
    await screen.findByText("No investment holdings are recorded yet.");
    await resolveExactInstrument();
    fillPurchase();
    fireEvent.click(screen.getByLabelText("Record a dated valuation price after this purchase"));
    fireEvent.change(screen.getByLabelText("Valuation date"), { target: { value: "2026-02-06" } });
    fireEvent.change(screen.getByLabelText("Valuation price"), { target: { value: "9.000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review purchase" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm purchase" }));
    expect((await screen.findByRole("alert")).textContent).toContain("facts were recorded");
    const reloadGroup = screen.getByRole("group", { name: "Reload holdings" });
    expect(within(reloadGroup).getByText("The purchase and dated price are recorded. Reload holdings to display the resulting position.")).toBeTruthy();
    expect(within(reloadGroup).queryByText(/appends a new owner-entered event/i)).toBeNull();
    expect(within(reloadGroup).queryByText(/record valuation price/i)).toBeNull();
    const committedPrice = screen.getByLabelText<HTMLInputElement>("Valuation price");
    expect(committedPrice.disabled).toBe(true);
    fireEvent.change(committedPrice, { target: { value: "9.200000" } });
    expect(screen.getByRole("button", { name: "Reload holdings" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reload holdings" }));
    expect(screen.getByRole("button", { name: "Reloading holdings…" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Recording purchase…" })).toBeNull();
    releaseHoldings(positions([position]));
    await screen.findByRole("article", { name: candidate.name });

    expect(vi.mocked(request).mock.calls.filter(([path]) => path === "/investment-events/acquisitions")).toHaveLength(1);
    expect(createKey).toHaveBeenCalledTimes(1);
  });

  it("retries a generic initial holdings failure with exactly one new read", async () => {
    let reads = 0;
    const request = requestFrom(async (path) => {
      if (path !== "/portfolio/positions") throw new Error(`Unexpected synthetic route ${path}`);
      reads += 1;
      if (reads === 1) throw new TypeError("offline");
      return positions([position]);
    });
    renderScreen(request);

    expect(await screen.findByText("Recorded holdings could not be loaded.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("article", { name: candidate.name });
    expect(reads).toBe(2);
  });

  it.each([[401, "/login"], [403, "/unauthorized"]] as const)(
    "routes a protected investment read returning %i to %s",
    async (status, destination) => {
      const navigate = vi.fn();
      const inventory: AccountsResponse = { accounts: [investmentAccount], totals: [] };
      const request = requestFrom(async (path) => {
        if (path === "/household") return household;
        if (path === "/accounts") return inventory;
        if (path === "/portfolio/positions") throw new ApiError(status, "synthetic");
        throw new Error(`Unexpected synthetic route ${path}`);
      });

      render(<Workspace section="investments" request={request} navigate={navigate} />);

      await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
      expect(screen.queryByRole("heading", { name: "Investments" })).toBeNull();
    },
  );
});
