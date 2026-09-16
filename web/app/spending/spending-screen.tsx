"use client";

import Link from "next/link";
import { type CSSProperties, useEffect, useState } from "react";

import { errorText, type apiFetch } from "@/lib/api/client";
import { apiUrl } from "@/lib/api/url";
import { moneyText } from "@/lib/money/format";
import type {
  BucketSpending,
  CategoryRow,
  CategorySpending,
  CurrencyRow,
  HouseholdCurrency,
  SpendingResponse,
} from "@/types/api";

type Props = {
  // Not read directly by this screen (Task 4.1 Task 1) — Workspace loads it for this section
  // because the budget panel joining this screen in Task 2 needs the same taxonomy.
  categories: Array<CategoryRow>;
  currencies: Array<CurrencyRow>;
  currency: HouseholdCurrency;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";
type RangeMode = "month" | "custom";

const DAY_MS = 24 * 60 * 60 * 1000;

function currentMonthValue(): string {
  return new Date().toISOString().slice(0, 7);
}

const MONTH_PATTERN = /^\d{4}-\d{2}$/;

/** The first and last calendar day of a `YYYY-MM` value, in UTC so no timezone shifts the
 * month boundary — `Date` arithmetic only, never `Number`/`parseInt` on the pieces. A cleared or
 * half-typed value returns an empty range instead of throwing: `type="month"` is a plain text box
 * in Firefox and Safari, and Chrome lets the owner clear it. */
function monthRange(month: string): { from: string; to: string } {
  if (!MONTH_PATTERN.test(month)) return { from: "", to: "" };
  const first = new Date(`${month}-01T00:00:00Z`);
  const nextMonth = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1));
  const lastDay = new Date(nextMonth.getTime() - DAY_MS);
  return { from: `${month}-01`, to: lastDay.toISOString().slice(0, 10) };
}

/** This period's amount in the toggled reporting currency; the previous period is always shown
 * in the household base currency (the server names only `previousAmountBase`, never a secondary
 * figure), so it is labelled with that currency rather than silently switching under the owner. */
function amountFor(base: string, secondary: string, response: SpendingResponse, currency: HouseholdCurrency) {
  return currency.code === response.baseCurrency ? base : secondary;
}

/** A category whose in-range refunds exceed its spending has a negative ratio; the bar is drawn
 * flat and the row is marked, rather than scaling below the track and overflowing it. */
function barRatio(chartRatio: string) {
  return chartRatio.startsWith("-") ? "0" : chartRatio;
}

function transactionsHref(categoryId: string | null, from: string, to: string) {
  const parameters = new URLSearchParams();
  if (categoryId) parameters.set("categoryId", categoryId);
  parameters.set("dateFrom", from);
  parameters.set("dateTo", to);
  return `/transactions?${parameters.toString()}`;
}

