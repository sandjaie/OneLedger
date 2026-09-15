"use client";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  AccountRow,
  CommitmentCreate,
  CommitmentRow,
  CommitmentsResponse,
  CurrencyRow,
  ForecastResponse,
} from "@/types/api";

import { SettlementPanel } from "./settlement-panel";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type FormState = {
  accountId: string;
  label: string;
  amount: string;
  cadence: "once" | "monthly" | "annual";
  firstDate: string;
  endDate: string;
  certainty: "confirmed" | "estimated";
  nextDueDate: string;
};

const STATUS_TEXT: Record<CommitmentRow["status"], string> = {
  upcoming: "Upcoming",
  due: "Due",
  overdue: "Overdue",
  finished: "Finished",
  cancelled: "Cancelled",
};

function statusText(commitment: CommitmentRow): string {
  const base = STATUS_TEXT[commitment.status];
  return commitment.partlyPaid ? `${base} · Partly paid` : base;
}

const EXCLUDED_ACCOUNT_TEXT: Record<string, string> = {
  not_ledger_valued: "This account is valued from holdings or a snapshot, not the ledger.",
  no_balance_date: "This account has no ledger balance date.",
  balance_after_as_of: "This account's balance date is after the forecast date.",
};

const EXCLUDED_COMMITMENT_TEXT: Record<string, string> = {
  account_closed: "Its account is closed.",
  account_excluded: "Its account is excluded from the forecast.",
  currency_mismatch: "Its currency no longer matches its account's currency.",
};

function emptyForm(accounts: Array<AccountRow>): FormState {
  return {
    accountId: accounts[0]?.id ?? "",
    label: "",
    amount: "",
    cadence: "monthly",
    firstDate: "",
    endDate: "",
    certainty: "confirmed",
    nextDueDate: "",
  };
}

function formFromCommitment(commitment: CommitmentRow): FormState {
  return {
    accountId: commitment.accountId,
    label: commitment.label,
    amount: commitment.amount,
    cadence: commitment.cadence,
    firstDate: commitment.firstDate,
    endDate: commitment.endDate ?? "",
    certainty: commitment.certainty,
    nextDueDate: commitment.nextDueDate ?? "",
  };
}

