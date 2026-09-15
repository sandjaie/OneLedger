"use client";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type { AccountRow, CurrencyRow, RegisterResponse, RegisterRow } from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type RegisterKind = RegisterRow["kind"];
type PremiumCadence = "monthly" | "annual";

const KIND_GROUPS: Array<{ kind: RegisterKind; label: string }> = [
  { kind: "insurance_policy", label: "Insurance policies" },
  { kind: "deposit", label: "Deposits" },
  { kind: "claim", label: "Claims" },
  { kind: "nominee", label: "Nominee records" },
  { kind: "administration", label: "Administration deadlines" },
];

const DEPOSIT_ACCOUNT_TYPES = new Set(["fixed_deposit", "recurring_deposit"]);

type FormState = {
  title: string;
  counterparty: string;
  covered: string;
  amount: string;
  currency: string;
  premiumAmount: string;
  premiumCadence: PremiumCadence;
  keyDate: string;
  accountId: string;
  instruction: string;
  nomineeRecorded: boolean;
};

function emptyForm(currencies: Array<CurrencyRow>, accounts: Array<AccountRow>): FormState {
  return {
    title: "",
    counterparty: "",
    covered: "",
    amount: "",
    currency: currencies[0]?.code ?? "",
    premiumAmount: "",
    premiumCadence: "annual",
    keyDate: "",
    accountId: accounts[0]?.id ?? "",
    instruction: "",
    nomineeRecorded: true,
  };
}

function formFromRecord(record: RegisterRow): FormState {
  return {
    title: record.title,
    counterparty: record.counterparty ?? "",
    covered: record.covered ?? "",
    amount: record.amount ?? "",
    currency: record.currency ?? "",
    premiumAmount: record.premiumAmount ?? "",
    premiumCadence: (record.premiumCadence as PremiumCadence | null) ?? "annual",
    keyDate: record.keyDate ?? "",
    accountId: record.accountId ?? "",
    instruction: record.instruction ?? "",
    nomineeRecorded: record.nomineeRecorded ?? true,
  };
}

// Only the fields a kind uses travel in the request; every other field is sent null so a kind
// never carries another kind's data (spec "a field its kind forbids is 422, before the database").
function fieldsForKind(kind: RegisterKind, form: FormState): Record<string, unknown> {
  const hasCurrency = Boolean(form.amount || form.premiumAmount);
  switch (kind) {
    case "insurance_policy":
      return {
        title: form.title,
        counterparty: form.counterparty || null,
        covered: form.covered || null,
        amount: form.amount || null,
        currency: hasCurrency ? form.currency : null,
        premiumAmount: form.premiumAmount || null,
        premiumCadence: form.premiumAmount ? form.premiumCadence : null,
        keyDate: form.keyDate,
        accountId: null,
        instruction: null,
        nomineeRecorded: null,
      };
    case "deposit":
      return {
        title: form.title,
        counterparty: form.counterparty || null,
        covered: null,
        amount: form.amount || null,
        currency: form.amount ? form.currency : null,
        premiumAmount: null,
        premiumCadence: null,
        keyDate: form.keyDate,
        accountId: form.accountId,
        instruction: form.instruction || null,
        nomineeRecorded: null,
      };
    case "claim":
      return {
        title: form.title,
        counterparty: form.counterparty || null,
        covered: null,
        amount: form.amount || null,
        currency: form.amount ? form.currency : null,
        premiumAmount: null,
        premiumCadence: null,
        keyDate: form.keyDate,
        accountId: null,
        instruction: null,
        nomineeRecorded: null,
      };
    case "nominee":
      return {
        title: form.title,
        counterparty: null,
        covered: null,
        amount: null,
        currency: null,
        premiumAmount: null,
        premiumCadence: null,
        keyDate: null,
        accountId: form.accountId,
        instruction: null,
        nomineeRecorded: form.nomineeRecorded,
      };
    case "administration":
      return {
        title: form.title,
        counterparty: null,
        covered: null,
        amount: null,
        currency: null,
        premiumAmount: null,
        premiumCadence: null,
        keyDate: form.keyDate,
        accountId: null,
        instruction: null,
        nomineeRecorded: null,
      };
  }
}

