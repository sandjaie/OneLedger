"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { InvestmentEventForm } from "@/app/investments/investment-event-form";
import { WealthRefreshNotice } from "@/app/investments/wealth-refresh-notice";
import { LONG_REQUEST_TIMEOUT_MS, type apiFetch } from "@/lib/api/client";
import { dateLabel } from "@/lib/format/date";
import type {
  AccountRow,
  AcquisitionPosition,
  PortfolioPositions,
  PortfolioSummary,
  PriceStatus,
  RefreshOutcome,
} from "@/types/api";

type Props = {
  request: typeof apiFetch;
  accounts: Array<AccountRow>;
};

type LoadState = "loading" | "ready" | "error";

const REFRESH_INTERVAL_MS = 24 * 60 * 60 * 1000;

const XIRR_REASON_TEXT: Record<string, string> = {
  no_sign_change: "No sign change across the cash flows.",
  multiple_roots: "More than one rate solves the cash flows.",
  missing_terminal_value: "No current or realised value is recorded.",
  incomplete_history: "History includes a switch, transfer or split.",
  missing_price: "A dated price is missing.",
  blocked_action: "An unrecognized event blocks this calculation.",
  not_converged: "The calculation did not converge.",
};

function reasonText(reason: string | null) {
  if (!reason) return "";
  return XIRR_REASON_TEXT[reason] ?? reason.replaceAll("_", " ");
}

function isRefreshDue(status: PriceStatus) {
  if (!status.lastSuccessfulCheck) return true;
  return Date.now() - new Date(status.lastSuccessfulCheck).getTime() >= REFRESH_INTERVAL_MS;
}

function priceStatusLabel(position: AcquisitionPosition) {
  if (position.priceStatus === "missing") return "No dated price recorded.";
  if (position.priceStatus === "future_price_only") return "Only a future-dated price is recorded.";
  if (position.priceStatus === "stale") return `Price ${position.currency} ${position.price} · ${dateLabel(position.priceDate ?? "")} (stale)`;
  if (position.price !== null && position.priceDate !== null) {
    return `Price ${position.currency} ${position.price} · ${dateLabel(position.priceDate)}`;
  }
  return "";
}

function Metric({
  label,
  value,
  reason,
  asOf,
}: {
  label: string;
  value: string | null;
  reason?: string | null;
  asOf?: string;
}) {
  return (
    <article aria-label={label} className="portfolio-metric">
      <p className="section-label">{label}</p>
      {value !== null ? (
        <>
          <strong>{value}</strong>
          {asOf && <small>as of {dateLabel(asOf)}</small>}
        </>
      ) : (
        <p className="investment-state">{reasonText(reason ?? null) || "Unavailable."}</p>
      )}
    </article>
  );
}

