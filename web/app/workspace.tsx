"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { AccountsScreen } from "@/app/accounts/accounts-screen";
import { CoverageScreen } from "@/app/coverage/coverage-screen";
import { DocumentsScreen } from "@/app/documents/documents-screen";
import { OverviewScreen } from "@/app/overview/overview-screen";
import { CategoriesManager } from "@/app/settings/categories-manager";
import { DataQuality } from "@/app/settings/data-quality";
import { HouseholdForm } from "@/app/settings/household-form";
import { MerchantKnowledge } from "@/app/settings/merchant-knowledge";
import { RulesManager } from "@/app/settings/rules-manager";
import { ImportScreen } from "@/app/import/import-screen";
import { InvestmentEntryScreen } from "@/app/investments/entry-screen";
import { HoldingScreen } from "@/app/investments/holding-screen";
import { InvestmentImportScreen } from "@/app/investments/import/investment-import-screen";
import { PortfolioScreen } from "@/app/investments/portfolio-screen";
import { ReviewScreen } from "@/app/import/review-screen";
import { ClassificationReviewScreen } from "@/app/review/classification-review-screen";
import { PlansScreen } from "@/app/plans/plans-screen";
import { PayslipsScreen } from "@/app/salary/payslips-screen";
import { SpendingScreen } from "@/app/spending/spending-screen";
import { TransactionsScreen } from "@/app/transactions/transactions-screen";
import { WealthScreen } from "@/app/wealth/wealth-screen";
import { ApiError, apiFetch, errorText, LONG_REQUEST_TIMEOUT_MS } from "@/lib/api/client";
import type {
  AccountsResponse,
  CategoryRow,
  CurrencyRow,
  DocumentsResponse,
  FxRateResponse,
  HouseholdCurrency,
  HouseholdResponse,
  HouseholdUpdate,
  InstitutionRow,
  TransactionsResponse,
} from "@/types/api";

const PREFERENCE_KEY = "oneledger.reportingCurrency";

type Props = {
  section?: "overview" | "settings" | "accounts" | "transactions" | "spending" | "documents" | "investments" | "investments-entry" | "investments-holding" | "investments-import" | "import" | "review" | "classification-review" | "salary" | "plans" | "coverage" | "wealth";
  // The batch under review. A path parameter rather than a section of its own, because review
  // is where an import ends up rather than a sibling destination.
  batchId?: string;
  // The investment import being resumed at `/investments/import/{importId}`. Undefined for a
  // fresh upload — the screen tells the two apart by whether this is present.
  importId?: string;
  // The holding shown at `/investments/{holdingId}`.
  holdingId?: string;
  request?: typeof apiFetch;
  navigate?: (path: string) => void;
};

type FxState =
  | { status: "idle" }
  | { status: "loading"; requestedDate: string }
  | { status: "ready"; requestedDate: string; rates: Array<FxRateResponse> }
  | { status: "empty"; requestedDate: string }
  | { status: "service"; requestedDate: string };

type BackfillState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; written: number }
  | { status: "error"; message: string };

function authDestination(error: unknown) {
  if (!(error instanceof ApiError)) return undefined;
  if (error.status === 401) return "/login";
  if (error.status === 403) return "/unauthorized";
}

function configuredCurrency(household: HouseholdResponse): HouseholdCurrency {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(PREFERENCE_KEY);
  } catch {}
  return (
    household.currencies.find(({ code }) => code === stored) ??
    household.currencies.find(({ code }) => code === household.baseCurrency) ??
    household.currencies[0]
  );
}

