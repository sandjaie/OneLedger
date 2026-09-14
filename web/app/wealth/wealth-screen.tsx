"use client";

import { useEffect, useState } from "react";

import { errorText, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  CurrencyRow,
  HouseholdCurrency,
  MonthEndPosition,
  MonthPositionsResponse,
  MonthSummary,
  WealthHistoryResponse,
} from "@/types/api";

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

export function WealthScreen({ currency, currencies, request }: Props) {
  const [history, setHistory] = useState<WealthHistoryResponse>();
  const [state, setState] = useState<State>("loading");
  const [attempt, setAttempt] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const [month, setMonth] = useState<MonthPositionsResponse>();
  const [monthState, setMonthState] = useState<MonthState>("idle");

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
    try {
      await request("/wealth-history/refresh", { method: "POST" });
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

  function selectMonth(monthEnd: string) {
    setSelected(monthEnd);
    setMonthState("loading");
    request<MonthPositionsResponse>(`/wealth-history/${encodeURIComponent(monthEnd)}`)
      .then((loaded) => {
        setMonth(loaded);
        setMonthState("ready");
      })
      .catch(() => setMonthState("error"));
  }

  return (
    <section aria-labelledby="wealth-title" className="workspace-content wealth-screen">
      <header>
        <p className="eyebrow">Wealth history</p>
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
        {problem && <p className="inline-alert" role="alert">{problem}</p>}
      </header>

      {state === "loading" && <p role="status">Loading wealth history…</p>}
      {state === "error" && (
        <div role="alert">
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
                      <td>{row.complete ? "Complete" : "Incomplete month"}</td>
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
                <p className="inline-alert" role="alert">This month&rsquo;s positions could not be loaded.</p>
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
                            <td>{position.status}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}
