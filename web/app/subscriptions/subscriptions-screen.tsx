"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { CADENCE_TEXT, END_REASON_TEXT, STATUS_TEXT } from "@/app/recurring/recurring-screen";
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

  const subscriptions = series?.filter((row) => row.isSubscription) ?? [];
  const current = subscriptions.filter((row) => row.isCurrent);
  const past = subscriptions.filter((row) => !row.isCurrent);

  function rowActions(row: RecurringSeriesRow) {
    return (
      <div className="plans-settlement-actions">
        <button
          className="text-action"
          disabled={flipBusy === row.id}
          onClick={() => void flipOff(row)}
          type="button"
        >
          {flipBusy === row.id ? "Saving…" : "Not a subscription"}
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
      </div>
    );
  }

  function renameForm(row: RecurringSeriesRow) {
    if (renamingId !== row.id) return null;
    return (
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
    );
  }

  return (
    <section className="workspace-content" aria-labelledby="subscriptions-title">
      <p className="eyebrow">Subscriptions</p>
      <h1 className="workspace-title" id="subscriptions-title">Subscriptions</h1>
      <p className="lede">
        What the ledger says you pay for — estimates built from the latest charges. The one
        correction is &ldquo;not a subscription&rdquo;, which moves a row to Recurring.
      </p>

      {state === "loading" && <p role="status">Checking for subscriptions…</p>}
      {state === "error" && (
        <div role="alert">
          <p>{problem}</p>
          <button className="text-action" onClick={() => void reload()} type="button">
            Try again
          </button>
        </div>
      )}

      {state === "ready" && summary && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          {flipProblem && <p className="inline-alert" role="alert">{flipProblem}</p>}
          {statusProblem && <p className="inline-alert" role="alert">{statusProblem}</p>}
          {cancelProblem && <p className="inline-alert" role="alert">{cancelProblem}</p>}
          {resumeProblem && <p className="inline-alert" role="alert">{resumeProblem}</p>}

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
            <article>
              <p className="section-label">Renewing in 30 days</p>
              <p className="overview-summary-value">{summary.renewingIn30Days}</p>
            </article>
            <article>
              <p className="section-label">Price changes in 90 days</p>
              <p className="overview-summary-value">{summary.priceChangesIn90Days}</p>
            </article>
          </section>

          <h2>Upcoming</h2>
          {summary.upcoming.length === 0 && (
            <p>Nothing is expected to renew in the next 30 days.</p>
          )}
          {summary.upcoming.length > 0 && (
            <ul className="recurring-confirmed-list">
              {summary.upcoming.map((row) => (
                <li key={row.seriesId}>
                  <span>
                    {row.nextExpectedOn} ·{" "}
                    <Link href={`/subscriptions/${row.seriesId}`}>{row.label}</Link>
                  </span>
                  <span>
                    {row.nativeAmount !== null && row.nativeCurrency !== null
                      ? moneyText(row.nativeAmount, row.nativeCurrency, currencies)
                      : "Amount appears after the next refresh"}
                    {row.payingAccountName ? ` · ${row.payingAccountName}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <h2>Current</h2>
          {current.length === 0 && <p>No current subscription has been found yet.</p>}
          {current.length > 0 && (
            <ul className="recurring-confirmed-list">
              {current.map((row) => (
                <li key={row.id}>
                  <span>
                    <Link href={`/subscriptions/${row.id}`}>{row.label}</Link>
                    {row.needsName && <span className="plans-badge">Needs a name</span>}
                    {row.categoryName ? ` · ${row.categoryName}` : ""}
                  </span>
                  <div className="plans-badges">
                    <span className="plans-badge">{STATUS_TEXT[row.status] ?? row.status}</span>
                    {row.resumed && <span className="plans-badge">Resumed</span>}
                  </div>
                  <span>
                    {row.nativeAmount !== null && row.nativeCurrency !== null
                      ? `${moneyText(row.nativeAmount, row.nativeCurrency, currencies)} ${perCadence(
                          row.cadence,
                          row.customIntervalDays,
                        )}`
                      : "Amount appears after the next refresh"}
                    {row.annualAmount !== null && row.nativeCurrency !== null && (
                      <> · {moneyText(row.annualAmount, row.nativeCurrency, currencies)} a year</>
                    )}
                  </span>
                  <span>
                    {row.payingAccountName && (
                      <>
                        Paying: {row.payingAccountName}
                        {row.payingAccountMasked ? ` (${row.payingAccountMasked})` : ""}
                      </>
                    )}
                    {" "}· last charged {row.lastSeenOn}
                    {row.nextExpectedOn && ` · next expected ${row.nextExpectedOn}`}
                  </span>
                  {row.priceChangedFrom &&
                    row.priceChangedOn &&
                    row.nativeAmount !== null &&
                    row.nativeCurrency !== null && (
                      <span>
                        price changed from{" "}
                        {moneyText(row.priceChangedFrom, row.nativeCurrency, currencies)} to{" "}
                        {moneyText(row.nativeAmount, row.nativeCurrency, currencies)} on{" "}
                        {row.priceChangedOn}
                      </span>
                    )}
                  {rowActions(row)}
                  {renameForm(row)}
                </li>
              ))}
            </ul>
          )}

          <h2>Past</h2>
          {past.length === 0 && <p>No past subscription is recorded.</p>}
          {past.length > 0 && (
            <ul className="recurring-confirmed-list">
              {past.map((row) => (
                <li key={row.id}>
                  <span>
                    <Link href={`/subscriptions/${row.id}`}>{row.label}</Link>
                  </span>
                  <span>
                    {row.lastPeriodEndedOn && `ended ${row.lastPeriodEndedOn}`}
                    {row.lastEndReason &&
                      ` (${END_REASON_TEXT[row.lastEndReason] ?? row.lastEndReason})`}
                  </span>
                  {rowActions(row)}
                  {renameForm(row)}
                </li>
              ))}
            </ul>
          )}

          <h2>By card</h2>
          {(!byAccount || byAccount.length === 0) && (
            <p>No subscription is linked to a card yet.</p>
          )}
          {byAccount && byAccount.length > 0 && (
            <ul className="recurring-confirmed-list">
              {byAccount.map((row) => (
                <li key={row.accountId}>
                  <span>
                    {row.accountName}
                    {row.accountMasked ? ` (${row.accountMasked})` : ""}
                  </span>
                  <span>
                    {row.count} {row.count === 1 ? "subscription" : "subscriptions"} ·{" "}
                    {row.labels.join(", ")}
                  </span>
                  <span>{moneyText(row.monthlyBase, summary.baseCurrency, currencies)} / month</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