function sectionFromPath() {
  if (typeof window === "undefined") return "overview";
  if (window.location.pathname === "/settings") return "settings";
  if (window.location.pathname === "/accounts") return "accounts";
  if (window.location.pathname === "/transactions") return "transactions";
  if (window.location.pathname === "/spending") return "spending";
  if (window.location.pathname === "/documents") return "documents";
  if (window.location.pathname.startsWith("/investments/import")) return "investments-import";
  if (window.location.pathname === "/investments/entry") return "investments-entry";
  if (window.location.pathname === "/investments") return "investments";
  if (window.location.pathname.startsWith("/investments/")) return "investments-holding";
  if (window.location.pathname === "/import") return "import";
  if (window.location.pathname === "/review") return "classification-review";
  if (window.location.pathname === "/salary") return "salary";
  if (window.location.pathname === "/plans") return "plans";
  if (window.location.pathname === "/coverage") return "coverage";
  if (window.location.pathname === "/wealth") return "wealth";
  return "overview";
}

function sourceDocumentIdFromLocation() {
  if (typeof window === "undefined") return undefined;
  return new URLSearchParams(window.location.search).get("sourceDocumentId") ?? undefined;
}

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

// The Spending screen's own drill-through (`/transactions?categoryId=…&dateFrom=…&dateTo=…`),
// the same query-string convention `sourceDocumentIdFromLocation` and the accounts screen's own
// `valuation` deep link already use. A category id not in the loaded taxonomy, or a date that is
// not a plain `YYYY-MM-DD` value, is silently ignored rather than handed to the filter form.
function categoryIdFromLocation(categories: Array<CategoryRow> | undefined) {
  if (typeof window === "undefined") return undefined;
  const id = new URLSearchParams(window.location.search).get("categoryId");
  if (!id || !categories) return undefined;
  return categories.some((row) => row.id === id) ? id : undefined;
}

function dateFromLocation(param: string) {
  if (typeof window === "undefined") return undefined;
  const value = new URLSearchParams(window.location.search).get(param);
  return value && DATE_PATTERN.test(value) ? value : undefined;
}

// The first page Workspace fetches must carry every deep-linked filter, or the ledger below the
// filter form is not the set the form describes.
function transactionsQueryFromLocation(categories: Array<CategoryRow> | undefined) {
  const parameters = new URLSearchParams({ limit: "50", offset: "0" });
  const sourceDocumentId = sourceDocumentIdFromLocation();
  if (sourceDocumentId) parameters.set("sourceDocumentId", sourceDocumentId);
  const categoryId = categoryIdFromLocation(categories);
  if (categoryId) parameters.set("categoryId", categoryId);
  const dateFrom = dateFromLocation("dateFrom");
  if (dateFrom) parameters.set("dateFrom", dateFrom);
  const dateTo = dateFromLocation("dateTo");
  if (dateTo) parameters.set("dateTo", dateTo);
  return parameters.toString();
}

function fxStatusText(fx: FxState) {
  if (fx.status === "loading") return `Checking stored FX rates for ${fx.requestedDate}.`;
  if (fx.status === "empty") return `No FX rates are stored for ${fx.requestedDate}.`;
  if (fx.status === "ready") {
    return `Loaded ${fx.rates.length} FX ${fx.rates.length === 1 ? "rate" : "rates"} for ${fx.requestedDate}.`;
  }
  return "";
}

