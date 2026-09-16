"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { CADENCE_TEXT, END_REASON_TEXT, STATUS_TEXT } from "@/app/recurring/recurring-screen";
import { ApiError, errorText, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  CurrencyRow,
  PaymentChannelRow,
  RecurringSeriesDetail,
} from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  baseCurrency: string;
  request: typeof apiFetch;
  seriesId: string;
};

type LoadState = "loading" | "ready" | "error" | "not_found";

export function SubscriptionDetail({ currencies, baseCurrency, request, seriesId }: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [attempt, setAttempt] = useState(0);
  const [detail, setDetail] = useState<RecurringSeriesDetail>();
  const [channels, setChannels] = useState<Array<PaymentChannelRow>>([]);

  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameProblem, setRenameProblem] = useState<string>();

  const [flipBusy, setFlipBusy] = useState(false);
  const [flipProblem, setFlipProblem] = useState<string>();

  const [statusBusy, setStatusBusy] = useState(false);
  const [statusProblem, setStatusProblem] = useState<string>();

  const [channelBusy, setChannelBusy] = useState(false);
  const [channelProblem, setChannelProblem] = useState<string>();

  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelProblem, setCancelProblem] = useState<string>();

  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeProblem, setResumeProblem] = useState<string>();

  const [cadenceEditing, setCadenceEditing] = useState(false);
  const [cadenceValue, setCadenceValue] = useState("monthly");
  // A plain day count, not money — read straight from the input's own `valueAsNumber`, never
  // `Number()`/`parseInt()`, the same reason `recurring-screen.tsx` avoids them.
  const [customIntervalValue, setCustomIntervalValue] = useState<number | null>(null);
  const [cadenceBusy, setCadenceBusy] = useState(false);
  const [cadenceProblem, setCadenceProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    Promise.all([
      request<RecurringSeriesDetail>(`/recurring/${seriesId}`),
      request<{ channels: Array<PaymentChannelRow> }>("/payment-channels"),
    ])
      .then(([detailResult, channelsResult]) => {
        if (!current) return;
        setDetail(detailResult);
        setChannels(channelsResult.channels);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        setState(error instanceof ApiError && error.status === 404 ? "not_found" : "error");
      });
    return () => {
      current = false;
    };
  }, [seriesId, request, attempt]);

  async function reloadDetail() {
    const next = await request<RecurringSeriesDetail>(`/recurring/${seriesId}`);
    setDetail(next);
  }

  function startRename() {
    if (!detail) return;
    setRenaming(true);
    setRenameValue(detail.label);
    setRenameProblem(undefined);
  }

  async function submitRename() {
    setRenameBusy(true);
    setRenameProblem(undefined);
    try {
      await request(`/recurring/${seriesId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: renameValue }),
      });
      setRenaming(false);
      await reloadDetail();
    } catch (error) {
      setRenameProblem(errorText(error, "This name could not be saved."));
    } finally {
      setRenameBusy(false);
    }
  }

  async function flipOff() {
    setFlipBusy(true);
    setFlipProblem(undefined);
    try {
      await request(`/recurring/${seriesId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isSubscription: false }),
      });
      await reloadDetail();
    } catch (error) {
      setFlipProblem(errorText(error, "This could not be saved."));
    } finally {
      setFlipBusy(false);
    }
  }

  async function setStatus(lifecycle: string) {
    setStatusBusy(true);
    setStatusProblem(undefined);
    try {
      await request(`/recurring/${seriesId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lifecycle }),
      });
      await reloadDetail();
    } catch (error) {
      setStatusProblem(errorText(error, "This status could not be saved."));
    } finally {
      setStatusBusy(false);
    }
  }

  async function setChannel(paymentChannelId: string) {
    setChannelBusy(true);
    setChannelProblem(undefined);
    try {
      await request(`/recurring/${seriesId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paymentChannelId: paymentChannelId || null }),
      });
      await reloadDetail();
    } catch (error) {
      setChannelProblem(errorText(error, "This channel could not be saved."));
    } finally {
      setChannelBusy(false);
    }
  }

  async function cancel() {
    if (!detail) return;
    if (!window.confirm(`Cancel ${detail.label}? This closes its current billing period.`)) {
      return;
    }
    setCancelBusy(true);
    setCancelProblem(undefined);
    try {
      await request(`/recurring/${seriesId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      await reloadDetail();
    } catch (error) {
      setCancelProblem(errorText(error, "This could not be cancelled."));
    } finally {
      setCancelBusy(false);
    }
  }

  async function resume() {
    setResumeBusy(true);
    setResumeProblem(undefined);
    try {
      await request(`/recurring/${seriesId}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      await reloadDetail();
    } catch (error) {
      setResumeProblem(errorText(error, "This could not be resumed."));
    } finally {
      setResumeBusy(false);
    }
  }

  function startCadence() {
    if (!detail) return;
    setCadenceEditing(true);
    setCadenceValue(detail.cadence);
    setCustomIntervalValue(detail.customIntervalDays);
    setCadenceProblem(undefined);
  }

  async function submitCadence() {
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
      setCadenceEditing(false);
      await reloadDetail();
    } catch (error) {
      setCadenceProblem(errorText(error, "This cadence could not be saved."));
    } finally {
      setCadenceBusy(false);
    }
  }

  // Charges grouped by period, in the same order as the periods themselves (contract "the
  // charges grouped by period with links to the ledger"); a ledger link filters Transactions to
  // that charge's own date — the finest existing drill-through, since a charge carries no
  // account id, only its name.
  const chargesByPeriod = new Map<string, Array<RecurringSeriesDetail["charges"][number]>>();
  for (const charge of detail?.charges ?? []) {
    chargesByPeriod.set(charge.periodId, [...(chargesByPeriod.get(charge.periodId) ?? []), charge]);
  }

  return (
    <section className="workspace-content" aria-labelledby="subscription-detail-title">
      <header className="investment-entry-header">
        <div>
          <p className="eyebrow">Subscription</p>
          <h1 className="workspace-title" id="subscription-detail-title">
            {state === "ready" && detail ? detail.label : "Subscription"}
          </h1>
        </div>
        <Link className="text-action" href="/subscriptions">Back to Subscriptions</Link>
      </header>

      {state === "loading" && <p className="investment-state" role="status">Loading subscription…</p>}
      {state === "not_found" && (
        <p className="investment-state" role="alert">This subscription could not be found.</p>
      )}
      {state === "error" && (
        <div className="investment-state" role="alert">
          <p>The subscription could not be loaded.</p>
          <button
            className="text-action"
            onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}
            type="button"
          >
            Try again
          </button>
        </div>
      )}

      {state === "ready" && detail && (
        <>
          {detail.needsName && <p className="plans-badge">Needs a name</p>}
          {flipProblem && <p className="inline-alert" role="alert">{flipProblem}</p>}
          {statusProblem && <p className="inline-alert" role="alert">{statusProblem}</p>}
          {channelProblem && <p className="inline-alert" role="alert">{channelProblem}</p>}
          {cancelProblem && <p className="inline-alert" role="alert">{cancelProblem}</p>}
          {resumeProblem && <p className="inline-alert" role="alert">{resumeProblem}</p>}

          <dl className="portfolio-metrics" aria-label="Subscription facts">
            <div>
              <dt>Amount</dt>
              <dd>
                {detail.nativeAmount !== null && detail.nativeCurrency !== null
                  ? `${moneyText(detail.nativeAmount, detail.nativeCurrency, currencies)} per ${(
                      CADENCE_TEXT[detail.cadence] ?? detail.cadence
                    ).toLowerCase()}`
                  : "Amount appears after the next refresh"}
              </dd>
            </div>
            <div><dt>Category</dt><dd>{detail.categoryName ?? "Uncategorized"}</dd></div>
            <div><dt>Next expected</dt><dd>{detail.nextExpectedOn ?? "Unknown"}</dd></div>
            <div>
              <dt>Paying account</dt>
              <dd>
                {detail.payingAccountName
                  ? `${detail.payingAccountName}${
                      detail.payingAccountMasked ? ` (${detail.payingAccountMasked})` : ""
                    }`
                  : "Unknown"}
                {detail.paymentChannelName ? ` · ${detail.paymentChannelName}` : ""}
              </dd>
            </div>
            <div><dt>Started</dt><dd>{detail.firstSeenOn}</dd></div>
            <div><dt>Spent so far</dt><dd>{moneyText(detail.spentSoFarBase, baseCurrency, currencies)}</dd></div>
            <div><dt>Last 12 months</dt><dd>{moneyText(detail.last12MonthsBase, baseCurrency, currencies)}</dd></div>
            <div><dt>Status</dt><dd>{STATUS_TEXT[detail.status] ?? detail.status}{detail.resumed ? " · Resumed" : ""}</dd></div>
          </dl>

          {detail.priceChangedFrom && detail.priceChangedOn && detail.nativeAmount !== null && detail.nativeCurrency !== null && (
            <p className="inline-alert">
              Price changed from {moneyText(detail.priceChangedFrom, detail.nativeCurrency, currencies)} to{" "}
              {moneyText(detail.nativeAmount, detail.nativeCurrency, currencies)} on {detail.priceChangedOn}
            </p>
          )}

          <div className="plans-settlement-actions">
            <button className="text-action" disabled={flipBusy} onClick={() => void flipOff()} type="button">
              {flipBusy ? "Saving…" : "Not a subscription"}
            </button>
            {!renaming && (
              <button className="text-action" onClick={startRename} type="button">Rename</button>
            )}
            <select
              aria-label="Status"
              disabled={statusBusy}
              onChange={(e) => void setStatus(e.target.value)}
              value={detail.lifecycle}
            >
              <option value="active">Active</option>
              <option value="trial">Trial</option>
              <option value="paused">Paused</option>
            </select>
            <select
              aria-label="Payment channel"
              disabled={channelBusy}
              onChange={(e) => void setChannel(e.target.value)}
              value={detail.paymentChannelId ?? ""}
            >
              <option value="">No channel set</option>
              {channels.map((channel) => (
                <option key={channel.id} value={channel.id}>{channel.name}</option>
              ))}
            </select>
            {!cadenceEditing && (
              <button className="text-action" onClick={startCadence} type="button">Cadence</button>
            )}
            {detail.isCurrent ? (
              <button className="text-action" disabled={cancelBusy} onClick={() => void cancel()} type="button">
                {cancelBusy ? "Cancelling…" : "Cancel"}
              </button>
            ) : (
              <button className="text-action" disabled={resumeBusy} onClick={() => void resume()} type="button">
                {resumeBusy ? "Resuming…" : "Resume"}
              </button>
            )}
          </div>

          {renaming && (
            <div className="recurring-dismiss-form">
              <label className="text-field">
                <span>Label</span>
                <input onChange={(e) => setRenameValue(e.target.value)} value={renameValue} />
              </label>
              {renameProblem && <p className="inline-alert" role="alert">{renameProblem}</p>}
              <button
                className="primary-action"
                disabled={renameBusy || !renameValue.trim()}
                onClick={() => void submitRename()}
                type="button"
              >
                {renameBusy ? "Saving…" : "Save name"}
              </button>
              <button className="text-action" onClick={() => setRenaming(false)} type="button">Cancel</button>
            </div>
          )}

          {cadenceEditing && (
            <div className="recurring-dismiss-form">
              <label className="text-field">
                <span>Cadence</span>
                <select onChange={(e) => setCadenceValue(e.target.value)} value={cadenceValue}>
                  {Object.entries(CADENCE_TEXT).map(([value, text]) => (
                    <option key={value} value={value}>{text}</option>
                  ))}
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
                onClick={() => void submitCadence()}
                type="button"
              >
                {cadenceBusy ? "Saving…" : "Save cadence"}
              </button>
              <button className="text-action" onClick={() => setCadenceEditing(false)} type="button">Cancel</button>
            </div>
          )}

          <section aria-labelledby="subscription-timeline-title">
            <p className="section-label" id="subscription-timeline-title">Billing periods</p>
            <ul className="recurring-confirmed-list">
              {detail.periods.map((period) => (
                <li key={period.id}>
                  <span>
                    {period.startedOn} – {period.endedOn ?? "now"} ·{" "}
                    {period.endedOn
                      ? (END_REASON_TEXT[period.endReason ?? ""] ?? period.endReason ?? "ended")
                      : "active"}
                    {!period.endedOn && detail.resumed && ", resumed"}
                  </span>
                  <span>
                    {period.chargeCount} {period.chargeCount === 1 ? "charge" : "charges"} ·{" "}
                    {moneyText(period.spentBase, baseCurrency, currencies)}
                  </span>
                  <ul className="plans-history-list">
                    {(chargesByPeriod.get(period.id) ?? []).map((charge) => (
                      <li key={charge.transactionId}>
                        <Link href={`/transactions?dateFrom=${charge.observedOn}&dateTo=${charge.observedOn}`}>
                          {charge.observedOn} · {moneyText(charge.amount, charge.currency, currencies)} ·{" "}
                          {charge.accountName}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="subscription-price-history-title">
            <p className="section-label" id="subscription-price-history-title">Price history</p>
            {detail.priceHistory.length === 0 && <p>No price history is recorded.</p>}
            {detail.priceHistory.length > 0 && (
              <ul className="plans-history-list">
                {detail.priceHistory.map((entry, index) => (
                  <li key={`${entry.observedOn}-${index}`}>
                    {entry.observedOn} · {moneyText(entry.amount, entry.currency, currencies)}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </section>
  );
}
