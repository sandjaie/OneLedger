"use client";

import { useState } from "react";

import { type apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type {
  AccountRow,
  CategoryRow,
  CurrencyRow,
  HouseholdCurrency,
  HouseholdResponse,
  TransactionRow,
  TransactionsResponse,
} from "@/types/api";

type Props = {
  transactions: Array<TransactionRow>;
  total: number;
  accounts: Array<AccountRow>;
  categories: Array<CategoryRow>;
  currencies: Array<CurrencyRow>;
  household: HouseholdResponse;
  currency: HouseholdCurrency;
  request: typeof apiFetch;
  onChanged: () => void;
};

// The manual-entry set: linked types (transfers, investments, loans) arrive with their
// features, and the importer stamps its own.
const MANUAL_TYPES = ["expense", "income", "fee", "interest", "tax", "refund"] as const;

const PAGE = 50;

type Filters = {
  accountId: string;
  categoryId: string;
  txnType: string;
  dateFrom: string;
  dateTo: string;
  q: string;
  sort: string;
  direction: string;
};

const NO_FILTERS: Filters = {
  accountId: "",
  categoryId: "",
  txnType: "",
  dateFrom: "",
  dateTo: "",
  q: "",
  sort: "bookedDate",
  direction: "desc",
};

function query(filters: Filters, offset: number) {
  const parameters = new URLSearchParams();
  if (filters.accountId) parameters.set("accountId", filters.accountId);
  if (filters.categoryId) parameters.set("categoryId", filters.categoryId);
  if (filters.txnType) parameters.set("txnType", filters.txnType);
  if (filters.dateFrom) parameters.set("dateFrom", filters.dateFrom);
  if (filters.dateTo) parameters.set("dateTo", filters.dateTo);
  if (filters.q.trim()) parameters.set("q", filters.q.trim());
  if (filters.sort !== "bookedDate") parameters.set("sort", filters.sort);
  if (filters.direction !== "desc") parameters.set("direction", filters.direction);
  parameters.set("limit", String(PAGE));
  parameters.set("offset", String(offset));
  return `/transactions?${parameters.toString()}`;
}

export function TransactionsScreen({
  transactions,
  total,
  accounts,
  categories,
  currencies,
  household,
  currency,
  request,
  onChanged,
}: Props) {
  const [rows, setRows] = useState(transactions);
  const [count, setCount] = useState(total);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [draft, setDraft] = useState({
    accountId: "",
    bookedDate: "",
    description: "",
    amount: "",
    txnType: "expense",
    categoryId: "",
    notes: "",
  });

  const categoryName = new Map(categories.map((row) => [row.id, row.name]));
  const active = accounts.filter((account) => account.isActive);

  // Original currency first, converted second — never the conversion alone (UI section 4).
  function original(row: TransactionRow) {
    const native = currencies.find(({ code }) => code === row.currency);
    return native ? formatMoney(row.amount, native) : `${row.amount} ${row.currency}`;
  }

  function converted(row: TransactionRow) {
    if (row.currency === currency.code) return undefined;
    const value = currency.code === household.baseCurrency ? row.amountBase : row.amountSecondary;
    return formatMoney(value, currency);
  }

  // The frozen rate the displayed conversion actually used: rate, date and carried status
  // together, so the converted figure is auditable (UI section 4).
  function rateUsed(row: TransactionRow) {
    return currency.code === household.baseCurrency ? row.fxRateToBase : row.fxRateToSecondary;
  }

  async function reload(nextFilters: Filters, offset: number, append: boolean) {
    setBusy(true);
    setProblem(undefined);
    try {
      const page = await request<TransactionsResponse>(query(nextFilters, offset));
      setRows((current) => (append ? [...current, ...page.transactions] : page.transactions));
      setCount(page.total);
    } catch {
      setProblem("The ledger could not be loaded. Check the API service and retry.");
    } finally {
      setBusy(false);
    }
  }

  async function recategorize(row: TransactionRow, categoryId: string) {
    setBusy(true);
    setProblem(undefined);
    try {
      const updated = await request<TransactionRow>(`/transactions/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify({ categoryId: categoryId || null, notes: row.notes }),
        headers: { "Content-Type": "application/json" },
      });
      setRows((current) => current.map((one) => (one.id === updated.id ? updated : one)));
    } catch {
      setProblem("The category change was not saved. Retry.");
    } finally {
      setBusy(false);
    }
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
      await reload(filters, 0, false);
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

  return (
    <section className="workspace-content" aria-labelledby="transactions-title">
      <p className="eyebrow">The ledger</p>
      <h1 className="workspace-title" id="transactions-title">Transactions</h1>
      <p className="lede">
        {count === 0
          ? "No transactions yet."
          : `${count} ${count === 1 ? "row" : "rows"} · original currency first, converted second.`}
      </p>
      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      <form aria-label="Record a transaction" className="account-form" onSubmit={record}>
        <label className="select-field">
          <span>Account</span>
          <select
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
          </select>
        </label>
        <label className="date-field">
          <span>Date</span>
          <input
            onChange={(event) => setDraft({ ...draft, bookedDate: event.target.value })}
            required
            type="date"
            value={draft.bookedDate}
          />
        </label>
        <label className="text-field">
          <span>Description</span>
          <input
            onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            required
            value={draft.description}
          />
        </label>
        <label className="text-field">
          <span>Amount (signed)</span>
          <input
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
          <select
            onChange={(event) => setDraft({ ...draft, txnType: event.target.value })}
            value={draft.txnType}
          >
            {MANUAL_TYPES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </label>
        <label className="select-field">
          <span>Category</span>
          <select
            onChange={(event) => setDraft({ ...draft, categoryId: event.target.value })}
            value={draft.categoryId}
          >
            <option value="">Unclassified</option>
            {categories.filter((row) => row.isActive).map((row) => (
              <option key={row.id} value={row.id}>
                {row.parentId ? `— ${row.name}` : row.name}
              </option>
            ))}
          </select>
        </label>
        <div className="account-form-actions">
          <button className="primary-action" disabled={busy} type="submit">
            {busy ? "Saving…" : "Record transaction"}
          </button>
        </div>
      </form>

      <form
        aria-label="Filter the ledger"
        className="account-form"
        onSubmit={(event) => {
          event.preventDefault();
          void reload(filters, 0, false);
        }}
      >
        <label className="text-field">
          <span>Search descriptions</span>
          <input
            onChange={(event) => setFilters({ ...filters, q: event.target.value })}
            value={filters.q}
          />
        </label>
        <label className="select-field">
          <span>Account</span>
          <select
            onChange={(event) => setFilters({ ...filters, accountId: event.target.value })}
            value={filters.accountId}
          >
            <option value="">All accounts</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>{account.name}</option>
            ))}
          </select>
        </label>
        <label className="select-field">
          <span>Category</span>
          <select
            onChange={(event) => setFilters({ ...filters, categoryId: event.target.value })}
            value={filters.categoryId}
          >
            <option value="">All categories</option>
            {categories.map((row) => (
              <option key={row.id} value={row.id}>
                {row.parentId ? `— ${row.name}` : row.name}
              </option>
            ))}
          </select>
        </label>
        <label className="select-field">
          <span>Type</span>
          <select
            onChange={(event) => setFilters({ ...filters, txnType: event.target.value })}
            value={filters.txnType}
          >
            <option value="">All types</option>
            {MANUAL_TYPES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </label>
        <label className="date-field">
          <span>From</span>
          <input
            onChange={(event) => setFilters({ ...filters, dateFrom: event.target.value })}
            type="date"
            value={filters.dateFrom}
          />
        </label>
        <label className="date-field">
          <span>To</span>
          <input
            onChange={(event) => setFilters({ ...filters, dateTo: event.target.value })}
            type="date"
            value={filters.dateTo}
          />
        </label>
        <label className="select-field">
          <span>Sort by</span>
          <select
            onChange={(event) => setFilters({ ...filters, sort: event.target.value })}
            value={filters.sort}
          >
            <option value="bookedDate">Date</option>
            <option value="amount">Amount</option>
            <option value="description">Description</option>
            <option value="account">Account</option>
          </select>
        </label>
        <label className="select-field">
          <span>Direction</span>
          <select
            onChange={(event) => setFilters({ ...filters, direction: event.target.value })}
            value={filters.direction}
          >
            <option value="desc">Newest / largest first</option>
            <option value="asc">Oldest / smallest first</option>
          </select>
        </label>
        <div className="account-form-actions">
          <button className="primary-action" disabled={busy} type="submit">Apply filters</button>
        </div>
      </form>

      {rows.length === 0 ? (
        <p className="lede">
          The ledger is empty for this view. Record your first transaction with the form above;
          statement import arrives with M2.
        </p>
      ) : (
        <table className="account-table">
          <caption className="visually-hidden">The transaction ledger</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Description</th>
              <th scope="col">Category</th>
              <th scope="col">Account</th>
              <th scope="col">Amount</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.bookedDate}</td>
                <td>
                  {row.descriptionRaw}
                  {row.transferId && (
                    <span aria-label="Transfer leg, linked to its peer" role="img"> 🔗</span>
                  )}
                  {row.parentTransactionId && <small> · split</small>}
                </td>
                <td>
                  <label className="select-field">
                    <span className="visually-hidden">Category for {row.descriptionRaw}</span>
                    <select
                      disabled={busy}
                      onChange={(event) => void recategorize(row, event.target.value)}
                      value={row.categoryId ?? ""}
                    >
                      <option value="">Unclassified</option>
                      {categories.filter((one) => one.isActive || one.id === row.categoryId).map(
                        (one) => (
                          <option key={one.id} value={one.id}>
                            {one.parentId ? `— ${one.name}` : one.name}
                          </option>
                        ),
                      )}
                    </select>
                  </label>
                  {row.categoryId && !categoryName.has(row.categoryId) && (
                    <small>Category no longer listed</small>
                  )}
                </td>
                <td>{row.accountName}</td>
                <td>
                  <span>{original(row)}</span>
                  {converted(row) && <small> · {converted(row)}</small>}
                  <small>
                    {" "}· FX {rateUsed(row)} on {row.fxRateDate}
                    {row.fxRateIsCarried ? " (carried forward)" : ""}
                  </small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {rows.length < count && (
        <button
          className="text-action"
          disabled={busy}
          onClick={() => void reload(filters, rows.length, true)}
          type="button"
        >
          Load {Math.min(PAGE, count - rows.length)} more
        </button>
      )}
    </section>
  );
}