export function SpendingScreen({ currencies, currency, request }: Props) {
  const [mode, setMode] = useState<RangeMode>("month");
  const [month, setMonth] = useState(currentMonthValue);
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [state, setState] = useState<State>("loading");
  const [data, setData] = useState<SpendingResponse>();
  const [attempt, setAttempt] = useState(0);
  const [problem, setProblem] = useState<string>();

  const range = mode === "month" ? monthRange(month) : { from: customFrom, to: customTo };
  const rangeReady = Boolean(range.from && range.to);

  useEffect(() => {
    if (!rangeReady) return;
    let current = true;
    request<SpendingResponse>(
      `/spending?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`,
    )
      .then((result) => {
        if (!current) return;
        setData(result);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(errorText(error, "Spending could not be loaded."));
        setState("error");
      });
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `range` is derived each render
  }, [mode, month, customFrom, customTo, rangeReady, attempt, request]);

  const maxCategoryRows = data?.byCategory ?? [];

  return (
    <section className="workspace-content" aria-labelledby="spending-title">
      <p className="eyebrow">Spending</p>
      <h1 className="workspace-title" id="spending-title">Spending by category and bucket</h1>
      <p className="lede">
        Every figure reads the reportable ledger only — transfers, card payments and investment
        legs never appear here, and a refund lowers spending rather than counting as income.
      </p>

      <div className="spending-range" role="group" aria-label="Date range">
        <label className="text-field">
          <span className="visually-hidden">Range type</span>
          <select
            onChange={(event) => { setState("loading"); setMode(event.target.value as RangeMode); }}
            value={mode}
          >
            <option value="month">Month</option>
            <option value="custom">Custom range</option>
          </select>
        </label>
        {mode === "month" ? (
          <label className="date-field">
            <span>Month</span>
            <input
              max={currentMonthValue()}
              onChange={(event) => { setState("loading"); setMonth(event.target.value); }}
              type="month"
              value={month}
            />
          </label>
        ) : (
          <>
            <label className="date-field">
              <span>From</span>
              <input
                onChange={(event) => { setState("loading"); setCustomFrom(event.target.value); }}
                type="date"
                value={customFrom}
              />
            </label>
            <label className="date-field">
              <span>To</span>
              <input
                onChange={(event) => { setState("loading"); setCustomTo(event.target.value); }}
                type="date"
                value={customTo}
              />
            </label>
          </>
        )}
        {rangeReady && (
          <a
            className="text-action"
            download
            href={apiUrl(
              `/spending/export.csv?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`,
            )}
          >
            Download CSV
          </a>
        )}
      </div>

      {!rangeReady && <p role="status">Choose a from and to date.</p>}
      {rangeReady && state === "loading" && <p role="status">Loading spending…</p>}
      {rangeReady && state === "error" && (
        <div role="alert">
          <p>{problem}</p>
          <button
            className="text-action"
            onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}
            type="button"
          >Try again</button>
        </div>
      )}

      {rangeReady && state === "ready" && data && (
        <>
          <dl className="spending-totals">
            <div>
              <dt>This period</dt>
              <dd>{moneyText(amountFor(data.totalBase, data.totalSecondary, data, currency), currency.code, currencies)}</dd>
            </div>
            <div>
              <dt>Previous period ({data.baseCurrency})</dt>
              <dd>{moneyText(data.previousTotalBase, data.baseCurrency, currencies)}</dd>
            </div>
          </dl>

          <h2>By bucket</h2>
          <p className="lede">
            Buckets cover categorised spending only, so these rows can total less than the figure
            above: uncategorised spending, and any category left without a bucket, are not counted
            here.
          </p>
          {data.byBucket.length === 0 && <p>No spending in this range.</p>}
          {data.byBucket.length > 0 && (
            <table className="spending-table">
              <caption className="visually-hidden">Spending by bucket</caption>
              <thead>
                <tr>
                  <th scope="col">Bucket</th>
                  <th scope="col">This period</th>
                  <th scope="col">Previous period ({data.baseCurrency})</th>
                </tr>
              </thead>
              <tbody>
                {data.byBucket.map((row: BucketSpending) => (
                  <tr key={row.bucket}>
                    <th scope="row">{row.bucket}</th>
                    <td>{moneyText(amountFor(row.amountBase, row.amountSecondary, data, currency), currency.code, currencies)}</td>
                    <td>{moneyText(row.previousAmountBase, data.baseCurrency, currencies)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h2>By category</h2>
          {maxCategoryRows.length === 0 && <p>No spending in this range.</p>}
          {maxCategoryRows.length > 0 && (
            <>
              <div
                aria-label={`Spending by category in ${data.baseCurrency}, as a bar chart — the table below states each figure`}
                className="spending-chart"
                role="img"
              >
                {maxCategoryRows.map((row: CategorySpending) => (
                  <div className="spending-chart-column" key={row.categoryId ?? "uncategorised"}>
                    <div className="spending-chart-track">
                      <div
                        className="spending-chart-bar"
                        style={{ "--ratio": barRatio(row.chartRatio) } as CSSProperties}
                      />
                    </div>
                    <span className="spending-chart-label">
                      {row.categoryName}
                      {row.chartRatio.startsWith("-") && " (net refund)"}
                    </span>
                  </div>
                ))}
              </div>

              <table className="spending-table">
                <caption className="visually-hidden">Spending by category</caption>
                <thead>
                  <tr>
                    <th scope="col">Category</th>
                    <th scope="col">Bucket</th>
                    <th scope="col">This period</th>
                    <th scope="col">Previous period ({data.baseCurrency})</th>
                    <th scope="col">Transactions</th>
                  </tr>
                </thead>
                <tbody>
                  {maxCategoryRows.map((row: CategorySpending) => (
                    <tr key={row.categoryId ?? "uncategorised"}>
                      <th scope="row">
                        {row.parentCategoryName ? `${row.parentCategoryName} · ${row.categoryName}` : row.categoryName}
                      </th>
                      <td>{row.bucket ?? "—"}</td>
                      <td>{moneyText(amountFor(row.amountBase, row.amountSecondary, data, currency), currency.code, currencies)}</td>
                      <td>{moneyText(row.previousAmountBase, data.baseCurrency, currencies)}</td>
                      <td>
                        <Link href={transactionsHref(row.categoryId, data.from, data.to)}>
                          {row.transactionCount}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </section>
  );
}
