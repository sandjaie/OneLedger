"use client";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";

import { useEffect, useState } from "react";
import Link from "next/link";

import { EvidenceChart } from "@/app/ui/evidence-chart";
import { errorText, type apiFetch } from "@/lib/api/client";
import { dateLabel, monthLabel } from "@/lib/format/date";
import { formatPercent, moneyText } from "@/lib/money/format";
import type {
  CurrencyRow,
  EarningRuleRow,
  SalaryAnalysisResponse,
  SalaryCurrencyOut,
} from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
  /** Bumped by the payslips screen after an entry, a correction, an import or a link/unlink, so
   * this analysis reloads with them — the same "attempt" convention that reloads the payslip
   * list itself. */
  refreshKey: number;
};

type LoadState = "loading" | "ready" | "error";
type EarningComponent = EarningRuleRow["component"];

const RECEIPT_TEXT: Record<string, string> = {
  received: "Received",
  unlinked: "Not linked",
  ineligible: "Linked, but no longer eligible",
};

/** A month row in the Months table: either a payslip's own figures, or a gap with none. The
 * payslips array lists each payslip individually (a separate bonus payslip in the same month is
 * its own row), so no client-side sum across payslips is needed here. */
type MonthRow = { payMonth: string; payslip: SalaryCurrencyOut["payslips"][number] | undefined };

function monthRows(currency: SalaryCurrencyOut): Array<MonthRow> {
  const rows: Array<MonthRow> = [
    ...currency.payslips.map((payslip) => ({ payMonth: payslip.payMonth, payslip })),
    ...currency.gaps.map((payMonth) => ({ payMonth, payslip: undefined })),
  ];
  return rows.sort((a, b) => (a.payMonth < b.payMonth ? 1 : a.payMonth > b.payMonth ? -1 : 0));
}

