"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MoreHorizontalIcon, PlusIcon } from "lucide-react";

import { errorText, type apiFetch } from "@/lib/api/client";
import { apiUrl } from "@/lib/api/url";
import { ClassifyPrompt } from "@/app/transactions/classify-prompt";
import { CADENCE_TEXT } from "@/app/recurring/recurring-screen";
import { PeerPicker, TransferQueue } from "@/app/transactions/transfer-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { DataToolbar } from "@/app/ui/data-toolbar";
import { MetricStrip } from "@/app/ui/metric-strip";
import { RowActions } from "@/app/ui/row-actions";
import { StatusLabel } from "@/app/ui/status-label";
import { formatMoney, moneyText } from "@/lib/money/format";
import type {
  AccountRow,
  CategoryRow,
  CurrencyRow,
  HouseholdCurrency,
  HouseholdResponse,
  TransactionRow,
  TransactionSummary,
  TransactionsResponse,
} from "@/types/api";

type Props = {
  transactions: Array<TransactionRow>;
  total: number;
  summary: TransactionSummary;
  accounts: Array<AccountRow>;
  categories: Array<CategoryRow>;
  currencies: Array<CurrencyRow>;
  household: HouseholdResponse;
  currency: HouseholdCurrency;
  request: typeof apiFetch;
  onChanged: () => void;
  // Carried from `/transactions?sourceDocumentId=…` — the Documents screen's own link — so the
  // filter that already produced the first page is what the form shows too.
  initialSourceDocumentId?: string;
  // Carried from a workspace-search result. It remains part of reloads until the owner clears
  // filters, so an action on the selected row cannot silently replace it with the whole ledger.
  initialTransactionId?: string;
  // Carried from the Spending screen's own drill-through link
  // (`/transactions?categoryId=…&dateFrom=…&dateTo=…`); Workspace has already checked the
  // category id against the loaded taxonomy and the dates against a plain date pattern, so
  // whatever arrives here is applied as-is.
  initialCategoryId?: string;
  initialDateFrom?: string;
  initialDateTo?: string;
};

// The manual-entry set: linked types (transfers, investments, loans) arrive with their
// features, and the importer stamps its own.
const MANUAL_TYPES = ["expense", "income", "fee", "interest", "tax", "refund"] as const;
// The FILTER offers every type the ledger can hold — rows of the linked types become visible
// as their features land, and an unfilterable visible row would be a dead end.
const ALL_TYPES = [
  ...MANUAL_TYPES,
  "transfer_in",
  "transfer_out",
  "investment_buy",
  "investment_sell",
  "investment_income",
  "investment_fee",
  "loan_disbursement",
  "loan_repayment",
  "adjustment",
  "opening_balance",
] as const;

const PAGE = 50;

// The engine's own eligibility check (`v_transaction_reportable`, `txn_type <> 'income' AND
// amount_base < 0`) run client-side. The row carries no series-membership field, so this offers
// the action on every eligible charge and lets a 409 from `/recurring/from-transaction` say when
// one is already in a series — never a second source of truth about series membership.
function isSpendingCharge(row: TransactionRow): boolean {
  return (
    row.amount.startsWith("-") && row.txnType !== "income" && !row.transferId && !row.isSplitParent
  );
}

type Filters = {
  transactionId: string;
  accountId: string;
  categoryId: string;
  txnType: string;
  dateFrom: string;
  dateTo: string;
  q: string;
  sort: string;
  direction: string;
  amountMin: string;
  amountMax: string;
  currency: string;
  country: string;
  tag: string;
  reviewState: string;
  classifiedBy: string;
  merchantId: string;
  sourceDocumentId: string;
};

const NO_FILTERS: Filters = {
  transactionId: "",
  accountId: "",
  categoryId: "",
  txnType: "",
  dateFrom: "",
  dateTo: "",
  q: "",
  sort: "bookedDate",
  direction: "desc",
  amountMin: "",
  amountMax: "",
  currency: "",
  country: "",
  tag: "",
  reviewState: "",
  classifiedBy: "",
  merchantId: "",
  sourceDocumentId: "",
};

const CLASSIFIED_BY_VALUES: ReadonlyArray<string> = [
  "unclassified",
  "seed_rule",
  "merchant_alias",
  "recurring_series",
  "prior_match",
  "user_rule",
  "user_manual",
  "ai",
];

