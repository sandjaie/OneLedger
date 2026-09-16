"use client";

import { useEffect, useState } from "react";

import { errorText, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  CandidateRow,
  CategoryRow,
  ConfirmedSeriesRow,
  CurrencyRow,
  IdentityOut,
  RecurringResponse,
} from "@/types/api";

type Props = {
  categories: Array<CategoryRow>;
  currencies: Array<CurrencyRow>;
  // Every recurring amount is the household base currency (V56: `currency` always mirrors
  // `household.base_currency`, the same convention `budget-panel.tsx` follows), so a candidate
  // row — which carries no `currency` field of its own — reads this one.
  baseCurrency: string;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";

const CADENCES = ["weekly", "monthly", "quarterly", "annual"] as const;

const CADENCE_TEXT: Record<string, string> = {
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  annual: "Annual",
};

const STATUS_TEXT: Record<string, string> = {
  active: "Active",
  price_changed: "Price changed",
  overdue: "Overdue",
};

function identityKey(identity: IdentityOut): string {
  return identity.merchantId ?? identity.matchKey ?? "";
}

type ConfirmFormState = {
  label: string;
  cadence: string;
  expectedAmountBase: string;
  categoryId: string;
};

function formFor(candidate: CandidateRow): ConfirmFormState {
  return {
    label: candidate.label,
    cadence: candidate.cadence,
    expectedAmountBase: candidate.typicalAmountBase,
    categoryId: candidate.categoryId ?? "",
  };
}

export function RecurringPanel({ categories, currencies, baseCurrency, request }: Props) {
  const activeCategories = categories.filter((category) => category.isActive);

  const [data, setData] = useState<RecurringResponse>();
  const [state, setState] = useState<State>("loading");
  const [attempt, setAttempt] = useState(0);
  const [problem, setProblem] = useState<string>();

  const [confirmingKey, setConfirmingKey] = useState<string>();
  const [confirmForm, setConfirmForm] = useState<ConfirmFormState>();
  const [confirmKey, setConfirmKey] = useState(() => crypto.randomUUID());
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [confirmProblem, setConfirmProblem] = useState<string>();

  const [dismissBusy, setDismissBusy] = useState<string>();

  const [endingId, setEndingId] = useState<string>();
  const [endReason, setEndReason] = useState("");
  const [endBusy, setEndBusy] = useState(false);
  const [endProblem, setEndProblem] = useState<string>();

  const [acceptBusy, setAcceptBusy] = useState<string>();
  const [acceptProblem, setAcceptProblem] = useState<string>();

  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string>();
  const [refreshProblem, setRefreshProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<RecurringResponse>("/recurring")
      .then((result) => {
        if (!current) return;
        setData(result);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(errorText(error, "Recurring payments could not be loaded."));
        setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  function startConfirm(candidate: CandidateRow) {
    setConfirmingKey(identityKey(candidate.identity));
    setConfirmForm(formFor(candidate));
    setConfirmProblem(undefined);
  }

  async function submitConfirm(candidate: CandidateRow) {
    if (!confirmForm) return;
    setConfirmBusy(true);
    setConfirmProblem(undefined);
    try {
      await request("/recurring/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotencyKey: confirmKey,
          identity: candidate.identity,
          label: confirmForm.label,
          cadence: confirmForm.cadence,
          expectedAmountBase: confirmForm.expectedAmountBase,
          categoryId: confirmForm.categoryId || null,
        }),
      });
      setConfirmingKey(undefined);
      setConfirmForm(undefined);
      setConfirmKey(crypto.randomUUID());
      setAttempt((value) => value + 1);
    } catch (error) {
      setConfirmProblem(errorText(error, "This candidate could not be confirmed."));
    } finally {
      setConfirmBusy(false);
    }
  }

  async function dismiss(candidate: CandidateRow) {
    const key = identityKey(candidate.identity);
    setDismissBusy(key);
    setProblem(undefined);
    try {
      await request("/recurring/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identity: candidate.identity }),
      });
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This candidate could not be dismissed."));
    } finally {
      setDismissBusy(undefined);
    }
  }

  async function acceptPrice(row: ConfirmedSeriesRow) {
    setAcceptBusy(row.id);
    setAcceptProblem(undefined);
    try {
      await request(`/recurring/${row.id}/accept-price`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expectedAmountBase: row.expectedAmountBase }),
      });
      setAttempt((value) => value + 1);
    } catch (error) {
      setAcceptProblem(errorText(error, "The new amount could not be accepted."));
    } finally {
      setAcceptBusy(undefined);
    }
  }

  async function submitEnd(seriesId: string) {
    setEndBusy(true);
    setEndProblem(undefined);
    try {
      await request(`/recurring/${seriesId}/end`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endedReason: endReason }),
      });
      setEndingId(undefined);
      setEndReason("");
      setAttempt((value) => value + 1);
    } catch (error) {
      setEndProblem(errorText(error, "This series could not be ended."));
    } finally {
      setEndBusy(false);
    }
  }

  async function refresh() {
    if (!window.confirm("Check the ledger for new payments matching your confirmed series?")) {
      return;
    }
    setRefreshBusy(true);
    setRefreshMessage(undefined);
    setRefreshProblem(undefined);
    try {
      const result = await request<{ seriesTouched: number; membersAdded: number }>(
        "/recurring/refresh",
        { method: "POST" },
      );
      setRefreshMessage(
        result.membersAdded === 0
          ? "No new matching payments were found."
          : `Added ${result.membersAdded} payment${result.membersAdded === 1 ? "" : "s"} across ${result.seriesTouched} series.`,
      );
      setAttempt((value) => value + 1);
    } catch (error) {
      setRefreshProblem(errorText(error, "Refresh could not run."));
    } finally {
      setRefreshBusy(false);
    }
  }

  return (
    <section aria-labelledby="recurring-panel-title" className="recurring-panel" id="recurring-panel">
      <h2 id="recurring-panel-title">Recurring</h2>
      <p className="lede">
        Discovery only proposes — nothing here posts to the ledger. Confirming records the
        payments already found as evidence; dismissing suppresses a candidate you do not
        recognize; refreshing checks for a later matching payment.
      </p>

      {state === "loading" && <p role="status">Loading recurring payments…</p>}
      {state === "error" && (
        <div role="alert">
          <p>{problem}</p>
          <button className="text-action" onClick={() => setAttempt((value) => value + 1)} type="button">
            Try again
          </button>
        </div>
      )}

      {state === "ready" && data && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}

          <div className="recurring-panel-actions">
            <button className="text-action" disabled={refreshBusy} onClick={() => void refresh()} type="button">
              {refreshBusy ? "Checking…" : "Refresh"}
            </button>
          </div>
          {refreshMessage && <p role="status">{refreshMessage}</p>}
          {refreshProblem && <p className="inline-alert" role="alert">{refreshProblem}</p>}

          <h3>Confirmed</h3>
          {data.confirmed.length === 0 && <p>No recurring payment is confirmed yet.</p>}
          {data.confirmed.length > 0 && (
            <ul className="recurring-confirmed-list">
              {data.confirmed.map((row) => (
                <li key={row.id}>
                  <span>
                    {row.label} · {CADENCE_TEXT[row.cadence] ?? row.cadence}
                    {row.categoryName ? ` · ${row.categoryName}` : ""}
                  </span>
                  <div className="plans-badges">
                    <span className="plans-badge">{STATUS_TEXT[row.status] ?? row.status}</span>
                  </div>
                  <span>
                    Expected {moneyText(row.expectedAmountBase, row.currency, currencies)} · last{" "}
                    {moneyText(row.lastAmountBase, row.currency, currencies)} on {row.lastSeenOn} · next
                    expected {row.nextExpectedOn} · {row.memberCount} payment
                    {row.memberCount === 1 ? "" : "s"} recorded
                  </span>
                  {row.status === "price_changed" && (
                    <div className="plans-settlement-actions">
                      <button
                        className="text-action"
                        disabled={acceptBusy === row.id}
                        onClick={() => void acceptPrice(row)}
                        type="button"
                      >
                        {acceptBusy === row.id ? "Accepting…" : "Accept new amount"}
                      </button>
                    </div>
                  )}
                  {row.status === "overdue" && endingId !== row.id && (
                    <div className="plans-settlement-actions">
                      <button
                        className="text-action"
                        onClick={() => { setEndingId(row.id); setEndReason(""); setEndProblem(undefined); }}
                        type="button"
                      >
                        End series
                      </button>
                    </div>
                  )}
                  {endingId === row.id && (
                    <div className="recurring-dismiss-form">
                      <label className="text-field">
                        <span>Reason</span>
                        <input onChange={(e) => setEndReason(e.target.value)} value={endReason} />
                      </label>
                      {endProblem && <p className="inline-alert" role="alert">{endProblem}</p>}
                      <button
                        className="primary-action"
                        disabled={endBusy || !endReason.trim()}
                        onClick={() => void submitEnd(row.id)}
                        type="button"
                      >
                        {endBusy ? "Ending…" : "Confirm end"}
                      </button>
                      <button className="text-action" onClick={() => setEndingId(undefined)} type="button">
                        Cancel
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {acceptProblem && <p className="inline-alert" role="alert">{acceptProblem}</p>}

          <h3>Candidates</h3>
          {data.candidates.length === 0 && <p>No repeated payment has been found yet.</p>}
          {data.candidates.length > 0 && (
            <ul className="plans-candidate-list">
              {data.candidates.map((candidate) => {
                const key = identityKey(candidate.identity);
                return (
                  <li key={key}>
                    <span>
                      {candidate.label} · {CADENCE_TEXT[candidate.cadence] ?? candidate.cadence} ·{" "}
                      {candidate.occurrences} occurrences · typical{" "}
                      {moneyText(candidate.typicalAmountBase, baseCurrency, currencies)} · last{" "}
                      {moneyText(candidate.lastAmountBase, baseCurrency, currencies)} on{" "}
                      {candidate.lastSeenOn}
                    </span>
                    {confirmingKey !== key && (
                      <div className="plans-settlement-actions">
                        <button className="text-action" onClick={() => startConfirm(candidate)} type="button">
                          Confirm
                        </button>
                        <button
                          className="text-action"
                          disabled={dismissBusy === key}
                          onClick={() => void dismiss(candidate)}
                          type="button"
                        >
                          {dismissBusy === key ? "Dismissing…" : "Dismiss"}
                        </button>
                      </div>
                    )}
                    {confirmingKey === key && confirmForm && (
                      <div className="recurring-confirm-form">
                        <label className="text-field">
                          <span>Label</span>
                          <input
                            onChange={(e) => setConfirmForm((f) => f && { ...f, label: e.target.value })}
                            value={confirmForm.label}
                          />
                        </label>
                        <label className="select-field">
                          <span>Cadence</span>
                          <select
                            onChange={(e) => setConfirmForm((f) => f && { ...f, cadence: e.target.value })}
                            value={confirmForm.cadence}
                          >
                            {CADENCES.map((cadence) => (
                              <option key={cadence} value={cadence}>{CADENCE_TEXT[cadence]}</option>
                            ))}
                          </select>
                        </label>
                        <label className="text-field">
                          <span>Expected amount ({baseCurrency})</span>
                          <input
                            inputMode="decimal"
                            onChange={(e) =>
                              setConfirmForm((f) => f && { ...f, expectedAmountBase: e.target.value })
                            }
                            value={confirmForm.expectedAmountBase}
                          />
                        </label>
                        <label className="select-field">
                          <span>Category</span>
                          <select
                            onChange={(e) => setConfirmForm((f) => f && { ...f, categoryId: e.target.value })}
                            value={confirmForm.categoryId}
                          >
                            <option value="">None</option>
                            {activeCategories.map((category) => (
                              <option key={category.id} value={category.id}>{category.name}</option>
                            ))}
                          </select>
                        </label>
                        {confirmProblem && <p className="inline-alert" role="alert">{confirmProblem}</p>}
                        <button
                          className="primary-action"
                          disabled={confirmBusy || !confirmForm.expectedAmountBase}
                          onClick={() => void submitConfirm(candidate)}
                          type="button"
                        >
                          {confirmBusy ? "Confirming…" : "Confirm this series"}
                        </button>
                        <button
                          className="text-action"
                          onClick={() => { setConfirmingKey(undefined); setConfirmForm(undefined); }}
                          type="button"
                        >
                          Cancel
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
