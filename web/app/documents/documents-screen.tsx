"use client";

import { useState, useSyncExternalStore } from "react";
import Link from "next/link";

import { DescriptionRepair } from "@/app/documents/description-repair";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { type apiFetch } from "@/lib/api/client";
import { apiUrl } from "@/lib/api/url";
import { dateLabel } from "@/lib/format/date";
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

const DOCUMENT_TYPE_LABELS: Record<(typeof DOCUMENT_TYPES)[number], string> = {
  bank_statement: "Bank statement",
  card_statement: "Card statement",
  payslip: "Payslip",
  loan_statement: "Loan statement",
  investment_statement: "Investment statement",
  cas: "Consolidated account statement",
  tax_document: "Tax document",
  insurance_policy: "Insurance policy",
  property_document: "Property document",
  fd_certificate: "Fixed deposit certificate",
  receipt: "Receipt",
  other: "Other",
};

const IMPORT_STATUS_LABELS: Record<(typeof IMPORT_STATUSES)[number], string> = {
  uploaded: "Uploaded",
  extracting: "Extracting",
  normalizing: "Normalizing",
  deduping: "Checking duplicates",
  classifying: "Classifying",
  matching_transfers: "Matching transfers",
  reconciling: "Reconciling",
  awaiting_review: "Awaiting review",
  importing: "Importing",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  staged: "Staged",
  partially_committed: "Partially committed",
};

function readable(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase());
}

function typeLabel(value: string) {
  return DOCUMENT_TYPE_LABELS[value as keyof typeof DOCUMENT_TYPE_LABELS] ?? readable(value);
}

function statusLabel(value: string | null) {
  return value ? IMPORT_STATUS_LABELS[value as keyof typeof IMPORT_STATUS_LABELS] ?? readable(value) : "Stored";
}

const PAGE = 50;
const ADVANCED_BREAKPOINT = "(min-width: 588px)";

function subscribeBreakpoint(onChange: () => void) {
  const media = window.matchMedia?.(ADVANCED_BREAKPOINT);
  media?.addEventListener("change", onChange);
  return () => media?.removeEventListener("change", onChange);
}

