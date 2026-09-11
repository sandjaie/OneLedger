"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";

import { ApiError, type apiFetch } from "@/lib/api/client";
import { type Currency, formatMoney, formatOwed, formatPercent } from "@/lib/money/format";
import type {
  CardDue,
  CurrencyRow,
  DashboardResponse,
  HouseholdCurrency,
  HouseholdResponse,
  MissingRate,
  MonthFlows,
} from "@/types/api";

type Props = {
  household: HouseholdResponse;
  // The reporting currency chosen in the masthead: the month and the trend follow it.
  currency: HouseholdCurrency;
  // The catalogue formats a card in its own currency, which need not be one of the pair.
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
  children?: ReactNode;
};

type State =
  | { status: "loading" }
  | { status: "ready"; dashboard: DashboardResponse }
  | { status: "failed"; sentence: string };

const MONTH = new Intl.DateTimeFormat("en", { month: "short", year: "numeric", timeZone: "UTC" });

function monthLabel(first: string) {
  return MONTH.format(new Date(`${first}T00:00:00Z`));
}

function dueLabel(due: CardDue) {
  const days = due.daysRemaining;
  if (days === 0) return `${due.paymentDueDate} · due today`;
  if (days > 0) return `${due.paymentDueDate} · in ${days} ${days === 1 ? "day" : "days"}`;
  return `${due.paymentDueDate} · ${-days} ${days === -1 ? "day" : "days"} overdue`;
}

function missingSentence(missing: MissingRate) {
  return `No ${missing.currency} rate is stored on or before ${missing.asOf}, so net worth in ${missing.reportingCurrency} cannot be stated.`;
}

/** A month's four figures in one reporting currency — picked by name, never computed. */
function flows(month: MonthFlows, base: boolean) {
  return base
    ? { income: month.incomeBase, spending: month.spendingBase, net: month.netBase, savings: month.savingsRatePctBase }
    : {
        income: month.incomeSecondary,
        spending: month.spendingSecondary,
        net: month.netSecondary,
        savings: month.savingsRatePctSecondary,
      };
}

