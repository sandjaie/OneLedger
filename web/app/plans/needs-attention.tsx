"use client";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type { ActionRow, ActionsResponse, AccountRow, CurrencyRow } from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
  // Settle reuses the Task 1 settlement panel PlansScreen already renders per commitment.
  onSettle: (commitmentId: string) => void;
  // Plan payment hands the card item to PlansScreen, which pre-fills the commitment form.
  onPlanCardPayment: (item: ActionRow) => void;
  // A commitment's Mark done or Dismiss changes its status and the forecast; PlansScreen
  // refetches both. Nothing else here (a card or follow-up mark) touches either.
  onCommitmentResolved: () => void;
  // Bumped by PlansScreen after any commitment write (Plan payment, Settle, edit, archive) so the
  // list refetches with it.
  refreshKey: number;
};

const REASON_TEXT: Record<ActionRow["reason"], string> = {
  commitment_overdue: "Overdue",
  commitment_due: "Due soon",
  card_payment_unplanned: "Card payment due",
  follow_up: "Follow-up",
};

function itemAmountText(item: ActionRow, currencies: Array<CurrencyRow>): string | undefined {
  if (item.currency === null) return undefined;
  const amount = item.remaining ?? item.amount;
  if (amount === null) return undefined;
  const text = moneyText(amount, item.currency, currencies);
  return item.partlyPaid ? `${text} remaining` : text;
}

