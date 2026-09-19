"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { SidebarProvider } from "@/components/ui/sidebar";

import { AccountsScreen } from "@/app/accounts/accounts-screen";
import { CoverageScreen } from "@/app/coverage/coverage-screen";
import { DocumentsScreen } from "@/app/documents/documents-screen";
import { OverviewScreen } from "@/app/overview/overview-screen";
import { SettingsScreen } from "@/app/settings/settings-screen";
import { ImportScreen } from "@/app/import/import-screen";
import { InvestmentEntryScreen } from "@/app/investments/entry-screen";
import { HoldingScreen } from "@/app/investments/holding-screen";
import { InvestmentImportScreen } from "@/app/investments/import/investment-import-screen";
import { PortfolioScreen } from "@/app/investments/portfolio-screen";
import { ReviewScreen } from "@/app/import/review-screen";
import { ClassificationReviewScreen } from "@/app/review/classification-review-screen";
import { PlansScreen } from "@/app/plans/plans-screen";
import { RecurringScreen } from "@/app/recurring/recurring-screen";
import { PayslipsScreen } from "@/app/salary/payslips-screen";
import { SpendingScreen } from "@/app/spending/spending-screen";
import { SubscriptionDetail } from "@/app/subscriptions/subscription-detail";
import { SubscriptionsScreen } from "@/app/subscriptions/subscriptions-screen";
import { TransactionsScreen } from "@/app/transactions/transactions-screen";
import { WealthScreen } from "@/app/wealth/wealth-screen";
import { AppShell } from "@/app/ui/app-shell";
import { ApiError, apiFetch } from "@/lib/api/client";
import type {
  AccountsResponse,
  CategoryRow,
  CurrencyRow,
  DocumentsResponse,
  HouseholdCurrency,
  HouseholdResponse,
  InstitutionRow,
  TransactionsResponse,
} from "@/types/api";

const PREFERENCE_KEY = "oneledger.reportingCurrency";

type Props = {
  section?: "overview" | "settings" | "accounts" | "transactions" | "spending" | "subscriptions" | "subscriptions-detail" | "recurring" | "documents" | "investments" | "investments-entry" | "investments-holding" | "investments-import" | "import" | "review" | "classification-review" | "salary" | "plans" | "coverage" | "wealth";
  // The batch under review. A path parameter rather than a section of its own, because review
  // is where an import ends up rather than a sibling destination.
  batchId?: string;
  // The investment import being resumed at `/investments/import/{importId}`. Undefined for a
  // fresh upload — the screen tells the two apart by whether this is present.
  importId?: string;
  // The holding shown at `/investments/{holdingId}`.
  holdingId?: string;
  // The subscription shown at `/subscriptions/{seriesId}`.
  seriesId?: string;
  request?: typeof apiFetch;
  navigate?: (path: string) => void;
};

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
  if (window.location.pathname === "/subscriptions") return "subscriptions";
  if (window.location.pathname.startsWith("/subscriptions/")) return "subscriptions-detail";
  if (window.location.pathname === "/recurring") return "recurring";
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

export function Workspace({
  section,
  batchId,
  importId,
  holdingId,
  seriesId,
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
  const [fxEpoch, setFxEpoch] = useState(0);

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
        if (activeSection === "subscriptions" || activeSection === "subscriptions-detail") {
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          setCurrencies(catalogue);
        }
        if (activeSection === "recurring") {
          const catalogue = await request<Array<CurrencyRow>>("/currencies");
          if (!current) return;
          setCurrencies(catalogue);
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

  useEffect(() => {
    if (destination) navigate(destination);
  }, [destination, navigate]);

  async function logout() {
    setSigningOut(true);
    setLogoutFailed(false);
    setFxEpoch((value) => value + 1);
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
    setFxEpoch((value) => value + 1);
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

  return (
    <SidebarProvider className="site-shell">
      {state === "loading" && <main className="state-panel"><Skeleton aria-hidden="true" className="state-skeleton" /><p>Checking owner access…</p></main>}
      {state === "leaving" && <main className="state-panel"><Skeleton aria-hidden="true" className="state-skeleton" /><p>Closing owner workspace…</p></main>}
      {state === "service" && (
        <main className="state-panel">
          <Empty>
            <EmptyHeader>
              <p className="eyebrow">Connection problem</p>
              <EmptyTitle><h1 className="state-title">OneLedger is unavailable</h1></EmptyTitle>
              <EmptyDescription>The owner workspace could not be loaded. Check the API service and try again.</EmptyDescription>
            </EmptyHeader>
            <Button onClick={() => { setState("loading"); setAttempt((value) => value + 1); }} type="button">Try again</Button>
          </Empty>
        </main>
      )}
      {state === "ready" && household && currency && (
        <AppShell
          activeSection={activeSection}
          currency={currency}
          household={household}
          logoutFailed={logoutFailed}
          navigate={navigate}
          onCurrencyChange={selectCurrency}
          onLogout={() => void logout()}
          request={wrappedRequest}
          signingOut={signingOut}
        >

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

          {activeSection === "subscriptions" && currencies && (
            <SubscriptionsScreen
              currencies={currencies}
              request={wrappedRequest}
            />
          )}

          {activeSection === "subscriptions-detail" && seriesId && currencies && household && (
            <SubscriptionDetail
              baseCurrency={household.baseCurrency}
              currencies={currencies}
              request={wrappedRequest}
              seriesId={seriesId}
            />
          )}

          {activeSection === "recurring" && currencies && (
            <RecurringScreen
              currencies={currencies}
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
            <SettingsScreen
              categories={categories}
              currencies={currencies}
              fxEpoch={fxEpoch}
              household={household}
              logoutFailed={logoutFailed}
              onChanged={() => setAttempt((value) => value + 1)}
              request={wrappedRequest}
              signingOut={signingOut}
            />
          )}
        </AppShell>
      )}

      <footer className="footer"><p>Private owner workspace</p></footer>
    </SidebarProvider>
  );
}
