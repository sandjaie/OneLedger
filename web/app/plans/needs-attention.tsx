"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { useEffect, useState } from "react";

import { AttentionRow } from "@/app/ui/attention-row";
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
  goal_shortfall: "Goal shortfall",
  reserve_below_target: "Reserve below target",
  renewal_due: "Renewal due",
  maturity_due: "Maturity due",
  deadline_due: "Deadline due",
  review_due: "Review due",
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
  const [openForm, setOpenForm] = useState<{ key: string; kind: "reason" | "snooze" | "renew" }>();
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
      setAttempt((value) => value + 1);
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

  function markRecordReviewed(item: ActionRow) {
    if (!item.registerRecordId) return;
    void run(
      item.actionKey,
      async () => {
        await request(`/register/${item.registerRecordId}/reviewed`, { method: "POST" });
      },
      "This could not be marked reviewed.",
    );
  }

  function completeRecordDeadline(item: ActionRow) {
    if (!item.registerRecordId) return;
    void run(
      item.actionKey,
      async () => {
        await request(`/register/${item.registerRecordId}/complete`, { method: "POST" });
      },
      "This could not be completed.",
    );
  }

  function renewRecord(item: ActionRow, nextRenewalDate: string) {
    if (!item.registerRecordId) return;
    void run(
      item.actionKey,
      async () => {
        await request(`/register/${item.registerRecordId}/renewed`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nextRenewalDate }),
        });
      },
      "This could not be marked renewed.",
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
      setAttempt((value) => value + 1);
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
            <Button
              aria-label={`Settle ${item.title}`}
              variant="outline"
              onClick={() => onSettle(item.commitmentId as string)}
              type="button"
            >
              Settle
            </Button>
            <Button
              aria-label={`Mark done ${item.title}`}
              variant="outline"
              disabled={busy}
              onClick={() => markDone(item)}
              type="button"
            >Mark done</Button>
          </>
        )}
        {item.kind === "card_due" && (
          <Button
            aria-label={`Plan payment ${item.title}`}
            variant="outline"
            onClick={() => onPlanCardPayment(item)}
            type="button"
          >Plan payment</Button>
        )}
        {item.kind === "follow_up" && (
          <Button
            aria-label={`Complete ${item.title}`}
            variant="outline"
            disabled={busy}
            onClick={() => completeFollowUp(item)}
            type="button"
          >Complete</Button>
        )}
        {item.kind === "record" && item.reason === "review_due" && (
          <Button
            aria-label={`Mark reviewed ${item.title}`}
            variant="outline"
            disabled={busy}
            onClick={() => markRecordReviewed(item)}
            type="button"
          >Mark reviewed</Button>
        )}
        {item.kind === "record" && item.reason === "deadline_due" && (
          <Button
            aria-label={`Complete ${item.title}`}
            variant="outline"
            disabled={busy}
            onClick={() => completeRecordDeadline(item)}
            type="button"
          >Complete</Button>
        )}
        {item.kind === "record" && item.reason === "renewal_due" && formOpen !== "renew" && (
          <Button
            aria-label={`Renewed ${item.title}`}
            variant="outline"
            disabled={busy}
            onClick={() => { setOpenForm({ key: item.actionKey, kind: "renew" }); setDraftText(""); }}
            type="button"
          >Renewed</Button>
        )}
        {formOpen !== "reason" && item.kind !== "goal" && item.kind !== "reserve" && item.kind !== "record" && (
          <Button
            aria-label={`Dismiss ${item.title}`}
            variant="outline"
            disabled={busy}
            onClick={() => { setOpenForm({ key: item.actionKey, kind: "reason" }); setDraftText(""); }}
            type="button"
          >Dismiss</Button>
        )}
        {formOpen !== "snooze" && (
          <Button
            aria-label={`Snooze ${item.title}`}
            variant="outline"
            disabled={busy}
            onClick={() => { setOpenForm({ key: item.actionKey, kind: "snooze" }); setDraftText(""); }}
            type="button"
          >Snooze</Button>
        )}
        {formOpen === "reason" && (
          <span className="needs-attention-inline-form">
            <Input
              aria-label={`Reason to dismiss ${item.title}`}
              onChange={(e) => setDraftText(e.target.value)}
              value={draftText}
            />
            <Button
              variant="default"
              disabled={busy || !draftText.trim()}
              onClick={() => {
                if (item.kind === "commitment") dismissCommitment(item, draftText.trim());
                else if (item.kind === "card_due") dismissCard(item, draftText.trim());
                else dismissFollowUp(item, draftText.trim());
              }}
              type="button"
            >Confirm dismissal</Button>
            <Button variant="outline" onClick={closeForm} type="button">Cancel</Button>
          </span>
        )}
        {formOpen === "snooze" && (
          <span className="needs-attention-inline-form">
            <Input
              aria-label={`Snooze ${item.title} until`}
              onChange={(e) => setDraftText(e.target.value)}
              type="date"
              value={draftText}
            />
            <Button
              variant="default"
              disabled={busy || !draftText}
              onClick={() => snooze(item, draftText)}
              type="button"
            >Confirm snooze</Button>
            <Button variant="outline" onClick={closeForm} type="button">Cancel</Button>
          </span>
        )}
        {formOpen === "renew" && (
          <span className="needs-attention-inline-form">
            <Input
              aria-label={`Next renewal date for ${item.title}`}
              onChange={(e) => setDraftText(e.target.value)}
              type="date"
              value={draftText}
            />
            <Button
              variant="default"
              disabled={busy || !draftText}
              onClick={() => renewRecord(item, draftText)}
              type="button"
            >Confirm renewal</Button>
            <Button variant="outline" onClick={closeForm} type="button">Cancel</Button>
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

  const activeAccounts = accounts.filter((account) => account.isActive);

  return (
    <section className="plans-attention-section" aria-labelledby="actions-title" id="actions">
      <div className="console-section-head"><h2 id="actions-title">Needs attention</h2></div>
      {state === "loading" && <p role="status">Loading what needs attention…</p>}
      {state === "error" && (
        <div role="alert">
          <p>Needs attention could not be loaded.</p>
          <Button variant="outline" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</Button>
        </div>
      )}
      {state === "ready" && actions && (
        <>
          <AttentionRow
            tone={actions.items.length > 0 ? "attention" : "success"}
            title={actions.items.length > 0
              ? `${actions.items.length} ${actions.items.length === 1 ? "decision needs" : "decisions need"} attention`
              : "No decisions need attention"}
            detail={actions.items.length > 0
              ? "Review the dated evidence and choose the next action. Nothing changes automatically."
              : "There are no current overdue, due or follow-up items."}
          />
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          {actions.items.length === 0 && <p>Nothing needs attention right now.</p>}
          {actions.items.length > 0 && (
            <ul className="needs-attention-list">{actions.items.map((item) => row(item))}</ul>
          )}

          {actions.snoozed.length > 0 && (
            <div>
              <Button variant="outline" onClick={() => setShowSnoozed((v) => !v)} type="button">
                {showSnoozed ? "Hide" : "Show"} snoozed ({actions.snoozed.length})
              </Button>
              {showSnoozed && (
                <ul className="needs-attention-list">
                  {actions.snoozed.map((item) => (
                    <li key={item.actionKey}>
                      <span>
                        {REASON_TEXT[item.reason]} · {item.title} · snoozed until {item.snoozedUntil}
                      </span>
                      <Button
                        variant="outline"
                        disabled={busyKey === item.actionKey}
                        onClick={() => wake(item.actionKey)}
                        type="button"
                      >Wake</Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <h3>Add a follow-up</h3>
          <label className="text-field">
            <span>Title</span>
            <Input onChange={(e) => setNewTitle(e.target.value)} value={newTitle} />
          </label>
          <label className="date-field">
            <span>Due date (optional)</span>
            <Input onChange={(e) => setNewDueDate(e.target.value)} type="date" value={newDueDate} />
          </label>
          <label className="select-field">
            <span>Account (optional)</span>
            <NativeSelect onChange={(e) => setNewAccountId(e.target.value)} value={newAccountId}>
              <option value="">None</option>
              {activeAccounts.map((account) => (
                <option key={account.id} value={account.id}>{account.name}</option>
              ))}
            </NativeSelect>
          </label>
          {followUpProblem && <p className="inline-alert" role="alert">{followUpProblem}</p>}
          <Button
            variant="default"
            disabled={followUpBusy || !newTitle.trim()}
            onClick={() => void addFollowUp()}
            type="button"
          >Add follow-up</Button>

          {actions.resolvedFollowUps.length > 0 && (
            <div>
              <Button
                variant="outline"
                onClick={() => setShowResolved((v) => !v)}
                type="button"
              >{showResolved ? "Hide" : "Show"} recently resolved follow-ups</Button>
              {showResolved && (
                <ul className="needs-attention-list">
                  {actions.resolvedFollowUps.map((followUp) => (
                    <li key={followUp.id}>
                      <span>
                        {followUp.title}
                        {" · "}
                        {followUp.state === "completed" ? "Completed" : `Dismissed: ${followUp.dismissReason}`}
                      </span>
                      <Button
                        variant="outline"
                        disabled={busyKey === followUp.id}
                        onClick={() => reopenFollowUp(followUp.id)}
                        type="button"
                      >Reopen</Button>
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
