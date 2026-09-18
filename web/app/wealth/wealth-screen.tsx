"use client";

import Link from "next/link";
import { type CSSProperties, useEffect, useRef, useState } from "react";

import { errorText, LONG_REQUEST_TIMEOUT_MS, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  AttributionLeg,
  CurrencyRow,
  HouseholdCurrency,
  MonthAttributionResponse,
  MonthEndPosition,
  MonthPositionsResponse,
  MonthSummary,
  RefreshCounts,
  WealthHistoryResponse,
} from "@/types/api";

// The five position statuses `/wealth-history` computes (Phase 2.2 spec): a label for each, and
// where the owner would go to resolve it. `available` has nowhere to go — nothing is wrong.
const STATUS_LABEL: Record<string, string> = {
  available: "Available",
  missing_price: "Missing price",
  missing_rate: "Missing exchange rate",
  no_snapshot: "No valuation",
  before_opening: "Before opening",
};
const STATUS_HREF: Record<string, string> = {
  missing_price: "/investments",
  no_snapshot: "/accounts",
  missing_rate: "/settings",
};

function statusLabel(status: string) {
  return STATUS_LABEL[status] ?? status.replaceAll("_", " ");
}

/** Every incomplete month's non-zero status counts, as one short line — e.g. "Missing price: 2,
 * No valuation: 1" — so the owner sees why without opening the month. */
function statusCountsLine(counts: MonthSummary["statusCounts"]) {
  return Object.entries(counts)
    .filter(([status, count]) => count > 0 && status !== "available")
    .map(([status, count]) => `${statusLabel(status)}: ${count}`)
    .join(", ");
}

