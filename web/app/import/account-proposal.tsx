"use client";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { useEffect, useId, useRef, useState } from "react";

import { EyeIcon } from "@/app/accounts/eye-icon";
import { ACCOUNT_TYPES, INSTITUTION_TYPES, LIABILITY_TYPES } from "@/lib/accounts/types";
import { LONG_REQUEST_TIMEOUT_MS, type apiFetch, errorText } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type {
  AccountCreate,
  AccountRow,
  CurrencyRow,
  IdentityResolution,
  InstitutionCreate,
  InstitutionRow,
  RevealedStatementIdentifier,
  UnavailableStatementIdentifier,
} from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  file: File;
  layoutId?: string;
  onBusyChange: (busy: boolean) => void;
  onResolved: (account: AccountRow) => void | Promise<void>;
  password?: string;
  resolution: IdentityResolution;
  request: typeof apiFetch;
  screenBusy: boolean;
};

type Draft = {
  name: string;
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

function initialDraft(resolution: IdentityResolution): Draft {
  return {
    name: resolution.name,
    accountType: resolution.accountType,
    accountSubtype: "",
    currency: resolution.currency ?? "",
    country: resolution.country ?? "",
    openedOn: "",
    includeInNetWorth: true,
    isLiquid: true,
    openingBalance: resolution.openingBalance ?? "",
    openingBalanceDate: resolution.openingBalanceDate,
    openingBalanceNote: resolution.openingBalanceNote,
    creditLimit: resolution.creditLimit ?? "",
  };
}

const NEW_INSTITUTION = "new";

function initialInstitutionChoice(resolution: IdentityResolution) {
  if (resolution.institutionAction === "create") return NEW_INSTITUTION;
  if (resolution.institutionAction === "choose") return "";
  return resolution.institution.candidates.at(0)?.id ?? "";
}

function initialInstitutionDraft(resolution: IdentityResolution): InstitutionCreate {
  return {
    name: resolution.institution.proposedName ?? "",
    shortName: null,
    country: resolution.institution.country ?? "",
    institutionType: "bank",
  };
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

function decisionHeading(resolution: IdentityResolution, institutionChoice: string) {
  if (resolution.proposalGroupKey === null) return "Account identity needs review";
  if (resolution.accountAction === "enrich") return "Confirm this account identity";
  if (resolution.accountAction === "choose" && resolution.candidates.length > 0) {
    return "Choose the matching account";
  }
  if (resolution.institutionAction === "choose" && !institutionChoice) {
    return "Choose an institution for this new account";
  }
  if (institutionChoice === NEW_INSTITUTION) return "New institution and account";
  return "New account at an existing institution";
}

const CONFLICT_TEXT: Record<IdentityResolution["conflicts"][number], string> = {
  institution: "Institution does not match the stored account.",
  account_type: "Account type does not match the stored account.",
  currency: "Currency does not match the stored account.",
};

export function AccountProposalPanel({
  currencies,
  file,
  layoutId,
  onBusyChange,
  onResolved,
  password,
  resolution,
  request,
  screenBusy,
}: Props) {
  const headingId = useId();
  const [draft, setDraft] = useState(() => initialDraft(resolution));
  const [institutionChoice, setInstitutionChoice] = useState(() =>
    initialInstitutionChoice(resolution));
  const [institutionDraft, setInstitutionDraft] = useState(() =>
    initialInstitutionDraft(resolution));
  const [institutions, setInstitutions] = useState<Array<InstitutionRow>>([]);
  const [loadingInstitutions, setLoadingInstitutions] = useState(
    resolution.institution.status === "needs_confirmation",
  );
  const [busy, setBusy] = useState(false);
  const [revealBusy, setRevealBusy] = useState(false);
  const [revealed, setRevealed] = useState<string>();
  const [revealNote, setRevealNote] = useState<string>();
  const [sourceMaskOnly, setSourceMaskOnly] = useState(false);
  const [problem, setProblem] = useState<string>();
  const revealController = useRef<AbortController | undefined>(undefined);

  useEffect(() => {
    if (resolution.institution.status !== "needs_confirmation") {
      return;
    }
    let current = true;
    request<Array<InstitutionRow>>("/institutions")
      .then((rows) => { if (current) setInstitutions(rows); })
      .catch(() => {
        if (current) {
          setProblem("Institutions could not be loaded. You can still create the one read from this PDF.");
        }
      })
      .finally(() => { if (current) setLoadingInstitutions(false); });
    return () => { current = false; };
  }, [request, resolution.institution.status]);

  useEffect(() => () => {
    revealController.current?.abort();
    revealController.current = undefined;
  }, []);

  function change<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function confirm(form: FormData) {
    setBusy(true);
    onBusyChange(true);
    setProblem(undefined);
    try {
      const account = await request<AccountRow>("/parse/account", {
        method: "POST",
        body: form,
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      await onResolved(account);
    } catch (error) {
      setProblem(errorText(error, "The account could not be confirmed from this statement."));
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }

  function createAccount() {
    const account: AccountCreate = {
      name: draft.name.trim(),
      institutionId: institutionChoice === NEW_INSTITUTION ? null : institutionChoice,
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
    if (institutionChoice === NEW_INSTITUTION) {
      form.append("institution", JSON.stringify({
        ...institutionDraft,
        name: institutionDraft.name.trim(),
        shortName: institutionDraft.shortName?.trim() || null,
        country: institutionDraft.country.trim().toUpperCase(),
      } satisfies InstitutionCreate));
    }
    void confirm(form);
  }

  function adopt(accountId: string) {
    const form = statementBody(file, password, layoutId);
    form.append("accountId", accountId);
    void confirm(form);
  }

  async function toggleReveal() {
    if (revealed) {
      setRevealed(undefined);
      setRevealNote(undefined);
      return;
    }
    setRevealBusy(true);
    setRevealNote(undefined);
    const controller = new AbortController();
    revealController.current = controller;
    try {
      const result = await request<
        RevealedStatementIdentifier | UnavailableStatementIdentifier
      >("/parse/identifier/reveal", {
        method: "POST",
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
        body: statementBody(file, password, layoutId),
        signal: controller.signal,
      });
      if (controller.signal.aborted || revealController.current !== controller) return;
      if (result.status === "revealed") {
        setRevealed(result.value);
      } else {
        setRevealed(undefined);
        setSourceMaskOnly(true);
        setRevealNote("Statement supplied only a masked value.");
      }
    } catch (error) {
      if (controller.signal.aborted || revealController.current !== controller) return;
      setRevealed(undefined);
      setRevealNote(errorText(error, "The account number could not be revealed."));
    } finally {
      if (revealController.current === controller) {
        revealController.current = undefined;
        setRevealBusy(false);
      }
    }
  }

  const createsInstitution = institutionChoice === NEW_INSTITUTION;
  const institutionReady = createsInstitution
    ? institutionDraft.name.trim().length > 0
      && institutionDraft.name.trim().length <= 120
      && (institutionDraft.shortName?.trim().length ?? 0) <= 40
      && institutionDraft.country.trim().length === 2
    : institutionChoice.length > 0;
  const ready = accountContractAllows(draft) && institutionReady;
  const interactionBusy = busy || screenBusy;
  const openingPreview = moneyPreview(draft.openingBalance, draft.currency, currencies);
  const limitPreview = moneyPreview(draft.creditLimit, draft.currency, currencies);
  const hasSingularIdentity = resolution.proposalGroupKey !== null;
  const canCreate = hasSingularIdentity && resolution.conflicts.length === 0
    && (resolution.accountAction === "create"
      || (resolution.accountAction === "choose" && resolution.candidates.length === 0));
  const canChooseAccount = hasSingularIdentity && resolution.conflicts.length === 0
    && resolution.candidates.length > 0;
  const institutionOptions = resolution.institution.status === "needs_confirmation"
    ? institutions
    : resolution.institution.candidates;

  return (
    <section className="account-proposal" aria-labelledby={headingId}>
      <header>
        <p className="eyebrow">Identity decision</p>
        <h3 id={headingId}>{decisionHeading(resolution, institutionChoice)}</h3>
        <p>
          OneLedger read the institution and account identity from the PDF. Confirm what it should
          create or reuse before any statement can be staged.
        </p>
      </header>

      <div className="account-proposal-identity">
        <span>
          <small>Account number</small>
          <strong>{revealed ?? resolution.accountNumberMasked}</strong>
        </span>
        <Button
          aria-describedby={revealNote ? `${headingId}-reveal-note` : undefined}
          aria-label={sourceMaskOnly
            ? "Full account number unavailable from statement"
            : `${revealed ? "Hide" : "Show"} account number from statement`}
          className="account-identifier-reveal"
          disabled={revealBusy || sourceMaskOnly || !hasSingularIdentity || interactionBusy}
          onClick={() => void toggleReveal()}
          type="button"
        >
          <EyeIcon hidden={Boolean(revealed)} />
        </Button>
      </div>

      {revealNote && (
        <p className="account-proposal-note" id={`${headingId}-reveal-note`}>
          {revealNote}
        </p>
      )}

      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      {!hasSingularIdentity && (
        <p className="inline-alert">
          OneLedger found conflicting account-number captures in this PDF. Review its layout before
          continuing.
        </p>
      )}

      {resolution.conflicts.length > 0 && (
        <ul className="account-proposal-conflicts" aria-label="Identity conflicts">
          {resolution.conflicts.map((conflict) => (
            <li key={conflict}>{CONFLICT_TEXT[conflict]}</li>
          ))}
        </ul>
      )}

      {canChooseAccount && (
        <div className="account-proposal-suggestions" aria-label="Suggested existing accounts">
          {resolution.candidates.map((account) => (
            <Button
              variant="outline"
              disabled={interactionBusy}
              key={account.id}
              onClick={() => adopt(account.id)}
              type="button"
            >
              Use {account.name} {account.accountNumberMasked}
            </Button>
          ))}
        </div>
      )}

      {canCreate && <div className="account-proposal-fields">
        <label>
          Name (required)
          <Input required value={draft.name} onChange={(event) => change("name", event.target.value)} />
        </label>
        <label>
          Institution (required)
          <NativeSelect
            disabled={loadingInstitutions && resolution.institution.status === "needs_confirmation"}
            required
            value={institutionChoice}
            onChange={(event) => {
              const choice = event.target.value;
              const institution = institutionOptions.find((row) => row.id === choice);
              setInstitutionChoice(choice);
              if (choice === NEW_INSTITUTION) {
                change("country", institutionDraft.country);
              } else if (institution) {
                change("country", institution.country);
              }
            }}
          >
            {resolution.institution.status === "ambiguous" && (
              <option value="">Choose an institution…</option>
            )}
            {resolution.institution.status === "needs_confirmation" && (
              <option value={NEW_INSTITUTION}>Create a new institution</option>
            )}
            {institutionOptions.map((institution) => (
              <option key={institution.id} value={institution.id}>
                {institution.shortName ?? institution.name}
              </option>
            ))}
          </NativeSelect>
        </label>
        {createsInstitution && (
          <fieldset className="account-proposal-institution account-proposal-wide">
            <legend>New institution</legend>
            <label>
              Institution name (required)
              <Input
                required
                value={institutionDraft.name}
                onChange={(event) => setInstitutionDraft((current) => ({
                  ...current,
                  name: event.target.value,
                }))}
              />
            </label>
            <label>
              Institution short name
              <Input
                maxLength={40}
                value={institutionDraft.shortName ?? ""}
                onChange={(event) => setInstitutionDraft((current) => ({
                  ...current,
                  shortName: event.target.value,
                }))}
              />
            </label>
            <label>
              Institution country (required)
              <Input
                maxLength={2}
                required
                value={institutionDraft.country}
                onChange={(event) => {
                  const country = event.target.value.toUpperCase();
                  setInstitutionDraft((current) => ({ ...current, country }));
                  change("country", country);
                }}
              />
            </label>
            <label>
              Institution type (required)
              <NativeSelect
                required
                value={institutionDraft.institutionType}
                onChange={(event) => setInstitutionDraft((current) => ({
                  ...current,
                  institutionType: event.target.value,
                }))}
              >
                {INSTITUTION_TYPES.map((type) => (
                  <option key={type} value={type}>{label(type)}</option>
                ))}
              </NativeSelect>
            </label>
          </fieldset>
        )}
        <label>
          Account type (required)
          <NativeSelect required value={draft.accountType} onChange={(event) => change("accountType", event.target.value)}>
            <option value="">Choose a type…</option>
            {ACCOUNT_TYPES.map((type) => <option key={type} value={type}>{label(type)}</option>)}
          </NativeSelect>
        </label>
        <label>
          Account subtype
          <Input value={draft.accountSubtype} onChange={(event) => change("accountSubtype", event.target.value)} />
        </label>
        <label>
          Currency (required)
          <NativeSelect required value={draft.currency} onChange={(event) => change("currency", event.target.value)}>
            <option value="">Choose a currency…</option>
            {currencies.map((currency) => (
              <option key={currency.code} value={currency.code}>{currency.name}</option>
            ))}
          </NativeSelect>
        </label>
        <label>
          Country (required)
          <Input required value={draft.country} onChange={(event) => change("country", event.target.value)} />
        </label>
        <label>
          Opening balance (required)
          <Input required inputMode="decimal" value={draft.openingBalance} onChange={(event) => change("openingBalance", event.target.value)} />
          {openingPreview && <small>{openingPreview}</small>}
        </label>
        <label>
          Opening balance date (required)
          <Input required type="date" value={draft.openingBalanceDate} onChange={(event) => change("openingBalanceDate", event.target.value)} />
        </label>
        <label className="account-proposal-wide">
          Opening balance note
          <Input value={draft.openingBalanceNote} onChange={(event) => change("openingBalanceNote", event.target.value)} />
        </label>
        <label>
          Credit limit
          <Input inputMode="decimal" value={draft.creditLimit} onChange={(event) => change("creditLimit", event.target.value)} />
          {limitPreview && <small>{limitPreview}</small>}
        </label>
        <label>
          Opened on
          <Input type="date" value={draft.openedOn} onChange={(event) => change("openedOn", event.target.value)} />
        </label>
        <label className="check-field">
          <Checkbox checked={draft.includeInNetWorth} onCheckedChange={(checked) => change("includeInNetWorth", checked === true)} />
          Include in net worth
        </label>
        <label className="check-field">
          <Checkbox checked={draft.isLiquid} onCheckedChange={(checked) => change("isLiquid", checked === true)} />
          Liquid account
        </label>
      </div>}

      {resolution.openingBalanceIsDerived && (
        <p className="account-proposal-note">
          The opening balance was derived from the first row rather than stated by the document.
        </p>
      )}

      {canCreate && (
        <Button
          variant="default"
          disabled={interactionBusy || !ready}
          onClick={createAccount}
          type="button"
        >
          {busy ? "Confirming account…" : "Create account and continue"}
        </Button>
      )}
    </section>
  );
}
