"use client";

import { useState } from "react";

import { ApiError, type apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type {
  AccountCreate,
  AccountGroupTotal,
  AccountRow,
  AccountUpdate,
  CurrencyRow,
  InstitutionCreate,
  InstitutionRow,
} from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  totals: Array<AccountGroupTotal>;
  institutions: Array<InstitutionRow>;
  currencies: Array<CurrencyRow>;
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
  accountNumberMasked: string;
  openedOn: string;
  includeInNetWorth: boolean;
  isLiquid: boolean;
  openingBalance: string;
  openingBalanceDate: string;
  openingBalanceNote: string;
  creditLimit: string;
};

const INSTITUTION_TYPES = [
  "bank",
  "card_issuer",
  "broker",
  "fund_house",
  "lender",
  "govt",
  "insurer",
  "employer",
  "exchange_house",
  "other",
] as const;

const ACCOUNT_TYPES = [
  "cash",
  "current",
  "savings",
  "salary",
  "fixed_deposit",
  "recurring_deposit",
  "credit_card",
  "personal_loan",
  "home_loan",
  "auto_loan",
  "education_loan",
  "gold_loan",
  "loan_other",
  "brokerage",
  "mutual_fund_folio",
  "retirement",
  "property",
  "precious_metal",
  "crypto",
  "other_asset",
  "receivable",
  "payable",
] as const;

const LIABILITY_TYPES = new Set([
  "credit_card",
  "personal_loan",
  "home_loan",
  "auto_loan",
  "education_loan",
  "gold_loan",
  "loan_other",
  "payable",
]);

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

function emptyDraft(currencies: Array<CurrencyRow>, institutions: Array<InstitutionRow>): Draft {
  return {
    name: "",
    institutionId: "",
    accountType: "savings",
    accountSubtype: "",
    currency: currencies[0]?.code ?? "",
    country: institutions[0]?.country ?? "",
    accountNumberMasked: "",
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
    accountNumberMasked: account.accountNumberMasked ?? "",
    openedOn: account.openedOn ?? "",
    includeInNetWorth: account.includeInNetWorth,
    isLiquid: account.isLiquid,
    openingBalance: LIABILITY_TYPES.has(account.accountType)
      ? unsigned(account.openingBalance)
      : account.openingBalance,
    openingBalanceDate: account.openingBalanceDate,
    openingBalanceNote: account.openingBalanceNote ?? "",
    creditLimit: account.creditLimit ?? "",
  };
}

function formatDay(isoDate: string) {
  const [year, month, day] = isoDate.split("-");
  return `${day.replace(/^0/, "")} ${MONTHS[month]} ${year}`;
}

function currencyOf(currencies: Array<CurrencyRow>, code: string): CurrencyRow {
  return (
    currencies.find((row) => row.code === code) ?? {
      code,
      name: code,
      symbol: code,
      decimals: 2,
      numberFormat: "international",
    }
  );
}

function unsigned(value: string) {
  return value.startsWith("-") ? value.slice(1) : value;
}

function signedOpening(accountType: string, value: string) {
  if (!LIABILITY_TYPES.has(accountType) || value === "" || value.startsWith("-")) {
    return value;
  }
  return `-${value}`;
}

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
    accountNumberMasked: draft.accountNumberMasked || null,
    openedOn: draft.openedOn || null,
    includeInNetWorth: draft.includeInNetWorth,
    isLiquid: draft.isLiquid,
    openingBalance: signedOpening(draft.accountType, draft.openingBalance),
    openingBalanceDate: draft.openingBalanceDate,
    openingBalanceNote: draft.openingBalanceNote || null,
    creditLimit: draft.creditLimit || null,
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
    accountNumberMasked: draft.accountNumberMasked || null,
    openedOn: draft.openedOn || null,
    closedOn,
    includeInNetWorth: draft.includeInNetWorth,
    isLiquid: draft.isLiquid,
    openingBalance: signedOpening(draft.accountType, draft.openingBalance),
    openingBalanceDate: draft.openingBalanceDate,
    openingBalanceNote: draft.openingBalanceNote || null,
    creditLimit: draft.creditLimit || null,
    acknowledgeOpeningBalance: acknowledge,
  };
}