export function OverviewScreen({ household, currency, currencies, request, children }: Props) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  // The workspace hands a fresh wrapper each render; fetching on that would refetch on every
  // currency toggle. The latest one is kept, and the fetch runs on mount and on retry only.
  const latest = useRef(request);
  useEffect(() => {
    latest.current = request;
  });

  useEffect(() => {
    let current = true;
    latest
      .current<DashboardResponse>("/dashboard")
      .then((dashboard) => {
        if (current) setState({ status: "ready", dashboard });
      })
      .catch((error: unknown) => {
        if (!current) return;
        setState({
          status: "failed",
          sentence:
            error instanceof ApiError
              ? error.detail
              : "The overview could not be loaded. Check the API service and try again.",
        });
      });
    return () => {
      current = false;
    };
  }, [attempt]);

  const rowFor = (code: string): Currency | undefined =>
    household.currencies.find((row) => row.code === code) ?? currencies.find((row) => row.code === code);
  const money = (value: string, code: string) => {
    const row = rowFor(code);
    return row ? formatMoney(value, row) : `${value} ${code}`;
  };
  const owed = (value: string, code: string) => {
    const row = rowFor(code);
    return row ? formatOwed(value, row) : `${value} ${code}`;
  };
  const percent = (value: string | null) => (value === null ? "Not applicable" : formatPercent(value));

  return (
    <section className="workspace-content overview" aria-labelledby="overview-title">
      <div className="overview-page-heading visually-hidden">
        <div>
          <p className="eyebrow">Owner workspace</p>
          <h1 className="workspace-title" id="overview-title">Overview</h1>
        </div>
        {state.status === "ready" && (
          <p className="overview-as-of">Position as of {state.dashboard.netWorth.asOf}</p>
        )}
      </div>
      {children}
      {state.status === "loading" && <p className="lede" role="status">Loading the household&apos;s figures…</p>}
      {state.status === "failed" && (
        <>
          <p className="inline-alert" role="alert">{state.sentence}</p>
          <button
            className="primary-action"
            onClick={() => {
              setState({ status: "loading" });
              setAttempt((value) => value + 1);
            }}
            type="button"
          >Try again</button>
        </>
      )}
      {state.status === "ready" && render(state.dashboard)}
    </section>
  );

  function render(dashboard: DashboardResponse) {
    const worth = dashboard.netWorth;
    // No held currency means no account counts yet: zeros would be presented as facts.
    if (worth.rates.length === 0 && dashboard.cardsDue.length === 0) {
      return (
        <p className="lede">
          Nothing to report yet. Add an account or import a statement, and the household&apos;s
          figures appear here.
        </p>
      );
    }
    const base = currency.code === worth.baseCurrency;
    const shown = base ? worth.baseCurrency : worth.secondaryCurrency;
    const now = flows(dashboard.thisMonth, base);
    const sides = [
      { code: worth.baseCurrency, net: worth.totalBase, assets: worth.assetsBase, liabilities: worth.liabilitiesBase, liquid: worth.liquidBase },
      {
        code: worth.secondaryCurrency,
        net: worth.totalSecondary,
        assets: worth.assetsSecondary,
        liabilities: worth.liabilitiesSecondary,
        liquid: worth.liquidSecondary,
      },
    ];
    const primary = sides.find((side) => side.code === shown) ?? sides[0];
    const secondary = sides.find((side) => side.code !== shown) ?? sides[1];
    const carried = worth.rates.filter((rate) => rate.carried);

    const detail = (side: (typeof sides)[number], primarySide = false) => {
      const reasons = worth.missing.filter((missing) => missing.reportingCurrency === side.code);
      if (side.net === null || side.assets === null || side.liabilities === null || side.liquid === null) {
        return reasons.length > 0 ? (
          reasons.map((missing) => (
            <p className="inline-alert" key={missing.currency}>{missingSentence(missing)}</p>
          ))
        ) : (
          <p className="inline-alert">Net worth in {side.code} cannot be stated for this date.</p>
        );
      }

      return (
        <>
          {primarySide ? (
            <h3
              aria-label={`Net worth in ${side.code}: ${money(side.net, side.code)}`}
              className="overview-net-worth"
            >
              {money(side.net, side.code)}
            </h3>
          ) : (
            <p className="overview-secondary-worth">{money(side.net, side.code)}</p>
          )}
          <dl className="overview-position-detail">
            <div><dt>Assets</dt><dd>{money(side.assets, side.code)}</dd></div>
            <div><dt>Liabilities</dt><dd>{owed(side.liabilities, side.code)}</dd></div>
            <div><dt>Liquid</dt><dd>{money(side.liquid, side.code)}</dd></div>
          </dl>
        </>
      );
    };

    return (
      <>
        <section className="overview-hero-card" aria-labelledby="worth-title">
          <h2 className="visually-hidden" id="worth-title">Net worth</h2>
          <div className="overview-primary-position">
            <p className="section-label">Primary reporting currency · {primary.code}</p>
            {detail(primary, true)}
            <p className="overview-hero-date">As of {worth.asOf}</p>
          </div>
          <div className="overview-secondary-position">
            <p className="section-label">Also shown in {secondary.code}</p>
            {detail(secondary)}
          </div>
          {carried.length > 0 && (
            <ul className="overview-notes">
              {carried.map((rate) => (
                <li key={rate.currency}>{`${rate.currency}: as of the rate for ${rate.rateDate}, carried forward`}</li>
              ))}
            </ul>
          )}
        </section>

        {primary.assets !== null && primary.liabilities !== null && primary.liquid !== null && (
          <section aria-label="Position summary" className="overview-summary-grid">
            <article>
              <p className="section-label">Total assets</p>
              <p className="overview-summary-value">{money(primary.assets, primary.code)}</p>
              <p>Everything currently included in net worth</p>
            </article>
            <article>
              <p className="section-label">Total liabilities</p>
              <p className="overview-summary-value liability">{owed(primary.liabilities, primary.code)}</p>
              <p>Signed balances presented as what is owed</p>
            </article>
            <article>
              <p className="section-label">Liquid net worth</p>
              <p className="overview-summary-value">{money(primary.liquid, primary.code)}</p>
              <p>Accounts the owner marks as liquid, net of their signed balances</p>
            </article>
            <article>
              <p className="section-label">Savings rate</p>
              <p className="overview-summary-value">{percent(now.savings)}</p>
              <p>Income retained in the current month</p>
            </article>
          </section>
        )}

        <section className="overview-month-card" aria-labelledby="month-title">
          <h2 className="visually-hidden" id="month-title">This month</h2>
          <p className="section-label">This month · {monthLabel(dashboard.thisMonth.month)}</p>
          <dl className="overview-month-figures">
            <div><dt>Income</dt><dd>{money(now.income, shown)}</dd></div>
            <div><dt>Spending</dt><dd>{money(now.spending, shown)}</dd></div>
            <div><dt>Net</dt><dd>{money(now.net, shown)}</dd></div>
            <div><dt>Savings rate</dt><dd>{percent(now.savings)}</dd></div>
          </dl>
        </section>

        <div className="overview-lower-grid">
        <section className="overview-panel" aria-labelledby="trend-title">
          <h2 id="trend-title">The last twelve months</h2>
          <div className="overview-scroll">
            <table className="account-table overview-table" aria-labelledby="trend-title">
              <thead>
                <tr>
                  <th scope="col">Month</th>
                  <th scope="col">Income</th>
                  <th scope="col">Spending</th>
                  <th scope="col">Net</th>
                  <th scope="col">Savings rate</th>
                </tr>
              </thead>
              <tbody>
                {dashboard.trend.map((month) => {
                  const figures = flows(month, base);
                  return (
                    <tr key={month.month}>
                      <th scope="row">{monthLabel(month.month)}</th>
                      <td>{money(figures.income, shown)}</td>
                      <td>{money(figures.spending, shown)}</td>
                      <td>{money(figures.net, shown)}</td>
                      <td>{percent(figures.savings)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        {dashboard.cardsDue.length > 0 && (
          <section className="overview-panel" aria-labelledby="cards-title">
            <h2 id="cards-title">Cards due</h2>
            <div className="overview-scroll">
              <table className="account-table overview-table" aria-labelledby="cards-title">
                <thead>
                  <tr>
                    <th scope="col">Card</th>
                    <th scope="col">Statement balance</th>
                    <th scope="col">Minimum due</th>
                    <th scope="col">Due</th>
                    <th scope="col">Utilisation</th>
                  </tr>
                </thead>
                <tbody>
                  {dashboard.cardsDue.map((due) => (
                    <tr key={due.accountId}>
                      <th scope="row">{due.accountName}</th>
                      <td>{money(due.statementBalance, due.currency)}</td>
                      <td>{due.minimumDue === null ? "—" : money(due.minimumDue, due.currency)}</td>
                      <td>{dueLabel(due)}{due.isPaidInFull ? " · paid in full" : ""}</td>
                      <td>{due.utilisationPct === null ? "No limit stated" : formatPercent(due.utilisationPct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        </div>
      </>
    );
  }
}
