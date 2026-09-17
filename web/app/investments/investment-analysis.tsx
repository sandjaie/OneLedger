"use client";

import { Fragment, useEffect, useState } from "react";

import { errorText, type apiFetch } from "@/lib/api/client";
import { dateLabel } from "@/lib/format/date";
import type {
  AcquisitionPosition,
  AnalysisResponse,
  AssetClassName,
  BenchmarkOut,
  EffectWindowOut,
  TargetGroupOut,
} from "@/types/api";

type Props = {
  baseCurrency: string;
  positions: Array<AcquisitionPosition>;
  request: typeof apiFetch;
  /** Bumped after a price refresh or a recorded event so analysis reloads with them. */
  refreshKey: number;
};

type LoadState = "loading" | "ready" | "error";

const ASSET_CLASSES: Array<AssetClassName> = [
  "equity",
  "fixed_income",
  "cash",
  "commodity",
  "real_asset",
  "alternative",
  "other",
];

const XIRR_REASON_TEXT: Record<string, string> = {
  no_sign_change: "No sign change across the cash flows.",
  multiple_roots: "More than one rate solves the cash flows.",
  missing_terminal_value: "No current or realised value is recorded.",
  incomplete_history: "History includes a switch, transfer or split.",
  missing_price: "A dated price is missing.",
  blocked_action: "An unrecognized event blocks this calculation.",
  not_converged: "The calculation did not converge.",
};

const DIETZ_REASON_TEXT: Record<string, string> = {
  no_denominator: "Opening value and weighted contribution are zero.",
  missing_price: "A dated price is missing.",
};

function classLabel(label: string) {
  return label.replaceAll("_", " ");
}

function reasonText(reason: string | null, table: Record<string, string>) {
  if (!reason) return "";
  return table[reason] ?? reason.replaceAll("_", " ");
}

function money(baseCurrency: string, value: string | null) {
  return value === null ? "—" : `${baseCurrency} ${value}`;
}

function percent(value: string | null, reason: string | null, table: Record<string, string>) {
  if (value !== null) return `${value}%`;
  return reasonText(reason, table) || "—";
}

function ratio(value: string | null) {
  return value ?? "—";
}

function contributionCells(baseCurrency: string, row: EffectWindowOut) {
  return (
    <>
      <td>{percent(row.dietzPercent, row.dietzReason, DIETZ_REASON_TEXT)}</td>
      <td>{money(baseCurrency, row.contributions)}</td>
      <td>{money(baseCurrency, row.withdrawals)}</td>
      <td>{money(baseCurrency, row.income)}</td>
      <td>{money(baseCurrency, row.charges)}</td>
      <td>{money(baseCurrency, row.marketAndFx)}</td>
    </>
  );
}

function windowCells(baseCurrency: string, row: EffectWindowOut) {
  return (
    <>
      {contributionCells(baseCurrency, row)}
      <td>{percent(row.excessPercent, row.benchmarkDietzReason, DIETZ_REASON_TEXT)}</td>
    </>
  );
}

function uniqueSecurities(positions: Array<AcquisitionPosition>) {
  const seen = new Map<string, string>();
  for (const position of positions) {
    if (!seen.has(position.securityId)) seen.set(position.securityId, position.securityName);
  }
  return [...seen.entries()].map(([securityId, securityName]) => ({ securityId, securityName }));
}

function statusLine(counts: Record<string, number>) {
  const parts = Object.keys(counts)
    .sort()
    .filter((key) => counts[key] > 0)
    .map((key) => `${counts[key]} ${key.replaceAll("_", " ")}`);
  return parts.length === 0 ? "" : parts.join(", ");
}