function query(filters: Filters, offset: number) {
  const parameters = filterParameters(filters);
  parameters.set("limit", String(PAGE));
  parameters.set("offset", String(offset));
  return `/transactions?${parameters.toString()}`;
}

function filterParameters(filters: Filters) {
  const parameters = new URLSearchParams();
  if (filters.transactionId) parameters.set("transactionId", filters.transactionId);
  if (filters.accountId) parameters.set("accountId", filters.accountId);
  if (filters.categoryId) parameters.set("categoryId", filters.categoryId);
  if (filters.txnType) parameters.set("txnType", filters.txnType);
  if (filters.dateFrom) parameters.set("dateFrom", filters.dateFrom);
  if (filters.dateTo) parameters.set("dateTo", filters.dateTo);
  if (filters.q.trim()) parameters.set("q", filters.q.trim());
  if (filters.amountMin.trim()) parameters.set("amountMin", filters.amountMin.trim());
  if (filters.amountMax.trim()) parameters.set("amountMax", filters.amountMax.trim());
  if (filters.currency) parameters.set("currency", filters.currency);
  if (filters.country.trim()) parameters.set("country", filters.country.trim());
  if (filters.tag.trim()) parameters.set("tag", filters.tag.trim());
  if (filters.reviewState) parameters.set("reviewState", filters.reviewState);
  if (filters.classifiedBy) parameters.set("classifiedBy", filters.classifiedBy);
  if (filters.merchantId.trim()) parameters.set("merchantId", filters.merchantId.trim());
  if (filters.sourceDocumentId.trim())
    parameters.set("sourceDocumentId", filters.sourceDocumentId.trim());
  if (filters.sort !== "bookedDate") parameters.set("sort", filters.sort);
  if (filters.direction !== "desc") parameters.set("direction", filters.direction);
  return parameters;
}

const FILTER_KEYS = Object.keys(NO_FILTERS) as Array<keyof Filters>;

function filtersFromLocation(fallback: Filters): Filters {
  if (typeof window === "undefined") return fallback;
  const parameters = new URLSearchParams(window.location.search);
  const next = { ...fallback };
  for (const key of FILTER_KEYS) {
    const value = parameters.get(key);
    if (value !== null) next[key] = value;
  }
  if (!ALL_TYPES.includes(next.txnType as (typeof ALL_TYPES)[number])) next.txnType = "";
  if (!["bookedDate", "amount", "description", "account"].includes(next.sort)) {
    next.sort = NO_FILTERS.sort;
  }
  if (!["asc", "desc"].includes(next.direction)) next.direction = NO_FILTERS.direction;
  if (!["reviewed", "unreviewed"].includes(next.reviewState)) next.reviewState = "";
  if (!CLASSIFIED_BY_VALUES.includes(next.classifiedBy)) next.classifiedBy = "";
  return next;
}

function writeFiltersToLocation(filters: Filters) {
  if (typeof window === "undefined") return;
  const parameters = filterParameters(filters);
  const search = parameters.toString();
  window.history.pushState(null, "", `${window.location.pathname}${search ? `?${search}` : ""}`);
}