export function PortfolioScreen({ request, accounts }: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [attempt, setAttempt] = useState(0);
  const [summary, setSummary] = useState<PortfolioSummary>();
  const [positions, setPositions] = useState<Array<AcquisitionPosition>>([]);
  const [showClosed, setShowClosed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const [refreshProblem, setRefreshProblem] = useState(false);
  const [eventNotice, setEventNotice] = useState("");

  useEffect(() => {
    let current = true;
    Promise.all([
      request<PortfolioSummary>("/portfolio/summary"),
      request<PortfolioPositions>("/portfolio/positions?includeClosed=true"),
    ])
      .then(([nextSummary, nextPositions]) => {
        if (!current) return;
        setSummary(nextSummary);
        setPositions(nextPositions.positions);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  async function refreshPrices() {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshMessage("");
    setRefreshProblem(false);
    try {
      const outcome = await request<RefreshOutcome>("/investment-prices/refresh", {
        method: "POST",
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      setRefreshMessage(
        outcome.outcome === "refreshed"
          ? "Prices refreshed from the official source."
          : outcome.outcome === "source_unavailable"
            ? "The price source is unavailable; cached prices are kept."
            : "Prices were already checked within the last 24 hours.",
      );
      setAttempt((value) => value + 1);
    } catch {
      setRefreshProblem(true);
    } finally {
      setRefreshing(false);
    }
  }

  const open = positions.filter((position) => !position.closed);
  const closed = positions.filter((position) => position.closed);
  const visible = showClosed ? positions : open;
  const due = summary ? isRefreshDue(summary.priceStatus) : false;

  return (
    <section className="workspace-content portfolio-screen" aria-labelledby="portfolio-title">
      <header className="investment-entry-header">
        <div>
          <p className="eyebrow">Owner portfolio</p>
          <h1 className="workspace-title" id="portfolio-title">Investments</h1>
        </div>
        <div className="portfolio-links">
          <Link className="text-action" href="/investments/entry">Record a purchase</Link>
          <Link className="text-action" href="/investments/import">Import a statement</Link>
        </div>
      </header>

      {state === "loading" && <p className="investment-state" role="status">Loading portfolio…</p>}
      {state === "error" && (
        <div className="investment-state" role="alert">
          <p>The portfolio could not be loaded.</p>
          <button className="text-action" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }} type="button">Try again</button>
        </div>
      )}

      {state === "ready" && summary && (
        <>
          <div className="portfolio-metrics" aria-label="Portfolio totals">
            <Metric asOf={summary.asOf} label="Value" value={`${summary.baseCurrency} ${summary.valueBase}`} />
            <Metric asOf={summary.asOf} label="Contributions" value={`${summary.baseCurrency} ${summary.contributionsBase}`} />
            <Metric asOf={summary.asOf} label="Gain" reason={summary.gainBase === null ? "one or more holdings withhold gain" : null} value={summary.gainBase === null ? null : `${summary.baseCurrency} ${summary.gainBase}`} />
            <Metric label="XIRR" reason={summary.xirrReason} value={summary.xirrPercent === null ? null : `${summary.xirrPercent}%`} />
          </div>
          {!summary.valueComplete && (
            <p className="investment-state">{summary.unvaluedCount} of {summary.valuedCount + summary.unvaluedCount} holdings are not valued and are excluded from Value.</p>
          )}

          <section aria-labelledby="price-status-title" className="portfolio-price-status">
            <div>
              <p className="section-label" id="price-status-title">Official prices</p>
              <p>
                {summary.priceStatus.mappedCount} mapped, {summary.priceStatus.unmappedCount} unmapped.
                {summary.priceStatus.latestPriceDate && ` Latest price ${dateLabel(summary.priceStatus.latestPriceDate)}.`}
                {summary.staleCount > 0 && ` ${summary.staleCount} holding${summary.staleCount === 1 ? "" : "s"} carry a stale price.`}
              </p>
            </div>
            {due ? (
              <button className="text-action" disabled={refreshing} onClick={() => void refreshPrices()} type="button">
                {refreshing ? "Checking source…" : "Check for new prices"}
              </button>
            ) : (
              <p className="investment-exact">Checked within the last 24 hours.</p>
            )}
            {refreshMessage && <p className="investment-feedback" role="status">{refreshMessage}</p>}
            {refreshProblem && <p className="inline-alert" role="alert">The price check could not be completed.</p>}
          </section>

          <section aria-labelledby="allocation-title" className="portfolio-allocation">
            <p className="section-label" id="allocation-title">Allocation</p>
            {!summary.allocationComplete && <p className="investment-state">Incomplete — one or more holdings are unvalued.</p>}
            <div className="portfolio-allocation-groups">
              <div aria-label="Allocation by asset class">
                <h3>By asset class</h3>
                {summary.allocationByAssetClass.length === 0 && <p className="investment-state">No valued holdings.</p>}
                <dl>
                  {summary.allocationByAssetClass.map((group) => (
                    <div key={group.label}>
                      <dt>{group.label}</dt>
                      <dd>{summary.baseCurrency} {group.valueBase}{group.ratio && ` · ratio ${group.ratio}`}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <div aria-label="Allocation by account">
                <h3>By account</h3>
                <dl>
                  {summary.allocationByAccount.map((group) => (
                    <div key={group.label}>
                      <dt>{group.label}</dt>
                      <dd>{summary.baseCurrency} {group.valueBase}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </div>
          </section>

          <section aria-labelledby="holdings-title" className="investment-holdings">
            <div className="investment-section-head">
              <div>
                <p className="section-label">Open positions</p>
                <h2 id="holdings-title">Holdings</h2>
              </div>
              {closed.length > 0 && (
                <label className="check-field">
                  <input checked={showClosed} onChange={(event) => setShowClosed(event.target.checked)} type="checkbox" />
                  <span>Show {closed.length} closed holding{closed.length === 1 ? "" : "s"}</span>
                </label>
              )}
            </div>
            {visible.length === 0 && <p className="investment-state">No holdings to show.</p>}
            {visible.length > 0 && (
              <div className="investment-holding-grid">
                {visible.map((position) => (
                  <Link className="investment-holding" href={`/investments/${position.holdingId}`} key={position.holdingId}>
                    <div>
                      <p className="section-label">{position.assetClass}{position.closed ? " · closed" : ""}</p>
                      <h3>{position.securityName}</h3>
                      <p className="investment-exact">{position.units} units</p>
                    </div>
                    <div className="investment-holding-value">
                      <small>Current value</small>
                      <strong>{position.currentValueNative === null ? "Unavailable" : `${position.currency} ${position.currentValueNative}`}</strong>
                      <p>{priceStatusLabel(position)}</p>
                      <p>{position.gainNative === null ? "Gain unavailable" : `Gain ${position.currency} ${position.gainNative}`}</p>
                      <p>{position.xirrPercent === null ? reasonText(position.xirrReason ?? null) : `XIRR ${position.xirrPercent}%`}</p>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </section>

          <section aria-labelledby="portfolio-record-event-title">
            <p className="section-label" id="portfolio-record-event-title">Record an event</p>
            {eventNotice && <WealthRefreshNotice message={eventNotice} />}
            <InvestmentEventForm
              accounts={accounts}
              onRecorded={() => {
                setEventNotice("Event recorded.");
                setAttempt((value) => value + 1);
              }}
              request={request}
            />
          </section>
        </>
      )}
    </section>
  );
}
