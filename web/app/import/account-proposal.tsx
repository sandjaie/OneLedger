"use client";

import { useEffect, useState } from "react";

import { ACCOUNT_TYPES, LIABILITY_TYPES } from "@/app/accounts/accounts-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type {
  AccountCreate,
  AccountProposal,
  AccountRow,
  CurrencyRow,
  InstitutionRow,
} from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  file: File;
  layoutId?: string;
  onResolved: (account: AccountRow) => void | Promise<void>;
  password?: string;
  proposal: AccountProposal;
  request: typeof apiFetch;
};

type Draft = {
  name: string;
  institutionId: string;
  accountType: string;
  accountSubtype: string;
  currency: string;
  country: string;
  openedOn: string;
  includeInNetWorth: boolean;
  isLiquid: boolean;
  openingBalance: string;
  openingBalanceDate: string;
  openingBalanceNote: string;
  creditLimit: string;
};

function initialDraft(proposal: AccountProposal): Draft {
  return {
    name: proposal.name,
    institutionId: proposal.institutionId ?? "",
    accountType: proposal.accountType ?? "",
    accountSubtype: "",
    currency: proposal.currency ?? "",
    country: proposal.country ?? "",
    openedOn: "",
    includeInNetWorth: true,
    isLiquid: true,
    openingBalance: proposal.openingBalance ?? "",
    openingBalanceDate: proposal.openingBalanceDate,
    openingBalanceNote: proposal.openingBalanceNote,
    creditLimit: proposal.creditLimit ?? "",
  };
}

function problemText(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.detail : fallback;
}

function statementBody(
  file: File,
  password: string | undefined,
  layoutId: string | undefined,
) {
  const form = new FormData();
  form.append("file", file);
  if (password) form.append("password", password);
  if (layoutId) form.append("layoutId", layoutId);
  return form;
}

function label(value: string) {
  return value.replaceAll("_", " ");
}

const MONEY = /^-?\d{1,16}(?:\.\d{1,4})?$/;

function moneyPreview(value: string, code: string, currencies: Array<CurrencyRow>) {
  if (!MONEY.test(value)) return undefined;
  const currency = currencies.find((row) => row.code === code);
  return currency ? formatMoney(value, currency) : undefined;
}

function isZero(value: string) {
  return /^-?0+(?:\.0{1,4})?$/.test(value);
}

function accountContractAllows(draft: Draft) {
  if (
    !draft.name.trim()
    || draft.name.trim().length > 120
    || !ACCOUNT_TYPES.some((type) => type === draft.accountType)
    || draft.accountSubtype.length > 40
    || draft.currency.length !== 3
    || draft.country.trim().length !== 2
    || !MONEY.test(draft.openingBalance)
    || !draft.openingBalanceDate
  ) return false;

  if (
    LIABILITY_TYPES.has(draft.accountType)
    && !draft.openingBalance.startsWith("-")
    && !isZero(draft.openingBalance)
  ) return false;

  if (draft.accountType === "credit_card") {
    return MONEY.test(draft.creditLimit)
      && !draft.creditLimit.startsWith("-")
      && !isZero(draft.creditLimit);
  }
  return draft.creditLimit === "";
}

