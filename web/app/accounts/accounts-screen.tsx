"use client";

import { Fragment, useEffect, useRef, useState } from "react";

import { EyeIcon } from "@/app/accounts/eye-icon";
import { ValuationPanel } from "@/app/accounts/valuation-panel";
import { AttentionRow } from "@/app/ui/attention-row";
import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { MetricStrip } from "@/app/ui/metric-strip";
import { RowActions } from "@/app/ui/row-actions";
import { StatusLabel } from "@/app/ui/status-label";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import {
  ACCOUNT_ROLES,
  ACCOUNT_ROLE_LABEL,
  ACCOUNT_TYPES,
  INSTITUTION_TYPES,
  LIABILITY_TYPES,
  accountRole,
} from "@/lib/accounts/types";
import { ApiError, type apiFetch, errorText } from "@/lib/api/client";
import { formatOwed, moneyText } from "@/lib/money/format";
import Link from "next/link";
import type {
  AccountCreate,
  AccountGroupTotal,
  AccountIdentifierRow,
  AccountRow,
  AccountUpdate,
  CurrencyRow,
  DashboardResponse,
  HouseholdCurrency,
  InstitutionCreate,
  InstitutionRow,
  RevealedAccountIdentifier,
  UnavailableAccountIdentifier,
} from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  totals: Array<AccountGroupTotal>;
  institutions: Array<InstitutionRow>;
  currencies: Array<CurrencyRow>;
  baseCurrency: string;
  // The reporting currency chosen in the top bar: the reporting-pair strip follows it, while the
  // grouped subtotals below stay in each account's own native currency.
  currency: HouseholdCurrency;
  request: typeof apiFetch;
  onChanged: () => void;
};

/** The four evidence states the specification allows. No age threshold is invented: an old date
 * is not a problem unless a typed response already says so. */
type EvidenceStatus = "needs_confirmation" | "missing_snapshot" | "coverage_problem" | "current";

const EVIDENCE_LABEL: Record<EvidenceStatus, string> = {
  needs_confirmation: "Needs confirmation",
  missing_snapshot: "Missing snapshot",
  coverage_problem: "Coverage problem",
  current: "Current",
};

const EVIDENCE_TONE: Record<EvidenceStatus, "attention" | "danger" | "success"> = {
  needs_confirmation: "attention",
  missing_snapshot: "attention",
  coverage_problem: "danger",
  current: "success",
};

export function evidenceStatus(
  account: AccountRow,
  coverageProblems: ReadonlySet<string>,
): EvidenceStatus {
  if (!account.openingBalanceConfirmedAt) return "needs_confirmation";
  if (account.valuationMode === "snapshot" && !account.latestValuationDate) return "missing_snapshot";
  if (coverageProblems.has(account.id)) return "coverage_problem";
  return "current";
}

/** The account ids a dashboard response already names as a typed coverage problem. A missing
 * snapshot has its own status above, and a current price or current snapshot is not a problem. */
function coverageProblemAccounts(dashboard: DashboardResponse | undefined): ReadonlySet<string> {
  return new Set(
    (dashboard?.netWorth.investmentCoverage ?? [])
      .filter((item) => !["current_price", "current_snapshot", "missing_snapshot"].includes(item.status))
      .map((item) => item.accountId),
  );
}

type Draft = {
  name: string;
  institutionId: string;
  accountType: string;
  accountSubtype: string;
  currency: string;
  country: string;
  accountNumber: string;
  openedOn: string;
  includeInNetWorth: boolean;
  isLiquid: boolean;
  openingBalance: string;
  openingBalanceDate: string;
  openingBalanceNote: string;
  creditLimit: string;
};

const MONTHS: Record<string, string> = {
  "01": "Jan",
  "02": "Feb",
  "03": "Mar",
  "04": "Apr",
  "05": "May",
  "06": "Jun",
  "07": "Jul",
  "08": "Aug",
  "09": "Sep",
  "10": "Oct",
  "11": "Nov",
  "12": "Dec",
};

// The overview's coverage reasons deep-link a snapshot-mode account straight into its valuation
// panel (`/accounts?valuation={accountId}`), the same query-string convention `workspace.tsx`
// already reads for `sourceDocumentId`. `accounts` is always the fully loaded list by the time
// this screen mounts (Workspace withholds it until `/accounts` resolves), so the target is read
// once as each state's own lazy initializer rather than a mount effect — an id that resolves to
// no real, active asset account is silently ignored rather than opening a panel for a closed
// account or a liability.
function deepLinkedValuationAccount(accounts: Array<AccountRow>): AccountRow | undefined {
  if (typeof window === "undefined") return undefined;
  const accountId = new URLSearchParams(window.location.search).get("valuation");
  if (!accountId) return undefined;
  return accounts.find((row) => row.id === accountId && row.isActive && row.accountClass === "asset");
}