function desktopSnapshot() {
  return window.matchMedia?.(ADVANCED_BREAKPOINT).matches ?? window.innerWidth >= 588;
}

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
  const desktop = useSyncExternalStore(subscribeBreakpoint, desktopSnapshot, () => false);
  const [advancedOverride, setAdvancedOverride] = useState<boolean | null>(null);
  const advancedOpen = advancedOverride ?? desktop;

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
      {problem && <Alert variant="destructive"><AlertDescription>
        {problem} {rows.length > 0 && "The previously loaded records remain; new filters were not applied."}
      </AlertDescription></Alert>}

      <form
        aria-label="Filter documents"
        className="transaction-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(filters);
          void reload(filters, 0, false);
        }}
      >
        <div className="transaction-filter-main document-filter-primary">
          <label className="select-field">
            <span>Type</span>
            <NativeSelect
              onChange={(event) => setFilters({ ...filters, documentType: event.target.value })}
              value={filters.documentType}
            >
              <NativeSelectOption value="">All types</NativeSelectOption>
              {DOCUMENT_TYPES.map((value) => (
                <NativeSelectOption key={value} value={value}>{DOCUMENT_TYPE_LABELS[value]}</NativeSelectOption>
              ))}
            </NativeSelect>
          </label>
          <label className="select-field">
            <span>Account</span>
            <NativeSelect
              onChange={(event) => setFilters({ ...filters, accountId: event.target.value })}
              value={filters.accountId}
            >
              <NativeSelectOption value="">All accounts</NativeSelectOption>
              {accounts.map((account) => (
                <NativeSelectOption key={account.id} value={account.id}>{account.name}</NativeSelectOption>
              ))}
            </NativeSelect>
          </label>
        </div>
        <Collapsible className="document-advanced-filters" onOpenChange={setAdvancedOverride} open={advancedOpen}>
          <CollapsibleTrigger>Advanced filters <span aria-hidden="true">⌄</span></CollapsibleTrigger>
          <CollapsibleContent className="transaction-filter-main">
          <label className="date-field">
            <span>Period from</span>
            <Input
              onChange={(event) => setFilters({ ...filters, periodFrom: event.target.value })}
              type="date"
              value={filters.periodFrom}
            />
          </label>
          <label className="date-field">
            <span>Period to</span>
            <Input
              onChange={(event) => setFilters({ ...filters, periodTo: event.target.value })}
              type="date"
              value={filters.periodTo}
            />
          </label>
          <label className="select-field">
            <span>Import status</span>
            <NativeSelect
              onChange={(event) => setFilters({ ...filters, importStatus: event.target.value })}
              value={filters.importStatus}
            >
              <NativeSelectOption value="">Any status</NativeSelectOption>
              {IMPORT_STATUSES.map((value) => (
                <NativeSelectOption key={value} value={value}>{IMPORT_STATUS_LABELS[value]}</NativeSelectOption>
              ))}
            </NativeSelect>
          </label>
          </CollapsibleContent>
        </Collapsible>
        <div className="account-form-actions">
          <Button disabled={busy} type="submit">Apply filters</Button>
        </div>
      </form>

      {rows.length === 0 ? (
        <p className="lede">No stored documents match this view.</p>
      ) : (
        <div className="document-record-list" aria-label="Document library">
          {rows.map((row) => {
              const review = reviewHref(row);
              const title = row.title ?? typeLabel(row.documentType);
              const canRepair = Boolean(row.newerLayoutId && row.importStatus === "completed");
              const primary = review ? "review" : canRepair ? "repair" : row.importKind === "statement" ? "transactions" : "original";
              return (
                <article aria-label={title} className="document-record" key={row.id}>
                  <div className="document-record-head">
                    <div>
                      <h2>{title}</h2>
                      {row.title && <span>{typeLabel(row.documentType)}</span>}
                    </div>
                    <Badge variant={row.importStatus === "failed" ? "destructive" : "secondary"}>{statusLabel(row.importStatus)}</Badge>
                  </div>
                  <dl className="document-record-facts">
                    <div><dt>Account</dt><dd>{accountLabel(row)}</dd></div>
                    <div><dt>Period</dt><dd>{periodLabel(row)}</dd></div>
                  </dl>
                  <div className="document-actions">
                    {review && <Link className={buttonVariants({ variant: primary === "review" ? "default" : "outline" })} href={review}>Review</Link>}
                    {canRepair && (
                      <Button onClick={() => setRepairing((current) => (current === row.id ? undefined : row.id))}
                        type="button" variant={primary === "repair" ? "default" : "outline"}>
                        Re-read descriptions
                      </Button>
                    )}
                    {row.importKind === "statement" && (
                      <Link className={buttonVariants({ variant: primary === "transactions" ? "default" : "outline" })}
                        href={`/transactions?sourceDocumentId=${row.id}`}>Transactions</Link>
                    )}
                    <a className={buttonVariants({ variant: primary === "original" ? "default" : "outline" })}
                      href={apiUrl(`/documents/${row.id}/original`)} rel="noreferrer" target="_blank">Original</a>
                  </div>
                  {repairing === row.id && <DescriptionRepair document={row}
                    onApplied={() => void reload(applied, 0, false)} request={request} />}
                  <Collapsible className="document-record-details">
                    <CollapsibleTrigger>Details for {title} <span aria-hidden="true">⌄</span></CollapsibleTrigger>
                    <CollapsibleContent>
                      <dl>
                        <div><dt>Pages</dt><dd>{row.pageCount === null ? "Unknown" : `${row.pageCount} ${row.pageCount === 1 ? "page" : "pages"}`}</dd></div>
                        <div><dt>Uploaded</dt><dd>{dateLabel(row.uploadedOn.slice(0, 10))}</dd></div>
                        {row.isPasswordProtected && <div><dt>Protection</dt><dd>Password protected</dd></div>}
                        {row.parsedStatus && <div><dt>Parsing</dt><dd>{readable(row.parsedStatus)}</dd></div>}
                        {row.reconciliationStatus && <div><dt>Reconciliation</dt><dd>{readable(row.reconciliationStatus)}</dd></div>}
                        {row.layoutId && <div><dt>Layout</dt><dd>{row.layoutId}{row.layoutVersion ? ` · version ${row.layoutVersion}` : ""}</dd></div>}
                      </dl>
                    </CollapsibleContent>
                  </Collapsible>
                </article>
              );
            })}
        </div>
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
