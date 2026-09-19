"use client";

import { Button, buttonVariants } from "@/components/ui/button";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { EvidenceChart, type EvidenceSeries } from "@/app/ui/evidence-chart";
import { DecisionBand } from "@/app/ui/decision-band";
import { MetricStrip } from "@/app/ui/metric-strip";
import { PageHeader } from "@/app/ui/page-header";
import { errorText, type apiFetch } from "@/lib/api/client";
import { type Currency, formatOwed, formatPercent, moneyText } from "@/lib/money/format";
import type {
  ActionsResponse,
  CardDue,
  CurrencyRow,
  DashboardResponse,
  HouseholdCurrency,
  HouseholdResponse,
  InvestmentCoverage,
  MissingRate,
  MonthFlows,
  TransactionsResponse,
  YearToDate,
} from "@/types/api";

const NEEDS_ATTENTION_PREVIEW = 5;

type Props = {
  household: HouseholdResponse;
  // The reporting currency chosen in the masthead: the month and the trend follow it.
  currency: HouseholdCurrency;
  // The catalogue formats a card in its own currency, which need not be one of the pair.
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
  // Workspace's one caller shows this when a sign-out request failed but left the session intact.
  logoutFailed?: boolean;
};

/** Every status that leaves a total unavailable or stale — everything except a current price or
 * a current-dated snapshot. Stale still contributes a number; missing, future-only, out-of-range
 * and carried do not (or contribute an aged one), so each gets its own reason and link. */
function isCoverageConcern(item: InvestmentCoverage): boolean {
  return item.status !== "current_price" && item.status !== "current_snapshot";
}

function coverageReasonText(item: InvestmentCoverage): string {
  switch (item.status) {
    case "missing_price":
      return "No accepted price is recorded for this holding.";
    case "future_price_only":
      return "Only a future-dated price is recorded for this holding.";
    case "outside_supported_range":
      return "This holding's value is outside the supported calculation range.";
    case "missing_snapshot":
      return "No valuation snapshot is recorded for this account.";
    case "carried_snapshot":
      return `The latest valuation snapshot is from ${item.valueDate}, carried forward.`;
    case "stale_price":
      return `The latest price for this holding is from ${item.valueDate}.`;
    default:
      return item.status.replaceAll("_", " ");
  }
}

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

/** The year's five figures in one reporting currency — picked by name, never computed. */
function yearToDate(year: YearToDate, base: boolean) {
  return base
    ? {
        income: year.incomeBase,
        spending: year.spendingBase,
        net: year.netBase,
        savings: year.savingsRatePctBase,
        invested: year.investedBase,
      }
    : {
        income: year.incomeSecondary,
        spending: year.spendingSecondary,
        net: year.netSecondary,
        savings: year.savingsRatePctSecondary,
        invested: year.investedSecondary,
      };
}

function flowTrend(dashboard: DashboardResponse, base: boolean, shown: string, money: (value: string, code: string) => string) {
  const evidence = new Map(dashboard.flowEvidence.map((item) => [item.month, item]));
  const fields = [
    { id: "income", label: "Income", color: "var(--category-1)", lineStyle: "solid", marker: "circle" },
    { id: "spending", label: "Spending", color: "var(--category-2)", lineStyle: "dashed", marker: "square" },
    { id: "net", label: "Net", color: "var(--category-3)", lineStyle: "dotted", marker: "diamond" },
  ] as const;
  const series: EvidenceSeries[] = fields.map((field) => ({
    ...field,
    points: dashboard.trend.map((month) => {
      const item = evidence.get(month.month);
      const coordinate = item?.status === "complete"
        ? item[`${field.id}${base ? "Base" : "Secondary"}Coordinate`]
        : null;
      return {
        period: month.month.slice(0, 7),
        coordinate: coordinate ?? null,
        exact: coordinate === null || coordinate === undefined ? null
          : money(flows(month, base)[field.id], shown),
      };
    }),
  }));
  return { periods: dashboard.trend.map((month) => month.month.slice(0, 7)), series };
}