export function TransactionsScreen({
  transactions,
  total,
  summary,
  accounts,
  categories,
  currencies,
  household,
  currency,
  request,
  onChanged,
  initialSourceDocumentId,
  initialTransactionId,
  initialCategoryId,
  initialDateFrom,
  initialDateTo,
}: Props) {
  const initialFilters: Filters = {
    ...NO_FILTERS,
    transactionId: initialTransactionId ?? "",
    sourceDocumentId: initialSourceDocumentId ?? "",
    categoryId: initialCategoryId ?? "",
    dateFrom: initialDateFrom ?? "",
    dateTo: initialDateTo ?? "",
  };
  const firstFilters = filtersFromLocation(initialFilters);
  const [rows, setRows] = useState(transactions);
  const [count, setCount] = useState(total);
  const [transactionSummary, setTransactionSummary] = useState(summary);
  const [summaryReady, setSummaryReady] = useState(true);
  const [filters, setFilters] = useState<Filters>(firstFilters);
  // Pagination continues the APPLIED set: edited-but-unapplied controls must not leak into
  // "Load more", or the ledger would append one result set onto another.
  const [applied, setApplied] = useState<Filters>(firstFilters);
  const requestSequence = useRef(0);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [entryOpen, setEntryOpen] = useState(false);
  // The row whose peers are open for manual linking, if any.
  const [linking, setLinking] = useState<TransactionRow>();
  const [classifying, setClassifying] = useState<{
    row: TransactionRow;
    categoryId: string;
  }>();
  const [draft, setDraft] = useState({
    accountId: "",
    bookedDate: "",
    description: "",
    amount: "",
    txnType: "expense",
    categoryId: "",
    notes: "",
  });

  // "Add as subscription" (Task 7): one row's inline draft at a time, mirroring the rename/cadence
  // forms other screens already use. `subscribeKey` is one idempotency key per draft: minted when
  // a draft opens and reused on every retry of that draft.
  const [subscribingId, setSubscribingId] = useState<string>();
  const [subscribeForm, setSubscribeForm] = useState({
    cadence: "monthly",
    customIntervalDays: null as number | null,
    label: "",
  });
  const [subscribeKey, setSubscribeKey] = useState(() => crypto.randomUUID());
  const [subscribeBusy, setSubscribeBusy] = useState(false);
  const [subscribeProblem, setSubscribeProblem] = useState<string>();

  const categoryName = new Map(categories.map((row) => [row.id, row.name]));
  // A child option names its parent too — "Insurance → Health" and "Health" are different
  // classifications, and a bare leaf name cannot tell them apart.
  function categoryLabel(row: CategoryRow) {
    if (!row.parentId) return row.name;
    const parent = categoryName.get(row.parentId);
    return parent ? `${parent} → ${row.name}` : row.name;
  }
  const active = accounts.filter((account) => account.isActive);

  // Original currency first, converted second — never the conversion alone (UI section 4).
  function original(row: TransactionRow) {
    return moneyText(row.amount, row.currency, currencies);
  }

  // The bank's own conversion, when it made one: the merchant-currency figure that the
  // account amount settled (product contract: the actual original stays visible).
  function bankOriginal(row: TransactionRow) {
    if (!row.originalAmount || !row.originalCurrency) return undefined;
    return moneyText(row.originalAmount, row.originalCurrency, currencies);
  }

  function converted(row: TransactionRow) {
    if (row.currency === currency.code) return undefined;
    const value = currency.code === household.baseCurrency ? row.amountBase : row.amountSecondary;
    return formatMoney(value, currency);
  }

  const transferLegs = new Map<string, Array<TransactionRow>>();
  for (const row of rows) {
    if (!row.transferId) continue;
    transferLegs.set(row.transferId, [...(transferLegs.get(row.transferId) ?? []), row]);
  }

  function transferMovement(row: TransactionRow) {
    if (!row.transferId) return undefined;
    const pair = transferLegs.get(row.transferId);
    if (!pair || pair.length !== 2) return undefined;
    const source = pair.find((leg) => leg.amount.startsWith("-")) ?? pair[0];
    const destination = pair.find((leg) => leg.id !== source.id);
    return destination && source.id === row.id ? { source, destination } : undefined;
  }

  // The frozen rate the displayed conversion actually used: rate, date and carried status
  // together, so the converted figure is auditable (UI section 4).
  function rateUsed(row: TransactionRow) {
    return currency.code === household.baseCurrency ? row.fxRateToBase : row.fxRateToSecondary;
  }

  const reload = useCallback(async (nextFilters: Filters, offset: number, append: boolean) => {
    const sequence = ++requestSequence.current;
    setBusy(true);
    if (!append) setSummaryReady(false);
    setProblem(undefined);
    try {
      const page = await request<TransactionsResponse>(query(nextFilters, offset));
      if (sequence !== requestSequence.current) return;
      setRows((current) => (append ? [...current, ...page.transactions] : page.transactions));
      setCount(page.total);
      if (page.summary) {
        setTransactionSummary(page.summary);
        setSummaryReady(true);
      }
    } catch {
      if (sequence !== requestSequence.current) return;
      setProblem("The ledger could not be loaded. Check the API service and retry.");
    } finally {
      if (sequence === requestSequence.current) setBusy(false);
    }
  }, [request]);

  useEffect(() => {
    function restoreFromHistory() {
      const restored = filtersFromLocation(NO_FILTERS);
      setFilters(restored);
      setApplied(restored);
      void reload(restored, 0, false);
    }
    window.addEventListener("popstate", restoreFromHistory);
    return () => {
      window.removeEventListener("popstate", restoreFromHistory);
      requestSequence.current += 1;
    };
  }, [reload]);

  // A link or an unlink changes two rows this ledger is showing and the balances behind
  // them, so the applied view reloads and the workspace is told.
  function refresh() {
    void reload(applied, 0, false);
    onChanged();
  }

  async function patchRow(
    row: TransactionRow,
    categoryId: string | null,
    notes: string | null,
    bubbleError = false,
  ) {
    const categoryChanged = categoryId !== row.categoryId;
    setBusy(true);
    setProblem(undefined);
    try {
      const updated = await request<TransactionRow>(`/transactions/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify({ categoryId, notes }),
        headers: { "Content-Type": "application/json" },
      });
      if (categoryChanged || applied.categoryId || applied.reviewState || applied.classifiedBy) {
        // A category change also changes review/classification state and the filtered summary.
        // A stale local replacement may therefore show wrong totals or make the next page's
        // offset skip a real match.
        await reload(applied, 0, false);
      } else {
        setRows((current) => current.map((one) => (one.id === updated.id ? updated : one)));
      }
    } catch (error) {
      if (bubbleError) throw error;
      setProblem("The change was not saved. Retry.");
    } finally {
      setBusy(false);
    }
  }

  function recategorize(row: TransactionRow, categoryId: string) {
    if (row.classifiedBy === "unclassified" && categoryId) {
      setClassifying({ row, categoryId });
      return Promise.resolve();
    }
    return patchRow(row, categoryId || null, row.notes);
  }

  function saveNote(row: TransactionRow, note: string) {
    const value = note.trim() ? note : null;
    if (value === row.notes) return Promise.resolve();
    return patchRow(row, row.categoryId, value);
  }

  async function record(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setProblem(undefined);
    try {
      await request<TransactionRow>("/transactions", {
        method: "POST",
        body: JSON.stringify({
          accountId: draft.accountId,
          bookedDate: draft.bookedDate,
          description: draft.description,
          // Sent verbatim as a decimal string; no arithmetic happens here.
          amount: draft.amount,
          txnType: draft.txnType,
          categoryId: draft.categoryId || null,
          notes: draft.notes || null,
        }),
        headers: { "Content-Type": "application/json" },
      });
      setDraft({ ...draft, description: "", amount: "", notes: "" });
      await reload(applied, 0, false);
      onChanged();
    } catch (error) {
      setProblem(
        error instanceof Error && error.message
          ? error.message
          : "The transaction was not saved. Check the values and retry.",
      );
    } finally {
      setBusy(false);
    }
  }

  function startSubscribe(row: TransactionRow) {
    setSubscribingId(row.id);
    setSubscribeKey(crypto.randomUUID());
    setSubscribeForm({
      cadence: "monthly",
      customIntervalDays: null,
      label: row.descriptionClean ?? row.descriptionRaw,
    });
    setSubscribeProblem(undefined);
  }

  async function submitSubscribe(row: TransactionRow) {
    setSubscribeBusy(true);
    setSubscribeProblem(undefined);
    try {
      await request("/recurring/from-transaction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transactionId: row.id,
          cadence: subscribeForm.cadence,
          customIntervalDays: subscribeForm.cadence === "custom" ? subscribeForm.customIntervalDays : null,
          label: subscribeForm.label,
          isSubscription: true,
          idempotencyKey: subscribeKey,
        }),
      });
      setSubscribingId(undefined);
      await reload(applied, 0, false);
      onChanged();
    } catch (error) {
      setSubscribeProblem(errorText(error, "This could not be added as a subscription."));
    } finally {
      setSubscribeBusy(false);
    }
  }

  const usingBaseCurrency = currency.code === household.baseCurrency;
  const summaryIncome = usingBaseCurrency
    ? transactionSummary.reportableIncomeBase
    : transactionSummary.reportableIncomeSecondary;
  const summarySpending = usingBaseCurrency
    ? transactionSummary.reportableSpendingBase
    : transactionSummary.reportableSpendingSecondary;

  return (
    <section className="workspace-content transactions-screen" aria-labelledby="transactions-title">
      <div id="transactions-title">
        <ConsolePageHeader
          actions={(
            <Button
              aria-controls="manual-transaction-entry"
              aria-expanded={entryOpen}
              onClick={() => setEntryOpen((open) => !open)}
              type="button"
            >
              <PlusIcon aria-hidden="true" />
              {entryOpen ? "Close manual entry" : "Record a transaction"}
            </Button>
          )}
          description={`${count} ${count === 1 ? "transaction" : "transactions"} · ${active.length} active ${active.length === 1 ? "account" : "accounts"}`}
          title="Transactions"
        />
      </div>
      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      {entryOpen && <form
        aria-label="Record a transaction"
        className="account-form transaction-entry-form"
        id="manual-transaction-entry"
        onSubmit={record}
      >
        <label className="select-field">
          <span>Account</span>
          <NativeSelect
            onChange={(event) => setDraft({ ...draft, accountId: event.target.value })}
            required
            value={draft.accountId}
          >
            <option value="">Choose an account</option>
            {active.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} · {account.currency}
              </option>
            ))}
          </NativeSelect>
        </label>
        <label className="date-field">
          <span>Date</span>
          <Input
            onChange={(event) => setDraft({ ...draft, bookedDate: event.target.value })}
            required
            type="date"
            value={draft.bookedDate}
          />
        </label>
        <label className="text-field">
          <span>Description</span>
          <Input
            onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            required
            value={draft.description}
          />
        </label>
        <label className="text-field">
          <span>Amount (signed)</span>
          <Input
            inputMode="decimal"
            onChange={(event) => setDraft({ ...draft, amount: event.target.value })}
            placeholder="-310.5000"
            required
            type="text"
            value={draft.amount}
          />
        </label>
        <label className="select-field">
          <span>Type</span>
          <NativeSelect
            onChange={(event) => setDraft({ ...draft, txnType: event.target.value })}
            value={draft.txnType}
          >
            {MANUAL_TYPES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </NativeSelect>
        </label>
        <label className="select-field">
          <span>Category</span>
          <NativeSelect
            onChange={(event) => setDraft({ ...draft, categoryId: event.target.value })}
            value={draft.categoryId}
          >
            <option value="">Unclassified</option>
            {categories.filter((row) => row.isActive).map((row) => (
              <option key={row.id} value={row.id}>{categoryLabel(row)}</option>
            ))}
          </NativeSelect>
        </label>
        <label className="text-field">
          <span>Note</span>
          <Input
            onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
            value={draft.notes}
          />
        </label>
        <div className="account-form-actions">
          <Button disabled={busy} type="submit">
            {busy ? "Saving…" : "Record transaction"}
          </Button>
        </div>
      </form>}

      {summaryReady && <MetricStrip
        items={[
          {
            label: "Reportable income",
            value: formatMoney(summaryIncome, currency),
            detail: "Filtered reportable rows",
          },
          {
            label: "Reportable spending",
            value: formatMoney(summarySpending, currency),
            detail: "Refunds reduce this total",
          },
          {
            label: "Needs review",
            value: `${transactionSummary.attentionCount} ${transactionSummary.attentionCount === 1 ? "item" : "items"}`,
            detail: `${transactionSummary.unreviewedCount} unreviewed · ${transactionSummary.uncategorizedCount} uncategorized`,
          },
        ]}
        label="Filtered transaction summary"
      />}

      <form
        aria-label="Filter the ledger"
        className="transaction-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(filters);
          writeFiltersToLocation(filters);
          void reload(filters, 0, false);
        }}
      >
        <DataToolbar summary={`${count} matching ${count === 1 ? "row" : "rows"}`}>
        <div className="transaction-filter-main">
        <label className="text-field">
          <span>Search descriptions</span>
          <Input
            onChange={(event) => setFilters({ ...filters, q: event.target.value })}
            value={filters.q}
          />
        </label>
        <label className="select-field">
          <span>Account</span>
          <NativeSelect
            onChange={(event) => setFilters({ ...filters, accountId: event.target.value })}
            value={filters.accountId}
          >
            <option value="">All accounts</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>{account.name}</option>
            ))}
          </NativeSelect>
        </label>
        <label className="select-field">
          <span>Category</span>
          <NativeSelect
            onChange={(event) => setFilters({ ...filters, categoryId: event.target.value })}
            value={filters.categoryId}
          >
            <option value="">All categories</option>
            {categories.map((row) => (
              <option key={row.id} value={row.id}>{categoryLabel(row)}</option>
            ))}
          </NativeSelect>
        </label>
        <label className="date-field">
          <span>From</span>
          <Input
            onChange={(event) => setFilters({ ...filters, dateFrom: event.target.value })}
            type="date"
            value={filters.dateFrom}
          />
        </label>
        <label className="date-field">
          <span>To</span>
          <Input
            onChange={(event) => setFilters({ ...filters, dateTo: event.target.value })}
            type="date"
            value={filters.dateTo}
          />
        </label>
        </div>
        <details className="transaction-filter-advanced">
          <summary>More filters</summary>
          <div className="transaction-filter-advanced-fields">
        <label className="select-field">
          <span>Type</span>
          <NativeSelect
            onChange={(event) => setFilters({ ...filters, txnType: event.target.value })}
            value={filters.txnType}
          >
            <option value="">All types</option>
            {ALL_TYPES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </NativeSelect>
        </label>
        <label className="text-field">
          <span>Amount from</span>
          <Input
            inputMode="decimal"
            onChange={(event) => setFilters({ ...filters, amountMin: event.target.value })}
            value={filters.amountMin}
          />
        </label>
        <label className="text-field">
          <span>Amount to</span>
          <Input
            inputMode="decimal"
            onChange={(event) => setFilters({ ...filters, amountMax: event.target.value })}
            value={filters.amountMax}
          />
        </label>
        <label className="select-field">
          <span>Currency</span>
          <NativeSelect
            onChange={(event) => setFilters({ ...filters, currency: event.target.value })}
            value={filters.currency}
          >
            <option value="">All currencies</option>
            {currencies.map((option) => (
              <option key={option.code} value={option.code}>{option.code}</option>
            ))}
          </NativeSelect>
        </label>
        <label className="text-field">
          <span>Country</span>
          <Input
            maxLength={2}
            onChange={(event) => setFilters({ ...filters, country: event.target.value })}
            value={filters.country}
          />
        </label>
        <label className="text-field">
          <span>Tag</span>
          <Input
            onChange={(event) => setFilters({ ...filters, tag: event.target.value })}
            value={filters.tag}
          />
        </label>
        <label className="text-field">
          <span>Merchant id</span>
          <Input
            onChange={(event) => setFilters({ ...filters, merchantId: event.target.value })}
            value={filters.merchantId}
          />
        </label>
        <label className="text-field">
          <span>Source document id</span>
          <Input
            onChange={(event) =>
              setFilters({ ...filters, sourceDocumentId: event.target.value })
            }
            value={filters.sourceDocumentId}
          />
        </label>
        <label className="select-field">
          <span>Review state</span>
          <NativeSelect
            onChange={(event) => setFilters({ ...filters, reviewState: event.target.value })}
            value={filters.reviewState}
          >
            <option value="">All rows</option>
            <option value="reviewed">Reviewed</option>
            <option value="unreviewed">Unreviewed</option>
          </NativeSelect>
        </label>
        <label className="select-field">
          <span>Classification</span>
          <NativeSelect
            onChange={(event) => setFilters({ ...filters, classifiedBy: event.target.value })}
            value={filters.classifiedBy}
          >
            <option value="">All rows</option>
            <option value="unclassified">Needs a category</option>
            <option value="seed_rule">Seed rule</option>
            <option value="merchant_alias">Merchant alias</option>
            <option value="recurring_series">Recurring series</option>
            <option value="prior_match">Prior match</option>
            <option value="user_rule">Owner rule</option>
            <option value="user_manual">Owner decision</option>
            <option value="ai">AI</option>
          </NativeSelect>
        </label>
        <label className="select-field">
          <span>Sort by</span>
          <NativeSelect
            onChange={(event) => setFilters({ ...filters, sort: event.target.value })}
            value={filters.sort}
          >
            <option value="bookedDate">Date</option>
            <option value="amount">Amount</option>
            <option value="description">Description</option>
            <option value="account">Account</option>
          </NativeSelect>
        </label>
        <label className="select-field">
          <span>Direction</span>
          <NativeSelect
            onChange={(event) => setFilters({ ...filters, direction: event.target.value })}
            value={filters.direction}
          >
            <option value="desc">Newest / largest first</option>
            <option value="asc">Oldest / smallest first</option>
          </NativeSelect>
        </label>
          </div>
        </details>
        <div className="account-form-actions">
          <Button disabled={busy} type="submit">Apply filters</Button>
          <Button
            disabled={busy}
            onClick={() => {
              setFilters(NO_FILTERS);
              setApplied(NO_FILTERS);
              writeFiltersToLocation(NO_FILTERS);
              void reload(NO_FILTERS, 0, false);
            }}
            type="button"
            variant="outline"
          >
            Clear
          </Button>
        </div>
        </DataToolbar>
      </form>

      <TransferQueue
        currencies={currencies}
        household={household}
        onChanged={refresh}
        request={request}
      />
      {linking && (
        <PeerPicker
          currencies={currencies}
          household={household}
          onChanged={refresh}
          onClose={() => setLinking(undefined)}
          request={request}
          transaction={linking}
        />
      )}

      {rows.length === 0 ? (
        <p className="lede">
          The ledger is empty for this view. Use Record a transaction for a manual entry, or import
          a statement.
        </p>
      ) : (
        <DataTableRegion label="Transaction ledger">
          <table className="account-table console-table transaction-ledger">
          <caption className="visually-hidden">The transaction ledger</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Description</th>
              <th scope="col">Account</th>
              <th scope="col">Category</th>
              <th scope="col">Evidence</th>
              <th scope="col">Amount</th>
              <th scope="col"><span className="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const movement = transferMovement(row);
              return (
              <tr className={row.transferId ? "transaction-transfer-row" : undefined} key={row.id}>
                <td className="transaction-date">{row.bookedDate}</td>
                <td className="transaction-description">
                  {movement ? (
                    <div
                      aria-label={`Confirmed transfer from ${movement.source.accountName} to ${movement.destination.accountName}`}
                      className="transaction-transfer-movement"
                    >
                      <span
                        aria-label="Transfer leg, linked to its peer"
                        className="transaction-transfer-badge"
                        role="img"
                      >
                        TRANSFER
                      </span>
                      <span className="transaction-transfer-stop">
                        <strong>{movement.source.accountName}</strong>
                        <small>{original(movement.source)}</small>
                      </span>
                      <span className="transaction-transfer-to">to</span>
                      <span className="transaction-transfer-stop destination">
                        <strong>{movement.destination.accountName}</strong>
                        <small>{original(movement.destination)}</small>
                      </span>
                    </div>
                  ) : row.transferId ? (
                    <span
                      aria-label="Transfer leg, linked to its peer"
                      className="transaction-transfer-badge"
                      role="img"
                    >
                      TRANSFER
                    </span>
                  ) : null}
                  <span className="transaction-description-raw">{row.descriptionRaw}</span>
                  {!row.transferId && !row.isSplitParent && (
                    <Button
                      aria-label={`Link ${row.descriptionRaw} as a transfer`}
                      disabled={busy}
                      onClick={() => setLinking(row)}
                      size="sm"
                      type="button"
                      variant="ghost"
                    >
                      Link as transfer
                    </Button>
                  )}
                  {isSpendingCharge(row) && subscribingId !== row.id && (
                    <Button
                      disabled={busy}
                      onClick={() => startSubscribe(row)}
                      size="sm"
                      type="button"
                      variant="ghost"
                    >
                      Add as subscription
                    </Button>
                  )}
                  {row.parentTransactionId && <small> · split</small>}
                  <small> · {row.source}</small>
                  {subscribingId === row.id && (
                    <div className="recurring-dismiss-form">
                      <label className="text-field">
                        <span>Name</span>
                        <Input
                          onChange={(event) =>
                            setSubscribeForm({ ...subscribeForm, label: event.target.value })
                          }
                          value={subscribeForm.label}
                        />
                      </label>
                      <label className="select-field">
                        <span>Cadence</span>
                        <NativeSelect
                          onChange={(event) =>
                            setSubscribeForm({ ...subscribeForm, cadence: event.target.value })
                          }
                          value={subscribeForm.cadence}
                        >
                          {Object.entries(CADENCE_TEXT).map(([value, text]) => (
                            <option key={value} value={value}>{text}</option>
                          ))}
                        </NativeSelect>
                      </label>
                      {subscribeForm.cadence === "custom" && (
                        <label className="text-field">
                          <span>Days between charges</span>
                          <Input
                            max={730}
                            min={7}
                            onChange={(event) =>
                              setSubscribeForm({
                                ...subscribeForm,
                                customIntervalDays: event.target.valueAsNumber,
                              })
                            }
                            type="number"
                            value={subscribeForm.customIntervalDays ?? ""}
                          />
                        </label>
                      )}
                      {subscribeProblem && (
                        <p className="inline-alert" role="alert">{subscribeProblem}</p>
                      )}
                      <Button
                        disabled={
                          subscribeBusy ||
                          !subscribeForm.label.trim() ||
                          (subscribeForm.cadence === "custom" &&
                            (subscribeForm.customIntervalDays === null ||
                              Number.isNaN(subscribeForm.customIntervalDays)))
                        }
                        onClick={() => void submitSubscribe(row)}
                        size="sm"
                        type="button"
                      >
                        {subscribeBusy ? "Saving…" : "Add as subscription"}
                      </Button>
                      <Button
                        onClick={() => setSubscribingId(undefined)}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        Cancel
                      </Button>
                    </div>
                  )}
                </td>
                <td className="transaction-account">{row.accountName}</td>
                <td className="transaction-category">
                  <label className="select-field">
                    <span className="visually-hidden">Category for {row.descriptionRaw}</span>
                    <NativeSelect
                      disabled={busy || row.isSplitParent}
                      title={
                        row.isSplitParent
                          ? "A split parent is a container; recategorize its children"
                          : undefined
                      }
                      onChange={(event) => void recategorize(row, event.target.value)}
                      value={
                        classifying?.row.id === row.id
                          ? classifying.categoryId
                          : (row.categoryId ?? "")
                      }
                    >
                      <option value="">Unclassified</option>
                      {categories.filter((one) => one.isActive || one.id === row.categoryId).map(
                        (one) => (
                          <option key={one.id} value={one.id}>{categoryLabel(one)}</option>
                        ),
                      )}
                    </NativeSelect>
                  </label>
                  {row.categoryId && !categoryName.has(row.categoryId) && (
                    <small>Category no longer listed</small>
                  )}
                </td>
                <td className="transaction-evidence">
                  {row.transferId ? (
                    <StatusLabel tone="success">Linked</StatusLabel>
                  ) : row.isSplitParent || row.parentTransactionId ? (
                    <StatusLabel>Split</StatusLabel>
                  ) : row.isExcluded ? (
                    <StatusLabel>Excluded</StatusLabel>
                  ) : row.sourceDocumentId ? (
                    <StatusLabel tone="success">Imported</StatusLabel>
                  ) : row.classifiedBy === "unclassified" ? (
                    <StatusLabel tone="attention">Needs category</StatusLabel>
                  ) : (
                    <StatusLabel>Recorded</StatusLabel>
                  )}
                </td>
                <td className={row.amount.startsWith("-") ? "console-number transaction-amount" : "console-number transaction-amount positive"}>
                  <span>{original(row)}</span>
                  {bankOriginal(row) && <small> · originally {bankOriginal(row)}</small>}
                  {converted(row) && <small> · {converted(row)}</small>}
                  <small>
                    {" "}· FX {rateUsed(row)} on {row.fxRateDate}
                    {row.fxRateIsCarried ? " (carried forward)" : ""}
                  </small>
                </td>
                <td className="transaction-row-actions">
                  <RowActions
                    floating
                    label={`More actions for ${row.descriptionRaw}`}
                    triggerContent={<MoreHorizontalIcon aria-hidden="true" />}
                  >
                    {row.sourceDocumentId && (
                      <a
                        href={apiUrl(`/documents/${row.sourceDocumentId}/original`)}
                        rel="noreferrer"
                        target="_blank"
                      >
                        Page {row.sourcePageNumber} · row {row.sourceRowNumber}
                      </a>
                    )}
                    <label className="text-field">
                      <span>Note for {row.descriptionRaw}</span>
                      <Input
                        defaultValue={row.notes ?? ""}
                        disabled={busy}
                        onBlur={(event) => void saveNote(row, event.target.value)}
                        placeholder="Add a note"
                      />
                    </label>
                  </RowActions>
                </td>
              </tr>
              );
            })}
          </tbody>
          </table>
        </DataTableRegion>
      )}

      {classifying && (
        <ClassifyPrompt
          categoryId={classifying.categoryId}
          categoryName={categoryName.get(classifying.categoryId) ?? "this category"}
          onApplied={async () => {
            await reload(applied, 0, false);
            onChanged();
          }}
          onClose={() => setClassifying(undefined)}
          onJustOne={() =>
            patchRow(classifying.row, classifying.categoryId, classifying.row.notes, true)
          }
          request={request}
          row={classifying.row}
        />
      )}

      {rows.length < count && (
        <Button
          disabled={busy}
          onClick={() => void reload(applied, rows.length, true)}
          type="button"
          variant="outline"
        >
          Load {Math.min(PAGE, count - rows.length)} more
        </Button>
      )}
    </section>
  );
}