export function PlansScreen({ accounts, currencies, request }: Props) {
  // A closed account cannot receive a new commitment (the route refuses it, 422), so the picker
  // and its default only ever offer an active one.
  const activeAccounts = accounts.filter((account) => account.isActive);
  const [forecast, setForecast] = useState<ForecastResponse>();
  const [forecastState, setForecastState] = useState<"loading" | "ready" | "error">("loading");
  const [commitments, setCommitments] = useState<Array<CommitmentRow>>();
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState<CommitmentRow>();
  const [form, setForm] = useState<FormState>(() => emptyForm(activeAccounts));
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [feedback, setFeedback] = useState<string>();
  const [settlingId, setSettlingId] = useState<string>();

  useEffect(() => {
    let current = true;
    Promise.all([
      request<ForecastResponse>("/forecast"),
      request<CommitmentsResponse>("/commitments"),
    ])
      .then(([nextForecast, nextCommitments]) => {
        if (!current) return;
        setForecast(nextForecast);
        setCommitments(nextCommitments.commitments);
        setForecastState("ready");
      })
      .catch(() => {
        if (current) setForecastState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  function changeForm<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [field]: value }));
    setProblem(undefined);
  }

  function startEdit(commitment: CommitmentRow) {
    setEditing(commitment);
    setForm(formFromCommitment(commitment));
    setProblem(undefined);
    setFeedback(undefined);
  }

  function cancelEdit() {
    setEditing(undefined);
    setForm(emptyForm(activeAccounts));
    setIdempotencyKey(crypto.randomUUID());
    setProblem(undefined);
  }

  const selectedAccount = accounts.find((account) => account.id === form.accountId);

  async function submit() {
    if (!selectedAccount) return;
    setBusy(true);
    setProblem(undefined);
    setFeedback(undefined);
    try {
      if (editing) {
        await request<CommitmentRow>(`/commitments/${editing.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            accountId: form.accountId,
            label: form.label,
            amount: form.amount,
            currency: selectedAccount.currency,
            cadence: form.cadence,
            firstDate: form.firstDate,
            endDate: form.endDate || null,
            certainty: form.certainty,
            // Only an owner-changed next due date is sent: the server keeps the stored one when
            // it is still on the (possibly edited) schedule, else applies its default rule.
            nextDueDate:
              form.nextDueDate && form.nextDueDate !== (editing.nextDueDate ?? "")
                ? form.nextDueDate
                : null,
          }),
        });
        setFeedback("Commitment updated.");
      } else {
        const body: CommitmentCreate = {
          accountId: form.accountId,
          label: form.label,
          amount: form.amount,
          currency: selectedAccount.currency,
          cadence: form.cadence,
          firstDate: form.firstDate,
          endDate: form.endDate || null,
          certainty: form.certainty,
          nextDueDate: form.nextDueDate || null,
          idempotencyKey,
        };
        await request<CommitmentRow>("/commitments", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        setFeedback("Commitment added.");
      }
      setEditing(undefined);
      setForm(emptyForm(activeAccounts));
      setIdempotencyKey(crypto.randomUUID());
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This commitment could not be saved."));
    } finally {
      setBusy(false);
    }
  }

  async function archive(commitment: CommitmentRow) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<CommitmentRow>(`/commitments/${commitment.id}/archive`, { method: "POST" });
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This commitment could not be archived."));
    } finally {
      setBusy(false);
    }
  }

  const completeForm = Boolean(
    form.accountId && form.label && form.amount && form.firstDate && selectedAccount,
  );

  return (
    <section className="workspace-content plans-screen" aria-labelledby="plans-title">
      <header>
        <p className="eyebrow">Read-only projection</p>
        <h1 className="workspace-title" id="plans-title">Plans</h1>
        <p>Commitments drive a 30, 60 and 90-day forecast; the forecast itself posts nothing.</p>
      </header>

      <section aria-labelledby="plans-assumptions-title">
        <h2 id="plans-assumptions-title">Assumptions</h2>
        <ul className="plans-assumptions">
          <li>Balances start from each account&rsquo;s latest ledger balance and its date.</li>
          <li>
            Every active commitment occurs in full on its scheduled date and is unposted when
            dated after that balance date.
          </li>
          <li>
            Nothing else changes: no other income or spending, interest, card settlement, prices
            or exchange rates.
          </li>
          <li>Balances stay in each account&rsquo;s own currency.</li>
          <li>A forecast posts nothing.</li>
          <li>A resolved occurrence is not projected.</li>
          <li>A part-paid occurrence projects its remainder, not its full amount.</li>
        </ul>
      </section>

      <section aria-labelledby="plans-forecast-title">
        <h2 id="plans-forecast-title">Forecast{forecast ? ` as of ${forecast.asOf}` : ""}</h2>
        {forecastState === "loading" && <p role="status">Loading the forecast…</p>}
        {forecastState === "error" && (
          <div role="alert">
            <p>The forecast could not be loaded.</p>
            <button
              className="text-action"
              onClick={() => { setForecastState("loading"); setAttempt((value) => value + 1); }}
              type="button"
            >Try again</button>
          </div>
        )}
        {forecastState === "ready" && forecast && (
          <>
            {forecast.accounts.length === 0 && <p>No account can be forecast yet.</p>}
            <ul className="plans-account-list">
              {forecast.accounts.map((account) => {
                return (
                  <li key={account.accountId}>
                    <strong>{account.accountName}</strong>
                    <p>
                      Start {moneyText(account.startBalance, account.currency, currencies)} as of {account.balanceDate}
                    </p>
                    <ul className="plans-points">
                      {account.points.map((point) => (
                        <li key={point.days}>
                          {point.days}d ({point.date}): {moneyText(point.balance, account.currency, currencies)}
                        </li>
                      ))}
                    </ul>
                    {account.occurrences.length > 0 && (
                      <ul className="plans-occurrences">
                        {account.occurrences.map((occurrence, index) => (
                          <li key={index}>
                            {occurrence.date} {occurrence.label}{" "}
                            {moneyText(occurrence.amount, account.currency, currencies)}
                            {occurrence.certainty === "estimated" && <small> · estimated</small>}
                            {occurrence.dueByAsOf && <small> · not yet in the ledger</small>}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
            {forecast.excludedAccounts.length > 0 && (
              <div>
                <h3>Accounts not forecast</h3>
                <ul className="plans-exclusions">
                  {forecast.excludedAccounts.map((excluded) => (
                    <li key={excluded.accountId}>
                      {excluded.accountName}: {EXCLUDED_ACCOUNT_TEXT[excluded.reason] ?? excluded.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {forecast.excludedCommitments.length > 0 && (
              <div>
                <h3>Commitments not applied</h3>
                <ul className="plans-exclusions">
                  {forecast.excludedCommitments.map((excluded) => (
                    <li key={excluded.commitmentId}>
                      {excluded.label}: {EXCLUDED_COMMITMENT_TEXT[excluded.reason] ?? excluded.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </section>

      <section aria-labelledby="plans-commitments-title">
        <h2 id="plans-commitments-title">Commitments</h2>
        {commitments && commitments.length === 0 && <p>No commitments are recorded yet.</p>}
        {commitments && commitments.length > 0 && (
          <ul className="plans-commitment-list">
            {commitments.map((commitment) => {
              return (
                <li key={commitment.id}>
                  <span>
                    {commitment.accountName} · {commitment.label} ·{" "}
                    {moneyText(commitment.amount, commitment.currency, currencies)} · {commitment.cadence}
                    {" · "}
                    {commitment.certainty === "estimated" ? "Estimated" : "Confirmed"}
                  </span>
                  <span>
                    {statusText(commitment)}
                    {commitment.nextDueDate && <> · Next due {commitment.nextDueDate}</>}
                    {commitment.nextDueDate && (
                      <>
                        {" · Paid "}
                        {moneyText(commitment.paidTowardNext, commitment.currency, currencies)}
                        {commitment.remainingNext !== null && (
                          <>
                            {" · Remaining "}
                            {moneyText(commitment.remainingNext, commitment.currency, currencies)}
                          </>
                        )}
                      </>
                    )}
                  </span>
                  {commitment.archivedAt ? (
                    <small>Archived</small>
                  ) : (
                    <>
                      <button
                        className="text-action"
                        onClick={() => setSettlingId(commitment.id)}
                        type="button"
                      >{commitment.nextDueDate ? "Settle" : "History"}</button>
                      <button className="text-action" onClick={() => startEdit(commitment)} type="button">Edit</button>
                      <button
                        className="text-action"
                        disabled={busy}
                        onClick={() => void archive(commitment)}
                        type="button"
                      >Archive</button>
                    </>
                  )}
                  {settlingId === commitment.id && (
                    <SettlementPanel
                      commitment={commitment}
                      currencies={currencies}
                      request={request}
                      onClose={() => setSettlingId(undefined)}
                      onChanged={() => setAttempt((value) => value + 1)}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        )}

        <h3>{editing ? `Edit ${editing.label}` : "Add a commitment"}</h3>
        <label className="select-field">
          <span>Account</span>
          <select onChange={(e) => changeForm("accountId", e.target.value)} value={form.accountId}>
            {activeAccounts.map((account) => (
              <option key={account.id} value={account.id}>{account.name}</option>
            ))}
          </select>
        </label>
        <label className="text-field"><span>Label</span><input onChange={(e) => changeForm("label", e.target.value)} value={form.label} /></label>
        <label className="text-field">
          <span>Amount{selectedAccount ? ` (${selectedAccount.currency})` : ""}</span>
          <input inputMode="decimal" onChange={(e) => changeForm("amount", e.target.value)} value={form.amount} />
        </label>
        <label className="select-field">
          <span>Cadence</span>
          <select onChange={(e) => changeForm("cadence", e.target.value as FormState["cadence"])} value={form.cadence}>
            <option value="once">One-off</option>
            <option value="monthly">Monthly</option>
            <option value="annual">Annual</option>
          </select>
        </label>
        <label className="date-field"><span>First date</span><input onChange={(e) => changeForm("firstDate", e.target.value)} type="date" value={form.firstDate} /></label>
        {form.cadence !== "once" && (
          <label className="date-field"><span>End date (optional)</span><input onChange={(e) => changeForm("endDate", e.target.value)} type="date" value={form.endDate} /></label>
        )}
        <label className="select-field">
          <span>Certainty</span>
          <select
            onChange={(e) => changeForm("certainty", e.target.value as FormState["certainty"])}
            value={form.certainty}
          >
            <option value="confirmed">Confirmed</option>
            <option value="estimated">Estimated</option>
          </select>
        </label>
        <label className="date-field">
          <span>Next due date (optional)</span>
          <input onChange={(e) => changeForm("nextDueDate", e.target.value)} type="date" value={form.nextDueDate} />
        </label>

        {problem && <p className="inline-alert" role="alert">{problem}</p>}
        {feedback && <p role="status">{feedback}</p>}
        <button className="primary-action" disabled={!completeForm || busy} onClick={() => void submit()} type="button">
          {busy ? "Saving…" : editing ? "Save changes" : "Add commitment"}
        </button>
        {editing && <button className="text-action" onClick={cancelEdit} type="button">Cancel</button>}
      </section>
    </section>
  );
}
