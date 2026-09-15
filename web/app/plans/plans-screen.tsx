"use client";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  AccountRow,
  ActionRow,
  CommitmentCreate,
  CommitmentRow,
  CommitmentsResponse,
  CurrencyRow,
  UnplannedCardDue,
} from "@/types/api";

import { CashForecast } from "./cash-forecast";
import { EmergencyReserve } from "./emergency-reserve";
import { Goals } from "./goals";
import { MonthlyReview } from "./monthly-review";
import { NeedsAttention } from "./needs-attention";
import { SettlementPanel } from "./settlement-panel";

// Spec "Card dues and actions (Task 2)": Plan payment pre-fills the negated statement balance —
// a sign toggle on the server's own string, not a computed total, so it stays outside the
// "no TypeScript money arithmetic" rule the same way formatMoney's sign handling does.
function negateAmount(value: string): string {
  return value.startsWith("-") ? value.slice(1) : `-${value}`;
}

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
  const [commitments, setCommitments] = useState<Array<CommitmentRow>>();
  const [commitmentsFailed, setCommitmentsFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState<CommitmentRow>();
  const [form, setForm] = useState<FormState>(() => emptyForm(activeAccounts));
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [feedback, setFeedback] = useState<string>();
  const [settlingId, setSettlingId] = useState<string>();
  // Set only while planning a card statement's payment (Plan payment); cleared on cancel, on any
  // other edit start, and after a successful submit. Limits the account picker to active asset
  // accounts in the statement's currency and is sent as the create body's sourceCardStatementId.
  const [planningCard, setPlanningCard] = useState<{ statementId: string; currency: string }>();

  useEffect(() => {
    let current = true;
    request<CommitmentsResponse>("/commitments")
      .then((result) => {
        if (!current) return;
        setCommitments(result.commitments);
        setCommitmentsFailed(false);
      })
      .catch(() => {
        // Needs attention and the forecast each report their own fetch state.
        if (current) setCommitmentsFailed(true);
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
    setPlanningCard(undefined);
    setProblem(undefined);
    setFeedback(undefined);
  }

  function cancelEdit() {
    setEditing(undefined);
    setPlanningCard(undefined);
    setForm(emptyForm(activeAccounts));
    setIdempotencyKey(crypto.randomUUID());
    setProblem(undefined);
  }

  // Plan payment (Needs attention Task 2, and the forecast's Card statements with no planned
  // payment): the commitment form pre-filled from a card due — a one-off, the negated statement
  // balance, the due date, and the source; the account picker narrows to active asset accounts
  // in the statement's currency (formAccounts below). The two callers hand this a differently
  // shaped server row, so each narrows to these five fields before calling in.
  function applyCardPlan(due: {
    cardStatementId: string;
    currency: string;
    title: string;
    amount: string;
    dueDate: string | null;
  }) {
    setEditing(undefined);
    setPlanningCard({ statementId: due.cardStatementId, currency: due.currency });
    const firstChoice = accounts.find(
      (account) => account.isActive && account.accountClass === "asset" && account.currency === due.currency,
    );
    setForm({
      accountId: firstChoice?.id ?? "",
      label: due.title,
      amount: negateAmount(due.amount),
      cadence: "once",
      firstDate: due.dueDate ?? "",
      endDate: "",
      certainty: "confirmed",
      nextDueDate: "",
    });
    setIdempotencyKey(crypto.randomUUID());
    setProblem(undefined);
    setFeedback(undefined);
  }

  function startCardPlan(item: ActionRow) {
    if (!item.cardStatementId || !item.currency || !item.amount) return;
    applyCardPlan({
      cardStatementId: item.cardStatementId,
      currency: item.currency,
      title: item.title,
      amount: item.amount,
      dueDate: item.dueDate,
    });
  }

  function startCardPlanFromDue(due: UnplannedCardDue) {
    applyCardPlan({
      cardStatementId: due.cardStatementId,
      currency: due.currency,
      title: due.accountName,
      amount: due.statementBalance,
      dueDate: due.paymentDueDate,
    });
  }

  const selectedAccount = accounts.find((account) => account.id === form.accountId);
  // The ordinary picker offers every active account; planning a card payment narrows it to the
  // spec's own rule (active asset accounts in the statement's currency).
  const formAccounts = planningCard
    ? activeAccounts.filter(
        (account) => account.accountClass === "asset" && account.currency === planningCard.currency,
      )
    : activeAccounts;

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
          sourceCardStatementId: planningCard?.statementId ?? null,
        };
        await request<CommitmentRow>("/commitments", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        setFeedback("Commitment added.");
      }
      setEditing(undefined);
      setPlanningCard(undefined);
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

      <NeedsAttention
        accounts={accounts}
        currencies={currencies}
        onCommitmentResolved={() => setAttempt((value) => value + 1)}
        onPlanCardPayment={startCardPlan}
        onSettle={(commitmentId) => setSettlingId(commitmentId)}
        refreshKey={attempt}
        request={request}
      />

      <MonthlyReview accounts={accounts} currencies={currencies} request={request} />

      <Goals accounts={accounts} currencies={currencies} request={request} />

      <EmergencyReserve accounts={accounts} currencies={currencies} request={request} />

      <CashForecast
        accounts={accounts}
        currencies={currencies}
        onPlanCardPayment={startCardPlanFromDue}
        onSettle={(commitmentId) => setSettlingId(commitmentId)}
        refreshKey={attempt}
        request={request}
      />

      <section aria-labelledby="plans-commitments-title">
        <h2 id="plans-commitments-title">Commitments</h2>
        {commitmentsFailed && (
          <div role="alert">
            <p>The commitments could not be loaded.</p>
            <button className="text-action" onClick={() => setAttempt((value) => value + 1)} type="button">
              Try again
            </button>
          </div>
        )}
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
                    {commitment.source === "card_statement" && (
                      <> · From the {commitment.sourceCardAccountName} card statement due on its date</>
                    )}
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

        <h3>
          {editing
            ? `Edit ${editing.label}`
            : planningCard
              ? "Plan the card payment"
              : "Add a commitment"}
        </h3>
        <label className="select-field">
          <span>Account</span>
          <select onChange={(e) => changeForm("accountId", e.target.value)} value={form.accountId}>
            {formAccounts.map((account) => (
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
        {(editing || planningCard) && (
          <button className="text-action" onClick={cancelEdit} type="button">Cancel</button>
        )}
      </section>
    </section>
  );
}