export function SalaryAnalysis({ currencies, request, refreshKey }: Props) {
  const [data, setData] = useState<SalaryAnalysisResponse>();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [retryAttempt, setRetryAttempt] = useState(0);
  const [selectedCurrency, setSelectedCurrency] = useState<string>();
  const [ruleBusy, setRuleBusy] = useState<string>();
  const [ruleProblem, setRuleProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<SalaryAnalysisResponse>("/salary/analysis")
      .then((result) => {
        if (!current) return;
        setData(result);
        setSelectedCurrency((prior) =>
          result.currencies.some((row) => row.currency === prior) ? prior : result.currencies[0]?.currency,
        );
        setLoadState("ready");
      })
      .catch(() => {
        if (current) setLoadState("error");
      });
    return () => {
      current = false;
    };
  }, [refreshKey, retryAttempt, request]);

  async function saveRule(label: string, component: EarningComponent) {
    setRuleBusy(label);
    setRuleProblem(undefined);
    try {
      await request<EarningRuleRow>(`/salary/earning-rules/${encodeURIComponent(label)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ component }),
      });
      // The save succeeded; a failed reload shows the load error with its retry, not a save error.
      setRetryAttempt((value) => value + 1);
    } catch (error) {
      setRuleProblem(errorText(error, "This component could not be saved."));
    } finally {
      setRuleBusy(undefined);
    }
  }

  async function clearRule(label: string) {
    setRuleBusy(label);
    setRuleProblem(undefined);
    try {
      await request<void>(`/salary/earning-rules/${encodeURIComponent(label)}`, { method: "DELETE" });
      setRetryAttempt((value) => value + 1);
    } catch (error) {
      setRuleProblem(errorText(error, "This rule could not be removed."));
    } finally {
      setRuleBusy(undefined);
    }
  }

  const current: SalaryCurrencyOut | undefined =
    data?.currencies.find((row) => row.currency === selectedCurrency) ?? data?.currencies[0];

  return (
    <section aria-labelledby="salary-analysis-title">
      <h2 id="salary-analysis-title">Analysis</h2>

      {loadState === "loading" && <p role="status">Loading salary analysis…</p>}
      {loadState === "error" && (
        <div role="alert">
          <p>Salary analysis could not be loaded.</p>
          <Button
            variant="outline"
            onClick={() => {
              setLoadState("loading");
              setRetryAttempt((value) => value + 1);
            }}
            type="button"
          >
            Try again
          </Button>
        </div>
      )}

      {loadState === "ready" && data && (
        <>
          {ruleProblem && <p className="inline-alert" role="alert">{ruleProblem}</p>}

          {data.currencies.length === 0 && <p>No payslips yet.</p>}

          {current && (
            <>
              {data.currencies.length > 1 && (
                <div aria-label="Analysis currency" className="currency-toggle" role="group">
                  {data.currencies.map((row) => (
                    <Button
                      aria-pressed={row.currency === current.currency}
                      key={row.currency}
                      onClick={() => setSelectedCurrency(row.currency)}
                      type="button"
                    >
                      {row.currency}
                    </Button>
                  ))}
                </div>
              )}

              {current.trend.length > 0 && (
                <EvidenceChart
                  title={`Payslip net pay and linked receipts · ${current.currency}`}
                  description="Payslip net pay is an earned claim; linked receipt is shown only when every payslip in that month has an eligible matching deposit. Unlinked or ineligible receipts and months without payslips remain gaps; unmatched salary credits are listed below."
                  periods={current.trend.map((point) => point.payMonth)}
                  series={[
                    {
                      id: "earned", label: "Payslip net pay", color: "var(--category-1)", lineStyle: "solid", marker: "circle",
                      points: current.trend.map((point) => ({
                        period: point.payMonth, coordinate: point.earnedCoordinate,
                        exact: point.earnedNetPay === null ? null : moneyText(point.earnedNetPay, current.currency, currencies),
                      })),
                    },
                    {
                      id: "received", label: "Linked cash received", color: "var(--category-2)", lineStyle: "dashed", marker: "square",
                      points: current.trend.map((point) => ({
                        period: point.payMonth, coordinate: point.receivedCoordinate,
                        exact: point.cashReceived === null ? null : moneyText(point.cashReceived, current.currency, currencies),
                      })),
                    },
                  ]}
                />
              )}

              <section aria-labelledby="salary-years-title">
                <p className="section-label" id="salary-years-title">Years</p>
                <div className="overview-scroll">
                  <table className="spending-table">
                    <caption className="visually-hidden">Salary by year</caption>
                    <thead>
                      <tr>
                        <th scope="col">Year</th>
                        <th scope="col">Compensation</th>
                        <th scope="col">Fixed</th>
                        <th scope="col">Variable</th>
                        <th scope="col">Variable share</th>
                        <th scope="col">Reimbursement</th>
                        <th scope="col">Deductions</th>
                        <th scope="col">Net pay</th>
                        <th scope="col">Cash received</th>
                        <th scope="col">Payslip months</th>
                        <th scope="col">Gap months</th>
                        <th scope="col">Received months</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.years.map((year) => (
                        <tr key={year.year}>
                          <th scope="row">{year.year}</th>
                          <td>{moneyText(year.compensation, current.currency, currencies)}</td>
                          <td>{moneyText(year.fixed, current.currency, currencies)}</td>
                          <td>{moneyText(year.variable, current.currency, currencies)}</td>
                          <td>{year.variableShare !== null ? formatPercent(year.variableShare) : "—"}</td>
                          <td>{moneyText(year.reimbursement, current.currency, currencies)}</td>
                          <td>{moneyText(year.deductions, current.currency, currencies)}</td>
                          <td>{moneyText(year.netPay, current.currency, currencies)}</td>
                          <td>{moneyText(year.cashReceived, current.currency, currencies)}</td>
                          <td>{year.payslipMonths}</td>
                          <td>{year.gapMonths}</td>
                          <td>{year.receivedMonths}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section aria-labelledby="salary-months-title">
                <p className="section-label" id="salary-months-title">Months</p>
                <div className="overview-scroll">
                  <table className="spending-table">
                    <caption className="visually-hidden">Salary by month</caption>
                    <thead>
                      <tr>
                        <th scope="col">Month</th>
                        <th scope="col">Compensation</th>
                        <th scope="col">Fixed</th>
                        <th scope="col">Variable</th>
                        <th scope="col">Reimbursement</th>
                        <th scope="col">Deductions</th>
                        <th scope="col">Net pay</th>
                        <th scope="col">Cash received</th>
                        <th scope="col">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {monthRows(current).map((row, index) => (
                        <tr key={`${row.payMonth}-${index}`}>
                          <th scope="row">{monthLabel(row.payMonth)}</th>
                          {row.payslip ? (
                            <>
                              <td>{moneyText(row.payslip.compensation, current.currency, currencies)}</td>
                              <td>{moneyText(row.payslip.fixed, current.currency, currencies)}</td>
                              <td>{moneyText(row.payslip.variable, current.currency, currencies)}</td>
                              <td>{moneyText(row.payslip.reimbursement, current.currency, currencies)}</td>
                              <td>{moneyText(row.payslip.deductions, current.currency, currencies)}</td>
                              <td>{moneyText(row.payslip.netPay, current.currency, currencies)}</td>
                              <td>
                                {row.payslip.cashReceived !== null
                                  ? moneyText(row.payslip.cashReceived, current.currency, currencies)
                                  : "—"}
                              </td>
                              <td>{RECEIPT_TEXT[row.payslip.receiptStatus] ?? row.payslip.receiptStatus}</td>
                            </>
                          ) : (
                            <td colSpan={8}>No payslip</td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section aria-labelledby="salary-increments-title">
                <p className="section-label" id="salary-increments-title">Increments</p>
                {current.increments.length === 0 && <p>No increment is recorded yet.</p>}
                {current.increments.length > 0 && (
                  <ul className="recurring-confirmed-list">
                    {current.increments.map((increment) => (
                      <li key={increment.effectiveMonth}>
                        <span>
                          {monthLabel(increment.effectiveMonth)} ·{" "}
                          {moneyText(increment.previousFixed, current.currency, currencies)} →{" "}
                          {moneyText(increment.newFixed, current.currency, currencies)}
                        </span>
                        <span>
                          {moneyText(increment.change, current.currency, currencies)}
                          {increment.changePercent !== null && ` (${formatPercent(increment.changePercent)})`}
                        </span>
                        {(increment.confirmed === false || increment.acrossGap) && (
                          <span className="plans-badges">
                            {!increment.confirmed && <span className="plans-badge">Unconfirmed</span>}
                            {increment.acrossGap && <span className="plans-badge">After a gap</span>}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section aria-labelledby="salary-variable-pay-title">
                <p className="section-label" id="salary-variable-pay-title">Variable pay</p>
                {current.variablePay.length === 0 && <p>No variable pay is recorded yet.</p>}
                {current.variablePay.length > 0 && (
                  <ul className="recurring-confirmed-list">
                    {current.variablePay.map((entry, index) => (
                      <li key={`${entry.payslipId}-${index}`}>
                        <span>{monthLabel(entry.payMonth)} · {entry.label}</span>
                        <span>{moneyText(entry.amount, current.currency, currencies)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section aria-labelledby="salary-labels-title">
                <p className="section-label" id="salary-labels-title">Earning labels</p>
                <div className="overview-scroll">
                  <table className="spending-table">
                    <caption className="visually-hidden">Earning labels</caption>
                    <thead>
                      <tr>
                        <th scope="col">Label</th>
                        <th scope="col">Component</th>
                        <th scope="col">Months</th>
                        <th scope="col">First month</th>
                        <th scope="col">Last month</th>
                        <th scope="col">Latest amount</th>
                        <th scope="col">Rule</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.labels.map((label) => (
                        <tr key={label.label}>
                          <th scope="row">{label.label}</th>
                          <td>
                            <NativeSelect
                              aria-label={`Component for ${label.label}`}
                              disabled={ruleBusy === label.label}
                              onChange={(e) => void saveRule(label.label, e.target.value as EarningComponent)}
                              value={label.component}
                            >
                              <option value="fixed">Fixed</option>
                              <option value="variable">Variable</option>
                              <option value="reimbursement">Reimbursement</option>
                            </NativeSelect>
                            {label.source === "inferred" && <span className="plans-badge">Inferred</span>}
                          </td>
                          <td>{label.monthCount}</td>
                          <td>{monthLabel(label.firstMonth)}</td>
                          <td>{monthLabel(label.lastMonth)}</td>
                          <td>{moneyText(label.latestAmount, current.currency, currencies)}</td>
                          <td>
                            {label.source === "rule" && (
                              <Button
                                variant="outline"
                                disabled={ruleBusy === label.label}
                                onClick={() => void clearRule(label.label)}
                                type="button"
                              >
                                Use inference
                              </Button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}

          <section aria-labelledby="salary-credits-without-payslip-title">
            <p className="section-label" id="salary-credits-without-payslip-title">
              Salary credits without a payslip
            </p>
            {data.salaryCreditsWithoutPayslip.length === 0 && <p>No salary credit is missing a payslip.</p>}
            {data.salaryCreditsWithoutPayslip.length > 0 && (
              <ul className="recurring-confirmed-list">
                {data.salaryCreditsWithoutPayslip.map((credit) => (
                  <li key={credit.transactionId}>
                    <span>
                      <Link href={`/transactions?dateFrom=${credit.bookedDate}&dateTo=${credit.bookedDate}`}>
                        {dateLabel(credit.bookedDate)}
                      </Link>{" "}
                      · {credit.accountName}
                    </span>
                    <span>{moneyText(credit.amount, credit.currency, currencies)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </section>
  );
}
