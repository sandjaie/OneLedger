"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { CADENCE_TEXT, END_REASON_TEXT, STATUS_TEXT } from "@/app/recurring/recurring-screen";
import { ConfirmAction } from "@/app/ui/confirm-action";
import { RowActions } from "@/app/ui/row-actions";
import { SectionNav } from "@/app/ui/section-nav";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
      <RowActions label={`Manage ${row.label}`}>
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

  function subscriptionRecord(row: RecurringSeriesRow) {
    return (
      <li className="record-row subscription-record" key={row.id}>
        <div className="subscription-record-head">
          <Link href={`/subscriptions/${row.id}`}>{row.label}</Link>
          <Badge variant={row.status === "overdue" ? "destructive" : "secondary"}>
            {STATUS_TEXT[row.status] ?? row.status}
          </Badge>
          {row.needsName && <Badge variant="outline">Needs a name</Badge>}
        </div>
        <strong className="subscription-record-amount">
          {row.nativeAmount !== null && row.nativeCurrency !== null
            ? `${moneyText(row.nativeAmount, row.nativeCurrency, currencies)} ${perCadence(row.cadence, row.customIntervalDays)}`
            : "Amount appears after the next refresh"}
        </strong>
        <span className="subscription-record-date">
          {row.status === "ended"
            ? `ended ${row.lastPeriodEndedOn ?? row.lastSeenOn}${row.lastEndReason ? ` (${END_REASON_TEXT[row.lastEndReason] ?? row.lastEndReason})` : ""}`
            : row.nextExpectedOn ? `Next expected ${row.nextExpectedOn}` : `Last charged ${row.lastSeenOn}`}
        </span>
        {rowActions(row)}
      </li>
    );
  }

  return (
    <section className="workspace-content subscriptions-screen" aria-labelledby="subscriptions-title">
      <header className="page-head">
        <p className="eyebrow">Your commitments</p>
        <h1 className="workspace-title" id="subscriptions-title">Subscriptions</h1>
      </header>
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

          <section aria-label="Next renewal" className="subscription-next">
            <Card>
              <CardHeader><CardTitle>Next renewal</CardTitle></CardHeader>
              <CardContent>
                {summary.upcoming[0] ? (
                  <div className="subscription-next-facts">
                    <Link href={`/subscriptions/${summary.upcoming[0].seriesId}`}>{summary.upcoming[0].label}</Link>
                    <strong>{summary.upcoming[0].nextExpectedOn}</strong>
                    <span>{summary.upcoming[0].nativeAmount !== null && summary.upcoming[0].nativeCurrency !== null
                      ? moneyText(summary.upcoming[0].nativeAmount, summary.upcoming[0].nativeCurrency, currencies)
                      : "Amount appears after the next refresh"}</span>
                    {summary.upcoming[0].payingAccountName && <span>Paid from {summary.upcoming[0].payingAccountName}</span>}
                    <span>{summary.renewingIn30Days} renewing in the next 30 days</span>
                  </div>
                ) : <p>Nothing is expected to renew in the next 30 days.</p>}
              </CardContent>
            </Card>
          </section>

          <section aria-label="Subscription totals" className="overview-summary-grid">
            <article>
              <p className="section-label">Monthly equivalent</p>
              <p className="overview-summary-value">
                {moneyText(summary.monthlyBase, summary.baseCurrency, currencies)}
              </p>
            </article>
            <article>
              <p className="section-label">Annual spend</p>
              <p className="overview-summary-value">
                {moneyText(summary.annualBase, summary.baseCurrency, currencies)}
              </p>
            </article>
            <article>
              <p className="section-label">Current</p>
              <p className="overview-summary-value">{summary.currentCount}</p>
            </article>
          </section>

          <SectionNav activeId={view} items={[
            { id: "current", label: "Current" },
            { id: "upcoming", label: "Upcoming" },
            { id: "past", label: "Past" },
          ]} onSelect={(id) => setView(id as View)} />

          {view === "upcoming" && <section className="subscription-view" aria-label="Upcoming subscriptions">
            <h2>Upcoming</h2>
            {summary.upcoming.length === 0 && <p>Nothing is expected to renew in the next 30 days.</p>}
            {summary.upcoming.length > 0 && <ul className="recurring-confirmed-list">
              {summary.upcoming.map((row) => <li className="record-row subscription-record" key={row.seriesId}>
                <Link href={`/subscriptions/${row.seriesId}`}>{row.label}</Link>
                <strong>{row.nativeAmount !== null && row.nativeCurrency !== null
                  ? moneyText(row.nativeAmount, row.nativeCurrency, currencies)
                  : "Amount appears after the next refresh"}</strong>
                <span>Expected {row.nextExpectedOn}</span>
                {row.payingAccountName && <span>Paid from {row.payingAccountName}</span>}
              </li>)}
            </ul>}
          </section>}

          {view === "current" && <section className="subscription-view" aria-label="Current subscriptions">
            <div className="section-head">
              <h2>Current</h2>
              <Badge variant="outline">{summary.priceChangesIn90Days} price changes in 90 days</Badge>
            </div>
            {current.length === 0 && <p>No current subscription has been found yet.</p>}
            {current.length > 0 && <ul className="recurring-confirmed-list">{current.map(subscriptionRecord)}</ul>}
            <div className="subscription-by-card">
              <h3>By card</h3>
              {(!byAccount || byAccount.length === 0) && <p>No subscription is linked to a card yet.</p>}
              {byAccount && byAccount.length > 0 && <ul className="recurring-confirmed-list">
                {byAccount.map((row) => <li className="record-row" key={row.accountId}>
                  <strong>{row.accountName}{row.accountMasked ? ` (${row.accountMasked})` : ""}</strong>
                  <span>{row.count} {row.count === 1 ? "subscription" : "subscriptions"} · {row.labels.join(", ")}</span>
                  <span>{moneyText(row.monthlyBase, summary.baseCurrency, currencies)} / month</span>
                </li>)}
              </ul>}
            </div>
          </section>}

          {view === "past" && <section className="subscription-view" aria-label="Past subscriptions">
            <h2>Past</h2>
            {past.length === 0 && <p>No past subscription is recorded.</p>}
            {past.length > 0 && <ul className="recurring-confirmed-list">{past.map(subscriptionRecord)}</ul>}
          </section>}
        </>
      )}
    </section>
  );
}
