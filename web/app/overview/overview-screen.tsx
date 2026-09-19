"use client";

import { Button, buttonVariants } from "@/components/ui/button";

import Link from "next/link";
import { type CSSProperties, useEffect, useRef, useState } from "react";

import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { EvidenceChart, type EvidenceSeries } from "@/app/ui/evidence-chart";
import { MetricStrip } from "@/app/ui/metric-strip";
import { Module, ModuleGrid } from "@/app/ui/module-grid";
import { StatusLabel } from "@/app/ui/status-label";
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
  PositionCompositionRow,
  TransactionsResponse,
  WealthHistoryResponse,
  YearToDate,
} from "@/types/api";

const NEEDS_ATTENTION_PREVIEW = 5;
const HISTORY_MONTHS = 12;
const ACTIVITY_PREVIEW = 5;
const UNAVAILABLE = "Not available";

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

/** A composition row's three figures in one reporting currency — picked by name. The share and
 * the coordinate are the server's; the browser neither divides nor scales a money value. */
function bucket(row: PositionCompositionRow, base: boolean) {
  return base
    ? { value: row.valueBase, share: row.sharePercentBase, coordinate: row.chartCoordinateBase }
    : {
        value: row.valueSecondary,
        share: row.sharePercentSecondary,
        coordinate: row.chartCoordinateSecondary,
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
  // retry — the module is secondary to the position and never blocks or reorders the dashboard
  // load above (this effect is declared after it, so /dashboard always fires first).
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
      .current<TransactionsResponse>(`/transactions?limit=${ACTIVITY_PREVIEW}&offset=0`)
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

  // Net-position history is the stored month-end evidence `GET /wealth-history` already owns,
  // with its own coordinates; Overview plots it rather than deriving a second history.
  const [historyState, setHistoryState] = useState<"loading" | "ready" | "error">("loading");
  const [history, setHistory] = useState<WealthHistoryResponse>();
  useEffect(() => {
    let current = true;
    latest
      .current<WealthHistoryResponse>("/wealth-history")
      .then((result) => {
        if (current) {
          setHistory(result);
          setHistoryState("ready");
        }
      })
      .catch(() => {
        if (current) setHistoryState("error");
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

  const commitmentsModule = (dashboard: DashboardResponse) => {
    const commitments = dashboard.upcomingCommitments;
    const base = currency.code === dashboard.netWorth.baseCurrency;
    const shown = base ? dashboard.netWorth.baseCurrency : dashboard.netWorth.secondaryCurrency;
    const total = base ? commitments.totalBase : commitments.totalSecondary;
    const missingRates = base
      ? commitments.missingBaseCurrencies
      : commitments.missingSecondaryCurrencies;

    return (
      <Module
        title="Upcoming commitments — next 30 days"
        meta={`${commitments.asOf} to ${commitments.through}`}
        footer={
          <>
            <Link href="/plans#commitments">Review commitments</Link>
            {commitments.count > 0 && (
              <span>
                {total === null ? `Total unavailable in ${shown}` : `${owed(total, shown)} total`}
              </span>
            )}
          </>
        }
      >
        {commitments.count === 0 ? (
          <p>No open commitments fall in this period.</p>
        ) : (
          <>
            {total === null && (
              <p className="inline-alert">
                The total cannot be stated in {shown} because one or more required rates are missing.
                Missing rates: {missingRates.join(", ")}.
              </p>
            )}
            <ul className="console-list">
              {commitments.items.slice(0, NEEDS_ATTENTION_PREVIEW).map((item) => (
                <li className="console-list-row" key={item.commitmentId}>
                  <div>
                    <span className="console-list-title">{item.label}</span>
                    <span className="console-list-sub">Due {item.dueDate}</span>
                  </div>
                  <span className="console-list-value">
                    {owed(item.nativeAmount, item.nativeCurrency)}
                  </span>
                </li>
              ))}
            </ul>
            <p className="console-module-sub">
              {commitments.count} {commitments.count === 1 ? "commitment" : "commitments"} due
              through {commitments.through}
            </p>
          </>
        )}
      </Module>
    );
  };

  const attentionModule = () => {
    const open = actions?.items.length ?? 0;
    const tone = actions === undefined
      ? "neutral"
      : actions.items.some((item) => item.overdue)
        ? "danger"
        : open > 0 || !actions.previousMonthReview.completedAt
          ? "attention"
          : "success";
    return (
      <Module
        title={open > 0 ? `Needs attention (${open})` : "Needs attention"}
        meta={
          actionsState === "ready"
            ? <StatusLabel tone={tone}>{open > 0 ? `${open} open` : "Nothing open"}</StatusLabel>
            : undefined
        }
        footer={
          actionsState === "ready" && actions ? (
            <>
              <Link href={open > 0 ? "/plans#actions" : "/plans#review"}>Review plans</Link>
              <Link href="/plans#review">
                {actions.previousMonthReview.completedAt
                  ? `Last month's review is complete (${actions.previousMonthReview.completedAt.slice(0, 10)})`
                  : "Last month's review is not yet complete"}
              </Link>
            </>
          ) : undefined
        }
      >
        {actionsState === "loading" && <p role="status">Checking for actions…</p>}
        {actionsState === "error" && (
          <p className="inline-alert" role="alert">
            Needs attention could not be loaded.{" "}
            <Button variant="outline" onClick={() => setActionsAttempt((value) => value + 1)} type="button">Try again</Button>
          </p>
        )}
        {actionsState === "ready" && actions && open === 0 && (
          <p>
            {actions.previousMonthReview.completedAt
              ? "The previous monthly review is complete."
              : "The previous monthly review is still open."}
          </p>
        )}
        {actionsState === "ready" && actions && open > 0 && (
          <ul className="console-list">
            {actions.items.slice(0, NEEDS_ATTENTION_PREVIEW).map((item) => (
              <li className="console-list-row" key={item.actionKey}>
                <div>
                  <span className="console-list-title">{item.title}</span>
                  <span className="console-list-sub">
                    {item.dueDate ?? "No due date recorded"}
                    {item.overdue && " · overdue"}
                  </span>
                </div>
                {item.overdue ? <StatusLabel tone="danger">Overdue</StatusLabel> : null}
              </li>
            ))}
          </ul>
        )}
      </Module>
    );
  };

  const asOf = state.status === "ready" ? state.dashboard.netWorth.asOf : null;

  return (
    <section className="workspace-content overview">
      <ConsolePageHeader
        title="Overview"
        description="Your current position, this year's movement, and the next decisions."
        actions={
          <>
            {asOf && <span className="console-period">As of {asOf} · last 12 months</span>}
            <Link className={buttonVariants({ variant: "outline" })} href="/coverage">Coverage</Link>
            <Link className={buttonVariants()} href="/import">Import a statement</Link>
          </>
        }
      />
      {logoutFailed && (
        <p className="inline-alert" role="alert">OneLedger could not confirm that the server session was revoked. Your workspace remains open; retry sign out.</p>
      )}
      {state.status === "loading" && <p role="status">Loading the household&apos;s figures…</p>}
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
      {state.status !== "ready" && <ModuleGrid>{attentionModule()}</ModuleGrid>}
      {state.status === "ready" && render(state.dashboard)}
    </section>
  );

  function compositionModule(dashboard: DashboardResponse, base: boolean, shown: string) {
    const { rows, missing } = dashboard.positionComposition;
    const stated = rows.filter((row) => bucket(row, base).value !== null);
    return (
      <Module
        title="Position composition"
        meta={`As of ${dashboard.netWorth.asOf}`}
        footer={
          <>
            <Link href="/accounts">View accounts</Link>
            <span>{rows.length} {rows.length === 1 ? "group" : "groups"} included</span>
          </>
        }
      >
        {rows.length === 0 ? (
          <p>No included account has a recorded value for this date.</p>
        ) : (
          <>
            {stated.length === 0 && (
              <p className="inline-alert">
                The composition cannot be stated in {shown}: {missing.length}{" "}
                {missing.length === 1 ? "included account has" : "included accounts have"} no value
                in that reporting currency. Shares are withheld rather than recalculated without them.
              </p>
            )}
            {stated.length > 0 && (
              <div aria-hidden="true" className="position-bar">
                {rows.map((row) => {
                  const coordinate = bucket(row, base).coordinate;
                  return coordinate === null ? null : (
                    <span
                      data-bucket={row.key}
                      key={row.key}
                      style={{ "--share": coordinate } as CSSProperties}
                    />
                  );
                })}
              </div>
            )}
            <ul className="console-list">
              {rows.map((row) => {
                const figures = bucket(row, base);
                return (
                  <li className="console-list-row" key={row.key}>
                    <div>
                      <span className="console-list-title">{row.label}</span>
                      <span className="console-list-sub">
                        {figures.share === null ? "Share unavailable" : `${formatPercent(figures.share)} of the position`}
                      </span>
                    </div>
                    <span className="console-list-value">
                      {figures.value === null
                        ? UNAVAILABLE
                        : row.key === "liabilities"
                          ? owed(figures.value, shown)
                          : money(figures.value, shown)}
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </Module>
    );
  }

  function historyModule(baseCurrency: string) {
    const months = (history?.months ?? []).slice(-HISTORY_MONTHS);
    const incomplete = months.filter((row) => !row.complete).length;
    return (
      <Module
        title="Net position history"
        meta={`Month ends · ${baseCurrency}`}
        footer={<><Link href="/wealth">Explore history</Link><span>{months.length} stored months</span></>}
      >
        {historyState === "loading" && <p role="status">Loading stored month ends…</p>}
        {historyState === "error" && <p className="inline-alert">Net position history could not be loaded.</p>}
        {historyState === "ready" && months.length === 0 && (
          <p>No month end has been stored yet. <Link href="/wealth">Refresh the history</Link> to build it.</p>
        )}
        {historyState === "ready" && months.length > 0 && (
          <>
            <p className="console-module-sub">
              {incomplete === 0
                ? "Every stored month has complete values."
                : `${incomplete} of ${months.length} months are incomplete and remain gaps, never zero.`}
            </p>
            <EvidenceChart
              dense
              title={`Month-end net position · ${baseCurrency}`}
              description="Stored month-end totals only. Incomplete months are gaps."
              periods={months.map((row) => row.monthEnd.slice(0, 7))}
              series={[{
                id: "position",
                label: "Net position",
                color: "var(--category-1)",
                lineStyle: "solid",
                marker: "circle",
                points: months.map((row) => ({
                  period: row.monthEnd.slice(0, 7),
                  coordinate: row.complete ? row.chartCoordinate : null,
                  exact: !row.complete || row.totalBase === null ? null : money(row.totalBase, baseCurrency),
                })),
              }]}
            />
          </>
        )}
      </Module>
    );
  }

  function cashFlowModule(dashboard: DashboardResponse, base: boolean, shown: string) {
    const now = flows(dashboard.thisMonth, base);
    const trendChart = flowTrend(dashboard, base, shown, money);
    const unknownMonths = dashboard.flowEvidence.filter((item) => item.status !== "complete").length;
    return (
      <Module
        title="Cash flow"
        meta={monthLabel(dashboard.thisMonth.month)}
        footer={<><Link href="/spending">View spending</Link><span>Savings rate {percent(now.savings)}</span></>}
      >
        <p className="console-module-value">{money(now.net, shown)}</p>
        <p className="console-module-sub">
          Net this month · {money(now.income, shown)} in, {money(now.spending, shown)} out
        </p>
        <EvidenceChart
          dense
          title="Income, spending and net trend"
          description={`Last twelve months · ${shown}. Only reconciled, completed statement months are plotted; ${unknownMonths} unknown months remain gaps.`}
          periods={trendChart.periods}
          series={trendChart.series}
        />
        {unknownMonths > 0 && (
          <p className="console-module-sub">
            {unknownMonths} months have incomplete statement coverage and are plotted as gaps.{" "}
            <Link href="/coverage">Review coverage</Link>
          </p>
        )}
      </Module>
    );
  }

  function activityModule() {
    return (
      <Module
        title="Recent activity"
        meta="Latest recorded"
        footer={<><Link href="/transactions">View all transactions</Link><span>Newest first</span></>}
      >
        {recentState === "loading" && <p role="status">Loading recent activity…</p>}
        {recentState === "error" && <p className="inline-alert">Recent activity could not be loaded.</p>}
        {recentState === "ready" && recent?.transactions.length === 0 && <p>No recorded transactions yet.</p>}
        {recentState === "ready" && recent && recent.transactions.length > 0 && (
          <ul className="console-list">
            {recent.transactions.map((transaction) => (
              <li className="console-list-row" key={transaction.id}>
                <div>
                  <span className="console-list-title">{transaction.descriptionClean ?? transaction.descriptionRaw}</span>
                  <span className="console-list-sub">{transaction.accountName} · {transaction.bookedDate}</span>
                </div>
                <span className="console-list-value">{money(transaction.amount, transaction.currency)}</span>
              </li>
            ))}
          </ul>
        )}
      </Module>
    );
  }

  function render(dashboard: DashboardResponse) {
    const worth = dashboard.netWorth;
    const currentPosition = worth.rates.length > 0 || dashboard.cardsDue.length > 0;
    // No held currency means no account counts yet: zeros would be presented as facts.
    if (!currentPosition && !dashboard.hasRecordedFlows) {
      return (
        <>
          <div className="console-empty-focus">
            <h2>Nothing to report yet</h2>
            <p>Add an account or import a statement to build the household picture.</p>
            <div className="console-empty-actions">
              <Link className={buttonVariants()} href="/accounts">Add an account</Link>
              <Link className={buttonVariants({ variant: "outline" })} href="/import">Import a statement</Link>
            </div>
          </div>
          <ModuleGrid>
            {attentionModule()}
            {commitmentsModule(dashboard)}
          </ModuleGrid>
        </>
      );
    }
    const base = currency.code === worth.baseCurrency;
    const shown = base ? worth.baseCurrency : worth.secondaryCurrency;
    const commitments = dashboard.upcomingCommitments;
    const commitmentTotal = base ? commitments.totalBase : commitments.totalSecondary;
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
    const carried = worth.rates.filter((rate) => rate.carried);
    const concerns = worth.investmentCoverage.filter(isCoverageConcern);

    return (
      <>
        {!currentPosition && <div className="console-empty-focus">
          <h2>No current position</h2>
          <p>Past transactions remain below. Closed or excluded accounts are not included in current net worth.</p>
          <div className="console-empty-actions"><Link className={buttonVariants({ variant: "outline" })} href="/accounts">Review accounts</Link></div>
        </div>}

        <MetricStrip
          label="Position summary"
          items={[
            {
              label: "Net position",
              value: primary.net === null ? UNAVAILABLE : money(primary.net, primary.code),
              detail: `In ${primary.code} · as of ${worth.asOf}`,
            },
            {
              label: "Liquid balance",
              value: primary.liquid === null ? UNAVAILABLE : money(primary.liquid, primary.code),
              detail: "Owner-marked liquid accounts",
            },
            {
              label: "Known next 30 days",
              value: commitments.count === 0
                ? "None recorded"
                : commitmentTotal === null ? UNAVAILABLE : owed(commitmentTotal, shown),
              detail: `${commitments.count} ${commitments.count === 1 ? "commitment" : "commitments"} due through ${commitments.through}`,
            },
          ]}
        />

        <ModuleGrid>
          {compositionModule(dashboard, base, shown)}
          {historyModule(worth.baseCurrency)}
          {cashFlowModule(dashboard, base, shown)}
          {attentionModule()}
          {commitmentsModule(dashboard)}
          {activityModule()}
        </ModuleGrid>

        {(concerns.length > 0 || worth.missing.length > 0 || carried.length > 0) && (
          <section aria-label="Why some figures may be incomplete" className="console-section">
            <h2>Why some figures may be incomplete</h2>
            <ul className="console-notes">
              {worth.missing.map((missing) => (
                <li key={`${missing.currency}-${missing.reportingCurrency}`}>{missingSentence(missing)}</li>
              ))}
              {carried.map((rate) => (
                <li key={rate.currency}>{`${rate.currency}: as of the rate for ${rate.rateDate}, carried forward`}</li>
              ))}
              {concerns.map((item) => (
                <li key={`${item.accountId}-${item.holdingId ?? ""}-${item.status}`}>
                  {coverageReasonText(item)} <Link href={coverageHref(item)}>{coverageLabel(item)}</Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        {dashboard.cardsDue.length > 0 && (
          <section aria-label="Cards due" className="console-section">
            <h2>Cards due</h2>
            <DataTableRegion label="Cards due table">
              <table className="console-table">
                <caption className="visually-hidden">Cards due</caption>
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
            </DataTableRegion>
          </section>
        )}

        <details className="console-details">
          <summary>Detailed totals and evidence</summary>
          <div className="console-details-body">
            <section aria-label="Position detail" className="console-section">
              <h3>Position detail</h3>
              <DataTableRegion label="Position detail table">
                <table className="console-table">
                  <thead>
                    <tr>
                      <th scope="col">Reporting currency</th>
                      <th scope="col">Net position</th>
                      <th scope="col">Assets</th>
                      <th scope="col">Liabilities</th>
                      <th scope="col">Liquid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sides.map((side) => (
                      <tr key={side.code}>
                        <th scope="row">{side.code}</th>
                        <td>{side.net === null ? UNAVAILABLE : money(side.net, side.code)}</td>
                        <td>{side.assets === null ? UNAVAILABLE : money(side.assets, side.code)}</td>
                        <td>{side.liabilities === null ? UNAVAILABLE : owed(side.liabilities, side.code)}</td>
                        <td>{side.liquid === null ? UNAVAILABLE : money(side.liquid, side.code)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </DataTableRegion>
              <p className="console-module-sub">
                <Link href="/wealth">Net position history</Link>
              </p>
            </section>

            <section aria-label="This year so far" className="console-section">
              <h3>This year so far · {dashboard.yearToDate.yearStart} to {dashboard.yearToDate.asOf}</h3>
              <dl className="console-figures">
                <div><dt>Income</dt><dd>{money(ytd.income, shown)}</dd></div>
                <div><dt>Spending</dt><dd>{money(ytd.spending, shown)}</dd></div>
                <div><dt>Saved</dt><dd>{money(ytd.net, shown)}</dd></div>
                <div><dt>Savings rate</dt><dd>{percent(ytd.savings)}</dd></div>
                <div><dt>Invested</dt><dd><Link href="/investments">{money(ytd.invested, shown)}</Link></dd></div>
              </dl>
            </section>

            <section aria-label="Recorded monthly totals" className="console-section">
              <h3>Recorded monthly totals</h3>
              <p className="console-module-sub">Includes activity in months whose statement coverage is incomplete.</p>
              <DataTableRegion label="Recorded monthly totals table">
                <table className="console-table">
                  <caption className="visually-hidden">The last twelve months</caption>
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
              </DataTableRegion>
            </section>
          </div>
        </details>
      </>
    );
  }
}
