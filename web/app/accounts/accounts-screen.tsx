"use client";

import { useEffect, useRef, useState } from "react";

import { EyeIcon } from "@/app/accounts/eye-icon";
import { ValuationPanel } from "@/app/accounts/valuation-panel";
import { ACCOUNT_TYPES, INSTITUTION_TYPES, LIABILITY_TYPES } from "@/lib/accounts/types";
import { ApiError, type apiFetch, errorText } from "@/lib/api/client";
import { formatOwed, moneyText } from "@/lib/money/format";
import type {
  AccountCreate,
  AccountGroupTotal,
  AccountIdentifierRow,
  AccountRow,
  AccountUpdate,
  CurrencyRow,
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
  request: typeof apiFetch;
  onChanged: () => void;
};

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
    <thead className="visually-hidden">
      <tr>
        <th scope="col">Institution</th>
        <th scope="col">Account</th>
        <th scope="col">Detail</th>
        <th scope="col">Masked number</th>
        <th scope="col">Balance</th>
        <th scope="col">Status</th>
        <th scope="col">Evidence</th>
        <th scope="col">Actions</th>
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
        <button
          aria-describedby={!canReveal || note ? explanationId : undefined}
          aria-label={accessibleName}
          className="account-identifier-reveal"
          disabled={!canReveal || busy}
          onClick={() => void reveal()}
          type="button"
        >
          <EyeIcon hidden={Boolean(revealed)} />
        </button>
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

  function holdUntilRefresh() {
    setHeld({ accounts, institutions });
  }

  const active = accounts.filter((row) => row.isActive);
  const closed = accounts.filter((row) => !row.isActive);
  const latestBalance = latestBalanceDate(active);
  const groups = [...new Set(active.map((row) => `${row.accountClass}\0${row.country}`))].sort((left, right) => {
    const [leftClass, leftCountry] = left.split("\0");
    const [rightClass, rightCountry] = right.split("\0");
    if (leftClass !== rightClass) return leftClass === "asset" ? -1 : 1;
    return leftCountry.localeCompare(rightCountry);
  });

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

  function openValuation(account: AccountRow) {
    setMode("valuation");
    setValuing(account);
    setProblem(undefined);
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
          <input onChange={(event) => field("name", event.target.value)} required value={draft.name} />
        </label>
        <label className="select-field">
          <span>Institution</span>
          <select onChange={(event) => field("institutionId", event.target.value)} value={draft.institutionId}>
            <option value="">None</option>
            {institutions.map((row) => (
              <option key={row.id} value={row.id}>{row.name}</option>
            ))}
          </select>
        </label>
        <button onClick={() => setAddingInstitution((open) => !open)} type="button">
          Add institution
        </button>
        {addingInstitution && (
          <fieldset className="institution-fields">
            <legend>New institution</legend>
            <label className="text-field">
              <span>Institution name</span>
              <input
                onChange={(event) => setInstitutionDraft((current) => ({ ...current, name: event.target.value }))}
                value={institutionDraft.name}
              />
            </label>
            <label className="text-field">
              <span>Short name</span>
              <input
                onChange={(event) => setInstitutionDraft((current) => ({ ...current, shortName: event.target.value }))}
                value={institutionDraft.shortName ?? ""}
              />
            </label>
            <label className="text-field">
              <span>Institution country</span>
              <input
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
              <select
                onChange={(event) => setInstitutionDraft((current) => ({
                  ...current,
                  institutionType: event.target.value,
                }))}
                value={institutionDraft.institutionType}
              >
                {INSTITUTION_TYPES.map((type) => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select>
            </label>
            <button disabled={locked} onClick={() => void saveInstitution()} type="button">Save institution</button>
          </fieldset>
        )}
        <label className="select-field">
          <span>Account type</span>
          <select onChange={(event) => field("accountType", event.target.value)} value={draft.accountType}>
            {ACCOUNT_TYPES.map((type) => (
              <option key={type} value={type}>{type}</option>
            ))}
          </select>
        </label>
        <label className="text-field">
          <span>Subtype</span>
          <input onChange={(event) => field("accountSubtype", event.target.value)} value={draft.accountSubtype} />
        </label>
        <label className="select-field">
          <span>Currency</span>
          <select onChange={(event) => field("currency", event.target.value)} value={draft.currency}>
            {currencies.map((row) => (
              <option key={row.code} value={row.code}>{row.code}</option>
            ))}
          </select>
        </label>
        <label className="text-field">
          <span>Country</span>
          <input
            maxLength={2}
            onChange={(event) => field("country", event.target.value.toUpperCase())}
            required
            value={draft.country}
          />
        </label>
        {creating && (
          <label className="text-field">
            <span>Account or card number</span>
            <input
              autoComplete="off"
              onChange={(event) => field("accountNumber", event.target.value)}
              value={draft.accountNumber}
            />
          </label>
        )}
        <label className="date-field">
          <span>Opened on</span>
          <input onChange={(event) => field("openedOn", event.target.value)} type="date" value={draft.openedOn} />
        </label>
        <label className="text-field">
          <span>{LIABILITY_TYPES.has(draft.accountType) ? "Amount owed" : "Opening balance"}</span>
          <input
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
          <input
            onChange={(event) => field("openingBalanceDate", event.target.value)}
            required
            type="date"
            value={draft.openingBalanceDate}
          />
        </label>
        <label className="text-field">
          <span>Opening balance note</span>
          <input
            onChange={(event) => field("openingBalanceNote", event.target.value)}
            value={draft.openingBalanceNote}
          />
        </label>
        <label className="text-field">
          <span>Credit limit</span>
          <input
            inputMode="decimal"
            onChange={(event) => field("creditLimit", event.target.value)}
            required={draft.accountType === "credit_card"}
            type="text"
            value={draft.creditLimit}
          />
        </label>
        <label className="check-field">
          <input
            checked={draft.includeInNetWorth}
            onChange={(event) => field("includeInNetWorth", event.target.checked)}
            type="checkbox"
          />
          <span>Include in net worth</span>
        </label>
        <label className="check-field">
          <input
            checked={draft.isLiquid}
            onChange={(event) => field("isLiquid", event.target.checked)}
            type="checkbox"
          />
          <span>Liquid</span>
        </label>
        {problem && <p className="inline-alert" role="alert">{problem}</p>}
        <div className="account-form-actions">
          <button className="primary-action" disabled={locked} type="submit">
            {creating ? "Create account" : "Save account"}
          </button>
          <button
            onClick={() => {
              setMode("view");
              setEditing(undefined);
              setDraft(emptyDraft(currencies, institutions));
            }}
            type="button"
          >Cancel</button>
        </div>
      </form>
    );
  }

  function renderRow(account: AccountRow) {
    const meta = [
      account.institutionName,
      account.accountSubtype,
      account.currency,
      account.accountClass === "asset" && account.valuationMode
        ? VALUATION_MODE_LABEL[account.valuationMode]
        : null,
    ].filter(Boolean).join(" · ");
    const needsFirstValuation = account.valuationMode === "snapshot" && !account.latestValuationDate;
    return (
      <tr className="account-card" key={account.id}>
        <td className="account-mark">{markFor(account, institutions)}</td>
        <td className="account-card-name">{account.name}</td>
        <td className="account-card-detail">{meta}</td>
        <td className="account-card-mask">
          {mode === "view" && account.identifiers.length > 0 ? (
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
          ) : "—"}
        </td>
        <td
          aria-label={`${account.name} balance ${balanceLabel(account)}`}
          className={
            account.accountClass === "liability" && account.currentBalance.startsWith("-")
              ? "account-card-balance account-owed"
              : "account-card-balance"
          }
        >
          {balanceLabel(account)}
        </td>
        <td className="account-card-status">
          {[
            account.isActive
              ? account.currentBalanceAsOf
                ? `Updated ${formatDay(account.currentBalanceAsOf.slice(0, 10))}`
                : null
              : account.closedOn
                ? `Closed ${formatDay(account.closedOn)}`
                : null,
            account.includeInNetWorth ? null : "Excluded from totals",
          ].filter(Boolean).join(" · ")}
        </td>
        <td className="account-card-evidence">
          {!account.openingBalanceConfirmedAt && (
            <div className="opening-evidence">
              <span>Opening balance not acknowledged</span>
              <button
                aria-label={`Acknowledge ${account.name}`}
                disabled={locked}
                onClick={() => void acknowledge(account)}
                type="button"
              >
                Acknowledge
              </button>
            </div>
          )}
        </td>
        <td className="account-card-actions">
          <div className="account-row-actions">
            <button
              aria-label={`Edit ${account.name}`}
              disabled={locked}
              onClick={() => openEdit(account)}
              type="button"
            >
              Edit
            </button>
            {account.accountClass === "asset" && account.isActive && (
              <button
                aria-label={
                  needsFirstValuation
                    ? `Add valuation for ${account.name}`
                    : `Valuation for ${account.name}`
                }
                disabled={locked}
                onClick={() => openValuation(account)}
                type="button"
              >
                {needsFirstValuation ? "Add valuation" : "Valuation"}
              </button>
            )}
            {account.isActive ? (
              <button
                aria-label={`Close ${account.name}`}
                disabled={locked}
                onClick={() => void closeAccount(account)}
                type="button"
              >
                Close
              </button>
            ) : (
              <button
                aria-label={`Reopen ${account.name}`}
                disabled={locked}
                onClick={() => void reopenAccount(account)}
                type="button"
              >
                Reopen
              </button>
            )}
          </div>
        </td>
      </tr>
    );
  }

  if (accounts.length === 0 && mode === "view") {
    return (
      <section className="accounts-card accounts-empty" aria-labelledby="accounts-title">
        <p className="eyebrow">Your money</p>
        <h1 className="workspace-title" id="accounts-title">Accounts</h1>
        <div className="accounts-empty-panel">
          <h2 id="accounts-empty-title">Add your first account</h2>
          <p>
            An account is a named store of money you own or owe. Start with its opening balance;
            closed accounts remain visible so their history is not lost.
          </p>
          <button className="primary-action" onClick={openAdd} type="button">Add an account</button>
        </div>
      </section>
    );
  }

  return (
    <section className="accounts-card" aria-labelledby="accounts-title">
      <div className="accounts-heading">
        <div>
          <h1 className="workspace-title" id="accounts-title">Accounts</h1>
          <p className="accounts-summary">
            {active.length} active {active.length === 1 ? "account" : "accounts"}
            {latestBalance ? ` · latest balance ${latestBalance}` : ""}
          </p>
        </div>
        {mode === "view" && (
          <button className="primary-action" onClick={openAdd} type="button">Add an account</button>
        )}
      </div>

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
          onClose={() => {
            setMode("view");
            setValuing(undefined);
          }}
          request={request}
        />
      )}

      {groups.map((key) => {
        const [accountClass, country] = key.split("\0") as ["asset" | "liability", string];
        const rows = active.filter((row) => row.accountClass === accountClass && row.country === country);
        const groupTotals = totals.filter((row) => row.accountClass === accountClass && row.country === country);
        const title = `${accountClass === "asset" ? "Assets" : "Liabilities"} · ${country}`;
        return (
          <section className="account-group" key={key}>
            <div className="account-group-head">
              <h2>{title}</h2>
              <p>
                {groupTotals.map((row, index) => (
                  <span key={`${row.currency}-${row.total}`}>
                    {index > 0 ? " · " : ""}
                    {moneyText(
                      row.accountClass === "liability" && row.total.startsWith("-")
                        ? unsigned(row.total)
                        : row.total,
                      row.currency,
                      currencies,
                    )}
                    {row.accountClass === "liability" && row.total.startsWith("-") ? " owed" : ""}
                    {asOfFor(rows, row.currency)}
                  </span>
                ))}
              </p>
            </div>
            <table className="account-table">
              <caption className="visually-hidden">{title}</caption>
              <AccountTableHead />
              <tbody className="account-card-grid">{rows.map(renderRow)}</tbody>
            </table>
          </section>
        );
      })}

      {closed.length > 0 && (
        <section className="account-group" aria-labelledby="closed-accounts-title">
          <div className="account-group-head">
            <h2 id="closed-accounts-title">Closed accounts</h2>
            <p>Kept for history and reconciliation</p>
          </div>
          <table className="account-table">
            <caption className="visually-hidden">Closed accounts</caption>
            <AccountTableHead />
            <tbody className="account-card-grid">{closed.map(renderRow)}</tbody>
          </table>
        </section>
      )}
    </section>
  );
}