function completeForm(kind: RegisterKind, form: FormState): boolean {
  if (!form.title) return false;
  switch (kind) {
    case "insurance_policy":
      return Boolean(form.keyDate);
    case "deposit":
      return Boolean(form.keyDate && form.accountId);
    case "claim":
      return Boolean(form.keyDate && form.counterparty && form.amount);
    case "nominee":
      return Boolean(form.accountId);
    case "administration":
      return Boolean(form.keyDate);
  }
}

function recordSummary(record: RegisterRow, currencies: Array<CurrencyRow>): string {
  const parts: Array<string> = [record.title];
  if (record.counterparty) parts.push(record.counterparty);
  if (record.covered) parts.push(record.covered);
  if (record.accountName) parts.push(record.accountName);
  if (record.kind === "insurance_policy" && record.keyDate) parts.push(`Renews ${record.keyDate}`);
  if (record.kind === "deposit" && record.keyDate) parts.push(`Matures ${record.keyDate}`);
  if (record.kind === "claim" && record.keyDate) parts.push(`Submitted ${record.keyDate}`);
  if (record.kind === "administration" && record.keyDate) parts.push(`Due ${record.keyDate}`);
  if (record.amount && record.currency) parts.push(moneyText(record.amount, record.currency, currencies));
  if (record.premiumAmount && record.currency) {
    parts.push(`Premium ${moneyText(record.premiumAmount, record.currency, currencies)} ${record.premiumCadence}`);
  }
  if (record.kind === "nominee") parts.push(record.nomineeRecorded ? "Nominee recorded" : "No nominee recorded");
  if (record.completedAt) parts.push("Completed");
  if (record.lastReviewedOn) parts.push(`Reviewed ${record.lastReviewedOn}`);
  if (record.reviewDue) parts.push("Review due");
  if (record.archivedAt) parts.push("Archived");
  return parts.join(" · ");
}