export function InvestmentAnalysis({ baseCurrency, positions, request, refreshKey }: Props) {
  const [data, setData] = useState<AnalysisResponse>();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [retryAttempt, setRetryAttempt] = useState(0);
  const [targetDraft, setTargetDraft] = useState<Record<string, string>>({});
  const [targetBusy, setTargetBusy] = useState(false);
  const [targetProblem, setTargetProblem] = useState<string>();
  const [benchmarkBusy, setBenchmarkBusy] = useState<string>();
  const [benchmarkProblem, setBenchmarkProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<AnalysisResponse>("/portfolio/analysis")
      .then((result) => {
        if (!current) return;
        setData(result);
        const draft: Record<string, string> = {};
        for (const group of result.target.groups) draft[group.label] = group.ratio;
        setTargetDraft(draft);
        setLoadState("ready");
      })
      .catch(() => {
        if (current) setLoadState("error");
      });
    return () => {
      current = false;
    };
  }, [refreshKey, retryAttempt, request]);

  async function saveTargets() {
    setTargetBusy(true);
    setTargetProblem(undefined);
    const groups: Array<TargetGroupOut> = ASSET_CLASSES.filter(
      (label) => (targetDraft[label] ?? "").trim() !== "",
    ).map((label) => ({ label, ratio: targetDraft[label]!.trim() }));
    try {
      await request("/portfolio/allocation-targets", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ groups }),
      });
      setRetryAttempt((value) => value + 1);
    } catch (error) {
      setTargetProblem(errorText(error, "This allocation target could not be saved."));
    } finally {
      setTargetBusy(false);
    }
  }

  async function restoreInference() {
    setTargetBusy(true);
    setTargetProblem(undefined);
    try {
      await request("/portfolio/allocation-targets", { method: "DELETE" });
      setRetryAttempt((value) => value + 1);
    } catch (error) {
      setTargetProblem(errorText(error, "The stored target could not be removed."));
    } finally {
      setTargetBusy(false);
    }
  }

  async function assignBenchmark(scopeKind: "portfolio" | "asset_class", securityId: string, assetClass?: string) {
    const key = assetClass ?? "portfolio";
    setBenchmarkBusy(key);
    setBenchmarkProblem(undefined);
    const query = assetClass ? `?assetClass=${encodeURIComponent(assetClass)}` : "";
    try {
      if (securityId === "") {
        await request(`/portfolio/benchmarks/${scopeKind}${query}`, { method: "DELETE" });
      } else {
        await request(`/portfolio/benchmarks/${scopeKind}${query}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ securityId }),
        });
      }
      setRetryAttempt((value) => value + 1);
    } catch (error) {
      setBenchmarkProblem(errorText(error, "This benchmark could not be updated."));
    } finally {
      setBenchmarkBusy(undefined);
    }
  }

  const securities = uniqueSecurities(positions);
  const portfolioBenchmark = data?.benchmarks.find((row) => row.scopeKind === "portfolio");

  function classBenchmark(label: string): BenchmarkOut | undefined {
    return data?.benchmarks.find((row) => row.scopeKind === "asset_class" && row.assetClass === label);
  }

  return (
    <section aria-labelledby="investment-analysis-title">
      <h2 id="investment-analysis-title">Analysis</h2>

      {loadState === "loading" && <p role="status">Loading investment analysis…</p>}
      {loadState === "error" && (
        <div role="alert">
          <p>Investment analysis could not be loaded.</p>
          <button
            className="text-action"
            onClick={() => {
              setLoadState("loading");
              setRetryAttempt((value) => value + 1);
            }}
            type="button"
          >
            Try again
          </button>
        </div>
      )}

      {loadState === "ready" && data && (
        <>
          {data.classes.length === 0 && data.accounts.length === 0 && (
            <p>No holdings to analyse.</p>
          )}

          {data.wealthHistoryReason === "wealth_history_not_refreshed" && (
            <p className="investment-state">Wealth history has not been refreshed. Month and year figures are empty until a refresh stores them.</p>
          )}

          {data.coverage.length > 0 && (
            <section aria-labelledby="analysis-coverage-title">
              <p className="section-label" id="analysis-coverage-title">Coverage</p>
              <ul>
                {data.coverage.map((row) => (
                  <li key={row.accountId}>
                    {row.accountName} · {classLabel(row.valuationMode)} — excluded from holdings analysis
                  </li>
                ))}
              </ul>
            </section>
          )}

          {data.classes.length > 0 && (
            <section aria-labelledby="analysis-classes-title">
              <p className="section-label" id="analysis-classes-title">Asset classes</p>
              <div className="overview-scroll">
                <table className="spending-table">
                  <caption className="visually-hidden">Returns by asset class</caption>
                  <thead>
                    <tr>
                      <th scope="col">Class</th>
                      <th scope="col">XIRR</th>
                      <th scope="col">Value</th>
                      <th scope="col">Inception Dietz</th>
                      <th scope="col">Contributions</th>
                      <th scope="col">Withdrawals</th>
                      <th scope="col">Income</th>
                      <th scope="col">Charges</th>
                      <th scope="col">Market and FX</th>
                      <th scope="col">Excess</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.classes.map((row) => (
                      <tr key={row.label}>
                        <th scope="row">{classLabel(row.label)}</th>
                        <td>{percent(row.xirrPercent, row.xirrReason, XIRR_REASON_TEXT)}</td>
                        <td>{money(baseCurrency, row.valueBase)}</td>
                        {windowCells(baseCurrency, row.inception)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {data.accounts.length > 0 && (
            <section aria-labelledby="analysis-accounts-title">
              <p className="section-label" id="analysis-accounts-title">Accounts</p>
              <div className="overview-scroll">
                <table className="spending-table">
                  <caption className="visually-hidden">Returns by account</caption>
                  <thead>
                    <tr>
                      <th scope="col">Account</th>
                      <th scope="col">XIRR</th>
                      <th scope="col">Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.accounts.map((row) => (
                      <tr key={row.accountId}>
                        <th scope="row">{row.accountName}</th>
                        <td>{percent(row.xirrPercent, row.xirrReason, XIRR_REASON_TEXT)}</td>
                        <td>{money(baseCurrency, row.valueBase)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section aria-labelledby="analysis-inception-title">
            <p className="section-label" id="analysis-inception-title">Inception</p>
            <div className="overview-scroll">
              <table className="spending-table">
                <caption className="visually-hidden">Inception to as-of</caption>
                <thead>
                  <tr>
                    <th scope="col">Dietz</th>
                    <th scope="col">Contributions</th>
                    <th scope="col">Withdrawals</th>
                    <th scope="col">Income</th>
                    <th scope="col">Charges</th>
                    <th scope="col">Market and FX</th>
                    <th scope="col">Excess</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {windowCells(baseCurrency, data.inception)}
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="analysis-years-title">
            <p className="section-label" id="analysis-years-title">Years</p>
            {data.years.length === 0 && <p>No year figures yet.</p>}
            {data.years.length > 0 && (
              <div className="overview-scroll">
                <table className="spending-table">
                  <caption className="visually-hidden">Returns by year</caption>
                  <thead>
                    <tr>
                      <th scope="col">Year</th>
                      <th scope="col">Dietz</th>
                      <th scope="col">Contributions</th>
                      <th scope="col">Withdrawals</th>
                      <th scope="col">Income</th>
                      <th scope="col">Charges</th>
                      <th scope="col">Market and FX</th>
                      <th scope="col">Excess</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.years.map((year) => (
                      <Fragment key={year.year}>
                        <tr>
                          <th scope="row">
                            {year.year}
                            {year.incompleteMonths > 0 && (
                              <> · {year.incompleteMonths} incomplete month{year.incompleteMonths === 1 ? "" : "s"}</>
                            )}
                          </th>
                          {year.incompleteMonths > 0 ? (
                            <td colSpan={7}>No sums — a month in this year is incomplete.</td>
                          ) : (
                            windowCells(baseCurrency, year.portfolio)
                          )}
                        </tr>
                        {year.incompleteMonths === 0 &&
                          year.classes.map((row) => (
                            <tr key={`${year.year}-${row.label}`}>
                              <th scope="row">{classLabel(row.label)}</th>
                              {windowCells(baseCurrency, row)}
                            </tr>
                          ))}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-labelledby="analysis-months-title">
            <p className="section-label" id="analysis-months-title">Months</p>
            {data.months.length === 0 && <p>No month figures yet.</p>}
            {data.months.length > 0 && (
              <div className="overview-scroll">
                <table className="spending-table">
                  <caption className="visually-hidden">Returns by month</caption>
                  <thead>
                    <tr>
                      <th scope="col">Month</th>
                      <th scope="col">Dietz</th>
                      <th scope="col">Contributions</th>
                      <th scope="col">Withdrawals</th>
                      <th scope="col">Income</th>
                      <th scope="col">Charges</th>
                      <th scope="col">Market and FX</th>
                      <th scope="col">Allocation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.months.map((month) => (
                      <tr key={month.monthEnd}>
                        <th scope="row">{dateLabel(month.monthEnd)}</th>
                        {month.complete ? (
                          <>
                            {contributionCells(baseCurrency, month.portfolio)}
                            <td>
                              {month.classes.map((row) => (
                                <div key={row.label}>
                                  {classLabel(row.label)}
                                  {" · "}
                                  {percent(row.dietzPercent, row.dietzReason, DIETZ_REASON_TEXT)}
                                  {" · contrib "}
                                  {money(baseCurrency, row.contributions)}
                                  {" · "}
                                  {ratio(row.ratio)}
                                  {row.targetRatio !== null && ` · target ${row.targetRatio}`}
                                  {row.drift !== null && ` · drift ${row.drift}`}
                                </div>
                              ))}
                            </td>
                          </>
                        ) : (
                          <td colSpan={7}>
                            Incomplete month
                            {statusLine(month.statusCounts) && ` · ${statusLine(month.statusCounts)}`}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-labelledby="analysis-target-title">
            <p className="section-label" id="analysis-target-title">Allocation target</p>
            {targetProblem && <p className="inline-alert" role="alert">{targetProblem}</p>}
            <p>
              {data.target.source === "rule" && "Owner set"}
              {data.target.source === "inferred" && (
                <>
                  Inferred <span className="plans-badge">Inferred</span>
                </>
              )}
              {data.target.source === "none" && "No target yet."}
            </p>
            <div className="overview-scroll">
              <table className="spending-table">
                <caption className="visually-hidden">Allocation target ratios</caption>
                <thead>
                  <tr>
                    <th scope="col">Class</th>
                    <th scope="col">Ratio</th>
                  </tr>
                </thead>
                <tbody>
                  {ASSET_CLASSES.map((label) => (
                    <tr key={label}>
                      <th scope="row">{classLabel(label)}</th>
                      <td>
                        <input
                          aria-label={`Target ratio for ${classLabel(label)}`}
                          disabled={targetBusy}
                          onChange={(event) =>
                            setTargetDraft((current) => ({ ...current, [label]: event.target.value }))
                          }
                          value={targetDraft[label] ?? ""}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="portfolio-links">
              <button className="text-action" disabled={targetBusy} onClick={() => void saveTargets()} type="button">
                Save target
              </button>
              {data.target.source === "rule" && (
                <button className="text-action" disabled={targetBusy} onClick={() => void restoreInference()} type="button">
                  Use inference
                </button>
              )}
            </div>
          </section>

          <section aria-labelledby="analysis-benchmarks-title" className="analysis-benchmarks">
            <p className="section-label" id="analysis-benchmarks-title">Benchmarks</p>
            {benchmarkProblem && <p className="inline-alert" role="alert">{benchmarkProblem}</p>}
            <label>
              Portfolio
              <select
                aria-label="Portfolio benchmark"
                disabled={benchmarkBusy === "portfolio"}
                onChange={(event) => void assignBenchmark("portfolio", event.target.value)}
                value={portfolioBenchmark?.securityId ?? ""}
              >
                <option value="">No benchmark</option>
                {securities.map((security) => (
                  <option key={security.securityId} value={security.securityId}>
                    {security.securityName}
                  </option>
                ))}
              </select>
            </label>
            {data.classes.map((row) => (
              <label key={row.label}>
                {classLabel(row.label)}
                <select
                  aria-label={`${classLabel(row.label)} benchmark`}
                  disabled={benchmarkBusy === row.label}
                  onChange={(event) => void assignBenchmark("asset_class", event.target.value, row.label)}
                  value={classBenchmark(row.label)?.securityId ?? ""}
                >
                  <option value="">No benchmark</option>
                  {securities.map((security) => (
                    <option key={security.securityId} value={security.securityId}>
                      {security.securityName}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </section>
        </>
      )}
    </section>
  );
}