type Props = {
  // The reporting currency chosen in the masthead — the month table and the position values
  // both follow it, picked by name from whichever of base/secondary the response names.
  currency: HouseholdCurrency;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";
type MonthState = "idle" | "loading" | "ready" | "error";

function monthTotal(month: MonthSummary, history: WealthHistoryResponse, currency: HouseholdCurrency) {
  return currency.code === history.baseCurrency ? month.totalBase : month.totalSecondary;
}

function positionValue(
  position: MonthEndPosition,
  month: MonthPositionsResponse,
  currency: HouseholdCurrency,
) {
  return currency.code === month.baseCurrency ? position.valueBase : position.valueSecondary;
}

function attributionLeg(body: MonthAttributionResponse, currency: HouseholdCurrency): AttributionLeg {
  return currency.code === body.baseCurrency ? body.base : body.secondary;
}

// Spec "Attribution for one month": the waterfall's own line order, residual last and always
// shown. `null` is `unavailable`'s own contract, not a formatting choice — the row still renders.
const WATERFALL_LINES: Array<[keyof AttributionLeg, string]> = [
  ["retainedIncome", "Retained income"],
  ["investmentIncome", "Investment income"],
  ["internalMovementsAndCosts", "Internal movements and costs"],
  ["otherExternalChanges", "Other external changes"],
  ["marketMovement", "Market movement"],
  ["assetRevaluation", "Asset revaluation"],
  ["fxTranslation", "FX translation"],
  ["residual", "Residual"],
];

export function WealthScreen({ currency, currencies, request }: Props) {
  const [history, setHistory] = useState<WealthHistoryResponse>();
  const [state, setState] = useState<State>("loading");
  const [attempt, setAttempt] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [refreshCounts, setRefreshCounts] = useState<RefreshCounts>();
  const [selected, setSelected] = useState<string>();
  const [month, setMonth] = useState<MonthPositionsResponse>();
  const [monthState, setMonthState] = useState<MonthState>("idle");
  const [waterfall, setWaterfall] = useState<MonthAttributionResponse>();
  const [waterfallState, setWaterfallState] = useState<MonthState>("idle");
  // OL-007 (review repair round): a token per selectMonth call, so a slow, now-superseded
  // response (the owner clicked a second month before the first one's positions/attribution
  // came back) is dropped instead of clobbering what's now on screen.
  const selectionRef = useRef(0);

  useEffect(() => {
    let current = true;
    request<WealthHistoryResponse>("/wealth-history")
      .then((loaded) => {
        if (!current) return;
        setHistory(loaded);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  async function refresh() {
    setRefreshing(true);
    setProblem(undefined);
    setRefreshCounts(undefined);
    try {
      const counts = await request<RefreshCounts>("/wealth-history/refresh", {
        method: "POST",
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      setRefreshCounts(counts);
      setAttempt((value) => value + 1);
      // The month table reloads from the `attempt` effect above; a month already open on screen
      // would otherwise keep showing its pre-refresh positions until the owner reselects it.
      if (selected) selectMonth(selected);
    } catch (error) {
      setProblem(errorText(error, "History could not be refreshed."));
    } finally {
      setRefreshing(false);
    }
  }

  async function loadWaterfall(monthEnd: string, token: number) {
    setWaterfallState("loading");
    try {
      const loaded = await request<MonthAttributionResponse>(
        `/wealth-history/${encodeURIComponent(monthEnd)}/attribution`,
      );
      if (selectionRef.current !== token) return; // a later selectMonth superseded this one
      setWaterfall(loaded);
      setWaterfallState("ready");
    } catch {
      if (selectionRef.current === token) setWaterfallState("error");
    }
  }

  function selectMonth(monthEnd: string) {
    const token = ++selectionRef.current;
    setSelected(monthEnd);
    setMonthState("loading");
    request<MonthPositionsResponse>(`/wealth-history/${encodeURIComponent(monthEnd)}`)
      .then((loaded) => {
        if (selectionRef.current !== token) return;
        setMonth(loaded);
        setMonthState("ready");
      })
      .catch(() => {
        if (selectionRef.current === token) setMonthState("error");
      });

    void loadWaterfall(monthEnd, token);
  }

  return (
    <section aria-labelledby="wealth-title" className="workspace-content wealth-screen">
      <header className="page-head">
        <h1 className="workspace-title" id="wealth-title">Wealth</h1>
        <p>
          Month-end net worth from the history anchor, with each account&rsquo;s evidence date
          and translation rate stored so a past figure is reproducible.
        </p>
        <button
          className="primary-action"
          disabled={refreshing}
          onClick={() => void refresh()}
          type="button"
        >{refreshing ? "Refreshing…" : "Refresh history"}</button>
        {refreshCounts && (
          <p className="page-status" role="status">
            Refreshed {refreshCounts.monthsComputed} {refreshCounts.monthsComputed === 1 ? "month" : "months"},{" "}
            {refreshCounts.positionsComputed} {refreshCounts.positionsComputed === 1 ? "position" : "positions"}.
          </p>
        )}
        {problem && <p className="page-status" role="alert">{problem}</p>}
        {state === "ready" && history && history.months.length > 0 && (
          <p>Last refreshed {new Date(history.months.reduce((latest, row) => (row.computedAt > latest ? row.computedAt : latest), history.months[0].computedAt)).toLocaleString()}</p>
        )}
      </header>

      {state === "loading" && <p className="loading-frame" role="status">Loading wealth history…</p>}
      {state === "error" && (
        <div className="page-status" role="alert">
          <p>Wealth history could not be loaded.</p>
          <button
            className="text-action"
            onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}
            type="button"
          >Try again</button>
        </div>
      )}

      {state === "ready" && history && (
        <>
          {history.months.length > 0 && (
            <p className="lede">The chart shows month-end net worth in {history.baseCurrency}.</p>
          )}
          {history.months.length > 0 && (
            <div
              aria-label={`Month-end net worth in ${history.baseCurrency}, as a bar chart — the table below states each figure`}
              className="data-chart data-chart--wealth"
              role="img"
            >
              {history.months.map((row) => (
                <div className="data-chart-column" key={row.monthEnd}>
                  <div className="data-chart-track">
                    {row.chartRatio !== null && (
                      <div
                        className="data-chart-bar"
                        style={{ "--ratio": row.chartRatio } as CSSProperties}
                      />
                    )}
                  </div>
                  <span className="data-chart-label">{row.monthEnd.slice(2, 7)}</span>
                </div>
              ))}
            </div>
          )}

          <div className="wealth-table-wrap">
            <table className="wealth-table">
              <caption>Month-end net worth, in {currency.code}</caption>
              <thead>
                <tr>
                  <th scope="col">Month</th>
                  <th scope="col">Net worth</th>
                  <th scope="col">Coverage</th>
                </tr>
              </thead>
              <tbody>
                {history.months.length === 0 && (
                  <tr><td colSpan={3}>No history yet. Refresh to compute it.</td></tr>
                )}
                {history.months.map((row) => {
                  const total = monthTotal(row, history, currency);
                  return (
                    <tr key={row.monthEnd}>
                      <th scope="row">
                        <button
                          aria-current={selected === row.monthEnd ? "true" : undefined}
                          className="text-action"
                          onClick={() => selectMonth(row.monthEnd)}
                          type="button"
                        >{row.monthEnd}</button>
                      </th>
                      <td>
                        {total === null
                          ? "Unavailable — incomplete"
                          : moneyText(total, currency.code, currencies)}
                      </td>
                      <td>
                        {row.complete
                          ? "Complete"
                          : (
                            <>
                              Incomplete month
                              {statusCountsLine(row.statusCounts) && (
                                <><br /><small>{statusCountsLine(row.statusCounts)}</small></>
                              )}
                            </>
                          )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {selected && (
            <section aria-labelledby="wealth-month-title" className="wealth-month">
              <h2 id="wealth-month-title">{selected} positions</h2>
              {monthState === "loading" && <p role="status">Loading {selected}&rsquo;s positions…</p>}
              {monthState === "error" && (
                <p className="inline-alert" role="alert">
                  This month&rsquo;s positions could not be loaded.{" "}
                  <button className="text-action" onClick={() => selectMonth(selected)} type="button">
                    Try again
                  </button>
                </p>
              )}
              {monthState === "ready" && month && (
                <div className="wealth-positions-wrap">
                  <table className="wealth-positions">
                    <caption>{selected}, one row per account or holding</caption>
                    <thead>
                      <tr>
                        <th scope="col">Account</th>
                        <th scope="col">Security</th>
                        <th scope="col">Mode</th>
                        <th scope="col">Native value</th>
                        <th scope="col">Evidence date</th>
                        <th scope="col">Value ({currency.code})</th>
                        <th scope="col">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {month.positions.length === 0 && (
                        <tr><td colSpan={7}>No counted positions for {selected}.</td></tr>
                      )}
                      {month.positions.map((position) => {
                        const value = positionValue(position, month, currency);
                        return (
                          <tr key={`${position.accountId}:${position.holdingId ?? ""}`}>
                            <th scope="row">{position.accountName}</th>
                            <td>{position.securityName ?? "—"}</td>
                            <td>{position.valuationMode}</td>
                            <td>
                              {position.nativeValue === null
                                ? "—"
                                : moneyText(position.nativeValue, position.currency, currencies)}
                            </td>
                            <td>{position.evidenceDate ?? "—"}</td>
                            <td>{value === null ? "—" : moneyText(value, currency.code, currencies)}</td>
                            <td>
                              {STATUS_HREF[position.status] ? (
                                <Link href={STATUS_HREF[position.status]}>{statusLabel(position.status)}</Link>
                              ) : (
                                statusLabel(position.status)
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              <section aria-labelledby="wealth-waterfall-title" className="wealth-waterfall">
                <h3 id="wealth-waterfall-title">What changed, in {currency.code}</h3>
                {waterfallState === "loading" && (
                  <p role="status">Loading {selected}&rsquo;s attribution…</p>
                )}
                {waterfallState === "error" && (
                  <p className="inline-alert" role="alert">
                    This month&rsquo;s attribution could not be loaded.{" "}
                    <button className="text-action" onClick={() => selectMonth(selected)} type="button">
                      Try again
                    </button>
                  </p>
                )}
                {waterfallState === "ready" && waterfall && (() => {
                  const leg = attributionLeg(waterfall, currency);
                  if (!leg.available) {
                    return (
                      <div role="alert">
                        <p>Attribution is unavailable for {selected}.</p>
                        <ul>
                          {leg.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                        </ul>
                      </div>
                    );
                  }
                  return (
                    <dl className="wealth-waterfall-list">
                      <div>
                        <dt>Opening</dt>
                        <dd>
                          {leg.opening === null
                            ? "—"
                            : moneyText(leg.opening, currency.code, currencies)}
                        </dd>
                      </div>
                      <div>
                        <dt>Closing</dt>
                        <dd>
                          {leg.closing === null
                            ? "—"
                            : moneyText(leg.closing, currency.code, currencies)}
                        </dd>
                      </div>
                      <div>
                        <dt>Actual change</dt>
                        <dd>
                          {leg.actualChange === null
                            ? "—"
                            : moneyText(leg.actualChange, currency.code, currencies)}
                        </dd>
                      </div>
                      {WATERFALL_LINES.map(([key, label]) => {
                        const value = leg[key] as string | null;
                        return (
                          <div key={key}>
                            <dt>{label}</dt>
                            <dd>{value === null ? "—" : moneyText(value, currency.code, currencies)}</dd>
                          </div>
                        );
                      })}
                    </dl>
                  );
                })()}
              </section>
            </section>
          )}
        </>
      )}
    </section>
  );
}
