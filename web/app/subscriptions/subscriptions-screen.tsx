"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MoreHorizontalIcon } from "lucide-react";

import { CADENCE_TEXT, END_REASON_TEXT, STATUS_TEXT } from "@/app/recurring/recurring-screen";
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
import type {
  ByAccountResponse,
  ByAccountRow,
  CurrencyRow,
  RecurringResponse,
  RecurringSeriesRow,
  RecurringSummary,
} from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";
type View = "current" | "upcoming" | "past";

const PER_CADENCE_TEXT: Record<string, string> = {
  weekly: "a week",
  monthly: "a month",
  quarterly: "a quarter",
  semiannual: "every six months",
  annual: "a year",
};

/** "a month", "every 45 days" — the words after an amount, never "per monthly". */
export function perCadence(cadence: string, customIntervalDays: number | null): string {
  if (cadence === "custom" && customIntervalDays !== null) return `every ${customIntervalDays} days`;
  return PER_CADENCE_TEXT[cadence] ?? (CADENCE_TEXT[cadence] ?? cadence).toLowerCase();
}

export function SubscriptionsScreen({ currencies, request }: Props) {
  const [series, setSeries] = useState<Array<RecurringSeriesRow>>();
  const [summary, setSummary] = useState<RecurringSummary>();
  const [byAccount, setByAccount] = useState<Array<ByAccountRow>>();
  const [state, setState] = useState<State>("loading");
  const [problem, setProblem] = useState<string>();
  const [view, setView] = useState<View>("current");

  const [renamingId, setRenamingId] = useState<string>();
  const [renameValue, setRenameValue] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameProblem, setRenameProblem] = useState<string>();

  const [flipBusy, setFlipBusy] = useState<string>();
  const [flipProblem, setFlipProblem] = useState<string>();

  const [statusBusy, setStatusBusy] = useState<string>();
  const [statusProblem, setStatusProblem] = useState<string>();

  const [cancelBusy, setCancelBusy] = useState<string>();
  const [cancelProblem, setCancelProblem] = useState<string>();

  const [resumeBusy, setResumeBusy] = useState<string>();
  const [resumeProblem, setResumeProblem] = useState<string>();

  // Refresh runs exactly once, when the screen opens (contract: "runs POST /recurring/refresh
  // once on open") — never on every reload after a local action. `request` is Workspace's own
  // stable `useCallback`, so this effect never re-fires on an unrelated re-render.
  useEffect(() => {
    let current = true;
    request("/recurring/refresh", { method: "POST" })
      .catch(() => undefined) // a refresh hiccup should not hide what is already stored
      .then(() =>
        Promise.all([
          request<RecurringResponse>("/recurring"),
          request<RecurringSummary>("/recurring/summary"),
          request<ByAccountResponse>("/recurring/by-account"),
        ]),
      )
      .then(([seriesResult, summaryResult, byAccountResult]) => {
        if (!current) return;
        setSeries(seriesResult.series);
        setSummary(summaryResult);
        setByAccount(byAccountResult.accounts);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(errorText(error, "Subscriptions could not be loaded."));
        setState("error");
      });
    return () => {
      current = false;
    };
  }, [request]);

  async function reload() {
    try {
      const [seriesResult, summaryResult, byAccountResult] = await Promise.all([
        request<RecurringResponse>("/recurring"),
        request<RecurringSummary>("/recurring/summary"),
        request<ByAccountResponse>("/recurring/by-account"),
      ]);
      setSeries(seriesResult.series);
      setSummary(summaryResult);
      setByAccount(byAccountResult.accounts);
      setProblem(undefined);
      setState("ready");
    } catch (error) {
      setProblem(errorText(error, "Subscriptions could not be reloaded."));
    }
  }

  async function flipOff(row: RecurringSeriesRow) {
    setFlipBusy(row.id);
    setFlipProblem(undefined);
    try {
      await request(`/recurring/${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isSubscription: false }),
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

  const subscriptions = series?.filter((row) => row.isSubscription) ?? [];
  const current = subscriptions.filter((row) => row.isCurrent);
  const past = subscriptions.filter((row) => !row.isCurrent);

  function rowActions(row: RecurringSeriesRow) {
    return (
      <RowActions
        floating
        label={`Manage ${row.label}`}
        triggerContent={<MoreHorizontalIcon aria-hidden="true" />}
      >
        <div className="plans-settlement-actions">
          <Button disabled={flipBusy === row.id} onClick={() => void flipOff(row)} type="button" variant="outline">
            {flipBusy === row.id ? "Saving…" : "Not a subscription"}
          </Button>
          {renamingId !== row.id && (
            <Button onClick={() => startRename(row)} type="button" variant="outline">Rename</Button>
          )}
          {row.isCurrent && <NativeSelect aria-label="Status" disabled={statusBusy === row.id}
            onChange={(event) => void setStatus(row, event.target.value)} value={row.lifecycle}>
            <NativeSelectOption value="active">Active</NativeSelectOption>
            <NativeSelectOption value="trial">Trial</NativeSelectOption>
            <NativeSelectOption value="paused">Paused</NativeSelectOption>
          </NativeSelect>}
          {row.isCurrent ? (
            <ConfirmAction actionLabel="Confirm cancel" busy={cancelBusy === row.id}
              message={`Cancel ${row.label}? This closes its current billing period.`}
              onConfirm={() => cancel(row)} title="Cancel subscription" triggerLabel="Cancel" />
          ) : (
            <Button disabled={resumeBusy === row.id} onClick={() => void resume(row)} type="button" variant="outline">
              {resumeBusy === row.id ? "Resuming…" : "Resume"}
            </Button>
          )}
        </div>
        {renameForm(row)}
      </RowActions>
    );
  }

  function renameForm(row: RecurringSeriesRow) {
    if (renamingId !== row.id) return null;
    return (
      <div className="recurring-dismiss-form">
        <label className="subscription-edit-field">
          <span>Label</span>
          <Input onChange={(e) => setRenameValue(e.target.value)} value={renameValue} />
        </label>
        {renameProblem && <Alert variant="destructive"><AlertDescription>{renameProblem}</AlertDescription></Alert>}
        <Button
          disabled={renameBusy || !renameValue.trim()}
          onClick={() => void submitRename(row.id)}
          type="button"
        >
          {renameBusy ? "Saving…" : "Save name"}
        </Button>
        <Button onClick={() => setRenamingId(undefined)} type="button" variant="ghost">
          Cancel
        </Button>
      </div>
    );
  }

  function statusTone(row: RecurringSeriesRow) {
    return row.status === "overdue" ? "danger" : row.status === "ended" ? "neutral" : "success";
  }

  function subscriptionRecord(row: RecurringSeriesRow) {
    return (
      <tr key={row.id}>
        <th scope="row">
          <div className="console-entity">
            <span aria-hidden="true" className="console-entity-mark">SUB</span>
            <div>
              <Link className="console-entity-title" href={`/subscriptions/${row.id}`}>{row.label}</Link>
              <span className="console-entity-sub">{row.categoryName ?? "Uncategorized"}</span>
            </div>
          </div>
        </th>
        <td>{CADENCE_TEXT[row.cadence] ?? row.cadence}</td>
        <td className="subscription-source">
          {row.payingAccountName
            ? `${row.payingAccountName}${row.payingAccountMasked ? ` (${row.payingAccountMasked})` : ""}`
            : "Not recorded"}
          {row.paymentChannelName && <span>{row.paymentChannelName}</span>}
        </td>
        <td>
          <StatusLabel tone={statusTone(row)}>{STATUS_TEXT[row.status] ?? row.status}</StatusLabel>
          {row.needsName && <StatusLabel tone="attention">Needs a name</StatusLabel>}
        </td>
        <td>
          <span className="subscription-record-date">
            {row.status === "ended"
              ? `ended ${row.lastPeriodEndedOn ?? row.lastSeenOn}${row.lastEndReason ? ` (${END_REASON_TEXT[row.lastEndReason] ?? row.lastEndReason})` : ""}`
              : row.nextExpectedOn ? `Next expected ${row.nextExpectedOn}` : `Last charged ${row.lastSeenOn}`}
          </span>
        </td>
        <td className="console-number subscription-record-amount">
          {row.nativeAmount !== null && row.nativeCurrency !== null
            ? `${moneyText(row.nativeAmount, row.nativeCurrency, currencies)} ${perCadence(row.cadence, row.customIntervalDays)}`
            : "Amount appears after the next refresh"}
        </td>
        <td className="console-row-actions">{rowActions(row)}</td>
      </tr>
    );
  }

  return (
    <section className="workspace-content subscriptions-screen" aria-labelledby="subscriptions-title">
      <div id="subscriptions-title">
        <ConsolePageHeader
          description="Renewals, lifecycle and payment evidence in one compact register."
          title="Subscriptions"
        />
      </div>
      <Collapsible className="experience-disclosure">
        <CollapsibleTrigger>How subscriptions are identified</CollapsibleTrigger>
        <CollapsibleContent><p>Estimates come from the latest ledger charges. Choose &ldquo;not a subscription&rdquo; to move a row to Recurring.</p></CollapsibleContent>
      </Collapsible>

      {state === "loading" && <p className="loading-frame" role="status">Checking for subscriptions…</p>}
      {state === "error" && (
        <Alert variant="destructive"><AlertDescription>
          {problem} <Button onClick={() => void reload()} type="button" variant="outline">Try again</Button>
        </AlertDescription></Alert>
      )}

      {state === "ready" && summary && (
        <>
          {[problem, flipProblem, statusProblem, cancelProblem, resumeProblem].filter(Boolean).map((message) => (
            <Alert key={message} variant="destructive"><AlertDescription>{message}</AlertDescription></Alert>
          ))}

          <section aria-label="Next renewal" className="subscription-next console-lead">
            <div>
              <span>Next renewal</span>
              {summary.upcoming[0] ? (
                <>
                  <Link href={`/subscriptions/${summary.upcoming[0].seriesId}`}>{summary.upcoming[0].label}</Link>
                  <strong>{summary.upcoming[0].nextExpectedOn}</strong>
                </>
              ) : <strong>Nothing is expected to renew in the next 30 days.</strong>}
            </div>
            {summary.upcoming[0] && <div className="console-lead-facts">
              <strong>{summary.upcoming[0].nativeAmount !== null && summary.upcoming[0].nativeCurrency !== null
                ? moneyText(summary.upcoming[0].nativeAmount, summary.upcoming[0].nativeCurrency, currencies)
                : "Amount appears after the next refresh"}</strong>
              {summary.upcoming[0].payingAccountName && <span>Paid from {summary.upcoming[0].payingAccountName}</span>}
              <span>{summary.renewingIn30Days} renewing in the next 30 days</span>
            </div>}
          </section>

          <MetricStrip label="Subscription totals" items={[
            { label: "Monthly equivalent", value: moneyText(summary.monthlyBase, summary.baseCurrency, currencies), detail: "Server-normalized" },
            { label: "Annual spend", value: moneyText(summary.annualBase, summary.baseCurrency, currencies), detail: `${summary.priceChangesIn90Days} price changes in 90 days` },
            { label: "Current", value: String(summary.currentCount), detail: `${summary.renewingIn30Days} renew in 30 days` },
          ]} />

          <DataToolbar
            label="Subscription lifecycle views"
            summary={`${subscriptions.length} ${subscriptions.length === 1 ? "subscription" : "subscriptions"}`}
          >
            <SectionNav activeId={view} items={[
              { id: "current", label: "Current" },
              { id: "upcoming", label: "Upcoming" },
              { id: "past", label: "Past" },
            ]} onSelect={(id) => setView(id as View)} />
          </DataToolbar>

          {view === "upcoming" && <section className="subscription-view" aria-label="Upcoming subscriptions">
            <div className="console-section-head"><h2>Upcoming renewals</h2><span>Next 30 days</span></div>
            {summary.upcoming.length === 0 && <p>Nothing is expected to renew in the next 30 days.</p>}
            {summary.upcoming.length > 0 && <DataTableRegion label="Upcoming renewal table">
              <table className="console-table subscription-lifecycle-table">
                <thead><tr><th>Subscription</th><th>Expected</th><th>Payment source</th><th className="console-number">Amount</th></tr></thead>
                <tbody>{summary.upcoming.map((row) => <tr key={row.seriesId}>
                  <th scope="row"><Link href={`/subscriptions/${row.seriesId}`}>{row.label}</Link></th>
                  <td>{row.nextExpectedOn}</td>
                  <td>{row.payingAccountName ?? "Not recorded"}</td>
                  <td className="console-number">{row.nativeAmount !== null && row.nativeCurrency !== null
                    ? moneyText(row.nativeAmount, row.nativeCurrency, currencies)
                    : "Amount appears after the next refresh"}</td>
                </tr>)}</tbody>
              </table>
            </DataTableRegion>}
          </section>}

          {view === "current" && <section className="subscription-view" aria-label="Current subscriptions">
            <div className="console-section-head"><h2>Current subscriptions</h2><span>Sorted by source order</span></div>
            {current.length === 0 && <p>No current subscription has been found yet.</p>}
            {current.length > 0 && <DataTableRegion label="Current subscription table">
              <table className="console-table subscription-lifecycle-table">
                <thead><tr><th>Subscription</th><th>Cadence</th><th>Payment source</th><th>Status</th><th>Renewal</th><th className="console-number">Amount</th><th><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>{current.map(subscriptionRecord)}</tbody>
              </table>
            </DataTableRegion>}
            <div className="subscription-by-card">
              <div className="console-section-head"><h3>By card</h3><span>{byAccount?.length ?? 0} payment sources</span></div>
              {(!byAccount || byAccount.length === 0) && <p>No subscription is linked to a card yet.</p>}
              {byAccount && byAccount.length > 0 && <DataTableRegion label="Subscriptions by card">
                <table className="console-table subscription-account-table">
                  <thead><tr><th>Payment source</th><th>Subscriptions</th><th className="console-number">Monthly</th></tr></thead>
                  <tbody>{byAccount.map((row) => <tr key={row.accountId}>
                    <th scope="row">{row.accountName}{row.accountMasked ? ` (${row.accountMasked})` : ""}</th>
                    <td>{row.count} {row.count === 1 ? "subscription" : "subscriptions"} · {row.labels.join(", ")}</td>
                    <td className="console-number">{moneyText(row.monthlyBase, summary.baseCurrency, currencies)} / month</td>
                  </tr>)}</tbody>
                </table>
              </DataTableRegion>}
            </div>
          </section>}

          {view === "past" && <section className="subscription-view" aria-label="Past subscriptions">
            <div className="console-section-head"><h2>Past subscriptions</h2><span>Ended history retained</span></div>
            {past.length === 0 && <p>No past subscription is recorded.</p>}
            {past.length > 0 && <DataTableRegion label="Past subscription table">
              <table className="console-table subscription-lifecycle-table">
                <thead><tr><th>Subscription</th><th>Cadence</th><th>Payment source</th><th>Status</th><th>Ended</th><th className="console-number">Amount</th><th><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>{past.map(subscriptionRecord)}</tbody>
              </table>
            </DataTableRegion>}
          </section>}
        </>
      )}
    </section>
  );
}