function emptyDraft(currencies: Array<CurrencyRow>, institutions: Array<InstitutionRow>): Draft {
  return {
    name: "",
    institutionId: "",
    accountType: "savings",
    accountSubtype: "",
    currency: currencies[0]?.code ?? "",
    country: institutions[0]?.country ?? "",
    accountNumber: "",
    openedOn: "",
    includeInNetWorth: true,
    isLiquid: true,
    openingBalance: "",
    openingBalanceDate: "",
    openingBalanceNote: "",
    creditLimit: "",
  };
}

function draftFrom(account: AccountRow): Draft {
  return {
    name: account.name,
    institutionId: account.institutionId ?? "",
    accountType: account.accountType,
    accountSubtype: account.accountSubtype ?? "",
    currency: account.currency,
    country: account.country,
    accountNumber: "",
    openedOn: account.openedOn ?? "",
    includeInNetWorth: account.includeInNetWorth,
    isLiquid: account.isLiquid,
    openingBalance: account.openingBalance,
    openingBalanceDate: account.openingBalanceDate,
    openingBalanceNote: account.openingBalanceNote ?? "",
    creditLimit: account.creditLimit ?? "",
  };
}

function formatDay(isoDate: string) {
  const [year, month, day] = isoDate.split("-");
  return `${day.replace(/^0/, "")} ${MONTHS[month]} ${year}`;
}

function asOfFor(rows: Array<AccountRow>, currency: string) {
  const included = rows.filter((row) => row.includeInNetWorth && row.currency === currency);
  if (included.length === 0 || included.some((row) => !row.currentBalanceAsOf)) return "";
  const dates = [
    ...new Set(included.map((row) => row.currentBalanceAsOf?.slice(0, 10)).filter((day): day is string => Boolean(day))),
  ].sort();
  if (dates.length === 1) return ` as of ${formatDay(dates[0])}`;
  return ` as of ${formatDay(dates[0])}–${formatDay(dates[dates.length - 1])}`;
}

function latestBalanceDate(rows: Array<AccountRow>) {
  const dates = rows
    .map((row) => row.currentBalanceAsOf?.slice(0, 10))
    .filter((date): date is string => Boolean(date))
    .sort();
  return dates.length > 0 ? formatDay(dates[dates.length - 1]) : null;
}

function unsigned(value: string) {
  return value.startsWith("-") ? value.slice(1) : value;
}

// The write side, not display: a liability's opening is entered as owed and sent negative.
function signedOpening(accountType: string, value: string) {
  if (!LIABILITY_TYPES.has(accountType) || value === "" || value.startsWith("-")) {
    return value;
  }
  return `-${value}`;
}

type ValuationMode = NonNullable<AccountRow["valuationMode"]>;

const VALUATION_MODE_LABEL: Record<ValuationMode, string> = {
  ledger_balance: "Ledger balance",
  holdings: "Holdings",
  snapshot: "Dated valuation",
};

function markFor(account: AccountRow, institutions: Array<InstitutionRow>) {
  const match = institutions.find((row) => row.id === account.institutionId);
  const source = match?.shortName || account.institutionName || account.name;
  return source.slice(0, 2).toUpperCase();
}

function toCreate(draft: Draft): AccountCreate {
  return {
    name: draft.name,
    institutionId: draft.institutionId || null,
    accountType: draft.accountType,
    accountSubtype: draft.accountSubtype || null,
    currency: draft.currency,
    country: draft.country,
    accountNumber: draft.accountNumber || null,
    openedOn: draft.openedOn || null,
    includeInNetWorth: draft.includeInNetWorth,
    isLiquid: draft.isLiquid,
    openingBalance: signedOpening(draft.accountType, draft.openingBalance),
    openingBalanceDate: draft.openingBalanceDate,
    openingBalanceNote: draft.openingBalanceNote || null,
    creditLimit: draft.accountType === "credit_card" ? draft.creditLimit || null : null,
  };
}

function toUpdate(draft: Draft, closedOn: string | null, acknowledge: boolean): AccountUpdate {
  return {
    name: draft.name,
    institutionId: draft.institutionId || null,
    accountType: draft.accountType,
    accountSubtype: draft.accountSubtype || null,
    currency: draft.currency,
    country: draft.country,
    openedOn: draft.openedOn || null,
    closedOn,
    includeInNetWorth: draft.includeInNetWorth,
    isLiquid: draft.isLiquid,
    openingBalance: signedOpening(draft.accountType, draft.openingBalance),
    openingBalanceDate: draft.openingBalanceDate,
    openingBalanceNote: draft.openingBalanceNote || null,
    creditLimit: draft.accountType === "credit_card" ? draft.creditLimit || null : null,
    acknowledgeOpeningBalance: acknowledge,
  };
}