export function Workspace({
  section,
  batchId,
  importId,
  holdingId,
  request = apiFetch,
  navigate = (path) => window.location.replace(path),
}: Props = {}) {
  const activeSection = section ?? sectionFromPath();
  const [household, setHousehold] = useState<HouseholdResponse>();
  const [currencies, setCurrencies] = useState<Array<CurrencyRow>>();
  const [inventory, setInventory] = useState<AccountsResponse>();
  const [institutions, setInstitutions] = useState<Array<InstitutionRow>>();
  const [ledger, setLedger] = useState<TransactionsResponse>();
  const [documents, setDocuments] = useState<DocumentsResponse>();
  const [categories, setCategories] = useState<Array<CategoryRow>>();
  const [currency, setCurrency] = useState<HouseholdCurrency>();
  const [state, setState] = useState<"loading" | "ready" | "service" | "leaving">("loading");
  const [attempt, setAttempt] = useState(0);
  const [destination, setDestination] = useState<string>();
  const [signingOut, setSigningOut] = useState(false);
  const [logoutFailed, setLogoutFailed] = useState(false);
  const [fxDate, setFxDate] = useState("");
  const [fx, setFx] = useState<FxState>({ status: "idle" });
  const fxRequest = useRef<{ id: number; controller: AbortController } | undefined>(undefined);
  const [backfillStart, setBackfillStart] = useState("");
  const [backfillEnd, setBackfillEnd] = useState("");
  const [backfill, setBackfill] = useState<BackfillState>({ status: "idle" });

  useEffect(() => {
    let current = true;
    request<HouseholdResponse>("/household")
      .then(async (configuration) => {
        if (!current) return;
        setHousehold(configuration);
        setCurrency(configuredCurrency(configuration));
        if (activeSection === "overview") {
          // The overview formats each card in that card's own currency, which need not be one
          // of the household's pair.
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          setCurrencies(catalogue);
        }
        if (activeSection === "settings") {
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          const taxonomy = await request<Array<CategoryRow>>("/categories");
          if (!current) return;
          setCurrencies(catalogue);
          setCategories(taxonomy);
        }
        if (activeSection === "transactions") {
          // The taxonomy is loaded first so a `categoryId` deep link can be checked against it
          // before the page is fetched: the first page must already be the filtered set the
          // filter form says is applied, exactly as `sourceDocumentId` has always behaved.
          const taxonomy = await request<Array<CategoryRow>>("/categories");
          if (!current) return;
          const page = await request<TransactionsResponse>(
            `/transactions?${transactionsQueryFromLocation(taxonomy)}`,
          );
          if (!current) return;
          const nextInventory = await request<AccountsResponse>("/accounts");
          if (!current) return;
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          setLedger(page);
          setCategories(taxonomy);
          setInventory(nextInventory);
          setCurrencies(catalogue);
        }
        if (activeSection === "spending") {
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          const taxonomy = await request<Array<CategoryRow>>("/categories");
          if (!current) return;
          setCurrencies(catalogue);
          setCategories(taxonomy);
        }
        if (activeSection === "documents") {
          const page = await request<DocumentsResponse>("/documents?limit=50&offset=0");
          if (!current) return;
          const nextInventory = await request<AccountsResponse>("/accounts");
          if (!current) return;
          setDocuments(page);
          setInventory(nextInventory);
        }
        if (activeSection === "classification-review") {
          const taxonomy = await request<Array<CategoryRow>>("/categories");
          if (!current) return;
          setCategories(taxonomy);
        }
        if (activeSection === "review" || activeSection === "import") {
          // The account list is the override picker's options; the currency catalogue formats
          // the statement's own declared figures. Neither is the boundary — the routes are.
          const nextInventory = await request<AccountsResponse>("/accounts");
          if (!current) return;
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          setInventory(nextInventory);
          setCurrencies(catalogue);
        }
        if (
          activeSection === "investments-entry" || activeSection === "investments-import"
          || activeSection === "investments" || activeSection === "investments-holding"
        ) {
          const nextInventory = await request<AccountsResponse>("/accounts");
          if (!current) return;
          setInventory(nextInventory);
        }
        if (activeSection === "accounts") {
          const nextInventory = await request<AccountsResponse>("/accounts");
          if (!current) return;
          const nextInstitutions = await request<Array<InstitutionRow>>("/institutions");
          if (!current) return;
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          setInventory(nextInventory);
          setInstitutions(nextInstitutions);
          setCurrencies(catalogue);
        }
        if (activeSection === "salary") {
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          const taxonomy = await request<Array<CategoryRow>>("/categories");
          if (!current) return;
          setCurrencies(catalogue);
          setCategories(taxonomy);
        }
        if (activeSection === "plans") {
          const nextInventory = await request<AccountsResponse>("/accounts");
          if (!current) return;
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          setInventory(nextInventory);
          setCurrencies(catalogue);
        }
        if (activeSection === "wealth") {
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          setCurrencies(catalogue);
        }
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        const next = authDestination(error);
        if (next) setDestination(next);
        else setState("service");
      });
    return () => {
      current = false;
    };
  }, [attempt, request, activeSection]);

  useEffect(() => () => fxRequest.current?.controller.abort(), []);

  useEffect(() => {
    if (destination) navigate(destination);
  }, [destination, navigate]);

  function retireFxRequest() {
    const id = (fxRequest.current?.id ?? 0) + 1;
    fxRequest.current?.controller.abort();
    fxRequest.current = { id, controller: new AbortController() };
    setFx({ status: "idle" });
  }

  async function logout() {
    setSigningOut(true);
    setLogoutFailed(false);
    retireFxRequest();
    try {
      await request<void>("/auth/logout", { method: "POST" });
      leaveForAuth("/login");
    } catch (error) {
      const next = authDestination(error);
      if (next) {
        leaveForAuth(next);
      } else {
        setLogoutFailed(true);
      }
    } finally {
      setSigningOut(false);
    }
  }

  function selectCurrency(next: HouseholdCurrency) {
    setCurrency(next);
    try {
      localStorage.setItem(PREFERENCE_KEY, next.code);
    } catch {}
  }

  const leaveForAuth = useCallback((path: string) => {
    fxRequest.current?.controller.abort();
    setHousehold(undefined);
    setCurrency(undefined);
    setInventory(undefined);
    setInstitutions(undefined);
    setLedger(undefined);
    setDocuments(undefined);
    setCategories(undefined);
    setState("leaving");
    setDestination(path);
  }, []);

  // One stable wrapper, reused by every section below: an inline arrow recreated each render
  // would give each section a new `request` reference every time *anything* in Workspace
  // re-renders, and a section whose data-loading effect depends on `request` would reload on
  // every unrelated keystroke or currency toggle rather than once per mount.
  const wrappedRequest = useCallback<typeof apiFetch>(
    async (path, init) => {
      try {
        return await request(path, init);
      } catch (error) {
        const next = authDestination(error);
        if (next) leaveForAuth(next);
        throw error;
      }
    },
    [request, leaveForAuth],
  );

  async function loadFx() {
    const requestedDate = fxDate;
    if (!requestedDate || signingOut) return;
    fxRequest.current?.controller.abort();
    const controller = new AbortController();
    const id = (fxRequest.current?.id ?? 0) + 1;
    fxRequest.current = { id, controller };
    setFx({ status: "loading", requestedDate });
    try {
      const rates = await request<Array<FxRateResponse>>(`/fx/${encodeURIComponent(requestedDate)}`, {
        signal: controller.signal,
      });
      if (fxRequest.current?.id !== id) return;
      setFx(rates.length ? { status: "ready", requestedDate, rates } : { status: "empty", requestedDate });
    } catch (error) {
      if (fxRequest.current?.id !== id) return;
      const next = authDestination(error);
      if (next) leaveForAuth(next);
      else setFx({ status: "service", requestedDate });
    }
  }

  async function runBackfill() {
    if (!backfillStart || !backfillEnd || signingOut) return;
    setBackfill({ status: "loading" });
    try {
      const result = await request<{ written: number }>(
        `/fx/backfill?start=${encodeURIComponent(backfillStart)}&end=${encodeURIComponent(backfillEnd)}`,
        { method: "POST", timeoutMs: LONG_REQUEST_TIMEOUT_MS },
      );
      setBackfill({ status: "ready", written: result.written });
    } catch (error) {
      const next = authDestination(error);
      if (next) leaveForAuth(next);
      else setBackfill({ status: "error", message: errorText(error, "Rates could not be backfilled.") });
    }
  }

  return (
    <div className="site-shell">
      <header className="masthead app-toolbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">OL</span>
          <span>OneLedger</span>
        </div>
        {state === "ready" && household && currency && (
          <div className="masthead-actions">
            <div className="currency-toggle" role="group" aria-label="Reporting currency">
              {household.currencies.map((option) => (
                <button
                  aria-pressed={currency.code === option.code}
                  key={option.code}
                  onClick={() => selectCurrency(option)}
                  type="button"
                >{option.code}</button>
              ))}
            </div>
            <span
              aria-label={`${household.ownerDisplayName} account`}
              className="owner-badge"
              title={household.ownerDisplayName}
            >
              {household.ownerDisplayName.trim().slice(0, 1).toUpperCase()}
            </span>
            <button className="text-action" disabled={signingOut} onClick={logout} type="button">
              {signingOut ? "Signing out…" : logoutFailed ? "Retry sign out" : "Sign out"}
            </button>
          </div>
        )}
      </header>

      {state === "loading" && <main className="state-panel"><p>Checking owner access…</p></main>}
      {state === "leaving" && <main className="state-panel"><p>Closing owner workspace…</p></main>}
      {state === "service" && (
        <main className="state-panel">
          <p className="eyebrow">Connection problem</p>
          <h1 className="state-title">OneLedger is unavailable</h1>
          <p className="lede">The owner workspace could not be loaded. Check the API service and try again.</p>
          <button className="primary-action" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }} type="button">Try again</button>
        </main>
      )}
      {state === "ready" && household && currency && (
        <main className="workspace-grid">
          <nav className="workspace-nav" aria-label="Workspace">
            <Link aria-current={activeSection === "overview" ? "page" : undefined} href="/">Overview</Link>
            <Link aria-current={activeSection === "accounts" ? "page" : undefined} href="/accounts">Accounts</Link>
            <Link
              aria-current={
                activeSection === "import" || activeSection === "review" ? "page" : undefined
              }
              href="/import"
            >Import</Link>
            <Link aria-current={activeSection === "transactions" ? "page" : undefined} href="/transactions">Transactions</Link>
            <Link aria-current={activeSection === "spending" ? "page" : undefined} href="/spending">Spending</Link>
            <Link aria-current={activeSection === "documents" ? "page" : undefined} href="/documents">Documents</Link>
            <Link
              aria-current={
                activeSection === "investments"
                || activeSection === "investments-entry"
                || activeSection === "investments-holding"
                  ? "page"
                  : undefined
              }
              href="/investments"
            >Investments</Link>
            <Link aria-current={activeSection === "investments-import" ? "page" : undefined} href="/investments/import">Import investments</Link>
            <Link aria-current={activeSection === "classification-review" ? "page" : undefined} href="/review">Classify</Link>
            <Link aria-current={activeSection === "salary" ? "page" : undefined} href="/salary">Salary</Link>
            <Link aria-current={activeSection === "plans" ? "page" : undefined} href="/plans">Plans</Link>
            <Link aria-current={activeSection === "coverage" ? "page" : undefined} href="/coverage">Coverage</Link>
            <Link aria-current={activeSection === "wealth" ? "page" : undefined} href="/wealth">Wealth</Link>
            <Link aria-current={activeSection === "settings" ? "page" : undefined} href="/settings">Settings</Link>
          </nav>

          {activeSection === "overview" && currencies && (
            <OverviewScreen
              currencies={currencies}
              currency={currency}
              household={household}
              request={wrappedRequest}
              logoutFailed={logoutFailed}
            />
          )}

          {activeSection === "import" && inventory && currencies && (
            <ImportScreen
              accounts={inventory.accounts}
              currencies={currencies}
              request={wrappedRequest}
            />
          )}

          {activeSection === "review" && currencies && inventory && batchId && (
            <ReviewScreen
              accounts={inventory.accounts}
              batchId={batchId}
              currencies={currencies}
              currency={currency}
              household={household}
              request={wrappedRequest}
            />
          )}

          {activeSection === "accounts" && inventory && institutions && currencies && household && (
            <AccountsScreen
              accounts={inventory.accounts}
              baseCurrency={household.baseCurrency}
              currencies={currencies}
              institutions={institutions}
              onChanged={() => setAttempt((value) => value + 1)}
              request={wrappedRequest}
              totals={inventory.totals}
            />
          )}

          {activeSection === "transactions" && ledger && categories && inventory && currencies && (
            <TransactionsScreen
              accounts={inventory.accounts}
              categories={categories}
              currencies={currencies}
              currency={currency}
              household={household}
              initialSourceDocumentId={sourceDocumentIdFromLocation()}
              initialCategoryId={categoryIdFromLocation(categories)}
              initialDateFrom={dateFromLocation("dateFrom")}
              initialDateTo={dateFromLocation("dateTo")}
              onChanged={() => setAttempt((value) => value + 1)}
              request={wrappedRequest}
              total={ledger.total}
              transactions={ledger.transactions}
            />
          )}

          {activeSection === "spending" && categories && currencies && currency && (
            <SpendingScreen
              categories={categories}
              currencies={currencies}
              currency={currency}
              request={wrappedRequest}
            />
          )}

          {activeSection === "documents" && documents && inventory && (
            <DocumentsScreen
              accounts={inventory.accounts}
              documents={documents.documents}
              request={wrappedRequest}
              total={documents.total}
            />
          )}

          {activeSection === "investments" && inventory && (
            <PortfolioScreen
              accounts={inventory.accounts}
              request={wrappedRequest}
            />
          )}

          {activeSection === "investments-holding" && holdingId && inventory && (
            <HoldingScreen
              accounts={inventory.accounts}
              holdingId={holdingId}
              request={wrappedRequest}
            />
          )}

          {activeSection === "investments-entry" && inventory && (
            <InvestmentEntryScreen
              accounts={inventory.accounts}
              request={wrappedRequest}
            />
          )}

          {activeSection === "investments-import" && inventory && (
            <InvestmentImportScreen
              accounts={inventory.accounts}
              importId={importId}
              request={wrappedRequest}
            />
          )}

          {activeSection === "salary" && currencies && categories && (
            <PayslipsScreen
              currencies={currencies}
              categories={categories}
              request={wrappedRequest}
            />
          )}

          {activeSection === "plans" && inventory && currencies && household && (
            <PlansScreen
              accounts={inventory.accounts}
              currencies={currencies}
              householdCurrencies={{ base: household.baseCurrency, secondary: household.secondaryCurrency }}
              request={wrappedRequest}
            />
          )}

          {activeSection === "coverage" && (
            <CoverageScreen
              request={wrappedRequest}
            />
          )}

          {activeSection === "wealth" && currencies && (
            <WealthScreen
              currencies={currencies}
              currency={currency}
              request={wrappedRequest}
            />
          )}

          {activeSection === "classification-review" && categories && (
            <ClassificationReviewScreen
              categories={categories}
              request={wrappedRequest}
            />
          )}

          {activeSection === "settings" && (
            <section className="workspace-content" aria-labelledby="settings-title">
              <p className="eyebrow">Owner settings</p>
              <h1 className="workspace-title" id="settings-title">Settings</h1>
              <p className="lede">
                Manage the household, data quality, categories, classification rules and stored
                exchange rates. Choose a date below to inspect recorded exchange rates, their
                sources and whether a prior rate was carried forward.
              </p>
              {logoutFailed && <p className="inline-alert" role="alert">OneLedger could not confirm that the server session was revoked. Your workspace remains open; retry sign out.</p>}

              <DataQuality
                request={wrappedRequest}
              />

              {currencies && (
                <HouseholdForm
                  currencies={currencies}
                  household={household}
                  onSaved={() => setAttempt((value) => value + 1)}
                  save={(body: HouseholdUpdate) =>
                    wrappedRequest<void>("/household", {
                      method: "PATCH",
                      body: JSON.stringify(body),
                      headers: { "Content-Type": "application/json" },
                    })
                  }
                />
              )}

              {categories && (
                <CategoriesManager
                  categories={categories}
                  onChanged={() => setAttempt((value) => value + 1)}
                  request={wrappedRequest}
                />
              )}

              {categories && (
                <RulesManager
                  categories={categories}
                  onChanged={() => setAttempt((value) => value + 1)}
                  request={wrappedRequest}
                />
              )}

              {categories && (
                <MerchantKnowledge
                  categories={categories}
                  request={wrappedRequest}
                />
              )}

              <section className="fx-card" aria-labelledby="fx-title">
                <div>
                  <p className="section-label">FX inspector</p>
                  <h2 id="fx-title">Stored rate</h2>
                </div>
                <label className="date-field">
                  <span>FX date</span>
                  <input
                    onChange={(event) => setFxDate(event.target.value)}
                    required
                    type="date"
                    value={fxDate}
                  />
                </label>
                <button className="primary-action" disabled={!fxDate || signingOut} onClick={loadFx} type="button">
                  {fx.status === "service" ? "Retry FX rate" : "Check FX rate"}
                </button>

                <p className="fx-empty" role="status" aria-atomic="true">{fxStatusText(fx)}</p>
                {fx.status === "service" && <p className="inline-alert" role="alert">FX rates could not be loaded. Check the API service and retry.</p>}
                {fx.status === "ready" && (
                  <div className="fx-results">
                    {fx.rates.map((item) => (
                      <article className="fx-result" key={`${item.base}-${item.quote}`}>
                        <div>
                          <span className="section-label">{item.base}/{item.quote}</span>
                          <strong>{item.rate}</strong>
                        </div>
                        <dl>
                          <div><dt>Source</dt><dd>{item.source}</dd></div>
                          <div><dt>Carry</dt><dd>{item.carried ? `Carried from ${item.carried_from}` : "Direct rate"}</dd></div>
                          <div><dt>As of</dt><dd>As of {fx.requestedDate}</dd></div>
                        </dl>
                      </article>
                    ))}
                  </div>
                )}
              </section>

              <section className="fx-card" aria-labelledby="fx-backfill-title">
                <div>
                  <p className="section-label">Exchange rates</p>
                  <h2 id="fx-backfill-title">Backfill rates</h2>
                </div>
                <div className="fx-backfill-dates">
                  <label className="date-field">
                    <span>Start date</span>
                    <input
                      onChange={(event) => setBackfillStart(event.target.value)}
                      required
                      type="date"
                      value={backfillStart}
                    />
                  </label>
                  <label className="date-field">
                    <span>End date</span>
                    <input
                      onChange={(event) => setBackfillEnd(event.target.value)}
                      required
                      type="date"
                      value={backfillEnd}
                    />
                  </label>
                </div>
                <button
                  className="primary-action"
                  disabled={!backfillStart || !backfillEnd || signingOut || backfill.status === "loading"}
                  onClick={() => void runBackfill()}
                  type="button"
                >
                  {backfill.status === "loading" ? "Backfilling…" : "Backfill rates"}
                </button>

                {backfill.status === "ready" && (
                  <p className="fx-empty" role="status" aria-atomic="true">
                    Wrote {backfill.written} {backfill.written === 1 ? "rate" : "rates"}.
                  </p>
                )}
                {backfill.status === "error" && (
                  <p className="inline-alert" role="alert">{backfill.message}</p>
                )}
              </section>
            </section>
          )}
        </main>
      )}

      <footer className="footer"><p>Private owner workspace</p></footer>
    </div>
  );
}