function isAuthError(error: unknown) {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
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
  request,
  onChanged,
}: Props) {
  const [mode, setMode] = useState<"view" | "add" | "edit">("view");
  const [editing, setEditing] = useState<AccountRow>();
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

  const active = accounts.filter((row) => row.isActive);
  const closed = accounts.filter((row) => !row.isActive);
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
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(error instanceof ApiError ? error.detail : "The account could not be created. Try again.");
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
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(error instanceof ApiError ? error.detail : "The account could not be saved. Try again.");
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
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(error instanceof ApiError ? error.detail : "The opening balance could not be acknowledged.");
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
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(error instanceof ApiError ? error.detail : "The account could not be closed.");
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
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(error instanceof ApiError ? error.detail : "The account could not be reopened.");
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
      onChanged();
    } catch (error) {
      if (isAuthError(error)) return;
      setProblem(error instanceof ApiError ? error.detail : "The institution could not be created.");
    } finally {
      setBusy(false);
    }
  }

  function balanceLabel(account: AccountRow) {
    const currency = currencyOf(currencies, account.currency);
    if (account.accountClass === "liability") {
      return `You owe ${formatMoney(unsigned(account.currentBalance), currency)}`;
    }
    return formatMoney(account.currentBalance, currency);
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
        <h3>{creating ? "New account" : `Edit ${editing?.name ?? "account"}`}</h3>
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
            <button disabled={busy} onClick={() => void saveInstitution()} type="button">Save institution</button>
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
        <label className="text-field">
          <span>Masked number</span>
          <input
            maxLength={32}
            onChange={(event) => field("accountNumberMasked", event.target.value)}
            value={draft.accountNumberMasked}
          />
        </label>
        <label className="date-field">
          <span>Opened on</span>
          <input onChange={(event) => field("openedOn", event.target.value)} type="date" value={draft.openedOn} />
        </label>
        <label className="text-field">
          <span>{LIABILITY_TYPES.has(draft.accountType) ? "Amount owed" : "Opening balance"}</span>
          <input
            inputMode="decimal"
            onChange={(event) => field("openingBalance", event.target.value)}
            required
            type="text"
            value={draft.openingBalance}
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
          <button className="primary-action" disabled={busy} type="submit">
            {creating ? "Create account" : "Save account"}
          </button>
          <button onClick={() => { setMode("view"); setEditing(undefined); }} type="button">Cancel</button>
        </div>
      </form>
    );
  }

  function renderRow(account: AccountRow) {
    const meta = [account.institutionName, account.accountSubtype, account.currency].filter(Boolean).join(" · ");
    return (
      <tr key={account.id}>
        <td className="account-mark">{markFor(account, institutions)}</td>
        <td>{account.name}</td>
        <td>{meta}</td>
        <td>{account.accountNumberMasked}</td>
        <td
          aria-label={`${account.name} balance ${balanceLabel(account)}`}
          className={account.accountClass === "liability" ? "account-owed" : undefined}
        >
          {balanceLabel(account)}
        </td>
        <td>
          {account.isActive
            ? account.currentBalanceAsOf
              ? `Updated ${formatDay(account.currentBalanceAsOf.slice(0, 10))}`
              : null
            : account.closedOn
              ? `Closed ${formatDay(account.closedOn)}`
              : null}
        </td>
        <td>
          {!account.openingBalanceConfirmedAt && (
            <div className="opening-evidence">
              <span>Opening balance not acknowledged</span>
              <button disabled={busy} onClick={() => void acknowledge(account)} type="button">
                Acknowledge
              </button>
            </div>
          )}
        </td>
        <td>
          <div className="account-row-actions">
            <button onClick={() => openEdit(account)} type="button">Edit {account.name}</button>
            {account.isActive ? (
              <button disabled={busy} onClick={() => void closeAccount(account)} type="button">
                Close account
              </button>
            ) : (
              <button disabled={busy} onClick={() => void reopenAccount(account)} type="button">
                Reopen account
              </button>
            )}
          </div>
        </td>
      </tr>
    );
  }

  if (accounts.length === 0 && mode === "view") {
    return (
      <section className="accounts-card" aria-labelledby="accounts-empty-title">
        <p className="section-label">Accounts</p>
        <h2 className="workspace-title" id="accounts-empty-title">Add your first account</h2>
        <p className="lede">
          An account here is a named store of money you own or owe, with its opening balance stated
          as evidence. Closed accounts stay visible so history is not deleted.
        </p>
        <button className="primary-action" onClick={openAdd} type="button">Add an account</button>
      </section>
    );
  }

  return (
    <section className="accounts-card" aria-labelledby="accounts-title">
      <div className="accounts-heading">
        <div>
          <p className="section-label">Accounts</p>
          <h2 className="workspace-title" id="accounts-title">Account inventory</h2>
          <p className="lede">
            Native balances, grouped by class then country. An unacknowledged opening balance is
            stated, not verified.
          </p>
        </div>
        {mode === "view" && (
          <button className="primary-action" onClick={openAdd} type="button">Add an account</button>
        )}
      </div>

      {problem && mode === "view" && <p className="inline-alert" role="alert">{problem}</p>}
      {mode !== "view" && renderForm()}

      {groups.map((key) => {
        const [accountClass, country] = key.split("\0") as ["asset" | "liability", string];
        const rows = active.filter((row) => row.accountClass === accountClass && row.country === country);
        const groupTotals = totals.filter((row) => row.accountClass === accountClass && row.country === country);
        const title = `${accountClass === "asset" ? "Assets" : "Liabilities"} · ${country}`;
        return (
          <section className="account-group" key={key}>
            <div className="account-group-head">
              <h3>{title}</h3>
              <p>
                {groupTotals.map((row, index) => (
                  <span key={`${row.currency}-${row.total}`}>
                    {index > 0 ? " · " : ""}
                    {formatMoney(row.total.startsWith("-") ? unsigned(row.total) : row.total, currencyOf(currencies, row.currency))}
                    {row.accountClass === "liability" ? " owed" : ""}
                  </span>
                ))}
              </p>
            </div>
            <table className="account-table">
              <caption className="visually-hidden">{title}</caption>
              <tbody>{rows.map(renderRow)}</tbody>
            </table>
          </section>
        );
      })}

      {closed.length > 0 && (
        <section className="account-group">
          <table className="account-table">
            <caption className="visually-hidden">Closed accounts</caption>
            <tbody>{closed.map(renderRow)}</tbody>
          </table>
        </section>
      )}
    </section>
  );
}
