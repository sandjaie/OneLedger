"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import Link from "next/link";
import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type { AccountRow, CurrencyRow, MonthlyReviewResponse } from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

export function MonthlyReview({ accounts, currencies, request }: Props) {
  // The month input's own upper bound — the server already refuses a future month (422), this
  // just keeps the owner from picking one.
  const currentMonth = new Date().toISOString().slice(0, 7);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [review, setReview] = useState<MonthlyReviewResponse>();
  // Undefined until the owner picks a month explicitly — the server names its own default
  // (last month) and the picker shows whatever it resolved to, without asking again for the
  // same month the response already named (`month` below).
  const [monthOverride, setMonthOverride] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  const month = monthOverride ?? review?.month.slice(0, 7);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  // The server's own sentence for a load failure (a future month is 422) — kept apart from
  // `problem`, which is only for a write (Mark complete / Reopen) failure.
  const [loadProblem, setLoadProblem] = useState<string>();
  const [newTitle, setNewTitle] = useState("");
  const [newDueDate, setNewDueDate] = useState("");
  const [newAccountId, setNewAccountId] = useState("");
  const [followUpKey, setFollowUpKey] = useState(() => crypto.randomUUID());
  const [followUpBusy, setFollowUpBusy] = useState(false);
  const [followUpFeedback, setFollowUpFeedback] = useState<string>();

  useEffect(() => {
    let current = true;
    const query = monthOverride ? `?month=${monthOverride}` : "";
    request<MonthlyReviewResponse>(`/monthly-review${query}`)
      .then((result) => {
        if (!current) return;
        setReview(result);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        setLoadProblem(errorText(error, "The monthly review could not be loaded."));
        setState("error");
      });
    return () => {
      current = false;
    };
  }, [monthOverride, attempt, request]);

  async function markComplete() {
    if (!month) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<MonthlyReviewResponse>(`/monthly-review/${month}/completion`, {
        method: "PUT",
      });
      setReview(result);
    } catch (error) {
      setProblem(errorText(error, "This review could not be marked complete."));
    } finally {
      setBusy(false);
    }
  }

  async function reopen() {
    if (!month) return;
    setBusy(true);
    setProblem(undefined);
    try {
      await request(`/monthly-review/${month}/completion`, { method: "DELETE" });
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This review could not be reopened."));
    } finally {
      setBusy(false);
    }
  }

  async function addFollowUp() {
    if (!newTitle.trim()) return;
    setFollowUpBusy(true);
    setFollowUpFeedback(undefined);
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
      setFollowUpFeedback("Follow-up added.");
      setAttempt((value) => value + 1);
    } catch (error) {
      setFollowUpFeedback(errorText(error, "This follow-up could not be added."));
    } finally {
      setFollowUpBusy(false);
    }
  }

  const activeAccounts = accounts.filter((account) => account.isActive);

  return (
    <section aria-labelledby="review-title" id="review">
      <h2 id="review-title">Monthly review</h2>
      <label className="month-field">
        <span>Month</span>
        <Input
          max={currentMonth}
          onChange={(e) => setMonthOverride(e.target.value)}
          type="month"
          value={month ?? ""}
        />
      </label>

      {state === "loading" && <p role="status">Loading the monthly review…</p>}
      {state === "error" && (
        <div role="alert">
          <p>{loadProblem}</p>
          <Button variant="outline" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</Button>
        </div>
      )}
      {state === "ready" && review && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          <ul className="plans-review-steps">
            <li>
              <Link href="/coverage">Coverage</Link>: {review.steps.coverage.gapCells} gap cell
              {review.steps.coverage.gapCells === 1 ? "" : "s"} across{" "}
              {review.steps.coverage.rowsWithGaps} row
              {review.steps.coverage.rowsWithGaps === 1 ? "" : "s"}
            </li>
            <li>
              <Link href="/transactions">Transfers</Link>: {review.steps.transfers.candidateCount}{" "}
              candidate{review.steps.transfers.candidateCount === 1 ? "" : "s"}
            </li>
            <li>
              <Link href="/review">Categories</Link>: {review.steps.classification.unclassifiedCount}{" "}
              unclassified
            </li>
            <li>
              Position:{" "}
              {review.steps.position.complete ? (
                <>
                  {moneyText(
                    review.steps.position.totalBase ?? "0",
                    review.steps.position.baseCurrency,
                    currencies,
                  )}{" "}
                  as of {review.steps.position.monthEnd}
                  {review.steps.position.previousComplete && (
                    <>
                      {" "}
                      · previous{" "}
                      {moneyText(
                        review.steps.position.previousTotalBase ?? "0",
                        review.steps.position.baseCurrency,
                        currencies,
                      )}
                    </>
                  )}
                  {review.steps.position.changeBase !== null && (
                    <>
                      {" "}
                      · change{" "}
                      {moneyText(
                        review.steps.position.changeBase,
                        review.steps.position.baseCurrency,
                        currencies,
                      )}
                    </>
                  )}
                </>
              ) : (
                <>
                  Not yet refreshed for this month. <Link href="/wealth">Refresh</Link>
                </>
              )}
            </li>
            <li>
              <Link href="/plans#actions">Commitments</Link>: {review.steps.commitments.dueNext30Days}{" "}
              due in the next 30 days · {review.steps.commitments.overdue} overdue ·{" "}
              {review.steps.actions.open} open action
              {review.steps.actions.open === 1 ? "" : "s"} · {review.steps.actions.snoozed} snoozed
            </li>
          </ul>

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
          {followUpFeedback && <p role="status">{followUpFeedback}</p>}
          <Button
            variant="default"
            disabled={followUpBusy || !newTitle.trim()}
            onClick={() => void addFollowUp()}
            type="button"
          >Add follow-up</Button>

          {review.completedAt ? (
            <p>
              Review complete since {review.completedAt.slice(0, 10)} · {review.steps.actions.open} action
              {review.steps.actions.open === 1 ? "" : "s"} remaining.{" "}
              <Button variant="outline" disabled={busy} onClick={() => void reopen()} type="button">
                Reopen review
              </Button>
            </p>
          ) : (
            <Button
              variant="default"
              disabled={busy}
              onClick={() => void markComplete()}
              type="button"
            >Mark review complete</Button>
          )}
        </>
      )}
    </section>
  );
}