export function Register({ accounts, currencies, request }: Props) {
  const [data, setData] = useState<RegisterResponse>();
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [creatingKind, setCreatingKind] = useState<RegisterKind>("insurance_policy");
  const [editing, setEditing] = useState<RegisterRow>();
  const [form, setForm] = useState<FormState>(() => emptyForm(currencies, accounts));
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [feedback, setFeedback] = useState<string>();
  const [renewingId, setRenewingId] = useState<string>();
  const [renewalDate, setRenewalDate] = useState("");

  useEffect(() => {
    let current = true;
    request<RegisterResponse>("/register?includeArchived=true")
      .then((result) => {
        if (!current) return;
        setData(result);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  function refetch() {
    setAttempt((value) => value + 1);
  }

  function changeForm<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [field]: value }));
    setProblem(undefined);
  }

  function startCreate(kind: RegisterKind) {
    setEditing(undefined);
    setCreatingKind(kind);
    setForm(emptyForm(currencies, accounts));
    setProblem(undefined);
    setFeedback(undefined);
  }

  function startEdit(record: RegisterRow) {
    setEditing(record);
    setForm(formFromRecord(record));
    setRenewingId(undefined);
    setProblem(undefined);
    setFeedback(undefined);
  }

  function cancelEdit() {
    setEditing(undefined);
    setForm(emptyForm(currencies, accounts));
    setIdempotencyKey(crypto.randomUUID());
    setProblem(undefined);
  }

  async function submit() {
    const kind = editing?.kind ?? creatingKind;
    setBusy(true);
    setProblem(undefined);
    setFeedback(undefined);
    try {
      const fields = fieldsForKind(kind, form);
      if (editing) {
        await request<RegisterRow>(`/register/${editing.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(fields),
        });
        setFeedback("Record updated.");
      } else {
        await request<RegisterRow>("/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind, ...fields, idempotencyKey }),
        });
        setFeedback("Record added.");
      }
      setEditing(undefined);
      setForm(emptyForm(currencies, accounts));
      setIdempotencyKey(crypto.randomUUID());
      refetch();
    } catch (error) {
      setProblem(errorText(error, "This record could not be saved."));
    } finally {
      setBusy(false);
    }
  }

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    setProblem(undefined);
    try {
      await action();
      refetch();
    } catch (error) {
      setProblem(errorText(error, fallback));
    } finally {
      setBusy(false);
    }
  }

  function markReviewed(record: RegisterRow) {
    void run(async () => {
      await request(`/register/${record.id}/reviewed`, { method: "POST" });
    }, "This could not be marked reviewed.");
  }

  function startRenew(record: RegisterRow) {
    setRenewingId(record.id);
    setRenewalDate("");
    setProblem(undefined);
  }

  function submitRenewal(record: RegisterRow) {
    void run(async () => {
      await request(`/register/${record.id}/renewed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nextRenewalDate: renewalDate }),
      });
      setRenewingId(undefined);
    }, "This could not be marked renewed.");
  }

  function toggleComplete(record: RegisterRow) {
    const path = record.completedAt ? "reopen" : "complete";
    void run(async () => {
      await request(`/register/${record.id}/${path}`, { method: "POST" });
    }, "This could not be updated.");
  }

  function archive(record: RegisterRow) {
    void run(async () => {
      await request(`/register/${record.id}/archive`, { method: "POST" });
    }, "This record could not be archived.");
  }

  // The kind driving both the form fields shown and the request built: fixed to the record's own
  // kind while editing (kind cannot change), otherwise the kind chosen for a new record.
  const activeKind = editing?.kind ?? creatingKind;
  const depositAccounts = accounts.filter(
    (account) => account.isActive && DEPOSIT_ACCOUNT_TYPES.has(account.accountType),
  );
  const accountOptionsForKind = activeKind === "deposit" ? depositAccounts : accounts;

  return (
    <section aria-labelledby="register-title" id="register">
      <h2 id="register-title">Register</h2>

      {state === "loading" && <p role="status">Loading the register…</p>}
      {state === "error" && (
        <div role="alert">
          <p>The register could not be loaded.</p>
          <button className="text-action" onClick={refetch} type="button">Try again</button>
        </div>
      )}

      {state === "ready" && data && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          {feedback && <p role="status">{feedback}</p>}

          {KIND_GROUPS.map(({ kind, label }) => {
            const records = data.records.filter((record) => record.kind === kind);
            if (records.length === 0) return null;
            return (
              <div key={kind}>
                <h3>{label}</h3>
                <ul className="plans-register-list">
                  {records.map((record) => (
                    <li key={record.id}>
                      <span>{recordSummary(record, currencies)}</span>
                      {!record.archivedAt && (
                        <div className="needs-attention-controls">
                          <button className="text-action" onClick={() => startEdit(record)} type="button">Edit</button>
                          {kind !== "claim" && kind !== "administration" && (
                            <button
                              className="text-action"
                              disabled={busy}
                              onClick={() => markReviewed(record)}
                              type="button"
                            >Mark reviewed</button>
                          )}
                          {kind === "insurance_policy" && renewingId !== record.id && (
                            <button className="text-action" onClick={() => startRenew(record)} type="button">Renewed</button>
                          )}
                          {(kind === "claim" || kind === "administration") && (
                            <button
                              className="text-action"
                              disabled={busy}
                              onClick={() => toggleComplete(record)}
                              type="button"
                            >{record.completedAt ? "Reopen" : "Complete"}</button>
                          )}
                          <button
                            className="text-action"
                            disabled={busy}
                            onClick={() => archive(record)}
                            type="button"
                          >Archive</button>
                        </div>
                      )}
                      {renewingId === record.id && (
                        <span className="needs-attention-inline-form">
                          <input
                            aria-label={`Next renewal date for ${record.title}`}
                            onChange={(e) => setRenewalDate(e.target.value)}
                            type="date"
                            value={renewalDate}
                          />
                          <button
                            className="primary-action"
                            disabled={busy || !renewalDate}
                            onClick={() => submitRenewal(record)}
                            type="button"
                          >Confirm renewal</button>
                          <button className="text-action" onClick={() => setRenewingId(undefined)} type="button">Cancel</button>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          {data.records.length === 0 && <p>No records are kept yet.</p>}

          <h3>{editing ? `Edit ${editing.title}` : "Add a record"}</h3>
          {!editing && (
            <label className="select-field">
              <span>Kind</span>
              <select
                onChange={(e) => startCreate(e.target.value as RegisterKind)}
                value={creatingKind}
              >
                {KIND_GROUPS.map(({ kind, label }) => (
                  <option key={kind} value={kind}>{label}</option>
                ))}
              </select>
            </label>
          )}
          <p><small>Do not record identity numbers or credentials here.</small></p>

          <label className="text-field">
            <span>Title</span>
            <input onChange={(e) => changeForm("title", e.target.value)} value={form.title} />
          </label>

          {(activeKind === "insurance_policy" || activeKind === "deposit" || activeKind === "claim") && (
            <label className="text-field">
              <span>Counterparty</span>
              <input onChange={(e) => changeForm("counterparty", e.target.value)} value={form.counterparty} />
            </label>
          )}

          {activeKind === "insurance_policy" && (
            <label className="text-field">
              <span>Covered</span>
              <input onChange={(e) => changeForm("covered", e.target.value)} value={form.covered} />
            </label>
          )}

          {(activeKind === "insurance_policy" || activeKind === "deposit" || activeKind === "claim") && (
            <>
              <label className="text-field">
                <span>
                  {activeKind === "insurance_policy" && "Coverage amount"}
                  {activeKind === "deposit" && "Expected proceeds"}
                  {activeKind === "claim" && "Expected amount"}
                </span>
                <input inputMode="decimal" onChange={(e) => changeForm("amount", e.target.value)} value={form.amount} />
              </label>
              <label className="select-field">
                <span>Currency</span>
                <select onChange={(e) => changeForm("currency", e.target.value)} value={form.currency}>
                  {currencies.map((currency) => (
                    <option key={currency.code} value={currency.code}>{currency.code}</option>
                  ))}
                </select>
              </label>
            </>
          )}

          {activeKind === "insurance_policy" && (
            <>
              <label className="text-field">
                <span>Premium amount</span>
                <input
                  inputMode="decimal"
                  onChange={(e) => changeForm("premiumAmount", e.target.value)}
                  value={form.premiumAmount}
                />
              </label>
              <label className="select-field">
                <span>Premium cadence</span>
                <select
                  onChange={(e) => changeForm("premiumCadence", e.target.value as PremiumCadence)}
                  value={form.premiumCadence}
                >
                  <option value="monthly">Monthly</option>
                  <option value="annual">Annual</option>
                </select>
              </label>
            </>
          )}

          {activeKind !== "nominee" && (
            <label className="date-field">
              <span>
                {activeKind === "insurance_policy" && "Renewal date"}
                {activeKind === "deposit" && "Maturity date"}
                {activeKind === "claim" && "Submission date"}
                {activeKind === "administration" && "Deadline"}
              </span>
              <input onChange={(e) => changeForm("keyDate", e.target.value)} type="date" value={form.keyDate} />
            </label>
          )}

          {(activeKind === "deposit" || activeKind === "nominee") && (
            <label className="select-field">
              <span>Account</span>
              <select onChange={(e) => changeForm("accountId", e.target.value)} value={form.accountId}>
                {accountOptionsForKind.map((account) => (
                  <option key={account.id} value={account.id}>{account.name}</option>
                ))}
              </select>
            </label>
          )}

          {activeKind === "deposit" && (
            <label className="text-field">
              <span>Renewal instruction (optional)</span>
              <input onChange={(e) => changeForm("instruction", e.target.value)} value={form.instruction} />
            </label>
          )}

          {activeKind === "nominee" && (
            <label className="select-field">
              <span>Nominee recorded</span>
              <select
                onChange={(e) => changeForm("nomineeRecorded", e.target.value === "yes")}
                value={form.nomineeRecorded ? "yes" : "no"}
              >
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </label>
          )}

          <button
            className="primary-action"
            disabled={busy || !completeForm(activeKind, form)}
            onClick={() => void submit()}
            type="button"
          >{busy ? "Saving…" : editing ? "Save changes" : "Add record"}</button>
          {editing && (
            <button className="text-action" onClick={cancelEdit} type="button">Cancel</button>
          )}
        </>
      )}
    </section>
  );
}