export function AccountProposalPanel({
  currencies,
  file,
  layoutId,
  onResolved,
  password,
  proposal,
  request,
}: Props) {
  const [draft, setDraft] = useState(() => initialDraft(proposal));
  const [institutions, setInstitutions] = useState<Array<InstitutionRow>>([]);
  const [loadingInstitutions, setLoadingInstitutions] = useState(true);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<Array<InstitutionRow>>("/institutions")
      .then((rows) => { if (current) setInstitutions(rows); })
      .catch(() => {
        if (current) setProblem("Institutions could not be loaded; you can continue without one.");
      })
      .finally(() => { if (current) setLoadingInstitutions(false); });
    return () => { current = false; };
  }, [request]);

  function change<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function confirm(form: FormData) {
    setBusy(true);
    setProblem(undefined);
    try {
      const account = await request<AccountRow>("/parse/account", {
        method: "POST",
        body: form,
      });
      await onResolved(account);
    } catch (error) {
      setProblem(problemText(error, "The account could not be confirmed from this statement."));
    } finally {
      setBusy(false);
    }
  }

  function createAccount() {
    const account: AccountCreate = {
      name: draft.name.trim(),
      institutionId: draft.institutionId || null,
      accountType: draft.accountType,
      accountSubtype: draft.accountSubtype.trim() || null,
      currency: draft.currency,
      country: draft.country.trim(),
      openedOn: draft.openedOn || null,
      includeInNetWorth: draft.includeInNetWorth,
      isLiquid: draft.isLiquid,
      openingBalance: draft.openingBalance,
      openingBalanceDate: draft.openingBalanceDate,
      openingBalanceNote: draft.openingBalanceNote.trim() || null,
      creditLimit: draft.creditLimit || null,
    };
    const form = statementBody(file, password, layoutId);
    form.append("account", JSON.stringify(account));
    void confirm(form);
  }

  function adopt(accountId: string) {
    const form = statementBody(file, password, layoutId);
    form.append("accountId", accountId);
    void confirm(form);
  }

  const ready = accountContractAllows(draft);
  const openingPreview = moneyPreview(draft.openingBalance, draft.currency, currencies);
  const limitPreview = moneyPreview(draft.creditLimit, draft.currency, currencies);

  return (
    <section className="account-proposal" aria-labelledby={`account-proposal-${proposal.accountNumberMasked}`}>
      <header>
        <p className="eyebrow">Account found in statement</p>
        <h3 id={`account-proposal-${proposal.accountNumberMasked}`}>{proposal.accountNumberMasked}</h3>
        <p>Confirm the statement’s details, or choose an existing account below.</p>
      </header>

      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      {proposal.suggestedAccounts.length > 0 && (
        <div className="account-proposal-suggestions" aria-label="Suggested existing accounts">
          {proposal.suggestedAccounts.map((account) => (
            <button
              className="text-action"
              disabled={busy}
              key={account.id}
              onClick={() => adopt(account.id)}
              type="button"
            >
              This is {account.name} {account.accountNumberMasked}
            </button>
          ))}
        </div>
      )}

      <div className="account-proposal-fields">
        <label>
          Name (required)
          <input required value={draft.name} onChange={(event) => change("name", event.target.value)} />
        </label>
        <label>
          Institution
          <select
            disabled={loadingInstitutions}
            value={draft.institutionId}
            onChange={(event) => {
              const institution = institutions.find((row) => row.id === event.target.value);
              change("institutionId", event.target.value);
              if (institution) change("country", institution.country);
            }}
          >
            <option value="">None stated</option>
            {institutions.map((institution) => (
              <option key={institution.id} value={institution.id}>
                {institution.shortName ?? institution.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Account type (required)
          <select required value={draft.accountType} onChange={(event) => change("accountType", event.target.value)}>
            <option value="">Choose a type…</option>
            {ACCOUNT_TYPES.map((type) => <option key={type} value={type}>{label(type)}</option>)}
          </select>
        </label>
        <label>
          Account subtype
          <input value={draft.accountSubtype} onChange={(event) => change("accountSubtype", event.target.value)} />
        </label>
        <label>
          Currency (required)
          <select required value={draft.currency} onChange={(event) => change("currency", event.target.value)}>
            <option value="">Choose a currency…</option>
            {currencies.map((currency) => (
              <option key={currency.code} value={currency.code}>{currency.name}</option>
            ))}
          </select>
        </label>
        <label>
          Country (required)
          <input required value={draft.country} onChange={(event) => change("country", event.target.value)} />
        </label>
        <label>
          Opening balance (required)
          <input required inputMode="decimal" value={draft.openingBalance} onChange={(event) => change("openingBalance", event.target.value)} />
          {openingPreview && <small>{openingPreview}</small>}
        </label>
        <label>
          Opening balance date (required)
          <input required type="date" value={draft.openingBalanceDate} onChange={(event) => change("openingBalanceDate", event.target.value)} />
        </label>
        <label className="account-proposal-wide">
          Opening balance note
          <input value={draft.openingBalanceNote} onChange={(event) => change("openingBalanceNote", event.target.value)} />
        </label>
        <label>
          Credit limit
          <input inputMode="decimal" value={draft.creditLimit} onChange={(event) => change("creditLimit", event.target.value)} />
          {limitPreview && <small>{limitPreview}</small>}
        </label>
        <label>
          Opened on
          <input type="date" value={draft.openedOn} onChange={(event) => change("openedOn", event.target.value)} />
        </label>
        <label className="check-field">
          <input checked={draft.includeInNetWorth} onChange={(event) => change("includeInNetWorth", event.target.checked)} type="checkbox" />
          Include in net worth
        </label>
        <label className="check-field">
          <input checked={draft.isLiquid} onChange={(event) => change("isLiquid", event.target.checked)} type="checkbox" />
          Liquid account
        </label>
      </div>

      {proposal.openingBalanceIsDerived && (
        <p className="account-proposal-note">
          The opening balance was derived from the first row rather than stated by the document.
        </p>
      )}

      <button
        className="primary-action"
        disabled={busy || !ready}
        onClick={createAccount}
        type="button"
      >
        {busy ? "Confirming account…" : "Create account and continue"}
      </button>
    </section>
  );
}
