"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MoreHorizontalIcon } from "lucide-react";

import { ConfirmAction } from "@/app/ui/confirm-action";
import { CADENCE_TEXT, END_REASON_TEXT, STATUS_TEXT } from "@/app/recurring/recurring-screen";
import { AttentionRow } from "@/app/ui/attention-row";
import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { MetricStrip } from "@/app/ui/metric-strip";
import { RowActions } from "@/app/ui/row-actions";
import { StatusLabel } from "@/app/ui/status-label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { ApiError, errorText, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import { perCadence } from "@/app/subscriptions/subscriptions-screen";
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

  const manageActions = detail ? (
    <RowActions
      floating
      label={`Manage ${detail.label}`}
      triggerContent={<MoreHorizontalIcon aria-hidden="true" />}
    >
      <div className="plans-settlement-actions">
        <Button disabled={flipBusy} onClick={() => void flipOff()} type="button" variant="outline">
          {flipBusy ? "Saving…" : "Not a subscription"}
        </Button>
        {!renaming && <Button onClick={startRename} type="button" variant="outline">Rename</Button>}
        {detail.isCurrent && <NativeSelect aria-label="Status" disabled={statusBusy}
          onChange={(e) => void setStatus(e.target.value)} value={detail.lifecycle}>
          <NativeSelectOption value="active">Active</NativeSelectOption>
          <NativeSelectOption value="trial">Trial</NativeSelectOption>
          <NativeSelectOption value="paused">Paused</NativeSelectOption>
        </NativeSelect>}
        <NativeSelect aria-label="Payment channel" disabled={channelBusy}
          onChange={(e) => void setChannel(e.target.value)} value={detail.paymentChannelId ?? ""}>
          <NativeSelectOption value="">No channel set</NativeSelectOption>
          {channels.map((channel) => <NativeSelectOption key={channel.id} value={channel.id}>{channel.name}</NativeSelectOption>)}
        </NativeSelect>
        {!cadenceEditing && <Button onClick={startCadence} type="button" variant="outline">Cadence</Button>}
        {detail.isCurrent ? (
          <ConfirmAction actionLabel="Confirm cancel" busy={cancelBusy}
            message={`Cancel ${detail.label}? This closes its current billing period.`}
            onConfirm={cancel} title="Cancel subscription" triggerLabel="Cancel" />
        ) : (
          <Button disabled={resumeBusy} onClick={() => void resume()} type="button" variant="outline">
            {resumeBusy ? "Resuming…" : "Resume"}
          </Button>
        )}
      </div>
    </RowActions>
  ) : null;

  return (
    <section className="workspace-content subscription-detail-screen" aria-labelledby="subscription-detail-title">
      <div id="subscription-detail-title">
        <ConsolePageHeader
          actions={<>
            <Link className={buttonVariants({ variant: "outline", size: "sm" })} href="/subscriptions">Back to Subscriptions</Link>
            {manageActions}
          </>}
          description="Billing evidence, lifecycle periods and price history."
          title={state === "ready" && detail ? detail.label : "Subscription"}
        />
      </div>

      {state === "loading" && <p className="investment-state" role="status">Loading subscription…</p>}
      {state === "not_found" && (
        <p className="investment-state" role="alert">This subscription could not be found.</p>
      )}
      {state === "error" && (
        <div className="investment-state" role="alert">
          <p>The subscription could not be loaded.</p>
          <Button
            variant="outline"
            onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}
            type="button"
          >
            Try again
          </Button>
        </div>
      )}

      {state === "ready" && detail && (
        <>
          <div className="subscription-detail-status">
            <StatusLabel tone={detail.status === "overdue" ? "danger" : detail.status === "ended" ? "neutral" : "success"}>{STATUS_TEXT[detail.status] ?? detail.status}</StatusLabel>
            {detail.needsName && <StatusLabel tone="attention">Needs a name</StatusLabel>}
          </div>
          {[flipProblem, statusProblem, channelProblem, cancelProblem, resumeProblem].filter(Boolean).map((message) => (
            <Alert key={message} variant="destructive"><AlertDescription>{message}</AlertDescription></Alert>
          ))}

          <MetricStrip label="Subscription facts" items={[
            { label: "Amount", value: detail.nativeAmount !== null && detail.nativeCurrency !== null
              ? `${moneyText(detail.nativeAmount, detail.nativeCurrency, currencies)} ${perCadence(detail.cadence, detail.customIntervalDays)}`
              : "Amount appears after the next refresh", detail: CADENCE_TEXT[detail.cadence] ?? detail.cadence },
            { label: "Next expected", value: detail.nextExpectedOn ?? "Unknown", detail: STATUS_TEXT[detail.status] ?? detail.status },
            { label: "Spent so far", value: moneyText(detail.spentSoFarBase, baseCurrency, currencies), detail: `Since ${detail.firstSeenOn}` },
            { label: "Last 12 months", value: moneyText(detail.last12MonthsBase, baseCurrency, currencies), detail: `${detail.periodCount} ${detail.periodCount === 1 ? "period" : "periods"}` },
          ]} />

          <dl className="console-figures subscription-context-facts" aria-label="Subscription context">
            <div><dt>Category</dt><dd>{detail.categoryName ?? "Uncategorized"}</dd></div>
            <div><dt>Paying account</dt><dd>{detail.payingAccountName
              ? `${detail.payingAccountName}${detail.payingAccountMasked ? ` (${detail.payingAccountMasked})` : ""}`
              : "Unknown"}</dd></div>
            <div><dt>Channel</dt><dd>{detail.paymentChannelName ?? "Not recorded"}</dd></div>
            <div><dt>Lifecycle</dt><dd>{STATUS_TEXT[detail.status] ?? detail.status}{detail.resumed ? " · Resumed" : ""}</dd></div>
          </dl>

          {detail.priceChangedFrom && detail.priceChangedOn && detail.nativeAmount !== null && detail.nativeCurrency !== null && (
            <AttentionRow detail={`From ${moneyText(detail.priceChangedFrom, detail.nativeCurrency, currencies)} to ${moneyText(detail.nativeAmount, detail.nativeCurrency, currencies)} on ${detail.priceChangedOn}`} title="Price changed" tone="attention" />
          )}

          {renaming && (
            <div className="recurring-dismiss-form">
              <label className="text-field">
                <span>Label</span>
                <Input onChange={(e) => setRenameValue(e.target.value)} value={renameValue} />
              </label>
              {renameProblem && <p className="inline-alert" role="alert">{renameProblem}</p>}
              <Button
                disabled={renameBusy || !renameValue.trim()}
                onClick={() => void submitRename()}
                type="button"
              >
                {renameBusy ? "Saving…" : "Save name"}
              </Button>
              <Button onClick={() => setRenaming(false)} type="button" variant="ghost">Cancel</Button>
            </div>
          )}

          {cadenceEditing && (
            <div className="recurring-dismiss-form">
              <label className="text-field">
                <span>Cadence</span>
                <NativeSelect onChange={(e) => setCadenceValue(e.target.value)} value={cadenceValue}>
                  {Object.entries(CADENCE_TEXT).map(([value, text]) => (
                    <NativeSelectOption key={value} value={value}>{text}</NativeSelectOption>
                  ))}
                </NativeSelect>
              </label>
              {cadenceValue === "custom" && (
                <label className="text-field">
                  <span>Days between charges</span>
                  <Input
                    max={730}
                    min={7}
                    onChange={(e) => setCustomIntervalValue(e.target.valueAsNumber)}
                    type="number"
                    value={customIntervalValue ?? ""}
                  />
                </label>
              )}
              {cadenceProblem && <p className="inline-alert" role="alert">{cadenceProblem}</p>}
              <Button
                disabled={
                  cadenceBusy ||
                  (cadenceValue === "custom" &&
                    (customIntervalValue === null || Number.isNaN(customIntervalValue)))
                }
                onClick={() => void submitCadence()}
                type="button"
              >
                {cadenceBusy ? "Saving…" : "Save cadence"}
              </Button>
              <Button onClick={() => setCadenceEditing(false)} type="button" variant="ghost">Cancel</Button>
            </div>
          )}

          <section aria-labelledby="subscription-timeline-title" className="console-section">
            <div className="console-section-head"><h2 id="subscription-timeline-title">Billing periods</h2><span>{detail.periods.length} recorded</span></div>
            <DataTableRegion label="Billing period timeline">
              <table className="console-table subscription-period-table">
                <thead><tr><th>Period</th><th>Status</th><th>Charges</th><th className="console-number">Spend</th><th>Ledger evidence</th></tr></thead>
                <tbody>{detail.periods.map((period) => <tr key={period.id}>
                  <th scope="row">{period.startedOn} – {period.endedOn ?? "now"}</th>
                  <td><StatusLabel tone={period.endedOn ? "neutral" : "success"}>{period.endedOn
                    ? (END_REASON_TEXT[period.endReason ?? ""] ?? period.endReason ?? "ended")
                    : `active${detail.resumed ? ", resumed" : ""}`}</StatusLabel></td>
                  <td>{period.chargeCount} {period.chargeCount === 1 ? "charge" : "charges"}</td>
                  <td className="console-number">{moneyText(period.spentBase, baseCurrency, currencies)}</td>
                  <td><ul className="subscription-charge-links">{(chargesByPeriod.get(period.id) ?? []).map((charge) => <li key={charge.transactionId}>
                    <Link href={`/transactions?dateFrom=${charge.observedOn}&dateTo=${charge.observedOn}`}>
                      {charge.observedOn} · {moneyText(charge.amount, charge.currency, currencies)} · {charge.accountName}
                    </Link>
                  </li>)}</ul></td>
                </tr>)}</tbody>
              </table>
            </DataTableRegion>
          </section>

          <section aria-labelledby="subscription-price-history-title" className="console-section">
            <div className="console-section-head"><h2 id="subscription-price-history-title">Price history</h2><span>Observed charges only</span></div>
            {detail.priceHistory.length === 0 && <p>No price history is recorded.</p>}
            {detail.priceHistory.length > 0 && (
              <ol className="subscription-price-timeline">
                {detail.priceHistory.map((entry, index) => (
                  <li key={`${entry.observedOn}-${index}`}>
                    <span>{entry.observedOn}</span>
                    <strong>{moneyText(entry.amount, entry.currency, currencies)}</strong>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </>
      )}
    </section>
  );
}