function AccountTableHead() {
  return (
    <thead>
      <tr>
        <th scope="col">Account</th>
        <th scope="col">Role</th>
        <th scope="col">Currency</th>
        <th scope="col">Evidence</th>
        <th scope="col">Status</th>
        <th className="console-number" scope="col">Balance</th>
        <th scope="col"><span className="visually-hidden">Actions</span></th>
      </tr>
    </thead>
  );
}

function isAuthError(error: unknown) {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

function identifierName(kind: AccountIdentifierRow["kind"]) {
  if (kind === "iban") return "IBAN";
  if (kind === "card") return "card number";
  return "bank account number";
}

function identifierHeading(kind: AccountIdentifierRow["kind"]) {
  if (kind === "iban") return "IBAN";
  if (kind === "card") return "Card";
  return "Account";
}

function revealFailure(kind: AccountIdentifierRow["kind"]) {
  if (kind === "iban") return "The IBAN could not be revealed.";
  if (kind === "card") return "The card number could not be revealed.";
  return "The account number could not be revealed.";
}

function AccountIdentifierValue({
  accountId,
  accountName,
  identifier,
  request,
}: {
  accountId: string;
  accountName: string;
  identifier: AccountIdentifierRow;
  request: typeof apiFetch;
}) {
  const [revealed, setRevealed] = useState<string>();
  const [note, setNote] = useState<string>();
  const [busy, setBusy] = useState(false);
  const revealController = useRef<AbortController | undefined>(undefined);
  const name = identifierName(identifier.kind);

  useEffect(() => () => {
    revealController.current?.abort();
    revealController.current = undefined;
  }, []);

  async function reveal() {
    if (revealed) {
      setRevealed(undefined);
      setNote(undefined);
      return;
    }
    setBusy(true);
    setNote(undefined);
    const controller = new AbortController();
    revealController.current = controller;
    try {
      const result = await request<RevealedAccountIdentifier | UnavailableAccountIdentifier>(
        `/accounts/${accountId}/identifiers/${identifier.id}/reveal`,
        { method: "POST", signal: controller.signal },
      );
      if (controller.signal.aborted || revealController.current !== controller) return;
      if (result.status === "revealed") {
        setRevealed(result.value);
      } else {
        setRevealed(undefined);
        setNote("Statement supplied only a masked value.");
      }
    } catch (error) {
      if (controller.signal.aborted || revealController.current !== controller) return;
      setRevealed(undefined);
      if (!isAuthError(error)) {
        setNote(revealFailure(identifier.kind));
      }
    } finally {
      if (revealController.current === controller) {
        revealController.current = undefined;
        setBusy(false);
      }
    }
  }

  const explanationId = `identifier-note-${identifier.id}`;
  const canReveal = identifier.canReveal;
  const action = revealed ? "Hide" : "Show";
  const accessibleName = canReveal
    ? `${action} ${name} for ${accountName}`
    : `Full ${name} unavailable for ${accountName}`;

  return (
    <div className="account-identifier">
      <span className="account-identifier-kind">
        {identifierHeading(identifier.kind)}{identifier.isActive ? "" : " · Previous"}
      </span>
      <span className="account-identifier-line">
        <span className="account-identifier-value">{revealed ?? identifier.masked}</span>
        <Button
          aria-describedby={!canReveal || note ? explanationId : undefined}
          aria-label={accessibleName}
          className="account-identifier-reveal"
          disabled={!canReveal || busy}
          onClick={() => void reveal()}
          size="icon-xs"
          type="button"
          variant="ghost"
        >
          <EyeIcon hidden={Boolean(revealed)} />
        </Button>
      </span>
      {!canReveal && (
        <span className="account-identifier-note" id={explanationId}>
          Statement supplied only a masked value.
        </span>
      )}
      {note && (
        <span className="account-identifier-note" id={explanationId} role={note.startsWith("The ") ? "alert" : undefined}>
          {note}
        </span>
      )}
    </div>
  );
}

function today() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

export function AccountsScreen({
  accounts,
  totals,
  institutions,
  currencies,
  baseCurrency,
  currency,
  request,
  onChanged,
}: Props) {
  const [mode, setMode] = useState<"view" | "add" | "edit" | "valuation">(() =>
    deepLinkedValuationAccount(accounts) ? "valuation" : "view",
  );
  const [editing, setEditing] = useState<AccountRow>();
  const [valuing, setValuing] = useState<AccountRow | undefined>(() => deepLinkedValuationAccount(accounts));
  const [draft, setDraft] = useState<Draft>(() => emptyDraft(currencies, institutions));
  const [addingInstitution, setAddingInstitution] = useState(false);
  const [institutionDraft, setInstitutionDraft] = useState<InstitutionCreate>({
    name: "",
    shortName: "",
    country: "",
    institutionType: "bank",
  });
  const [problem, setProblem] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [held, setHeld] = useState<{
    accounts: Array<AccountRow>;
    institutions: Array<InstitutionRow>;
  }>();
  const locked = busy || (held != null && held.accounts === accounts && held.institutions === institutions);
  // The reporting-pair figures and the typed coverage problems both come from `/dashboard`,
  // which already resolves one valuation source per account. Fetched once: the strip is
  // orientation, and a balance change reaches it on the next visit rather than through a refetch
  // on every row mutation. The native grouped subtotals below always come from `/accounts`.
  const [dashboard, setDashboard] = useState<DashboardResponse>();
  const [dashboardState, setDashboardState] = useState<"loading" | "ready" | "error">("loading");
  // Only the accounts whose evidence needs an owner action, when the attention row asks for them.
  const [evidenceOnly, setEvidenceOnly] = useState(false);
  // Focus returns to the row action that opened the valuation panel, not to the top of the page.
  const valuationOpener = useRef<string>(undefined);

  useEffect(() => {
    let current = true;
    request<DashboardResponse>("/dashboard")
      .then((loaded) => {
        if (!current) return;
        setDashboard(loaded);
        setDashboardState("ready");
      })
      .catch(() => {
        if (current) setDashboardState("error");
      });
    return () => {
      current = false;
    };
  }, [request]);

  function holdUntilRefresh() {
    setHeld({ accounts, institutions });
  }

  const active = accounts.filter((row) => row.isActive);
  const closed = accounts.filter((row) => !row.isActive);
  const latestBalance = latestBalanceDate(active);
  function openAdd() {
    setMode("add");
    setEditing(undefined);
    setDraft(emptyDraft(currencies, institutions));
    setProblem(undefined);
  }

  function openEdit(account: AccountRow) {
    setMode("edit");
    setEditing(account);
    setDraft(draftFrom(account));
    setProblem(undefined);
  }

  function openValuation(account: AccountRow, openerId?: string) {
    valuationOpener.current = openerId;
    setMode("valuation");
    setValuing(account);
    setProblem(undefined);
  }

  function closeValuation() {
    setMode("view");
    setValuing(undefined);
    const opener = valuationOpener.current;
    valuationOpener.current = undefined;
    if (opener) window.setTimeout(() => document.getElementById(opener)?.focus(), 0);
  }

  function field<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function saveCreate() {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<AccountRow>("/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toCreate(draft)),
      });
      setMode("view");
      setDraft(emptyDraft(currencies, institutions));
      holdUntilRefresh();
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(errorText(error, "The account could not be created. Try again."));
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit() {
    if (!editing) return;
    setBusy(true);
    setProblem(undefined);
    try {
      await request<AccountRow>(`/accounts/${editing.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toUpdate(draft, editing.closedOn, Boolean(editing.openingBalanceConfirmedAt))),
      });
      setMode("view");
      setEditing(undefined);
      setDraft(emptyDraft(currencies, institutions));
      holdUntilRefresh();
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(errorText(error, "The account could not be saved. Try again."));
    } finally {
      setBusy(false);
    }
  }

  async function acknowledge(account: AccountRow) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<AccountRow>(`/accounts/${account.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toUpdate(draftFrom(account), account.closedOn, true)),
      });
      holdUntilRefresh();
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(errorText(error, "The opening balance could not be acknowledged."));
    } finally {
      setBusy(false);
    }
  }

  async function closeAccount(account: AccountRow) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<AccountRow>(`/accounts/${account.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toUpdate(draftFrom(account), today(), Boolean(account.openingBalanceConfirmedAt))),
      });
      holdUntilRefresh();
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(errorText(error, "The account could not be closed."));
    } finally {
      setBusy(false);
    }
  }

  async function reopenAccount(account: AccountRow) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<AccountRow>(`/accounts/${account.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toUpdate(draftFrom(account), null, Boolean(account.openingBalanceConfirmedAt))),
      });
      holdUntilRefresh();
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(errorText(error, "The account could not be reopened."));
    } finally {
      setBusy(false);
    }
  }

  async function saveInstitution() {
    setBusy(true);
    setProblem(undefined);
    try {
      const created = await request<InstitutionRow>("/institutions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...institutionDraft,
          shortName: institutionDraft.shortName || null,
        }),
      });
      setDraft((current) => ({
        ...current,
        institutionId: created.id,
        country: current.country || created.country,
      }));
      setAddingInstitution(false);
      holdUntilRefresh();
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(errorText(error, "The institution could not be created."));
    } finally {
      setBusy(false);
    }
  }

  function balanceLabel(account: AccountRow) {
    // A snapshot-valued account's stored `currentBalance` is a placeholder the net-worth sum
    // already ignores, and the Evidence column beside it names the valuation's own date — so
    // printing that balance would pair a ledger figure with a valuation date. `/accounts` does
    // not carry the snapshot's value, so this row states where the value lives instead: nothing
    // recorded reads as unavailable, and a recorded one sends the owner to its evidence.
    if (account.valuationMode === "snapshot") {
      return account.latestValuationDate ? "See valuation" : "Not available";
    }
    const currency = currencies.find((row) => row.code === account.currency);
    if (account.accountClass !== "liability" || !currency) {
      return moneyText(account.currentBalance, account.currency, currencies);
    }
    return formatOwed(account.currentBalance, currency);
  }

  function renderForm() {
    const creating = mode === "add";
    return (
      <form
        className="account-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (creating) void saveCreate();
          else void saveEdit();
        }}
      >
        <h2>{creating ? "New account" : `Edit ${editing?.name ?? "account"}`}</h2>
        <label className="text-field">
          <span>Account name</span>
          <Input onChange={(event) => field("name", event.target.value)} required value={draft.name} />
        </label>
        <label className="select-field">
          <span>Institution</span>
          <NativeSelect onChange={(event) => field("institutionId", event.target.value)} value={draft.institutionId}>
            <option value="">None</option>
            {institutions.map((row) => (
              <option key={row.id} value={row.id}>{row.name}</option>
            ))}
          </NativeSelect>
        </label>
        <Button className="justify-self-start" onClick={() => setAddingInstitution((open) => !open)} type="button" variant="outline">
          Add institution
        </Button>
        {addingInstitution && (
          <fieldset className="institution-fields">
            <legend>New institution</legend>
            <label className="text-field">
              <span>Institution name</span>
              <Input
                onChange={(event) => setInstitutionDraft((current) => ({ ...current, name: event.target.value }))}
                value={institutionDraft.name}
              />
            </label>
            <label className="text-field">
              <span>Short name</span>
              <Input
                onChange={(event) => setInstitutionDraft((current) => ({ ...current, shortName: event.target.value }))}
                value={institutionDraft.shortName ?? ""}
              />
            </label>
            <label className="text-field">
              <span>Institution country</span>
              <Input
                maxLength={2}
                onChange={(event) => setInstitutionDraft((current) => ({
                  ...current,
                  country: event.target.value.toUpperCase(),
                }))}
                value={institutionDraft.country}
              />
            </label>
            <label className="select-field">
              <span>Institution type</span>
              <NativeSelect
                onChange={(event) => setInstitutionDraft((current) => ({
                  ...current,
                  institutionType: event.target.value,
                }))}
                value={institutionDraft.institutionType}
              >
                {INSTITUTION_TYPES.map((type) => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </NativeSelect>
            </label>
            <Button className="self-start" disabled={locked} onClick={() => void saveInstitution()} type="button">Save institution</Button>
          </fieldset>
        )}
        <label className="select-field">
          <span>Account type</span>
          <NativeSelect onChange={(event) => field("accountType", event.target.value)} value={draft.accountType}>
            {ACCOUNT_TYPES.map((type) => (
              <option key={type} value={type}>{type}</option>
            ))}
          </NativeSelect>
        </label>
        <label className="text-field">
          <span>Subtype</span>
          <Input onChange={(event) => field("accountSubtype", event.target.value)} value={draft.accountSubtype} />
        </label>
        <label className="select-field">
          <span>Currency</span>
          <NativeSelect onChange={(event) => field("currency", event.target.value)} value={draft.currency}>
            {currencies.map((row) => (
              <option key={row.code} value={row.code}>{row.code}</option>
            ))}
          </NativeSelect>
        </label>
        <label className="text-field">
          <span>Country</span>
          <Input
            maxLength={2}
            onChange={(event) => field("country", event.target.value.toUpperCase())}
            required
            value={draft.country}
          />
        </label>
        {creating && (
          <label className="text-field">
            <span>Account or card number</span>
            <Input
              autoComplete="off"
              onChange={(event) => field("accountNumber", event.target.value)}
              value={draft.accountNumber}
            />
          </label>
        )}
        <label className="date-field">
          <span>Opened on</span>
          <Input onChange={(event) => field("openedOn", event.target.value)} type="date" value={draft.openedOn} />
        </label>
        <label className="text-field">
          <span>{LIABILITY_TYPES.has(draft.accountType) ? "Amount owed" : "Opening balance"}</span>
          <Input
            inputMode="decimal"
            onChange={(event) => field(
              "openingBalance",
              LIABILITY_TYPES.has(draft.accountType)
                ? signedOpening(draft.accountType, event.target.value)
                : event.target.value,
            )}
            required
            type="text"
            value={
              LIABILITY_TYPES.has(draft.accountType)
                ? unsigned(draft.openingBalance)
                : draft.openingBalance
            }
          />
        </label>
        <label className="date-field">
          <span>Opening balance date</span>
          <Input
            onChange={(event) => field("openingBalanceDate", event.target.value)}
            required
            type="date"
            value={draft.openingBalanceDate}
          />
        </label>
        <label className="text-field">
          <span>Opening balance note</span>
          <Input
            onChange={(event) => field("openingBalanceNote", event.target.value)}
            value={draft.openingBalanceNote}
          />
        </label>
        <label className="text-field">
          <span>Credit limit</span>
          <Input
            inputMode="decimal"
            onChange={(event) => field("creditLimit", event.target.value)}
            required={draft.accountType === "credit_card"}
            type="text"
            value={draft.creditLimit}
          />
        </label>
        <Field orientation="horizontal">
          <Checkbox
            checked={draft.includeInNetWorth}
            id="account-include-net-worth"
            onCheckedChange={(checked) => field("includeInNetWorth", checked === true)}
          />
          <FieldLabel htmlFor="account-include-net-worth">Include in net worth</FieldLabel>
        </Field>
        <Field orientation="horizontal">
          <Checkbox
            checked={draft.isLiquid}
            id="account-is-liquid"
            onCheckedChange={(checked) => field("isLiquid", checked === true)}
          />
          <FieldLabel htmlFor="account-is-liquid">Liquid</FieldLabel>
        </Field>
        {problem && <p className="inline-alert" role="alert">{problem}</p>}
        <div className="account-form-actions">
          <Button disabled={locked} type="submit">
            {creating ? "Create account" : "Save account"}
          </Button>
          <Button
            onClick={() => {
              setMode("view");
              setEditing(undefined);
              setDraft(emptyDraft(currencies, institutions));
            }}
            type="button"
            variant="outline"
          >Cancel</Button>
        </div>
      </form>
    );
  }

  function renderRow(account: AccountRow) {
    const needsFirstValuation = account.valuationMode === "snapshot" && !account.latestValuationDate;
    const status = evidenceStatus(account, coverageProblems);
    const evidenceDate = account.valuationMode === "snapshot"
      ? account.latestValuationDate
      : account.currentBalanceAsOf?.slice(0, 10) ?? null;
    const triggerId = `account-actions-${account.id}`;
    const detail = [
      account.institutionName,
      account.accountSubtype,
      account.accountClass === "asset" && account.valuationMode
        ? VALUATION_MODE_LABEL[account.valuationMode]
        : null,
      account.includeInNetWorth ? null : "Excluded from totals",
      account.isActive ? null : account.closedOn ? `Closed ${formatDay(account.closedOn)}` : "Closed",
    ].filter(Boolean).join(" · ");

    return (
      <tr key={account.id}>
        <th scope="row">
          <div className="console-entity">
            <span aria-hidden="true" className="console-entity-mark">{markFor(account, institutions)}</span>
            <div>
              <span className="console-entity-title">{account.name}</span>
              <span className="console-entity-sub">{detail}</span>
              {mode === "view" && account.identifiers.length > 0 && (
                <span className="account-identifier-list">
                  {account.identifiers.map((identifier) => (
                    <AccountIdentifierValue
                      accountId={account.id}
                      accountName={account.name}
                      identifier={identifier}
                      key={identifier.id}
                      request={request}
                    />
                  ))}
                </span>
              )}
            </div>
          </div>
        </th>
        <td>{ACCOUNT_ROLE_LABEL[accountRole(account.accountType)]}</td>
        <td>{account.currency}</td>
        <td>{evidenceDate ? formatDay(evidenceDate) : "Not recorded"}</td>
        <td>
          <StatusLabel tone={EVIDENCE_TONE[status]}>{EVIDENCE_LABEL[status]}</StatusLabel>
        </td>
        <td
          aria-label={`${account.name} balance ${balanceLabel(account)}`}
          className={
            account.accountClass === "liability" && account.currentBalance.startsWith("-")
              && status !== "missing_snapshot"
              ? "console-number account-owed"
              : "console-number"
          }
        >
          {balanceLabel(account)}
        </td>
        <td className="console-row-actions">
          <RowActions floating label={`Actions for ${account.name}`} triggerContent="⋯" triggerId={triggerId}>
            <Button
              aria-label={`Edit ${account.name}`}
              disabled={locked}
              onClick={() => openEdit(account)}
              size="sm"
              type="button"
              variant="ghost"
            >Edit</Button>
            {account.accountClass === "asset" && account.isActive && (
              <Button
                aria-label={
                  needsFirstValuation
                    ? `Add valuation for ${account.name}`
                    : `Valuation for ${account.name}`
                }
                disabled={locked}
                onClick={() => openValuation(account, triggerId)}
                size="sm"
                type="button"
                variant="ghost"
              >{needsFirstValuation ? "Add valuation" : "Valuation"}</Button>
            )}
            {!account.openingBalanceConfirmedAt && (
              <Button
                aria-label={`Acknowledge ${account.name}`}
                disabled={locked}
                onClick={() => void acknowledge(account)}
                size="sm"
                type="button"
                variant="ghost"
              >Acknowledge opening balance</Button>
            )}
            {account.isActive ? (
              <Button
                aria-label={`Close ${account.name}`}
                disabled={locked}
                onClick={() => void closeAccount(account)}
                size="sm"
                type="button"
                variant="ghost"
              >Close</Button>
            ) : (
              <Button
                aria-label={`Reopen ${account.name}`}
                disabled={locked}
                onClick={() => void reopenAccount(account)}
                size="sm"
                type="button"
                variant="ghost"
              >Reopen</Button>
            )}
          </RowActions>
        </td>
      </tr>
    );
  }

  /** One table, the display roles in the specification's order; inside a role, by name then the
   * stable id, so the same inventory always renders in the same order. */
  function renderRoleTable(rows: Array<AccountRow>, label: string) {
    const ordered = ACCOUNT_ROLES.map((role) => ({
      role,
      rows: rows
        .filter((row) => accountRole(row.accountType) === role)
        .sort((left, right) => left.name.localeCompare(right.name) || left.id.localeCompare(right.id)),
    })).filter((group) => group.rows.length > 0);

    return (
      <DataTableRegion label={`${label} table`}>
        <table className="console-table">
          <caption className="visually-hidden">{label}</caption>
          <AccountTableHead />
          <tbody>
            {ordered.map((group) => (
              <Fragment key={group.role}>
                <tr className="console-group-row">
                  <td colSpan={7}>
                    {ACCOUNT_ROLE_LABEL[group.role]} · {group.rows.length}{" "}
                    {group.rows.length === 1 ? "account" : "accounts"}
                  </td>
                </tr>
                {group.rows.map(renderRow)}
              </Fragment>
            ))}
          </tbody>
        </table>
      </DataTableRegion>
    );
  }

  const coverageProblems = coverageProblemAccounts(dashboard);
  const attention = active.filter((row) => evidenceStatus(row, coverageProblems) !== "current");
  const needingConfirmation = attention.filter((row) => !row.openingBalanceConfirmedAt).length;
  const needingSnapshot = attention.filter(
    (row) => evidenceStatus(row, coverageProblems) === "missing_snapshot",
  ).length;
  const needingCoverage = attention.filter(
    (row) => evidenceStatus(row, coverageProblems) === "coverage_problem",
  ).length;
  const shown = evidenceOnly ? attention : active;

  if (accounts.length === 0 && mode === "view") {
    return (
      <section className="workspace-content accounts-screen" aria-labelledby="accounts-title">
        <ConsolePageHeader
          title="Accounts"
          description="Balances, evidence state and account roles in one operational view."
        />
        <div className="console-empty-focus">
          <h2 id="accounts-empty-title">Add your first account</h2>
          <p>
            An account is a named store of money you own or owe. Start with its opening balance;
            closed accounts remain visible so their history is not lost.
          </p>
          <div className="console-empty-actions">
            <Button onClick={openAdd} type="button">Add account</Button>
          </div>
        </div>
      </section>
    );
  }

  const reportingBase = dashboard ? currency.code === dashboard.netWorth.baseCurrency : true;
  const reporting = dashboard
    ? {
        code: reportingBase ? dashboard.netWorth.baseCurrency : dashboard.netWorth.secondaryCurrency,
        assets: reportingBase ? dashboard.netWorth.assetsBase : dashboard.netWorth.assetsSecondary,
        liabilities: reportingBase
          ? dashboard.netWorth.liabilitiesBase
          : dashboard.netWorth.liabilitiesSecondary,
      }
    : undefined;
  // `/dashboard` sums active, included accounts only, so the caption counts the same population.
  const counted = active.filter((row) => row.includeInNetWorth);
  const assetCount = counted.filter((row) => row.accountClass === "asset").length;
  const liabilityCount = counted.filter((row) => row.accountClass === "liability").length;
  const unavailableDetail = dashboardState === "error"
    ? "The reporting total could not be loaded"
    : dashboardState === "loading"
      ? "Loading the reporting total"
      : `Not stated in ${currency.code} for this date`;

  return (
    <section className="workspace-content accounts-screen" aria-labelledby="accounts-title">
      <ConsolePageHeader
        title="Accounts"
        description="Balances, evidence state and account roles in one operational view."
        actions={mode === "view" ? (
          <>
            <Link className={buttonVariants({ variant: "outline" })} href="/import">Import evidence</Link>
            <Button onClick={openAdd} type="button">Add account</Button>
          </>
        ) : undefined}
      />

      <MetricStrip
        label="Position summary"
        items={[
          {
            label: "Assets",
            value: reporting?.assets == null
              ? "Not available"
              : moneyText(reporting.assets, reporting.code, currencies),
            detail: reporting?.assets == null
              ? unavailableDetail
              : `Across ${assetCount} ${assetCount === 1 ? "asset account" : "asset accounts"}`,
          },
          {
            label: "Liabilities",
            value: reporting?.liabilities == null
              ? "Not available"
              : moneyText(unsigned(reporting.liabilities), reporting.code, currencies),
            detail: reporting?.liabilities == null
              ? unavailableDetail
              : `Owed across ${liabilityCount} ${liabilityCount === 1 ? "liability account" : "liability accounts"}`,
          },
          {
            // The opening and snapshot counts come from `/accounts` and are always exact; the
            // coverage count needs `/dashboard`, so until that answers this tile says what it
            // does not yet know rather than reporting an all-clear.
            label: "Evidence actions",
            value: attention.length === 0 && dashboardState === "ready"
              ? "None"
              : `${attention.length} ${attention.length === 1 ? "account" : "accounts"}`,
            detail: dashboardState !== "ready"
              ? [
                  ...(needingConfirmation + needingSnapshot > 0
                    ? [`${needingConfirmation} opening · ${needingSnapshot} snapshot`]
                    : ["No opening or snapshot action"]),
                  dashboardState === "error"
                    ? "coverage problems could not be loaded"
                    : "coverage problems not loaded yet",
                ].join(" · ")
              : attention.length === 0
                ? "Every open account has current evidence"
                : [
                    needingConfirmation > 0 ? `${needingConfirmation} opening` : null,
                    needingSnapshot > 0 ? `${needingSnapshot} snapshot` : null,
                    needingCoverage > 0 ? `${needingCoverage} coverage` : null,
                  ].filter(Boolean).join(" · "),
          },
        ]}
      />

      {attention.length > 0 && (
        <AttentionRow
          tone="attention"
          title={`${attention.length} ${attention.length === 1 ? "account needs" : "accounts need"} an evidence action`}
          detail="Confirm an opening balance, record a required valuation, or resolve a named coverage problem."
          action={
            <Button onClick={() => setEvidenceOnly((only) => !only)} type="button" variant="outline">
              {evidenceOnly ? "Show all accounts" : "Show accounts needing evidence"}
            </Button>
          }
        />
      )}

      {problem && mode === "view" && <p className="inline-alert" role="alert">{problem}</p>}
      {(mode === "add" || mode === "edit") && renderForm()}
      {mode === "valuation" && valuing && (
        <ValuationPanel
          accountId={valuing.id}
          accountName={valuing.name}
          baseCurrency={baseCurrency}
          currencies={currencies}
          currency={valuing.currency}
          onChanged={() => {
            holdUntilRefresh();
            onChanged();
          }}
          onClose={closeValuation}
          request={request}
        />
      )}

      <div className="console-section-head">
        <h2 id="accounts-title">Accounts by role</h2>
        <span>
          {evidenceOnly
            ? `${shown.length} of ${active.length} open ${active.length === 1 ? "account" : "accounts"}`
            : `${active.length} open ${active.length === 1 ? "account" : "accounts"}`}
          {latestBalance ? ` · latest balance ${latestBalance}` : ""}
        </span>
      </div>
      {shown.length === 0
        ? <p>No open account matches this view.</p>
        : renderRoleTable(shown, "Open accounts by role")}

      <details className="console-details">
        <summary>Grouped native totals</summary>
        <div className="console-details-body">
          <p className="console-module-sub">
            Each group&rsquo;s exact total in its own currency, never converted or combined.
          </p>
          <DataTableRegion label="Grouped native totals table">
            <table className="console-table">
              <caption className="visually-hidden">Grouped native totals</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Currency</th>
                  <th className="console-number" scope="col">Total</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {totals.map((row) => {
                  const rows = active.filter(
                    (item) => item.accountClass === row.accountClass && item.country === row.country,
                  );
                  const owedTotal = row.accountClass === "liability" && row.total.startsWith("-");
                  return (
                    <tr key={`${row.accountClass}-${row.country}-${row.currency}-${row.total}`}>
                      <th scope="row">
                        {row.accountClass === "asset" ? "Assets" : "Liabilities"} · {row.country}
                      </th>
                      <td>{row.currency}</td>
                      <td className="console-number">
                        {moneyText(owedTotal ? unsigned(row.total) : row.total, row.currency, currencies)}
                        {owedTotal ? " owed" : ""}
                      </td>
                      <td>{asOfFor(rows, row.currency).replace(/^ as of /, "") || "No shared date"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </DataTableRegion>
        </div>
      </details>

      {closed.length > 0 && (
        <details className="console-details">
          <summary>Closed accounts ({closed.length})</summary>
          <div className="console-details-body">
            <p className="console-module-sub">Kept for history and reconciliation; edit, reopen and evidence stay available.</p>
            {renderRoleTable(closed, "Closed accounts by role")}
          </div>
        </details>
      )}
    </section>
  );
}
