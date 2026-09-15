"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  CommitmentRow,
  CurrencyRow,
  PaymentCandidate,
  PaymentCandidatesResponse,
  ResolutionRow,
  ResolutionsResponse,
} from "@/types/api";

type Props = {
  commitment: CommitmentRow;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
  onClose: () => void;
  onChanged: () => void;
};

function resolutionSentence(resolution: ResolutionRow): string {
  if (resolution.kind === "dismissed") return `Dismissed: ${resolution.reason}`;
  if (resolution.kind === "completed") return "Marked done";
  return resolution.closes ? "Payment (closed the occurrence)" : "Part payment";
}

export function SettlementPanel({ commitment, currencies, request, onClose, onChanged }: Props) {
  const [attempt, setAttempt] = useState(0);
  const [candidatesState, setCandidatesState] = useState<"loading" | "ready" | "error">("loading");
  const [candidates, setCandidates] = useState<PaymentCandidatesResponse>();
  const [historyState, setHistoryState] = useState<"loading" | "ready" | "error">("loading");
  const [history, setHistory] = useState<ResolutionsResponse>();
  const [selected, setSelected] = useState<PaymentCandidate>();
  const [dismissReason, setDismissReason] = useState("");
  const [showDismiss, setShowDismiss] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [feedback, setFeedback] = useState<string>();

  const hasOpenOccurrence = Boolean(commitment.nextDueDate) && !commitment.archivedAt;
  // A claim or deposit's expected money always plans positive; a policy's premium always plans
  // negative (the sign this row's own amount already carries, the same sign check negateAmount
  // uses elsewhere — not money arithmetic or a money-string comparison). The database refuses an
  // income posting for a claim- or deposit-sourced commitment (spec "Register-sourced commitments
  // (V54)"), so the hint only ever applies here.
  const showIncomeHint = commitment.source === "register_record" && !commitment.amount.startsWith("-");

  useEffect(() => {
    let current = true;
    if (hasOpenOccurrence) {
      request<PaymentCandidatesResponse>(`/commitments/${commitment.id}/payment-candidates`)
        .then((result) => {
          if (!current) return;
          setCandidates(result);
          setCandidatesState("ready");
        })
        .catch(() => {
          if (current) setCandidatesState("error");
        });
    }
    request<ResolutionsResponse>(`/commitments/${commitment.id}/resolutions`)
      .then((result) => {
        if (!current) return;
        setHistory(result);
        setHistoryState("ready");
      })
      .catch(() => {
        if (current) setHistoryState("error");
      });
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commitment.id, commitment.nextDueDate, attempt, request]);

  async function post(body: object, closingFeedback: string, closesOccurrence: boolean) {
    setBusy(true);
    setProblem(undefined);
    setFeedback(undefined);
    try {
      await request(`/commitments/${commitment.id}/resolutions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dueDate: commitment.nextDueDate, ...body }),
      });
      setFeedback(closingFeedback);
      setSelected(undefined);
      setShowDismiss(false);
      setDismissReason("");
      onChanged();
      if (closesOccurrence) {
        onClose();
      } else {
        setAttempt((value) => value + 1);
      }
    } catch (error) {
      setProblem(errorText(error, "This could not be recorded."));
    } finally {
      setBusy(false);
    }
  }

  async function recordPayment(closes: boolean) {
    if (!selected) return;
    await post(
      { kind: "payment", transactionId: selected.transactionId, closes },
      closes ? "Payment recorded; occurrence closed." : "Part payment recorded.",
      closes,
    );
  }

  async function markDone() {
    await post({ kind: "completed" }, "Marked done.", true);
  }

  async function dismiss() {
    if (!dismissReason.trim()) return;
    await post({ kind: "dismissed", reason: dismissReason.trim() }, "Dismissed.", true);
  }

  async function undo(resolutionId: string) {
    setBusy(true);
    setProblem(undefined);
    setFeedback(undefined);
    try {
      await request(`/commitments/${commitment.id}/resolutions/${resolutionId}`, {
        method: "DELETE",
      });
      setFeedback("Undone.");
      onChanged();
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This could not be undone."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="plans-settlement-panel" aria-label={`Settle ${commitment.label}`}>
      <div className="plans-settlement-head">
        <h3>Settle {commitment.label}</h3>
        <button className="text-action" onClick={onClose} type="button">Close</button>
      </div>

      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {feedback && <p role="status">{feedback}</p>}
      {showIncomeHint && (
        <p>
          <small>
            An income posting cannot settle this — a reimbursement or returned principal is never
            counted as newly earned income. If the posting shows as income,{" "}
            <Link href={`/transactions?accountId=${commitment.accountId}`}>recategorise it</Link>{" "}
            as a refund or a transfer first.
          </small>
        </p>
      )}

      {hasOpenOccurrence && (
        <>
          <h4>Payment candidates</h4>
          {candidatesState === "loading" && <p role="status">Looking for candidates…</p>}
          {candidatesState === "error" && (
            <p className="inline-alert" role="alert">Payment candidates could not be loaded.</p>
          )}
          {candidatesState === "ready" && candidates && candidates.candidates.length === 0 && (
            <p>No eligible posting was found within 45 days of the due date.</p>
          )}
          {candidatesState === "ready" && candidates && candidates.candidates.length > 0 && (
            <ul className="plans-candidate-list">
              {candidates.candidates.map((candidate) => (
                <li key={candidate.transactionId}>
                  <span>
                    {candidate.bookedDate} · {moneyText(candidate.amount, candidate.currency, currencies)}
                    {candidate.description ? ` · ${candidate.description}` : ""}
                  </span>
                  <button
                    className="text-action"
                    disabled={busy}
                    onClick={() => setSelected(candidate)}
                    type="button"
                  >Select</button>
                </li>
              ))}
            </ul>
          )}
          {selected && (
            <div className="plans-candidate-confirm">
              <p>
                Link {selected.bookedDate} · {moneyText(selected.amount, selected.currency, currencies)}?
              </p>
              <button
                className="primary-action"
                disabled={busy}
                onClick={() => void recordPayment(true)}
                type="button"
              >Record full payment</button>
              <button
                className="text-action"
                disabled={busy}
                onClick={() => void recordPayment(false)}
                type="button"
              >Record part payment</button>
              <button
                className="text-action"
                disabled={busy}
                onClick={() => setSelected(undefined)}
                type="button"
              >Cancel</button>
            </div>
          )}

          <div className="plans-settlement-actions">
            <button className="text-action" disabled={busy} onClick={() => void markDone()} type="button">
              Mark done
            </button>
            {!showDismiss && (
              <button
                className="text-action"
                disabled={busy}
                onClick={() => setShowDismiss(true)}
                type="button"
              >Dismiss</button>
            )}
          </div>
          {showDismiss && (
            <div className="plans-dismiss-form">
              <label className="text-field">
                <span>Reason</span>
                <input
                  onChange={(e) => setDismissReason(e.target.value)}
                  value={dismissReason}
                />
              </label>
              <button
                className="primary-action"
                disabled={busy || !dismissReason.trim()}
                onClick={() => void dismiss()}
                type="button"
              >Confirm dismissal</button>
              <button className="text-action" onClick={() => setShowDismiss(false)} type="button">
                Cancel
              </button>
            </div>
          )}
        </>
      )}

      <h4>History</h4>
      {historyState === "loading" && <p role="status">Loading history…</p>}
      {historyState === "error" && (
        <p className="inline-alert" role="alert">History could not be loaded.</p>
      )}
      {historyState === "ready" && history && history.resolutions.length === 0 && (
        <p>No resolutions are recorded yet.</p>
      )}
      {historyState === "ready" && history && history.resolutions.length > 0 && (
        <ul className="plans-history-list">
          {history.resolutions.map((resolution) => (
            <li key={resolution.id}>
              <span>
                {resolution.dueDate} · {resolutionSentence(resolution)}
                {resolution.transaction && (
                  <>
                    {" · "}
                    {resolution.transaction.bookedDate}{" "}
                    {moneyText(resolution.transaction.amount, commitment.currency, currencies)}
                    {resolution.transaction.description ? ` · ${resolution.transaction.description}` : ""}
                    {!resolution.transaction.eligible && <small> · no longer eligible</small>}
                  </>
                )}
              </span>
              {resolution.undoable && (
                <button
                  className="text-action"
                  disabled={busy}
                  onClick={() => void undo(resolution.id)}
                  type="button"
                >Undo</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