export function OverviewScreen({ household, currency, currencies, request, logoutFailed }: Props) {
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
          sentence: errorText(
            error,
            "The overview could not be loaded. Check the API service and try again.",
          ),
        });
      });
    return () => {
      current = false;
    };
  }, [attempt]);

  // Needs attention (Phase 3.1 Task 2): its own fetch, own loading/error state, and its own
  // retry — the panel is secondary to net worth and never blocks or reorders the dashboard load
  // above (this effect is declared after it, so /dashboard always fires first).
  const [actionsState, setActionsState] = useState<"loading" | "ready" | "error">("loading");
  const [actions, setActions] = useState<ActionsResponse>();
  const [actionsAttempt, setActionsAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    latest
      .current<ActionsResponse>("/actions")
      .then((result) => {
        if (current) {
          setActions(result);
          setActionsState("ready");
        }
      })
      .catch(() => {
        if (current) setActionsState("error");
      });
    return () => {
      current = false;
    };
  }, [actionsAttempt]);

  const [recentState, setRecentState] = useState<"loading" | "ready" | "error">("loading");
  const [recent, setRecent] = useState<TransactionsResponse>();
  useEffect(() => {
    let current = true;
    latest
      .current<TransactionsResponse>("/transactions?limit=5&offset=0")
      .then((result) => {
        if (current) {
          setRecent(result);
          setRecentState("ready");
        }
      })
      .catch(() => {
        if (current) setRecentState("error");
      });
    return () => {
      current = false;
    };
  }, []);

  const rowFor = (code: string): Currency | undefined =>
    household.currencies.find((row) => row.code === code) ?? currencies.find((row) => row.code === code);
  const money = (value: string, code: string) =>
    moneyText(value, code, [...household.currencies, ...currencies]);
  const owed = (value: string, code: string) => {
    const row = rowFor(code);
    return row ? formatOwed(value, row) : `${value} ${code}`;
  };
  const percent = (value: string | null) => (value === null ? "Not applicable" : formatPercent(value));
  const coverageHref = (item: InvestmentCoverage) => {
    if (item.holdingId) return `/investments/${item.holdingId}`;
    if (item.valuationMode === "snapshot") return `/accounts?valuation=${item.accountId}`;
    return "/accounts";
  };
  const coverageLabel = (item: InvestmentCoverage) => (item.holdingId ? "View holding" : "View account");

  const attentionPanel = () => (
    <>
      {actionsState === "loading" && <p role="status">Checking for actions…</p>}
      {actionsState === "ready" && actions && (
        <DecisionBand
          tone={actions.items.some((item) => item.overdue) ? "danger" : actions.items.length > 0 || !actions.previousMonthReview.completedAt ? "attention" : "success"}
          title={actions.items.length > 0 ? `Needs attention (${actions.items.length})` : "Needs attention"}
          summary={actions.items.length > 0
            ? `${actions.items.length} ${actions.items.length === 1 ? "item needs" : "items need"} an owner decision.`
            : actions.previousMonthReview.completedAt
              ? "The previous monthly review is complete."
              : "The previous monthly review is still open."}
          action={<Link className={buttonVariants({ variant: "outline" })} href={actions.items.length > 0 ? "/plans#actions" : "/plans#review"}>Review plans</Link>}
        >
          {actions.items.length > 0 && (
            <ul className="overview-notes">
              {actions.items.slice(0, NEEDS_ATTENTION_PREVIEW).map((item) => (
                <li key={item.actionKey}>
                  {item.title}
                  {item.dueDate && <> · {item.dueDate}</>}
                  {item.overdue && <> · overdue</>}
                </li>
              ))}
            </ul>
          )}
          <p><Link href="/plans#review">
            {actions.previousMonthReview.completedAt
              ? `Last month's review is complete (${actions.previousMonthReview.completedAt.slice(0, 10)})`
              : "Last month's review is not yet complete"}
          </Link></p>
        </DecisionBand>
      )}
      {actionsState === "error" && <p className="inline-alert" role="alert">
        Needs attention could not be loaded.{" "}
        <Button variant="outline" onClick={() => setActionsAttempt((value) => value + 1)} type="button">Try again</Button>
      </p>}
    </>
  );

  return (
    <section className="workspace-content overview" aria-labelledby="page-title">
      <PageHeader
        eyebrow="Your money"
        title="Overview"
        description="Your current financial position, cash flow, and next decisions."
      />
      {logoutFailed && (
        <p className="inline-alert" role="alert">OneLedger could not confirm that the server session was revoked. Your workspace remains open; retry sign out.</p>
      )}
      {state.status === "loading" && <p className="lede" role="status">Loading the household&apos;s figures…</p>}
      {state.status === "failed" && (
        <>
          <p className="inline-alert" role="alert">{state.sentence}</p>
          <Button
            variant="default"
            onClick={() => {
              setState({ status: "loading" });
              setAttempt((value) => value + 1);
            }}
            type="button"
          >Try again</Button>
        </>
      )}
      {state.status !== "ready" && attentionPanel()}
      {state.status === "ready" && render(state.dashboard)}
    </section>
  );

  function render(dashboard: DashboardResponse) {
    const worth = dashboard.netWorth;
    const currentPosition = worth.rates.length > 0 || dashboard.cardsDue.length > 0;
    // No held currency means no account counts yet: zeros would be presented as facts.
    if (!currentPosition && !dashboard.hasRecordedFlows) {
      return (
        <>
          <div className="overview-empty-focus">
            <p className="section-label">Start with evidence</p>
            <h2>Nothing to report yet</h2>
            <p>Add an account or import a statement to build the household picture.</p>
            <div className="overview-empty-actions">
              <Link className={buttonVariants()} href="/accounts">Add an account</Link>
              <Link className={buttonVariants({ variant: "outline" })} href="/import">Import a statement</Link>
            </div>
          </div>
          {attentionPanel()}
        </>
      );
    }
    const base = currency.code === worth.baseCurrency;
    const shown = base ? worth.baseCurrency : worth.secondaryCurrency;
    const now = flows(dashboard.thisMonth, base);
    const ytd = yearToDate(dashboard.yearToDate, base);
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
    const trendChart = flowTrend(dashboard, base, shown, money);
    const unknownMonths = dashboard.flowEvidence.filter((item) => item.status !== "complete").length;
    const commitments = dashboard.upcomingCommitments;
    const commitmentTotal = base ? commitments.totalBase : commitments.totalSecondary;
    const missingCommitmentRates = base
      ? commitments.missingBaseCurrencies
      : commitments.missingSecondaryCurrencies;

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
              <Link href="/wealth">{money(side.net, side.code)}</Link>
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
        {!currentPosition && <div className="overview-empty-focus">
          <p className="section-label">Historical activity</p>
          <h2>No current position</h2>
          <p>Past transactions remain below. Closed or excluded accounts are not included in current net worth.</p>
          <div className="overview-empty-actions"><Link className={buttonVariants({ variant: "outline" })} href="/accounts">Review accounts</Link></div>
        </div>}
        {(currentPosition || dashboard.hasRecordedFlows) && <div className="overview-lead-grid">
        {currentPosition && <section className="overview-hero-card" aria-labelledby="worth-title">
          <h2 className="visually-hidden" id="worth-title">Net worth</h2>
          <div className="overview-primary-position">
            <p className="section-label">Primary reporting currency · {primary.code}</p>
            {detail(primary, true)}
            <p className="overview-hero-date">As of {worth.asOf} · <Link href="/coverage">Coverage</Link></p>
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
        </section>}
        <div className="overview-trend-card">
          <EvidenceChart
            title="Income, spending and net trend"
            description={`Last twelve months · ${shown}. Only reconciled, completed statement months are plotted; ${unknownMonths} unknown months remain gaps. Exact values are in the chart table.`}
            periods={trendChart.periods}
            series={trendChart.series}
          />
          {unknownMonths > 0 && <p className="overview-chart-note">The detailed totals below include recorded activity in incomplete months. <Link href="/coverage">Review coverage</Link> before treating those months as complete.</p>}
        </div>
        </div>}

        {attentionPanel()}

        {worth.investmentCoverage.filter(isCoverageConcern).length > 0 && (
          <section className="overview-panel" aria-labelledby="coverage-title">
            <h2 id="coverage-title">Why some figures may be incomplete</h2>
            <ul className="overview-notes">
              {worth.investmentCoverage.filter(isCoverageConcern).map((item) => (
                <li key={`${item.accountId}-${item.holdingId ?? ""}-${item.status}`}>
                  {coverageReasonText(item)} <Link href={coverageHref(item)}>{coverageLabel(item)}</Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        {currentPosition && primary.assets !== null && primary.liabilities !== null && primary.liquid !== null && (
          <MetricStrip
            label="Position summary"
            items={[
              { label: "Total assets", value: money(primary.assets, primary.code), detail: "Included in net worth" },
              { label: "Total liabilities", value: owed(primary.liabilities, primary.code), detail: "Presented as what is owed" },
              { label: "Liquid net worth", value: money(primary.liquid, primary.code), detail: "Owner-marked liquid accounts" },
              { label: "Savings rate", value: percent(now.savings), detail: "Current month" },
            ]}
          />
        )}

        {currentPosition && <>
        <div className="overview-period-grid">
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

        <section className="overview-month-card" aria-labelledby="year-title">
          <h2 className="visually-hidden" id="year-title">This year so far</h2>
          <p className="section-label">This year so far · {dashboard.yearToDate.yearStart} to {dashboard.yearToDate.asOf}</p>
          <dl className="overview-month-figures">
            <div><dt>Income</dt><dd>{money(ytd.income, shown)}</dd></div>
            <div><dt>Spending</dt><dd>{money(ytd.spending, shown)}</dd></div>
            <div><dt>Saved</dt><dd>{money(ytd.net, shown)}</dd></div>
            <div><dt>Savings rate</dt><dd>{percent(ytd.savings)}</dd></div>
            <div><dt>Invested</dt><dd><Link href="/investments">{money(ytd.invested, shown)}</Link></dd></div>
          </dl>
        </section>
        </div>
        </>}

        <div className="overview-lower-grid">
        <section aria-labelledby="recent-title" className="overview-panel overview-recent">
          <div className="overview-section-head">
            <div>
              <p className="section-label">Ledger</p>
              <h2 id="recent-title">Recent activity</h2>
            </div>
            <Link href="/transactions">View all transactions</Link>
          </div>
          {recentState === "loading" && <p role="status">Loading recent activity…</p>}
          {recentState === "error" && <p className="inline-alert">Recent activity could not be loaded.</p>}
          {recentState === "ready" && recent?.transactions.length === 0 && <p>No recorded transactions yet.</p>}
          {recentState === "ready" && recent && recent.transactions.length > 0 && (
            <ul className="overview-activity-list">
              {recent.transactions.map((transaction) => (
                <li key={transaction.id}>
                  <div>
                    <strong>{transaction.descriptionClean ?? transaction.descriptionRaw}</strong>
                    <span>{transaction.accountName} · {transaction.bookedDate}</span>
                  </div>
                  <span>{owed(transaction.amount, transaction.currency)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="commitments-title" className="overview-panel overview-commitments">
          <div className="overview-section-head">
            <div>
              <p className="section-label">Forward view</p>
              <h2 id="commitments-title">Upcoming commitments — next 30 days</h2>
            </div>
            <Link href="/plans#commitments">Review commitments</Link>
          </div>
          {commitments.count === 0 ? (
            <p>No open commitments fall in this period.</p>
          ) : (
            <>
              {commitmentTotal === null ? (
                <div className="overview-commitment-total">
                  <p>The total cannot be stated in {shown} because one or more required rates are missing.</p>
                  <p>Missing rates: {missingCommitmentRates.join(", ")}.</p>
                </div>
              ) : (
                <div className="overview-commitment-total">
                  <strong>{owed(commitmentTotal, shown)}</strong>
                  <span>{commitments.count} {commitments.count === 1 ? "commitment" : "commitments"} due</span>
                </div>
              )}
              {commitments.next && (
                <p className="overview-next-commitment">
                  <strong>{commitments.next.label}</strong>
                  <span>{commitments.next.dueDate} · {owed(commitments.next.nativeAmount, commitments.next.nativeCurrency)}</span>
                </p>
              )}
            </>
          )}
        </section>

        <details className="overview-panel overview-recorded-details">
          <summary>Recorded monthly totals</summary>
          <p>Includes activity in months whose statement coverage is incomplete.</p>
          <div className="overview-scroll">
            <table className="account-table overview-table" aria-label="The last twelve months">
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
        </details>

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