export function NeedsAttention({
  accounts,
  currencies,
  request,
  onSettle,
  onPlanCardPayment,
  onCommitmentResolved,
  refreshKey,
}: Props) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [actions, setActions] = useState<ActionsResponse>();
  const [attempt, setAttempt] = useState(0);
  const [busyKey, setBusyKey] = useState<string>();
  const [problem, setProblem] = useState<string>();
  const [openForm, setOpenForm] = useState<{ key: string; kind: "reason" | "snooze" }>();
  const [draftText, setDraftText] = useState("");
  const [showSnoozed, setShowSnoozed] = useState(false);
  const [showResolved, setShowResolved] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDueDate, setNewDueDate] = useState("");
  const [newAccountId, setNewAccountId] = useState("");
  const [followUpKey, setFollowUpKey] = useState(() => crypto.randomUUID());
  const [followUpBusy, setFollowUpBusy] = useState(false);
  const [followUpProblem, setFollowUpProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<ActionsResponse>("/actions")
      .then((result) => {
        if (!current) return;
        setActions(result);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, refreshKey, request]);

  function refetch() {
    setAttempt((value) => value + 1);
  }

  function closeForm() {
    setOpenForm(undefined);
    setDraftText("");
  }

  async function run(key: string, action: () => Promise<void>, fallback: string) {
    setBusyKey(key);
    setProblem(undefined);
    try {
      await action();
      closeForm();
      refetch();
    } catch (error) {
      setProblem(errorText(error, fallback));
    } finally {
      setBusyKey(undefined);
    }
  }

  function markDone(item: ActionRow) {
    if (!item.commitmentId) return;
    void run(
      item.actionKey,
      async () => {
        await request(`/commitments/${item.commitmentId}/resolutions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dueDate: item.dueDate, kind: "completed" }),
        });
        onCommitmentResolved();
      },
      "This could not be marked done.",
    );
  }

  function dismissCommitment(item: ActionRow, reason: string) {
    if (!item.commitmentId) return;
    void run(
      item.actionKey,
      async () => {
        await request(`/commitments/${item.commitmentId}/resolutions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dueDate: item.dueDate, kind: "dismissed", reason }),
        });
        onCommitmentResolved();
      },
      "This could not be dismissed.",
    );
  }

  function dismissCard(item: ActionRow, reason: string) {
    void run(
      item.actionKey,
      async () => {
        await request(`/actions/marks/${item.actionKey}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason }),
        });
      },
      "This could not be dismissed.",
    );
  }

  function snooze(item: ActionRow, until: string) {
    void run(
      item.actionKey,
      async () => {
        await request(`/actions/marks/${item.actionKey}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ until }),
        });
      },
      "This could not be snoozed.",
    );
  }

  function wake(actionKey: string) {
    void run(
      actionKey,
      async () => {
        await request(`/actions/marks/${actionKey}`, { method: "DELETE" });
      },
      "This could not be woken.",
    );
  }

  function completeFollowUp(item: ActionRow) {
    if (!item.actionItemId) return;
    void run(
      item.actionKey,
      async () => {
        await request(`/actions/follow-ups/${item.actionItemId}/complete`, { method: "POST" });
      },
      "This could not be completed.",
    );
  }

  function dismissFollowUp(item: ActionRow, reason: string) {
    if (!item.actionItemId) return;
    void run(
      item.actionKey,
      async () => {
        await request(`/actions/follow-ups/${item.actionItemId}/dismiss`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason }),
        });
      },
      "This could not be dismissed.",
    );
  }

  function reopenFollowUp(id: string) {
    void run(
      id,
      async () => {
        await request(`/actions/follow-ups/${id}/reopen`, { method: "POST" });
      },
      "This could not be reopened.",
    );
  }

  async function addFollowUp() {
    if (!newTitle.trim()) return;
    setFollowUpBusy(true);
    setFollowUpProblem(undefined);
    try {
      await request("/actions/follow-ups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: newTitle,
          dueDate: newDueDate || null,
          accountId: newAccountId || null,
          idempotencyKey: followUpKey,
        }),
      });
      setNewTitle("");
      setNewDueDate("");
      setNewAccountId("");
      setFollowUpKey(crypto.randomUUID());
      refetch();
    } catch (error) {
      setFollowUpProblem(errorText(error, "This follow-up could not be added."));
    } finally {
      setFollowUpBusy(false);
    }
  }

  function controls(item: ActionRow) {
    const busy = busyKey === item.actionKey;
    const formOpen = openForm?.key === item.actionKey ? openForm.kind : undefined;
    return (
      <div className="needs-attention-controls">
        {item.kind === "commitment" && item.commitmentId && (
          <>
            <button className="text-action" onClick={() => onSettle(item.commitmentId as string)} type="button">
              Settle
            </button>
            <button
              className="text-action"
              disabled={busy}
              onClick={() => markDone(item)}
              type="button"
            >Mark done</button>
          </>
        )}
        {item.kind === "card_due" && (
          <button
            className="text-action"
            onClick={() => onPlanCardPayment(item)}
            type="button"
          >Plan payment</button>
        )}
        {item.kind === "follow_up" && (
          <button
            className="text-action"
            disabled={busy}
            onClick={() => completeFollowUp(item)}
            type="button"
          >Complete</button>
        )}
        {formOpen !== "reason" && (
          <button
            className="text-action"
            disabled={busy}
            onClick={() => { setOpenForm({ key: item.actionKey, kind: "reason" }); setDraftText(""); }}
            type="button"
          >Dismiss</button>
        )}
        {formOpen !== "snooze" && (
          <button
            className="text-action"
            disabled={busy}
            onClick={() => { setOpenForm({ key: item.actionKey, kind: "snooze" }); setDraftText(""); }}
            type="button"
          >Snooze</button>
        )}
        {formOpen === "reason" && (
          <span className="needs-attention-inline-form">
            <input
              aria-label={`Reason to dismiss ${item.title}`}
              onChange={(e) => setDraftText(e.target.value)}
              value={draftText}
            />
            <button
              className="primary-action"
              disabled={busy || !draftText.trim()}
              onClick={() => {
                if (item.kind === "commitment") dismissCommitment(item, draftText.trim());
                else if (item.kind === "card_due") dismissCard(item, draftText.trim());
                else dismissFollowUp(item, draftText.trim());
              }}
              type="button"
            >Confirm dismissal</button>
            <button className="text-action" onClick={closeForm} type="button">Cancel</button>
          </span>
        )}
        {formOpen === "snooze" && (
          <span className="needs-attention-inline-form">
            <input
              aria-label={`Snooze ${item.title} until`}
              onChange={(e) => setDraftText(e.target.value)}
              type="date"
              value={draftText}
            />
            <button
              className="primary-action"
              disabled={busy || !draftText}
              onClick={() => snooze(item, draftText)}
              type="button"
            >Confirm snooze</button>
            <button className="text-action" onClick={closeForm} type="button">Cancel</button>
          </span>
        )}
      </div>
    );
  }

  function row(item: ActionRow) {
    const amountText = itemAmountText(item, currencies);
    return (
      <li className={item.overdue ? "needs-attention-overdue" : undefined} key={item.actionKey}>
        <span>
          {REASON_TEXT[item.reason]} · {item.title}
          {item.dueDate && <> · {item.dueDate}</>}
          {item.accountName && <> · {item.accountName}</>}
          {amountText && <> · {amountText}</>}
          {item.certainty === "estimated" && <> · estimated</>}
        </span>
        {controls(item)}
      </li>
    );
  }

  const activeAssetAccounts = accounts.filter((account) => account.isActive);

  return (
    <section aria-labelledby="actions-title" id="actions">
      <h2 id="actions-title">Needs attention</h2>
      {state === "loading" && <p role="status">Loading what needs attention…</p>}
      {state === "error" && (
        <div role="alert">
          <p>Needs attention could not be loaded.</p>
          <button className="text-action" onClick={refetch} type="button">Try again</button>
        </div>
      )}
      {state === "ready" && actions && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          {actions.items.length === 0 && <p>Nothing needs attention right now.</p>}
          {actions.items.length > 0 && (
            <ul className="needs-attention-list">{actions.items.map((item) => row(item))}</ul>
          )}

          {actions.snoozed.length > 0 && (
            <div>
              <button className="text-action" onClick={() => setShowSnoozed((v) => !v)} type="button">
                {showSnoozed ? "Hide" : "Show"} snoozed ({actions.snoozed.length})
              </button>
              {showSnoozed && (
                <ul className="needs-attention-list">
                  {actions.snoozed.map((item) => (
                    <li key={item.actionKey}>
                      <span>
                        {REASON_TEXT[item.reason]} · {item.title} · snoozed until {item.snoozedUntil}
                      </span>
                      <button
                        className="text-action"
                        disabled={busyKey === item.actionKey}
                        onClick={() => wake(item.actionKey)}
                        type="button"
                      >Wake</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <h3>Add a follow-up</h3>
          <label className="text-field">
            <span>Title</span>
            <input onChange={(e) => setNewTitle(e.target.value)} value={newTitle} />
          </label>
          <label className="date-field">
            <span>Due date (optional)</span>
            <input onChange={(e) => setNewDueDate(e.target.value)} type="date" value={newDueDate} />
          </label>
          <label className="select-field">
            <span>Account (optional)</span>
            <select onChange={(e) => setNewAccountId(e.target.value)} value={newAccountId}>
              <option value="">None</option>
              {activeAssetAccounts.map((account) => (
                <option key={account.id} value={account.id}>{account.name}</option>
              ))}
            </select>
          </label>
          {followUpProblem && <p className="inline-alert" role="alert">{followUpProblem}</p>}
          <button
            className="primary-action"
            disabled={followUpBusy || !newTitle.trim()}
            onClick={() => void addFollowUp()}
            type="button"
          >Add follow-up</button>

          {actions.resolvedFollowUps.length > 0 && (
            <div>
              <button
                className="text-action"
                onClick={() => setShowResolved((v) => !v)}
                type="button"
              >{showResolved ? "Hide" : "Show"} recently resolved follow-ups</button>
              {showResolved && (
                <ul className="needs-attention-list">
                  {actions.resolvedFollowUps.map((followUp) => (
                    <li key={followUp.id}>
                      <span>
                        {followUp.title}
                        {" · "}
                        {followUp.state === "completed" ? "Completed" : `Dismissed: ${followUp.dismissReason}`}
                      </span>
                      <button
                        className="text-action"
                        disabled={busyKey === followUp.id}
                        onClick={() => reopenFollowUp(followUp.id)}
                        type="button"
                      >Reopen</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
