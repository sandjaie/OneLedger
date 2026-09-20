"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AllocationEvidenceChart } from "@/app/investments/allocation-evidence-chart";
import { InvestmentAnalysis } from "@/app/investments/investment-analysis";
import { ReviewQueue } from "@/app/investments/review-queue";
import { XIRR_REASON_TEXT } from "@/app/investments/return-reasons";
import { AttentionRow } from "@/app/ui/attention-row";
import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { MetricStrip } from "@/app/ui/metric-strip";
import { StatusLabel } from "@/app/ui/status-label";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { INVESTMENT_ACCOUNT_TYPES } from "@/lib/accounts/types";
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

export function PortfolioScreen({ request, accounts }: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [attempt, setAttempt] = useState(0);
  const [summary, setSummary] = useState<PortfolioSummary>();
  const [positions, setPositions] = useState<Array<AcquisitionPosition>>([]);
  const [showClosed, setShowClosed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const [refreshProblem, setRefreshProblem] = useState(false);

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
  const hasInvestmentAccount = accounts.some((account) => account.isActive && INVESTMENT_ACCOUNT_TYPES.has(account.accountType));

  function positionTone(position: AcquisitionPosition) {
    if (position.closed) return "neutral";
    if (position.priceStatus === "missing" || position.priceStatus === "future_price_only") return "attention";
    if (position.priceStatus === "stale") return "danger";
    return "success";
  }

  function positionStatus(position: AcquisitionPosition) {
    if (position.closed) return "Closed";
    if (position.priceStatus === "missing") return "Missing price";
    if (position.priceStatus === "future_price_only") return "Future price only";
    if (position.priceStatus === "stale") return "Stale price";
    return "Valued";
  }

  return (
    <section className="workspace-content portfolio-screen" aria-labelledby="portfolio-title">
      <div id="portfolio-title">
        <ConsolePageHeader
          actions={state === "ready" && positions.length > 0 ? <>
            <Link className={buttonVariants()} href="/investments/entry">Record event</Link>
            <Link className={buttonVariants({ variant: "outline" })} href="/investments/import">Import evidence</Link>
          </> : undefined}
          description="Valuation evidence, lifecycle state and return history without estimated gaps."
          title="Investments"
        />
      </div>

      {state === "loading" && <p className="investment-state" role="status">Loading portfolio…</p>}
      {state === "error" && (
        <div className="investment-state" role="alert">
          <p>The portfolio could not be loaded.</p>
          <Button variant="outline" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }} type="button">Try again</Button>
        </div>
      )}

      {state === "ready" && summary && (
        <>
          {positions.length === 0 && <section aria-label="Start your investments" className="console-empty-focus portfolio-start">
            <h2>Bring your investments into view</h2>
            <div className="portfolio-start-actions">
              <Link className={buttonVariants()} href="/investments/entry">Record event</Link>
              <Link className={buttonVariants({ variant: "outline" })} href="/investments/import">Import evidence</Link>
            </div>
            <p>Analysis needs a recorded holding and dated evidence before value, gain or return can be shown.
              {!hasInvestmentAccount && <> An investment account is required before recording a purchase. <Link href="/accounts">Add an account</Link>.</>}
            </p>
          </section>}

          {positions.length === 0 && <ReviewQueue quietEmpty request={request} />}
          {positions.length === 0 && <InvestmentAnalysis baseCurrency={summary.baseCurrency} positions={positions}
            quietEmpty refreshKey={attempt} request={request} />}

          {positions.length > 0 && <>

          <MetricStrip label="Portfolio totals" items={[
            {
              label: summary.valueComplete ? "Value" : "Valued portion",
              value: summary.valuedCount === 0 ? "Unavailable" : `${summary.baseCurrency} ${summary.valueBase}`,
              detail: summary.valuedCount === 0 ? "No holdings have a supported current value." : `As of ${dateLabel(summary.asOf)}`,
            },
            { label: "Contributions", value: `${summary.baseCurrency} ${summary.contributionsBase}`, detail: `As of ${dateLabel(summary.asOf)}` },
            {
              label: "Gain",
              value: summary.gainBase === null ? "Unavailable" : `${summary.baseCurrency} ${summary.gainBase}`,
              detail: summary.gainBase === null ? "One or more holdings do not have a supported gain." : `As of ${dateLabel(summary.asOf)}`,
            },
            {
              label: "XIRR",
              value: summary.xirrPercent === null ? "Unavailable" : `${summary.xirrPercent}%`,
              detail: summary.xirrPercent === null ? reasonText(summary.xirrReason) || "Return evidence is incomplete." : "Portfolio cash-flow return",
            },
          ]} />

          <section aria-label="Valuation confidence" className="portfolio-confidence">
            <div className="console-section-head"><h2>Valuation confidence</h2><span>Server evidence status</span></div>
            <dl className="console-figures portfolio-confidence-facts">
              <div><dt>Valued</dt><dd>{summary.valuedCount}</dd></div>
              <div><dt>Unvalued</dt><dd>{summary.unvaluedCount}</dd></div>
              <div><dt>Stale</dt><dd>{summary.staleCount}</dd></div>
              <div><dt>Closed history</dt><dd>{closed.length}</dd></div>
            </dl>
            {(!summary.valueComplete || summary.staleCount > 0) && <AttentionRow
              detail={`${summary.unvaluedCount} unvalued · ${summary.staleCount} stale. Missing or stale evidence remains explicit and excluded where required.`}
              title="Valuation gaps"
              tone="attention"
            />}
            <section aria-labelledby="price-status-title" className="portfolio-price-status">
              <div>
                <h3 id="price-status-title">Official prices</h3>
                <p>
                  {summary.priceStatus.mappedCount} mapped · {summary.priceStatus.unmappedCount} unmapped
                  {summary.priceStatus.latestPriceDate && ` · latest ${dateLabel(summary.priceStatus.latestPriceDate)}`}
                </p>
              </div>
              {due ? (
                <Button disabled={refreshing} onClick={() => void refreshPrices()} type="button" variant="outline">
                  {refreshing ? "Checking source…" : "Check for new prices"}
                </Button>
              ) : <StatusLabel tone="success">Checked within the last 24 hours.</StatusLabel>}
              {refreshMessage && <p className="investment-feedback" role="status">{refreshMessage}</p>}
              {refreshProblem && <p className="inline-alert" role="alert">The price check could not be completed.</p>}
            </section>
          </section>

          <section aria-labelledby="holdings-title" className="investment-holdings">
            <div className="console-section-head">
              <h2 id="holdings-title">Holdings</h2>
              {closed.length > 0 && (
                <label className="check-field">
                  <Checkbox checked={showClosed} onCheckedChange={(checked) => setShowClosed(checked === true)} />
                  <span>Show {closed.length} closed holding{closed.length === 1 ? "" : "s"}</span>
                </label>
              )}
            </div>
            {visible.length === 0 && <p className="investment-state">No holdings to show.</p>}
            {visible.length > 0 && <DataTableRegion label="Portfolio holdings table">
              <table className="console-table portfolio-holdings-table">
                <thead><tr><th>Holding</th><th className="console-number">Units</th><th>Price evidence</th><th>Status</th><th className="console-number">Current value</th><th className="console-number">Gain</th></tr></thead>
                <tbody>{visible.map((position) => <tr key={position.holdingId}>
                  <th scope="row"><div className="console-entity">
                    <span aria-hidden="true" className="console-entity-mark">INV</span>
                    <div><Link className="console-entity-title" href={`/investments/${position.holdingId}`}>{position.securityName}</Link>
                      <span className="console-entity-sub">{position.assetClass}{position.closed ? " · closed" : ""}</span></div>
                  </div></th>
                  <td className="console-number">{position.units}</td>
                  <td className="portfolio-price-evidence"><p>{priceStatusLabel(position)}</p></td>
                  <td><StatusLabel tone={positionTone(position)}>{positionStatus(position)}</StatusLabel></td>
                  <td className="console-number">{position.currentValueNative === null ? "Unavailable" : `${position.currency} ${position.currentValueNative}`}</td>
                  <td className="console-number">{position.gainNative === null ? "Gain unavailable" : `${position.currency} ${position.gainNative}`}</td>
                </tr>)}</tbody>
              </table>
            </DataTableRegion>}
          </section>

          <ReviewQueue request={request} />

          <InvestmentAnalysis baseCurrency={summary.baseCurrency} positions={positions}
            refreshKey={attempt} request={request} />

          <section aria-labelledby="allocation-title" className="portfolio-allocation">
            <div className="console-section-head"><h2 id="allocation-title">Allocation</h2><span>Valued holdings only</span></div>
            {!summary.allocationComplete && <AttentionRow detail="One or more holdings are unvalued; ratios cover only the valued evidence." title="Incomplete allocation" tone="attention" />}
            <AllocationEvidenceChart currency={summary.baseCurrency} groups={summary.allocationByAssetClass} />
            <details className="console-details investment-disclosure">
              <summary>Allocation by account</summary>
              <DataTableRegion label="Allocation by account table">
                <table aria-label="Allocation by account exact values" className="console-table portfolio-allocation-table">
                  <thead><tr><th scope="col">Account</th><th scope="col">Value</th><th scope="col">Ratio</th></tr></thead>
                  <tbody>{summary.allocationByAccount.map((group) => (
                    <tr key={group.label}><th scope="row">{group.label}</th><td>{summary.baseCurrency} {group.valueBase}</td><td>{group.ratio ?? "Not available"}</td></tr>
                  ))}</tbody>
                </table>
              </DataTableRegion>
            </details>
          </section>
          </>}
        </>
      )}
    </section>
  );
}
