"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MoreHorizontalIcon } from "lucide-react";

import { ConfirmAction } from "@/app/ui/confirm-action";
import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { DataToolbar } from "@/app/ui/data-toolbar";
import { MetricStrip } from "@/app/ui/metric-strip";
import { RowActions } from "@/app/ui/row-actions";
import { SectionNav } from "@/app/ui/section-nav";
import { StatusLabel } from "@/app/ui/status-label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { errorText, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type { CurrencyRow, RecurringResponse, RecurringSeriesRow } from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";
type View = "active" | "ended";

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

function todayLocalIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

export function RecurringScreen({ currencies, request }: Props) {
  const [series, setSeries] = useState<Array<RecurringSeriesRow>>();
  const [state, setState] = useState<State>("loading");
  const [problem, setProblem] = useState<string>();
  const [view, setView] = useState<View>("active");

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
  const active = shown.filter((row) => row.status !== "ended");
  const ended = shown.filter((row) => row.status === "ended");
  const next = active.filter((row) => row.nextExpectedOn !== null)
    .reduce<RecurringSeriesRow | undefined>((earliest, row) =>
      !earliest || (row.nextExpectedOn && row.nextExpectedOn < earliest.nextExpectedOn!) ? row : earliest, undefined);

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

  function statusTone(row: RecurringSeriesRow) {
    return row.status === "overdue" ? "danger" : row.status === "ended" ? "neutral" : "success";
  }

  function paymentRow(row: RecurringSeriesRow) {
    return (
      <tr key={row.id}>
        <th scope="row">
          <div className="console-entity">
            <span aria-hidden="true" className="console-entity-mark">REC</span>
            <div>
              <span className="console-entity-title">{row.label}</span>
              <span className="console-entity-sub">{row.categoryName ?? "Not categorised"}</span>
              <Collapsible className="recurring-record-review">
                <CollapsibleTrigger>Review {row.label}</CollapsibleTrigger>
                <CollapsibleContent>
                  <dl>
                    <div><dt>Last seen</dt><dd>{row.lastSeenOn}</dd></div>
                    <div><dt>Evidence</dt><dd>{row.detectionMethod === "detected" ? "Detected from ledger charges" : row.detectionMethod}</dd></div>
                    <div><dt>Periods seen</dt><dd>{row.periodCount}</dd></div>
                    {row.resumed && <div><dt>History</dt><dd>Resumed</dd></div>}
                    {row.priceChangedFrom && row.priceChangedOn && row.nativeAmount !== null && row.nativeCurrency !== null &&
                      <div><dt>Price change</dt><dd>From {moneyText(row.priceChangedFrom, row.nativeCurrency, currencies)} to {moneyText(row.nativeAmount, row.nativeCurrency, currencies)} on {row.priceChangedOn}</dd></div>}
                  </dl>
                </CollapsibleContent>
              </Collapsible>
            </div>
          </div>
        </th>
        <td>{CADENCE_TEXT[row.cadence] ?? row.cadence}</td>
        <td className="recurring-payment-source">
          {row.payingAccountName
            ? `${row.payingAccountName}${row.payingAccountMasked ? ` (${row.payingAccountMasked})` : ""}`
            : "Not recorded"}
          {row.paymentChannelName && <span>{row.paymentChannelName}</span>}
        </td>
        <td><StatusLabel tone={statusTone(row)}>{STATUS_TEXT[row.status] ?? row.status}</StatusLabel></td>
        <td className="recurring-record-date">
          {row.status === "ended"
            ? `ended ${row.lastPeriodEndedOn ?? row.lastSeenOn}${row.lastEndReason ? ` (${END_REASON_TEXT[row.lastEndReason] ?? row.lastEndReason})` : ""}`
            : row.nextExpectedOn
              ? `${row.nextExpectedOn < todayLocalIso() || row.status === "overdue" ? "Overdue" : "Next expected"} ${row.nextExpectedOn}`
              : `Last seen ${row.lastSeenOn}`}
        </td>
        <td className="console-number recurring-record-amount">
          {row.nativeAmount !== null && row.nativeCurrency !== null
            ? moneyText(row.nativeAmount, row.nativeCurrency, currencies)
            : "Amount appears after the next refresh"}
        </td>
        <td className="console-row-actions">
          <RowActions
            floating
            label={`Manage ${row.label}`}
            triggerContent={<MoreHorizontalIcon aria-hidden="true" />}
          >
            <div className="plans-settlement-actions">
              <Button disabled={flipBusy === row.id} onClick={() => void flip(row)} type="button" variant="outline">
                {flipBusy === row.id ? "Saving…" : row.isSubscription ? "Not a subscription" : "A subscription"}
              </Button>
              {renamingId !== row.id && <Button onClick={() => startRename(row)} type="button" variant="outline">Rename</Button>}
              {row.isCurrent && <NativeSelect aria-label="Status" disabled={statusBusy === row.id}
                onChange={(e) => void setStatus(row, e.target.value)} value={row.lifecycle}>
                <NativeSelectOption value="active">Active</NativeSelectOption>
                <NativeSelectOption value="trial">Trial</NativeSelectOption>
                <NativeSelectOption value="paused">Paused</NativeSelectOption>
              </NativeSelect>}
              {cadenceEditingId !== row.id && <Button onClick={() => startCadence(row)} type="button" variant="outline">Cadence</Button>}
              {row.isCurrent ? (
                <ConfirmAction actionLabel="Confirm cancel" busy={cancelBusy === row.id}
                  message={`Cancel ${row.label}? This closes its current billing period.`}
                  onConfirm={() => cancel(row)} title="Cancel recurring payment" triggerLabel="Cancel" />
              ) : (
                <Button disabled={resumeBusy === row.id} onClick={() => void resume(row)} type="button" variant="outline">
                  {resumeBusy === row.id ? "Resuming…" : "Resume"}
                </Button>
              )}
              <ConfirmAction actionLabel="Confirm remove" busy={removeBusy === row.id}
                message={`Remove ${row.label}? This is not "not a subscription" — it stops tracking this payment, and it will not come back on its own.`}
                onConfirm={() => remove(row)} title="Remove recurring payment" triggerLabel="Remove" />
            </div>

            {cadenceEditingId === row.id && (
              <div className="recurring-dismiss-form">
                <label className="recurring-edit-field">
                  <span>Cadence</span>
                  <NativeSelect onChange={(e) => setCadenceValue(e.target.value)} value={cadenceValue}>
                    {Object.entries(CADENCE_TEXT).map(([value, text]) => (
                      <NativeSelectOption key={value} value={value}>{text}</NativeSelectOption>
                    ))}
                  </NativeSelect>
                </label>
                {cadenceValue === "custom" && <label className="recurring-edit-field">
                  <span>Days between charges</span>
                  <Input max={730} min={7} onChange={(e) => setCustomIntervalValue(e.target.valueAsNumber)}
                    type="number" value={customIntervalValue ?? ""} />
                </label>}
                {cadenceProblem && <Alert variant="destructive"><AlertDescription>{cadenceProblem}</AlertDescription></Alert>}
                <Button disabled={cadenceBusy || (cadenceValue === "custom" &&
                  (customIntervalValue === null || Number.isNaN(customIntervalValue)))}
                  onClick={() => void submitCadence(row.id)} type="button">
                  {cadenceBusy ? "Saving…" : "Save cadence"}
                </Button>
                <Button onClick={() => setCadenceEditingId(undefined)} type="button" variant="ghost">Cancel</Button>
              </div>
            )}

            {renamingId === row.id && <div className="recurring-dismiss-form">
              <label className="recurring-edit-field"><span>Label</span>
                <Input onChange={(e) => setRenameValue(e.target.value)} value={renameValue} />
              </label>
              {renameProblem && <Alert variant="destructive"><AlertDescription>{renameProblem}</AlertDescription></Alert>}
              <Button disabled={renameBusy || !renameValue.trim()} onClick={() => void submitRename(row.id)} type="button">
                {renameBusy ? "Saving…" : "Save name"}
              </Button>
              <Button onClick={() => setRenamingId(undefined)} type="button" variant="ghost">Cancel</Button>
            </div>}
          </RowActions>
        </td>
      </tr>
    );
  }

  return (
    <section className="workspace-content recurring-screen" aria-labelledby="recurring-title">
      <div id="recurring-title">
        <ConsolePageHeader
          description="Regular outgoings with cadence, payment source and lifecycle kept visible."
          title="Recurring payments"
        />
      </div>
      <Collapsible className="experience-disclosure">
        <CollapsibleTrigger>How this list works</CollapsibleTrigger>
        <CollapsibleContent><p>Nothing here needs confirming. Mark a payment as a <Link href="/subscriptions">subscription</Link> to move it there. Remove hides it from this list permanently.</p></CollapsibleContent>
      </Collapsible>

      {state === "loading" && <p className="loading-frame" role="status">Checking for recurring payments…</p>}
      {state === "error" && (
        <Alert variant="destructive"><AlertDescription>
          {problem} <Button onClick={() => void reload()} type="button" variant="outline">Try again</Button>
        </AlertDescription></Alert>
      )}

      {state === "ready" && series && (
        <>
          {[problem, flipProblem, removeProblem, statusProblem, cancelProblem, resumeProblem].filter(Boolean).map((message) => (
            <Alert key={message} variant="destructive"><AlertDescription>{message}</AlertDescription></Alert>
          ))}

          <section aria-label="Next payment" className="recurring-next console-lead">
            <div>
              <span>Next payment</span>
              {next ? <><strong>{next.label}</strong><time dateTime={next.nextExpectedOn!}>{next.nextExpectedOn}</time></> : <strong>{shown.length === 0
                ? "No recurring payment other than a subscription has been found yet."
                : "No next date is available for an active recurring payment."}</strong>}
            </div>
            {next && <div className="console-lead-facts">
              <strong>{next.nativeAmount !== null && next.nativeCurrency !== null
                ? moneyText(next.nativeAmount, next.nativeCurrency, currencies)
                : "Amount appears after the next refresh"}</strong>
              <span>{CADENCE_TEXT[next.cadence] ?? next.cadence}</span>
              <StatusLabel tone={next.nextExpectedOn! < todayLocalIso() || next.status === "overdue" ? "danger" : "success"}>
                {next.nextExpectedOn! < todayLocalIso() || next.status === "overdue" ? "Overdue" : "Expected"} {next.nextExpectedOn}
              </StatusLabel>
            </div>}
          </section>

          <MetricStrip label="Recurring payment totals" items={[
            { label: "Active", value: String(active.length), detail: "Non-subscription series" },
            { label: "Ended", value: String(ended.length), detail: "History retained" },
            { label: "Needs a name", value: String(shown.filter((row) => row.needsName).length), detail: "Owner review" },
          ]} />

          {shown.length > 0 && <DataToolbar
            label="Recurring lifecycle views"
            summary={`${shown.length} ${shown.length === 1 ? "payment" : "payments"}`}
          ><SectionNav activeId={view} items={[
              { id: "active", label: "Active" }, { id: "ended", label: "Ended" },
            ]} onSelect={(id) => setView(id as View)} /></DataToolbar>}

          {shown.length > 0 && <section className="recurring-view" aria-label={`${view === "active" ? "Active" : "Ended"} payments`}>
            <div className="console-section-head">
              <h2>{view === "active" ? "Active payments" : "Ended payments"}</h2>
              <span>{view === "active" ? "Cadence and next date" : "Lifecycle history"}</span>
            </div>
            {(view === "active" ? active : ended).length === 0 && <p>
              {view === "active" ? "No active recurring payment is recorded." : "No ended recurring payment is recorded."}
            </p>}
            {(view === "active" ? active : ended).length > 0 && (
              <DataTableRegion label={`${view === "active" ? "Active" : "Ended"} recurring payment table`}>
                <table className="console-table recurring-lifecycle-table">
                  <thead><tr><th>Payment</th><th>Cadence</th><th>Payment source</th><th>Status</th><th>{view === "active" ? "Next" : "Ended"}</th><th className="console-number">Amount</th><th><span className="sr-only">Actions</span></th></tr></thead>
                  <tbody>{(view === "active" ? active : ended).map(paymentRow)}</tbody>
                </table>
              </DataTableRegion>
            )}
          </section>}
        </>
      )}
    </section>
  );
}
