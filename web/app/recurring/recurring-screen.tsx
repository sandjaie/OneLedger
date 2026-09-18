"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { errorText, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type { CurrencyRow, RecurringResponse, RecurringSeriesRow } from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";

// Exported so the Subscriptions screen, its detail page and the Transactions "Add as
// subscription" form show the same cadence/status/end-reason words rather than a second copy.
export const CADENCE_TEXT: Record<string, string> = {
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  semiannual: "Semiannual",
  annual: "Annual",
  custom: "Custom",
};

export const STATUS_TEXT: Record<string, string> = {
  active: "Active",
  overdue: "Overdue",
  ended: "Ended",
};

export const END_REASON_TEXT: Record<string, string> = {
  cancelled: "cancelled",
  lapsed: "stopped charging",
};

export function RecurringScreen({ currencies, request }: Props) {
  const [series, setSeries] = useState<Array<RecurringSeriesRow>>();
  const [state, setState] = useState<State>("loading");
  const [problem, setProblem] = useState<string>();

  const [renamingId, setRenamingId] = useState<string>();
  const [renameValue, setRenameValue] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameProblem, setRenameProblem] = useState<string>();

  const [flipBusy, setFlipBusy] = useState<string>();
  const [flipProblem, setFlipProblem] = useState<string>();

  const [removeBusy, setRemoveBusy] = useState<string>();
  const [removeProblem, setRemoveProblem] = useState<string>();

  const [statusBusy, setStatusBusy] = useState<string>();
  const [statusProblem, setStatusProblem] = useState<string>();

  const [cancelBusy, setCancelBusy] = useState<string>();
  const [cancelProblem, setCancelProblem] = useState<string>();

  const [resumeBusy, setResumeBusy] = useState<string>();
  const [resumeProblem, setResumeProblem] = useState<string>();

  const [cadenceEditingId, setCadenceEditingId] = useState<string>();
  const [cadenceValue, setCadenceValue] = useState("monthly");
  // A plain day count, not money — held as a number read straight from the input's own
  // `valueAsNumber`, never `Number()`/`parseInt()` (banned in this codebase for the same reason
  // money never goes through them: a string on the wire stays a string until it doesn't need to).
  const [customIntervalValue, setCustomIntervalValue] = useState<number | null>(null);
  const [cadenceBusy, setCadenceBusy] = useState(false);
  const [cadenceProblem, setCadenceProblem] = useState<string>();

  // Refresh runs exactly once, when the screen opens — never on every reload after a local
  // action, and never in a loop. `request` is Workspace's own stable `useCallback`, so this
  // effect's dependency array never re-fires on an unrelated re-render.
  useEffect(() => {
    let current = true;
    request("/recurring/refresh", { method: "POST" })
      .catch(() => undefined) // a refresh hiccup should not hide what is already stored
      .then(() => request<RecurringResponse>("/recurring"))
      .then((result) => {
        if (!current) return;
        setSeries(result.series);
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
  }, [request]);

  async function reload() {
    try {
      const result = await request<RecurringResponse>("/recurring");
      setSeries(result.series);
      setProblem(undefined);
      setState("ready");
    } catch (error) {
      setProblem(errorText(error, "Recurring payments could not be reloaded."));
    }
  }

  async function flip(row: RecurringSeriesRow) {
    setFlipBusy(row.id);
    setFlipProblem(undefined);
    try {
      await request(`/recurring/${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isSubscription: !row.isSubscription }),
      });
      await reload();
    } catch (error) {
      setFlipProblem(errorText(error, "This could not be saved."));
    } finally {
      setFlipBusy(undefined);
    }
  }

  function startRename(row: RecurringSeriesRow) {
    setRenamingId(row.id);
    setRenameValue(row.label);
    setRenameProblem(undefined);
  }

  async function submitRename(seriesId: string) {
    setRenameBusy(true);
    setRenameProblem(undefined);
    try {
      await request(`/recurring/${seriesId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: renameValue }),
      });
      setRenamingId(undefined);
      await reload();
    } catch (error) {
      setRenameProblem(errorText(error, "This name could not be saved."));
    } finally {
      setRenameBusy(false);
    }
  }

  async function remove(row: RecurringSeriesRow) {
    if (
      !window.confirm(
        `Remove ${row.label}? This is not "not a subscription" — it stops tracking this payment, and it will not come back on its own.`,
      )
    ) {
      return;
    }
    setRemoveBusy(row.id);
    setRemoveProblem(undefined);
    try {
      await request(`/recurring/${row.id}/remove`, { method: "POST" });
      await reload();
    } catch (error) {
      setRemoveProblem(errorText(error, "This could not be removed."));
    } finally {
      setRemoveBusy(undefined);
    }
  }

  async function setStatus(row: RecurringSeriesRow, lifecycle: string) {
    setStatusBusy(row.id);
    setStatusProblem(undefined);
    try {
      await request(`/recurring/${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lifecycle }),
      });
      await reload();
    } catch (error) {
      setStatusProblem(errorText(error, "This status could not be saved."));
    } finally {
      setStatusBusy(undefined);
    }
  }

  async function cancel(row: RecurringSeriesRow) {
    if (!window.confirm(`Cancel ${row.label}? This closes its current billing period.`)) {
      return;
    }
    setCancelBusy(row.id);
    setCancelProblem(undefined);
    try {
      await request(`/recurring/${row.id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      await reload();
    } catch (error) {
      setCancelProblem(errorText(error, "This could not be cancelled."));
    } finally {
      setCancelBusy(undefined);
    }
  }

  async function resume(row: RecurringSeriesRow) {
    setResumeBusy(row.id);
    setResumeProblem(undefined);
    try {
      await request(`/recurring/${row.id}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      await reload();
    } catch (error) {
      setResumeProblem(errorText(error, "This could not be resumed."));
    } finally {
      setResumeBusy(undefined);
    }
  }

  // Subscriptions have their own screen (Task 7 contract "Recurring keeps listing recurring
  // payments that are not subscriptions"); flipping the flag moves a row there without this
  // list re-numbering anything else on the page.
  const shown = series?.filter((row) => !row.isSubscription) ?? [];

  function startCadence(row: RecurringSeriesRow) {
    setCadenceEditingId(row.id);
    setCadenceValue(row.cadence);
    setCustomIntervalValue(row.customIntervalDays);
    setCadenceProblem(undefined);
  }

  async function submitCadence(seriesId: string) {
    setCadenceBusy(true);
    setCadenceProblem(undefined);
    try {
      await request(`/recurring/${seriesId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cadence: cadenceValue,
          customIntervalDays: cadenceValue === "custom" ? customIntervalValue : null,
        }),
      });
      setCadenceEditingId(undefined);
      await reload();
    } catch (error) {
      setCadenceProblem(errorText(error, "This cadence could not be saved."));
    } finally {
      setCadenceBusy(false);
    }
  }

  return (
    <section className="workspace-content recurring-screen" aria-labelledby="recurring-title">
      <p className="eyebrow">Regular outgoings</p>
      <h1 className="workspace-title" id="recurring-title">Recurring payments</h1>
      <p className="lede">Regular payments found in your ledger, with their cadence and latest charge.</p>
      <details className="experience-disclosure">
        <summary>How this list works</summary>
        <p>Nothing here needs confirming. Mark a payment as a <Link href="/subscriptions">subscription</Link> to move it there. Remove hides it from this list permanently.</p>
      </details>

      {state === "loading" && <p role="status">Checking for recurring payments…</p>}
      {state === "error" && (
        <div role="alert">
          <p>{problem}</p>
          <button className="text-action" onClick={() => void reload()} type="button">
            Try again
          </button>
        </div>
      )}

      {state === "ready" && series && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          {flipProblem && <p className="inline-alert" role="alert">{flipProblem}</p>}
          {removeProblem && <p className="inline-alert" role="alert">{removeProblem}</p>}
          {statusProblem && <p className="inline-alert" role="alert">{statusProblem}</p>}
          {cancelProblem && <p className="inline-alert" role="alert">{cancelProblem}</p>}
          {resumeProblem && <p className="inline-alert" role="alert">{resumeProblem}</p>}

          {shown.length === 0 && (
            <p>No recurring payment other than a subscription has been found yet.</p>
          )}
          {shown.length > 0 && (
            <ul className="recurring-confirmed-list">
              {shown.map((row) => (
                <li className="record-row" key={row.id}>
                  <span>
                    {row.label}
                    {row.needsName && <span className="plans-badge">Needs a name</span>}
                    {" "}· {CADENCE_TEXT[row.cadence] ?? row.cadence}
                    {row.categoryName ? ` · ${row.categoryName}` : ""}
                  </span>
                  <div className="plans-badges">
                    <span className="plans-badge">{STATUS_TEXT[row.status] ?? row.status}</span>
                    {row.isSubscription && <span className="plans-badge">Subscription</span>}
                    {row.resumed && <span className="plans-badge">Resumed</span>}
                  </div>
                  <span>
                    {row.nativeAmount !== null && row.nativeCurrency !== null
                      ? moneyText(row.nativeAmount, row.nativeCurrency, currencies)
                      : "Amount appears after the next refresh"}{" "}
                    · last seen {row.lastSeenOn}
                    {row.priceChangedFrom &&
                      row.priceChangedOn &&
                      row.nativeAmount !== null &&
                      row.nativeCurrency !== null && (
                        <>
                          {" "}· price changed from{" "}
                          {moneyText(row.priceChangedFrom, row.nativeCurrency, currencies)} to{" "}
                          {moneyText(row.nativeAmount, row.nativeCurrency, currencies)} on{" "}
                          {row.priceChangedOn}
                        </>
                      )}
                    {!row.isCurrent && row.lastPeriodEndedOn && (
                      <>
                        {" "}· ended {row.lastPeriodEndedOn}
                        {row.lastEndReason &&
                          ` (${END_REASON_TEXT[row.lastEndReason] ?? row.lastEndReason})`}
                      </>
                    )}
                  </span>
                  {(row.payingAccountName || row.paymentChannelName) && (
                    <span>
                      {row.payingAccountName && (
                        <>
                          Paying account: {row.payingAccountName}
                          {row.payingAccountMasked ? ` (${row.payingAccountMasked})` : ""}
                        </>
                      )}
                      {row.payingAccountName && row.paymentChannelName ? " · " : ""}
                      {row.paymentChannelName && <>Channel: {row.paymentChannelName}</>}
                    </span>
                  )}

                  <div className="plans-settlement-actions">
                    <button
                      className="text-action"
                      disabled={flipBusy === row.id}
                      onClick={() => void flip(row)}
                      type="button"
                    >
                      {flipBusy === row.id
                        ? "Saving…"
                        : row.isSubscription
                          ? "Not a subscription"
                          : "A subscription"}
                    </button>
                    {renamingId !== row.id && (
                      <button className="text-action" onClick={() => startRename(row)} type="button">
                        Rename
                      </button>
                    )}
                    <select
                      aria-label="Status"
                      disabled={statusBusy === row.id}
                      onChange={(e) => void setStatus(row, e.target.value)}
                      value={row.lifecycle}
                    >
                      <option value="active">Active</option>
                      <option value="trial">Trial</option>
                      <option value="paused">Paused</option>
                    </select>
                    {cadenceEditingId !== row.id && (
                      <button className="text-action" onClick={() => startCadence(row)} type="button">
                        Cadence
                      </button>
                    )}
                    {row.isCurrent ? (
                      <button
                        className="text-action"
                        disabled={cancelBusy === row.id}
                        onClick={() => void cancel(row)}
                        type="button"
                      >
                        {cancelBusy === row.id ? "Cancelling…" : "Cancel"}
                      </button>
                    ) : (
                      <button
                        className="text-action"
                        disabled={resumeBusy === row.id}
                        onClick={() => void resume(row)}
                        type="button"
                      >
                        {resumeBusy === row.id ? "Resuming…" : "Resume"}
                      </button>
                    )}
                    <button
                      className="text-action"
                      disabled={removeBusy === row.id}
                      onClick={() => void remove(row)}
                      type="button"
                    >
                      {removeBusy === row.id ? "Removing…" : "Remove"}
                    </button>
                  </div>

                  {cadenceEditingId === row.id && (
                    <div className="recurring-dismiss-form">
                      <label className="text-field">
                        <span>Cadence</span>
                        <select
                          onChange={(e) => setCadenceValue(e.target.value)}
                          value={cadenceValue}
                        >
                          <option value="weekly">Weekly</option>
                          <option value="monthly">Monthly</option>
                          <option value="quarterly">Quarterly</option>
                          <option value="semiannual">Semiannual</option>
                          <option value="annual">Annual</option>
                          <option value="custom">Custom</option>
                        </select>
                      </label>
                      {cadenceValue === "custom" && (
                        <label className="text-field">
                          <span>Days between charges</span>
                          <input
                            max={730}
                            min={7}
                            onChange={(e) => setCustomIntervalValue(e.target.valueAsNumber)}
                            type="number"
                            value={customIntervalValue ?? ""}
                          />
                        </label>
                      )}
                      {cadenceProblem && <p className="inline-alert" role="alert">{cadenceProblem}</p>}
                      <button
                        className="primary-action"
                        disabled={
                          cadenceBusy ||
                          (cadenceValue === "custom" &&
                            (customIntervalValue === null || Number.isNaN(customIntervalValue)))
                        }
                        onClick={() => void submitCadence(row.id)}
                        type="button"
                      >
                        {cadenceBusy ? "Saving…" : "Save cadence"}
                      </button>
                      <button
                        className="text-action"
                        onClick={() => setCadenceEditingId(undefined)}
                        type="button"
                      >
                        Cancel
                      </button>
                    </div>
                  )}

                  {renamingId === row.id && (
                    <div className="recurring-dismiss-form">
                      <label className="text-field">
                        <span>Label</span>
                        <input onChange={(e) => setRenameValue(e.target.value)} value={renameValue} />
                      </label>
                      {renameProblem && <p className="inline-alert" role="alert">{renameProblem}</p>}
                      <button
                        className="primary-action"
                        disabled={renameBusy || !renameValue.trim()}
                        onClick={() => void submitRename(row.id)}
                        type="button"
                      >
                        {renameBusy ? "Saving…" : "Save name"}
                      </button>
                      <button className="text-action" onClick={() => setRenamingId(undefined)} type="button">
                        Cancel
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
