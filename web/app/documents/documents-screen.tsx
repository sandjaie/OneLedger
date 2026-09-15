"use client";

import { useState } from "react";
import Link from "next/link";

import { DescriptionRepair } from "@/app/documents/description-repair";
import { type apiFetch } from "@/lib/api/client";
import { apiUrl } from "@/lib/api/url";
import type { AccountRow, DocumentRow, DocumentsResponse } from "@/types/api";

type Props = {
  documents: Array<DocumentRow>;
  total: number;
  accounts: Array<AccountRow>;
  request: typeof apiFetch;
};

// schema.md's `document_type` enum, restated for the filter — `routes/documents.py` owns the
// same closed set on the API side.
const DOCUMENT_TYPES = [
  "bank_statement",
  "card_statement",
  "payslip",
  "loan_statement",
  "investment_statement",
  "cas",
  "tax_document",
  "insurance_policy",
  "property_document",
  "fd_certificate",
  "receipt",
  "other",
] as const;

// The union of `import_status` and `investment_import_status` `routes/documents.py` filters by.
const IMPORT_STATUSES = [
  "uploaded",
  "extracting",
  "normalizing",
  "deduping",
  "classifying",
  "matching_transfers",
  "reconciling",
  "awaiting_review",
  "importing",
  "completed",
  "failed",
  "cancelled",
  "staged",
  "partially_committed",
] as const;

const PAGE = 50;

type Filters = {
  documentType: string;
  accountId: string;
  periodFrom: string;
  periodTo: string;
  importStatus: string;
};

const NO_FILTERS: Filters = {
  documentType: "",
  accountId: "",
  periodFrom: "",
  periodTo: "",
  importStatus: "",
};

function query(filters: Filters, offset: number) {
  const parameters = new URLSearchParams();
  if (filters.documentType) parameters.set("documentType", filters.documentType);
  if (filters.accountId) parameters.set("accountId", filters.accountId);
  if (filters.periodFrom) parameters.set("periodFrom", filters.periodFrom);
  if (filters.periodTo) parameters.set("periodTo", filters.periodTo);
  if (filters.importStatus) parameters.set("importStatus", filters.importStatus);
  parameters.set("limit", String(PAGE));
  parameters.set("offset", String(offset));
  return `/documents?${parameters.toString()}`;
}

function periodLabel(row: DocumentRow) {
  if (!row.period.start || !row.period.end) return "—";
  const derived = row.period.isDerived ? " (derived from the rows)" : "";
  return `${row.period.start} – ${row.period.end}${derived}`;
}

function accountLabel(row: DocumentRow) {
  if (!row.accountId) return "—";
  return row.accountNumberMasked ? `${row.accountName} · ${row.accountNumberMasked}` : (row.accountName ?? "—");
}

// A statement awaiting review, or an investment import still in progress: everywhere else
// review already happened, so nothing links there.
function reviewHref(row: DocumentRow) {
  if (!row.importId) return undefined;
  if (row.importKind === "statement" && row.importStatus === "awaiting_review") {
    return `/import/${row.importId}`;
  }
  if (
    row.importKind === "investment"
    && (row.importStatus === "staged"
      || row.importStatus === "awaiting_review"
      || row.importStatus === "partially_committed")
  ) {
    return `/investments/import/${row.importId}`;
  }
  return undefined;
}

export function DocumentsScreen({ documents, total, accounts, request }: Props) {
  const [rows, setRows] = useState(documents);
  const [count, setCount] = useState(total);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [applied, setApplied] = useState<Filters>(NO_FILTERS);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [repairing, setRepairing] = useState<string>();

  async function reload(nextFilters: Filters, offset: number, append: boolean) {
    setBusy(true);
    setProblem(undefined);
    try {
      const page = await request<DocumentsResponse>(query(nextFilters, offset));
      setRows((current) => (append ? [...current, ...page.documents] : page.documents));
      setCount(page.total);
    } catch {
      setProblem("The document library could not be loaded. Check the API service and retry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="workspace-content documents-screen" aria-labelledby="documents-title">
      <header className="transaction-page-head">
        <div>
          <h1 className="workspace-title" id="documents-title">Documents</h1>
          <p className="transaction-summary">
            {count} {count === 1 ? "document" : "documents"}
          </p>
        </div>
      </header>
      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      <form
        aria-label="Filter documents"
        className="transaction-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(filters);
          void reload(filters, 0, false);
        }}
      >
        <div className="transaction-filter-main">
          <label className="select-field">
            <span>Type</span>
            <select
              onChange={(event) => setFilters({ ...filters, documentType: event.target.value })}
              value={filters.documentType}
            >
              <option value="">All types</option>
              {DOCUMENT_TYPES.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
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
          <label className="date-field">
            <span>Period from</span>
            <input
              onChange={(event) => setFilters({ ...filters, periodFrom: event.target.value })}
              type="date"
              value={filters.periodFrom}
            />
          </label>
          <label className="date-field">
            <span>Period to</span>
            <input
              onChange={(event) => setFilters({ ...filters, periodTo: event.target.value })}
              type="date"
              value={filters.periodTo}
            />
          </label>
          <label className="select-field">
            <span>Import status</span>
            <select
              onChange={(event) => setFilters({ ...filters, importStatus: event.target.value })}
              value={filters.importStatus}
            >
              <option value="">Any status</option>
              {IMPORT_STATUSES.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="account-form-actions">
          <button className="primary-action" disabled={busy} type="submit">Apply filters</button>
        </div>
      </form>

      {rows.length === 0 ? (
        <p className="lede">No stored documents match this view.</p>
      ) : (
        <table className="account-table">
          <caption className="visually-hidden">The document library</caption>
          <thead>
            <tr>
              <th scope="col">Type</th>
              <th scope="col">Account</th>
              <th scope="col">Period</th>
              <th scope="col">Pages</th>
              <th scope="col">Uploaded</th>
              <th scope="col">Import</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const review = reviewHref(row);
              return (
                <tr key={row.id}>
                  <td>
                    {row.title ?? row.documentType}
                    {row.title && <small> · {row.documentType}</small>}
                    {row.isPasswordProtected && <small> · password protected</small>}
                  </td>
                  <td>{accountLabel(row)}</td>
                  <td>{periodLabel(row)}</td>
                  <td>{row.pageCount ?? "—"}</td>
                  <td>{row.uploadedOn.slice(0, 10)}</td>
                  <td>
                    {row.importStatus ?? "—"}
                    {row.reconciliationStatus && <small> · {row.reconciliationStatus}</small>}
                  </td>
                  <td className="document-actions">
                    <a href={apiUrl(`/documents/${row.id}/original`)} rel="noreferrer" target="_blank">
                      Original
                    </a>
                    {row.importKind === "statement" && (
                      <Link href={`/transactions?sourceDocumentId=${row.id}`}>Transactions</Link>
                    )}
                    {review && <Link href={review}>Review</Link>}
                    {row.newerLayoutId && row.importStatus === "completed" && (
                      <button
                        className="text-action"
                        onClick={() => setRepairing((current) => (current === row.id ? undefined : row.id))}
                        type="button"
                      >
                        Re-read descriptions
                      </button>
                    )}
                    {repairing === row.id && (
                      <DescriptionRepair
                        document={row}
                        onApplied={() => void reload(applied, 0, false)}
                        request={request}
                      />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {rows.length < count && (
        <button
          className="text-action"
          disabled={busy}
          onClick={() => void reload(applied, rows.length, true)}
          type="button"
        >
          Load {Math.min(PAGE, count - rows.length)} more
        </button>
      )}
    </section>
  );
}
